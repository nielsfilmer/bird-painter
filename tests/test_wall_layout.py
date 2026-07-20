"""Guards for the Python layout port (`bird_painter/wall_layout.py`). These
mirror the key invariants of the JS suite (`static/layout.test.js`); the
node-vs-Python parity test (`test_wall_layout_parity.py`) proves the two agree
numerically, and these run even when node is absent."""

from bird_painter.wall_layout import (
    PLATE_ASPECT,
    compute_collage,
    overlap_area,
)

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
VIEWPORTS = [(1600, 1200, 168), (1920, 1080, 150), (375, 812, 120), (2400, 1000, 150)]


def _footprint(p, vmin):
    w = p.size_vmin * vmin
    return {"x": p.x, "y": p.y, "w": w, "h": w * PLATE_ASPECT}


def test_birds_never_visibly_overlap():
    for w, h, band_top in VIEWPORTS:
        vmin = min(w, h) / 100
        for n in (1, 4, 8, 12):
            placed = compute_collage(FILES[:n], w, h, band_top)
            for i in range(len(placed)):
                for j in range(i + 1, len(placed)):
                    ov = overlap_area(
                        _footprint(placed[i], vmin), _footprint(placed[j], vmin)
                    )
                    assert ov <= 0.5, f"overlap {ov} at {w}x{h} n={n}"


def test_layout_is_deterministic():
    a = compute_collage(FILES, 1600, 1200, 168)
    b = compute_collage(FILES, 1600, 1200, 168)
    assert a == b


def test_zero_viewport_yields_nothing():
    assert compute_collage(FILES, 0, 0, 0) == []
    assert compute_collage(FILES, 800, 0, 0) == []


def test_no_bird_far_smaller_than_largest():
    placed = compute_collage(FILES, 1600, 1200, 168)
    sizes = [p.size_vmin for p in placed]
    assert min(sizes) >= max(sizes) * 0.8


def test_every_plate_stays_on_screen():
    w, h, band_top = 1600, 1200, 168
    vmin = min(w, h) / 100
    for p in compute_collage(FILES, w, h, band_top):
        f = _footprint(p, vmin)
        assert f["x"] - f["w"] / 2 >= -w / 2 - 0.5
        assert f["x"] + f["w"] / 2 <= w / 2 + 0.5
        assert f["y"] + f["h"] / 2 <= h / 2 + 0.5
