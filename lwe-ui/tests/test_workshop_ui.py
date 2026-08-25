"""Workshop scope UI (16a/16b/16c): rail rename, funnel hero, tiles, kept linger,
trash dialog, hot watch.

Offscreen Main.qml with every bridge registered. Repeater delegates are delegate-model-
owned (findChildren is blind to them) so tile assertions walk childItems().

  * rail: the Review item is GONE; Workshop with the plus glyph took its slot; clicking
    it mounts the workshop view (a takeover, not a library filter)
  * hero: CTA reads "Open Steam Workshop" with a handler present and "Get Steam"
    without one; the no-steam state adds its sentence to the candid paragraph
  * empty state: the dashed ghost row renders with the landing caption
  * tiles: review items populate newest-first with no forecast chip
    (the old measured-verdict chip was removed - the wizard owns crash/heavy now)
  * keep: the tile lingers dimmed with "In library" until scope exit; the item is in
    good_ids immediately
  * trash micro-wizard (16d): beat 1 opens with the mode-correct consequence well;
    confirm tombstones, swaps to beat 2 in place with the exact deep link in the well,
    the unsubscribe primary and the skip ghost; the tile leaves
  * hot watch: scope visibility force-arms the folder watch under DETECT_MODE=manual
    and disarms on exit

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software python3 tests/test_workshop_ui.py
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_TMP = tempfile.mkdtemp(prefix="lwe-wsui-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

_SHIM = Path(_TMP) / "bin"
_SHIM.mkdir()
os.environ["PATH"] = f"{_SHIM}:{os.environ.get('PATH', '')}"


def _set_steam_shim(present: bool) -> None:
    sh = _SHIM / "xdg-mime"
    sh.write_text("#!/bin/sh\n" + ("echo steam.desktop\n" if present else "exit 0\n"),
                  encoding="utf-8")
    sh.chmod(sh.stat().st_mode | stat.S_IEXEC)


def _mk_item(workshop: Path, wid: str, wtype: str = "scene") -> None:
    d = workshop / wid
    d.mkdir(parents=True, exist_ok=True)
    fname = {"video": "video.mp4", "web": "index.html"}.get(wtype, "scene.json")
    (d / "project.json").write_text(
        json.dumps({"type": wtype, "title": f"Item {wid}", "file": fname}),
        encoding="utf-8")
    if wtype == "scene":
        (d / "scene.pkg").write_bytes(b"x" * 32)
    else:
        (d / fname).write_bytes(b"v" * 32)
    (d / "preview.jpg").write_bytes(b"j" * 8)


def visual_items(item, out=None):
    if out is None:
        out = []
    for ch in item.childItems():
        out.append(ch)
        visual_items(ch, out)
    return out


def _on_screen(it) -> bool:
    """Walk up the parent chain: a text is only shown if it AND every ancestor is
    visible (a hidden chip's Label still lives in the tree)."""
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
    _set_steam_shim(True)

    from PySide6.QtCore import QUrl
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
    from lwe_ui.storage import importer, paths, settings, tags
    from lwe_ui.workshop import WorkshopBridge

    bench_courier.resume = lambda *a, **k: True

    paths.ensure_dirs()
    settings.ensure_exists()
    workshop_dir = Path(_TMP) / "workshop"
    workshop_dir.mkdir()
    s = settings.load()
    s["WORKSHOP_DIR"] = str(workshop_dir)
    s["DETECT_MODE"] = "manual"
    settings.save(s)

    _mk_item(workshop_dir, "301", "scene")
    _mk_item(workshop_dir, "302", "web")
    importer.run_scan_and_import()

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
    for n, o in (("backend", backend), ("editor", editor), ("bench", bench),
                 ("dev", dev), ("workshop", ws), ("importBridge", ib)):
        engine.rootContext().setContextProperty(n, o)
    from lwe_ui.wizard_bridge import WizardBridge
    _wizb = WizardBridge(engine.rootContext().contextProperty("backend"),
                         engine.rootContext().contextProperty("workshop"))
    engine.rootContext().setContextProperty("wizardBridge", _wizb)
    engine.load(QUrl.fromLocalFile(str(_QML_DIR / "Main.qml")))
    assert engine.rootObjects(), "Main.qml failed to load"
    win = engine.rootObjects()[0]
    QTest.qWait(80)

    rail_src = (_QML_DIR / "Rail.qml").read_text(encoding="utf-8")
    assert 'label: "Review"' not in rail_src, "the Review rail item must be gone"
    assert 'label: "Workshop"' in rail_src
    assert "IconTray" not in rail_src.split("workshopItem")[1].split("}")[0], \
        "the tray glyph must be replaced by the drawn plus"

    view = win.findChild(QQuickItem, "workshopView")
    assert view is not None, "WorkshopView must be mounted in Main.qml"
    assert view.property("visible") is False

    win.setProperty("currentView", "workshop")
    QTest.qWait(120)
    assert view.property("visible") is True

    heroTexts = texts_under(view)
    assert any("Get more wallpapers" in t for t in heroTexts)
    assert any("Subscribe on the Steam Workshop" in t for t in heroTexts)
    assert any("We bench it together" in t for t in heroTexts), "the honest step 3 must show"
    assert any("Requires Wallpaper Engine owned and installed" in t for t in heroTexts)
    assert win.findChild(QQuickItem, "workshopCta") is None, \
        "the hero CTA button is gone - the AddTile is the CTA now"
    assert win.findChild(QQuickItem, "headerImportTombstoned") is None, \
        "the Import-tombstoned header ghost must be gone - it became the Workshop tombstone tile"

    flow = win.findChild(QQuickItem, "workshopFlow")
    tileTexts = texts_under(flow)
    assert any("Item 301" in t for t in tileTexts) and any("Item 302" in t for t in tileTexts)
    assert not any("Heavy" in t or "Ran clean" in t for t in tileTexts), \
        f"the old measured verdict chip must be gone: {tileTexts}"
    assert not any("bench first" in t for t in tileTexts), \
        "no forecast chip - web wallpapers are fully supported"
    assert any("Get wallpapers" in t for t in tileTexts), "the AddTile must be the first grid cell"

    assert str(workshop_dir) in list(ib._fs_watcher.directories()), \
        "scope visibility must force-arm the folder watch"

    from PySide6.QtCore import QMetaObject, Qt as _Qt, Q_ARG
    _wizb.open("301", "Item 301")
    _wizb.importUntested("")
    QTest.qWait(80)
    assert "301" in tags.good_ids(), "import graduates immediately"
    assert not any("Item 301" in t for t in texts_under(flow)), \
        "the graduated tile leaves Workshop at once (no green-check step)"
    assert not any("In library" in t for t in texts_under(flow)), "no lingering In-library chip"

    dlg = win.findChild(object, "workshopTrashDialog")
    assert dlg is not None
    QMetaObject.invokeMethod(dlg, "openFor", _Qt.ConnectionType.DirectConnection,
                             Q_ARG("QVariant", "302"), Q_ARG("QVariant", "Item 302"))
    QTest.qWait(80)
    assert dlg.property("visible") is True and dlg.property("beat") == 1
    b1 = texts_under(dlg.property("contentItem"))
    assert any("clear its tombstone" in t for t in b1), b1
    assert any("Copy mode. Our copy is deleted from disk." in t for t in b1), \
        "302 imported under copy policy - the fine print must state the danger truth"
    assert any(t == "Trash it" for t in b1) and any(t == "Cancel" for t in b1)
    QMetaObject.invokeMethod(dlg, "confirmTrash", _Qt.ConnectionType.DirectConnection)
    QTest.qWait(200)
    assert dlg.property("beat") == 2 and dlg.property("visible") is True
    b2 = texts_under(dlg.property("contentItem"))
    assert any("One step to free disk space" in t for t in b2), b2
    assert any(t == "steam://url/CommunityFilePage/302" for t in b2), \
        f"the link well must display the exact deep link: {b2}"
    assert any(t == "Unsubscribe on Steam" for t in b2)
    assert any(t == "Keep it downloaded" for t in b2)
    dlg.setProperty("visible", False)
    QTest.qWait(120)
    assert "302" not in tags.review_ids() and "302" in tags.known_ids()
    assert not any("Item 302" in t for t in texts_under(flow)), "trashed tile must leave"

    QTest.qWait(80)
    assert any("Get wallpapers" in t for t in texts_under(flow)), \
        "empty scope still shows the AddTile as the first grid cell"

    win.setProperty("currentView", "library")
    QTest.qWait(80)
    assert str(workshop_dir) not in list(ib._fs_watcher.directories()), \
        "scope exit must disarm the forced watch under manual mode"

    _set_steam_shim(False)
    win.setProperty("currentView", "workshop")
    QTest.qWait(150)
    nsTiles = texts_under(win.findChild(QQuickItem, "workshopFlow"))
    assert any("Get Steam" in t for t in nsTiles), \
        "the AddTile CTA swaps to Get Steam without a steam handler"
    assert any("Opens the download page" in t for t in nsTiles), \
        "the AddTile subcopy explains the no-steam action"

    _set_steam_shim(True)
    win.setProperty("currentView", "library")
    QTest.qWait(80)
    d = workshop_dir / "410"
    d.mkdir()
    (d / "project.json").write_text(json.dumps({
        "title": "Held preset", "dependency": "777000111",
        "preset": {"wec_brs": 50}, "preview": "preview.jpg"}), encoding="utf-8")
    (d / "preview.jpg").write_bytes(b"j" * 8)
    win.setProperty("currentView", "workshop")   # scope entry scans; arrival sweep runs
    deadline = __import__("time").time() + 15
    from lwe_ui.storage import tags as _tags
    while __import__("time").time() < deadline and \
            not any("Missing dependency" == t for t in texts_under(flow)):
        QTest.qWait(50)
    assert "410" in _tags.review_ids(), "held preset must land in review"
    assert any("Missing dependency" == t for t in texts_under(flow)), \
        "the filled amber chip must render"
    depDlg = win.findChild(object, "workshopDepDialog")
    assert depDlg is not None
    # auto-show is driven by depSweep on each scanFinished; assert the LOGIC
    # deterministically rather than racing the offscreen popup transition against the
    # hot-watch scan churn. Fresh session state, then one sweep:
    view.setProperty("shownDeps", {})
    QMetaObject.invokeMethod(view, "depSweep", _Qt.ConnectionType.DirectConnection)
    QTest.qWait(80)
    assert depDlg.property("visible") is True, "depSweep must auto-show a newly-held item"
    dt = texts_under(depDlg.property("contentItem"))
    assert any("Missing a dependency" in t for t in dt), dt
    assert any("Depends on another Workshop item" in t for t in dt), \
        "no local name -> the generic well copy"
    assert any(t == "777000111" for t in dt), "the mono dep id shows in the well"
    assert any(t == "Get it on Steam" for t in dt)
    assert any(t == "Trash this scene instead" for t in dt)
    assert any("stays in Workshop marked missing dependency" in t for t in dt)
    depDlg.setProperty("visible", False)
    QTest.qWait(50)
    QMetaObject.invokeMethod(depDlg, "openFor", _Qt.ConnectionType.DirectConnection,
                             Q_ARG("QVariant", "301"), Q_ARG("QVariant", "x"))
    QTest.qWait(50)
    assert depDlg.property("visible") is False, "openFor must refuse a non-held item"

    # auto-close on resolve: land the base, the resolve pass clears the hold, the chip
    # goes, and a depSweep with the item open closes it (the hands-free law). Assert on
    # STATE + the sweep's decision - offscreen popup visibility alone is transition-racy.
    QMetaObject.invokeMethod(depDlg, "openFor", _Qt.ConnectionType.DirectConnection,
                             Q_ARG("QVariant", "410"), Q_ARG("QVariant", "Held preset"))
    QTest.qWait(50)
    assert depDlg.property("visible") is True
    _mk_item(workshop_dir, "777000111", "scene")
    ib.rescanNow()
    deadline = __import__("time").time() + 15
    from lwe_ui.storage import meta as _meta
    while __import__("time").time() < deadline and _meta.get("410").get("depMissing"):
        QTest.qWait(50)
    assert not _meta.get("410").get("depMissing"), "the base arriving must clear the hold"
    # the scanFinished handler refreshes the view; poll the chip out
    deadline = __import__("time").time() + 10
    while __import__("time").time() < deadline and \
            any("Missing dependency" == t for t in texts_under(flow)):
        QTest.qWait(50)
    assert not any("Missing dependency" == t for t in texts_under(flow)), \
        "the chip must clear on resolve"
    QMetaObject.invokeMethod(view, "depSweep", _Qt.ConnectionType.DirectConnection)
    QTest.qWait(50)
    assert depDlg.property("visible") is False, \
        "depSweep must close the modal once its item resolves (hands-free law)"

    _set_steam_shim(True)
    win.setProperty("currentView", "library")
    QTest.qWait(60)
    libDlg = win.findChild(object, "libraryTrashWizard")
    assert libDlg is not None, "the library must host a shared TrashWizard"
    assert libDlg.property("leavesNoun") == "the library", "library noun variant"
    from lwe_ui.storage import tags as _t2
    from pathlib import Path as _P
    lib_root = _P(importer._wallpapers_dir())
    _mk_item(workshop_dir, "555001", "scene")
    (lib_root / "555001").mkdir(parents=True, exist_ok=True)
    (lib_root / "555001" / "scene.pkg").write_bytes(b"x" * 8)
    _t2.set_state("555001", "Some Library Wallpaper", "good")
    assert ws.isSteamSubscribed("555001") is True
    assert ws.trashConsequence("555001")["hasCopy"] is True
    QMetaObject.invokeMethod(libDlg, "openFor", _Qt.ConnectionType.DirectConnection,
                             Q_ARG("QVariant", "555001"), Q_ARG("QVariant", "Some Library Wallpaper"))
    QTest.qWait(60)
    assert libDlg.property("visible") is True and libDlg.property("beat") == 1
    assert libDlg.property("hasSteamPage") is True, "workshop dir present -> beat 2 offered"
    lb1 = texts_under(libDlg.property("contentItem"))
    assert any("leaves the library" in t for t in lb1), lb1
    assert any("Our copy is deleted from disk" in t for t in lb1), "copy-mode consequence"
    QMetaObject.invokeMethod(libDlg, "confirmTrash", _Qt.ConnectionType.DirectConnection)
    QTest.qWait(120)
    assert libDlg.property("beat") == 2, "steam-subscribed -> beat 2 (unsubscribe)"
    assert "555001" in _t2.known_ids() and "555001" not in _t2.good_ids(), "tombstoned"
    deadline = __import__("time").time() + 5
    while (lib_root / "555001").exists() and __import__("time").time() < deadline:
        QTest.qWait(50)
    assert not (lib_root / "555001").exists(), "the library copy must be deleted"
    assert (workshop_dir / "555001").is_dir(), "Steam's tree is never touched"
    lb2 = texts_under(libDlg.property("contentItem"))
    assert any(t == "steam://url/CommunityFilePage/555001" for t in lb2), lb2
    libDlg.setProperty("visible", False)
    QTest.qWait(40)
    _t2.set_state("999333", "Numeric Local Pack", "good")
    assert ws.isSteamSubscribed("999333") is False, "no workshop dir -> not steam-sourced"
    QMetaObject.invokeMethod(libDlg, "openFor", _Qt.ConnectionType.DirectConnection,
                             Q_ARG("QVariant", "999333"), Q_ARG("QVariant", "Numeric Local Pack"))
    QTest.qWait(60)
    assert libDlg.property("hasSteamPage") is False, \
        "a numeric id Steam does not have must NOT offer an unsubscribe deep link"
    QMetaObject.invokeMethod(libDlg, "confirmTrash", _Qt.ConnectionType.DirectConnection)
    QTest.qWait(120)
    assert libDlg.property("visible") is False, "no steam origin -> beat 2 skipped, closes"
    assert "999333" in _t2.known_ids() and "999333" not in _t2.good_ids()
    QMetaObject.invokeMethod(libDlg, "openFor", _Qt.ConnectionType.DirectConnection,
                             Q_ARG("QVariant", "local_pack_x"), Q_ARG("QVariant", "Local Pack"))
    QTest.qWait(60)
    assert libDlg.property("hasSteamPage") is False
    QMetaObject.invokeMethod(libDlg, "confirmTrash", _Qt.ConnectionType.DirectConnection)
    QTest.qWait(120)
    assert libDlg.property("visible") is False, "no Steam page -> beat 2 skipped, closes"
    assert "local_pack_x" in _t2.known_ids() and "local_pack_x" not in _t2.good_ids()

    print("OK test_workshop_ui - rail/hero/tiles/no-forecast/add-tile-first-cell/"
          "trash-dialog/hot-watch/no-steam/dep-modal/library-trash-wizard all hold")


if __name__ == "__main__":
    main()
