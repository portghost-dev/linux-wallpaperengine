"""The words of `wallpaper <w>` that read one wallpaper's scene: `properties` lists its knobs with the
values in force, and `objects [<filter>]` lists its scene's parts as a tree.

This module registers no verb: `wallpaper` is the wallpaper command's. Each entry of WALLPAPER_WORDS
is run(ctx, pick, args) -> exit code, pick being a select.Pick and args the words after the entry's
word. Both read files only and write nothing.
"""
from __future__ import annotations

import json

from .. import DONE, REFUSED, USAGE

VERBS = ()


def _shown(value: object) -> str:
    """A value as text: a string as it is, anything else as JSON writes it."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _print_json(ctx, data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")), file=ctx.out)


def _refuse(ctx, message: str, code: int) -> int:
    ctx.error(message, "lwe: " + message)
    return code


def _files_missing(ctx, pick) -> int:
    return _refuse(ctx, f"{pick.title} ({pick.ui_id}): its files are missing", REFUSED)


def _knob_line(knob: dict) -> str:
    line = f"{knob['name']}  {knob['label']}: {_shown(knob['value'])}"
    if knob["yours"]:
        line += " (yours)"
    if knob["kind"] == "combo":
        line += "  choices: " + ", ".join(_shown(option["value"]) for option in knob["options"] or [])
    elif knob["kind"] == "slider":
        line += f"  range {_shown(knob['min'])} to {_shown(knob['max'])}, step {_shown(knob['step'])}"
    return line


def _properties(ctx, pick, args: list[str]) -> int:
    if args:
        return _refuse(ctx, "properties takes no words", USAGE)
    from ...library import catalog, scene

    if not catalog.render_dir(pick.ui_id):
        return _files_missing(ctx, pick)
    knobs = scene.knobs(pick.ui_id)
    if ctx.json:
        _print_json(ctx, knobs)
    elif not knobs:
        print("no properties", file=ctx.out)
    else:
        for knob in knobs:
            print(_knob_line(knob), file=ctx.out)
    return DONE


def _objects(ctx, pick, args: list[str]) -> int:
    if len(args) > 1:
        return _refuse(ctx, "objects takes at most one filter word", USAGE)
    from ...discovery import project
    from ...library import catalog, scene
    from ...storage import wp

    folder = catalog.render_dir(pick.ui_id)
    if not folder:
        return _files_missing(ctx, pick)
    if project.read(folder)["type"] in ("video", "web"):
        return _refuse(ctx, "only scenes have parts", REFUSED)
    skip = scene.skip_ids(str(wp.load_set(pick.ui_id).get("SKIP") or ""))
    rows = scene.tree(folder, skip, args[0] if args else None)
    if ctx.json:
        _print_json(ctx, [{"id": part["objid"], "type": part["type"], "name": part["name"],
                           "parent": part["parent"], "depth": depth, "author_hidden": author_hidden,
                           "you_hidden": you_hidden}
                          for depth, part, author_hidden, you_hidden in rows])
        return DONE
    for depth, part, author_hidden, you_hidden in rows:
        line = "  " * depth + f"{part['objid']}  {part['type']}  {part['name']}"
        if author_hidden:
            line += "  hidden by its author"
        if you_hidden:
            line += "  hidden by you"
        print(line, file=ctx.out)
    return DONE


WALLPAPER_WORDS = {"properties": _properties, "objects": _objects}
