"""discover.json - Steam Workshop acquisition config (Tier B, JSON).

Stored keys: apiKey, acquireMethod (client|steamcmd), steamcmdPath. Loaded values are merged
over C.DISCOVER_DEFAULTS so a partial/absent file still yields a complete dict.
"""
from __future__ import annotations

import json
import zipfile
from typing import Any

from .. import constants as C
from . import atomic, foreign, migrate, paths
from .store import Store


def load() -> dict[str, Any]:
    """C.DISCOVER_DEFAULTS overlaid with whatever the file provides."""
    out = dict(C.DISCOVER_DEFAULTS)
    data = atomic.read_json(paths.discover_file(), default={})
    if isinstance(data, dict):
        data, _ = migrate.apply_tables("discovery", data)
        out.update(data)
    return out


def save(d: dict[str, Any]) -> None:
    """Persist defaults overlaid with the caller's values (so file is always complete)."""
    out = dict(C.DISCOVER_DEFAULTS)
    out.update(d or {})
    atomic.atomic_write_json(paths.discover_file(), out)


# --- backup ---------------------------------------------------------------------------
MEMBER = "discover.json"


def _backup_export(z: zipfile.ZipFile, r: dict[str, Any]) -> None:
    # every key is user-authored, the API key included, so the whole store travels;
    # steamcmdPath is a path on the source machine and travels verbatim, unresolved;
    # a machine with no file has nothing to carry
    r["counts"]["discovery"] = 0
    extra = foreign.extras("discovery", MEMBER)
    if not paths.discover_file().is_file() and not extra:
        return
    try:
        out = {k: v for k, v in load().items() if k in C.DISCOVER_DEFAULTS}
        out.update({k: v for k, v in extra.items() if k not in out})
        foreign.emitted(r, len([k for k in extra if k not in C.DISCOVER_DEFAULTS]))
        z.writestr(MEMBER, json.dumps(out, indent=1))
    except Exception as exc:
        r["errors"].append({"file": MEMBER, "reason": str(exc)})
        return
    r["counts"]["discovery"] = 1


def _backup_preflight(z: zipfile.ZipFile, r: dict[str, Any], plan: dict[str, Any],
                      cfg_after: dict[str, Any]) -> bool:
    plan["discovery"] = None
    r["counts"]["discovery"] = 0
    if MEMBER not in z.namelist():
        return True
    try:
        d = json.loads(z.read(MEMBER).decode("utf-8"))
    except ValueError:
        r["errors"].append({"file": MEMBER, "reason": "not readable, skipped"})
        return True
    if not isinstance(d, dict):
        r["errors"].append({"file": MEMBER, "reason": "not readable, skipped"})
        return True
    named, actions = migrate.apply_tables("discovery", d)
    migrate.report("discovery", actions, r, MEMBER, "discovery")
    kept: dict[str, Any] = {}
    for k, v in named.items():
        if k not in C.DISCOVER_DEFAULTS:
            foreign.record(plan, "discovery", MEMBER, k, v, r)
        elif isinstance(v, str):
            kept[k] = v
        else:
            r["dropped"].append({"kind": "discovery", "id": k, "reason": "not a value this version holds"})
    plan["discovery"] = kept
    r["counts"]["discovery"] = 1
    return True


def _backup_apply(plan: dict[str, Any], r: dict[str, Any]) -> bool:
    d = plan.get("discovery")
    if not d:
        return True
    try:
        save({**load(), **d})
    except Exception as exc:
        r["errors"].append({"file": MEMBER, "reason": str(exc)})
    return True


BACKUP = Store("discovery", (MEMBER,), _backup_export, _backup_preflight, _backup_apply)
