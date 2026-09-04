"""The panel's packed rosette (owner, 2026-09-04): newest bird on the
sheet's centre, largest; everything else as close to it as it fits, in
recency order; the whole arrangement as large as the sheet allows; no
randomness."""

import math

import pytest

from bird_painter.frame_layout import (
    BOTTOM_MARGIN,
    MAX_NEWEST_WIDTH,
    NEWEST_SHARE,
    OLD_WEIGHT_MIN,
    RECENT_COUNT,
    SIDE_MARGIN,
    _layout,
    _rows,
    _Sheet,
    compute_frame_layout,
)

# The three real sheets: the e-paper frame, the 7" in landscape, the 10" in
# portrait — with what the renderer actually passes (each bird's own ink
# aspect, a fixed caption height, each caption's measured width).
SHEETS = {"frame": (1600, 1200, 54), "seven": (1280, 720, 0), "ten": (1200, 1920, 0)}
ASPECTS = [1.05, 0.72, 1.55, 0.88, 1.30, 0.95, 1.80, 0.65, 1.20, 1.40, 0.80, 1.10]
CAPTIONS = [150, 220, 190, 260, 170, 210, 240, 160, 200, 180, 230, 175]
CAPTION_PX = 33.0


def place(count: int, sheet: str = "frame", salt: str = ""):
    w, h, band = SHEETS[sheet]
    files = [f"bird{salt}{i:02d}.jpg" for i in range(count)]  # newest first
    return compute_frame_layout(
        files,
        w,
        h,
        band,
        aspects=ASPECTS[:count],
        caption_px=CAPTION_PX,
        caption_widths=CAPTIONS[:count],
    )


def footprint(p, index, sheet):
    """The box the renderer occupies: the cell, at least the caption's width,
    plus the caption hanging below."""
    w, h, _ = SHEETS[sheet]
    vmin = min(w, h) / 100
    fw = max(p.size_vmin * vmin, CAPTIONS[index])
    fh = p.height_vmin * vmin
    left, top = p.x + w / 2 - fw / 2, p.y + h / 2 - fh / 2
    return (left, top, left + fw, top + fh + CAPTION_PX)


def fill(placements, sheet):
    w, h, band = SHEETS[sheet]
    vmin = min(w, h) / 100
    area = sum(
        (p.size_vmin * vmin) * (p.height_vmin * vmin + CAPTION_PX) for p in placements
    )
    return area / (w * (1 - 2 * SIDE_MARGIN) * (h * (1 - BOTTOM_MARGIN) - band))


@pytest.mark.parametrize("sheet", list(SHEETS))
@pytest.mark.parametrize("count", [1, 2, 3, 6, 12])
def test_the_newest_bird_sits_on_the_sheets_centre(sheet, count):
    w, h, band = SHEETS[sheet]
    newest = place(count, sheet)[0]
    assert newest.x == pytest.approx(0, abs=0.5)
    # Vertically: the usable sheet's centre, its own caption counted in.
    usable_mid = band + (h * (1 - BOTTOM_MARGIN) - band) / 2 - CAPTION_PX / 2
    assert newest.y + h / 2 == pytest.approx(usable_mid, abs=0.5)
    assert newest.z == count  # on top


@pytest.mark.parametrize("sheet", list(SHEETS))
@pytest.mark.parametrize("count", [2, 3, 6, 9, 12])
def test_nothing_overlaps_and_everything_stays_on_the_sheet(sheet, count):
    w, h, band = SHEETS[sheet]
    boxes = [footprint(p, i, sheet) for i, p in enumerate(place(count, sheet))]
    assert len(boxes) == count
    for i, a in enumerate(boxes):
        assert a[0] >= w * SIDE_MARGIN - 0.5 and a[2] <= w * (1 - SIDE_MARGIN) + 0.5
        assert a[1] >= band - 0.5 and a[3] <= h * (1 - BOTTOM_MARGIN) + 0.5
        for b in boxes[i + 1 :]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (
                i,
                a,
                b,
            )


@pytest.mark.parametrize("sheet", list(SHEETS))
def test_the_sheet_is_filled(sheet):
    """The owner's ask: "fills the screen as much as possible". The scatter
    managed ~0.15 at six birds; the rosette holds nearly half the sheet
    there (measured 0.44–0.50 on the three panels) and keeps filling as
    the wall grows."""
    assert fill(place(6, sheet), sheet) >= 0.42
    assert fill(place(12, sheet), sheet) >= 0.55
    assert fill(place(3, sheet), sheet) >= 0.34


@pytest.mark.parametrize("sheet", list(SHEETS))
def test_the_birds_stay_one_cluster(sheet):
    """No bird alone in a corner: every bird's footprint is within three
    vmin of some other bird's (measured: under two on all three sheets) —
    the sheet fills outward from the middle, so the cluster has no islands.
    The scatter placed its old birds "wherever emptiest", tens of vmin from
    anything."""
    w, h, _ = SHEETS[sheet]
    placements = place(6, sheet)
    boxes = [footprint(p, i, sheet) for i, p in enumerate(placements)]
    reach = 3 * min(w, h) / 100
    for i, a in enumerate(boxes):
        others = [b for j, b in enumerate(boxes) if j != i]
        gap = min(
            max(0, max(b[0] - a[2], a[0] - b[2]))
            + max(0, max(b[1] - a[3], a[1] - b[3]))
            for b in others
        )
        assert gap <= reach, (i, gap, reach)


def test_growth_in_place_is_capped_by_a_birds_own_weight():
    """Three birds on the 7" with tall captions leave room: without the
    INFLATE_MAX cap the oldest grew to 0.94 of the newest and the size story
    was gone (the six-bird guard doesn't see this — at six nothing has room
    to grow)."""
    files = [f"b{i}.jpg" for i in range(3)]
    placements = compute_frame_layout(
        files,
        1280,
        720,
        0,
        aspects=ASPECTS[:3],
        caption_px=66,
        caption_widths=CAPTIONS[:3],
    )
    areas = [p.size_vmin * p.height_vmin for p in placements]
    assert areas[2] <= areas[0] * 0.62 * 1.12 + 1e-6
    assert areas[1] <= areas[0] * NEWEST_SHARE + 1e-6


def test_sizes_follow_recency():
    """Newest largest, the recent five a step below, older ones tapering —
    the size story from the scatter, kept. Growth in place may lift a bird,
    but never past the one a rank newer, nor past NEWEST_SHARE of the newest."""
    placements = place(12)
    areas = [p.size_vmin * p.height_vmin for p in placements]
    assert areas[0] == max(areas)
    assert max(areas[1:]) <= areas[0] * NEWEST_SHARE + 1e-6
    assert min(areas[1 : 1 + RECENT_COUNT]) > max(areas[1 + RECENT_COUNT :])
    assert min(areas) >= areas[0] * OLD_WEIGHT_MIN * 0.9


@pytest.mark.parametrize("sheet", list(SHEETS))
def test_a_six_bird_wall_still_shows_its_recency(sheet):
    """Six birds is the newest plus exactly RECENT_COUNT others — the
    ordinary wall — and the ring tapers within itself: the oldest of six is
    visibly smaller than the newest (owner, 2026-08-20: a flat ring read as
    "nothing gets smaller with age"). Growth in place is capped at 1.12×
    a bird's own weight so this holds after inflation too."""
    areas = [p.size_vmin * p.height_vmin for p in place(6, sheet)]
    assert areas[-1] <= areas[0] * 0.72
    assert areas[1] > areas[-1]


def test_layout_is_deterministic_and_has_no_dice():
    a = [(p.x, p.y, p.size_vmin) for p in place(7, salt="a")]
    b = [(p.x, p.y, p.size_vmin) for p in place(7, salt="a")]
    assert a == b
    # A different set may land differently (its spiral starts elsewhere).
    c = [(p.x, p.y, p.size_vmin) for p in place(7, salt="b")]
    assert a != c


@pytest.mark.parametrize("sheet", list(SHEETS))
def test_a_lone_bird_is_big_but_not_a_poster(sheet):
    """One bird takes the newest's cap on whichever side binds — width on a
    landscape sheet, height on a portrait one — and no more."""
    w, h, band = SHEETS[sheet]
    (only,) = place(1, sheet)
    vmin = min(w, h) / 100
    usable_w, usable_h = w * (1 - 2 * SIDE_MARGIN), h * (1 - BOTTOM_MARGIN) - band
    width_share = only.size_vmin * vmin / usable_w
    height_share = (only.height_vmin * vmin + CAPTION_PX) / usable_h
    assert width_share <= MAX_NEWEST_WIDTH + 0.005
    assert width_share >= MAX_NEWEST_WIDTH - 0.005 or height_share >= 0.92 - 0.005


def test_an_empty_or_impossible_sheet_places_nothing():
    assert compute_frame_layout([], 1600, 1200, 54) == []
    assert compute_frame_layout(["a.jpg"], 0, 0, 0) == []
    # A sheet too small for one caption: nothing, not an exception.
    assert (
        compute_frame_layout(["a.jpg"], 60, 40, 0, caption_px=33, caption_widths=[150])
        == []
    )


def test_when_nothing_packs_around_the_centre_the_sheet_gets_rows_not_nothing():
    """The scatter's forced pass could not fail; the rosette's scan can (a
    dozen birds with two-line captions on a small sheet). Then the birds go
    in centred rows — the wall is never blank, because the browser freezes
    on an empty plan and the e-paper spends a redraw on it."""
    sheet = _Sheet(
        left=36,
        top=0,
        uw=1128,
        uh=1824,
        gap=10,
        caption_px=66,
        caption_ws=(500.0,) * 12,
        base_angle=0.3,
        vmin=12,
    )
    cells = [(80.0, 100.0)] * 12
    spots = _rows(cells, sheet)
    assert spots is not None and len(spots) == 12
    rects = [r for _, _, r in spots]
    for i, a in enumerate(rects):
        assert sheet.inside(a)
        for b in rects[i + 1 :]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
    # Centred as a block: the rows' left and right margins match.
    assert min(r[0] for r in rects) - sheet.left == pytest.approx(
        sheet.left + sheet.uw - max(r[2] for r in rects), abs=1
    )
    # A single footprint wider than the sheet is the one honest failure.
    assert _rows([(2000.0, 10.0)], sheet) is None
    # …and the public function reaches the rows when the spiral can't pack:
    # twelve wide-captioned birds on a 7" sheet packed around the centre
    # would need a scale below the floor; they still all land.
    placements = compute_frame_layout(
        [f"b{i}.jpg" for i in range(12)],
        1280,
        720,
        0,
        aspects=[1.2] * 12,
        caption_px=60,
        caption_widths=[400.0] * 12,
    )
    assert len(placements) == 12
    # …with the invariants intact, and at a size the rows had room for
    # (their own scan, not the floor the spiral gave up at).
    vmin = 7.2
    boxes = []
    for p in placements:
        fw, fh = max(p.size_vmin * vmin, 400.0), p.height_vmin * vmin
        left, top = p.x + 640 - fw / 2, p.y + 360 - fh / 2
        boxes.append((left, top, left + fw, top + fh + 60))
    for i, a in enumerate(boxes):
        assert (
            a[0] >= 1280 * SIDE_MARGIN - 0.5 and a[2] <= 1280 * (1 - SIDE_MARGIN) + 0.5
        )
        assert a[1] >= -0.5 and a[3] <= 720 * (1 - BOTTOM_MARGIN) + 0.5
        for b in boxes[i + 1 :]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
    assert placements[0].size_vmin * vmin > 40  # rows: 50 px, not the spiral's 28


def test_the_plan_is_memoised_on_its_inputs():
    """Deterministic, so the wall's poll and the frame's two-layer render
    ask for the same plan again and again: they must not pay for it twice."""
    _layout.cache_clear()
    a = place(6, "ten")
    hits = _layout.cache_info().hits
    b = place(6, "ten")
    assert _layout.cache_info().hits == hits + 1
    assert a == b and a is not b  # a fresh list each time, the same content


def test_a_crowded_sheet_still_places_every_bird():
    placements = place(12, "seven")
    assert len(placements) == 12
    assert len({p.file for p in placements}) == 12
    assert not any(math.isnan(p.x) or math.isnan(p.y) for p in placements)
