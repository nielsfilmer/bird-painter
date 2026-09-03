"""The table model's boot splash — the wall's paper, the wall's serif, one
bird, "waking up…" — as a PNG for plymouth and for the desktop wallpaper.

Drawn as the landscape composition the owner sees, then rotated to the
panel's NATIVE portrait orientation for plymouth, which paints the raw
framebuffer before any compositor rotates anything. `wlr-randr --transform
90` is wlroots' "rotate 90° counter-clockwise" of the logical image into the
native buffer, so the native splash is the landscape one rotated CCW. The
wallpaper is shown by the (already rotated) compositor and stays landscape.

Usage: make_splash.py OUT_DIR [BIRD_IMAGE] [ROTATE]
Writes OUT_DIR/splash-landscape.png (1280x720) and OUT_DIR/splash-native.png
(720x1280). ROTATE is the unit's `wlr-randr --transform` value from
unit.conf (90, the default, or 270): the native image is the landscape one
turned the way the compositor turns the desktop into the panel's buffer.
wl_output's words for transform 90 are "90 degrees counter-clockwise" — the
transform the compositor applies to a surface — so 90 turns the landscape
image CCW and 270 turns it CW. That reading is the one thing here no test
can check without a camera on the panel: if the first boot shows the splash
upside down, the two branches below are swapped, nothing else.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from bird_painter import fonts as fonts_module
from bird_painter.render import INK, INK_DIM, PAPER, _drop_ground, _fit_to_cell

W, H = 1280, 720


def _font(size, italic=False):
    path = fonts_module.first_existing(
        fonts_module.SERIF_ITALIC if italic else fonts_module.SERIF
    )
    return ImageFont.truetype(path, size) if path else ImageFont.load_default(size)


def paper(w, h):
    """The wall's cream — its centre-to-edge gradient, without the CSS noise
    (plymouth's dithering would eat it anyway)."""
    img = Image.new("RGB", (w, h), PAPER)
    px = img.load()
    inner, mid, edge = (244, 237, 218), (236, 225, 198), (221, 205, 166)
    cx, cy = w / 2, h / 2
    rmax = (cx * cx + cy * cy) ** 0.5
    for y in range(h):
        for x in range(w):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / rmax
            if d < 0.62:
                t = d / 0.62
                c = tuple(round(inner[i] + (mid[i] - inner[i]) * t) for i in range(3))
            else:
                t = min(1.0, (d - 0.62) / 0.38)
                c = tuple(round(mid[i] + (edge[i] - mid[i]) * t) for i in range(3))
            px[x, y] = c
    return img


def tracked(draw, cx, y, text, font, fill, tracking):
    widths = [font.getlength(ch) for ch in text]
    total = sum(widths) + tracking * max(0, len(text) - 1)
    x = cx - total / 2
    for ch, w in zip(text, widths, strict=True):
        draw.text((x, y), ch, font=font, fill=fill)
        x += w + tracking


def main(out_dir: Path, bird_path: Path | None, rotate: int = 90) -> None:
    img = paper(W, H)
    draw = ImageDraw.Draw(img)
    # The wall's header at this size: eyebrow 12px italic dim, title 30px caps
    # tracked 0.22em — the same numbers index.html's clamps land on at
    # 1280x720, scaled a little up because a splash is read from further away.
    eyebrow = _font(19, italic=True)
    draw.text((W / 2, 176), "birds outside", font=eyebrow, fill=INK_DIM, anchor="ma")
    title = _font(30)
    tracked(draw, W / 2, 204, "HEARD RECENTLY", title, INK, tracking=30 * 0.22)
    if bird_path and bird_path.exists():
        bird = Image.open(bird_path).convert("RGB")
        cell_w, cell_h = 264, 330
        fitted = _fit_to_cell(bird, cell_w, cell_h)
        alpha = _drop_ground(fitted)
        x0, y0 = round(W / 2 - cell_w / 2), 262
        region = img.crop((x0, y0, x0 + cell_w, y0 + cell_h))
        from PIL import ImageChops

        region.paste(ImageChops.multiply(region, fitted), (0, 0), alpha)
        img.paste(region, (x0, y0))
    hint = _font(19, italic=True)
    tracked(draw, W / 2, H - 84 - 10, "waking up…", hint, INK_DIM, tracking=19 * 0.12)

    out_dir.mkdir(parents=True, exist_ok=True)
    img.save(out_dir / "splash-landscape.png", "PNG")
    turn = 90 if rotate == 90 else -90  # PIL: positive = counter-clockwise
    img.rotate(turn, expand=True).save(out_dir / "splash-native.png", "PNG")
    print("wrote", out_dir / "splash-landscape.png", "and splash-native.png")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: make_splash.py OUT_DIR [BIRD_IMAGE] [ROTATE]")
    out = Path(sys.argv[1])
    bird = Path(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else None
    rotate = int(sys.argv[3]) if len(sys.argv) > 3 else 90
    if rotate not in (90, 270):
        sys.exit("ROTATE must be 90 or 270 (a landscape stand for a portrait panel)")
    main(out, bird, rotate)
