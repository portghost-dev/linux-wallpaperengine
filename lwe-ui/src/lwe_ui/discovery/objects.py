"""Scene-object enumeration WITH type, written to the objindex (see docs/findings.md).

The engine has no `type` field in scene.json; `ObjectParser.cpp` dispatches by marker-key
presence in a fixed order, with type guards. We replicate that dispatch exactly in `classify`.

Critical gotcha (verified on real wallpapers): particle objects carry `"image": null` and
`"model": null` sub-fields, so a naive `"image" in obj` test misclassifies every particle as an
image. The `isinstance(...)` guards below are load-bearing - `image` only counts when it is a
`str`, `sound` only when it is a `list`, and `particle` is checked before any model fallback.
`model` is never a top-level object-type marker in this engine.
"""
from __future__ import annotations

from pathlib import Path

from ..storage import atomic, paths
from . import pkg, project


def classify(obj: dict) -> str:
    """Return the object type for one scene object, mirroring ObjectParser.cpp dispatch order."""
    if obj.get("type"):
        return str(obj["type"]).lower()
    if isinstance(obj.get("image"), str):
        return "image"
    if isinstance(obj.get("sound"), list):
        return "sound"
    if "particle" in obj:
        return "particle"
    if "text" in obj:
        return "text"
    if "light" in obj:
        return "light"
    if "shape" in obj:
        return "light"
    return "generic"


def extract(wallpaper_dir: str | Path) -> list[dict]:
    """List typed objects for a scene wallpaper: [{objid, name, type}, ...].

    Video/web wallpapers have no scene graph -> []. A scene whose `scene.pkg` or `scene.json`
    is missing/unreadable also yields [] (the wallpaper simply has no enumerable objects).
    """
    wdir = Path(wallpaper_dir)
    info = project.read(wdir)
    if info["type"] != "scene":
        return []

    scene_pkg = wdir / "scene.pkg"
    scene: dict
    if scene_pkg.is_file():
        try:
            scene = pkg.read_scene_json(scene_pkg)
        except (pkg.PkgError, KeyError, ValueError, OSError):
            return []
    else:
        # Some scenes ship an unpacked scene.json next to project.json.
        loose = atomic.read_json(wdir / "scene.json", default=None)
        if not isinstance(loose, dict):
            return []
        scene = loose

    objects = scene.get("objects")
    if not isinstance(objects, list):
        return []

    out: list[dict] = []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        parent = obj.get("parent")
        out.append(
            {
                "objid": str(obj.get("id", "")),
                "name": obj.get("name", "") or "",
                "type": classify(obj),
                # optional fields for the editor objects panel (tree + row detail); absent
                # keys read as empty, so older objindex caches stay compatible.
                "parent": "" if parent in (None, "") else str(parent),
                "origin": str(obj.get("origin", "") or ""),
                "visible": obj.get("visible", True) is not False,
            }
        )
    return out


def build_index(wid: str, wallpapers_dir: str | Path) -> dict:
    """Build + atomically persist `paths.objindex_file(wid)` = {"objects": [...]}; return it."""
    index = {"objects": extract(Path(wallpapers_dir) / wid)}
    atomic.atomic_write_json(paths.objindex_file(wid), index)
    return index
