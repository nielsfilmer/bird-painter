"""Night mode (`bird_painter/night.py`): the schedule, the backlight arithmetic,
and the transition-only writes."""

from datetime import datetime
from pathlib import Path

from bird_painter.night import Backlight, NightSchedule, NightWatch, find_backlight


def at(h, m=0):
    return datetime(2026, 9, 3, h, m)


def test_schedule_wraps_midnight_and_respects_enabled():
    s = NightSchedule(start_hour=22, end_hour=7)
    assert s.is_night(at(22)) and s.is_night(at(0, 30)) and s.is_night(at(6, 59))
    assert not s.is_night(at(7)) and not s.is_night(at(12))
    assert not s.is_night(at(21, 59))
    day = NightSchedule(start_hour=1, end_hour=5)
    assert day.is_night(at(3)) and not day.is_night(at(23))
    assert not NightSchedule(enabled=False).is_night(at(23))
    assert not NightSchedule(start_hour=9, end_hour=9).is_night(at(9))


def fake_backlight(tmp_path: Path, maximum=31, current=15) -> Path:
    dev = tmp_path / "panel_backlight@1"
    dev.mkdir()
    (dev / "max_brightness").write_text(f"{maximum}\n")
    (dev / "brightness").write_text(f"{current}\n")
    return dev


def test_backlight_percent_round_trips_on_the_units_scale(tmp_path):
    bl = Backlight(fake_backlight(tmp_path))
    assert bl.max == 31 and bl.read_percent() == 48  # 15/31
    bl.write_percent(20)
    assert (bl.device / "brightness").read_text() == "6\n"  # 6.2 → 6
    bl.write_percent(150)
    assert (bl.device / "brightness").read_text() == "31\n"  # clamped
    assert find_backlight(tmp_path) == bl.device
    assert find_backlight(tmp_path / "nowhere") is None


def test_watch_writes_only_on_transitions_and_restores_the_day_level(tmp_path):
    bl = Backlight(fake_backlight(tmp_path, current=15))
    now = {"t": at(12)}
    schedule = NightSchedule(22, 7, night_percent=20)
    watch = NightWatch(schedule, bl, clock=lambda: now["t"])
    # Daytime start: nothing to restore, nothing written.
    assert watch.tick() is False
    assert (bl.device / "brightness").read_text() == "15\n"
    assert watch.tick() is None  # same state, no write
    now["t"] = at(22, 30)
    assert watch.tick() is True
    assert (bl.device / "brightness").read_text() == "6\n"
    # A hand adjustment at night is not fought on the next tick.
    (bl.device / "brightness").write_text("10\n")
    assert watch.tick() is None
    assert (bl.device / "brightness").read_text() == "10\n"
    now["t"] = at(7, 1)
    assert watch.tick() is False
    assert (bl.device / "brightness").read_text() == "15\n"  # the day level it found


def test_watch_without_a_backlight_still_tracks_state():
    now = {"t": at(23)}
    watch = NightWatch(NightSchedule(22, 7), None, clock=lambda: now["t"])
    assert watch.tick() is True and watch.is_night is True
    now["t"] = at(8)
    assert watch.tick() is False and watch.is_night is False
