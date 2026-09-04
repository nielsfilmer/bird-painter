"""The table model's boot splash — the wall's paper, the wall's serif, one
bird, "waking up…" — as a PNG for plymouth and for the desktop wallpaper.

Drawn as the composition the owner sees (landscape or portrait, per the
stand), then turned to the panel's NATIVE orientation for plymouth, which
paints the raw framebuffer before any compositor rotates anything — which
way is decided by ROTATE below. The wallpaper is shown by the (already
rotated) compositor and stays as seen.

Usage: make_splash.py OUT_DIR [BIRD_IMAGE] [ROTATE] [NATIVE_WxH]
Writes OUT_DIR/splash-desktop.png (the picture as the owner sees it — the
desktop and greeter wallpaper) and OUT_DIR/splash-native.png (the same in
the panel's native buffer orientation, for plymouth). ROTATE is the unit's
`wlr-randr --transform` value from unit.conf: 90 (the default) or 270 for a
landscape stand, 0 or 180 for a portrait one — the composition follows.
NATIVE is the panel's own mode (720x1280 for the 7", 1200x1920 for the
10.1"); the install script reads it from the DSI connector.
wl_output's words for transform 90 are "90 degrees counter-clockwise" — the
transform the compositor applies to a surface — so 90 turns the landscape
image CCW and 270 turns it CW. That reading is the one thing here no test
can check without a camera on the panel: if the first boot shows the splash
upside down, the two branches below are swapped, nothing else.
"""

import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from bird_painter import fonts as fonts_module
from bird_painter.render import INK, INK_DIM, PAPER, _drop_ground, _fit_to_cell

# The 7" panel's native size; the install script passes the real one
# (`cat /sys/class/drm/card*-DSI-*/modes`), so the 10" gets its own pixels.
NATIVE_W, NATIVE_H = 720, 1280


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


def seen_size(native_w: int, native_h: int, rotate: int) -> tuple[int, int]:
    """The size of the picture the owner sees: the panel's native size,
    turned the way the compositor turns it (90/270 = landscape stand)."""
    return (native_h, native_w) if rotate in (90, 270) else (native_w, native_h)


def compose(w: int, h: int, bird_path: Path | None) -> Image.Image:
    """The splash as the owner sees it, at any size: sizes in vmin so the
    7" and the 10" read the same, positions as fractions of the height so a
    portrait sheet keeps the bird near its centre."""
    v = min(w, h) / 100
    img = paper(w, h)
    draw = ImageDraw.Draw(img)
    # The wall's header: eyebrow 2.64vmin italic dim, title 4.17vmin caps
    # tracked 0.22em — index.html's clamps at 1280x720, a little up because a
    # splash is read from further away.
    eyebrow = _font(round(2.64 * v), italic=True)
    draw.text(
        (w / 2, round(0.244 * h)),
        "birds outside",
        font=eyebrow,
        fill=INK_DIM,
        anchor="ma",
    )
    title_px = round(4.17 * v)
    title = _font(title_px)
    tracked(
        draw,
        w / 2,
        round(0.283 * h),
        "HEARD RECENTLY",
        title,
        INK,
        tracking=title_px * 0.22,
    )
    if bird_path and bird_path.exists():
        bird = Image.open(bird_path).convert("RGB")
        cell_w, cell_h = round(36.7 * v), round(45.8 * v)
        fitted = _fit_to_cell(bird, cell_w, cell_h)
        alpha = _drop_ground(fitted)
        x0, y0 = round(w / 2 - cell_w / 2), round(0.364 * h)
        region = img.crop((x0, y0, x0 + cell_w, y0 + cell_h))
        from PIL import ImageChops

        region.paste(ImageChops.multiply(region, fitted), (0, 0), alpha)
        img.paste(region, (x0, y0))
    hint_px = round(2.64 * v)
    hint = _font(hint_px, italic=True)
    tracked(
        draw,
        w / 2,
        h - round(13 * v),
        "waking up…",
        hint,
        INK_DIM,
        tracking=hint_px * 0.12,
    )
    return img


def main(
    out_dir: Path,
    bird_path: Path | None,
    rotate: int = 90,
    native: tuple[int, int] = (NATIVE_W, NATIVE_H),
) -> None:
    w, h = seen_size(*native, rotate)
    img = compose(w, h, bird_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    # The desktop/greeter wallpaper is shown by the (already turned)
    # compositor: the picture as seen. plymouth paints the raw buffer:
    # turned back to native — 90 = CCW, 270 = CW (see the module docstring),
    # 180 = upside down, 0 = as is.
    img.save(out_dir / "splash-desktop.png", "PNG")
    turn = {0: 0, 90: 90, 180: 180, 270: -90}[
        rotate
    ]  # PIL: positive = counter-clockwise
    (img.rotate(turn, expand=True) if turn else img).save(
        out_dir / "splash-native.png", "PNG"
    )
    print("wrote", out_dir / "splash-desktop.png", "and splash-native.png")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: make_splash.py OUT_DIR [BIRD_IMAGE] [ROTATE] [NATIVE_WxH]")
    out = Path(sys.argv[1])
    bird = Path(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else None
    rotate = int(sys.argv[3]) if len(sys.argv) > 3 else 90
    if rotate not in (0, 90, 180, 270):
        sys.exit("ROTATE must be 0, 90, 180 or 270 (the unit's wlr-randr transform)")
    native = (NATIVE_W, NATIVE_H)
    if len(sys.argv) > 4 and sys.argv[4]:
        parts = sys.argv[4].lower().split("x")
        ascii_digits = all(re.fullmatch(r"[0-9]+", p) for p in parts)
        if (
            len(parts) != 2
            or not ascii_digits
            or not all(200 <= int(p) <= 8000 for p in parts)
        ):
            sys.exit("NATIVE must look like 1200x1920 (the panel's own mode)")
        native = (int(parts[0]), int(parts[1]))
    main(out, bird, rotate, native)
