"""v1.4 T3 acceptance: the light pass is a TOKEN pass - zero geometry changes.

  1. GEOMETRY INVARIANCE (the spec's pixel-diff intent, stronger form): load the whole
     shell (Main.qml) under OLED Electric, walk the visual tree capturing every item's
     (class, x, y, w, h), switch the live theme to Light through the bridge, walk again -
     the two structures must be identical. Colors flip, geometry may not.
  2. LIVE RESTYLE at the QML layer: the flip happens with no reload - a color read from
     the loaded tree changes between walks.
  3. Store-side dual-value laws: toggle off-state (law 4), warning wash + light amber
     (law 6), the neutral ladder direction (law 1), scrim byte-identity across all 17
     themes (never-themed hard law).

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software python3 tests/test_light_mode.py
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

_TMP = tempfile.mkdtemp(prefix="lwe-light-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")


def main() -> None:
    from lwe_ui.storage import themes

    dark_tok = themes.resolve(themes.base_roles("oledElectric"))
    light_tok = themes.resolve(themes.base_roles("light"))

    assert dark_tok["toggleKnobOn"] == "#FFFFFF" and light_tok["toggleKnobOn"] == "#FFFFFF"
    assert light_tok["toggleOffKnob"] == "#FFFFFF", "light off-knob is white"
    assert dark_tok["toggleOffKnob"] != "#FFFFFF", "dark off-knob stays muted"
    lt = themes.luminance(light_tok["toggleOffTrack"])
    assert 0.70 <= lt <= 0.95, f"light off-track must be the #E2E4E6 class, got {lt}"

    assert dark_tok["warning"] == "#EF9F27" and light_tok["warning"] == "#B26B00"
    assert dark_tok["warningWash"].upper().endswith("EF9F27")
    assert light_tok["warningWash"].upper().endswith("B26B00")

    assert dark_tok["ladderStrong"].upper().endswith("FFFFFF")
    assert light_tok["ladderStrong"].upper().endswith("000000")

    # badge text (Design amendment): #D4D4D4 tinted 18% toward each theme's accent -
    # per-theme identity with near-constant luminance (contrast on the plate holds)
    for key in ("oledElectric", "cozyPink", "gridGraphite"):
        tok = themes.resolve(themes.base_roles(key))
        expect = themes._mix("#D4D4D4", themes.base_roles(key)["accent"], 0.18)
        assert tok["badgeText"] == expect, (key, tok["badgeText"], expect)
        assert abs(themes.luminance(tok["badgeText"]) - themes.luminance("#D4D4D4")) < 0.12, key

    # never-themed: scrims byte-identical across every shippable theme
    scrims = {(themes.resolve(themes.base_roles(t["key"]))["scrimPlate"],
               themes.resolve(themes.base_roles(t["key"]))["scrimHover"])
              for t in themes.theme_list()}
    assert scrims == {("#8C000000", "#59000000")}, scrims

    from PySide6.QtCore import QUrl, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance
    from lwe_ui import bench_courier
    from lwe_ui.models import Backend, ThemeBridge, ThemeTokens
    from lwe_ui.editor import EditorBridge
    from lwe_ui.bench_bridge import BenchBridge
    from lwe_ui.dev import DevBridge
    from lwe_ui.storage import paths, settings
    from lwe_ui.app import _resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME

    paths.ensure_dirs()
    settings.ensure_exists()
    bench_courier.available = lambda: True
    bench_courier.wait_clear = lambda *a, **k: True
    bench_courier.standdown = lambda *a, **k: True
    bench_courier.resume = lambda *a, **k: True

    app = QGuiApplication.instance() or QGuiApplication(["t"])
    tokens = ThemeTokens(_resolve_theme_tokens())
    tb = ThemeBridge(tokens)
    qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(_QML_DIR))
    backend = Backend()
    editor = EditorBridge()
    bench = BenchBridge()
    dev = DevBridge()
    from lwe_ui.workshop import WorkshopBridge
    from lwe_ui.models import ImportBridge
    workshop = WorkshopBridge(backend, dev)
    import_bridge = ImportBridge(backend)
    for n, o in (("backend", backend), ("editor", editor), ("bench", bench),
                 ("dev", dev), ("themeBridge", tb), ("workshop", workshop),
                 ("importBridge", import_bridge)):
        engine.rootContext().setContextProperty(n, o)
    from lwe_ui.wizard_bridge import WizardBridge
    _wizb = WizardBridge(engine.rootContext().contextProperty("backend"),
                         engine.rootContext().contextProperty("workshop"))
    engine.rootContext().setContextProperty("wizardBridge", _wizb)
    engine.load(QUrl.fromLocalFile(str(_QML_DIR / "Main.qml")))
    assert len(engine.rootObjects()) == 1
    root = engine.rootObjects()[0]

    def settle():
        # outlast the 120ms switch-knob tween and any deferred Component.onCompleted
        # seeding: a knob caught mid-flight reads as a phantom geometry diff
        from PySide6.QtTest import QTest
        for _ in range(4):
            app.processEvents()
        QTest.qWait(300)
        app.processEvents()

    def walk():
        # PySide types unknown QML classes as plain QObject (no childItems method), so
        # the visual walk reads geometry through PROPERTIES over the deterministic
        # findChildren order: every visual item exposes x/y/width/height; non-items
        # return None and drop out. Blinking cursors are excluded (wall-time movers).
        from PySide6.QtCore import QObject
        out = []
        for c in root.findChildren(QObject):
            cls = c.metaObject().className()
            if "Cursor" in cls:
                continue
            # effect plumbing is not layout: the light-theme glow law (v2.3.5) legitimately
            # runs a ~40% blur radius on light, which resizes the Glow's INTERNAL
            # ShaderEffect texture pad while the visible tile geometry stays identical
            if "ShaderEffect" in cls:
                continue
            w = c.property("width")
            if w is None:
                continue
            out.append((cls, round(float(c.property("x") or 0), 1),
                        round(float(c.property("y") or 0), 1),
                        round(float(w), 1),
                        round(float(c.property("height") or 0), 1)))
        return out

    settle()
    geo_dark = walk()
    color_dark = str(root.property("color").name()) if root.property("color") else ""

    tb.setActive("light")
    settle()
    geo_light = walk()
    color_light = str(root.property("color").name()) if root.property("color") else ""

    # live restyle happened (no reload): the window background flipped
    assert color_dark != color_light, (color_dark, color_light)
    assert color_light.upper() == "#FDFDFC", color_light

    assert len(geo_dark) == len(geo_light), \
        f"item count changed with the theme: {len(geo_dark)} -> {len(geo_light)}"
    diffs = [(a, b) for a, b in zip(geo_dark, geo_light) if a != b]
    assert not diffs, f"{len(diffs)} geometry diffs, first: {diffs[0]}"

    # acceptance 7: on-imagery furniture byte-identical across themes. Render one card
    # (badge "scene" visible) under a dark and a light theme and byte-compare the badge
    # corner crop - the plate and text are never-themed literals.
    from PySide6.QtQuick import QQuickView, QQuickWindow
    tb.setActive("oledElectric")
    settle()
    card_view = QQuickView()
    card_view.engine().addImportPath(str(_QML_DIR))
    card_view.setSource(QUrl.fromLocalFile(str(_QML_DIR / "WallpaperCard.qml")))
    assert card_view.status() == QQuickView.Status.Ready,         [e.toString() for e in card_view.errors()]
    card = card_view.rootObject()
    card.setProperty("wpType", "scene")
    card.setProperty("width", 240)
    card.setProperty("height", 200)
    card_view.resize(240, 200)
    card_view.show()
    settle()

    def plate_strip():
        img = QQuickWindow.grabWindow(card_view)
        if img.isNull() or img.width() < 100:
            return None
        # a thin strip of the pill's leading edge: plate-only pixels (the 7px text
        # padding guarantees no glyph lands there). The TEXT tints per theme by the
        # Design amendment, so only the PLATE is byte-compared now.
        return img.copy(9, img.height() - 38, 4, 26)

    def is_platelike(img):
        for yy in range(img.height()):
            c = img.pixelColor(1, yy)
            if 0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue() < 140:
                return True
        return False

    strip_dark = plate_strip()
    tb.setActive("cozyPink")
    settle()
    strip_light = plate_strip()
    if strip_dark is not None and strip_light is not None:
        assert is_platelike(strip_dark), "fixture regression: no plate pixels in the strip"
        assert strip_dark == strip_light, \
            "the badge PLATE must render byte-identical across themes (never-themed law)"
    else:
        print("SKIP plate byte-compare (no frame grabbed on this platform)")
    card_view.close()

    tb.setActive("oledElectric")
    QTimer.singleShot(50, app.quit)
    app.exec()

    print(f"OK test_light_mode - {len(geo_dark)} tree items byte-identical across the "
          "dark/light flip; live restyle proven; toggle/stripe/ladder/scrim laws hold")


if __name__ == "__main__":
    main()
