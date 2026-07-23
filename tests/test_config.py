import pytest

from bird_painter.__main__ import _parse_port_arg
from bird_painter.config import (
    CONFIDENCE_FLOOR_MAX,
    CONFIDENCE_FLOOR_MIN,
    ConfigError,
    _confidence_floor,
    _env_bool,
    _env_int,
    load_config,
    load_config_or_exit,
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


def test_location_filter_off_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("BP_ARCHIVE_DIR", str(tmp_path))
    config = load_config()
    assert config.latitude is None
    assert config.longitude is None


def test_location_filter_parses_both(monkeypatch, tmp_path):
    monkeypatch.setenv("BP_ARCHIVE_DIR", str(tmp_path))
    monkeypatch.setenv("BP_LATITUDE", "52.37")
    monkeypatch.setenv("BP_LONGITUDE", "4.90")
    config = load_config()
    assert config.latitude == 52.37
    assert config.longitude == 4.90


@pytest.mark.parametrize(
    "lat,lon", [("52.37", None), (None, "4.90")]
)
def test_location_filter_requires_both(monkeypatch, tmp_path, lat, lon):
    monkeypatch.setenv("BP_ARCHIVE_DIR", str(tmp_path))
    if lat is not None:
        monkeypatch.setenv("BP_LATITUDE", lat)
    if lon is not None:
        monkeypatch.setenv("BP_LONGITUDE", lon)
    with pytest.raises(ConfigError, match="must be set together"):
        load_config()


@pytest.mark.parametrize(
    "lat,lon,bad",
    [("120", "4.90", "BP_LATITUDE"), ("52.37", "200", "BP_LONGITUDE")],
)
def test_location_filter_range_checked(monkeypatch, tmp_path, lat, lon, bad):
    monkeypatch.setenv("BP_ARCHIVE_DIR", str(tmp_path))
    monkeypatch.setenv("BP_LATITUDE", lat)
    monkeypatch.setenv("BP_LONGITUDE", lon)
    with pytest.raises(ConfigError, match=bad):
        load_config()


def test_location_filter_rejects_non_numeric(monkeypatch, tmp_path):
    monkeypatch.setenv("BP_ARCHIVE_DIR", str(tmp_path))
    monkeypatch.setenv("BP_LATITUDE", "north")
    monkeypatch.setenv("BP_LONGITUDE", "4.90")
    with pytest.raises(ConfigError, match="BP_LATITUDE must be a number"):
        load_config()


def test_load_config_surfaces_config_error(monkeypatch):
    monkeypatch.setenv("BP_MAX_PAINTS_PER_HOUR", "lots")
    with pytest.raises(ConfigError):
        load_config()


def test_parse_port_arg_accepts_number_and_rejects_junk():
    assert _parse_port_arg([]) is None
    assert _parse_port_arg(["8600"]) == 8600
    with pytest.raises(ConfigError, match="port must be a number"):
        _parse_port_arg(["zzz"])


def test_load_config_or_exit_exits_2_on_bad_env(monkeypatch):
    monkeypatch.setenv("BP_MAX_PAINTS_PER_HOUR", "lots")
    with pytest.raises(SystemExit) as exc:
        load_config_or_exit()
    assert exc.value.code == 2


def test_load_config_or_exit_returns_config_when_clean(monkeypatch, tmp_path):
    monkeypatch.setenv("BP_ARCHIVE_DIR", str(tmp_path))
    assert load_config_or_exit().max_paints_per_hour == 20


def test_host_defaults_to_all_interfaces_and_is_overridable(monkeypatch, tmp_path):
    # The frame + other devices reach the wall over the LAN, so the default
    # bind is all-interfaces; BP_HOST can restrict it.
    monkeypatch.setenv("BP_ARCHIVE_DIR", str(tmp_path))
    monkeypatch.delenv("BP_HOST", raising=False)
    assert load_config().host == "0.0.0.0"  # noqa: S104 — the documented default
    monkeypatch.setenv("BP_HOST", "127.0.0.1")
    assert load_config().host == "127.0.0.1"
