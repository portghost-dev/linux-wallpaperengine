"""discover.json - Steam Workshop acquisition config (Tier B, JSON).

Stored keys: apiKey, acquireMethod (client|steamcmd), steamcmdPath. Loaded values are merged
over C.DISCOVER_DEFAULTS so a partial/absent file still yields a complete dict.
"""
from __future__ import annotations

from typing import Any

from .. import constants as C
from . import atomic, paths


def load() -> dict[str, Any]:
    """C.DISCOVER_DEFAULTS overlaid with whatever the file provides."""
    out = dict(C.DISCOVER_DEFAULTS)
    data = atomic.read_json(paths.discover_file(), default={})
    if isinstance(data, dict):
        out.update(data)
    return out


def save(d: dict[str, Any]) -> None:
    """Persist defaults overlaid with the caller's values (so file is always complete)."""
    out = dict(C.DISCOVER_DEFAULTS)
    out.update(d or {})
    atomic.atomic_write_json(paths.discover_file(), out)
