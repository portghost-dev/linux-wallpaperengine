"""Store ownership: no config file the backup can forget.

Every writer that puts a file under config_dir() is run in a sandbox, then every file it
left must be claimed by exactly one Store in the registry or by exactly one NOT_BACKED_UP
entry, and every path a Store declares must match a file some writer produced. The test is
proved sensitive both ways: a file nobody claims fails, and dropping any single store from
the registry orphans at least one file. Also the round trip for the two stores the hand
list forgot: the live theme store and discover.json.
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket and the host probes before any lwe_ui import)
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))


def _claimed_by(rel: str, stores, tables) -> int:
    from lwe_ui.storage import registry
    n = sum(1 for s in stores if any(registry.matches(rel, p) for p in s.owns))
    return n + sum(1 for p in tables if registry.matches(rel, p))


def _reexport(backup, home: str, name: str) -> str:
    out = os.path.join(home, f"{name}.lwebackup")
    r = backup.export_to(out)
    assert r["errors"] == [], r["errors"]
    return out


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-ownership-")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
    os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
    os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
    from lwe_ui.engine import daemon_unit
    from lwe_ui.storage import (atomic, backup, discover_cfg, foreign, meta, paths, playlists,
                                registry, rules, settings, tags, themes, wp)

    lib = os.path.join(home, "walls")
    os.makedirs(os.path.join(lib, "111"))

    # --- every writer that owns a file under the config dir
    paths.ensure_dirs()
    settings.ensure_exists()
    settings.save({**settings.load(), "WALLPAPERS_DIR": lib})
    playlists.save("mine", {"NAME": "Mine", "MODE": "shuffle", "INTERVAL": 600, "UNIT": "min",
                            "MEMBERS": "111"})
    playlists.set_active("mine")
    playlists.delete(playlists.create("Gone"))              # tombstone under legacy/playlists
    wp.write_keys("111", {"BG": "111", "SPEED": "2.5"})
    tags.set_state("111", "One", "good")
    meta.update("111", {"favorite": True})
    themes.save_config({"active": "dark", "overlays": {"dark": {"accent": "#123456"}}})
    discover_cfg.save({"apiKey": "KEY-abc", "acquireMethod": "steamcmd",
                       "steamcmdPath": "/usr/bin/steamcmd"})
    for name in rules.FILES:
        rules.save(name, "game.exe\n")
    foreign.save({"settings": {"settings.conf": {"FUTURE_KNOB": "1"}}})
    # the generated engine env, written without touching systemd
    atomic.atomic_write_text(paths.config_dir() / daemon_unit.ENV_FILE_NAME,
                             daemon_unit.build_env_content(["DP-1"], None))

    cfg = paths.config_dir()
    files = sorted(p.relative_to(cfg).as_posix() for p in cfg.rglob("*") if p.is_file())
    assert len(files) >= 10, files

    # --- exactly one claim per file, in one table or the other
    for rel in files:
        assert len(registry.claims(rel)) == 1, f"{rel}: {registry.claims(rel)}"
    for pattern, reason in registry.NOT_BACKED_UP.items():
        assert isinstance(reason, str) and reason.strip(), pattern

    # --- a store that claims nothing is a stale entry
    for st in registry.STORES:
        for pattern in st.owns:
            assert any(registry.matches(rel, pattern) for rel in files), \
                f"store {st.name} declares {pattern}, which no writer produced"

    # --- every storage module that writes must declare a Store or be exempt with a reason,
    # so a new module with a writer cannot be added and registered nowhere
    import importlib, pkgutil
    from lwe_ui import storage as storage_pkg
    from lwe_ui.storage.store import Store
    writer_names = ("save", "save_config", "save_rows", "write_keys", "update", "set_state", "ensure_exists")
    # migrate has an apply_tables, not a writer; it is named below with the other helpers
    exempt = {
        "foreign": "no member of its own: what it holds travels inside the members it came from (registry.CARRIED_INSIDE)",
        "atomic": "primitive writers every store uses",
        "tier_a": "serialiser, writes nothing itself",
        "paths": "creates directories only",
        "theme_cfg": "retired store; only the token resolver reads it and only a test writes it",
        "records": "state dir, not config",
        "records_view": "reads records",
        "tombstones": "state dir, migrated",
        "wizard": "state dir",
        "bench_verdict": "state dir",
        "importer": "writes through the override store",
        "backup": "the archive, not a store",
        "registry": "the table itself",
        "store": "the record type",
        "migrate": "applies the rename tables, writes nothing itself",
    }
    for info in pkgutil.iter_modules(storage_pkg.__path__):
        mod = importlib.import_module(f"lwe_ui.storage.{info.name}")
        writes = any(callable(getattr(mod, n, None)) for n in writer_names)
        declares = isinstance(getattr(mod, "BACKUP", None), Store)
        if writes and not declares:
            assert info.name in exempt, \
                f"storage.{info.name} has a writer and declares no Store; register it or exempt it with a reason"
        if declares:
            assert mod.BACKUP in registry.STORES, f"storage.{info.name} declares a Store the registry does not list"
    for name, reason in exempt.items():
        assert importlib.util.find_spec(f"lwe_ui.storage.{name}") is not None, f"stale exemption: {name}"
        assert reason.strip(), name

    # --- every NOT_BACKED_UP entry is produced by something, or it is a stale exemption
    for pattern in registry.NOT_BACKED_UP:
        assert any(registry.matches(rel, pattern) for rel in files), \
            f"NOT_BACKED_UP names {pattern}, which no writer produced"

    # --- sensitivity: a file nobody claims, and any single store removed
    stray = cfg / "stray.json"
    stray.write_text("{}", encoding="utf-8")
    assert registry.claims("stray.json") == [], "an unclaimed file must fail the check"
    stray.unlink()
    assert registry.STORES[0].name == "settings", "every later plan is decided against the settings"
    assert registry.claims("foreign.json") == ["carried inside: foreign.json"], registry.claims("foreign.json")
    tables = {**registry.CARRIED_INSIDE, **registry.NOT_BACKED_UP}
    for st in registry.STORES:
        others = tuple(s for s in registry.STORES if s is not st)
        orphans = [rel for rel in files
                   if _claimed_by(rel, others, tables) != 1]
        assert orphans, f"dropping the {st.name} store left every file claimed"

    # --- the two stores the hand list forgot travel and come back
    out = os.path.join(home, "rt.lwebackup")
    r = backup.export_to(out)
    assert r["errors"] == [], r["errors"]
    assert r["counts"]["theme"] == 1 and r["counts"]["discovery"] == 1, r["counts"]
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert {"theme.json", "discover.json"} <= names, names
        assert daemon_unit.ENV_FILE_NAME not in names, "the generated env file never travels"
        theme = json.loads(z.read("theme.json"))
        assert theme == {"active": "dark", "overlays": {"dark": {"accent": "#123456"}}}, theme
        assert "preset" not in theme, "the live theme store travels, not the retired one"
        assert json.loads(z.read("discover.json"))["apiKey"] == "KEY-abc", "the API key travels"

    themes.save_config({"active": themes.DEFAULT_ACTIVE, "overlays": {}})
    discover_cfg.save({})
    r2 = backup.import_from(out)
    assert r2["errors"] == [], r2["errors"]
    assert themes.load_config() == {"active": "dark", "overlays": {"dark": {"accent": "#123456"}}}, \
        themes.load_config()
    back = discover_cfg.load()
    assert back["apiKey"] == "KEY-abc" and back["acquireMethod"] == "steamcmd", back

    # --- an unknown key in either store is dropped and named, never written
    doctored = os.path.join(home, "doctored.lwebackup")
    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(doctored, "w") as zout:
        for n in zin.namelist():
            data = zin.read(n)
            if n in ("theme.json", "discover.json"):
                d = json.loads(data)
                d["glow"] = True
                data = json.dumps(d).encode()
            zout.writestr(n, data)
    pre = backup.preflight(doctored)
    kept = {(d["store"], d["key"]) for d in pre["preserved"]}
    assert ("theme", "glow") in kept and ("discovery", "glow") in kept, pre["preserved"]
    backup.apply(pre)
    assert "glow" not in json.loads(paths.theme_file().read_text(encoding="utf-8")), "the file itself carries no unknown key"
    assert foreign.load()["theme"]["theme.json"]["glow"] is True, foreign.load()
    with zipfile.ZipFile(_reexport(backup, home, "again")) as z:
        assert json.loads(z.read("theme.json"))["glow"] is True, "the preserved key is re-emitted"
    print("OK store ownership: every config file claimed once, theme and discovery travel, "
          "an unknown key is preserved and re-emitted")


if __name__ == "__main__":
    main()
