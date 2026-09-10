"""Backup and restore: one .lwebackup zip with a manifest; import goes through the schemas
(settings migrations included, unknown keys dropped and named), overrides keep their
set-ness, overrides and tags for wallpapers not in the library are held, a machine path
that does not exist here is re-resolved, a newer format is refused, the export is atomic,
and the importer keeps a pre-seeded override's user keys at first arrival only."""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket and the host probes before any lwe_ui import)
import json
import os
import shutil
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
    from lwe_ui.storage import backup, meta, paths, playlists, settings, tags, tier_a, wp

    paths.ensure_dirs()
    settings.ensure_exists()
    lib = os.path.join(home, "walls")
    os.makedirs(os.path.join(lib, "111"))          # 111 is in the library, 222 is not
    settings.save({**settings.load(), "WALLPAPERS_DIR": lib, "ENGINE_LAYER": "top",
                   "INTERFACE_SCALE": 125})
    playlists.save("mine", {"NAME": "Mine", "MODE": "shuffle", "INTERVAL": 600, "UNIT": "min",
                            "MEMBERS": "111 222"})
    # sparse overrides, as the editor writes them: only the keys the user set are present
    (paths.wp_dir() / "111.conf").write_text(
        f"BG={os.path.join(lib, '111')}\nSPEED=2.5\nPROP_hue=0.3\n", encoding="utf-8")
    # 222 references a workshop folder that will not exist on the other machine
    (paths.wp_dir() / "222.conf").write_text("BG=/gone/ref-222\nMOUSE=true\n", encoding="utf-8")
    tags.set_state("111", "One", "good")
    tags.set_state("222", "Two", "good")
    meta.update("111", {"favorite": True})
    (paths.config_dir() / "pause-blacklist.txt").write_text("game.exe\n", encoding="utf-8")

    # --- export: one zip, manifest, portable folder, sparse override text, counts, atomic
    out = os.path.join(home, backup.default_name())
    assert out.endswith(".lwebackup")
    r = backup.export_to(out)
    assert r["errors"] == [], r["errors"]
    assert not os.path.exists(out + ".part"), "the temp file is renamed away"
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        m = json.loads(z.read("manifest.json"))
        assert m["format"] == backup.FORMAT and m["app"] == "lwe-ui"
        assert {"settings.conf", "theme.json", "playlists/mine.conf", "wp/111.conf", "wp/222.conf",
                "tags.csv", "meta.json", "rules/pause-blacklist.txt"} <= names, names
        assert "engine-env" not in " ".join(names)
        conf111 = tier_a.parse(z.read("wp/111.conf").decode())
        assert conf111 == {"BG": "111", "SPEED": "2.5", "PROP_hue": "0.3"}, \
            "only the keys the file carried travel, the library folder as the id"
    assert r["counts"] == {"theme": 1, "overlays": 0, "discovery": 0, "playlists": 1, "overrides": 2,
                           "tags": 2, "favourites": 1, "rules": 1}, r["counts"]

    # --- a fresh machine: the old library is gone, a different one has one wallpaper, and
    # the file carries a retired key, a renamed key, an unknown key and a bad value
    shutil.rmtree(lib)
    home2 = tempfile.mkdtemp(prefix="lwe-restore-")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home2, "c")
    lib2 = os.path.join(home2, "walls")
    os.makedirs(os.path.join(lib2, "111"))
    paths.ensure_dirs()
    settings.ensure_exists()
    settings.save({**settings.load(), "WALLPAPERS_DIR": lib2})
    doctored = os.path.join(home2, "doctored.lwebackup")
    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(doctored, "w") as zout:
        for n in zin.namelist():
            data = zin.read(n)
            if n == "settings.conf":
                # an older build's file: minutes, not seconds, and a vendor decoder token
                data = b"\n".join(ln for ln in data.split(b"\n") if not ln.startswith(b"DETECT_INTERVAL_SEC="))
                data += b"\nFUTURE_KNOB=1\nENGINE_HWDEC=nvdec\nDETECT_INTERVAL_MIN=5\n"
            if n == "wp/222.conf":
                data += b"\nNEWER_THING=x\nSPEED=99\n"
            if n == "theme.json":
                t = json.loads(data)
                t["glow"] = True
                data = json.dumps(t).encode()
            zout.writestr(n, data)

    pre = backup.preflight(doctored)
    assert pre["errors"] == [], pre["errors"]
    dropped = {(d["kind"], d["id"]) for d in pre["dropped"]}
    assert ("setting", "FUTURE_KNOB") in dropped, dropped
    assert ("override-key", "222:NEWER_THING") in dropped and ("override-key", "222:SPEED") in dropped, dropped
    assert ("theme", "glow") in dropped, dropped
    assert ("setting", "DETECT_INTERVAL_MIN") not in dropped, "a renamed key migrates, it is not dropped"
    assert pre["plan"]["settings"]["ENGINE_HWDEC"] == "auto", "the decoder migration a normal load applies"
    assert pre["plan"]["settings"]["DETECT_INTERVAL_SEC"] == 300, "minutes became seconds"
    held = {(h["kind"], h.get("id")) for h in pre["held"]}
    assert ("override", "222") in held and ("playlist-members", "mine") in held, held
    assert ("override", "111") not in held
    assert ("tags", None) in held and pre["counts"]["tags_held"] == 1, "the tag for the absent wallpaper waits"
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
    assert s["ENGINE_HWDEC"] == "auto" and s["DETECT_INTERVAL_SEC"] == 300
    assert s["WALLPAPERS_DIR"] == lib2, "a path that does not exist here keeps the current value"
    assert "FUTURE_KNOB" not in open(paths.settings_file(), encoding="utf-8").read()
    set111 = wp.load_set("111")
    assert set111 == {"BG": "111", "SPEED": 2.5, "props": {"hue": "0.3"}}, \
        f"set-ness kept: only the keys the backup carried are present, got {set111}"
    set222 = wp.load_set("222")
    assert set222 == {"BG": "222", "MOUSE": True, "props": {}}, set222
    assert playlists.load("mine")["MEMBERS"] == "111 222", "missing members stay in the playlist"
    assert {r["id"] for r in tags.load()} == {"111"}, "no tag row for a wallpaper with no folder"
    assert meta.get("111").get("favorite") is True
    assert (paths.config_dir() / "pause-blacklist.txt").read_text() == "game.exe\n"
    from lwe_ui.storage import themes
    import json as _json
    assert "glow" not in _json.loads(paths.theme_file().read_text(encoding="utf-8")), "the file carries no unknown key"
    line = backup.receipt_line(r2)
    assert line.startswith("Restored 1 playlist, 2 overrides, 1 tag · 2 waiting for wallpapers · "), line
    assert line.endswith("dropped"), line

    # --- a second import after the wallpaper arrived restores the held tag
    os.makedirs(os.path.join(lib2, "222"))
    r3 = backup.import_from(doctored)
    assert r3["errors"] == []
    assert {r["id"] for r in tags.load()} == {"111", "222"}
    assert r3["counts"]["tags_held"] == 0 and r3["counts"]["overrides_held"] == 0

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

    # --- first arrival writes facts, not defaults; a pre-seeded override keeps the user's
    # keys and gains only the facts the user has not set; a preset wire rewrites whole
    from lwe_ui.storage import importer
    fresh = {"BG": "222", "TYPE": "video", "CC": "0.5 0.5 0.5 0", "AUDIO_REACTIVE": True, "props": {"x": "1"}}
    importer._write_conf("222", fresh)
    kept = wp.load_set("222")
    assert kept["MOUSE"] is True, "the user's key stays"
    assert kept["BG"] == "222" and kept["TYPE"] == "video", "the importer owns the identity"
    assert kept["CC"] == "0.5 0.5 0.5 0" and kept["AUDIO_REACTIVE"] is True and kept["props"] == {"x": "1"}, \
        f"facts the user never set are added: {kept}"
    assert "SPEED" not in kept and "VOLUME" not in kept, "no default is ever written"
    importer._write_conf("333", {**fresh, "BG": "333", "CC": "1 1 1 0", "AUDIO_REACTIVE": False, "props": {}})
    assert wp.load_set("333") == {"BG": "333", "TYPE": "video", "props": {}}, \
        f"identity only when the facts are the defaults: {wp.load_set('333')}"
    wp.write_keys("444", {"BG": "/base/444", "TYPE": "scene", "AUDIO_REACTIVE": "true"})
    (paths.wp_dir() / "555.conf").write_text("BG=555\nMOUSE=true\n", encoding="utf-8")
    cfg = settings.load()
    assert importer._wire_preset_conf("555", {"raw": {"preset": {}}}, "444", cfg) is True
    wired = wp.load_set("555")
    assert wired["AUDIO_REACTIVE"] is True and wired["BG"] == "/base/444" and "MOUSE" not in wired, \
        f"the preset wire rewrites through the base, sparsely: {wired}"
    print("OK backup: sparse round trip, migrations, held overrides and tags, refusals, sparse arrival")


if __name__ == "__main__":
    main()
