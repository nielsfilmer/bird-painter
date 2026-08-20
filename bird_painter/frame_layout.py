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

import logging
import math
import random
from dataclasses import dataclass

from .wall_layout import hash_str

logger = logging.getLogger(__name__)

# The anchor box: a centred region holding this share of the usable area.
ANCHOR_BOX_AREA = 0.30
# The five birds heard before the newest one ("surround it").
RECENT_COUNT = 5
# Relative plate widths by recency rank. The newest dominates, the recent
# five sit a step below it, and everything older tapers off with age.
#
# The ring tapers WITHIN itself as well. A flat ring band read as "nothing
# gets smaller with age" on the panel (owner, 2026-08-20) — and it always
# would have, because a wall of six birds is the newest plus exactly
# RECENT_COUNT others, so the old-bird taper below never ran at all. Six
# birds is a completely ordinary wall, so the size story has to be legible
# without a seventh.
NEWEST_WEIGHT = 1.0
RECENT_WEIGHT_MAX = 0.78
RECENT_WEIGHT_MIN = 0.62
OLD_WEIGHT_MAX = 0.56
OLD_WEIGHT_MIN = 0.40
# How many older birds it takes to reach OLD_WEIGHT_MIN. Fixed, so a bird's
# size follows its own age rather than the size of the wall it landed on.
OLD_TAPER_SPAN = 8
# How much of the usable sheet the plates' footprints aim to occupy. The
# global scale is solved from this, so two birds come out big and twelve
# come out small without separate rules per count.
FILL = 0.62
PLATE_ASPECT = 1.25  # cell height / width, as the wall's plates
# Captions are FIXED-SIZE text (owner: don't resize the text), so the room
# they need is absolute pixels, not a fraction of the plate — a small bird's
# caption is exactly as tall as a large bird's. The renderer measures and
# passes both the height and each caption's true width; the width becomes a
# floor on the footprint so two small neighbours can't overlap lettering.
CAPTION_SPACE = 0.20  # fallback fraction when the caller passes no caption_px
SIDE_MARGIN = 0.03
BOTTOM_MARGIN = 0.05
# Minimum clear space between footprints, in vmin. Small: birds carry their
# own whitespace once cropped to their ink.
GAP_VMIN = 0.8
# Candidate positions sampled per bird; the scorer picks among the valid ones.
CANDIDATES = 60
# The recent five are placed in POLAR coordinates around the anchor: each
# takes a sector of the circle and creeps outward from the anchor until it
# stops overlapping, so it ends up as close as it can get. Sampling them
# uniformly over the whole sheet (as this did until 2026-08-20) barely ever
# offered a spot near the anchor, so "nearest valid of sixty distant ones"
# won and the ring never actually surrounded anything.
RING_STEPS = 48  # radii tried, innermost first
RING_STEP = 0.35  # each step outward, as a share of the bird's own size
RING_WOBBLE = 0.30  # radians of jitter within a sector, so it isn't a clock face
# How far a petal may creep from its innermost possible radius before the whole
# layout shrinks and tries again, as a multiple of the bird's own size. Without
# a cap a blocked sector sends one petal halfway across the sheet — it ends up
# farther out than most of the OLDER birds, which reads as no arrangement at
# all (QA measured one at 846px against a rosette body of 453px).
RING_MAX_CREEP = 1.7
# When a pass can't place everything, shrink and retry; the last pass places
# regardless, so the function cannot fail outright.
SHRINK = 0.94
MAX_PASSES = 30
# How strongly the small old birds prefer the outskirts, scaled by how much
# smaller than the newest they are (0 = ignore the edges, 1 = the edge matters
# as much as emptiness). The recent five aren't affected — they hug the anchor.
# Both terms it weighs are normalised to 0..1 first; when they were raw pixel
# distances the edge term simply outweighed emptiness and pinned every old
# bird to a border (owner, 2026-08-20).
EDGE_PULL = 0.55
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


def _ramp(lo: float, hi: float, i: int, n: int) -> float:
    """`hi` at i=0 down to `lo` at i=n-1 (or `hi` when there's only one)."""
    return hi if n <= 1 else hi + (lo - hi) * (i / (n - 1))


def _weights(count: int) -> list[float]:
    """Plate width by recency rank.

    Both ramps run over a FIXED span rather than over however many birds
    happen to be on the wall. Scaling the ramp to the count puts the whole
    drop into whatever birds exist: with seven birds the single old one sat at
    the top of its range, and with eight the second one fell straight to the
    bottom — a cliff between two consecutive birds, the same shape as the
    six-bird bug. A fixed span means a bird's size depends on its own age, not
    on how many others were heard, so the wall grows smoothly."""
    weights = []
    for rank in range(count):
        if rank == 0:
            weight = NEWEST_WEIGHT
        elif rank <= RECENT_COUNT:
            weight = _ramp(
                RECENT_WEIGHT_MIN, RECENT_WEIGHT_MAX, rank - 1, RECENT_COUNT
            )
        else:
            age = rank - RECENT_COUNT - 1
            weight = _ramp(
                OLD_WEIGHT_MIN, OLD_WEIGHT_MAX, min(age, OLD_TAPER_SPAN - 1),
                OLD_TAPER_SPAN,
            )
        weights.append(weight)
    return weights


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _overlaps(a: tuple, b: tuple, gap: float) -> bool:
    return not (
        a[2] + gap <= b[0]
        or b[2] + gap <= a[0]
        or a[3] + gap <= b[1]
        or b[3] + gap <= a[1]
    )


def _try_layout(
    files, dims, scale, rng, left, top, uw, uh, gap, caption_px, caption_ws, force
):
    """One placement attempt at one scale. None = didn't fit, go smaller."""
    placed = []  # (cx, cy, rect)
    anchor = anchor_foot = None
    ring_phase = rng.random() * math.tau  # where the rosette's first bird sits
    ring_radii = []  # how far each rosette petal sits from the anchor
    diag = math.hypot(uw, uh)
    for i, _file in enumerate(files):
        w = dims[i][0] * scale
        plate_h = dims[i][1] * scale
        foot_w = max(w, caption_ws[i])
        foot_h = plate_h + (caption_px or plate_h * CAPTION_SPACE)
        min_cx, max_cx = left + foot_w / 2, left + uw - foot_w / 2
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
            anchor_foot = (foot_w, plate_h)
            placed.append(
                (cx, cy,
                 (cx - foot_w / 2, cy - plate_h / 2,
                  cx + foot_w / 2, cy - plate_h / 2 + foot_h))
            )
            continue

        def _rect(cx, cy, foot_w=foot_w, plate_h=plate_h, foot_h=foot_h):
            return (cx - foot_w / 2, cy - plate_h / 2,
                    cx + foot_w / 2, cy - plate_h / 2 + foot_h)

        if i <= RECENT_COUNT:
            # The recent five surround the newest. Each owns a sector of the
            # circle and creeps outward from the anchor until it clears its
            # neighbours, so it lands as close to the anchor as it can — which
            # is what "gathered around it" means, and what sampling the whole
            # sheet could never produce.
            sector = math.tau / min(RECENT_COUNT, len(files) - 1)
            angle = (
                ring_phase
                + (i - 1) * sector
                + (rng.random() - 0.5) * RING_WOBBLE
            )
            step = RING_STEP * max(foot_w, foot_h)
            start = 0.5 * (
                min(anchor_foot[0], anchor_foot[1]) + min(foot_w, foot_h)
            )
            spot = None
            ceiling = start + RING_MAX_CREEP * max(foot_w, foot_h)
            for k in range(RING_STEPS):
                radius = start + k * step
                if radius > ceiling:
                    break  # too far to still be "gathered around" — shrink instead
                cx = anchor[0] + radius * math.cos(angle)
                cy = anchor[1] + radius * math.sin(angle)
                cx, cy = min(max(cx, min_cx), max_cx), min(max(cy, min_cy), max_cy)
                rect = _rect(cx, cy)
                if not any(_overlaps(rect, r, gap) for _, _, r in placed):
                    spot = (cx, cy, rect)
                    break
            if spot is None:
                if not force:
                    return None  # shrink and try again: the rosette didn't fit
                cx = min(max(anchor[0] + start * math.cos(angle), min_cx), max_cx)
                cy = min(max(anchor[1] + start * math.sin(angle), min_cy), max_cy)
                spot = (cx, cy, _rect(cx, cy))
            placed.append(spot)
            ring_radii.append(
                math.hypot(spot[0] - anchor[0], spot[1] - anchor[1])
            )
            continue

        # The bar an old bird has to clear is the rosette's MEDIAN radius, not
        # its farthest petal. One petal can creep a long way out looking for
        # space, and holding every later bird beyond that outlier is a bar the
        # sheet may have no room for — it would push the old birds off the
        # edge or fail the pass outright.
        ring_edge = _median(ring_radii)

        best = best_outside = None
        # Half the candidates are drawn from the annulus beyond the rosette,
        # where an old bird BELONGS, and half uniformly over the sheet so the
        # corners still get filled. Drawing them all uniformly left the outside
        # pool empty whenever the ring was wide, and the bird fell back to a
        # gap between two petals.
        for candidate in range(CANDIDATES):
            if candidate % 2 == 0:
                angle = rng.random() * math.tau
                radius = ring_edge + (diag - ring_edge) * math.sqrt(rng.random())
                cx = anchor[0] + radius * math.cos(angle)
                cy = anchor[1] + radius * math.sin(angle)
                cx = min(max(cx, min_cx), max_cx)
                cy = min(max(cy, min_cy), max_cy)
            else:
                cx = rng.uniform(min_cx, max_cx)
                cy = rng.uniform(min_cy, max_cy)
            rect = _rect(cx, cy)
            collides = any(_overlaps(rect, r, gap) for _, _, r in placed)
            if collides and not force:
                continue
            # Older (smaller) birds fill the emptiest region — the candidate
            # whose nearest neighbour is farthest wins — with a pull outward
            # on top (owner: small birds on the outskirts, large ones inside).
            # The pull grows as the bird shrinks, so the oldest drift furthest
            # out. Both terms are normalised to 0..1 so the two actually trade
            # off; as raw pixel distances the pull simply won and pinned every
            # old bird to a border.
            #
            # Outward means away from the ANCHOR, not from the middle of the
            # sheet. The anchor is where the composition's weight sits, and it
            # can be well off-centre — measured from the sheet's middle, the
            # small birds happily filled the space between the anchor and the
            # far edge, i.e. straight through the ring they're supposed to be
            # outside of.
            nearest = math.sqrt(min(
                (cx - px) ** 2 + (cy - py) ** 2 for px, py, _ in placed
            ))
            from_focus = math.hypot(cx - anchor[0], cy - anchor[1])
            outward = EDGE_PULL * (1 - dims[i][0] / dims[0][0])
            score = nearest / diag + outward * from_focus / (diag / 2)
            if collides:
                score -= 1e12  # forced pass: overlap only as a last resort
            if best is None or score > best[0]:
                best = (score, cx, cy, rect)
            # The pull outward is a score term, and a score term can be
            # outvoted: a gap BETWEEN two ring petals scores well on emptiness
            # while sitting closer in than the ring itself, which is how the
            # oldest bird on a real nine-bird wall ended up nearest the anchor
            # of all nine. "Smaller birds on the outskirts" is a rule, not a
            # preference, so a candidate outside the ring's outer edge beats
            # every candidate inside it outright.
            # Not on the forced pass, though: an overlapping spot outside the
            # rosette must not beat a clean one inside it, or the `-= 1e12`
            # last-resort penalty above is defeated by the very next line.
            if not collides and from_focus >= ring_edge and (
                best_outside is None or score > best_outside[0]
            ):
                best_outside = (score, cx, cy, rect)
        chosen = best_outside or best
        if chosen is None:
            return None
        placed.append((chosen[1], chosen[2], chosen[3]))
    return placed


def compute_frame_scatter(
    files: list[str],
    width: float,
    height: float,
    band_top: float,
    *,
    aspects: list[float] | None = None,
    caption_px: float = 0.0,
    caption_widths: list[float] | None = None,
) -> list[Placement]:
    """Lay the birds out as a focal scatter. `files` is newest-first.

    `aspects` is each bird's OWN ink aspect (height/width), so its cell is
    shaped like the bird rather than a 4:5 plate — a heron gets a tall thin
    cell, a plump owl a squarish one, and the whitespace a mismatched cell
    carried inside itself is gone (owner: "crop the birds so there is less
    whitespace in the paints itself"). The recency weights set each bird's
    AREA, so a tall bird doesn't out-bulk a wide one of the same rank."""
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
    # The weight is an AREA: unit dims per bird follow its own ink shape.
    dims = [
        (w / math.sqrt(a), w * math.sqrt(a))
        for w, a in zip(weights, aspects, strict=True)
    ]
    # Solve the global scale so the footprints (bird + its fixed-height
    # caption) hit the fill target: sum(uw*uh)*s^2 + sum(uw)*caption*s = F*U.
    quad = sum(dw * dh for dw, dh in dims)
    lin = sum(dw for dw, _ in dims) * caption_px
    target = FILL * uw * uh
    scale = (-lin + math.sqrt(lin * lin + 4 * quad * target)) / (2 * quad)
    scale = min(
        scale,
        MAX_NEWEST_WIDTH * uw / dims[0][0],
        (MAX_NEWEST_HEIGHT * uh - caption_px) / dims[0][1],
    )
    gap = GAP_VMIN * vmin
    seed = hash_str("|".join(files))

    placed = None
    for attempt in range(MAX_PASSES):
        rng = random.Random(seed * 1000003 + attempt)  # noqa: S311 — layout jitter, not cryptography
        placed = _try_layout(
            files, dims, scale * SHRINK**attempt, rng,
            left, top, uw, uh, gap, caption_px, caption_ws,
            force=attempt == MAX_PASSES - 1,
        )
        if placed is not None:
            scale = scale * SHRINK**attempt
            break

    if placed is None:
        # Even the forced pass bails when a single plate is wider or taller
        # than the usable sheet — a viewport far smaller than any real panel.
        # An empty wall is the honest answer there; the alternative was a
        # TypeError from zipping against None, under a docstring promising
        # this function cannot fail outright.
        logger.warning(
            "frame layout: %d birds don't fit %gx%g; rendering nothing",
            len(files), width, height,
        )
        return []

    placements = []
    for i, (file, (cx, cy, _rect)) in enumerate(zip(files, placed, strict=True)):
        placements.append(
            Placement(
                file=file,
                x=cx - width / 2,
                y=cy - height / 2,
                size_vmin=dims[i][0] * scale / vmin,
                height_vmin=dims[i][1] * scale / vmin,
                z=len(files) - i,  # newest on top, as on the wall
            )
        )
    return placements
