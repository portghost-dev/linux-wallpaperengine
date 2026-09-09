"""The library view stays square through a drag: a card lifted in the edge band does not nudge
the view, the band scrolls once the pointer has left it, and a drop lands on a row boundary.

Loads the real Library.qml offscreen over a seeded library (four members, seventy in the pool,
three rows showing) and drives a real pointer drag with QTest: press on a bottom-row pool card,
move past the lift threshold, hold inside the bottom 40 px band, leave it, come back, go up to
the member row, release. The view's contentY is read at every step. The old behaviour scrolled
the view down for the frames the pointer was still in the band after the lift, and nothing
squared it up afterwards, so the top row sat cut off under the edge.
Run: PYTHONPATH=src python3 tests/test_library_drop_scroll.py
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lwe-dropscroll-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QMetaObject, QObject, QPoint, QPointF, Qt, QUrl  # noqa: E402
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


def _walk(item):
    out = []
    for c in item.childItems():
        out.append(c)
        out.extend(_walk(c))
    return out


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
    d["MEMBERS"] = " ".join(IDS[:4])
    playlists.save(slug, d)
    b.refresh()
    b.playlistsChanged.emit()

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", b)
    host = Path(_QML_DIR) / "_dropscroll_host.qml"
    host.write_text('import QtQuick\nimport QtQuick.Window\nimport "."\n'
                    'Window { width: 1280; height: 620; visible: true\n'
                    '  Library { objectName: "lib"; anchors.fill: parent } }\n', encoding="utf-8")
    try:
        engine.load(QUrl.fromLocalFile(str(host)))
        assert engine.rootObjects(), "host failed to load"
        win = engine.rootObjects()[0]
        lib = win.findChild(QObject, "lib")
        grid = win.findChild(QObject, "libraryGrid")
        QTest.qWait(400)
        content_y = lambda: float(grid.property("contentY"))
        origin = float(grid.property("originY"))
        assert abs(content_y() - origin) < 0.5

        gh = float(grid.property("height"))
        gbot = grid.mapToScene(QPointF(0, 0)).toPoint().y() + gh
        cards = [c for c in _walk(grid.property("contentItem")) if c.property("wpId")]
        bottom = lambda c: c.mapToScene(QPointF(0, c.height())).toPoint().y()
        card = next(c for c in cards if gbot - 40 <= bottom(c) <= gbot + 2 and not c.property("inPlaylist"))
        p = card.mapToScene(QPointF(card.width() / 2, card.height() - 6)).toPoint()

        # lift inside the bottom band and hold: the view must not move
        QTest.mousePress(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
        for dx in range(4, 40, 6):
            QTest.mouseMove(win, p + QPoint(dx, 0), 16)
        QTest.qWait(300)
        assert lib.property("dragId") == card.property("wpId"), "the card lifted"
        assert abs(content_y() - origin) < 0.5, f"a lift inside the band must not scroll, got {content_y()}"
        assert lib.property("bandArmed") is False
        # leave the band, come back: now it scrolls
        for k in range(1, 6):
            QTest.mouseMove(win, p + QPoint(40, -20 * k), 16)
        QTest.qWait(100)
        assert lib.property("bandArmed") is True, "leaving the band arms it"
        for k in range(5, -1, -1):
            QTest.mouseMove(win, p + QPoint(40, -20 * k), 16)
        QTest.qWait(300)
        assert content_y() > origin + 40, f"the armed band scrolls, got {content_y()}"
        # up to the member row and release: the view lands on a row boundary
        for k in range(1, 15):
            QTest.mouseMove(win, p + QPoint(40, -35 * k), 16)
        QTest.qWait(200)
        QTest.mouseRelease(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p + QPoint(40, -490))
        QTest.qWait(400)
        assert lib.property("dragId") == ""
        cell = float(grid.property("cellHeight"))
        rel = content_y() - origin
        assert abs(rel - round(rel / cell) * cell) < 0.5, f"a drop lands on a row boundary, got {rel}"

        # the snap itself: nearest row top, pool rows offset by the hairline band
        pool_offset = float(grid.property("poolOffset"))
        for start, want in ((37.0, 0.0), (150.0, cell), (cell + pool_offset + 30, cell + pool_offset)):
            grid.setProperty("contentY", origin + start)
            QTest.qWait(30)
            QMetaObject.invokeMethod(grid, "snapToRow")
            QTest.qWait(30)
            assert abs(content_y() - origin - want) < 0.5, f"snap from {start}: want {want}, got {content_y() - origin}"
        # the rows fit the height left after the hairline band, and the band never costs a
        # row: at every window height the view shows as many whole rows as the fit without
        # the band would, and the last of them ends inside the view
        heights_seen = set()
        for h in range(760, 430, -8):
            win.setProperty("height", h)
            QTest.qWait(120)
            gh = float(grid.property("height"))
            heights_seen.add(gh)
            rows_v = int(grid.property("rowsVisible"))
            cell_h = float(grid.property("cellHeight"))
            band = float(grid.property("poolOffset"))
            nominal = float(grid.property("nominalCellH"))
            thumb = float(grid.property("baseThumbH"))
            assert band > 0, "the seeded library has members, so the band is up"
            # the fit as it was without the band: rows at nominal height plus the 10 % shrink
            plain_fit = max(1, int(gh // nominal))
            plain_over = (plain_fit + 1) * nominal - gh
            plain_rows = plain_fit + 1 if 0 < plain_over <= thumb * 0.10 * (plain_fit + 1) else plain_fit
            assert rows_v >= plain_rows, \
                f"at grid height {gh}: {rows_v} rows shown, {plain_rows} fit without the band"
            assert rows_v * cell_h + band <= gh + 0.5, \
                f"at grid height {gh}: {rows_v} rows of {cell_h} plus the {band} band overshoot the view"
        assert len(heights_seen) > 5, "the host window must actually resize for this sweep to mean anything"
        win.setProperty("height", 620)
        QTest.qWait(150)
        print("OK test_library_drop_scroll (band armed after leaving it; drop snaps to a row; rows fit around the band)")
    finally:
        host.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
