"""The unit's own settings and network — what the table model's settings
screen reads and writes (#123).

Two files hold a unit's settings, and this module is the service's one
reader and writer of both:

- `~/.config/bird-painter/unit.conf` — the install script's KEY=VALUE file
  (CAPTION, UI, MAX_LIVE, ROTATE, OUTPUT): what makes this unit the 7" or
  the 10". The screen edits it in place; a re-run of the script keeps it.
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
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .night import NightSchedule

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

# Every knob the screen may write, with its bounds and where it lives.
# unit.conf keys are bare; .env keys carry the BP_ prefix.
KNOBS: dict[str, tuple[float, float, str]] = {
    "CAPTION": (0.5, 2.0, "unit"),
    "UI": (0.5, 2.0, "unit"),
    "MAX_LIVE": (1, 12, "unit"),
    "ROTATE": (0, 270, "unit"),
    "NIGHT_ENABLED": (0, 1, "env"),
    "NIGHT_FROM": (0, 23, "env"),
    "NIGHT_TO": (0, 23, "env"),
    "NIGHT_BRIGHTNESS": (1, 100, "env"),
}
ENV_NAMES = {
    "MAX_LIVE": "BP_WALL_MAX_LIVE",
    "NIGHT_ENABLED": "BP_NIGHT_ENABLED",
    "NIGHT_FROM": "BP_NIGHT_FROM",
    "NIGHT_TO": "BP_NIGHT_TO",
    "NIGHT_BRIGHTNESS": "BP_NIGHT_BRIGHTNESS",
}


@dataclass
class LiveSettings:
    """The few config values the screen changes at runtime, kept apart from
    the frozen Config so the routes that read them see the new value at
    once. Built from the config at startup."""

    wall_max_live: int
    caption: float = 1.0
    ui: float = 1.0
    rotate: int = 90
    night: NightSchedule = field(default_factory=NightSchedule)

    @classmethod
    def from_config(cls, config, conf_path: Path | None = None) -> LiveSettings:
        unit = read_conf(conf_path or UNIT_CONF)
        return cls(
            wall_max_live=config.wall_max_live,
            caption=_bounded("CAPTION", unit.get("CAPTION"), 1.0),
            ui=_bounded("UI", unit.get("UI"), 1.0),
            rotate=int(_bounded("ROTATE", unit.get("ROTATE"), 90)),
            night=NightSchedule.from_config(config),
        )

    def as_json(self) -> dict:
        n = self.night
        return {
            "CAPTION": self.caption,
            "UI": self.ui,
            "MAX_LIVE": self.wall_max_live,
            "ROTATE": self.rotate,
            "NIGHT_ENABLED": 1 if n.enabled else 0,
            "NIGHT_FROM": n.start_hour,
            "NIGHT_TO": n.end_hour,
            "NIGHT_BRIGHTNESS": n.night_percent,
        }


def _bounded(key: str, raw, default: float) -> float:
    lo, hi, _ = KNOBS[key]
    try:
        value = float(raw) if raw is not None else default
    except ValueError:
        value = default
    return min(hi, max(lo, value))


# ---- the files -------------------------------------------------------------


def read_conf(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; comments and blanks ignored; last value wins."""
    values: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def write_conf(updates: dict[str, str], path: Path) -> dict[str, str]:
    """Merge `updates` into the file, keeping every other line as it was
    (comments, order, a FAL_KEY), and write it atomically. A key that appears
    twice collapses to its first line. Returns the merged values."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = path.read_text().splitlines()
    except OSError:
        lines = []
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
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(out) + "\n")
    os.replace(tmp, path)
    return read_conf(path)


def clean_updates(payload: dict) -> dict[str, float]:
    """Validate a settings write from the page: known keys only, numeric,
    clamped. Anything else is dropped, not errored — the screen only ever
    sends what it shows, so an unknown key is a bug there, not a request."""
    updates: dict[str, float] = {}
    for key in KNOBS:
        if key not in payload:
            continue
        try:
            value = float(payload[key])
        except (TypeError, ValueError):
            continue
        updates[key] = _bounded(key, value, value)
    return updates


def _fmt(key: str, value: float) -> str:
    if key == "NIGHT_ENABLED":
        return "true" if value else "false"
    if key in ("MAX_LIVE", "ROTATE", "NIGHT_FROM", "NIGHT_TO", "NIGHT_BRIGHTNESS"):
        return str(int(value))
    return f"{value:g}"


def apply(
    updates: dict[str, float],
    live: LiveSettings,
    *,
    conf_path: Path | None = None,
    env_path: Path | None = None,
) -> LiveSettings:
    """Write the changed knobs to their files and to the running settings.
    The caller reschedules the night watch from `live.night`."""
    conf_path = conf_path or UNIT_CONF
    env_path = env_path or ENV_FILE
    unit_writes = {k: _fmt(k, v) for k, v in updates.items() if KNOBS[k][2] == "unit"}
    env_writes = {
        ENV_NAMES[k]: _fmt(k, v) for k, v in updates.items() if k in ENV_NAMES
    }
    if unit_writes:
        write_conf(unit_writes, conf_path)
    if env_writes:
        write_conf(env_writes, env_path)
    if "CAPTION" in updates:
        live.caption = updates["CAPTION"]
    if "UI" in updates:
        live.ui = updates["UI"]
    if "MAX_LIVE" in updates:
        live.wall_max_live = int(updates["MAX_LIVE"])
    if "ROTATE" in updates:
        live.rotate = int(updates["ROTATE"])
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
    # and an SSID from the touchscreen is just one argument.
    return subprocess.run(  # noqa: S603
        [NMCLI, "-t", "--escape", "no", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    ).stdout


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
        for line in listing.splitlines():
            parts = line.split(":")
            if len(parts) < 4 or not parts[1]:
                continue
            active = parts[0] == "yes"
            net = Network(
                ssid=parts[1],
                signal=int(parts[2] or 0),
                secured=parts[3].strip() not in ("", "--"),
                active=active,
            )
            networks.append(net)
            if active:
                ssid = net.ssid
        for line in _nmcli("-f", "IP4.ADDRESS", "device", "show", "wlan0").splitlines():
            if line.startswith("IP4.ADDRESS"):
                ip = line.partition(":")[2].split("/")[0]
                break
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    # One row per SSID — the strongest, the active one on top.
    best: dict[str, Network] = {}
    for net in networks:
        if net.ssid not in best or net.signal > best[net.ssid].signal or net.active:
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
        return False, (exc.stderr or "systemctl refused").strip().splitlines()[-1]
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not run systemctl: {exc.__class__.__name__}"
    return True, "rebooting"
