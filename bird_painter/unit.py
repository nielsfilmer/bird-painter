"""The unit's own settings and network — what the table model's settings
screen reads and writes (#123).

Two files hold a unit's settings, and this module is the service's one
reader and writer of both:

- `~/.config/bird-painter/unit.conf` — the install script's KEY=VALUE file
  (CAPTION, UI, MAX_LIVE, ROTATE, STYLE, OUTPUT): what makes this unit the
  7" or the 10", and how it paints. The screen edits it in place; a re-run
  of the script keeps it.
- `~/bird-painter/.env` — the app's own knobs. The screen touches only the
  night group (BP_NIGHT_*) and the bird cap (BP_WALL_MAX_LIVE, which the
  install script mirrors from unit.conf); everything else — the key above
  all — is preserved byte for byte.

Plus the two things a unit in someone else's house must be able to do from
its own screen: see which network it is on, and join another. NetworkManager
is driven through `nmcli`; the install script grants the unit's user
NetworkManager's polkit actions, because a service with no session otherwise
gets "auth" and nothing can answer it. Every subprocess is an argv list,
never a shell: an SSID is untrusted text that arrives from a touchscreen.
A password is handed to nmcli as an argument, so it is never in a log line —
but it is in that process's argv (`/proc/<pid>/cmdline`) for the seconds the
join takes, readable by any user on the box. The unit has one.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .night import NightSchedule
from .styles import DEFAULT_STYLE, STYLES, style_for

logger = logging.getLogger(__name__)

# Overridable so a throwaway instance (make qa-up, the tests) never writes
# into the developer's own home.
UNIT_CONF = Path(
    os.environ.get("BP_UNIT_CONF")
    or Path.home() / ".config" / "bird-painter" / "unit.conf"
)
ENV_FILE = Path(os.environ.get("BP_ENV_FILE") or Path.home() / "bird-painter" / ".env")
NMCLI = "/usr/bin/nmcli"
NMCLI_TIMEOUT = 30
JOIN_TIMEOUT = 60


@dataclass(frozen=True)
class Knob:
    """One setting the screen may change: its bounds, the step its stepper
    takes, which file it lives in (`unit.conf` bare, `.env` as BP_*), and
    for MAX_LIVE both. The ONE table: the page reads it from /unit, so a
    stepper can't drift from the server's bounds (review of #157)."""

    lo: float
    hi: float
    step: float
    unit: bool = True
    env: str | None = None

    @property
    def integer(self) -> bool:
        return float(self.step).is_integer()


KNOBS: dict[str, Knob] = {
    "CAPTION": Knob(0.5, 2.0, 0.1),
    "UI": Knob(0.5, 2.0, 0.1),
    "MAX_LIVE": Knob(1, 12, 1, env="BP_WALL_MAX_LIVE"),
    "ROTATE": Knob(0, 270, 90),  # a quarter turn; snapped in _bounded
    "NIGHT_ENABLED": Knob(0, 1, 1, unit=False, env="BP_NIGHT_ENABLED"),
    "NIGHT_FROM": Knob(0, 23, 1, unit=False, env="BP_NIGHT_FROM"),
    "NIGHT_TO": Knob(0, 23, 1, unit=False, env="BP_NIGHT_TO"),
    "NIGHT_BRIGHTNESS": Knob(5, 100, 5, unit=False, env="BP_NIGHT_BRIGHTNESS"),
}


# The one non-numeric setting: a choice from styles.py, in unit.conf.
CHOICES: dict[str, tuple[str, ...]] = {"STYLE": tuple(s.key for s in STYLES)}


def knobs_json() -> dict:
    return {k: {"min": v.lo, "max": v.hi, "step": v.step} for k, v in KNOBS.items()}


@dataclass
class LiveSettings:
    """The few config values the screen changes at runtime, kept apart from
    the frozen Config so the routes that read them see the new value at
    once. Built from the config at startup."""

    wall_max_live: int
    caption: float = 1.0
    ui: float = 1.0
    rotate: int = 90
    style: str = DEFAULT_STYLE
    night: NightSchedule = field(default_factory=NightSchedule)

    @classmethod
    def from_config(cls, config, conf_path: Path | None = None) -> LiveSettings:
        unit = read_conf(conf_path or UNIT_CONF)
        return cls(
            wall_max_live=config.wall_max_live,
            caption=_bounded("CAPTION", unit.get("CAPTION"), 1.0),
            ui=_bounded("UI", unit.get("UI"), 1.0),
            rotate=int(_bounded("ROTATE", unit.get("ROTATE"), 90)),
            style=style_for(unit.get("STYLE") or config.style).key,
            night=NightSchedule.from_config(config),
        )

    def as_json(self) -> dict:
        n = self.night
        return {
            "CAPTION": self.caption,
            "UI": self.ui,
            "MAX_LIVE": self.wall_max_live,
            "ROTATE": self.rotate,
            "STYLE": self.style,
            "NIGHT_ENABLED": 1 if n.enabled else 0,
            "NIGHT_FROM": n.start_hour,
            "NIGHT_TO": n.end_hour,
            "NIGHT_BRIGHTNESS": n.night_percent,
        }


def _bounded(key: str, raw, default: float) -> float:
    knob = KNOBS[key]
    try:
        value = float(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    if value != value:  # NaN: min/max would pass it through by argument order
        value = default
    value = min(knob.hi, max(knob.lo, value))
    if key == "ROTATE":
        value = int((value + 45) // 90) * 90 % 360  # wlr-randr knows quarter turns only
    return value


# ---- the files -------------------------------------------------------------


class SettingsWriteError(OSError):
    """A settings file could not be written; the message names it."""


def read_conf(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; comments and blanks ignored; last value wins. A file
    that can't be read — missing, or not UTF-8 — is empty, not fatal: this
    runs at startup, and one stray byte in .env must not stop the wall."""
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    except (OSError, UnicodeDecodeError):
        pass
    return values


def write_conf(updates: dict[str, str], path: Path) -> dict[str, str]:
    """Merge `updates` into the file, keeping every other line as it was
    (comments, order, a FAL_KEY), and write it atomically. A key that appears
    twice collapses to its first line. Returns the merged values."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        mode = path.stat().st_mode & 0o777
    except (OSError, UnicodeDecodeError):
        lines, mode = [], 0o600
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.partition("=")[0].strip()
        if key in updates and not line.lstrip().startswith("#"):
            if key not in seen:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
            continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    # The temp file is born with the target's mode (0600 for a new file): a
    # .env with the key in it must never sit world-readable, even briefly.
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return read_conf(path)


def clean_updates(payload: dict) -> dict[str, float | str]:
    """Validate a settings write from the page: known keys only, numeric,
    clamped — or, for a choice, one of its allowed values. Anything else is
    dropped, not errored — the screen only ever sends what it shows, so an
    unknown key is a bug there, not a request."""
    updates: dict[str, float | str] = {}
    for key, allowed in CHOICES.items():
        if key in payload and isinstance(payload[key], str) and payload[key] in allowed:
            updates[key] = payload[key]
    for key in KNOBS:
        if key not in payload:
            continue
        try:
            value = float(payload[key])
        except (TypeError, ValueError):
            continue
        if value != value or value in (float("inf"), float("-inf")):
            continue  # NaN and the infinities are not settings
        updates[key] = _bounded(key, value, value)
    return updates


def _fmt(key: str, value: float | str) -> str:
    if isinstance(value, str):
        return f'"{value}"'  # as the install script writes it (the file is sourced)
    if key == "NIGHT_ENABLED":
        return "true" if value else "false"
    if KNOBS[key].integer:
        return str(int(value))
    return f"{value:g}"


def apply(
    updates: dict[str, float],
    live: LiveSettings,
    *,
    conf_path: Path | None = None,
    env_path: Path | None = None,
) -> LiveSettings:
    """Write the changed knobs to their files and then to the running
    settings — the files first, so a write that fails leaves the process as
    it was and the error says which file did not take (the other, if any,
    already did; a retry is idempotent). The caller reschedules the night
    watch from `live.night`."""
    conf_path = conf_path or UNIT_CONF
    env_path = env_path or ENV_FILE
    unit_writes = {
        k: _fmt(k, v) for k, v in updates.items() if k in CHOICES or KNOBS[k].unit
    }
    env_writes = {
        KNOBS[k].env: _fmt(k, v)
        for k, v in updates.items()
        if k not in CHOICES and KNOBS[k].env
    }
    for writes, path in ((unit_writes, conf_path), (env_writes, env_path)):
        if not writes:
            continue
        try:
            write_conf(writes, path)
        except OSError as exc:
            raise SettingsWriteError(f"could not write {path.name}: {exc}") from exc
    if "CAPTION" in updates:
        live.caption = updates["CAPTION"]
    if "UI" in updates:
        live.ui = updates["UI"]
    if "MAX_LIVE" in updates:
        live.wall_max_live = int(updates["MAX_LIVE"])
    if "ROTATE" in updates:
        live.rotate = int(updates["ROTATE"])
    if "STYLE" in updates:
        live.style = str(updates["STYLE"])
    n = live.night
    live.night = NightSchedule(
        start_hour=int(updates.get("NIGHT_FROM", n.start_hour)),
        end_hour=int(updates.get("NIGHT_TO", n.end_hour)),
        night_percent=int(updates.get("NIGHT_BRIGHTNESS", n.night_percent)),
        enabled=bool(updates.get("NIGHT_ENABLED", n.enabled)),
        day_percent=n.day_percent,
    )
    changed = ", ".join(f"{k}={_fmt(k, v)}" for k, v in updates.items())
    logger.info("unit: settings changed: %s", changed)
    return live


# ---- network ---------------------------------------------------------------


@dataclass(frozen=True)
class Network:
    ssid: str
    signal: int  # percent
    secured: bool
    active: bool = False


@dataclass(frozen=True)
class Connectivity:
    """NetworkManager's own word for it — none, portal, limited, full — or
    'unknown' when nmcli isn't there (a dev machine)."""

    state: str = "unknown"
    ssid: str | None = None
    ip: str | None = None
    networks: list[Network] = field(default_factory=list)

    def as_json(self) -> dict:
        return asdict(self)


def _nmcli(*args: str, timeout: float = NMCLI_TIMEOUT) -> str:
    # An absolute path and an argv list: nothing here is shell-interpreted,
    # and an SSID from the touchscreen is just one argument. Terse output
    # with escapes ON: a `:` inside an SSID arrives as `\:`, see _fields.
    return subprocess.run(  # noqa: S603
        [NMCLI, "-t", "--escape", "yes", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    ).stdout


def _fields(line: str) -> list[str]:
    """Split one terse nmcli line on its unescaped colons ("Cafe\\: Guest"
    is one field). A `\\\\` is a literal backslash."""
    out: list[str] = []
    cur: list[str] = []
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if c == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


def connectivity(rescan: bool = False) -> Connectivity:
    """Where the unit stands. Fail-soft: any nmcli trouble is 'unknown'."""
    try:
        state = _nmcli(
            "networking", "connectivity", *(["check"] if rescan else [])
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return Connectivity()
    ssid = ip = None
    networks: list[Network] = []
    try:
        listing = _nmcli(
            "-f",
            "ACTIVE,SSID,SIGNAL,SECURITY",
            "device",
            "wifi",
            "list",
            *(["--rescan", "yes"] if rescan else []),
        )
    except (OSError, subprocess.SubprocessError):
        listing = ""
    for line in listing.splitlines():
        # One odd line (a signal that isn't a number) loses that line, not
        # the list after it.
        parts = _fields(line)
        if len(parts) < 4 or not parts[1]:
            continue
        try:
            signal = int(parts[2] or 0)
        except ValueError:
            continue
        active = parts[0] == "yes"
        net = Network(
            ssid=parts[1],
            signal=signal,
            secured=parts[3].strip() not in ("", "--"),
            active=active,
        )
        networks.append(net)
        if active:
            ssid = net.ssid
    try:
        for line in _nmcli("-f", "IP4.ADDRESS", "device", "show", "wlan0").splitlines():
            parts = _fields(line)
            if parts[0].startswith("IP4.ADDRESS") and len(parts) > 1:
                ip = parts[1].split("/")[0]
                break
    except (OSError, subprocess.SubprocessError):
        pass
    # One row per SSID — the active one whatever its signal, else the
    # strongest; the active one on top.
    best: dict[str, Network] = {}
    for net in networks:
        held = best.get(net.ssid)
        if held is None or (net.active and not held.active):
            best[net.ssid] = net
        elif held.active == net.active and net.signal > held.signal:
            best[net.ssid] = net
    ordered = sorted(best.values(), key=lambda n: (not n.active, -n.signal))
    return Connectivity(state=state or "unknown", ssid=ssid, ip=ip, networks=ordered)


_SSID_OK = re.compile(r"^[^\x00-\x1f\x7f]{1,32}$")


def join(ssid: str, password: str | None) -> tuple[bool, str]:
    """Join a network. Returns (ok, message). The password never reaches a
    log line."""
    if not _SSID_OK.match(ssid or ""):
        return False, "that isn't a network name"
    args = ["device", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    try:
        out = _nmcli(*args, timeout=JOIN_TIMEOUT)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip().splitlines()
        reason = detail[-1] if detail else "NetworkManager refused"
        if password:
            reason = reason.replace(password, "…")
        logger.info("wifi: join %r failed: %s", ssid, reason)
        return False, reason
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not run nmcli: {exc.__class__.__name__}"
    logger.info("wifi: joined %r", ssid)
    tail = out.strip().splitlines()
    return True, tail[-1] if tail else "connected"


def forget(ssid: str) -> tuple[bool, str]:
    if not _SSID_OK.match(ssid or ""):
        return False, "that isn't a network name"
    try:
        _nmcli("connection", "delete", "id", ssid)
    except (OSError, subprocess.SubprocessError):
        return False, "could not forget that network"
    logger.info("wifi: forgot %r", ssid)
    return True, "forgotten"


SPLASH_REFRESH = "/usr/local/sbin/bird-splash-refresh"


def refresh_splash() -> tuple[bool, str]:
    """Redraw the boot splash for the unit's current stand — after ROTATE
    changed from the screen. The root helper the installer set up (with a
    sudoers line for exactly this command, no arguments) reads unit.conf
    itself and rebuilds the initramfs; a minute's work, so the caller runs
    it off the request (the helper serialises itself, so a second rotation
    queues and the last one wins). Fail-soft: without the helper (a dev
    box) the splash simply stays as it was. The timeout is a floor on how
    long this thread may sit, not a kill: sudo is setuid and its caller
    can't signal it, so after the timeout the wait simply continues (the
    helper's own flock gives up after 600 s)."""
    try:
        done = subprocess.run(  # noqa: S603 — fixed argv, no input
            ["/usr/bin/sudo", "-n", SPLASH_REFRESH],
            capture_output=True,
            text=True,
            timeout=300,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        reason = (exc.stderr or exc.stdout or "refused").strip().splitlines()[-1]
        logger.warning("unit: splash not redrawn: %s", reason)
        return False, reason
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("unit: splash not redrawn (%s)", exc.__class__.__name__)
        return False, f"could not run the splash helper: {exc.__class__.__name__}"
    line = (
        done.stdout.strip().splitlines()[-1]
        if done.stdout.strip()
        else "splash redrawn"
    )
    logger.info("unit: %s", line)
    return True, line


def reboot() -> tuple[bool, str]:
    """Reboot the unit (rotation and the kiosk URL apply on the next login).
    Through logind's polkit action, which the install script grants the
    unit's user — no sudo."""
    try:
        subprocess.run(  # noqa: S603 — fixed argv
            ["/usr/bin/systemctl", "reboot"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        reason = (exc.stderr or "systemctl refused").strip().splitlines()[-1]
        logger.warning("unit: reboot refused: %s", reason)
        return False, reason
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("unit: could not run systemctl: %r", exc)
        return False, f"could not run systemctl: {exc.__class__.__name__}"
    logger.info("unit: rebooting (settings screen)")
    return True, "rebooting"
