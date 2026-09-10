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


def _console_surface(app, root, dev, con_col, settle) -> None:
    """The console is one text surface: every line held is on it (source and filter aside),
    marks ride the stderr rows, the bars sit in the box padding, a copy yields raw lines, the
    count names the buffer, and Tail pins the picker to its side."""
    from PySide6.QtGui import QGuiApplication

    def shown_list():
        v = con_col.property("shown")
        return v.toVariant() if hasattr(v, "toVariant") else list(v)

    text = _find(root, "devConsoleText")
    flick = _find(root, "devConsoleFlick")
    box = _find(root, "devConsoleBox")
    count = _find(root, "devConsoleCount")
    marks = _find(root, "devConsoleMarks")

    n_shown = len(shown_list())
    assert n_shown == con_col.lineCount("A") + con_col.lineCount("B") == 5, n_shown
    plain = text.getText(0, 10 ** 8)
    assert plain.count("\u2029") == n_shown - 1, "one block per shown line, nothing elided"
    assert "GL context lost, reinit" in plain and "daemon line" not in plain
    assert count.property("text") == "6 lines", count.property("text")

    # the stderr rows carry the mark at their row, the others do not
    err_rows = [i for i, e in enumerate(shown_list()) if e["err"]]
    got = marks.property("rows")
    got = got.toVariant() if hasattr(got, "toVariant") else list(got)
    assert err_rows and got == err_rows, (err_rows, got)

    # the bars ride the padding: clear of the text on the right, of the box edge by 3
    vbar = _find(box, "devConsoleVBar")
    assert vbar.parentItem() is box
    assert int(vbar.property("x") + vbar.property("width")) == int(box.property("width")) - 3
    assert vbar.property("x") >= flick.property("x") + flick.property("width"), \
        ("the bar never covers the text", vbar.property("x"), vbar.property("width"), flick.property("x"),
         flick.property("width"), box.property("width"))

    # a burst past the box: the count grows, the buffer keeps all of it, the filter reads all of it
    dev._push([dev._entry("A", f"burst {i} {'odd' if i % 2 else 'even'}" + (" x" * 200 if i == 7 else ""),
                          i % 7 == 0) for i in range(300)])
    settle()
    assert count.property("text") == "306 lines"
    assert len(shown_list()) == 305
    row_h = con_col.property("rowH")
    assert row_h > 0 and abs(row_h - text.property("contentHeight") / 305) < 0.01, \
        "rows are uniform: a long line scrolls sideways, it never wraps"
    assert flick.property("contentWidth") > flick.property("width") + 100, "the long line widens the surface"
    con_col.setProperty("filter", "odd")
    settle()
    assert len(shown_list()) == 150, "the filter searches the whole buffer, not a window"
    assert count.property("text") == "306 lines", "the count names the buffer, not the view"
    con_col.setProperty("filter", "")
    settle()

    # the bar's handle keeps one size while scrolling: the surface height is exact
    sizes = set()
    step = max(1.0, (flick.property("contentHeight") - flick.property("height")) / 12)
    for k in range(13):
        flick.setProperty("contentY", k * step)
        settle(10)
        sizes.add(round(vbar.property("size"), 4))
    assert len(sizes) == 1, f"the handle size must not change while scrolling: {sorted(sizes)}"

    # a copy yields the raw lines under the selection, whole lines
    shown = shown_list()
    plain = text.getText(0, 10 ** 8)
    blocks = plain.split("\u2029")
    start = len("\u2029".join(blocks[:3])) + 4
    end = len("\u2029".join(blocks[:5])) - 2
    text.select(start, end)
    con_col.copySelection()
    app.processEvents()
    assert QGuiApplication.clipboard().text() == "\n".join(e["raw"] for e in shown[3:5]), \
        QGuiApplication.clipboard().text()[:120]

    # the user's own paths: a drag across two lines selects them inside the scrolling surface,
    # and Ctrl+C on the keyboard copies their raw text
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    win = text.window()
    flick.setProperty("contentY", 0)
    settle(20)
    origin = text.mapToScene(QPointF(30, row_h * 1.5))
    target = text.mapToScene(QPointF(120, row_h * 2.5))
    QTest.mousePress(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, origin.toPoint())
    for k in range(1, 6):
        QTest.mouseMove(win, (origin + (target - origin) * (k / 5)).toPoint())
        settle(10)
    QTest.mouseRelease(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target.toPoint())
    settle(20)
    sel = text.property("selectedText")
    assert sel.count("\u2029") == 1 and text.property("activeFocus") is True, \
        ("a drag selects across lines and the surface takes focus", sel[:80], flick.property("contentY"))
    assert flick.property("contentY") == 0, "the drag selected, it did not flick the surface"
    QGuiApplication.clipboard().setText("")
    QTest.keyClick(win, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    settle(20)
    assert QGuiApplication.clipboard().text() == "\n".join(e["raw"] for e in shown_list()[1:3]), \
        ("Ctrl+C yields the raw lines under the drag", QGuiApplication.clipboard().text()[:120])

    # Tail pins the picker to its side and puts the residue in view under its header
    dev.showTail("A")
    settle()
    assert con_col.property("source") == 0
    rows = shown_list()
    at = next(i for i, e in enumerate(rows) if e["residue"])
    assert at == len(rows) - 2 and rows[at]["text"] == "Last run \u00b7 exit 139 \u00b7 09:41", rows[at]
    assert rows[at]["time"] == " " * 8, "the header names its own time; the time column stays blank"
    assert rows[at + 1]["text"] == "GL context lost" and rows[at + 1]["err"] is True
    assert abs(flick.property("contentY") - min(at * row_h, flick.property("contentHeight") - flick.property("height"))) < 1, \
        "the view lands on the residue header"
    dev.showTail("A")
    settle()
    assert sum(1 for e in shown_list() if e["residue"]) == 2, "Tail twice shows the residue once"
    con_col.setProperty("source", 2)
    settle()


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
        seed = DevBridge()
        seed.setScene("A", "111")
        seed.setScene("B", "111")
        seed.slots["B"].last_code = 3
        seed.slots["B"].last_ts = "09:40"
        seed.slots["B"].last_tail = [("old line", False, "09:39:58")]
        seed._persist()
        dev = DevBridge()

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

        # the user's path: a click on a scene row of the open popup lands on that row alone, it
        # never reaches the Binary field the popup covers (a passive grab travelled through)
        from PySide6.QtCore import QPointF, Qt
        bin_menu = _find(_find(root, "devSlotA"), "devBinaryMenu")
        dev.setScene("A", "")
        settle()
        pop.open()
        settle()
        row = next(o for o in _items(scene_list) if o.metaObject().indexOfProperty("modelData") >= 0
                   and (o.property("modelData") or {}).get("wid") == "111")
        at = row.mapToScene(QPointF(row.property("width") / 2, row.property("height") / 2)).toPoint()
        bin_drop = next(o for o in _items(_find(root, "devSlotA")) if o.objectName() == "devBinaryField")
        tl = bin_drop.mapToScene(QPointF(0, 0)); br = bin_drop.mapToScene(QPointF(bin_drop.property("width"), bin_drop.property("height")))
        assert tl.x() <= at.x() <= br.x() and tl.y() <= at.y() <= br.y(), \
            ("the probe must land over the Binary field or it proves nothing", at, tl, br)
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
        settle(150)
        assert dev.slotState("A")["scene"] == "111", "the click chose the scene"
        assert pop.property("visible") is False, "the popup closed on the choice"
        assert bin_menu.property("visible") is False, "the Binary menu under the popup must not open"
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
        dev.slots["A"].last_tail = [("GL context lost", True, "09:40:59")]
        dev.stateChanged.emit()
        settle()
        assert state_a.property("text") == "exit 139"
        residue = _find(_find(root, "devSlotA"), "devSlotResidue")
        assert "Last run · exit " in residue.property("text") and "139" in residue.property("text")
        tail_btn = _find(_find(root, "devSlotA"), "devSlotTail")
        assert tail_btn.property("visible") is True, "a failed run offers Tail beside its exit code"

        dev._say("A", "present fence ok", False)
        dev._say("B", "GL context lost, reinit", True)
        dev._say("D", "daemon line", False)
        settle()
        shows = lambda src: con_col.shows(src)
        assert shows("A") and shows("B") and not shows("D"), "Both shows the exhibits and hides the daemon"
        con_col.setProperty("source", 3)
        assert shows("D") and not shows("A")
        con_col.setProperty("source", 0)
        assert shows("A") and not shows("B")

        con_col.setProperty("source", 2)
        dev._clear_side("A")
        settle()
        assert con_col.lineCount("A") == 0 and con_col.lineCount("B") == 3, \
            "a launch clears only that side's console lines (B keeps its replayed tail and its line)"
        dev._say("A", "=================================", False)
        dev._say("A", "Beginning new bench run 2026.09.03 17:53:53", False)
        settle()
        assert con_col.lineCount("A") == 2

        _console_surface(app, root, dev, con_col, settle)

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
        # drop the QML tree while the application lives: a focused text surface torn down
        # after the application by the interpreter's collector reaches a dead input method
        view.setSource(QUrl())
        settle()
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
