"""foreign.json: the keys this build does not know, kept so a restore never loses one.

Shape is {store: {scope: {key: raw value}}}, where scope is the member the key came from:
"settings.conf", "theme.json", "discover.json", a wid for an override, a slug for a
playlist. Every store's export asks `extras` for the keys it must re-emit into its own
member, so a value written by a newer build survives a round trip through an older one.
A key RETIRED names is never re-emitted and never kept.

This store carries no archive member of its own: its content travels inside the members it
came from. The Store record exists because foreign.json is a file under config_dir() and
every such file is claimed by exactly one store (test_store_ownership).
"""
from __future__ import annotations

import zipfile
from typing import Any

from .. import constants as C
from . import atomic, paths
from .store import Store

MEMBER = "foreign.json"


def _file():
    return paths.config_dir() / MEMBER


def load() -> dict[str, dict[str, dict[str, Any]]]:
    data = atomic.read_json(_file(), default={})
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for store, scopes in data.items():
        if not isinstance(scopes, dict):
            continue
        keep = {s: dict(k) for s, k in scopes.items() if isinstance(k, dict)}
        if keep:
            out[str(store)] = keep
    return out


def save(d: dict[str, dict[str, dict[str, Any]]]) -> None:
    atomic.atomic_write_json(_file(), d)


def extras(store: str, scope: str, data: dict | None = None) -> dict[str, Any]:
    """The preserved keys this store re-emits into `scope`, minus anything RETIRED. A key
    the build has since learned is gone from here by then (promote), so the caller's only
    guard is its own member: a key the member already carries is never overwritten."""
    retired = C.RETIRED.get(store, {})
    kept = (load() if data is None else data).get(store, {}).get(scope, {})
    return {k: v for k, v in kept.items() if k not in retired}


def emitted(r: dict[str, Any], n: int) -> None:
    """Count the preserved keys an export actually wrote into a member."""
    r["counts"]["preserved"] = r["counts"].get("preserved", 0) + n


def promote(log=None) -> dict[str, int]:
    """Once this build knows a key an earlier import kept aside, the value belongs in its
    member: written there through the store's own writer, then removed from here, so it
    is never re-emitted over a value the user has since set or cleared. Scopes whose
    playlist or override no longer exists are dropped too. Runs at every panel start."""
    from .. import constants as C
    from . import discover_cfg, playlists, settings, themes, wp
    data = load()
    if not data:
        return {"promoted": 0, "pruned": 0}
    done = {"promoted": 0, "pruned": 0}

    def promote_dense(store, scope, known, read, write, coerce):
        keys = data.get(store, {}).get(scope, {})
        for key in [k for k in keys if known(k)]:
            try:
                cur = read()
                cur[key] = coerce(key, keys[key])
                write(cur)
            except Exception as exc:
                if log:
                    log.warning("foreign: could not promote %s %s: %s", store, key, exc)
                continue
            del keys[key]
            done["promoted"] += 1

    promote_dense("settings", settings.MEMBER, lambda k: k in C.SETTINGS_SCHEMA,
                  settings.load, settings.save,
                  lambda k, v: settings._coerce(k, v, C.SETTINGS_SCHEMA[k]))
    promote_dense("theme", themes.MEMBER, lambda k: k in themes.CONFIG_KEYS,
                  themes.load_config, themes.save_config, lambda k, v: v)
    promote_dense("discovery", discover_cfg.MEMBER, lambda k: k in C.DISCOVER_DEFAULTS,
                  discover_cfg.load, discover_cfg.save, lambda k, v: v)
    for slug in list(data.get("playlists", {})):
        if not paths.playlist_file(slug).exists():
            del data["playlists"][slug]
            done["pruned"] += 1
            continue
        promote_dense("playlists", slug, lambda k: k in C.PLAYLIST_SCHEMA,
                      lambda: playlists.load(slug), lambda d: playlists.save(slug, d),
                      lambda k, v: playlists._coerce(k, v, C.PLAYLIST_SCHEMA[k]))
    for wid in list(data.get("overrides", {})):
        if not wp.exists(wid):
            del data["overrides"][wid]
            done["pruned"] += 1
            continue
        keys = data["overrides"][wid]
        known = [k for k in keys if k in C.WP_SCHEMA or k.startswith(C.WP_PROP_PREFIX)]
        if not known:
            continue
        try:
            present = wp.load_set(wid)
            wp.update_set(wid, {k: keys[k] for k in known if k not in present})
        except Exception as exc:
            if log:
                log.warning("foreign: could not promote override %s: %s", wid, exc)
            continue
        for k in known:
            del keys[k]
        done["promoted"] += 1
    for store in list(data):
        data[store] = {s: k for s, k in data[store].items() if k}
        if not data[store]:
            del data[store]
    save(data)
    if log and (done["promoted"] or done["pruned"]):
        log.info("foreign: promoted %d, pruned %d", done["promoted"], done["pruned"])
    return done


def record(plan: dict[str, Any], store: str, scope: str, key: str, value: Any,
           r: dict[str, Any]) -> None:
    """Keep one unknown key in the import plan and name it in the receipt."""
    plan.setdefault("foreign", {}).setdefault(store, {}).setdefault(scope, {})[key] = value
    r["preserved"].append({"kind": "preserved", "store": store, "id": scope, "key": key})


# --- backup ---------------------------------------------------------------------------

def _backup_export(z: zipfile.ZipFile, r: dict[str, Any]) -> None:
    """Nothing of its own: each store re-emits its own extras and counts them (emitted)."""
    r["counts"].setdefault("preserved", 0)


def _backup_preflight(z: zipfile.ZipFile, r: dict[str, Any], plan: dict[str, Any],
                      cfg_after: dict[str, Any]) -> bool:
    """The other stores fill plan["foreign"] as they read their members; this runs last and
    only counts what they kept."""
    r["counts"]["preserved"] = sum(len(k) for s in (plan.get("foreign") or {}).values()
                                   for k in s.values())
    return True


def _backup_apply(plan: dict[str, Any], r: dict[str, Any]) -> bool:
    kept = plan.get("foreign") or {}
    if not kept:
        return True
    try:
        current = load()
        for store, scopes in kept.items():
            have = current.setdefault(store, {})
            for scope, keys in scopes.items():
                have.setdefault(scope, {}).update(keys)
        save(current)
    except Exception as exc:
        r["errors"].append({"file": MEMBER, "reason": str(exc)})
    return True


BACKUP = Store("foreign", (MEMBER,), _backup_export, _backup_preflight, _backup_apply)
