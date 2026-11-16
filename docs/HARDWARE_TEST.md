# Hardware day: testing Insert Coin on the Cardputer Zero

Everything below already works in the simulator. These are the things only
the real device can show. Work top to bottom: later steps depend on earlier
ones. Use **signet** (test coins) for all of it.

When something fails, collect:

```
journalctl -u czwallet -u czsigner -u cznet -u czscan -b --no-pager | tail -200
sudo cat /var/lib/czwallet/app/crash.log
```

and send both, plus a photo of the screen if it's a display problem.

---

## 1. Install and boot

- [ ] `sudo sh deploy/install.sh` finishes without errors. Note how long the
      `evdev` and `argon2` installs take.
- [ ] `sudo reboot`, then `systemctl status czsigner czwallet cznet czscan`:
      all four `active (running)`.
- [ ] The INSERT COIN title screen appears on its own after boot, with the
      coin spinning smoothly. How many seconds from power-on?
- [ ] Press OK: the coin chime plays from the speaker (not too loud, no
      crackle), then the welcome screen or the PIN appears. If it's silent:
      `aplay -l` should list a sound card; `sudo -u czapp speaker-test -c1 -t sine -l1`
      tests the app's permission. SETUP › SOUND turns it off.
- [ ] `free -m` after boot: how much memory is used and free?

## 2. Keys

- [ ] `sudo python3 tools/keytest.py`, press every key (ESC three times to quit).
- [ ] What does **sym** send? If it isn't `KEY_RIGHTALT`, set `CZ_KEY_SYM` in
      both `czsigner.service` and `czwallet.service`.
- [ ] Does **fn** send its own code, or does fn+F arrive as `KEY_UP`? (Both work;
      just note which.)
- [ ] In the wallet: D▲ Z◀ X▼ C▶ move, OK selects, DEL goes back, HOME goes home,
      shift gives capitals, fn+left/right moves the text cursor.
- [ ] Holding a key repeats in text boxes, not in menus.
- [ ] Symbols: there's no separate sym key. Find out what types the blue
      symbols (Aa? fn?) and whether Aa gives capitals. Type a Wi-Fi password
      with a symbol and a capital to be sure. Report what `keytest.py` shows.
- [ ] Does the HOME button arrive as `KEY_HOMEPAGE` (or ESC)? What does NEXT send?

## 3. Screen

- [ ] Colors look right: gold is gold and the sky is dark blue. If red and blue
      are swapped, report it (the pixel format needs flipping).
- [ ] Nothing is cut off at any edge; the picture is right way up.
- [ ] Moving through menus feels instant. Animated QR codes run smoothly.
- [ ] If the screen stays black: `ls /sys/class/graphics/` and set `CZ_FB`.

## 4. Make a wallet

- [ ] CREATE NEW WALLET → 12 WORDS. The gold shield and your security words
      show in the corner during the PIN and words screens.
- [ ] Try ADD DICE ROLLS once (any rolls) to be sure it doesn't hang.
- [ ] **Unlock time:** after choosing the PIN, lock (L) and unlock. How many
      seconds does the PIN take? Aim: 1 to 3 s. (Argon2 settings can be tuned.)
- [ ] Type a wrong PIN once: hearts drop to 9. Then the right PIN works.
- [ ] Pull the power **during** a PIN check, power back on: the try still counts
      (9 → 8), and the wallet still opens with the right PIN.

## 5. Camera and QR

- [ ] `rpicam-hello --list-cameras` lists the camera.
- [ ] Press Q on home: the preview shows, and scanning a signet address from a
      phone screen at 10 to 15 cm starts a send.
- [ ] Scan an animated PSBT from Sparrow (UR and BBQr both). How many seconds?
- [ ] Leave the scan screen: the camera light (if any) goes off.
- [ ] If "camera not available": `journalctl -u czscan` and send it.

## 6. Radios: Vault and Online

- [ ] At boot (Vault): `rfkill list` shows Wi-Fi and Bluetooth **blocked**.
- [ ] SETUP › WI-FI: networks are listed and you can join one.
- [ ] MODE › Online: `rfkill` shows Wi-Fi unblocked; the status bar says ONLINE.
- [ ] MODE › Vault again: Wi-Fi drops within a few seconds.
- [ ] **Savings account:** while Online, try to send from it. It must refuse.

## 7. Network: Tor and sync

- [ ] Online with Tor on: the status line shows syncing, then a balance.
      How long does the first sync take?
- [ ] Get signet coins from a faucet to a RECEIVE address; they show as
      PENDING, then confirm.
- [ ] Send some back: the confirm screen is the signer's (gold shield), the
      payment broadcasts, and it shows in HISTORY.
- [ ] Sign a send in Vault mode → it waits in the Outbox → go Online → it's
      offered for broadcast.
- [ ] `date` shows the right date and time once online (the daily spend cap
      resets by date).

## 8. Add-ons (each one you have)

Run `sudo python3 tools/hwtest.py` with everything plugged in, and send the output.

- [ ] **Secure chip:** SETUP › HARDWARE › SECURE CHIP › USE. Lock and unlock:
      note the new unlock time. Unplug the chip: unlocking must say the chip
      is missing (not "wrong PIN") and must not cost a heart.
- [ ] **Fingerprint:** ADD A FINGER (3 touches), turn on QUICK UNLOCK, lock,
      unlock with the finger. A different finger must fail.
- [ ] **NFC Cap:** `sudo python3 tools/hwtest.py nfc`. If no reader is found,
      try `CZ_NFC_SPI=0.1` and `CZ_CC1101_SPI=0.0` (the buses may be swapped).
- [ ] Tap a SATSCARD (T on home): slot and address show; GENUINE check passes.
- [ ] Tap a TAPSIGNER: its key can be added to a 2-of-2 multisig on signet.

## 9. Share files over Wi-Fi

- [ ] Online, SETUP › SHARE FILES › START SHARING: the screen shows an address
      and QR. Scan it with a phone on the same Wi-Fi: the login page opens
      (accept the certificate warning; the fingerprint should match).
- [ ] Log in with admin / bitcoin, download a history CSV.
- [ ] With PAPER WALLETS › INCLUDE: download one, then DELETE it on the page.
      If DELETE fails, the card's FAT side isn't writable by `czshare`: report it.
- [ ] Press OK on the wallet: the page stops answering right away.
- [ ] `systemctl status czshare` shows it inactive afterwards.

## 10. Backups and updates

- [ ] SETUP › CARD BACKUP › SAVE. Put the microSD in a PC: the file is in
      `czwallet-backup/`.
- [ ] WIPE DEVICE, restore from your words: it offers the card backup, and
      your accounts come back.
- [ ] UPDATE with a signed release on the SD card installs and reboots into the
      new version; an unsigned one is refused.

## 11. Leave it running

- [ ] Leave it on home for an hour: no crash, auto-lock works, and the battery
      percentage looks believable.
- [ ] `crash.log` is empty (or send it).

## Menu sounds (0.8.11)

- [ ] Scroll a long menu: each move gives a soft low thock, audible but not sharp.
- [ ] Hold DOWN: the clicks keep up without stacking or stuttering (each one is a
      separate `aplay`; if ALSA reports the device busy, the clicks drop out
      silently instead and we should move to one long-lived player).
- [ ] The low end on the 1 W speaker: if it sounds thin, tell me and I'll lean
      the harmonics further up.

## Paper wallets off the card (0.8.15)

- [ ] Make a paper wallet: `ls -l /var/lib/czwallet/paper` shows the files as
      `root czshare` with `-rw-r-----`, and `/boot/firmware/InsertCoin-paper`
      is gone.
- [ ] `sudo -u czapp cat /var/lib/czwallet/paper/*.txt` is refused.
- [ ] SHARE FILES with PAPER WALLETS: INCLUDE asks for your own password
      first; after setting one, the files download and DELETE removes them.

## AUTO LOCK and sharing (0.8.17)

- [ ] SETUP › AUTO LOCK › 5 MIN asks for the PIN on the gold signer screen;
      2 MIN afterwards applies without one.
- [ ] Browse menus for 3+ minutes with AUTO LOCK at 2 MIN, then SEND: no PIN.
- [ ] Start SHARE FILES with a 10-minute limit and touch nothing: the page
      stays reachable the full 10 minutes and the wallet doesn't lock.
- [ ] After a restart, AUTO LOCK still shows what you picked.

## PRINT paper wallets (0.8.19)

- [ ] `systemctl status czprint` is running; `ls -l /run/czprint/print.sock`
      shows group czprint.
- [ ] PAPER WALLETS › PRINT lists your printer (mDNS). If not, TYPE ADDRESS
      with its IP works.
- [ ] First print asks to trust the fingerprint; the page prints and both
      QR codes scan.
- [ ] A printer with only plain IPP shows NO TLS and won't print.
- [ ] `sudo -u czprint curl -m 5 https://example.com` fails (no internet).
- [ ] Vault mode: PRINT says go Online first.

## Wipe PIN, countdown, spending policy (0.8.20)

- [ ] Set a WIPE PIN; lock; enter it: the title screen comes up, then
      welcome. `ls /var/lib/czwallet/signer /var/lib/czwallet/paper` are empty.
- [ ] LOGIN COUNTDOWN 5 MIN: after the PIN the countdown runs on the gold
      screen, keys don't skip it, the wallet opens at zero. Pull the power
      mid-countdown: after reboot the PIN and the full wait are needed again.
- [ ] PHONE 2FA: after a cold boot with no Wi-Fi, 2FA setup says the clock
      isn't set; go Online once and it works with Aegis / Google Authenticator.
- [ ] BYPASS PIN set: SHOW SEED WORDS refuses; changing a limit asks for the
      bypass PIN, then yours.
