"""The frame's grid: the panel is a fixed sheet, so it gets rows that fill it
rather than the browser wall's spiral. Rules from the owner, after seeing the
spiral on the real panel (2026-08-13): fewer birds should be bigger, and no
large horizontal gaps."""

from bird_painter.frame_layout import (
    compute_frame_grid,
    rows_for,
    split_rows,
)

PANEL = (1600, 1200)
BAND_TOP = 150


def place(count: int):
    files = [f"bird{i}.jpg" for i in range(count)]
    return compute_frame_grid(files, *PANEL, BAND_TOP)


def test_up_to_three_birds_share_one_row():
    for count in (1, 2, 3):
        assert rows_for(count) == 1
        assert len({round(p.y, 3) for p in place(count)}) == 1


def test_up_to_six_birds_use_two_rows():
    for count in (4, 5, 6):
        assert rows_for(count) == 2
        assert len({round(p.y, 3) for p in place(count)}) == 2


def test_fewer_birds_are_drawn_larger():
    """Rule 1 and 2: one row fills the panel, two rows are somewhat smaller,
    and it keeps shrinking from there. A spiral sized for twelve looked lost
    when two birds had sung."""
    sizes = [place(count)[0].size_vmin for count in (2, 5, 9, 16)]
    assert sizes == sorted(sizes, reverse=True), sizes
    assert sizes[0] > 1.8 * sizes[-1], "a lone pair should dominate the sheet"


def test_no_large_horizontal_gap_between_neighbours():
    """Rule 3. The spiral could leave two birds at opposite edges of a wide
    panel with a void between them; even columns make that impossible."""
    for count in (2, 3, 4, 5, 7, 11, 12):
        placements = place(count)
        rows: dict[float, list] = {}
        for p in placements:
            rows.setdefault(round(p.y, 3), []).append(p)
        for row in rows.values():
            row.sort(key=lambda p: p.x)
            width = row[0].size_vmin * (min(PANEL) / 100)
            for left, right in zip(row, row[1:], strict=False):
                gap = (right.x - left.x) - width
                assert gap < 0.5 * width, f"{count} birds: gap {gap:.0f}px"


def test_every_bird_stays_on_the_panel():
    for count in (1, 3, 7, 12, 16):
        for p in place(count):
            w = p.size_vmin * (min(PANEL) / 100)
            h = (p.height_vmin or 0) * (min(PANEL) / 100)
            assert -PANEL[0] / 2 <= p.x - w / 2 and p.x + w / 2 <= PANEL[0] / 2
            # the caption hangs below the plate, so leave room for it too
            assert p.y + h / 2 < PANEL[1] / 2, f"{count} birds run off the bottom"


def test_rows_are_balanced_with_the_short_row_last():
    assert split_rows(7, 3) == [3, 2, 2]
    assert split_rows(12, 3) == [4, 4, 4]
    assert split_rows(5, 2) == [3, 2]
    assert sum(split_rows(11, 4)) == 11


def test_an_empty_wall_places_nothing():
    assert compute_frame_grid([], *PANEL, BAND_TOP) == []
    assert compute_frame_grid(["a.jpg"], 0, 0, 0) == []
