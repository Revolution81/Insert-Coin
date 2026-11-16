"""
czprint: the PRINT service for paper wallets (see core/printing.py).

Listens on /run/czprint/print.sock for the app (group czprint), one JSON
request per connection, one at a time. Runs as user czprint, which can read
the paper folder and reach the local network only (deploy/czprint.service).
"""
import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import ipp  # noqa: E402
from core.printing import SOCKET, PrintError, PrintService  # noqa: E402

PAPER = "/var/lib/czwallet/paper"
STATE = os.environ.get("STATE_DIRECTORY", "/var/lib/czprint")


def serve(svc: PrintService, path: str, once: bool = False) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    os.chmod(path, 0o660)                   # the app reaches it through group czprint
    srv.listen(2)
    while True:
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            buf = b""
            while not buf.endswith(b"\n") and len(buf) < 16384:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            req = json.loads(buf)
            try:
                result = {"result": svc.handle(req.get("method"), req.get("params") or {})}
            except (PrintError, ipp.PrinterError) as e:
                result = {"error": str(e)}
            except Exception:  # noqa: BLE001 - never echo internals (or file contents)
                result = {"error": "printing failed"}
            conn.settimeout(120)
            conn.sendall(json.dumps(result).encode() + b"\n")
        except (OSError, ValueError, AttributeError):
            pass
        finally:
            conn.close()
        if once:
            srv.close()
            return


def main() -> int:
    serve(PrintService(PAPER, STATE), SOCKET)
    return 0


if __name__ == "__main__":
    sys.exit(main())
