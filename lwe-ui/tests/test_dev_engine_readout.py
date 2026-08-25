"""The Developer area's session bar shows the LIVE DAEMON, not just its own bench.

Previously that surface derived everything from bench children the panel spawned,
so it read as dead whenever no bench was running - which is the normal state while
developing against a daemon that is right there. It now renders the engine leaves carried
on the existing 2 s status poll (no extra socket call).

The rule the tests pin: an absent engine is NOT a value. Each part hides when the daemon
has not reported it, rather than printing a dash that looks like a reading of zero.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software \
     python3 tests/test_dev_engine_readout.py
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

# Imported for its SIDE EFFECT, and it is load-bearing: PySide6 decides the wrapper class
# for an object at the moment it is handed out, and without QtQuick loaded, findChildren
# returns bare QObjects with no childItems(). The visual walk below then silently reports an
# empty tree for a UI that is rendering correctly. Do not "clean up" this import.
from PySide6.QtQuick import QQuickItem  # noqa: F401,E402


def _find(root, name):
    from PySide6.QtCore import QObject
    return next((o for o in root.findChildren(QObject) if o.objectName() == name), None)


def _texts(node) -> str:
    """Everything the readout actually renders, joined.

    Walks VISUAL children (QQuickItem.childItems), not QObject children. findChildren does
    NOT see Repeater delegates - they are visual children of the Repeater's parent while
    their QObject ownership sits elsewhere - so a findChildren-based helper reports an empty
    row for a Repeater that is working perfectly. That cost a wrong diagnosis here: the UI
    was correct and the test was blind.
    """
    out = []

    def walk(item):
        # duck-typed rather than isinstance(QQuickItem): PySide6 hands back wrappers whose
        # concrete class varies with how the object was reached, and an isinstance gate here
        # silently returned nothing for a tree that was rendering correctly.
        kids = getattr(item, "childItems", None)
        if kids is None:
            return
        for child in kids():
            if child.property("visible"):
                txt = child.property("text")
                if isinstance(txt, str) and txt.strip() not in ("", "."):
                    out.append(txt)
            walk(child)

    walk(node)
    return " | ".join(out)


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-devstatus-")
    orig = {k: os.environ.get(k) for k in
            ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
    try:
        os.environ["HOME"] = home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
        os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
        os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlComponent, QQmlEngine, qmlRegisterSingletonInstance
        from PySide6.QtTest import QTest
        from lwe_ui import bench_bridge
        from lwe_ui.models import Backend, ThemeTokens
        from lwe_ui.editor import EditorBridge
        from lwe_ui.dev import DevBridge
        from lwe_ui.storage import paths, settings
        from lwe_ui.app import _resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME

        paths.ensure_dirs()
        settings.ensure_exists()

        app = QGuiApplication.instance() or QGuiApplication(["t"])
        tokens = ThemeTokens(_resolve_theme_tokens())
        qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)

        # QQmlComponent rather than QQuickView: a QQuickView does not size its root item
        # unless shown, so the session bar had width 0 and every anchored gap inside it
        # computed negative. Creating the item directly and setting a width is both simpler
        # and the pattern the other DevView test already uses.
        engine = QQmlEngine()
        engine.addImportPath(str(_QML_DIR))
        for n, o in (("backend", Backend()), ("editor", EditorBridge()),
                     ("bench", bench_bridge.BenchBridge()), ("dev", DevBridge())):
            engine.rootContext().setContextProperty(n, o)
        comp = QQmlComponent(engine, QUrl.fromLocalFile(str(_QML_DIR / "DevView.qml")))
        root = comp.create()
        assert root is not None, comp.errorString()
        root.setProperty("width", 1600)
        root.setProperty("height", 700)

        def settle():
            for _ in range(3):
                app.processEvents()
            QTest.qWait(50)


        settle()
        readout = _find(root, "devEngineReadout")
        assert readout is not None, "the session bar must carry an engine readout"

        # 1. no daemon answer at all -> the whole readout hides. A dev area with no engine
        #    must not draw a row of dashes that look like measurements.
        assert readout.property("visible") is False, \
            "with no status the readout must hide, not print placeholders"

        root.setProperty("engineStatus", {
            "state": "up", "outputs_state": "live", "outputs_reason": "",
            "manual_pause": False, "fullscreen_pause": False,
            "rotation_count": 54, "rotation_pos": 19,
            "uptime_s": 19059, "clients": 1,
        })
        settle()
        assert readout.property("visible") is True, "a live daemon must show"
        shown = _texts(readout)
        assert "outputs live" in shown, f"outputs state must render, got: {shown}"
        assert "history 19/54" in shown, f"rotation position must render, got: {shown}"
        assert "up 5h17m" in shown, f"19059s must format as 5h17m, got: {shown}"
        assert "1 client" in shown and "1 clients" not in shown, \
            f"the client count must be singular at 1, got: {shown}"
        assert "paused" not in shown, f"an unpaused engine must not say paused: {shown}"

        # 3. released outputs with no reason must SAY the reason is missing rather than
        #    rendering an empty parenthetical that reads like "released ()"
        root.setProperty("engineStatus", {
            "state": "up", "outputs_state": "released", "outputs_reason": "",
            "manual_pause": False, "fullscreen_pause": False,
        })
        settle()
        shown = _texts(readout)
        assert "reason unreported" in shown, \
            f"released-with-no-reason must say so, not render an empty (), got: {shown}"

        # 4. paused is a warning state, and fullscreen-pause names itself
        root.setProperty("engineStatus", {
            "state": "up", "outputs_state": "live", "outputs_reason": "",
            "manual_pause": False, "fullscreen_pause": True,
        })
        settle()
        shown = _texts(readout)
        assert "paused (fullscreen)" in shown, \
            f"a fullscreen pause must name its cause, got: {shown}"

        # 5. engine down: the readout still shows (the dot goes gray) because "down" is
        #    itself information the developer needs, unlike "never answered"
        root.setProperty("engineStatus", {"state": "down"})
        settle()
        shown = _texts(readout)
        assert readout.property("visible") is True, \
            "a DOWN engine is a fact worth showing; only an unanswered one hides"
        assert "engine down" in shown, f"a down engine must say so, got: {shown}"
        assert "outputs" not in shown and "up " not in shown, \
            f"a down engine must not report stale leaves, got: {shown}"

        print("OK test_dev_engine_readout - session bar reflects the live daemon; "
              "absent status hides rather than printing dashes; down still reports")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
