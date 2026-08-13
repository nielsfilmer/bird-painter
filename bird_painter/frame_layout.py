"""Placement for the e-paper frame — a focal scatter, not a grid.

The grid filled the sheet but read as a spreadsheet (owner, 2026-08-13).
Replaced with the arrangement the owner dictated:

- an ANCHOR is picked inside a central box holding ~30% of the sheet's
  area — so it can sit off-centre, but never in a corner;
- the newest bird sits on the anchor, largest;
- the five heard before it gather AROUND it, a step smaller;
- everything older is smaller again, shrinking with its age rank, and is
  placed wherever the sheet is currently emptiest — which is naturally
  the side the anchor left open, so the whole sheet stays covered and
  the distribution comes out roughly uniform;
- positions carry jitter, but DETERMINISTIC jitter: the same live set
  always lays out the same way. The frame redraws only when the image
  bytes change, so a layout that wandered on every render would wear the
  panel for nothing. Any new bird reseeds the whole composition, which
  is one redraw it was going to spend anyway.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .wall_layout import hash_str

# The anchor box: a centred region holding this share of the usable area.
ANCHOR_BOX_AREA = 0.30
# The five birds heard before the newest one ("surround it").
RECENT_COUNT = 5
# Relative plate widths by recency rank. The newest dominates, the recent
# five sit a step below it, and everything older tapers off with age.
NEWEST_WEIGHT = 1.0
RECENT_WEIGHT = 0.72
OLD_WEIGHT_MAX = 0.58
OLD_WEIGHT_MIN = 0.42
# How much of the usable sheet the plates' footprints aim to occupy. The
# global scale is solved from this, so two birds come out big and twelve
# come out small without separate rules per count.
FILL = 0.52
PLATE_ASPECT = 1.25  # cell height / width, as the wall's plates
# Room under each plate for its caption lines, as a fraction of plate height.
CAPTION_SPACE = 0.20
SIDE_MARGIN = 0.03
BOTTOM_MARGIN = 0.05
# Minimum clear space between footprints, in vmin. Small: birds carry their
# own whitespace once cropped to their ink.
GAP_VMIN = 1.0
# Candidate positions sampled per bird; the scorer picks among the valid ones.
CANDIDATES = 60
# When a pass can't place everything, shrink and retry; the last pass places
# regardless, so the function cannot fail outright.
SHRINK = 0.94
MAX_PASSES = 30
# Sanity caps so one lone bird doesn't become a poster.
MAX_NEWEST_WIDTH = 0.46  # of usable width
MAX_NEWEST_HEIGHT = 0.92  # of usable height, footprint incl. caption


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


def _weights(count: int) -> list[float]:
    weights = []
    olds = count - RECENT_COUNT - 1
    for rank in range(count):
        if rank == 0:
            weight = NEWEST_WEIGHT
        elif rank <= RECENT_COUNT:
            weight = RECENT_WEIGHT
        else:
            t = 0.0 if olds <= 1 else (rank - RECENT_COUNT - 1) / (olds - 1)
            weight = OLD_WEIGHT_MAX + (OLD_WEIGHT_MIN - OLD_WEIGHT_MAX) * t
        weights.append(weight)
    return weights


def _overlaps(a: tuple, b: tuple, gap: float) -> bool:
    return not (
        a[2] + gap <= b[0]
        or b[2] + gap <= a[0]
        or a[3] + gap <= b[1]
        or b[3] + gap <= a[1]
    )


def _try_layout(files, weights, scale, rng, left, top, uw, uh, gap, force):
    """One placement attempt at one scale. None = didn't fit, go smaller."""
    placed = []  # (cx, cy, rect)
    anchor = None
    for i, _file in enumerate(files):
        w = weights[i] * scale
        plate_h = w * PLATE_ASPECT
        foot_h = plate_h * (1 + CAPTION_SPACE)
        min_cx, max_cx = left + w / 2, left + uw - w / 2
        min_cy = top + plate_h / 2
        max_cy = top + uh - foot_h + plate_h / 2
        if min_cx > max_cx or min_cy > max_cy:
            return None  # plate wider/taller than the sheet at this scale

        if i == 0:
            # The anchor: uniform within a centred box of ANCHOR_BOX_AREA.
            box_w = uw * math.sqrt(ANCHOR_BOX_AREA)
            box_h = uh * math.sqrt(ANCHOR_BOX_AREA)
            cx = left + uw / 2 + (rng.random() - 0.5) * box_w
            cy = top + uh / 2 + (rng.random() - 0.5) * box_h
            cx = min(max(cx, min_cx), max_cx)
            cy = min(max(cy, min_cy), max_cy)
            anchor = (cx, cy)
            placed.append(
                (cx, cy,
                 (cx - w / 2, cy - plate_h / 2,
                  cx + w / 2, cy - plate_h / 2 + foot_h))
            )
            continue

        best = None
        for _ in range(CANDIDATES):
            cx = rng.uniform(min_cx, max_cx)
            cy = rng.uniform(min_cy, max_cy)
            rect = (cx - w / 2, cy - plate_h / 2,
                    cx + w / 2, cy - plate_h / 2 + foot_h)
            collides = any(_overlaps(rect, r, gap) for _, _, r in placed)
            if collides and not force:
                continue
            if i <= RECENT_COUNT:
                # The recent five hug the newest: nearest valid spot wins,
                # and non-overlap pushes each successive one further around
                # the ring.
                score = -((cx - anchor[0]) ** 2 + (cy - anchor[1]) ** 2)
            else:
                # Older birds fill the emptiest region: the candidate whose
                # nearest neighbour is farthest away wins. That is what sends
                # them to whatever side the anchor left open, and what keeps
                # the whole sheet covered.
                score = min(
                    (cx - px) ** 2 + (cy - py) ** 2 for px, py, _ in placed
                )
            if collides:
                score -= 1e12  # forced pass: overlap only as a last resort
            if best is None or score > best[0]:
                best = (score, cx, cy, rect)
        if best is None:
            return None
        placed.append((best[1], best[2], best[3]))
    return placed


def compute_frame_scatter(
    files: list[str], width: float, height: float, band_top: float
) -> list[Placement]:
    """Lay the birds out as a focal scatter. `files` is newest-first."""
    if width <= 0 or height <= 0 or not files:
        return []
    vmin = min(width, height) / 100
    left, top = width * SIDE_MARGIN, band_top
    uw = width * (1 - 2 * SIDE_MARGIN)
    uh = height * (1 - BOTTOM_MARGIN) - band_top
    if uw <= 0 or uh <= 0:
        return []

    weights = _weights(len(files))
    footprint = PLATE_ASPECT * (1 + CAPTION_SPACE)
    scale = math.sqrt(FILL * uw * uh / (footprint * sum(w * w for w in weights)))
    scale = min(
        scale,
        MAX_NEWEST_WIDTH * uw / weights[0],
        MAX_NEWEST_HEIGHT * uh / (weights[0] * footprint),
    )
    gap = GAP_VMIN * vmin
    seed = hash_str("|".join(files))

    placed = None
    for attempt in range(MAX_PASSES):
        rng = random.Random(seed * 1000003 + attempt)  # noqa: S311 — layout jitter, not cryptography
        placed = _try_layout(
            files, weights, scale * SHRINK**attempt, rng,
            left, top, uw, uh, gap,
            force=attempt == MAX_PASSES - 1,
        )
        if placed is not None:
            scale = scale * SHRINK**attempt
            break

    placements = []
    for i, (file, (cx, cy, _rect)) in enumerate(zip(files, placed, strict=True)):
        w = weights[i] * scale
        placements.append(
            Placement(
                file=file,
                x=cx - width / 2,
                y=cy - height / 2,
                size_vmin=w / vmin,
                height_vmin=w * PLATE_ASPECT / vmin,
                z=len(files) - i,  # newest on top, as on the wall
            )
        )
    return placements
