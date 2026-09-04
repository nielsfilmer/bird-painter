"""The boot splash generator (`scripts/make_splash.py`) — run as the install
script runs it, so a rename in render.py's helpers it borrows fails here
rather than on a unit mid-install (review of #154)."""

import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "make_splash.py"
BIRD = ROOT / "tests" / "fixtures" / "plates" / "good-hummingbird.jpg"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 — our own script, our own interpreter
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60
    )


def test_writes_both_images_turned_by_the_units_rotate(tmp_path: Path):
    assert run(str(tmp_path / "a"), str(BIRD), "90").returncode == 0
    landscape = Image.open(tmp_path / "a" / "splash-desktop.png")
    native = Image.open(tmp_path / "a" / "splash-native.png")
    assert landscape.size == (1280, 720) and native.size == (720, 1280)
    assert run(str(tmp_path / "b"), str(BIRD), "270").returncode == 0
    other = Image.open(tmp_path / "b" / "splash-native.png")
    # The contract: 90 turns the landscape counter-clockwise, 270 clockwise
    # (the one direction no test can see is whether the panel agrees).
    assert native.tobytes() == landscape.rotate(90, expand=True).tobytes()
    assert other.tobytes() == landscape.rotate(-90, expand=True).tobytes()
    # The bird is on the paper: the middle is not bare cream.
    assert landscape.getpixel((640, 420)) != landscape.getpixel((40, 40))


def test_a_portrait_stand_and_the_ten_inch_get_their_own_picture(tmp_path: Path):
    """The 10" unit stands in portrait (ROTATE 0) at 1200x1920: the picture
    as seen IS the native one, and the 10" is not handed the 7"'s pixels."""
    assert run(str(tmp_path / "p"), str(BIRD), "0", "1200x1920").returncode == 0
    seen = Image.open(tmp_path / "p" / "splash-desktop.png")
    native = Image.open(tmp_path / "p" / "splash-native.png")
    assert seen.size == (1200, 1920) and native.tobytes() == seen.tobytes()
    assert seen.getpixel((600, 1000)) != seen.getpixel((40, 40))  # the bird, mid-sheet
    assert run(str(tmp_path / "u"), str(BIRD), "180", "1200x1920").returncode == 0
    upside = Image.open(tmp_path / "u" / "splash-native.png")
    assert upside.tobytes() == seen.rotate(180).tobytes()
    assert run(str(tmp_path / "l"), str(BIRD), "90", "1200x1920").returncode == 0
    assert Image.open(tmp_path / "l" / "splash-desktop.png").size == (1920, 1200)
    assert run(str(tmp_path / "x"), str(BIRD), "90", "huge").returncode != 0


def test_refuses_a_missing_out_dir_and_an_odd_rotate(tmp_path: Path):
    bare = run()
    assert bare.returncode != 0 and "usage" in bare.stderr
    bad = run(str(tmp_path), str(BIRD), "45")
    assert bad.returncode != 0 and "0, 90, 180 or 270" in bad.stderr
