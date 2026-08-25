"""lwe-discover: rebuild the per-wallpaper object + property indexes.

For each requested wallpaper id, rebuild both indexes via the discovery layer:
  * `discovery.objects.build_index(wid, WALLPAPERS_DIR)`     -> state/objindex/<id>.json
  * `discovery.properties.build_index(wid, WALLPAPERS_DIR)`  -> state/propindex/<id>.json

WALLPAPERS_DIR comes from the saved settings (falling back to the resolved default). `--all`
discovers every id that is a directory under WALLPAPERS_DIR.

This process NEVER spawns the wallpaper engine - discovery is pure-Python (the engine `-z`
segfaults and lacks types; see docs/findings.md).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .discovery import objects, properties
from .storage import paths, tier_a


def _wallpapers_dir() -> Path:
    """Resolve WALLPAPERS_DIR from settings.conf, falling back to the resolved default."""
    sf = paths.settings_file()
    if sf.is_file():
        try:
            parsed = tier_a.parse(sf.read_text(encoding="utf-8"))
            val = parsed.get("WALLPAPERS_DIR", "")
            if val:
                return Path(val)
        except OSError:
            pass
    return Path(paths.default_settings()["WALLPAPERS_DIR"])


def _all_ids(wallpapers_dir: Path) -> list[str]:
    """Every immediate subdirectory of WALLPAPERS_DIR, sorted (each is a wallpaper id)."""
    if not wallpapers_dir.is_dir():
        return []
    return sorted(p.name for p in wallpapers_dir.iterdir() if p.is_dir())


def rebuild(wid: str, wallpapers_dir: Path) -> dict:
    """Rebuild both indexes for one id; return {id, objects, properties} counts."""
    obj_index = objects.build_index(wid, wallpapers_dir)
    prop_index = properties.build_index(wid, wallpapers_dir)
    return {
        "id": wid,
        "objects": len(obj_index.get("objects", [])),
        "properties": len(prop_index.get("properties", [])),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="lwe-discover",
        description="Rebuild the object + property indexes for one wallpaper id, or --all.",
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("id", nargs="?", help="wallpaper id to (re)index")
    g.add_argument("--all", action="store_true", help="index every id under WALLPAPERS_DIR")
    args = ap.parse_args(argv)

    if not args.all and not args.id:
        ap.error("provide a wallpaper <id> or --all")

    paths.ensure_dirs()  # objindex/propindex dirs must exist before atomic writes land there
    wallpapers_dir = _wallpapers_dir()

    if args.all:
        ids = _all_ids(wallpapers_dir)
        if not ids:
            print(f"no wallpapers found under {wallpapers_dir}")
            return 0
    else:
        ids = [args.id]

    print(f"indexing {len(ids)} wallpaper(s) under {wallpapers_dir}")
    rc = 0
    for wid in ids:
        try:
            r = rebuild(wid, wallpapers_dir)
            print(f"  {r['id']}: {r['objects']} object(s), {r['properties']} property(ies)")
        except Exception as exc:  # one bad wallpaper must not abort an --all run
            rc = 1
            print(f"  {wid}: ERROR {exc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
