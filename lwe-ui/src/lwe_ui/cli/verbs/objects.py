"""The words of `wallpaper <w>` that read one wallpaper's scene: `properties` lists its knobs with the
values in force, and `objects [<filter>]` lists its scene's parts as a tree. `hide <id>` and
`unhide <id>` change the wallpaper's SKIP through the change runner, and `blink <id>` hides a part
on screen for two seconds, writing nothing.

This module registers no verb: `wallpaper` is the wallpaper command's. Each entry of WALLPAPER_WORDS
is run(ctx, pick, args) -> exit code, pick being a select.Pick and args the words after the entry's
word. properties and objects read files only and write nothing.
"""
from __future__ import annotations

import json
import re
import signal
import time

from .. import DONE, ENGINE_DOWN, REFUSED, USAGE

VERBS = ()

MAX_PART_ID = 1000000
MAX_HIDDEN = 256
BLINK_S = 2.0
_DIGITS = re.compile(r"[0-9]+")
_ID_TEXT = "a part id is a whole number from 0 to 1000000"


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
    knobs = scene.knobs(pick.ui_id, typed=ctx.json)
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


def _label(pick) -> str:
    return f"{pick.title} ({pick.ui_id})"


def _part_id(args: list[str]) -> int | None:
    """The one part id args name, ASCII digits from 0 to MAX_PART_ID, else None."""
    if len(args) != 1 or not _DIGITS.fullmatch(args[0]):
        return None
    digits = args[0].lstrip("0") or "0"
    return int(digits) if len(digits) <= len(str(MAX_PART_ID)) and int(digits) <= MAX_PART_ID else None


def _scene_part(ctx, pick, objid: int) -> dict | int:
    """The part objid names in the scene of the pick's render folder, or the exit code of the
    refusal: files missing, a video or web, or no such part."""
    from ...discovery import project
    from ...library import catalog, scene

    folder = catalog.render_dir(pick.ui_id)
    if not folder:
        return _files_missing(ctx, pick)
    if project.read(folder)["type"] in ("video", "web"):
        return _refuse(ctx, "only scenes have parts", REFUSED)
    found = scene.part(folder, objid)
    if found is None:
        return _refuse(ctx, f"{_label(pick)} has no part {objid}", REFUSED)
    return found


def _stored_skip(pick) -> list[int]:
    from ...library import scene
    from ...storage import wp

    return scene.skip_ids(str(wp.load_set(pick.ui_id).get("SKIP") or ""))


def _skip_edit(objid: int, hide: bool):
    """wp.modify_set's fn: the SKIP line with objid appended, or with every token naming it
    (storage/wp.py::skip_id) removed; an emptied SKIP deletes the key."""
    from ...storage import wp

    def edit(raw: dict) -> dict:
        tokens = str(raw.get("SKIP") or "").split()
        kept = [tok for tok in tokens if wp.skip_id(tok) != objid]
        if hide:
            return {} if len(kept) < len(tokens) else {"SKIP": " ".join(tokens + [str(objid)])}
        return {} if len(kept) == len(tokens) else {"SKIP": " ".join(kept) or None}
    return edit


def _change(ctx, pick, objid: int, hide: bool, name: str = "") -> int:
    """The SKIP change through the change runner, in its order: one status read with the
    version check before any lock, then the overrides lock, the marker, the SKIP line and the
    wallpaper's live push. The receipt names the part by name, else by id."""
    from ... import version
    from .. import report
    from ...engine import push
    from ...storage import wp

    first = push.read_status()
    if first[0] == "ok":
        refusal = version.running_refusal(first[1], version.panel_stamp())
        if refusal is not None:
            return _refuse(ctx, refusal, REFUSED)
    try:
        outcome = push.run_change(("overrides",), lambda: wp.modify_set(pick.ui_id, _skip_edit(objid, hide)),
                                  [("wp_live", "SKIP")], wid=pick.ui_id, status=first, run="command")
    except ValueError as exc:
        return _refuse(ctx, str(exc), REFUSED)
    print(f"{_label(pick)} part {objid} {'hidden' if hide else 'shown again'}", file=ctx.out)
    print(report.change_text(report.change_receipt(f"{name or f'part {objid}'} of {pick.title}", outcome)),
          file=ctx.out)
    return REFUSED if outcome.kind == "refused" or outcome.reason == "version" else DONE


def _hide(ctx, pick, args: list[str]) -> int:
    objid = _part_id(args)
    if objid is None:
        return _refuse(ctx, _ID_TEXT, USAGE)
    found = _scene_part(ctx, pick, objid)
    if isinstance(found, int):
        return found
    ids = _stored_skip(pick)
    if objid in ids:
        print(f"{_label(pick)} part {objid} is already hidden", file=ctx.out)
        return DONE
    if len(ids) >= MAX_HIDDEN:
        return _refuse(ctx, "at most 256 parts can be hidden", REFUSED)
    return _change(ctx, pick, objid, hide=True, name=found["name"])


def _unhide(ctx, pick, args: list[str]) -> int:
    objid = _part_id(args)
    if objid is None:
        return _refuse(ctx, _ID_TEXT, USAGE)
    if objid not in _stored_skip(pick):
        print(f"{_label(pick)} part {objid} was not hidden", file=ctx.out)
        return DONE
    return _change(ctx, pick, objid, hide=False)


def _on_screen(status: dict | None) -> str:
    """The wallpaper status names on screen: current.ui_id, else current.id."""
    current = (status or {}).get("current")
    return str(current.get("ui_id") or current.get("id") or "") if isinstance(current, dict) else ""


def _blink_status(ctx, pick) -> int | None:
    """One status read and its checks: the exit code of the refusal, or None when the engine of
    this build shows the pick on the screens."""
    from ... import api_client, version

    status = api_client.status()
    if status is None:
        if not api_client.available():
            return _refuse(ctx, "the service is not running", ENGINE_DOWN)
        return _refuse(ctx, "the service is not answering", REFUSED)
    refusal = version.running_refusal(status, version.panel_stamp())
    if refusal is not None:
        return _refuse(ctx, refusal, REFUSED)
    if _on_screen(status) != pick.ui_id:
        return _refuse(ctx, f"{_label(pick)} is not on screen", REFUSED)
    outputs = status.get("outputs")
    if isinstance(outputs, dict) and outputs.get("state") == "released":
        return _refuse(ctx, "the wallpaper is off the screens; lwe on puts it back", REFUSED)
    return None


class _Stopped(Exception):
    pass


def _wait(seconds: float) -> bool:
    """Sleep with SIGINT, SIGTERM and SIGHUP handled; True when one of them arrived."""
    state = {"waiting": True, "stopped": False}

    def stop(signum, frame) -> None:
        state["stopped"] = True
        if state["waiting"]:
            state["waiting"] = False
            raise _Stopped

    old = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        time.sleep(seconds)
    except _Stopped:
        pass
    finally:
        state["waiting"] = False
        for sig, handler in old.items():
            signal.signal(sig, handler)
    return state["stopped"]


def _refused_skip(reply) -> str | None:
    """The engine's reason when a set-skip reply refused it."""
    return str(reply.get("error") or "") if isinstance(reply, dict) and reply.get("ok") is False else None


def _blink(ctx, pick, args: list[str]) -> int:
    objid = _part_id(args)
    if objid is None:
        return _refuse(ctx, _ID_TEXT, USAGE)
    found = _scene_part(ctx, pick, objid)
    if isinstance(found, int):
        return found
    from ... import api_client
    from ...engine import push
    from ...storage import lock

    refused = _blink_status(ctx, pick)
    if refused is not None:
        return refused
    baseline = _stored_skip(pick)
    if objid in baseline or not found["visible"]:
        return _refuse(ctx, f"part {objid} is already hidden", REFUSED)
    try:
        with push.engine_only():
            refused = _blink_status(ctx, pick)
            if refused is not None:
                return refused
            reason = _refused_skip(api_client.set_skip(baseline + [objid]))
    except lock.StoreBusy as exc:
        return _refuse(ctx, str(exc), REFUSED)
    if reason is not None:
        return _refuse(ctx, reason, REFUSED)
    stopped = _wait(BLINK_S)
    try:
        with push.engine_only():
            if _on_screen(api_client.status()) == pick.ui_id:
                reason = _refused_skip(api_client.set_skip(baseline))
    except lock.StoreBusy:
        return _refuse(ctx, f"could not show part {objid} again; the next show brings it back", REFUSED)
    if reason is not None:
        return _refuse(ctx, reason, REFUSED)
    if stopped:
        return _refuse(ctx, f"blink stopped; part {objid} is shown again", REFUSED)
    print(f"Blinked part {objid} ({found['name']}) of {pick.title} ({pick.ui_id})", file=ctx.out)
    return DONE


WALLPAPER_WORDS = {"properties": _properties, "objects": _objects, "hide": _hide, "unhide": _unhide,
                   "blink": _blink}
