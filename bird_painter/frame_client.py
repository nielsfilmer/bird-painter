"""Thin e-paper frame client — Phase 4, slice 3 (#50).

Runs on the FRAME Pi (Waveshare 13.3" Spectra 6 HAT+, driver `epd13in3E`). It
fetches the recorder's server-rendered collage (`GET /wall.png`) on a slow
timer and pushes it to the panel, dithered to the panel's six colours. It does
no capture and no painting — the recorder does all of that; the frame is a dumb
display ("local ears, cloud brush", one app instance + thin client).

Deliberately dependency-light and hardware-guarded: the Waveshare driver is a
manual install on the frame Pi and is imported only when actually driving the
panel, so this module imports (and unit-tests) fine on a dev box without it.

Run it on the frame:  python -m bird_painter.frame_client
Config via environment:
  BP_FRAME_SOURCE            recorder wall-image URL
                             (default http://birdrecorder.local:8537/wall.png)
  BP_FRAME_INTERVAL_SECONDS  seconds between refreshes (default 300). The panel
                             takes ~25–35 s per full redraw and colour e-paper
                             shouldn't be hammered — keep this minutes, not
                             seconds. This is the FALLBACK cadence; a painted
                             bird wakes the frame immediately (below).
  BP_FRAME_WAKE_ON_PAINT     watch the recorder's /ws/detections stream and
                             redraw as soon as a bird is painted, instead of
                             waiting out the interval (default true). Falls
                             back to polling alone if the stream can't be
                             reached, so an older recorder still works.
  BP_FRAME_MIN_SECONDS       never redraw more often than this, however many
                             birds arrive (default 90). Bursts are coalesced
                             into one redraw — the dawn chorus can paint
                             several birds a minute, and the panel needs ~30 s
                             per redraw.
  BP_FRAME_WIDTH/HEIGHT      panel size (default 1600x1200, the Spectra 6).
  BP_FRAME_ROTATE            0|90|180|270 to match the frame's orientation. NB:
                             0/180 preserve the wall's aspect; 90/270 rotate a
                             landscape render onto a fixed landscape panel and
                             so stretch it — for a portrait hang, render
                             portrait instead (set BP_WALL_PNG_WIDTH/HEIGHT on
                             the recorder + BP_FRAME_WIDTH/HEIGHT to match).
  BP_FRAME_TIMEOUT_SECONDS   HTTP fetch timeout (default 30).
  BP_FRAME_CRISP_TEXT        fetch the wall as two layers — picture and
                             lettering — dither only the picture, then stamp
                             the text through its mask in pure panel black
                             (default true). Dithering an 8px italic turns it
                             into speckle; this keeps type as type. Also asks
                             for a white ground instead of the wall's cream,
                             which isn't one of the panel's six colours and
                             would otherwise dither into a speckle everywhere.
                             Falls back to the single-image fetch if the
                             recorder is too old to serve layers.
  BP_FRAME_DRIVER_PATH       dir to add to sys.path to find the Waveshare
                             driver. The Spectra 6 (E) driver ships as a flat
                             `epd13in3E` module under the panel's own
                             `…/13.3inch_e-Paper_E/RaspberryPi/python/lib`, not
                             in the `waveshare_epd` package; point this there.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import sys
import threading
import time
import urllib.parse

import httpx
from PIL import Image

logger = logging.getLogger(__name__)

DEFAULT_SOURCE = "http://birdrecorder.local:8537/wall.png"
DEFAULT_INTERVAL_SECONDS = 300
# The floor between redraws when birds wake the frame. A full Spectra 6 redraw
# takes ~25–35 s and colour e-paper wears with every one, so a burst of birds
# becomes ONE redraw showing all of them rather than a queue of them.
DEFAULT_MIN_SECONDS = 90
# How long to wait before trying the stream again after it drops. The frame
# still polls on its own timer meanwhile, so a missing stream degrades the
# latency, never the display.
STREAM_RETRY_SECONDS = 30
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_SIZE = (1600, 1200)
# A mask pixel this bright or brighter gets the ink. Glyph edges are
# anti-aliased, and a partial paste would blend black with the picture into a
# colour the six-colour panel doesn't have — after the only quantisation step,
# so nothing would map it back. Half-lit counts as lit.
MASK_THRESHOLD = 128

# The Spectra 6 fixed palette: black, white, red, green, blue, yellow. The
# server renders full-colour on purpose; the frame is where the reduction to
# these six happens (dithered), so the panel gets clean palette pixels.
PANEL_PALETTE = [
    (0, 0, 0),
    (255, 255, 255),
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (255, 255, 0),
]


def _palette_image() -> Image.Image:
    """A PIL 'P'-mode image carrying the six panel colours, for quantize()."""
    pal = Image.new("P", (1, 1))
    flat: list[int] = []
    for rgb in PANEL_PALETTE:
        flat.extend(rgb)
    flat.extend([0, 0, 0] * (256 - len(PANEL_PALETTE)))
    pal.putpalette(flat)
    return pal


def dither_to_panel(
    img: Image.Image, size: tuple[int, int], rotate: int = 0
) -> Image.Image:
    """Reduce a full-colour image to the panel's six colours (Floyd–Steinberg),
    at the panel's size and orientation. Returns an RGB image whose pixels are
    all drawn from PANEL_PALETTE — the driver's getbuffer() then maps each to a
    panel colour with no further loss."""
    img = img.convert("RGB")
    if rotate:
        img = img.rotate(-rotate, expand=True)
    if img.size != size:
        img = img.resize(size)
    quantized = img.quantize(
        palette=_palette_image(), dither=Image.Dither.FLOYDSTEINBERG
    )
    return quantized.convert("RGB")


def fetch_image(
    url: str,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> bytes:
    """Fetch the recorder's /wall.png bytes. Raises on a non-2xx or transport
    error — the caller treats a failed fetch as 'keep the current frame'.
    `timeout` applies only to a client we create here (a passed-in client
    carries its own)."""
    owned = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        response = client.get(url)
        response.raise_for_status()
        return response.content
    finally:
        if owned:
            client.close()


def load_panel():
    """Import + initialise the Waveshare 13.3" Spectra 6 driver. Only present on
    the frame Pi with the driver installed, which is why the import is here and
    not at module top. The Spectra 6 (E) driver ships as a *flat* `epd13in3E`
    module (Waveshare's separate-program tree), unlike the mono/3-colour panels
    which use the `waveshare_epd` package — so try the flat layout first, then
    the package. `BP_FRAME_DRIVER_PATH` puts the driver's `lib` dir on the path."""
    driver_path = os.environ.get("BP_FRAME_DRIVER_PATH")
    if driver_path and driver_path not in sys.path:
        sys.path.insert(0, driver_path)
    try:
        import epd13in3E  # flat module (Spectra 6 separate-program lib)
    except ModuleNotFoundError as exc:
        # Only fall back when the flat module itself is absent — if it's present
        # but its own dep (e.g. spidev) is missing, surface that real error
        # instead of a misleading "no module named waveshare_epd".
        if exc.name != "epd13in3E":
            raise
        from waveshare_epd import epd13in3E  # packaged layout (other panels)
    epd = epd13in3E.EPD()
    epd.Init()
    return epd


def _push(panel, image: Image.Image) -> None:  # pragma: no cover - hardware-only
    """Draw one image and put the panel back to sleep (deep sleep between
    refreshes protects colour e-paper and cuts idle power to ~zero)."""
    panel.display(panel.getbuffer(image))
    panel.sleep()


def fetch_layers(
    url: str,
    *,
    timeout: float,
    client: httpx.Client | None = None,
) -> tuple[bytes, bytes | None]:
    """Fetch the picture layer and the lettering mask.

    Returns (picture, text mask) — the mask is None when the recorder doesn't
    serve layers, in which case the picture is the ordinary wall image and the
    caller just dithers it as before.

    "Doesn't serve layers" cannot be detected by catching an error. A recorder
    older than this client doesn't reject `?layer=text` — FastAPI ignores query
    params a route doesn't declare, so it answers 200 with the ordinary
    full-colour wall. Nothing raises, and that RGB wall then becomes the alpha
    mask: cream is nearly opaque, so the panel is stamped black almost edge to
    edge, and the hash cache keeps it there. What actually distinguishes the
    two is the image itself — a text layer is an L-mode mask and a wall is RGB,
    so that is what we test. The Pis do go out of step; PLAN.md's 2026-08-05
    entry exists because of it."""
    picture_url = _with_query(url, layer="picture", style="panel")
    picture = fetch_image(picture_url, client=client, timeout=timeout)
    try:
        text = fetch_image(
            _with_query(url, layer="text", style="panel"),
            client=client,
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001 — the frame keeps drawing whatever happens
        # A recorder new enough to honour `layer` answered the picture request
        # with a caption-LESS render and then failed here, so what we hold is
        # unusable on its own: a panel of unnamed birds is worse than a
        # dithered one. Ask again for the whole wall. Log the cause — round 1's
        # bug was a fallback that couldn't say why it fired.
        logger.info(
            "frame: text layer request failed; refetching the whole wall",
            exc_info=True,
        )
        return fetch_image(url, client=client, timeout=timeout), None
    if Image.open(io.BytesIO(text)).mode != "L":
        # Not a failure: a recorder older than this client, ignoring both
        # unknown params. That means `picture` is ALREADY the ordinary wall,
        # captions and all — so use it rather than fetching a third copy of a
        # ~290 KB render every cycle, forever, on exactly the deployment this
        # branch exists for.
        logger.info("frame: no text layer from the wall; dithering the whole image")
        return picture, None
    return picture, text


def _with_query(url: str, **params: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query) + list(params.items())
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(query),
            parsed.fragment,
        )
    )


def dither_free_mask(data: bytes, size: tuple[int, int], rotate: int) -> Image.Image:
    """The lettering mask, rotated and resized to match the picture — with
    NEAREST, so a glyph edge stays a glyph edge rather than being smoothed into
    grey and then thresholded into crumbs.

    The order matters and must match `dither_to_panel` exactly: rotate first,
    then resize to the panel. Resizing first and rotating after leaves a 90°
    mask transposed against its picture (1200x1600 over 1600x1200), which
    `stamp_text` then squashed back into place — every caption landing in the
    wrong spot on a portrait-mounted panel."""
    mask = Image.open(io.BytesIO(data)).convert("L")
    if rotate:
        mask = mask.rotate(-rotate, expand=True, resample=Image.NEAREST)
    if mask.size != size:
        mask = mask.resize(size, Image.NEAREST)
    return mask


def stamp_text(picture: Image.Image, mask: Image.Image) -> Image.Image:
    """Stamp the lettering onto an already-dithered picture, in flat black.

    After this, no glyph is dithered: every inked pixel is the panel's own
    black, which is what makes an 8px italic legible at arm's length instead
    of a grey suggestion."""
    stamped = picture.convert("RGB")
    ink = Image.new("RGB", stamped.size, (0, 0, 0))
    mask = mask.convert("L")
    if mask.size != stamped.size:
        # Should not happen — dither_free_mask is handed the same size and
        # rotation as the picture. If it ever does, match with NEAREST: the
        # default resize is bicubic, which greys the glyph edges this whole
        # two-layer dance exists to keep crisp.
        logger.warning(
            "frame: text mask %s doesn't match picture %s; resizing",
            mask.size, stamped.size,
        )
        mask = mask.resize(stamped.size, Image.NEAREST)
    # Threshold, so a soft glyph edge can't paste a part-black pixel: the
    # picture is already quantised to the panel's six colours and a blend would
    # put an off-palette colour on it after the only quantisation step.
    stamped.paste(ink, (0, 0), mask.point(lambda v: 255 if v >= MASK_THRESHOLD else 0))
    return stamped


def refresh_once(
    url: str,
    size: tuple[int, int],
    rotate: int,
    last_hash: str | None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
    panel_factory=load_panel,
    push=_push,
    crisp_text: bool = True,
) -> str | None:
    """One fetch→(maybe)draw cycle. Skips the panel redraw when the image is
    byte-identical to the last one drawn — no point wearing the panel (and
    spending ~30 s) on an unchanged wall. Returns the hash to carry forward;
    on any error it logs and returns `last_hash` unchanged (keep the current
    frame, retry next tick)."""
    try:
        if crisp_text:
            data, text = fetch_layers(url, timeout=timeout, client=client)
        else:
            data, text = fetch_image(url, client=client, timeout=timeout), None
    except Exception:  # noqa: BLE001 — a bad fetch must not kill the loop
        logger.exception("frame: fetch failed; keeping the current image")
        return last_hash
    # Hash both layers: the picture can be unchanged while a caption's clock
    # has moved on, and vice versa.
    digest = hashlib.sha256(data + (text or b"")).hexdigest()
    if digest == last_hash:
        logger.debug("frame: image unchanged; skipping redraw")
        return last_hash
    try:
        image = dither_to_panel(Image.open(io.BytesIO(data)), size, rotate)
        if text is not None:
            # After the dither, never before: the whole point is that these
            # pixels don't get scattered into the 6-colour approximation.
            image = stamp_text(image, dither_free_mask(text, size, rotate))
        panel = panel_factory()
        push(panel, image)
    except Exception:  # noqa: BLE001 — a bad draw must not kill the loop
        logger.exception("frame: draw failed; will retry")
        return last_hash
    logger.info("frame: updated")
    return digest


def stream_url(source: str) -> str:
    """The detection stream that belongs to the wall image we're fetching.

    Derived from BP_FRAME_SOURCE rather than configured separately: they are
    always the same recorder, and two URLs to keep in step is one more thing to
    get wrong when the wall moves."""
    if "//" not in source:  # "host:8537/wall.png" — urlsplit reads that as a path
        source = f"http://{source}"
    parsed = urllib.parse.urlsplit(source)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    # Keep any prefix the wall is served under: a recorder behind a proxy at
    # /birds/wall.png streams at /birds/ws/detections, not at the root.
    prefix = parsed.path.rsplit("/", 1)[0]
    return urllib.parse.urlunsplit(
        (scheme, parsed.netloc, f"{prefix}/ws/detections", "", "")
    )


def watch_for_paintings(
    url: str,
    wake: threading.Event,
    *,
    connect=None,
    retry_seconds: float = STREAM_RETRY_SECONDS,
    reconnect: bool = True,
) -> None:
    """Set `wake` whenever the recorder says it painted a bird.

    Runs on its own thread with a synchronous WebSocket client, so the draw
    loop stays a plain blocking loop — the panel push takes half a minute and
    has no business inside an event loop.

    Every failure here is survivable by design: the frame keeps its own timer,
    so a recorder that's down, older, or unreachable costs latency, not the
    picture."""
    if connect is None:  # imported lazily: the frame runs without it if need be
        try:
            from websockets.sync.client import connect
        except ImportError:
            # The frame installs --no-deps (no BirdNET/TF stack), so this is a
            # real deployment state, not a bug. Say so once, plainly, and leave
            # the frame polling — a stack trace in the journal is not a
            # diagnosis.
            logger.warning(
                "frame: websockets not installed — polling only. "
                "Install it on the frame to redraw the moment a bird is painted."
            )
            return
    complained = False
    while True:
        try:
            with connect(url, open_timeout=10) as socket:
                logger.info("frame: watching %s for painted birds", url)
                for message in socket:
                    # A stream that accepts the upgrade and drops immediately
                    # is still broken; only a stream that SAYS something has
                    # earned the right to complain again if it fails later.
                    complained = False
                    try:
                        event = json.loads(message)
                    except ValueError:
                        continue  # not our business what else the wall says
                    if event.get("type") == "painted":
                        logger.info(
                            "frame: %s painted — refreshing now",
                            event.get("species_common", "a bird"),
                        )
                        wake.set()
        except Exception as exc:  # noqa: BLE001 — a nicety, not a need
            if not complained:
                # Once at INFO, then quiet: an older recorder 404s here forever
                # and would otherwise retry ~2,880 times a day in total silence
                # — which is the exact failure class this feature came from.
                logger.info(
                    "frame: no detection stream at %s (%s); polling every %ds "
                    "instead. Retrying quietly.",
                    url, type(exc).__name__, retry_seconds,
                )
                complained = True
            logger.debug("frame: detection stream unavailable", exc_info=True)
        if not reconnect:
            return
        time.sleep(max(1.0, retry_seconds))  # never busy-spin on a dead stream


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer; using %d", name, raw, default)
        return default


def wait_for_next_draw(
    wake: threading.Event,
    last_redraw_at: float | None,
    *,
    interval: float,
    min_seconds: float,
    now=time.monotonic,
    sleep=time.sleep,
) -> bool:
    """Wait until it's time to redraw. Returns True if a bird caused it.

    A painted bird cuts the wait short — that's the point of the stream — but
    never to less than `min_seconds` after the panel was last REDRAWN. The
    panel takes ~30 s per redraw and wears with each one, so a burst of birds
    settles into ONE redraw showing all of them rather than a queue of redraws
    showing them one at a time.

    `last_redraw_at` is None until the panel has actually been drawn, and only
    advances when it is — not on every fetch. Most polls in a quiet garden find
    an unchanged image and draw nothing; anchoring the floor to those would
    have made a bird wait up to 90 s to protect a panel that had been idle for
    an hour, which is the opposite of what this feature is for."""
    if not wake.wait(timeout=interval):
        # The timer path gets the floor too: BP_FRAME_INTERVAL_SECONDS is a
        # knob, and a short one would otherwise sidestep the only guard the
        # panel has.
        if last_redraw_at is not None:
            settle = min_seconds - (now() - last_redraw_at)
            if settle > 0:
                sleep(settle)
                # Symmetric with the wake path: a bird painted during this
                # settle is already in the image about to be fetched, so its
                # wake-up isn't owed a redraw. Without this it would buy a
                # spurious settle later (harmless — the hash guard absorbs it —
                # but it reads as a bug the next time someone looks).
                wake.clear()
        return False  # the ordinary timer expired
    wake.clear()
    if last_redraw_at is None:
        return True  # nothing drawn yet: nothing to protect
    settle = min_seconds - (now() - last_redraw_at)
    if settle > 0:
        sleep(settle)
        # Birds that arrived while settling are already in the image we're
        # about to fetch, so their wake-ups aren't owed another redraw.
        wake.clear()
    return True


def _bool_env(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S"
    )
    url = os.environ.get("BP_FRAME_SOURCE") or DEFAULT_SOURCE
    interval = _int_env("BP_FRAME_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)
    size = (
        _int_env("BP_FRAME_WIDTH", DEFAULT_SIZE[0]),
        _int_env("BP_FRAME_HEIGHT", DEFAULT_SIZE[1]),
    )
    rotate = _int_env("BP_FRAME_ROTATE", 0) % 360
    timeout = _int_env("BP_FRAME_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    min_seconds = _int_env("BP_FRAME_MIN_SECONDS", DEFAULT_MIN_SECONDS)
    crisp_text = _bool_env("BP_FRAME_CRISP_TEXT", True)

    wake = threading.Event()
    if _bool_env("BP_FRAME_WAKE_ON_PAINT", True):
        threading.Thread(
            target=watch_for_paintings,
            args=(stream_url(url), wake),
            daemon=True,
        ).start()
        logger.info(
            "frame client: %s, on paint (min %ds apart) and every %ds "
            "-> %dx%d panel (rotate %d)",
            url, min_seconds, interval, size[0], size[1], rotate,
        )
    else:
        logger.info(
            "frame client: %s every %ds -> %dx%d panel (rotate %d)",
            url, interval, size[0], size[1], rotate,
        )

    last_hash: str | None = None
    last_redraw_at: float | None = None
    while True:
        before = last_hash
        last_hash = refresh_once(
            url, size, rotate, last_hash, timeout=timeout, crisp_text=crisp_text
        )
        if last_hash != before:
            # refresh_once only returns a new hash when it actually pushed to
            # the panel, so this — not the fetch — is when the panel was worn.
            last_redraw_at = time.monotonic()
        wait_for_next_draw(
            wake, last_redraw_at, interval=interval, min_seconds=min_seconds
        )


if __name__ == "__main__":
    main()
