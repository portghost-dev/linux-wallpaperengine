"""meta.json - per-wallpaper app metadata keyed by id (favorites, notes, timestamps, etc.).

Tier B (JSON). The schema is open/free-form; this module only guarantees a dict keyed by id.
"""
from __future__ import annotations

import json
import zipfile
from typing import Any

from . import atomic, paths
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
    with _WRITE_LOCK:
        _update_locked(id, patch)


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


def _backup_export(z: zipfile.ZipFile, r: dict[str, Any]) -> None:
    m = load()
    z.writestr(MEMBER, json.dumps(m, indent=1))
    r["counts"]["favourites"] = sum(1 for v in m.values() if isinstance(v, dict) and v.get("favorite"))


def _backup_preflight(z: zipfile.ZipFile, r: dict[str, Any], plan: dict[str, Any],
                      cfg_after: dict[str, Any]) -> bool:
    out: dict[str, dict] = {}
    if MEMBER in z.namelist():
        try:
            m = json.loads(z.read(MEMBER).decode("utf-8"))
            out = {k: v for k, v in m.items() if isinstance(v, dict) and paths.is_safe_wid(str(k))}
        except ValueError:
            r["errors"].append({"file": MEMBER, "reason": "not readable, skipped"})
    plan["meta"] = out
    r["counts"]["favourites"] = sum(1 for v in out.values() if v.get("favorite"))
    return True


def _backup_apply(plan: dict[str, Any], r: dict[str, Any]) -> bool:
    patches = plan.get("meta") or {}
    if not patches:
        return True
    try:
        m = load()
        for wid, patch in patches.items():
            m[wid] = {**m.get(wid, {}), **patch}
        save(m)
    except Exception as exc:
        r["errors"].append({"file": MEMBER, "reason": str(exc)})
    return True


BACKUP = Store("meta", (MEMBER,), _backup_export, _backup_preflight, _backup_apply)
