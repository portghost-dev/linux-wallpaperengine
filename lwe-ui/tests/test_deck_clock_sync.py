"""The deck's countdown readout across pause and resume, driven through the real click path
(backend.setPaused -> settings -> sync -> the engine's lanes-set reply) against an engine modelled
on Api/Lane.cpp's millisecond law, at several clock phases. The readout must never step back and
must advance by one second per second: the judder seen on the desk came from whole-second anchoring, which
this pins as gone.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software python3 tests/test_deck_clock_sync.py
"""
from __future__ import annotations

import _sandbox  # noqa: F401
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

IV = 900


class EngineModel:
    """Lane.cpp in miniature: elapsed and frozen remainder in milliseconds, whole-second next_in_s."""

    def __init__(self, now: float, elapsed0: float) -> None:
        self.last_show = now - elapsed0
        self.frozen_ms = -1

    def next_in_ms(self, now: float) -> int:
        if self.frozen_ms >= 0:
            return self.frozen_ms
        return max(0, IV * 1000 - int((now - self.last_show) * 1000))

    def pause(self, now: float) -> int:
        self.frozen_ms = self.next_in_ms(now)
        return self.frozen_ms

    def resume(self, now: float) -> int:
        self.last_show = now - (IV * 1000 - self.frozen_ms) / 1000
        self.frozen_ms = -1
        return self.next_in_ms(now)

    def snapshot(self, now: float) -> dict:
        ms = self.next_in_ms(now)
        return {"state": "up", "current": "x", "interval": str(IV), "next_in": str(ms // 1000),
                "next_in_ms": str(ms), "outputs_state": "live"}


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-clock-")
    orig = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
    try:
        os.environ["HOME"] = home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
        os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
        os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")
        from PySide6.QtCore import QUrl, QObject, Signal, Slot
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQuick import QQuickView
        from PySide6.QtTest import QTest
        from PySide6.QtQml import qmlRegisterSingletonInstance
        from lwe_ui import api_client, bench_bridge, bench_courier, version
        from lwe_ui.models import Backend, ThemeTokens
        from lwe_ui.editor import EditorBridge
        from lwe_ui.dev import DevBridge
        from lwe_ui.deck_popup import DeckPopupBridge
        from lwe_ui.storage import paths, playlists, settings
        from lwe_ui.app import _resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME

        paths.ensure_dirs()
        settings.ensure_exists()
        playlists.save("main", {"NAME": "Main", "MODE": "shuffle", "INTERVAL": IV, "UNIT": "min", "MEMBERS": "111"})
        settings.update({"ACTIVE_PLAYLIST": "main"})
        bench_courier.available = lambda: True
        app = QGuiApplication.instance() or QGuiApplication(["t"])
        tokens = ThemeTokens(_resolve_theme_tokens())
        qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)
        backend = Backend()

        class StubWizard(QObject):
            phaseChanged = Signal()

            @Slot(result=str)
            def phase(self):
                return "p1"

            @Slot(result=str)
            def wid(self):
                return ""

            @Slot(result=str)
            def wpTitle(self):
                return ""

            @Slot(result=str)
            def wid(self):
                return ""

            @Slot(result=str)
            def wpTitle(self):
                return ""

            @Slot()
            def close(self):
                pass

            @Slot()
            def killBench(self):
                pass

            @Slot(result=int)
            def benchLoadRemaining(self):
                return -1

        view = QQuickView()
        view.engine().addImportPath(str(_QML_DIR))
        ctx = view.rootContext()
        for n, o in (("backend", backend), ("editor", EditorBridge()), ("bench", bench_bridge.BenchBridge()),
                     ("dev", DevBridge()), ("wizardBridge", StubWizard()), ("deckPopup", DeckPopupBridge(backend))):
            ctx.setContextProperty(n, o)
        view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        view.setSource(QUrl.fromLocalFile(str(_QML_DIR / "Deck.qml")))
        assert view.status() == QQuickView.Status.Ready, [e.toString() for e in view.errors()]
        view.resize(1280, 72)
        deck = view.rootObject()
        deck.setProperty("width", 1280)
        label = next(o for o in deck.findChildren(QObject) if o.objectName() == "deckElapsed")

        # the engine, answering the real click path: lanes-set replies with the envelope shape
        eng = {"m": None}
        api_client.available = lambda: True
        api_client.status = lambda *a, **k: {"api": 1, "version": version.panel_stamp(), "pid": 1, "current": {"id": "", "ui_id": ""}}
        api_client.playlist_set = lambda *a, **k: {"ok": True, "status": "done"}
        api_client.schedule_set = lambda *a, **k: {"ok": True, "status": "done"}

        def lanes_set(lanes):
            enabled = bool(lanes[0].get("enabled", True))
            m = eng["m"]
            now = time.monotonic()
            if not enabled and m.frozen_ms < 0:
                m.pause(now)
            elif enabled and m.frozen_ms >= 0:
                m.resume(now)
            return {"ok": True, "status": "done",
                    "result": {"lanes": [{"id": "all", "next_in_ms": m.next_in_ms(now), "interval_s": IV}]}}
        api_client.lanes_set = lanes_set

        def settle():
            for _ in range(2):
                app.processEvents()
            QTest.qWait(10)

        worst_back = 0.0
        worst_jump = 0.0
        cycles = 0
        deck.setProperty("masterActive", True)
        for anchor_phase in (0.05, 0.35, 0.65, 0.95):
            for click_phase in (0.15, 0.5, 0.85):
                m = EngineModel(time.monotonic(), 600.0 + anchor_phase)
                eng["m"] = m
                backend._set_setting("ROTATION_ENABLED", True)
                deck.setProperty("engineStatus", m.snapshot(time.monotonic()))
                settle()
                series = []
                t_start = time.monotonic()
                next_poll = t_start + 2.0
                did_pause = did_resume = False
                while time.monotonic() - t_start < 4.2:
                    t = time.monotonic()
                    if t >= next_poll:
                        deck.setProperty("engineStatus", m.snapshot(t))
                        next_poll += 2.0
                    if not did_pause and t - t_start >= 0.6 + click_phase:
                        backend.setPaused(True)   # the click: settings, sync, the engine's reply
                        deck.refreshRotation()
                        did_pause = True
                    if did_pause and not did_resume and t - t_start >= 2.3 + click_phase:
                        backend.setPaused(False)
                        deck.refreshRotation()
                        did_resume = True
                    txt = str(label.property("text"))
                    mm, ss = txt.split(":") if ":" in txt else ("0", "0")
                    series.append((t - t_start, int(mm) * 60 + int(ss) if ss.isdigit() else -1))
                    QTest.qWait(50)
                cycles += 1
                prev = None
                last_tick_t = None
                for t, val in series:
                    if prev is not None and val >= 0 and prev >= 0:
                        if val < prev:
                            worst_back = max(worst_back, prev - val)
                        if val > prev + 1:
                            worst_jump = max(worst_jump, val - prev)
                        if val == prev + 1:
                            # a running readout ticks once a second: a tick sooner than that
                            # after the previous one is a spurious step at the click
                            if last_tick_t is not None and t - last_tick_t < 0.7 and not (0.6 + click_phase <= t <= 2.3 + click_phase):
                                raise AssertionError(
                                    f"ticks {t - last_tick_t:.2f}s apart (anchor {anchor_phase}, click {click_phase}): {series}")
                            last_tick_t = t
                    prev = val if val >= 0 else prev
                assert worst_back == 0, (
                    f"the readout stepped back by {worst_back}s (anchor phase {anchor_phase}, "
                    f"click phase {click_phase}): {series}")
                assert worst_jump == 0, (
                    f"the readout jumped by {worst_jump}s (anchor phase {anchor_phase}, "
                    f"click phase {click_phase}): {series}")
        print(f"OK test_deck_clock_sync - {cycles} pause/resume cycles across clock phases: "
              f"no step back, no jump, one second per second")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
