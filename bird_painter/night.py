"""Night mode for the table model — dim the panel's backlight on a schedule.

A backlit panel showing a cream wall is a lamp; in a living room at 23:00
that is the difference between an ornament and a nuisance (#122). The unit's
panel exposes `/sys/class/backlight/<name>/brightness`, group-writable by
`video`, which the service user is in — so the service writes the LEDs
directly. No udev rule, no sudo. The page dims itself as well, on the
`night` flag `/api/live` carries — that is the only dimming a screen with
no backlight knob gets.

The schedule is per unit and local time. Transitions are written ONCE, when
the clock crosses them — never on every tick — so a hand adjustment during
the day is not fought; and a transition whose write failed stays pending,
so a transient error is retried a minute later rather than leaving the wall
bright until morning.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BACKLIGHT_ROOT = Path("/sys/class/backlight")
FULL = 100  # the day level when nothing better is known


@dataclass(frozen=True)
class NightSchedule:
    """Dim from `start_hour` to `end_hour` (local, 24h; may wrap midnight) to
    `night_percent` of the panel's maximum. `day_percent` is what to restore
    in the morning; None = whatever the panel showed by day (see
    `NightWatch.tick`). `enabled=False` never writes anything; the same hour
    twice means never."""

    start_hour: int = 22
    end_hour: int = 7
    night_percent: int = 20
    enabled: bool = True
    day_percent: int | None = None

    def is_night(self, at: datetime) -> bool:
        if not self.enabled or self.start_hour == self.end_hour:
            return False
        h = at.hour + at.minute / 60
        if self.start_hour < self.end_hour:
            return self.start_hour <= h < self.end_hour
        return h >= self.start_hour or h < self.end_hour  # wraps midnight

    @classmethod
    def from_config(cls, config) -> NightSchedule:
        return cls(
            start_hour=config.night_from_hour,
            end_hour=config.night_to_hour,
            night_percent=config.night_brightness,
            enabled=config.night_enabled,
            day_percent=config.night_day_brightness,
        )


def find_backlight(root: Path = BACKLIGHT_ROOT, name: str | None = None) -> Path | None:
    """The backlight device to drive: `name` if given (`BP_NIGHT_BACKLIGHT`),
    else the first in name order that has a writable `brightness` — which is
    the whole search. The target panel exposes exactly one
    (`panel_backlight@1`); a laptop may expose several (`acpi_video0` sorts
    before `intel_backlight`), which is what the override is for."""
    candidates = [root / name] if name else []
    if not name:
        try:
            candidates = sorted(root.iterdir())
        except OSError:
            return None
    for dev in candidates:
        node = dev / "brightness"
        if (dev / "max_brightness").exists() and os.access(node, os.W_OK):
            return dev
    return None


class Backlight:
    """One backlight device. Percent in, sysfs integers out."""

    def __init__(self, device: Path):
        self.device = device
        # A max of 0 would be a broken driver; 1 keeps the arithmetic finite.
        self.max = max(1, int((device / "max_brightness").read_text().strip() or 1))

    def read_percent(self) -> int:
        raw = int((self.device / "brightness").read_text().strip() or 0)
        return round(100 * raw / self.max)

    def write_percent(self, percent: int) -> None:
        raw = max(0, min(self.max, round(self.max * max(0, min(100, percent)) / 100)))
        (self.device / "brightness").write_text(f"{raw}\n")


class NightWatch:
    """The daemon thread: checks the schedule once a minute and writes the
    backlight on transitions. `is_night` is the state `/api/live` reports
    (the page dims itself on it) — None until the first tick.

    The day level: the configured `day_percent` if there is one; otherwise
    the panel's own level, read when the day is seen — at a daytime start,
    and again at each day→night crossing (so a hand adjustment during the
    day is what comes back the next morning). A start inside the night
    window reads the panel only if it is still brighter than the night
    level: after a restart that follows the dim, the panel is AT the night
    level, and taking that for "day" would leave the wall dim all day
    (review of #149). With nothing known, morning means full."""

    def __init__(
        self,
        schedule: NightSchedule,
        backlight: Backlight | None,
        *,
        interval_seconds: float = 60.0,
        clock=datetime.now,
    ):
        self.schedule = schedule
        self.backlight = backlight
        self.interval = interval_seconds
        self.clock = clock
        self.is_night: bool | None = None
        self._day_percent = schedule.day_percent
        self._wake = threading.Event()  # set to make the loop tick now
        self._stop = False
        self._thread: threading.Thread | None = None

    @property
    def day_percent(self) -> int:
        return self._day_percent if self._day_percent is not None else FULL

    def _learn_day_level(self, *, only_if_brighter_than: int | None = None) -> None:
        if self.schedule.day_percent is not None:
            return  # configured: the panel's reading never overrides it
        seen = self.backlight.read_percent()
        if only_if_brighter_than is not None and seen <= only_if_brighter_than:
            return
        self._day_percent = seen

    def tick(self) -> bool | None:
        """One check. Returns the transition applied (True = went dark,
        False = went light) or None when nothing changed — including when
        the write failed, so the transition stays pending and is retried."""
        night = self.schedule.is_night(self.clock())
        if night == self.is_night:
            return None
        previous = self.is_night
        if self.backlight is None:
            self.is_night = night
            logger.info("night: %s (no backlight; the page dims itself)", _word(night))
            return night
        try:
            if night:
                if previous is False:
                    self._learn_day_level()
                elif previous is None:
                    self._learn_day_level(only_if_brighter_than=self.schedule.night_percent)
                self.backlight.write_percent(self.schedule.night_percent)
                written: int | None = self.schedule.night_percent
            elif previous is None:
                self._learn_day_level()  # a daytime start: nothing to restore
                written = None
            else:
                written = self.day_percent
                self.backlight.write_percent(written)
        except (OSError, ValueError) as exc:
            logger.warning(
                "night: could not %s the backlight (%s); retrying next tick",
                "dim" if night else "restore",
                exc,
            )
            return None
        self.is_night = night
        if written is None:
            logger.info(
                "night: off (daytime start; backlight left at %d%%)", self.day_percent
            )
        else:
            logger.info("night: %s — backlight %d%%", _word(night), written)
        return night

    def reschedule(self, schedule: NightSchedule) -> None:
        """A new schedule (the settings screen, #123). The state is kept —
        the next tick compares the new answer with it and writes the panel
        only if they differ, so a unit dimmed at 22:00 whose owner moves
        "from" to 23:00 gets its light back within a second, and one whose
        schedule didn't change for it sees no write at all."""
        self.schedule = schedule
        if schedule.day_percent is not None:
            self._day_percent = schedule.day_percent
        self.start()  # a watch that began disabled has no thread yet
        self._wake.set()

    def start(self) -> None:
        if self._thread is not None:
            return
        if not self.schedule.enabled:
            self.is_night = False
            logger.info("night: disabled (BP_NIGHT_ENABLED)")
            return

        def run() -> None:
            while not self._stop:
                self._wake.clear()
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 — the wall must survive night
                    logger.exception("night: tick failed")
                self._wake.wait(self.interval)

        self._thread = threading.Thread(target=run, name="night", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()


def _word(night: bool) -> str:
    return "on" if night else "off"


def watch_from_config(config) -> NightWatch:
    """The watch for this machine: the config's schedule, and the backlight
    the kernel exposes (`BP_NIGHT_BACKLIGHT` names one; else the first).
    None on this Mac and on the recorder's headless Pi — the watch then only
    keeps the state the wall reads."""
    device = find_backlight(name=config.night_backlight)
    backlight = None
    if device is not None:
        try:
            backlight = Backlight(device)
        except (OSError, ValueError):
            logger.warning("night: backlight at %s is unreadable; state only", device)
    return NightWatch(NightSchedule.from_config(config), backlight)
