#!/bin/sh
# Build a ready-to-burn Insert Coin image from M5Stack's CardputerZero OS.
#
#   sudo BASE_SHA256=<hash> sh tools/build_image.sh
#
# Runs on an arm64 Linux machine (GitHub's ubuntu-24.04-arm runner; see
# .github/workflows/image.yml) as root. It downloads M5Stack's image, checks
# its SHA-256 if you give one, installs Insert Coin in dedicated mode inside
# it (deploy/install.sh --dedicated --image), switches off M5Stack's launcher
# and first-boot wizard, and writes out/Insert-Coin-<version>-cardputerzero.img.xz
# plus its .sha256. Every step prints what it does, so the log shows exactly
# what went into the image.
set -eu
SRC="$(cd "$(dirname "$0")/.." && pwd)"
BASE_URL="${BASE_URL:-https://cardputer-zero-repo.oss-cn-shenzhen.aliyuncs.com/cardputerzero-trixie-arm64-latest.img.xz}"
# The M5Stack image this was last built and checked against (2026-09-29 "latest").
# If M5Stack publishes a new one, the build stops: look it over, then pass its hash.
BASE_SHA256="${BASE_SHA256:-1b1002f7def184d6ca18d973f8b94622ccbe6552f3159cf1789ea955e833ed0a}"
GROW="${GROW:-2G}"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$SRC/core/version.py")"
WORK="${WORK:-$SRC/work}"
OUT="${OUT:-$SRC/out}"
NAME="Insert-Coin-$VERSION-cardputerzero"
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

echo "== tidy"
in_root apt-get clean
rm -rf "$ROOT/opt/insert-coin-src" "$ROOT/usr/sbin/policy-rc.d" "$ROOT/var/lib/apt/lists/"*
rm -f "$ROOT/etc/resolv.conf"
[ -e "$ROOT/etc/resolv.conf.orig" ] || [ -L "$ROOT/etc/resolv.conf.orig" ] && mv "$ROOT/etc/resolv.conf.orig" "$ROOT/etc/resolv.conf"
{
  echo "Insert Coin $VERSION, dedicated wallet image"
  echo "base: $BASE_URL"
  echo "base sha256: $SHA"
  echo "source: $(cd "$SRC" && git rev-parse HEAD 2>/dev/null || echo unknown)"
} > "$ROOT/etc/insert-coin-image"
cat "$ROOT/etc/insert-coin-image"

echo "== unmount and compress"
cleanup
trap - EXIT
LOOP=""
xz -T0 -6 -c "$WORK/image.img" > "$OUT/$NAME.img.xz"
(cd "$OUT" && sha256sum "$NAME.img.xz" > "$NAME.img.xz.sha256" && cat "$NAME.img.xz.sha256")
ls -lh "$OUT"
