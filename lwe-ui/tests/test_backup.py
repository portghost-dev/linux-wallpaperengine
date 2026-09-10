"""Backup and restore: one .lwebackup zip with a manifest; import goes through the schemas
(unknown keys dropped and named), overrides for wallpapers not in the library are held, a
machine path that does not exist here is re-resolved, a newer format is refused, and the
importer keeps a pre-seeded override's user keys when the wallpaper arrives."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-backup-")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
    os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
    os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
    from lwe_ui import constants as C
    from lwe_ui.storage import backup, meta, paths, playlists, settings, tags, wp

    paths.ensure_dirs()
    settings.ensure_exists()
    lib = os.path.join(home, "walls")
    os.makedirs(os.path.join(lib, "111"))          # 111 is in the library, 222 is not
    settings.save({**settings.load(), "WALLPAPERS_DIR": lib, "ENGINE_LAYER": "top",
                   "INTERFACE_SCALE": 125})
    playlists.save("mine", {"NAME": "Mine", "MODE": "shuffle", "INTERVAL": 600, "UNIT": "min",
                            "MEMBERS": "111 222"})
    wp.save("111", {**{k: s["default"] for k, s in C.WP_SCHEMA.items()},
                    "BG": os.path.join(lib, "111"), "SPEED": 2.5, "props": {"hue": "0.3"}})
    # 222 references a workshop folder that will not exist on the other machine
    wp.save("222", {**{k: s["default"] for k, s in C.WP_SCHEMA.items()}, "BG": "/gone/ref-222", "MOUSE": True})
    tags.set_state("111", "One", "good")
    tags.set_state("222", "Two", "good")
    meta.update("111", {"favorite": True})
    (paths.config_dir() / "pause-blacklist.txt").write_text("game.exe\n", encoding="utf-8")

    # --- export: one zip, manifest, portable folder, counts
    out = os.path.join(home, backup.default_name())
    assert out.endswith(".lwebackup")
    r = backup.export_to(out)
    assert r["errors"] == [], r["errors"]
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        m = json.loads(z.read("manifest.json"))
        assert m["format"] == backup.FORMAT and m["app"] == "lwe-ui"
        assert {"settings.conf", "theme.json", "playlists/mine.conf", "wp/111.conf", "wp/222.conf",
                "tags.csv", "meta.json", "rules/pause-blacklist.txt"} <= names, names
        assert "engine-env" not in " ".join(names)
        assert "BG=111" in z.read("wp/111.conf").decode(), "the library's own folder travels as the id"
        assert "PROP_hue=0.3" in z.read("wp/111.conf").decode()
    assert r["counts"] == {"playlists": 1, "overrides": 2, "tags": 2, "favourites": 1, "rules": 1}, r["counts"]

    # --- a fresh machine: the old library is gone, a different one has one wallpaper,
    # and the file carries keys this build does not know
    import shutil
    shutil.rmtree(lib)
    home2 = tempfile.mkdtemp(prefix="lwe-restore-")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home2, "c")
    lib2 = os.path.join(home2, "walls")
    os.makedirs(os.path.join(lib2, "111"))
    paths.ensure_dirs()
    settings.ensure_exists()
    settings.save({**settings.load(), "WALLPAPERS_DIR": lib2})
    # doctor the archive: a key this build does not know, in settings and in an override
    doctored = os.path.join(home2, "doctored.lwebackup")
    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(doctored, "w") as zout:
        for n in zin.namelist():
            data = zin.read(n)
            if n == "settings.conf":
                data += b"\nFUTURE_KNOB=1\n"
            if n == "wp/222.conf":
                data += b"\nNEWER_THING=x\n"
            zout.writestr(n, data)

    pre = backup.preflight(doctored)
    assert pre["errors"] == [], pre["errors"]
    dropped = {(d["kind"], d["id"]) for d in pre["dropped"]}
    assert ("setting", "FUTURE_KNOB") in dropped and ("override-key", "222:NEWER_THING") in dropped, dropped
    held = {(h["kind"], h["id"]) for h in pre["held"]}
    assert ("override", "222") in held and ("playlist-members", "mine") in held, held
    assert ("override", "111") not in held
    rr = {x["key"]: x for x in pre["reresolved"]}
    assert "WALLPAPERS_DIR" in rr and rr["WALLPAPERS_DIR"]["to"] == lib2, rr
    assert "override 222 folder" in rr and rr["override 222 folder"]["to"] == "222", rr
    kinds = {f["kind"] for f in pre["followups"]}
    assert {"relaunch", "engine-restart"} <= kinds, kinds
    assert pre["counts"]["overrides"] == 2 and pre["counts"]["overrides_held"] == 1

    r2 = backup.apply(pre)
    assert r2["errors"] == [], r2["errors"]
    s = settings.load()
    assert s["ENGINE_LAYER"] == "top" and s["INTERFACE_SCALE"] == 125
    assert s["WALLPAPERS_DIR"] == lib2, "a path that does not exist here keeps the current value"
    assert "FUTURE_KNOB" not in open(paths.settings_file(), encoding="utf-8").read()
    assert wp.load("111")["SPEED"] == 2.5 and wp.load("111")["props"] == {"hue": "0.3"}
    assert wp.load("111")["BG"] == "111"
    d222 = wp.load("222")
    assert d222["MOUSE"] is True and d222["BG"] == "222", "held override written, folder re-resolved to the id"
    assert playlists.load("mine")["MEMBERS"] == "111 222", "missing members stay in the playlist"
    assert {r["id"] for r in tags.load()} == {"111", "222"}
    assert meta.get("111").get("favorite") is True
    assert (paths.config_dir() / "pause-blacklist.txt").read_text() == "game.exe\n"
    line = backup.receipt_line(r2)
    assert line.startswith("Restored 1 playlist, 2 overrides, 2 tags · 1 waiting for wallpapers · 2 dropped"), line

    # --- refusals
    bad = os.path.join(home2, "x.lwebackup")
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": backup.FORMAT + 1, "app": "lwe-ui"}))
        z.writestr("settings.conf", "")
    assert "newer version" in backup.preflight(bad)["errors"][0]["reason"]
    open(bad, "wb").write(b"not a zip")
    assert "not an LWE backup" in backup.preflight(bad)["errors"][0]["reason"]
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("readme.txt", "hi")
    assert "not an LWE backup" in backup.preflight(bad)["errors"][0]["reason"]

    # --- the importer keeps a pre-seeded override's user keys when the wallpaper arrives
    from lwe_ui.storage import importer
    fresh = {k: s["default"] for k, s in C.WP_SCHEMA.items()}
    fresh.update({"BG": "222", "TYPE": "video", "CC": "0.5 0.5 0.5 0", "AUDIO_REACTIVE": True, "props": {"x": "1"}})
    importer._write_conf("222", fresh)
    kept = wp.load("222")
    assert kept["MOUSE"] is True and kept["CC"] == "1 1 1 0", "the user's override wins over the importer's derivation"
    assert kept["BG"] == "222" and kept["TYPE"] == "video", "the importer still owns the folder and the type"
    importer._write_conf("333", {**fresh, "BG": "333"})
    assert wp.load("333")["CC"] == "0.5 0.5 0.5 0", "a wallpaper with no override gets the derived one"
    print("OK backup: export zip + manifest, schema import, held and re-resolved, refusals, importer merge")


if __name__ == "__main__":
    main()
