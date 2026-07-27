"""Trim the empty white margins off a painting so the bird fills its plate.

FLUX paints the bird small on a large flat-white canvas — often 30–50% margin
on every side — so on the wall each bird looked smaller than its plate. This
crops to the bird's bounding box (plus a breathing margin), then pads back out
to the plate's 4:5 aspect so the wall's `object-fit: cover` / the PNG render
never cuts into the bird. Padding is white, which the wall's multiply-blend
melts into the paper.

Runs once at store time (detection + /dev/paint paths); the archive keeps the
trimmed painting. Fail-soft: anything unparseable (SVG placeholder plates) or
without a clear white margin comes back unchanged.
"""

from __future__ import annotations

import io
import logging

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# A pixel is "background" when all channels are at least this bright. FLUX's
# flat-white ground is 250+; the threshold is forgiving of slight tinting.
WHITE_THRESHOLD = 242
# Breathing room kept around the bird's bounding box, as a fraction of the
# box's larger side — so the crop doesn't kiss the wingtips.
MARGIN_FRAC = 0.07
# The plate's aspect (height/width), matching PLATE_ASPECT in the layouts and
# the wall CSS `aspect-ratio: 4 / 5`.
PLATE_ASPECT = 5 / 4
# Skip the crop when the bird already fills most of the canvas (nothing worth
# trimming), or when almost nothing is non-white (blank/failed image).
MIN_CONTENT_FRAC = 0.02
MAX_CONTENT_FRAC = 0.85


def trim_to_bird(image_bytes: bytes, extension: str) -> bytes:
    """Return `image_bytes` cropped to the bird + margin, padded to 4:5.
    On any failure (or nothing sensible to trim) returns the input unchanged."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:  # noqa: BLE001 — e.g. SVG placeholder; leave untouched
        return image_bytes
    try:
        arr = np.asarray(img)
        content = (arr < WHITE_THRESHOLD).any(axis=2)
        frac = float(content.mean())
        if not MIN_CONTENT_FRAC < frac < MAX_CONTENT_FRAC:
            return image_bytes
        ys, xs = np.nonzero(content)
        top, bottom = int(ys.min()), int(ys.max())
        left, right = int(xs.min()), int(xs.max())
        margin = round(max(bottom - top, right - left) * MARGIN_FRAC)
        top, bottom = top - margin, bottom + margin
        left, right = left - margin, right + margin

        # Expand the shorter axis to the plate's 4:5 aspect around the centre,
        # so downstream cover-fitting never crops into the bird.
        box_w, box_h = right - left, bottom - top
        if box_h / box_w < PLATE_ASPECT:
            grow = round((box_w * PLATE_ASPECT - box_h) / 2)
            top, bottom = top - grow, bottom + grow
        else:
            grow = round((box_h / PLATE_ASPECT - box_w) / 2)
            left, right = left - grow, right + grow

        # Crop within bounds, then pad with white for any part of the target
        # box that fell outside the source image.
        src_left, src_top = max(0, left), max(0, top)
        src_right = min(img.width, right)
        src_bottom = min(img.height, bottom)
        cropped = img.crop((src_left, src_top, src_right, src_bottom))
        out = Image.new("RGB", (right - left, bottom - top), (255, 255, 255))
        out.paste(cropped, (src_left - left, src_top - top))

        buf = io.BytesIO()
        if extension.lower() in ("jpg", "jpeg"):
            out.save(buf, "JPEG", quality=92)
        else:
            out.save(buf, "PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001 — trimming must never lose a painting
        logger.exception("trim: failed; storing the painting untrimmed")
        return image_bytes
