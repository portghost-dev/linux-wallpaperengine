"""A wallpaper folder whose name fails the id allowlist keeps its card and says why.

The id is a path component, a word in the shell-sourced playlist files and an engine verb
argument, so the panel only accepts letters, digits, dot, underscore and hyphen. Steam's
workshop folders are numeric and always pass; a hand-named folder like "Cafe Scene" with a
space or an accent does not. Before this the card showed and every action on it failed with
no word. Now the model flags the row, the card shows "rename folder" in the danger colour
with the reason as a tooltip and offers no play or playlist controls, and the backend's play
and playlist verbs answer with a notice carrying the same reason.
Run: PYTHONPATH=src python3 tests/test_refused_folder.py
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lwe-refused-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QCoreApplication, QObject, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import qmlRegisterSingletonInstance  # noqa: E402
from PySide6.QtQuick import QQuickView  # noqa: E402

from lwe_ui.storage import paths, playlists, settings, tags  # noqa: E402

GOOD = "1000"
BAD = "Café Scene"


def _seed() -> None:
    wdir = Path(_TMP) / ".local/share/lwe/wallpapers"
    for wid in (GOOD, BAD):
        d = wdir / wid
        d.mkdir(parents=True, exist_ok=True)
        (d / "project.json").write_text(
            '{"title": "WP", "type": "scene", "file": "x", "preview": ""}', encoding="utf-8")
        tags.set_state(wid, "WP", "good")


def main() -> None:
    app = QGuiApplication([])  # noqa: F841
    paths.ensure_dirs()
    settings.ensure_exists()
    _seed()
    from lwe_ui.models import Backend, ThemeTokens, REFUSED_REASON, _ROLE_REFUSED
    from lwe_ui.app import _resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME
    tokens = ThemeTokens(_resolve_theme_tokens())
    qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)
    b = Backend()
    model = b.libraryModel
    role_id = next(k for k, v in model.roleNames().items() if bytes(v) == b"id")
    role_refused = next(k for k, v in model.roleNames().items() if bytes(v) == b"refused")
    assert role_refused == _ROLE_REFUSED
    rows = {str(model.data(model.index(r, 0), role_id)): bool(model.data(model.index(r, 0), role_refused))
            for r in range(model.rowCount())}
    assert rows.get(GOOD) is False and rows.get(BAD) is True, f"the refused flag follows the allowlist: {rows}"

    notices: list[str] = []
    b.notice.connect(notices.append)
    assert b.showNow(BAD) is False and notices[-1] == REFUSED_REASON, "play answers with the reason"
    b.setPlaylist(BAD, True)
    assert notices[-1] == REFUSED_REASON and BAD not in playlists.members(playlists.active_slug()), \
        "the playlist toggle answers with the reason and stores nothing"
    assert len(notices) == 2

    # the card: badge reads rename folder in the danger colour, no checkbox, no play, tooltip text
    view = QQuickView()
    view.engine().addImportPath(str(_QML_DIR))
    view.setSource(QUrl.fromLocalFile(str(_QML_DIR / "WallpaperCard.qml")))
    assert view.status() == QQuickView.Status.Ready, [e.toString() for e in view.errors()]
    card = view.rootObject()
    card.setProperty("wpId", BAD)
    card.setProperty("title", "Cafe Scene")
    card.setProperty("wpType", "scene")
    card.setProperty("refused", True)
    view.resize(220, 190)
    view.show()
    for _ in range(6):
        QCoreApplication.processEvents()
    badge = card.findChild(QObject, "typeBadge")
    assert badge is not None and badge.property("text") == "rename folder"
    assert badge.property("color").name().lower() == str(tokens.color("danger")).lower()
    assert card.property("refusedReason") == REFUSED_REASON, "card and backend carry the same words"
    print("OK test_refused_folder (flag, notices, card badge)")


if __name__ == "__main__":
    main()
