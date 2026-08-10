"""Is this actually a bird on white, or has the model drifted?

FLUX mostly obeys the house prompt, but a small share of paintings come back as
something else entirely, and they reach the wall looking broken:

- **A photograph OF a painting.** The model renders the artefact rather than
  the subject: a watercolour sheet lying on a desk, a marker pen beside it, a
  signature in the corner. The trim can't rescue it — the desk fills the frame,
  so there is no white margin to crop.
- **A flat block.** A large plain rectangle of one colour across part of the
  canvas, with the bird squeezed into what's left.

Both share a signature the good plates don't have: **a large expanse of a
single flat colour that isn't white.** A real watercolour bird is textured —
the biggest uniform non-white patch in a good plate is around 1% of it. The
desk photo runs to 7%, the grey block to 21%.

So the check is one measurement, not a classifier. It doesn't know what a bird
looks like; it knows what a plate that will look wrong on the wall looks like,
which is the decision actually being made here.
"""

from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

# Colour buckets for the flatness test: 16 levels per channel. Fine enough that
# a watercolour wash doesn't collapse into one bucket, coarse enough that a
# flat fill's dithering still does.
QUANTISE = 16
# Anything brighter than this in every channel is "the white ground", which is
# supposed to dominate — it's what the wall's multiply-blend drops.
WHITE = 224
# The largest share of the image one flat non-white colour may occupy.
# Measured over the archive: good plates 0.8–1.7%, the desk photo 6.6%, the
# grey block 21.4%. 4% sits in the gap with room either side.
MAX_FLAT_SHARE = 0.04
# How white the outer border must be BEFORE trimming. A bird centred on white
# has a white edge; a photograph of a desk does not. Checked pre-trim, since
# trimming pads the edges back to white and would erase the evidence.
BORDER_RING = 0.04
MIN_BORDER_WHITE = 0.9


def describe_problem(image_bytes: bytes, extension: str) -> str | None:
    """What's wrong with this plate, or None if nothing is.

    Returns a short human-readable reason so the log says *why* a painting was
    thrown away — "flat grey block over 21% of it" is actionable; "rejected"
    isn't.
    """
    if extension == "svg":
        return None  # our own placeholder plate, not the model's work
    try:
        import numpy as np
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        pixels = np.asarray(image)
        if pixels.size == 0:
            return "the image is empty"

        height, width, _ = pixels.shape
        ring = max(2, int(BORDER_RING * min(height, width)))
        border = np.concatenate(
            [
                pixels[:ring].reshape(-1, 3),
                pixels[-ring:].reshape(-1, 3),
                pixels[:, :ring].reshape(-1, 3),
                pixels[:, -ring:].reshape(-1, 3),
            ]
        )
        white_border = float((border > WHITE).all(axis=1).mean())
        if white_border < MIN_BORDER_WHITE:
            return (
                f"only {white_border:.0%} of the border is white — the bird "
                "isn't on a white ground (a photo of a painting, or a scene)"
            )

        buckets = (pixels // QUANTISE * QUANTISE).reshape(-1, 3)
        colours, counts = np.unique(buckets, axis=0, return_counts=True)
        coloured = ~(colours > WHITE).all(axis=1)
        if not coloured.any():
            return "the image is blank"
        flattest = float(counts[coloured].max() / (height * width))
        if flattest > MAX_FLAT_SHARE:
            return (
                f"one flat colour covers {flattest:.0%} of it — a block, not a "
                "painting"
            )
        return None
    except Exception:  # noqa: BLE001 — a quality check must never be the thing
        # that breaks the loop. An unreadable image is the store's problem, and
        # a surprising one is better shown than silently dropped.
        logger.exception("plate check failed; keeping the painting")
        return None
