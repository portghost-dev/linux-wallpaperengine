"""A wallpaper's parts and properties as commands read them, without Qt: the part tree of a render
folder's scene, the knobs of its project.json with the values in force, and the ids a SKIP value
names.

Everything here reads files: the render folder and the wallpaper's own conf. Nothing is written, so
the editor's object and property caches stay as they are.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from ..discovery import objects, project, properties
from ..storage import wp
from . import catalog

_DIGITS = re.compile(r"[0-9]+")


def skip_ids(text: str) -> list[int]:
    """The ASCII-digit tokens of a SKIP value as ints; every other token is dropped."""
    return [int(tok) for tok in str(text).split() if _DIGITS.fullmatch(tok)]


def _parents(parts: list[dict]) -> list[int | None]:
    """Each part's parent as an index into parts, None for a root. A parent id the scene lacks makes
    a root; following parents from a part, the first part that repeats becomes a root, which cuts
    the cycle there. An id held by two parts names the first of them."""
    first: dict[str, int] = {}
    for i, part in enumerate(parts):
        first.setdefault(part["objid"], i)
    up = [first.get(part["parent"]) if part["parent"] else None for part in parts]
    done = [False] * len(parts)
    for start in range(len(parts)):
        chain: list[int] = []
        on_chain: set[int] = set()
        i: int | None = start
        while i is not None and not done[i] and i not in on_chain:
            chain.append(i)
            on_chain.add(i)
            i = up[i]
        if i is not None and i in on_chain:
            up[i] = None
        for j in chain:
            done[j] = True
    return up


def _tree_order(up: list[int | None]) -> list[tuple[int, int]]:
    """(index, depth) for every part: roots in scene order, each part's children after it in scene
    order."""
    children: list[list[int]] = [[] for _ in up]
    roots: list[int] = []
    for i, parent in enumerate(up):
        (roots if parent is None else children[parent]).append(i)
    order: list[tuple[int, int]] = []
    stack = [(i, 0) for i in reversed(roots)]
    while stack:
        i, depth = stack.pop()
        order.append((i, depth))
        stack.extend((child, depth + 1) for child in reversed(children[i]))
    return order


def tree(render_dir: str, skip: Iterable[int], word: str | None = None) -> list[tuple[int, dict, bool, bool]]:
    """The scene's parts in tree order as (depth, part, author_hidden, you_hidden), part being
    discovery.objects.extract's record. author_hidden: the scene sets visible false; you_hidden: the
    part's id is in skip. With word, only the parts whose type equals it or whose name contains it,
    ignoring case, and their ancestors."""
    if not render_dir:
        return []
    parts = objects.extract(render_dir)
    up = _parents(parts)
    keep: set[int] | None = None
    if word is not None:
        folded = word.casefold()
        keep = set()
        for i, part in enumerate(parts):
            if part["type"].casefold() == folded or folded in part["name"].casefold():
                j: int | None = i
                while j is not None and j not in keep:
                    keep.add(j)
                    j = up[j]
    hidden = set(skip)
    rows: list[tuple[int, dict, bool, bool]] = []
    for i, depth in _tree_order(up):
        if keep is not None and i not in keep:
            continue
        part = parts[i]
        objid = part["objid"]
        rows.append((depth, part, not part["visible"], bool(_DIGITS.fullmatch(objid)) and int(objid) in hidden))
    return rows


def knobs(wid: str) -> list[dict]:
    """The properties of the wallpaper's render folder's project.json, each as {name, label, kind,
    value, yours, options, min, max, step}: value is the wallpaper's own PROP_<name> when its conf
    carries one (yours true), else the author's value. Conditions are not evaluated."""
    folder = catalog.render_dir(wid)
    if not folder:
        return []
    own = wp.load_set(wid)["props"]
    rows: list[dict] = []
    for entry in properties.normalize_all(project.read(folder)["properties"]):
        name = entry["name"]
        yours = name in own
        rows.append({
            "name": name,
            "label": entry["label"],
            "kind": entry["kind"],
            "value": own[name] if yours else entry["value"],
            "yours": yours,
            "options": entry.get("options"),
            "min": entry.get("min"),
            "max": entry.get("max"),
            "step": entry.get("step"),
        })
    return rows
