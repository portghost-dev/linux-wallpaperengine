"""meta.json - per-wallpaper app metadata keyed by id (favourites, titles, tags, notes, and
what the importer derived about this machine's copy).

Tier B (JSON). The schema is open/free-form; this module only guarantees a dict keyed by id.
The backup carries the USER_META keys alone, by allowlist: an unknown key is dropped by name,
not preserved, since an open map has no schema to say what it was.
"""
from __future__ import annotations

import json
import zipfile
from typing import Any, Callable

from . import atomic, lock, paths
from .store import Store


def load() -> dict[str, dict]:
    """Whole meta map keyed by id. Missing/corrupt file -> {}."""
    data = atomic.read_json(paths.meta_file(), default={})
    return data if isinstance(data, dict) else {}


def save(d: dict[str, dict]) -> None:
    atomic.atomic_write_json(paths.meta_file(), d)


def get(id: str) -> dict:
    """Metadata for one id (empty dict if none)."""
    entry = load().get(id)
    return entry if isinstance(entry, dict) else {}


# update is load-modify-save; the import worker (dep markers) and the GUI thread
# (verdicts, favorites) both call it - same hazard tags.py locked, same lock (B10 M2)
_WRITE_LOCK = __import__("threading").Lock()


def update(id: str, patch: dict[str, Any]) -> None:
    """Merge `patch` into the entry for `id` and atomically save the whole map.
    Thread-safe."""
    with _WRITE_LOCK, lock.held("meta"):
        _update_locked(id, patch)


def modify(id: str, fn: Callable[[dict[str, Any]], dict[str, Any] | None]) -> dict[str, Any]:
    """Under the meta locks: fn receives a copy of the entry for `id`, read fresh, and returns
    the keys to merge into it; the map is saved only when there are any. Returns the entry as
    it stands after the call. Thread-safe."""
    with _WRITE_LOCK, lock.held("meta"):
        data = load()
        entry = data.get(id)
        entry = dict(entry) if isinstance(entry, dict) else {}
        patch = fn(dict(entry)) or {}
        if patch:
            entry.update(patch)
            data[id] = entry
            save(data)
        return entry


def _update_locked(id: str, patch: dict[str, Any]) -> None:
    data = load()
    entry = data.get(id)
    if not isinstance(entry, dict):
        entry = {}
    entry.update(patch)
    data[id] = entry
    save(data)


# --- backup ---------------------------------------------------------------------------
MEMBER = "meta.json"
#: the entry keys a person authored: these travel
USER_META = ("favorite", "tags", "title", "note")
#: what a probe or the importer derived about THIS machine's copy: these stay behind
MACHINE_META = ("depMissing", "depWid", "depName", "resolution")
_TYPES = {"favorite": bool, "tags": str, "title": str, "note": str}


def _user_keys(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k in USER_META}


def _backup_export(z: zipfile.ZipFile, r: dict[str, Any]) -> None:
    m = {k: _user_keys(v) for k, v in load().items() if isinstance(v, dict)}
    m = {k: v for k, v in m.items() if v}
    z.writestr(MEMBER, json.dumps(m, indent=1))
    r["counts"]["favourites"] = sum(1 for v in m.values() if v.get("favorite"))


def _backup_preflight(z: zipfile.ZipFile, r: dict[str, Any], plan: dict[str, Any],
                      cfg_after: dict[str, Any]) -> bool:
    """User meta is written whether or not the wallpaper is here yet, the rule overrides
    follow, and counted as held while it is absent. A key a probe wrote on the other machine
    describes that machine's copy and is dropped by name."""
    out: dict[str, dict] = {}
    held = 0
    if MEMBER in z.namelist():
        try:
            m = json.loads(z.read(MEMBER).decode("utf-8"))
        except ValueError:
            m = {}
            r["errors"].append({"file": MEMBER, "reason": "not readable, skipped"})
        if m and not isinstance(m, dict):
            m = {}
            r["errors"].append({"file": MEMBER, "reason": "not readable, skipped"})
        for wid, entry in m.items():
            if not isinstance(entry, dict) or not paths.is_safe_wid(str(wid)):
                r["dropped"].append({"kind": "meta", "id": str(wid), "reason": "bad entry"})
                continue
            for key, val in list(entry.items()):
                if key not in USER_META:
                    reason = ("derived on the machine that wrote the backup" if key in MACHINE_META
                              else "unknown to this version")
                    r["dropped"].append({"kind": "meta", "id": f"{wid}:{key}", "reason": reason})
                elif not isinstance(val, _TYPES[key]):
                    entry.pop(key)
                    r["dropped"].append({"kind": "meta", "id": f"{wid}:{key}", "reason": "not a value this version holds"})
            kept = _user_keys(entry)
            if not kept:
                continue
            out[str(wid)] = kept
            if not paths.wallpaper_present(str(wid), cfg_after):
                held += 1
    if held:
        r["held"].append({"kind": "meta", "count": held})
    plan["meta"] = out
    r["counts"]["favourites"] = sum(1 for v in out.values() if v.get("favorite"))
    r["counts"]["meta_held"] = held
    return True


def _backup_apply(plan: dict[str, Any], r: dict[str, Any]) -> bool:
    patches = plan.get("meta") or {}
    if not patches:
        return True
    try:
        with lock.held("meta"):
            m = load()
            for wid, patch in patches.items():
                m[wid] = {**m.get(wid, {}), **patch}
            save(m)
    except Exception as exc:
        r["errors"].append({"file": MEMBER, "reason": str(exc)})
    return True


BACKUP = Store("meta", (MEMBER,), _backup_export, _backup_preflight, _backup_apply)
