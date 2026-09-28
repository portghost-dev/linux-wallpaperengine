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
        # the Settings view mounts in the same window; the Escape tests reach it there
        from lwe_ui.settings_bridge import SettingsBridge
        from lwe_ui.models import ThemeBridge
        settings_bridge = SettingsBridge(backend, import_bridge)
        engine.rootContext().setContextProperty("settingsBridge", settings_bridge)
        theme_bridge = ThemeBridge(tokens)   # held: an unreferenced bridge is collected under QML
        engine.rootContext().setContextProperty("themeBridge", theme_bridge)
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

        def chip(key):
            return next(i for i in walk(win.contentItem())
                        if i.property("ckey") == key and i.property("entries") is not None
                        and i.property("editable") is None)

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
        ss, cc, fps = chip("SSFACTOR"), chip("CLAMPCOMPOSITES"), box("ENGINE_FPS")
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
        assert opened == "1.50", opened
        assert (failures, edits, editor._reshow.isActive(), conf.read_text(encoding="utf-8")) == \
            ([], [], False, before), "an own number entered unchanged saves nothing and re-shows nothing"
        tap(ss)
        entry(ss).setProperty("text", "2")
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(80)
        assert (_wp.load_set("synthwp_lo").get("SSFACTOR"), edits, editor._reshow.isActive()) == (2.0, [1], True)
        print("OK clamp chips and FPS box - a tap left or entered unchanged sends nothing; a changed entry saves")
        assert editor.setClampValue("CLAMPCOMPOSITES", "1") and editor.setClampValue("SSFACTOR", "0")
        QTest.qWait(80)
        stored = _wp.load_set("synthwp_lo")
        picked = ((stored.get("SSFACTOR"), stored.get("CLAMPCOMPOSITES")), ss.property("text"), cc.property("text"))
        assert editor.setClampValue("CLAMPCOMPOSITES", "0.5")
        QTest.qWait(80)
        shown = (ss.property("text"), cc.property("text"))
        assert (picked, shown) == (((0.0, 1.0), "Off", "1.00"), ("Off", "0.50")), (picked, shown)
        print("OK clamp rows - each chip shows its own key's value")

        # the value chips: a tap, then a move away with nothing typed, sends nothing; a typed change
        # saves. Escape in an open entry cancels it and the view stays on the editor; with no entry
        # open, Escape still leaves the editor
        from PySide6.QtQuick import QQuickItem
        view_item = win.findChild(QQuickItem, "editorView")

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

        # closed entries: Return, the keypad's Enter or a lost focus gives focus back to the view,
        # so the next Escape reaches it. Nothing forces focus before an Escape from here on
        def esc():
            QTest.keyClick(win, Qt.Key.Key_Escape)
            QTest.qWait(80)
            return win.property("currentView")

        def two_esc():
            first = esc()
            return first, (esc() if first != "library" else "-")

        def to_view(name):
            win.setProperty("currentView", name)
            QTest.qWait(200)

        from PySide6.QtQuick import QQuickWindow as _Window
        other = _Window()
        other.resize(120, 80)
        other.show()
        QTest.qWait(100)
        win.requestActivate()
        QTest.qWait(200)

        editor.open("synthwp_lo")
        to_view("editor")
        ss, fps, zoom = chip("SSFACTOR"), box("ENGINE_FPS"), chip("FIT_ZOOM")
        answers = []
        for item, typed, key in ((ss, "2", Qt.Key.Key_Return), (fps, None, Qt.Key.Key_Return),
                                 (zoom, "1.5", Qt.Key.Key_Return), (zoom, "1.6", Qt.Key.Key_Enter),
                                 (fps, None, Qt.Key.Key_Enter)):
            to_view("editor")
            tap(item)
            if typed is not None:
                entry(item).setProperty("text", typed)
            QTest.keyClick(win, key)
            QTest.qWait(80)
            holder = win.activeFocusItem()
            answers.append((item.property("ckey"), item.property("editing"), entry(item).hasActiveFocus(),
                            holder.objectName() if holder is not None else None, two_esc()))
        assert [a[1:] for a in answers] == [(False, False, "editorView", ("library", "-"))] * 5, answers
        stored = _wp.load_set("synthwp_lo")
        assert (stored.get("SSFACTOR"), stored.get("FIT_ZOOM")) == (2.0, 1.6), stored
        # another box's menu, or another window, takes the focus from an open entry and hands it
        # back to the hidden entry later; the entry passes it on to the view
        scaling = next(i for i in walk(view_item) if i.property("ckey") == "SCALING"
                       and i.property("editable") is not None)
        scaling_menu = next(o for o in scaling.findChildren(QObject) if o.inherits("QQuickMenu"))
        to_view("editor")
        tap(zoom)
        opened = (zoom.property("editing"), entry(zoom).hasActiveFocus())
        tap(scaling)
        QTest.qWait(150)
        shown = (scaling_menu.property("visible"), zoom.property("editing"))
        first = esc()
        QTest.qWait(250)
        after = (first, scaling_menu.property("visible"), entry(zoom).hasActiveFocus())
        assert (opened, shown, after) == ((True, True), (True, False), ("editor", False, False)), \
            (opened, shown, after)
        assert esc() == "library", "after another box's menu closes, Escape leaves the editor"
        to_view("editor")
        tap(fps)
        opened = (fps.property("editing"), entry(fps).hasActiveFocus())
        tap(scaling)
        QTest.qWait(150)
        first = esc()
        QTest.qWait(250)
        holder = win.activeFocusItem()
        after = (first, scaling_menu.property("visible"), entry(fps).hasActiveFocus(),
                 holder.objectName() if holder is not None else None)
        assert (opened, after) == ((True, True), ("editor", False, False, "editorView")), (opened, after)
        assert esc() == "library", "after another box's menu closes over a drop box entry, Escape leaves the editor"
        to_view("editor")
        assert win.isActive(), "the editor's window must be active before the tap"
        tap(zoom)
        other.requestActivate()
        QTest.qWait(200)
        away = (win.isActive(), zoom.property("editing"))
        win.requestActivate()
        QTest.qWait(200)
        back = (win.isActive(), entry(zoom).hasActiveFocus())
        assert (away, back, _wp.load_set("synthwp_lo").get("FIT_ZOOM")) == ((False, False), (True, False), 1.6), \
            (away, back)
        assert esc() == "library", "after another window took the focus, Escape leaves the editor"
        # the caret closes an open entry by the same rule, also while its menu will not reopen
        fps_menu = next(o for o in fps.findChildren(QObject) if o.inherits("QQuickMenu"))
        to_view("editor")
        tap(fps)
        opened = (fps.property("editing"), entry(fps).hasActiveFocus())
        fps_menu.setProperty("justClosed", True)
        at = fps.mapToScene(QPointF(fps.width() - 8, fps.height() / 2)).toPoint()
        QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
        QTest.qWait(80)
        closed = (fps.property("editing"), fps_menu.property("visible"), entry(fps).hasActiveFocus())
        fps_menu.setProperty("justClosed", False)
        assert (opened, closed, esc()) == ((True, True), (False, False, False), "library"), (opened, closed)
        print("OK closed entries - Return, the keypad's Enter, another box's menu or another window "
              "closes an entry and the next Escape leaves the editor")

        # a chip's entry opens on its label: an untouched Enter sends nothing, a cleared entry with
        # Enter returns the row to Global, and a typed value with Enter saves
        editor.open("synthwp_lo")
        to_view("editor")
        editor.setSpeedValue(2.0)
        QTest.qWait(120)
        speed_chip = chip("SPEED")
        tap(speed_chip)
        opened = (speed_chip.property("editing"), entry(speed_chip).property("text"), speed_chip.property("text"))
        view_item.forceActiveFocus()
        QTest.qWait(80)
        left = _wp.load_set("synthwp_lo").get("SPEED")
        tap(speed_chip)
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(120)
        entered = _wp.load_set("synthwp_lo").get("SPEED")
        assert (opened, left, entered) == ((True, "2.0x", "2.0x"), 2.0, 2.0), (opened, left, entered)
        tap(speed_chip)
        QTest.keyClick(win, Qt.Key.Key_X)
        QTest.keyClick(win, Qt.Key.Key_Backspace)
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(120)
        erased = _wp.load_set("synthwp_lo").get("SPEED")
        tap(speed_chip)
        entry(speed_chip).setProperty("text", "3")
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(120)
        typed = _wp.load_set("synthwp_lo").get("SPEED")
        assert (erased, typed) == (None, 3.0), (erased, typed)
        # a chip left by a click elsewhere sends a changed text, and a cleared one returns the row to Global
        title_entry = next(i for i in walk(view_item) if "TextField" in i.metaObject().className()
                           and i.property("text") == "synthwp_lo")
        tap(speed_chip)
        entry(speed_chip).setProperty("text", "4")
        tap(title_entry)
        QTest.qWait(120)
        left_typed = _wp.load_set("synthwp_lo").get("SPEED")
        tap(speed_chip)
        entry(speed_chip).setProperty("text", "")
        tap(title_entry)
        QTest.qWait(120)
        view_item.forceActiveFocus()
        QTest.qWait(80)
        assert (left_typed, _wp.load_set("synthwp_lo").get("SPEED")) == (4.0, None), left_typed
        blanks = {}
        for key, seat, blank in (("VOLUME", lambda: editor.setVolumeValue(7), ""),
                                 ("FIT_ZOOM", lambda: editor.setFit("zoom", "1.5"), " "),
                                 ("FIT_PAN_X", lambda: editor.setFit("pan_x", "0.5"), "")):
            seat()
            QTest.qWait(120)
            item = chip(key)
            tap(item)
            view_item.forceActiveFocus()
            QTest.qWait(80)
            kept = _wp.load_set("synthwp_lo").get(key)
            tap(item)
            entry(item).setProperty("text", blank)
            QTest.keyClick(win, Qt.Key.Key_Enter)
            QTest.qWait(120)
            blanks[key] = (kept is not None, _wp.load_set("synthwp_lo").get(key))
        assert blanks == dict.fromkeys(("VOLUME", "FIT_ZOOM", "FIT_PAN_X"), (True, None)), blanks
        # rows that inherit, and a chip with no Global: an untouched Enter changes, marks and fails
        # nothing; only a cleared Enter marks the failure
        editor.open("synthwp_props")
        to_view("editor")
        conf_before = _wp.load_set("synthwp_props")
        settings_before = paths.settings_file().read_bytes()
        failures.clear()
        edits.clear()
        quiet = {}
        for key in ("VOLUME", "FIT_PAN_Y", "ENGINE_VOLUME"):
            item = chip(key)
            tap(item)
            shown = entry(item).property("text") == item.property("text")
            QTest.keyClick(win, Qt.Key.Key_Return)
            QTest.qWait(120)
            quiet[key] = (shown, editor.isMarked(key))
        untouched = (quiet, list(edits), list(failures), _wp.load_set("synthwp_props") == conf_before,
                     paths.settings_file().read_bytes() == settings_before)
        item = chip("ENGINE_VOLUME")
        tap(item)
        entry(item).setProperty("text", "")
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(120)
        cleared = (list(failures), paths.settings_file().read_bytes() == settings_before)
        assert (untouched, cleared) == \
            ((dict.fromkeys(("VOLUME", "FIT_PAN_Y", "ENGINE_VOLUME"), (True, False)), [], [], True, True),
             ([["ENGINE_VOLUME"]], True)), (untouched, cleared)
        # a cancelled entry's typed text is gone when the chip opens again
        editor.setFit("zoom", "1.2")
        QTest.qWait(120)
        zoom_chip = chip("FIT_ZOOM")
        tap(zoom_chip)
        entry(zoom_chip).setProperty("text", "1.8")
        QTest.keyClick(win, Qt.Key.Key_Escape)
        QTest.qWait(80)
        tap(zoom_chip)
        reopened = (entry(zoom_chip).property("text"), zoom_chip.property("text"))
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(120)
        assert (reopened, _wp.load_set("synthwp_props").get("FIT_ZOOM")) == (("1.20", "1.20"), 1.2), reopened
        print("OK chip entries - a chip opens on its label, an untouched Enter changes nothing, a cleared Enter "
              "returns the row to Global, and a reopened chip shows its label")

        # the property filter is an entry: Escape drops its focus and keeps the filter
        editor.open("synthwp_props")
        to_view("editor")
        filt = next(i for i in walk(view_item) if i.property("placeholderText") == "Filter properties")
        at = filt.mapToScene(QPointF(20, filt.height() / 2)).toPoint()
        QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
        QTest.qWait(60)
        QTest.keyClick(win, Qt.Key.Key_X)
        focused = filt.hasActiveFocus()
        first = esc()
        state = (focused, first, filt.property("text"), filt.hasActiveFocus())
        assert (state, esc()) == ((True, "editor", "x", False), "library"), state
        filt.setProperty("text", "")
        print("OK filter - Escape drops the filter's focus and keeps its text; the next Escape leaves")

        # the title entry follows the store again after Escape; a Return commit then a move away
        # writes the title once
        editor.open("synthwp_fit")
        to_view("editor")
        title_entry = next(i for i in walk(view_item) if "TextField" in i.metaObject().className()
                           and i.property("text") == "synthwp_fit")
        tap(title_entry)
        title_entry.setProperty("text", "typed")
        QTest.keyClick(win, Qt.Key.Key_Escape)
        QTest.qWait(80)
        escaped = title_entry.property("text")
        editor.setTitle("Elsewhere")
        QTest.qWait(80)
        followed = title_entry.property("text")
        writes = []

        def count_write():
            writes.append(_meta.get("synthwp_fit").get("title"))
        editor.metadataChanged.connect(count_write)
        tap(title_entry)
        title_entry.setProperty("text", "Once")
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(80)
        kept = title_entry.hasActiveFocus()
        view_item.forceActiveFocus()
        QTest.qWait(80)
        editor.metadataChanged.disconnect(count_write)
        assert (escaped, followed, kept, writes) == ("synthwp_fit", "Elsewhere", True, ["Once"]), \
            (escaped, followed, kept, writes)
        editor.setTitle("")
        # a wallpaper switch while the title entry holds focus saves no title
        editor.open("synthwp_lo")
        to_view("editor")
        title_entry = next(i for i in walk(view_item) if "TextField" in i.metaObject().className()
                           and i.property("text") == "synthwp_lo")
        before = (_meta.get("synthwp_lo").get("title"), _meta.get("synthwp_hi").get("title"))
        tap(title_entry)
        focused = title_entry.hasActiveFocus()
        to_view("library")
        editor.open("synthwp_hi")
        to_view("editor")
        shown = (title_entry.property("text"), title_entry.hasActiveFocus())
        view_item.forceActiveFocus()
        QTest.qWait(120)
        after = (_meta.get("synthwp_lo").get("title"), _meta.get("synthwp_hi").get("title"))
        assert (focused, shown, after) == (True, ("synthwp_hi", False), before), (focused, shown, before, after)
        print("OK title entry - Escape brings the store binding back, a Return commit writes once, "
              "and a wallpaper switch saves no title")

        # Settings in the same window: a combo entry closed by Escape, Return or a lost focus gives
        # focus back to the view, and no other view inherits it
        def ss_combo():
            to_view("settings")
            sview = win.findChild(QQuickItem, "settingsView")
            sview.setProperty("pageIndex", 1)
            QTest.qWait(200)
            combo = next(i for i in walk(sview) if i.property("objectName") == "settingsSsfactorChip")
            return combo, next(i for i in walk(combo) if i.metaObject().className().startswith("QQuickTextInput"))

        combo, combo_entry = ss_combo()
        combo.setProperty("editing", True)
        QTest.qWait(80)
        opened = (combo_entry.isVisible(), combo_entry.hasActiveFocus())
        cancel = (esc(), esc(), esc())
        combo, combo_entry = ss_combo()
        combo.setProperty("editing", True)
        QTest.qWait(80)
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(80)
        holder = win.activeFocusItem()
        closed = (combo.property("editing"), combo_entry.hasActiveFocus(),
                  holder.objectName() if holder is not None else None)
        entered = (esc(), esc())
        combo, combo_entry = ss_combo()
        combo.setProperty("editing", True)
        QTest.qWait(80)
        esc()
        to_view("editor")
        leak = esc()
        combo, combo_entry = ss_combo()
        combo.setProperty("editing", True)
        QTest.qWait(80)
        other.requestActivate()
        QTest.qWait(200)
        away = combo.property("editing")
        win.requestActivate()
        QTest.qWait(200)
        back = (combo_entry.hasActiveFocus(), esc())
        combo, combo_entry = ss_combo()
        combo.setProperty("editing", True)
        QTest.qWait(80)
        QMetaObject.invokeMethod(popup_root, "open")
        QTest.qWait(300)
        covered = (popup_root.property("visible"), combo.property("editing"), esc())
        QTest.qWait(300)
        uncovered = (popup_root.property("visible"), combo_entry.hasActiveFocus(), esc())
        assert (opened, cancel, closed, entered, leak, away, back, covered, uncovered) == \
            ((True, True), ("settings", "library", "library"), (False, False, "settingsView"), ("library", "library"),
             "library", False, (False, "library"), (True, False, "settings"), (False, False, "library")), \
            (opened, cancel, closed, entered, leak, away, back, covered, uncovered)
        other.close()
        print("OK Settings - a combo entry closed by Escape, Return, another window or the deck popup gives "
              "focus back; the next Escape leaves Settings and no other view inherits the focus")

        # a view switch closes the left view's open entry as Escape does and gives focus to the view
        # shown: nothing typed before or after the switch is saved, and Escape reaches the new view
        def focus_name():
            h = win.activeFocusItem()
            return h.objectName() if h is not None else None

        editor.open("synthwp_lo")
        editor.clearOverride("speed")
        to_view("editor")
        speed_chip = chip("SPEED")
        tap(speed_chip)
        entry(speed_chip).setProperty("text", "4")
        to_view("library")
        f1 = (focus_name(), entry(speed_chip).hasActiveFocus(), speed_chip.property("editing"))
        QTest.keyClick(win, Qt.Key.Key_5)
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(120)
        f1 += (_wp.load_set("synthwp_lo").get("SPEED"),)
        fps_box = next(i for i in walk(view_item) if i.property("ckey") == "ENGINE_FPS" and i.property("editable"))
        fps_stored = paths.settings_file().read_bytes()
        to_view("editor")
        tap(fps_box)
        entry(fps_box).setProperty("text", "45")
        to_view("library")
        boxed = (focus_name(), entry(fps_box).hasActiveFocus(), paths.settings_file().read_bytes() == fps_stored)
        to_view("settings")
        settings_view = win.findChild(QQuickItem, "settingsView")
        settings_view.setProperty("pageIndex", 1)
        QTest.qWait(200)
        combo = next(i for i in walk(settings_view) if i.property("objectName") == "settingsSsfactorChip")
        combo_entry = next(i for i in walk(combo) if i.metaObject().className().startswith("QQuickTextInput"))
        stored = paths.settings_file().read_bytes()
        combo.setProperty("editing", True)
        QTest.qWait(80)
        combo_entry.setProperty("text", "3")
        to_view("library")
        f2 = (focus_name(), combo_entry.hasActiveFocus(), combo.property("editing"))
        QTest.keyClick(win, Qt.Key.Key_2)
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(120)
        f2 += (paths.settings_file().read_bytes() == stored,)
        to_view("editor")
        zoom_chip = chip("FIT_ZOOM")
        tap(zoom_chip)
        to_view("settings")
        f3 = (focus_name(), esc())
        to_view("editor")
        title_entry = next(i for i in walk(view_item) if "TextField" in i.metaObject().className()
                           and i.property("text") == "synthwp_lo")
        tap(title_entry)
        title_entry.setProperty("text", "Switched")
        to_view("library")
        titled = (focus_name(), title_entry.hasActiveFocus(), title_entry.property("text"),
                  _meta.get("synthwp_lo").get("title"))
        editor.open("synthwp_props")
        to_view("editor")
        QTest.qWait(200)
        props_before = dict(_wp.load_set("synthwp_props")["props"])
        for shown, text in ((lambda t: t.startswith("#"), "#00ff00"), (lambda t: t in ("hi", "bye"), "zzz")):
            to_view("editor")
            typed_entry = next(i for i in walk(view_item) if i.metaObject().className().startswith("QQuickTextInput")
                               and shown(str(i.property("text"))))
            tap(typed_entry)
            typed_entry.setProperty("text", text)
            to_view("library")
        propped = (focus_name(), dict(_wp.load_set("synthwp_props")["props"]) == props_before)
        assert settings_bridge.commit("DETECT_MODE", "interval")
        to_view("settings")
        settings_view.setProperty("pageIndex", 2)
        QTest.qWait(300)
        field = next(i for i in walk(settings_view) if i.metaObject().className().startswith("SettingsField"))
        field_entry = next(i for i in walk(field) if i.metaObject().className().startswith("QQuickTextInput"))
        interval = settings.load().get("DETECT_INTERVAL_SEC")
        tap(field_entry)
        field_entry.setProperty("text", "77")
        to_view("library")
        fielded = (focus_name(), field_entry.hasActiveFocus(), settings.load().get("DETECT_INTERVAL_SEC") == interval)
        settings_view.setProperty("pageIndex", 1)
        # the header search, and the deck bar when it holds focus, keep it across a switch, so typing
        # goes on where it was
        search = win.findChild(QQuickItem, "headerSearch")
        to_view("editor")
        search.forceActiveFocus()
        QTest.keyClick(win, Qt.Key.Key_H)
        to_view("library")
        searched = (focus_name(), search.property("text"))
        esc()
        searched += (search.property("text"),)
        deck_bar = win.findChild(QQuickItem, "deckBar")
        to_view("editor")
        deck_bar.forceActiveFocus()
        to_view("library")
        decked = focus_name()
        to_view("editor")
        to_view("library")
        # focus held outside the views stays there: the deck popup keeps its Escape across a switch
        QMetaObject.invokeMethod(popup_root, "open")
        QTest.qWait(300)
        to_view("settings")
        kept = (popup_root.property("activeFocus"), esc(), popup_root.property("visible"))
        QTest.qWait(200)
        assert (f1, boxed, f2, f3, titled, propped, fielded, searched, decked, kept) == \
            (("libraryView", False, False, None), ("libraryView", False, True), ("libraryView", False, False, True),
             ("settingsView", "library"), ("libraryView", False, "synthwp_lo", None), ("libraryView", True),
             ("libraryView", False, True), ("headerSearch", "h", ""), "deckBar", (True, "settings", False)), \
            (f1, boxed, f2, f3, titled, propped, fielded, searched, decked, kept)
        print("OK view switch - an entry open when the view changes closes without saving, and the view shown "
              "takes focus and the next Escape")

        # the theme hex field, the Developer label and the Settings text field take Escape themselves:
        # the stored text comes back, nothing is saved and the view stays; the next Escape leaves.
        # A view switch while one is open closes it the same way
        from lwe_ui.storage import themes as _theme_store

        def focus_name():
            h = win.activeFocusItem()
            return h.objectName() if h is not None else None

        saves = []
        real_save = backend.save_setting

        def recorded_save(key, value, write=None):
            saves.append((key, value))
            return real_save(key, value, write)
        backend.save_setting = recorded_save

        def type_into(item, text):
            item.forceActiveFocus()
            item.selectAll()
            for ch in text:
                QTest.keyClick(win, ord(ch))
            QTest.qWait(60)

        def settings_page(n):
            to_view("settings")
            page_view = win.findChild(QQuickItem, "settingsView")
            page_view.setProperty("pageIndex", n)
            QTest.qWait(300)
            return page_view

        def hex_field():
            return next(i for i in walk(settings_page(3)) if str(i.property("objectName") or "").startswith("themeHex_")
                        and i.isVisible())

        def label_field():
            to_view("developer")
            return next(i for i in walk(win.contentItem()) if i.property("objectName") == "devSlotLabel"
                        and i.isVisible())

        def text_field():
            field_item = next(i for i in walk(settings_page(2)) if i.metaObject().className().startswith("SettingsField"))
            return next(i for i in walk(field_item) if i.metaObject().className().startswith("QQuickTextInput"))

        assert settings_bridge.commit("DETECT_MODE", "interval")
        stores = (lambda: _theme_store.load_config().get("overlays"),
                  lambda: [s.label for s in dev.slots.values()],
                  lambda: settings.load().get("DETECT_INTERVAL_SEC"))
        escaped, switched = [], []
        for (find_field, typed, view_name), store in zip(((hex_field, "#00ff00", "settings"),
                                                         (label_field, "zz", "developer"),
                                                         (text_field, "77", "settings")), stores):
            before = store()
            saves.clear()
            field_item = find_field()
            shown = field_item.property("text")
            type_into(field_item, typed)
            first = esc()
            escaped.append((first, field_item.property("text") == shown, field_item.hasActiveFocus(),
                            store() == before, list(saves), esc()))
            field_item = find_field()
            type_into(field_item, typed)
            to_view("library")
            switched.append((focus_name(), field_item.property("text") == shown, store() == before, list(saves)))
        backend.save_setting = real_save
        settings_page(1)
        to_view("library")
        assert (escaped, switched) == \
            ([("settings", True, False, True, [], "library"), ("developer", True, False, True, [], "library"),
              ("settings", True, False, True, [], "library")],
             [("libraryView", True, True, [])] * 3), (escaped, switched)
        print("OK own Escape - the theme hex field, the Developer label and the Settings field cancel on Escape "
              "and on a view switch, saving nothing")

        # the deck popup's own entries keep the same rules. Its 2 s status poll would walk it onto
        # the engine's wallpaper (none here) mid-test, so the poll is held still
        polls = [t for t in win.findChildren(QObject)
                 if t.metaObject().className() == "QQmlTimer" and t.property("interval") == 2000]
        assert len(polls) == 1, len(polls)
        polls[0].setProperty("running", False)
        to_view("library")
        _wp.update_set("synthwp_hi", {"FIT_ZOOM": None})
        _wp.update_set("synthwp_props", {"PROP_mylabel": None})
        deck_popup.syncCurrent("synthwp_hi")
        QMetaObject.invokeMethod(popup_root, "open")
        QTest.qWait(400)
        pc = popup_root.property("contentItem")

        def tap_any(item):
            flick = item.parentItem()
            while flick is not None and not flick.metaObject().className().startswith("QQuickFlickable"):
                flick = flick.parentItem()
            if flick is not None:
                top = item.mapToItem(flick.property("contentItem"), QPointF(0, 0)).y()
                flick.setProperty("contentY", max(0.0, top - 40.0))
                QTest.qWait(60)
            at = item.mapToScene(QPointF(min(15.0, item.width() / 2), item.height() / 2)).toPoint()
            QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
            QTest.qWait(60)

        deck_zoom = next(i for i in walk(pc) if i.property("ckey") == "FIT_ZOOM" and i.property("editing") is not None
                         and i.property("display") is None and i.isVisible())
        tap_any(deck_zoom)
        opened = (deck_zoom.property("editing"), entry(deck_zoom).property("text"))
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(120)
        untouched = _wp.load_set("synthwp_hi").get("FIT_ZOOM")
        tap_any(deck_zoom)
        entry(deck_zoom).setProperty("text", "1.8")
        QTest.keyClick(win, Qt.Key.Key_Escape)
        QTest.qWait(120)
        cancelled = (deck_zoom.property("editing"), popup_root.property("visible"),
                     _wp.load_set("synthwp_hi").get("FIT_ZOOM"))
        assert deck_popup.setFit("zoom", "1.5")
        QTest.qWait(120)
        tap_any(deck_zoom)
        entry(deck_zoom).setProperty("text", "")
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(120)
        blank = (_wp.load_set("synthwp_hi").get("FIT_ZOOM"), popup_root.property("visible"),
                 entry(deck_zoom).hasActiveFocus())
        deck_fps = next(i for i in walk(pc) if i.property("ckey") == "ENGINE_FPS" and i.property("editable") is True
                        and i.isVisible())
        sent = []

        def on_state():
            sent.append("state")

        def on_fail(keys):
            sent.append(list(keys))
        deck_popup.stateChanged.connect(on_state)
        deck_popup.commitFailed.connect(on_fail)
        tap_any(deck_fps)
        fps_opened = deck_fps.property("editing")
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(120)
        deck_popup.stateChanged.disconnect(on_state)
        deck_popup.commitFailed.disconnect(on_fail)
        fps_menu = next(o for o in deck_fps.findChildren(QObject) if o.inherits("QQuickMenu"))
        tap_any(deck_fps)
        fps_menu.setProperty("justClosed", True)
        at = deck_fps.mapToScene(QPointF(deck_fps.width() - 6, deck_fps.height() / 2)).toPoint()
        QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
        QTest.qWait(80)
        fps_menu.setProperty("justClosed", False)
        fps_closed = (deck_fps.property("editing"), entry(deck_fps).hasActiveFocus(), popup_root.property("visible"))
        QTest.keyClick(win, Qt.Key.Key_Escape)
        QTest.qWait(300)
        assert (opened, untouched, cancelled, blank, fps_opened, sent, fps_closed, popup_root.property("visible")) == \
            ((True, "1.00"), None, (False, True, None), (None, True, False), True, [], (False, False, True), False), \
            (opened, untouched, cancelled, blank, fps_opened, sent, fps_closed, popup_root.property("visible"))
        deck_popup.syncCurrent("synthwp_props")
        QMetaObject.invokeMethod(popup_root, "open")
        QTest.qWait(400)
        text_entry = next(i for i in walk(pc) if i.metaObject().className().startswith("QQuickTextInput")
                          and i.property("text") == "hi" and i.isVisible())
        tap_any(text_entry)
        had = text_entry.hasActiveFocus()
        pc.forceActiveFocus()
        QTest.qWait(120)
        left = (had, text_entry.hasActiveFocus(), _wp.load_set("synthwp_props")["props"].get("mylabel"))
        tap_any(text_entry)
        text_entry.setProperty("text", "zzz")
        QTest.keyClick(win, Qt.Key.Key_Escape)
        QTest.qWait(120)
        escaped = (text_entry.property("text"), popup_root.property("visible"),
                   _wp.load_set("synthwp_props")["props"].get("mylabel"))
        tap_any(text_entry)
        text_entry.setProperty("text", "bye")
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(120)
        saved = _wp.load_set("synthwp_props")["props"].get("mylabel")
        assert (left, escaped, saved) == ((True, False, None), ("hi", True, None), "bye"), (left, escaped, saved)
        QMetaObject.invokeMethod(popup_root, "close")
        QTest.qWait(200)
        print("OK deck popup - an untouched chip or text entry sends nothing, a blank chip entry returns its row "
              "to Global, Escape cancels and keeps the popup, and a closed entry leaves the next Escape to it")

        # a click inside an open entry, on its text or its margin, leaves the typed text alone
        def click_at(item, x):
            at = item.mapToScene(QPointF(x, item.height() / 2)).toPoint()
            QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
            QTest.qWait(80)

        to_view("library")
        editor.open("synthwp_lo")
        editor.clearOverride("speed")
        to_view("editor")
        clicked = []
        speed_read = (lambda: _wp.load_set("synthwp_lo").get("SPEED"))
        for item, typed, x, read in ((chip("SPEED"), "3", 12, speed_read), (chip("SPEED"), "4", 3, speed_read),
                                     (next(i for i in walk(view_item) if i.property("ckey") == "ENGINE_FPS" and i.property("editable")),
                                      "50", 20, lambda: None)):
            tap(item)
            entry(item).setProperty("text", typed)
            click_at(item, x)
            kept = (entry(item).property("text"), item.property("editing"))
            QTest.keyClick(win, Qt.Key.Key_Return)
            QTest.qWait(120)
            clicked.append((kept, read()))
        assert clicked == [(("3", True), 3.0), (("4", True), 4.0), (("50", True), None)], clicked
        to_view("library")
        for poll in win.findChildren(QObject):
            if poll.metaObject().className() == "QQmlTimer" and poll.property("interval") == 2000:
                poll.setProperty("running", False)
        _wp.update_set("synthwp_hi", {"FIT_ZOOM": None})
        deck_popup.syncCurrent("synthwp_hi")
        QMetaObject.invokeMethod(popup_root, "open")
        QTest.qWait(400)
        deck_content = popup_root.property("contentItem")
        deck_zoom = next(i for i in walk(deck_content) if i.property("ckey") == "FIT_ZOOM"
                         and i.property("editing") is not None and i.property("display") is None and i.isVisible())
        deck_fps = next(i for i in walk(deck_content) if i.property("ckey") == "ENGINE_FPS"
                        and i.property("editable") is True and i.isVisible())
        deck_clicked = []
        for item, typed, x, read in ((deck_zoom, "1.7", 3, lambda: _wp.load_set("synthwp_hi").get("FIT_ZOOM")),
                                     (deck_fps, "45", 15, lambda: settings.load().get("ENGINE_FPS"))):
            at = item.mapToScene(QPointF(min(15.0, item.width() / 2), item.height() / 2)).toPoint()
            QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
            QTest.qWait(60)
            entry(item).setProperty("text", typed)
            click_at(item, x)
            kept = (entry(item).property("text"), item.property("editing"))
            QTest.keyClick(win, Qt.Key.Key_Return)
            QTest.qWait(120)
            deck_clicked.append((kept, read()))
        QMetaObject.invokeMethod(popup_root, "close")
        QTest.qWait(200)
        assert deck_clicked == [(("1.7", True), 1.7), (("45", True), 45)], deck_clicked
        print("OK click inside - a click on an open entry's text or margin keeps what was typed, in the editor "
              "and the deck popup")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
