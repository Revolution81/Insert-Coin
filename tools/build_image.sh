#!/bin/sh
# Build a ready-to-burn Insert Coin image from M5Stack's CardputerZero OS.
#
#   sudo BASE_SHA256=<hash> sh tools/build_image.sh
#
# Runs on an arm64 Linux machine (GitHub's ubuntu-24.04-arm runner; see
# .github/workflows/image.yml) as root. It downloads M5Stack's image, checks
# its SHA-256 if you give one, installs Insert Coin in dedicated mode inside
# it (deploy/install.sh --dedicated --image), switches off M5Stack's launcher
# and first-boot wizard, and writes out/Insert-Coin-Savings-<version>-cardputerzero.img.xz
# plus its .sha256. Every step prints what it does, so the log shows exactly
# what went into the image.
set -eu
SRC="$(cd "$(dirname "$0")/.." && pwd)"
BASE_URL="${BASE_URL:-https://cardputer-zero-repo.oss-cn-shenzhen.aliyuncs.com/cardputerzero-trixie-arm64-latest.img.xz}"
# The M5Stack image this was last built and checked against (2026-09-29 "latest").
# If M5Stack publishes a new one, the build stops: look it over, then pass its hash.
BASE_SHA256="${BASE_SHA256:-1b1002f7def184d6ca18d973f8b94622ccbe6552f3159cf1789ea955e833ed0a}"
GROW="${GROW:-2G}"            # working room while installing; shrunk away after
SPARE_MB="${SPARE_MB:-600}"   # free space left on the wallet's partition
MAX_BYTES="${MAX_BYTES:-7300000000}"   # must fit an "8 GB" card (about 7.4-7.9 GB real)
# DEV=1 with DEV_SSH_KEY="ssh-ed25519 AAAA... you@pc": a developer image for
# debugging (several keys: separate them with ";"). Same as the savings
# image, except SSH stays on for those keys only
# (never a password) and the title says DEVELOPER: NOT FOR REAL MONEY. Never
# attach one to a release.
DEV="${DEV:-0}"
DEV_SSH_KEY="${DEV_SSH_KEY:-}"
if [ "$DEV" = 1 ]; then
  case "$DEV_SSH_KEY" in ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*) ;;
    *) echo "!! DEV=1 needs DEV_SSH_KEY, your SSH public key (ssh-ed25519 ...)" >&2; exit 1 ;;
  esac
fi
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$SRC/core/version.py")"
WORK="${WORK:-$SRC/work}"
OUT="${OUT:-$SRC/out}"
NAME="Insert-Coin-Savings-$VERSION-cardputerzero"
[ "$DEV" = 1 ] && NAME="Insert-Coin-DEVELOPER-$VERSION-cardputerzero"
ROOT="$WORK/root"
LOOP=""

cleanup() {
  set +e
  for d in boot/firmware dev/pts dev proc sys run; do
    mountpoint -q "$ROOT/$d" && umount -l "$ROOT/$d"
  done
  mountpoint -q "$ROOT" && umount -l "$ROOT"
  [ -n "$LOOP" ] && losetup -d "$LOOP"
}
trap cleanup EXIT
in_root() { chroot "$ROOT" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LANG=C.UTF-8 "$@"; }

mkdir -p "$WORK" "$OUT" "$ROOT"
echo "== base image: $BASE_URL"
[ -f "$WORK/base.img.xz" ] || curl -fL --retry 5 -o "$WORK/base.img.xz" "$BASE_URL"
SHA="$(sha256sum "$WORK/base.img.xz" | cut -d' ' -f1)"
echo "base image sha256: $SHA"
if [ -n "$BASE_SHA256" ] && [ "$SHA" != "$BASE_SHA256" ]; then
  echo "!! the base image doesn't match the expected SHA-256 ($BASE_SHA256)" >&2
  exit 1
fi

echo "== unpack and grow by $GROW"
xz -dc -T0 "$WORK/base.img.xz" > "$WORK/image.img"
truncate -s "+$GROW" "$WORK/image.img"
parted -s "$WORK/image.img" resizepart 2 100%
LOOP="$(losetup -fP --show "$WORK/image.img")"
e2fsck -pf "${LOOP}p2" || true
resize2fs "${LOOP}p2"

echo "== mount"
mount "${LOOP}p2" "$ROOT"
mkdir -p "$ROOT/boot/firmware"
mount "${LOOP}p1" "$ROOT/boot/firmware"
for d in dev dev/pts proc sys run; do mount --bind "/$d" "$ROOT/$d"; done
cp --remove-destination /etc/resolv.conf "$ROOT/etc/resolv.conf.cz-build" 2>/dev/null || true
if [ -e "$ROOT/etc/resolv.conf" ] || [ -L "$ROOT/etc/resolv.conf" ]; then
  mv "$ROOT/etc/resolv.conf" "$ROOT/etc/resolv.conf.orig"
fi
mv "$ROOT/etc/resolv.conf.cz-build" "$ROOT/etc/resolv.conf"
# no services start inside the chroot
printf '#!/bin/sh\nexit 101\n' > "$ROOT/usr/sbin/policy-rc.d"
chmod 755 "$ROOT/usr/sbin/policy-rc.d"

echo "== what the base image runs at boot"
ls -1 "$ROOT"/etc/systemd/system/*.wants 2>/dev/null || true
ls -1 "$ROOT"/usr/lib/systemd/user "$ROOT"/etc/systemd/user 2>/dev/null | grep -iE "applaunch|wizard|pipewire|wireplumber" || true

echo "== install Insert Coin $VERSION (dedicated)"
rm -rf "$ROOT/opt/insert-coin-src"
mkdir -p "$ROOT/opt/insert-coin-src"
(cd "$SRC" && tar --exclude=./work --exclude=./out --exclude=./.git -cf - .) | tar -xf - -C "$ROOT/opt/insert-coin-src"
in_root sh /opt/insert-coin-src/deploy/install.sh --dedicated --image

echo "== switch off M5Stack's launcher, wizard and auto-updaters"
for u in $(in_root systemctl list-unit-files --no-legend 2>/dev/null | awk '{print $1}' \
           | grep -iE '^(applaunch|launchwizard|launch-wizard|.*wizard|userconfig)' || true); do
  echo "mask $u"; in_root systemctl mask "$u" || true
done
for f in "$ROOT"/usr/lib/systemd/user/* "$ROOT"/etc/systemd/user/*; do
  [ -f "$f" ] || continue
  u="$(basename "$f")"
  case "$u" in *[Aa][Pp][Pp][Ll]aunch*|*[Ww]izard*)
    echo "mask (user) $u"; in_root systemctl --global mask "$u" || true ;;
  esac
done

echo "== lock down logins (the screen and keyboard are the only way in)"
# M5Stack's image ships with SSH on and users meant to be set up by its
# first-boot wizard, which is switched off above. A savings wallet takes no
# logins at all: SSH off (and the boot-partition switch that turns it on),
# every account's password locked, cloud-init (which could add users and
# keys from files on the card) disabled.
if [ "$DEV" = 1 ]; then
  echo "DEVELOPER image: SSH stays on, for one key only"
  in_root systemctl mask sshswitch.service || true
  install -d -m 700 -o 1000 -g 1000 "$ROOT/home/pi/.ssh"
  printf '%s\n' "$DEV_SSH_KEY" | tr ';' '\n' | sed 's/^ *//; s/ *$//; /^$/d' > "$ROOT/home/pi/.ssh/authorized_keys"
  echo "authorized keys: $(wc -l < "$ROOT/home/pi/.ssh/authorized_keys")"
  chown 1000:1000 "$ROOT/home/pi/.ssh/authorized_keys"; chmod 600 "$ROOT/home/pi/.ssh/authorized_keys"
  mkdir -p "$ROOT/etc/ssh/sshd_config.d"
  printf '%s\n' "PasswordAuthentication no" "KbdInteractiveAuthentication no" \
      "PermitRootLogin no" "AllowUsers pi" > "$ROOT/etc/ssh/sshd_config.d/10-insert-coin-dev.conf"
  in_root systemctl enable ssh.service || true
  printf '[Service]\nEnvironment=CZ_EDITION=developer\n' \
      > "$ROOT/etc/systemd/system/czwallet.service.d/savings.conf"
fi
for u in ssh.service ssh.socket sshd.service sshswitch.service; do
  [ "$DEV" = 1 ] && break
  if in_root systemctl list-unit-files --no-legend "$u" 2>/dev/null | grep -q .; then
    echo "mask $u"; in_root systemctl mask "$u" || true
  fi
done
in_root awk -F: '($3 == 0 || $3 >= 1000) && $3 != 65534 {print $1}' /etc/passwd | while read -r who; do
  in_root passwd -l "$who" >/dev/null 2>&1 && echo "locked password: $who"
done
if [ -d "$ROOT/etc/cloud" ]; then
  touch "$ROOT/etc/cloud/cloud-init.disabled"; echo "cloud-init disabled"
fi
echo "accounts (name uid shell password-state):"
in_root awk -F: '($3 == 0 || $3 >= 1000) && $3 != 65534 {print $1, $3, $7}' /etc/passwd | while read -r who uid sh; do
  st="$(in_root passwd -S "$who" 2>/dev/null | awk '{print $2}')"
  echo "  $who $uid $sh ${st:-?}"
done

echo "== remove apps a wallet never uses (browsers, media, remote desktop)"
# Less to store, and less code that could be attacked: a browser and a
# remote-desktop server have no place on a savings wallet. apt is asked
# first what it would remove; if that touches anything the hardware or the
# wallet needs, nothing is removed.
DROP="firefox chromium chromium-l10n chromium-common vlc vlc-l10n vlc-plugin-base vlc-bin
      rpi-userguide realvnc-vnc-server wayvnc rpi-imager mkvtoolnix pocketsphinx-en-us
      python3-mypy"
PRESENT=""
for p in $DROP; do
  if in_root dpkg -s "$p" >/dev/null 2>&1; then PRESENT="$PRESENT $p"; fi
done
if [ -n "$PRESENT" ]; then
  PLAN="$(in_root apt-get -s purge $PRESENT | awk '/^Purg /{print $2}')"
  echo "would remove:" $PLAN
  KEEP='^(linux-|raspi-firmware|firmware-brcm|raspberrypi-|rpi-eeprom|libcamera|python3$|python3\.|python3-(evdev|picamera2|pil|numpy|libcamera)|tor$|network-manager|sudo|systemd|udev|libc6|pipewire|wireplumber|alsa|libzbar|zbar|m5|cardputer|cp0)'
  if echo "$PLAN" | tr ' ' '\n' | grep -qiE "$KEEP"; then
    echo "!! that would remove something the device needs; keeping everything"
  else
    in_root env DEBIAN_FRONTEND=noninteractive apt-get purge -y $PRESENT
  fi
fi

echo "== space"
df -h "$ROOT" | tail -1

echo "== tidy"
in_root apt-get clean
rm -rf "$ROOT/opt/insert-coin-src" "$ROOT/usr/sbin/policy-rc.d" "$ROOT/var/lib/apt/lists/"*
rm -f "$ROOT/etc/resolv.conf"
[ -e "$ROOT/etc/resolv.conf.orig" ] || [ -L "$ROOT/etc/resolv.conf.orig" ] && mv "$ROOT/etc/resolv.conf.orig" "$ROOT/etc/resolv.conf"
{
  if [ "$DEV" = 1 ]; then echo "Insert Coin $VERSION, DEVELOPER image (SSH for one key; not for real money)"
  else echo "Insert Coin $VERSION, savings edition (dedicated wallet image)"; fi
  echo "base: $BASE_URL"
  echo "base sha256: $SHA"
  echo "source: $(cd "$SRC" && git rev-parse HEAD 2>/dev/null || echo unknown)"
} > "$ROOT/etc/insert-coin-image"
cat "$ROOT/etc/insert-coin-image"

echo "== unmount"
cleanup
trap - EXIT
LOOP=""

echo "== shrink to fit small cards"
# The install needed working room; give it back so the image fits an 8 GB
# card, keeping $SPARE_MB MB free for the wallet's data and logs.
START="$(sfdisk -d "$WORK/image.img" | sed -n 's/^.*image\.img2 : start= *\([0-9]*\),.*/\1/p')"   # 512-byte sectors
[ -n "$START" ] || { echo "!! can't read partition 2's start" >&2; exit 1; }
LOOP="$(losetup -fP --show "$WORK/image.img")"
trap cleanup EXIT
e2fsck -fy "${LOOP}p2" || true
BS="$(dumpe2fs -h "${LOOP}p2" 2>/dev/null | awk -F: '/^Block size/ {gsub(/ /,"",$2); print $2}')"
MIN="$(resize2fs -P "${LOOP}p2" 2>/dev/null | awk -F': ' '/minimum/ {print $2}')"
BLOCKS=$(( MIN + SPARE_MB * 1024 * 1024 / BS ))
echo "block size $BS, minimum $MIN blocks, keeping $BLOCKS"
resize2fs "${LOOP}p2" "$BLOCKS"
losetup -d "$LOOP"; LOOP=""; trap - EXIT
SECTORS=$(( BLOCKS * BS / 512 ))
printf '%s,%s\n' "$START" "$SECTORS" | sfdisk --no-reread --no-tell-kernel -N 2 "$WORK/image.img"
SIZE=$(( (START + SECTORS) * 512 ))
truncate -s "$SIZE" "$WORK/image.img"
echo "image size: $SIZE bytes ($(( SIZE / 1024 / 1024 )) MiB)"
if [ "$SIZE" -gt "$MAX_BYTES" ]; then
  echo "!! the image is bigger than an 8 GB card holds ($MAX_BYTES bytes)" >&2
  exit 1
fi
# check the shrunk filesystem is whole
LOOP="$(losetup -fP --show "$WORK/image.img")"; trap cleanup EXIT
e2fsck -fn "${LOOP}p2"
losetup -d "$LOOP"; LOOP=""; trap - EXIT

echo "== compress"
xz -T0 -6 -c "$WORK/image.img" > "$OUT/$NAME.img.xz"
(cd "$OUT" && sha256sum "$NAME.img.xz" > "$NAME.img.xz.sha256" && cat "$NAME.img.xz.sha256")
ls -lh "$OUT"
