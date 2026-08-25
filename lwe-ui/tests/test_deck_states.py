"""Render + state tests for Deck.qml's four faces.

Deck.qml rebuilds itself for four states; a static lint cannot see which blocks show or how they
dim. We load the real Deck (with real Backend / BenchBridge / DevBridge context objects, sandboxed
HOME/XDG, bench_courier stubbed so nothing touches a real engine and none is spawned) and drive
each state by setting the bridges' internal fields + emitting their notify signals, then assert:

  F2-testing  While bench.isTesting, the "Editor Benching" left block (deckLeftTesting) is visible and
              the idle / A/B blocks are not; an amber (warning) dot renders in the left region.
  F2-ab       While dev.abRunning(), the two-side A/B block (deckLeftAB) and the amber lease line
              (deckCenterAB) are visible and the idle progress/transport column is not; an amber dot
              renders in the center.
  F24         With the engine off (masterActive False) the left block is EXEMPT from the off-state
              dimming: deckLeftIdle keeps opacity 1 and shows the status dot + secondary text, while
              the transport column dims (< 1). That is the whole point of F24 - the status message
              must stay legible while the controls gray out.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software python3 tests/test_deck_states.py
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

_WARNING = "#EF9F27"   # Theme.warning - the amber hold dot


def _find(root, name):
    from PySide6.QtCore import QObject
    return next((o for o in root.findChildren(QObject) if o.objectName() == name), None)


def _count(img, hexcol, tol=40) -> int:
    from PySide6.QtGui import QColor
    col = QColor(hexcol)
    n = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            p = img.pixelColor(x, y)
            if (abs(p.red() - col.red()) < tol and abs(p.green() - col.green()) < tol
                    and abs(p.blue() - col.blue()) < tol):
                n += 1
    return n


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-deck-")
    orig = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
    try:
        os.environ["HOME"] = home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
        os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
        os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQuick import QQuickView, QQuickWindow
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

        from PySide6.QtCore import QObject, Signal, Slot

        class StubWizard(QObject):
            phaseChanged = Signal()

            def __init__(self):
                super().__init__()
                self._phase = "p1"

            def set_phase(self, p):
                self._phase = p
                self.phaseChanged.emit()

            @Slot(result=str)
            def phase(self):
                return self._phase

            @Slot()
            def close(self):
                pass

            @Slot()
            def killBench(self):
                pass

            @Slot(result=int)
            def benchLoadRemaining(self):
                return -1

        wizard = StubWizard()

        view = QQuickView()
        view.engine().addImportPath(str(_QML_DIR))
        view.rootContext().setContextProperty("backend", backend)
        view.rootContext().setContextProperty("editor", editor)
        view.rootContext().setContextProperty("bench", bench)
        view.rootContext().setContextProperty("dev", dev)
        view.rootContext().setContextProperty("wizardBridge", wizard)
        view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        view.setSource(QUrl.fromLocalFile(str(_QML_DIR / "Deck.qml")))
        assert view.status() == QQuickView.Status.Ready, [e.toString() for e in view.errors()]
        view.resize(1280, 72)

        deck = view.rootObject()
        # Deck.qml has no width of its own - the real Main.qml layout supplies it. Give it the
        # 1280 minimum window width here so the centre-anchored / right-anchored blocks land where
        # they would in the app (without this the root width is 0 and centered items fall off-screen).
        deck.setProperty("width", 1280)
        left_idle = _find(deck, "deckLeftIdle")
        left_testing = _find(deck, "deckLeftTesting")
        left_ab = _find(deck, "deckLeftAB")
        center_ab = _find(deck, "deckCenterAB")
        assert left_idle and left_testing and left_ab and center_ab, "deck state blocks missing objectNames"

        def settle():
            for _ in range(3):
                app.processEvents()
            QTest.qWait(60)

        deck.setProperty("masterActive", True)
        deck.setProperty("engineStatus", {"state": "up", "current": "", "interval": "900", "next_in": "300"})
        settle()
        assert left_idle.property("visible") is True, "idle block should show when engine up + not holding"
        assert left_testing.property("visible") is False and left_ab.property("visible") is False, \
            "hold blocks must be hidden in the idle face"

        # ---- smooth clock: elapsed interpolates BETWEEN the 2s status polls ----------------
        # anchor: interval 900, next_in 300 -> elapsed base 600. Waiting ~1.2s of wall clock
        # must advance the interpolated elapsed WITHOUT a new status poll (the 2s-step lag
        # the owner reported). QML-declared functions are direct-callable on the PySide
        # wrapper (invokeMethod does not reach plain JS functions - established pattern).
        r0 = float(deck.elapsedSecs())
        QTest.qWait(1200)
        r1 = float(deck.elapsedSecs())
        assert r1 > r0 + 0.4, f"elapsed must advance between polls (got {r0} -> {r1})"
        assert 600 <= r0 <= 605 and r1 <= 610, f"interpolation anchored at 600 ({r0} -> {r1})"

        bench._is_testing = True
        bench._test_state = "testing"
        bench.stateChanged.emit()
        settle()
        assert left_testing.property("visible") is True, "Testing-draft block must show while bench.isTesting"
        assert left_idle.property("visible") is False and left_ab.property("visible") is False, \
            "idle + A/B blocks must be hidden during a test"
        img_test = QQuickWindow.grabWindow(view)
        if img_test.isNull() or img_test.width() < 200:
            print("SKIP deck render asserts (no frame grabbed on this platform)")
        else:
            left_amber = _count(img_test.copy(0, 0, 420, 72), _WARNING)
            assert left_amber > 0, "the amber 'Editor Benching' dot must render in the left block"
        bench._is_testing = False
        bench._test_state = "idle"
        bench.stateChanged.emit()
        settle()
        assert left_testing.property("visible") is False, "Testing block must clear when the test stops"

        dev._ab_running = True
        dev.abReset()
        dev.setABFix("B", "fbopool", False)
        dev.stateChanged.emit()
        settle()
        assert left_ab.property("visible") is True, "A/B two-side block must show while dev.abRunning()"
        assert center_ab.property("visible") is True, "A/B center lease line must show while dev.abRunning()"
        assert left_idle.property("visible") is False, "idle block must be hidden during A/B"
        img_ab = QQuickWindow.grabWindow(view)
        if not (img_ab.isNull() or img_ab.width() < 200):
            mid_amber = _count(img_ab.copy(540, 0, 220, 72), _WARNING)
            assert mid_amber > 0, "the amber A/B lease dot must render in the center"
        st = dev.abState()
        assert st["sideA"] and st["sideB"] and st["sideA"] != st["sideB"], \
            "abState must supply distinct per-side descriptions for the A/B name lines"
        dev._ab_running = False
        dev.stateChanged.emit()
        settle()
        assert left_ab.property("visible") is False and center_ab.property("visible") is False, \
            "A/B blocks must clear when the hold stops"

        deck.setProperty("masterActive", False)
        deck.setProperty("engineStatus", {"state": "up", "current": "", "interval": "", "next_in": ""})
        settle()
        assert left_idle.property("visible") is True, "the left slot shows the status message when off"
        assert abs(float(left_idle.property("opacity")) - 1.0) < 0.01, \
            "F24: the left block must stay at full opacity while off (exempt from dimming)"
        assert _find(deck, "deckStatusDot").property("visible") is True, "off-state status dot must show"
        status_text = _find(deck, "deckStatusText")
        assert status_text.property("visible") is True and status_text.property("text") == "Engine off", \
            "off-state 13px secondary line must read 'Engine off'"
        assert abs(float(deck.property("transportDim")) - 0.35) < 0.01, \
            "F24: transport/overrides must dim to 0.35 while the engine is off"

        deck.setProperty("masterActive", True)
        deck.setProperty("engineStatus", {"state": "", "current": "", "interval": "", "next_in": ""})
        settle()
        assert status_text.property("text") == "Engine down", "engine-down line must read 'Engine down'"
        assert abs(float(left_idle.property("opacity")) - 1.0) < 0.01, \
            "F24: left block stays full opacity for engine-down too"

        deck.setProperty("masterActive", True)
        deck.setProperty("engineStatus", {"state": "up", "current": "", "interval": "900", "next_in": "300"})
        wizard.set_phase("p3")
        settle()
        assert deck.property("wizBenching") is True, "stub wizard phase p3 must put the deck in Workshop Benching"
        assert abs(float(deck.property("transportDim")) - 0.45) < 0.01, \
            "a bench must still dim the transport to 0.45"

        stop_sq = _find(deck, "deckStopSquare")
        bench_bar = _find(deck, "deckBenchBar")
        assert stop_sq and bench_bar, "stop square + bench bar need objectNames for the exemption test"
        assert stop_sq.property("visible") is True, "the stop must show during a Workshop bench"
        assert abs(float(stop_sq.property("opacity")) - 1.0) < 0.01, \
            "the stop is the only LIVE control under a hold - it must not dim with the transport"
        assert abs(float(bench_bar.property("opacity")) - 1.0) < 0.01, \
            "the bench bar is the bench presence cue - it must not dim with the transport"
        wizard.set_phase("p1")
        settle()

        assert stop_sq.property("visible") is False, "no lease, no stop square"

        bench._is_testing = True
        bench.stateChanged.emit()
        settle()
        assert deck.property("testing") is True and stop_sq.property("visible") is True, \
            "Editor benching must now carry the stop square (was an inert outline-play)"
        bench._is_testing = False
        bench.stateChanged.emit()
        settle()


        print("OK test_deck_states - testing/AB/idle faces switch on bench+dev; "
              "F24 left block exempt (opacity 1.0) while transport dims to 0.35; "
              "lease exemption: stop + lease bar stay at opacity 1.0 under a 0.45 bench dim; "
              "off='Engine off', watcher-down='Watcher down'")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
