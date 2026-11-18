#!/bin/sh
# Takes Insert Coin off the boot and gives the screen back to the
# Cardputer Zero's own launcher.
#   sudo sh deploy/uninstall.sh            stop and disable; keep everything
#   sudo sh deploy/uninstall.sh --purge    also delete the wallet's data
#
# Without --purge nothing is deleted: the encrypted seed, settings and
# history stay in /var/lib/czwallet, and install.sh brings it all back.
# Make sure you have your seed words before using --purge.
set -u
echo "== stopping Insert Coin"
for s in czwallet czprint czscan cznet czsigner czwallet-vault czshare; do
  systemctl disable --now "$s" 2>/dev/null || true
done
echo "== radios back on"
rfkill unblock wifi 2>/dev/null || true
rfkill unblock bluetooth 2>/dev/null || true
nmcli networking on 2>/dev/null || true
echo "== screen and keyboard back to everyone"
rm -f /etc/udev/rules.d/60-czwallet-screen.rules
udevadm control --reload-rules 2>/dev/null && udevadm trigger --subsystem-match=graphics 2>/dev/null || true
echo "== launcher back"
target="$(cat /etc/czwallet/previous-target 2>/dev/null || echo graphical.target)"
systemctl set-default "$target"
systemctl enable lightdm 2>/dev/null || true
systemctl enable getty@tty1 2>/dev/null || true
if [ "${1:-}" = "--purge" ]; then
  echo "== deleting wallet data"
  /usr/local/sbin/cz-backup wipe 2>/dev/null || true
  rm -rf /var/lib/czwallet /opt/czwallet /opt/czwallet-releases /opt/czwallet-venv \
         /etc/czwallet /etc/sudoers.d/czwallet
fi
echo "Done. Reboot to get the launcher back: sudo reboot"
