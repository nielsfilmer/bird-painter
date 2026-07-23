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
                             seconds.
  BP_FRAME_WIDTH/HEIGHT      panel size (default 1600x1200, the Spectra 6).
  BP_FRAME_ROTATE            0|90|180|270 to match the frame's orientation. NB:
                             0/180 preserve the wall's aspect; 90/270 rotate a
                             landscape render onto a fixed landscape panel and
                             so stretch it — for a portrait hang, render
                             portrait instead (set BP_WALL_PNG_WIDTH/HEIGHT on
                             the recorder + BP_FRAME_WIDTH/HEIGHT to match).
  BP_FRAME_TIMEOUT_SECONDS   HTTP fetch timeout (default 30).
  BP_FRAME_DRIVER_PATH       dir to add to sys.path to find the Waveshare
                             driver. The Spectra 6 (E) driver ships as a flat
                             `epd13in3E` module under the panel's own
                             `…/13.3inch_e-Paper_E/RaspberryPi/python/lib`, not
                             in the `waveshare_epd` package; point this there.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import sys
import time

import httpx
from PIL import Image

logger = logging.getLogger(__name__)

DEFAULT_SOURCE = "http://birdrecorder.local:8537/wall.png"
DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_SIZE = (1600, 1200)

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
) -> str | None:
    """One fetch→(maybe)draw cycle. Skips the panel redraw when the image is
    byte-identical to the last one drawn — no point wearing the panel (and
    spending ~30 s) on an unchanged wall. Returns the hash to carry forward;
    on any error it logs and returns `last_hash` unchanged (keep the current
    frame, retry next tick)."""
    try:
        data = fetch_image(url, client=client, timeout=timeout)
    except Exception:  # noqa: BLE001 — a bad fetch must not kill the loop
        logger.exception("frame: fetch failed; keeping the current image")
        return last_hash
    digest = hashlib.sha256(data).hexdigest()
    if digest == last_hash:
        logger.debug("frame: image unchanged; skipping redraw")
        return last_hash
    try:
        image = dither_to_panel(Image.open(io.BytesIO(data)), size, rotate)
        panel = panel_factory()
        push(panel, image)
    except Exception:  # noqa: BLE001 — a bad draw must not kill the loop
        logger.exception("frame: draw failed; will retry")
        return last_hash
    logger.info("frame: updated")
    return digest


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer; using %d", name, raw, default)
        return default


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
    logger.info(
        "frame client: %s every %ds -> %dx%d panel (rotate %d)",
        url, interval, size[0], size[1], rotate,
    )
    last_hash: str | None = None
    while True:
        last_hash = refresh_once(url, size, rotate, last_hash, timeout=timeout)
        time.sleep(interval)


if __name__ == "__main__":
    main()
