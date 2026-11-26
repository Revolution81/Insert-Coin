"""
Insert Coin: the app process (UI + watch-only wallet service).

    sudo -u czapp python3 app.py          # on the device (see deploy/)
    CZ_SIGNER_INPROC=1 CZ_DATA=./devdata python3 app.py --png
                                          # dev: signer in-process, frames to frame.png

The signer runs separately (core/signer_service.py) and holds the keys. It
also owns the screen and keyboard: this process sends it frames and gets key
presses back, except while the signer is asking you something itself (PIN,
confirming a payment, seed words). See ui/console.py.
"""
from __future__ import annotations

import glob
import os
import queue
import sys
import threading
import time
import traceback

from core import radios
from core.config import APP_DIR, Settings
from core import power
from core.signer import network_is_up
from core.signer_service import RemoteError, SignerClient
from core.wallet import SignedTx, WalletService
from ui import theme as t
from ui.flow import HomeScreen, LockedScreen, locked_screen, unlock, welcome
from ui.input import Mode, Translator
from ui.widgets import BusyScreen, ConfirmScreen, MessageScreen, Screen

EXT_BUS = os.environ.get("CZ_EXT_BUS", "/dev/i2c-3")   # tools/ext_bus.py

# Prompts that end with the app setting up a wallet: "ONE MOMENT..." sits
# under the signer's screens meanwhile (see App.prompt)
DEBUG_KEYS = os.path.exists("/boot/firmware/insert-coin-debug.txt")

HOLD_PROMPTS = {"create_prompt", "restore_prompt", "import_prompt", "shares_prompt",
                "restore_backup_prompt"}

SYNC_EVERY_S = 60
DEFAULT_FEES = {"fast": 10, "normal": 5, "slow": 2}


class App:
    def __init__(self, signer=None, sync_jobs: bool = False):
        self.settings = Settings.load()
        from ui.appearance import apply as apply_look
        apply_look(self.settings)
        from ui import sound
        sound.set_volume(self.settings.volume)
        self.osd_until = 0.0                # the volume pop-up (ui/osd.py)
        self.signer = signer or SignerClient()
        self.tr = Translator(Mode.MENU)
        self.stack: list[Screen] = []
        self.wallet: WalletService | None = None
        self.fingerprint = ""
        self.balance: dict | None = None
        self.sync_jobs = sync_jobs          # tests run jobs inline
        self._results: queue.Queue = queue.Queue()
        self._posted: queue.Queue = queue.Queue()
        self._sync_thread: threading.Thread | None = None
        self.syncing = False
        self.net_error = ""
        self._fees: tuple[float, dict] | None = None
        self.last_input = time.monotonic()
        self.outbox_dir = os.path.join(APP_DIR, "outbox")
        self.public: dict = {}              # descriptors + fingerprint from the signer
        self.wallet_id: str | None = None   # None = single-sig, else a multisig id
        self.wallet_name = "Main"
        self.account: dict | None = None     # public info of the open single-sig account
        self.vault_only = False
        self.hw = {"chip": {"state": "absent"}, "finger": {"state": "absent"},
                   "nfc": {"state": "absent"}}
        self.nfc = None
        self.finger = None                  # fingerprint sensor, if plugged in
        from ui.camera import open_camera
        self.camera_factory = open_camera   # tests swap in a FakeCamera
        self._prompt_results: queue.Queue = queue.Queue()
        self.prompting = 0                  # requests waiting on the signer's screen
        # In-process signer (simulator, tests): its screen is drawn here too.
        self.console = getattr(self.signer, "console", None)
        if self.console is not None:
            self.console.sync_jobs = sync_jobs
            self.console.listeners.append(lambda active: self.tr.reset())

    # ---------------------------------------------------------- the stack
    @property
    def top(self) -> Screen:
        return self.stack[-1]

    def _shown(self):
        self.tr.set_mode(self.top.mode)
        self.top.on_show(self)

    def _hide(self, screens):
        for s in screens:
            hook = getattr(s, "on_hide", None)
            if hook:
                hook(self)

    def push(self, s: Screen):
        self.stack.append(s)
        self._shown()

    def pop(self):
        if len(self.stack) > 1:
            self._hide([self.stack.pop()])
            self._shown()

    def replace(self, s: Screen):
        self._hide(self.stack)
        self.stack = [s]
        self._shown()

    def home(self):
        if self.wallet:
            self._hide(self.stack[1:])
            del self.stack[1:]
            if not isinstance(self.stack[0], HomeScreen):
                self._hide(self.stack)
                self.stack = [HomeScreen()]
            self._shown()

    # -------------------------------------------------------------- start
    @staticmethod
    def system_steps():
        """An update installed by an older helper didn't run tools/migrate.py
        (new groups and permissions): ask the root helper once. It restarts
        the app if anything changed; nothing changes the second time."""
        import grp
        import pwd
        import subprocess
        helper = "/usr/local/sbin/cz-backup"
        try:
            if pwd.getpwuid(os.getuid()).pw_name != "czapp" or not os.path.exists(helper):
                return
            if any(grp.getgrgid(g).gr_name == "gpio" for g in os.getgroups()):
                return
            subprocess.Popen(["sudo", "-n", helper, "migrate"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001 - never stop the app over this
            pass

    _addons_on = False

    def addon_power(self):
        """Switch on the 5 V of the Grove port and the EXT header (root
        helper; M5's launcher used to do it). Once per run, then a short
        wait so the fingerprint sensor and the Cap are awake for the probe.
        True if the side port's 5 V was off until now (a fresh power-on)."""
        import pwd
        import subprocess
        import time
        from core.hw import grove
        helper = "/usr/local/sbin/cz-backup"
        if self._addons_on:
            return False
        was_off = grove.power() is False
        # the gpio group can switch them itself (M5's tmpfiles rule)
        if os.path.isdir("/sys/class/leds/grove_5v_out"):
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
            try:
                import addon_power
                res = addon_power.switch_on()
            finally:
                sys.path.pop(0)
            for line in res:
                print(f"add-on power: {line}", file=sys.stderr, flush=True)
            if all(r.endswith(": on") for r in res):
                App._addons_on = True
                time.sleep(1.0)
                return was_off
        try:
            if pwd.getpwuid(os.getuid()).pw_name != "czapp" or not os.path.exists(helper):
                return False
            r = subprocess.run(["sudo", "-n", helper, "addons"], stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=20)
            for line in (r.stdout + r.stderr).strip().splitlines()[:80]:
                print(line[:400], file=sys.stderr, flush=True)
            if r.returncode == 0:
                App._addons_on = True
                time.sleep(1.0)
                return was_off
        except Exception as e:  # noqa: BLE001 - never stop the app over this
            print(f"add-on power: {e}", file=sys.stderr, flush=True)
        return False

    def _root(self, cmd: str) -> tuple[bool, str]:
        """One word to the root helper (tools/copy_backup.py), on the device."""
        import pwd
        import subprocess
        helper = "/usr/local/sbin/cz-backup"
        try:
            if pwd.getpwuid(os.getuid()).pw_name != "czapp" or not os.path.exists(helper):
                return False, "no helper"
            r = subprocess.run(["sudo", "-n", helper, cmd], stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=30)
            out = (r.stdout + r.stderr).strip()
            print(f"hardware: {cmd}: {out[:200]}", file=sys.stderr, flush=True)
            return r.returncode == 0, out
        except Exception as e:  # noqa: BLE001
            return False, str(e)

    def _chips(self) -> dict:
        try:
            return self.signer.chip_status()
        except Exception as e:  # noqa: BLE001
            return {"state": "error", "error": str(e), "found": []}

    @staticmethod
    def _chip_at(st: dict, where: str) -> bool:
        return any(c.get("where") == where for c in st.get("found") or [])

    def _close_addons(self):
        old = getattr(self, "finger", None)
        if old is not None and hasattr(getattr(old, "port", None), "close"):
            try:
                old.port.close()                        # one open handle on the UART at a time
            except Exception:  # noqa: BLE001
                pass
        self.finger = None
        if getattr(self, "nfc", None) is not None:
            try:
                self.nfc.close()
            except Exception:  # noqa: BLE001
                pass
        self.nfc = None
        try:
            self.signer.nfc_unit_close()
        except Exception:  # noqa: BLE001
            pass

    def _hwlock(self):
        lock = self.__dict__.get("_hw_lock_obj")
        if lock is None:
            lock = self.__dict__.setdefault("_hw_lock_obj", threading.RLock())
        return lock

    def refresh_hardware(self):
        with self._hwlock():
            hw = self._refresh_hardware()
        self._start_side_watch()
        return hw

    # ------------------------------------------------ side port, while running
    SIDE_WATCH_S = 1.5
    FINGER_PROBE_S = 8.0                               # asking an empty port takes ~5 s

    def _start_side_watch(self):
        """On the device: notice a unit leaving the side port (and a
        fingerprint sensor arriving) without RESCAN. Above all, a unit
        unplugged while the port is on I2C puts it straight back on UART:
        a fingerprint sensor plugged into the keyboard's bus jams it (the
        keys stopped until it was unplugged, seen on the device)."""
        from core.hw import grove
        if self.sync_jobs or self.__dict__.get("_side_watching") or \
                not os.path.exists(grove.LED):
            return
        self._side_watching = True

        def loop():
            while not getattr(self, "exit_requested", False):
                time.sleep(self.SIDE_WATCH_S)
                try:
                    self._side_check()
                except Exception as e:  # noqa: BLE001 - never stop over this
                    print(f"side port watch: {e}", file=sys.stderr, flush=True)
        threading.Thread(target=loop, daemon=True, name="side-watch").start()

    def _side_present(self, what: str) -> bool:
        if what == "chip":
            return self._chip_at(self._chips(), "GROVE")
        try:                                           # the NFC Unit's identity register
            ident = bytes.fromhex(self.signer.nfc_unit_xfer(msgs=[[0, "7f"], [1, 1]]))
            return bool(ident) and (ident[0] >> 3) & 0x1F == 0x05
        except Exception:  # noqa: BLE001
            return False

    def _side_check(self):
        """One look at the side port (the watch thread, or tests)."""
        from core.hw import grove
        from core.hw.finger import open_sensor
        lock = self._hwlock()
        if not lock.acquire(blocking=False):            # RESCAN is running
            return
        try:
            what = self.hw.get("grove")
            if what in ("chip", "nfc") and grove.mode() == "i2c":
                if what == "nfc" and getattr(getattr(self.nfc, "chip", None), "field", False):
                    return                             # in use on a TAP screen
                if self._side_present(what):
                    self._side_misses = 0
                    return
                self._side_misses = getattr(self, "_side_misses", 0) + 1
                if self._side_misses < 2:              # one bad read isn't a removal
                    return
                self._side_misses = 0
                if not grove.set_mode("uart"):
                    self._root("grove-uart")
                print(f"hardware: side port: {what} gone, back to UART", file=sys.stderr, flush=True)
                gone = self.nfc if what == "nfc" else None
                chips = self._chips()

                def lost(app):
                    app.hw["grove"] = None
                    app.hw["chip"] = chips
                    if what == "nfc":
                        if app.nfc is gone:
                            app.nfc = None
                        app.hw["nfc"] = {"state": "absent", "errors": ["Unit: unplugged"]}
                self.post(lost)
                if gone is not None:
                    try:
                        gone.close()
                    except Exception:  # noqa: BLE001
                        pass
                return
            if what is None and grove.mode() == "uart" and self.finger is None:
                now = time.monotonic()                 # a fingerprint sensor plugged in?
                if now - getattr(self, "_side_probed", 0.0) < self.FINGER_PROBE_S:
                    return
                self._side_probed = now
                f, st = self._probe_finger(False)
                if f is None:
                    return
                print(f"hardware: side port: fingerprint plugged in: {st}", file=sys.stderr, flush=True)

                def found(app):
                    app.finger, app.hw["finger"], app.hw["grove"] = f, st, "finger"
                self.post(found)
        finally:
            lock.release()

    def _refresh_hardware(self):
        """What's on the two add-on connectors (ui/hardware_flow.py shows it):

        top / GPIO header  the NFC Cap, or a secure chip on pins 3/1 (its
                           own I2C bus, made while running: tools/ext_bus.py)
        side / Grove port  the fingerprint sensor (UART), or M5Stack's
                           secure chip or the NFC Unit (I2C: core/hw/grove.py)
        """
        from core.hw import grove
        from core.hw.finger import open_sensor
        from core.hw.nfc import open_reader, release_cap
        fresh = self.addon_power()                    # True: the 5 V just came on
        self._close_addons()
        nfc_st = {"state": "absent", "errors": []}
        self.hw["ext"] = self.hw["grove"] = None
        self.hw["finger"] = {"state": "absent"}

        def switch(m: str) -> bool:
            if grove.set_mode(m):
                return True
            self._root(f"grove-{m}")                  # not writable by the app's groups
            return grove.mode() == m

        def on_i2c() -> bool:
            """Side port on I2C, only while something answers there (it's
            the keyboard's bus)."""
            if not switch("i2c"):
                print("hardware: side port won't switch to I2C", file=sys.stderr, flush=True)
                return False
            if self._chip_at(self._chips(), "GROVE"):
                self.hw["grove"] = "chip"
                return True
            if self.nfc is None:
                reader, st = open_reader(cap=False, unit_relay=self._unit_relay)
                if reader is not None:
                    self.nfc, self.hw["nfc"] = reader, st
                    self.hw["grove"] = "nfc"
                    return True
                nfc_st.setdefault("errors", []).extend(e for e in st.get("errors", [])
                                                        if e.startswith("Unit"))
            return False

        # ---- side, first: the fingerprint sensor answers only for a few
        # seconds after its power comes on (seen on the device), so ask it
        # straight away, giving it fresh power when it wasn't just switched on
        side_i2c = grove.mode() == "i2c" and on_i2c()  # still there: no need to switch
        if not side_i2c:
            switch("uart")
            self.finger, self.hw["finger"] = self._probe_finger(fresh)
            if self.finger is not None:
                self.hw["grove"] = "finger"

        # ---- top: a chip on its bus, else the Cap, else look for a chip
        if os.path.exists(EXT_BUS):
            if self._chip_at(self._chips(), "GPIO"):
                self.hw["ext"] = "chip"
            else:
                self._root("ext-off")
        if self.hw["ext"] is None:
            cap_reader, cap_st = open_reader(cap=not os.path.exists(EXT_BUS))
            if cap_st.get("state") == "ready":
                self.hw["ext"] = "cap"
                if self.nfc is None:
                    self.nfc, nfc_st = cap_reader, cap_st
                else:                                   # the NFC Unit's already in use
                    cap_reader.close()
            else:
                nfc_st["errors"] = cap_st.get("errors", []) + nfc_st.get("errors", [])
                release_cap()                             # GPIO26 is the chip bus's SCL
                if self._root("ext-on")[0]:
                    if self._chip_at(self._chips(), "GPIO"):
                        self.hw["ext"] = "chip"
                    else:
                        self._root("ext-off")

        # ---- side, last: nothing on UART, so look on I2C
        if not side_i2c and self.finger is None and not on_i2c():
            switch("uart")
        if self.hw["grove"] != "nfc":
            self.hw["nfc"] = nfc_st
        self.hw["chip"] = self._chips()
        self._hw_at = time.monotonic()
        for k in ("chip", "finger", "nfc", "ext", "grove"):  # for the boot log
            print(f"hardware: {k}: {self.hw.get(k)}"[:400], file=sys.stderr, flush=True)
        print(f"hardware: side port mode: {grove.mode()} ({grove.LED}"
              f"{', writable' if os.access(grove.LED, os.W_OK) else ', not writable'})",
              file=sys.stderr, flush=True)
        return self.hw

    def _probe_finger(self, fresh: bool):
        """Ask the side port for the fingerprint sensor. Unless its power just
        came on, switch the side port's 5 V off and on first: Unit
        Fingerprint2 answered within a second of power-on and then, left
        alone, not at all (plugged in at start, or while running)."""
        from core.hw import grove
        from core.hw.finger import open_sensor
        if not fresh and grove.power_cycle():
            time.sleep(0.8)
        return open_sensor()

    def _unit_relay(self, msgs):
        return self.signer.nfc_unit_xfer(msgs=msgs)

    def start(self, title: bool = False):
        """title=True (the device and the simulator): the INSERT COIN screen
        first; OK plays the coin chime and carries on."""
        if title:
            from ui.title import TitleScreen
            self.system_steps()
            self.refresh_hardware()
            self.replace(TitleScreen(lambda app: app.start()))
            return
        if time.monotonic() - getattr(self, "_hw_at", -1e9) > 30:   # not again right after the title
            self.refresh_hardware()
        st = self.signer.status()
        if not st["has_seed"]:
            self.replace(welcome(self))
        else:
            if st.get("network") and st["network"] != self.settings.network:
                self.settings.network = st["network"]
                self.settings.save()
            self.replace(locked_screen(self))
            unlock(self)

    def open_wallet(self, info: dict):
        self.sync_lock_after()
        if info["network"] != self.settings.network:
            self.settings.network = info["network"]
            self.settings.save()
        self.fingerprint = info["fingerprint"]
        self.public = info
        self.switch_wallet(None)

    def switch_wallet(self, entry: dict | None):
        """None = the main single-sig wallet; {"account": "tr:0", ...} = another
        single-sig account (core/accounts.py); else a registered multisig."""
        if self.wallet:
            self.wallet.disconnect()
        self.vault_only = False             # the signer's rule for this account (shown only)
        if entry is None:
            info = self.public
            self.wallet = WalletService(self.settings, info["descriptor"],
                                        info["change_descriptor"], info["fingerprint"])
            temp = info.get("temporary")
            self.wallet_id, self.wallet_name = None, ("Temp " + temp["label"] if temp else "Main")
            self.account = info
        elif entry.get("account"):
            info = self.signer.account_info(account=entry["account"])
            self.wallet = WalletService(self.settings, info["descriptor"],
                                        info["change_descriptor"], info["fingerprint"])
            self.wallet_id, self.wallet_name = info["id"], entry.get("name") or info["id"]
            self.account = info
            self.vault_only = self.signer.account_rules().get(info["id"]) == "vault"
        else:
            self.wallet = WalletService(self.settings, entry["descriptor"], None, entry["id"],
                                        role=entry.get("role"))
            self.wallet_id, self.wallet_name = entry["id"], entry["name"]
        self.refresh_balance()
        self.replace(HomeScreen())
        if self.online:
            self.start_sync()

    def sync_lock_after(self):
        """Mirror the signer's AUTO LOCK so the screen locks when it does."""
        try:
            v = int(self.signer.status().get("lock_after", self.settings.auto_lock_s))
        except Exception:  # noqa: BLE001 - keep the last known value
            return
        if v != self.settings.auto_lock_s:
            self.settings.auto_lock_s = v
            self.settings.save()

    def quick_unlock_ready(self) -> bool:
        return bool(self.settings.finger_unlock and self.finger is not None
                    and self.hw["finger"].get("saved"))

    def lock(self, full: bool = False):
        """Soft lock (screen only) when fingerprint quick unlock is on and
        this isn't a full lock; otherwise the signer forgets the seed too."""
        soft = not full and self.quick_unlock_ready()
        if not soft:
            try:
                self.signer.lock()
            except Exception:
                pass
            if self.public and self.public.get("temporary"):   # the signer is back on the main seed
                self.public = {k: v for k, v in self.public.items() if k != "temporary"}
        self.replace(locked_screen(self, soft=soft))
        if not soft:
            unlock(self)

    def reset(self):
        self.wallet = None
        self.balance = None
        self.replace(welcome(self))

    def play_game(self):
        """The game PIN was entered at unlock: COIN INVADERS, full screen.
        In game+wipe mode the signer has already erased everything, so the
        app's data goes too, before the first frame; leaving the game then
        shows a device like new. Otherwise the wallet stays locked and
        leaving (hold HOME) goes back to the lock screen."""
        from ui.arcade import ArcadeScreen
        wiped = False
        try:
            wiped = not self.signer.status().get("has_seed", True)
        except Exception:  # noqa: BLE001
            pass
        if wiped:
            self.factory_reset(title=False)

        def save_hi(v):
            self.settings.arcade_hi = int(v)
            self.settings.save()

        def leave():
            if game in self.stack:
                self.stack.remove(game)
                self.tr.reset()
                if self.stack:
                    self._shown()
        game = ArcadeScreen(leave, hi=self.settings.arcade_hi, save_hi=save_hi)
        self.push(game)

    def factory_reset(self, title: bool = True):
        """After a wipe (WIPE DEVICE, the wipe PIN, ten wrong PINs): the
        app's own data goes too (settings, wallet cache, labels, unsent
        transactions, printers), and paper wallets and history exports on
        the device. Then it starts like the first time."""
        import shutil
        import subprocess
        from core import config
        for root in {config.APP_DIR, self.outbox_dir}:
            if os.path.isdir(root):
                for name in os.listdir(root):
                    path = os.path.join(root, name)
                    try:
                        if os.path.isdir(path) and not os.path.islink(path):
                            shutil.rmtree(path, ignore_errors=True)
                        else:
                            os.remove(path)
                    except OSError:
                        pass
        helper = "/usr/local/sbin/cz-backup"
        if not os.environ.get("CZ_SIM") and os.path.exists(helper):
            try:
                subprocess.run(["sudo", "-n", helper, "wipe"], capture_output=True, timeout=60)
            except Exception:  # noqa: BLE001 - best effort; the seed is already gone
                pass
        self.settings = Settings.load()            # defaults
        from ui import sound
        sound.set_volume(self.settings.volume)
        if os.environ.get("CZ_SIM") and not os.environ.get("CZ_SIM_ALLOW_MAINNET"):
            self.settings.network = "signet"       # the simulator is a PC: test coins only
        try:
            from ui import appearance
            appearance.apply(self.settings)
        except Exception:  # noqa: BLE001
            pass
        self.wallet = None
        self.balance = None
        self.fingerprint = ""
        self.stack = []
        self.start(title=title)

    # --------------------------------------------------------------- jobs
    def job(self, label, fn, on_done, on_error=None):
        """Run fn in the background behind a busy screen."""
        busy = BusyScreen(label)
        self.push(busy)
        if self.sync_jobs:
            self._finish(busy, *self._run(fn), on_done, on_error)
            return
        threading.Thread(target=lambda: self._results.put(
            (busy, *self._run(fn), on_done, on_error)), daemon=True).start()

    @staticmethod
    def _run(fn):
        try:
            return fn(), None
        except Exception as e:  # noqa: BLE001
            if not isinstance(e, RemoteError):
                traceback.print_exc()
            return None, e

    def _finish(self, busy, result, err, on_done, on_error):
        if busy in self.stack:
            self.stack.remove(busy)
            self._shown()
        if err is None:
            on_done(self, result)
        elif getattr(err, "kind", "") == "Locked":
            self.lock(full=True)
        elif on_error:
            on_error(self, err)
        else:
            self.push(MessageScreen("OOPS", [str(err)[:120]], border="red"))

    # ------------------------------------------------------------ camera
    def open_scanner(self, on_text):
        """A QR scanner. On the device the camera and zbar live in the
        sandboxed czscan service (CZ_SCAN_SOCK); in the simulator and tests
        they run here, with whatever camera_factory gives."""
        from ui.camera import RemoteScanner, Scanner, open_camera
        if os.environ.get("CZ_SCAN_SOCK") and self.camera_factory is open_camera:
            return RemoteScanner(on_text)
        return Scanner(self.camera_factory(), on_text)

    def camera_noise(self) -> bytes:
        """A hash of a few raw camera frames for seed randomness, or b"" if
        there's no working camera. Never fails: it's only an extra source."""
        from ui.camera import RemoteScanner, frame_noise, open_camera
        try:
            if os.environ.get("CZ_SCAN_SOCK") and self.camera_factory is open_camera:
                r = RemoteScanner(lambda t: None)
                try:
                    return r.noise()
                finally:
                    r.stop()
            cam = self.camera_factory()
            try:
                return frame_noise(cam)
            finally:
                cam.close()
        except Exception:  # noqa: BLE001
            return b""

    # ------------------------------------------------- the signer's screen
    def prompt(self, method, on_done, on_cancel=None, on_error=None, **params):
        """Ask the signer to take the screen (see core/trusted.py). The call
        blocks until you finish there, so it runs on its own thread; this
        app keeps drawing, but nothing it draws is shown meanwhile."""
        self.prompting += 1
        # Underneath the signer's screens: when the signer hands the screen
        # back, this shows until the result is handled, not the menu the
        # prompt was started from (which flashed up after setting a PIN)
        hold = BusyScreen("ONE MOMENT...") if method in HOLD_PROMPTS else None
        if hold is not None and self.stack:
            self.push(hold)

        def work():
            try:
                r, e = getattr(self.signer, method)(**params), None
            except Exception as ex:  # noqa: BLE001
                r, e = None, ex
            self._prompt_results.put((r, e, on_done, on_cancel, on_error, hold))
        threading.Thread(target=work, daemon=True).start()

    def _prompt_done(self, result, err, on_done, on_cancel, on_error, hold=None):
        self.prompting -= 1
        if hold is not None and hold in self.stack:
            self.stack.remove(hold)
            if self.stack:
                self._shown()
        self.last_input = time.monotonic()
        kind = getattr(err, "kind", "")
        if err is None:
            on_done(self, result)
        elif kind == "Cancelled":
            if on_cancel:
                on_cancel(self)
        elif kind == "FreshStart":
            self.factory_reset()                # the wipe PIN: just a first boot, no word of it
        elif kind == "GameTime":
            self.play_game()                    # the game PIN: a toy, as far as anyone can see
        elif kind == "Wiped":
            self.factory_reset(title=False)
            self.push(MessageScreen("WIPED", ["Too many wrong PINs.", "The seed was erased.",
                                              "Restore from your words."], border="red"))
        elif kind == "Locked":
            self.lock(full=True)
        elif on_error:
            on_error(self, err)
        else:
            self.push(MessageScreen("OOPS", [str(err)[:120]], border="red"))

    @property
    def visible(self) -> Screen:
        """The screen you're looking at: the signer's while it has the screen."""
        if self.console is not None and self.console.stack:
            return self.console.top
        return self.top

    def handle_event(self, ev):
        """An already-translated event (e.g. pasted text) for the visible screen."""
        if self.console is not None and self.console.stack:
            self.console.top.handle(ev, self.console)
        else:
            self.top.handle(ev, self)

    def idle(self) -> bool:
        """Nothing in flight (tests wait for this after each key)."""
        if not (self._results.empty() and self._posted.empty()
                and self._prompt_results.empty()):
            return False
        if self.console is not None:
            if not self.console.idle():
                return False
            return self.prompting == 0 or bool(self.console.stack)
        return self.prompting == 0

    def post(self, fn):
        """Run fn(app) on the UI thread (from background threads)."""
        self._posted.put(fn)

    def pump(self) -> bool:
        """Handle finished jobs and posted calls. True if anything changed."""
        changed = False
        if self.console is not None and self.console.pump():
            changed = True
        while not self._prompt_results.empty():
            self._prompt_done(*self._prompt_results.get())
            changed = True
        while not self._results.empty():
            self._finish(*self._results.get())
            changed = True
        while not self._posted.empty():
            self._posted.get()(self)
            changed = True
        poll = getattr(self.top, "poll", None) if self.stack else None
        if poll and poll(self):
            changed = True
        return changed

    # ----------------------------------------------------------- network
    @property
    def online(self) -> bool:
        return network_is_up()

    exit_requested = False

    def request_exit(self) -> None:
        """App mode: lock (the seed leaves memory) and end the run loop."""
        try:
            self.signer.lock()
        except Exception:  # noqa: BLE001
            pass
        self.exit_requested = True

    def network_link(self) -> bool:
        return network_is_up()

    def wifi_name(self) -> str | None:
        """The Wi-Fi network joined, for SETUP › NETWORK. Read in the
        background at most every 10 s (nmcli is slow on the CM0)."""
        now = time.monotonic()
        if not getattr(self, "_wifi_busy", False) and now - getattr(self, "_wifi_at", -99) > 10:
            self._wifi_busy, self._wifi_at = True, now

            def read():
                try:
                    self._wifi_ssid = radios.wifi_current()
                except Exception:  # noqa: BLE001
                    self._wifi_ssid = None
                finally:
                    self._wifi_busy = False
            threading.Thread(target=read, daemon=True).start()
        return getattr(self, "_wifi_ssid", None) if self.online else None

    def lan_name(self) -> str | None:
        """A plugged-in, working network cable (read from sysfs, at most
        every 2 s: it's drawn on every frame)."""
        now = time.monotonic()
        if now - getattr(self, "_lan_at", -99) > 2:
            self._lan_at = now
            try:
                self._lan = radios.wired_link()
            except Exception:  # noqa: BLE001
                self._lan = None
        return getattr(self, "_lan", None)

    def wifi_joined(self, ssid: str | None) -> None:
        self._wifi_ssid, self._wifi_at = ssid, time.monotonic()

    def go_online(self):
        radios.online_mode()
        for _ in range(20):           # give DHCP a moment
            if network_is_up():
                break
            time.sleep(0.5)

    def go_vault(self):
        radios.vault_mode()
        if self.wallet:
            self.wallet.disconnect()

    def start_sync(self):
        if not self.wallet or (self._sync_thread and self._sync_thread.is_alive()):
            return
        if self.sync_jobs:
            self._sync_once()
            return
        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._sync_thread.start()

    def _sync_once(self):
        self.syncing = True
        try:
            self.wallet.sync()
            self.net_error = ""
            self.post(lambda app: (app.refresh_balance(), app._offer_outbox()))
        except Exception as e:  # noqa: BLE001
            self.net_error = str(e)
            self.wallet.disconnect()
        finally:
            self.syncing = False

    def _sync_loop(self):
        while self.wallet and self.online:
            self._sync_once()
            for _ in range(SYNC_EVERY_S):
                if not self.online:
                    return
                time.sleep(1)

    def refresh_balance(self):
        if self.wallet:
            self.balance = self.wallet.balance()

    def fees(self) -> dict:
        if self.wallet and self.wallet.connected:
            if not self._fees or time.monotonic() - self._fees[0] > 300:
                self._fees = (time.monotonic(), self.wallet.fee_estimates())
            return self._fees[1]
        return dict(DEFAULT_FEES)

    def net_line(self) -> str:
        if not self.online:
            return "VAULT MODE - RADIOS OFF"
        if self.syncing:
            return "SYNCING..."
        if self.net_error:
            return "NET ERROR: " + self.net_error[:28]
        if self.wallet and self.wallet.last_sync:
            tip = self.wallet.tip_height
            return "SYNCED " + t.ago(time.time() - self.wallet.last_sync) + \
                (f"  BLOCK {tip:,}" if tip else "")
        return "ONLINE - NOT SYNCED YET"

    # ------------------------------------------------------------ outbox
    def save_outbox(self, tx, replaces: str | None = None):
        os.makedirs(self.outbox_dir, exist_ok=True)
        if replaces:                     # an unsent tx that got bumped: drop the old one
            old = os.path.join(self.outbox_dir, f"{replaces}.hex")
            if os.path.exists(old):
                os.remove(old)
        with open(os.path.join(self.outbox_dir, f"{tx.compute_txid()}.hex"), "w") as f:
            f.write(tx.serialize().hex())
        self.wallet.record_unconfirmed(tx, replaces=replaces)

    def outbox_count(self) -> int:
        return len(glob.glob(os.path.join(self.outbox_dir, "*.hex")))

    def _offer_outbox(self):
        n = self.outbox_count()
        if not n or any(isinstance(s, (ConfirmScreen, BusyScreen)) for s in self.stack):
            return

        def send_all():
            sent = []
            for p in sorted(glob.glob(os.path.join(self.outbox_dir, "*.hex"))):
                with open(p) as f:
                    raw = bytes.fromhex(f.read().strip())
                txid = os.path.basename(p)[:-4]
                sent.append(self.wallet.broadcast(SignedTx(txid, raw)))
                os.remove(p)
            return sent

        self.push(ConfirmScreen("BROADCAST?", [f"{n} signed transaction(s) from",
                                               "Vault mode are waiting.", "Send now?"],
                                lambda app: app.job("BROADCASTING...", send_all,
                                                    lambda app, ids: app.push(MessageScreen(
                                                        "SENT!", [f"{len(ids)} broadcast."])))))

    # ---------------------------------------------------------------- UI
    def status_bar(self, img, d, screen):
        from ui.hardware_flow import status_icons
        typing = self.tr.indicators if screen.mode is Mode.TEXT else None
        level, charging = power.battery()
        t.status_bar(img, d, mode="ONLINE" if self.online else "VAULT",
                     battery_frac=level, charging=charging,
                     online=bool(self.wallet and self.wallet.connected), typing=typing,
                     own_server=self.settings.own_server,
                     icons=status_icons(self), temp=bool((self.public or {}).get("temporary")),
                     lan=bool(self.online and self.lan_name()))

    def feed(self, name: str, value: int):
        self.last_input = time.monotonic()
        if self.console is not None and self.console.feed(name, value):
            return                         # the signer has the keyboard
        raw = getattr(self.top, "raw_key", None) if self.stack else None
        if raw is not None:                # a game: every press, repeat and release
            from ui.input import VOLUME_KEYS
            if name not in VOLUME_KEYS:            # volume keys still work
                raw(name, value, self)
                return
        from ui.input import Kind
        evs = self.tr.feed(name, value)
        if DEBUG_KEYS and self.tr.mode is Mode.MENU:
            # test cards only (insert-coin-debug.txt): every key in menus, to
            # learn what this keyboard sends. Never while typing text.
            print(f"key: {name} {value} -> {[(e.kind.value, e.value) for e in evs]}",
                  file=sys.stderr, flush=True)
        elif DEBUG_KEYS and value == 1 and not evs and not name.startswith("CHR:") and not (
                len(name) == 5 and name.startswith("KEY_") and name[4].isalnum()):
            # test cards only: a key that does nothing here (its name, never a
            # letter or digit) goes to the journal, to learn what the keyboard sends
            print(f"key: unused {name}", file=sys.stderr, flush=True)
        for ev in evs:
            if ev.kind is Kind.VOLUME:
                self.change_volume(ev.value)
                continue
            self.menu_sound(ev)
            self.top.handle(ev, self)

    def change_volume(self, how: str) -> None:
        """Fn+D louder, Fn+S quieter, Fn+A sound off/on (the same switch as
        SETUP › SOUND). Saved, and shown in the middle of the screen."""
        from ui import sound
        steps = sound.volume()
        if how == "mute":
            self.settings.sound = not self.settings.sound
        else:
            steps = max(0, min(sound.VOLUME_STEPS, steps + (1 if how == "+" else -1)))
            self.settings.volume = steps
            sound.set_volume(steps)
            if how == "+" and not self.settings.sound:
                self.settings.sound = True          # turning it up turns it on
        self.settings.save()
        if self.settings.sound and steps > 0:
            sound.play("ui_move")                  # hear the new level
        from ui import osd
        self.osd_until = time.monotonic() + osd.SHOW_S

    def menu_sound(self, ev):
        """The menu boop (SETUP › SOUND). Only for the app's own screens: the
        signer's (PIN, seed words) stay silent, and so does the title, which
        plays the fanfare."""
        from ui import sound
        from ui.input import Kind
        if getattr(self.top, "quiet", False):
            return
        quiet_for = getattr(self.top, "quiet_for", None)
        if quiet_for and quiet_for(ev):
            return
        kind = {Kind.NAV: "move", Kind.SELECT: "ok", Kind.BACK: "back",
                Kind.HOME: "back"}.get(ev.kind)
        if kind:
            sound.menu(kind, self.settings)

    def render(self, frame: int):
        img = self.top.render(self, frame)
        if time.monotonic() < self.osd_until:
            from ui import osd, sound
            img = osd.draw(img, sound.volume(), not self.settings.sound)
        if self.console is not None:       # in-process signer: it draws the final frame
            img = self.console.compose(img, frame)
        return img

    @property
    def animated(self) -> bool:
        if self.console is not None and self.console.stack:
            return self.console.animated
        return isinstance(self.top, BusyScreen) or getattr(self.top, "animated", False)

    # ------------------------------------------------------------ crash net
    def safely(self, fn, *args):
        """Run one step of the main loop. A bug in a screen shouldn't take the
        whole wallet down: log it, go home, say so. Repeated crashes still
        raise, so systemd restarts the app from scratch."""
        try:
            return fn(*args)
        except Exception:  # noqa: BLE001
            self.recover()
            return None

    def recover(self):
        now = time.monotonic()
        self._crashes = [t for t in getattr(self, "_crashes", []) if now - t < 30] + [now]
        tb = traceback.format_exc()
        sys.stderr.write(tb)
        try:
            from core.config import APP_DIR
            os.makedirs(APP_DIR, exist_ok=True)
            path = os.path.join(APP_DIR, "crash.log")
            if os.path.exists(path) and os.path.getsize(path) > 200_000:
                os.replace(path, path + ".old")
            with open(path, "a", encoding="utf-8") as f:
                from core.version import VERSION as v
                f.write(time.strftime("%Y-%m-%d %H:%M:%S v") + v + "\n" + tb + "\n")
        except OSError:
            pass
        if len(self._crashes) > 3:
            raise RuntimeError("crashing repeatedly; restarting")
        self.tr.reset()
        if self.wallet:
            self.home()
        else:
            self._hide(self.stack[1:])
            del self.stack[1:]
            self._shown()
        self.push(MessageScreen("OOPS", ["Something went wrong,", "so you're back home.",
                                         "Your keys and coins are safe.",
                                         "Details: crash.log"], border="red"))

    def check_idle(self):
        """SETUP > AUTO LOCK. The signer keeps the setting and forgets the
        seed on its own; this locks the screen at the same time. Screens that
        must keep running (SHARE FILES) set keep_awake."""
        locked = self.wallet is None or (len(self.stack) == 1 and isinstance(self.top, LockedScreen))
        if locked or self.prompting:
            return
        if getattr(self.top, "keep_awake", False):
            self.last_input = time.monotonic()
            return
        after = self.lock_after
        if after and time.monotonic() - self.last_input > after:
            self.lock()

    @property
    def lock_after(self) -> int:
        """Seconds idle before locking (0 = off), as the signer last said."""
        return self.settings.auto_lock_s


def battery_level() -> float:
    return power.battery()[0]


class LocalScreen:
    """Display and keyboard opened by this process: only for development
    with an in-process signer (CZ_SIGNER_INPROC=1)."""

    def __init__(self, sleep_fn=lambda: 120):
        from ui.display import Display, ScreenSleep
        from ui.input import KeyboardDevice
        self.disp, self.kb = Display("frame.png"), KeyboardDevice()
        self.sleeper = ScreenSleep(sleep_fn)

    def show(self, img):
        if not self.sleeper.asleep:
            self.disp.show(img)

    def poll(self, timeout):
        keys = [("key", n, v) for n, v in self.kb.poll(timeout) if self.sleeper.keep(n, v)]
        self.sleeper.tick()
        return keys

    def close(self):
        self.kb.close()


def run(png: bool = False):
    # Every start is in Vault mode, radios off, until you choose Online.
    # (Installed, czwallet-vault.service also does this at boot.) CZ_KEEP_RADIOS=1
    # skips it, for development over SSH.
    if os.environ.get("CZ_KEEP_RADIOS") != "1":
        try:
            radios.vault_mode()
        except Exception:  # noqa: BLE001 - no permission in a dev checkout: carry on
            pass
    app = App()
    if app.console is not None:
        screen = LocalScreen(app.signer.local.screen_sleep)
    else:
        from core.screen_link import ScreenClient
        screen = ScreenClient()
    app.start(title=True)
    frame, last_blink = 0, time.monotonic()
    secure = False                       # the signer has the screen: our frames aren't shown
    try:
        screen.show(app.render(frame))
        while not app.exit_requested:
            msgs = screen.poll(0.05)
            for m in msgs:
                if m[0] == "key":
                    app.safely(app.feed, m[1], m[2])
                elif m[0] == "secure":
                    secure = m[1]
                    app.tr.reset()
            # a key release redraws nothing (one frame per press, not two),
            # except for the modifier badges and a game, which use releases
            moved = [m for m in msgs if m[0] != "key" or m[2] != 0 or _release_matters(app, m[1])]
            changed = bool(app.safely(app.pump)) or bool(moved)
            sleeper = getattr(screen, "sleeper", None)
            if sleeper is not None:
                if getattr(screen, "_dark", False) and not sleeper.asleep:
                    changed = True                    # just woke: draw at once
                screen._dark = sleeper.asleep
            app.safely(app.check_idle)
            now = time.monotonic()
            if now - last_blink >= 0.5:
                frame ^= 1
                last_blink = now
                changed = True
            if app.animated:
                changed = True
            if changed and not secure:
                img = app.safely(app.render, frame)
                screen.show(img if img is not None else app.render(frame))
    finally:
        screen.close()


def _release_matters(app, name: str) -> bool:
    return (any(k in name for k in ("SHIFT", "CTRL", "ALT", "FN", "META"))
            or bool(app.stack and hasattr(app.top, "raw_key")))


if __name__ == "__main__":
    try:
        run(png="--png" in sys.argv)
    except KeyboardInterrupt:          # Ctrl+C in a terminal: a normal way out
        print("Insert Coin stopped.")
