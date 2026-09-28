"""The schedule line, SCHEDULE="HH:MM=slug;HH:MM=slug": day's row first, night's second, by
position only. Rows are read as written: nothing here sorts, drops or cuts a row, and a read
never cleans the line. A stored time is exactly HH:MM in ASCII digits, hour 00-23, minute 00-59,
the form the engine accepts. Plain Python over the store; no Qt import.
"""
from __future__ import annotations

import re

from . import paths

_PARTS = ("day", "night")
_TIME = re.compile(r"([0-9]{2}):([0-9]{2})")
_ENGINE_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _minutes(time: str) -> int | None:
    m = _TIME.fullmatch(time)
    if m is None or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def _exists(slug: str) -> bool:
    return paths.is_safe_wid(slug) and paths.playlist_file(slug).exists()


def parse_rows(text: str) -> list[tuple[str, str | None]]:
    """The SCHEDULE value as rows in stored order, each (time, playlist) exactly as written, an
    empty playlist kept as ""; a row without "=" is (the row, None). An empty value has no rows."""
    if not text.strip():
        return []
    rows: list[tuple[str, str | None]] = []
    for piece in text.split(";"):
        time, eq, playlist = piece.partition("=")
        rows.append((time, playlist) if eq else (piece, None))
    return rows


def format_rows(rows: list[tuple[str, str | None]]) -> str:
    """Rows back into the SCHEDULE value, in their order; format_rows(parse_rows(text)) == text."""
    return ";".join(time if playlist is None else f"{time}={playlist}" for time, playlist in rows)


def set_field(rows: list[tuple[str, str | None]], part: str, field: str,
              value: int | str) -> list[tuple[str, str | None]]:
    """A copy of `rows` with one field of day's or night's row set: "time" takes minutes of the
    day and stores HH:MM, "playlist" takes a slug. With no rows the rows start as
    "08:00=;20:00="; one row is day, and night takes 20:00, or 08:00 when day holds 20:00. More
    than two rows, or a row without "=", cannot be edited here: ValueError."""
    rows = list(rows)
    if any(playlist is None for _time, playlist in rows) or len(rows) > 2:
        raise ValueError("only a schedule of at most two time=playlist rows can be edited")
    if not rows:
        rows = [("08:00", ""), ("20:00", "")]
    elif len(rows) == 1:
        rows.append(("08:00" if _minutes(rows[0][0]) == 20 * 60 else "20:00", ""))
    index = _PARTS.index(part)
    time, playlist = rows[index]
    if field == "time":
        if not isinstance(value, int) or not 0 <= value < 24 * 60:
            raise ValueError(f"a time is minutes of the day, 0 to 1439: {value!r}")
        rows[index] = (f"{value // 60:02d}:{value % 60:02d}", playlist)
    elif field == "playlist":
        rows[index] = (time, str(value))
    else:
        raise ValueError(f"unknown schedule field: {field!r}")
    return rows


def check_on(rows: list[tuple[str, str | None]]) -> list[tuple[str, str]]:
    """What keeps these rows from turning the schedule on, as (reason, part) pairs; empty when
    nothing does. "rows": not exactly two time=playlist rows (nothing else is checked then);
    then per part, day first: "time" (not a stored time), "unset" (no playlist), "name" (a file
    name the engine refuses, [A-Za-z0-9_-]{1,64}), "missing" (no such playlist file); then, with
    part "": "same time" and "same playlist"."""
    if len(rows) != 2 or any(playlist is None for _time, playlist in rows):
        return [("rows", "")]
    reasons: list[tuple[str, str]] = []
    for part, (time, playlist) in zip(_PARTS, rows):
        if _minutes(time) is None:
            reasons.append(("time", part))
        if not playlist:
            reasons.append(("unset", part))
            continue
        if not _ENGINE_NAME.fullmatch(playlist):
            reasons.append(("name", part))
        if not _exists(playlist):
            reasons.append(("missing", part))
    times = [_minutes(time) for time, _playlist in rows]
    if None not in times and times[0] == times[1]:
        reasons.append(("same time", ""))
    if rows[0][1] and rows[0][1] == rows[1][1]:
        reasons.append(("same playlist", ""))
    return reasons


def check_reload(text: str, enabled: bool) -> list[tuple[str, str, str]]:
    """Reload's rules for the SCHEDULE value `text` with SCHEDULE_ENABLED `enabled`, as
    (reason, part, line) triples naming the SCHEDULE line; empty when the line passes. An empty
    value is valid only while the schedule is off; a populated one must be two time=playlist
    entries with valid, different times, on or off ("rows", "time", "same time"); its playlists
    must exist only while the schedule is on ("unset", "missing")."""
    line = f"SCHEDULE={text}"
    rows = parse_rows(text)
    if not rows:
        return [("rows", "", line)] if enabled else []
    if len(rows) != 2 or any(playlist is None for _time, playlist in rows):
        return [("rows", "", line)]
    reasons: list[tuple[str, str, str]] = []
    times = [_minutes(time) for time, _playlist in rows]
    for part, minutes in zip(_PARTS, times):
        if minutes is None:
            reasons.append(("time", part, line))
    if None not in times and times[0] == times[1]:
        reasons.append(("same time", "", line))
    if enabled:
        for part, (_time, playlist) in zip(_PARTS, rows):
            if not playlist:
                reasons.append(("unset", part, line))
            elif not _exists(playlist):
                reasons.append(("missing", part, line))
    return reasons


def day_range(rows: list[tuple[str, str | None]]) -> tuple[int, int]:
    """Where day begins and ends in minutes of the local day: the first two valid stored times,
    whether the schedule is on or off; 08:00 and 20:00 when fewer are valid."""
    times = [m for m in (_minutes(time) for time, _playlist in rows) if m is not None]
    if len(times) >= 2:
        return times[0], times[1]
    return 8 * 60, 20 * 60


def is_day_at(minute: int, day_from: int, day_to: int) -> bool:
    """Whether `minute` of the day falls in day, which runs from day_from until day_to and
    wraps past midnight when day_from is later."""
    if day_from <= day_to:
        return day_from <= minute < day_to
    return minute >= day_from or minute < day_to
