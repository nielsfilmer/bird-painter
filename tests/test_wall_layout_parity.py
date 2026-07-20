"""The load-bearing test for the two walls matching: run the JS `computeCollage`
and the Python `compute_collage` on identical inputs and assert byte-for-byte
placement agreement. If the port drifts from `static/layout.js`, this fails.

Skipped when node isn't installed (same policy as `make test-js`)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bird_painter.wall_layout import compute_collage

_ROOT = Path(__file__).resolve().parents[1]
LAYOUT_JS = _ROOT / "bird_painter" / "static" / "layout.js"

FILES = [
    "1784571015_carrion-crow_a027eb65.jpg",
    "1784571013_eurasian-jay_2edc6dd9.jpg",
    "1784571012_common-buzzard_1cfff3dd.jpg",
    "1784500000_great-tit_deadbeef.jpg",
    "1784400000_blue-tit_12345678.jpg",
    "1784300000_song-thrush_0f0f0f0f.jpg",
    "1784200000_dunnock_abcdef01.jpg",
    "1784100000_goldcrest_99887766.jpg",
    "1784000000_wren_55443322.jpg",
    "1783900000_robin_11223344.jpg",
    "1783800000_chaffinch_aabbccdd.jpg",
    "1783700000_chiffchaff_eeff0011.jpg",
]
# (width, height, band_top, n): also vary the count so parity is checked for
# sparse walls (where the fit-scale pins at 1) and dense ones (where it shrinks
# and the spiral packs tightly), not only the 12-bird maximum.
CASES = [
    (1600, 1200, 168, 12),
    (1920, 1080, 150, 1),
    (1920, 1080, 150, 4),
    (375, 812, 120, 7),
    (2400, 1000, 150, 12),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_python_layout_matches_javascript():
    script = f"""
import {{ computeCollage }} from {json.dumps(LAYOUT_JS.as_uri())};
const files = {json.dumps(FILES)};
const cases = {json.dumps(CASES)};
const out = cases.map(([W, H, B, n]) => computeCollage(files.slice(0, n), W, H, B));
console.log(JSON.stringify(out));
"""
    node = shutil.which("node")
    result = subprocess.run(  # noqa: S603 — node path from shutil.which, static script
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    js = json.loads(result.stdout)
    for (w, h, band_top, n), js_placed in zip(CASES, js, strict=True):
        py_placed = compute_collage(FILES[:n], w, h, band_top)
        assert len(js_placed) == len(py_placed), f"count differs at {w}x{h} n={n}"
        for a, b in zip(js_placed, py_placed, strict=True):
            assert a["file"] == b.file
            assert abs(a["x"] - b.x) < 1e-6, f"x differs for {b.file} at {w}x{h}"
            assert abs(a["y"] - b.y) < 1e-6, f"y differs for {b.file} at {w}x{h}"
            assert abs(a["sizeVmin"] - b.size_vmin) < 1e-6
            assert a["z"] == b.z
