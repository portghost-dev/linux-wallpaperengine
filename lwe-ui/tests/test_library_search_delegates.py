"""Typing in the search field keeps the tiles that survive it.

Loads the real Library.qml offscreen over a seeded library (52 members, 22 pool), records the
delegate OBJECT behind every visible tile, then applies a search that keeps some of them. The
surviving rows must be the same objects afterwards: a delegate that is destroyed and rebuilt
reloads its image asynchronously, which is the blank flash a keystroke used to cause.

Run: PYTHONPATH=src python3 tests/test_library_search_delegates.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lwe-searchdeleg-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QObject, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402, F401  (registers the item converters)
from PySide6.QtTest import QTest  # noqa: E402

from lwe_ui.storage import playlists, tags  # noqa: E402

IDS = [str(1000 + i) for i in range(74)]


def seed_library() -> None:
    wdir = Path(_TMP) / ".local/share/lwe/wallpapers"
    for wid in IDS:
        d = wdir / wid
        d.mkdir(parents=True, exist_ok=True)
        (d / "project.json").write_text(
            '{"title": "WP %s", "type": "scene", "file": "x", "preview": ""}' % wid, encoding="utf-8")
        tags.set_state(wid, "WP " + wid, "good")


def main() -> None:
    app = QGuiApplication([])
    seed_library()
    from lwe_ui.models import Backend, ThemeTokens
    from lwe_ui.app import _resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME
    tokens = ThemeTokens()
    tokens.set_tokens(_resolve_theme_tokens())
    qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)
    b = Backend()
    slug = playlists.active_slug()
    d = playlists.load(slug)
    d["MEMBERS"] = " ".join(IDS[:52])
    playlists.save(slug, d)
    b.refresh()
    b.playlistsChanged.emit()

    engine = QQmlApplicationEngine()
    problems: list[str] = []
    engine.warnings.connect(lambda ws: problems.extend(w.toString() for w in ws))
    engine.rootContext().setContextProperty("backend", b)
    host = Path(_QML_DIR) / "_searchdeleg_host.qml"
    host.write_text('''import QtQuick
import QtQuick.Window
import "."
Window { id: win; width: 1280; height: 700; visible: true
    Library { objectName: "lib"; anchors.fill: parent } }
''', encoding="utf-8")
    try:
        engine.load(QUrl.fromLocalFile(str(host)))
        assert engine.rootObjects(), problems
        win = engine.rootObjects()[0]
        grid = win.findChild(QObject, "libraryGrid")
        assert grid is not None
        QTest.qWait(300)
        om = b.orderModel
        resets = {"n": 0}
        om.modelReset.connect(lambda: resets.__setitem__("n", resets["n"] + 1))

        def tiles() -> dict:
            """The delegate object behind every tile inside the viewport, by wallpaper id."""
            top = float(grid.property("contentY"))
            bottom = top + float(grid.property("height"))
            out = {}
            for it in grid.property("contentItem").childItems():
                wid = it.property("wpId")
                if not wid or it.property("index") is None:
                    continue
                if int(it.property("index")) >= om.rowCount():
                    continue
                if (float(it.property("y")) + float(it.property("height")) > top
                        and float(it.property("y")) < bottom):
                    out[str(wid)] = it
            return out

        before = tiles()
        assert len(before) >= 10, f"only {len(before)} tiles in view"
        updates = om._updates

        b.setSearch("100")   # WP 1000..WP 1009: the first ten member tiles, all in view
        QTest.qWait(200)
        after = tiles()
        kept = sorted(set(before) & set(after))
        assert len(kept) >= 8, f"the search kept {len(kept)} of the visible tiles, expected ten"
        same = [wid for wid in kept if after[wid] is before[wid]]
        assert same == kept, f"rebuilt delegates for {sorted(set(kept) - set(same))}"
        assert resets["n"] == 0, f"{resets['n']} model resets for one keystroke"
        assert om._updates == updates + 1, f"{om._updates - updates} updates for one keystroke"
        assert om._reset_fallbacks == 0, om._reset_fallbacks

        # widening again brings the rest back without disturbing the ten that stayed
        held = dict(after)
        b.setSearch("10")
        QTest.qWait(200)
        back = tiles()
        still = [wid for wid in held if wid in back and back[wid] is held[wid]]
        assert sorted(still) == sorted(held), f"widening rebuilt {sorted(set(held) - set(still))}"
        assert resets["n"] == 0, resets["n"]
        assert om._reset_fallbacks == 0, om._reset_fallbacks
        # the survivors kept their loaded image: no reload, no blank frame
        for wid, it in back.items():
            if wid in held:
                img = it.findChild(QQuickItem, "rawThumb")
                assert img is not None, f"{wid}: no thumbnail image in the card"
                src = img.property("source")
                src = src.toString() if hasattr(src, "toString") else str(src or "")
                if src:
                    assert float(img.property("progress")) == 1.0, f"{wid} reloaded its image"

        # a search narrowed from a scrolled position brings the viewport back onto the content
        b.setSearch("")
        QTest.qWait(200)
        grid.setProperty("contentY", max(0.0, float(grid.property("contentHeight")) - float(grid.property("height"))))
        QTest.qWait(100)
        assert float(grid.property("contentY")) > 100, "the grid did not scroll down"
        b.setSearch("100")
        QTest.qWait(700)
        max_y = max(0.0, float(grid.property("contentHeight")) - float(grid.property("height")))
        assert float(grid.property("contentY")) <= max_y + 0.5, \
            f"viewport left below the content: contentY {grid.property('contentY')} > {max_y}"
        assert not problems, problems
        print("OK: a search keeps the surviving tiles' own delegates, with no model reset "
              "and one update per keystroke, and the viewport stays on the content")
    finally:
        host.unlink(missing_ok=True)
        engine.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    main()
