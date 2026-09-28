"""order and interval: how the playing playlist moves on, and how long each wallpaper stays up.

Both act on the derived active playlist, the playlist the engine plays for the panel: the engine's
binding while its schedule is on and that playlist's file exists, else the saved active playlist
(cli/settings_table.py::derived_active_playlist). They edit that playlist's file, name it in the
receipt and never write ACTIVE_PLAYLIST. A change saves one line through the change runner, which
sends the playlist's transfer and then the lane's enabled state; an unchanged value writes and sends
nothing. `order load` and `interval load` send the playlist from its file again under the sync lock
and write nothing. UNSET holds config unset's form of each: every assignment deleted, with the same
push.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any

from .. import DONE, ENGINE_DOWN, REFUSED, USAGE, Context, report, settings_table, vocabulary
from ..registry import Verb
from ..values import UsageError, format_duration

NO_PLAYLIST = "No playlist is playing; lwe playlist <p> picks one"
NOT_RUNNING = "the service is not running"
NOT_ANSWERING = "the service is not answering"
NO_ANSWER = "the engine did not answer in time, so it may have applied"
_NAMES = ("order", "interval")


class _Gone(Exception):
    """The playlist's file was removed before its write."""


def _refuse(ctx: Context, message: str, code: int) -> int:
    ctx.error(message)
    return code


def _gone(path: Any) -> str:
    return f"{path} is gone, so nothing was saved"


def _version(ctx: Context, first: tuple[str, dict | None]) -> int | None:
    """The version check of a status read that answered: REFUSED, named on stderr, for a running
    engine from another build; otherwise None."""
    from ... import version
    if first[0] != "ok":
        return None
    refusal = version.running_refusal(first[1], version.panel_stamp())
    return None if refusal is None else _refuse(ctx, refusal, REFUSED)


def _receipt(ctx: Context, name: str, slug: str, saved: bool, kind: str | None = None, reason: str = "",
             clock: tuple[int, int] | None = None, sent: bool = False) -> int:
    """The receipt for the value in force now, naming the playlist; kind is the change runner's
    outcome, None when nothing was sent. A refusal exits 1."""
    from ...storage import playlists
    loaded = playlists.load(slug)
    row = settings_table.BY_NAME[name]
    title, shown = str(loaded["NAME"] or slug), row.format(loaded[row.key])
    subject = f"{name.capitalize()} of {title}"
    if ctx.json:
        print(json.dumps({"playlist": title, "setting": name, "value": shown, "saved": saved, "outcome": kind,
                          "reason": reason}, ensure_ascii=False, separators=(",", ":")), file=ctx.out)
    elif sent:
        print(f"{subject} sent to the engine: {shown}.", file=ctx.out)
    elif kind is None or (kind == report.APPLIED and not saved):
        print(f"{subject} is already {shown}.", file=ctx.out)
    elif kind == report.APPLIED:
        tail = ""
        if name == "interval" and clock:
            tail = f"; next change in {format_duration(math.ceil(clock[0] / 1000))}"
        print(f"{subject} set to {shown}{tail}.", file=ctx.out)
    else:
        print(report.text(report.receipt(f"{subject} set to", shown, saved, row.reach, kind, reason)), file=ctx.out)
    return REFUSED if kind == report.REFUSED else DONE


def _change(ctx: Context, name: str, value: Any) -> int:
    """order or interval set to `value`, or with value None every assignment deleted, through the
    change runner with the playlists lock; the file's presence and an unchanged value are checked
    under that lock first."""
    from ...engine import push
    from ...storage import lock, paths, playlists
    first = push.read_status()
    code = _version(ctx, first)
    if code is not None:
        return code
    slug = settings_table.derived_active_playlist(first[1])[0]
    if slug is None:
        return _refuse(ctx, NO_PLAYLIST, REFUSED)
    key = settings_table.BY_NAME[name].key
    path = paths.playlist_file(slug)
    with lock.held("playlists"):
        current = playlists.load(slug)[key] if path.exists() else None
    if current is None:
        return _refuse(ctx, _gone(path), REFUSED)
    if value is not None and current == value:
        return _receipt(ctx, name, slug, False)
    saved: list[bool] = []

    def write() -> None:
        if not path.exists():
            raise _Gone
        before = path.read_bytes()
        playlists.modify(slug, lambda loaded: {key: value} if value is None or loaded[key] != value else {})
        saved.append(path.read_bytes() != before)

    try:
        outcome = push.run_change(("playlists",), write, [("policy", key)], slug=slug, status=first,
                                  run="command")
    except _Gone:
        return _refuse(ctx, _gone(path), REFUSED)
    return _receipt(ctx, name, slug, bool(saved and saved[0]), outcome.kind,
                    outcome.message or outcome.reason or "", outcome.clock)


def _load(ctx: Context, name: str) -> int:
    """order load or interval load: the playlist sent again from its file under the sync lock, its
    parts and then the lane's enabled state; nothing is written."""
    from ... import api_client
    from ...engine import push
    from ...engine.resolve import split_playlist_parts
    first = push.read_status()
    if first[0] == "away":
        return _refuse(ctx, NOT_RUNNING, ENGINE_DOWN)
    if first[0] != "ok":
        return _refuse(ctx, NOT_ANSWERING, REFUSED)
    code = _version(ctx, first)
    if code is not None:
        return code
    slug = settings_table.derived_active_playlist(first[1])[0]
    if slug is None:
        return _refuse(ctx, NO_PLAYLIST, REFUSED)
    with push.engine_only():
        entries, interval, order, enabled, label = push._playlist_payload(slug)
        parts = split_playlist_parts(entries)
        for number, part in enumerate(parts, start=1):
            reply = api_client.playlist_set(slug, part, order, interval, part=number, of=len(parts), label=label)
            if api_client.last_class() != "ok":
                break
        else:
            reply = api_client.lanes_set([{"id": "all", "enabled": enabled}])
    cls = api_client.last_class()
    if cls == "away":
        return _refuse(ctx, NOT_RUNNING, ENGINE_DOWN)
    if cls == "refused":
        return _refuse(ctx, f"the engine refused it: {reply.get('error') or ''}", REFUSED)
    if cls != "ok":
        return _refuse(ctx, NO_ANSWER, REFUSED)
    return _receipt(ctx, name, slug, False, report.APPLIED, sent=True)


def _set(ctx: Context, name: str, args: list[str]) -> int:
    if not args:
        from .settings import _help
        return _help(ctx, name)
    if len(args) > 1:
        return _refuse(ctx, f"{name} takes one value; got {' '.join(args)}", USAGE)
    if args[0] == "load":
        return _load(ctx, name)
    try:
        value = settings_table.BY_NAME[name].parse(args[0])
    except UsageError as exc:
        return _refuse(ctx, f"{name} {exc}", USAGE)
    return _change(ctx, name, value)


def _verb(name: str) -> Callable[[Context, list[str]], int]:
    return lambda ctx, args: _set(ctx, name, args)


UNSET: dict[str, Callable[[Context], int]] = {
    "order": lambda ctx: _change(ctx, "order", None),
    "interval": lambda ctx: _change(ctx, "interval", None),
}

VERBS = tuple(Verb(row["name"], _verb(row["name"]), row["what"], "Settings") for row in vocabulary.SETTINGS
              if row["name"] in _NAMES)
