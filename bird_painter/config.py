"""Configuration knobs. Defaults are the v0 values pinned in PLAN.md;
every knob can be overridden via environment variable (loaded from .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Config:
    # Paint TTL doubles as the per-species repaint cooldown (one knob, not two).
    paint_ttl_seconds: int = field(
        default_factory=lambda: _env_int("BP_PAINT_TTL_SECONDS", 3 * 60 * 60)
    )
    confidence_floor: float = field(
        default_factory=lambda: _env_float("BP_CONFIDENCE_FLOOR", 0.6)
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
    archive_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("BP_ARCHIVE_DIR", "data/archive"))
    )
    fal_key: str = field(default_factory=lambda: os.environ.get("FAL_KEY", ""))


def load_config() -> Config:
    return Config()
