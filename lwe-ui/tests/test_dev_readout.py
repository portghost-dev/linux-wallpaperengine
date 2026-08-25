"""Wiring proof for the two live paths added in the 2026-07-19 gate round.

  1. Subsystem readout stream: a bench log line tagged for the open lens's instruments
     must land as a row in the readout table (the pane was anatomy-only before - the
     "empty readout" finding). A line for another subsystem must NOT land.
  2. Deck dev-hold face: while the dev cockpit holds (single
     bench), the deck shows the status-only "Developer Benching" LEFT block (deckLeftDevBench)
     in place of the idle now-playing block and dims the transport to 0.45 (the shared center
     BenchBar is the hold cover); the block clears and idle returns when the hold ends. The old
     centered click-to-release overlay is gone.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software python3 tests/test_dev_readout.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _find(root, name):
    from PySide6.QtCore import QObject
    return next((o for o in root.findChildren(QObject) if o.objectName() == name), None)


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-readout-")
    orig = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
    try:
        os.environ["HOME"] = home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
        os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
        os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQuick import QQuickView
        from PySide6.QtTest import QTest
        from PySide6.QtQml import qmlRegisterSingletonInstance
        from lwe_ui import bench_bridge, bench_courier
        from lwe_ui.models import Backend, ThemeTokens
        from lwe_ui.editor import EditorBridge
        from lwe_ui.dev import DevBridge
        from lwe_ui.storage import paths, settings
        from lwe_ui.app import _resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME

        paths.ensure_dirs()
        settings.ensure_exists()
        bench_courier.available = lambda: True
        bench_courier.wait_clear = lambda *a, **k: True
        bench_courier.standdown = lambda *a, **k: True
        bench_courier.resume = lambda *a, **k: True

        app = QGuiApplication.instance() or QGuiApplication(["t"])
        tokens = ThemeTokens(_resolve_theme_tokens())
        qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)

        backend = Backend()
        editor = EditorBridge()
        bench = bench_bridge.BenchBridge()
        dev = DevBridge()

        def settle():
            for _ in range(3):
                app.processEvents()
            QTest.qWait(60)

        view = QQuickView()
        view.engine().addImportPath(str(_QML_DIR))
        for n, o in (("backend", backend), ("editor", editor), ("bench", bench), ("dev", dev)):
            view.rootContext().setContextProperty(n, o)
        view.setSource(QUrl.fromLocalFile(str(_QML_DIR / "DevView.qml")))
        assert view.status() == QQuickView.Status.Ready, [e.toString() for e in view.errors()]
        view.resize(1280, 640)
        root = view.rootObject()
        root.setProperty("tab", 1)
        settle()

        rlist = _find(root, "lensReadoutList")
        assert rlist is not None, "lens readout list must exist on a subsystem tab"
        assert rlist.property("count") == 0

        dev.logLine.emit("LWE-MODELPASS obj=12 lights=3 tint=0.5")
        dev.logLine.emit("LWE-PARTSTATS obj=9 live=100")
        settle()
        assert rlist.property("count") == 1, \
            f"one MODELPASS line must land on the Lighting lens (got {rlist.property('count')})"

        dev.logLine.emit("LWE-SCRIPTTRACE light1.color -> [1,0,0]")
        settle()
        assert rlist.property("count") == 2, "every scoped tag of the lens must stream in"

        # a lens with no verified instruments has no tags - nothing may ever land there
        root.setProperty("tab", dev.subsystems().index("Puppets"))
        settle()
        rlist2 = _find(root, "lensReadoutList")
        dev.logLine.emit("LWE-MODELPASS obj=1")
        settle()
        assert rlist2.property("count") == 0, "a census-pending lens streams nothing"

        deckview = QQuickView()
        deckview.engine().addImportPath(str(_QML_DIR))
        for n, o in (("backend", backend), ("editor", editor), ("bench", bench), ("dev", dev)):
            deckview.rootContext().setContextProperty(n, o)
        deckview.setSource(QUrl.fromLocalFile(str(_QML_DIR / "Deck.qml")))
        assert deckview.status() == QQuickView.Status.Ready, \
            [e.toString() for e in deckview.errors()]
        deck = deckview.rootObject()
        deck.setProperty("width", 1280)
        assert _find(deck, "deckDevHold") is None, "the centered dev-hold overlay must be removed"
        dev_block = _find(deck, "deckLeftDevBench")
        assert dev_block is not None, "deck must have the Developer Benching left block"
        left_idle = _find(deck, "deckLeftIdle")
        assert left_idle is not None
        settle()
        assert dev_block.property("visible") is False, "Developer Benching block hidden while nothing holds"
        assert left_idle.property("visible") is True, "idle block shows while nothing holds"

        dev.isHolding = lambda: True        # the deck re-reads it on the rev bump
        dev.activeTargetWid = lambda: "2185197772"
        dev.stateChanged.emit()
        settle()
        assert dev_block.property("visible") is True, \
            "dev cockpit hold must show the Developer Benching left block"
        assert left_idle.property("visible") is False, \
            "the idle now-playing block steps aside during a dev-cockpit hold"
        assert abs(float(deck.property("transportDim")) - 0.45) < 0.01, \
            "transport must dim to 0.45 under the dev hold (unified lease-cover dim)"
        assert str(dev_block.property("devWid")) == "2185197772", \
            "the Developer Benching block must show the bench target"

        dev.isHolding = lambda: False
        dev.stateChanged.emit()
        settle()
        assert dev_block.property("visible") is False, "block clears when the hold ends"
        assert left_idle.property("visible") is True, "idle now-playing returns when the hold ends"

        print("OK test_dev_readout - lens stream lands scoped tags only; "
              "deck Developer Benching left block replaces idle under a dev hold (dim 0.45) and clears")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
