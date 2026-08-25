"""Isolator multi-select: the S chip must ADD to the solo set, never replace it (owner report).

The row delegate is the surface that broke - "if i select a second one it deselects the first"
- and no other test instantiates it, because the isolator list only builds rows when
`dev.objectList()` returns objects and the fake targets elsewhere return none. So this test
stubs a real object list, mounts ToolsPalette, and reads `soloed` off the live delegates:

  * two soloed rows are BOTH lit at once (the regression)
  * re-soloing one row clears only that row
  * skipping a soloed row drops just it, leaving the others soloed
  * the composed argv renders the set as its complement (skip every non-soloed object),
    because the engine's object= filter holds exactly one id

Sandboxed HOME/XDG; bench_courier stubbed; no engine ever spawns (compose only).

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software \
     python3 tests/test_isolator_multiselect_ui.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_OBJECTS = [
    {"objid": "1", "name": "background", "type": "image", "parent": "", "origin": "", "visible": True},
    {"objid": "2", "name": "clouds", "type": "image", "parent": "", "origin": "", "visible": True},
    {"objid": "3", "name": "sparks", "type": "particle", "parent": "", "origin": "", "visible": True},
]


def _rows(pal):
    """The isolator row delegates, keyed by objid (delegates carry `soloed` + `modelData`).

    ListView delegates are VISUAL children of the view's contentItem, not QObject children,
    so `pal.findChildren()` never returns them - walk childItems() instead. Reaching
    QQuickItem properties at all needs QtQuick imported (it registers the converter)."""
    from PySide6.QtCore import QObject
    from PySide6.QtQuick import QQuickItem  # noqa: F401  (registers QQuickItem* conversion)
    out = {}
    for view in pal.findChildren(QObject):
        if view.metaObject().className() != "QQuickListView":
            continue
        content = view.property("contentItem")
        for item in (content.childItems() if content else []):
            md = item.property("modelData")
            if isinstance(md, dict) and item.property("soloed") is not None and "objid" in md:
                out[str(md["objid"])] = item
    return out


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-isoms-")
    orig = {k: os.environ.get(k)
            for k in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
    try:
        os.environ["HOME"] = home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
        os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
        os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

        from PySide6.QtCore import QUrl, Slot
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlComponent, QQmlEngine, qmlRegisterSingletonInstance
        from PySide6.QtQuick import QQuickItem  # noqa: F401  (QQuickItem* converter for _rows)
        from PySide6.QtTest import QTest
        from lwe_ui import bench_courier
        from lwe_ui.models import ThemeTokens
        from lwe_ui.dev import DevBridge
        from lwe_ui.storage import paths, settings
        from lwe_ui.app import _resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME

        paths.ensure_dirs()
        settings.ensure_exists()
        bench_courier.wait_clear = lambda *a, **k: True
        bench_courier.standdown = lambda *a, **k: True
        bench_courier.resume = lambda *a, **k: True

        app = QGuiApplication.instance() or QGuiApplication(["t"])  # noqa: F841
        tokens = ThemeTokens(_resolve_theme_tokens())
        qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)

        engine = QQmlEngine()
        engine.addImportPath(str(_QML_DIR))

        class _StubDev(DevBridge):
            """objectList is the isolator's row source. It must stay a @Slot to be callable
            from QML - replacing the plain attribute drops it out of the metaobject and the
            list silently renders empty."""
            @Slot(result="QVariantList")
            def objectList(self) -> list:
                return list(_OBJECTS)

        dev = _StubDev()
        dev._auto_relaunch = False
        engine.rootContext().setContextProperty("dev", dev)

        pcomp = QQmlComponent(engine, QUrl.fromLocalFile(str(_QML_DIR / "ToolsPalette.qml")))
        pal = pcomp.create()
        assert pal is not None, pcomp.errorString()
        pal.setProperty("tab", 0)           # Isolator
        # a ListView only incubates delegates once its window is shown AND exposed; an
        # unshown palette yields count==3 with zero row items (the offscreen platform keeps
        # this headless).
        pal.show()
        assert QTest.qWaitForWindowExposed(pal, 5000), "the palette window never exposed"
        QTest.qWait(120)

        rows = _rows(pal)
        assert set(rows) == {"1", "2", "3"}, f"isolator rows did not mount: {sorted(rows)}"
        assert not any(r.property("soloed") for r in rows.values()), "nothing is soloed at rest"

        # THE REGRESSION: soloing a second object must not deselect the first
        dev.solo("1")
        QTest.qWait(20)
        assert rows["1"].property("soloed") is True, "first solo did not light its row"
        dev.solo("2")
        QTest.qWait(20)
        assert rows["1"].property("soloed") is True, \
            "soloing a second object DESELECTED the first (the reported bug)"
        assert rows["2"].property("soloed") is True, "the second solo did not light its row"
        assert rows["3"].property("soloed") is False, "an untouched row must stay unsoloed"

        # the set reaches the engine as its complement: hide everything not soloed
        dev.setTarget("2114739882")
        DevBridge._dev_outputs = lambda self: ["TEST-OUT"]
        argv = dev.compose_argv()
        assert "skip-object=3" in argv, f"non-soloed objects must be skipped: {argv}"
        assert "skip-object=1" not in argv and "skip-object=2" not in argv, argv
        assert not any(a.startswith("object=") for a in argv), \
            "the single-object filter cannot express a set - it would drop one of the two"

        dev.solo("1")
        QTest.qWait(20)
        assert rows["1"].property("soloed") is False and rows["2"].property("soloed") is True, \
            "re-soloing a row must toggle only that row"

        # back to a single solo -> the engine's native filter, not a complement
        argv = dev.compose_argv()
        assert "object=2" in argv and not any(a.startswith("skip-object=") for a in argv), argv

        dev.solo("3")
        QTest.qWait(20)
        dev.setSkipObject("2", True)
        QTest.qWait(20)
        assert rows["2"].property("soloed") is False and rows["2"].property("skipped") is True
        assert rows["3"].property("soloed") is True, \
            "skipping one soloed row must leave the others soloed"

        dev.clearIsolation()
        QTest.qWait(20)
        assert not any(r.property("soloed") for r in rows.values()), "Clear must unsolo everything"

        print("OK test_isolator_multiselect_ui - S adds to the solo set (both rows lit); "
              "re-solo/skip toggle one row; argv renders the set as its complement")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    main()
