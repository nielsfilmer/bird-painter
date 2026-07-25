"""Python port of the wall's collage-layout maths (`static/layout.js`).

The e-paper frame can't run the browser wall, so `/wall.png` (slice 2) renders
the collage server-side — and to *match* the live wall it must place birds with
the exact same algorithm. This module is a line-for-line port of
`computeCollage` in `static/layout.js`; the two MUST stay in sync. A parity
test (`tests/test_wall_layout_parity.py`) runs the JS and this port on shared
inputs and asserts identical placements (skipped when node is absent), so a
drift between them fails CI rather than silently desyncing the two walls.

Given the live files (newest-first), the viewport, and the y where the title
band ends, `compute_collage` returns one `Placement` per file:
    Placement(file, x, y, size_vmin, z)
x/y are pixels relative to the viewport centre.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

GOLDEN_ANGLE = 2.399963229728653  # radians, 137.5°
SIZE_MIN_VMIN = 22
SIZE_SPAN_VMIN = 5  # plate width 22–26 vmin
MAX_INDEX = 12  # matches the wall's live cap
PLATE_ASPECT = 5 / 4  # painted image is 4:5 portrait
CAPTION_ALLOWANCE = 1.1
CAPTION_FLOOR_PX = 26
TOP_Z = 200
GAP_VMIN = 0.5
SPIRAL_STEP = 0.22
MAX_TRIES = 220
FILL_FACTOR = 0.92
SHRINK_RETRIES = 8
SHRINK_STEP = 0.9
CLUSTER_W_FRAC = 0.92
CLUSTER_H_FRAC = 0.9
CLUSTER_ASPECT = 1.7

_U32 = 0xFFFFFFFF


@dataclass(frozen=True)
class Placement:
    file: str
    x: float
    y: float
    size_vmin: float
    z: int


def caption_px(image_height_px: float) -> float:
    return max(CAPTION_FLOOR_PX, image_height_px * (CAPTION_ALLOWANCE - 1))


def hash_str(s: str) -> int:
    """FNV-1a, matching layout.js `hash()` (returns an unsigned 32-bit int).
    JS uses Math.imul + `>>> 0`; masking to 32 bits each step reproduces the
    same bit pattern, and the sign-sensitive ops downstream (`% span`, `>> 8`)
    all run on this unsigned result."""
    h = 2166136261
    for ch in s:
        h ^= ord(ch)
        h = (h * 16777619) & _U32
    return h & _U32


def overlap_area(a: dict, b: dict) -> float:
    w = min(a["x"] + a["w"] / 2, b["x"] + b["w"] / 2) - max(
        a["x"] - a["w"] / 2, b["x"] - b["w"] / 2
    )
    h = min(a["y"] + a["h"] / 2, b["y"] + b["h"] / 2) - max(
        a["y"] - a["h"] / 2, b["y"] - b["h"] / 2
    )
    return max(0.0, w) * max(0.0, h)


def _compute_layout(files, scale, vmin, half_w, half_h, bound_w, bound_h):
    """One layout pass at a given scale — the phyllotaxis spiral bounded to a
    central oval, clamped on screen. Returns (placed, fallbacks)."""
    placed = []
    fallbacks = 0
    for index, file in enumerate(files):
        h = hash_str(file)
        size_vmin = (SIZE_MIN_VMIN + (h % SIZE_SPAN_VMIN)) * scale
        size_px = size_vmin * vmin
        image_h = size_px * PLATE_ASPECT
        box_w = size_px + GAP_VMIN * vmin
        box_h = image_h + caption_px(image_h) + GAP_VMIN * vmin
        jitter_a = (((h >> 8) % 100) / 100 - 0.5) * 0.5  # ±0.25 rad
        clamp_x = max(0.0, bound_w - size_px / 2)
        clamp_y = max(0.0, bound_h - (image_h + caption_px(image_h)) / 2)
        best = None
        best_overlap = math.inf
        t = index
        for _ in range(MAX_TRIES):
            angle = t * GOLDEN_ANGLE + jitter_a
            reach = math.sqrt(t) / math.sqrt(MAX_INDEX)
            x = math.cos(angle) * reach * half_w
            y = math.sin(angle) * reach * half_h
            x = max(-clamp_x, min(clamp_x, x))
            y = max(-clamp_y, min(clamp_y, y))
            box = {"x": x, "y": y, "w": box_w, "h": box_h}
            overlap = sum(overlap_area(box, o["box"]) for o in placed)
            if overlap == 0:
                best, best_overlap = box, 0
                break
            if overlap < best_overlap:
                best, best_overlap = box, overlap
            t += SPIRAL_STEP
        if best_overlap > 0:
            fallbacks += 1
        placed.append(
            {"box": best, "file": file, "size_vmin": size_vmin, "index": index}
        )
    return placed, fallbacks


def compute_collage(files, w: float, h: float, band_top: float) -> list[Placement]:
    if w <= 0 or h <= 0:
        return []
    vmin = min(w, h) / 100
    band_h = h - band_top
    y_offset = band_top / 2
    half_h = (CLUSTER_H_FRAC * band_h) / 2
    half_w = min((CLUSTER_W_FRAC * w) / 2, half_h * CLUSTER_ASPECT)
    natural_area = 0.0
    for file in files:
        s = (SIZE_MIN_VMIN + (hash_str(file) % SIZE_SPAN_VMIN)) * vmin
        image_h = s * PLATE_ASPECT
        natural_area += (s + GAP_VMIN * vmin) * (
            image_h + caption_px(image_h) + GAP_VMIN * vmin
        )
    cluster_area = math.pi * half_w * half_h
    scale = min(1.0, math.sqrt((FILL_FACTOR * cluster_area) / (natural_area or 1)))
    bound_w, bound_h = w / 2, band_h / 2
    placed, fallbacks = _compute_layout(
        files, scale, vmin, half_w, half_h, bound_w, bound_h
    )
    i = 0
    while i < SHRINK_RETRIES and fallbacks > 0:
        scale *= SHRINK_STEP
        placed, fallbacks = _compute_layout(
            files, scale, vmin, half_w, half_h, bound_w, bound_h
        )
        i += 1
    return [
        Placement(
            file=p["file"],
            x=p["box"]["x"],
            y=p["box"]["y"] + y_offset,
            size_vmin=p["size_vmin"],
            z=TOP_Z - p["index"],
        )
        for p in placed
    ]
