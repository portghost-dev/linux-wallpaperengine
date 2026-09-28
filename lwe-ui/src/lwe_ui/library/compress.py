"""Compressing a wallpaper's textures into the engine's cache, for the compress command, and the
lines that report it. Plain Python; no Qt."""
from __future__ import annotations

import os
from typing import NamedTuple

from .. import texcomp
from . import catalog


UNREADABLE = "its package could not be read"


class Result(NamedTuple):
    kind: str
    before: int
    after: int
    failed: int
    disk: int
    links: tuple[str, ...] = ()


def compress_one(row: catalog.Row, *, folder: str | None = None) -> Result:
    """Encode the uncached textures in folder, by default the one the row renders from (a preset's
    base), owned by that folder. kind is "compressed", "already", "nothing", "missing",
    "unreadable" (the package could not be read, so nothing was written) or "failed" (no texture
    was written while at least one failed). links names the packages and exempt.txt that are
    symbolic links, which are not read (texcomp.links)."""
    d = catalog.render_dir(row.id) if folder is None else folder
    if not d:
        return Result("missing", 0, 0, 0, 0)
    if row.type in ("video", "web"):
        return Result("nothing", 0, 0, 0, 0)
    measure: dict[str, int] = {}
    links = tuple(texcomp.links(d))
    try:
        done = texcomp.encode_scene(d, os.path.basename(d.rstrip("/")), measure=measure)
    except texcomp.PackageError:
        return Result("unreadable", 0, 0, 0, 0, links)
    if measure["eligible"] == 0:
        return Result("nothing", 0, 0, 0, 0, links)
    if done["total"] == 0:
        return Result("already", 0, 0, 0, 0, links)
    kind = "failed" if done["encoded"] == 0 and done["failed"] > 0 else "compressed"
    return Result(kind, measure["bytes_before"], measure["bytes_after"], done["failed"], measure["disk_bytes"],
                  links)


def size(b: int) -> str:
    """Decimal units: whole megabytes below a gigabyte, gigabytes with two decimals above."""
    return f"{round(b / 10**6)} MB" if b < 10**9 else f"{b / 10**9:.2f} GB"


def row_line(row: catalog.Row, text: str) -> str:
    """The row's title and id, padded to 28 columns, a space, then text."""
    label = f"{row.title} ({row.id})"
    return f"{label:<28} {text}"


def result_text(row: catalog.Row, result: Result) -> str:
    if result.kind in ("compressed", "failed"):
        text = f"textures {size(result.before)} before, {size(result.after)} after"
        if result.failed == 1:
            text += "; 1 texture failed"
        elif result.failed > 1:
            text += f"; {result.failed} textures failed"
    elif result.kind == "already":
        text = "already compressed"
    elif result.kind == "nothing":
        text = f"{row.type or 'scene'}, nothing to compress"
    elif result.kind == "unreadable":
        text = f"not compressed: {UNREADABLE}"
    else:
        text = "files missing, nothing to compress"
    if result.links:
        text += f"; {'link' if len(result.links) == 1 else 'links'} not read: {', '.join(result.links)}"
    return text


def result_line(row: catalog.Row, result: Result) -> str:
    return row_line(row, result_text(row, result))


def written(result: Result) -> bool:
    """Whether at least one texture of the wallpaper reached the cache."""
    return result.kind == "compressed" and result.disk > 0


def total_line(results: list[Result]) -> str:
    return (f"Compressed {sum(1 for r in results if written(r))} of {len(results)} wallpapers. "
            f"Textures {size(sum(r.before for r in results))} before, "
            f"{size(sum(r.after for r in results))} after. "
            f"The cache adds {size(sum(r.disk for r in results))} on disk.")
