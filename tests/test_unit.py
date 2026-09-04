"""The unit's settings files and its nmcli wrappers (`bird_painter/unit.py`)."""

import subprocess
from pathlib import Path

from bird_painter import unit
from bird_painter.config import Config
from bird_painter.night import NightSchedule


def test_conf_round_trips_and_keeps_what_it_does_not_own(tmp_path: Path):
    conf = tmp_path / "unit.conf"
    conf.write_text(
        "# written by the install script\nOUTPUT=DSI-2\nROTATE=90\n"
        "CAPTION=1.5\nCAPTION=1.2\n"
    )
    assert unit.read_conf(conf) == {"OUTPUT": "DSI-2", "ROTATE": "90", "CAPTION": "1.2"}
    merged = unit.write_conf({"CAPTION": "1.7", "NIGHT_FROM": "23"}, conf)
    assert merged == {
        "OUTPUT": "DSI-2",
        "ROTATE": "90",
        "CAPTION": "1.7",
        "NIGHT_FROM": "23",
    }
    text = conf.read_text()
    assert text.startswith("# written by the install script\nOUTPUT=DSI-2\n")
    assert text.count("CAPTION=") == 1, "a duplicate key collapses to one line"
    assert text.endswith("NIGHT_FROM=23\n")
    assert not (tmp_path / "unit.conf.tmp").exists()


def test_env_writes_never_touch_the_key(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "FAL_KEY=abc:123==\n# BP_WALL_MAX_LIVE=12\nBP_WALL_MAX_LIVE=3"
    )  # no newline
    unit.write_conf({"BP_WALL_MAX_LIVE": "5", "BP_NIGHT_FROM": "23"}, env)
    assert env.read_text() == (
        "FAL_KEY=abc:123==\n# BP_WALL_MAX_LIVE=12\n"
        "BP_WALL_MAX_LIVE=5\nBP_NIGHT_FROM=23\n"
    )


def test_conf_is_created_when_missing(tmp_path: Path):
    conf = tmp_path / "deeper" / "unit.conf"
    assert unit.read_conf(conf) == {}
    assert unit.write_conf({"UI": "1.5"}, conf) == {"UI": "1.5"}


def config_for(tmp_path: Path, **over) -> Config:
    return Config(archive_dir=tmp_path / "archive", enable_listener=False, **over)


def test_live_settings_come_from_unit_conf_and_config(tmp_path: Path):
    conf = tmp_path / "unit.conf"
    conf.write_text("CAPTION=9\nUI=1.5\nROTATE=abc\nOUTPUT=DSI-2\n")
    live = unit.LiveSettings.from_config(
        config_for(tmp_path, wall_max_live=3, night_from_hour=23), conf
    )
    assert (
        live.caption == 2.0 and live.ui == 1.5 and live.rotate == 90
    )  # clamped, default
    assert live.wall_max_live == 3 and live.night.start_hour == 23
    assert live.as_json()["NIGHT_FROM"] == 23 and live.as_json()["MAX_LIVE"] == 3


def test_clean_updates_drops_the_unknown_and_clamps_the_rest():
    out = unit.clean_updates(
        {"CAPTION": "1.55", "UI": 3, "MAX_LIVE": "x", "FAL_KEY": "nope", "ROTATE": 180}
    )
    assert out == {"CAPTION": 1.55, "UI": 2.0, "ROTATE": 180}


def test_apply_writes_each_knob_to_its_file_and_to_the_live_settings(tmp_path: Path):
    conf, env = tmp_path / "unit.conf", tmp_path / ".env"
    conf.write_text("CAPTION=1.5\nUI=1.5\nMAX_LIVE=3\nROTATE=90\nOUTPUT=DSI-2\n")
    env.write_text("FAL_KEY=secret\nBP_WALL_MAX_LIVE=3\n")
    live = unit.LiveSettings.from_config(config_for(tmp_path, wall_max_live=3), conf)
    unit.apply(
        {"CAPTION": 1.2, "MAX_LIVE": 5, "NIGHT_FROM": 23, "NIGHT_ENABLED": 0},
        live,
        conf_path=conf,
        env_path=env,
    )
    assert unit.read_conf(conf) == {
        "CAPTION": "1.2",
        "UI": "1.5",
        "MAX_LIVE": "5",
        "ROTATE": "90",
        "OUTPUT": "DSI-2",
    }
    assert unit.read_conf(env) == {
        "FAL_KEY": "secret",
        "BP_WALL_MAX_LIVE": "5",
        "BP_NIGHT_FROM": "23",
        "BP_NIGHT_ENABLED": "false",
    }
    assert live.caption == 1.2 and live.wall_max_live == 5 and live.ui == 1.5
    assert live.night == NightSchedule(23, 7, 20, enabled=False, day_percent=None)


class FakeRun:
    """Stands in for subprocess.run: scripted stdout per nmcli subcommand."""

    def __init__(self, answers, fail=()):
        self.answers, self.fail, self.calls = answers, fail, []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        key = " ".join(argv[4:6])
        if key in self.fail:
            raise subprocess.CalledProcessError(
                4, argv, stderr="Error: Secrets were required, but not provided."
            )
        for prefix, out in self.answers.items():
            if " ".join(argv[4:]).startswith(prefix):
                return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


WIFI_LIST = (
    "no:home-wifi:90:WPA2\n"  # a stronger BSSID of the same network, not the active one
    "yes:home-wifi:84:WPA2\n"
    "no:Cafe\\: Guest:55:\n"  # a colon in the name, escaped by nmcli
    "no::30:WPA2\n"
    "no:odd-one:not-a-number:WPA2\n"  # one bad line loses that line only
    "no:neighbours-5G:22:WPA2 WPA3\n"
)


def test_connectivity_parses_nmcli_and_dedupes_ssids(monkeypatch):
    run = FakeRun(
        {
            "networking connectivity": "full\n",
            "-f ACTIVE,SSID,SIGNAL,SECURITY device wifi list": WIFI_LIST,
            "-f IP4.ADDRESS device show wlan0": "IP4.ADDRESS[1]:192.0.2.7/24\n",
        }
    )
    monkeypatch.setattr(unit.subprocess, "run", run)
    c = unit.connectivity()
    assert c.state == "full" and c.ssid == "home-wifi" and c.ip == "192.0.2.7"
    assert [n.ssid for n in c.networks] == ["home-wifi", "Cafe: Guest", "neighbours-5G"]
    assert c.networks[0].active and c.networks[0].signal == 84  # active beats stronger
    assert not c.networks[1].secured and c.networks[2].secured
    assert all(a[:4] == [unit.NMCLI, "-t", "--escape", "yes"] for a in run.calls)
    assert not any("--rescan" in a for a in run.calls)
    unit.connectivity(rescan=True)
    assert any("--rescan" in a for a in run.calls)


def test_connectivity_is_unknown_without_nmcli(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("nmcli")

    monkeypatch.setattr(unit.subprocess, "run", missing)
    assert unit.connectivity() == unit.Connectivity()


def test_join_passes_the_password_as_an_argument_never_a_shell(monkeypatch):
    run = FakeRun({"device wifi connect": "Device 'wlan0' successfully activated.\n"})
    monkeypatch.setattr(unit.subprocess, "run", run)
    ok, msg = unit.join("home-wifi", "s3cret")
    assert ok and "activated" in msg
    assert run.calls[-1][4:] == [
        "device",
        "wifi",
        "connect",
        "home-wifi",
        "password",
        "s3cret",
    ]
    assert unit.join("", "x") == (False, "that isn't a network name")
    assert unit.join("bad\nname", "x")[0] is False
    assert unit.forget("bad\nname")[0] is False


def test_join_reports_nmclis_last_line_on_failure_without_the_password(monkeypatch):
    def fail(argv, **kw):
        raise subprocess.CalledProcessError(
            4,
            argv,
            stderr="Error: Connection activation failed: (7) s3cret was refused.",
        )

    monkeypatch.setattr(unit.subprocess, "run", fail)
    ok, msg = unit.join("home-wifi", "s3cret")
    assert not ok and "refused" in msg and "s3cret" not in msg


def test_terse_fields_honour_nmclis_escapes():
    assert unit._fields("yes:Cafe\\: Guest:55:WPA2") == [
        "yes",
        "Cafe: Guest",
        "55",
        "WPA2",
    ]
    assert unit._fields("a\\\\b:c") == ["a\\b", "c"]
    assert unit._fields("IP4.ADDRESS[1]:192.0.2.7/24") == [
        "IP4.ADDRESS[1]",
        "192.0.2.7/24",
    ]


def test_write_conf_keeps_the_files_mode_and_never_exposes_the_key(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("FAL_KEY=secret\n")
    env.chmod(0o600)
    unit.write_conf({"BP_NIGHT_FROM": "23"}, env)
    assert env.stat().st_mode & 0o777 == 0o600
    fresh = tmp_path / "new.conf"
    unit.write_conf({"UI": "1"}, fresh)
    assert fresh.stat().st_mode & 0o777 == 0o600


def test_rotate_snaps_to_a_quarter_turn_and_nan_is_dropped():
    assert unit.clean_updates({"ROTATE": 45}) == {"ROTATE": 90}
    assert unit.clean_updates({"ROTATE": 300}) == {"ROTATE": 270}
    assert unit.clean_updates({"CAPTION": "nan"}) == {}
    assert unit.clean_updates({"CAPTION": float("inf")}) == {}
    assert unit.clean_updates({"CAPTION": 1e999}) == {}  # JSON's way of saying inf


def test_apply_names_the_file_that_could_not_be_written(tmp_path: Path):
    import pytest

    conf = tmp_path / "unit.conf"
    conf.write_text("CAPTION=1.5\n")
    env = tmp_path / "ro" / ".env"  # its directory does not exist and cannot be made
    (tmp_path / "ro").write_text("not a directory")
    live = unit.LiveSettings.from_config(config_for(tmp_path), conf)
    with pytest.raises(unit.SettingsWriteError, match=".env"):
        unit.apply(
            {"CAPTION": 1.7, "NIGHT_FROM": 23}, live, conf_path=conf, env_path=env
        )
    assert unit.read_conf(conf)["CAPTION"] == "1.7"  # the first file took
    assert live.caption == 1.5  # the process did not move


def test_refresh_splash_runs_the_root_helper_and_fails_soft(monkeypatch):
    calls = []

    def ok(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, stdout="bird-splash-refresh: rotate 0 — done\n", stderr=""
        )

    monkeypatch.setattr(unit.subprocess, "run", ok)
    assert unit.refresh_splash() == (True, "bird-splash-refresh: rotate 0 — done")
    assert calls == [["/usr/bin/sudo", "-n", unit.SPLASH_REFRESH]]

    def refused(argv, **kw):
        raise subprocess.CalledProcessError(
            1, argv, stderr="sudo: a password is required\n"
        )

    monkeypatch.setattr(unit.subprocess, "run", refused)
    assert unit.refresh_splash() == (False, "sudo: a password is required")

    def missing(argv, **kw):
        raise FileNotFoundError("sudo")

    monkeypatch.setattr(unit.subprocess, "run", missing)
    assert unit.refresh_splash()[0] is False
