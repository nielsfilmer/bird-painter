"""Placement for the panel — a packed rosette, newest at the centre.

Replaces the focal scatter (owner, 2026-09-04): "fills the screen as much
as possible and always has the most recent bird centered (drop the random
placement)". Three rules, no dice:

- the NEWEST bird sits on the sheet's centre, largest;
- every other bird, in recency order, takes the spot nearest the centre
  where it fits — so the sheet fills from the middle outward and the
  cluster stays one cluster, never a bird alone in a corner;
- the whole arrangement is scaled up until nothing more fits: the size is
  SOLVED from the sheet, not guessed from a fill fraction.

Deterministic per live set: the same birds always land the same way (the
e-paper redraws only when the bytes change), and a new bird re-packs the
whole sheet, which is one redraw it was going to spend anyway. The only
per-set variation is the angle the spiral of candidate spots starts at,
hashed from the set, so two walls of the same shape don't look stamped.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from functools import lru_cache

from .wall_layout import hash_str

logger = logging.getLogger(__name__)

# Relative plate AREAS by recency rank: the newest dominates, the five heard
# before it sit a step below, everything older tapers with age (kept from
# the scatter — the size story was right, the placing wasn't).
RECENT_COUNT = 5
NEWEST_WEIGHT = 1.0
RECENT_WEIGHT_MAX = 0.78
RECENT_WEIGHT_MIN = 0.62
OLD_WEIGHT_MAX = 0.56
OLD_WEIGHT_MIN = 0.40
OLD_TAPER_SPAN = 8
PLATE_ASPECT = 1.25  # cell height / width when a bird's own ink shape is unknown
SIDE_MARGIN = 0.03
BOTTOM_MARGIN = 0.05
# Minimum clear space between footprints, in vmin. Small: birds carry their
# own whitespace once cropped to their ink.
GAP_VMIN = 0.8
# The spiral of candidate spots: rings this many vmin apart, spots the same
# distance apart along each ring (at least ANGLES_PER_RING of them), each
# bird's ring starting a golden angle on from the last so nothing lines up.
RADIUS_STEP_VMIN = 1.5
ANGLES_PER_RING = 24
GOLDEN = math.radians(137.507764)
# Sanity caps so one lone bird doesn't become a poster.
MAX_NEWEST_WIDTH = 0.6  # of usable width
MAX_NEWEST_HEIGHT = 0.92  # of usable height, footprint incl. caption
# No other bird's area passes this share of the newest's, whatever the scan.
NEWEST_SHARE = 0.9
# The scale scan: each step shrinks by this factor, down to this share of
# the starting scale (below it nothing sensible is left).
SCALE_STEP = 0.97
SCALE_FLOOR = 0.05
# After packing, birds grow in place into free room: this factor a step,
# this many passes over the set — and never past this multiple of their own
# recency weight, so a ring of six still tapers with age (the owner's
# 2026-08-20 complaint was a flat ring).
INFLATE_STEP = 1.03
INFLATE_PASSES = 2
INFLATE_MAX = 1.12
# Plans are deterministic, so the last few are kept: the wall asks for the
# same plan on every poll until its set or viewport changes, and the frame
# renders the same plan twice per redraw (picture and text layers).
PLAN_CACHE = 32


@dataclass(frozen=True)
class Placement:
    """Same shape `wall_layout` returns, so the renderer doesn't care which
    layout produced it."""

    file: str
    x: float  # offset from the panel centre
    y: float
    size_vmin: float
    z: int
    height_vmin: float = 0.0


def _ramp(lo: float, hi: float, i: int, n: int) -> float:
    return hi if n <= 1 else hi + (lo - hi) * (i / (n - 1))


def _weights(count: int) -> list[float]:
    """Relative AREA per bird, newest first."""
    weights = [NEWEST_WEIGHT]
    recent = min(RECENT_COUNT, max(0, count - 1))
    for i in range(recent):
        weights.append(_ramp(RECENT_WEIGHT_MIN, RECENT_WEIGHT_MAX, i, recent))
    for i in range(count - 1 - recent):
        weights.append(
            _ramp(
                OLD_WEIGHT_MIN,
                OLD_WEIGHT_MAX,
                min(i, OLD_TAPER_SPAN),
                OLD_TAPER_SPAN + 1,
            )
        )
    return weights[:count]


@dataclass(frozen=True)
class _Sheet:
    """The usable sheet and what every bird carries onto it: the fixed
    caption height, each caption's measured width, the gap between
    footprints, and where this set's spiral of candidate spots starts."""

    left: float
    top: float
    uw: float
    uh: float
    gap: float
    caption_px: float
    caption_ws: tuple[float, ...]
    base_angle: float
    vmin: float

    @property
    def centre(self) -> tuple[float, float]:
        # The newest's centre: the sheet's centre, its caption counted in,
        # so bird plus lettering sit centred as a whole.
        return self.left + self.uw / 2, self.top + self.uh / 2 - self.caption_px / 2

    def inside(self, rect: tuple[float, float, float, float]) -> bool:
        return (
            rect[0] >= self.left
            and rect[1] >= self.top
            and rect[2] <= self.left + self.uw
            and rect[3] <= self.top + self.uh
        )


Rect = tuple[float, float, float, float]
Spot = tuple[float, float, Rect]


def _footprint(
    cx: float, cy: float, w: float, h: float, cap_w: float, cap_h: float
) -> Rect:
    """The box a bird really occupies: its cell plus the caption hanging
    below it, at least as wide as the caption's lettering. Centre = the
    image's centre (what the renderer places)."""
    fw = max(w, cap_w)
    return (cx - fw / 2, cy - h / 2, cx + fw / 2, cy + h / 2 + cap_h)


def _overlaps(a: Rect, b: Rect, gap: float) -> bool:
    return not (
        a[2] + gap <= b[0]
        or b[2] + gap <= a[0]
        or a[3] + gap <= b[1]
        or b[3] + gap <= a[1]
    )


def _sized(
    dims: list[tuple[float, float]], scale: float, cap_w: float, cap_h: float
) -> list[tuple[float, float]]:
    """Every bird's cell at `scale` — except that the newest never grows
    past its caps: once it is as big as a bird should be, the sheet's
    remaining room goes to the others (a global cap would have frozen them
    at their ratio to it, and left the sheet half empty)."""
    out = [(w * scale, h * scale) for w, h in dims]
    w0, h0 = out[0]
    k = min(1.0, cap_w / w0 if w0 else 1.0, cap_h / h0 if h0 else 1.0)
    out[0] = (w0 * k, h0 * k)
    # …and nobody outgrows it: once the newest is capped the scan could
    # otherwise lift the others past it (two birds on the 10" had the
    # second larger than the first, and the newest at 0.39 of the width
    # with 0.6 to spare — QA on #161). Others stop at NEWEST_SHARE of its
    # area, so "newest largest" holds in area at every scale.
    ceiling = out[0][0] * out[0][1] * NEWEST_SHARE
    for i in range(1, len(out)):
        w, h = out[i]
        if w * h > ceiling:
            f = math.sqrt(ceiling / (w * h))
            out[i] = (w * f, h * f)
    return out


def _pack(cells: list[tuple[float, float]], sheet: _Sheet) -> list[Spot] | None:
    """Place every bird (its cell already sized), nearest the centre first;
    None if one doesn't fit anywhere on the sheet."""
    cx0, cy0 = sheet.centre
    w0, h0 = cells[0]
    first = _footprint(cx0, cy0, w0, h0, sheet.caption_ws[0], sheet.caption_px)
    if not sheet.inside(first):
        return None
    placed: list[Spot] = [(cx0, cy0, first)]
    max_r = math.hypot(sheet.uw, sheet.uh) / 2
    step = RADIUS_STEP_VMIN * sheet.vmin
    for i in range(1, len(cells)):
        w, h = cells[i]
        found = None
        r = step
        while r <= max_r and found is None:
            # Spots a step apart along the ring too, so pockets by the
            # sheet's edge are found as surely as those near the centre.
            angles = max(ANGLES_PER_RING, int(2 * math.pi * r / step))
            for k in range(angles):
                ang = sheet.base_angle + i * GOLDEN + k * (2 * math.pi / angles)
                cx, cy = cx0 + r * math.cos(ang), cy0 + r * math.sin(ang)
                rect = _footprint(cx, cy, w, h, sheet.caption_ws[i], sheet.caption_px)
                if not sheet.inside(rect):
                    continue
                if any(_overlaps(rect, p[2], sheet.gap) for p in placed):
                    continue
                found = (cx, cy, rect)
                break
            r += step
        if found is None:
            return None
        placed.append(found)
    return placed


def _rows(cells: list[tuple[float, float]], sheet: _Sheet) -> list[Spot] | None:
    """The last resort when no scale packs around the centre (a dozen birds
    with captions two lines wide on a small sheet): rows, centred, newest
    first from the top. Loses "newest in the middle", never the wall — the
    browser freezes on an empty plan and the e-paper spends a redraw on a
    blank sheet. None only if a single footprint is wider than the sheet."""
    x = y = 0.0
    row_h = 0.0
    row: list[tuple[int, float, float]] = []
    rows: list[tuple[list[tuple[int, float, float]], float, float]] = []
    for i, (w, h) in enumerate(cells):
        fw = max(w, sheet.caption_ws[i])
        fh = h + sheet.caption_px
        if fw > sheet.uw or fh > sheet.uh:
            return None
        if row and x + sheet.gap + fw > sheet.uw:
            rows.append((row, x, row_h))
            y += row_h + sheet.gap
            row, x, row_h = [], 0.0, 0.0
        if row:
            x += sheet.gap
        row.append((i, x, fw))
        x += fw
        row_h = max(row_h, fh)
    rows.append((row, x, row_h))
    if y + row_h > sheet.uh:
        return None
    y = sheet.top + (sheet.uh - (y + row_h)) / 2
    spots: dict[int, Spot] = {}
    for row, width, height in rows:
        x0 = sheet.left + (sheet.uw - width) / 2
        for i, dx, fw in row:
            w, h = cells[i]
            cx, cy = x0 + dx + fw / 2, y + h / 2
            spots[i] = (
                cx,
                cy,
                _footprint(cx, cy, w, h, sheet.caption_ws[i], sheet.caption_px),
            )
        y += height + sheet.gap
    return [spots[i] for i in range(len(cells))]


def _inflate(
    placed: list[Spot],
    cells: list[tuple[float, float]],
    weights: list[float],
    scale: float,
    sheet: _Sheet,
) -> tuple[list[Spot], list[tuple[float, float]]]:
    """Grow each bird in place into the room around it — a few percent a
    step, until it would touch a neighbour or the edge — never past the
    bird one rank newer, and never past INFLATE_MAX of its own weight, so
    the size still tells the age. The newest stays as the scan sized it.
    Two passes: a bird's growth frees nothing, but a later bird's ceiling
    (its newer neighbour) may have risen."""
    cells = list(cells)
    for _ in range(INFLATE_PASSES):
        # Ceilings from this pass's starting sizes: a bird grown earlier in
        # the pass doesn't lift the ceiling of the one after it.
        areas = [w * h for w, h in cells]
        for i in range(1, len(cells)):
            cx, cy, _rect = placed[i]
            w, h = cells[i]
            ceiling = min(areas[i - 1], weights[i] * INFLATE_MAX * scale * scale)
            k = 1.0
            while True:
                nk = k * INFLATE_STEP
                nw, nh = w * nk, h * nk
                if nw * nh > ceiling:
                    break
                rect = _footprint(cx, cy, nw, nh, sheet.caption_ws[i], sheet.caption_px)
                if not sheet.inside(rect):
                    break
                if any(
                    _overlaps(rect, p[2], sheet.gap)
                    for j, p in enumerate(placed)
                    if j != i
                ):
                    break
                k = nk
            if k > 1.0:
                cells[i] = (w * k, h * k)
                placed[i] = (
                    cx,
                    cy,
                    _footprint(
                        cx, cy, w * k, h * k, sheet.caption_ws[i], sheet.caption_px
                    ),
                )
    return placed, cells


def compute_frame_layout(
    files: list[str],
    width: float,
    height: float,
    band_top: float,
    *,
    aspects: list[float] | None = None,
    caption_px: float = 0.0,
    caption_widths: list[float] | None = None,
) -> list[Placement]:
    """Lay the birds out as a packed rosette. `files` is newest-first.

    `aspects` is each bird's OWN ink aspect (height/width) so its cell is
    shaped like the bird; the recency weights set each bird's AREA. The
    plan is a pure function of its inputs and is memoised (PLAN_CACHE)."""
    return list(
        _layout(
            tuple(files),
            float(width),
            float(height),
            float(band_top),
            None if aspects is None else tuple(aspects),
            float(caption_px),
            None if caption_widths is None else tuple(caption_widths),
        )
    )


@lru_cache(maxsize=PLAN_CACHE)
def _layout(
    files: tuple[str, ...],
    width: float,
    height: float,
    band_top: float,
    aspects: tuple[float, ...] | None,
    caption_px: float,
    caption_widths: tuple[float, ...] | None,
) -> tuple[Placement, ...]:
    if width <= 0 or height <= 0 or not files:
        return ()
    vmin = min(width, height) / 100
    left, top = width * SIDE_MARGIN, band_top
    uw = width * (1 - 2 * SIDE_MARGIN)
    uh = height * (1 - BOTTOM_MARGIN) - band_top
    if uw <= 0 or uh <= 0:
        return ()
    weights = _weights(len(files))
    shapes = [PLATE_ASPECT] * len(files) if aspects is None else list(aspects)
    shapes = [min(2.4, max(0.45, a)) for a in shapes]
    # A weight is an AREA: the cell's side is its root, shaped by the bird's
    # own ink aspect. (The scatter's cells were weight/√a × weight·√a — an
    # area of weight², the oldest a quarter of the newest; this is the size
    # story the constants describe, and a deliberate change of look.)
    dims = [
        (math.sqrt(w) / math.sqrt(a), math.sqrt(w) * math.sqrt(a))
        for w, a in zip(weights, shapes, strict=True)
    ]
    sheet = _Sheet(
        left=left,
        top=top,
        uw=uw,
        uh=uh,
        gap=GAP_VMIN * vmin,
        caption_px=caption_px,
        caption_ws=caption_widths or (0.0,) * len(files),
        base_angle=(hash_str("|".join(files)) % 3600) / 3600 * 2 * math.pi,
        vmin=vmin,
    )
    # The largest scale at which everything fits: scanned DOWN from the
    # scale at which the largest bird alone would fill the sheet, a few
    # percent a step. Not a binary search — a greedy packer isn't monotonic
    # (a slightly smaller scale can fail where a larger one fit, because the
    # spots it picks change), and a bisection stops at the first false
    # failure. The newest is capped inside `_sized`, so the scan really asks
    # how big the others can go.
    cap_w = MAX_NEWEST_WIDTH * uw
    cap_h = max(1.0, MAX_NEWEST_HEIGHT * uh - caption_px)
    hi = min(uw / max(w for w, _ in dims), (uh - caption_px) / max(h for _, h in dims))
    if hi <= 0:
        return ()
    best, scale = None, hi
    while scale > hi * SCALE_FLOOR:
        cells = _sized(dims, scale, cap_w, cap_h)
        best = _pack(cells, sheet)
        if best is not None:
            break
        scale *= SCALE_STEP
    if best is None:
        scale = hi * SCALE_FLOOR
        cells = _sized(dims, scale, cap_w, cap_h)
        best = _rows(cells, sheet)
        if best is None:
            logger.warning(
                "frame layout: %d birds don't fit %gx%g; rendering nothing",
                len(files),
                width,
                height,
            )
            return ()
        logger.warning(
            "frame layout: %d birds packed in rows, not around the centre (%gx%g)",
            len(files),
            width,
            height,
        )
    best, cells = _inflate(best, cells, weights, scale, sheet)
    return tuple(
        Placement(
            file=file,
            x=cx - width / 2,
            y=cy - height / 2,
            size_vmin=cells[i][0] / vmin,
            height_vmin=cells[i][1] / vmin,
            z=len(files) - i,
        )
        for i, (file, (cx, cy, _rect)) in enumerate(zip(files, best, strict=True))
    )
