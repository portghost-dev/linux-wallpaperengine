"""Header search field interactions: the clear-x, and drag-to-select.

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

        clear = next((o for o in header.findChildren(QObject)
                      if o.objectName() == "searchClear"), None)
        assert clear is not None, "the search clear-x did not mount"
        field = clear.parent()
        assert field is not None and field.property("text") is not None, \
            "the clear-x must live inside the search field"

        assert field.property("text") == "", "the field starts empty"
        assert clear.property("visible") is False, \
            "the clear-x must be invisible while there is nothing to clear"

        field.setProperty("text", "meteor")
        QTest.qWait(20)
        assert clear.property("visible") is True, "the clear-x must appear once the field has text"
        assert header.property("query") == "meteor", "the header query mirrors the field"

        backend.pushed.clear()
        centre = clear.mapToScene(QPointF(clear.property("width") / 2,
                                          clear.property("height") / 2)).toPoint()
        QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, centre)
        QTest.qWait(60)
        assert field.property("text") == "", f"the tap must empty the field: {field.property('text')!r}"
        assert header.property("query") == "", "the mirrored query must clear too"
        assert backend.pushed == [""], \
            f"a programmatic clear must push the empty query to the backend, got {backend.pushed}"
        assert clear.property("visible") is False, "the x hides again once the field is empty"

        dh = [o for o in header.findChildren(QObject)
              if o.metaObject().className().startswith("QQuickDragHandler")]
        assert len(dh) == 1, f"expected the one titlebar DragHandler, found {len(dh)}"
        drag = dh[0]

        field.setProperty("text", "meteor shower")
        QTest.qWait(20)
        y = field.mapToScene(QPointF(0, field.property("height") / 2)).toPoint().y()
        x0 = field.mapToScene(QPointF(40, 0)).toPoint().x()
        QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, QPoint(x0, y))
        QTest.qWait(20)
        stole = False
        for dx in range(8, 120, 12):
            QTest.mouseMove(view, QPoint(x0 + dx, y))
            QTest.qWait(12)
            stole = stole or bool(drag.property("active"))
        QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, QPoint(x0 + 120, y))
        QTest.qWait(30)
        assert not stole, "the window drag stole the grab from the search field mid-drag"
        assert field.property("selectedText") != "", \
            "dragging inside the search field must select text"

        field.setProperty("text", "")
        QTest.qWait(20)
        ex = int(header.property("width")) // 2
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

        src = (_ROOT / "src/lwe_ui/qml/HeaderBar.qml").read_text(encoding="utf-8")
        block = src.split("id: clearX", 1)[1]
        block = block[:block.index("\n            }")]
        code = "\n".join(ln.split("//", 1)[0] for ln in block.splitlines())
        assert "Rectangle" not in code, \
            "the clear-x is a bare glyph - no plate/button rectangle behind it"
        assert "Theme." in code, "the glyph color must come from Theme tokens (token law)"
        assert not re.search(r'color:\s*["#]', code), \
            'no literal color: a hard white vanishes on the light palettes'
        assert re.search(r"color:\s*\w*[Hh]ov\w*\.hovered\s*\?\s*Theme\.\w+\s*:\s*Theme\.\w+", code), \
            "the clear-x must roll over between two Theme tokens on hover"

        print("OK test_search_clear - clear-x hidden when empty / shown with text / tap clears "
              "field + pushes empty query; bare glyph, themed token; drag-in-field selects "
              "text while empty-space drag still moves the window")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    main()
