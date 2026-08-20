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


# What the renderer actually passes: each bird's own ink aspect, a fixed
# caption height in pixels, and each caption's measured width. The suite used
# to call compute_frame_scatter with none of them, which is why a placement bug
# reached the panel — with plain unit cells the rosette never got wide enough
# to leave a tempting gap between its petals.
IN_THE_WILD_ASPECTS = [1.05, 0.72, 1.55, 0.88, 1.30, 0.95, 1.80, 0.65, 1.20,
                       1.40, 0.80, 1.10, 1.62, 0.90, 1.15, 0.75]
IN_THE_WILD_CAPTIONS = [150, 220, 190, 260, 170, 210, 240, 160, 200, 180,
                        230, 175, 205, 145, 250, 165]
CAPTION_PX = 33.0


def place_as_rendered(count: int, salt: str = ""):
    files = [f"bird{salt}{i:02d}.jpg" for i in range(count)]
    return compute_frame_scatter(
        files, *PANEL, BAND_TOP,
        aspects=IN_THE_WILD_ASPECTS[:count],
        caption_px=CAPTION_PX,
        caption_widths=IN_THE_WILD_CAPTIONS[:count],
    )


def rendered_footprint(p, index):
    """The box the renderer really occupies: the caption's measured width is a
    floor on it, so two small neighbours can't overlap each other's lettering."""
    w = max(p.size_vmin * VMIN, IN_THE_WILD_CAPTIONS[index])
    h = p.height_vmin * VMIN
    left = (p.x + PANEL[0] / 2) - w / 2
    top = (p.y + PANEL[1] / 2) - h / 2
    return (left, top, left + w, top + h + CAPTION_PX)


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
    assert sizes == sorted(sizes, reverse=True), "size only ever falls with age"
    recent = sizes[1 : RECENT_COUNT + 1]
    assert recent[0] > recent[-1], "the ring itself tapers"
    old = sizes[RECENT_COUNT + 1 :]
    assert all(o < recent[-1] for o in old), "older birds are smaller still"


def test_a_six_bird_wall_still_shows_its_recency():
    """Six birds is the newest plus exactly RECENT_COUNT others, so the
    old-bird taper never runs — and with a flat ring band nothing on the panel
    got smaller with age at all (owner, 2026-08-20)."""
    sizes = [p.size_vmin for p in place(6)]
    assert sizes == sorted(sizes, reverse=True)
    assert sizes[-1] < 0.85 * sizes[1], "the oldest is visibly below the newest"


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
    """Owner: keep the smaller birds on the outskirts, the larger inside.

    Measured from the NEWEST bird, which is where the composition's weight
    sits — the anchor can be well off-centre, and against the sheet's middle
    the smallest birds scored well by sitting between the anchor and the far
    edge, which is inside the ring they belong outside of."""
    for salt in ("", "a", "b"):
        placements = place(12, salt)
        newest = placements[0]

        def from_focus(p, newest=newest):
            return math.hypot(p.x - newest.x, p.y - newest.y)

        ring = [from_focus(p) for p in placements[1 : RECENT_COUNT + 1]]
        oldest = [from_focus(p) for p in placements[-3:]]  # the smallest three
        assert sum(oldest) / len(oldest) > 1.15 * (sum(ring) / len(ring)), salt


def test_no_old_bird_sits_inside_the_ring_on_a_real_wall():
    """The averages above passed while the single oldest bird sat nearer the
    anchor than every other bird on the panel (QA, 2026-08-20). A gap BETWEEN
    two ring petals scores well on emptiness, so the outward pull — a score
    term — was outvoted. No old bird may sit inside the body of the rosette,
    and the check has to run on production-shaped input: with plain unit cells
    the rosette is too tight to leave such a gap.

    The bar is the ring's MEDIAN radius. A single petal can creep far out
    hunting for space, and holding every later bird beyond that outlier is a
    bar the sheet may simply not have room for."""
    for count in (7, 9, 12, 16):
        for salt in ("", "a", "b", "c"):
            placements = place_as_rendered(count, salt)
            anchor = placements[0]

            def distance(p, anchor=anchor):
                return math.hypot(p.x - anchor.x, p.y - anchor.y)

            ring = sorted(distance(p) for p in placements[1 : RECENT_COUNT + 1])
            old = [distance(p) for p in placements[RECENT_COUNT + 1 :]]
            body = ring[len(ring) // 2]
            # A hair of tolerance: on a full sheet the last bird can find no
            # non-overlapping spot outside the rosette at all, and a placement
            # just inside the bar beats no placement. The bug this guards
            # against was not marginal — an old bird at 291 against a ring at
            # 537 — so a 5% skirt still catches it with room to spare.
            assert min(old) >= body * 0.95, (
                f"{count} birds, salt {salt!r}: an old bird at {min(old):.0f} "
                f"sits inside the rosette's body at {body:.0f}"
            )


def test_a_real_wall_never_overlaps_or_runs_off_the_sheet():
    """The same production shape, against the two invariants that matter most:
    captions are fixed-size, so a small bird's lettering is wider than the bird
    and is what actually collides."""
    for count in (1, 2, 6, 9, 12, 16):
        for salt in ("", "a", "b"):
            rects = [
                rendered_footprint(p, i)
                for i, p in enumerate(place_as_rendered(count, salt))
            ]
            for a in rects:
                assert a[0] >= PANEL[0] * SIDE_MARGIN - 1
                assert a[2] <= PANEL[0] * (1 - SIDE_MARGIN) + 1
                assert a[1] >= BAND_TOP - 1
                assert a[3] <= PANEL[1] * (1 - BOTTOM_MARGIN) + 1
            for i, a in enumerate(rects):
                for b in rects[i + 1 :]:
                    assert (
                        a[2] <= b[0] or b[2] <= a[0]
                        or a[3] <= b[1] or b[3] <= a[1]
                    ), f"{count} birds, salt {salt!r}: {a} collides with {b}"


def test_a_crowded_sheet_still_places_every_bird_on_it():
    """The forced pass — the one where overlap is permitted as a last resort —
    had no test reaching it (QA, 2026-08-20), which is how a regression in the
    candidate chooser could have gone unnoticed. Squeeze sixteen birds with
    wide captions onto a small sheet: every bird must still be placed, and
    still be on the sheet, however tight it gets."""
    files = [f"crowd{i:02d}.jpg" for i in range(16)]
    placements = compute_frame_scatter(
        files, 500, 380, 20,
        aspects=IN_THE_WILD_ASPECTS,
        caption_px=18.0,
        caption_widths=[90] * 16,
    )
    assert len(placements) == len(files)
    for p in placements:
        w, h = p.size_vmin * 3.8, p.height_vmin * 3.8  # vmin of a 500x380 sheet
        assert w > 0 and h > 0
        assert -250 <= p.x <= 250 and -190 <= p.y <= 190


def test_a_sheet_too_small_for_one_caption_renders_nothing():
    """Rather than raising out of `zip(..., strict=True)` under a docstring
    that promises the function cannot fail outright.

    Shrinking always eventually fits the BIRDS — but a caption's width is
    fixed pixels and doesn't shrink with them, so a caption wider than the
    sheet can never be placed at any scale. That's the reachable failure."""
    assert compute_frame_scatter(
        ["a.jpg", "b.jpg"], 200, 150, 10,
        caption_px=12.0,
        caption_widths=[400.0, 400.0],  # each caption is twice the sheet's width
    ) == []
