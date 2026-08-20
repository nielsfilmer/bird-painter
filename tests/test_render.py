"""The /wall.png renderer (`bird_painter/render.py`): valid PNG of the
configured size, populated + empty, and resilient to unreadable plate images
(the SVG placeholder plates it can't decode)."""

import io
from pathlib import Path

from PIL import Image

from bird_painter.render import render_wall_png


def _make_image(path: Path, color=(120, 90, 60)):
    Image.new("RGB", (256, 320), color).save(path, "PNG")


def _open(png: bytes) -> Image.Image:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    return Image.open(io.BytesIO(png))


def test_render_is_png_of_configured_size(tmp_path):
    for i in range(3):
        _make_image(tmp_path / f"bird{i}.png")
    paintings = [
        {
            "file": f"bird{i}.png",
            "species_common": f"Test Bird {i}",
            "born_at": 1784570000 + i,
        }
        for i in range(3)
    ]
    png = render_wall_png(paintings, tmp_path, 640, 480)
    assert _open(png).size == (640, 480)


def test_render_empty_still_valid_png(tmp_path):
    png = render_wall_png([], tmp_path, 400, 300)
    assert _open(png).size == (400, 300)


def test_render_survives_unreadable_image(tmp_path):
    # Placeholder mode writes SVG plates Pillow can't open; the render must not
    # crash — it draws a neutral stand-in instead.
    (tmp_path / "plate.svg").write_text("<svg/>")
    paintings = [
        {"file": "plate.svg", "species_common": "Song Thrush", "born_at": 1784570000}
    ]
    png = render_wall_png(paintings, tmp_path, 500, 400)
    assert _open(png).size == (500, 400)


def test_render_missing_file_does_not_crash(tmp_path):
    paintings = [
        {"file": "gone.jpg", "species_common": "Ghost Bird", "born_at": 1784570000}
    ]
    png = render_wall_png(paintings, tmp_path, 500, 400)
    assert _open(png).size == (500, 400)


def test_render_falls_back_when_no_serif_font_exists(tmp_path, monkeypatch):
    # On a host without any of the candidate serif faces, captions must fall
    # back to Pillow's bundled font rather than crash. Force that by emptying
    # the candidate lists (so discovery finds nothing).
    from bird_painter import render

    monkeypatch.setattr(render, "_SERIF", [])
    monkeypatch.setattr(render, "_SERIF_ITALIC", [])
    _make_image(tmp_path / "b.png")
    paintings = [{"file": "b.png", "species_common": "Wren", "born_at": 1784570000}]
    png = render_wall_png(paintings, tmp_path, 500, 400)
    assert _open(png).size == (500, 400)


def test_ink_bounds_keep_a_thin_tail_but_drop_a_far_speck():
    """Regression for a cut-off jackdaw: a pixel-percentile box sliced off the
    thin head and tail (real ink, few pixels per row). Bounds now come from
    connected components — extremities are attached to the bird, specks
    aren't."""
    import numpy as np

    from bird_painter.render import _ink_bounds

    mask = np.zeros((200, 200), dtype=bool)
    mask[80:120, 60:120] = True  # the body
    mask[100:101, 120:170] = True  # a one-pixel-thick tail, attached
    mask[82:84, 55:60] = True  # the head's crown, attached
    mask[10:12, 190:192] = True  # an isolated speck, far away
    top, left, bottom, right = _ink_bounds(mask)
    assert right >= 170, "the tail tip must survive"
    assert left <= 55, "the crown must survive"
    assert top >= 80 and bottom <= 121, "the far speck must not stretch the box"


PLATES = Path(__file__).parent / "fixtures" / "plates"


def test_a_grey_field_inside_a_white_border_is_read_as_ground():
    """A real archived plate FLUX painted on light grey inside a white border.
    The border's median says 255, so nothing was keyed out and the plate landed
    on the white panel as a grey rectangle with a small bird in it — the newest
    bird at that, looking like the smallest (owner, 2026-08-20)."""
    import numpy as np
    from PIL import Image

    from bird_painter.render import _ground_level, _ink_key

    pixels = np.asarray(Image.open(PLATES / "grey-field-gull.jpg").convert("L"))
    assert _ground_level(pixels) < 246, "the grey field is the ground, not the border"
    assert (pixels < _ink_key(pixels)).mean() < 0.25, "the field is not the bird"


def test_a_pale_bird_on_white_keeps_its_body():
    """The counterweight, and a real regression: a rule that took the darkest
    well-covered light level instead of the most common one read this
    hawfinch's breast as ground and rendered it as a hollow shell."""
    import numpy as np
    from PIL import Image

    from bird_painter.render import _ground_level, _ink_key

    pixels = np.asarray(Image.open(PLATES / "pale-bird-hawfinch.jpg").convert("L"))
    assert _ground_level(pixels) >= 246, "a white plate's ground is white"
    ink = pixels < _ink_key(pixels)
    assert ink.mean() > 0.15, "the pale breast is part of the bird"


def test_the_browser_wall_captions_still_fit_the_room_the_layout_reserves():
    """The panel's caption sizes were hoisted above the `if panel:` branch and
    silently applied to the browser wall too — species 12→14, "heard at"
    10→15, plus a faux-bold double-draw. `wall_layout` reserves room for the
    OLD sizes (CAPTION_FLOOR_PX), so wall captions ran into the bird below
    (review, 2026-08-20). The panel keeps its larger type; the wall keeps its
    own."""
    from bird_painter.render import _clamp
    from bird_painter.wall_layout import CAPTION_FLOOR_PX

    for width, height in ((1600, 1200), (1280, 800), (900, 1600)):
        vmin = min(width, height) / 100
        species = _clamp(8, 1.05 * vmin, 12)
        heard = _clamp(7, 0.85 * vmin, 10)
        # Where the second line is drawn, plus its own height.
        block = species * 1.25 + heard
        assert block <= CAPTION_FLOOR_PX, (
            f"{width}x{height}: a {block:.0f}px caption block doesn't fit the "
            f"{CAPTION_FLOOR_PX}px wall_layout reserves"
        )
        panel_species = _clamp(9, 1.15 * vmin, 14)
        assert panel_species > species, "the panel's type is its own, and bigger"
