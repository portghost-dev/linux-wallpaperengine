"""Read a wallpaper's `project.json` into a normalized dict.

`type` is taken from the top-level `.type` key ONLY, lowercased and passed through as-is (the
engine only acts on scene/video/web, but this reader does not clamp the value). It is NEVER located
by regex: the substring `"type"` recurs inside `general.properties.<name>.type`, so a regex over
the raw text would misclassify scene wallpapers. A `.file` ending in `.mp4`/`.webm` forces "video"
(some packs ship a video with `type` unset or stale).
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

from ..storage import atomic

_VIDEO_EXTS = (".mp4", ".webm")


def read(wallpaper_dir: str | Path) -> dict:
    """Parse `<wallpaper_dir>/project.json`.

    Returns {id, title, type, file, preview, properties, raw}:
        id          basename of the directory (the payload/workshop id).
        title       raw["title"] or "".
        type        raw["type"] lowercased and passed through as-is (not clamped; the engine
                    only acts on scene/video/web); if `file` endswith .mp4/.webm it is forced
                    to "video".
        file        raw["file"] or "".
        preview     absolute path to <dir>/<raw["preview"]> if that file exists, else the
                    first matching `preview.*` in the dir, else "".
        properties  raw["general"]["properties"] or {}.
        raw         the full parsed project.json (empty dict if missing/unparsable).
    """
    wdir = Path(wallpaper_dir)
    raw = atomic.read_json(wdir / "project.json", default={}) or {}
    if not isinstance(raw, dict):
        raw = {}

    file = raw.get("file") or ""
    if not isinstance(file, str):
        file = str(file)

    wtype = raw.get("type") or ""
    wtype = str(wtype).lower() if wtype else ""
    if file.lower().endswith(_VIDEO_EXTS):
        wtype = "video"

    general = raw.get("general")
    properties = {}
    if isinstance(general, dict):
        props = general.get("properties")
        if isinstance(props, dict):
            properties = props

    title = raw.get("title") or ""
    if not isinstance(title, str):
        title = str(title)

    return {
        "id": wdir.name,
        "title": title,
        "type": wtype,
        "file": file,
        "preview": _resolve_preview(wdir, raw.get("preview")),
        "properties": properties,
        "raw": raw,
    }


def _resolve_preview(wdir: Path, declared: object) -> str:
    """Absolute path to the preview image, or "" if none can be found."""
    if isinstance(declared, str) and declared:
        cand = wdir / declared
        if cand.is_file():
            return str(cand)
    for match in sorted(glob.glob(os.path.join(str(wdir), "preview.*"))):
        if os.path.isfile(match):
            return match
    return ""



# project.json color-grade preset keys -> CC component slot order ("b c s h").
# Each is 0..100 with 50 = neutral; value/50 maps 50->1.0 (identity), 0->0, 100->2.0.
# hue is the 4th CC slot; its neutral is 0 (identity "1 1 1 0"), so it is handled per-slot below.
_WEC_KEYS = ("wec_brs", "wec_con", "wec_sa", "wec_hue")
CC_DEFAULT = "1 1 1 0"
_CC_DEFAULT = CC_DEFAULT


def derive_cc(raw: dict) -> str:
    """Build a CC string "b c s h" from a project.json preset `wec_*` block, if present.

    Each wec_* is 0..100, 50 = neutral. brightness/contrast/saturation map value/50 (50 -> 1.0);
    hue maps to the 4th slot where neutral is 0. Missing keys keep the identity default. If no
    wec_* key is present at all, returns the identity "1 1 1 0".

    wec_e is the color-correction ENABLED flag: when it is explicitly false the preset
    ships its brs/con/sat/hue sliders but the engine applies NONE of them, so deriving a
    CC from those (dormant) values would apply a correction the preset turned off - e.g.
    OLED Black carries wec_con 100 with wec_e false, and applying 2x contrast crushed the
    scene's white highlights. Honor the flag: disabled -> identity.
    """
    if not isinstance(raw, dict):
        return CC_DEFAULT
    if raw.get("wec_e") is False:
        return CC_DEFAULT
    found = False
    comps: list[str] = []
    defaults = (1.0, 1.0, 1.0, 0.0)
    for key, neutral_out in zip(_WEC_KEYS, defaults):
        val = raw.get(key)
        if val is None:
            comps.append(_fmt(neutral_out))
            continue
        try:
            n = float(val)
        except (TypeError, ValueError):
            comps.append(_fmt(neutral_out))
            continue
        found = True
        if key == "wec_hue":
            # neutral 50 -> 0 (identity); scale to roughly [-1, 1] around neutral.
            comps.append(_fmt((n - 50.0) / 50.0))
        else:
            comps.append(_fmt(n / 50.0))
    if not found:
        return CC_DEFAULT
    return " ".join(comps)


def _fmt(x: float) -> str:
    """Compact float: integers render without a trailing '.0' so CC stays terse and shell-safe."""
    if x == int(x):
        return str(int(x))
    return repr(round(x, 6))


_derive_cc = derive_cc
