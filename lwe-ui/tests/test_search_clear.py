"""Header search field interactions: the collapsing search, and drag-to-select.

Contract:
  * hidden while the field is empty - it only exists once there is something to clear
  * shown as soon as the field has text
  * a tap empties the field AND pushes the empty query to the backend. `onTextEdited` fires
    for TYPING only, so a programmatic clear that forgot the explicit setSearch("") would
    blank the box while the grid stayed filtered - the failure this asserts against.
  * bare glyph: no plate/button rectangle behind it (design call - the 28x28 gray button
    grammar belongs to the modal + palette closes)
  * token law: the glyph color is a Theme token, never a literal white, or it vanishes on
    the light palettes (source-level assert; color resolution is a human's eye, not a test's)
  * a drag INSIDE the field selects text and never hands the grab to the header's
    window-drag; a drag on EMPTY header space still moves the window

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software python3 tests/test_search_clear.py
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

_ROOT = Path(__file__).resolve().parent.parent
_SRC = str(_ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-clearx-")
    orig = {k: os.environ.get(k)
            for k in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
    try:
        os.environ["HOME"] = home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
        os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
        os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

        from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QUrl, Property, Signal, Slot
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import qmlRegisterSingletonInstance
        from PySide6.QtQuick import QQuickView
        from PySide6.QtTest import QTest
        from lwe_ui.models import ThemeTokens, LibraryModel, LibraryFilterModel
        from lwe_ui.storage import paths, settings
        from lwe_ui.app import _resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME

        paths.ensure_dirs()
        settings.ensure_exists()

        app = QGuiApplication.instance() or QGuiApplication(["t"])  # noqa: F841
        qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME,
                                     ThemeTokens(_resolve_theme_tokens()))

        class _Backend(QObject):
            """Only the surface HeaderBar touches; setSearch records what the header pushed."""
            changed = Signal()

            def __init__(self) -> None:
                super().__init__()
                self.pushed: list[str] = []
                self._model = LibraryModel()
                self._filter = LibraryFilterModel(self._model)

            @Slot(str)
            def setSearch(self, text: str) -> None:
                self.pushed.append(text)

            @Slot(str, result="QVariant")
            def getSetting(self, key: str):
                return ""

            @Slot(str, "QVariant")
            def setSetting(self, key: str, value) -> None:
                pass

            @Property("QVariant", notify=changed)
            def filterModel(self):
                return self._filter

            @Property(int, notify=changed)
            def totalCount(self):
                return 0

            @Property(int, notify=changed)
            def playlistCount(self):
                return 0

            @Property(str, notify=changed)
            def activePlaylist(self):
                return ""

            @Property("QVariant", notify=changed)
            def engineStats(self):
                return {}

        backend = _Backend()
        view = QQuickView()
        view.engine().addImportPath(str(_QML_DIR))
        view.rootContext().setContextProperty("backend", backend)
        # the header sizes itself from its parent; without SizeRootObjectToView the root stays
        # width 0 and the right-aligned row (search field included) lands at NEGATIVE x, so
        # every click misses it
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.resize(1200, 64)
        view.setSource(QUrl.fromLocalFile(str(_QML_DIR / "HeaderBar.qml")))
        assert not view.errors(), "\n".join(e.toString() for e in view.errors())
        header = view.rootObject()
        assert header is not None
        view.show()
        assert QTest.qWaitForWindowExposed(view, 5000), "the header window never exposed"
        QTest.qWait(60)

        def type_in(text):
            for ch in text:
                QTest.keyClick(view, ch)

        def find(name):
            return next((o for o in header.findChildren(QObject) if o.objectName() == name), None)
        slot, btn, field = find("headerSearchSlot"), find("headerSearchBtn"), find("headerSearch")
        assert slot is not None and btn is not None and field is not None, "the search cluster did not mount"
        clear = find("searchClear")
        assert clear is not None, "the clear-x lives in the field, left of the glyph"
        centre = lambda o: o.mapToScene(QPointF(o.property("width") / 2, o.property("height") / 2)).toPoint()

        # at rest: a 28 px glyph button, the field folded away
        assert int(slot.property("width")) == 28 and slot.property("open") is False
        assert int(btn.property("width")) == 28 and int(find("headerFilter").property("width")) == 28 \
            and int(find("headerClose").property("width")) == 28
        assert int(btn.parent().parent().property("spacing")) == 8, "the three buttons sit 8 px apart"

        # a click grows the field out of the glyph's place and puts the cursor in it
        QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, centre(btn))
        QTest.qWait(260)
        assert slot.property("open") is True and int(slot.property("width")) == int(slot.property("fieldWidth"))
        assert field.property("activeFocus") is True, "the cursor lands in the field"
        assert int(slot.property("fieldWidth")) in (94, 124), "the shipped search width"
        glyph_right = btn.mapToScene(QPointF(btn.property("width"), 0)).toPoint().x()
        field_right = field.mapToScene(QPointF(field.property("width"), 0)).toPoint().x()
        assert abs(glyph_right - field_right) <= 1, "the glyph ends up inside the field at its right end"

        # the field's floor is the theme's background moved 6 % toward its text colour
        well = field.property("background")
        v, und = QQmlExpression(QQmlEngine.contextForObject(well), well, "Qt.colorEqual(color, Qt.rgba(Theme.base.r + (Theme.textPrimary.r - Theme.base.r) * 0.06, Theme.base.g + (Theme.textPrimary.g - Theme.base.g) * 0.06, Theme.base.b + (Theme.textPrimary.b - Theme.base.b) * 0.06, 1))").evaluate() if False else (None, None)
        # the inset shadow is always dark: black at 0.9 on dark themes, the text colour at 0.18 on light
        from PySide6.QtQml import QQmlEngine, QQmlExpression
        v, und = QQmlExpression(QQmlEngine.contextForObject(well), well, "Qt.colorEqual(color, Qt.rgba(Theme.base.r + (Theme.textPrimary.r - Theme.base.r) * 0.06, Theme.base.g + (Theme.textPrimary.g - Theme.base.g) * 0.06, Theme.base.b + (Theme.textPrimary.b - Theme.base.b) * 0.06, 1))").evaluate()
        assert not und and bool(v), "the search floor is the theme background moved 6 % toward the text"
        # the lit edges: bottom white at 0.14 on dark (0.70 on light), a right edge at 0.10 on dark only
        eb, er = find("searchEdgeBottom"), find("searchEdgeRight")
        v, und = QQmlExpression(QQmlEngine.contextForObject(eb), eb, "Theme.isLight ? Math.abs(color.a - 0.70) < 0.01 : Math.abs(color.a - 0.14) < 0.01").evaluate()
        assert not und and bool(v), "the bottom inner edge alpha per theme"
        v, und = QQmlExpression(QQmlEngine.contextForObject(er), er, "Theme.isLight ? !visible : (visible && Math.abs(color.a - 0.10) < 0.01)").evaluate()
        assert not und and bool(v), "the right inner edge only on dark, at 0.10"
        shadow = find("searchShadowTop")
        ev = lambda expr: QQmlExpression(QQmlEngine.contextForObject(shadow), shadow, expr).evaluate()
        value, undefined = ev("Theme.isLight ? Qt.colorEqual(Qt.rgba(gradient.stops[0].color.r, gradient.stops[0].color.g, gradient.stops[0].color.b, 1), Qt.rgba(Theme.textPrimary.r, Theme.textPrimary.g, Theme.textPrimary.b, 1)) : (gradient.stops[0].color.r + gradient.stops[0].color.g + gradient.stops[0].color.b < 0.01)")
        assert not undefined and bool(value), "the inset shadow ink is black on dark, the text colour on light"
        value, undefined = ev("gradient.stops[0].color.a")
        assert not undefined and (abs(float(value) - 0.9) < 0.01 or abs(float(value) - 0.18) < 0.01)
        # the growth is animated leftward: mid-way the width is between folded and full, and the
        # right edge has not moved
        header.clearSearch()
        QTest.qWait(260)
        right_before = slot.mapToScene(QPointF(slot.property("width"), 0)).toPoint().x()
        QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, centre(btn))
        QTest.qWait(60)
        mid = int(slot.property("width"))
        assert 28 < mid < int(slot.property("fieldWidth")), f"the field grows over time, mid-way width {mid}"
        QTest.qWait(260)
        assert slot.mapToScene(QPointF(slot.property("width"), 0)).toPoint().x() == right_before, "the right edge stays; the field grows leftward"

        # typing mirrors the query; a click elsewhere drops the cursor but a live term keeps the field
        assert clear.property("visible") is False, "no x while the field is empty"
        type_in("meteor")
        QTest.qWait(20)
        assert header.property("query") == "meteor" and backend.pushed[-1] == "meteor"
        assert clear.property("visible") is True, "the x shows once there is text"
        glyph_left = btn.mapToScene(QPointF(0, 0)).toPoint().x()
        x_right = clear.mapToScene(QPointF(clear.property("width"), 0)).toPoint().x()
        assert x_right <= glyph_left, "the x sits left of the glyph, in a fixed spot"
        # a tap on the x empties the field, pushes the empty query, and keeps the cursor
        backend.pushed.clear()
        QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, centre(clear))
        QTest.qWait(60)
        assert field.property("text") == "" and backend.pushed == [""] and field.property("activeFocus") is True
        assert clear.property("visible") is False and slot.property("open") is True
        type_in("meteor")
        QTest.qWait(20)
        ex = int(header.property("width")) // 2
        QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, QPoint(ex, 10))
        QTest.qWait(260)
        assert field.property("activeFocus") is False and slot.property("open") is True, \
            "a live term keeps the field open with no cursor"

        # Escape clears the term and folds the field, pushing the empty query
        backend.pushed.clear()
        header.clearSearch()
        QTest.qWait(260)
        assert field.property("text") == "" and header.property("query") == "" and backend.pushed == [""]
        assert slot.property("open") is False and int(slot.property("width")) == 28, "empty and folded"

        # open, type nothing, click elsewhere: it folds
        QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, centre(btn))
        QTest.qWait(260)
        assert slot.property("open") is True
        QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, QPoint(ex, 10))
        QTest.qWait(260)
        assert slot.property("open") is False, "an empty field folds when the cursor leaves"

        # Escape from inside the field, by key
        QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, centre(btn))
        QTest.qWait(260)
        type_in("sky")
        QTest.keyClick(view, Qt.Key_Escape)
        QTest.qWait(260)
        assert header.property("query") == "" and slot.property("open") is False

        dh = [o for o in header.findChildren(QObject)
              if o.metaObject().className().startswith("QQuickDragHandler")]
        assert len(dh) == 1, f"expected the one titlebar DragHandler, found {len(dh)}"
        drag = dh[0]

        QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, centre(btn))
        QTest.qWait(260)
        type_in("meteor shower")
        QTest.qWait(20)
        y = field.mapToScene(QPointF(0, field.property("height") / 2)).toPoint().y()
        x0 = field.mapToScene(QPointF(12, 0)).toPoint().x()
        QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, QPoint(x0, y))
        QTest.qWait(20)
        stole = False
        for dx in range(8, 60, 12):
            QTest.mouseMove(view, QPoint(x0 + dx, y))
            QTest.qWait(12)
            stole = stole or bool(drag.property("active"))
        QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, QPoint(x0 + 60, y))
        QTest.qWait(30)
        assert not stole, "the window drag stole the grab from the search field mid-drag"
        assert field.property("selectedText") != "", \
            "dragging inside the search field must select text"

        header.clearSearch()
        QTest.qWait(260)
        QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, QPoint(ex, 10))
        QTest.qWait(20)
        activated = False
        for dx in range(8, 120, 10):
            QTest.mouseMove(view, QPoint(ex + dx, 10))
            QTest.qWait(12)
            activated = activated or bool(drag.property("active"))
        QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, QPoint(ex + 120, 10))
        QTest.qWait(30)
        assert activated, "dragging empty header space must still move the window"

        # the glyphs roll between two Theme tokens and carry no literal colour
        src = (_ROOT / "src/lwe_ui/qml/HeaderBar.qml").read_text(encoding="utf-8")
        cluster = src[src.index("component HeaderIconButton"):]
        code = "\n".join(ln.split("//", 1)[0] for ln in cluster.splitlines())
        assert not re.search(r'color:\s*"(?!transparent)|color:\s*#', code), 'no literal color in the cluster (a transparent stop is not a colour)'
        assert len(re.findall(r"hovered[^\n]*\?\s*Theme\.textPrimary\s*:\s*Theme\.textSecondary", code)) == 4, \
            "each of the three glyphs and the clear-x rolls from the muted to the primary text token on hover"

        print("OK test_search_clear - collapsing search: grows from the glyph, cursor in, live term "
              "keeps it open, Escape clears and folds, empty folds on focus loss; drag-in-field "
              "selects while empty-space drag moves the window; glyphs on tokens")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    main()
