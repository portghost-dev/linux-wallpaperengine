"""schedule: day and night, each a start time and a playlist, and whether the schedule is on.

Bare schedule reads the saved SCHEDULE and SCHEDULE_ENABLED and one status; it takes no lock and
writes nothing. schedule on|off and the day or night time and playlist edits read status once
before any lock, then save under the settings lock through engine/push.py's change runner with the
schedule row: every scheduled playlist, then schedule-set; never lanes-set, never a manual switch.
Rows are read and edited with storage/schedule.py, which never sorts, drops or cuts a row.
"""
from __future__ import annotations

import json
import time
from typing import Any

from .. import DONE, REFUSED, USAGE, Context, report
from ..registry import Verb
from ..values import UsageError, format_time, parse_time

USAGE_TEXT = ("usage: lwe schedule, lwe schedule on|off, lwe schedule day|night time <time> or "
              "lwe schedule day|night playlist <playlist>")
_PARTS = ("day", "night")
_AWAY = "the service is not running; live state unknown"
_SILENT = "the service is not answering; live state unknown"
_OFF_FIRST = "Turn the schedule off first. Nothing changed."
_STAYS_OFF = "The schedule stays off. Nothing changed."
_HELD = "(the playlist you picked by hand stops holding)"
_DIFFERS = "The running service has a different schedule; lwe reload sends the saved one."


def _print_json(ctx: Context, data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")), file=ctx.out)


def _refuse(ctx: Context, lines: list[str], code: int = REFUSED) -> int:
    """Each line on err, or one {"error"} line under -j."""
    if ctx.json:
        ctx.error(" ".join(lines))
    else:
        for line in lines:
            print(line, file=ctx.err)
    return code


def _usage(ctx: Context, message: str = USAGE_TEXT) -> int:
    ctx.error(message)
    return USAGE


def _first_read(ctx: Context, write: bool) -> tuple[tuple[str, dict | None], int | None]:
    """One status read before any lock: the read, and REFUSED for a write when the running
    engine is from another build. A read names the mismatch on err and goes on."""
    from ... import version
    from ...engine import push
    first = push.read_status()
    refusal = version.running_refusal(first[1], version.panel_stamp()) if first[1] is not None else None
    if refusal is None:
        return first, None
    ctx.error(refusal)
    return first, REFUSED if write else None


def _numbers() -> dict[str, tuple[int, str]]:
    from .. import select
    return {row.slug: (row.number, row.name) for row in select.playlist_rows()}


def _named(slug: str | None, numbers: dict[str, tuple[int, str]]) -> str:
    """A playlist as the schedule prints it: name and number, "missing" or "not set"."""
    if not slug:
        return "not set"
    if slug in numbers:
        number, name = numbers[slug]
        return f"{name or slug} ({number})"
    return f"{slug} (missing)"


def _minute_now() -> int:
    now = time.localtime()
    return now.tm_hour * 60 + now.tm_min


def _rows_refusal(rows: list) -> str:
    if len(rows) > 2:
        return (f"This schedule has {len(rows)} entries; day and night editing needs two. Nothing changed; "
                "fix the SCHEDULE line and run lwe reload.")
    if any(playlist is None for _time, playlist in rows):
        return ('This schedule has an entry without "="; day and night editing needs two time=playlist '
                "entries. Nothing changed; fix the SCHEDULE line and run lwe reload.")
    return (f"The schedule has {len(rows)} of its two entries; day and night each need a time and a "
            "playlist. Nothing changed.")


def _reason(reason: str, part: str, rows: list, numbers: dict[str, tuple[int, str]]) -> str:
    """One check_on reason as a line."""
    if not part:
        if reason == "same time":
            return f"Day and night both start at {rows[0][0]}."
        return f"Day and night both play {_named(rows[0][1], numbers)}."
    title = part.capitalize()
    at, slug = rows[_PARTS.index(part)]
    if reason == "time":
        return f"{title}'s time {at} is not a time the engine takes (HH:MM)."
    if reason == "unset":
        return f"{title} has no playlist yet (lwe schedule {part} playlist <playlist>)."
    if reason == "name":
        return (f"{title}'s playlist file {slug}.conf has a name the engine refuses: use only letters, "
                "digits, - and _, up to 64.")
    return f"{title}'s playlist {slug} has no file."


def _outcome(ctx: Context, value: str, lines: list[str], outcome: Any) -> int:
    """The receipt of a saved change: the lines when applied, else cli/report.py's receipt."""
    if outcome.kind == report.APPLIED and not ctx.json:
        for line in lines:
            print(line, file=ctx.out)
        return DONE
    reason = (outcome.message or "") if outcome.kind == report.REFUSED else ""
    report.emit(ctx, report.receipt("schedule", value, True, "now", outcome.kind, reason))
    return REFUSED if outcome.kind == report.REFUSED else DONE


def _unchanged(ctx: Context, value: str, line: str) -> int:
    if ctx.json:
        report.emit(ctx, report.receipt("schedule", value, False, "now"))
    else:
        print(line, file=ctx.out)
    return DONE


def _bound(status: dict | None) -> str:
    """The playlist the engine's lane is bound to, or ""."""
    lanes = status.get("lanes") if isinstance(status, dict) else None
    if isinstance(lanes, list) and lanes and isinstance(lanes[0], dict):
        return str(lanes[0].get("playlist") or "")
    return ""


def _switch(ctx: Context, turn_on: bool) -> int:
    from ...engine import push
    from ...storage import lock, schedule, settings
    word = "on" if turn_on else "off"
    first, code = _first_read(ctx, write=True)
    if code is not None:
        return code
    with lock.held("settings"):
        current = settings.load()
        if bool(current.get("SCHEDULE_ENABLED")) == turn_on:
            return _unchanged(ctx, word, f"Schedule already {word}.")
        rows = schedule.parse_rows(str(current.get("SCHEDULE") or ""))
        changes: dict[str, Any] = {"SCHEDULE_ENABLED": turn_on}
        if turn_on:
            reasons = schedule.check_on(rows)
            if reasons:
                numbers = _numbers()
                if reasons[0][0] == "rows":
                    return _refuse(ctx, [_rows_refusal(rows)])
                return _refuse(ctx, [_reason(r, p, rows, numbers) for r, p in reasons] + [_STAYS_OFF])
        elif _bound(first[1]):
            changes["ACTIVE_PLAYLIST"] = _bound(first[1])
        ticket = push.save_change(("settings",), lambda: settings.update(changes),
                                  [("schedule", "SCHEDULE_ENABLED")], status=first, run="command")
    outcome = push.deliver(ticket)
    if not turn_on:
        return _outcome(ctx, word, ["Schedule off; day and night stay as you set them."], outcome)
    is_day = schedule.is_day_at(_minute_now(), *schedule.day_range(rows))
    numbers = _numbers()
    slug = rows[0 if is_day else 1][1]
    name = numbers[slug][1] if slug in numbers and numbers[slug][1] else slug
    line = f"Schedule on. It is {'day' if is_day else 'night'} now: {name} plays from the next change."
    return _outcome(ctx, word, [line], outcome)


def _edit(ctx: Context, part: str, field: str, value: Any) -> int:
    from ...engine import push
    from ...storage import lock, schedule, settings
    first, code = _first_read(ctx, write=True)
    if code is not None:
        return code
    title = part.capitalize()
    with lock.held("settings"):
        current = settings.load()
        text = str(current.get("SCHEDULE") or "")
        rows = schedule.parse_rows(text)
        if len(rows) > 2 or any(playlist is None for _time, playlist in rows):
            return _refuse(ctx, [_rows_refusal(rows)])
        new = schedule.set_field(rows, part, field, value if field == "time" else value.slug)
        reasons = schedule.check_on(new)
        if ("same time", "") in reasons:
            return _refuse(ctx, [f"Day and night cannot both start at {new[0][0]}. Nothing changed."])
        if current.get("SCHEDULE_ENABLED") and reasons:
            return _refuse(ctx, [_OFF_FIRST])
        if field == "time":
            shown = format_time(value)
            said, applied, already = f"{part} time {shown}", f"{title} starts at {shown}.", \
                f"{title} already starts at {shown}."
        else:
            shown = f"{value.name or value.slug} ({value.number})"
            said, applied, already = f"{part} playlist {shown}", f"{title} plays {shown}.", \
                f"{title} already plays {shown}."
        after = schedule.format_rows(new)
        if after == text:
            return _unchanged(ctx, said, already)
        ticket = push.save_change(("settings",), lambda: settings.update({"SCHEDULE": after}),
                                  [("schedule", "SCHEDULE")], status=first, run="command")
    outcome = push.deliver(ticket)
    lines = [applied]
    live = first[1].get("schedule") if isinstance(first[1], dict) else None
    if field == "time" and isinstance(live, dict) and live.get("held"):
        lines.append(_HELD)
    return _outcome(ctx, said, lines, outcome)


def _show(ctx: Context) -> int:
    from ...storage import schedule, settings
    current = settings.load()
    enabled = bool(current.get("SCHEDULE_ENABLED"))
    rows = schedule.parse_rows(str(current.get("SCHEDULE") or ""))
    (cls, status), _code = _first_read(ctx, write=False)
    numbers = _numbers()
    live = status.get("schedule") if isinstance(status, dict) else None
    live = live if isinstance(live, dict) else None
    stored = [{"at": at, "playlist": playlist} for at, playlist in rows]
    same = live is not None and live.get("entries") == stored
    from_engine = same and isinstance(live.get("is_day"), bool)
    is_day = live["is_day"] if from_engine else schedule.is_day_at(_minute_now(), *schedule.day_range(rows))
    editable = len(rows) <= 2 and all(playlist is not None for _time, playlist in rows)
    filled = schedule.set_field(rows, "day", "playlist", rows[0][1] if rows else "") if editable else rows
    slots = []
    for index, part in enumerate(_PARTS):
        at, slug = filled[index] if index < len(filled) else ("", "")
        number = numbers[slug][0] if slug in numbers else None
        slots.append((part, at, slug or "", number, editable and index >= len(rows)))
    now = "day" if is_day else "night"
    if ctx.json:
        _print_json(ctx, {
            "enabled": enabled,
            **{part: {"time": at, "playlist": slug, "number": number} for part, at, slug, number, _d in slots},
            "now": now,
            "held": bool(live.get("held")) if live is not None else None,
            "pending": str(live.get("pending") or "") if live is not None else None,
        })
        return DONE
    if editable:
        for part, at, slug, _number, defaulted in slots:
            print(f"{part.capitalize():<5}  {at}{' (default)' if defaulted else ''}  {_named(slug, numbers)}",
                  file=ctx.out)
    else:
        print("SCHEDULE as stored (day and night editing needs exactly two time=playlist entries):", file=ctx.out)
        for row in schedule.format_rows(rows).split(";"):
            print(f"  {row}", file=ctx.out)
    fallback = not from_engine and sum(1 for at, _playlist in rows if _valid(at)) < 2
    print(f"Schedule {'on' if enabled else 'off'}. It is {now} now"
          f"{' (default times 08:00 to 20:00)' if fallback else ''}.", file=ctx.out)
    if live is not None:
        if live.get("held"):
            print("A playlist picked by hand holds until the next start time.", file=ctx.out)
        if live.get("pending"):
            print(f"Pending: {_named(str(live['pending']), numbers)} plays from the next change.", file=ctx.out)
        if not same:
            print(_DIFFERS, file=ctx.out)
    else:
        print(_AWAY if cls == "away" else _SILENT, file=ctx.out)
    return DONE


def _valid(at: str) -> bool:
    """A stored time: exactly HH:MM in ASCII digits, hour 00-23, minute 00-59."""
    try:
        return format_time(parse_time(at)) == at
    except UsageError:
        return False


def run(ctx: Context, args: list[str]) -> int:
    if not args:
        return _show(ctx)
    if args[0] in ("on", "off"):
        if len(args) != 1:
            return _usage(ctx, f"schedule {args[0]} takes no more words; {USAGE_TEXT}")
        return _switch(ctx, args[0] == "on")
    if args[0] not in _PARTS or len(args) < 2 or args[1] not in ("time", "playlist"):
        return _usage(ctx)
    part, field = args[0], args[1]
    if len(args) != 3:
        hint = "; a name with spaces is one quoted word" if field == "playlist" and len(args) > 3 else ""
        return _usage(ctx, f"schedule {part} {field} takes one value{hint}; {USAGE_TEXT}")
    if field == "time":
        try:
            minutes = parse_time(args[2])
        except UsageError as exc:
            return _usage(ctx, f"schedule {part} time {exc}")
        return _edit(ctx, part, field, minutes)
    from .. import select
    try:
        pick = select.playlist(args[2])
    except select.PickError as exc:
        return select.report(ctx, exc)
    return _edit(ctx, part, field, pick)


VERBS = (
    Verb("schedule", run, "Shows day and night: each start time and playlist, whether the schedule is on, and "
         "whether it is day or night now.", "Schedule"),
)
