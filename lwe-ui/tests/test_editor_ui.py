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
        for name, val in (("synthwp_hi", 0.9), ("synthwp_lo", 0.1)):
            wd = os.path.join(str(paths.default_wallpapers_dir()), name)
            os.makedirs(wd, exist_ok=True)
            json.dump({"type": "scene", "file": "scene.json", "title": name, "general": {"properties": {
                "myslider": {"type": "slider", "value": val, "min": 0, "max": 1, "step": 0.01, "text": "Slide"},
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
        # at 0). Threshold calibrated to the canvas-compact 100px slider (T2): a 0.8 value
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
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
