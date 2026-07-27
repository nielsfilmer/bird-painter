import dataclasses
from unittest.mock import patch

from bird_painter.ears import Detection
from bird_painter.gate import TriggerGate
from bird_painter.runner import PaintRunner
from bird_painter.store import Store


def detection(name: str) -> Detection:
    return Detection(name, f"{name} scientific", 0.9, 0.0, 3.0)


def make_runner(config, archive_dir):
    store = Store(archive_dir, config.paint_ttl_seconds)
    gate = TriggerGate(store, config.paint_ttl_seconds, config.max_paints_per_hour)
    return PaintRunner(config, store, gate), store


def test_successful_paint_lands_in_store(config, archive_dir):
    runner, store = make_runner(config, archive_dir)
    with patch(
        "bird_painter.runner.paint_species", return_value=(b"img", "jpg")
    ) as paint:
        runner.on_detections([detection("Robin"), detection("Wren")])
    assert paint.call_count == 2
    assert sorted(p.species_common for p in store.live()) == ["Robin", "Wren"]
    assert all(p.source == "detection" for p in store.live())


def test_cooldown_blocks_repaint_without_calling_brush(config, archive_dir):
    runner, store = make_runner(config, archive_dir)
    with patch(
        "bird_painter.runner.paint_species", return_value=(b"img", "jpg")
    ) as paint:
        runner.on_detections([detection("Robin")])
        runner.on_detections([detection("Robin")])
    assert paint.call_count == 1
    assert len(store.live()) == 1


def test_failed_paint_stores_nothing_and_retries(config, archive_dir):
    runner, store = make_runner(config, archive_dir)
    with patch("bird_painter.runner.paint_species", return_value=None) as paint:
        runner.on_detections([detection("Junco")])
        runner.on_detections([detection("Junco")])
    assert paint.call_count == 2  # species stayed free to retry
    assert store.live() == []


def test_hourly_cap_stops_painting(config, archive_dir):
    capped = dataclasses.replace(config, max_paints_per_hour=1)
    runner, store = make_runner(capped, archive_dir)
    with patch(
        "bird_painter.runner.paint_species", return_value=(b"img", "jpg")
    ) as paint:
        runner.on_detections([detection("Robin"), detection("Wren")])
    assert paint.call_count == 1
    assert len(store.live()) == 1


def test_paint_with_window_archives_the_detection_clip(config, archive_dir):
    import numpy as np

    runner, store = make_runner(config, archive_dir)
    window = np.zeros(15 * 48000, dtype="float32")
    with patch("bird_painter.runner.paint_species", return_value=(b"img", "jpg")):
        runner.on_detections([detection("Robin")], window, 48000)
    painting = store.live()[0]
    clip = store.audio_file_for(painting.file)
    assert clip is not None  # the sound behind the painting is archived


def test_clip_failure_never_costs_the_painting(config, archive_dir):
    import numpy as np

    runner, store = make_runner(config, archive_dir)
    window = np.zeros(15 * 48000, dtype="float32")
    with (
        patch("bird_painter.runner.paint_species", return_value=(b"img", "jpg")),
        patch(
            "bird_painter.runner.detection_clip_wav", side_effect=RuntimeError("boom")
        ),
    ):
        runner.on_detections([detection("Robin")], window, 48000)
    assert [p.species_common for p in store.live()] == ["Robin"]
    assert store.audio_file_for(store.live()[0].file) is None


def test_detections_without_window_still_paint(config, archive_dir):
    runner, store = make_runner(config, archive_dir)
    with patch("bird_painter.runner.paint_species", return_value=(b"img", "jpg")):
        runner.on_detections([detection("Robin")])  # legacy no-audio call
    assert [p.species_common for p in store.live()] == ["Robin"]
