#!/bin/sh
# Installs Insert Coin on the Cardputer Zero (Raspberry Pi OS / Debian, as root).
#   sudo sh deploy/install.sh
set -eu
SRC="$(cd "$(dirname "$0")/.." && pwd)"
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
install -m 644 "$SRC/deploy/60-czwallet-screen.rules" /etc/udev/rules.d/
install -m 644 "$SRC/deploy/61-czwallet-backlight.rules" /etc/udev/rules.d/
udevadm control --reload-rules && udevadm trigger --subsystem-match=graphics || true

echo "== interfaces for add-ons"
# I2C for the secure chip; the UART (without a login console) for the fingerprint sensor
raspi-config nonint do_i2c 0 || true
raspi-config nonint do_serial_hw 0 || true
raspi-config nonint do_serial_cons 1 || true
getent group i2c >/dev/null && usermod -aG i2c czsigner

echo "== services"
install -m 644 "$SRC"/deploy/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable tor czwallet-vault czsigner cznet czscan czprint czwallet
# Keep the login console off the wallet screen (the signer owns it)
systemctl disable getty@tty1 2>/dev/null || true
# The Cardputer Zero's own launcher runs in a desktop session started by
# lightdm. Two programs can't share the screen and keyboard, so boot to
# Insert Coin instead. deploy/uninstall.sh puts the launcher back.
if systemctl list-unit-files lightdm.service >/dev/null 2>&1; then
  systemctl get-default > /etc/czwallet/previous-target 2>/dev/null || true
  systemctl disable lightdm 2>/dev/null || true
  systemctl set-default multi-user.target
fi

echo "Done. Reboot to start Insert Coin: sudo reboot"
echo "Note: it boots in Vault mode with Wi-Fi off, so SSH works only after you"
echo "switch the wallet to Online mode. To go back to the M5Stack launcher:"
echo "  sudo sh deploy/uninstall.sh"
