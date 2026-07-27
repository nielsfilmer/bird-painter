"""Occasion hats (`bird_painter/occasions.py`) + the env knobs that feed them.
Personal days are env-only; these tests use synthetic dates, never real ones."""

import datetime

import pytest

from bird_painter.config import ConfigError, _hat_dates, _hat_days
from bird_painter.occasions import PARTY_HAT, hat_for


def d(day, month, year=2026):
    return datetime.date(year, month, day)


def test_ordinary_day_has_no_hat():
    assert hat_for(d(3, 3)) is None


def test_public_holidays_have_their_hats():
    assert "Santa" in hat_for(d(25, 12))
    assert "orange" in hat_for(d(27, 4))
    assert "witch" in hat_for(d(31, 10))
    assert "mitre" in hat_for(d(5, 12))
    assert "party" in hat_for(d(1, 1))


def test_personal_day_gets_a_party_hat_every_year():
    days = ((14, 6),)
    assert hat_for(d(14, 6, 2026), personal_days=days) == PARTY_HAT
    assert hat_for(d(14, 6, 2031), personal_days=days) == PARTY_HAT
    assert hat_for(d(15, 6, 2026), personal_days=days) is None


def test_one_time_date_fires_only_that_exact_date():
    dates = (d(14, 6, 2026),)
    assert hat_for(d(14, 6, 2026), one_time_dates=dates) == PARTY_HAT
    assert hat_for(d(14, 6, 2027), one_time_dates=dates) is None


def test_personal_day_beats_a_public_holiday():
    # A birthday on Christmas gets the party hat, not the Santa hat.
    assert hat_for(d(25, 12), personal_days=((25, 12),)) == PARTY_HAT


def test_hat_days_env_parsing():
    assert _hat_days("14-06, 01-02") == ((14, 6), (1, 2))
    assert _hat_days(None) == ()
    assert _hat_days("  ") == ()
    with pytest.raises(ConfigError, match="DD-MM"):
        _hat_days("junk")
    with pytest.raises(ConfigError, match="DD-MM"):
        _hat_days("32-01")  # no 32nd
    with pytest.raises(ConfigError, match="DD-MM"):
        _hat_days("14-06-2026")  # that's a date, wrong knob


def test_hat_dates_env_parsing():
    assert _hat_dates("14-06-2026") == (d(14, 6, 2026),)
    assert _hat_dates(None) == ()
    with pytest.raises(ConfigError, match="DD-MM-YYYY"):
        _hat_dates("14-06")
    with pytest.raises(ConfigError, match="DD-MM-YYYY"):
        _hat_dates("31-02-2026")  # no Feb 31


def test_leap_day_is_a_valid_hat_day():
    assert _hat_days("29-02") == ((29, 2),)


def test_brush_prompt_carries_the_hat_before_the_no_text_tail():
    from bird_painter.brush import build_prompt

    prompt = build_prompt("European Robin", "Erithacus rubecula", hat=PARTY_HAT)
    assert PARTY_HAT in prompt
    assert prompt.index(PARTY_HAT) < prompt.index("No text")
    # No hat → prompt unchanged shape
    assert PARTY_HAT not in build_prompt("European Robin", "Erithacus rubecula")


def test_runner_passes_the_hat_to_the_brush(config, archive_dir, monkeypatch):
    import dataclasses
    from unittest.mock import patch

    from bird_painter.ears import Detection
    from bird_painter.gate import TriggerGate
    from bird_painter.runner import PaintRunner
    from bird_painter.store import Store

    hatted = dataclasses.replace(config, hat_days=((1, 1),))  # any recurring day
    store = Store(archive_dir, hatted.paint_ttl_seconds)
    runner = PaintRunner(
        hatted, store, TriggerGate(store, hatted.paint_ttl_seconds, 20)
    )
    monkeypatch.setattr(
        "bird_painter.runner.hat_for", lambda today, days, dates: PARTY_HAT
    )
    with patch(
        "bird_painter.runner.paint_species", return_value=(b"img", "jpg")
    ) as paint:
        runner.on_detections([Detection("Robin", "E. rubecula", 0.9, 0.0, 3.0)])
    assert paint.call_args.kwargs["hat"] == PARTY_HAT
