"""foreign.json: the keys this build does not know, kept so a restore never loses one.

Shape is {store: {scope: {key: raw value}}}, where scope is the member the key came from:
"settings.conf", "theme.json", "discover.json", a wid for an override, a slug for a
playlist. Every store's export asks `extras` for the keys it must re-emit into its own
member, so a value written by a newer build survives a round trip through an older one.
A key RETIRED names is never re-emitted and never kept.

The file is no archive member and no store: its content travels inside the members it came
from, which is what registry.CARRIED_INSIDE says of it for the ownership law.
"""
from __future__ import annotations

from typing import Any

from .. import constants as C
from . import atomic, lock, paths

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
    with lock.held("foreign"):
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
    with lock.held("foreign"):
        data = load()
        if not data:
            return {"promoted": 0, "pruned": 0}
        done = {"promoted": 0, "pruned": 0}

        def promote_dense(store, scope, known, update, coerce):
            keys = data.get(store, {}).get(scope, {})
            for key in [k for k in keys if known(k)]:
                try:
                    update({key: coerce(key, keys[key])})
                except Exception as exc:
                    if log:
                        log.warning("foreign: could not promote %s %s: %s", store, key, exc)
                    continue
                del keys[key]
                done["promoted"] += 1

        def merged(store, read, write):
            def update(changes):
                with lock.held(store):
                    write({**read(), **changes})
            return update

        promote_dense("settings", settings.MEMBER, lambda k: k in C.SETTINGS_SCHEMA,
                      settings.update, lambda k, v: settings._coerce(k, v, C.SETTINGS_SCHEMA[k]))
        promote_dense("theme", themes.MEMBER, lambda k: k in themes.CONFIG_KEYS,
                      merged("theme", themes.load_config, themes.save_config), lambda k, v: v)
        promote_dense("discovery", discover_cfg.MEMBER, lambda k: k in C.DISCOVER_DEFAULTS,
                      merged("discovery", discover_cfg.load, discover_cfg.save), lambda k, v: v)
        for slug in list(data.get("playlists", {})):
            if not paths.playlist_file(slug).exists():
                del data["playlists"][slug]
                done["pruned"] += 1
                continue
            promote_dense("playlists", slug, lambda k: k in C.PLAYLIST_SCHEMA,
                          lambda changes: playlists.update(slug, changes),
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
                wp.modify_set(wid, lambda present: {k: keys[k] for k in known if k not in present})
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


def count_plan(plan: dict[str, Any], r: dict[str, Any]) -> None:
    """How many keys the import will keep aside, once every store has read its member."""
    r["counts"]["preserved"] = sum(len(k) for s in (plan.get("foreign") or {}).values()
                                   for k in s.values())


def apply_plan(plan: dict[str, Any], r: dict[str, Any]) -> None:
    """Write what the stores kept aside, merged over what is already here."""
    kept = plan.get("foreign") or {}
    if not kept:
        return
    try:
        with lock.held("foreign"):
            current = load()
            for store, scopes in kept.items():
                have = current.setdefault(store, {})
                for scope, keys in scopes.items():
                    have.setdefault(scope, {}).update(keys)
            save(current)
    except Exception as exc:
        r["errors"].append({"file": MEMBER, "reason": str(exc)})
