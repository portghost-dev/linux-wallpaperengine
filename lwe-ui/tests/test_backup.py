"""Backup and restore: one .lwebackup zip with a manifest; import goes through the schemas
(the migration tables applied, unknown keys kept aside and named, every adjustment named),
overrides keep their set-ness, overrides and tags for wallpapers not in the library are
held, a machine path that does not exist here is re-resolved, a newer format restores with
a note, the export is atomic, the importer keeps a pre-seeded override's user keys at
first arrival only, and a wallpaper's list of meta tags travels while a mixed list is dropped."""
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
                           "tags": 2, "favourites": 1, "rules": 1, "preserved": 0}, r["counts"]

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
    kept = {(d["store"], d["id"], d["key"]) for d in pre["preserved"]}
    assert ("settings", "settings.conf", "FUTURE_KNOB") in kept, pre["preserved"]
    assert ("overrides", "222", "NEWER_THING") in kept, pre["preserved"]
    assert ("theme", "theme.json", "glow") in kept, pre["preserved"]
    assert dropped == set(), f"nothing is dropped where it can be kept: {dropped}"
    adjusted = {(a["kind"], a["store"], a["id"], a["key"]): (a["from"], a["to"])
                for a in pre["adjusted"]}
    assert adjusted[("clamp", "overrides", "222", "SPEED")] == ("99", 10.0), adjusted
    assert adjusted[("alias", "settings", "settings.conf", "ENGINE_HWDEC")] == ("nvdec", "auto"), adjusted
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
    assert set222 == {"BG": "222", "MOUSE": True, "SPEED": 10.0, "props": {}}, \
        f"a value out of range is clamped, not deleted: {set222}"
    assert playlists.load("mine")["MEMBERS"] == "111 222", "missing members stay in the playlist"
    assert {r["id"] for r in tags.load()} == {"111"}, "no tag row for a wallpaper with no folder"
    assert meta.get("111").get("favorite") is True
    assert (paths.config_dir() / "pause-blacklist.txt").read_text() == "game.exe\n"
    from lwe_ui.storage import themes
    import json as _json
    assert "glow" not in _json.loads(paths.theme_file().read_text(encoding="utf-8")), "the file carries no unknown key"
    assert adjusted[("rename", "settings", "settings.conf", "DETECT_INTERVAL_SEC")] == \
        ("DETECT_INTERVAL_MIN", "DETECT_INTERVAL_SEC"), "a renamed key is named on the receipt"
    line = backup.receipt_line(r2)
    assert line == ("Restored 1 playlist, 2 overrides, 1 tag · 2 waiting for wallpapers · 3 adjusted"
                    " · 3 kept aside"), line
    snaps = sorted((paths.state_dir() / "backups").glob("pre-restore-*.lwebackup"))
    assert snaps, "apply takes a snapshot of the configuration it is about to overwrite"
    assert {"kind": "snapshot", "path": str(snaps[-1])} in r2["notes"], r2["notes"]

    # --- a second import after the wallpaper arrived restores the held tag
    os.makedirs(os.path.join(lib2, "222"))
    r3 = backup.import_from(doctored)
    assert r3["errors"] == []
    assert {r["id"] for r in tags.load()} == {"111", "222"}
    assert r3["counts"]["tags_held"] == 0 and r3["counts"]["overrides_held"] == 0

    # --- a newer format is read best effort and named; only a file that is no backup is refused
    bad = os.path.join(home2, "x.lwebackup")
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": backup.FORMAT + 1, "app": "lwe-ui"}))
        z.writestr("settings.conf", "")
    newer = backup.preflight(bad)
    assert not newer.get("refused"), newer
    assert {"kind": "newer", "format": backup.FORMAT + 1} in newer["notes"], newer["notes"]
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
    assert kept["SPEED"] == 10.0, "the clamped override the restore wrote is still the user's"
    assert "VOLUME" not in kept, "no default is ever written"
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


def _doctor(src: str, dst: str, edits: dict, drop: tuple[str, ...] = ()) -> str:
    """A copy of `src` with each named member replaced by edits[name](bytes) and each member
    in `drop` left out. The archives every door is tested against are built from the live
    stores, never written by hand."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for n in zin.namelist():
            if n in drop:
                continue
            data = zin.read(n)
            if n in edits:
                data = edits[n](data)
            zout.writestr(n, data)
    return dst


def doors() -> None:
    """The doors an older or newer build knocks on: renamed and retired keys, a value this
    build cannot hold, a key it does not know, a manifest whose schema disagrees, a format
    from the future, and a backup with no settings at all."""
    home = tempfile.mkdtemp(prefix="lwe-backup-doors-")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
    os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
    os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
    from lwe_ui import constants as C
    from lwe_ui.storage import backup, foreign, meta, paths, playlists, settings, tags, themes, tier_a, wp

    paths.ensure_dirs()
    settings.ensure_exists()
    lib = os.path.join(home, "walls")
    os.makedirs(os.path.join(lib, "aaa"))          # aaa is in the library, bbb is not
    settings.save({**settings.load(), "WALLPAPERS_DIR": lib, "ACTIVE_PLAYLIST": "mine",
                   "INTERFACE_SCALE": 125, "ENGINE_VOLUME": 15, "STEAM_DIR": "/gone/steam"})
    playlists.save("mine", {"NAME": "Mine", "MODE": "shuffle", "INTERVAL": 600, "UNIT": "min",
                            "MEMBERS": "aaa"})
    wp.write_keys("bbb", {"BG": "bbb", "SPEED": "1.5"})
    tags.set_state("bbb", "Trashed", "bad")
    meta.update("aaa", {"favorite": True, "depMissing": True, "depWid": "ccc"})

    base = os.path.join(home, "base.lwebackup")
    backup.export_to(base)
    with zipfile.ZipFile(base) as z:
        carried = json.loads(z.read("meta.json"))
        assert carried["aaa"] == {"favorite": True}, \
            f"only what a person authored travels; a probe's key stays here: {carried}"

    def older_settings(data: bytes) -> bytes:
        text = "\n".join(ln for ln in data.decode().splitlines()
                          if not ln.startswith(("DETECT_INTERVAL_SEC=", "ENGINE_HWDEC=",
                                                "INTERFACE_SCALE=", "ENGINE_VOLUME=")))
        return (text + "\nDETECT_INTERVAL_MIN=5\nENGINE_HWDEC=nvdec\nINTERFACE_SCALE=400"
                       "\nENGINE_VOLUME=42\n").encode()

    def older_theme(data: bytes) -> bytes:
        t = json.loads(data)
        t.update({"preset": "True Black", "accent": "#FF0000"})
        return json.dumps(t).encode()

    def other_manifest(data: bytes) -> bytes:
        m = json.loads(data)
        m["format"] = backup.FORMAT + 1
        m["machine_keys"] = ["WALLPAPERS_DIR"]
        m["schemas"]["settings"]["INTERFACE_SCALE"] = {"type": "int", "default": 100,
                                                       "min": 75, "max": 400}
        m["schemas"]["settings"]["ENGINE_VOLUME"]["default"] = 42
        return json.dumps(m).encode()

    old_build = _doctor(base, os.path.join(home, "old.lwebackup"), {
        "settings.conf": older_settings,
        "theme.json": older_theme,
        "wp/bbb.conf": lambda d: d + b"\nSPEED=99\nGLOW=1\n",
        "meta.json": lambda d: json.dumps({**json.loads(d), "aaa": {"favorite": True,
                                                                   "depMissing": True}}).encode(),
        "manifest.json": other_manifest,
    })

    settings.save({**settings.load(), "INTERFACE_SCALE": 100, "ENGINE_VOLUME": 15, "STEAM_DIR": home})
    tags.save([])
    pre = backup.preflight(old_build)
    assert not pre.get("refused"), pre
    notes = {n["kind"]: n for n in pre["notes"]}
    assert notes["newer"]["format"] == backup.FORMAT + 1, pre["notes"]
    assert backup.receipt_line(pre).endswith(" · from a newer version"), backup.receipt_line(pre)
    dropped = {(d["kind"], d["id"]): d["reason"] for d in pre["dropped"]}
    assert dropped[("theme", "preset")].startswith("retired:"), dropped
    assert dropped[("theme", "accent")].startswith("retired:"), dropped
    assert ("meta", "aaa:depMissing") in dropped, dropped
    adjusted = {(a["kind"], a["key"]): (a["from"], a["to"]) for a in pre["adjusted"]}
    assert adjusted[("clamp", "INTERFACE_SCALE")] == (400, 150), \
        f"a range this build narrowed clamps and names both numbers: {adjusted}"
    assert ("default", "ENGINE_VOLUME") not in adjusted, \
        f"a dense settings file cannot tell a choice from a default, so the value stands: {adjusted}"
    assert adjusted[("alias", "ENGINE_HWDEC")] == ("nvdec", "auto"), adjusted
    assert adjusted[("clamp", "SPEED")] == ("99", 10.0), adjusted
    assert ("overrides", "bbb", "GLOW") in {(k["store"], k["id"], k["key"]) for k in pre["preserved"]}
    assert "STEAM_DIR" in {x["key"] for x in pre["reresolved"]}, \
        "this build's own path keys are re-resolved; the manifest is not consulted"

    r = backup.apply(pre)
    assert r["errors"] == [], r["errors"]
    s = settings.load()
    assert s["DETECT_INTERVAL_SEC"] == 300 and s["ENGINE_HWDEC"] == "auto", s
    assert s["INTERFACE_SCALE"] == 150 and s["ENGINE_VOLUME"] == 42, s
    assert s["STEAM_DIR"] == home, "a path that does not exist here never lands"
    assert wp.load_set("bbb")["SPEED"] == 10.0, wp.load_set("bbb")
    assert {row["id"] for row in tags.load()} == {"bbb"}, \
        "a trashed verdict lands before the wallpaper does, or the importer re-imports it"
    assert meta.get("aaa") == {"favorite": True, "depMissing": True, "depWid": "ccc"}, \
        f"this machine's own probe keys stay exactly as they were: {meta.get('aaa')}"
    assert foreign.load()["overrides"]["bbb"]["GLOW"] == "1", foreign.load()

    # --- a choice this build lacks: the dense stores snap it and name it, the theme's
    # active key goes through the load door's check, a number that is not one is named,
    # and a dangling schedule entry switches the schedule off as the panel does
    def without(data: bytes, *keys: str) -> str:
        return "\n".join(ln for ln in data.decode().splitlines() if not ln.startswith(keys))

    odd = _doctor(base, os.path.join(home, "odd.lwebackup"), {
        "settings.conf": lambda d: (without(d, "ENGINE_VOLUME=", "SCHEDULE_ENABLED=", "SCHEDULE=", "ROTATION_ENABLED=")
                                    + "\nENGINE_VOLUME=loud\nSCHEDULE_ENABLED=true\n"
                                      "SCHEDULE=07:00=mine;19:00=gone\nROTATION_ENABLED=maybe\n").encode(),
        "theme.json": lambda d: json.dumps({**json.loads(d), "active": "neon-dream"}).encode(),
        "playlists/mine.conf": lambda d: (without(d, "MODE=") + "\nMODE=mosaic\n").encode(),
        "tags.csv": lambda d: d + b"qqq,Q,archived\n",
        "meta.json": lambda d: json.dumps({**json.loads(d), "bbb": {"favorite": {"a": 1}, "title": "T"}}).encode(),
    })
    pre4 = backup.preflight(odd)
    assert ("tags", "state") in {(a["store"], a["key"]) for a in pre4["adjusted"]} and \
        [a for a in pre4["adjusted"] if a["store"] == "tags"][0]["from"] == "archived", pre4["adjusted"]
    assert ("meta", "bbb:favorite") in {(d["kind"], d["id"]) for d in pre4["dropped"]}, pre4["dropped"]
    adj = {(a["store"], a["key"]): (a["from"], a["to"]) for a in pre4["adjusted"]}
    assert adj[("settings", "ENGINE_VOLUME")] == ("loud", 15), adj
    assert adj[("theme", "active")] == ("neon-dream", themes.DEFAULT_ACTIVE), adj
    assert adj[("playlists", "MODE")] == ("mosaic", "shuffle"), adj
    assert adj[("settings", "SCHEDULE_ENABLED")] == (True, False), adj
    assert pre4["plan"]["settings"]["SCHEDULE"] == "07:00=mine", pre4["plan"]["settings"]["SCHEDULE"]
    assert all(p["store"] != "playlists" for p in pre4["preserved"]), \
        "a dense store has nowhere to put a preserved value, so it snaps and names instead"
    r4 = backup.apply(pre4)
    assert r4["errors"] == [], r4["errors"]
    assert themes.load_config()["active"] == themes.DEFAULT_ACTIVE
    assert playlists.load("mine")["MODE"] == "shuffle"
    assert settings.load()["SCHEDULE_ENABLED"] is False and settings.load()["ENGINE_VOLUME"] == 15

    # --- a bool that is not one is named too
    assert adj[("settings", "ROTATION_ENABLED")] == ("maybe", C.SETTINGS_SCHEMA["ROTATION_ENABLED"]["default"]), adj

    # --- an archive from a build without the schedule switch: a dropped entry still turns
    # the LIVE switch off
    nosw = _doctor(base, os.path.join(home, "nosw.lwebackup"), {
        "settings.conf": lambda d: (without(d, "SCHEDULE_ENABLED=", "SCHEDULE=")
                                    + "\nSCHEDULE=07:00=gone\n").encode()})
    settings.save({**settings.load(), "SCHEDULE_ENABLED": True})
    pre5 = backup.preflight(nosw)
    assert pre5["plan"]["settings"]["SCHEDULE_ENABLED"] is False, pre5["plan"]["settings"]
    assert ("SCHEDULE_ENABLED", True, False) in {(a["key"], a["from"], a["to"]) for a in pre5["adjusted"]}

    # --- a preserved key: in the dense stores the live value wins once the build knows the
    # key; in the sparse override store it is re-emitted unless the live file carries it;
    # a key the format cannot hold is named, not raised
    wp.write_keys("zzz", {"BG": "zzz"})
    foreign.save({**foreign.load(),
                  "overrides": {"bbb": {"SPEED": "3", "GLOW": "1"}, "zzz": {"SPEED": "3"}},
                  "settings": {"settings.conf": {"MY.KEY": "x", "FUTURE_KNOB": "7"}},
                  "playlists": {"mine": {"MODE": "mosaic"}}})
    again = os.path.join(home, "again.lwebackup")
    r5 = backup.export_to(again)
    with zipfile.ZipFile(again) as z:
        bbb = tier_a.parse(z.read("wp/bbb.conf").decode())
        zzz = tier_a.parse(z.read("wp/zzz.conf").decode())
        conf = tier_a.parse(z.read("settings.conf").decode())
        mine = tier_a.parse(z.read("playlists/mine.conf").decode())
    assert bbb["SPEED"] == "1.5" and bbb["GLOW"] == "1", bbb
    assert zzz["SPEED"] == "3", zzz
    assert conf["FUTURE_KNOB"] == "7" and "MY.KEY" not in conf, conf
    assert mine["MODE"] == "shuffle", mine
    assert ("preserved-key", "MY.KEY") in {(d["kind"], d["id"]) for d in r5["dropped"]}, r5["dropped"]
    assert r5["counts"]["preserved"] == 3, r5["counts"]  # FUTURE_KNOB, zzz SPEED, bbb GLOW: what was re-emitted
    with zipfile.ZipFile(again) as z:
        m5 = json.loads(z.read("manifest.json"))
    assert m5["schemas"] == backup.schemas(), "the manifest carries exactly what schemas() returns"

    # --- a referenced folder outside the library whose name is the id still travels whole;
    # a retired key kept aside is never re-emitted
    refdir = os.path.join(home, "steam", "workshop", "777")
    os.makedirs(refdir, exist_ok=True)
    wp.write_keys("777", {"BG": refdir})
    foreign.save({**foreign.load(), "theme": {"theme.json": {"preset": "x", "glow2": "1"}}})
    r6 = backup.export_to(again)
    with zipfile.ZipFile(again) as z:
        w777 = tier_a.parse(z.read("wp/777.conf").decode())
        theme6 = json.loads(z.read("theme.json").decode())
    assert w777["BG"] == refdir, w777
    assert "preset" not in theme6 and theme6.get("glow2") == "1", theme6

    # --- a stored key whose new name the same file also carries is retired, named as such
    both = _doctor(base, os.path.join(home, "both.lwebackup"), {
        "settings.conf": lambda d: (without(d, "DETECT_INTERVAL_SEC=")
                                    + "\nDETECT_INTERVAL_MIN=5\nDETECT_INTERVAL_SEC=42\n").encode()})
    pre6 = backup.preflight(both)
    coll = [d for d in pre6["dropped"] if d["id"] == "DETECT_INTERVAL_MIN"]
    assert coll and coll[0]["reason"].startswith("retired: the file also carries"), pre6["dropped"]
    assert pre6["plan"]["settings"]["DETECT_INTERVAL_SEC"] == 42

    # --- promotion: a kept-aside key this build knows is written into its member and
    # leaves the file; a scope with no playlist is pruned; the unknown key stays
    kept0 = foreign.load()
    foreign.save({**kept0,
                  "settings": {"settings.conf": {"ENGINE_LAYER": "overlay", "FUTURE_KNOB": "7"}},
                  "overrides": {**kept0.get("overrides", {}), "zzz": {"MOUSE": "true"}},
                  "playlists": {"gone-slug": {"MODE": "shuffle"}}})
    done = foreign.promote()
    assert done == {"promoted": 3, "pruned": 1}, done  # settings, zzz, and bbb whose known SPEED left the file
    assert settings.load()["ENGINE_LAYER"] == "overlay"
    assert wp.load_set("zzz").get("MOUSE") is True, wp.load_set("zzz")
    kept1 = foreign.load()
    assert kept1["settings"] == {"settings.conf": {"FUTURE_KNOB": "7"}}, kept1
    assert "zzz" not in kept1.get("overrides", {}) and "playlists" not in kept1, kept1
    wp.update_set("zzz", {"MOUSE": None})
    r7 = backup.export_to(again)
    with zipfile.ZipFile(again) as z:
        zzz7 = tier_a.parse(z.read("wp/zzz.conf").decode())
    assert "MOUSE" not in zzz7, "a cleared key is never resurrected from what was kept aside"

    # --- a member one store cannot read holds that store with the reason; the rest restore
    unread = _doctor(base, os.path.join(home, "unread.lwebackup"), {
        "tags.csv": lambda d: d + b'"' + b"x" * 200000 + b'",t,good\n',
        "theme.json": lambda d: b"[1, 2]"})
    pre8 = backup.preflight(unread)
    assert not pre8.get("refused") and pre8["plan"].get("settings"), pre8["errors"]
    assert {e["file"] for e in pre8["errors"]} == {"tags", "theme.json"}, pre8["errors"]
    r8 = backup.apply(pre8)
    assert backup.receipt_line(r8).startswith("Restore incomplete · 2 failed (theme.json)"), backup.receipt_line(r8)
    assert playlists.load("mine")["NAME"], "the stores that could be read restored"

    # --- a live file the snapshot cannot read refuses the restore with the file named
    os.chmod(paths.wp_file("777"), 0)
    settings.save({**settings.load(), "ENGINE_LAYER": "top"})
    try:
        blocked2 = backup.apply(backup.preflight(base))
    finally:
        os.chmod(paths.wp_file("777"), 0o600)
    assert blocked2.get("refused") and "777" in blocked2["errors"][0]["reason"], blocked2["errors"]
    assert settings.load()["ENGINE_LAYER"] == "top", "a refused restore writes nothing"
    assert not list((paths.state_dir() / "backups").glob("pre-restore-*")) or \
        all(zipfile.ZipFile(p).testzip() is None for p in (paths.state_dir() / "backups").glob("pre-restore-*"))

    # --- a store whose write fails refuses the receipt: the row shows the error, not a count
    os.chmod(paths.config_dir(), 0o500)
    try:
        failed = backup.apply(backup.preflight(base))
    finally:
        os.chmod(paths.config_dir(), 0o700)
    assert failed.get("refused") and failed["errors"], failed
    assert backup.receipt_line(failed) == "", backup.receipt_line(failed)
    assert not (paths.theme_file().read_text(encoding="utf-8").find('"preset"') >= 0), \
        "a retired key never reaches the file"

    again = os.path.join(home, "again.lwebackup")
    backup.export_to(again)
    with zipfile.ZipFile(again) as z:
        assert tier_a.parse(z.read("wp/bbb.conf").decode())["GLOW"] == "1", \
            "a key this build does not know is re-emitted where it came from"

    # --- a backup with no settings at all decides the plan against the current settings
    headless = _doctor(base, os.path.join(home, "headless.lwebackup"), {}, drop=("settings.conf",))
    playlists.delete("mine")
    pre2 = backup.preflight(headless)
    assert not pre2.get("refused"), pre2
    assert {"kind": "no-settings", "member": "settings.conf"} in pre2["notes"], pre2["notes"]
    r2 = backup.apply(pre2)
    assert r2["errors"] == [], r2["errors"]
    assert playlists.load("mine")["NAME"] == "Mine", "everything else still restores"

    # --- a settings value naming a playlist nothing will leave behind
    dangling = _doctor(base, os.path.join(home, "dangling.lwebackup"), {
        "settings.conf": lambda d: d + b"\nSCHEDULE=06:00=mine;18:00=gone\n",
    }, drop=("playlists/mine.conf",))
    playlists.delete("mine")
    pre3 = backup.preflight(dangling)
    slugs = {(n["key"], n["slug"]) for n in pre3["notes"] if n["kind"] == "dangling"}
    assert ("SCHEDULE", "mine") in slugs and ("SCHEDULE", "gone") in slugs, pre3["notes"]
    assert pre3["plan"]["settings"]["SCHEDULE"] == "", pre3["plan"]["settings"]["SCHEDULE"]

    # --- five pre-restore snapshots are kept, the older ones deleted, the newest always
    # among them even when all were taken inside one second
    for _ in range(backup.SNAPSHOTS_KEPT + 2):
        last = backup.apply(backup.preflight(base))
    snaps = sorted((paths.state_dir() / "backups").glob("pre-restore-*.lwebackup"))
    assert len(snaps) == backup.SNAPSHOTS_KEPT, [p.name for p in snaps]
    newest = [n["path"] for n in last["notes"] if n["kind"] == "snapshot"][0]
    assert os.path.exists(newest), (newest, [p.name for p in snaps])

    # --- a snapshot that cannot be written abandons the whole import: nothing is written
    for stale in snaps:
        stale.unlink()
    (paths.state_dir() / "backups").rmdir()
    (paths.state_dir() / "backups").write_text("in the way\n", encoding="utf-8")
    settings.save({**settings.load(), "ENGINE_LAYER": "overlay"})
    blocked = backup.apply(backup.preflight(base))
    assert blocked.get("refused") and blocked["errors"], blocked
    assert backup.receipt_line(blocked) == "", backup.receipt_line(blocked)
    assert settings.load()["ENGINE_LAYER"] == "overlay", "an abandoned import writes nothing"
    shutil.rmtree(home, ignore_errors=True)
    print("OK backup doors: renamed and retired keys, clamps and aliases named, unknown keys "
          "preserved and re-emitted, a newer format and a missing settings member restored, "
          "snapshots kept")


def meta_tag_lists() -> None:
    """The list of tags the editor writes into meta.json travels: a list of strings, empty included, and
    the older string form are restored exactly; a list holding anything but strings is dropped and named."""
    home = tempfile.mkdtemp(prefix="lwe-backup-meta-")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
    os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
    os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
    from lwe_ui.storage import backup, meta, paths, settings

    paths.ensure_dirs()
    settings.ensure_exists()
    lib = os.path.join(home, "walls")
    for wid in ("aaa", "bbb", "ccc", "ddd"):
        os.makedirs(os.path.join(lib, wid))
    settings.save({**settings.load(), "WALLPAPERS_DIR": lib})
    meta.update("aaa", {"tags": ["space", "night"]})
    meta.update("bbb", {"tags": []})
    meta.update("ccc", {"tags": "legacy"})
    base = os.path.join(home, "base.lwebackup")
    assert backup.export_to(base)["errors"] == []
    mixed = _doctor(base, os.path.join(home, "mixed.lwebackup"), {
        "meta.json": lambda d: json.dumps({**json.loads(d), "ddd": {"tags": ["space", 1]}}).encode()})
    pre = backup.preflight(mixed)
    assert [(d["kind"], d["id"]) for d in pre["dropped"]] == [("meta", "ddd:tags")], pre["dropped"]
    assert pre["plan"]["meta"] == {"aaa": {"tags": ["space", "night"]}, "bbb": {"tags": []},
                                   "ccc": {"tags": "legacy"}}, pre["plan"]["meta"]
    meta.save({})
    r = backup.apply(pre)
    assert r["errors"] == [], r["errors"]
    assert {wid: meta.get(wid).get("tags") for wid in ("aaa", "bbb", "ccc")} == \
        {"aaa": ["space", "night"], "bbb": [], "ccc": "legacy"}, meta.load()
    assert "ddd" not in meta.load(), meta.load()
    shutil.rmtree(home, ignore_errors=True)
    print("OK backup meta tags: lists of strings and strings restored, a mixed list dropped and named")


if __name__ == "__main__":
    main()
    doors()
    meta_tag_lists()
