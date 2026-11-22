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
# M5Stack's stable CardputerZero OS of 2026-11-19 (launcher 0.6.111), from
# their os-list.json. It has the keyboard driver with the Fn layer (Fn+A mute,
# Fn+S volume down, Fn+D up); the 2026-09-29 image had none, so Fn did nothing.
BASE_URL="${BASE_URL:-https://cardputer-zero-repo.oss-cn-shenzhen.aliyuncs.com/cardputerzero-trixie-arm64-20261119-094529-3b0e6f6.img.xz}"
# If the file ever differs, the build stops: look it over, then pass its hash.
BASE_SHA256="${BASE_SHA256:-27e35401ccb8f46356f45c59f16a4cfaed8423bf61862a3fe449e55405e788b9}"
GROW="${GROW:-2G}"            # working room while installing; shrunk away after
SPARE_MB="${SPARE_MB:-600}"   # free space left on the wallet's partition
MAX_BYTES="${MAX_BYTES:-7300000000}"   # must fit an "8 GB" card (about 7.4-7.9 GB real)
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$SRC/core/version.py")"
WORK="${WORK:-$SRC/work}"
OUT="${OUT:-$SRC/out}"
NAME="Insert-Coin-Savings-$VERSION-cardputerzero"
ROOT="$WORK/root"
LOOP=""

# Runs in a subshell, so its "keep going on errors" can never leak into the
# rest of the build (it once did, and a failed shrink went out unnoticed).
cleanup() (
  set +e
  for d in boot/firmware dev/pts dev proc sys run; do
    mountpoint -q "$ROOT/$d" && umount -l "$ROOT/$d"
  done
  mountpoint -q "$ROOT" && umount -l "$ROOT"
  [ -n "$LOOP" ] && losetup -d "$LOOP"
  true
)

# Attach the image and wait until its partitions exist (they appear a moment
# after losetup returns; using them too early fails with "no such file").
attach() {
  LOOP="$(losetup -fP --show "$WORK/image.img")"
  i=0
  until [ -b "${LOOP}p1" ] && [ -b "${LOOP}p2" ]; do
    i=$((i + 1)); [ "$i" -le 50 ] || { echo "!! partitions of $LOOP never appeared" >&2; exit 1; }
    partx -u "$LOOP" 2>/dev/null || true; sleep 0.2
  done
  udevadm settle 2>/dev/null || true
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
attach
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
# M5Stack's splash (fb_load, 2026-11 OS on) holds the panel until their
# launcher starts, which never happens here: the logo stayed on top of the
# wallet for good (a Conflicts= in czsigner didn't stop it). Off entirely;
# the signer switches the panel on itself (ui/display.wake_panel).
if in_root systemctl list-unit-files --no-legend 2>/dev/null | grep -q '^fb_load\.service'; then
  echo "mask fb_load.service"; in_root systemctl mask fb_load.service
fi

echo "== keyboard: the Fn layer must be there (volume keys)"
# The keyboard overlay in use must carry linux,keymap2 (the Fn layer) with
# KEY_MUTE/VOLUMEDOWN/VOLUMEUP; an older M5Stack base had none and Fn+A/S/D
# could never reach the wallet. Stop rather than ship that again.
OVL=""
for o in $(sed -n 's/^dtoverlay=\([^,]*\).*/\1/p' "$ROOT/boot/firmware/config.txt"); do
  f="$ROOT/boot/firmware/overlays/$o.dtbo"
  if [ -f "$f" ] && dtc -I dtb -O dts "$f" 2>/dev/null | grep -q "tca8418"; then OVL="$f"; fi
done
echo "keyboard overlay: ${OVL:-none}"
if [ -z "$OVL" ] || ! dtc -I dtb -O dts "$OVL" 2>/dev/null | grep -q "linux,keymap2"; then
  echo "!! this base image's keyboard has no Fn layer (linux,keymap2)" >&2
  exit 1
fi
dtc -I dtb -O dts "$OVL" 2>/dev/null | grep -E "fn-button-code|sym-button-code|keymap2" | head

echo "== no blinking text cursor over the wallet's screen"
# The Linux console shares the panel; without this its cursor blinks in the
# top-left corner on top of every frame the signer draws.
CMD="$ROOT/boot/firmware/cmdline.txt"
if [ -f "$CMD" ] && ! grep -q "vt.global_cursor_default=0" "$CMD"; then
  sed -i '1 s/$/ vt.global_cursor_default=0/' "$CMD"
fi
cat "$CMD"

echo "== lock down logins (the screen and keyboard are the only way in)"
# M5Stack's image ships with SSH on and users meant to be set up by its
# first-boot wizard, which is switched off above. A savings wallet takes no
# logins at all: SSH off (and the boot-partition switch that turns it on),
# every account's password locked, cloud-init (which could add users and
# keys from files on the card) disabled.
for u in ssh.service ssh.socket sshd.service sshswitch.service; do
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
  echo "Insert Coin $VERSION, savings edition (dedicated wallet image)"
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
attach
trap cleanup EXIT
# -fy fixes what it can; exit codes 0-2 mean the filesystem is fine now
e2fsck -fy "${LOOP}p2" || [ $? -le 2 ]
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
attach; trap cleanup EXIT
e2fsck -fn "${LOOP}p2"
# and the size the filesystem claims must fit the partition it now lives in
FSB="$(dumpe2fs -h "${LOOP}p2" 2>/dev/null | awk -F: '/^Block count/ {gsub(/ /,"",$2); print $2}')"
DEVB=$(( $(blockdev --getsize64 "${LOOP}p2") / BS ))
echo "filesystem $FSB blocks, partition $DEVB blocks"
[ "$FSB" -le "$DEVB" ] || { echo "!! filesystem is bigger than its partition" >&2; exit 1; }
mkdir -p "$WORK/check" && mount -o ro "${LOOP}p2" "$WORK/check" && ls "$WORK/check/etc/insert-coin-image" && umount "$WORK/check"
losetup -d "$LOOP"; LOOP=""; trap - EXIT

echo "== compress"
xz -T0 -6 -c "$WORK/image.img" > "$OUT/$NAME.img.xz"
(cd "$OUT" && sha256sum "$NAME.img.xz" > "$NAME.img.xz.sha256" && cat "$NAME.img.xz.sha256")
ls -lh "$OUT"
