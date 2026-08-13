"""The frame's focal scatter, encoding the owner's dictated rules
(2026-08-13): newest bird largest on an anchor inside a central box, the
five before it around it a step smaller, older birds smaller still and
spread wherever the sheet is emptiest, deterministic jitter, whole sheet
covered."""

import math

from bird_painter.frame_layout import (
    ANCHOR_BOX_AREA,
    BOTTOM_MARGIN,
    CAPTION_SPACE,
    PLATE_ASPECT,
    RECENT_COUNT,
    SIDE_MARGIN,
    compute_frame_scatter,
)

PANEL = (1600, 1200)
BAND_TOP = 54
VMIN = min(PANEL) / 100


def place(count: int, salt: str = ""):
    files = [f"bird{salt}{i:02d}.jpg" for i in range(count)]  # newest first
    return compute_frame_scatter(files, *PANEL, BAND_TOP)


def footprint(p):
    w = p.size_vmin * VMIN
    h = p.height_vmin * VMIN
    top = (p.y + PANEL[1] / 2) - h / 2
    return (
        (p.x + PANEL[0] / 2) - w / 2,
        top,
        (p.x + PANEL[0] / 2) + w / 2,
        top + h * (1 + CAPTION_SPACE),
    )


def test_newest_is_largest_then_recent_then_tapering_old():
    sizes = [p.size_vmin for p in place(12)]
    assert sizes[0] > sizes[1], "the newest bird dominates"
    recent = sizes[1 : RECENT_COUNT + 1]
    assert max(recent) - min(recent) < 1e-9, "the recent five share one size"
    old = sizes[RECENT_COUNT + 1 :]
    assert all(o < recent[0] for o in old), "older birds are smaller still"
    assert old == sorted(old, reverse=True), "and shrink with age"


def test_the_anchor_sits_inside_the_central_box():
    """A box holding ~30% of the area has sides sqrt(0.3) of the sheet's —
    the newest bird's centre must land inside it, wherever the seed falls."""
    half_w = PANEL[0] * math.sqrt(ANCHOR_BOX_AREA) / 2
    half_h = PANEL[1] * math.sqrt(ANCHOR_BOX_AREA) / 2
    for salt in ("", "a", "b", "c", "d"):
        newest = place(9, salt)[0]
        assert abs(newest.x) <= half_w + 1
        assert abs(newest.y) <= half_h + BAND_TOP  # usable area sits low

def test_the_recent_five_gather_around_the_newest():
    placements = place(12)
    newest = placements[0]

    def distance(p):
        return math.hypot(p.x - newest.x, p.y - newest.y)

    ring = [distance(p) for p in placements[1 : RECENT_COUNT + 1]]
    others = [distance(p) for p in placements[RECENT_COUNT + 1 :]]
    # Not every old bird is far — they fill gaps — but the ring must be
    # decisively nearer on average: that's what "surround it" means.
    assert sum(ring) / len(ring) < 0.7 * (sum(others) / len(others))


def test_nothing_overlaps():
    for count in (2, 5, 8, 12):
        rects = [footprint(p) for p in place(count)]
        for i, a in enumerate(rects):
            for b in rects[i + 1 :]:
                assert (
                    a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
                ), f"{count} birds: {a} collides with {b}"


def test_everything_stays_on_the_sheet():
    for count in (1, 3, 7, 12, 16):
        for p in place(count):
            x0, y0, x1, y1 = footprint(p)
            assert x0 >= PANEL[0] * SIDE_MARGIN - 1
            assert x1 <= PANEL[0] * (1 - SIDE_MARGIN) + 1
            assert y0 >= BAND_TOP - 1
            assert y1 <= PANEL[1] * (1 - BOTTOM_MARGIN) + 1


def test_the_whole_sheet_is_covered_when_there_are_many_birds():
    """The owner's balance rule: an off-centre anchor pushes the rest to the
    other side, so the composition stays roughly uniform. Centres must span
    most of the sheet and their centroid must sit near the middle."""
    placements = place(12)
    xs = [p.x for p in placements]
    ys = [p.y for p in placements]
    assert max(xs) - min(xs) > 0.55 * PANEL[0], "spread across the width"
    assert max(ys) - min(ys) > 0.45 * PANEL[1], "and down the height"
    assert abs(sum(xs) / len(xs)) < 0.12 * PANEL[0], "centroid near centre"
    left = sum(1 for x in xs if x < 0)
    assert 3 <= left <= 9, "neither half of the sheet is abandoned"


def test_layout_is_deterministic_per_live_set():
    """The frame redraws only when the bytes change; a layout that wandered
    per render would wear the panel for nothing."""
    assert place(9) == place(9)
    assert place(9) != place(9, salt="other")  # a new set reshuffles


def test_a_lone_bird_is_big_but_not_a_poster():
    lone = place(1)[0]
    assert lone.size_vmin * VMIN > 0.30 * PANEL[0]
    assert lone.size_vmin * VMIN <= 0.46 * PANEL[0] * (1 - 2 * SIDE_MARGIN) + 1
    plate_h = lone.height_vmin * VMIN
    assert abs(plate_h / (lone.size_vmin * VMIN) - PLATE_ASPECT) < 1e-6


def test_an_empty_wall_places_nothing():
    assert compute_frame_scatter([], *PANEL, BAND_TOP) == []
    assert compute_frame_scatter(["a.jpg"], 0, 0, 0) == []


def test_smaller_birds_sit_further_out_than_larger_ones():
    """Owner: keep the smaller birds on the outskirts, the larger inside. The
    newest (largest) anchors the centre region; the oldest (smallest) should
    average a decisively greater distance from the sheet's centre than the
    recent five."""
    centre_y = BAND_TOP / 2  # usable area sits below the top margin

    def from_centre(p):
        return math.hypot(p.x, p.y - centre_y)

    for salt in ("", "a", "b"):
        placements = place(12, salt)
        ring = [from_centre(p) for p in placements[1 : RECENT_COUNT + 1]]
        oldest = [from_centre(p) for p in placements[-3:]]  # the smallest three
        assert sum(oldest) / len(oldest) > 1.15 * (sum(ring) / len(ring)), salt
