"""One function applies a store's RENAMES, RETIRED and VALUE_ALIASES tables to a raw mapping.

Every store that stores keys calls this at both doors, the normal load and the backup
import, so a stored file and an archive member go through exactly the same table. The
caller turns the returned actions into receipt lines; this function writes nothing.
"""
from __future__ import annotations

from typing import Any

from .. import constants as C

Action = dict[str, Any]


def apply_tables(store: str, raw: dict[str, Any]) -> tuple[dict[str, Any], list[Action]]:
    """(the mapping under this build's names, what the tables did).

    Actions are {"kind": "rename", "from", "to"}, {"kind": "retired", "from", "reason"} and
    {"kind": "alias", "key", "from", "to"}. A renamed key whose new name is already present
    in `raw` is retired instead: the current name wins over the stored one.
    """
    renames = C.RENAMES.get(store, {})
    retired = C.RETIRED.get(store, {})
    aliases = C.VALUE_ALIASES.get(store, {})
    out: dict[str, Any] = {}
    actions: list[Action] = []
    for key, val in raw.items():
        if key in retired:
            actions.append({"kind": "retired", "from": key, "reason": retired[key]})
            continue
        name, fn = key, None
        entry = renames.get(key)
        if entry is not None:
            name, fn = (entry[0], entry[1] if len(entry) > 1 else None) if isinstance(entry, (tuple, list)) else (entry, None)
        if name != key:
            if name in retired:
                actions.append({"kind": "retired", "from": key, "reason": retired[name]})
                continue
            if name in raw:
                actions.append({"kind": "retired", "from": key,
                                "reason": f"the file also carries {name}, which this build reads"})
                continue
            if fn is not None:
                val = fn(val)
            actions.append({"kind": "rename", "from": key, "to": name})
        table = aliases.get(name)
        if table:
            alias = table.get(str(val).strip())
            if alias is not None and alias != val:
                actions.append({"kind": "alias", "key": name, "from": val, "to": alias})
                val = alias
        out[name] = val
    return out, actions


def report(store: str, actions: list[Action], r: dict[str, Any], scope: str,
           dropped_kind: str, prefix: str = "") -> None:
    """Turn apply_tables' actions into receipt lines: a retirement is a named drop, a
    rename or an alias a named adjustment. One function, so no store forgets a kind."""
    for a in actions:
        if a["kind"] == "retired":
            r["dropped"].append({"kind": dropped_kind, "id": prefix + a["from"],
                                 "reason": "retired: " + a["reason"]})
        elif a["kind"] == "rename":
            r["adjusted"].append({"kind": "rename", "store": store, "id": scope,
                                  "key": a["to"], "from": a["from"], "to": a["to"]})
        elif a["kind"] == "alias":
            r["adjusted"].append({"kind": "alias", "store": store, "id": scope,
                                  "key": a["key"], "from": a["from"], "to": a["to"]})
