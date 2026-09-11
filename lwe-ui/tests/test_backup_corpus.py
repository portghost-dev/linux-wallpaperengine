"""The corpus invariant: every archive under tests/fixtures/backups/ still restores whole.

For each archive, restored into a fresh sandbox whose library holds only some of its
wallpapers: no receipt error, no Python warning, no dropped entry the RETIRED table does not
name, every value the generator wrote back verbatim (or under its new name through RENAMES),
every override's set-ness kept key for key, and the newest generation carrying every key
the current schemas define, so adding a key without regenerating the corpus fails by its
name. A plain archive must need no adjustment at all; its faulted sibling must be adjusted
and kept aside exactly as its record names, and nothing more.

The expected values are the generator's own record (tests/corpus/make_corpus.py), never a
hand-written list. EXPECTED below names the cases the shipped code cannot satisfy yet; a
case that starts passing fails this suite, so the entry is deleted with the fix.
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (must stay the first project import)
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import warnings
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "backups"

#: Findings, not licences: the case, and why the shipped code loses it.
EXPECTED: dict[tuple[str, str], str] = {}

#: receipt "kind" -> the store whose RETIRED/RENAMES table governs it, named as the Store
#: record names it
STORE_OF_KIND = {"setting": "settings", "theme": "theme", "override": "overrides",
                 "override-key": "overrides", "playlist": "playlists",
                 "playlist-key": "playlists", "playlist-members": "playlists",
                 "tag": "tags", "meta": "meta", "discovery": "discovery",
                 "rule-line": "rules", "preserved-key": "foreign"}


def _retired(store: str, key: str) -> bool:
    from lwe_ui import constants as C
    return key in C.RETIRED.get(store, {})


def _renamed(store: str, key: str):
    """The key's current name and the function that carries its value, from RENAMES."""
    from lwe_ui import constants as C
    entry = C.RENAMES.get(store, {}).get(key)
    if entry is None:
        return key, None
    if isinstance(entry, (tuple, list)):
        return entry[0], (entry[1] if len(entry) > 1 else None)
    return entry, None


def _expected(store: str, key: str, want):
    """(current name, expected value) for a stored key, or None when it is retired."""
    if _retired(store, key):
        return None
    new, fn = _renamed(store, key)
    return new, (fn(want) if fn else want)


class Run:
    """One archive's restore and the failures it produced."""

    def __init__(self, archive: Path, record: dict):
        self.archive, self.record = archive, record
        self.receipt: dict = {}
        self.failures: list[str] = []
        self.expected_hit: set[tuple[str, str]] = set()

    def fail(self, case: tuple[str, str] | None, msg: str) -> None:
        if case is not None and case in EXPECTED:
            self.expected_hit.add(case)
            return
        self.failures.append(msg)

    def passes(self, case: tuple[str, str]) -> None:
        """A case EXPECTED to fail that did not: the entry has to go."""
        if case in EXPECTED:
            self.failures.append(
                f"expected failure {case[0]}:{case[1]} no longer reproduces - delete its "
                f"EXPECTED entry ({EXPECTED[case]})")


def _sandbox(home: str, present: list[str]) -> str:
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "config")
    os.environ["XDG_STATE_HOME"] = os.path.join(home, "state")
    os.environ["XDG_DATA_HOME"] = os.path.join(home, "data")
    lib = os.path.join(home, "wallpapers")
    for wid in present:
        os.makedirs(os.path.join(lib, wid), exist_ok=True)
    from lwe_ui.storage import themes, paths, settings
    paths.ensure_dirs()
    settings.ensure_exists()
    settings.save({**settings.load(), "WALLPAPERS_DIR": lib})
    return lib


def _check_settings(run: Run) -> None:
    from lwe_ui import constants as C
    from lwe_ui.storage import settings
    got = settings.load()
    machine = {k for k, s in C.SETTINGS_SCHEMA.items() if s["type"] == "path"}
    reresolved = {x["key"] for x in run.receipt.get("reresolved", [])}
    for key, want in run.record["settings"].items():
        exp = _expected("settings", key, want)
        if exp is None:
            continue
        name, value = exp
        if key in machine:
            if got.get(name) != value and name not in reresolved:
                run.fail(None, f"settings {name}: this machine's value {got.get(name)!r} "
                               f"replaced {value!r} without saying so in reresolved")
            continue
        if name not in got:
            run.fail(("setting", key), f"settings {name}: gone after the restore (wrote {value!r})")
        elif got[name] != value:
            run.fail(("setting", key),
                     f"settings {name}: wrote {value!r}, restored {got[name]!r}")


def _check_theme(run: Run) -> None:
    from lwe_ui.storage import themes
    got = {k: v for k, v in themes.load_config().items() if k in themes.CONFIG_KEYS}
    for key, want in run.record["theme"].items():
        exp = _expected("theme", key, want)
        if exp is None:
            continue
        name, value = exp
        if name not in got:
            run.fail(("theme", key), f"theme {name}: gone after the restore (wrote {value!r})")
        elif got[name] != value:
            run.fail(("theme", key), f"theme {name}: wrote {value!r}, restored {got[name]!r}")
        else:
            run.passes(("theme", key))


def _check_discovery(run: Run) -> None:
    from lwe_ui.storage import discover_cfg
    got = discover_cfg.load()
    for key, want in run.record.get("discovery", {}).items():
        exp = _expected("discovery", key, want)
        if exp is None:
            continue
        name, value = exp
        if got.get(name) != value:
            run.fail(("discovery", key), f"discovery {name}: wrote {value!r}, restored {got.get(name)!r}")
        else:
            run.passes(("discovery", key))


def _check_playlists(run: Run) -> None:
    from lwe_ui.storage import playlists
    for slug, want in run.record["playlists"].items():
        got = playlists.load(slug)
        for key, value in want.items():
            exp = _expected("playlists", key, value)
            if exp is None:
                continue
            name, expect = exp
            if got.get(name) != expect:
                run.fail(("playlist", slug),
                         f"playlist {slug} {name}: wrote {expect!r}, restored {got.get(name)!r}")


def _check_overrides(run: Run) -> None:
    from lwe_ui.storage import paths, tier_a
    for wid, entry in run.record["overrides"].items():
        want = {}
        for key, value in entry["keys"].items():
            exp = _expected("overrides", key, value)
            if exp is not None:
                want[exp[0]] = exp[1]
        path = paths.wp_file(wid)
        if not path.exists():
            run.fail(("override", wid), f"override {wid} ({entry['kind']}): no file after the "
                                        f"restore, {len(want)} keys lost")
            continue
        got = tier_a.parse(path.read_text(encoding="utf-8"))
        for key in sorted(set(want) - set(got)):
            run.fail(("override-key", f"{wid}:{key}"),
                     f"override {wid} ({entry['kind']}) {key}: gone, wrote {want[key]!r}")
        for key in sorted(set(got) - set(want)):
            run.fail(("override-key", f"{wid}:{key}"),
                     f"override {wid} ({entry['kind']}) {key}: appeared as {got[key]!r}, the "
                     f"file never carried it (set-ness broken)")
        for key in sorted(set(got) & set(want)):
            if got[key] != want[key]:
                run.fail(("override-key", f"{wid}:{key}"),
                         f"override {wid} ({entry['kind']}) {key}: wrote {want[key]!r}, "
                         f"restored {got[key]!r}")


def _check_tags(run: Run) -> None:
    from lwe_ui.storage import tags
    got = {r["id"]: r for r in tags.load() if r.get("id")}
    held = sum(h.get("count", 0) for h in run.receipt.get("held", []) if h["kind"] == "tags")
    present = set(run.record["library_present"])
    for row in run.record["tags"]:
        wid = row["id"]
        if wid not in got:
            if wid in present or not held:
                run.fail(("tag", wid), f"tag {wid} ({row['state']}): gone after the restore and "
                                       f"not held")
            continue
        for field in ("title", "state"):
            if got[wid].get(field) != row[field]:
                run.fail(("tag", wid), f"tag {wid} {field}: wrote {row[field]!r}, restored "
                                       f"{got[wid].get(field)!r}")


def _check_meta(run: Run) -> None:
    from lwe_ui.storage import meta
    got = meta.load()
    for wid, entry in run.record["meta"].items():
        for key, value in entry.items():
            if got.get(wid, {}).get(key) != value:
                run.fail(("meta", wid), f"meta {wid} {key}: wrote {value!r}, restored "
                                        f"{got.get(wid, {}).get(key)!r}")


def _check_rules(run: Run) -> None:
    from lwe_ui.storage import paths
    named = {str(d.get("id")) for d in run.receipt.get("dropped", []) if d.get("kind") == "rule-line"}
    for name, lines in run.record["rules"].items():
        path = paths.config_dir() / name
        got = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        for line in lines:
            if line in got:
                continue
            if not line.isprintable() and f"{name}:{line!r}" in named:
                continue   # a line the file cannot hold, named in the receipt
            run.fail(("rules", name),
                     f"rule {name}: line {line!r} did not survive the restore and the receipt "
                     f"does not name it")


def _check_dropped(run: Run) -> None:
    """A KEY may be dropped only where RETIRED names it, or when it is the old name of a
    renamed key whose new name the same file carries. A rule LINE has no key and no table:
    it may be dropped only when the file cannot hold it, which _check_rules pairs with the
    line the generator wrote."""
    for d in run.receipt.get("dropped", []):
        kind, ident = d.get("kind", "?"), str(d.get("id", "?"))
        if kind == "rule-line":
            continue
        if str(d.get("reason", "")).startswith("retired: the file also carries"):
            continue  # a stored key whose new name the file also carries: named, legal
        store = STORE_OF_KIND.get(kind, kind)
        key = ident.split(":", 1)[1] if ":" in ident and kind != "tag" else ident
        if _retired(store, key):
            continue
        run.fail((kind, ident), f"dropped {kind} {ident}: {d.get('reason')} - a drop is legal "
                                f"only where constants.RETIRED[{store!r}] names the key")


def _check_faults(run: Run) -> None:
    """A faulted archive must be adjusted and kept aside exactly as its record names: every
    named fault appears on the receipt and nothing else does; the values then come back
    as the record says (the other checks)."""
    faults = run.record["faults"]
    got_adj = {(a.get("kind"), a.get("store"), a.get("key")): a for a in run.receipt.get("adjusted", [])}
    for f in faults["adjusted"]:
        a = got_adj.pop((f["kind"], f["store"], f["key"]), None)
        if a is None:
            run.fail(None, f"faulted {f['store']} {f['key']}: expected a {f['kind']} on the receipt, none")
        elif str(a.get("to")) != str(f["to"]) or str(a.get("from")) != str(f["from"]):
            run.fail(None, f"faulted {f['store']} {f['key']}: {f['kind']} named {a.get('from')!r} -> "
                           f"{a.get('to')!r}, the record says {f['from']!r} -> {f['to']!r}")
    for a in got_adj.values():
        run.fail(None, f"adjusted {a.get('kind')} {a.get('store')} {a.get('key')} beyond the faults the record names")
    got_kept = {(p.get("store"), p.get("id"), p.get("key")) for p in run.receipt.get("preserved", [])}
    want_kept = {(p["store"], p["id"], p["key"]) for p in faults["preserved"]}
    for missing in sorted(want_kept - got_kept):
        run.fail(None, f"faulted {missing}: expected kept aside, not on the receipt")
    for extra in sorted(got_kept - want_kept):
        run.fail(None, f"kept aside {extra} beyond the faults the record names")


def _check_adjusted(run: Run) -> None:
    """The generator wrote every value through the live stores, so nothing in an archive of
    this generation can need a clamp, a snap, an alias or a preserving."""
    if run.record.get("faulted"):
        _check_faults(run)
        return
    for a in run.receipt.get("adjusted", []):
        run.fail(None, f"adjusted {a.get('kind')} {a.get('store')} {a.get('id')} "
                       f"{a.get('key')}: {a.get('from')!r} -> {a.get('to')!r} - a value this "
                       f"build wrote must restore verbatim")
    for a in run.receipt.get("preserved", []):
        run.fail(None, f"preserved {a.get('store')} {a.get('id')} {a.get('key')} - this "
                       f"build's own key reads as foreign")
    for n in run.receipt.get("notes", []):
        if n.get("kind") != "snapshot":
            run.fail(None, f"note {n} - an archive of this generation needs no note but the "
                           f"pre-restore snapshot")


def _check_newest_covers_schema(records: dict[Path, dict], failures: list[str]) -> None:
    """The newest generation must carry every key the current schemas define."""
    from lwe_ui import constants as C
    from lwe_ui.storage import tags, tier_a
    newest = max(r["generated"] for r in records.values())
    for archive, record in sorted(records.items()):
        if record["generated"] != newest:
            continue
        with zipfile.ZipFile(archive) as z:
            names = set(z.namelist())
            have = {"settings": set(tier_a.parse(z.read("settings.conf").decode("utf-8"))),
                    "overrides": set(), "playlists": set(), "theme": set(), "discovery": set(),
                    "tag_states": set()}
            for n in names:
                if n.startswith("wp/"):
                    have["overrides"] |= set(tier_a.parse(z.read(n).decode("utf-8")))
                elif n.startswith("playlists/"):
                    have["playlists"] |= set(tier_a.parse(z.read(n).decode("utf-8")))
            if "theme.json" in names:
                have["theme"] = set(json.loads(z.read("theme.json").decode("utf-8")))
            if "discover.json" in names:
                have["discovery"] = set(json.loads(z.read("discover.json").decode("utf-8")))
            if "tags.csv" in names:
                rows = csv.DictReader(io.StringIO(z.read("tags.csv").decode("utf-8")))
                have["tag_states"] = {str(r.get("state") or "").strip() for r in rows}
        from lwe_ui.storage import themes
        wanted = {"settings": set(C.SETTINGS_SCHEMA), "overrides": set(C.WP_SCHEMA),
                  "playlists": set(C.PLAYLIST_SCHEMA), "theme": set(themes.CONFIG_KEYS),
                  "discovery": set(C.DISCOVER_DEFAULTS), "tag_states": set(tags._VALID_STATES)}
        for store, keys in wanted.items():
            missing = sorted(k for k in keys - have[store] if not _retired(store, k))
            if missing:
                failures.append(f"{archive.name}: the newest corpus does not carry "
                                f"{store} {', '.join(missing)} - regenerate it with "
                                f"PYTHONPATH=src python3 tests/corpus/make_corpus.py")


def main() -> None:
    archives = sorted(FIXTURES.glob("*.lwebackup"))
    if not archives:
        raise SystemExit(f"no corpus archives under {FIXTURES}; generate them with "
                         f"PYTHONPATH=src python3 tests/corpus/make_corpus.py")
    from lwe_ui.storage import backup

    failures: list[str] = []
    expected_hit: set[tuple[str, str]] = set()
    records: dict[Path, dict] = {}
    for archive in archives:
        side = archive.with_suffix(".json")
        if not side.exists():
            failures.append(f"{archive.name}: no {side.name} beside it; every archive carries "
                            f"the generator's record of what was written")
            continue
        record = json.loads(side.read_text(encoding="utf-8"))
        records[archive] = record
        run = Run(archive, record)
        home = tempfile.mkdtemp(prefix="lwe-corpus-restore-")
        _sandbox(home, record["library_present"])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            pre = backup.preflight(str(archive))
            run.receipt = backup.apply(pre)
        for w in caught:
            run.fail(None, f"warning during the restore: {w.category.__name__}: {w.message}")
        for e in run.receipt.get("errors", []):
            run.fail(None, f"receipt error on {e.get('file')}: {e.get('reason')}")
        if not run.receipt.get("held"):
            run.fail(None, "nothing was held: this archive no longer exercises the path where "
                           "the library is missing wallpapers the backup carries")
        _check_dropped(run)
        _check_adjusted(run)
        _check_settings(run)
        _check_theme(run)
        _check_discovery(run)
        _check_playlists(run)
        _check_overrides(run)
        _check_tags(run)
        _check_meta(run)
        _check_rules(run)
        expected_hit |= run.expected_hit
        failures += [f"{archive.name}: {f}" for f in run.failures]
        shutil.rmtree(home, ignore_errors=True)

    plain = {a: r for a, r in records.items() if not r.get("faulted")}
    if plain:
        _check_newest_covers_schema(plain, failures)
    for case in sorted(set(EXPECTED) - expected_hit):
        failures.append(f"expected failure {case[0]}:{case[1]} never reproduced - delete its "
                        f"EXPECTED entry ({EXPECTED[case]})")
    if failures:
        print(f"corpus invariant broken, {len(failures)} finding(s):")
        for f in failures:
            print("  " + f)
        raise SystemExit(1)
    known = ", ".join(f"{a}:{b}" for a, b in sorted(EXPECTED)) or "none"
    faulted = sum(1 for r in records.values() if r.get("faulted"))
    print(f"OK backup corpus: {len(archives)} archive(s) restored whole ({faulted} faulted, adjusted "
          f"exactly as recorded); known losses still pinned: {known}")


if __name__ == "__main__":
    main()
