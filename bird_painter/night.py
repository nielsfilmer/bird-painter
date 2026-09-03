"""Night mode for the table model — dim the panel's backlight on a schedule.

A backlit panel showing a cream wall is a lamp; in a living room at 23:00
that is the difference between an ornament and a nuisance (#122). The unit's
panel exposes `/sys/class/backlight/<name>/brightness`, group-writable by
`video`, which the service user is in — so the service writes the LEDs
directly. No udev rule, no sudo, no CSS trick as the primary mechanism.

The schedule is per unit and local time. Transitions are written ONCE, when
the clock crosses them — never on every tick — so a hand adjustment during
the day is not fought, and a panel that isn't there (a dev machine, a
headless recorder) is simply left alone.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BACKLIGHT_ROOT = Path("/sys/class/backlight")


@dataclass(frozen=True)
class NightSchedule:
    """Dim from `start_hour` to `end_hour` (local, 24h; may wrap midnight) to
    `night_percent` of the panel's maximum; `day_percent` is restored after.
    `enabled=False` never writes anything."""

    start_hour: int = 22
    end_hour: int = 7
    night_percent: int = 20
    enabled: bool = True
    day_percent: int | None = None  # None = whatever the panel had at start

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
        )


def find_backlight(root: Path = BACKLIGHT_ROOT) -> Path | None:
    """The first backlight device with a writable brightness, else None."""
    try:
        for dev in sorted(root.iterdir()):
            node = dev / "brightness"
            if node.exists() and (dev / "max_brightness").exists():
                return dev
    except OSError:
        pass
    return None


class Backlight:
    """One backlight device. Percent in, sysfs integers out."""

    def __init__(self, device: Path):
        self.device = device
        self.max = int((device / "max_brightness").read_text().strip() or 1)

    def read_percent(self) -> int:
        raw = int((self.device / "brightness").read_text().strip() or 0)
        return round(100 * raw / self.max)

    def write_percent(self, percent: int) -> None:
        raw = max(0, min(self.max, round(self.max * max(0, min(100, percent)) / 100)))
        (self.device / "brightness").write_text(f"{raw}\n")


def watch_from_config(config) -> NightWatch:
    """The watch for this machine: the config's schedule, and the first
    backlight the kernel exposes (none on a dev box or the recorder — the
    watch then only keeps the state the wall reads)."""
    device = find_backlight()
    backlight = None
    if device is not None:
        try:
            backlight = Backlight(device)
        except (OSError, ValueError):
            logger.warning("night: backlight at %s is unreadable; state only", device)
    return NightWatch(NightSchedule.from_config(config), backlight)


class NightWatch:
    """The daemon thread: checks the schedule once a minute, writes the
    backlight on transitions. `state` is readable by the API (`/api/unit`)
    so the wall can dim its own chrome too."""

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
        self.is_night: bool | None = None  # unknown until the first tick
        self._day_percent = schedule.day_percent
        self._stop = threading.Event()  # replaced on reschedule to wake the loop
        self._stopped = False
        self._thread: threading.Thread | None = None

    def tick(self) -> bool | None:
        """One check. Returns the transition applied (True = went dark,
        False = went light) or None when nothing changed."""
        night = self.schedule.is_night(self.clock())
        if night == self.is_night:
            return None
        previous = self.is_night
        self.is_night = night
        if self.backlight is None:
            logger.info("night: %s (no backlight to write)", "on" if night else "off")
            return night
        try:
            if night:
                if self._day_percent is None:
                    self._day_percent = self.backlight.read_percent()
                self.backlight.write_percent(self.schedule.night_percent)
            elif previous is not None or self._day_percent is not None:
                # Going light: restore the day level. On the very first tick
                # of a daytime start there is nothing to restore.
                self.backlight.write_percent(self._day_percent or 100)
            logger.info(
                "night: %s — backlight %d%%",
                "on" if night else "off",
                self.schedule.night_percent if night else (self._day_percent or 100),
            )
        except OSError:
            logger.exception("night: could not write the backlight")
        return night

    def reschedule(self, schedule: NightSchedule) -> None:
        """A new schedule from the settings screen: forget the current
        state so the next tick re-evaluates and writes the panel if the
        answer changed (a unit dimmed at 22:00 whose owner moves "from" to
        23:00 gets its light back within a minute)."""
        self.schedule = schedule
        self.is_night = None
        self._stop.set()  # wake the loop
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return

        def run() -> None:
            while not self._stopped:
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 — the wall must survive night
                    logger.exception("night: tick failed")
                self._stop.wait(self.interval)

        self._thread = threading.Thread(target=run, name="night", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped = True
        self._stop.set()
