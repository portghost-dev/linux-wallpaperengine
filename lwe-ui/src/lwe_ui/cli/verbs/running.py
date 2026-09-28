"""show, next, prev and pause: the running commands that show a wallpaper, step through the playlist or
hold its timer.

show picks one wallpaper by the rules in cli/select.py and, once status answers, sends it through
engine/push.py::show_final under the sync lock, held until the load finishes; it starts no service,
writes nothing and is never retried.

next and prev are engine-only: one status read first, then the engine's next or prev sent under the
sync lock, which stays held until the engine's final reply; they write nothing, leave the playlist timer
as it is and are never retried. pause saves ROTATION_ENABLED through the change runner
(engine/push.py::save_change and deliver), whose own push is one lanes-set with the lane's enabled
state; it never sends set-speed, and a value already saved is neither written nor sent.
"""
from __future__ import annotations

import json

from .. import DONE, ENGINE_DOWN, REFUSED, USAGE, Context, report, vocabulary
from ..registry import Verb
from ..values import UsageError, parse_switch

EMPTY_HINT = "The service has no playlist loaded; lwe reload sends yours."
STATIC = "the playlist is static, so the timer is not running anyway"


def _print_json(ctx: Context, data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")), file=ctx.out)


def _refuse(ctx: Context, message: str, code: int) -> int:
    ctx.error(message)
    return code


def _countdown(seconds: int) -> str:
    """Seconds as the engine's status prints its countdown: 1h 05m 03s, 7m 02s or 45s."""
    hours, minutes, rest = seconds // 3600, seconds % 3600 // 60, seconds % 60
    if hours:
        return f"{hours}h {minutes:02d}m {rest:02d}s"
    if minutes:
        return f"{minutes}m {rest:02d}s"
    return f"{rest}s"


def _step(ctx: Context, name: str, args: list[str]) -> int:
    """next or prev: the status read, then the request under the sync lock until its final reply,
    then one status read for what is on screen."""
    if args:
        return _refuse(ctx, f"{name} takes no words; got {' '.join(args)}", USAGE)
    from ... import api_client, version
    from ...engine import push
    from ...storage.lock import StoreBusy

    kind, status = push.read_status()
    if status is None:
        if kind == "away":
            return _refuse(ctx, "the service is not running", ENGINE_DOWN)
        return _refuse(ctx, "the service is not answering", REFUSED)
    refusal = version.running_refusal(status, version.panel_stamp())
    if refusal is not None:
        return _refuse(ctx, refusal, REFUSED)
    send = api_client.next_wallpaper if name == "next" else api_client.prev_wallpaper
    try:
        with push.engine_only():
            reply = send(wait_done=True)
            nothing_ran = api_client.last_class() == "away"
    except StoreBusy:
        return _refuse(ctx, "the service is busy", REFUSED)
    if not isinstance(reply, dict):
        if nothing_ran:
            return _refuse(ctx, "the service is not running", ENGINE_DOWN)
        return _refuse(ctx, "accepted but not finished", REFUSED)
    cls = api_client.reply_class(reply)
    if cls == "refused":
        reason = str(reply.get("error") or "")
        ctx.error(reason)
        if reason == "rotation set is empty" and not ctx.json:
            print(EMPTY_HINT, file=ctx.err)
        return REFUSED
    if cls != "ok":
        return _refuse(ctx, "accepted but not finished", REFUSED)
    after = api_client.status()
    current = after.get("current") if isinstance(after, dict) else None
    if not isinstance(current, dict):
        result = reply.get("result")
        current = result if isinstance(result, dict) else {}
    wid, ui_id = str(current.get("id") or ""), str(current.get("ui_id") or "")
    title, shown = str(current.get("title") or ""), ui_id or wid
    if ctx.json:
        _print_json(ctx, {"id": wid, "ui_id": ui_id, "title": title})
    else:
        print(f"Now showing {title or shown} ({shown})", file=ctx.out)
    return DONE


def _next(ctx: Context, args: list[str]) -> int:
    return _step(ctx, "next", args)


def _prev(ctx: Context, args: list[str]) -> int:
    return _step(ctx, "prev", args)


def _applied_text(paused: bool) -> str:
    """The receipt of an applied pause, from one status read after the push."""
    from ... import api_client
    status = api_client.status()
    rotation = status.get("rotation") if isinstance(status, dict) else None
    rotation = rotation if isinstance(rotation, dict) else {}
    if rotation.get("order") == "static":
        return f"Playlist timer {'paused' if paused else 'on'}; {STATIC}."
    if paused:
        return "Playlist timer paused."
    left = rotation.get("next_in_s")
    if isinstance(left, int) and not isinstance(left, bool) and left >= 0:
        return f"Playlist timer running again; next change in {_countdown(left)}."
    return "Playlist timer running again."


def _pause(ctx: Context, args: list[str]) -> int:
    """pause [on|off|toggle], bare on: the status read, then ROTATION_ENABLED read and written in one
    hold of the settings lock, the marker set through the change runner, and its delivery once the
    lock is released."""
    if len(args) > 1:
        return _refuse(ctx, f"pause takes one value; got {' '.join(args)}", USAGE)
    try:
        word = parse_switch(args[0]) if args else "on"
    except UsageError as exc:
        return _refuse(ctx, f"pause {exc}", USAGE)
    from ... import version
    from ...engine import push
    from ...storage import lock, settings

    first = push.read_status()
    if first[1] is not None:
        refusal = version.running_refusal(first[1], version.panel_stamp())
        if refusal is not None:
            return _refuse(ctx, refusal, REFUSED)
    with lock.held("settings"):
        was_paused = not settings.load()["ROTATION_ENABLED"]
        paused = not was_paused if word == "toggle" else word == "on"
        if paused == was_paused:
            if ctx.json:
                _print_json(ctx, {"paused": paused, "saved": False, "outcome": None, "reason": None})
            else:
                print("already paused" if paused else "already running", file=ctx.out)
            return DONE
        ticket = push.save_change(("settings",), lambda: settings.update({"ROTATION_ENABLED": not paused}),
                                  [(push.SETTING_ROWS["ROTATION_ENABLED"], "ROTATION_ENABLED")],
                                  status=first, run="command")
    outcome = push.deliver(ticket)
    if outcome.kind == "pending" and outcome.reason == "version":
        return _refuse(ctx, str(outcome.message), REFUSED)
    if ctx.json:
        reason = outcome.message if outcome.kind == "refused" else outcome.reason
        _print_json(ctx, {"paused": paused, "saved": True, "outcome": outcome.kind, "reason": reason})
    elif outcome.kind == "applied":
        print(_applied_text(paused), file=ctx.out)
    else:
        print(report.text(report.receipt("pause", "on" if paused else "off", True, "now", outcome.kind,
                                         outcome.message or "")), file=ctx.out)
    if outcome.warning and not ctx.json:
        print(outcome.warning, file=ctx.err)
    return REFUSED if outcome.kind == "refused" else DONE


def _show(ctx: Context, args: list[str]) -> int:
    """show <w>: the pick and its line, the status read, then show_final under the sync lock until
    its final reply."""
    if len(args) != 1:
        return _refuse(ctx, f"show takes one wallpaper; got {len(args)} words", USAGE)
    from ... import api_client, version
    from ...engine import push
    from ...storage.lock import StoreBusy
    from .. import select

    try:
        pick = select.wallpaper(args[0])
    except select.PickError as exc:
        return select.report(ctx, exc)
    line = select.pick_line(pick)

    def refused(message: str, code: int) -> int:
        if not ctx.json:
            print(line, file=ctx.out)
        return _refuse(ctx, message, code)

    if pick.source == "download":
        return refused(f"not in your pool yet; lwe add {pick.number} brings it in, lwe bench {pick.number} tries it",
                       REFUSED)
    kind, status = push.read_status()
    if status is None:
        if kind == "away":
            return refused("the service is not running", ENGINE_DOWN)
        return refused("the service is not answering", REFUSED)
    refusal = version.running_refusal(status, version.panel_stamp())
    if refusal is not None:
        return refused(refusal, REFUSED)
    outputs = status.get("outputs")
    released = isinstance(outputs, dict) and outputs.get("state") == "released"
    try:
        with push.engine_only():
            reply = push.show_final(pick.ui_id)
            nothing_ran = api_client.last_class() == "away"
    except StoreBusy:
        return refused("the service is busy", REFUSED)
    if not isinstance(reply, dict):
        if nothing_ran:
            return refused("the service is not running", ENGINE_DOWN)
        return refused("accepted but not finished", REFUSED)
    cls = api_client.reply_class(reply)
    if cls == "refused":
        return refused(str(reply.get("error") or ""), REFUSED)
    if cls != "ok":
        return refused("accepted but not finished", REFUSED)
    if ctx.json:
        _print_json(ctx, {"number": pick.number, "id": pick.ui_id, "title": pick.title,
                          "screens_back_on": released})
    else:
        print(line + (" (screens back on)" if released else ""), file=ctx.out)
    return DONE


def _what(name: str) -> str:
    return next(row["what"] for row in vocabulary.COMMANDS if row["name"] == name)


VERBS = (
    Verb("show", _show, _what("show"), "Running"),
    Verb("next", _next, _what("next"), "Running"),
    Verb("prev", _prev, _what("prev"), "Running"),
    Verb("pause", _pause, _what("pause"), "Running"),
)
