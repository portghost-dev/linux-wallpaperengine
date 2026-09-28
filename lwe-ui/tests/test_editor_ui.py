"""Editor takeover: the QML slider-init regression, plus the render-level acceptance tests.

A scene-property slider must initialize from the wallpaper's STORED value. The Loader assigns
`prop` AFTER the child's Component.onCompleted, so init must be a declarative binding, not
onCompleted (else every slider shows 0). Verified by a render diff: a slider whose stored value
is 0.9 fills far more accent-coloured track than one whose value is 0.1. If init were stuck at 0
both renders would be identical.

The editor is a center takeover (EditorView); the archived slide-over editor/bench drawers were
removed. This drives the takeover directly (open the bridge on a scene, then flip the
shell's currentView to "editor") and diffs the accent fill of the column-1 property slider. The
old drawer slide-animation assertion (#B) went away with the drawer.

Python can't reach Repeater-created delegates (PySide QQuickItem* limit), so this is a pixel diff.
One engine, two synthetic wallpapers (0.9 / 0.1 sliders).
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import json
import os
import shutil
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

# Theme.accent - the slider's filled-track color. Resolved INSIDE main after the
# sandbox HOME is exported: at module scope this read the developer's REAL theme.json
# (their live active theme + overlays) while the sandboxed app rendered the stock
# default - zero matching pixels the moment the two configs diverged.
_ACCENT = ""


def _accent_pixels(img, col, x_max=None) -> int:
    # count accent pixels, optionally cropped to x < x_max. The editor takeover carries fixed
    # accent chrome (slider fills, marks) in its right columns that does not vary with the
    # scene slider value, so the slider diff is measured over column 1 alone.
    xm = img.width() if x_max is None else min(x_max, img.width())
    n = 0
    for y in range(0, img.height(), 2):
        for x in range(0, xm, 2):
            p = img.pixelColor(x, y)
            if abs(p.red() - col.red()) < 24 and abs(p.green() - col.green()) < 24 and abs(p.blue() - col.blue()) < 24:
                n += 1
    return n


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-edui-")
    orig = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
    try:
        os.environ["HOME"] = home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
        os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
        os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")
        # resolve AFTER every sandbox path is exported (theme_file lives under
        # XDG_CONFIG_HOME - resolving between the exports still read the real config)
        global _ACCENT
        from lwe_ui.storage import themes as _themes
        _ACCENT = _themes.resolve_active()["accent"]

        from PySide6.QtCore import QUrl, QObject
        from PySide6.QtGui import QColor, QGuiApplication
        from PySide6.QtQuick import QQuickWindow
        from PySide6.QtTest import QTest
        from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance
        from lwe_ui.models import Backend, ThemeTokens
        from lwe_ui.editor import EditorBridge
        from lwe_ui.storage import paths, settings
        from lwe_ui.app import _resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME

        paths.ensure_dirs()
        for name, val in (("synthwp_hi", 0.9), ("synthwp_lo", 0.1), ("synthwp_fit", 0.5)):
            wd = os.path.join(str(paths.default_wallpapers_dir()), name)
            os.makedirs(wd, exist_ok=True)
            json.dump({"type": "scene", "file": "scene.json", "title": name, "general": {"properties": {
                "myslider": {"type": "slider", "value": val, "min": 0, "max": 1, "step": 0.01, "text": "Slide"},
            }}}, open(os.path.join(wd, "project.json"), "w"))
        wd = os.path.join(str(paths.default_wallpapers_dir()), "synthwp_props")
        os.makedirs(wd, exist_ok=True)
        json.dump({"type": "scene", "file": "scene.json", "title": "synthwp_props", "general": {"properties": {
            "mytint": {"type": "color", "value": "1 0 0", "text": "Tint"},
            "mylabel": {"type": "textinput", "value": "hi", "text": "Label"},
        }}}, open(os.path.join(wd, "project.json"), "w"))
        settings.ensure_exists()

        app = QGuiApplication.instance() or QGuiApplication(["t"])
        tokens = ThemeTokens(_resolve_theme_tokens())
        qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)
        engine = QQmlApplicationEngine()
        engine.addImportPath(str(_QML_DIR))
        backend = Backend()
        editor = EditorBridge()
        from lwe_ui.dev import DevBridge
        from lwe_ui.bench_bridge import BenchBridge
        dev = DevBridge()
        bench = BenchBridge()
        engine.rootContext().setContextProperty("backend", backend)
        engine.rootContext().setContextProperty("editor", editor)
        engine.rootContext().setContextProperty("dev", dev)
        from lwe_ui.workshop import WorkshopBridge
        from lwe_ui.models import ImportBridge
        workshop = WorkshopBridge(backend, dev)
        import_bridge = ImportBridge(backend)
        engine.rootContext().setContextProperty("workshop", workshop)
        engine.rootContext().setContextProperty("importBridge", import_bridge)
        engine.rootContext().setContextProperty("bench", bench)
        from lwe_ui.wizard_bridge import WizardBridge
        _wizb = WizardBridge(engine.rootContext().contextProperty("backend"),
                             engine.rootContext().contextProperty("workshop"))
        engine.rootContext().setContextProperty("wizardBridge", _wizb)
        from lwe_ui.deck_popup import DeckPopupBridge
        deck_popup = DeckPopupBridge(backend)
        engine.rootContext().setContextProperty("deckPopup", deck_popup)
        engine.load(QUrl.fromLocalFile(str(_QML_DIR / "Main.qml")))
        assert engine.rootObjects(), "Main.qml failed to load"
        win = engine.rootObjects()[0]
        accent = QColor(_ACCENT)

        # column 1 (authored properties) is 400px wide; the scene slider lives there. Crop the
        # accent count to that column so the fixed accent chrome in columns 2/3 (Save button,
        # override borders) does not swamp the slider-fill difference.
        COL1 = 400

        def open_and_grab(wid: str) -> int:
            # drive the takeover the way Library.onOpenEditor does: load the bridge, then mount
            # the editor view. EditorView column 1 renders the scene-property slider whose fill
            # reflects the stored value.
            editor.open(wid)
            win.setProperty("currentView", "editor")
            QTest.qWait(300)
            img = QQuickWindow.grabWindow(win)
            return _accent_pixels(img, accent, x_max=COL1)

        hi = open_and_grab("synthwp_hi")
        lo = open_and_grab("synthwp_lo")
        assert QQuickWindow.grabWindow(win).width() > 100, "no frame grabbed"

        # slider reflects stored value: 0.9 fills more accent track than 0.1. The rest of
        # column 1 (labels, filter field) is identical between the two, so the difference is the
        # slider fill alone; near-equal means the slider ignored its stored value (init stuck
        # at 0). Threshold calibrated to the canvas-compact 100px slider: a 0.8 value
        # delta on a 3px-tall 100px track sampled every 2x2 lands ~25-30 px; a stuck-at-0
        # slider lands under 8. 15 separates them with margin on both sides.
        assert hi > lo + 15, (
            f"a stored slider value of 0.9 must fill more accent track than 0.1 "
            f"(column-1 accent px: 0.9->{hi}, 0.1->{lo}); near-equal means the slider ignored its "
            f"stored value and initialized at 0"
        )
        print(f"OK test_editor_ui - takeover slider reflects value (col1 accent px 0.9->{hi} vs 0.1->{lo})")

        ev = win.findChild(QObject, "editorView")
        assert ev is not None, "EditorView must be reachable by objectName"

        def mode_at(w: int, h: int = 720) -> bool:
            win.setProperty("width", w)
            win.setProperty("height", h)
            QTest.qWait(120)
            return bool(ev.property("compactMode"))

        wide = mode_at(1280)
        assert wide is False, "a 1280x720 quarter panel must stay in full three-workspace mode (T12)"
        narrow = mode_at(640)
        assert narrow is True, "a 640x720 eighth panel must collapse to one workspace (T12)"
        # widening back past the RESTORE threshold returns it; the hysteresis means a width
        # that merely clears the collapse threshold is not enough
        assert mode_at(1280) is False, "widening past the restore threshold must restore (T11)"

        # the surviving workspace keeps its navigation state across the transition: only
        # visibility changes, so the segment index the user chose survives a resize round-trip
        mode_at(640)
        ev.setProperty("activeWorkspace", 2)
        mode_at(1280)
        mode_at(640)
        assert int(ev.property("activeWorkspace")) == 2, \
            "collapsing must preserve per-workspace state, not reset it (T11)"
        mode_at(1280)
        print("OK T11/T12 - compact law collapses at 640, restores at 1280, state survives")

        # fit rows: present on both surfaces, seated from the store, round trip, revert. A
        # wallpaper not opened before, so its session seat is the values written here.
        from lwe_ui.storage import wp as _wp
        _wp.update_set("synthwp_fit", {"FIT_ZOOM": 1.5, "FIT_PAN_X": 0.5})
        editor.open("synthwp_fit")
        QTest.qWait(200)
        sliders = {name: win.findChild(QObject, name)
                   for name in ("editorFitZoom", "editorPanX", "editorPanY",
                                "popupFitZoom", "popupPanX", "popupPanY")}
        missing = [n for n, s in sliders.items() if s is None]
        assert not missing, f"fit rows must be reachable by objectName: missing {missing}"
        assert abs(float(sliders["editorFitZoom"].property("value")) - 1.5) < 1e-6, \
            "the Zoom slider must seat from the stored FIT_ZOOM"
        assert abs(float(sliders["editorPanX"].property("value")) - 0.5) < 1e-6, \
            "the Pan X slider must seat from the stored FIT_PAN_X"
        assert abs(float(sliders["editorPanY"].property("value"))) < 1e-6, \
            "an absent FIT_PAN_Y seats the Pan Y slider at centre"
        # the chip arithmetic: pan is a fraction of the travel the zoom leaves (zoom 2, pan 1 =
        # a quarter of the picture), +x right; a % entry converts back through the zoom
        from PySide6.QtCore import QMetaObject, Q_RETURN_ARG, Q_ARG
        def call(obj, name, *args):
            return QMetaObject.invokeMethod(obj, name, Q_RETURN_ARG("QVariant"),
                                            *[Q_ARG("QVariant", a) for a in args])
        popup_root = next(c for c in win.findChildren(QObject)
                          if c.metaObject().className().startswith("DeckSettingsPopup"))
        for surface in (ev, popup_root):
            # the chip reads percent of the screen at any zoom: fifty at the ends
            assert call(surface, "panPercent", 1.0, 2.0) == "+50%"
            assert call(surface, "panPercent", -1.0, 2.0) == "-50%"
            assert call(surface, "panPercent", 0.5, 1.6) == "+25%"
            assert call(surface, "panPercent", 0.7, 1.0) == "+35%"
            assert call(surface, "panPercent", -0.01, 1.6) == "+0%", "a rounded zero is never -0%"
            assert abs(float(call(surface, "panFromEntry", "12%", 1.6)) - 0.24) < 1e-9
            assert abs(float(call(surface, "panFromEntry", "-0.25", 1.6)) + 0.25) < 1e-9
            assert abs(float(call(surface, "panFromEntry", "12%", 1.0)) - 0.24) < 1e-9, "a % entry needs no zoom"
        assert not editor.setFit("zoom", "wide"), "a non-number is refused"
        assert editor.setFit("zoom", "1.25")
        QTest.qWait(120)
        assert abs(float(sliders["editorFitZoom"].property("value")) - 1.25) < 1e-6, \
            "a bridge write must re-seat the Zoom slider through the store"
        assert _wp.load_set("synthwp_fit").get("FIT_ZOOM") == 1.25
        assert editor.isMarked("FIT_ZOOM"), "a changed fit row wears the mark"
        # a blank entry returns the row to what it inherits: the key goes
        assert editor.setFit("zoom", "")
        assert "FIT_ZOOM" not in _wp.load_set("synthwp_fit")
        assert editor.setFit("zoom", "1.25")
        # live class: when the edited wallpaper is the one on screen, a fit write pushes the
        # wallpaper layer through set-fit instead of queueing a re-show; otherwise nothing is sent
        from lwe_ui import editor as _editor_mod
        from lwe_ui import version as _version
        from lwe_ui.engine import marker as _marker, push as _push
        _marker.clear(_marker.read()["generation"])   # the commits above left their work pending
        _push._monotonic = lambda: 10_000.0
        _marker.record_served(1, 9900.0)
        pushes = []
        on_screen = ["synthwp_fit"]
        _editor_mod.api_client.available = lambda: True
        _editor_mod.api_client.status = lambda *a, **k: {
            "api": 1, "version": _version.panel_stamp(), "pid": 1, "uptime_s": 100,
            "current": {"id": on_screen[0], "ui_id": on_screen[0]}}
        _editor_mod.api_client.set_fit = lambda **kw: (pushes.append(dict(kw)) or {"ok": True, "status": "done"})
        editor.syncCurrent("synthwp_fit")
        assert editor.setFit("pan_x", "0.25")
        assert pushes == [{"layer": "wallpaper", "id": "synthwp_fit", "zoom": 1.25, "pan_x": 0.25, "pan_y": 0.0}], pushes
        assert not editor._reshow.isActive(), "a fit write must not queue a re-show"
        editor.syncCurrent("")
        on_screen[0] = "other"
        assert editor.setFit("pan_x", "0.5")
        assert len(pushes) == 1, "an editor open on a wallpaper not on screen sends nothing"
        # a slider mid-drag previews live through the same gate and never writes the store
        editor.previewFit("pan_y", "-0.25")
        QTest.qWait(80)
        assert len(pushes) == 1, "a preview off screen sends nothing"
        editor.syncCurrent("synthwp_fit")
        editor.previewFit("pan_y", "-0.100")
        editor.previewFit("pan_y", "-0.250")
        QTest.qWait(80)
        assert pushes[-1] == {"layer": "wallpaper", "id": "synthwp_fit", "zoom": 1.25, "pan_x": 0.5, "pan_y": -0.25}, pushes[-1]
        assert len(pushes) == 2, "drag steps in one tick coalesce to one push"
        assert "FIT_PAN_Y" not in _wp.load_set("synthwp_fit"), "a preview never writes the store"
        assert not editor.isMarked("FIT_PAN_Y")
        editor.syncCurrent("")
        # Speed, Volume and the dials preview the verb their release would send: the global
        # rows regardless of the scope gate, the per-wallpaper rows and the dials behind it
        sent = []
        _editor_mod.api_client.set_speed = lambda v: (sent.append(("speed", v)) or {"ok": True})
        _editor_mod.api_client.set_volume = lambda v: (sent.append(("volume", v)) or {"ok": True})
        _editor_mod.api_client.set_tuning = lambda **kw: (sent.append(("tuning", dict(kw))) or {"ok": True})
        editor.previewLive("wp_speed", 2.0)
        editor.previewLive("wp_volume", 30.0)
        editor.previewLive("dial:RESPONSE_THRESHOLD", 0.5)
        QTest.qWait(80)
        assert sent == [], "off screen, per-wallpaper rows and dials send nothing"
        editor.previewLive("speed", 1.5)
        editor.previewLive("volume", 20.0)
        QTest.qWait(80)
        assert ("volume", 20) in sent and any(k == "speed" for k, _ in sent), sent
        sent.clear()
        editor.syncCurrent("synthwp_fit")
        editor.previewLive("wp_speed", 2.0)
        editor.previewLive("wp_volume", 30.0)
        editor.previewLive("dial:RESPONSE_THRESHOLD", 0.5)
        QTest.qWait(80)
        assert ("volume", 30) in sent and any(k == "speed" for k, _ in sent) and any(k == "tuning" and "audio_gain" in v for k, v in sent), sent
        assert "SPEED" not in _wp.load_set("synthwp_fit") and "VOLUME" not in _wp.load_set("synthwp_fit"), "a preview never writes the store"
        editor.syncCurrent("")
        assert editor.revertChanges()
        QTest.qWait(120)
        assert _wp.load_set("synthwp_fit").get("FIT_ZOOM") == 1.5, "revert restores the seated value"
        assert abs(float(sliders["editorFitZoom"].property("value")) - 1.5) < 1e-6
        assert not editor.isMarked("FIT_ZOOM")
        # the popup reads the same store once its bridge is seated on the wallpaper. Last,
        # and read without waiting: the deck's status tick re-syncs the popup to whatever the
        # engine shows (nothing here), which is the popup's own mark boundary.
        deck_popup.syncCurrent("synthwp_fit")
        assert abs(float(sliders["popupFitZoom"].property("value")) - 1.5) < 1e-6, \
            "the popup Zoom slider must seat from the same stored FIT_ZOOM"
        assert abs(float(sliders["popupPanX"].property("value")) - 0.5) < 1e-6, \
            "the popup Pan X slider must seat from the same stored FIT_PAN_X"
        assert abs(float(sliders["popupPanY"].property("value"))) < 1e-6
        print("OK fit rows - six sliders present, seated from the store on both surfaces, "
              "round trip, revert, chip arithmetic pinned")

        settings.update({"ENGINE_VOLUME": 120})
        _wp.update_set("synthwp_hi", {"VOLUME": 120})
        editor.open("synthwp_hi")
        deck_popup.stateChanged.emit()
        QTest.qWait(120)
        chips = {name: win.findChild(QObject, name)
                 for name in ("editorVolumeChip", "editorWpVolumeChip", "popupVolumeChip")}
        missing = [n for n, c in chips.items() if c is None]
        assert not missing, f"the volume chips must be reachable by objectName: missing {missing}"
        texts = {n: c.property("text") for n, c in chips.items()}
        assert texts == dict.fromkeys(chips, "120"), f"every volume chip must read the stored 120: {texts}"
        print("OK volume chips - a stored 120 reads 120 on both editor rows and the popup")
        _wp.update_set("synthwp_hi", {"VOLUME": 7})
        editor.open("synthwp_fit")
        editor.open("synthwp_hi")
        QTest.qWait(120)
        texts = {n: chips[n].property("text") for n in ("editorVolumeChip", "editorWpVolumeChip")}
        assert texts == {"editorVolumeChip": "120", "editorWpVolumeChip": "7"}, \
            f"the wallpaper's Volume chip must read its own VOLUME, not ENGINE_VOLUME: {texts}"
        print("OK volume chips - VOLUME 7 beside ENGINE_VOLUME 120 reads 7 on the wallpaper row")

        # the editable boxes: an entry opened by a tap and left with the text it opened with, by
        # Enter or by moving to another box, sends nothing; a changed entry saves; each clamp row's
        # menu pick and display are its own key's
        from PySide6.QtCore import QPointF, Qt

        def walk(item):
            yield item
            for child in item.childItems():
                yield from walk(child)

        def box(key):
            return next(i for i in walk(win.contentItem())
                        if i.property("ckey") == key and i.property("editable") is not None)

        def entry(item):
            return next(i for i in walk(item) if i.metaObject().className().startswith("QQuickTextInput"))

        def tap(item):
            flick = item.parentItem()
            while not flick.metaObject().className().startswith("QQuickFlickable"):
                flick = flick.parentItem()
            top = item.mapToItem(flick.property("contentItem"), QPointF(0, 0)).y()
            flick.setProperty("contentY", max(0.0, top - 40.0))
            QTest.qWait(60)
            at = item.mapToScene(QPointF(15, item.height() / 2)).toPoint()
            QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
            QTest.qWait(60)

        editor.open("synthwp_lo")
        win.requestActivate()
        QTest.qWait(200)
        failures, edits = [], []
        editor.commitFailed.connect(lambda keys: failures.append(list(keys)))
        editor.edited.connect(lambda: edits.append(1))
        ss, cc, fps = box("SSFACTOR"), box("CLAMPCOMPOSITES"), box("ENGINE_FPS")
        conf = paths.wp_file("synthwp_lo")
        tap(ss)
        opened = (ss.property("editing"), entry(ss).property("text"), entry(ss).hasActiveFocus())
        tap(cc)
        QTest.keyClick(win, Qt.Key.Key_Return)
        tap(ss)
        QTest.keyClick(win, Qt.Key.Key_Return)
        tap(fps)
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(80)
        left = [b.property("editing") for b in (ss, cc, fps)]
        assert opened == (True, "Global", True), opened
        assert (left, failures, edits, conf.exists(), editor._reshow.isActive()) == \
            ([False] * 3, [], [], False, False), (left, failures, edits, conf.exists())
        assert editor.setClampValue("SSFACTOR", "1.5")
        QTest.qWait(750)
        failures.clear()
        edits.clear()
        before = conf.read_text(encoding="utf-8")
        tap(ss)
        opened = entry(ss).property("text")
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(80)
        assert opened == editor.clampValue("SSFACTOR"), (opened, editor.clampValue("SSFACTOR"))
        assert (failures, edits, editor._reshow.isActive(), conf.read_text(encoding="utf-8")) == \
            ([], [], False, before), "an own number entered unchanged saves nothing and re-shows nothing"
        tap(ss)
        entry(ss).setProperty("text", "2")
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(80)
        assert (_wp.load_set("synthwp_lo").get("SSFACTOR"), edits, editor._reshow.isActive()) == (2.0, [1], True)
        print("OK clamp and FPS boxes - a tap left or entered unchanged sends nothing; a changed entry saves")
        QMetaObject.invokeMethod(cc, "picked", Qt.ConnectionType.DirectConnection, Q_ARG("QString", "1"))
        QMetaObject.invokeMethod(ss, "picked", Qt.ConnectionType.DirectConnection, Q_ARG("QString", "0"))
        QTest.qWait(80)
        stored = _wp.load_set("synthwp_lo")
        assert (stored.get("SSFACTOR"), stored.get("CLAMPCOMPOSITES")) == (0.0, 1.0), stored
        assert editor.setClampValue("CLAMPCOMPOSITES", "0.5")
        QTest.qWait(80)
        shown = (ss.property("display"), cc.property("display"))
        assert shown == (editor.clampValue("SSFACTOR"), editor.clampValue("CLAMPCOMPOSITES")) \
            and shown[0] != shown[1], shown
        print("OK clamp rows - each menu pick saves and each box displays its own key")

        # the value chips: a tap, then a move away with nothing typed, sends nothing; a typed change
        # saves. Escape in an open entry cancels it and the view stays on the editor; with no entry
        # open, Escape still leaves the editor
        from PySide6.QtQuick import QQuickItem
        view_item = win.findChild(QQuickItem, "editorView")

        def chip(key):
            return next(i for i in walk(win.contentItem())
                        if i.property("ckey") == key and i.property("entries") is not None
                        and i.property("editable") is None)

        zoom, speed = chip("FIT_ZOOM"), chip("ENGINE_TIMESCALE")
        assert editor.setFit("zoom", "1.25")
        QTest.qWait(80)
        failures.clear()
        for item in (zoom, speed):
            tap(item)
            opened = (item.property("editing"), entry(item).hasActiveFocus())
            view_item.forceActiveFocus()
            QTest.qWait(80)
            assert (opened, item.property("editing")) == ((True, True), False), (item.property("ckey"), opened)
        assert (_wp.load_set("synthwp_lo").get("FIT_ZOOM"), failures) == (1.25, []), \
            (_wp.load_set("synthwp_lo").get("FIT_ZOOM"), failures)
        tap(zoom)
        entry(zoom).setProperty("text", "1.5")
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(80)
        assert _wp.load_set("synthwp_lo").get("FIT_ZOOM") == 1.5
        assert editor.setFit("zoom", "1.75")
        tap(zoom)
        view_item.forceActiveFocus()
        QTest.qWait(80)
        assert _wp.load_set("synthwp_lo").get("FIT_ZOOM") == 1.75, "the entry's last typed text is not sent again"
        print("OK value chips - a tap left with nothing typed sends nothing; a typed change saves")
        plus = next(i for i in walk(view_item) if i.property("text") == "+")
        tags_before = editor.tags()
        for item, key in ((ss, "SSFACTOR"), (zoom, "FIT_ZOOM"), (plus.parentItem(), "tags")):
            kept = _wp.load_set("synthwp_lo").get(key) if key != "tags" else tags_before
            tap(item)
            typed = entry(item)
            typed.setProperty("text", "1.9")
            opened = typed.hasActiveFocus()
            QTest.keyClick(win, Qt.Key.Key_Escape)
            QTest.qWait(80)
            now = _wp.load_set("synthwp_lo").get(key) if key != "tags" else editor.tags()
            assert (opened, typed.hasActiveFocus(), now, win.property("currentView")) == \
                (True, False, kept, "editor"), (key, opened, now, win.property("currentView"))
        editor.open("synthwp_props")
        QTest.qWait(300)
        inputs = [i for i in walk(view_item) if i.metaObject().className().startswith("QQuickTextInput")]
        hex_entry = next(i for i in inputs if str(i.property("text")).startswith("#"))
        text_entry = next(i for i in inputs if i.property("text") == "hi")
        for typed, text in ((hex_entry, "#00ff00"), (text_entry, "changed")):
            tap(typed)
            opened = typed.hasActiveFocus()
            typed.setProperty("text", text)
            QTest.keyClick(win, Qt.Key.Key_Escape)
            QTest.qWait(80)
            assert win.property("currentView") == "editor", (text, "Escape left the editor")
            assert (opened, typed.hasActiveFocus()) == (True, False), (text, opened)
        assert "mytint" not in _wp.load_set("synthwp_props")["props"], "Escape in the hex entry commits nothing"
        view_item.forceActiveFocus()
        QTest.keyClick(win, Qt.Key.Key_Escape)
        QTest.qWait(80)
        assert win.property("currentView") == "library", "with no entry open, Escape leaves the editor"
        win.setProperty("currentView", "editor")
        QTest.qWait(80)
        print("OK Escape - an open entry cancels and the editor stays; with no entry open it leaves the editor")

        # the text property entry and the title entry: a tap left untouched, or Escaped, writes nothing
        # and keeps the editor; a typed change closed with Return saves
        from lwe_ui.storage import meta as _meta
        _wp.update_set("synthwp_props", {"PROP_mylabel": None})
        editor.open("synthwp_fit")
        editor.open("synthwp_props")
        QTest.qWait(300)
        stores = (paths.wp_file("synthwp_props"), paths.meta_file())

        def snapshot():
            return tuple(p.read_text(encoding="utf-8") if p.exists() else None for p in stores)

        text_entry = next(i for i in walk(view_item) if i.property("text") == "hi"
                          and i.metaObject().className().startswith("QQuickTextInput"))
        title = editor.property("title")
        title_entry = next(i for i in walk(view_item) if i.property("text") == title
                           and "TextField" in i.metaObject().className())
        before = snapshot()
        for typed, shown in ((text_entry, "hi"), (title_entry, title)):
            tap(typed)
            opened = typed.hasActiveFocus()
            view_item.forceActiveFocus()
            QTest.qWait(80)
            assert (opened, snapshot()) == (True, before), (shown, "a tap left untouched writes nothing")
            tap(typed)
            typed.setProperty("text", "typed")
            QTest.keyClick(win, Qt.Key.Key_Escape)
            QTest.qWait(80)
            assert (snapshot(), typed.property("text"), typed.hasActiveFocus(), win.property("currentView")) == \
                (before, shown, False, "editor"), (shown, "Escape writes nothing and keeps the editor")
        tap(text_entry)
        text_entry.setProperty("text", "bye")
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(80)
        tap(title_entry)
        title_entry.setProperty("text", "Renamed")
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(80)
        saved = (_wp.load_set("synthwp_props")["props"].get("mylabel"), _meta.get("synthwp_props").get("title"))
        assert saved == ("bye", "Renamed"), saved
        editor.open("synthwp_fit")
        editor.open("synthwp_props")
        QTest.qWait(300)
        text_entry = next(i for i in walk(view_item) if i.property("text") == "bye"
                          and i.metaObject().className().startswith("QQuickTextInput"))
        tap(text_entry)
        text_entry.setProperty("text", "")
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(80)
        view_item.forceActiveFocus()
        QTest.qWait(80)
        cleared = (text_entry.property("text"), _wp.load_set("synthwp_props")["props"].get("mylabel"))
        assert cleared == ("hi", None), ("a cleared entry shows the author's value and pins nothing", cleared)
        editor.setProp("mylabel", "bye")
        QTest.qWait(80)
        tap(text_entry)
        text_entry.setProperty("text", "zzz")
        QTest.keyClick(win, Qt.Key.Key_Escape)
        QTest.qWait(80)
        escaped = text_entry.property("text")
        editor.setProp("mylabel", "")
        QTest.qWait(80)
        followed = (escaped, text_entry.property("text"), _wp.load_set("synthwp_props")["props"].get("mylabel"))
        assert followed == ("bye", "hi", None), ("after Escape the entry follows its row again", followed)
        print("OK text and title entries - a tap left untouched or Escaped writes nothing; a typed change saves")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
