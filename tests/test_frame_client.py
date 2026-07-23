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


def test_load_panel_prefers_the_flat_driver_module(monkeypatch):
    # The Spectra 6 driver is a flat `epd13in3E` module (not the waveshare_epd
    # package). load_panel must import it and Init() the panel.
    import sys
    import types

    inits = []
    fake = types.ModuleType("epd13in3E")

    class EPD:
        def Init(self):
            inits.append(True)

    fake.EPD = EPD
    monkeypatch.setitem(sys.modules, "epd13in3E", fake)
    monkeypatch.setenv("BP_FRAME_DRIVER_PATH", "/some/driver/lib")
    epd = fc.load_panel()
    assert isinstance(epd, EPD)
    assert inits == [True]
    assert "/some/driver/lib" in sys.path


def test_importing_frame_client_needs_no_hardware_driver():
    # The module must import without the Waveshare lib (it's a manual install on
    # the frame Pi); the driver import lives inside load_panel, called at run.
    import importlib

    importlib.reload(fc)
    assert hasattr(fc, "refresh_once")
