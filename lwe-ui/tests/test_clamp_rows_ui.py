"""The two resolution limit rows, on the Engine settings page and in the editor, through the real
Main.qml offscreen: a slider from Off to 2.00 with a snap at 0 and a mild detent at 1.00, and a value
chip that reads and takes the number. The editor rows keep Global."""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import json
import os
import shutil
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

RENDER_CAPTION = ("Off is no limit. 1.00 renders at your screen's resolution; 2.00 renders above it. "
                  "Below 1.00 trades away quality fast.")
EFFECT_CAPTION = ("Off is no limit. 1.00 renders scene effect layers at your screen's resolution; 2.00 renders "
                  "above it. Below 1.00 trades away quality fast.")


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-clampui-")
    orig = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
    try:
        os.environ["HOME"] = home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
        os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
        os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

        from PySide6.QtCore import QUrl, QObject, QPoint, QPointF, Qt, QMetaObject, Q_ARG
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQuick import QQuickItem
        from PySide6.QtTest import QTest
        from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance
        from lwe_ui.models import Backend, ThemeTokens, ImportBridge, ThemeBridge
        from lwe_ui.editor import EditorBridge
        from lwe_ui.storage import paths, settings, wp as _wp
        from lwe_ui.app import _resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME

        paths.ensure_dirs()
        wd = os.path.join(str(paths.default_wallpapers_dir()), "synthwp_clamp")
        os.makedirs(wd, exist_ok=True)
        json.dump({"type": "scene", "file": "scene.json", "title": "synthwp_clamp", "general": {"properties": {}}},
                  open(os.path.join(wd, "project.json"), "w"))
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
        from lwe_ui.workshop import WorkshopBridge
        from lwe_ui.wizard_bridge import WizardBridge
        from lwe_ui.deck_popup import DeckPopupBridge
        from lwe_ui.settings_bridge import SettingsBridge
        dev, bench = DevBridge(), BenchBridge()
        workshop = WorkshopBridge(backend, dev)
        import_bridge = ImportBridge(backend)
        held = {"wizard": WizardBridge(backend, workshop), "deck": DeckPopupBridge(backend),
                "settings": SettingsBridge(backend, import_bridge), "theme": ThemeBridge(tokens)}
        ctx = engine.rootContext()
        for name, obj in (("backend", backend), ("editor", editor), ("dev", dev), ("bench", bench),
                          ("workshop", workshop), ("importBridge", import_bridge),
                          ("wizardBridge", held["wizard"]), ("deckPopup", held["deck"]),
                          ("settingsBridge", held["settings"]), ("themeBridge", held["theme"])):
            ctx.setContextProperty(name, obj)
        sb = held["settings"]
        # the clamp keys are service keys: the engine file writer's output probe is pinned, and no
        # subprocess may run
        import subprocess
        from unittest import mock
        from lwe_ui.engine import daemon_unit
        ran = []

        def run(args, *a, **k):
            ran.append(args)
            return subprocess.CompletedProcess(args, 1, "", "")
        pins = (mock.patch.object(daemon_unit, "enumerate_outputs", lambda: ["DP-1"]),
                mock.patch.object(daemon_unit.subprocess, "run", run))
        for pin in pins:
            pin.start()
        engine.load(QUrl.fromLocalFile(str(_QML_DIR / "Main.qml")))
        assert engine.rootObjects(), "Main.qml failed to load"
        win = engine.rootObjects()[0]
        win.setProperty("width", 1400)
        win.setProperty("height", 900)
        for poll in win.findChildren(QObject):
            if poll.metaObject().className() == "QQmlTimer" and poll.property("interval") == 2000:
                poll.setProperty("running", False)
        win.requestActivate()
        QTest.qWait(200)

        def walk(item):
            yield item
            for child in item.childItems():
                yield from walk(child)

        def named(root, name):
            return next(i for i in walk(root) if i.objectName() == name)

        def to_view(name):
            win.setProperty("currentView", name)
            QTest.qWait(200)

        def reveal(item):
            flick = item.parentItem()
            while not flick.metaObject().className().startswith("QQuickFlickable"):
                flick = flick.parentItem()
            top = item.mapToItem(flick.property("contentItem"), QPointF(0, 0)).y()
            flick.setProperty("contentY", max(0.0, top - 200.0))
            QTest.qWait(150)

        def knob_x(slider, value):
            lp, aw = slider.property("leftPadding"), slider.property("availableWidth")
            return lp + 5.0 + (max(0.0, min(2.0, value)) / 2.0) * (aw - 10.0)

        def drag(slider, to_value=None):
            # a press on the knob, eight moves and a release; no target leaves the knob where it stood
            y = slider.height() / 2
            start = slider.mapToScene(QPointF(knob_x(slider, slider.property("value")), y)).toPoint()
            end = start if to_value is None else slider.mapToScene(QPointF(knob_x(slider, to_value), y)).toPoint()
            QTest.mousePress(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
            QTest.qWait(30)
            for k in range(1, 9):
                QTest.mouseMove(win, QPoint(start.x() + (end.x() - start.x()) * k // 8, start.y()))
                QTest.qWait(15)
            QTest.mouseRelease(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
            QTest.qWait(150)

        def enter(chip_item, text):
            QMetaObject.invokeMethod(chip_item, "entered", Qt.ConnectionType.DirectConnection, Q_ARG("QString", text))
            QTest.qWait(100)

        # the Engine page: labels, whole captions, drag, snap, detent, typing, refusals, a value above 2
        to_view("settings")
        page = win.findChild(QQuickItem, "settingsView")
        page.setProperty("pageIndex", 1)
        QTest.qWait(400)
        rows = {r.property("label"): r for r in walk(page) if r.metaObject().className().startswith("SettingsRow")}
        written = []
        for label, caption in (("Render resolution limit", RENDER_CAPTION), ("Effect resolution limit", EFFECT_CAPTION)):
            row = rows[label]
            shown = [c for c in walk(row) if c.property("text") == caption]
            written.append((row.property("caption") == caption, len(shown) == 1 and not shown[0].property("truncated")))
        assert written == [(True, True), (True, True)], written
        assert "Resolution clamp" not in rows and "Effect clamp" not in rows, sorted(rows)
        reasons = []
        sb.commitFailed.connect(lambda keys, reason: reasons.append((list(keys), reason)))
        slider, chip_item = named(page, "settingsSsfactorSlider"), named(page, "settingsSsfactorChip")
        effect_slider = named(page, "settingsClampCompositesSlider")
        reveal(slider)
        assert sb.commit("SSFACTOR", 0.5) and sb.commit("CLAMPCOMPOSITES", 0.5)
        QTest.qWait(150)
        dragged = []
        for target in (1.5, 1.02, 0.02):
            drag(slider, target)
            dragged.append((settings.load()["SSFACTOR"], chip_item.property("displayText")))
        assert abs(dragged[0][0] - 1.5) < 0.03 and dragged[0][1] == "%.2f" % dragged[0][0], dragged
        assert dragged[1:] == [(1.0, "1.00"), (0.0, "Off")], dragged
        drag(slider, 1.0)
        before = paths.settings_file().read_bytes()
        drag(slider)
        untouched = paths.settings_file().read_bytes() == before
        typed = []
        for text in ("1.25", "off", "2"):
            enter(chip_item, text)
            typed.append((settings.load()["SSFACTOR"], round(slider.property("value"), 6)))
        reasons.clear()
        for text in ("3", "-0.5", "wide"):
            enter(chip_item, text)
        refused = (settings.load()["SSFACTOR"], [r for _, r in reasons])
        assert sb.commit("SSFACTOR", 3.5)
        QTest.qWait(150)
        pinned = (round(slider.property("value"), 6), chip_item.property("displayText"))
        drag(slider)
        pinned += (settings.load()["SSFACTOR"], settings.load()["CLAMPCOMPOSITES"])
        drag(effect_slider, 1.5)
        effect = (settings.load()["SSFACTOR"], abs(settings.load()["CLAMPCOMPOSITES"] - 1.5) < 0.03)
        assert (untouched, typed, refused, pinned, effect) == \
            (True, [(1.25, 1.25), (0.0, 0.0), (2.0, 2.0)],
             (2.0, ["That value is outside the allowed range.", "That value is outside the allowed range.",
                    "That is not a number."]),
             (2.0, "3.50", 3.5, 0.5), (3.5, True)), (untouched, typed, refused, pinned, effect)
        print("OK Engine page - the resolution limits drag, snap to Off, hold 1.00, take 0 to 2 or off, refuse "
              "the rest, and pin a stored 3.5 at the slider's end while the chip reads 3.50")

        # the editor: Global, drag, snap, detent, typing, a refusal, the entry rules, a value above 2
        assert sb.commit("SSFACTOR", 1) and sb.commit("CLAMPCOMPOSITES", 1)
        editor.open("synthwp_clamp")
        to_view("editor")
        QTest.qWait(300)
        view = win.findChild(QQuickItem, "editorView")
        failures = []
        editor.commitFailed.connect(lambda keys: failures.append(list(keys)))
        eslider, echip = named(view, "editorSsfactorSlider"), named(view, "editorSsfactorChip")
        effect_chip = named(view, "editorClampCompositesChip")
        reveal(eslider)
        labels = [r.property("label") for r in walk(view) if r.property("label") in
                  ("Render resolution limit", "Effect resolution limit", "Resolution clamp", "Effect clamp")]
        inherits = (echip.property("text"), round(eslider.property("value"), 6), effect_chip.property("text"))
        own = lambda: _wp.load_set("synthwp_clamp").get("SSFACTOR")
        assert editor.setClampValue("SSFACTOR", "0.5")
        QTest.qWait(150)
        edragged = []
        for target in (1.5, 1.02, 0.02):
            drag(eslider, target)
            edragged.append((own(), echip.property("text")))
        assert abs(edragged[0][0] - 1.5) < 0.03 and edragged[0][1] == "%.2f" % edragged[0][0], edragged
        assert edragged[1:] == [(1.0, "1.00"), (0.0, "Off")], edragged
        conf = paths.wp_file("synthwp_clamp")
        before = conf.read_text(encoding="utf-8")
        drag(eslider)
        euntouched = conf.read_text(encoding="utf-8") == before
        etyped = []
        for text in ("1.25", "OFF"):
            enter(echip, text)
            etyped.append((own(), round(eslider.property("value"), 6)))
        failures.clear()
        enter(echip, "3")
        erefused = (own(), list(failures))

        def tap(item):
            at = item.mapToScene(QPointF(min(15.0, item.width() / 2), item.height() / 2)).toPoint()
            QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
            QTest.qWait(80)

        def chip_entry(item):
            return next(i for i in walk(item) if i.metaObject().className().startswith("QQuickTextInput"))

        before = conf.read_text(encoding="utf-8")
        tap(echip)
        seeded = chip_entry(echip).property("text")
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(100)
        tap(echip)
        chip_entry(echip).setProperty("text", "1.75")
        QTest.keyClick(win, Qt.Key.Key_Escape)
        QTest.qWait(100)
        rules = (seeded, conf.read_text(encoding="utf-8") == before, win.property("currentView"))
        tap(echip)
        chip_entry(echip).setProperty("text", "")
        QTest.keyClick(win, Qt.Key.Key_Return)
        QTest.qWait(150)
        back = (own(), echip.property("text"), round(eslider.property("value"), 6))
        assert editor.setClampValue("SSFACTOR", "3.5")
        QTest.qWait(150)
        epinned = (round(eslider.property("value"), 6), echip.property("text"))
        drag(eslider)
        epinned += (own(), _wp.load_set("synthwp_clamp").get("CLAMPCOMPOSITES"))
        assert (labels, inherits, euntouched, etyped, erefused, rules, back, epinned) == \
            (["Render resolution limit", "Effect resolution limit"], ("Global", 1.0, "Global"), True,
             [(1.25, 1.25), (0.0, 0.0)], (0.0, [["SSFACTOR"]]), ("Off", True, "editor"), (None, "Global", 1.0),
             (2.0, "3.50", 3.5, None)), (labels, inherits, euntouched, etyped, erefused, rules, back, epinned)
        print("OK editor - the resolution limits read Global from the settings, drag, snap to Off, hold 1.00, take "
              "0 to 2 or off, refuse 3, keep the chip entry rules, return to Global when cleared, and pin 3.5")
        for pin in pins:
            pin.stop()
        assert ran == [], f"the resolution limit rows ran a subprocess: {ran}"
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
