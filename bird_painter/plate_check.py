"""Is this actually a bird on white, or has the model drifted?

FLUX mostly obeys the house prompt, but a small share of paintings come back as
something else, and they reach the wall looking broken:

- **A photograph OF a painting.** The model renders the artefact rather than
  the subject: a watercolour sheet on a desk, a marker pen beside it, a
  signature in the corner. The trim can't rescue it — the desk fills the frame,
  so there's no white margin to crop.
- **A flat block.** A plain rectangle of one colour across part of the canvas,
  the bird squeezed into what's left.

Two measurements, each aimed at one of those, calibrated over the whole archive
(277 plates) rather than the handful that prompted the work:

1. **The painting must be surrounded by white.** A bird painted on white has
   ground on every side; a photograph of a desk runs to the frame edge. Asked
   as "how much empty margin is there per side", not by sampling the outermost
   pixels, which was circular (crop to the content and the "border" IS the
   bird). Padding a plate makes its margins LARGER, never smaller, so trimming
   can only make this rule more lenient — never reject something it would
   otherwise have passed.
2. **No single flat colour may dominate the SUBJECT.** Measured against the
   non-white pixels, not the canvas — which makes it exactly padding-invariant
   (verified to four decimal places), so a threshold calibrated on the archive
   is valid pre-trim, where this actually runs. The first version measured
   against the canvas and was therefore calibrated on one thing and applied to
   another.

Both thresholds sit in a measured gap rather than at a guess. Flat share of the
subject: 273 good plates 1.4–28.4%, grey block 93.3%, letterbox bars 72.9%,
desk photo 77.5%. White margin: good plates have ground on all four sides; the
desk photos have it on none.

A false positive is the expensive mistake here — a bird that was genuinely
heard would silently never appear, and nobody would know to look — so the
thresholds sit on the loose side of those gaps, and anything the check can't
read is kept rather than thrown away. Over the whole archive it rejects 3
plates, all of them genuinely broken, and no good one.

This catches the two shapes above, and is not a general "is this good art"
judgement. Known misses, all of which stay visible on the wall rather than
silently deleting a bird: a print photographed on PALE GREY (the Eurasian
Oystercatcher in the archive — grey that light counts as ground; tightening
the threshold rejects good plates painted on warm off-white), a photograph on
a WHITE desk, a caption baked into the image, a drawn frame, and a textured
rather than flat block.
"""

from __future__ import annotations

import io
import logging

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# Analysis size. Every measure here is a ratio, so the answers are unchanged by
# working on a thumbnail — and it takes the check from ~400 ms to ~100 ms,
# which matters in the mic thread between detections.
ANALYSIS_PIXELS = 512

# Colour buckets for the flatness test: 16 levels per channel. Fine enough that
# a watercolour wash doesn't collapse into one bucket, coarse enough that a
# flat fill's dithering does.
QUANTISE = 16
# At or above this in every channel is "the white ground". Compared against
# bucket FLOORS, so the boundary is inclusive: a warm white like (250, 250,
# 232) quantises to [240 240 224], and an exclusive `>` would have called that
# colour — which is precisely the bug that made this check reject three good
# plates, including a flawless hummingbird, at 89%.
WHITE = 224
WHITE_BUCKET = (WHITE // QUANTISE) * QUANTISE
# 224 rather than trim.py's stricter 242 on purpose: at 242 a warm off-white
# ground reads as coloured and good plates are rejected. The cost is a known
# blind spot — a print photographed on PALE GREY passes (see the Eurasian
# Oystercatcher in the archive: a sheet on grey, signature and all). Missing a
# bad plate shows up on the wall; rejecting a good one shows up as a bird that
# never arrives, and only one of those is noticeable.

# A side counts as having white ground when at least this fraction of the
# canvas beyond the painting is empty.
MIN_MARGIN = 0.02
# The subject's extent is taken between these percentiles of its pixels, so a
# few stray specks can't stand in for the painting's edge.
EDGE_PERCENTILE = 0.5
MAX_FLAT_SHARE = 0.50


def _white_margins(pixels: np.ndarray) -> tuple[float, ...]:
    """How much white surrounds the painting, per side, as a fraction of the
    canvas.

    This is the question that separates a bird on white from a photograph of a
    desk: the bird has ground around it, the photograph runs to the frame edge.
    Asking it this way — rather than sampling the outermost pixels — survives
    `trim` padding the plate back to 4:5, so the same plate answers the same
    before and after, and the thresholds keep meaning what they were measured
    to mean.
    """
    coloured = np.argwhere(~(pixels >= WHITE).all(axis=2))
    if len(coloured) == 0:
        return (1.0, 1.0, 1.0, 1.0)  # blank: all ground, no painting
    height, width, _ = pixels.shape
    # A percentile rather than the raw extremes: with min/max, two stray dark
    # pixels in opposite corners — a speck of JPEG noise — describe a subject
    # spanning the whole canvas and reject a perfectly good plate.
    (top, left) = np.percentile(coloured, EDGE_PERCENTILE, axis=0)
    (bottom, right) = np.percentile(coloured, 100 - EDGE_PERCENTILE, axis=0)
    return (
        top / height,
        (height - bottom) / height,
        left / width,
        (width - right) / width,
    )


def describe_problem(image_bytes: bytes, extension: str) -> str | None:
    """What's wrong with this plate, or None if nothing is.

    Returns a reason rather than a bool so the log says *why* a painting was
    thrown away — "one flat colour covers 93% of the bird" is actionable,
    "rejected" isn't.
    """
    if extension == "svg":
        return None  # our own placeholder plate, not the model's work
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        image.thumbnail((ANALYSIS_PIXELS, ANALYSIS_PIXELS))
        pixels = np.asarray(image)
        height, width, _ = pixels.shape
        if height < 8 or width < 8:
            return None  # too small to judge; not the same as judged bad

        margins = sorted(_white_margins(pixels))
        # Three sides must have a real white margin. Three rather than four
        # because a legitimate plate often has the bird's feet or tail running
        # close to one edge; a desk photo has ground on none.
        if margins[1] < MIN_MARGIN:
            crowded = sum(margin < MIN_MARGIN for margin in margins)
            return (
                f"the painting reaches the edge on {crowded} of 4 sides — it "
                "isn't a bird on a white ground (a photo of a painting, or a "
                "scene)"
            )

        buckets = (pixels // QUANTISE * QUANTISE).reshape(-1, 3)
        colours, counts = np.unique(buckets, axis=0, return_counts=True)
        coloured = ~(colours >= WHITE_BUCKET).all(axis=1)
        if not coloured.any():
            return None  # all white: blank, but blankness isn't this test's job
        flattest = float(counts[coloured].max() / counts[coloured].sum())
        if flattest > MAX_FLAT_SHARE:
            return (
                f"one flat colour covers {flattest:.0%} of the bird — a block, "
                "not a painting"
            )
        return None
    except Exception:  # noqa: BLE001 — a quality check must never be the thing
        # that breaks the loop. An unreadable image is the store's problem, and
        # a surprising one is better shown than silently dropped.
        logger.exception("plate check failed; keeping the painting")
        return None
