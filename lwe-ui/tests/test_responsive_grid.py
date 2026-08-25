"""Responsive law v1.6 (B15): the library grid auto-fits columns, aspect-locks the
thumb to 16:10, and optically fits rows; container max-widths hold.

  * tiles clamp to [216, 320] at every width - never distorted, never under-sized
  * a wider window adds COLUMNS, not fatter tiles (supersedes the pinned-5 rule)
  * thumb height tracks tile width at 16:10 (crop-filled, never stretched)
  * optical row fit: the flexed thumb height stays within +/-10% of the 16:10 base

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software python3 tests/test_responsive_grid.py
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

_TMP = tempfile.mkdtemp(prefix="lwe-resp-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")


def main() -> None:
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
    assert engine.rootObjects(), "Main.qml failed to load"
    win = engine.rootObjects()[0]
    grid = win.findChild(QObject, "libraryGrid")
    assert grid is not None

    assert int(win.property("minimumWidth")) == 1080
    assert int(win.property("minimumHeight")) == 640

    prev_cols = 0
    for w, h in ((1080, 640), (1280, 720), (1600, 900), (1920, 1080), (3840, 2160)):
        win.setProperty("width", w)
        win.setProperty("height", h)
        QTest.qWait(40)
        gw = float(grid.property("width"))
        h_grid = float(grid.property("height"))
        cw = float(grid.property("cellWidth"))
        tile = float(grid.property("tileW"))
        cols = int(grid.property("cols"))
        base_thumb = float(grid.property("baseThumbH"))
        thumb = float(grid.property("thumbH"))
        rendered = int(gw / cw) if cw else 0

        assert 216 <= tile <= 320, f"@{w}px tile {tile} out of [216,320]"
        assert abs(base_thumb - tile * 10 / 16) < 1.0, f"@{w}px thumb not 16:10: {base_thumb} vs {tile*10/16}"
        assert abs(thumb - base_thumb) <= base_thumb * 0.10 + 0.5, \
            f"@{w}px optical flex exceeds +/-10%: base {base_thumb} -> {thumb}"
        rows_vis = int(grid.property("rowsVisible"))
        cell_h = int(grid.property("cellHeight"))
        assert rows_vis * cell_h <= h_grid + 1, \
            f"@{w}px {rows_vis} rows x {cell_h} = {rows_vis*cell_h} clips the {h_grid} viewport"
        assert rendered == cols, f"@{w}px rendered {rendered} != computed {cols} columns"
        assert cols >= prev_cols, f"@{w}px columns went DOWN ({cols} < {prev_cols})"
        if w == 1280:
            assert cols == 5, f"1280 window must be 5 columns (v1.6-a1), got {cols}"
            assert 220 <= tile <= 228, f"1280 flagship tile ~224px, got {tile}"
        prev_cols = cols
        print(f"  @{w}x{h}: {cols} cols, tile {tile:.0f}px, thumb {thumb:.0f}px (16:10 base {base_thumb:.0f})")

    win.setProperty("width", 1280); win.setProperty("height", 697)
    for _ in range(6):
        QTest.qWait(40)
    rf = int(grid.property("rowsFit")); rv = int(grid.property("rowsVisible"))
    can_shrink = bool(grid.property("canShrink"))
    gh = float(grid.property("height")); ch = int(grid.property("cellHeight"))
    assert rv == rf + 1 and can_shrink, \
        f"flagship must shrink to fit one more row: rowsFit={rf} rowsVisible={rv} shrink={can_shrink}"
    assert rv * ch <= gh + 1, f"flagship {rv} rows must not clip the {gh} viewport"
    print(f"  bidirectional shrink @1280x697: {rf} nominal -> {rv} rows flush (no clip)")

    win.setProperty("width", 1080); QTest.qWait(20)
    cols_min = int(grid.property("cols"))
    win.setProperty("width", 3840); QTest.qWait(20)
    cols_max = int(grid.property("cols"))
    assert cols_max > cols_min + 3, f"ultrawide must add many columns: {cols_min} -> {cols_max}"

    win.setProperty("currentView", "workshop")
    QTest.qWait(60)
    ws = win.findChild(QObject, "workshopTileScroll")
    assert ws is not None, "workshop tile grid not found"
    for w, h in ((1080, 640), (1600, 900), (3840, 2160)):
        win.setProperty("width", w); win.setProperty("height", h); QTest.qWait(40)
        win.setProperty("currentView", "library"); QTest.qWait(20)
        lib_tile = int(grid.property("tileW")); lib_thumb = round(float(grid.property("baseThumbH")))
        win.setProperty("currentView", "workshop"); QTest.qWait(20)
        ws_tile = int(ws.property("tileW")); ws_thumb = int(ws.property("thumbH"))
        assert ws_tile == lib_tile, f"@{w}px workshop tile {ws_tile} != library {lib_tile}"
        assert abs(ws_thumb - lib_thumb) <= 1, f"@{w}px workshop thumb {ws_thumb} != library {lib_thumb}"
        assert 216 <= ws_tile <= 320, f"@{w}px workshop tile out of range: {ws_tile}"
    print("  workshop/library tile parity holds at 1080, 1600, 3840")

    print("OK test_responsive_grid - auto-fit columns / 16:10 aspect lock / <=10% optical "
          "flex / min-window all hold across 1080..3840")


if __name__ == "__main__":
    main()
