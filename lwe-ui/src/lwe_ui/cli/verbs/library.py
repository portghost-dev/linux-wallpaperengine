"""lwe list, workshop and scan: the library catalog printed. They only read; nothing is locked,
written or sent to the engine."""
from __future__ import annotations

import json

from .. import DONE, USAGE, Context
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


VERBS = (
    Verb("list", _list, "Your library, numbered by title, with each wallpaper's id and alias.", "library"),
    Verb("workshop", _workshop, "Workshop downloads that are not in your pool yet, numbered, including ones "
         "waiting for review.", "library"),
    Verb("scan", _scan, "Looks for new Workshop downloads now.", "library"),
)
