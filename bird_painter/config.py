"""Configuration knobs. Defaults are the v0 values pinned in PLAN.md;
every knob can be overridden via environment variable (loaded from .env)."""

from __future__ import annotations

import datetime
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .styles import DEFAULT_STYLE, STYLES, is_style

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


def _env_int_opt(name: str) -> int | None:
    """An int env var that is genuinely optional: unset/empty → None."""
    raw = os.environ.get(name)
    if not raw or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got: {raw!r}") from None


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
    raise ConfigError(f"{name} must be one of {sorted(_TRUE | _FALSE)}, got: {raw!r}")


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


def _host() -> str:
    # Default all-interfaces so the frame + LAN devices reach the wall; set
    # BP_HOST=127.0.0.1 to restrict to this machine.
    return os.environ.get("BP_HOST") or "0.0.0.0"  # noqa: S104 — intentional LAN bind


def _hat_days(raw: str | None) -> tuple[tuple[int, int], ...]:
    """Parse BP_HAT_DAYS: comma-separated DD-MM recurring party-hat days
    (personal dates live only in the env, never in the repo)."""
    if not raw or not raw.strip():
        return ()
    days = []
    for part in raw.split(","):
        part = part.strip()
        try:
            day, month = (int(x) for x in part.split("-"))
            datetime.date(2000, month, day)  # validates the pair (leap-safe year)
        except (ValueError, TypeError):
            raise ConfigError(
                f"BP_HAT_DAYS entries must be DD-MM, got: {part!r}"
            ) from None
        days.append((day, month))
    return tuple(days)


def _hat_dates(raw: str | None) -> tuple[datetime.date, ...]:
    """Parse BP_HAT_DATES: comma-separated DD-MM-YYYY one-time party dates."""
    if not raw or not raw.strip():
        return ()
    dates = []
    for part in raw.split(","):
        part = part.strip()
        try:
            day, month, year = (int(x) for x in part.split("-"))
            dates.append(datetime.date(year, month, day))
        except (ValueError, TypeError):
            raise ConfigError(
                f"BP_HAT_DATES entries must be DD-MM-YYYY, got: {part!r}"
            ) from None
    return tuple(dates)


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
    # restricts predictions to species plausible at that PLACE (its meta
    # model), cutting implausible detections. The time of year is a separate
    # opt-in — see seasonal_filter below. Both coordinates must be set to
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
    wall_max_live: int = field(default_factory=lambda: _env_int("BP_WALL_MAX_LIVE", 12))
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
    # Bind address (see _host): all-interfaces by default so the e-paper frame
    # and other devices on the LAN can reach the wall / /wall.png.
    host: str = field(default_factory=lambda: _host())
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
    # Artifacts (painting + audio clip) are purged after this many days; the
    # archive is a rolling month by default (owner decision 2026-07-27).
    retention_days: int = field(
        default_factory=lambda: _env_int("BP_RETENTION_DAYS", 31)
    )
    # Occasion hats: personal party-hat days (DD-MM, recurring) and one-time
    # dates (DD-MM-YYYY) — env-only so they stay out of the public repo.
    # Public holidays are in occasions.py.
    hat_days: tuple[tuple[int, int], ...] = field(
        default_factory=lambda: _hat_days(os.environ.get("BP_HAT_DAYS"))
    )
    hat_dates: tuple[datetime.date, ...] = field(
        default_factory=lambda: _hat_dates(os.environ.get("BP_HAT_DATES"))
    )
    # Start the live mic listener alongside the wall. Off → wall-only (tests,
    # QA, or a machine with no mic); the /dev/paint endpoint still works.
    enable_listener: bool = field(
        default_factory=lambda: _env_bool("BP_ENABLE_LISTENER", True)
    )
    # Narrow the location filter to the time of year as well. OFF by default,
    # so BP_LATITUDE/BP_LONGITUDE mean "plausible here" rather than "plausible
    # here, this week": BirdNET's seasonal list is about half the size for the
    # same place, and what it removes is removed silently — no detection, no
    # log line, indistinguishable from a dead microphone (2026-08-05: a
    # nightingale identified at 0.87 confidence vanished exactly this way).
    seasonal_filter: bool = field(
        default_factory=lambda: _env_bool("BP_SEASONAL_FILTER", False)
    )
    # Clean up the archived detection clip — denoise, band-limit to the bird's
    # own band, normalise — so the wall's replay is audible rather than a
    # rumble with a bird somewhere in it. Off archives the raw cut instead.
    enhance_clips: bool = field(
        default_factory=lambda: _env_bool("BP_ENHANCE_CLIPS", True)
    )
    # Night mode (#122): between these local hours the backlight is dimmed
    # to BP_NIGHT_BRIGHTNESS percent (on a panel that exposes one) and the
    # wall dims itself (everywhere). A lit cream wall in a dark room is a
    # lamp; the table model lives in living rooms. Same hour twice = never.
    night_enabled: bool = field(
        default_factory=lambda: _env_bool("BP_NIGHT_ENABLED", True)
    )
    night_from_hour: int = field(default_factory=lambda: _env_int("BP_NIGHT_FROM", 22))
    night_to_hour: int = field(default_factory=lambda: _env_int("BP_NIGHT_TO", 7))
    night_brightness: int = field(
        default_factory=lambda: _env_int("BP_NIGHT_BRIGHTNESS", 20)
    )
    # The level to restore in the morning. Unset = the panel's own level as
    # seen by day (a restart at night can't tell day from dim, so a unit
    # that restarts inside the window comes back to full at dawn — set this
    # to pin it).
    night_day_brightness: int | None = field(
        default_factory=lambda: _env_int_opt("BP_NIGHT_DAY_BRIGHTNESS")
    )
    # The painting style (bird_painter/styles.py); a table model's settings
    # screen overrides this per unit through unit.conf.
    style: str = field(
        default_factory=lambda: os.environ.get("BP_STYLE") or DEFAULT_STYLE
    )
    # Which /sys/class/backlight/<name> to drive; unset = the first found.
    night_backlight: str | None = field(
        default_factory=lambda: os.environ.get("BP_NIGHT_BACKLIGHT") or None
    )

    def __post_init__(self) -> None:
        if not is_style(self.style):
            raise ConfigError(
                f"BP_STYLE must be one of {', '.join(s.key for s in STYLES)}; got "
                f"{self.style!r}"
            )
        for env, value in (
            ("BP_NIGHT_FROM", self.night_from_hour),
            ("BP_NIGHT_TO", self.night_to_hour),
        ):
            if not 0 <= value <= 23:
                raise ConfigError(f"{env} is an hour, 0..23; got {value}")
        for env, value in (
            ("BP_NIGHT_BRIGHTNESS", self.night_brightness),
            ("BP_NIGHT_DAY_BRIGHTNESS", self.night_day_brightness),
        ):
            if value is not None and not 1 <= value <= 100:
                raise ConfigError(f"{env} is a percentage, 1..100; got {value}")
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
