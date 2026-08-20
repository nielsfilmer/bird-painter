"""The e-paper frame client (`bird_painter/frame_client.py`). The panel driver
is hardware-only, so these test the pure image processing + the fetch→draw
cycle logic with a fake panel (no Waveshare lib, no real SPI)."""

import contextlib
import io
import logging

import httpx
import pytest
from PIL import Image

from bird_painter import frame_client as fc


def _png_bytes(color=(180, 60, 40), size=(40, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_dither_reduces_to_the_six_panel_colours_at_panel_size():
    # A smooth gradient (many colours) must come out using ONLY the panel's six.
    src = Image.new("RGB", (64, 64))
    src.putdata([(x * 4 % 256, y * 4 % 256, 128) for y in range(64) for x in range(64)])
    out = fc.dither_to_panel(src, (200, 150))
    assert out.size == (200, 150)
    used = {color for _count, color in out.getcolors(maxcolors=256)}
    assert used <= set(fc.PANEL_PALETTE)
    assert len(used) > 1  # a gradient dithers across several panel colours


def test_dither_rotates_then_fits_panel_size():
    src = Image.new("RGB", (100, 40), (0, 0, 255))
    out = fc.dither_to_panel(src, (300, 200), rotate=90)
    assert out.size == (300, 200)  # rotation handled, still resized to the panel


def test_fetch_image_returns_body(monkeypatch):
    def handler(request):
        return httpx.Response(200, content=b"PNGDATA")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fc.fetch_image("http://recorder/wall.png", client=client) == b"PNGDATA"


def test_fetch_image_raises_on_error_status():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(httpx.HTTPStatusError):
        fc.fetch_image("http://recorder/wall.png", client=client)


class _FakePanel:
    def __init__(self):
        self.displays = 0

    def getbuffer(self, image):
        return image

    def display(self, buf):
        self.displays += 1

    def sleep(self):
        pass


def test_refresh_draws_then_skips_when_image_unchanged(monkeypatch):
    png = _png_bytes()
    monkeypatch.setattr(fc, "fetch_image", lambda url, client=None, timeout=None: png)
    panel = _FakePanel()
    calls = []

    def push(p, image):
        calls.append(image)
        p.display(p.getbuffer(image))

    # First tick draws.
    h1 = fc.refresh_once(
        "u", (120, 90), 0, None, panel_factory=lambda: panel, push=push
    )
    assert h1 is not None and panel.displays == 1
    # Identical image next tick: no redraw, same hash carried forward.
    h2 = fc.refresh_once(
        "u", (120, 90), 0, h1, panel_factory=lambda: panel, push=push
    )
    assert h2 == h1 and panel.displays == 1


def test_refresh_redraws_when_image_changes(monkeypatch):
    state = {"png": _png_bytes((10, 20, 30))}
    monkeypatch.setattr(
        fc, "fetch_image", lambda url, client=None, timeout=None: state["png"]
    )
    panel = _FakePanel()
    factory = lambda: panel  # noqa: E731
    push = lambda p, image: p.display(p.getbuffer(image))  # noqa: E731

    h1 = fc.refresh_once("u", (120, 90), 0, None, panel_factory=factory, push=push)
    state["png"] = _png_bytes((200, 10, 10))  # a different wall
    h2 = fc.refresh_once("u", (120, 90), 0, h1, panel_factory=factory, push=push)
    assert h2 != h1 and panel.displays == 2


def test_refresh_keeps_last_frame_on_fetch_failure(monkeypatch):
    def boom(url, client=None, timeout=None):
        raise httpx.ConnectError("recorder unreachable")

    monkeypatch.setattr(fc, "fetch_image", boom)
    panel = _FakePanel()
    result = fc.refresh_once(
        "u", (120, 90), 0, "prevhash", panel_factory=lambda: panel
    )
    assert result == "prevhash"  # unchanged; no draw attempted
    assert panel.displays == 0


def _fake_epd_module(inits):
    import types

    module = types.ModuleType("epd13in3E")

    class EPD:
        def Init(self):
            inits.append(True)

    module.EPD = EPD
    return module, EPD


def test_load_panel_prefers_the_flat_driver_module(monkeypatch):
    # The Spectra 6 driver is a flat `epd13in3E` module (not the waveshare_epd
    # package). load_panel must import it, add the driver dir to the path, and
    # Init() the panel.
    import sys

    monkeypatch.setattr(sys, "path", list(sys.path))  # so the insert can't leak
    inits = []
    module, EPD = _fake_epd_module(inits)
    monkeypatch.setitem(sys.modules, "epd13in3E", module)
    monkeypatch.setenv("BP_FRAME_DRIVER_PATH", "/some/driver/lib")
    epd = fc.load_panel()
    assert isinstance(epd, EPD)
    assert inits == [True]
    assert "/some/driver/lib" in sys.path


def test_load_panel_falls_back_to_the_package_layout(monkeypatch):
    # When the flat module is absent, fall back to `from waveshare_epd import
    # epd13in3E` (the mono/3-colour panels' layout).
    import sys
    import types

    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "epd13in3E", raising=False)  # flat absent
    inits = []
    submodule, EPD = _fake_epd_module(inits)
    package = types.ModuleType("waveshare_epd")
    package.epd13in3E = submodule
    monkeypatch.setitem(sys.modules, "waveshare_epd", package)
    monkeypatch.setitem(sys.modules, "waveshare_epd.epd13in3E", submodule)
    epd = fc.load_panel()
    assert isinstance(epd, EPD)
    assert inits == [True]


def test_importing_frame_client_needs_no_hardware_driver():
    # The module must import without the Waveshare lib (it's a manual install on
    # the frame Pi); the driver import lives inside load_panel, called at run.
    import importlib

    importlib.reload(fc)
    assert hasattr(fc, "refresh_once")


def test_the_stream_url_follows_the_wall_it_is_watching():
    """One recorder, one address: the stream is derived from BP_FRAME_SOURCE
    rather than configured separately, so moving the wall can't leave the two
    pointing at different machines."""
    from bird_painter.frame_client import stream_url

    assert stream_url("http://birdrecorder.local:8537/wall.png") == (
        "ws://birdrecorder.local:8537/ws/detections"
    )
    assert stream_url("https://wall.example:9000/wall.png") == (
        "wss://wall.example:9000/ws/detections"
    )


class _FakeSocket:
    """A stand-in for the recorder's stream: yields messages, then ends."""

    def __init__(self, messages):
        self._messages = messages

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._messages)


def test_a_painted_bird_wakes_the_frame():
    import json
    import threading

    from bird_painter.frame_client import watch_for_paintings

    wake = threading.Event()
    messages = [
        json.dumps({"type": "hello", "recent": []}),
        json.dumps({"type": "detected", "species_common": "Robin"}),  # not painted
    ]
    watch_for_paintings(
        "ws://x/ws",
        wake,
        connect=lambda *a, **k: _FakeSocket(messages),
        reconnect=False,
    )
    assert not wake.is_set(), "a mere detection must not spend a panel redraw"

    messages.append(json.dumps({"type": "painted", "species_common": "Song Thrush"}))
    watch_for_paintings(
        "ws://x/ws",
        wake,
        connect=lambda *a, **k: _FakeSocket(messages),
        reconnect=False,
    )
    assert wake.is_set()


def test_a_dead_stream_never_takes_the_frame_down_with_it():
    """The stream is a nicety: without it the frame still polls on its timer,
    so an unreachable or older recorder costs latency, not the picture."""
    import threading

    from bird_painter.frame_client import watch_for_paintings

    def refuse(*args, **kwargs):
        raise OSError("connection refused")

    wake = threading.Event()
    watch_for_paintings("ws://x/ws", wake, connect=refuse, reconnect=False)  # no raise
    assert not wake.is_set()


def test_junk_on_the_stream_is_ignored():
    import threading

    from bird_painter.frame_client import watch_for_paintings

    wake = threading.Event()
    socket = _FakeSocket(["not json at all", "[]", '{"type": "ping"}'])
    watch_for_paintings(
        "ws://x/ws", wake, connect=lambda *a, **k: socket, reconnect=False
    )
    assert not wake.is_set()


def test_a_burst_of_birds_becomes_one_redraw():
    """The dawn chorus can paint several birds a minute; the panel needs ~30 s
    per redraw and wears with each. Waking early is the feature — waking early
    twenty times is a broken panel."""
    import threading

    from bird_painter.frame_client import wait_for_next_draw

    wake = threading.Event()
    wake.set()  # a bird landed the instant the last redraw finished
    slept = []
    clock = iter([0.0, 0.0])  # drawn_at = 0, and "now" is still 0

    woken = wait_for_next_draw(
        wake,
        last_redraw_at=0.0,
        interval=300,
        min_seconds=90,
        now=lambda: next(clock),
        sleep=slept.append,
    )
    assert woken is True
    assert slept == [90.0], "should hold off the full floor before redrawing"
    assert not wake.is_set(), "birds seen while settling are already in the image"


def test_the_ordinary_timer_still_fires_without_any_birds():
    import threading

    from bird_painter.frame_client import wait_for_next_draw

    wake = threading.Event()  # never set: no birds
    slept = []
    assert wait_for_next_draw(
        wake,
        last_redraw_at=None,  # nothing drawn yet, so nothing to protect
        interval=0.01,
        min_seconds=90,
        sleep=slept.append,
    ) is False
    assert slept == [], "no settling when nothing woke us and nothing is drawn"


def test_a_bird_arriving_late_in_the_interval_redraws_immediately():
    """If the floor has already elapsed since the last redraw, a bird should
    not wait at all — that's the whole point of watching the stream."""
    import threading

    from bird_painter.frame_client import wait_for_next_draw

    wake = threading.Event()
    wake.set()
    slept = []
    woken = wait_for_next_draw(
        wake,
        last_redraw_at=0.0,
        interval=300,
        min_seconds=90,
        now=lambda: 200.0,  # 200 s since the last redraw, well past the floor
        sleep=slept.append,
    )
    assert woken is True
    assert slept == [], "no artificial delay once the floor has passed"


def test_a_bird_is_not_delayed_to_protect_a_panel_that_never_drew():
    """Round-1 review of #103: the floor was anchored to the last FETCH, not
    the last redraw. In a quiet garden nearly every poll finds an unchanged
    image and draws nothing, so ~30% of birds waited out the floor protecting
    a panel that had been idle for an hour."""
    import threading

    from bird_painter.frame_client import wait_for_next_draw

    wake = threading.Event()
    wake.set()
    slept = []
    woken = wait_for_next_draw(
        wake,
        last_redraw_at=None,  # fetches happened; no redraw did
        interval=300,
        min_seconds=90,
        sleep=slept.append,
    )
    assert woken is True
    assert slept == [], "nothing was drawn, so there is nothing to protect"


def test_the_poll_path_respects_the_floor_too():
    """A short BP_FRAME_INTERVAL_SECONDS must not sidestep the panel guard."""
    import threading

    from bird_painter.frame_client import wait_for_next_draw

    wake = threading.Event()  # no birds; the timer expires
    slept = []
    woken = wait_for_next_draw(
        wake,
        last_redraw_at=0.0,
        interval=0.01,
        min_seconds=90,
        now=lambda: 10.0,  # only 10 s since the panel was drawn
        sleep=slept.append,
    )
    assert woken is False
    assert slept == [80.0], "the timer path is floored as well"


def test_a_replayed_backlog_does_not_trigger_a_redraw_storm():
    """The recorder nests recent events under `hello.recent`, so a reconnect
    replays them. The watcher only looks at the TOP-LEVEL type — load-bearing,
    since otherwise every reconnect would redraw once per remembered bird."""
    import json
    import threading

    from bird_painter.frame_client import watch_for_paintings

    wake = threading.Event()
    hello = json.dumps(
        {
            "type": "hello",
            "recent": [
                {"type": "painted", "species_common": "Robin"},
                {"type": "painted", "species_common": "Wren"},
            ],
        }
    )
    watch_for_paintings(
        "ws://x/ws",
        wake,
        connect=lambda *a, **k: _FakeSocket([hello]),
        reconnect=False,
    )
    assert not wake.is_set()


def test_a_missing_websockets_library_says_so_once_and_keeps_polling():
    """The frame installs --no-deps, so this is a deployment state, not a bug.
    It must not be a stack trace in the journal and nothing else."""
    import builtins
    import threading

    from bird_painter.frame_client import watch_for_paintings

    real_import = builtins.__import__

    def no_websockets(name, *args, **kwargs):
        if name.startswith("websockets"):
            raise ImportError("no module named websockets")
        return real_import(name, *args, **kwargs)

    wake = threading.Event()
    builtins.__import__ = no_websockets
    try:
        watch_for_paintings("ws://x/ws", wake)  # returns, does not raise or spin
    finally:
        builtins.__import__ = real_import
    assert not wake.is_set()


def test_a_source_without_a_scheme_still_yields_a_usable_stream_url():
    """Round-2 review: `birdrecorder.local:8537/wall.png` parsed as a path and
    produced `ws:8537/ws/detections`. Pre-existing, but a plausible thing to
    put in a unit file."""
    from bird_painter.frame_client import stream_url

    assert stream_url("birdrecorder.local:8537/wall.png") == (
        "ws://birdrecorder.local:8537/ws/detections"
    )


def test_the_timer_path_clears_the_wake_after_settling():
    """Symmetric with the wake path: a bird painted during a floored poll is
    already in the image about to be fetched."""
    import threading

    from bird_painter.frame_client import wait_for_next_draw

    wake = threading.Event()
    slept = []

    def sleep_and_a_bird_lands(seconds):
        slept.append(seconds)
        wake.set()  # a bird is painted while we settle

    wait_for_next_draw(
        wake,
        last_redraw_at=0.0,
        interval=0.01,
        min_seconds=90,
        now=lambda: 10.0,
        sleep=sleep_and_a_bird_lands,
    )
    assert slept == [80.0]
    assert not wake.is_set(), "that bird is in the image we're about to fetch"


def _mask_bytes(size=(40, 30), ink=None) -> bytes:
    """An L-mode lettering mask: black field, white where glyphs go."""
    mask = Image.new("L", size, 0)
    if ink:
        mask.paste(255, ink)
    buf = io.BytesIO()
    mask.save(buf, "PNG")
    return buf.getvalue()


def test_an_older_recorder_does_not_black_out_the_panel():
    """A recorder older than this client doesn't REJECT `?layer=text` — FastAPI
    ignores query params a route doesn't declare, so it answers 200 with the
    ordinary full-colour wall. Nothing raises, so a fallback that waits for an
    exception never fires, and that RGB wall becomes the alpha mask: cream is
    nearly opaque, so the panel gets stamped black almost edge to edge and the
    hash cache holds it there (QA + review, 2026-08-20)."""
    wall = _png_bytes(color=(236, 225, 198))  # the cream wall, as `main` serves

    def handler(request):
        return httpx.Response(200, content=wall)  # every URL: the same wall

    client = httpx.Client(transport=httpx.MockTransport(handler))
    picture, text = fc.fetch_layers(
        "http://recorder/wall.png", timeout=5, client=client
    )
    assert text is None, "an RGB wall is not a lettering mask"
    assert picture == wall

    # And the consequence the guard exists to prevent.
    panel = fc.dither_to_panel(Image.open(io.BytesIO(picture)), (40, 30))
    import numpy as np

    stamped_anyway = fc.stamp_text(panel, Image.open(io.BytesIO(wall)))
    assert np.asarray(stamped_anyway.convert("L")).mean() < 60, (
        "this is what the panel would have looked like — near black"
    )


def test_a_real_text_layer_is_used():
    def handler(request):
        if "layer=text" in str(request.url):
            return httpx.Response(200, content=_mask_bytes())
        return httpx.Response(200, content=_png_bytes())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    _picture, text = fc.fetch_layers(
        "http://recorder/wall.png", timeout=5, client=client
    )
    assert text is not None
    assert Image.open(io.BytesIO(text)).mode == "L"


def test_the_fallback_refetches_the_captioned_wall():
    """A recorder new enough to honour `layer` but failing on the text layer
    would have sent a caption-less picture. A panel of unnamed birds is worse
    than a dithered one, so the fallback asks again for the whole wall."""
    asked = []

    def handler(request):
        asked.append(str(request.url))
        if "layer=text" in str(request.url):
            return httpx.Response(500)
        return httpx.Response(200, content=_png_bytes())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    _picture, text = fc.fetch_layers(
        "http://recorder/wall.png", timeout=5, client=client
    )
    assert text is None
    assert asked[-1] == "http://recorder/wall.png", "the plain wall, with captions"


def test_the_text_mask_lands_square_on_a_portrait_panel():
    """`BP_FRAME_ROTATE=90` is a documented mounting. The picture rotates then
    resizes; the mask used to resize then rotate, coming out transposed
    (1200x1600 over a 1600x1200 panel) and getting squashed back into place —
    every caption in the wrong spot."""
    picture_src = Image.new("RGB", (160, 120), (255, 255, 255))
    mask_src = _mask_bytes(size=(160, 120), ink=(10, 10, 40, 30))
    for rotate in (0, 90, 180, 270):
        size = (120, 160) if rotate in (90, 270) else (160, 120)
        picture = fc.dither_to_panel(picture_src, size, rotate)
        mask = fc.dither_free_mask(mask_src, size, rotate)
        assert mask.size == picture.size, f"rotate={rotate}"


def test_stamping_leaves_only_panel_colours():
    """The picture is quantised to six colours and nothing quantises after the
    stamp, so a part-lit glyph edge would put an off-palette colour on the
    panel for good."""
    picture = fc.dither_to_panel(Image.new("RGB", (60, 40), (200, 40, 40)), (60, 40))
    soft = Image.new("L", (60, 40), 0)
    soft.paste(90, (5, 5, 30, 20))  # a half-lit edge: neither ink nor ground
    soft.paste(255, (10, 10, 20, 15))
    stamped = fc.stamp_text(picture, soft)
    used = {color for _count, color in stamped.getcolors(maxcolors=4096)}
    assert used <= set(fc.PANEL_PALETTE)


def test_the_search_draws_the_wall_as_soon_as_the_recorder_answers():
    """The search IS a draw attempt, not a liveness ping: a recorder that's up
    costs no extra request, and its wall goes on the panel immediately."""
    attempts = []

    def draw():
        attempts.append(1)
        return "wall-hash" if len(attempts) == 3 else None

    slept = []
    clock = [0.0]

    def sleep(seconds):
        slept.append(seconds)
        clock[0] += seconds

    result = fc.search_for_recorder(
        draw, search_seconds=60, poll_seconds=5,
        now=lambda: clock[0], sleep=sleep,
    )
    assert result == "wall-hash"
    assert len(attempts) == 3, "stopped as soon as it found one"
    assert slept == [5, 5]


def test_the_search_gives_up_after_its_window_and_trims_its_last_wait():
    """Named for what it actually proves. The WAIT is trimmed to the deadline;
    the window as a whole is a floor, not a ceiling, because the clock is read
    after each attempt and an attempt is an HTTP fetch that can take its full
    timeout. Cutting one off mid-flight to honour the deadline would throw away
    the answer we were waiting for."""
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds

    attempts = []
    result = fc.search_for_recorder(
        lambda: attempts.append(1),  # always None: nothing ever answers
        search_seconds=12, poll_seconds=5,
        now=lambda: clock[0], sleep=sleep,
    )
    assert result is None
    assert clock[0] == 12, "the last wait is trimmed to the deadline, not past it"
    assert len(attempts) == 4  # t=0, 5, 10, 12


def test_a_slow_attempt_pushes_the_window_out_rather_than_being_abandoned():
    """The overrun the name above used to imply was impossible. A recorder that
    accepts the connection and then says nothing burns a whole fetch timeout,
    so a 60 s search can end past 60 s — deliberately."""
    clock = [0.0]

    def slow_draw():
        clock[0] += 30.0  # a fetch that hangs until its timeout
        return None

    attempts = []
    fc.search_for_recorder(
        lambda: (attempts.append(1), slow_draw())[1],
        search_seconds=60, poll_seconds=5,
        now=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s),
    )
    # t=0 attempt (→30), sleep 5 (→35), attempt (→65), deadline already past.
    assert clock[0] == 65, "the window is a floor, not a ceiling"
    assert len(attempts) == 2


def test_a_zero_poll_interval_does_not_live_lock_the_search():
    """`poll_seconds` is a public kwarg, and 0 spun forever against an injected
    clock that only advances on sleep — 200k iterations, no progress (QA)."""
    clock = [0.0]
    attempts = []
    result = fc.search_for_recorder(
        lambda: attempts.append(1),
        search_seconds=3, poll_seconds=0,
        now=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s),
    )
    assert result is None
    assert len(attempts) <= 10, "it made progress instead of spinning"


def test_a_recorder_that_answers_immediately_is_never_interrupted_by_a_notice():
    """The notice costs a ~30 s redraw and wears the panel, so it must only
    appear when the search actually found nothing."""
    assert fc.search_for_recorder(
        lambda: "wall-hash", search_seconds=60,
        now=lambda: 0.0, sleep=lambda _s: None,
    ) == "wall-hash"


def test_the_notice_is_centred_crisp_and_only_panel_colours():
    panel = fc.render_notice("Looking for recorder", (400, 300), 0)
    assert panel.size == (400, 300)
    used = {color for _count, color in panel.getcolors(maxcolors=4096)}
    assert used <= set(fc.PANEL_PALETTE), "no dithered grey on a six-colour panel"
    assert (0, 0, 0) in used and (255, 255, 255) in used

    import numpy as np

    ink = np.asarray(panel.convert("L")) < 128
    assert ink.any(), "there is text"
    rows, cols = np.nonzero(ink)
    # Centred: the ink's own middle sits at the panel's middle.
    assert abs((cols.min() + cols.max()) / 2 - 200) < 6
    assert abs((rows.min() + rows.max()) / 2 - 150) < 6
    # And it doesn't run off the sheet.
    assert cols.min() > 4 and cols.max() < 396


def test_the_notice_reads_the_right_way_up_on_a_rotated_panel():
    for rotate in (0, 90, 180, 270):
        size = (300, 400) if rotate in (90, 270) else (400, 300)
        panel = fc.render_notice("Looking for recorder", size, rotate)
        assert panel.size == size, f"rotate={rotate}"


def test_the_notice_is_drawn_once_and_yields_to_the_first_wall():
    """An e-paper redraw wears the panel, so the notice is drawn once and any
    real wall replaces it. While it's up `last_hash` stays None, so a failed
    fetch changes nothing and the first successful one differs from it."""
    drawn = []
    assert fc.show_notice(
        "Looking for recorder", (200, 150), 0,
        panel_factory=lambda: object(),
        push=lambda _panel, image: drawn.append(image),
    ) is True
    assert len(drawn) == 1


def test_a_panel_that_cannot_be_drawn_leaves_the_frame_alone():
    def explode():
        raise RuntimeError("no panel wired up")

    assert fc.show_notice("x", (200, 150), 0, panel_factory=explode) is False


@contextlib.contextmanager
def _captured_logs():
    records = []

    class Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    collector = Collector()
    previous = fc.logger.level
    fc.logger.addHandler(collector)
    fc.logger.setLevel(logging.INFO)
    try:
        yield records
    finally:
        fc.logger.removeHandler(collector)
        fc.logger.setLevel(previous)


def _refusing_client():
    def handler(request):
        raise httpx.ConnectError("refused")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_the_boot_searchs_misses_are_not_news():
    """At boot the frame hasn't found the recorder yet, so `Contact` starts
    unreachable and a minute of looking logs a line per try, not a minute of
    stack traces that say nothing but "connection refused"."""
    contact = fc.Contact()
    assert contact.reachable is False
    with _captured_logs() as records:
        assert fc.refresh_once(
            "http://recorder/wall.png", (40, 30), 0, None,
            client=_refusing_client(), crisp_text=False, contact=contact,
        ) is None
    assert [r.exc_info for r in records] == [None], "no traceback"
    assert "still no answer" in records[0].getMessage()


def test_losing_a_recorder_is_loud_once_then_quiet():
    """The case the first attempt at this missed (review, 2026-08-20): a
    recorder that goes away AFTER a good wall. Worth one stack trace — that's
    real news — and then one line per retry, not a traceback every 300 s all
    night."""
    contact = fc.Contact(reachable=True)  # a wall was drawn a moment ago
    client = _refusing_client()
    with _captured_logs() as records:
        for _ in range(3):
            fc.refresh_once(
                "http://recorder/wall.png", (40, 30), 0, "old-hash",
                client=client, crisp_text=False, contact=contact,
            )
    assert records[0].exc_info is not None, "losing it is worth a traceback"
    assert [r.exc_info for r in records[1:]] == [None, None], "then quiet"
    assert contact.reachable is False


def test_a_recorder_that_comes_back_resets_the_volume():
    """So the NEXT outage is reported as news again rather than swallowed."""
    contact = fc.Contact(reachable=False)

    def handler(request):
        return httpx.Response(200, content=_png_bytes())

    fc.refresh_once(
        "http://recorder/wall.png", (40, 30), 0, None,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        crisp_text=False, contact=contact,
        panel_factory=lambda: object(), push=lambda _p, _i: None,
    )
    assert contact.reachable is True

    with _captured_logs() as records:
        fc.refresh_once(
            "http://recorder/wall.png", (40, 30), 0, "hash",
            client=_refusing_client(), crisp_text=False, contact=contact,
        )
    assert records[0].exc_info is not None, "a fresh outage is news again"


def test_a_long_notice_is_shrunk_to_fit_rather_than_clipped():
    """The caller passes the text, so a longer string is a matter of somebody
    writing one — and half a sentence centred on a wall reads as a broken
    frame, not a message."""
    import numpy as np

    long_text = "Looking for the bird recorder somewhere on this network"
    for text in ("Looking for recorder", long_text):
        panel = fc.render_notice(text, (400, 300), 0)
        ink = np.asarray(panel.convert("L")) < 128
        cols = np.nonzero(ink)[1]
        assert cols.min() > 2 and cols.max() < 397, f"{text!r} runs off the sheet"
