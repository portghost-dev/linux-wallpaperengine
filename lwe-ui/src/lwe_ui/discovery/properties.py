"""User-facing wallpaper properties -> propindex.

Reads `general.properties` from project.json (via project.read) and normalizes each entry to a
UI-ready record. The Wallpaper Engine `type` is mapped to a small `kind` enum the editor knows how
to render; the human label lives in WE's `text` field and may contain HTML (e.g.
`<br><i><small><mark>...`), which we strip.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..storage import atomic, paths
from . import project

# WE property type -> editor kind. Both the modern names used in this library (bool/slider/combo)
# and the spec's longer aliases (boolean/textinput) are accepted.
_KIND_MAP = {
    "bool": "bool",
    "boolean": "bool",
    "slider": "slider",
    "combo": "combo",
    "color": "color",
    "text": "text",
    "textinput": "text",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(text: object) -> str:
    """Remove HTML tags and collapse whitespace from a WE label string."""
    if not isinstance(text, str):
        return ""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", text)).strip()


def _normalize(name: str, spec: dict) -> dict | None:
    """Normalize one WE property spec to a propindex entry, or None to skip it."""
    we_type = str(spec.get("type", "")).lower()
    kind = _KIND_MAP.get(we_type)
    if kind is None:
        return None

    entry: dict = {
        "name": name,
        "kind": kind,
        "label": _strip_html(spec.get("text")),
        "value": spec.get("value"),
    }

    cond = None
    user = spec.get("user")
    if isinstance(user, dict) and user.get("condition") is not None:
        cond = {"key": str(user.get("name") or ""), "target": str(user.get("condition"))}
    elif isinstance(spec.get("condition"), str):
        m = re.match(r"\s*([\w.]+?)(?:\.value)?\s*==\s*(.+)", spec["condition"])
        if m:
            cond = {"key": m.group(1).strip(), "target": m.group(2).strip().strip("'\"")}
    if cond and cond["key"]:
        entry["condition"] = cond

    if kind == "slider":
        for key in ("min", "max", "step"):
            if key in spec:
                entry[key] = spec[key]
    elif kind == "combo":
        options = spec.get("options")
        if isinstance(options, list):
            entry["options"] = [
                {
                    "label": _strip_html(o.get("label")) if isinstance(o, dict) else "",
                    "value": o.get("value") if isinstance(o, dict) else o,
                }
                for o in options
            ]
    return entry


def normalize_all(properties: object) -> list[dict]:
    """Normalize a project.json `general.properties` dict to a list of propindex entries.

    The shared core of build_index, exposed so a surface that already holds a project dict
    (a preset renders from its base's dir, so its properties do not live under
    WALLPAPERS_DIR/<its own id>) can normalize without a cache write and without routing
    through the editor bridge. Unknown WE types are skipped here exactly as they are there.
    """
    entries: list[dict] = []
    if isinstance(properties, dict):
        for name, spec in properties.items():
            if not isinstance(spec, dict):
                continue
            entry = _normalize(name, spec)
            if entry is not None:
                entries.append(entry)
    return entries


def build_index(wid: str, wallpapers_dir: str | Path) -> dict:
    """Build + atomically persist `paths.propindex_file(wid)` = {"properties": [...]}; return it.

    Properties whose WE type is unknown (not in the kind map) are skipped. Entry order follows
    the insertion order of `general.properties`.
    """
    properties = project.read(Path(wallpapers_dir) / wid)["properties"]
    index = {"properties": normalize_all(properties)}
    atomic.atomic_write_json(paths.propindex_file(wid), index)
    return index
