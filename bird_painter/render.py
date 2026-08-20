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

from .frame_layout import compute_frame_scatter
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
# How far below its own ground a pixel must sit to count as the bird. Small
# enough to catch a pale wing edge, large enough that JPEG noise in the ground
# isn't mistaken for a feather.
GROUND_TOLERANCE = 6
# A connected ink component smaller than this share of the total ink is a
# speck (JPEG noise, a stray droplet), not part of the bird.
SPECK_SHARE = 0.004
# Finding a plate's ground (see _ground_level): a ground is light, and smears
# over a few adjacent levels once JPEG has been at it.
GROUND_MIN_LUM = 200
GROUND_SMOOTH = 5
# Air between a bird's feet and its name, on the panel (in vmin).
CAPTION_GAP_VMIN = 1.4
# The panel's top margin, once the title is gone.
PANEL_TOP_MARGIN = 0.045
# Glyphs in the text-layer mask: white where ink goes, so the frame can paste
# a single colour through it.
MASK_INK = 255
# What render_wall_png accepts. `web.WALL_LAYERS`/`WALL_STYLES` mirror these
# for the HTTP layer and a test pins the two together.
LAYERS = ("all", "picture", "text")
STYLES = ("wall", "panel")
# Faux weight, for a face we have no bold cut of: draw the glyph twice, offset
# one pixel horizontally. A hairline serif at panel sizes reads as grey rather
# than as a letter, but a full stroke around each glyph is too much — on the
# italic it closes the counters (the digits fill in, "heard at" merges into one
# word), and on the species caps it read as shouty on the real panel. One pixel
# of doubling is the half-step that both lines wanted.
DOUBLE_X = 1

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


def _feather_mask(w: int, h: int, soft: bool = True) -> Image.Image:
    """Radial ellipse alpha matching the wall's CSS mask: opaque within ~72% of
    the 58%-radius ellipse, fading to transparent by ~96% — so the bird melts
    into the paper with no hard rectangle edge.

    `soft=False` widens it for the frame, where the bird's ink is fitted to the
    cell rather than floating in a padded plate: the wall's ellipse would clip
    a heron's beak and feet. The ground key-out is what hides the plate edge
    there, so this only needs to catch stray corners."""
    yy, xx = np.ogrid[0:h, 0:w]
    rx, ry = (0.58 if soft else 0.80) * w, (0.58 if soft else 0.80) * h
    dx = (xx - (w - 1) / 2) / rx
    dy = (yy - (h - 1) / 2) / ry
    d = np.sqrt(dx * dx + dy * dy)
    a = np.clip((0.96 - d) / (0.96 - 0.72), 0.0, 1.0)
    return Image.fromarray((a * 255).astype("uint8"), "L")


def _caption_sizes(vmin: float, panel: bool) -> tuple[float, float]:
    """Species and "heard at" type sizes, in pixels.

    The panel's are its own: it is read from across a room, so its type is
    larger and the "heard at" line is deliberately the bigger of the two
    (owner, 2026-08-07). The browser wall is read at desk distance and keeps
    exactly the type it always had — when the panel's sizes were briefly
    applied to both, the wall's timestamp grew by half and its captions ran
    into the bird below, because `wall_layout` reserves room for these sizes.

    One function so a test can pin both sets, rather than re-deriving them and
    passing whatever the code happens to do."""
    if panel:
        return _clamp(9, 1.15 * vmin, 14), _clamp(11, 1.3 * vmin, 16)
    return _clamp(8, 1.05 * vmin, 12), _clamp(7, 0.85 * vmin, 10)


def _ground_level(luminance: np.ndarray) -> float:
    """This plate's own ground brightness, as a luminance.

    The border's median was the first rule and it has one bad case, which the
    owner spotted on the panel (2026-08-20): FLUX sometimes paints a light-grey
    field inside a white outer border. The border then reads 255, nothing is
    keyed out, and the plate lands on the white panel as a grey rectangle with
    a small bird in it — the newest bird, at that, looking like the smallest.

    So instead of asking the border, ask the whole plate: the ground is its
    most common LIGHT level. That is what a ground is — the one tone covering
    more of the sheet than anything else. It picks the grey field on the four
    archived plates that have one, and keying everything brighter than it
    removes the white border along with it.

    Two rules were tried before this one. A fixed 246 misses any plate whose
    ground is darker. "The darkest level covering >2% of the plate" catches
    those, but also catches a hawfinch's breast, an owl's chest and a collared
    dove's entire body — pale birds came out as hollow shells, because a big
    pale bird IS a large light region; only "most common" separates the two,
    and it does so on all 200 archived plates."""
    counts = np.bincount(
        np.clip(luminance, 0, 255).astype(np.uint8).ravel(), minlength=256
    ).astype(np.float64)
    # A flat field lands on a few adjacent levels once JPEG has been at it.
    spread = np.convolve(counts, np.ones(GROUND_SMOOTH), mode="same")
    spread[:GROUND_MIN_LUM] = 0.0  # a ground is light, by house style
    if not spread.any():
        # No light pixels at all: a night scene, a dark drift, an unreadable
        # plate. There is no ground to key out, so claim white and key nothing
        # — the plate renders whole. Returning a dark level instead (the
        # border's median, as this once did) sets the key below every pixel on
        # the plate and dissolves the bird completely.
        return 255.0
    return float(spread.argmax())


def _ink_key(luminance: np.ndarray) -> float:
    """Below this luminance a pixel is the bird, not the plate's ground."""
    return min(WHITE_KEY, _ground_level(luminance) - GROUND_TOLERANCE)


def _drop_ground(bird: Image.Image) -> Image.Image:
    """Alpha that hides the plate's own near-white ground.

    The wall gets this for free from CSS multiply-blending onto cream; a white
    panel has nothing to blend with, so the ground has to be keyed out or it
    haloes."""
    luminance = np.asarray(bird.convert("L"), dtype=np.float32)
    key = _ink_key(luminance) + 1
    solid = key - (WHITE_KEY - WHITE_SOLID)
    alpha = (key - luminance) / max(key - solid, 1.0)
    return Image.fromarray((np.clip(alpha, 0.0, 1.0) * 255).astype("uint8"), "L")


def _ink_bounds(mask: np.ndarray) -> tuple[int, int, int, int]:
    """The ink's bounding box, ignoring isolated specks but keeping every
    attached extremity.

    A pixel-percentile box was tried first and cut off a jackdaw's head and
    tail: thin extremities are real ink but only a few pixels per row, so a
    percentile can't tell them from noise. Connectedness can — a speck is a
    tiny component of its own, a tail tip belongs to the bird's component.
    Tiny components are dropped, and the box is the full extent of what
    remains."""
    from scipy import ndimage

    labels, count = ndimage.label(mask)
    if count > 1:
        sizes = np.bincount(labels.ravel())
        sizes[0] = 0
        keep = sizes >= max(16, int(mask.sum() * SPECK_SHARE))
        if keep.any():
            mask = keep[labels]
    inked = np.argwhere(mask)
    (top, left), (bottom, right) = inked.min(0), inked.max(0) + 1
    return int(top), int(left), int(bottom), int(right)


def _fit_to_cell(bird: Image.Image, w: int, h: int) -> Image.Image:
    """Scale the bird's OWN ink to fill the cell, on a white field.

    Plates are padded to 4:5 at store time, so a heron carries wide empty
    margins and a plump owl doesn't — meaning identical cells render birds at
    visibly different sizes, and thin birds look far away. Cropping to the ink
    first makes every bird as large as its cell allows, whatever its shape."""
    pixels = np.asarray(bird.convert("L"))
    # The threshold has to follow the plate's OWN ground, not a fixed number:
    # FLUX paints some plates on 245-grey, and against a fixed 246 every pixel
    # counts as ink, so the crop does nothing and that bird renders visibly
    # smaller than its neighbours.
    mask = pixels < _ink_key(pixels)
    if not mask.any():
        return bird.resize((w, h))
    top, left, bottom, right = _ink_bounds(mask)
    cropped = bird.crop((left, top, right, bottom))
    scale = min(w / cropped.width, h / cropped.height)
    sized = cropped.resize(
        (max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale)))
    )
    cell = Image.new("RGB", (w, h), PANEL_GROUND)
    cell.paste(sized, ((w - sized.width) // 2, (h - sized.height) // 2))
    return cell


def _paste_bird(
    img, path: Path, cx: float, cy: float, w: int, h: int, bare: bool = False
) -> None:
    if w <= 0 or h <= 0:
        return
    try:
        bird = Image.open(path).convert("RGB")
        bird = _fit_to_cell(bird, w, h) if bare else bird.resize((w, h))
    except Exception:  # noqa: BLE001 — SVG placeholders / unreadable files
        # Placeholder mode (no FAL_KEY) writes SVG plates Pillow can't open;
        # a soft grey stand-in keeps the collage populated for tests/QA.
        #
        # It has to be an INSET shape on white, not a flat fill: on the panel
        # the ground key-out reads a plate's border as its ground, so a flat
        # grey rectangle keys itself away entirely and leaves a caption
        # floating under nothing.
        bird = Image.new("RGB", (w, h), PANEL_GROUND if bare else (208, 198, 172))
        if bare:
            inset = ImageDraw.Draw(bird)
            inset.rounded_rectangle(
                (w * 0.18, h * 0.18, w * 0.82, h * 0.82),
                radius=max(2, round(w * 0.06)),
                fill=(208, 198, 172),
            )
    mask = _feather_mask(w, h, soft=not bare)
    if bare:
        # Feather AND ground-key: the edge still melts away, and the plate's
        # own off-white no longer sits on the panel as a grey rectangle.
        mask = ImageChops.multiply(mask, _drop_ground(bird))
    x0, y0 = round(cx - w / 2), round(cy - h / 2)
    region = img.crop((x0, y0, x0 + w, y0 + h))
    region.paste(ImageChops.multiply(region, bird), (0, 0), mask)
    img.paste(region, (x0, y0))


def _ink_aspect(path: Path) -> float:
    """The bird's own ink aspect (height/width), measured against the plate's
    own ground — same rule as _fit_to_cell, so the cell the layout shapes is
    the cell the paste will fill."""
    try:
        image = Image.open(path).convert("L")
        image.thumbnail((256, 256))
        pixels = np.asarray(image)
        mask = pixels < _ink_key(pixels)
        if not mask.any():
            return PLATE_ASPECT
        top, left, bottom, right = _ink_bounds(mask)
        if right - left < 4 or bottom - top < 4:
            return PLATE_ASPECT
        return (bottom - top) / (right - left)
    except Exception:  # noqa: BLE001 — unreadable file: the 4:5 default
        return PLATE_ASPECT


def _caption_width(draw, meta, species_font, heard_font, tracking) -> float:
    """The widest of a bird's two caption lines, plus the doubling pixel —
    the layout uses it as a floor on the footprint so fixed-size lettering
    can't collide however small its bird is."""
    name = meta["species_common"].upper()
    name_w = sum(draw.textlength(c, font=species_font) + tracking for c in name)
    heard_w = draw.textlength(_heard_text(meta["born_at"]), font=heard_font)
    return max(name_w - tracking, heard_w) + DOUBLE_X + 6


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
    style: str = "wall",
) -> bytes:
    """Render the collage to PNG bytes. `paintings` is newest-first, each a
    dict with `file`, `species_common`, `born_at` (as `/api/live` serves).

    `layer` splits the render for the e-paper frame, which has to dither:

    - `"all"` (default) — the wall as it has always been, for browsers and
      anything else that wants one image.
    - `"picture"` — the same collage with NO lettering.
    - `"text"` — only the lettering, as a mask: white where ink goes.

    `style="panel"` drops the cream paper for plain white — the e-paper's own
    ground. Cream isn't one of the panel's six colours, so it dithers into a
    red/green speckle over every pixel; white is, and costs nothing. It also
    swaps the spiral for the focal scatter and fits each bird to its own ink.

    The frame dithers the picture and then stamps the text through the mask in
    pure panel black. Dithering scatters a 6-colour approximation across every
    pixel, which is fine for a watercolour and ruinous for an 8px italic — the
    caption came out as speckle. Splitting the layers is what lets the type
    stay type. Both layers come from THIS function, so the two can't drift out
    of alignment the way a second layout implementation would."""
    if layer not in LAYERS:
        raise ValueError(f"layer must be one of {LAYERS}, got {layer!r}")
    if style not in STYLES:
        raise ValueError(f"style must be one of {STYLES}, got {style!r}")
    # "panel" is everything the e-paper frame needs and the browser doesn't:
    # its own white as the ground, the focal scatter instead of the spiral,
    # birds fitted to their cells, and no title — the frame hangs on a wall,
    # where a heading saying what it is costs a row of birds to state the
    # obvious.
    panel = style == "panel"
    text_only = layer == "text"
    lettering = layer != "picture"

    fonts = _Fonts(font, italic_font)
    # The text layer is a mask: black ground, white glyphs, so the frame can
    # paste one colour through it.
    if text_only:
        img = Image.new("L", (width, height), 0)
    else:
        img = Image.new("RGB", (width, height), PANEL_GROUND if panel else PAPER)
    draw = ImageDraw.Draw(img)
    ink = MASK_INK if text_only else INK
    dim_ink = MASK_INK if text_only else INK_DIM
    heard_ink = MASK_INK if text_only else HEARD_INK
    header_vmin = min(width, height) / 100
    if panel:
        # No header: the sheet is the header. This is the single biggest gain
        # in room for the birds.
        band_top = height * PANEL_TOP_MARGIN
    else:
        band_top = _draw_header(
            draw, width, header_vmin, fonts, lettering, ink, dim_ink
        )

    # Lay the cluster out into a slightly shorter box than the full canvas, so
    # the bottom row's caption clears the panel edge (the cluster's downward
    # offset into the title band would otherwise push the last "heard at …"
    # line a few px past the bottom). The draw uses this same reduced-height
    # vmin, so plate sizes/positions stay consistent with the layout.
    layout_h = height - round(2.2 * header_vmin)
    vmin = min(width, layout_h) / 100

    files = [p["file"] for p in paintings]
    by_file = {p["file"]: p for p in paintings}
    # Caption typography is fixed-size (owner: don't resize the text) and the
    # panel layout needs to MEASURE it before placing anything, so it's
    # defined before the layout rather than after. Sizes: see _caption_sizes.
    species_size, heard_size = _caption_sizes(vmin, panel)
    species_font = fonts.get(species_size)
    heard_font = fonts.get(heard_size, italic=True)
    # The panel is a fixed sheet seen from across a room, so it gets the focal
    # scatter that fills it (see frame_layout) rather than the browser wall's
    # spiral, which is built to reflow in a window. `style="wall"` renders the
    # spiral — what the README's hero image and any browser expects.
    if panel:
        tracking = species_size * 0.05 + 0.5
        placements = compute_frame_scatter(
            files,
            width,
            layout_h,
            band_top,
            aspects=[_ink_aspect(image_dir / f) for f in files],
            # Two fixed-size caption lines plus the air above them.
            caption_px=(
                CAPTION_GAP_VMIN * vmin + species_size * 1.3 + heard_size * 1.25
            ),
            caption_widths=[
                _caption_width(
                    draw, by_file[f], species_font, heard_font, tracking
                )
                for f in files
            ],
        )
    else:
        placements = compute_collage(files, width, layout_h, band_top)

    if not placements:
        empty_font = fonts.get(_clamp(16, 2.6 * vmin, 24), italic=True)
        if lettering:
            draw.text(
                (width / 2, height / 2), "listening…", font=empty_font,
                fill=dim_ink, anchor="mm",
            )
        return _encode(img)

    cx0, cy0 = width / 2, height / 2

    # Oldest first so the newest bird (highest z) composites on top, as on the
    # wall (z-index).
    for pl in sorted(placements, key=lambda p: p.z):
        w = pl.size_vmin * vmin
        # The frame's cells carry their own height (see frame_layout); the
        # wall's spiral plates are always 4:5.
        image_h = (getattr(pl, "height_vmin", 0.0) or 0.0) * vmin or w * PLATE_ASPECT
        cx, cy = cx0 + pl.x, cy0 + pl.y
        if not text_only:
            _paste_bird(
                img, image_dir / pl.file, cx, cy, round(w), round(image_h), bare=panel
            )
        if not lettering:
            continue
        meta = by_file[pl.file]
        # On the panel the bird's ink is fitted to the cell, so its feet ARE
        # the cell's bottom edge; the wall's slight overlap put the name right
        # against the bird. Give it air there, keep the overlap on the wall
        # where the plate has its own white margin.
        gap = CAPTION_GAP_VMIN * vmin if panel else -0.4 * vmin
        caption_y = cy + image_h / 2 + gap
        # The faux weight is the panel's too: e-paper renders a hairline serif
        # thinly, so each line is drawn twice a pixel apart. On a backlit screen
        # that just looks smudged, and the wall never asked for it.
        offsets = (0, DOUBLE_X) if panel else (0,)
        for dx in offsets:
            _tracked(
                draw, cx + dx, caption_y, meta["species_common"].upper(),
                species_font, ink, tracking=species_size * 0.05 + 0.5,
            )
        heard_y = caption_y + species_size * (1.3 if panel else 1.25)
        heard = _heard_text(meta["born_at"])
        for dx in offsets:
            draw.text(
                (cx + dx, heard_y), heard, font=heard_font,
                fill=heard_ink, anchor="ma",
            )
    return _encode(img)


def _encode(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()
