# Insert Coin security model

This is the document to start from if you're reviewing the code. It says
what each part is trusted with, what an attacker gets from breaking each
part, and what's known to be weak. **The code has not had an outside
review yet.** Until it has, don't keep more on it than you'd keep in a
phone wallet.

## Processes

Five Linux users, four long-running services and three root helpers.

| Process | User | Holds | Network | Files | Talks to |
|---|---|---|---|---|---|
| signer (`core/signer_service.py`) | `czsigner` | the seed, while unlocked; **the screen and keyboard** | **none** (Unix sockets only) | `/var/lib/czwallet/signer` | the app, over `/run/czwallet/signer.sock` and `/run/czwallet/screen.sock` (both uid-checked); panel, keyboard, I2C chip |
| app (`app.py`) | `czapp` | public keys, wallet history, labels | **none** (no IP; Unix + netlink only) | `/var/lib/czwallet/app` | signer, cznet, czscan, UART fingerprint sensor |
| QR scanner (`core/scand.py`) | `czscan` | nothing; sees the camera | **none** (empty network namespace) | none (the wallet folders are made inaccessible) | the app, over `/run/czscan/scan.sock` (uid-checked); the camera |
| network relay (`core/netd.py`) | `cznet` | nothing | Tor and the Electrum server | none (the wallet folders are made inaccessible) | the app, over `/run/cznet/net.sock` (uid-checked) |
| `cz-radio` | root, via sudo | nothing | turns radios on/off | none | takes only `vault` or `online` |
| `cz-update` | root, via sudo | release keys (public) | none | installs releases | takes one path; re-verifies everything itself |
| `cz-backup` | root, via sudo | nothing | none | copies one backup file to the card's FAT side | takes one file name (pattern-checked), opens it without following symlinks, must be a sealed backup |

Boot order: `czwallet-vault` turns all radios off first, so the device
always starts in Vault mode.

## What protects what

**The trusted path** (`ui/console.py`, `ui/secure_flows.py`,
`core/trusted.py`, `core/screen_link.py`): the signer owns the panel (its
own `czscreen` group) and the keyboard (`input`). The app sends it frames
and gets key presses back. Whenever the signer needs you it takes over:
the app's frames aren't shown and keys aren't forwarded until it's done.
PIN entry, the passphrase, new and restored seeds, seed display, every
signature confirmation, PIN-guarded settings and multisig registration all
happen there. The app's request list (`SAFE` and `PROMPTS` in
`core/trusted.py`) has nothing that takes a PIN or returns seed words.
The signer marks its screens two ways the app can't copy: it draws the
top-left corner of every frame last (gold shield while it has the screen,
blank otherwise), and PIN screens show two security words kept in the
signer's folder.

**The seed at rest** (`core/vault.py`): AES-256-GCM, key from Argon2id
(64 MiB, 3 passes) over the PIN. With the secure chip bound
(`core/hw/atecc.py`), the key also mixes in an ECDH the chip computes on a
point derived from the PIN, so every guess needs that physical chip. The
file has two identical-looking slots (real seed, and decoy or random), so it
never shows whether a duress PIN exists. The try counter is written before
each attempt; at the limit the vault is wiped.

**Signing** (`core/signer.py`, `describe` and `sign`; the prompt in
`ui/secure_flows.py: sign`): the signer reads the PSBT itself, builds the
summary and draws the confirm screen. It requires
every input to be its own (a single-sig BIP84 or BIP86 key-path Taproot
account key, or a registered multisig or inheritance policy, each
re-derived in full), all from the same wallet or account, treats an output as change
only if it re-derives to the same wallet's change branch, refuses fees over
1,000 sat/vB, and signs only the exact PSBT that was just confirmed (the
whole PSBT, not only the transaction, since 0.8.15). For SegWit v0 inputs
it requires each input's full previous transaction, checks that it hashes
to the txid being spent and agrees with any `witness_utxo`, so a PSBT
can't hide a fee by lying about input amounts (the 2020 SegWit fee attack;
Taproot signatures commit to every amount, so `witness_utxo` is enough
there). Odd sighash types are refused, and change sent to an address
index beyond 10,000 gets a warning. While any network interface is up, it enforces a daily spend cap
that only the PIN can change (0 by default), and refuses outright to sign
from an account you've made vault-only (e.g. SAVINGS) while any radio is
on; that rule, too, changes only with the PIN on the signer's screen. The
cap's day can't be moved by the clock alone: within one boot a new day
also needs 20 hours on the kernel's boot clock, so a spoofed network time
server can't refill it.

**New seeds** (`core/seed.py: new_mnemonic`): SHA-256 over the kernel's
`getrandom` (always), plus camera-frame noise, the secure chip's RNG and
whatever you add (dice, key timing, coin flips, cards), each labelled.
DICE ONLY deliberately skips the device's sources so the seed can be
checked; it's only as random as the rolls. The camera hash reaches the signer via
czscan and the app, so it's untrusted; like every extra source it can only
add randomness, never remove the kernel's.

**Card backups** (`core/backup.py`): AES-256-GCM with a key the signer
derives from the seed (HMAC of the BIP32 root secret), so only the same
seed and passphrase open one, and the tag also authenticates it. No seed,
PIN or private key inside. Restoring is confirmed on the signer's screen
and only adds: multisig entries are re-validated, rules can only tighten.

**Wallet data** (`core/wallet.py`): encrypted with a key from the public
descriptor, which only exists after unlocking. A copied card shows no
balances, addresses or labels.

**Updates** (`core/update.py`, `tools/apply_update.py`): Ed25519 signatures
from keys pinned in `/etc/czwallet/release_keys.txt`; reproducible
tarballs; downgrades refused; the root helper copies the files before
verifying (no swap mid-check) and unpacks only plain files under the
release's own folder.

## Attackers, and what they get

**Someone on the network, or a malicious Electrum server.** They reach only
`cznet`. They can lie about history (wrong balance, hide a payment, claim
coins that don't exist) and fee rates. They can't make the signer sign
anything you didn't confirm, and a lie about coins only produces
transactions that fail to broadcast. Fee lies are bounded by the
1,000 sat/vB limit and shown on the confirm screen. Use your own node (and
Tor) to remove this.

**A bug in TLS, SOCKS or JSON parsing, exploited (cznet compromised).** The
attacker runs code as `cznet`: no keys, no keyboard, no screen, no wallet
files, no camera. Same powers as a lying server, plus seeing which
addresses you query (it relays them anyway).

**The app compromised.** This is the most important boundary to review.
Since the trusted path, a compromised app:
- never sees your PIN, passphrase or seed words (except a seed you restore
  by scanning a SeedQR, see gaps), and has no request that accepts a PIN;
- can't get a signature without you saying YES on the signer's screen,
  which shows the signer's own reading of the transaction;
- **can** ask the signer to show a prompt at a moment you didn't expect
  (e.g. a confirm screen for a payment to the attacker, hoping you press
  YES out of habit). The screen shows the real destination and amount:
  read it;
- **can** draw a fake PIN screen without the corner shield or your
  security words, hoping you type your PIN into it. That PIN is useless to
  the app itself (nothing it can call takes a PIN), but would help someone
  who later steals the device;
- **can** show you a wrong receive address. Press V on the receive screen
  to have the signer derive and show it;
- **can** keep the signer's quick-unlock window open by calling `touch`
  (it reads the fingerprint sensor), and can lock it or remove a registered
  multisig (denial of service only).
What else makes the app hard to exploit: no IP networking, so it can't
fetch code or send data out except by broadcasting a transaction through
cznet's allowlist; and what reaches it from outside is limited to camera
frames (decoded by zbar, a C library, in the app process), the relay's JSON
replies, files on the SD card or a USB stick (updates are verified by root,
not by the app), the fingerprint sensor's serial replies, and key presses
the signer forwards.

**A crafted QR that exploits zbar or libcamera (czscan compromised).**
The attacker runs code as `czscan`: the camera, and nothing else. No
network (not even loopback to anything), no wallet, signer or update files,
no screen, no keyboard, no keys, and a systemd syscall filter. It can send
the app preview pixels and decoded text, which the app only accepts in
fixed shapes (`RemoteScanner` in `ui/camera.py`; malformed output ends the
session). So it can make the app "scan" any text it likes, exactly what a
QR code in front of the camera could already do, and every such text still
goes through the same checks (a scanned PSBT still needs your YES on the
signer's screen). It can also see what the camera sees while it's open;
the camera is opened only while a scan screen is up.

**A malicious NFC card.** Cards talk to the app process (the reader is on
its SPI bus), through a strict CBOR parser with size and depth limits.
Every claim a card makes that can be checked is: slot keys by their
signatures, an unsealed key against the slot's public key, a TAPSIGNER
signature against the key the PSBT names, genuineness by the certificate
chain. A fake card can still lie about anything unprovable (e.g. that a
slot is sealed) and can refuse to cooperate. The SATSCARD sweep is signed
in the app, since the key is the card's, not your seed's; the TAPSIGNER
only ever adds signatures to a PSBT the signer already approved.

**The signer's screen code.** The signer now runs Pillow and the UI
widgets, so its attack surface grew: it parses key events from evdev and
the app's frames. Frames are a fixed-size raw RGB buffer
(`Image.frombytes`, nothing else is parsed), and the connection is dropped
on anything else. Nothing the app sends is rendered as text on the
signer's screens except the wallet name you typed for a multisig.

**Someone with the device (theft).** Locked: they need the PIN. Ten wrong
tries wipe the vault. Pulling the SD card and guessing offline is limited
only by Argon2 (an estimated 1 to 2 seconds a guess on the CM0, much faster
on a PC; to be measured on the device): a 6-digit PIN falls quickly, an 8+ character mixed PIN doesn't. With
the stock Unit ID chip bound, offline guessing needs the chip too
(an estimated 10 to 20 guesses a second through it, set by the chip's
ECDH speed), and a copied card alone is useless.
A blank ATECC608 configured to enforce the try limit itself would stop even
that; that mode is detected but not finished (it needs testing on a real
chip, since its setup is permanent). An unstored BIP39 passphrase protects
against all of this. The fingerprint is a convenience only: it reopens the
screen while the signer is still unlocked from a PIN entry.

**Someone forcing you to unlock (duress).** The duress PIN opens a decoy
wallet and can erase the real seed. The vault file looks the same either
way. Inside the decoy, settings show no duress PIN.

**A malicious update.** Needs a signing key you trust. Anyone can rebuild
a release from source and compare its SHA-256 with the one on the confirm
screen.

**Supply chain.** Python packages are installed from `requirements.lock`
with `pip --require-hashes`: exact versions, every file's SHA-256, wheels
only (embit, pure Python, is built from its hashed source with apt's
setuptools). A package swapped on PyPI or in transit is refused. What's
left: the hashes are whatever PyPI served when `tools/lock.py` was run, so
a package that was already malicious then is still trusted; and apt
packages (evdev, picamera2, libzbar, Python itself) are trusted via
Debian's signatures.

## Known gaps (worth an issue each)

1. **Trusted-path limits**: a SeedQR restore passes the seed through the
   app (the camera is the app's); the app can show fake PIN screens (no
   shield, no security words) and spring unexpected confirm prompts; the
   fingerprint sensor is read by the app, so quick unlock trusts the app's
   word that a finger matched. The corner shield only helps if you look for
   it. (The confirm screen no longer says "bump fee" or "cancel": the
   signer can't tell a replacement from a new payment while offline, so it
   says what the transaction does.)
2. **czscan's sandbox is untested on the device.** The syscall filter and
   device list (`deploy/czscan.service`) are written for libcamera's usual
   needs; they may need loosening on real hardware, and
   `MemoryDenyWriteExecute` is left off until tried there.
3. **Python can't wipe memory.** The seed and PIN live in Python objects
   until garbage collected; `MemorySwapMax=0` keeps them out of swap.
4. **Only the source tarball is reproducible**, not the virtualenv; and a
   signed update that needs different packages still has to be installed
   with `deploy/install.sh`, not from the UPDATE screen.
5. **Blank-chip mode unfinished** (chip-enforced try limit).
6. **The secure-chip slot choice is inferred from the chip's config** and
   needs confirming on real hardware (`tools/hwtest.py`).
7. **Electrum trust**: no SPV proofs; balances shown are what the server
   says. Signing safety doesn't depend on it, the displayed balance does.
8. **systemd sandboxing could go further**: `MemoryDenyWriteExecute`,
   `SystemCallFilter` for each service, and a separate user for the camera.
9. **Wipe on SD cards is best effort**: flash remaps blocks.
10. **Fee estimation** for multisig and inheritance inputs uses a fixed
    worst-case size per input; slight overpay, never underpay.

## Where to look first

- `core/signer.py`: `describe`, `sign`, `_cost` (spend cap), the
  registry functions. Everything the signer accepts from the app.
- `core/vault.py`: `_key`, `unlock`, `set_duress`, `bind_chip`.
- `core/signer_service.py`: the socket protocol, `run_screen`.
- `core/trusted.py`: `SAFE` and `PROMPTS`, everything the app may ask.
- `ui/console.py` and `ui/secure_flows.py`: the signer's screens; check
  that each prompt validates its arguments and shows only signer-derived
  text. `core/screen_link.py`: the frame/key wire.
- `core/netd.py`: `check_calls`, `check_server`.
- `core/scand.py` and `RemoteScanner` in `ui/camera.py`: the scanner wire.
- `core/update.py`: `verify`, `safe_extract`, `install`;
  `tools/apply_update.py`.
- `core/hw/atecc.py`: `pin_point`, `mix`, `ecdh_slot`.
- `deploy/*.service`: the sandboxing.

## Tests that back these claims

`python3 -m pytest tests` (267 tests, offline). Notable ones:
`test_scan.py` (scanning through the sandbox wire, the camera closed after
each scan, a compromised scanner's malformed output refused);
`test_trusted.py` (the app can't fake the corner, keys on the signer's
screen never reach the app, seed words come back as nothing, address
mismatch caught, the frame link drops garbage);
`test_core.py::test_client_has_no_pin_or_seed_methods`;
`tests/script_vm.py` runs every kind of spend through a small script
interpreter with real signature and CHECKSEQUENCEVERIFY checks;
`test_core.py::test_hidden_payment_disguised_as_change_is_caught`;
`test_hardware.py::test_copied_vault_useless_without_the_chip`;
`test_netd.py` (relay refusals); `test_update.py` (tampered, untrusted,
downgraded and booby-trapped releases); the official SLIP39, UR, bytewords
and BIP84 test vectors.

## Reporting

Until there's a public repository with a security contact, report issues
privately to the maintainer of your build.

## Wipe PIN, login countdown, spending policy (0.8.20)

**Wipe PIN.** The vault header always carries a 32-byte tag. With no wipe PIN
it's random; with one it's an HMAC keyed by the Argon2 key of the wipe PIN.
Unlocking runs Argon2 once, as always; if no slot opens and the tag matches,
the signer erases its whole folder and the app clears its own data and the
paper/export folders (root helper `cz-backup wipe`; encrypted card backups
stay). Wrong, right, duress and wipe PINs all cost exactly one Argon2 run, and
the file's shape doesn't change, so neither timing nor the file reveals one.
The wipe, duress, bypass and main PINs must all differ.

**Login countdown.** After a correct PIN the signer keeps the opened vault in
a pending login and opens the wallet only when its boot clock has run the
full time (a reboot starts over; the wall clock plays no part). finish_login
is not on the app's lists, so the app can't hurry it.

**Spending policy.** Checked in `describe` and again in `sign` (the summary
can't be swapped, see F2). Its file lives in the signer's folder and holds the
2FA secret and an Argon2 hash of the bypass PIN. A damaged file fails closed:
no signing, no seed export. With a bypass PIN set, every seed-revealing path
(words, SeedQR, split shares, child seeds), raising the online cap and turning
vault-only off are refused; wrong bypass PINs count toward the ten tries.
2FA codes allow one step either side and can't be replayed.

Gaps: the 2FA secret sits in the signer's folder, so someone who copies the
card and also cracks your PIN offline gets it too. 2FA needs a set clock:
the CM0 has no clock battery, so after a power loss go Online once.

## PRINT for paper wallets (0.8.19)

A paper wallet can be sent straight to a printer on the local network.
`czprint` (its own user and service) reads the paper folder read-only, may
reach only private and link-local addresses plus mDNS multicast (kernel
firewall, `IPAddressAllow`), and binds no ports. It takes literal local IP
addresses only, never hostnames, so no DNS answer can steer a job to the
internet or a cloud print service. IPP runs over TLS only; printers sign
their own certificates, so each one is pinned when you approve its
fingerprint, and every print re-checks the pin before the page is even
made. Files are opened without following symlinks, must be well-formed
paper wallet files, and the key inside must match the file's address.

Risks left: some printers keep recent jobs in memory or on disk (reset one
before selling it); the first connection to a new printer could be
intercepted by someone already inside your network (that's what comparing
the fingerprint is for). **Known gap, shared with SHARE FILES:** both are
started by the app, so a compromised app with an accomplice on your network
could send a paper wallet to the accomplice's "printer" (after pinning it
itself) or open a share for them. The fix for both is to have the signer's
gold screen confirm any paper wallet leaving the device; planned.

## AUTO LOCK (0.8.17)

How long the seed stays in memory without a key press is the signer's
setting (`autolock.json` in its folder), not the app's: 1 minute to 24
hours, or OFF. Making it shorter needs nothing; longer or OFF needs the PIN
on the signer's screen. The signer counts every physical key as activity
(it reads the keyboard itself), but checks the timeout first, so a key never
revives a seed that already timed out. OFF trades safety for convenience:
the seed stays in memory until LOCK NOW, a restart or power loss.

While SHARE FILES is open the app pings the signer's `touch` every 20 s so
the share isn't cut off. `touch` is on the app's no-PIN list (it existed for
fingerprint quick unlock), so a compromised app could keep an unlocked seed
in memory indefinitely; it still can't sign without your YES. Noted as a
gap.

## Paper wallets (0.8.7)

Paper wallet files hold a private key in plain text, on purpose: that's what
a paper wallet is. Since 0.8.15 they never go on the card: the card's FAT
side has no per-user permissions, so every process could read them there,
the app included. The signer writes them; the root helper moves them to
`/var/lib/czwallet/paper` (root:czshare, mode 2770, files 0640), which only
root and the SHARE FILES service can read. You download them with SHARE
FILES, which refuses to include them until you've set your own password,
and you can delete each one there once saved. Files left on the card by
0.8.7-0.8.14 are moved in the next time paper wallets are made. Anyone with
the printout can take those coins: keep amounts small. The keys are not
covered by your seed words or card backups.

## SHARE FILES (0.8.8)

A web page on the local network is an attack surface, so it's fenced in:
off unless you start it from the screen, time-limited, local addresses only
(server check plus kernel firewall rules on the device), download-only,
separate service user with read access to the two export folders only,
HTTPS, login lockout, cookies Secure/HttpOnly/SameSite=Strict, Origin check
on every form, strict Content-Security-Policy, files served only by names in
the folder's own listing. The default password (`bitcoin`) is public, so on
shared networks change it. Paper wallets are left out unless you choose to
include them for that session.

