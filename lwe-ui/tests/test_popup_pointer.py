"""Pointer regressions for the deck's non-modal popups: the quick panel, the clock popover
and the playlist menu, driven with real mouse events against a live Deck.

Hosts the Deck at the bottom of a Window whose whole area is a lift target: an Item carrying a
DragHandler configured exactly like WallpaperCard's lift (target null, 24 px threshold, may take
over from items and from handlers of another type). That is the thing a library tile does
beneath an open popup, and the thing the popups must never let it do.

#1 a drag that starts inside any of the three popups never lifts the target beneath, whether
   it starts over a plain label, over a tap-handler row, or in the padding ring between the
   content and the popup's edge (the background outside the content, which no control covers;
   a MouseArea there sees nothing, since a tap handler above it accepts the press, and it could
   not hold the grab against a DragHandler anyway); a drag on the bare window still lifts it,
   so the harness is shown to measure.
#2 the playlist menu's per-row delete, by clicks: the trash arms, the same trash disarms,
   another trash or another row only cancels (the menu stays open, the active playlist does
   not change), No cancels, Yes is inert inside its first quarter second, a later Yes deletes
   and closes, and a plain row tap still selects. The earlier tests armed the row through the
   arm() function and never saw that the row's own tap handler, underneath the trash, cleared
   the row on the same click.
"""
from __future__ import annotations

import _sandbox  # noqa: F401
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lwe-popup-pointer-")
os.environ["HOME"] = _TMP
for _k, _sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_STATE_HOME", ".local/state"), ("XDG_DATA_HOME", ".local/share")):
    os.environ[_k] = str(Path(_TMP) / _sub)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QUrl, QObject, QMetaObject, QPointF, QPoint, Qt, Signal, Slot  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402,F401  (registers the item converter)
from PySide6.QtTest import QTest  # noqa: E402

from lwe_ui import bench_bridge, bench_courier  # noqa: E402
from lwe_ui.models import Backend, ThemeTokens  # noqa: E402
from lwe_ui.editor import EditorBridge  # noqa: E402
from lwe_ui.dev import DevBridge  # noqa: E402
from lwe_ui.deck_popup import DeckPopupBridge  # noqa: E402
from lwe_ui.storage import paths, settings, playlists  # noqa: E402
from lwe_ui.app import _resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME  # noqa: E402

_HOST = """
import QtQuick
import QtQuick.Window
import "."
Window {
    width: 1280; height: 800; visible: true; color: "#0b0b0e"
    Item {
        objectName: "liftTarget"; anchors.fill: parent
        property int lifts: 0
        DragHandler {
            target: null; dragThreshold: 24
            grabPermissions: PointerHandler.CanTakeOverFromItems | PointerHandler.CanTakeOverFromHandlersOfDifferentType
            onActiveChanged: if (active) parent.lifts++
        }
    }
    Deck { objectName: "deck"; width: parent.width; height: 72; anchors.bottom: parent.bottom }
}
"""


class _StubWizard(QObject):
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

    @Slot()
    def close(self):
        pass

    @Slot()
    def killBench(self):
        pass

    @Slot(result=int)
    def benchLoadRemaining(self):
        return -1


def _settle(app, ms=250):
    for _ in range(3):
        app.processEvents()
    QTest.qWait(ms)


def _walk(item):
    out = []
    for c in item.childItems():
        out.append(c)
        out.extend(_walk(c))
    return out


def _scene(item, fx=0.5, fy=0.5) -> QPoint:
    return item.mapToScene(QPointF(item.width() * fx, item.height() * fy)).toPoint()


def _drag(win, p: QPoint) -> None:
    QTest.mousePress(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
    for d in range(4, 84, 8):
        QTest.mouseMove(win, p + QPoint(d, 0), 20)
    QTest.mouseRelease(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p + QPoint(84, 0))
    QTest.qWait(60)


def _click(win, p: QPoint) -> None:
    QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)


def _build(app):
    paths.ensure_dirs()
    settings.ensure_exists()
    for n in ("All wallpapers", "Evening", "Party"):
        playlists.create(n)
    bench_courier.available = lambda: True
    backend = Backend()
    playlists.set_active(next(p["slug"] for p in backend.playlistList() if p["name"] == "All wallpapers"))
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(_QML_DIR))
    ctx = engine.rootContext()
    for n, o in (("backend", backend), ("editor", EditorBridge()), ("bench", bench_bridge.BenchBridge()),
                 ("dev", DevBridge()), ("wizardBridge", _StubWizard()), ("deckPopup", DeckPopupBridge(backend))):
        ctx.setContextProperty(n, o)
    engine.loadData(_HOST.encode(), QUrl.fromLocalFile(str(_QML_DIR / "_popup_pointer_host.qml")))
    assert engine.rootObjects(), "host failed to load"
    win = engine.rootObjects()[0]
    deck = win.findChild(QObject, "deck")
    deck.setProperty("masterActive", True)
    deck.setProperty("engineStatus", {"state": "up", "current": "111", "interval": "900", "next_in": "300"})
    _settle(app)
    return engine, win, deck, backend, win.findChild(QObject, "liftTarget")


def test_popups_hold_the_pointer(app, win, deck, target) -> None:
    # the harness measures: a drag on the bare window lifts the target
    _drag(win, QPoint(300, 300))
    assert target.property("lifts") == 1, "the lift target must react to a drag on the bare window"
    for name in ("deckSettingsPopup", "clockPopover", "nameMenu"):
        pop = next(o for o in deck.findChildren(QObject) if o.objectName() == name)
        QMetaObject.invokeMethod(pop, "open")
        _settle(app)
        assert pop.property("opened"), f"{name} did not open"
        content = pop.property("contentItem")
        background = pop.property("background")
        # a row or label near the content's top-left corner, the content's centre, and the
        # padding ring: the background outside the content, which no control covers
        points = [content.mapToScene(QPointF(6, 6)).toPoint(), _scene(content)]
        bw, bh = background.width(), background.height()
        points += [background.mapToScene(QPointF(x, y)).toPoint()
                   for x, y in ((4, 4), (bw / 2, 4), (4, bh / 2), (bw / 2, bh - 4))]
        for p in points:
            before = target.property("lifts")
            _drag(win, p)
            assert target.property("lifts") == before, f"a drag inside {name} at {p} lifted the target beneath"
        # a drag that leaves the popup altogether keeps the grab to the release
        before = target.property("lifts")
        p = _scene(content)
        QTest.mousePress(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
        for k in range(1, 11):
            QTest.mouseMove(win, p + QPoint(0, -40 * k), 20)
        QTest.mouseRelease(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p + QPoint(0, -400))
        QTest.qWait(60)
        assert target.property("lifts") == before, f"a drag out of {name} lifted the target beneath"
        # a drag that starts inside a text field (the interval field, an open fit chip) is the
        # one case where an item inside holds the press; it must not reach the tile either
        fields = [c for c in _walk(content) if ("TextField" in c.metaObject().className() or "TextInput" in c.metaObject().className()) and c.isVisible()]
        if name == "deckSettingsPopup":
            chip = next(c for c in _walk(content) if c.property("ckey") == "FIT_ZOOM")
            chip.setProperty("editing", True)
            _settle(app)
            fields = [c for c in chip.childItems() if c.metaObject().className().startswith("QQuickTextInput")]
        if name in ("deckSettingsPopup", "clockPopover"):
            assert fields, f"{name}: no text field found to drag from"
        for f in fields[:1]:
            before = target.property("lifts")
            p = _scene(f)
            QTest.mousePress(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
            for k in range(1, 11):
                QTest.mouseMove(win, p + QPoint(0, -40 * k), 20)
            QTest.mouseRelease(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p + QPoint(0, -400))
            QTest.qWait(60)
            assert target.property("lifts") == before, f"a drag out of a text field in {name} lifted the target beneath"
        QMetaObject.invokeMethod(pop, "close")
        _settle(app)
    print("OK test_popups_hold_the_pointer (quick panel, clock popover, playlist menu; bare window still lifts)")


def test_menu_delete_by_clicks(app, win, deck, backend) -> None:
    menu = next(o for o in deck.findChildren(QObject) if o.objectName() == "nameMenu")

    def rows():
        return [r for r in _walk(menu.property("contentItem")) if r.objectName() == "plRow"]

    def part(row, name):
        return next(c for c in _walk(row) if c.objectName() == name)

    def reopen():
        if not menu.property("opened"):
            QMetaObject.invokeMethod(menu, "open")
            _settle(app)
        return rows()

    def armed():
        return menu.property("armedSlug")

    def click(item, wait=0):
        QTest.qWait(wait)
        _click(win, _scene(item))
        _settle(app)

    r0, r1 = reopen()[:2]
    s0, s1 = r0.property("modelData")["slug"], r1.property("modelData")["slug"]
    active0 = backend.activePlaylist()["slug"]
    assert s0 == active0, "row 0 is the active playlist in this config"

    click(part(r1, "rowTrash"))
    assert armed() == s1, "a trash click arms its row"
    click(part(r1, "rowTrash"))
    assert armed() == "", "the same trash disarms"
    click(part(r1, "rowTrash"))
    click(part(r0, "rowTrash"))
    assert armed() == "", "another trash only cancels"
    click(part(r1, "rowTrash"))
    click(part(r0, "rowBody"))
    assert armed() == "" and menu.property("opened") and backend.activePlaylist()["slug"] == active0, \
        "a click on another row cancels without selecting it or closing the menu"
    click(part(r1, "rowTrash"))
    click(part(r1, "confirmNo"), 300)
    assert armed() == "" and menu.property("opened"), "No cancels and keeps the menu open"
    # a Yes inside the quarter-second guard is inert. One frame after the arm, so the rows
    # beneath have moved down out from under the confirm line, then the click at once
    _click(win, _scene(part(r1, "rowTrash")))
    QTest.qWait(40)
    _click(win, _scene(part(r1, "confirmYes")))
    _settle(app)
    assert armed() == s1 and s1 in {p["slug"] for p in backend.playlistList()}, "Yes must ignore a click inside its first quarter second"
    before = {p["slug"] for p in backend.playlistList()}
    click(part(r1, "confirmYes"), 300)
    assert before - {p["slug"] for p in backend.playlistList()} == {s1}, "a later Yes deletes the row's playlist"
    assert not menu.property("opened") and backend.activePlaylist()["slug"] == active0, \
        "the menu closes and the active playlist is untouched"
    r0 = reopen()[0]
    click(part(r0, "rowBody"))
    assert not menu.property("opened") and backend.activePlaylist()["slug"] == r0.property("modelData")["slug"], \
        "a plain row tap still selects and closes"
    print("OK test_menu_delete_by_clicks (arm, disarm, cancel, No, guarded Yes, delete, select)")


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    tokens = ThemeTokens(_resolve_theme_tokens())
    qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)
    engine, win, deck, backend, target = _build(app)
    test_popups_hold_the_pointer(app, win, deck, target)
    test_menu_delete_by_clicks(app, win, deck, backend)
    print("ALL popup pointer regressions passed")


if __name__ == "__main__":
    main()
