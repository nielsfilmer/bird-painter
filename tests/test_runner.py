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
