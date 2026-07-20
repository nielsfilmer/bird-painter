import pytest

from bird_painter.config import (
    CONFIDENCE_FLOOR_MAX,
    CONFIDENCE_FLOOR_MIN,
    ConfigError,
    _confidence_floor,
    _env_bool,
    _env_int,
    load_config,
)


def test_env_int_parses_and_defaults(monkeypatch):
    monkeypatch.delenv("BP_PORT", raising=False)
    assert _env_int("BP_PORT", 8537) == 8537
    monkeypatch.setenv("BP_PORT", "9000")
    assert _env_int("BP_PORT", 8537) == 9000


def test_env_int_rejects_non_numeric_with_named_error(monkeypatch):
    monkeypatch.setenv("BP_PORT", "abc")
    with pytest.raises(ConfigError, match="BP_PORT must be an integer"):
        _env_int("BP_PORT", 8537)


def test_env_bool_accepts_true_and_false_words(monkeypatch):
    for raw in ("true", "1", "on", "YES"):
        monkeypatch.setenv("BP_ENABLE_LISTENER", raw)
        assert _env_bool("BP_ENABLE_LISTENER", True) is True
    for raw in ("false", "0", "off", "No"):
        monkeypatch.setenv("BP_ENABLE_LISTENER", raw)
        assert _env_bool("BP_ENABLE_LISTENER", True) is False


def test_env_bool_rejects_typos_instead_of_silently_disabling(monkeypatch):
    monkeypatch.setenv("BP_ENABLE_LISTENER", "ture")  # typo
    with pytest.raises(ConfigError, match="BP_ENABLE_LISTENER must be one of"):
        _env_bool("BP_ENABLE_LISTENER", True)


def test_confidence_floor_clamped_to_valid_range(monkeypatch):
    monkeypatch.setenv("BP_CONFIDENCE_FLOOR", "0")
    assert _confidence_floor() == CONFIDENCE_FLOOR_MIN
    monkeypatch.setenv("BP_CONFIDENCE_FLOOR", "1.0")
    assert _confidence_floor() == CONFIDENCE_FLOOR_MAX
    monkeypatch.setenv("BP_CONFIDENCE_FLOOR", "0.6")
    assert _confidence_floor() == 0.6


def test_load_config_surfaces_config_error(monkeypatch):
    monkeypatch.setenv("BP_MAX_PAINTS_PER_HOUR", "lots")
    with pytest.raises(ConfigError):
        load_config()
