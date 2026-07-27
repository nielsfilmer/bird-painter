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
SIZE_MIN_VMIN = 16
SIZE_SPAN_VMIN = 5  # plate width 16–20 vmin
MAX_INDEX = 12  # matches the wall's live cap
PLATE_ASPECT = 5 / 4  # painted image is 4:5 portrait
CAPTION_ALLOWANCE = 1.1
CAPTION_FLOOR_PX = 26
TOP_Z = 200
GAP_VMIN = 0.2
SPIRAL_STEP = 0.22
MAX_TRIES = 220
GROW_FACTOR = 1.12  # widen-to-fit: widen the oval by this per step…
GROW_STEPS = 24  # …up to this many, until the set fits (or caps out)
FILL_FACTOR = 0.92  # when width-capped: plates claim at most this share
SHRINK_RETRIES = 8
SHRINK_STEP = 0.9
CLUSTER_W_FRAC = 0.92  # oval may widen to at most this fraction of the width
CLUSTER_H_FRAC = 0.88  # oval height: this fraction of the sub-title band
ROW_LIMIT = 3  # up to this many birds: one horizontal row (see layout.js)

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


def _compute_layout(
    entries, scale, vmin, half_w, half_h, bound_w, bound_h, placed, clear_half_h=0.0
):
    """One layout pass for a batch of (file, index) entries — mirrors
    computeLayout in static/layout.js: spiral by LOCAL batch position, avoid
    everything already in `placed` (appended to). Returns fallbacks."""
    fallbacks = 0
    for local, (file, index) in enumerate(entries):
        h = hash_str(file)
        size_vmin = (SIZE_MIN_VMIN + (h % SIZE_SPAN_VMIN)) * scale
        size_px = size_vmin * vmin
        image_h = size_px * PLATE_ASPECT
        box_w = size_px + GAP_VMIN * vmin
        box_h = image_h + caption_px(image_h) + GAP_VMIN * vmin
        jitter_a = (((h >> 8) % 100) / 100 - 0.5) * 0.5  # ±0.25 rad
        # Clamp plate centres to the oval extents AND on screen (see layout.js).
        clamp_x = min(half_w, max(0.0, bound_w - size_px / 2))
        clamp_y = min(half_h, max(0.0, bound_h - (image_h + caption_px(image_h)) / 2))
        best = None
        best_overlap = math.inf
        t = local
        for _ in range(MAX_TRIES):
            angle = t * GOLDEN_ANGLE + jitter_a
            reach = math.sqrt(t) / math.sqrt(MAX_INDEX)
            x = math.cos(angle) * reach * half_w
            y = math.sin(angle) * reach * half_h
            x = max(-clamp_x, min(clamp_x, x))
            y = max(-clamp_y, min(clamp_y, y))
            if clear_half_h > 0:
                # Owner rule: newer birds go ABOVE or BELOW the shelf, never
                # level with it (see layout.js).
                min_y = min(clear_half_h + box_h / 2, clamp_y)
                if abs(y) < min_y:
                    y = -min_y if (y < 0 or (y == 0 and math.sin(angle) < 0)) else min_y
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
    return fallbacks


def compute_collage(files, w: float, h: float, band_top: float) -> list[Placement]:
    if w <= 0 or h <= 0:
        return []
    vmin = min(w, h) / 100
    band_h = h - band_top
    y_offset = band_top / 2
    natural_area = 0.0
    for file in files:
        s = (SIZE_MIN_VMIN + (hash_str(file) % SIZE_SPAN_VMIN)) * vmin
        image_h = s * PLATE_ASPECT
        natural_area += (s + GAP_VMIN * vmin) * (
            image_h + caption_px(image_h) + GAP_VMIN * vmin
        )
    max_half_w = (CLUSTER_W_FRAC * w) / 2
    bound_w, bound_h = w / 2, band_h / 2
    max_box_w = 1.0
    max_box_h = 1.0
    for file in files:
        s = (SIZE_MIN_VMIN + (hash_str(file) % SIZE_SPAN_VMIN)) * vmin
        image_h = s * PLATE_ASPECT
        max_box_w = max(max_box_w, s + GAP_VMIN * vmin)
        max_box_h = max(max_box_h, image_h + caption_px(image_h) + GAP_VMIN * vmin)
    # The rule (mirrors static/layout.js): the up-to-ROW_LIMIT OLDEST birds
    # keep a single horizontal row across the band centre for good; every
    # newer bird stacks vertically around that shelf.
    entries = [(file, index) for index, file in enumerate(files)]
    row_count = min(ROW_LIMIT, len(entries))
    row_entries = entries[len(entries) - row_count :]
    tall_entries = entries[: len(entries) - row_count]
    full_half_h = (CLUSTER_H_FRAC * band_h) / 2

    def place_row(entries, scale, placed):
        # The shelf is PACKED, not spiralled: oldest→newest runs left→right,
        # centred as a block — members never swap sides as the wall grows.
        # Wider-than-screen counts as fallbacks so the shrink loop engages.
        boxes = []
        for file, index in entries:
            size_px = (SIZE_MIN_VMIN + (hash_str(file) % SIZE_SPAN_VMIN)) * scale * vmin
            image_h = size_px * PLATE_ASPECT
            boxes.append(
                {
                    "file": file, "index": index, "size_px": size_px,
                    "box_w": size_px + GAP_VMIN * vmin,
                    "box_h": image_h + caption_px(image_h) + GAP_VMIN * vmin,
                }
            )
        ordered = list(reversed(boxes))  # entries slice is newest-first
        total_w = sum(b["box_w"] for b in ordered)
        fallbacks = 0
        cursor = -total_w / 2
        for b in ordered:
            x = cursor + b["box_w"] / 2
            cursor += b["box_w"]
            if abs(x) + b["size_px"] / 2 > bound_w:
                fallbacks += 1
            placed.append(
                {
                    "box": {"x": x, "y": 0, "w": b["box_w"], "h": b["box_h"]},
                    "file": b["file"],
                    "size_vmin": b["size_px"] / vmin,
                    "index": b["index"],
                }
            )
        return fallbacks

    def layout_pass(scale, half_w):
        placed: list = []
        fallbacks = place_row(row_entries, scale, placed)
        row_clear_half = max((p["box"]["h"] / 2 for p in placed), default=0.0)
        fallbacks += _compute_layout(
            tall_entries, scale, vmin, half_w, full_half_h, bound_w, bound_h,
            placed, row_clear_half,
        )
        return placed, fallbacks

    half_w0 = min(max_half_w, max_box_w / 2)
    scale = 1.0
    half_w = half_w0
    placed: list = []
    fallbacks = 0
    k = 1.0
    for _ in range(GROW_STEPS):
        half_w = min(max_half_w, half_w0 * k)
        placed, fallbacks = layout_pass(scale, half_w)
        if fallbacks == 0 or half_w >= max_half_w:
            break
        k *= GROW_FACTOR
    if fallbacks > 0:
        seed_half_h = full_half_h if len(tall_entries) > 0 else max_box_h / 2
        cluster_area = math.pi * half_w * seed_half_h
        scale = min(1.0, math.sqrt((FILL_FACTOR * cluster_area) / (natural_area or 1)))
        placed, fallbacks = layout_pass(scale, half_w)
        i = 0
        while i < SHRINK_RETRIES and fallbacks > 0:
            scale *= SHRINK_STEP
            placed, fallbacks = layout_pass(scale, half_w)
            i += 1
    # Preserve the input (newest-first) order in the result.
    placed.sort(key=lambda p: p["index"])
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
