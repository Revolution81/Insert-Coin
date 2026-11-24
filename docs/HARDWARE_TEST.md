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

## First session on a real device (2026-11-17)

M5Stack image: Debian 13 trixie, aarch64, Python 3.13.5, Pillow 12.3, 352 MB
RAM. Run by hand (`CZ_SIGNER_INPROC=1 CZ_DATA=$HOME/czdev python app.py`,
M5's launcher paused with `systemctl --user stop APPLaunch`).

Found and fixed:
- **Arrows are F▲ Z◀ X▼ C▶** (was D for up). Sym layer read off the keycaps.
- **Sym is done by the keyboard driver** (tca8418c): sym alone = `KEY_CD`, fn
  alone = `KEY_DVD`, sym + key = its own key code (sym+S = 80 = ':'), from
  `/usr/share/keymaps/tca8418_keypad_m5stack_keymap.map`. fn + digits give
  F-keys. HOME button = `KEY_ESC`, TAB button = `KEY_TAB`.
- **Black screen, cause 1:** Pillow 12 removed the `BGR;16` packer; own RGB565
  conversion now. Colours and orientation verified correct.
- **Black screen, cause 2:** M5's launcher leaves the backlight at 0 when it
  stops (`/sys/class/backlight/backlight`); the wallet turns it up at start.
- **The launcher is APPLaunch**, a systemd *user* service for `pi`, not
  lightdm; it needs `/dev/fb0` (= `/dev/fb_lcd`, panel-mipi-dbid 320x170
  16 bpp). The old installer's screen rule locked it out: don't use the
  dedicated install on M5's image until app mode is done.
- **Sound** goes through PipeWire (pw-play), not straight ALSA.
- **Wi-Fi:** `nmcli device wifi connect` fails ("key-mgmt: property is
  missing"); joins now use explicit profiles. Verified: joins and syncs.
- **Tor** isn't on M5's image: `sudo apt-get install -y tor`. Verified syncing
  through Tor.

Also verified 11-17: your own Electrum server set by scanning its QR
(Umbrel, `umbrel.local:50003:t`, green Wi-Fi icon); Vault mode cutting Wi-Fi.

## Where testing stands

Ticked boxes below were confirmed on the device, run by hand over SSH. Not
yet tried on hardware: the wallet image, the PIN timing and power-cut tests, animated
PSBTs from Sparrow, the Savings account refusal online, card backup, SHARE
FILES, printing, the wipe PIN, countdown and spending policy, and **every
add-on** (secure chip, fingerprint, NFC Cap). Still to check from the first
session: the fanfare's first note since the lead-in fix, sym typing inside
the wallet, `free -m` while running.

---

## 1. Install and boot

Tested image: M5Stack's Debian 13 (trixie), aarch64, Python 3.13, 352 MB RAM.
The installer turns off M5Stack's launcher (lightdm) so Insert Coin owns the
screen; `sudo sh deploy/uninstall.sh` brings the launcher back (add
`--purge` to also delete the wallet's data). After a reboot the wallet starts
in Vault mode with Wi-Fi off, so SSH only works once you switch it Online.

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

- [x] `sudo python3 tools/keytest.py`, press every key (ESC three times to quit). *Done 11-17.*
- [x] What does **sym** send? If it isn't `KEY_RIGHTALT`, set `CZ_KEY_SYM` in *Done: the driver handles it (see above).*
      both `czsigner.service` and `czwallet.service`.
- [x] Does **fn** send its own code, or does fn+F arrive as `KEY_UP`? (Both work; *Done: KEY_DVD alone.*
      just note which.)
- [x] In the wallet: F▲ Z◀ X▼ C▶ move, OK selects, DEL goes back, HOME goes home, *Movement keys done; shift and the text cursor still to try.*
      shift gives capitals, fn+left/right moves the text cursor.
- [ ] Holding a key repeats in text boxes, not in menus.
- [ ] Symbols: there's no separate sym key. Find out what types the blue
      symbols (Aa? fn?) and whether Aa gives capitals. Type a Wi-Fi password
      with a symbol and a capital to be sure. Report what `keytest.py` shows.
- [x] Does the HOME button arrive as `KEY_HOMEPAGE` (or ESC)? What does NEXT send? *Done: KEY_ESC.*

## 3. Screen

- [x] Colors look right: gold is gold and the sky is dark blue. If red and blue *Done.*
      are swapped, report it (the pixel format needs flipping).
- [x] Nothing is cut off at any edge; the picture is right way up. *Done.*
- [ ] Moving through menus feels instant. Animated QR codes run smoothly.
- [ ] If the screen stays black: `ls /sys/class/graphics/` and set `CZ_FB`.

## 4. Make a wallet

- [x] CREATE NEW WALLET → 12 WORDS. The gold shield and your security words *Wallet made 11-17.*
      show in the corner during the PIN and words screens.
- [ ] Try ADD DICE ROLLS once (any rolls) to be sure it doesn't hang.
- [ ] **Unlock time:** after choosing the PIN, lock (L) and unlock. How many
      seconds does the PIN take? Aim: 1 to 3 s. (Argon2 settings can be tuned.)
- [ ] Type a wrong PIN once: hearts drop to 9. Then the right PIN works.
- [ ] Pull the power **during** a PIN check, power back on: the try still counts
      (9 → 8), and the wallet still opens with the right PIN.

## 5. Camera and QR

- [ ] `rpicam-hello --list-cameras` lists the camera.
- [x] Press Q on home: the preview shows, and scanning a signet address from a *Scanning works (own node's QR, 11-17); camera flipped the right way since.*
      phone screen at 10 to 15 cm starts a send.
- [ ] Scan an animated PSBT from Sparrow (UR and BBQr both). How many seconds?
- [ ] Leave the scan screen: the camera light (if any) goes off.
- [ ] If "camera not available": `journalctl -u czscan` and send it.

## 6. Radios: Vault and Online

- [ ] At boot (Vault): `rfkill list` shows Wi-Fi and Bluetooth **blocked**.
- [x] SETUP › WI-FI: networks are listed and you can join one. *Done.*
- [x] MODE › Online: `rfkill` shows Wi-Fi unblocked; the status bar says ONLINE. *Online works.*
- [x] MODE › Vault again: Wi-Fi drops within a few seconds. *Done: SSH dropped as expected.*
- [ ] **Savings account:** while Online, try to send from it. It must refuse.

## 7. Network: Tor and sync

- [x] Online with Tor on: the status line shows syncing, then a balance. *Done.*
      How long does the first sync take?
- [x] Get signet coins from a faucet to a RECEIVE address; they show as *Done on mainnet: 2,000 sats received.*
      PENDING, then confirm.
- [x] Send some back: the confirm screen is the signer's (gold shield), the *Done 11-18 on mainnet: MAX send to a phone wallet, broadcast, PENDING in HISTORY.*
      payment broadcasts, and it shows in HISTORY.
- [x] Sign a send in Vault mode → it waits in the Outbox → go Online → it's *Done 11-18: Vault send of one picked coin, broadcast on going Online.*
      offered for broadcast.
- [ ] `date` shows the right date and time once online (the daily spend cap
      resets by date).

## 8. Add-ons (each one you have)

Run `sudo python3 tools/hwtest.py` with everything plugged in, and send the output.
Since 0.8.24 the installer no longer switches SPI/I2C/UART on by itself on a
running device (doing it live froze M5Stack's screen). First check
`ls /dev/spidev* /dev/i2c-* /dev/serial0`; if the one an add-on needs is
missing, we change the boot config and reboot.

- [ ] **Secure chip:** SETUP › HARDWARE › SECURE CHIP › USE. Lock and unlock:
      note the new unlock time. Unplug the chip: unlocking must say the chip
      is missing (not "wrong PIN") and must not cost a heart.
- [ ] **Fingerprint:** ADD A FINGER (3 touches), turn on QUICK UNLOCK, lock,
      unlock with the finger. A different finger must fail.
- [ ] **NFC Cap:** `sudo python3 tools/hwtest.py nfc`. If no reader is found,
      try `CZ_NFC_SPI=0.1` and `CZ_CC1101_SPI=0.0` (the buses may be swapped).
- [ ] **Unit NFC (U216) instead of the Cap:** wire it to the EXT header,
      not the Grove port (that's I2C1, the keyboard's bus, which the app is
      never given): yellow (SDA) to pin 3, white (SCL) to pin 1, red to
      pin 6 (5V), black to pin 4 (GND). Not with the Cap fitted (same
      header). `sudo sh deploy/install.sh --nfc-unit`, or on the wallet
      image add these two lines to `config.txt` on the boot partition:
      `gpio=23,26=pu` and
      `dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=23,i2c_gpio_scl=26,i2c_gpio_delay_us=10`.
      Reboot, then `sudo python3 tools/hwtest.py unit`. HARDWARE › NFC
      should say UNIT NFC.
- [ ] Tap a SATSCARD (T on home): slot and address show; GENUINE check passes.
- [ ] Tap a TAPSIGNER: its key can be added to a 2-of-2 multisig on signet.

## 8b. LAN and USB stick

- [ ] **LAN:** Online, plug a network cable in. The status bar shows a plug
      instead of the Wi-Fi fan (green on your own server, blue on the
      default) and SETUP › NETWORK › LAN says CONNECTED; it syncs. Vault
      mode: `ip link` shows the cable's interface DOWN.
- [ ] **USB stick:** a FAT32 or exFAT stick in the USB-A port (switch on
      HOST), SETUP › FILES › SAVE TO USB STICK: SAVED names the stick; on a
      PC, InsertCoin/ holds the CSVs, PSBTs and README. An NTFS stick says
      to format it; no stick says to plug one in.

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

## App mode, merge, image (0.8.22)

App mode (from M5Stack's image, over SSH):
- [x] `sudo sh deploy/install.sh --app` finishes; the **Insert Coin** coin icon *Done 11-18.*
      appears on the home carousel (reopen the carousel if it doesn't).
- [x] Open it: the title screen comes up, OK plays the fanfare (PipeWire is *Done 11-18.*
      paused for the session), the status bar says VAULT.
- [x] MODE › Online works; SETUP › NETWORK shows your Wi-Fi. *Done 11-18.*
- [x] SETUP › EXIT TO MENU: back on M5Stack's home screen within a few *Done 11-18.*
      seconds, Wi-Fi back on if it was on before, launcher sounds work.
- [ ] `systemctl status czsigner czwallet` afterwards: both inactive.
- [x] HOME on the LOCKED screen also offers EXIT. *Done 11-18.*

Merge coins:
- [ ] With 3+ coins: COINS, M (nothing picked) merges all; MERGE FEE lists
      the cheapest first; the signer's screen says MERGE n COINS INTO 1.
- [x] Pick two with OK, then M: only those two merge. *Done 11-18: MERGE 2 COINS INTO 1, fee and YOU KEEP correct.*

Wallet image:
- [ ] Burn the release's `.img.xz` with M5 Imager (custom image) after
      checking `sha256sum` against the `.sha256` file.
- [ ] First boot ends on the INSERT COIN title, not M5Stack's wizard or
      launcher. How long from power-on?
- [ ] It fits and boots from an 8 GB card.
- [ ] Online, from a PC on the same Wi-Fi: `ssh pi@<the device's address>`
      is refused (no SSH at all).
- [ ] Sound, keys, camera, Wi-Fi join, Tor sync as in the sections above.

## Editions (0.8.23)

- [ ] Spending edition (`--app`): the launcher icon reads *Insert Coin
      Spending*; the title says SPENDING WALLET (orange); the welcome screen
      says small amounts; CREATE NEW WALLET shows the orange SPENDING WALLET
      notice first, and OK carries on.
- [ ] Savings edition (image or `--dedicated`): the title says SAVINGS WALLET
      (green); no notice on the welcome screen.
