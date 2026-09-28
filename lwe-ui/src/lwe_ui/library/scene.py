"""A wallpaper's parts and properties as commands read them, without Qt: the part tree of a render
folder's scene, the knobs of its project.json with the values in force, and the ids a SKIP value
names.

Everything here reads files: the render folder and the wallpaper's own conf. Nothing is written, so
the editor's object and property caches stay as they are.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable

from ..discovery import objects, project, properties
from ..engine import daemon_unit
from ..storage import wp
from . import catalog

_DIGITS = re.compile(r"[0-9]+")
_INTEGER = re.compile(r"[+-]?[0-9]{1,18}")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def skip_ids(text: str) -> list[int]:
    """The part ids of a SKIP value that the engine takes (storage/wp.py::skip_ids); every other token
    is dropped."""
    return wp.skip_ids(text)


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


def part(render_dir: str, objid: int) -> dict | None:
    """The first part of the render folder's scene whose id is objid, as discovery.objects.extract
    records it, or None."""
    if not render_dir:
        return None
    return next((p for p in objects.extract(render_dir) if p["objid"] == str(objid)), None)


def _typed(kind: str, value: object, authored: object, override: bool = False) -> object:
    """A knob's value with one type per kind: a slider a number (an int when the author's value is an int
    and the text a whole number, else a float), a bool true or false, anything else a string; a slider text
    that does not read as a number stays a string. A bool reads as the engine reads it: an override text is
    true only when it is exactly "true" or "1" (Property.h, PropertyBoolean::update); the author's value is
    true when it is JSON true, the text "true", or a text whose C strtod prefix is a finite number other than
    0, and false otherwise, a JSON number or null included (PropertyParser.cpp::parseBoolean, JSON.h)."""
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if override:
            return str(value) in ("true", "1")
        if not isinstance(value, str):
            return False
        number = daemon_unit.strtod(value)
        return value == "true" or (number is not None and math.isfinite(number) and number != 0)
    if value is None or kind != "slider":
        return value if value is None or isinstance(value, str) else json.dumps(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value).strip()
    if isinstance(authored, int) and not isinstance(authored, bool) and _INTEGER.fullmatch(text):
        return int(text)
    if _NUMBER.fullmatch(text) and len(text) <= 64 and math.isfinite(float(text)):
        return float(text)
    return str(value)


def knobs(wid: str, typed: bool = True) -> list[dict]:
    """The properties of the wallpaper's render folder's project.json, each as {name, label, kind,
    value, yours, options, min, max, step}: value is the wallpaper's own PROP_<name> when its conf
    carries one (yours true), else the author's value, typed by _typed when typed, else as stored.
    Conditions are not evaluated."""
    folder = catalog.render_dir(wid)
    if not folder:
        return []
    own = wp.load_set(wid)["props"]
    rows: list[dict] = []
    for entry in properties.normalize_all(project.read(folder)["properties"]):
        name = entry["name"]
        yours = name in own
        value = own[name] if yours else entry["value"]
        rows.append({
            "name": name,
            "label": entry["label"],
            "kind": entry["kind"],
            "value": _typed(entry["kind"], value, entry["value"], override=yours) if typed else value,
            "yours": yours,
            "options": entry.get("options"),
            "min": entry.get("min"),
            "max": entry.get("max"),
            "step": entry.get("step"),
        })
    return rows
