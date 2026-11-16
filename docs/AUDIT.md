# Insert Coin security review, November 2026 (0.8.15)

An internal review of the whole wallet ahead of first hardware testing.
This is not a substitute for an outside audit: the same team wrote and
reviewed the code. Each fixed finding has a regression test in
`tests/test_audit.py`.

## What was reviewed

- The signer and vault: seed storage, PIN tries and wipe, duress PIN,
  secure-chip binding, PSBT checks, spend cap, message signing, paper
  wallets (`core/signer.py`, `core/vault.py`).
- The trusted path and process isolation: what the app may ask the signer,
  who draws the screen and reads the keys, the systemd sandboxes, the root
  helpers and sudo rules (`core/trusted.py`, `core/signer_service.py`,
  `ui/console.py`, `deploy/`).
- Everything that reads outside input: Electrum client and relay, QR and
  NFC parsers, signed updates, card backups, the SHARE FILES web server.
- The fuzzer (3,000 rounds over every parser: 0 problems) and the monkey
  tester (12 wallets x 1,500 random key presses, 69 screens: 0 crashes).

## Findings

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| F1 | High | SegWit fee attack: the signer trusted `witness_utxo` amounts | Fixed |
| F2 | Medium | The confirmation covered the transaction, not the whole PSBT | Fixed |
| F3 | Medium | Moving the clock could refill the online spend cap | Fixed |
| F4 | Medium | Paper wallet keys on the card were readable by the app | Fixed |
| F5 | Medium | Paper wallets could be shared behind the default password | Fixed |
| F6 | Low | Change sent to a far-away address index wasn't flagged | Fixed |
| F7 | Low | The app can remove a registered multisig wallet without asking | Noted |
| F8 | Low | Odd signature types were only refused at signing time | Fixed |
| F9 | Low | A hostile Electrum server could exhaust cznet's memory | Fixed |

### F1 SegWit fee attack (High, fixed)

SegWit v0 signatures commit only to their own input's amount. A PSBT that
lies about input amounts can show a small fee, and a second signing of the
same coins with a different lie lets an attacker combine the valid halves
into a transaction with a huge fee (the 2020 issue fixed in Trezor and
others). The signer used the `witness_utxo` amount as given.

Fix: for v0 inputs the signer now requires the full previous transaction,
checks that it hashes to the txid being spent, and refuses if a
`witness_utxo` disagrees with it. Taproot inputs need only `witness_utxo`,
because Taproot signatures commit to every input's amount. The wallet's
own PSBTs already carried the full transactions; Sparrow includes them by
default. A coordinator that leaves them out now gets a clear refusal.

### F2 Confirmation bound to the whole PSBT (Medium, fixed)

`sign` checked only that the unsigned transaction matched what was
confirmed. The app could call `describe` again with the same transaction
but different metadata (coin amounts, key paths), replacing the stored
summary used for the spend cap. Now `sign` also requires the whole PSBT to
be byte-for-byte what was confirmed.

### F3 Spend cap and the clock (Medium, fixed)

The daily cap reset whenever the calendar date changed, in either
direction. Devices set their clock from unauthenticated network time, so
someone on the network could move the date and refill the cap repeatedly.
Now, within one boot, a new day also needs 20 hours to have passed on the
kernel's boot clock, which no time server can change. After a reboot the
date decides, as before.

### F4 Paper wallet keys on the card (Medium, fixed)

Paper wallet files were written by the signer and moved to the card's FAT
side so a PC could read them. FAT has no per-user permissions, so every
process on the device could read them, including the app. A compromised app
could have read the keys and leaked them through the Electrum relay,
breaking the design rule that the app never holds a private key.

Fix: the files now go to `/var/lib/czwallet/paper` (root:czshare, 2770,
files 0640), which only root and the SHARE FILES service can read. You get
them to a PC with SHARE FILES (own password required, see F5). Any left on
the card by an older version are moved in and the card folder removed.

### F5 Default password and paper wallets (Medium, fixed)

SHARE FILES uses admin / bitcoin until changed. That's fine for history
CSVs, but paper wallets are private keys. Including them is now refused
until you set your own password.

### F6 Far-away change (Low, fixed)

Change re-derived correctly but sent to an index like #2,000,000,000 is
still yours, but your wallet would never look there, so the coins would
seem lost. The confirm screen now warns for change or self-sends beyond
index 10,000.

### F7 Removing a multisig wallet (Low, noted)

`remove_multisig` is on the app's no-PIN list. A compromised app could
remove a registered multisig wallet, and the signer would then refuse to
sign for it until you register it again on the signer's screen. No coins
can move this way; it's a nuisance, not a theft. Candidate for a signer
prompt.

### F8 Odd signature types (Low, fixed)

The signing library already refused to sign with SIGHASH_NONE, SINGLE or
ANYONECANPAY, but only at the last step. The confirm screen now refuses
such PSBTs up front with a clear message.

### F9 Electrum replies (Low, fixed)

The Electrum client read replies with no size limit, so a hostile server
could send an endless line and exhaust the relay's memory. Replies are
now capped at 16 MB (the largest real reply is about 8 MB), and non-JSON
or non-object replies are errors instead of crashes.

## What held up

- No request from the app takes a PIN or returns seed words; the signer
  draws its own screens and stamps the corner of every frame.
- Every input and every change output is re-derived from the seed; a
  compromised app can't disguise a payment as change.
- The vault: Argon2id + AES-GCM, both slots look the same, the try counter
  is written before each attempt, the header is authenticated.
- Signed updates: pinned Ed25519 keys, copy before verify, strict unpack,
  no downgrades.
- The root helpers take one checked argument each and open files without
  following symlinks.
- The network relay passes only the Electrum calls the wallet uses, with
  checked parameters.

## Still open from SECURITY.md

The known gaps listed there still apply, notably: a SeedQR restore passes
the seed through the app; the fingerprint match is reported by the app;
Python can't wipe memory; czscan's sandbox is untested on hardware; no
outside audit yet.


---

# Addendum: 0.8.20 review (wipe PIN, countdown, spending policy)

A second full round after adding the wipe PIN, login countdown and spending
policy: the whole test suite (395 tests), the fuzzer (5,000 rounds, now also
covering IPP, mDNS, paper files, print requests and policy values), the
monkey tester (16 wallets x 2,500 random keys, 107 screens), a new
memory-leak checker (`tools/leakcheck.py`: seven long-running loops) and new
attack tests (`tests/test_attacks.py`).

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| F10 | Medium | WIPE DEVICE and ten wrong PINs left the app's data (labels, settings, unsent transactions) and paper wallets behind | Fixed |
| F11 | Low | A huge PSBT could tie the signer up for minutes on the CM0 | Fixed: 500 inputs / 500 outputs |
| F12 | Low | A prompt cancelled before it began could later open a stray signer screen | Fixed |
| F13 | Low | Print service: junk request types gave generic errors | Fixed: strict types |
| F14 | Low | After a wipe in the simulator, the welcome screen crashed on NETWORK | Fixed |
| F15 | Low | A damaged policy file would have silently dropped every rule | Fixed: fails closed |
| F16 | Low | Wipe PIN or a new main PIN could equal the bypass PIN | Fixed |
| F17 | Low | PER DAY counted fee bumps twice, and COOLDOWN blocked them | Fixed |

Attack tests now pin down: nothing outside the app's two lists is reachable
(every Signer method tried by name); junk to every no-PIN request and every
prompt gives a clean error and never hangs; prompts can't be stacked (one at
a time, the rest get Busy); the try counter survives a power cut mid-guess;
every kind of PIN costs the same Argon2 work; tampering with the vault header
never helps (even the right PIN fails); file sizes don't reveal duress or
wipe PINs; ten wrong PINs remove every signer file.

Memory: every loop levels off once warm (the largest, 9,000 random key
presses across every screen, settles under 70 KiB).

Known gaps unchanged from SECURITY.md, plus: the 2FA secret on the card (see
there), and `touch` (used by SHARE FILES and quick unlock) can keep an
unlocked seed in memory if the app is compromised, though it still can't sign
without your YES.
