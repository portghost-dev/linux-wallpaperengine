"""Exhibit gesture contract (chip = handle): drag deltas become relative compositor moves
for the cached side address, fullscreen toggles by double-click, drags no-op while that
side is fullscreen, and a live drag freezes that side's chip in the follower.

All hyprctl traffic is stubbed; nothing touches a compositor.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 tests/test_ab_gestures.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-gest-")
    os.environ["HOME"] = home
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
    os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
    os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
    os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

    from PySide6.QtCore import QCoreApplication
    from lwe_ui.dev import DevBridge
    from lwe_ui.storage import paths, settings

    app = QCoreApplication.instance() or QCoreApplication(["t"])  # noqa: F841
    paths.ensure_dirs()
    settings.ensure_exists()

    dev = DevBridge()
    sent: list[str] = []
    dev._hypr_dispatch = lambda expr: (sent.append(expr) or True)

    # no cached address -> silently nothing (A/B not running)
    dev.exhibitDragBy("A", 10, 5)
    dev.exhibitToggleFullscreen("A")
    assert sent == [], sent

    # cached sides (what the follower tick would have stored)
    dev._ab_addr = {"A": "0xaaa", "B": "0xbbb"}
    dev._ab_fullscreen = {"A": False, "B": False}

    dev.exhibitDragBy("A", 24, -8)
    assert len(sent) == 1 and "relative = true" in sent[0] \
        and "x = 24" in sent[0] and "y = -8" in sent[0] and "0xaaa" in sent[0], sent

    dev.exhibitToggleFullscreen("B")
    assert "fullscreen" in sent[-1] and "0xbbb" in sent[-1] \
        and 'mode = "maximized"' in sent[-1], sent

    dev._ab_fullscreen["A"] = True
    n = len(sent)
    dev.exhibitDragBy("A", 5, 5)
    assert len(sent) == n, "a fullscreen exhibit must not accept drag moves"

    # a live drag freezes that side's chip in the follower
    dev.exhibitDragActive("a", True)
    assert dev._ab_drag_side == "A"
    dev.exhibitDragActive("a", False)
    assert dev._ab_drag_side == ""

    # dead-man: a freeze whose release was eaten clears itself after 2s of silence
    # (the follower tick runs the sweep; emulate its guard directly)
    import time as _t
    dev.exhibitDragActive("b", True)
    assert dev._ab_drag_side == "B"
    dev._ab_drag_last = _t.monotonic() - 3.0
    dev._ab_running = True
    dev._hyprctl_clients = lambda: []
    dev._ab_place_tick()
    assert dev._ab_drag_side == "", "a stuck drag freeze must self-clear via the dead-man"
    dev._ab_running = False

    print("OK test_ab_gestures - relative drags, fullscreen toggle, fullscreen drag "
          "refusal, drag-freeze handshake")


if __name__ == "__main__":
    main()
