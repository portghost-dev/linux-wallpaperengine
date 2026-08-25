"""Render-level regression tests (offscreen pixel grab) for live-app bugs #4 and #2.

#4 the card border must frame ALL FOUR corners - including the TOP, over the MultiEffect-masked
   thumbnail. We render a WallpaperCard with missing=true and assert the top edge shows the danger
   color (before the fix the masked thumbnail painted over the top border -> gray, not red). The
   missing state now draws a 1px danger border + a dangerWash fill (restyled off the
   old out-of-whitelist 2px border), so this also checks the wash by comparing the missing card's
   interior against a non-missing card's.
#2 the GridView must recompute contentHeight when the filter changes (gaps + dead scroll were a
   stale-layout symptom). We bind a GridView to the proxy, filter down then clear, and assert
   contentHeight returns to the full multi-row height (so the view is scrollable, no gaps).

Runs headless (offscreen + software renderer). Skips cleanly if the platform can't grab a frame.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import tempfile
from pathlib import Path

from PySide6.QtCore import QUrl, QCoreApplication
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQml import qmlRegisterSingletonInstance
from PySide6.QtQuick import QQuickView

from lwe_ui.models import ThemeTokens, LibraryModel, LibraryFilterModel, _Row
from lwe_ui.app import _resolve_theme_tokens, _TOKENS_URI, _TOKENS_NAME, _QML_DIR


def _near(px: QColor, hex_target: str, tol: int = 45) -> bool:
    c = QColor(hex_target)
    return (abs(px.red() - c.red()) <= tol
            and abs(px.green() - c.green()) <= tol
            and abs(px.blue() - c.blue()) <= tol)


def _grab_card(missing: bool):
    view = QQuickView()
    view.engine().addImportPath(str(_QML_DIR))
    view.setSource(QUrl.fromLocalFile(str(_QML_DIR / "WallpaperCard.qml")))
    assert view.status() == QQuickView.Status.Ready, [e.toString() for e in view.errors()]
    card = view.rootObject()
    card.setProperty("wpId", "x")
    card.setProperty("title", "Missing One" if missing else "Present One")
    card.setProperty("thumb", "")
    card.setProperty("missing", missing)
    view.resize(220, 190)
    view.show()
    for _ in range(6):
        QCoreApplication.processEvents()
    return view, view.grabWindow()


def _danger_tinted(px: QColor) -> bool:
    # a danger-tinted pixel: red channel clearly dominant over green/blue (the dangerWash fill
    # over the dark surface reads as a muted red, not pure #E24B4A).
    return px.red() > px.green() + 20 and px.red() > px.blue() + 20


def test_card_border_frames_top_when_missing(app, tokens) -> None:
    view, img = _grab_card(missing=True)
    if img.isNull() or img.width() < 100:
        print("SKIP test_card_border (no frame grabbed on this platform)")
        return
    danger = tokens.color("danger")  # "#E24B4A"
    w = img.width()
    # A3: the missing state draws a 1px danger border, so the top edge is ~1px of danger over
    # the masked thumbnail. Sample the top 2px band (border + antialiasing) and require danger
    # present - before the #4 fix the masked thumbnail painted over it (gray, no red).
    hits = 0
    samples = 0
    for x in range(int(w * 0.25), int(w * 0.75)):
        for y in (0, 1):
            samples += 1
            if _near(img.pixelColor(x, y), danger):
                hits += 1
    frac = hits / max(1, samples)
    assert frac > 0.25, (
        f"top border should show danger red when missing (got {frac:.0%} red along the top edge); "
        f"the masked thumbnail is painting over the border (#4)"
    )

    # A3 wash: the missing card's interior must be danger-tinted (dangerWash fill), clearly more
    # so than a non-missing card whose fill is the neutral surface/variant.
    view2, img2 = _grab_card(missing=False)
    cx0, cx1 = int(w * 0.3), int(w * 0.7)
    cy0, cy1 = int(img.height() * 0.3), int(img.height() * 0.6)

    def _tint_count(im) -> int:
        n = 0
        for x in range(cx0, cx1, 2):
            for y in range(cy0, cy1, 2):
                if _danger_tinted(im.pixelColor(x, y)):
                    n += 1
        return n

    miss_tint = _tint_count(img)
    clean_tint = _tint_count(img2)
    assert miss_tint > clean_tint + 20, (
        f"the missing card must carry a dangerWash fill (danger-tinted interior px "
        f"missing->{miss_tint} vs present->{clean_tint}); the A3 wash is absent (#4/A3)"
    )
    print(f"OK test_card_border_frames_top_when_missing (top edge {frac:.0%} danger-red; "
          f"wash tint missing->{miss_tint} vs present->{clean_tint})")


def test_gridview_relayouts_on_filter(app) -> None:
    # 7 rows; 720-wide grid @ cellWidth 232 -> 3 columns -> 3 rows tall -> contentHeight 3*202.
    src = LibraryModel()
    rows = []
    for i in range(7):
        r = _Row(f"id{i}")
        r.title = f"Wallpaper {i}"
        rows.append(r)
    src.beginResetModel(); src._rows = rows; src.endResetModel()
    proxy = LibraryFilterModel(src)

    qml = (
        "import QtQuick\n"
        "GridView {\n"
        "  width: 720; height: 400\n"
        "  cellWidth: 232; cellHeight: 202\n"
        "  model: testProxy\n"
        "  delegate: Rectangle { width: 200; height: 180; color: '#333' }\n"
        "}\n"
    )
    tmp = Path(tempfile.mkdtemp()) / "Probe.qml"
    tmp.write_text(qml)
    view = QQuickView()
    view.rootContext().setContextProperty("testProxy", proxy)
    view.setSource(QUrl.fromLocalFile(str(tmp)))
    assert view.status() == QQuickView.Status.Ready, [e.toString() for e in view.errors()]
    view.resize(720, 400)
    view.show()
    grid = view.rootObject()

    def _content_height() -> float:
        # a render pass forces the GridView's layout polish so contentHeight reflects the model.
        for _ in range(3):
            QCoreApplication.processEvents()
        view.grabWindow()
        QCoreApplication.processEvents()
        return grid.property("contentHeight")

    cell_h = 202
    full = _content_height()
    assert proxy.rowCount() == 7
    assert full >= cell_h * 3 - 1, f"full grid should be ~3 rows tall, got contentHeight={full}"
    assert full > 400, "full content must exceed the viewport (scrollable, not dead-scroll)"

    proxy.setSearchText("Wallpaper 3")
    assert proxy.rowCount() == 1, f"proxy should filter to 1 row, got {proxy.rowCount()}"
    one = _content_height()
    assert abs(one - cell_h) < 2, f"filtered to 1 row -> contentHeight ~= one cell, got {one}"

    proxy.setSearchText("")
    assert proxy.rowCount() == 7
    restored = _content_height()
    assert abs(restored - full) < 2, (
        f"clearing the filter must recompute contentHeight back to full ({full}), got {restored} "
        f"-> this is the gap/dead-scroll bug (#2)"
    )
    print(f"OK test_gridview_relayouts_on_filter (contentHeight {full}->{one}->{restored})")


if __name__ == "__main__":
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    tokens = ThemeTokens(_resolve_theme_tokens())
    qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)
    test_card_border_frames_top_when_missing(app, tokens)
    test_gridview_relayouts_on_filter(app)
    print("ALL card-render regressions passed")
