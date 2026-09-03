"""Night mode (`bird_painter/night.py`): the schedule, the backlight arithmetic,
the transition-only writes, and the day-level rules a real unit needs."""

import logging
from datetime import datetime
from pathlib import Path

from bird_painter.night import (
    Backlight,
    NightSchedule,
    NightWatch,
    find_backlight,
)


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
    assert not NightSchedule(start_hour=9, end_hour=9).is_night(at(9))  # never


def fake_backlight(tmp_path: Path, maximum=31, current=15, name="panel_backlight@1"):
    dev = tmp_path / name
    dev.mkdir()
    (dev / "max_brightness").write_text(f"{maximum}\n")
    (dev / "brightness").write_text(f"{current}\n")
    return dev


def raw(bl: Backlight) -> str:
    return (bl.device / "brightness").read_text()


def test_backlight_percent_round_trips_on_the_units_scale(tmp_path):
    bl = Backlight(fake_backlight(tmp_path))
    assert bl.max == 31 and bl.read_percent() == 48  # 15/31
    bl.write_percent(20)
    assert raw(bl) == "6\n"  # 6.2 → 6
    bl.write_percent(150)
    assert raw(bl) == "31\n"  # clamped
    assert Backlight(fake_backlight(tmp_path, maximum=0, name="broken")).max == 1


def test_find_backlight_by_name_or_first_writable(tmp_path):
    acpi = fake_backlight(tmp_path, name="acpi_video0")
    intel = fake_backlight(tmp_path, name="intel_backlight")
    assert find_backlight(tmp_path) == acpi  # name order: the documented limitation
    assert find_backlight(tmp_path, name="intel_backlight") == intel
    assert find_backlight(tmp_path, name="nope") is None
    assert find_backlight(tmp_path / "nowhere") is None
    (acpi / "brightness").chmod(0o444)
    try:
        assert find_backlight(tmp_path) == intel  # a read-only node is skipped
    finally:
        (acpi / "brightness").chmod(0o644)


def watch_at(bl, hour, schedule=None):
    now = {"t": at(hour)}
    schedule = schedule or NightSchedule(22, 7, night_percent=20)
    w = NightWatch(schedule, bl, clock=lambda: now["t"])
    return w, now


def test_watch_writes_only_on_transitions_and_restores_the_day_level(tmp_path):
    bl = Backlight(fake_backlight(tmp_path, current=15))
    watch, now = watch_at(bl, 12)
    # Daytime start: nothing written, but the day level is learnt.
    assert watch.tick() is False
    assert raw(bl) == "15\n" and watch.day_percent == 48
    assert watch.tick() is None  # same state, no write
    # A hand adjustment during the day is what comes back tomorrow.
    (bl.device / "brightness").write_text("25\n")
    now["t"] = at(22, 30)
    assert watch.tick() is True
    assert raw(bl) == "6\n" and watch.day_percent == 81
    # …and one at night is not fought.
    (bl.device / "brightness").write_text("10\n")
    assert watch.tick() is None
    assert raw(bl) == "10\n"
    now["t"] = at(7, 1)
    assert watch.tick() is False
    assert raw(bl) == "25\n"


def test_a_restart_after_the_dim_does_not_take_night_for_day(tmp_path):
    """Review of #149: the service restarts (installer re-run, Restart=always).
    A start inside the window with the panel already AT the night level must
    not remember 20% as the day — the wall would stay dim all day."""
    bl = Backlight(fake_backlight(tmp_path, current=6))  # 20%: the previous run's dim
    watch, now = watch_at(bl, 23)
    assert watch.tick() is True
    assert watch.day_percent == 100, "nothing known: morning means full"
    now["t"] = at(7)
    assert watch.tick() is False
    assert raw(bl) == "31\n"
    # A first-ever start at night, panel still bright: that IS the day level.
    bl2 = Backlight(fake_backlight(tmp_path, current=15, name="fresh"))
    watch2, now2 = watch_at(bl2, 23)
    assert watch2.tick() is True and watch2.day_percent == 48
    now2["t"] = at(8)
    watch2.tick()
    assert raw(bl2) == "15\n"


def test_a_configured_day_level_wins_and_zero_is_a_level(tmp_path):
    bl = Backlight(fake_backlight(tmp_path, current=31))
    pinned = NightSchedule(22, 7, night_percent=20, day_percent=40)
    watch, now = watch_at(bl, 12, pinned)
    watch.tick()
    assert watch.day_percent == 40  # the panel's 100% did not override it
    now["t"] = at(23)
    watch.tick()
    now["t"] = at(8)
    watch.tick()
    assert raw(bl) == "12\n"  # 40% of 31
    # 0 is a legitimate configured level, not "unset".
    zero = NightSchedule(22, 7, night_percent=20, day_percent=0)
    watch0, now0 = watch_at(bl, 23, zero)
    watch0.tick()
    now0["t"] = at(8)
    watch0.tick()
    assert raw(bl) == "0\n"


def test_a_failed_write_keeps_the_transition_pending(tmp_path, caplog):
    """Review of #149: the state used to flip before the write; one transient
    error then meant a bright wall until morning. Now the tick reports no
    change and the next one retries."""
    bl = Backlight(fake_backlight(tmp_path, current=15))
    watch, now = watch_at(bl, 23)
    (bl.device / "brightness").chmod(0o444)
    try:
        with caplog.at_level(logging.WARNING, logger="bird_painter.night"):
            assert watch.tick() is None
        assert watch.is_night is None
        assert "retrying next tick" in caplog.text
    finally:
        (bl.device / "brightness").chmod(0o644)
    assert watch.tick() is True
    assert raw(bl) == "6\n"


def test_a_daytime_start_survives_an_unreadable_backlight(tmp_path, caplog):
    """Round 2 of #149: a failed READ at a daytime start used to be reported
    as a failed restore, every minute, with the state never settling."""
    bl = Backlight(fake_backlight(tmp_path, current=15))
    watch, now = watch_at(bl, 12)
    (bl.device / "brightness").chmod(0o000)
    try:
        with caplog.at_level(logging.WARNING, logger="bird_painter.night"):
            assert watch.tick() is False
        assert watch.is_night is False and watch.day_percent == 100
        assert "could not read" in caplog.text and "restore" not in caplog.text
        assert watch.tick() is None  # settled: no warning per minute
    finally:
        (bl.device / "brightness").chmod(0o644)
    now["t"] = at(23)
    assert watch.tick() is True and watch.day_percent == 48  # dusk read it


def test_log_reports_only_what_was_written(tmp_path, caplog):
    bl = Backlight(fake_backlight(tmp_path, current=15))
    watch, now = watch_at(bl, 12)
    with caplog.at_level(logging.INFO, logger="bird_painter.night"):
        watch.tick()
        now["t"] = at(23)
        watch.tick()
    lines = [r.getMessage() for r in caplog.records]
    assert lines == [
        "night: off (daytime start; backlight left at 48%)",
        "night: on — backlight 20%",
    ]


def test_watch_without_a_backlight_still_tracks_state():
    watch, now = watch_at(None, 23)
    assert watch.tick() is True and watch.is_night is True
    now["t"] = at(8)
    assert watch.tick() is False and watch.is_night is False


def test_disabled_watch_starts_no_thread_and_reschedule_wakes_it(tmp_path):
    bl = Backlight(fake_backlight(tmp_path, current=15))
    watch, now = watch_at(bl, 22, NightSchedule(22, 7, enabled=False))
    watch.start()
    assert watch.is_night is False and watch._thread is None
    # The settings screen turns it on: the thread starts and ticks at once.
    watch.interval = 0.05
    watch.reschedule(NightSchedule(22, 7, night_percent=20))
    assert watch._thread is not None
    for _ in range(50):
        if watch.is_night is True:
            break
        watch._thread.join(0.02)
    assert watch.is_night is True and raw(bl) == "6\n"
    # And moves "from" past now: light comes back without waiting a minute.
    watch.interval = 60
    watch.reschedule(NightSchedule(23, 7, night_percent=20, day_percent=48))
    for _ in range(50):
        if watch.is_night is False:
            break
        watch._thread.join(0.02)
    assert watch.is_night is False and raw(bl) == "15\n"
    watch.stop()
    watch._thread.join(1)
    assert not watch._thread.is_alive()
