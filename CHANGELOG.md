# Changelog

Newest first. Version numbers follow `core/version.py`; every release is signed (see `tools/release.py`).

## 0.8.24: steadier outbox screen

From the second session on the device:
- After signing in Vault mode the screen is now **SAVED TO OUTBOX** and says
  plainly that the payment sends itself when you go Online. A small payment
  shows one steady QR instead of flipping through parts (bigger ones still
  animate). OK closes it; the payment stays in the outbox either way.
- Animated QR codes play slower (2 parts a second), and the arrow keys
  pause them and step one part at a time; PART n/N shows PAUSED.
- **SETUP › DISPLAY & SOUND › CLOCK**: 24H (21:56) or 12H (9:56PM) on every
  status bar, the signer's screens included, plus SYNCED on the home screen
  and a transaction's time in HISTORY. The signer keeps the setting.
- **Faster updates**: running `deploy/install.sh` again skips system
  packages that are already installed, Python packages when
  `requirements.lock` hasn't changed, and the interface setup, so an update
  mostly just copies the new code. `--full` redoes everything.
- **Fixed: the screen froze after an install or update** (M5Stack's menu
  stopped responding). The installer ran raspi-config to switch on SPI and
  I2C for add-ons, live, on the buses the screen and keyboard already use.
  It now does that only when building the wallet image; a running device is
  left as it is.
- **Wallet image locked down and smaller.** The Savings image now has SSH
  switched off (including the boot-partition switch), every account's
  password locked and cloud-init disabled, so nothing can log in over the
  network or be added from files on the card. Browsers (Firefox, Chromium),
  VLC, the RealVNC and wayvnc remote-desktop servers, Thonny and the Imager
  are removed: about 1.1 GB less, and less code to attack. After installing,
  the build shrinks the image (600 MB kept free) to 6.4 GiB, so it fits an
  8 GB card.
- The merge confirm screen no longer offers TAB: SHOW FULL ADDRESS, since a
  merge sends nothing to an outside address.

## 0.8.23: savings and spending editions

Insert Coin now comes in two clearly labelled editions, so nobody puts
savings on the wrong one.

- **Savings edition**: the wallet image (now named
  `Insert-Coin-Savings-<version>-cardputerzero.img.xz`) or
  `deploy/install.sh --dedicated`. The device runs nothing else. The title
  screen says SAVINGS WALLET in green.
- **Spending edition**: app mode, `deploy/install.sh --app`. The launcher
  icon is *Insert Coin Spending*, the title screen says SPENDING WALLET in
  orange, the welcome screen says small amounts only, and an orange notice
  explains why before a seed is created or restored. The installer prints the
  same warning.
- README, website, SECURITY.md and the image release notes explain the
  difference side by side. The M5Stack Store version was dropped: the Store
  doesn't allow apps that need root, and the wallet's isolation needs it.

## 0.8.22: merge coins, app mode, wallet image

**Merge coins.** COINS › M merges the coins you picked with OK (or every coin
when none are picked) into one coin at a fresh address of the same wallet.
The fee list starts with the cheapest, the summary mentions the privacy cost
(the coins are linked afterwards), and the signer's screen shows MERGE n
COINS INTO 1 / YOU KEEP. Coin rows now show their address.

**App mode.** `deploy/install.sh --app` (the default when M5Stack's launcher
is installed) puts an Insert Coin icon on the CardputerZero home screen.
Each session starts in Vault mode; SETUP › EXIT TO MENU (also on the welcome
screen, and HOME on the lock screen) locks, stops every wallet service and
puts Wi-Fi and the backlight back. The desktop's PipeWire is paused for the
session so the wallet has the speaker. See SECURITY.md › App mode for what
it gives up compared with a dedicated install.

**Dedicated mode** masks M5Stack's launcher and desktop audio properly
instead of fighting them.

**Wallet image.** `tools/build_image.sh` and the *Build wallet image*
workflow turn M5Stack's CardputerZero OS into a burnable Insert Coin image
(dedicated mode), published as a draft release with its SHA-256.

**Checks.** 416 tests; fuzzer 5,000 rounds plus new targets (server strings,
the keymap file, power readings), which found and fixed non-UTF-8 crashes in
two file readers; monkey tester 16 x 2,500 keys; leak checker clean.

## 0.8.21: first night on a real Cardputer Zero

Tested on M5Stack's Debian 13 image. Everything below came from using it.

**Fixes found on the device.** The panel stayed black: Pillow 12 removed the
RGB565 packer (own conversion now) and M5's launcher leaves the backlight at
0 (turned up at start). Sound plays through PipeWire, with a short lead-in so
the speaker wakes in time. Wi-Fi joins through explicit NetworkManager
profiles (its `wifi connect` fails on this image). The keyboard driver does
the sym layer itself; those codes are read now. The camera picture is turned
the right way up. Plain messages instead of tracebacks for Ctrl+C, a busy
keyboard and a missing Tor.

**New.**
- Every start is in Vault mode until you choose Online.
- SETUP › NETWORK shows the Wi-Fi network you're on.
- The battery shows a lightning bolt while charging.
- SCREEN SLEEP (DISPLAY & SOUND): screen off after 2 minutes idle, or 5, 10,
  15, custom, never. The waking key does nothing else.
- FEE FROM AMOUNT?: when the amount fits but amount + fee doesn't, take the
  fee out of the amount instead of failing.
- Your own node: the Wi-Fi icon is green on a server you set, blue on the
  public default; home servers (umbrel.local, 192.168.x.x) are reached
  directly instead of through Tor; SERVER scans your node's QR (TAB).
- Menus: only UP/DOWN move the cursor; WALLETS marks the open one IN USE.

## 0.8.20: wipe PIN, login countdown, spending policy

**Keyboard matched to a production Cardputer Zero.** The arrows are printed
on **F**▲ Z◀ X▼ C▶, not D, so UP is now F (and fn+F in text fields). The
sym layer now follows the real keycaps: Q~ W` E_ R- T+ Y= U[ I] O{ P}
A; S: D' G" H< J> K\ L| V, B. N/ M?. The booklet's controls page shows the
real layout, with the HOME button left of the screen and TAB on the right.

All in SETUP › SECURITY, all set on the signer's gold screen with your PIN.

**WIPE PIN.** A second PIN that, entered at unlock, erases everything (the
seed, settings, the spending policy, paper wallets, history exports) and the
device starts like new: title screen, then welcome. Nothing on screen says a
wipe happened. The vault file looks the same with or without one.

**LOGIN COUNTDOWN.** After a correct PIN the wallet waits before it opens:
5, 15, 30 or 60 minutes, CUSTOM (1 to 1440), or OFF. The signer enforces it;
no key skips it; HOME gives up. Any change needs your PIN.

**SPENDING POLICY.** Rules the signer checks before every payment, in Vault
and Online mode, each optional:
- PER PAYMENT and PER DAY limits (fee bumps aren't counted twice)
- COOLDOWN between payments (5 choices or custom; fee bumps exempt)
- ALLOWED ADDRESSES: add by QR or typing, turn the allow-list on or off
- PHONE 2FA: a 6-digit code from an authenticator app, for every payment or
  only above an amount you choose (needs the clock set: go Online once)
- MAX FEE: your own fee ceiling (default 1,000 sat/vB)
- BYPASS PIN: locks the policy. Changing it then needs the bypass PIN and
  your PIN, and seed words, SeedQR, split backups and child seeds can't be
  shown until it's removed, so a thief who learns your PIN still can't get
  around the rules. Wrong bypass PINs count as wrong PINs.

The online daily cap is still there, renamed ONLINE CAP.

Also in this release, from a full bug, leak and attack round (see
docs/AUDIT.md, 0.8.20 addendum): WIPE DEVICE and ten wrong PINs now clear
the app's data too; PSBTs over 500 inputs or outputs are refused; fee bumps
show REPLACE TX in Vault mode too; a crash on the welcome screen after a wipe
in the simulator is fixed; new `tools/leakcheck.py` and attack tests.

## 0.8.19: print paper wallets on your Wi-Fi printer

TOOLS › PAPER WALLETS is now a menu: **MAKE NEW** (as before) and **PRINT**,
which sends a paper wallet straight to a printer on your home network, no
computer in between. Most printers from the last decade work (IPP
Everywhere / AirPrint, no driver needed).

1. Pick the paper wallet (by its address).
2. Pick a printer found on your network, or TYPE ADDRESS (its IP).
3. The first time, compare the printer's fingerprint and TRUST it.
4. A warning: this sends a private key to that printer. PRINT or CANCEL.

Safety rules:
- Online mode only, and **local network only**: printers are reached by
  local IP address, never by name, so no job can go to the internet or a
  cloud print service. The device's firewall enforces the same.
- **Encrypted printing only (IPPS).** Printers that only take unencrypted
  jobs are listed as NO TLS and refused.
- The printer's certificate is **pinned** when you trust it. If it changes,
  nothing is sent and you're told.
- Printing runs in its own locked-down service (`czprint`) that can read the
  paper folder; the app never sees the key.

Pages print as a 300 dpi JPEG (or the PDF, for printers that only take PDF).
`tools/fake_printer.py` is a pretend printer for trying it in the simulator.

## 0.8.18: SETUP in groups

SETUP was one list of 28 items; it's now six groups plus three direct items:

- **SECURITY**: CHANGE PIN, AUTO LOCK, SPEND CAP, DURESS PIN, HARDWARE, LOCK NOW
- **BACKUP**: SHOW SEED WORDS, SEEDQR BACKUP, SPLIT BACKUP, CARD BACKUP
- **NETWORK**: WI-FI, SERVER, TOR, EXPLORER
- **FILES**: SHARE FILES, EXPORT HISTORY, EXPORT LABELS, EXPORT WATCH-ONLY
- **TOOLS**: PAPER WALLETS, CHILD SEEDS, WALLETS
- **DISPLAY & SOUND**: APPEARANCE, SOUND, QR FORMAT
- **UPDATE**, **ABOUT**, and **WIPE DEVICE** last, below the fold.

Finishing something in a group (a PIN prompt, a new server) brings you back
to that group with the cursor where it was. The layout lives in
`SETUP_GROUPS` in `ui/flow.py`, so moving an item is a one-line change.

## 0.8.17: AUTO LOCK, and sharing keeps the wallet awake

- **SETUP › AUTO LOCK**: 2, 5, 10 or 15 minutes, CUSTOM (any number of
  minutes from 1 to 1440) or OFF. It's how long with no key pressed before
  the wallet locks and the signer forgets the seed. The signer keeps and
  enforces the setting: a shorter time applies straight away, a longer one
  (or OFF) asks for your PIN on the signer's screen. OFF means it stays
  unlocked until LOCK NOW or a restart.
- **Every key counts**: the signer now treats any key press as "in use",
  not only keys on its own screens, so you're no longer asked for the PIN
  after two minutes of busy menu use. A seed that already timed out is never
  brought back by a key press.
- **SHARE FILES keeps the wallet awake**: while the sharing screen is up,
  AUTO LOCK waits, so only the time limit you picked ends the share. The
  idle count restarts when sharing stops.

## 0.8.16: one SOUND switch

SETUP › SOUND is now a single ON/OFF switch (LEFT/RIGHT or OK). OFF silences
everything: the title screen's fanfare and the menu boop. It's saved, so it
stays the way you left it after a restart. Turning it back on plays one boop.
The title screen always plays FANFARE; the other coin sounds and the separate
MENU SOUND setting are gone.

## 0.8.15: security review fixes

A full internal review (`docs/AUDIT.md`), with a regression test for each
fix in `tests/test_audit.py`:

- The signer now checks every SegWit v0 input against its full previous
  transaction, closing the SegWit fee attack.
- Signing needs the exact PSBT you confirmed, not only the same transaction.
- Moving the clock can't refill the online spend cap.
- Paper wallets can't be shared behind the default SHARE password.
- Change sent to a far-away address gets a warning; odd signature types
  are refused up front.
- The Electrum client caps reply size, so a hostile server can't exhaust
  the relay's memory.

- Paper wallets no longer go on the card, where any program could read
  them. They're kept in a folder only SHARE FILES can read: download them
  there (PAPER WALLETS: INCLUDE, with your own password). Any left on the
  card by an older version are moved off it.

## 0.8.14: menu sound back on by default

The menu boop is **on by default** again, to match the title screen's sound.
SETUP › MENU SOUND still turns it off, and SETUP › SOUND OFF silences
everything.

## 0.8.13: the FANFARE title sound

Pressing OK on the title screen now plays **FANFARE**, a chiptune treasure
fanfare: "da-da-da-DAAA" on two square-wave voices with a triangle bass and
noise hats, like an old console's sound chip, the last note holding with a
wobble. An original tune made in `ui/sound.py`, nothing sampled. It's the new
default; ARCADE, CHUTE, 8-BIT, KA-CHUNK, CREDIT and ARPEGGIO are all still in
SETUP › SOUND (your saved choice is kept, so pick FANFARE there if you've
already set one).

The title screen now makes its sound in the background while it's showing, so
OK plays it straight away on the device too.

## 0.8.12: ARCADE coin-up, menu sound off by default

New default title sound, **ARCADE**: the punch of the coin and a low thump,
then a quick run up a C major chord (C, E, G) landing on a ringing high C with
an E under it. Sweet FM bells with every overtone in tune, muffled as if heard
from inside the cabinet, with a bit of cabinet echo, in the spirit of 90s
fighting-game coin-ups. An original sound made in `ui/sound.py`, nothing
sampled. CHUTE, 8-BIT,
KA-CHUNK, CREDIT and ARPEGGIO are all still in SETUP › SOUND.

The menu boop is now **off by default**; turn it on in SETUP › MENU SOUND.

Already set up? Your saved choice stays as it was: pick ARCADE in SETUP ›
SOUND to hear the new one.

## 0.8.11: menu sounds

Moving through menus now plays one short, low note (35 ms) each time the
cursor moves, and the same note for OK and BACK/HOME. It's a single steady note with a gentle start (no click at the
front), at under half the coin sound's level. Holding a key down repeats at most one every 40 ms.

SETUP › MENU SOUND turns them ON or OFF; SETUP › SOUND OFF silences
everything. The signer's own screens (PIN, seed words) stay silent, typing is
silent, and changing SETUP › SOUND plays only the coin sound you land on.

## 0.8.10: coin sounds

The title screen's sound is now a coin dropping down the chute: it hits the
slot, bounces, rattles down the metal chute and lands in the coin box. SETUP ›
SOUND picks it with LEFT/RIGHT, playing each as you land on it:
**CHUTE** (the default until 0.8.12), **8-BIT** (the chute through an old 4-bit sound chip),
**KA-CHUNK** (the coin mech's latch accepting the coin), **CREDIT** (a two-tone
credit blip), **ARPEGGIO** (the original 0.8.2 Insert Coin chime, sample for sample), or
**OFF**. All synthesized in `ui/sound.py`, nothing sampled or copied from any
game. The first four are evened out to the same loudness; ARPEGGIO keeps its
original level so it sounds exactly as it first shipped.

## 0.8.9: the real Cardputer Zero keyboard

Checked against a photo of the device: the up arrow is printed on **D**
(not F), so menus move with **D Z X C**, and **fn + D Z X C** moves the text
cursor. The symbol layer now matches the keycaps: Q~ W` E+ R- T/ Y\ U{ I}
O[ P] F| G= H: J; K_ L? V< B> N' M" and , gives `.`. The Cardputer Zero has no
ESC key: its **HOME** button (beside the screen) goes home, so on-screen hints
say HOME (Esc still works on a PC). The instruction booklet's Controls page
shows the real layout.

## 0.8.8: share files over Wi-Fi

SETUP › SHARE FILES (`core/share.py`, `ui/share_flow.py`) puts a small web
page on your local network, like M5Launcher's file manager, for downloading
history CSVs and (only if you choose INCLUDE) paper wallets. Scan the QR on
the screen with a phone, or type the address (`https://192.168.x.x:8443`).

- Login **admin / bitcoin** by default; change it under PASSWORD (6+
  characters; empty puts the default back). 5 wrong tries lock the page for
  30 s, doubling each time. Stored as a PBKDF2 hash.
- Download only: no uploads at all. Paper wallets can be deleted from the
  page once saved; nothing else can be changed.
- Starts only from that screen, only in Online mode, and stops when you leave
  it or after the TIME LIMIT (5/10/15/30 min), whichever is first.
- Local network only: public internet addresses are refused by the server
  and, on the device, by the kernel (systemd `IPAddressAllow`); it never
  opens ports on your router.
- HTTPS with the device's own certificate. Browsers warn the first time;
  compare the FINGERPRINT on the wallet's screen.
- On the device it's its own service (`deploy/czshare.service`) and user
  (`czshare`) that can see the two export folders and nothing else; the app
  itself still has no network access and starts/stops it through the root
  helper `cz-share`. In the simulator it runs inside the app; on Windows,
  allow Python through the firewall on private networks when asked.

## 0.8.7: paper wallets

SETUP › PAPER WALLETS works like COLDCARD's (Advanced/Tools › Paper Wallets):
a brand-new private key that has **nothing to do with your seed**, saved to
the card as

- `<address>.txt`: deposit address, private key (WIF and 32-byte hex), a
  Bitcoin Core `importdescriptors` command (with checksum), and both as QR
  codes drawn with Unicode block characters (needs UTF-8 and a white
  background);
- `<address>.pdf` (PRINTABLE PDF on/off): one letter-size page, deposit QR on
  top, a fold line, the private key QR below, in Insert Coin's pixel font.
  No template tool needed.

Options: ADDRESS TYPE (SegWit `bc1q`/`tb1q` or classic `1`/`m`/`n`), USE DICE
(99+ rolls; the key is SHA-256 of the rolls as typed, so another tool can
check it), GENERATE WALLET (kernel randomness plus the secure chip's, if
connected). Make as many as you like, then DONE.

Sir Signer makes the key and writes the files into his own folder; the app
only ever learns the address. `cz-backup paper` then moves them to
`/var/lib/czwallet/paper/`, readable only by root and SHARE FILES (since
0.8.15; earlier versions put them on the card), and deletes the signer's
copies. In the simulator they stay in `simdata/signer/paper/`.
Code: `core/paper.py`, `ui/secure_flows.py` (paper). For small amounts only.

## 0.8.6: track transactions

- **TRACK IT**: HISTORY › a transaction › SHOW TXID now shows a QR of the
  transaction's mempool.space link (scan it with a phone and the page opens),
  with the TXID written out; LEFT/RIGHT or TAB switches to a QR of just the
  TXID. Links follow the network (mainnet, `/testnet4`, `/signet`).
- **Export history**: HISTORY › SAVE ALL TO CARD (or SETUP › EXPORT HISTORY)
  saves every transaction as a CSV spreadsheet: date, status, amount in sats
  and BTC, fee, TXID, label and link (`core/history_export.py`). On the device
  the root helper copies it to `InsertCoin-exports/` on the card's
  PC-readable side (the ten newest per wallet); in the simulator it lands in
  `simdata/app/exports/`. No keys, but it lists every payment, so the wallet
  asks first. Labels can't sneak spreadsheet formulas in.
- **EXPLORER** (SETUP): point links at your own mempool server, e.g. over Tor
  (`http://xxxx.onion`). Empty = mempool.space.

## 0.8.5: box color presets

BOX COLOR is now one setting: flip through nine ready-made colors with
LEFT/RIGHT, or pick CUSTOM and press OK for the RGB sliders. See 0.8.3.

## 0.8.4: settings change in place

On/off and pick-one settings (SETUP: TOR, QR FORMAT, SOUND; APPEARANCE:
BALANCE DIGITS; WELCOME: NETWORK) now change with LEFT/RIGHT (or OK) and the
cursor stays on them. The selected one shows `< VALUE >`. Coming back to SETUP
from editing the server, the spend cap or the duress PIN also returns to that
item instead of the top, and menus refresh their values when you come back.

## 0.8.3: appearance

SETUP › APPEARANCE (`ui/appearance.py`):

- **Balance digits**: ALL BIG (`0.02150000`, the default) or SMALL SATS
  (the last three digits small and dim, as before).
- **Box color**: LEFT/RIGHT flips through ready-made colors, applied as you
  go: CLASSIC, EMERALD, GRAPE, RUBY, SUNSET, TEAL, ROSE, SLATE, MIDNIGHT, then
  CUSTOM. OK on CUSTOM opens red, green and blue sliders that recolor the
  boxes as you slide (UP/DOWN picks a color, LEFT/RIGHT moves it by 8,
  PAGE UP/DOWN by 32; OK keeps it, DEL or ESC puts it back). Your custom mix
  is remembered while you try the presets. Colors too bright for white text
  are toned down automatically. (0.8.5)
  Sir Signer's screens (PIN, words, confirm) always keep the classic blue,
  on the device and in the simulator, so his screens stay recognisable.

Also: the COINS list says PENDING, like HISTORY, instead of PEND.

## 0.8.2: Insert Coin

The project has a name. On screen: the animated title screen at boot
(`ui/title.py`), an original synthesized coin-up chime when you press OK
(`ui/sound.py`, played with `aplay` on the device's 1 W speaker, or through
the PC speakers in the simulator), and a SOUND switch in SETUP. The name is
also on the ABOUT screen, the simulator window and the service descriptions.

Unchanged on purpose: the internal names (`czwallet` folders, users,
services, update files and the labels mixed into backup and storage keys).
Renaming those would break existing installs, backups and saved data for no
visible gain.

## 0.8.1: bug hunt

Three new test tools, which now run alongside the unit tests:

- `tools/fuzz.py` throws damaged and hostile data at every parser that reads
  outside input (QR codes, UR and BBQr animations, descriptors, card replies).
- `tools/monkey.py` drives the whole app with random key presses (30 runs x
  2,500 keys reached 104 screens).
- `tools/crawl.py` opens every menu item a few levels deep.

They found, and 0.8.1 fixes:

- Six kinds of junk QR code that crashed the app instead of showing an error:
  a broken PSBT in base64 or hex, a BBQr frame with a negative number, a
  Lightning invoice inside an animated code, UR parts claiming absurd sizes
  (one made the app try to allocate gigabytes), and deeply nested CBOR.
- A garbled tap-card reply gave a confusing error.
- Amounts typed in BTC used floating-point maths; now exact, and more than
  8 decimals is refused.
- A menu whose list shrank while open could crash on OK.
- A PSBT missing its previous outputs gave a technical error; now a plain refusal.
- Wrong addresses now say what's wrong: another coin (Ethereum, Litecoin,
  Bitcoin Cash, Dogecoin, Tron, Cardano, Dash, Lightning), the other Bitcoin
  network (mainnet vs signet/testnet), or a typo (bad checksum). Legacy
  Bitcoin Cash and Bitcoin SV addresses look exactly like old Bitcoin `1...`
  ones and can't be told apart by any wallet.
- Crash net: any remaining bug in a screen now logs to `crash.log`, shows
  "OOPS" and returns home instead of quitting (repeated crashes still restart).

Also on Windows: the simulator now starts (it imported a Unix-only feature),
saves the seed file as binary, and runs in UTF-8 mode.

---

## Before 0.8: how it was built

The wallet was built in phases before the first numbered release. Kept here for reviewers who want to know why things are the way they are.

### Features (phase 1)

- Create (12/24 words, optional dice) or restore, with a backup check
- Optional BIP39 passphrase, asked every unlock or stored encrypted
- PIN with hearts for tries; wipes after 10 wrong tries
- BIP84 native SegWit, mainnet / testnet4 / signet / regtest
- Receive with QR, fresh-address on demand, gap-limit scanning
- Send with fee presets from the server or custom sat/vB, send-max,
  coin control (COINS screen), RBF enabled
- History with confirmations, coin list, Electrum server and Tor settings
- Show seed words (PIN), change PIN, lock, wipe

### Features (phase 2: camera and QR)

- **Q** on the home screen scans anything: an address or `bitcoin:` payment
  request starts a send (amount filled in), a PSBT goes to signing, a signed
  transaction can be broadcast
- **TAB** on the SEND TO screen scans an address instead of typing it
- Animated QR in and out using **BBQr**, read in any order, with a part counter
- **Air-gapped signing with Sparrow:** SETUP > EXPORT WATCH-ONLY shows the
  descriptor (Z/C switches to xpub). Sparrow builds the transaction and shows
  it as BBQr, you scan it with Q, check it on the confirm screen, sign, and
  Sparrow scans the signed PSBT back off the screen
- Sending in Vault mode now also shows the signed transaction as a QR, so any
  online wallet can broadcast it

In Sparrow, turn on BBQr for animated QRs (it uses UR by default). UR codes
aren't supported yet; the scanner says so if it sees one.

### Features (phase 3: fee bumps, labels, simulator)

- **Bump fee / cancel (RBF):** HISTORY > pick an unconfirmed payment.
  BUMP FEE keeps the payment and raises the fee (adding a coin if needed);
  CANCEL TX replaces it with one that pays everything back to you. Works in
  Vault mode too: the outbox copy is swapped for the new one. The signer
  credits a replacement against the online spend cap, since only one of the
  two can ever confirm.
- **Labels (BIP329 style):** L on the receive screen labels an address;
  LABEL on a transaction labels it; a `label=` in a scanned payment request
  is saved for you. History shows labels, and they follow a fee bump.
  (`WalletService.export_labels()` gives BIP329 JSON Lines; a way to get
  them off the device comes later.)
- **Desktop simulator** (`sim.py`): see below.

### Features (phase 4: UR, duress, backups)

- **UR codes** (Sparrow's default animated QR) read and written, including
  the fountain-coded parts, so frames the camera misses are recovered from
  later ones. Checked against the spec's test vectors. SETUP > QR FORMAT
  picks UR (default) or BBQr for signed PSBTs; scanning accepts both.
- **Label export:** SETUP > EXPORT LABELS shows BIP329 labels as an
  animated QR.
- **Duress PIN** (SETUP > DURESS PIN): a second PIN that opens a decoy
  wallet, optionally also erasing the real seed. The vault file always has
  two identical-looking slots, so nobody can tell from the file whether a
  duress PIN exists; one Argon2 run checks both. Inside the decoy, the
  settings show no duress PIN and setting one changes nothing real. Put a
  small amount in the decoy so it looks used.
- **SeedQR backup** (SETUP > SEEDQR BACKUP): the seed as a SeedSigner
  Standard SeedQR with guide lines to copy onto a SeedQR card, and
  RESTORE > SCAN SEEDQR to restore from one.
- **Encrypted history:** the wallet's history file is encrypted with a key
  only available after unlocking, so a copied SD card shows no balances,
  addresses or labels, and not how many wallets the device holds.

### Features (phase 5: multisig)

- **k-of-n multisig** (`wsh(sortedmulti(...))`, BIP48 keys): the format
  Sparrow, Coldcard, SeedSigner, Keystone and Nunchuk share.
- **W** on the home screen (or SETUP > WALLETS) switches between the
  single-sig wallet and any multisig wallets; the home screen shows which.
- **NEW MULTISIG**: pick 2-of-3, 2-of-2 or 3-of-5, scan each cosigner's
  key QR, check the fingerprints, name it. The device then shows the
  descriptor to load into Sparrow and every cosigner. **Back it up.**
- **IMPORT DESCRIPTOR**: scan one exported from Sparrow (apostrophes,
  `/0/*` and checksums are all accepted).
- **MY COSIGNER KEY**: this device's `[fingerprint/48'/c'/0'/2']xpub` as a QR.
- **Spending**: build and sign on the device; it shows the half-signed PSBT
  ("Signed 1 of 2") as an animated QR for a cosigner, and Q scans their
  signature back. With enough signatures it broadcasts (Online) or goes to
  the outbox (Vault). The pending PSBT survives a restart.
- PSBTs made elsewhere (Sparrow as coordinator) are signed the usual way:
  Q, check, sign, show QR.

How the signer stays safe with multisig:
- Registered wallets live in a file encrypted with a key derived from the
  seed, so the decoy (duress) seed sees only its own and the file shows
  nothing.
- Every multisig input is checked by re-deriving the whole registered
  policy; an unregistered wallet's PSBT is refused.
- "Change" only counts as change inside the same wallet as the inputs.
  Moving single-sig coins into a multisig is a payment, so the online spend
  cap applies.

### Features (phase 6: hardware add-ons)

SETUP > HARDWARE shows what's plugged in, and the status bar shows an icon
for each detected add-on:

| Icon | Meaning |
|---|---|
| shield, gold | secure chip in use: the seed needs it to unlock |
| shield, grey | chip detected, not in use yet |
| shield, red | the seed needs the chip, but it's missing |
| fingerprint, cyan | fingerprint quick unlock is on |
| fingerprint, grey | sensor detected, quick unlock off |

**Secure chip: M5Stack Unit ID (ATECC608B Trust&GO).** USE IT binds the
vault to the chip: the key becomes Argon2(PIN) mixed with the chip's ECDH on
a point derived from that PIN. A copied SD card is useless without this
exact chip, and every PIN guess has to go through it (about 20 a second).
Use an 8+ character PIN with it. If the chip dies, restore from your seed
words. Binding clears the duress PIN (set it again after).

**Blank ATECC608 chips** are detected and shown. Their mode, where the chip
itself enforces a hard limit on PIN tries, needs a one-time setup that
permanently locks the chip's configuration, so it's being finished and
tested on a real chip before it's switched on.

**Fingerprint: M5Stack Unit Fingerprint2.** ADD A FINGER (press and lift 3
times), then QUICK UNLOCK for 1, 4 or 8 hours. After a PIN unlock, L locks
just the screen and a finger reopens it; the signer keeps the seed for that
window. After the window, a reboot, or SETUP > LOCK NOW, the PIN is needed
again. The sensor only reports "match", so a finger is a convenience, never
a key.

Wiring: the fingerprint unit goes in the Grove port (UART mode, set at
boot). I2C units (secure chip, NFC) go on the EXT header's I2C pins
(8 = SDA, 10 = SCL) with a Grove-to-jumper cable, or are soldered inside.
`sudo python3 tools/hwtest.py` checks both read-only and prints what it
finds. Simulator: `python3 sim.py --with-chip --with-finger` (hold F2 as
your finger).

### Features (phase 7: inheritance wallets)

An inheritance wallet has two keys: the **owner** can spend any time; the
**heir** can spend a coin only once it has sat unmoved for a set time.

```
wsh(or_d(pk(OWNER/<0;1>/*),and_v(v:pkh(HEIR/<0;1>/*),older(N))))
```

The same template Liana uses, so the heir can also use Liana. The device
can play either role.

- **W > NEW INHERITANCE**: pick I'M THE OWNER or I'M THE HEIR, scan the
  other person's key QR (MY COSIGNER KEY on their device, or an xpub with
  its origin), pick the delay (3 months to 15 months; on test networks also
  10 blocks and 1 day), check the fingerprints, name it. Then give the heir
  a copy of the descriptor (SHOW DESCRIPTOR). **Both of you need it.**
- **The timer runs per coin**, from the block it confirmed in. The home
  screen shows it: as owner, a gold "REFRESH SOON" a month before the heir
  could spend and a red warning once they can; as heir, when the next coin
  unlocks and how much already has.
- **REFRESH TIMER** (owner, from the wallet's menu under W) moves every coin
  back into the same wallet, which restarts the timer. The signer sees it as
  a payment of 0 to someone else, so the online spend cap doesn't block it.
- **The heir's send** only uses unlocked coins and sets each input's
  sequence to the delay, which the script checks (BIP68/112). Offline, it
  uses the last known block height; it asks you to go Online once if it
  has none.
- The longest delay is 65,535 blocks (about 15 months), a limit of
  Bitcoin's relative timelocks.

Every spend in the tests (single-sig, multisig, owner, heir, refresh) is
run through a small Bitcoin script interpreter (`tests/script_vm.py`) that
checks the signatures and the timelock against the actual transaction, and
an heir spend with a sequence one block too short is shown to fail.

### Features (phase 8: split backups)

- **SETUP > SPLIT BACKUP** (PIN): your seed as 2-of-3, 3-of-5 or 2-of-2
  SLIP39 shares (Trezor's Shamir backup format, using their MIT-licensed
  reference library). Any K papers rebuild the wallet; fewer reveal
  nothing. The device shows each share (20 words for a 12-word seed, 33 for
  24), then checks two random words from every paper.
- **RESTORE > SPLIT SHARES**: type shares one at a time; the device reads
  how many are needed from the first, refuses a repeated paper or one from
  a different split, and rebuilds your original words.
- What's split is the entropy behind your BIP39 words, so the shares give
  back exactly those words. A SLIP39 wallet such as a Trezor would open a
  *different* wallet from them; to use the shares elsewhere, recover with
  Trezor's `shamir recover` tool and turn the result back into BIP39 words.
- Tested against all 45 official SLIP39 test vectors, and every K-subset of
  shares is checked to rebuild the seed.

Word entry, everywhere seeds or shares are typed: type 4 letters and press
SPACE (or OK/TAB). A word typed in full is taken at once unless a longer
word starts with it ("act" waits, since "action" exists). The last word
always waits for OK.

### Phase 9: hardening

**Networking moved out of the app.** A third service, `cznet`
(`core/netd.py`), is now the only thing that talks to the internet: Tor,
TLS and the Electrum connection. It has no keys, no screen, no keyboard and
can't read the wallet's files. The app itself has no IP networking at all
and reaches the Electrum server only through cznet, which relays just the
seven Electrum calls the wallet uses, with checked parameters, and only for
the app's user. A bug in the network-facing code now lands somewhere with
nothing to steal.

**Reproducible, signed releases and offline updates.**

```
python3 tools/release.py keygen     # once, on your PC: Ed25519 key, passphrase-protected
python3 tools/release.py build      # dist/czwallet-X.Y.Z.tar.gz, same SHA-256 every time
python3 tools/release.py sign       # dist/czwallet-X.Y.Z.tar.gz.sig
```

Put your public key in `/etc/czwallet/release_keys.txt` on the device.
To update, copy the `.tar.gz` and `.sig` into a `czwallet-update` folder on
the microSD boot partition (or a USB stick) and open SETUP > UPDATE. It
shows the version, the signing key and the SHA-256 (anyone can rebuild the
release from source and compare). A root helper re-checks everything itself,
unpacks only plain files, and switches `/opt/czwallet` to the new release;
the previous one stays for rollback. Older versions and releases that need
new Python packages are refused.

**[SECURITY.md](SECURITY.md)**: the threat model for reviewers: what each
process holds, what breaking each one gives an attacker, and the known gaps.
At the time the biggest gap was that the app read the PIN and drew the
confirm screen. Phase 10 closed it.

### Phase 10: the trusted path

**The signer now owns the screen and keyboard.** The app draws its menus
as before, but sends each frame to the signer, which puts it on the panel,
and gets your key presses from the signer. Whenever the signer needs you,
it takes over: the app's frames stop being shown and your keys stop
reaching it. That covers:

- **PIN entry**: unlocking, and every setting that needs the PIN (spend
  cap, change PIN, duress PIN, secure chip, quick unlock, wipe);
- **seeds**: new wallets (words made, shown and checked there, dice rolls
  too), restoring from words or split shares, the passphrase, showing your
  words, SeedQR and split backups;
- **confirming**: every signature (sends, fee bumps, cancels, inheritance
  refreshes, PSBTs from other wallets, multisig) and registering a multisig
  or inheritance wallet;
- **checking a receive address**: R then **V** shows the address the
  signer derived from your keys; if the app showed a different one, you
  get a red MISMATCH warning.

How to tell it's the signer:

- **The gold shield in the top-left corner.** The signer draws that corner
  on every frame, last, over whatever the app drew: gold while it has the
  screen, blank otherwise.
- **Your security words** on every PIN screen: two words made for this
  device and kept in the signer's folder, which the app can't read. You see
  them when you set your PIN. A PIN screen without them isn't the signer's:
  don't type your PIN there.

What this changes: the app has no way to ask for or receive your PIN, seed
words or passphrase, and can't fake the screen you say YES on. The signer's
whole request list is in `core/trusted.py`. The one seed that still passes
through the app is one scanned from a SeedQR (the camera belongs to the
app); the signer says so when you use it.

On the device the panel gets its own group (`deploy/60-czwallet-screen.rules`)
and only the signer is in it and in `input`.

### Phase 11: the QR sandbox

Reading a QR code means running camera pixels through zbar (C) and
libcamera (C++). A booby-trapped QR that exploited either would run code
in whatever process did the decoding, which used to be the app. Now it's
**czscan** (`core/scand.py`), a fourth service with nothing in it worth
having: its own empty network namespace, no wallet, signer or update
files, no screen or keyboard, a syscall filter, and only the camera
devices. The app no longer has the camera at all (it left the `video`
group).

The app asks czscan to start or stop, and gets back two things in fixed
shapes: a 132x99 grayscale preview and the decoded text (UTF-8, 8 KB at
most). Anything else ends the session. The camera is only open while a
scan screen is up. A compromised czscan can make the app "scan" any text,
which a QR code could do anyway; a scanned PSBT still needs your YES on the
signer's screen.

In the simulator and tests the camera runs in-process as before; the
sandbox wire has its own tests (`tests/test_scan.py`).

#### Seed randomness

A new seed hashes together every source the device has (`new_mnemonic` in
`core/seed.py`), each under its own label:

- **Linux's random number generator**, always: 32 bytes from `getrandom`,
  which waits until the kernel's generator is seeded (the Pi's hardware
  RNG and hardware timing noise feed it);
- **camera noise**: czscan hashes four raw full-resolution frames (image
  sensors are noisy even in the dark); skipped if there's no camera or the
  frames come out identical;
- **the secure chip's hardware RNG**, only if a chip is connected (a blank,
  unlocked chip returns a fixed pattern, so it's skipped);
- **anything you add yourself** on the NEW WALLET screen, as many as you
  like before picking 12 or 24 words:
  - **ADD DICE ROLLS**: about 2.6 bits a roll;
  - **ADD KEY MASHING**: type at random; the microsecond timing between
    keys is what counts, credited at 2 bits a key (none for a held key
    auto-repeating);
  - **ADD COIN FLIPS**: H or T, 1 bit each;
  - **ADD CARD SHUFFLE**: type a shuffled deck in order (AS 7H TD...),
    up to about 226 bits for all 52 cards.

The NEW WALLET screen (on the signer) lists the device's sources and the
bits you added. Because everything is hashed together, the seed is at least
as strong as the best source: a broken or hostile one can't weaken the
others.

**DICE ONLY** (advanced) is the exception: the seed comes from your rolls
alone (50+ for 12 words, 99+ for 24), so you can check it elsewhere. The
entropy is SHA-256 of the rolls exactly as typed: `printf '%s' 3615... |
sha256sum`, then the first 32 hex characters (12 words) or all 64 (24 words)
in any BIP39 tool that takes entropy. Only as good as your dice and your
honesty: roll, never pick.

### Phase 12: locked dependencies, message signing, child seeds

**Hash-locked packages.** `requirements.lock` pins every Python package
(and their dependencies) to an exact version and the SHA-256 of every file
PyPI has for it. The installer uses `pip --require-hashes`, so a swapped or
tampered package is refused. Wheels only, so nothing runs at install time;
the one source package (embit, pure Python) is built with apt's
setuptools, and evdev now comes from apt. `python3 tools/lock.py`
regenerates the lock when you bump a version in its `PINS`; review the diff.

**Sign a message** (R, then **M**): type or scan a message, pick a format,
and the signer shows the address and the whole message on its own screen
(YES stays disabled until you've scrolled through all of it). You get the
signature as a QR; TAB shows the full signed-message block. Formats:
STANDARD (what Electrum and Sparrow write), BIP137 (Trezor), BIP322 (the
newest; checked against BIP322's own test vectors). Single-sig addresses.

**Child seeds** (SETUP > CHILD SEEDS, BIP85): pick 12 or 24 words and a
child number, enter your PIN, and the signer shows a separate seed for
another wallet, as words or a SeedQR. Your main backup can always
re-create it from the same number; the child reveals nothing about the
main seed. Checked against BIP85's test vectors.

### Phase 13: Taproot and multiple accounts

**W > NEW ACCOUNT** adds another single-sig wallet from the same seed:

- **SAVINGS**: a Taproot account the signer makes **vault-only**. It won't
  sign anything from Savings while any radio is on, so even someone who
  took over the device online can't move those coins. Receive any time;
  to spend, switch to Vault mode. Setting it asks for your PIN on the
  signer's screen.
- **SPENDING**: an everyday Taproot account, online sends allowed (under
  the daily spend cap).
- **CUSTOM TAPROOT** (bc1p..., BIP86) or **CUSTOM SEGWIT** (bc1q..., BIP84):
  name it yourself.

Each takes the next free account number for its type. In WALLETS, an
account's menu has **VAULT ONLY ON/OFF**: either way needs the PIN on the
signer, so the app can't quietly loosen it. Rules are stored by the signer,
encrypted per seed (a duress decoy has its own). Moving coins from Savings
to Spending is an ordinary send done in Vault mode. The WALLETS
list shows MAIN (the original), your accounts, then any multisig or
inheritance wallets; HIDE takes an account off the list without touching
its coins (add the same account again to see them). All accounts come back
from your one backup, and Sparrow or Bitcoin Core find the same addresses
with the same derivation paths.

What works per account: receive (with V to verify on the signer), send,
RBF bump/cancel, coin control, labels, watch-only export (the `tr(...)`
descriptor for Taproot), and message signing (BIP322 only for Taproot;
classic formats can't express a Taproot signature).

On the signer: every input must re-derive to one of its own account keys
(the full BIP84/86 path, the key, and for Taproot the tweaked output key),
all inputs must come from the same account, and change only counts as
change inside that account; sending to another of your own accounts shows
as a payment. Taproot inputs must be key-path only: a PSBT with script
leaves or a merkle root is refused. The confirm screen names the account
(e.g. TAPROOT #0). Fees use 57.5 vB per Taproot input (68 for SegWit), so
Taproot sends are a bit cheaper.

Checked against BIP86's test vectors and BIP322's Taproot vector; every
Taproot spend in the tests is verified with a real Schnorr signature check
against the output key.

### Phase 14: card backup

**SETUP > CARD BACKUP** saves everything about your setup except the seed:
extra accounts and their names, multisig and inheritance wallets (their
descriptors), vault-only rules, every wallet's labels, and settings
(server, Tor, QR format). Not in it: the seed, passphrase, PIN, duress
setup, chip binding, fingerprint and spend cap; history comes back from
the network.

- **Encrypted with a key from your seed** (and passphrase): AES-256-GCM,
  key derived by the signer. Only the same seed opens it, and the same key
  proves nobody edited it. Someone who finds the card learns nothing.
- **Where:** the app's folder, plus a copy in `czwallet-backup/` on the
  card's FAT partition so a PC can read it and you can keep a copy
  elsewhere (`deploy/cz-backup`, a root helper that takes only a checked
  file name and never follows symlinks). The five newest per seed are
  kept. Restore also looks on USB sticks under /media.
- **Restore** (CARD BACKUP > RESTORE BACKUP, newest first) shows on the
  signer's screen what it would bring back, then merges it: it only adds,
  never removes, and can't loosen a rule. Each multisig wallet is
  re-checked and must include this device's key.
- **After restoring from words**, if the card has a backup for that seed,
  the device offers to restore it right away.

### Phase 15: NFC tap cards (the Cap CC1101 & NFC)

Press **T** on the home screen and hold a card to the back.

- **SATSCARD**: shows the current slot (sealed or unsealed), its address
  (proven by the card's signature and matched against what the card
  reports), whether it's a genuine Coinkite card (certificate chain to
  Coinkite's factory key) and, Online, its balance. **SWEEP TO WALLET**
  asks for the 6-digit code on the back, unseals the slot (permanent),
  checks the key really is the slot's, and sends everything to the open
  wallet's next address. If the broadcast fails, the signed sweep stays in
  the outbox. **SET UP NEXT SLOT** readies the next one.
- **TAPSIGNER** as a multisig cosigner: in NEW MULTISIG press **TAB** on
  the scan screen to tap the card instead of scanning a QR; it gives its
  BIP48 key. To spend, this device signs, then **T** > SIGN PENDING TX
  has the card sign (each signature checked against the key the PSBT
  names) and the transaction is finished.

The NFC field is on only while a TAP screen or a card's menu is up. The
card's code goes to the card via ECDH, never in clear. The CC1101 sub-GHz
radio on the same Cap is reset and put to sleep at start and never used;
HARDWARE > NFC + SUB-GHZ shows both, and the status bar shows an NFC icon
(cyan while the field is on).

Built from Coinkite's protocol document and M5Stack's MIT-licensed
ST25R3916 library (register map and start-up sequence). **Unverified on
real hardware**: the card simulators implement the document's card side,
so the tests show this code follows the document, not that real cards
agree; and which SPI bus and chip-selects the Cardputer Zero gives the Cap
is a guess (`CZ_NFC_SPI=0.0`, `CZ_CC1101_SPI=0.1`). Run
`sudo python3 tools/hwtest.py nfc` with a card on the back first; try it
with a SATSCARD holding a few sats before trusting more to it.

Simulator: `python3 sim.py --with-satscard` (or `--with-tapsigner`), F3
puts the card on the reader; its code is 123456.

Next phases from the plan: Lightning.
