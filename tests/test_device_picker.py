import dataclasses

import pytest

from bird_painter import capture
from bird_painter.__main__ import _maybe_pick_device
from bird_painter.capture import resolve_device_choice


def test_resolve_choice_blank_is_default():
    assert resolve_device_choice("", {0, 2, 4}) is None
    assert resolve_device_choice("   ", {0, 2, 4}) is None


def test_resolve_choice_valid_index():
    assert resolve_device_choice("2", {0, 2, 4}) == 2
    assert resolve_device_choice(" 4 ", {0, 2, 4}) == 4


def test_resolve_choice_unlisted_or_junk_falls_back_to_default():
    assert resolve_device_choice("9", {0, 2, 4}) is None  # not listed
    assert resolve_device_choice("abc", {0, 2, 4}) is None


def _cfg(config, **kw):
    return dataclasses.replace(config, **kw)


def test_picker_skipped_when_device_already_pinned(monkeypatch, config):
    monkeypatch.setenv("BP_INPUT_DEVICE", "3")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    called = []
    monkeypatch.setattr(
        "bird_painter.capture.select_input_device", lambda: called.append(1)
    )
    _maybe_pick_device(_cfg(config, enable_listener=True), skip=False)
    assert called == []  # respected the explicit pin


def test_picker_skipped_when_not_a_tty(monkeypatch, config):
    monkeypatch.delenv("BP_INPUT_DEVICE", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    called = []
    monkeypatch.setattr(
        "bird_painter.capture.select_input_device", lambda: called.append(1)
    )
    _maybe_pick_device(_cfg(config, enable_listener=True), skip=False)
    assert called == []


def test_picker_skipped_when_listener_disabled(monkeypatch, config):
    monkeypatch.delenv("BP_INPUT_DEVICE", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    called = []
    monkeypatch.setattr(
        "bird_painter.capture.select_input_device", lambda: called.append(1)
    )
    _maybe_pick_device(_cfg(config, enable_listener=False), skip=False)
    assert called == []


def test_picker_runs_and_exports_choice(monkeypatch, config):
    monkeypatch.delenv("BP_INPUT_DEVICE", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("bird_painter.capture.select_input_device", lambda: 4)
    _maybe_pick_device(_cfg(config, enable_listener=True), skip=False)
    import os

    assert os.environ["BP_INPUT_DEVICE"] == "4"


def test_picker_default_choice_leaves_env_unset(monkeypatch, config):
    monkeypatch.delenv("BP_INPUT_DEVICE", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("bird_painter.capture.select_input_device", lambda: None)
    _maybe_pick_device(_cfg(config, enable_listener=True), skip=False)
    import os

    assert "BP_INPUT_DEVICE" not in os.environ



_FAKE_DEVICES = (
    [
        (3, {"name": "Scarlett", "default_samplerate": 48000}),
        (4, {"name": "MacBook", "default_samplerate": 48000}),
    ],
    3,
)


def test_select_input_device_returns_chosen_index(monkeypatch):
    monkeypatch.setattr(capture, "_input_devices", lambda: _FAKE_DEVICES)
    monkeypatch.setattr(capture, "device_name", lambda d: f"dev{d}")
    monkeypatch.setattr("builtins.input", lambda prompt="": "4")
    assert capture.select_input_device() == 4


def test_select_input_device_blank_is_default(monkeypatch):
    monkeypatch.setattr(capture, "_input_devices", lambda: _FAKE_DEVICES)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert capture.select_input_device() is None


def test_select_input_device_eof_is_default(monkeypatch):
    monkeypatch.setattr(capture, "_input_devices", lambda: _FAKE_DEVICES)

    def raise_eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)
    assert capture.select_input_device() is None


def test_select_input_device_ctrl_c_aborts(monkeypatch):
    monkeypatch.setattr(capture, "_input_devices", lambda: _FAKE_DEVICES)

    def raise_kbd(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", raise_kbd)
    with pytest.raises(SystemExit) as exc:
        capture.select_input_device()
    assert exc.value.code == 130


def test_select_input_device_no_devices_returns_none(monkeypatch):
    monkeypatch.setattr(capture, "_input_devices", lambda: ([], None))
    assert capture.select_input_device() is None
