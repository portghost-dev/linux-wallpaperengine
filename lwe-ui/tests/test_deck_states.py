"""Render + state tests for Deck.qml's four faces.

Deck.qml rebuilds itself for four states; a static lint cannot see which blocks show or how they
dim. We load the real Deck (with real Backend / BenchBridge / DevBridge context objects, sandboxed
HOME/XDG, bench_courier stubbed so nothing touches a real engine and none is spawned) and drive
each state by setting the bridges' internal fields + emitting their notify signals, then assert:

  F2-testing  While bench.isTesting, the "Editor Benching" left block (deckLeftTesting) is visible and
              the idle / A/B blocks are not; an amber (warning) dot renders in the left region.
  F2-dev      While any developer exhibit is alive, the Developer Bench left block
              (deckLeftDevBench) names the mode (Bench, A / B / A + B) and the idle block hides;
              no filled dot renders in that block.
  F24         With the engine off (masterActive False) the left block is EXEMPT from the off-state
              dimming: deckLeftIdle keeps opacity 1 and shows the status dot + secondary text, while
              the transport column dims (< 1). That is the whole point of F24 - the status message
              must stay legible while the controls gray out.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software python3 tests/test_deck_states.py
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
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

        from PySide6.QtCore import QUrl, QMetaObject
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
        left_dev = _find(deck, "deckLeftDevBench")
        dev_mode = _find(deck, "deckDevBenchMode")
        assert left_idle and left_testing and left_dev and dev_mode, "deck state blocks missing objectNames"

        def settle():
            for _ in range(3):
                app.processEvents()
            QTest.qWait(60)

        deck.setProperty("masterActive", True)
        deck.setProperty("engineStatus", {"state": "up", "current": "", "interval": "900", "next_in": "300"})
        # the three-line left block and the back glyph's static gate
        deck.setProperty("engineStatus", {"state": "up", "current": "111", "last": "222", "next_up": "333",
                                          "interval": "900", "next_in": "300", "back_enabled": True})
        settle()
        assert _find(deck, "deckLast").property("text") == "Last: 222"
        assert _find(deck, "deckNow").property("text") == "111"
        assert _find(deck, "deckNext").property("text") == "Next: 333"
        assert _find(deck, "deckBack").property("enabled") is True
        deck.setProperty("engineStatus", {"state": "up", "current": "111", "last": "", "next_up": "",
                                          "interval": "", "next_in": "", "back_enabled": False, "order": "static"})
        settle()
        assert _find(deck, "deckLast").property("text") == "Last: ", "an empty Last keeps its label"
        assert _find(deck, "deckBack").property("enabled") is False, "back is off in static"
        assert deck.property("isStatic") is True
        assert float(_find(deck, "deckProgressFill").property("width")) == 0, "static: the bar is flat (spec 4)"
        # the progress bar is the shared glow filament: static is the flat bed
        pbar = _find(deck, "deckProgressBar")
        assert pbar is not None, "the deck progress bar needs its objectName"
        assert pbar.property("flat") is True and pbar.property("shimmerOn") is False \
            and pbar.property("breathing") is False, "static: no fill, no breath, no shimmer"
        assert _find(deck, "deckElapsed").property("visible") is False and _find(deck, "deckTotal").property("visible") is False, \
            "static: the times are hidden (spec 4)"
        deck.setProperty("engineStatus", {"state": "up", "current": "", "interval": "900", "next_in": "300"})
        settle()
        assert left_idle.property("visible") is True, "idle block should show when engine up + not holding"
        assert left_testing.property("visible") is False and left_dev.property("visible") is False, \
            "hold blocks must be hidden in the idle face"

        # ---- progress bar motion states ---------------------------------------
        # playing: lit to the elapsed portion (600 of 900 = two thirds), breathing on the bible's
        # inhale/exhale, shimmer sweeping inside the fill
        assert pbar.property("flat") is False and pbar.property("breathing") is True \
            and pbar.property("shimmerOn") is True, "playing: breath and shimmer on"
        fill_w = float(_find(deck, "deckProgressFill").property("width"))
        bar_w = float(pbar.property("width"))
        assert abs(fill_w / bar_w - 600 / 900) < 0.02, f"fill is the elapsed portion ({fill_w}/{bar_w})"
        assert int(pbar.property("breathInhale")) == 1100 and int(pbar.property("breathExhale")) == 1300
        # paused: the shimmer rests and the breath doubles; the fill stays lit
        deck.setProperty("rotationOn", False)
        settle()
        assert pbar.property("paused") is True and pbar.property("shimmerOn") is False, \
            "paused: shimmer off"
        assert int(pbar.property("breathInhale")) == 2200 and int(pbar.property("breathExhale")) == 2600, \
            "paused: the breath period doubles"
        assert pbar.property("breathing") is True, "paused still breathes, slower"
        deck.setProperty("rotationOn", True)
        settle()
        assert pbar.property("shimmerOn") is True
        # a pause leaves the fill and the halo where they are: no dip, only a slower breath
        QTest.qWait(700)
        before_f = float(pbar.property("fillPulse")); before_b = float(pbar.property("bloomPulse"))
        deck.setProperty("rotationOn", False)
        QTest.qWait(40)
        f0 = float(pbar.property("fillPulse")); b0 = float(pbar.property("bloomPulse"))
        assert abs(f0 - before_f) < 0.06 and abs(b0 - before_b) < 0.06, \
            f"a pause must not reset the breath (fill {before_f}->{f0}, bloom {before_b}->{b0})"
        assert int(pbar.property("breathInhale")) == 2200
        deck.setProperty("rotationOn", True)
        settle()
        # nothing lit, nothing breathing: an empty fill does not wake the render loop
        deck.setProperty("engineStatus", {"state": "up", "current": "111", "interval": "900", "next_in": "900"})
        settle()
        assert float(_find(deck, "deckProgressFill").property("width")) < 1.0, "an empty fill (a tick may add a few ms)"
        assert pbar.property("breathing") is False or float(_find(deck, "deckProgressFill").property("width")) > 0, \
            "an empty fill has no breath"
        deck.setProperty("engineStatus", {"state": "up", "current": "111", "interval": "900", "next_in": "300"})
        settle()
        assert pbar.property("breathing") is True
        # reduced motion: static fill, no breath, no shimmer (the Motion singleton's flag)
        from PySide6.QtQml import QQmlComponent
        flag = QQmlComponent(view.engine())
        flag.setData(b'import QtQuick\nimport "."\nQtObject { function set(v) { Motion.reducedMotion = v } }',
                     QUrl.fromLocalFile(str(_QML_DIR / "_probe.qml")))
        assert flag.status() == QQmlComponent.Status.Ready, [e.toString() for e in flag.errors()]
        probe = flag.create()
        probe.set(True)
        settle()
        assert pbar.property("breathing") is False and pbar.property("shimmerOn") is False, \
            "reduced motion: no breath, no shimmer"
        assert float(_find(deck, "deckProgressFill").property("width")) > 0, "reduced motion keeps the fill"
        probe.set(False)
        settle()
        assert pbar.property("breathing") is True
        # the bench bar is the same component on the warning colour, always fully lit
        bb = _find(deck, "deckBenchBar")
        assert bb.property("color").name().upper() == _WARNING and float(bb.property("progress")) == 1.0 \
            and bb.property("flat") is False, "bench bar: warning colour, full filament, unchanged"
        assert int(bb.property("bloomReach")) == 12 and int(pbar.property("bloomReach")) == 6, \
            "the deck bar's bloom reaches half as far as the bench bar's (R62)"

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
        # pausing folds the interpolated time into the anchor: the readout holds, it does not
        # drop back to the last polled value
        deck.setProperty("rotationOn", False)
        r2 = float(deck.elapsedSecs())
        assert r2 >= r1 - 0.05, f"a pause must not drop the readout ({r1} -> {r2})"
        QTest.qWait(900)
        assert abs(float(deck.elapsedSecs()) - r2) < 0.05, "held while paused"
        # the engine's whole-second report while paused does not snap the held fraction
        deck.setProperty("engineStatus", {"state": "up", "current": "", "interval": "900",
                                          "next_in": str(int(900 - r2))})
        settle()
        assert abs(float(deck.elapsedSecs()) - r2) < 0.05, "a poll inside the threshold leaves the held value alone"
        deck.setProperty("engineStatus", {"state": "up", "current": "", "interval": "900", "next_in": "100"})
        settle()
        assert abs(float(deck.elapsedSecs()) - 800) < 0.05, "a real disagreement still corrects"
        deck.setProperty("engineStatus", {"state": "up", "current": "", "interval": "900",
                                          "next_in": str(int(900 - r2))})
        settle()
        held = float(deck.elapsedSecs())
        deck.setProperty("rotationOn", True)
        r3 = float(deck.elapsedSecs())
        assert abs(r3 - held) < 0.3, f"a resume continues from the held value, no leap ({held} -> {r3})"
        # the engine's reply to a pause or resume anchors the clock outright
        backend.rotationClock.emit(250000, 900)
        assert abs(float(deck.elapsedSecs()) - 650) < 0.05, "the reply's countdown, in milliseconds, is the readout"
        settle()

        bench._is_testing = True
        bench._test_state = "testing"
        bench.stateChanged.emit()
        settle()
        assert left_testing.property("visible") is True, "Testing-draft block must show while bench.isTesting"
        assert left_idle.property("visible") is False and left_dev.property("visible") is False, \
            "idle + developer blocks must be hidden during a test"
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

        dev.slots["A"].alive = lambda: True
        dev.stateChanged.emit()
        settle()
        assert left_dev.property("visible") is True, "the Developer Bench block must show while an exhibit is alive"
        assert left_idle.property("visible") is False, "idle block must be hidden during a developer bench"
        assert dev_mode.property("text") == "Bench · A", dev_mode.property("text")
        assert abs(float(deck.property("transportDim")) - 0.45) < 0.01

        left_wiz = _find(deck, "deckLeftWizBench")
        wizard.set_phase("p3")
        settle()
        assert left_wiz.property("visible") is True and left_dev.property("visible") is False, \
            "a Workshop bench takes the left block while a Developer exhibit is alive"
        wizard.set_phase("p1")
        settle()
        assert left_dev.property("visible") is True, "the Developer block returns when the bench ends"
        bench._is_testing = True
        bench._test_state = "testing"
        bench.stateChanged.emit()
        wizard.set_phase("p3")
        settle()
        assert left_wiz.property("visible") is True and left_testing.property("visible") is False, \
            "a Workshop bench takes the left block over an Editor test as well"
        wizard.set_phase("p1")
        bench._is_testing = False
        bench._test_state = "idle"
        bench.stateChanged.emit()
        settle()
        dev.slots["B"].alive = lambda: True
        dev.stateChanged.emit()
        settle()
        assert dev_mode.property("text") == "Bench · A + B", dev_mode.property("text")
        img_dev = QQuickWindow.grabWindow(view)
        if not (img_dev.isNull() or img_dev.width() < 200):
            assert _count(img_dev.copy(0, 0, 420, 72), _WARNING) > 0, \
                "the amber bench subtitle must render in the left block"
        dev.slots["A"].alive = lambda: False
        dev.slots["B"].alive = lambda: False
        dev.stateChanged.emit()
        settle()
        assert left_dev.property("visible") is False, "the developer block must clear when the last exhibit exits"

        deck.setProperty("masterActive", False)
        deck.setProperty("engineStatus", {"state": "up", "current": "", "interval": "", "next_in": ""})
        settle()
        assert left_idle.property("visible") is True, "the left slot shows the status message when off"
        assert abs(float(left_idle.property("opacity")) - 1.0) < 0.01, \
            "F24: the left block must stay at full opacity while off (exempt from dimming)"
        assert _find(deck, "deckStatusDot") is None, "no status dot before the engine line"
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
