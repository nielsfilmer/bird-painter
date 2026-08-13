"""Placement for the e-paper frame — a fixed panel, laid out as a wall chart.

The browser wall's spiral collage (`wall_layout.compute_collage`) is built for
a window that can be any shape and that the viewer sits close to. The frame is
neither: it's a fixed 1600×1200 sheet on a wall, looked at from across a room,
and it never reflows. Optimising for it means something a spiral can't do —
fill the sheet.

So the frame gets rows instead:

- **Fewer birds, bigger birds.** One row of up to three fills the panel; two
  rows are a little smaller; beyond that the rows keep shrinking to fit. A
  spiral sized for twelve looks lost when only two birds have sung.
- **Even columns, no holes.** Every row shares one gutter and is centred, so
  two birds can't end up at opposite edges with a void between them — which is
  exactly what the spiral did on a wide panel.
- **The last row is centred**, not left-packed, so an uneven count reads as a
  deliberate arrangement rather than a row that ran out.

The browser wall keeps the spiral: it's the medium the spiral was designed
for, and its parity test with the JS still holds. The two renders diverge here
on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass

# Rows to use for a given number of birds. The owner's rule: up to three is one
# row, up to six is two, and after that keep adding rows rather than shrinking
# a single row into a strip of stamps.
ROW_BREAKS = ((3, 1), (6, 2), (12, 3))
MAX_ROWS = 4

# How much of the space below the title a block of rows may occupy. Nearly all
# of it: the ROW COUNT is what makes fewer birds bigger (one row of three is
# limited only by the panel's width), so holding a fixed fraction back just
# left the sheet looking underfilled.
FILL_BY_ROWS = {1: 0.95, 2: 0.95, 3: 0.97, 4: 0.97}

# Gutters as a fraction of a plate's width/height. The columns have NO gutter
# at all (owner's call): birds are cropped to their own ink and then fitted
# inside their cells preserving aspect, so each one already carries whitespace
# of its own — an extra gutter only pushed them apart and shrank them. Cells
# touch; birds don't.
COLUMN_GUTTER = 0.0
ROW_GUTTER = 0.16
# Room under each plate for its two caption lines PLUS the air between the
# bird's feet and its name (render.CAPTION_GAP_VMIN), as a fraction of plate
# height.
CAPTION_SPACE = 0.20
# The panel's own margins, as a fraction of its width/height.
SIDE_MARGIN = 0.03
BOTTOM_MARGIN = 0.05

PLATE_ASPECT = 1.25  # height / width, as the wall's plates
# The tallest a cell may get relative to its width. Beyond this a single bird
# stops reading as a plate on a chart and starts reading as a poster.
MAX_CELL_ASPECT = 1.45


@dataclass(frozen=True)
class Placement:
    """Where one bird goes, in the same shape `wall_layout` returns so the
    renderer doesn't care which layout produced it."""

    file: str
    x: float  # offset from the panel centre
    y: float
    size_vmin: float
    z: int
    # The cell's height, which the frame sets independently of its width. The
    # bird is fitted INSIDE the cell preserving its own aspect, so a cell that
    # isn't 4:5 doesn't distort anything — it just stops a single row of three
    # from being limited to the height a 4:5 plate would have had.
    height_vmin: float = 0.0


def rows_for(count: int) -> int:
    for limit, rows in ROW_BREAKS:
        if count <= limit:
            return rows
    return MAX_ROWS


def split_rows(count: int, rows: int) -> list[int]:
    """How many birds per row, as even as possible, with any remainder going to
    the EARLIER rows — so a short row sits at the bottom, where a centred gap
    reads as deliberate rather than as a hole in the middle."""
    base, extra = divmod(count, rows)
    return [base + (1 if i < extra else 0) for i in range(rows)]


def compute_frame_grid(
    files: list[str], width: float, height: float, band_top: float
) -> list[Placement]:
    """Lay the birds out as centred rows filling the panel below the title."""
    if width <= 0 or height <= 0 or not files:
        return []

    vmin = min(width, height) / 100
    rows = rows_for(len(files))
    counts = split_rows(len(files), rows)
    widest = max(counts)

    usable_w = width * (1 - 2 * SIDE_MARGIN)
    usable_h = (height - band_top - height * BOTTOM_MARGIN) * FILL_BY_ROWS[rows]

    # Width and height are taken independently: the cell is as big as the grid
    # allows in each direction, and the bird is fitted inside it. Tying the two
    # together through the plate's 4:5 was what left a single row of three
    # sitting in the middle of an empty sheet — the width bound the cell, and
    # the height it implied was far shorter than the panel had room for.
    plate_w = usable_w / (widest + (widest - 1) * COLUMN_GUTTER)
    per_row_h = usable_h / (rows + (rows - 1) * ROW_GUTTER)
    plate_h = per_row_h / (1 + CAPTION_SPACE)
    # …but don't let a lone bird become a grotesque: cap how far a cell may
    # stray from the plates' own proportions.
    plate_h = min(plate_h, plate_w * MAX_CELL_ASPECT)

    row_pitch = plate_h * (1 + CAPTION_SPACE) + plate_h * ROW_GUTTER
    block_h = rows * plate_h * (1 + CAPTION_SPACE) + (rows - 1) * plate_h * ROW_GUTTER
    top = band_top + (height - band_top - height * BOTTOM_MARGIN - block_h) / 2

    placements: list[Placement] = []
    index = 0
    for row, in_row in enumerate(counts):
        span = in_row * plate_w + (in_row - 1) * plate_w * COLUMN_GUTTER
        left = (width - span) / 2  # centred: no row is left-packed
        cy = top + row * row_pitch + plate_h / 2
        for column in range(in_row):
            cx = left + column * plate_w * (1 + COLUMN_GUTTER) + plate_w / 2
            placements.append(
                Placement(
                    file=files[index],
                    x=cx - width / 2,
                    y=cy - height / 2,
                    size_vmin=plate_w / vmin,
                    height_vmin=plate_h / vmin,
                    # Newest first in `files`, and nothing overlaps here, so z
                    # only settles ties; keep the wall's newest-on-top rule.
                    z=len(files) - index,
                )
            )
            index += 1
    return placements
