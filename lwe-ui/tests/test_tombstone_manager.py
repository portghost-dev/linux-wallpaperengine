"""Tombstone manager modal (B13): the Settings > Library Edit button opens
a centered modal (not the old inline drop-down); rows carry name + plain reason +
Restore; Clear all takes no confirm; restore/clear reflect live.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software python3 tests/test_tombstone_manager.py
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

_TMP = tempfile.mkdtemp(prefix="lwe-tomb-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")


def visual_items(item, out=None):
    if out is None:
        out = []
    for ch in item.childItems():
        out.append(ch)
        visual_items(ch, out)
    return out


def _on_screen(it) -> bool:
    node = it
    while node is not None:
        if node.property("visible") is False:
            return False
        node = node.parentItem()
    return True


def texts_under(item):
    out = []
    for it in visual_items(item):
        t = it.property("text")
        if t and _on_screen(it):
            out.append(str(t))
    return out


def main() -> None:
    from PySide6.QtCore import Q_ARG, QMetaObject, Qt, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    from lwe_ui import bench_courier
    from lwe_ui.app import _QML_DIR, _TOKENS_NAME, _TOKENS_URI, _resolve_theme_tokens
    from lwe_ui.bench_bridge import BenchBridge
    from lwe_ui.dev import DevBridge
    from lwe_ui.editor import EditorBridge
    from lwe_ui.models import Backend, ImportBridge, ThemeTokens
    from lwe_ui.storage import paths, settings, tags, tombstones
    from lwe_ui.workshop import WorkshopBridge


    paths.ensure_dirs()
    settings.ensure_exists()
    for wid, title, reason in (("501", "Crown of Midnight", "rejected-untested"),
                               ("502", "Painting the Sharks 4K", "crashed"),
                               ("503", "Ling Cage", "rejected-untested")):
        tags.set_state(wid, title, "bad")
        tombstones.record(wid, title, reason)

    app = QGuiApplication.instance() or QGuiApplication(["t"])  # noqa: F841
    tokens = ThemeTokens(_resolve_theme_tokens())
    qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(_QML_DIR))
    backend = Backend()
    editor = EditorBridge()
    bench = BenchBridge()
    dev = DevBridge()
    ws = WorkshopBridge(backend, dev)
    ib = ImportBridge(backend)
    from lwe_ui.settings_bridge import SettingsBridge
    sb = SettingsBridge(backend, ib)
    for n, o in (("backend", backend), ("editor", editor), ("bench", bench),
                 ("dev", dev), ("workshop", ws), ("importBridge", ib),
                 ("settingsBridge", sb)):
        engine.rootContext().setContextProperty(n, o)
    from lwe_ui.wizard_bridge import WizardBridge
    _wizb = WizardBridge(engine.rootContext().contextProperty("backend"),
                         engine.rootContext().contextProperty("workshop"))
    engine.rootContext().setContextProperty("wizardBridge", _wizb)
    engine.load(QUrl.fromLocalFile(str(_QML_DIR / "Main.qml")))
    assert engine.rootObjects(), "Main.qml failed to load"
    win = engine.rootObjects()[0]

    src = (_QML_DIR / "SettingsLibrary.qml").read_text(encoding="utf-8")
    assert "TombstoneManager" in src, "the modal must be hosted"
    assert "id: tombList" not in src, "the inline drop-down Column must be removed"
    assert 'text: "Edit"' in src, "the Tombstones row's verb is `Edit`"
    assert "tombstones.openManager()" in src, "the verb opens the shared modal"

    win.setProperty("currentView", "settings")
    QTest.qWait(60)
    # navigate to the Library settings page (pageIndex 2) so SettingsLibrary + its
    # TombstoneManager child instantiate (lazy Loader)
    for v in win.findChildren(QQuickItem):
        if v.property("pageIndex") is not None:
            v.setProperty("pageIndex", 2)
    QTest.qWait(120)

    mgr = win.findChild(object, "tombstoneManager")
    assert mgr is not None, "the modal component must exist"
    assert mgr.property("visible") is False, "modal starts closed (never an inline expansion)"

    QMetaObject.invokeMethod(mgr, "openManager", Qt.ConnectionType.DirectConnection)
    QTest.qWait(80)
    assert mgr.property("visible") is True, "Edit opens the modal"
    txt = texts_under(mgr.property("contentItem"))
    assert any(t == "Tombstones" for t in txt), txt
    assert any("Import brings one back to Workshop" in t for t in txt)
    assert any("Crown of Midnight" in t for t in txt)
    assert any(t == "Deleted" for t in txt), "the collapsed row shows the outcome chip"
    QMetaObject.invokeMethod(mgr, "toggleExpand", Qt.ConnectionType.DirectConnection,
                             Q_ARG("QVariant", "502"))
    QTest.qWait(60)
    txt2 = texts_under(mgr.property("contentItem"))
    assert any("it crashed at the bench" in t for t in txt2), "502's crash reason composes in the timeline"
    assert any(t == "Purge" for t in txt), "Purge is still the destructive action"
    assert any(t == "Import" for t in txt), "the merged surface carries Import"
    assert not any(t == "Restore" for t in txt), "Restore stays dropped - Import IS the restore"
    assert not any(t == "Clear all" for t in txt), "Clear-all is dropped (purge is per-item)"
    assert any("unsubscribed" in t for t in txt), "unimportable rows state why Import is disabled"
    assert any("Import all (0)" in t for t in txt), "the all-button counts IMPORTABLE rows only"

    # purge one: the record is wiped, the tags gate dropped (re-import un-gated), count live
    from lwe_ui.storage import records
    ws.purgeRecord("502")
    QMetaObject.invokeMethod(mgr, "reload", Qt.ConnectionType.DirectConnection)
    QTest.qWait(40)
    assert records.has_record("502") is False, "purge wipes the record file"
    assert "502" not in tags.known_ids(), "purge drops the tags gate -> re-import un-gated"
    txt2 = texts_under(mgr.property("contentItem"))
    assert not any("it crashed at the bench" in t for t in txt2), "purged row leaves the modal"
    assert any("· 2" in t for t in txt2), f"count decremented live: {txt2}"

    ws.purgeAllRecords()
    QMetaObject.invokeMethod(mgr, "reload", Qt.ConnectionType.DirectConnection)
    QTest.qWait(40)
    assert records.list_wids() == [], "Purge all wipes every remaining record file"
    assert "501" not in tags.known_ids() and "503" not in tags.known_ids(), \
        "Purge all drops every tags gate so the items can re-import"

    print("OK test_tombstone_manager - records viewer / composed neutral lines / "
          "purge-and-ungate / live-count; Restore + Clear-all dropped")


if __name__ == "__main__":
    main()
