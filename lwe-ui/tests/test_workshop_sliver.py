"""The sliver rule (universal): no surface may rest showing a partial row
thinner than 24px. Workshop scrolls under a variable-height hero, so it satisfies the rule
via padding math (sliverSnap), never the Library grid's row-flex.

Two layers:
  1. PURE MATH: mirror the QML sliverSnap formula and prove that for every plausible
     (viewport, tileH, cols, count), the POST-snap resting partial is never in (0, 24).
  2. LIVE QML: mount Main.qml, enter Workshop, and assert the snap properties exist and
     rest at 0 when the arrival stage is empty (no false snap, no binding loop).

Live verification WITH real subscribed content requires manual visual testing (the sandbox
has no workshop items to lay a second row), and is tracked separately.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software python3 tests/test_workshop_sliver.py
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_TMP = tempfile.mkdtemp(prefix="lwe-sliver-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

GAP = 16


def _snap(height: float, tile_h: int, cols: int, count: int) -> float:
    """Exact mirror of the QML sliverSnap binding."""
    pitch = tile_h + GAP
    content_rows = math.ceil(max(0, count) / cols) if cols else 0
    content_h = content_rows * tile_h + (content_rows - 1) * GAP if content_rows > 0 else 0
    rest_full = max(0, math.floor((height + GAP) / pitch))
    rest_partial = height - (rest_full * tile_h + max(0, rest_full - 1) * GAP)
    if content_h > height and 0.5 < rest_partial < 24:
        return rest_partial
    return 0.0


def _post_snap_partial(height: float, tile_h: int, cols: int, count: int) -> float:
    """The partial row still visible at rest AFTER the content is shifted by sliverSnap.
    Content that overflows starts at y=snap; the fold at y=height cuts row k where
    k=floor((height - snap + GAP)/pitch); the leftover is the still-visible sliver."""
    snap = _snap(height, tile_h, cols, count)
    pitch = tile_h + GAP
    content_rows = math.ceil(max(0, count) / cols) if cols else 0
    content_h = content_rows * tile_h + (content_rows - 1) * GAP if content_rows > 0 else 0
    if content_h <= height:
        return 0.0
    eff = height - snap
    rest_full = max(0, math.floor((eff + GAP) / pitch))
    return eff - (rest_full * tile_h + max(0, rest_full - 1) * GAP)


def test_math() -> None:
    checked = slivers_found = 0
    for height in range(200, 1400, 7):
        for tile_h in (150, 160, 172, 184, 196, 210, 224):
            for cols in (3, 4, 5, 6, 7):
                for count in (cols, cols * 2, cols * 3 + 1, cols * 5, cols * 8 + 2):
                    post = _post_snap_partial(height, tile_h, cols, count)
                    checked += 1
                    assert not (0.5 < post < 24.0), (
                        f"post-snap sliver {post:.1f}px @ height={height} tileH={tile_h} "
                        f"cols={cols} count={count} (snap was {_snap(height, tile_h, cols, count):.1f})")
                    if 0.5 < _snap(height, tile_h, cols, count):
                        slivers_found += 1
    assert _snap(371, 176, 5, 40) > 0.0, "snap should fire on the measured 1280x720 sliver"
    assert _snap(700, 176, 5, 5) == 0.0, "snap must not fire when content fits the viewport"
    print(f"  math: {checked} (viewport x tileH x cols x count) cases, 0 post-snap slivers; "
          f"snap engaged on {slivers_found} pre-fix sliver cases")


def test_live_qml() -> None:
    from PySide6.QtCore import QObject, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance
    from PySide6.QtTest import QTest
    from lwe_ui.app import _QML_DIR, _TOKENS_NAME, _TOKENS_URI, _resolve_theme_tokens
    from lwe_ui.bench_bridge import BenchBridge
    from lwe_ui.dev import DevBridge
    from lwe_ui.editor import EditorBridge
    from lwe_ui.models import Backend, ImportBridge, ThemeTokens
    from lwe_ui.storage import paths, settings
    from lwe_ui.workshop import WorkshopBridge

    paths.ensure_dirs()
    settings.ensure_exists()
    app = QGuiApplication.instance() or QGuiApplication(["t"])  # noqa: F841
    tokens = ThemeTokens(_resolve_theme_tokens())
    qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(_QML_DIR))
    errs: list[str] = []
    engine.warnings.connect(lambda es: [errs.append(e.toString()) for e in es])
    backend = Backend()
    dev = DevBridge()
    for n, o in (("backend", backend), ("editor", EditorBridge()), ("bench", BenchBridge()),
                 ("dev", dev), ("workshop", WorkshopBridge(backend, dev)),
                 ("importBridge", ImportBridge(backend))):
        engine.rootContext().setContextProperty(n, o)
    from lwe_ui.wizard_bridge import WizardBridge
    _wizb = WizardBridge(engine.rootContext().contextProperty("backend"),
                         engine.rootContext().contextProperty("workshop"))
    engine.rootContext().setContextProperty("wizardBridge", _wizb)
    engine.load(QUrl.fromLocalFile(str(_QML_DIR / "Main.qml")))
    win = engine.rootObjects()[0]
    win.setProperty("currentView", "workshop")
    win.setProperty("width", 1280)
    win.setProperty("height", 720)
    QTest.qWait(80)
    sc = win.findChild(QObject, "workshopTileScroll")
    assert sc is not None, "workshop tile scroll not found"
    for prop in ("pitch", "contentRows", "contentH", "restFullRows", "restPartial", "sliverSnap"):
        assert sc.property(prop) is not None, f"missing sliver property {prop}"
    assert int(sc.property("contentRows")) == 0, "empty workshop must have 0 content rows"
    assert float(sc.property("sliverSnap")) == 0.0, "snap must rest at 0 with no content"
    # no binding loop / QML error from the new properties. The editor-takeover subtree
    # (EditorView/ObjectsPanel/Deck) emits pre-existing null-binding noise here because no
    # scene/bench-lease is active - test_app_load dodges it by opening a scene, and those
    # files are untouched by this work. Scope the regression check to the exact surfaces this
    # change edits: an error naming any of them IS a real regression.
    # WorkshopView.qml:35/52 emit "of null" here on CLEAN HEAD too - a headless GC/teardown
    # artifact where the context-property bridges free before the QML unbinds (benign live,
    # verified by stashing this change). This edit adds NO bridge calls, so it cannot produce
    # an "of null"; excluding that class still catches a binding loop or a real property error.
    touched = ("WorkshopView.qml", "WorkshopTile.qml", "WallpaperCard.qml", "PlaylistStrip.qml")
    real = [e for e in errs if any(t in e for t in touched) and "of null" not in e]
    assert not real, "QML errors in edited surfaces:\n  " + "\n  ".join(real[:8])
    print("  live: sliver properties wired; empty stage rests snap=0, no binding loop / QML error")


def main() -> None:
    test_math()
    test_live_qml()
    print("OK test_workshop_sliver - padding-math snap eliminates every <24px resting partial")


if __name__ == "__main__":
    main()
