"""Build the backup corpus from the live schemas. Nothing here is hand-written.

Run from lwe-ui/:  PYTHONPATH=src python3 tests/corpus/make_corpus.py [outdir]

Each run writes one archive per variant into tests/fixtures/backups/ and, beside it, the
JSON of the values that were written - the only "expected" file the corpus has, produced
here, so a green run cannot be had by editing an expected list. A settings key holds one
value per file, so the coverage the invariant needs (every enum choice, both bool values,
both bounds) is spread across the variants; the wallpapers and playlists inside a single
archive cover the override and playlist schemas.

Every archive keeps its schema hash in its name. A schema change means a new hash: the new
corpus is generated and the old archives stay, because they must keep restoring.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from lwe_ui import constants as C  # noqa: E402
from lwe_ui.storage import backup, discover_cfg, meta, paths, playlists, settings, tags  # noqa: E402
from lwe_ui.storage import rules as rules_store  # noqa: E402
from lwe_ui.storage import themes, tier_a  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "backups"

DENSE = "corpus-dense"
COVER = "corpus-cover"
SPARSE = "corpus-sparse"
HELD = "corpus-held"
REF = "corpus-ref"
ABSENT = "corpus-absent"          # a playlist member with no folder and no override
PRESENT = (DENSE, COVER, SPARSE)  # the wallpapers a restoring library is given
PL_COVER = "corpus-cover"
PL_MISSING = "corpus-missing"
# a title the CSV and the Tier A quoting both have to carry
UNICODE_TITLE = "Rain, Ōsaka night"


def schema_snapshot() -> dict[str, Any]:
    return {
        "settings": C.SETTINGS_SCHEMA,
        "wp": C.WP_SCHEMA,
        "wp_prop_prefix": C.WP_PROP_PREFIX,
        "playlists": C.PLAYLIST_SCHEMA,
        "theme": {k: None for k in themes.CONFIG_KEYS},
        "discovery": {k: None for k in C.DISCOVER_DEFAULTS},
        "tag_states": list(tags._VALID_STATES),
    }


def schema_hash() -> str:
    """A short hash of the schemas' content: the corpus generation this archive belongs to."""
    text = json.dumps(schema_snapshot(), sort_keys=True, default=list)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def ring(spec: dict) -> list | None:
    """Every value of this key the corpus must show, in a stable order: each enum choice
    (empty last where the key is optional), both bool values, both bounds. None for a free
    string, which has no ring and takes a value from the caller's table instead."""
    t = spec["type"]
    if t == "enum":
        return list(spec["choices"])
    if t == "enum_or_empty":
        return list(spec["choices"]) + [""]
    if t == "bool":
        return [False, True]
    if t == "bool_or_empty":
        return [False, True, ""]
    if t in ("int", "int_or_empty", "float"):
        num = float if t == "float" else int
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and hi is not None:
            mid = round((lo + hi) / 2, 3) if t == "float" else (lo + hi) // 2
            vals: list = [num(lo), num(hi), num(mid), num(mid)]
        else:
            d = spec.get("default")
            base = num(d) if isinstance(d, (int, float)) and not isinstance(d, bool) else num(1)
            vals = [base + num(1), base + num(7), base + num(42), base + num(13)]
        if t == "int_or_empty":
            vals = vals[:3] + [""]   # empty is this type's "let the engine decide"
        return vals
    return None


def non_default(spec: dict):
    for v in ring(spec) or ():
        if v != spec["default"]:
            return v
    return None


def value_for(store: str, key: str, spec: dict, i: int, free: dict, *, dense: bool = False):
    """The value this variant writes for one schema key. A key whose type has no ring must
    be named in `free`, so a new free-string key fails the generator by name."""
    r = ring(spec)
    if r is None:
        if key not in free:
            raise SystemExit(f"make_corpus: no corpus value for {store} key {key} "
                             f"(type {spec['type']}); give it one in the caller's table")
        return free[key]
    if dense:
        v = non_default(spec)
        if v is not None:
            return v
    return r[i % len(r)]


def variants() -> int:
    """One archive per pass over the longest ring: enough files to show every value."""
    n = 2
    for schema in (C.SETTINGS_SCHEMA, C.WP_SCHEMA, C.PLAYLIST_SCHEMA):
        for spec in schema.values():
            r = ring(spec)
            if r:
                n = max(n, len(r))
    return n


def _text(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    return "" if val is None else str(val)


def _override(wid: str, flat: dict[str, str]) -> dict[str, str]:
    """Write one override file exactly as these keys and read back what landed."""
    path = paths.wp_dir() / f"{wid}.conf"
    path.write_text(tier_a.serialize(flat, header=f"lwe wallpaper override {wid} (Tier A)"),
                    encoding="utf-8")
    return tier_a.parse(path.read_text(encoding="utf-8"))


def build_variant(i: int, home: str) -> tuple[str, dict[str, Any]]:
    """Write a whole machine's configuration into `home`, export it, return the archive
    path and the record of every value that was written."""
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "config")
    os.environ["XDG_STATE_HOME"] = os.path.join(home, "state")
    os.environ["XDG_DATA_HOME"] = os.path.join(home, "data")
    lib = os.path.join(home, "wallpapers")
    for wid in PRESENT:
        os.makedirs(os.path.join(lib, wid), exist_ok=True)
    for name in ("assets", "workshop", "steam"):
        os.makedirs(os.path.join(home, name), exist_ok=True)
    engine_bin = os.path.join(home, "lwe-engine")
    Path(engine_bin).write_text("#!/bin/sh\n", encoding="utf-8")
    paths.ensure_dirs()
    settings.ensure_exists()

    free = {"SCHEDULE": f"06:00={PL_COVER};18:30={PL_MISSING}", "ACTIVE_PLAYLIST": PL_COVER,
            "ENGINE_BIN": engine_bin, "ASSETS_DIR": os.path.join(home, "assets"),
            "WALLPAPERS_DIR": lib, "WORKSHOP_DIR": os.path.join(home, "workshop"),
            "STEAM_DIR": os.path.join(home, "steam")}
    want = {k: value_for("settings", k, spec, i, free) for k, spec in C.SETTINGS_SCHEMA.items()}
    settings.save(want)
    got_settings = settings.load()
    for k, v in want.items():
        if got_settings[k] != v:
            raise SystemExit(f"make_corpus: settings {k}={v!r} did not survive its own "
                             f"store ({got_settings[k]!r}); the corpus cannot pin it")

    theme = {"active": themes.CUSTOM_KEYS[0],
             "overlays": {themes.CUSTOM_KEYS[0]: {"accent": "#FF2E97", "border": "#2C2E31"}}}
    themes.save_config(theme)
    got_theme = {k: v for k, v in themes.load_config().items() if k in themes.CONFIG_KEYS}

    discovery = {"apiKey": f"KEY-{i}-corpus", "acquireMethod": "steamcmd" if i % 2 else "client",
                 "steamcmdPath": os.path.join(home, "steamcmd")}
    discover_cfg.save(discovery)
    got_discovery = discover_cfg.load()
    if got_discovery != discovery:
        raise SystemExit(f"make_corpus: discovery did not survive its own store: {got_discovery}")

    out_playlists: dict[str, dict] = {}
    for slug, name, members in ((PL_COVER, UNICODE_TITLE, " ".join((*PRESENT, HELD))),
                                (PL_MISSING, "Missing member", f"{DENSE} {ABSENT}")):
        d = {k: value_for("playlists", k, spec, i, {"NAME": name, "MEMBERS": members})
             for k, spec in C.PLAYLIST_SCHEMA.items()}
        playlists.save(slug, d)
        back = playlists.load(slug)
        if back != d:
            raise SystemExit(f"make_corpus: playlist {slug} did not survive its own store: "
                             f"{d} -> {back}")
        out_playlists[slug] = back

    props = {f"{C.WP_PROP_PREFIX}hue": "0.35", f"{C.WP_PROP_PREFIX}label": "Ōsaka, night"}

    def full(wid: str, *, dense: bool) -> dict[str, str]:
        free_wp = {"BG": wid, "CC": "0.8 1.2 1.1 0.25", "SKIP": "obj-a obj-b"}
        flat = {k: _text(value_for("wp", k, spec, i, free_wp, dense=dense))
                for k, spec in C.WP_SCHEMA.items()}
        return {**flat, **props}

    overrides = {
        DENSE: {"kind": "dense", "keys": _override(DENSE, full(DENSE, dense=True))},
        COVER: {"kind": "cover", "keys": _override(COVER, full(COVER, dense=False))},
        SPARSE: {"kind": "sparse", "keys": _override(SPARSE, {"BG": SPARSE, "SPEED": "2.5"})},
        HELD: {"kind": "held", "keys": _override(HELD, {"BG": HELD, "MOUSE": "true",
                                                        "VOLUME": "42"})},
        # a folder outside the library: it must travel as it is, so it names a directory
        # that exists on any machine
        REF: {"kind": "reference", "keys": _override(REF, {"BG": tempfile.gettempdir(),
                                                           "TYPE": "video", "SPEED": "1.5"})},
    }

    rows = [{"id": DENSE, "title": UNICODE_TITLE, "state": "good"},
            {"id": COVER, "title": "Cover", "state": "review"},
            {"id": SPARSE, "title": "Sparse", "state": "bad"},
            {"id": HELD, "title": "Held, absent", "state": "bad"},
            {"id": REF, "title": "Reference", "state": "good"}]
    if len(tags._VALID_STATES) != len({r["state"] for r in rows}):
        raise SystemExit(f"make_corpus: tag states {tags._VALID_STATES} are not all in the corpus")
    tags.save_rows(rows)
    m = {DENSE: {"favorite": True, "note": "kept, with a comma"},
         SPARSE: {"favorite": False}, HELD: {"favorite": True}}
    meta.save(m)

    rules = {name: [f"{name.split('.')[0]}-plain.exe", "tabbed\tline.exe"]
             for name in rules_store.FILES}
    for name, lines in rules.items():
        (paths.config_dir() / name).write_text("".join(ln + "\n" for ln in lines), encoding="utf-8")

    archive = os.path.join(home, "corpus.lwebackup")
    r = backup.export_to(archive)
    if r["errors"]:
        raise SystemExit(f"make_corpus: export reported errors: {r['errors']}")

    record = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "schema_hash": schema_hash(),
        "schema_keys": {"settings": list(C.SETTINGS_SCHEMA), "wp": list(C.WP_SCHEMA),
                        "playlists": list(C.PLAYLIST_SCHEMA), "theme": list(themes.CONFIG_KEYS),
                        "discovery": list(C.DISCOVER_DEFAULTS),
                        "tag_states": list(tags._VALID_STATES)},
        "variant": i,
        "variants": variants(),
        "library_present": list(PRESENT),
        "settings": got_settings,
        "theme": got_theme,
        "discovery": got_discovery,
        "playlists": out_playlists,
        "overrides": overrides,
        "tags": rows,
        "meta": m,
        "rules": rules,
    }
    return archive, record


def main(out_dir: str | Path | None = None) -> list[Path]:
    out = Path(out_dir) if out_dir else FIXTURES
    out.mkdir(parents=True, exist_ok=True)
    keep = {k: os.environ.get(k) for k in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
    written: list[Path] = []
    try:
        for i in range(variants()):
            home = tempfile.mkdtemp(prefix="lwe-corpus-")
            try:
                archive, record = build_variant(i, home)
                stem = f"corpus-{record['schema_hash']}-{i}"
                shutil.copyfile(archive, out / f"{stem}{backup.EXTENSION}")
                (out / f"{stem}.json").write_text(
                    json.dumps(record, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8")
                written += [out / f"{stem}{backup.EXTENSION}", out / f"{stem}.json"]
            finally:
                shutil.rmtree(home, ignore_errors=True)
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return written


if __name__ == "__main__":
    for p in main(sys.argv[1] if len(sys.argv) > 1 else None):
        print(p)
