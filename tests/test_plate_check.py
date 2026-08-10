"""The plate check: does this look like a bird on white, or has the model
drifted into painting something else? Built from two real failures on the wall
(2026-08-06) — a photograph of a watercolour lying on a desk, and a plate with
a flat grey block across the top."""

import io

import numpy as np
import pytest
from PIL import Image

from bird_painter.plate_check import MAX_FLAT_SHARE, describe_problem

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


def test_the_threshold_leaves_room_either_side():
    """A block just under the limit passes, one just over fails — the point
    being that the measured gap (good plates ~1%, failures 7% and 21%) is wide
    enough that this isn't a coin toss."""
    width = SIZE[0] - 60  # inset, so only flatness is under test
    for share, expected_ok in (
        (MAX_FLAT_SHARE * 0.5, True),
        (MAX_FLAT_SHARE * 2, False),
    ):
        pixels = a_painted_bird()
        rows = int(share * SIZE[0] * SIZE[1] / width)
        pixels[30 : 30 + rows, 30 : 30 + width] = 205
        assert (describe_problem(as_jpeg(pixels), "jpg") is None) is expected_ok


def test_our_own_placeholder_is_never_second_guessed():
    """Placeholder plates are drawn by this app, not by the model — flat by
    design, and not the model's fault."""
    assert describe_problem(b"<svg/>", "svg") is None


@pytest.mark.parametrize("payload", [b"", b"not an image at all"])
def test_an_unreadable_image_is_kept_rather_than_thrown_away(payload):
    """Fail-soft: a check that can't run must not silently delete paintings.
    A surprising plate is better on the wall than a bird lost to a bug here."""
    assert describe_problem(payload, "jpg") is None
