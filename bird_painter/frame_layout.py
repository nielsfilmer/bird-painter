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
CAPTION_SPACE = 0.20  # fallback fraction when the caller passes no caption_px
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
# The scale scan: each step shrinks by this factor, down to this share of
# the starting scale (below it nothing sensible is left).
SCALE_STEP = 0.97
SCALE_FLOOR = 0.05
# After packing, birds grow in place into free room: this factor a step,
# this many passes over the set.
INFLATE_STEP = 1.03
INFLATE_PASSES = 2


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


def _footprint(cx: float, cy: float, w: float, h: float, cap_w: float, cap_h: float):
    """The box a bird really occupies: its cell plus the caption hanging
    below it, at least as wide as the caption's lettering. Centre = the
    image's centre (what the renderer places)."""
    fw = max(w, cap_w)
    return (cx - fw / 2, cy - h / 2, cx + fw / 2, cy + h / 2 + cap_h)


def _overlaps(a, b, gap: float) -> bool:
    return not (
        a[2] + gap <= b[0]
        or b[2] + gap <= a[0]
        or a[3] + gap <= b[1]
        or b[3] + gap <= a[1]
    )


def _inside(rect, left, top, uw, uh) -> bool:
    return (
        rect[0] >= left
        and rect[1] >= top
        and rect[2] <= left + uw
        and rect[3] <= top + uh
    )


def _sized(dims, scale, cap_w, cap_h):
    """Every bird's cell at `scale` — except that the newest never grows
    past its caps: once it is as big as a bird should be, the sheet's
    remaining room goes to the others (a global cap would have frozen them
    at their ratio to it, and left the sheet half empty)."""
    out = [(w * scale, h * scale) for w, h in dims]
    w0, h0 = out[0]
    k = min(1.0, cap_w / w0 if w0 else 1.0, cap_h / h0 if h0 else 1.0)
    out[0] = (w0 * k, h0 * k)
    return out


def _pack(cells, left, top, uw, uh, gap, caption_px, caption_ws, base_angle, vmin):
    """Place every bird (its cell already sized), nearest the centre first;
    None if one doesn't fit anywhere on the sheet."""
    cx0 = left + uw / 2
    # The newest's centre: the sheet's centre, its caption included, so the
    # bird plus its lettering sit centred as a whole.
    w0, h0 = cells[0]
    cy0 = top + uh / 2 - caption_px / 2
    first = _footprint(cx0, cy0, w0, h0, caption_ws[0], caption_px)
    if not _inside(first, left, top, uw, uh):
        return None
    placed = [(cx0, cy0, first)]
    max_r = math.hypot(uw, uh) / 2
    step = RADIUS_STEP_VMIN * vmin
    for i in range(1, len(cells)):
        w, h = cells[i]
        found = None
        r = step
        while r <= max_r and found is None:
            # Spots a step apart along the ring too, so pockets by the
            # sheet's edge are found as surely as those near the centre.
            angles = max(ANGLES_PER_RING, int(2 * math.pi * r / step))
            for k in range(angles):
                ang = base_angle + i * GOLDEN + k * (2 * math.pi / angles)
                cx, cy = cx0 + r * math.cos(ang), cy0 + r * math.sin(ang)
                rect = _footprint(cx, cy, w, h, caption_ws[i], caption_px)
                if not _inside(rect, left, top, uw, uh):
                    continue
                if any(_overlaps(rect, p[2], gap) for p in placed):
                    continue
                found = (cx, cy, rect)
                break
            r += step
        if found is None:
            return None
        placed.append(found)
    return placed


def _inflate(placed, cells, left, top, uw, uh, gap, caption_px, caption_ws):
    """Grow each bird in place into the room around it — a few percent a
    step, until it would touch a neighbour or the edge — never past the
    bird one rank newer, so the size still tells the age. The newest stays
    as the scan sized it. Two passes: a bird's growth frees nothing, but a
    later bird's ceiling (its newer neighbour) may have risen."""
    cells = list(cells)
    for _ in range(INFLATE_PASSES):
        for i in range(1, len(cells)):
            cx, cy, _rect = placed[i]
            w, h = cells[i]
            ceiling = cells[i - 1][0] * cells[i - 1][1]
            k = 1.0
            while True:
                nk = k * INFLATE_STEP
                nw, nh = w * nk, h * nk
                if nw * nh > ceiling:
                    break
                rect = _footprint(cx, cy, nw, nh, caption_ws[i], caption_px)
                if not _inside(rect, left, top, uw, uh):
                    break
                if any(
                    _overlaps(rect, p[2], gap) for j, p in enumerate(placed) if j != i
                ):
                    break
                k = nk
            if k > 1.0:
                cells[i] = (w * k, h * k)
                placed[i] = (
                    cx,
                    cy,
                    _footprint(cx, cy, w * k, h * k, caption_ws[i], caption_px),
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
    shaped like the bird; the recency weights set each bird's AREA."""
    if width <= 0 or height <= 0 or not files:
        return []
    vmin = min(width, height) / 100
    left, top = width * SIDE_MARGIN, band_top
    uw = width * (1 - 2 * SIDE_MARGIN)
    uh = height * (1 - BOTTOM_MARGIN) - band_top
    if uw <= 0 or uh <= 0:
        return []
    weights = _weights(len(files))
    if aspects is None:
        aspects = [PLATE_ASPECT] * len(files)
    aspects = [min(2.4, max(0.45, a)) for a in aspects]
    caption_ws = caption_widths or [0.0] * len(files)
    # A weight is an AREA: the cell's side is its root, shaped by the bird's
    # own ink aspect (the scatter had w/√a × w√a, i.e. weight², which made
    # the old birds a quarter of the newest instead of the 0.4 it stated).
    dims = [
        (math.sqrt(w) / math.sqrt(a), math.sqrt(w) * math.sqrt(a))
        for w, a in zip(weights, aspects, strict=True)
    ]
    gap = GAP_VMIN * vmin
    base_angle = (hash_str("|".join(files)) % 3600) / 3600 * 2 * math.pi

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
        return []
    args = (left, top, uw, uh, gap, caption_px, caption_ws, base_angle, vmin)
    best, scale = None, hi
    while scale > hi * SCALE_FLOOR:
        best = _pack(_sized(dims, scale, cap_w, cap_h), *args)
        if best is not None:
            break
        scale *= SCALE_STEP
    if best is None:
        logger.warning(
            "frame layout: %d birds don't fit %gx%g; rendering nothing",
            len(files),
            width,
            height,
        )
        return []
    cells = _sized(dims, scale, cap_w, cap_h)
    best, cells = _inflate(best, cells, left, top, uw, uh, gap, caption_px, caption_ws)
    return [
        Placement(
            file=file,
            x=cx - width / 2,
            y=cy - height / 2,
            size_vmin=cells[i][0] / vmin,
            height_vmin=cells[i][1] / vmin,
            z=len(files) - i,
        )
        for i, (file, (cx, cy, _rect)) in enumerate(zip(files, best, strict=True))
    ]
