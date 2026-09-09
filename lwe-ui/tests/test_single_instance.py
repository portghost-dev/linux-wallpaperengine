"""Single-instance guard contract (single_instance.py).

  * first acquire owns the socket at $XDG_RUNTIME_DIR/lwe/ui.sock
  * a second acquire returns None and the owner's on_present callback fires
  * a stale non-socket leftover at the path is removed and ownership taken
  * close() releases the socket so a later acquire can own it again

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 tests/test_single_instance.py
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_tmp = tempfile.TemporaryDirectory(prefix="lwe-ui-test-")
os.environ["XDG_RUNTIME_DIR"] = _tmp.name
os.environ["HOME"] = os.path.join(_tmp.name, "home")
os.makedirs(os.environ["HOME"], exist_ok=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from lwe_ui import single_instance


def main() -> None:
    app = QApplication([])

    sock = single_instance.socket_path()
    assert str(sock).startswith(_tmp.name), "sandboxed runtime dir must hold the socket"

    presented = {"n": 0}
    guard = single_instance.acquire(lambda: presented.__setitem__("n", presented["n"] + 1))
    assert guard is not None, "first acquire must own the panel"
    assert sock.is_socket(), "owning acquire must leave a live socket"

    second = single_instance.acquire(lambda: None)
    QTest.qWait(100)
    assert second is None, "second acquire must defer to the running owner"
    assert presented["n"] == 1, f"owner must be asked to present itself: {presented}"

    third = single_instance.acquire(lambda: None)
    QTest.qWait(100)
    assert third is None and presented["n"] == 2, "every duplicate launch pings the owner"

    guard.close()
    assert not sock.exists(), "close must release the socket file"

    # the tray's Exit rides the same socket as a second verb; "show" still presents
    quits = {"n": 0}
    owner = single_instance.acquire(lambda: presented.__setitem__("n", presented["n"] + 1),
                                    lambda: quits.__setitem__("n", quits["n"] + 1))
    assert owner is not None
    before = presented["n"]
    assert single_instance.request_quit() is True
    QTest.qWait(100)
    assert quits["n"] == 1 and presented["n"] == before, "quit must reach on_quit and not present"
    assert single_instance.notify_running() is True
    QTest.qWait(100)
    assert presented["n"] == before + 1 and quits["n"] == 1, "show still presents"
    assert single_instance.request_quit(single_instance.tray_socket_path()) is False, "no tray, no taker"
    owner.close()
    assert not sock.exists()

    sock.parent.mkdir(parents=True, exist_ok=True)
    sock.touch()
    takeover = single_instance.acquire(lambda: None)
    assert takeover is not None, "a stale non-socket leftover must not block ownership"
    assert sock.is_socket(), "takeover must replace the leftover with a live socket"
    takeover.close()

    del app
    print("OK test_single_instance - own/defer-present/re-ping/stale-takeover/release all hold")


if __name__ == "__main__":
    main()
