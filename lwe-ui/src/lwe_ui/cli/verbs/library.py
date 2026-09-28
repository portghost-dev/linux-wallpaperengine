"""lwe list, workshop and scan print the library catalog and only read; lwe compress builds the
engine's texture cache. None of them locks a store or sends anything to the engine."""
from __future__ import annotations

import json

from .. import DONE, REFUSED, USAGE, Context
from ..registry import Verb

_TYPES = ("scene", "video", "web")


def _refuse(ctx: Context, message: str) -> int:
    ctx.error(message, "lwe: " + message)
    return USAGE


def _rows() -> tuple[list, int]:
    from ...library import catalog

    return catalog.wallpaper_rows()


def _left_out(ctx: Context, unsafe: int) -> None:
    if unsafe:
        print(f"lwe: {unsafe} folders were left out because their names use characters other than "
              "letters, digits, dot, underscore and hyphen", file=ctx.err)


def _print(ctx: Context, rows: list, mark: str, marked_state: str) -> None:
    """One line per row, or under -j one JSON array; `mark` ends the lines of rows in marked_state."""
    if ctx.json:
        print(json.dumps([{"n": r.n, "id": r.id, "title": r.title, "alias": r.alias, "type": r.type,
                           "state": r.state} for r in rows], ensure_ascii=False, separators=(",", ":")),
              file=ctx.out)
        return
    width = len(str(max((r.n for r in rows), default=0)))
    for r in rows:
        line = f"{r.n:>{width}}  {r.title} ({r.id})"
        if r.alias:
            line += f"  @{r.alias}"
        if r.state == marked_state:
            line += f"  {mark}"
        print(line, file=ctx.out)


def _list(ctx: Context, args: list[str]) -> int:
    if len(args) > 1 or (args and args[0] not in _TYPES):
        return _refuse(ctx, "list takes scene, video or web")
    rows, unsafe = _rows()
    pool = [r for r in rows if r.state in ("pool", "missing") and (not args or r.type == args[0])]
    _print(ctx, pool, "files missing", "missing")
    _left_out(ctx, unsafe)
    return DONE


def _workshop(ctx: Context, args: list[str]) -> int:
    if args:
        return _refuse(ctx, "workshop takes no words")
    rows, unsafe = _rows()
    _print(ctx, [r for r in rows if r.state in ("waiting", "download")], "waiting for review", "waiting")
    _left_out(ctx, unsafe)
    return DONE


def _scan(ctx: Context, args: list[str]) -> int:
    if args:
        return _refuse(ctx, "scan takes no words")
    rows, unsafe = _rows()
    new = sum(1 for r in rows if r.state == "download")
    waiting = sum(1 for r in rows if r.state == "waiting")
    if ctx.json:
        print(json.dumps({"new": new, "waiting": waiting}, separators=(",", ":")), file=ctx.out)
    else:
        print(f"{new} new Workshop downloads, {waiting} waiting for review; lwe workshop lists them.",
              file=ctx.out)
    _left_out(ctx, unsafe)
    return DONE


def _compress(ctx: Context, args: list[str]) -> int:
    options = [a for a in args if a.startswith("-")]
    words = [a for a in args if not a.startswith("-")]
    if options not in ([], ["--all"]) or bool(words) == bool(options):
        return _refuse(ctx, "compress takes wallpapers or --all")
    from ... import texcomp
    from ...library import catalog, compress
    from .. import select

    rows = catalog.wallpaper_rows()[0]
    if options:
        named = [r for r in rows if r.state in ("pool", "missing")]
    else:
        try:
            picks = select.wallpapers(words)
        except select.PickError as exc:
            return select.report(ctx, exc)
        for p in picks:
            if p.source == "trashed":
                message = f"{p.title} ({p.ui_id}) is in the trash; untrash it first"
                ctx.error(message, "lwe: " + message)
                return REFUSED
        by_id = {r.id: r for r in rows}
        named = [by_id.get(p.ui_id) or catalog.Row(0, p.ui_id, p.title, p.alias, "", p.source, False)
                 for p in picks]
    if not texcomp.shim_available():
        message = f"the texture encoder {texcomp.shim_path()} is missing; nothing was compressed"
        ctx.error(message, "lwe: " + message)
        return REFUSED
    results = []
    for row in named:
        result = compress.compress_one(row)
        results.append(result)
        if not ctx.json:
            print(compress.result_line(row, result), file=ctx.out, flush=True)
    if ctx.json:
        print(json.dumps({
            "wallpapers": [{"id": row.id, "title": row.title, "result": r.kind, "bytes_before": r.before,
                            "bytes_after": r.after, "failed": r.failed, "disk_bytes": r.disk}
                           for row, r in zip(named, results)],
            "total": {"compressed": sum(1 for r in results if compress.written(r)), "named": len(results),
                      "bytes_before": sum(r.before for r in results),
                      "bytes_after": sum(r.after for r in results),
                      "disk_bytes": sum(r.disk for r in results)},
        }, ensure_ascii=False, separators=(",", ":")), file=ctx.out)
    elif len(results) > 1:
        print(compress.total_line(results), file=ctx.out)
    return DONE


VERBS = (
    Verb("list", _list, "Your library, numbered by title, with each wallpaper's id and alias.", "library"),
    Verb("workshop", _workshop, "Workshop downloads that are not in your pool yet, numbered, including ones "
         "waiting for review.", "library"),
    Verb("scan", _scan, "Looks for new Workshop downloads now.", "library"),
    Verb("compress", _compress, "Builds the compressed textures that make wallpapers load faster and use "
         "less video memory.", "library"),
)
