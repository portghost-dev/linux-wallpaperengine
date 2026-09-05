"""Render + wiring test for the Developer view QML.

Mounts DeveloperView with the real bridges (sandboxed HOME/XDG, bench_courier stubbed, no
engine spawns; the journal follower is stubbed out) and checks what a static lint cannot:
  * the three columns mount at the flagship geometry (Setup 500, Isolator 320, Console flex)
  * the toggle grid and instrument grid hold every table row, one switch per side
  * the isolator lists the slot's scene objects, grouped, with disabled switches while no
    exhibit is live
  * console lines land tagged, stderr marked, and the source picker filters display only
  * the compact law collapses the columns to one pane and the segment switches panes

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software python3 tests/test_developer_view_ui.py
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import json
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


def _items(item) -> list:
    out = []
    for c in item.childItems():
        out.append(c)
        out.extend(_items(c))
    return out


def _switches(item) -> list:
    return [o for o in _items(item)
            if o.metaObject().indexOfProperty("pillWidth") >= 0 and o.property("visible") is True]


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-devview-")
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
        from lwe_ui.dev import DevBridge, FEATURE_TOGGLES, RENDER_DEBUG_FLAGS, INSTRUMENTS
        from lwe_ui.storage import paths, settings
        from lwe_ui.app import _resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME

        paths.ensure_dirs()
        settings.ensure_exists()
        bench_courier.available = lambda: True
        bench_courier.standdown = lambda *a, **k: True
        bench_courier.resume = lambda *a, **k: True
        DevBridge.setFollowingDaemon = lambda self, on: None

        wp = Path(paths.default_wallpapers_dir()) / "111"
        wp.mkdir(parents=True)
        (wp / "project.json").write_text(json.dumps({"title": "Dead Space", "type": "scene", "file": "scene.json"}))
        (wp / "scene.json").write_text(json.dumps({"objects": [
            {"id": 453, "name": "Dust motes", "particle": "x"},
            {"id": 475, "name": "Fog", "image": "x"},
            {"id": 476, "name": "Fog", "image": "x"},
            {"id": 512, "name": "Warp core", "image": "x", "parent": 475},
        ]}))

        app = QGuiApplication.instance() or QGuiApplication(["t"])
        tokens = ThemeTokens(_resolve_theme_tokens())
        qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)

        backend = Backend()
        editor = EditorBridge()
        bench = bench_bridge.BenchBridge()
        dev = DevBridge()
        dev.setScene("A", "111")
        dev.setScene("B", "111")
        dev.slots["B"].last_code = 3
        dev.slots["B"].last_ts = "09:40"
        dev.slots["B"].last_tail = [("old line", False)]

        view = QQuickView()
        view.engine().addImportPath(str(_QML_DIR))
        for name, obj in (("backend", backend), ("editor", editor), ("bench", bench), ("dev", dev)):
            view.rootContext().setContextProperty(name, obj)
        view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        view.setSource(QUrl.fromLocalFile(str(_QML_DIR / "DeveloperView.qml")))
        assert view.status() == QQuickView.Status.Ready, [e.toString() for e in view.errors()]
        view.resize(1216, 596)
        view.show()

        def settle(ms=80):
            for _ in range(3):
                app.processEvents()
            QTest.qWait(ms)

        settle(200)
        root = view.rootObject()
        con_col = _find(root, "devConsoleColumn")
        assert con_col.lineCount("B") == 2, "a persisted tail replays once at startup under its header"

        setup = _find(root, "devSetupColumn")
        iso = _find(root, "devIsolatorColumn")
        con = _find(root, "devConsoleColumn")
        assert setup and iso and con, "three columns must mount"
        assert int(setup.property("width")) == 500 and int(iso.property("width")) == 320, \
            (setup.property("width"), iso.property("width"))
        assert int(iso.property("x")) == 501 and int(con.property("x")) == 822
        assert int(con.property("width")) == 1216 - 822

        grid = _find(root, "devToggleGrid")
        n_rows = len(FEATURE_TOGGLES) + len(RENDER_DEBUG_FLAGS)
        assert len(_switches(grid)) == 2 * n_rows, \
            f"toggle grid must hold two switches per row: {len(_switches(grid))} for {n_rows}"
        inst = _find(root, "devInstrumentGrid")
        assert len(_switches(inst)) == 2 * len(INSTRUMENTS)

        lst = _find(root, "devIsolatorList")
        assert lst.property("count") == 5, f"2 singles + group header + 2 members: {lst.property('count')}"
        masters = _find(root, "devIsolatorMasters")
        assert all(sw.property("enabled") is True for sw in _switches(masters)), \
            "isolator switches edit a stopped side once it has a scene"

        pop = _find(_find(root, "devSlotA"), "devScenePopup")
        pop.open()
        settle()
        scene_list = _find(_find(root, "devSlotA"), "devSceneList")
        assert scene_list.property("count") == 3, \
            f"scene popup lists the library, a rule and the empty probes item: {scene_list.property('count')}"
        entries = pop.entries().toVariant()
        assert entries[0]["wid"] == "111" and entries[1]["kind"] == "rule" and entries[2]["kind"] == "empty", entries
        pop.close()
        settle()
        dev.setScene("B", "")
        settle()
        assert [o for o in _items(_find(root, "devSlotB")) if o.property("text") == "Scene"], \
            "an empty slot shows the Scene placeholder"
        dev.setScene("B", "111")
        settle()
        assert [o for o in _items(_find(root, "devSlotB")) if o.property("text") == "Dead Space"], \
            "a chosen scene names the slot"

        state_a = _find(_find(root, "devSlotA"), "devSlotState")
        assert state_a.property("text") == "stopped"

        dev.slots["A"].last_code = 139
        dev.slots["A"].last_ts = "09:41"
        dev.slots["A"].last_tail = [("GL context lost", True)]
        dev.stateChanged.emit()
        settle()
        assert state_a.property("text") == "exit 139"
        residue = _find(_find(root, "devSlotA"), "devSlotResidue")
        assert "Last run · exit " in residue.property("text") and "139" in residue.property("text")

        dev.consoleLine.emit("A", "present fence ok", False)
        dev.consoleLine.emit("B", "GL context lost, reinit", True)
        dev.consoleLine.emit("D", "daemon line", False)
        settle()
        shows = lambda src: con_col.shows(src)
        assert shows("A") and shows("B") and not shows("D"), "Both shows the exhibits and hides the daemon"
        con_col.setProperty("source", 3)
        assert shows("D") and not shows("A")
        con_col.setProperty("source", 0)
        assert shows("A") and not shows("B")

        con_col.setProperty("source", 2)
        dev.runStarted.emit("A")
        settle()
        assert con_col.lineCount("A") == 0 and con_col.lineCount("B") == 3, \
            "a launch clears only that side's console lines (B keeps its replayed tail and its line)"
        dev.consoleLine.emit("A", "=================================", False)
        dev.consoleLine.emit("A", "Beginning new bench run 2026.09.03 17:53:53", False)
        settle()
        assert con_col.lineCount("A") == 2

        root.setProperty("compactBelow", 5000)
        settle()
        assert setup.property("visible") is True and iso.property("visible") is False \
            and con.property("visible") is False, "compact shows one pane"
        assert int(setup.property("width")) == 1216
        root.setProperty("pane", 1)
        settle()
        assert iso.property("visible") is True and setup.property("visible") is False
        assert int(iso.property("width")) == 1216

        view.hide()
        print("OK test_developer_view_ui - three columns at flagship geometry, full toggle + "
              "instrument grids, grouped isolator, tagged console, compact panes")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
