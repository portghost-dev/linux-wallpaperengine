"""The library grid's delegates stay in step with the order model through drags and scrolls.

Loads the real Library.qml offscreen over a seeded library shaped like a full one (52 members,
22 pool, five columns), then drives the backend's drag API and the view's contentY the way a
pointer would, and after every step checks every instantiated delegate: its model id is the
model's id at that index, it is visible exactly when it is not a padding cell, and its laid
position plus the pool shift is the row and column the model puts it in. A view out of step with
the model is what a misaligned row looks like on screen.

Run: PYTHONPATH=src python3 tests/test_library_grid_sync.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lwe-gridsync-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QObject, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402, F401  (registers the item converters)
from PySide6.QtTest import QTest  # noqa: E402

from lwe_ui.storage import playlists, tags  # noqa: E402

IDS = [str(1000 + i) for i in range(74)]


def seed_library() -> None:
    wdir = Path(_TMP) / ".local/share/lwe/wallpapers"
    for wid in IDS:
        d = wdir / wid
        d.mkdir(parents=True, exist_ok=True)
        (d / "project.json").write_text(
            '{"title": "WP %s", "type": "scene", "file": "x", "preview": ""}' % wid, encoding="utf-8")
        tags.set_state(wid, "WP " + wid, "good")


def main() -> None:
    app = QGuiApplication([])  # noqa: F841
    seed_library()
    from lwe_ui.models import Backend, ThemeTokens
    from lwe_ui.app import _resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME
    tokens = ThemeTokens()
    tokens.set_tokens(_resolve_theme_tokens())
    qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)
    b = Backend()
    slug = playlists.active_slug()
    d = playlists.load(slug)
    d["MEMBERS"] = " ".join(IDS[:52])
    playlists.save(slug, d)
    b.refresh()
    b.playlistsChanged.emit()

    engine = QQmlApplicationEngine()
    problems: list[str] = []
    engine.warnings.connect(lambda ws: problems.extend(w.toString() for w in ws))
    engine.rootContext().setContextProperty("backend", b)
    host = Path(_QML_DIR) / "_gridsync_host.qml"
    host.write_text('''import QtQuick
import QtQuick.Window
import "."
Window { id: win; width: 1280; height: 700; visible: true
    Library { objectName: "lib"; anchors.fill: parent } }
''', encoding="utf-8")
    try:
        engine.load(QUrl.fromLocalFile(str(host)))
        assert engine.rootObjects(), problems
        win = engine.rootObjects()[0]
        grid = win.findChild(QObject, "libraryGrid")
        assert grid is not None
        QTest.qWait(200)
        om = b.orderModel

        def settle() -> None:
            QTest.qWait(260)  # longer than the move / displaced transitions

        def check(label: str) -> None:
            cols = int(grid.property("cols"))
            cell_w = float(grid.property("cellWidth"))
            cell_h = float(grid.property("cellHeight"))
            origin_y = float(grid.property("originY"))
            pool_off = int(grid.property("poolOffset"))
            hair = int(om.hairlineIndex)
            count = int(grid.property("count"))
            content_y = float(grid.property("contentY"))
            view_h = float(grid.property("height"))
            assert count == om.rowCount(), f"{label}: view count {count} != model {om.rowCount()}"
            seen = 0
            content = grid.property("contentItem")
            for item in content.childItems():
                if item.property("wpId") is None or item.property("index") is None:
                    continue  # the hairline and the slot outline, not delegates
                row = int(item.property("index"))
                if row < 0 or row >= count:
                    continue  # a delegate on its way out
                want_y_row = origin_y + (row // cols) * cell_h
                if want_y_row + cell_h < content_y or want_y_row > content_y + view_h:
                    continue  # cached off-screen: the view positions it when it scrolls in
                seen += 1
                wid = om.idAt(row)
                shown = str(item.property("wpId"))
                filler = wid == ""
                assert shown == wid, f"{label}: delegate at row {row} shows {shown!r}, model has {wid!r}"
                assert bool(item.property("visible")) == (not filler), \
                    f"{label}: row {row} visible={item.property('visible')} for {'a padding cell' if filler else wid}"
                want_x = (row % cols) * cell_w
                want_y = origin_y + (row // cols) * cell_h
                dx = abs(float(item.property("x")) - want_x)
                dy = abs(float(item.property("y")) - want_y)
                assert dx < 1 and dy < 1, f"{label}: row {row} laid at ({item.property('x')}, {item.property('y')}), want ({want_x}, {want_y})"
                # the pool shift rides on a transform: present exactly for rows from the hairline on
                shift = float(item.property("poolShift"))
                want_shift = pool_off if hair >= 0 and row >= hair else 0
                assert abs(shift - want_shift) < 1, f"{label}: row {row} shifted {shift}, want {want_shift}"
            assert seen > 10, f"{label}: only {seen} delegates instantiated"

        check("initial")
        assert int(grid.property("cols")) == 5, grid.property("cols")

        # the window starts narrower than it ends up, as a tiled launch does: the padding must
        # follow the column count the view settles on
        win.setProperty("width", 1000)
        settle()
        check("at 1000")
        assert int(grid.property("cols")) == 4, grid.property("cols")
        assert om.fillerCount == 0 and om.hairlineIndex == 52, (om.fillerCount, om.hairlineIndex)
        win.setProperty("width", 1280)
        settle()
        assert int(grid.property("cols")) == 5, grid.property("cols")
        assert om.fillerCount == 3 and om.hairlineIndex == 55, (om.fillerCount, om.hairlineIndex)
        check("back at 1280")

        # a pool card lifted, carried through the member block, the padding, the pool, and home
        assert b.beginDrag(IDS[60])
        for row in (0, 7, 23, 50, 51, 52, 53, 54, 55, 60, 70, om.rowCount(), -2, -1, 52):
            b.dragOver(row)
            settle()
            check(f"pool card over {row}")
        b.endDrag(True)
        settle()
        check("after insert")

        # a member carried around while the view scrolls under it, as the auto-scroll does
        assert b.beginDrag(IDS[3])
        max_y = float(grid.property("contentHeight")) - float(grid.property("height"))
        for step, row in enumerate((10, 30, 51, 53, 60, -2, 20, 0)):
            grid.setProperty("contentY", min(max_y, step * 150.0))
            b.dragOver(row)
            settle()
            check(f"member over {row} at contentY {step * 150}")
        b.endDrag(True)
        settle()
        grid.setProperty("contentY", 0.0)
        settle()
        check("after reorder")

        # the auto-scroll during a drag: contentY driven to the bottom step by step with a card
        # lifted; the view must still show its last rows, never run past its content
        assert b.beginDrag(IDS[20])
        b.dragOver(30)
        settle()
        max_y = float(grid.property("contentHeight")) - float(grid.property("height"))
        y = 0.0
        while y < max_y:
            y = min(max_y, y + 12.0)
            grid.setProperty("contentY", y)
            QTest.qWait(2)
        settle()
        shown = [it for it in grid.property("contentItem").childItems()
                 if it.property("wpId") not in (None, "") and it.property("visible")
                 and float(it.property("y")) + float(it.property("height")) > float(grid.property("contentY"))
                 and float(it.property("y")) < float(grid.property("contentY")) + float(grid.property("height"))]
        assert len(shown) >= 5, f"grid blank at the bottom: contentY {grid.property('contentY')} of {grid.property('contentHeight')}, {len(shown)} delegates in view"
        check("scrolled to the bottom while lifted")
        b.endDrag(False)
        settle()
        grid.setProperty("contentY", 0.0)
        settle()

        # checkbox traffic: one card each way, then a rescan
        b.setPlaylist(IDS[65], True)
        settle()
        check("after check")
        b.setPlaylist(IDS[10], False)
        settle()
        check("after uncheck")
        b.refresh()
        settle()
        check("after rescan")
        print("OK: library grid delegates stay in step with the order model through drags, scrolls, "
              "checkbox moves and a rescan")
    finally:
        host.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
