#!/usr/bin/env bash
# bird-painter table model — install on the unit itself (Pi 5 + Touch
# Display 2). Run as the unit's user with passwordless sudo. Idempotent:
# re-running updates the checkout and the venv, and rewrites the units.
#
# Slices #120 (kiosk) and #121 (ears backend) of Phase 5. Tier 1 hardening
# (overlayfs, zram, watchdog — #124) is deliberately NOT here: it freezes the
# package set, and this unit is still being iterated on.
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  echo "run this as the unit's user, not root: everything lands in that user's home and session" >&2
  exit 1
fi
# Not $USER: under a bare `su` it still names the previous user.
USER="$(id -un)"

# Every file this script appends to is also hand-edited (the owner adds
# FAL_KEY to .env by hand). An editor that leaves no trailing newline would
# otherwise turn the next append into "FAL_KEY=abc123BP_WALL_MAX_LIVE=3" —
# a silently poisoned key on a unit in someone else's house.
append_line() {  # append_line FILE LINE
  [ -s "$1" ] && [ "$(tail -c1 "$1" | wc -l)" -eq 0 ] && echo >> "$1"
  echo "$2" >> "$1"
}

APP_DIR="$HOME/bird-painter"
REPO="https://github.com/nielsfilmer/bird-painter"
# `|| true`: under pipefail a missing .env (a fresh unit) or one without an
# active BP_PORT= line would otherwise abort the whole script here, silently.
PORT="${BP_PORT:-$( { grep -sE '^BP_PORT=' "$HOME/bird-painter/.env" || true; } | cut -d= -f2)}"
PORT="${PORT:-8537}"
# The Touch Display 2 is natively portrait (720x1280). The table model stands
# in landscape (owner, 2026-09-03), so the output is rotated: 90 = rotate
# right, 270 = rotate left — which one depends on which way the ribbon exits
# the mount. The compositor rotates the touch input with the output. The
# panel layout takes whatever viewport results.
# The table model is read from across a room and places birds exactly as the
# e-paper frame does (#139): the kiosk shows the panel layout. Per-unit
# tuning rides the same URL: BP_CAPTION scales the panel's type through the
# plan (the 7" runs 1.5 — owner, 2026-09-03), BP_UI scales the archive chrome
# (button, overlay heading and close, card lettering — the 7" runs 1.5), and
# BP_WALL_MAX_LIVE caps how many birds share the sheet (the 7" runs 3).
# Remembered in a per-unit file, so a maintenance re-run without them
# re-supplied keeps this unit's tuning instead of reverting the panel to
# defaults (review of #145). Environment overrides win and are written back.
UNIT_CONF="$HOME/.config/bird-painter/unit.conf"
if [ -f "$UNIT_CONF" ]; then
  # shellcheck disable=SC1090
  . "$UNIT_CONF"
fi
CAPTION="${BP_CAPTION:-${CAPTION:-1}}"
UI="${BP_UI:-${UI:-1}}"
MAX_LIVE="${BP_WALL_MAX_LIVE:-${MAX_LIVE:-12}}"
ROTATE="${BP_ROTATE:-${ROTATE:-90}}"
OUTPUT="${BP_OUTPUT:-${OUTPUT:-DSI-2}}"
mkdir -p "$(dirname "$UNIT_CONF")"
# OUTPUT is quoted: the login shell sources this file for the rotation.
printf 'CAPTION=%s\nUI=%s\nMAX_LIVE=%s\nROTATE=%s\nOUTPUT="%s"\n' \
  "$CAPTION" "$UI" "$MAX_LIVE" "$ROTATE" "$OUTPUT" > "$UNIT_CONF"
WALL_URL="http://127.0.0.1:${PORT}/?style=panel&caption=${CAPTION}&ui=${UI}"

log() { printf '\n==> %s\n' "$*"; }

log "system packages"
sudo apt-get update -q
sudo apt-get install -y -q git python3-venv python3-dev libportaudio2 chromium \
  wlr-randr curl plymouth plymouth-themes

log "checkout"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull -q --ff-only
else
  git clone -q "$REPO" "$APP_DIR"
fi

log "venv"
[ -x "$APP_DIR/.venv/bin/python" ] || python3 -m venv "$APP_DIR/.venv"
PIP="$APP_DIR/.venv/bin/pip"
"$PIP" install -q --upgrade pip wheel

# The ears' backend. pyproject pins the full `tensorflow` (422 MB of the
# measured 640 MB RSS on a dev machine — #121). BirdNET is a TFLite model and
# birdnetlib runs on the interpreter-only `tflite-runtime` when present, so
# prefer that and fall back to the full framework only if there is no wheel
# for this Python. Everything else is installed explicitly so tensorflow is
# never pulled in as a side effect.
log "python deps (everything but the ears' backend)"
"$PIP" install -q --no-deps -e "$APP_DIR"
"$PIP" install -q fastapi uvicorn python-dotenv httpx birdnetlib librosa \
  sounddevice pillow numpy websockets scipy
# birdnetlib imports `audioread` at module load but doesn't declare it. On a
# dev machine it arrives transitively from librosa 0.11; Python 3.13 resolves
# librosa 1.0, which dropped that dependency, and the import fails.
"$PIP" install -q audioread
# Python 3.13 removed `audioop` from the standard library (PEP 594); pydub —
# which birdnetlib loads audio through — still imports it and dies with
# "No module named 'audioop'". The PSF-blessed backport restores it.
if "$APP_DIR/.venv/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)'; then
  "$PIP" install -q audioop-lts
fi

log "ears backend"
SITE="$("$APP_DIR/.venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
if "$PIP" install -q tflite-runtime 2>/dev/null; then
  echo "tflite-runtime (interpreter-only)"
elif "$PIP" install -q ai-edge-litert 2>/dev/null; then
  # Python 3.13 has no tflite-runtime wheel; Google's successor package,
  # ai-edge-litert, ships one and exposes the same Interpreter API. birdnetlib
  # only knows to look for `tflite_runtime.interpreter` (then falls back to
  # the full tensorflow), so give it that name: a two-file shim that
  # re-exports LiteRT's interpreter. Same model, same interpreter API, a
  # fraction of the memory — which on a 2 GB unit sharing RAM with Chromium
  # is the difference between headroom and swapping to the SD card (#121).
  mkdir -p "$SITE/tflite_runtime"
  cat > "$SITE/tflite_runtime/__init__.py" <<'SHIM'
"""Shim: `tflite_runtime` has no wheel for this Python; `ai-edge-litert` is
its successor with the same Interpreter API. birdnetlib imports this name.
Installed by bird-painter's table-model setup script, not by pip."""
from ai_edge_litert import __version__  # noqa: F401
SHIM
  cat > "$SITE/tflite_runtime/interpreter.py" <<'SHIM'
from ai_edge_litert.interpreter import *  # noqa: F401,F403
from ai_edge_litert.interpreter import Interpreter  # noqa: F401
SHIM
  echo "ai-edge-litert $("$APP_DIR/.venv/bin/python" -c 'import ai_edge_litert as l; print(l.__version__)') via tflite_runtime shim"
else
  echo "no interpreter-only wheel for $(python3 --version); installing tensorflow"
  "$PIP" install -q tensorflow
fi
# Prove the ears load on this backend before enabling anything.
"$APP_DIR/.venv/bin/python" - <<'PY'
import numpy as np
from bird_painter.ears import Ears
ears = Ears(confidence_floor=0.5)
window = np.random.default_rng(0).standard_normal(48000 * 3).astype("float32") * 0.05
print("ears ok:", len(ears.detect_samples(window, 48000)),
      "detection(s) on seed-0 noise (one spurious is the known result on both backends)")
PY

log "env"
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi
# Pin the USB mic by name substring (the card index moves between boots).
grep -q '^BP_INPUT_DEVICE=' "$APP_DIR/.env" || append_line "$APP_DIR/.env" 'BP_INPUT_DEVICE=USB PnP'
grep -q '^BP_PORT=' "$APP_DIR/.env" || append_line "$APP_DIR/.env" "BP_PORT=${PORT}"
# The cap is a per-unit setting (unit.conf above), so it is always rewritten
# to that remembered value — never reverted to a default by a re-run.
sed -i '/^BP_WALL_MAX_LIVE=/d' "$APP_DIR/.env"
append_line "$APP_DIR/.env" "BP_WALL_MAX_LIVE=${MAX_LIVE}"
# FAL_KEY is NOT set here — it is a secret and never travels through a chat
# transcript or a script. Without it the unit paints placeholder plates, which
# is enough to prove the whole pipeline. Add it to ~/bird-painter/.env by hand.

log "service"
sudo tee /etc/systemd/system/bird-painter.service >/dev/null <<UNIT
[Unit]
Description=bird-painter table model (ears + wall)
After=network-online.target sound.target
Wants=network-online.target

[Service]
User=${USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python -m bird_painter --no-prompt
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable bird-painter
# `enable --now` never restarts a running service, so a re-run would pull
# new code and keep running the old (review of #145). Restart, always.
sudo systemctl restart bird-painter

log "kiosk"
mkdir -p "$HOME/.config/labwc" "$HOME/.local/bin"
# Chromium fullscreen on the panel. Disk cache in RAM: a kiosk polling every
# 5 s writes cache constantly, and that small random write load is what wears
# an SD card (#120). No first-run dialogs, no "restore pages?" after a power
# cut, no translate bar. --password-store=basic, or gnome-keyring parks a
# "choose a password for the new keyring" dialog over the wall on first
# launch (seen on the first unit). --touch-events=enabled declares the panel
# for what it is; on the first unit Chromium still never turned a touch drag
# into a scroll, so the page scrolls its archive itself (index.html) and the
# text-selection symptom was fixed in CSS — the flag is kept as a correct
# declaration, not credited with either fix. Runs in a loop: if Chromium
# dies, the panel gets it back in two seconds rather than a blank desktop
# until someone visits. Started via a wrapper so the autostart line stays
# one line and the flags live in one place.
cat > "$HOME/.local/bin/bird-kiosk" <<KIOSK
#!/usr/bin/env bash
# Each (re)launch waits for the wall to answer before opening the browser on
# it, so the first thing on the panel is birds rather than a connection error
# — also after a crash while the service happens to be down.
while true; do
  for _ in \$(seq 1 60); do
    curl -sf -o /dev/null "http://127.0.0.1:${PORT}/api/live" && break
    sleep 2
  done
  chromium --kiosk --noerrdialogs --disable-infobars --no-first-run \\
    --disable-session-crashed-bubble --disable-features=TranslateUI \\
    --disk-cache-dir=/dev/shm/chromium-cache --disk-cache-size=50000000 \\
    --ozone-platform=wayland --start-fullscreen --password-store=basic \\
    --touch-events=enabled \\
    "${WALL_URL}"
  sleep 2
done
KIOSK
chmod +x "$HOME/.local/bin/bird-kiosk"
AUTOSTART="$HOME/.config/labwc/autostart"
touch "$AUTOSTART"
# Rotation first, kiosk second: Chromium sizes itself to the output it finds.
# Only this script's own rotation line: a hand-added `--mode` line stays.
# The rotation is read from unit.conf at login rather than baked in, so a
# change from the settings screen (#123) takes effect on the next restart
# without re-running this script.
sed -i '/^wlr-randr --output [^ ]* --transform [0-9]*$/d' "$AUTOSTART"
sed -i '/^\. .*unit\.conf"*; wlr-randr --output /d' "$AUTOSTART"
sed -i "\#^$HOME/.local/bin/bird-kiosk &\$#d" "$AUTOSTART"
append_line "$AUTOSTART" ". \"${UNIT_CONF}\"; wlr-randr --output \"\${OUTPUT}\" --transform \"\${ROTATE}\""
append_line "$AUTOSTART" "$HOME/.local/bin/bird-kiosk &"
# Apply to the running session too, if there is one.
if [ -S /run/user/$(id -u)/wayland-0 ]; then
  WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/$(id -u) \
    wlr-randr --output "${OUTPUT}" --transform "${ROTATE}" 2>/dev/null || true
fi

log "cursor: an invisible theme, session-wide"
# There is no unclutter on Wayland. labwc reads XCURSOR_THEME from its
# session environment and exports it to every client, so a theme whose every
# cursor is a fully transparent 1x1 image hides the pointer everywhere — the
# compositor's own and Chromium's. A mouse cursor sat in the panel's corner
# after the first unit's boot (owner: "hide the cursor").
THEME="$HOME/.local/share/icons/invisible"
mkdir -p "$THEME/cursors"
"$APP_DIR/.venv/bin/python" - "$THEME/cursors/default" <<'PY'
# One Xcursor file, written by hand: the format is a header, a table of
# contents with one image entry, and the image — a single transparent pixel.
import struct, sys
path = sys.argv[1]
size, w, h = 24, 1, 1
img = struct.pack("<IIIIIIIII", 36, 0xFFFD0002, size, 1, w, h, 0, 0, 0) + b"\0\0\0\0"
toc = struct.pack("<III", 0xFFFD0002, size, 16 + 12)
hdr = struct.pack("<4sIII", b"Xcur", 16, 0x10000, 1)
with open(path, "wb") as f:
    f.write(hdr + toc + img)
PY
for name in left_ptr arrow pointer hand hand1 hand2 text xterm crosshair \
            wait watch progress grabbing move sb_h_double_arrow \
            sb_v_double_arrow col-resize row-resize ns-resize ew-resize \
            nwse-resize nesw-resize not-allowed help question_arrow; do
  ln -sf default "$THEME/cursors/$name"
done
printf '[Icon Theme]\nName=invisible\nComment=No cursor: the table model is a picture, not a desktop\n' > "$THEME/index.theme"
ENVFILE="$HOME/.config/labwc/environment"
touch "$ENVFILE"
sed -i '/^XCURSOR_THEME=/d' "$ENVFILE"
append_line "$ENVFILE" "XCURSOR_THEME=invisible"

log "unit: the settings screen's permissions"
# The service (no login session) drives NetworkManager and reboots through
# polkit; without a rule both answer "auth" that nothing can grant. The rule
# is scoped to this user only and lives with the repo (scripts/polkit/).
sed "s/__USER__/${USER}/g" "$APP_DIR/scripts/polkit/50-birdframe-unit.rules" \
  | sudo tee /etc/polkit-1/rules.d/50-birdframe-unit.rules >/dev/null

log "console: autologin to the desktop, no screen blanking"
if command -v raspi-config >/dev/null; then
  sudo raspi-config nonint do_boot_behaviour B4 || true   # desktop, autologin
  sudo raspi-config nonint do_blanking 1 || true          # 1 = disable
fi

log "boot: no Pi chrome from power-on to the wall"
# From power-on to the wall the unit used to show: the rainbow splash, the
# Pi's own plymouth theme, the greeter's wallpaper, then the desktop with
# its panel and icons until Chromium came up. Each is replaced with the
# wall's paper so the panel reads as one object waking up (owner: "boot
# without showing any Pi interfaces"). This step runs LAST, after autologin
# and blanking are set: it is cosmetic, and under `set -e` a failure here
# must not leave a fresh unit without its autologin. A wrong theme or
# wallpaper never stops a boot; the kernel line is not touched; the two
# system files edited are copied aside once (`.bp-orig`) before the first
# edit.
# 5 (first, because it needs no splash). No taskbar: the kiosk covers it,
#    but it flashes before Chromium and peeks out if Chromium ever
#    restarts. Pi OS starts it under `lwrespawn` (/etc/xdg/labwc/autostart),
#    which brings it straight back — so the respawner goes first, then the
#    panel, retried for a while because on a cold boot neither may exist
#    yet when our autostart runs. The [-] keeps the pattern from matching
#    the shell that carries it; single quotes keep $(seq …) for that shell.
sed -i '/pkill -x wf-panel-pi/d' "$AUTOSTART"
append_line "$AUTOSTART" 'for _ in $(seq 1 30); do pkill -f "lwrespawn /usr/bin/wf-panel[-]pi"; pkill -x wf-panel-pi && break; sleep 1; done &'
backup_once() {  # backup_once FILE — a root-owned copy aside, the first time only
  [ -f "$1.bp-orig" ] || sudo cp "$1" "$1.bp-orig"
}
sudo_append_line() {  # append_line, for a root-owned file
  if sudo test -s "$1" && [ "$(sudo tail -c1 "$1" | wc -l)" -eq 0 ]; then
    echo | sudo tee -a "$1" >/dev/null
  fi
  echo "$2" | sudo tee -a "$1" >/dev/null
}
SPLASH_DIR=/usr/local/share/bird-painter   # root-owned, world-readable: the greeter runs as lightdm
SPLASH_TMP="$(mktemp -d)"
SPLASH_NOTE="splash, greeter wallpaper and desktop take effect on the next boot"
if "$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/make_splash.py" "$SPLASH_TMP" \
     "$APP_DIR/tests/fixtures/plates/good-hummingbird.jpg" "$ROTATE"; then
  sudo mkdir -p "$SPLASH_DIR"
  sudo cp "$SPLASH_TMP/splash-landscape.png" "$SPLASH_TMP/splash-native.png" "$SPLASH_DIR/"
else
  SPLASH_NOTE="the splash could NOT be generated; the boot chrome stays as it was"
  echo "boot: $SPLASH_NOTE"
fi
rm -rf "$SPLASH_TMP"
if [ -f "$SPLASH_DIR/splash-native.png" ]; then
  # 1. The rainbow square at power-on. Appended at the end of config.txt,
  #    which Pi OS ends with an [all] section; if a hand-added filter
  #    section comes last, the line lands under it — put it where you want.
  if [ -f /boot/firmware/config.txt ]; then
    backup_once /boot/firmware/config.txt
    if ! grep -q '^disable_splash=1' /boot/firmware/config.txt; then
      sudo_append_line /boot/firmware/config.txt 'disable_splash=1'
    fi
  else
    echo "boot: no /boot/firmware/config.txt — not a Pi OS boot partition; rainbow left alone"
  fi
  # 2. plymouth: the same shape as the Pi's own "pix" theme (one image,
  #    nothing else), with the wall's paper. The image is pre-rotated to the
  #    panel's native orientation — plymouth paints before any compositor.
  #    A changed image or script needs the initramfs rebuilt (-R) as much
  #    as a changed theme does, so the check is on all three files, not on
  #    the theme name alone.
  THEME_DIR=/usr/share/plymouth/themes/birdpainter
  if [ "$(sudo /usr/sbin/plymouth-set-default-theme)" != "birdpainter" ] \
     || ! sudo cmp -s "$SPLASH_DIR/splash-native.png" "$THEME_DIR/splash.png" \
     || ! sudo cmp -s "$APP_DIR/scripts/plymouth/birdpainter.script" "$THEME_DIR/birdpainter.script" \
     || ! sudo cmp -s "$APP_DIR/scripts/plymouth/birdpainter.plymouth" "$THEME_DIR/birdpainter.plymouth"; then
    sudo mkdir -p "$THEME_DIR"
    sudo cp "$APP_DIR/scripts/plymouth/birdpainter.plymouth" \
            "$APP_DIR/scripts/plymouth/birdpainter.script" "$THEME_DIR/"
    sudo cp "$SPLASH_DIR/splash-native.png" "$THEME_DIR/splash.png"
    sudo /usr/sbin/plymouth-set-default-theme -R birdpainter \
      || echo "boot: plymouth theme not set (the boot is unaffected)"
  fi
  # 3. The greeter's wallpaper, for the moment before autologin.
  if [ -f /etc/lightdm/pi-greeter.conf ]; then
    backup_once /etc/lightdm/pi-greeter.conf
    for kv in "wallpaper=${SPLASH_DIR}/splash-landscape.png" "wallpaper_mode=fit"; do
      key="${kv%%=*}"
      if grep -qs "^${key}=" /etc/lightdm/pi-greeter.conf; then
        sudo sed -i "s#^${key}=.*#${kv}#" /etc/lightdm/pi-greeter.conf
      else
        sudo_append_line /etc/lightdm/pi-greeter.conf "$kv"
      fi
    done
  fi
  # 4. The desktop behind Chromium: the same paper, no icons. This file is
  #    the kiosk's, so it is written whole — a hand edit does not survive.
  mkdir -p "$HOME/.config/pcmanfm/LXDE-pi"
  cat > "$HOME/.config/pcmanfm/LXDE-pi/desktop-items-0.conf" <<DESK
[*]
wallpaper_mode=fit
wallpaper_common=1
wallpaper=${SPLASH_DIR}/splash-landscape.png
desktop_bg=#ece1c6
desktop_fg=#4a3f2e
desktop_shadow=#ece1c6
show_wm_menu=0
show_documents=0
show_trash=0
show_mounts=0
DESK
fi

log "done"
echo "unit:    caption ${CAPTION}, ui ${UI}, birds ${MAX_LIVE}, rotate ${ROTATE} on ${OUTPUT} (remembered in ${UNIT_CONF})"
echo "service: $(systemctl is-active bird-painter) (restarted)"
echo "kiosk:   the URL and flags take effect on the next login — reboot (a relaunched Chromium keeps the flags its loop started with)"
echo "boot:    ${SPLASH_NOTE}"
echo "         the splash's rotation on the panel is an assumption (see scripts/make_splash.py) — watch one boot; if it is upside down, say so"
echo "cursor/rotation/autologin/blanking: session settings — take effect on the next login (reboot)"
echo "wall:    http://$(hostname -I | awk '{print $1}'):${PORT}/?style=panel"
echo "backend: $("$APP_DIR/.venv/bin/python" -c 'import importlib.util as u; print("tflite-runtime" if u.find_spec("tflite_runtime") else ("tensorflow" if u.find_spec("tensorflow") else "NONE"))')"
