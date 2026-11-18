#!/bin/sh
# Installs Insert Coin on the Cardputer Zero (M5Stack's image, Debian 13), as root.
#
#   sudo sh deploy/install.sh --app        an app in M5Stack's launcher (default
#                                          when the launcher is there)
#   sudo sh deploy/install.sh --dedicated  the device boots straight into the
#                                          wallet and runs nothing else
#   ... --image                            building a disk image (in a chroot):
#                                          skip steps that need a running system
#
# deploy/uninstall.sh undoes either.
set -eu
SRC="$(cd "$(dirname "$0")/.." && pwd)"
MODE=""
IMAGE=0
for a in "$@"; do
  case "$a" in
    --app) MODE=app ;;
    --dedicated) MODE=dedicated ;;
    --image) IMAGE=1 ;;
    *) echo "usage: install.sh [--app|--dedicated] [--image]" >&2; exit 2 ;;
  esac
done
if [ -z "$MODE" ]; then
  if [ -d /usr/share/APPLaunch ]; then MODE=app; else MODE=dedicated; fi
fi
echo "== Insert Coin: $MODE mode"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$SRC/core/version.py")"
VENV=/opt/czwallet-venv
RELEASE=/opt/czwallet-releases/$VERSION
LINK=/opt/czwallet          # -> the active release; updates switch this link

echo "== packages"
apt-get update
# python3-evdev, picamera2 and the build tools come from apt (signed), not PyPI
apt-get install -y python3-venv python3-pip python3-setuptools python3-wheel \
    python3-evdev tor rfkill network-manager sudo alsa-utils
# Names differ between Raspberry Pi OS and plain Debian 13 (trixie's 64-bit
# time renames, e.g. libzbar0t64); these are needed only for add-ons.
apt-get install -y libzbar0t64 2>/dev/null || apt-get install -y libzbar0 2>/dev/null \
    || echo "!! zbar not installed: QR scanning won't work"
apt-get install -y python3-picamera2 2>/dev/null || echo "!! picamera2 not installed: no camera"
apt-get install -y python3-spidev 2>/dev/null || echo "!! spidev not installed: no NFC Cap"
# SPI for the Cap CC1101 & NFC
raspi-config nonint do_spi 0 2>/dev/null || true
# Optional: native secp256k1 makes signing much faster than embit's pure-Python fallback
apt-get install -y libsecp256k1-1 2>/dev/null || apt-get install -y libsecp256k1-2 2>/dev/null || true

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
usermod -aG czsigner,cznet,czscan,czprint,spi czapp
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

echo "== code ($VERSION)"
rm -rf "$RELEASE"
mkdir -p "$RELEASE"
cp -r "$SRC/app.py" "$SRC/requirements.txt" "$SRC/requirements.lock" "$SRC/core" "$SRC/ui" "$SRC/tools" "$SRC/deploy" "$RELEASE/"
[ -f "$SRC/SECURITY.md" ] && cp "$SRC/SECURITY.md" "$RELEASE/"
find "$RELEASE" -name __pycache__ -prune -exec rm -rf {} +
if [ -d "$LINK" ] && [ ! -L "$LINK" ]; then mv "$LINK" "$LINK.old-layout"; fi
ln -sfn "$RELEASE" "$LINK"
# --system-site-packages: evdev, picamera2 and setuptools come from apt.
[ -x "$VENV/bin/python" ] || python3 -m venv --system-site-packages "$VENV"
# Every PyPI file must match a SHA-256 in requirements.lock or pip refuses
# it. Wheels only (no install-time code), except embit, which is pure
# Python source built with apt's setuptools (no build tools downloaded).
"$VENV/bin/pip" install --require-hashes --no-deps --only-binary :all: \
    --no-binary embit --no-build-isolation -r "$SRC/requirements.lock"
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
if [ ! -f /etc/czwallet/release_keys.txt ]; then
  printf '%s\n' "# Ed25519 public keys (hex), one per line, trusted to sign updates." \
                 "# Get yours from: python3 tools/release.py keygen" > /etc/czwallet/release_keys.txt
fi
chmod 644 /etc/czwallet/release_keys.txt

echo "== root helpers (radios, updates, card copies, file sharing)"
install -o root -g root -m 755 "$SRC/deploy/cz-radio" /usr/local/sbin/cz-radio
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
if [ "$MODE" = dedicated ]; then
  # only the signer may draw on the panel or set the backlight
  install -m 644 "$SRC/deploy/60-czwallet-screen.rules" /etc/udev/rules.d/
  install -m 644 "$SRC/deploy/61-czwallet-backlight.rules" /etc/udev/rules.d/
  if [ "$IMAGE" = 0 ]; then
    udevadm control --reload-rules && udevadm trigger --subsystem-match=graphics || true
  fi
fi

echo "== interfaces for add-ons"
# I2C for the secure chip; the UART (without a login console) for the fingerprint sensor
raspi-config nonint do_i2c 0 || true
raspi-config nonint do_serial_hw 0 || true
raspi-config nonint do_serial_cons 1 || true
getent group i2c >/dev/null && usermod -aG i2c czsigner

echo "== services"
install -m 644 "$SRC"/deploy/*.service /etc/systemd/system/
mkdir -p /etc/systemd/system/czwallet.service.d
rm -f /etc/systemd/system/czwallet.service.d/appmode.conf
systemctl daemon-reload || true

if [ "$MODE" = app ]; then
  # Nothing starts at boot: the launcher's Insert Coin icon runs a session
  install -m 644 "$SRC/deploy/czwallet-appmode.conf" /etc/systemd/system/czwallet.service.d/appmode.conf
  systemctl daemon-reload || true
  systemctl disable tor czwallet-vault czsigner cznet czscan czprint czwallet 2>/dev/null || true
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
  echo "Done. Insert Coin is now an app on the CardputerZero home screen."
  echo "To remove it: sudo sh deploy/uninstall.sh"
  exit 0
fi

# Dedicated: boot straight into the wallet
systemctl enable tor czwallet-vault czsigner cznet czscan czprint czwallet
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

echo "Done. Reboot to start Insert Coin: sudo reboot"
echo "Note: it boots in Vault mode with Wi-Fi off, so SSH works only after you"
echo "switch the wallet to Online mode. To go back to the M5Stack launcher:"
echo "  sudo sh deploy/uninstall.sh"
