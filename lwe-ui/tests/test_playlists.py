"""Playlist store checks (storage/playlists.py).

Standalone runnable: sandboxes HOME/XDG into a temp tree BEFORE importing lwe_ui, so the
live ~/.config/lwe is never touched (project safety rule).
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import os
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lwe-playlists-test-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lwe_ui import constants as C                            # noqa: E402
from lwe_ui.storage import paths, playlists, settings, tags  # noqa: E402


def main() -> None:
    paths.ensure_dirs()

    assert playlists.slugify("Chill Evenings!") == "chill-evenings"
    assert playlists.slugify("   ") == "playlist"
    long_slug = playlists.slugify("Chill " * 20)
    assert len(long_slug) <= playlists.SLUG_MAX and not long_slug.endswith("-"), long_slug
    assert len(playlists._unique_slug("Chill " * 20)) <= 64  # the engine's id limit
    a = playlists.create("Chill Evenings!")
    b = playlists.create("Chill evenings")
    assert a == "chill-evenings" and b == "chill-evenings-2", (a, b)

    playlists.save(a, {"NAME": "Chill Evenings!", "MODE": "sequential", "INTERVAL": 120,
                       "UNIT": "s", "MEMBERS": "  111  222 111  wec_preset "})
    d = playlists.load(a)
    assert d["MODE"] == "sequential" and d["INTERVAL"] == 120 and d["UNIT"] == "s"
    assert d["MEMBERS"] == "111 222 wec_preset", d["MEMBERS"]

    # garbage snaps to defaults, never raises
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        playlists.save(a, {"NAME": "X", "MODE": "bogus", "INTERVAL": "nan",
                           "UNIT": "hours", "MEMBERS": "1 2"})
    d = playlists.load(a)
    assert d["MODE"] == "shuffle" and d["INTERVAL"] == 900 and d["UNIT"] == "min"

    # interval clamps to schema bounds (1 .. 9999 min in seconds)
    playlists.save(a, {"NAME": "X", "MODE": "shuffle", "INTERVAL": 0, "UNIT": "s", "MEMBERS": ""})
    assert playlists.load(a)["INTERVAL"] == 1
    playlists.save(a, {"NAME": "X", "MODE": "shuffle", "INTERVAL": 10**9, "UNIT": "s", "MEMBERS": ""})
    assert playlists.load(a)["INTERVAL"] == 599940

    assert playlists.toggle_member(a, "42") is True
    assert "42" in playlists.members(a)
    assert playlists.toggle_member(a, "42") is False
    assert "42" not in playlists.members(a)

    # active pointer validates existence; nothing mirrors into settings any more -
    # the user's pause switch (ROTATION_ENABLED) must survive playlist changes untouched
    assert playlists.active_slug() == ""
    st = settings.load(); st["ROTATION_ENABLED"] = False; settings.save(st)
    playlists.save(a, {"NAME": "X", "MODE": "shuffle", "INTERVAL": 300, "UNIT": "s", "MEMBERS": ""})
    playlists.set_active(a)
    assert playlists.active_slug() == a
    s = settings.load()
    assert s["ROTATION_ENABLED"] is False, "activating a playlist must not flip the pause switch"
    st = settings.load(); st["ROTATION_ENABLED"] = True; settings.save(st)

    # garbage playlist FILE -> load never crashes, falls to defaults
    paths.playlist_file(b).write_text("MODE=\nutter ??? garbage\nINTERVAL=abc\n", encoding="utf-8")
    d = playlists.load(b)
    assert d["MODE"] == "shuffle" and d["INTERVAL"] == 900

    playlists.set_active(a)
    playlists.delete(a)
    assert not paths.playlist_file(a).exists()
    stones = list(paths.legacy_playlists_dir().glob(f"{a}.conf.*"))
    assert len(stones) == 1, stones
    assert playlists.active_slug() == b

    playlists.delete(b)
    assert playlists.active_slug() == ""
    tags.set_state("100", "A", "good")
    tags.set_state("200", "B", "good")
    tags.set_state("300", "C", "bad")
    s = settings.load()
    s["ORDER"] = "sequential"
    s["INTERVAL"] = 1200
    settings.save(s)
    slug = playlists.ensure_default()
    d = playlists.load(slug)
    assert d["NAME"] == C.DEFAULT_PLAYLIST_NAME
    assert d["MODE"] == "sequential" and d["INTERVAL"] == 1200
    assert d["MEMBERS"].split() == ["100", "200"], d["MEMBERS"]  # good only, sorted; bad excluded
    assert playlists.active_slug() == slug
    assert playlists.ensure_default() == slug

    # the Tier A file really bash-sources + word-splits (the watcher contract)
    pf = paths.playlist_file(slug)
    r = subprocess.run(
        ["bash", "-c", f'set -a; . "{pf}"; for id in $MEMBERS; do echo "$id"; done'],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["100", "200"], r.stdout

    # a retired mode on disk reads as shuffle, silently, and is written back as shuffle
    legacy = playlists.create("Legacy", members=["100", "200", "300"], mode="shuffle")
    lf = paths.playlist_file(legacy)
    lf.write_text(lf.read_text().replace("MODE=shuffle", "MODE=random"))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert playlists.load(legacy)["MODE"] == "shuffle"
    playlists.save(legacy, playlists.load(legacy))
    assert "MODE=shuffle" in lf.read_text()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        playlists.save(legacy, {"NAME": "Legacy", "MODE": "random", "INTERVAL": 60, "UNIT": "s", "MEMBERS": "100 200 300"})
    assert playlists.load(legacy)["MODE"] == "shuffle"
    # the legacy single-playlist ORDER key reads the same way
    sf = paths.settings_file()
    st = settings.load(); st["ORDER"] = "sequential"; settings.save(st)
    sf.write_text(sf.read_text(encoding="utf-8").replace("ORDER=sequential", "ORDER=random"), encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert settings.load()["ORDER"] == "shuffle"
    assert "random" not in C.PLAYLIST_MODES and "random" not in C.ORDERS

    # reorder keeps every member: left-out ids trail in their old order, strangers are ignored
    assert playlists.reorder(legacy, ["300", "999", "100"]) == ["300", "100", "200"]
    assert playlists.members(legacy) == ["300", "100", "200"]
    assert playlists.reorder(legacy, []) == ["300", "100", "200"]

    # insert adds at a clamped index, moves an existing member instead of duplicating it,
    # and refuses an id that would split the shell list
    assert playlists.insert_member(legacy, "400", 1) == 1
    assert playlists.members(legacy) == ["300", "400", "100", "200"]
    assert playlists.insert_member(legacy, "200", 0) == 0
    assert playlists.members(legacy) == ["200", "300", "400", "100"]
    assert playlists.insert_member(legacy, "500", 99) == 4
    assert playlists.members(legacy) == ["200", "300", "400", "100", "500"]
    assert playlists.insert_member(legacy, "bad id", 0) == -1
    assert playlists.members(legacy) == ["200", "300", "400", "100", "500"]

    print("OK: playlists store - slug/round-trip/normalize/clamp/toggle/active/mirror/"
          "tombstone/migration/bash-source/legacy-random/reorder/insert all pass")


if __name__ == "__main__":
    main()
