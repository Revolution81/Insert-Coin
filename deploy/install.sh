#!/bin/sh
# Installs Insert Coin on the Cardputer Zero (M5Stack's image, Debian 13), as root.
#
#   sudo sh deploy/install.sh --app        SPENDING edition: an app in M5Stack's
#                                          launcher (default when the launcher
#                                          is there). Small amounts only.
#   sudo sh deploy/install.sh --dedicated  SAVINGS edition: the device boots
#                                          straight into the wallet and runs
#                                          nothing else
#   ... --image                            building a disk image (in a chroot):
#                                          skip steps that need a running system
#   ... --full                             redo every step, even ones an earlier
#                                          run already finished
#   ... --nfc-unit                         a Unit NFC (U216) wired to EXT pins 1
#                                          (SCL) and 3 (SDA): give it its own I2C
#                                          bus, /dev/i2c-3 (needs a reboot). Not
#                                          with the NFC Cap fitted: it uses the
#                                          same header.
#
# Running it again is how you update. Steps that are already done (system
# packages, Python packages with the same lock file, interface setup) are
# skipped, so an update mostly just copies the new code.
#
# deploy/uninstall.sh undoes either.
set -eu
# Keep going if the SSH connection drops halfway (Wi-Fi blips, the wallet
# turning radios off): a half-finished install is worse than a slow one.
trap '' HUP

SRC="$(cd "$(dirname "$0")/.." && pwd)"
MODE=""
IMAGE=0
FULL=0
NFC_UNIT=0
for a in "$@"; do
  case "$a" in
    --app) MODE=app ;;
    --dedicated) MODE=dedicated ;;
    --image) IMAGE=1 ;;
    --full) FULL=1 ;;
    --nfc-unit) NFC_UNIT=1 ;;
    *) echo "usage: install.sh [--app|--dedicated] [--image] [--full] [--nfc-unit]" >&2; exit 2 ;;
  esac
done
if [ -z "$MODE" ]; then
  if [ -d /usr/share/APPLaunch ]; then MODE=app; else MODE=dedicated; fi
fi
if [ "$MODE" = app ]; then
  echo "== Insert Coin, SPENDING edition (app in M5Stack's launcher)"
  echo "   Other apps on this device could reach the wallet while it's open."
  echo "   Keep only spending money on it. For savings, use the wallet image"
  echo "   or: sudo sh deploy/install.sh --dedicated"
else
  echo "== Insert Coin, SAVINGS edition (dedicated: boots into the wallet only)"
fi
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$SRC/core/version.py")"
VENV=/opt/czwallet-venv
RELEASE=/opt/czwallet-releases/$VERSION
LINK=/opt/czwallet          # -> the active release; updates switch this link

DONE=/etc/czwallet/install-done     # marks steps an earlier run finished
mkdir -p "$DONE"
[ "$FULL" = 1 ] && rm -f "$DONE"/*

NEED="python3-venv python3-pip python3-setuptools python3-wheel python3-evdev tor rfkill network-manager sudo alsa-utils i2c-tools"
have_all() { for p in $NEED; do dpkg -s "$p" >/dev/null 2>&1 || return 1; done; }
if [ -f "$DONE/packages" ] && have_all; then
echo "== packages: already installed (--full to check again)"
else
echo "== packages"
apt-get update
# python3-evdev, picamera2 and the build tools come from apt (signed), not PyPI
apt-get install -y $NEED
# Names differ between Raspberry Pi OS and plain Debian 13 (trixie's 64-bit
# time renames, e.g. libzbar0t64); these are needed only for add-ons.
apt-get install -y libzbar0t64 2>/dev/null || apt-get install -y libzbar0 2>/dev/null \
    || echo "!! zbar not installed: QR scanning won't work"
apt-get install -y python3-picamera2 2>/dev/null || echo "!! picamera2 not installed: no camera"
apt-get install -y python3-spidev 2>/dev/null || echo "!! spidev not installed: no NFC Cap"
# Optional: native secp256k1 makes signing much faster than embit's pure-Python fallback
apt-get install -y libsecp256k1-1 2>/dev/null || apt-get install -y libsecp256k1-2 2>/dev/null || true
touch "$DONE/packages"
fi

echo "== users"
id czsigner >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin czsigner
id czapp    >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin czapp
id cznet    >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin cznet
id czscan   >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin czscan
id czshare  >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin czshare
id czprint  >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin czprint
# czprint reads paper wallets (group czshare owns that folder); the app talks
# to it through its socket (group czprint) but can't read the folder itself
usermod -aG czshare czprint
getent group czscreen >/dev/null || groupadd --system czscreen
getent group spi >/dev/null || groupadd --system spi
getent group gpio >/dev/null || groupadd --system gpio
usermod -aG czsigner,cznet,czscan,czprint,spi,gpio czapp
usermod -aG video czscan
# the app used to read the keyboard and the camera itself; now only the
# signer reads keys and only czscan opens the camera
gpasswd -d czapp input 2>/dev/null || true
gpasswd -d czapp video 2>/dev/null || true
usermod -aG input,czscreen czsigner
# app mode: the screen keeps its usual group (video) so the launcher can
# still draw when the wallet isn't open
if [ "$MODE" = app ]; then
  usermod -aG video czsigner
  # and whichever group M5Stack's image lets the launcher set the backlight
  # with, so SCREEN SLEEP works inside a session too
  BLG="$(stat -c %G /sys/class/backlight/backlight/brightness 2>/dev/null || echo root)"
  if [ "$BLG" != root ] && getent group "$BLG" >/dev/null; then usermod -aG "$BLG" czsigner; fi
fi

# Never swap the code under a running wallet: close the session first, the
# same way EXIT TO MENU does (the signer forgets the seed, Wi-Fi and the
# backlight go back as they were).
if [ "$IMAGE" = 0 ] && systemctl is-active -q czwallet 2>/dev/null; then
  echo "== Insert Coin is open: closing it first"
  systemctl stop czwallet 2>/dev/null || true
  if [ -e /run/cz-session.lock ]; then
    flock -w 20 /run/cz-session.lock true 2>/dev/null || true   # wait for the session to tidy up
  fi
  systemctl stop czshare czprint czscan cznet czsigner 2>/dev/null || true
fi

echo "== code ($VERSION)"
rm -rf "$RELEASE"
mkdir -p "$RELEASE"
cp -r "$SRC/app.py" "$SRC/requirements.txt" "$SRC/requirements.lock" "$SRC/core" "$SRC/ui" "$SRC/tools" "$SRC/deploy" "$RELEASE/"
[ -f "$SRC/SECURITY.md" ] && cp "$SRC/SECURITY.md" "$RELEASE/"
find "$RELEASE" -name __pycache__ -prune -exec rm -rf {} +
# Compiled once here (the services may not write next to the code): without
# it every screen's code was compiled again each time it first opened.
python3 -m compileall -q "$RELEASE" >/dev/null 2>&1 || true
if [ -d "$LINK" ] && [ ! -L "$LINK" ]; then mv "$LINK" "$LINK.old-layout"; fi
ln -sfn "$RELEASE" "$LINK"
# --system-site-packages: evdev, picamera2 and setuptools come from apt.
[ -x "$VENV/bin/python" ] || python3 -m venv --system-site-packages "$VENV"
# Every PyPI file must match a SHA-256 in requirements.lock or pip refuses
# it. Wheels only (no install-time code), except embit, which is pure
# Python source built with apt's setuptools (no build tools downloaded).
LOCKSUM="$(sha256sum "$SRC/requirements.lock" | cut -d' ' -f1)"
# the mark lives in the venv itself, so a fresh venv always gets its packages
if [ "$(cat "$VENV/.insert-coin-lock" 2>/dev/null)" = "$LOCKSUM" ]; then
  echo "== python packages: unchanged since last install"
else
  "$VENV/bin/pip" install --require-hashes --no-deps --only-binary :all: \
      --no-binary embit --no-build-isolation -r "$SRC/requirements.lock"
  echo "$LOCKSUM" > "$VENV/.insert-coin-lock"
fi
chown -R root:root /opt/czwallet-releases "$VENV"
chmod -R go-w /opt/czwallet-releases "$VENV"

echo "== data"
install -d -o root -g root -m 755 /var/lib/czwallet
install -d -o czsigner -g czsigner -m 700 /var/lib/czwallet/signer
install -d -o czapp -g czapp -m 700 /var/lib/czwallet/app
# paper wallets (private keys): root and the SHARE FILES service only, never the app
install -d -o root -g czshare -m 2770 /var/lib/czwallet/paper
install -d -m 755 /etc/czwallet
echo "CZ_APP_UID=$(id -u czapp)" > /etc/czwallet/signer.env
if [ -f "$SRC/deploy/release_keys.txt" ]; then
  # the project's update-signing keys, public and in the repository
  install -m 644 "$SRC/deploy/release_keys.txt" /etc/czwallet/release_keys.txt
elif [ ! -f /etc/czwallet/release_keys.txt ]; then
  printf '%s\n' "# Ed25519 public keys (hex), one per line, trusted to sign updates." \
                 "# Get yours from: python3 tools/release.py keygen" > /etc/czwallet/release_keys.txt
fi
chmod 644 /etc/czwallet/release_keys.txt

echo "== root helpers (radios, updates, card copies, file sharing)"
install -o root -g root -m 755 "$SRC/deploy/cz-radio" /usr/local/sbin/cz-radio
install -o root -g root -m 755 "$SRC/deploy/cz-power" /usr/local/sbin/cz-power
install -o root -g root -m 755 "$SRC/deploy/cz-light" /usr/local/sbin/cz-light
install -o root -g root -m 755 "$SRC/deploy/cz-update" /usr/local/sbin/cz-update
install -o root -g root -m 755 "$SRC/deploy/cz-backup" /usr/local/sbin/cz-backup
install -o root -g root -m 755 "$SRC/deploy/cz-share" /usr/local/sbin/cz-share
{
  echo "czapp ALL=(root) NOPASSWD: /usr/local/sbin/cz-radio vault, /usr/local/sbin/cz-radio online"
  echo "czapp ALL=(root) NOPASSWD: /usr/local/sbin/cz-update *"
  echo "czapp ALL=(root) NOPASSWD: /usr/local/sbin/cz-backup *"
  echo "czapp ALL=(root) NOPASSWD: /usr/local/sbin/cz-share start, /usr/local/sbin/cz-share stop"
} > /etc/sudoers.d/czwallet
visudo -cf /etc/sudoers.d/czwallet
chmod 440 /etc/sudoers.d/czwallet
install -m 644 "$SRC/deploy/50-czwallet-nm.rules" /etc/polkit-1/rules.d/
# a fixed name for the built-in keyboard (M5's image has one; ours may not)
install -m 644 "$SRC/deploy/62-czwallet-keyboard.rules" /etc/udev/rules.d/
install -m 644 "$SRC/deploy/63-czwallet-nfc-unit.rules" /etc/udev/rules.d/
if [ "$MODE" = dedicated ]; then
  # only the signer may draw on the panel or set the backlight
  install -m 644 "$SRC/deploy/60-czwallet-screen.rules" /etc/udev/rules.d/
  install -m 644 "$SRC/deploy/61-czwallet-backlight.rules" /etc/udev/rules.d/
  if [ "$IMAGE" = 0 ]; then
    udevadm control --reload-rules && udevadm trigger --subsystem-match=graphics || true
  fi
fi

# Interfaces for add-ons (SPI: NFC Cap; I2C: secure chip; UART: fingerprint).
# Only when building an image, where raspi-config just edits files. On a
# running Cardputer Zero the screen and keyboard already sit on SPI and I2C,
# and raspi-config switching them on again live can freeze the screen; there
# they're already on, and the add-on steps in docs/HARDWARE_TEST.md say what
# (if anything) to change, followed by a reboot.
if [ "$IMAGE" = 1 ]; then
  echo "== interfaces for add-ons"
  raspi-config nonint do_spi 0 || true
  raspi-config nonint do_i2c 0 || true
  raspi-config nonint do_serial_hw 0 || true
  raspi-config nonint do_serial_cons 1 || true
  # M5Stack's OS ships with China time; the wallet shows no clock, and logs
  # and file names are clearer in UTC
  ln -sf /usr/share/zoneinfo/UTC /etc/localtime && echo UTC > /etc/timezone
else
  echo "== interfaces for add-ons: left as they are (no live changes)"
fi
getent group i2c >/dev/null && usermod -aG i2c czsigner

# Unit NFC: a bus of its own (software I2C on GPIO23 = SDA, GPIO26 = SCL,
# with the Pi's pull-ups, slowed for them). Never I2C1, which the keyboard
# is on: the app may open this bus, and must not reach the keyboard.
if [ "$NFC_UNIT" = 1 ]; then
  CFG=/boot/firmware/config.txt
  if ! grep -q "^dtoverlay=i2c-gpio,bus=3" "$CFG"; then
    printf '\n# Insert Coin: Unit NFC on its own I2C bus (EXT pin 3 SDA, pin 1 SCL)\ngpio=23,26=pu\ndtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=23,i2c_gpio_scl=26,i2c_gpio_delay_us=10\n' >> "$CFG"
    echo "== Unit NFC: /dev/i2c-3 set up; reboot to use it"
  else
    echo "== Unit NFC: /dev/i2c-3 already set up"
  fi
fi

echo "== services"
install -m 644 "$SRC"/deploy/*.service /etc/systemd/system/
mkdir -p /etc/systemd/system/czwallet.service.d
rm -f /etc/systemd/system/czwallet.service.d/appmode.conf \
      /etc/systemd/system/czwallet.service.d/savings.conf
systemctl daemon-reload || true

if [ "$MODE" = app ]; then
  # Nothing starts at boot: the launcher's Insert Coin icon runs a session
  install -m 644 "$SRC/deploy/czwallet-appmode.conf" /etc/systemd/system/czwallet.service.d/appmode.conf
  systemctl daemon-reload || true
  systemctl disable tor czwallet-vault czsigner cznet czscan czprint czwallet czwallet-ioe-ready czwallet-console-off czwallet-power czwallet-goodbye 2>/dev/null || true
  rm -f /usr/lib/systemd/system-shutdown/czwallet-done
  install -o root -g root -m 755 "$SRC/deploy/cz-session" /usr/local/sbin/cz-session
  install -o root -g root -m 755 "$SRC/deploy/insert-coin" /usr/local/bin/insert-coin
  install -d /usr/share/APPLaunch/applications /usr/share/APPLaunch/share/images
  install -m 644 "$SRC/deploy/insert-coin.desktop" /usr/share/APPLaunch/applications/insert-coin.desktop
  install -m 644 "$SRC/deploy/insert_coin_80.png" /usr/share/APPLaunch/share/images/insert_coin_80.png
  # the launcher's user (UID 1000) may start a session, and nothing else
  LUSER="$(getent passwd 1000 | cut -d: -f1 || true)"
  if [ -n "$LUSER" ]; then
    echo "$LUSER ALL=(root) NOPASSWD: /usr/local/sbin/cz-session" > /etc/sudoers.d/czwallet-app
    visudo -cf /etc/sudoers.d/czwallet-app
    chmod 440 /etc/sudoers.d/czwallet-app
  else
    echo "!! no UID 1000 user yet: run this again after the first-boot setup"
  fi
  echo "Done. Insert Coin Spending is now an app on the CardputerZero home screen."
  echo "Spending money only: keep savings on the wallet image or a dedicated install."
  echo "To remove it: sudo sh deploy/uninstall.sh"
  exit 0
fi

# Dedicated (the savings edition): boot straight into the wallet
install -m 644 "$SRC/deploy/czwallet-savings.conf" /etc/systemd/system/czwallet.service.d/savings.conf
systemctl daemon-reload || true
systemctl enable tor czwallet-vault czsigner cznet czscan czprint czwallet
# opt-in boot log for when the device won't start (see the unit's comments)
systemctl enable czwallet-bootlog 2>/dev/null || true
# M5's launcher (off here) normally ends the keyboard lights' boot walk and
# owns the screen; these two do just those parts
systemctl enable czwallet-ioe-ready czwallet-console-off czwallet-power
# switching off is M5Stack's powerfail-suo driver's job (card read-only in
# 0.5 s); the shutdown screens of some 0.8.24 test builds are gone
systemctl disable czwallet-goodbye 2>/dev/null || true
rm -f /etc/systemd/system/czwallet-goodbye.service /usr/lib/systemd/system-shutdown/czwallet-done
# Shutting down from the power switch has 10 s before the power is cut:
# no service gets the default 90 s to stop (the card is synced first anyway)
mkdir -p /etc/systemd/system.conf.d
printf '[Manager]\nDefaultTimeoutStopSec=4s\n' > /etc/systemd/system.conf.d/czwallet-shutdown.conf
# Keep the login console off the wallet screen (the signer owns it)
systemctl disable getty@tty1 2>/dev/null || true
# M5Stack's launcher (a user service) and its desktop audio would fight the
# wallet for the screen, keyboard and speaker: mask them for every user.
systemctl get-default > /etc/czwallet/previous-target 2>/dev/null || true
systemctl mask APPLaunch.service 2>/dev/null || true            # system service on newer images
systemctl --global mask APPLaunch.service 2>/dev/null || true   # user service on older ones
systemctl --global mask pipewire.socket pipewire.service pipewire-pulse.socket \
    pipewire-pulse.service wireplumber.service 2>/dev/null || true
systemctl disable lightdm 2>/dev/null || true
systemctl set-default multi-user.target
rm -f /usr/share/APPLaunch/applications/insert-coin.desktop /etc/sudoers.d/czwallet-app

echo "Done. Reboot to start Insert Coin (savings edition): sudo reboot"
echo "Note: it boots in Vault mode with Wi-Fi off, so SSH works only after you"
echo "switch the wallet to Online mode. To go back to the M5Stack launcher:"
echo "  sudo sh deploy/uninstall.sh"
