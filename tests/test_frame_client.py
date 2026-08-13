"""The e-paper frame client (`bird_painter/frame_client.py`). The panel driver
is hardware-only, so these test the pure image processing + the fetch→draw
cycle logic with a fake panel (no Waveshare lib, no real SPI)."""

import io

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
        "ws://x/ws", wake, connect=lambda *a, **k: _FakeSocket(messages), forever=False
    )
    assert not wake.is_set(), "a mere detection must not spend a panel redraw"

    messages.append(json.dumps({"type": "painted", "species_common": "Song Thrush"}))
    watch_for_paintings(
        "ws://x/ws", wake, connect=lambda *a, **k: _FakeSocket(messages), forever=False
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
    watch_for_paintings("ws://x/ws", wake, connect=refuse, forever=False)  # no raise
    assert not wake.is_set()


def test_junk_on_the_stream_is_ignored():
    import threading

    from bird_painter.frame_client import watch_for_paintings

    wake = threading.Event()
    socket = _FakeSocket(["not json at all", "[]", '{"type": "ping"}'])
    watch_for_paintings(
        "ws://x/ws", wake, connect=lambda *a, **k: socket, forever=False
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
        drawn_at=0.0,
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
        wake, drawn_at=0.0, interval=0.01, min_seconds=90, sleep=slept.append
    ) is False
    assert slept == [], "no settling when nothing woke us"


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
        drawn_at=0.0,
        interval=300,
        min_seconds=90,
        now=lambda: 200.0,  # 200 s since the last redraw, well past the floor
        sleep=slept.append,
    )
    assert woken is True
    assert slept == [], "no artificial delay once the floor has passed"
