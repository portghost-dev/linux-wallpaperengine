"""App-load smoke test: Main.qml (the whole UI tree) must instantiate to rootObjects==1 with no
QML errors. This is the guard that would have caught the editor's broken inline-component-in-a-
singleton regression (it loaded clean under qmllint but failed to *instantiate* at runtime - a
class of bug a load-smoke catches but a static lint does not).

Runs headless (offscreen + software). Registers Theme + backend + editor exactly like app.main.
Config + state are sandboxed to a tempfile tree seeded from the real tags.csv so nothing live is
written. "Unqualified access" warnings for the Python-registered context properties (backend/editor)
are expected and excluded.
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import os
import shutil
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-appload-")
    orig = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
    try:
        os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "config")
        os.environ["XDG_STATE_HOME"] = os.path.join(home, "state")
        # leave XDG_DATA_HOME at the real default so the library is scanned read-only
        os.makedirs(os.path.join(home, "config", "lwe"), exist_ok=True)
        real_tags = os.path.expanduser("~/.config/lwe/tags.csv")
        if os.path.exists(real_tags):
            shutil.copy(real_tags, os.path.join(home, "config", "lwe", "tags.csv"))

        from PySide6.QtCore import QUrl, QTimer, QObject
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance
        from lwe_ui.models import Backend, ThemeTokens
        from lwe_ui.editor import EditorBridge
        from lwe_ui.bench_bridge import BenchBridge
        from lwe_ui.dev import DevBridge
        from lwe_ui import bench_courier
        from lwe_ui.storage import paths, settings
        from lwe_ui.app import _resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME

        # STUB the courier before constructing BenchBridge so bench.open() never touches the
        # LIVE engine socket (sandbox contract - same as the other bench tests). Unavailable
        # keeps benchAvailable False; the couriers are no-ops regardless.
        bench_courier.available = lambda: False
        # the spawn-path engines-clear wait must not poll the LIVE engine in tests
        bench_courier.wait_clear = lambda *a, **k: True
        bench_courier.standdown = lambda *a, **k: True
        bench_courier.resume = lambda *a, **k: True

        paths.ensure_dirs()
        settings.ensure_exists()
        app = QGuiApplication.instance() or QGuiApplication(["test"])
        tokens = ThemeTokens(_resolve_theme_tokens())
        qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)
        engine = QQmlApplicationEngine()
        engine.addImportPath(str(_QML_DIR))
        errors: list[str] = []
        engine.warnings.connect(lambda es: [errors.append(e.toString()) for e in es])
        backend = Backend()
        editor = EditorBridge()
        bench = BenchBridge()
        dev = DevBridge()
        engine.rootContext().setContextProperty("backend", backend)
        engine.rootContext().setContextProperty("editor", editor)
        # the deck gear's settings popup bridge (POPUP-SPEC-v2.1) - Deck.qml mounts the popup,
        # so the tree does not resolve without it
        from lwe_ui.deck_popup import DeckPopupBridge
        deck_popup = DeckPopupBridge(backend)
        engine.rootContext().setContextProperty("deckPopup", deck_popup)
        engine.rootContext().setContextProperty("bench", bench)
        engine.rootContext().setContextProperty("dev", dev)
        from lwe_ui.workshop import WorkshopBridge
        from lwe_ui.models import ImportBridge
        workshop = WorkshopBridge(backend, dev)
        import_bridge = ImportBridge(backend)
        engine.rootContext().setContextProperty("workshop", workshop)
        engine.rootContext().setContextProperty("importBridge", import_bridge)
        # the Settings surface's own bridge: every page reads its
        # values and rides its change signal, so the tree does not resolve without it
        from lwe_ui.settings_bridge import SettingsBridge
        settings_bridge = SettingsBridge(backend, import_bridge)
        engine.rootContext().setContextProperty("settingsBridge", settings_bridge)
        from lwe_ui.wizard_bridge import WizardBridge
        _wizb = WizardBridge(engine.rootContext().contextProperty("backend"),
                             engine.rootContext().contextProperty("workshop"))
        engine.rootContext().setContextProperty("wizardBridge", _wizb)
        engine.load(QUrl.fromLocalFile(str(_QML_DIR / "Main.qml")))

        roots = len(engine.rootObjects())
        real_errors = [e for e in errors if "Unqualified access" not in e]
        # a status poke reads the engine at once: a control that changed engine state shows the
        # engine's word this frame, not at the poke timer's first tick
        if roots == 1:
            from PySide6.QtCore import QPointF
            from PySide6.QtQuick import QQuickItem  # noqa: F401  (registers the item converters)
            window = engine.rootObjects()[0]
            window.setProperty("engineStatus", {"marker": "stale"})
            backend.statusChanged.emit()
            assert "marker" not in dict(window.property("engineStatus") or {}), \
                "statusChanged must replace the deck's status immediately"
            # the root layout: header and deck span the window, the rail sits in the band between
            rail = next(o for o in window.findChildren(QObject) if o.metaObject().className().startswith("Rail"))
            hdr = window.findChild(QObject, "headerBar")
            dk = window.findChild(QObject, "deckBar")
            assert float(hdr.x()) == 0 and float(hdr.width()) == float(window.width()), "the header spans the window"
            assert float(dk.x()) == 0 and float(dk.width()) == float(window.width()), "the deck spans the window"
            assert abs(float(rail.y()) - float(hdr.height())) < 0.5 and abs(float(rail.y()) + float(rail.height()) - float(dk.y())) < 0.5, \
                "the rail occupies the band between the header and the deck"
            rail_src = (_QML_DIR / "Rail.qml").read_text(encoding="utf-8")
            assert "width: 17; height: 17; radius: 5" not in rail_src, "the rail's app mark is gone"
            # the active indicator sits on the active item: its centre is the All item's centre
            all_item = next(o for o in rail.findChildren(QObject) if o.property("label") == "All")
            bar = next(o for o in rail.childItems() if o.metaObject().className().startswith("QQuickRectangle") and int(o.width()) == 2 and int(o.height()) == 18)
            assert abs((float(bar.y()) + 9) - (all_item.mapToItem(rail, QPointF(0, all_item.height() / 2)).y())) < 0.5, \
                "the rail's accent bar lines up with the active item"
            # the transport sits at the midpoint between the left block and the pill
            row = next(o for o in dk.findChildren(QObject) if o.objectName() == "deckTransportRow")
            row_c = row.mapToItem(dk, QPointF(row.width() / 2, 0)).x()
            assert abs(row_c - float(dk.property("midpoint"))) <= 1.0, "the transport centres between the left block and the pill"
            # the header's search: a press on the library grid, a Flickable that accepts its
            # own presses, still takes the cursor out (the application-level press listener),
            # and Escape with a live term and no cursor clears it from the window
            from PySide6.QtCore import Qt
            from PySide6.QtTest import QTest
            from lwe_ui.app import install_search_focus_filter
            _flt = install_search_focus_filter(app, window)  # noqa: F841
            window.show()
            QTest.qWaitForWindowExposed(window, 5000)
            header = window.findChild(QObject, "headerBar")
            btn = next(o for o in header.findChildren(QObject) if o.objectName() == "headerSearchBtn")
            field = next(o for o in header.findChildren(QObject) if o.objectName() == "headerSearch")
            grid = window.findChild(QObject, "libraryGrid")
            c = btn.mapToScene(QPointF(14, 14)).toPoint()
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c)
            QTest.qWait(260)
            for ch in "nebula":
                QTest.keyClick(window, ch)
            QTest.qWait(30)
            assert field.property("activeFocus") is True and header.property("query") == "nebula"
            g = grid.mapToScene(QPointF(grid.width() / 2, grid.height() - 4)).toPoint()
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, g)
            QTest.qWait(100)
            assert field.property("activeFocus") is False, "a press on the grid takes the cursor"
            assert header.property("searchOpen") is True, "a live term keeps the field open"
            QTest.keyClick(window, Qt.Key_Escape)
            QTest.qWait(260)
            assert header.property("query") == "" and header.property("searchOpen") is False, \
                "Escape with a live term and no cursor clears and folds from the window"

        import json
        wdir = str(paths.default_wallpapers_dir())
        scene = None
        if os.path.isdir(wdir):
            for d in sorted(os.listdir(wdir)):
                pj = os.path.join(wdir, d, "project.json")
                try:
                    if os.path.exists(pj) and json.load(open(pj)).get("type") == "scene":
                        scene = d
                        break
                except Exception:
                    continue
        if scene:
            editor.open(scene)
            QTimer.singleShot(200, app.quit)
            app.exec()
            real_errors = [e for e in errors if "Unqualified access" not in e]

        assert roots == 1, f"Main.qml must instantiate to exactly 1 root object, got {roots}"
        assert not real_errors, "QML load/runtime errors:\n  " + "\n  ".join(real_errors[:10])

        win = engine.rootObjects()[0]

        # Responsive law v1.6: the pinned-5-column rule is superseded by auto-fit. The
        # grid lays as many columns as fit at tile width in [minTile, maxTile]; measured on
        # the live GridView (its column count is derived from cellWidth). Tiles must never
        # be distorted (fatter than maxTile) or under-sized (thinner than minTile).
        grid = win.findChild(QObject, "libraryGrid")
        assert grid is not None, "library GridView not found by objectName"
        gw = float(grid.property("width"))
        cw = float(grid.property("cellWidth"))
        tile = float(grid.property("tileW"))
        rendered_cols = int(gw / cw) if cw else 0
        assert rendered_cols >= 1 and cw > 0, f"grid must lay >=1 column (width {gw}, cell {cw})"
        assert 216 <= tile <= 320, (
            f"tile width must clamp to [216,320], never distort: {tile} across "
            f"{rendered_cols} columns at grid width {gw}")

        print(f"OK test_app_load - Main.qml rootObjects=1, editor.open({scene}) clean, "
              f"responsive grid {rendered_cols} cols tile {tile:.0f}px, 0 QML errors")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
