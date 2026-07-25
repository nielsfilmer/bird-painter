"""The painting trimmer (`bird_painter/trim.py`): crops flat-white margins so
the bird fills its plate, pads to the 4:5 plate aspect, never loses an image."""

import io

from PIL import Image

from bird_painter.trim import PLATE_ASPECT, trim_to_bird


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _bird_on_white(canvas=(1000, 1000), bird_box=(400, 350, 600, 650)) -> bytes:
    # A dark "bird" rectangle on a big flat-white canvas — lots of margin.
    img = Image.new("RGB", canvas, (255, 255, 255))
    x0, y0, x1, y1 = bird_box
    for x in range(x0, x1):
        for y in range(y0, y1):
            img.putpixel((x, y), (60, 50, 40))
    return _png(img)


def test_trim_crops_margin_and_keeps_plate_aspect():
    out = trim_to_bird(_bird_on_white(), "png")
    img = Image.open(io.BytesIO(out))
    # Much smaller than the 1000x1000 canvas (margin gone)…
    assert img.width < 500 and img.height < 600
    # …at the plate's 4:5 aspect (so cover-fitting never cuts the bird).
    assert abs((img.height / img.width) - PLATE_ASPECT) < 0.02
    # The bird still fully present: dark pixels survive.
    assert min(img.convert("L").getextrema()) < 100


def test_trim_result_fills_most_of_the_frame():
    out = trim_to_bird(_bird_on_white(), "png")
    img = Image.open(io.BytesIO(out)).convert("L")
    import numpy as np

    content = (np.asarray(img) < 242).mean()
    # The bird occupied 4% of the original canvas; after the trim it should
    # dominate the frame.
    assert content > 0.3


def test_all_white_image_unchanged():
    data = _png(Image.new("RGB", (400, 500), (255, 255, 255)))
    assert trim_to_bird(data, "png") == data


def test_busy_image_unchanged():
    # Nearly no white margin (e.g. a full-bleed painting): nothing to trim.
    img = Image.new("RGB", (400, 500), (120, 100, 80))
    data = _png(img)
    assert trim_to_bird(data, "png") == data


def test_unparseable_svg_unchanged():
    data = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    assert trim_to_bird(data, "svg") == data


def test_jpeg_roundtrip_stays_jpeg():
    img = Image.new("RGB", (800, 800), (255, 255, 255))
    for x in range(300, 500):
        for y in range(250, 550):
            img.putpixel((x, y), (50, 60, 70))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    out = trim_to_bird(buf.getvalue(), "jpg")
    assert Image.open(io.BytesIO(out)).format == "JPEG"
