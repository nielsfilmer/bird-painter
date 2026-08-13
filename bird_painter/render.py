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
# The e-paper frame's ground. The wall's cream paper is not one of the panel's
# six colours, so dithering scatters it into a permanent red/green speckle
# across the whole panel — noise that also robs the small type of contrast.
# The panel's own white IS in the palette and dithers to nothing, so the frame
# asks for this ground and lets the e-paper be the paper.
PANEL_GROUND = (255, 255, 255)
# FLUX doesn't always paint on pure white: one archived plate's ground is
# (245,245,245), 86% of its pixels. Against the panel's white that shows as a
# grey halo around the bird — invisible on the cream wall, where the multiply
# blend absorbs it, and obvious on e-paper where dithering turns it into a
# patch of speckle. Pixels brighter than WHITE_KEY are dropped entirely;
# between WHITE_KEY and WHITE_SOLID they fade in, so an antialiased feather
# edge stays soft instead of turning into a cut-out.
WHITE_KEY = 246
WHITE_SOLID = 228
# Glyphs in the text-layer mask: white where ink goes, so the frame can paste
# a single colour through it.
MASK_INK = 255
# Faux weight for the species line: a stroke around each glyph. A hairline
# serif at panel sizes reads as grey rather than as a letter, and we have no
# bold cut of this face.
CAPTION_WEIGHT = 1
# The italic "heard at" line gets its weight differently — drawn twice, offset
# one pixel horizontally. A full stroke closes its counters at this size: the
# digits fill in and "heard at" merges into one word, which is less legible
# than the hairline it replaced, not more.
ITALIC_DOUBLE_X = 1

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


def _tracked(draw, cx, y, text, font, fill, tracking, weight=0):
    """Draw letter-spaced text horizontally centred at cx, top at y (small-caps
    look for the species: upper-cased + positive tracking)."""
    widths = [font.getlength(ch) for ch in text]
    total = sum(widths) + tracking * max(0, len(text) - 1)
    x = cx - total / 2
    for ch, w in zip(text, widths, strict=True):
        draw.text((x, y), ch, font=font, fill=fill, stroke_width=weight,
                  stroke_fill=fill)
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


def _drop_ground(bird: Image.Image) -> Image.Image:
    """Alpha that hides the plate's own near-white ground.

    The wall gets this for free from CSS multiply-blending onto cream; a white
    panel has nothing to blend with, so the ground has to be keyed out or it
    haloes."""
    luminance = np.asarray(bird.convert("L"), dtype=np.float32)
    alpha = (WHITE_KEY - luminance) / float(WHITE_KEY - WHITE_SOLID)
    return Image.fromarray((np.clip(alpha, 0.0, 1.0) * 255).astype("uint8"), "L")


def _paste_bird(
    img, path: Path, cx: float, cy: float, w: int, h: int, bare: bool = False
) -> None:
    if w <= 0 or h <= 0:
        return
    try:
        bird = Image.open(path).convert("RGB").resize((w, h))
    except Exception:  # noqa: BLE001 — SVG placeholders / unreadable files
        # Placeholder mode (no FAL_KEY) writes SVG plates Pillow can't open;
        # a soft grey stand-in keeps the collage populated for tests/QA.
        bird = Image.new("RGB", (w, h), (208, 198, 172))
    mask = _feather_mask(w, h)
    if bare:
        # Feather AND ground-key: the edge still melts away, and the plate's
        # own off-white no longer sits on the panel as a grey rectangle.
        mask = ImageChops.multiply(mask, _drop_ground(bird))
    x0, y0 = round(cx - w / 2), round(cy - h / 2)
    region = img.crop((x0, y0, x0 + w, y0 + h))
    region.paste(ImageChops.multiply(region, bird), (0, 0), mask)
    img.paste(region, (x0, y0))


def _heard_text(born_at: float) -> str:
    # Fixed clock time, 24-hour — matches the wall (the e-ink panel refreshes
    # only every few minutes, so a relative "x min ago" would be stale).
    return f"heard at {datetime.fromtimestamp(born_at):%H:%M}"


def _draw_header(
    draw, width, vmin, fonts, lettering=True, ink=INK, dim_ink=INK_DIM
) -> float:
    """Draw the title chrome; return the y where the title band ends (band_top),
    the same value the live wall feeds computeCollage.

    The band's geometry is returned whether or not the lettering is drawn, so
    the picture and text layers place their plates identically."""
    top = 4.5 * vmin
    eyebrow_size = _clamp(12, 1.7 * vmin, 19)
    title_size = _clamp(22, 4.2 * vmin, 52)
    eyebrow = fonts.get(eyebrow_size, italic=True)
    title = fonts.get(title_size)
    title_y = top + eyebrow_size * 1.4
    if lettering:
        draw.text(
            (width / 2, top), "birds outside", font=eyebrow, fill=dim_ink, anchor="ma"
        )
        _tracked(
            draw, width / 2, title_y, "HEARD RECENTLY", title, ink,
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
    layer: str = "all",
    bare: bool = False,
) -> bytes:
    """Render the collage to PNG bytes. `paintings` is newest-first, each a
    dict with `file`, `species_common`, `born_at` (as `/api/live` serves).

    `layer` splits the render for the e-paper frame, which has to dither:

    - `"all"` (default) — the wall as it has always been, for browsers and
      anything else that wants one image.
    - `"picture"` — the same collage with NO lettering.
    - `"text"` — only the lettering, as a mask: white where ink goes.

    `bare` drops the cream paper for plain white — the e-paper's own ground.
    Cream isn't one of the panel's six colours, so it dithers into a red/green
    speckle over every pixel; white is, and costs nothing.

    The frame dithers the picture and then stamps the text through the mask in
    pure panel black. Dithering scatters a 6-colour approximation across every
    pixel, which is fine for a watercolour and ruinous for an 8px italic — the
    caption came out as speckle. Splitting the layers is what lets the type
    stay type. Both layers come from THIS function, so the two can't drift out
    of alignment the way a second layout implementation would."""
    if layer not in {"all", "picture", "text"}:
        raise ValueError(f"layer must be all/picture/text, got {layer!r}")
    text_only = layer == "text"
    lettering = layer != "picture"

    fonts = _Fonts(font, italic_font)
    # The text layer is a mask: black ground, white glyphs, so the frame can
    # paste one colour through it.
    if text_only:
        img = Image.new("L", (width, height), 0)
    else:
        img = Image.new("RGB", (width, height), PANEL_GROUND if bare else PAPER)
    draw = ImageDraw.Draw(img)
    ink = MASK_INK if text_only else INK
    dim_ink = MASK_INK if text_only else INK_DIM
    heard_ink = MASK_INK if text_only else HEARD_INK
    header_vmin = min(width, height) / 100
    band_top = _draw_header(draw, width, header_vmin, fonts, lettering, ink, dim_ink)

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
        if lettering:
            draw.text(
                (width / 2, height / 2), "listening…", font=empty_font,
                fill=dim_ink, anchor="mm",
            )
        return _encode(img)

    species_size = _clamp(9, 1.15 * vmin, 14)
    heard_size = _clamp(11, 1.3 * vmin, 16)
    species_font = fonts.get(species_size)
    heard_font = fonts.get(heard_size, italic=True)
    cx0, cy0 = width / 2, height / 2

    # Oldest first so the newest bird (highest z) composites on top, as on the
    # wall (z-index).
    for pl in sorted(placements, key=lambda p: p.z):
        w = pl.size_vmin * vmin
        image_h = w * PLATE_ASPECT
        cx, cy = cx0 + pl.x, cy0 + pl.y
        if not text_only:
            _paste_bird(
                img, image_dir / pl.file, cx, cy, round(w), round(image_h), bare=bare
            )
        if not lettering:
            continue
        meta = by_file[pl.file]
        caption_y = cy + image_h / 2 - 0.4 * vmin
        _tracked(
            draw, cx, caption_y, meta["species_common"].upper(),
            species_font, ink, tracking=species_size * 0.05 + 0.5,
            weight=CAPTION_WEIGHT,
        )
        heard_y = caption_y + species_size * 1.3
        heard = _heard_text(meta["born_at"])
        for dx in (0, ITALIC_DOUBLE_X):
            draw.text(
                (cx + dx, heard_y), heard, font=heard_font, fill=heard_ink,
                anchor="ma",
            )
    return _encode(img)


def _encode(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()
