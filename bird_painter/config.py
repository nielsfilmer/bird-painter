"""Configuration knobs. Defaults are the v0 values pinned in PLAN.md;
every knob can be overridden via environment variable (loaded from .env)."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# birdnetlib clamps min_conf to this range and filters with strict `>`.
CONFIDENCE_FLOOR_MIN = 0.01
CONFIDENCE_FLOOR_MAX = 0.99

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(ValueError):
    """A bad environment-variable value. Carries a message meant to be shown
    to the user (the CLIs catch it and exit cleanly instead of tracebacking)."""


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got: {raw!r}") from None


def _env_float_opt(name: str) -> float | None:
    """A float env var that is genuinely optional: unset/empty → None (the knob
    is off), rather than a numeric default."""
    raw = os.environ.get(name)
    if not raw or not raw.strip():
        return None
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got: {raw!r}") from None


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got: {raw!r}") from None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    # Don't silently disable the mic on a typo — say so.
    raise ConfigError(
        f"{name} must be one of {sorted(_TRUE | _FALSE)}, got: {raw!r}"
    )


def _confidence_floor() -> float:
    """The confidence floor, clamped to birdnetlib's honest [0.01, 0.99] range
    (it clamps internally anyway; clamping here keeps Config truthful and warns
    so a surprising 0 or 1.0 doesn't pass silently)."""
    value = _env_float("BP_CONFIDENCE_FLOOR", 0.6)
    clamped = min(CONFIDENCE_FLOOR_MAX, max(CONFIDENCE_FLOOR_MIN, value))
    if clamped != value:
        logger.warning(
            "BP_CONFIDENCE_FLOOR %s is outside [%.2f, %.2f]; using %s",
            value,
            CONFIDENCE_FLOOR_MIN,
            CONFIDENCE_FLOOR_MAX,
            clamped,
        )
    return clamped


def _resolve_device(raw: str | None) -> int | str | None:
    """A device given as a numeric string is a device index; anything else is
    a name substring sounddevice matches; empty/None means the default."""
    if raw is None or raw.strip() == "":
        return None
    raw = raw.strip()
    return int(raw) if raw.isdigit() else raw


@dataclass(frozen=True)
class Config:
    # Paint TTL doubles as the per-species repaint cooldown (one knob, not two).
    paint_ttl_seconds: int = field(
        default_factory=lambda: _env_int("BP_PAINT_TTL_SECONDS", 3 * 60 * 60)
    )
    confidence_floor: float = field(default_factory=_confidence_floor)
    # Location filter: when both a latitude and longitude are set, BirdNET
    # restricts predictions to species plausible at that place and time of year
    # (its meta model), cutting implausible detections. Both must be set to
    # enable it; unset = off (global model). Validated in __post_init__.
    latitude: float | None = field(
        default_factory=lambda: _env_float_opt("BP_LATITUDE")
    )
    longitude: float | None = field(
        default_factory=lambda: _env_float_opt("BP_LONGITUDE")
    )
    analysis_window_seconds: int = field(
        default_factory=lambda: _env_int("BP_ANALYSIS_WINDOW_SECONDS", 15)
    )
    max_paints_per_hour: int = field(
        default_factory=lambda: _env_int("BP_MAX_PAINTS_PER_HOUR", 20)
    )
    wall_max_live: int = field(
        default_factory=lambda: _env_int("BP_WALL_MAX_LIVE", 12)
    )
    # Server-rendered /wall.png size (the e-paper frame fetches this). Default
    # is the Waveshare 13.3" Spectra 6 panel's native 1600×1200 landscape;
    # override for a different panel. Optional serif font paths for the render
    # (defaults auto-discover DejaVu/Georgia; see render.py).
    wall_png_width: int = field(
        default_factory=lambda: _env_int("BP_WALL_PNG_WIDTH", 1600)
    )
    wall_png_height: int = field(
        default_factory=lambda: _env_int("BP_WALL_PNG_HEIGHT", 1200)
    )
    wall_font: str | None = field(
        default_factory=lambda: os.environ.get("BP_WALL_FONT") or None
    )
    wall_font_italic: str | None = field(
        default_factory=lambda: os.environ.get("BP_WALL_FONT_ITALIC") or None
    )
    port: int = field(default_factory=lambda: _env_int("BP_PORT", 8537))
    # Bind address. Defaults to 0.0.0.0 (all interfaces) because bird-painter is
    # an installation meant to be viewed from other devices — the e-paper frame
    # fetches /wall.png over the LAN, and you browse the wall from a laptop or
    # phone. Set BP_HOST=127.0.0.1 to restrict it to the local machine only.
    host: str = field(default_factory=lambda: os.environ.get("BP_HOST") or "0.0.0.0")
    # Mic input device: a numeric index or a name substring (see
    # `python -m bird_painter --list-devices`). None = system default input.
    input_device: int | str | None = field(
        default_factory=lambda: _resolve_device(os.environ.get("BP_INPUT_DEVICE"))
    )
    archive_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("BP_ARCHIVE_DIR", "data/archive"))
    )
    fal_key: str = field(default_factory=lambda: os.environ.get("FAL_KEY", ""))
    # fal model id for the brush. schnell is cheapest/fastest but follows the
    # no-text/white-background prompt loosely; fal-ai/flux/dev obeys it far
    # better (pricier). Override with BP_FAL_MODEL. Default sourced from brush
    # (lazy import — don't drag httpx into config just to read a constant).
    fal_model: str = field(
        default_factory=lambda: os.environ.get("BP_FAL_MODEL") or _brush_default_model()
    )
    # Start the live mic listener alongside the wall. Off → wall-only (tests,
    # QA, or a machine with no mic); the /dev/paint endpoint still works.
    enable_listener: bool = field(
        default_factory=lambda: _env_bool("BP_ENABLE_LISTENER", True)
    )

    def __post_init__(self) -> None:
        # The location filter keys on a lat/lon pair — one without the other is
        # a misconfiguration, not a partial filter. Fail loudly rather than
        # silently ignoring the half that was set.
        if (self.latitude is None) != (self.longitude is None):
            raise ConfigError(
                "BP_LATITUDE and BP_LONGITUDE must be set together (or both "
                "left unset to disable the location filter)."
            )
        if self.latitude is not None and not -90.0 <= self.latitude <= 90.0:
            raise ConfigError(
                f"BP_LATITUDE must be between -90 and 90, got: {self.latitude}"
            )
        if self.longitude is not None and not -180.0 <= self.longitude <= 180.0:
            raise ConfigError(
                f"BP_LONGITUDE must be between -180 and 180, got: {self.longitude}"
            )


def _brush_default_model() -> str:
    from .brush import DEFAULT_MODEL

    return DEFAULT_MODEL


def load_config() -> Config:
    return Config()


def load_config_or_exit() -> Config:
    """load_config() for CLI entrypoints: a bad env value prints its message
    and exits 2 instead of tracebacking."""
    try:
        return load_config()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from None
