"""TXIDs out of the wallet: explorer links, the TXID QR, and the CSV export."""
import csv
import io
import os

from core import history_export as hx
from core.wallet import Tx
from ui.qrscreens import AnimatedQRScreen

from test_app import app, create_wallet, enter, press, settle  # noqa: F401
from test_core import fund

TXID = "ab" * 32


def test_links_for_each_network():
    assert hx.tx_url("bitcoin", TXID) == f"https://mempool.space/tx/{TXID}"
    assert hx.tx_url("signet", TXID) == f"https://mempool.space/signet/tx/{TXID}"
    assert hx.tx_url("testnet4", TXID) == f"https://mempool.space/testnet4/tx/{TXID}"
    assert hx.tx_url("regtest", TXID) is None
    assert hx.tx_url("bitcoin", TXID, "mempool.example.onion/") == \
        f"https://mempool.example.onion/tx/{TXID}"
    assert hx.tx_url("bitcoin", TXID, "http://192.168.1.5:4080") == \
        f"http://192.168.1.5:4080/tx/{TXID}"


def test_csv_rows_and_formula_safety():
    txs = [Tx(TXID, 150_000, None, True, 800_000, 1_700_000_000),
           Tx("cd" * 32, -20_500, 500, False, None, None)]
    labels = {TXID: "=HYPERLINK(\"evil\")", "cd" * 32: "coffee, with a comma"}
    text = hx.to_csv(txs, labels.get, "signet")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == hx.HEADER
    assert rows[1][1:7] == ["confirmed", "150000", "+0.00150000", "", TXID,
                            "'=HYPERLINK(\"evil\")"]       # can't run as a formula
    assert rows[1][7] == f"https://mempool.space/signet/tx/{TXID}"
    assert rows[2][1:7] == ["pending", "-20500", "-0.00020500", "500", "cd" * 32,
                            "coffee, with a comma"]
    assert text.encode().startswith(b"date,status,amount_sats,")


def test_save_keeps_the_newest(tmp_path, monkeypatch):
    import core.config
    monkeypatch.setattr(core.config, "APP_DIR", str(tmp_path))
    names = []
    for i in range(12):
        monkeypatch.setattr(hx.time, "strftime",
                            lambda fmt, *a, i=i: f"2026111{i // 10}-1200{i % 10:02d}"
                            if "%Y%m%d" in fmt else "x")
        names.append(hx.save("date,status,amount_sats\n", "73C5DA0A-My Wallet!"))
    assert all(hx.NAME.match(n) for n in names)
    assert names[0].startswith("InsertCoin-history-73c5da0a-my-wallet-")
    kept = sorted(os.listdir(hx.local_dir()))
    assert len(kept) == hx.KEEP and kept[-1] == names[-1]


def test_card_helper_copies_exports_only(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "copy_backup", os.path.join(os.path.dirname(__file__), "..", "tools", "copy_backup.py"))
    cb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cb)
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    monkeypatch.setattr(cb, "EXPORT_SRC", str(src))
    monkeypatch.setattr(cb, "EXPORT_DST", str(dst))
    good = "InsertCoin-history-73c5da0a-main-20261114-101500.csv"
    (src / good).write_text("date,status,amount_sats,amount_btc\n")
    assert cb.main(["x", good]) == 0 and (dst / good).exists()
    bad = "InsertCoin-history-73c5da0a-main-20261114-101501.csv"
    (src / bad).write_bytes(b"CZWB1 not a csv")
    assert cb.main(["x", bad]) == 2 and not (dst / bad).exists()
    assert cb.main(["x", "../../etc/passwd"]) == 2
    link = "InsertCoin-history-73c5da0a-main-20261114-101502.csv"
    os.symlink("/etc/hostname", src / link)
    try:
        rc = cb.main(["x", link])
    except OSError:
        rc = 2                                   # O_NOFOLLOW refuses the symlink
    assert rc == 2 and not (dst / link).exists()


def test_txid_qr_and_export_in_the_ui(app):  # noqa: F811
    import core.config
    from ui.flow import history_screen
    create_wallet(app)
    fund(app.wallet, 70_000)
    fund(app.wallet, 30_000)
    app.refresh_balance()
    history_screen(app)
    settle(app)
    hist = app.visible
    assert hist.items[-1][0] == "SAVE ALL TO CARD"
    hist.sel = 0
    enter(app)                                    # the transaction
    tx_menu = app.visible
    tx_menu.sel = [i[0] for i in tx_menu.items].index("SHOW TXID")
    enter(app)
    qr = app.visible
    assert isinstance(qr, AnimatedQRScreen) and qr.title == "TRACK IT"
    txid = app.wallet.history()[0].txid
    assert qr.frames == [f"https://mempool.space/signet/tx/{txid}"]
    app.render(0)
    press(app, "KEY_TAB")
    assert qr.title == "TXID" and qr.frames == [txid]
    app.render(0)
    press(app, "KEY_BACKSPACE")
    press(app, "KEY_BACKSPACE")

    # save everything to the card
    hist = app.visible
    hist.sel = len(hist.items) - 1
    enter(app)
    assert app.visible.title == "EXPORT HISTORY?"
    press(app, "KEY_LEFT")                        # YES
    enter(app)
    settle(app)
    assert app.visible.title == "SAVED"
    files = os.listdir(os.path.join(core.config.APP_DIR, "exports"))
    assert len(files) == 1
    rows = list(csv.reader(open(os.path.join(core.config.APP_DIR, "exports", files[0]))))
    assert len(rows) == 3 and {r[5] for r in rows[1:]} == {t.txid for t in app.wallet.history()}
