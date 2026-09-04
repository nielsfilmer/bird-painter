#!/usr/bin/env bash
# bird-splash-refresh — (re)draw the table model's boot splash for the
# unit's CURRENT stand and put it where the boot reads it. Runs as root,
# installed by scripts/setup-table-model.sh to /usr/local/sbin with a
# sudoers line that lets the unit's user run exactly this command, with no
# arguments — so the settings screen's "rotate" can call it and the next
# boot's splash is upright, without the installer being run again.
#
# It reads everything it needs itself: the invoking user's unit.conf
# (ROTATE, OUTPUT), the panel's native mode from the DSI connector, and
# draws with the repo's venv AS THAT USER. Only the copies into the system
# directories and the initramfs rebuild happen as root.
#
# Prints what it did; exits non-zero only when nothing usable was drawn.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "bird-splash-refresh: run as root (the installer's sudoers line covers it)" >&2
  exit 1
fi
unit_user="${SUDO_USER:-}"
if [ -z "$unit_user" ] || [ "$unit_user" = "root" ]; then
  echo "bird-splash-refresh: no invoking user (must be run via sudo by the unit's user)" >&2
  exit 1
fi
home="$(getent passwd "$unit_user" | cut -d: -f6)"
app_dir="$home/bird-painter"
unit_conf="$home/.config/bird-painter/unit.conf"
splash_dir=/usr/local/share/bird-painter   # root-owned, world-readable: the greeter runs as lightdm
theme_dir=/usr/share/plymouth/themes/birdpainter

ROTATE=90; OUTPUT=DSI-2
if [ -f "$unit_conf" ]; then
  # KEY=VALUE, written by the installer and by the settings screen; values
  # are clamped numbers and a quoted connector name.
  ROTATE="$(sed -n 's/^ROTATE=//p' "$unit_conf" | tail -1 | tr -d '"' )"
  OUTPUT="$(sed -n 's/^OUTPUT=//p' "$unit_conf" | tail -1 | tr -d '"' )"
  ROTATE="${ROTATE:-90}"; OUTPUT="${OUTPUT:-DSI-2}"
fi
case "$ROTATE" in 0|90|180|270) ;; *) ROTATE=90 ;; esac

# The panel's own mode (720x1280 on the 7", 1200x1920 on the 10.1"). The
# glob may not match (panel asleep, connector renamed): the `|| true`
# keeps errexit out of it and the 7" size is the fallback.
native="$( { cat /sys/class/drm/card*-"${OUTPUT}"/modes 2>/dev/null || true; } | head -1)"
native="${native:-720x1280}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
chown "$unit_user" "$tmp"
if ! runuser -u "$unit_user" -- "$app_dir/.venv/bin/python" "$app_dir/scripts/make_splash.py" \
     "$tmp" "$app_dir/tests/fixtures/plates/good-hummingbird.jpg" "$ROTATE" "$native" >/dev/null; then
  echo "bird-splash-refresh: the splash could not be drawn (rotate $ROTATE, $native); nothing changed" >&2
  exit 1
fi

install -d -m 0755 "$splash_dir"
install -m 0644 "$tmp/splash-desktop.png" "$tmp/splash-native.png" "$splash_dir/"
rm -f "$splash_dir/splash-landscape.png"   # the name before #159

# plymouth: the same shape as the Pi's own "pix" theme (one image, nothing
# else). The initramfs is rebuilt only when what it would carry changed —
# the native image, the script, or the theme itself.
install -d -m 0755 "$theme_dir"
changed=0
for f in birdpainter.plymouth birdpainter.script; do
  if ! cmp -s "$app_dir/scripts/plymouth/$f" "$theme_dir/$f"; then
    install -m 0644 "$app_dir/scripts/plymouth/$f" "$theme_dir/$f"; changed=1
  fi
done
if ! cmp -s "$splash_dir/splash-native.png" "$theme_dir/splash.png"; then
  install -m 0644 "$splash_dir/splash-native.png" "$theme_dir/splash.png"; changed=1
fi
if [ "$(/usr/sbin/plymouth-set-default-theme)" != "birdpainter" ] || [ "$changed" -eq 1 ]; then
  if /usr/sbin/plymouth-set-default-theme -R birdpainter >/dev/null 2>&1; then
    echo "bird-splash-refresh: rotate $ROTATE, $native — splash drawn, initramfs rebuilt"
  else
    echo "bird-splash-refresh: rotate $ROTATE, $native — splash drawn; plymouth theme NOT set (boot unaffected)" >&2
  fi
else
  echo "bird-splash-refresh: rotate $ROTATE, $native — splash unchanged"
fi

# The running desktop shows the new picture at once (best effort: a
# session may not exist, e.g. during the install over ssh).
uid="$(id -u "$unit_user")"
if [ -S "/run/user/$uid/wayland-0" ]; then
  runuser -u "$unit_user" -- env WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR="/run/user/$uid" \
    timeout 10 pcmanfm --set-wallpaper="$splash_dir/splash-desktop.png" --wallpaper-mode=fit \
    >/dev/null 2>&1 || true
fi
