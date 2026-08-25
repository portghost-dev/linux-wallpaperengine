"""Backend playlist bridge, scopes, trash, and session overrides (design v1.0 4).

Sandboxes HOME/XDG before importing lwe_ui; drives the real Backend offscreen. The
watcher courier no-ops against an absent watcher, so nothing here can signal anything.
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lwe-bridge-test-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from lwe_ui import constants as C  # noqa: E402
from lwe_ui.storage import paths, playlists, settings, tags  # noqa: E402


def seed_library() -> None:
    """Two on-disk wallpapers (one scene, one video) plus tags for them."""
    wdir = Path(_TMP) / ".local/share/lwe/wallpapers"
    for wid, wtype in (("100", "scene"), ("200", "video")):
        d = wdir / wid
        d.mkdir(parents=True, exist_ok=True)
        (d / "project.json").write_text(
            '{"title": "WP %s", "type": "%s", "file": "x", "preview": ""}' % (wid, wtype),
            encoding="utf-8")
    tags.set_state("100", "WP 100", "good")
    tags.set_state("200", "WP 200", "good")
    wk = Path(_TMP) / "workshop"
    (wk / "300").mkdir(parents=True, exist_ok=True)
    s = settings.load()
    s["WORKSHOP_DIR"] = str(wk)
    settings.save(s)


def main() -> None:
    app = QGuiApplication([])  # noqa: F841  (Backend needs a Qt app context)
    seed_library()

    from lwe_ui.models import Backend
    b = Backend()

    ap = b.activePlaylist()
    assert ap["slug"], ap
    assert ap["name"] == C.DEFAULT_PLAYLIST_NAME
    assert ap["count"] == 2, ap
    assert b.totalCount() == 2
    assert b.playlistCount() == 2

    b.setPlaylist("100", False)
    assert "100" not in playlists.members(ap["slug"])
    assert b.playlistCount() == 1
    b.setPlaylist("100", True)
    assert "100" in playlists.members(ap["slug"])

    f = b.filterModel
    assert f.rowCount() == 2
    f.setTypeFilter("video")
    assert f.rowCount() == 1
    f.setTypeFilter("all")
    f.setPlaylistFilter("out")
    assert f.rowCount() == 0
    f.setPlaylistFilter("any")
    f.setScope("review")
    assert f.rowCount() == 0
    f.setScope("all")
    assert f.rowCount() == 2

    assert b.reviewCount() == 1

    slug2 = b.createPlaylist("Evening")
    assert slug2 and b.activePlaylist()["slug"] == slug2
    assert b.activePlaylist()["count"] == 0
    b.setPlaylistInterval(5, "min")
    assert playlists.load(slug2)["INTERVAL"] == 300
    b.setPlaylistMode("static")
    assert playlists.load(slug2)["MODE"] == "static"
    assert settings.load()["ROTATION_ENABLED"] is True, \
        "mode changes must not touch the user's pause switch (static disables at push time)"
    b.setPlaylistMode("shuffle")
    assert settings.load()["ROTATION_ENABLED"] is True

    forked = b.saveAsPlaylist("Evening copy")
    assert forked and forked != slug2
    b.setActivePlaylist(ap["slug"])
    assert b.activePlaylist()["slug"] == ap["slug"]

    s = settings.load()
    s["SCHEDULE_ENABLED"] = True
    settings.save(s)
    b.setActivePlaylist(slug2)
    assert paths.manual_hold_file().exists()
    paths.manual_hold_file().unlink()
    s = settings.load()
    s["SCHEDULE_ENABLED"] = False
    settings.save(s)

    b.setActivePlaylist(ap["slug"])
    b.trashWallpaper("200")
    rows = {r["id"]: r for r in tags.load()}
    assert rows["200"]["state"] == "bad"
    assert "200" not in playlists.members(ap["slug"])

    b.setSessionOverride("mute", True)
    b.setSessionOverride("parallax", True)
    assert settings.load()["OVERRIDE_MUTE"] is True
    assert b.sessionOverride("parallax") is True
    b.restoreSessionOverrides()
    cur = settings.load()
    assert cur["OVERRIDE_MUTE"] is False and cur["OVERRIDE_PARALLAX_OFF"] is False

    print("OK: playlist bridge - default/counts/membership/filters/review/lifecycle/"
          "manual-hold/trash/session-overrides all pass")


if __name__ == "__main__":
    main()
