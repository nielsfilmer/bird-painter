"""Server-side raster of the wall collage — `/wall.png` (Phase 4, slice 2).

The e-paper frame can't run the browser wall, so the "heard recently" collage
is rendered to a PNG here and served for a thin client to fetch and push to the
panel. Placement reuses the exact layout maths (`wall_layout`, a port of
`static/layout.js`) so the raster matches the live wall; the paper/ink colours
approximate the wall's CSS. Full-colour output — the panel's own driver dithers
to its 6-colour palette, so we don't pre-quantise here.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

from .wall_layout import PLATE_ASPECT, compute_collage

logger = logging.getLogger(__name__)

# Approximate the wall's aged-cream paper + ink (index.html). The 6-colour
# panel dithers, so exact matching isn't the point — the mood is.
PAPER = (236, 225, 198)
INK = (74, 63, 46)
INK_DIM = (141, 128, 101)
HEARD_INK = (107, 94, 69)

# Serif faces to try, in order, when no font is configured. Raspberry Pi OS /
# Debian first (the deploy target), then macOS (dev). Falls back to Pillow's
# bundled bitmap font if none exist — captions still render, just plainer.
_SERIF = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/Library/Fonts/Georgia.ttf",
]
_SERIF_ITALIC = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
]


def _first_existing(paths: list[str]) -> str | None:
    return next((p for p in paths if Path(p).exists()), None)


class _Fonts:
    """Resolves a regular + italic serif once, then caches loaded sizes."""

    def __init__(self, regular: str | None, italic: str | None):
        self._regular = regular or _first_existing(_SERIF)
        self._italic = italic or _first_existing(_SERIF_ITALIC) or self._regular
        self._cache: dict[tuple[bool, int], ImageFont.FreeTypeFont] = {}
        if self._regular is None:
            logger.warning(
                "render: no serif font found; captions use the default bitmap "
                "font (set BP_WALL_FONT to a .ttf for the intended look)"
            )

    def get(self, size: int, *, italic: bool = False):
        key = (italic, size)
        if key not in self._cache:
            path = self._italic if italic else self._regular
            try:
                self._cache[key] = (
                    ImageFont.truetype(path, size)
                    if path
                    else ImageFont.load_default(size)
                )
            except OSError:
                self._cache[key] = ImageFont.load_default(size)
        return self._cache[key]


def _clamp(lo: float, val: float, hi: float) -> int:
    return round(min(hi, max(lo, val)))


def _tracked(draw, cx, y, text, font, fill, tracking):
    """Draw letter-spaced text horizontally centred at cx, top at y (small-caps
    look for the species: upper-cased + positive tracking)."""
    widths = [font.getlength(ch) for ch in text]
    total = sum(widths) + tracking * max(0, len(text) - 1)
    x = cx - total / 2
    for ch, w in zip(text, widths, strict=True):
        draw.text((x, y), ch, font=font, fill=fill)
        x += w + tracking


def _feather_mask(w: int, h: int) -> Image.Image:
    """Radial ellipse alpha matching the wall's CSS mask: opaque within ~72% of
    the 58%-radius ellipse, fading to transparent by ~96% — so the bird melts
    into the paper with no hard rectangle edge."""
    yy, xx = np.ogrid[0:h, 0:w]
    rx, ry = 0.58 * w, 0.58 * h
    dx = (xx - (w - 1) / 2) / rx
    dy = (yy - (h - 1) / 2) / ry
    d = np.sqrt(dx * dx + dy * dy)
    a = np.clip((0.96 - d) / (0.96 - 0.72), 0.0, 1.0)
    return Image.fromarray((a * 255).astype("uint8"), "L")


def _paste_bird(img, path: Path, cx: float, cy: float, w: int, h: int) -> None:
    if w <= 0 or h <= 0:
        return
    try:
        bird = Image.open(path).convert("RGB").resize((w, h))
    except Exception:  # noqa: BLE001 — SVG placeholders / unreadable files
        # Placeholder mode (no FAL_KEY) writes SVG plates Pillow can't open;
        # a soft grey stand-in keeps the collage populated for tests/QA.
        bird = Image.new("RGB", (w, h), (208, 198, 172))
    mask = _feather_mask(w, h)
    x0, y0 = round(cx - w / 2), round(cy - h / 2)
    region = img.crop((x0, y0, x0 + w, y0 + h))
    region.paste(ImageChops.multiply(region, bird), (0, 0), mask)
    img.paste(region, (x0, y0))


def _heard_text(born_at: float) -> str:
    # Fixed clock time, 24-hour — matches the wall (the e-ink panel refreshes
    # only every few minutes, so a relative "x min ago" would be stale).
    return f"heard at {datetime.fromtimestamp(born_at):%H:%M}"


def _draw_header(draw, width, vmin, fonts) -> float:
    """Draw the title chrome; return the y where the title band ends (band_top),
    the same value the live wall feeds computeCollage."""
    top = 4.5 * vmin
    eyebrow_size = _clamp(12, 1.7 * vmin, 19)
    title_size = _clamp(22, 4.2 * vmin, 52)
    eyebrow = fonts.get(eyebrow_size, italic=True)
    title = fonts.get(title_size)
    draw.text(
        (width / 2, top), "birds outside", font=eyebrow, fill=INK_DIM, anchor="ma"
    )
    title_y = top + eyebrow_size * 1.4
    _tracked(
        draw, width / 2, title_y, "HEARD RECENTLY", title, INK,
        tracking=title_size * 0.22,
    )
    return title_y + title_size * 1.2 + 8


def render_wall_png(
    paintings: list[dict],
    image_dir: Path,
    width: int,
    height: int,
    *,
    font: str | None = None,
    italic_font: str | None = None,
) -> bytes:
    """Render the collage to PNG bytes. `paintings` is newest-first, each a
    dict with `file`, `species_common`, `born_at` (as `/api/live` serves)."""
    fonts = _Fonts(font, italic_font)
    img = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(img)
    header_vmin = min(width, height) / 100
    band_top = _draw_header(draw, width, header_vmin, fonts)

    # Lay the cluster out into a slightly shorter box than the full canvas, so
    # the bottom row's caption clears the panel edge (the cluster's downward
    # offset into the title band would otherwise push the last "heard at …"
    # line a few px past the bottom). The draw uses this same reduced-height
    # vmin, so plate sizes/positions stay consistent with the layout.
    layout_h = height - round(2.2 * header_vmin)
    vmin = min(width, layout_h) / 100

    files = [p["file"] for p in paintings]
    by_file = {p["file"]: p for p in paintings}
    placements = compute_collage(files, width, layout_h, band_top)

    if not placements:
        empty_font = fonts.get(_clamp(16, 2.6 * vmin, 24), italic=True)
        draw.text(
            (width / 2, height / 2), "listening…", font=empty_font,
            fill=INK_DIM, anchor="mm",
        )
        return _encode(img)

    species_size = _clamp(8, 1.05 * vmin, 12)
    heard_size = _clamp(7, 0.85 * vmin, 10)
    species_font = fonts.get(species_size)
    heard_font = fonts.get(heard_size, italic=True)
    cx0, cy0 = width / 2, height / 2

    # Oldest first so the newest bird (highest z) composites on top, as on the
    # wall (z-index).
    for pl in sorted(placements, key=lambda p: p.z):
        w = pl.size_vmin * vmin
        image_h = w * PLATE_ASPECT
        cx, cy = cx0 + pl.x, cy0 + pl.y
        _paste_bird(img, image_dir / pl.file, cx, cy, round(w), round(image_h))
        meta = by_file[pl.file]
        caption_y = cy + image_h / 2 - 0.4 * vmin
        _tracked(
            draw, cx, caption_y, meta["species_common"].upper(),
            species_font, INK, tracking=species_size * 0.05 + 0.5,
        )
        draw.text(
            (cx, caption_y + species_size * 1.25), _heard_text(meta["born_at"]),
            font=heard_font, fill=HEARD_INK, anchor="ma",
        )
    return _encode(img)


def _encode(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()
