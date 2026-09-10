"""The library grid survives a horizontal resize that changes its column count.

Loads the real Library.qml offscreen over a seeded library whose member block is small enough
that the hairline band and the pool rows under it sit inside the viewport, then widens the
window the way a compositor does when a neighbouring tile closes: one jump, and a run of steps
one frame apart. A column change re-cuts the padding band, which reaches the view as row
removals and row insertions; those start the grid's displaced/move transitions. A transition
started under one cell geometry finishes at the position that geometry named, and the view does
not re-place an item whose transition is running, so a second resize step arriving inside
Motion.removeReflow used to leave the rows below the band laid out at the width the resize
passed through: upper rows at N columns, lower rows drawn over them at N+1.

After every step this checks EVERY live delegate against the grid's own cell arithmetic:
x = index % cols * cellWidth, y = originY + index / cols * cellHeight, and the band shift the
grid applies as a transform.

Run: PYTHONPATH=src python3 tests/test_library_resize.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lwe-resize-")
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
MEMBERS = 8          # small enough that the band and the pool rows under it are in the viewport
NARROW = 1280        # five columns
WIDE = 1560          # six columns
# the widths a compositor walks through while a neighbouring tile closes; the column change
# lands part way in, so the steps after it move the cell geometry under the running transitions
STEPS = (1300, 1360, 1420, 1480, 1520, 1560)


def seed_library() -> None:
    wdir = Path(_TMP) / ".local/share/lwe/wallpapers"
    for wid in IDS:
        d = wdir / wid
        d.mkdir(parents=True, exist_ok=True)
        (d / "project.json").write_text(
            '{"title": "WP %s", "type": "scene", "file": "x", "preview": ""}' % wid, encoding="utf-8")
        tags.set_state(wid, "WP " + wid, "good")


def main() -> None:
    app = QGuiApplication([])
    seed_library()
    from lwe_ui.models import Backend, ThemeTokens
    from lwe_ui.app import _resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME
    tokens = ThemeTokens()
    tokens.set_tokens(_resolve_theme_tokens())
    qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)
    b = Backend()
    slug = playlists.active_slug()
    d = playlists.load(slug)
    d["MEMBERS"] = " ".join(IDS[:MEMBERS])
    playlists.save(slug, d)
    b.refresh()
    b.playlistsChanged.emit()

    engine = QQmlApplicationEngine()
    problems: list[str] = []
    engine.warnings.connect(lambda ws: problems.extend(w.toString() for w in ws))
    engine.rootContext().setContextProperty("backend", b)
    host = Path(_QML_DIR) / "_resize_host.qml"
    host.write_text('''import QtQuick
import QtQuick.Window
import "."
Window { id: win; width: %d; height: 700; visible: true
    Library { objectName: "lib"; anchors.fill: parent } }
''' % NARROW, encoding="utf-8")
    try:
        engine.load(QUrl.fromLocalFile(str(host)))
        assert engine.rootObjects(), problems
        win = engine.rootObjects()[0]
        grid = win.findChild(QObject, "libraryGrid")
        assert grid is not None
        QTest.qWait(300)
        om = b.orderModel

        def settle() -> None:
            QTest.qWait(400)  # well past Motion.removeReflow (180 ms) and removeFade (120 ms)

        def check(label: str) -> None:
            """Every delegate the view has placed sits on the grid's own cell arithmetic."""
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
            wrong: list[str] = []
            seen = 0
            for item in grid.property("contentItem").childItems():
                if item.property("wpId") is None or item.property("index") is None:
                    continue  # the hairline and the slot outline, not delegates
                row = int(item.property("index"))
                if row < 0 or row >= count:
                    continue  # a delegate on its way out
                want_y = origin_y + (row // cols) * cell_h
                got_x, got_y = float(item.property("x")), float(item.property("y"))
                # a delegate matters when its wanted place OR its actual place is on screen: a
                # tile stuck at its old place is exactly the failure, wherever it belongs now
                on_screen = lambda y: not (y + cell_h < content_y or y > content_y + view_h)
                if not on_screen(want_y) and not on_screen(got_y):
                    continue  # cached off-screen: the view positions it when it scrolls in
                seen += 1
                want_x = (row % cols) * cell_w
                if abs(got_x - want_x) >= 1 or abs(got_y - want_y) >= 1:
                    wrong.append(f"row {row} laid at ({got_x:.0f}, {got_y:.0f}), want ({want_x:.0f}, {want_y:.0f})")
                    continue
                shift = float(item.property("poolShift"))
                want_shift = pool_off if hair >= 0 and row >= hair else 0
                if abs(shift - want_shift) >= 1:
                    wrong.append(f"row {row} shifted {shift}, want {want_shift}")
            assert seen >= 12, f"{label}: only {seen} delegates in the viewport"
            assert not wrong, (f"{label}: {len(wrong)} misplaced delegates at {cols} columns, "
                               f"cell {cell_w}x{cell_h}: " + "; ".join(wrong[:12]))

        def widen_in_steps() -> None:
            for w in STEPS:
                win.setProperty("width", w)
                QTest.qWait(16)   # one frame, as a compositor animates a tile closing

        check("initial")
        assert int(grid.property("cols")) == 5, grid.property("cols")
        assert om.hairlineIndex > 0, "the seed left no hairline band to re-cut"
        band_row = om.hairlineIndex
        assert (band_row // 5) * float(grid.property("cellHeight")) < float(grid.property("height")), \
            "the band is below the viewport: the resize would displace nothing on screen"

        # one jump, as a maximise does
        win.setProperty("width", WIDE)
        settle()
        assert int(grid.property("cols")) == 6, grid.property("cols")
        check("one-step widen to six columns")

        win.setProperty("width", NARROW)
        settle()
        check("back to five columns")

        # the compositor case: the column change lands mid-run and the steps after it move the
        # cell geometry while the re-cut's transitions would still be animating
        widen_in_steps()
        settle()
        assert int(grid.property("cols")) == 6, grid.property("cols")
        check("stepped widen to six columns")

        # and back down the same way
        for w in reversed(STEPS[:-1] + (NARROW,)):
            win.setProperty("width", w)
            QTest.qWait(16)
        settle()
        assert int(grid.property("cols")) == 5, grid.property("cols")
        check("stepped shrink to five columns")

        # a column change while a search's row edits are still animating, in both shapes:
        # one jump and stepped. "7" keeps a scattered fourteen of the rows, so the survivors
        # genuinely reflow; the resize lands inside Motion.removeReflow
        column_resets = om._column_resets
        b.setSearch("7")
        assert om.rowCount() < 40, "the search must remove rows for this case to mean anything"
        QTest.qWait(60)
        win.setProperty("width", WIDE)
        settle()
        assert int(grid.property("cols")) == 6, grid.property("cols")
        check("one-jump widen inside a search reflow")
        assert om._column_resets == column_resets + 1, "a column change inside the reflow window rebuilds"
        b.setSearch("")
        settle()
        win.setProperty("width", NARROW)
        settle()
        check("search cleared, back to five columns")
        b.setSearch("7")
        QTest.qWait(60)
        widen_in_steps()
        settle()
        check("stepped widen inside a search reflow")
        b.setSearch("")
        settle()
        win.setProperty("width", NARROW)
        settle()
        # a column change while a playlist membership move is still animating
        column_resets = om._column_resets
        slug = playlists.active_slug()
        d = playlists.load(slug)
        d["MEMBERS"] = d["MEMBERS"] + " 1050"
        playlists.save(slug, d)
        om.resync()
        QTest.qWait(60)
        win.setProperty("width", WIDE)
        settle()
        check("one-jump widen inside a membership move")
        assert om._column_resets == column_resets + 1
        win.setProperty("width", NARROW)
        settle()
        check("back to five columns after the move")
        # well after any reflow, a column change keeps its delegates (the guarded relayout)
        column_resets = om._column_resets
        win.setProperty("width", WIDE)
        settle()
        check("one-jump widen at rest")
        assert om._column_resets == column_resets, "a column change at rest is a relayout, not a reset"
        win.setProperty("width", NARROW)
        settle()

        # scrolled down, where the pool rows under the band carry the whole viewport
        win.setProperty("width", NARROW)
        settle()
        grid.setProperty("contentY", 300.0)
        settle()
        widen_in_steps()
        settle()
        check("stepped widen while scrolled")

        assert not problems, problems
        print("OK: the library grid lays every delegate on its own cell arithmetic through "
              "one-step and stepped column changes, with a search in flight and while scrolled")
    finally:
        host.unlink(missing_ok=True)
        engine.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    main()
