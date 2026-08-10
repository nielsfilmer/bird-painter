"""The plate check: does this look like a bird on white, or has the model
drifted into painting something else? Built from two real failures on the wall
(2026-08-06) — a photograph of a watercolour lying on a desk, and a plate with
a flat grey block across the top."""

import io
import pathlib

import numpy as np
import pytest
from PIL import Image

from bird_painter.plate_check import describe_problem

SIZE = (400, 500)


def as_jpeg(pixels: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    image = Image.fromarray(pixels.astype("uint8"), "RGB")
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def a_painted_bird() -> np.ndarray:
    """A textured blob on white — what a real watercolour plate looks like to
    this check: no large flat area, white all round the edge."""
    rng = np.random.default_rng(7)
    pixels = np.full((SIZE[1], SIZE[0], 3), 255, dtype=float)
    body = (slice(150, 380), slice(120, 300))
    # Texture matters: a watercolour is never one flat colour, which is exactly
    # what separates it from a block.
    pixels[body] = rng.integers(60, 200, size=(230, 180, 3))
    return pixels


def test_a_real_looking_plate_passes():
    assert describe_problem(as_jpeg(a_painted_bird()), "jpg") is None


def test_a_flat_block_is_rejected():
    """The Great Egret case: a plain grey rectangle over the top 40%, the bird
    squeezed below it."""
    pixels = a_painted_bird()
    # Inset, exactly as the real plate was: a white margin all round, so the
    # border rule is satisfied and only the flatness test can catch it.
    pixels[30:230, 30:370] = 205
    problem = describe_problem(as_jpeg(pixels), "jpg")
    assert problem is not None
    assert "flat colour" in problem


def test_a_photograph_of_a_painting_on_a_desk_is_rejected():
    """The Egyptian Goose case: the model painted the artefact — a sheet on a
    tan desk — instead of the subject, so there is no white ground at all."""
    pixels = a_painted_bird()
    pixels[:, :] = np.where(pixels == 255, 222, pixels)  # tan desk everywhere
    pixels[40:460, 40:360] = 252  # the sheet of paper, inset
    pixels[150:380, 120:300] = a_painted_bird()[150:380, 120:300]
    problem = describe_problem(as_jpeg(pixels), "jpg")
    assert problem is not None
    assert "white" in problem


def test_the_flat_share_threshold_leaves_room_either_side():
    """The flat share is measured against the SUBJECT, so the numbers to beat
    are: 273 good archive plates at 1.4–28.4%, the broken ones at 72.9–93.3%.
    A block covering half the bird passes, one covering nearly all of it fails
    — the gap is wide enough that this isn't a coin toss."""
    for block_share, expected_ok in ((0.4, True), (0.9, False)):
        pixels = a_painted_bird()
        bird = np.argwhere(~(pixels >= 224).all(axis=2))
        (top, left), (bottom, right) = bird.min(0), bird.max(0) + 1
        rows = int(block_share * (bottom - top))
        pixels[top : top + rows, left:right] = 205  # one flat colour on the bird
        problem = describe_problem(as_jpeg(pixels), "jpg")
        assert (problem is None) is expected_ok, problem


def test_our_own_placeholder_is_never_second_guessed():
    """Placeholder plates are drawn by this app, not by the model — flat by
    design, and not the model's fault."""
    assert describe_problem(b"<svg/>", "svg") is None


@pytest.mark.parametrize("payload", [b"", b"not an image at all"])
def test_an_unreadable_image_is_kept_rather_than_thrown_away(payload):
    """Fail-soft: a check that can't run must not silently delete paintings.
    A surprising plate is better on the wall than a bird lost to a bug here."""
    assert describe_problem(payload, "jpg") is None


FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "plates"


@pytest.mark.parametrize(
    ("name", "should_pass"),
    [
        # Real model output, downscaled and committed: `data/` is gitignored
        # and purged monthly, so without these the thresholds would have no
        # durable evidence behind them — which is how the first version of this
        # check shipped tuned on nine plates out of 277.
        ("good-hummingbird.jpg", True),
        ("bad-desk-photo.jpg", False),
        ("bad-grey-block.jpg", False),
    ],
)
def test_real_plates_from_the_archive(name, should_pass):
    problem = describe_problem((FIXTURES / name).read_bytes(), "jpg")
    assert (problem is None) is should_pass, f"{name}: {problem}"


def test_the_warm_white_ground_that_broke_the_first_version():
    """The bug that made this check reject a flawless hummingbird at 89%: a
    warm white like (250, 250, 232) quantises to a bucket whose FLOOR is 224,
    and an exclusive `> 224` called that a colour — so the entire page counted
    as one flat non-white area. The boundary is inclusive now."""
    pixels = np.full((SIZE[1], SIZE[0], 3), (250, 250, 232), dtype=float)
    pixels[150:380, 120:300] = a_painted_bird()[150:380, 120:300]
    assert describe_problem(as_jpeg(pixels), "jpg") is None


def test_a_plate_reads_the_same_before_and_after_trimming():
    """The flat share is measured against the bird, not the canvas, and the
    border after any all-white margin is cropped — so padding a plate back to
    4:5 (what `trim` does) can't change the verdict. The first version measured
    against the canvas, which meant it was calibrated on post-trim numbers and
    applied to pre-trim images."""
    pixels = a_painted_bird()
    padded = np.full((SIZE[1] + 300, SIZE[0] + 300, 3), 255, dtype=float)
    padded[150 : 150 + SIZE[1], 150 : 150 + SIZE[0]] = pixels
    assert describe_problem(as_jpeg(pixels), "jpg") == describe_problem(
        as_jpeg(padded), "jpg"
    )
