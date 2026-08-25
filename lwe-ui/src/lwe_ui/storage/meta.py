"""meta.json - per-wallpaper app metadata keyed by id (favorites, notes, timestamps, etc.).

Tier B (JSON). The schema is open/free-form; this module only guarantees a dict keyed by id.
"""
from __future__ import annotations

from typing import Any

from . import atomic, paths


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
