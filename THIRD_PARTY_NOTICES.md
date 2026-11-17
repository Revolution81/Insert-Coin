# Third-party notices

Insert Coin's own code is under the terms in [LICENSE](LICENSE). It uses the
following third-party software, which keeps its own license.

## Python packages

Installed with pip from `requirements.lock` (pinned versions and SHA-256
hashes); not copied into this repository.

| Package | Version | License | Used for |
|---|---|---|---|
| embit | 0.8.0 | MIT | Bitcoin keys, addresses, PSBTs, descriptors |
| cryptography | 50.0.1 | Apache-2.0 or BSD-3-Clause | AES-GCM encryption of the vault |
| argon2-cffi | 25.1.0 | MIT | PIN hashing (Argon2id) |
| argon2-cffi-bindings | 26.1.0 | MIT | Argon2 C bindings |
| cffi | 2.1.1 | MIT-0 | C bindings |
| pycparser | 3.0 | BSD-3-Clause | used by cffi |
| shamir-mnemonic | 0.3.0 | MIT (Trezor / SatoshiLabs) | SLIP39 split backups |
| Pillow | 12.3.0 | MIT-CMU (HPND) | drawing every screen |
| qrcode | 8.2 | BSD | making QR codes |
| pyzbar | 0.1.9 | MIT | reading QR codes (uses the zbar library, LGPL-2.1, from the OS) |

From the operating system on the device: python3-evdev (BSD, keyboard),
python3-picamera2 (BSD-2-Clause, camera). For the PC simulator only:
pygame (LGPL-2.1).

## Fonts

In `ui/fonts/`, each with its full license file next to it. All are under
the SIL Open Font License 1.1.

- Press Start 2P: Copyright 2012 The Press Start 2P Project Authors
  (CodeMan38), Reserved Font Name "Press Start 2P"
- Silkscreen: Copyright 2001 The Silkscreen Project Authors (Jason Kottke)
- Tiny5: Copyright 2022-2024 The Tiny5 Project Authors (Gissio)

## Hardware drivers based on M5Stack code

`core/hw/nfc.py` follows the register map, commands and start-up sequence of
M5Stack's M5Unit-NFC library, and `core/hw/finger.py` follows the message
format of M5Stack's UIFlow driver for the Unit Fingerprint2, both
translated to Python. Those parts are used under the MIT License:

```
Copyright (c) 2025 M5Stack Technology CO LTD

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Standards

The wallet implements public Bitcoin standards (BIP32, 39, 48, 84, 85, 86,
137, 174, 322, 380-386; SLIP39; Blockchain Commons UR, bytewords and SeedQR)
and Coinkite's published SATSCARD / TAPSIGNER protocol.

Not affiliated with or endorsed by M5Stack, Coinkite, Trezor or Blockchain
Commons.
