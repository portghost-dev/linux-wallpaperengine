"""playlist, order and interval: which playlist plays, how it moves on, and how long each wallpaper stays up.

`playlist` and `playlist list` number the playlists by name and mark the playing one. `playlist <p>`
switches: with the schedule on it needs the running service, since the engine takes a hand switch only
as a manual bind, and it is refused before any write while the service is away; otherwise
ACTIVE_PLAYLIST is saved through the change runner, which binds the playlist, manual while the schedule
is on. `playlist load` binds the saved playlist again without manual. For reload, check_files checks every
playlist file from its raw text and writes nothing, and apply_cleanups makes its one cleanup.

order and interval act on the derived active playlist, the playlist the engine plays for the panel:
the engine's binding while its schedule is on and that playlist's file exists, else the saved active
playlist (cli/settings_table.py::derived_active_playlist). They edit that playlist's file, name it in
the receipt and never write ACTIVE_PLAYLIST. A change saves one line through the change runner, which
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
SWITCH_AWAY = "The schedule is on; a hand switch needs the running service (it holds until the next start time)."
_NAMES = ("order", "interval")
_DID_NOT_HAPPEN = {report.PENDING: "the service is not running or is busy",
                   report.UNCERTAIN: "the engine did not answer in time"}


class _Gone(Exception):
    """The playlist's file was removed before its write."""


def _refuse(ctx: Context, message: str, code: int) -> int:
    ctx.error(message)
    return code


def _gone(path: Any) -> str:
    return f"{path} is gone, so nothing was saved"


def _print_json(ctx: Context, data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")), file=ctx.out)


def _version(ctx: Context, first: tuple[str, dict | None]) -> int | None:
    """The version check of a status read that answered: REFUSED, named on stderr, for a running
    engine from another build; otherwise None."""
    from ... import version
    if first[0] != "ok":
        return None
    refusal = version.running_refusal(first[1], version.panel_stamp())
    return None if refusal is None else _refuse(ctx, refusal, REFUSED)


def _engine_first(ctx: Context) -> tuple[tuple[str, dict | None], int | None]:
    """An engine-only form's status read: away exits 2, an engine that does not answer exits 1, and so
    does one from another build."""
    from ...engine import push
    first = push.read_status()
    if first[0] == "away":
        return first, _refuse(ctx, NOT_RUNNING, ENGINE_DOWN)
    if first[0] != "ok":
        return first, _refuse(ctx, NOT_ANSWERING, REFUSED)
    return first, _version(ctx, first)


def _send(ctx: Context, slug: str, bind: bool, manual: bool = False) -> int | None:
    """The playlist's parts, then lanes-set with its enabled state, with the playlist field when bind
    and manual true when manual, all under the sync lock; the exit code of a request that did not end
    ok, else None."""
    from ... import api_client
    from ...engine import push
    from ...engine.resolve import split_playlist_parts
    with push.engine_only():
        entries, interval, order, enabled, label = push._playlist_payload(slug)
        parts = split_playlist_parts(entries)
        for number, part in enumerate(parts, start=1):
            reply = api_client.playlist_set(slug, part, order, interval, part=number, of=len(parts), label=label)
            if api_client.last_class() != "ok":
                break
        else:
            lane = {"id": "all", "playlist": slug, "enabled": enabled} if bind else {"id": "all", "enabled": enabled}
            reply = api_client.lanes_set([{**lane, "manual": True} if manual else lane])
    cls = api_client.last_class()
    if cls == "away":
        return _refuse(ctx, NOT_RUNNING, ENGINE_DOWN)
    if cls == "refused":
        return _refuse(ctx, f"the engine refused it: {reply.get('error') or ''}", REFUSED)
    if cls != "ok":
        return _refuse(ctx, NO_ANSWER, REFUSED)
    return None


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
    first, code = _engine_first(ctx)
    if code is not None:
        return code
    slug = settings_table.derived_active_playlist(first[1])[0]
    if slug is None:
        return _refuse(ctx, NO_PLAYLIST, REFUSED)
    code = _send(ctx, slug, bind=False)
    return code if code is not None else _receipt(ctx, name, slug, False, report.APPLIED, sent=True)


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


def _bound(status: dict | None) -> str:
    """The playlist the engine's lane is bound to, from a status that answered; "" otherwise."""
    lanes = status.get("lanes") if isinstance(status, dict) else None
    return str(lanes[0].get("playlist") or "") if isinstance(lanes, list) and lanes and isinstance(lanes[0], dict) \
        else ""


def _schedule_on(status: dict | None) -> bool:
    schedule = status.get("schedule") if isinstance(status, dict) else None
    return isinstance(schedule, dict) and bool(schedule.get("enabled"))


def _takes_over(status: dict | None) -> str:
    """" The schedule takes over again when night starts at 20:00." from the schedule block of a
    status that answered with the schedule on; "" when it does not say."""
    schedule = status.get("schedule") if isinstance(status, dict) else None
    entries = schedule.get("entries") if isinstance(schedule, dict) else None
    if not _schedule_on(status) or not isinstance(entries, list) or len(entries) < 2 \
            or not isinstance(schedule.get("is_day"), bool):
        return ""
    word, entry = ("night", entries[1]) if schedule["is_day"] else ("day", entries[0])
    at = entry.get("at") if isinstance(entry, dict) else None
    return f" The schedule takes over again when {word} starts at {at}." if at else ""


def _list(ctx: Context) -> int:
    """The playlists numbered by name: number, name, file, wallpaper count, order and interval, the
    playing one marked (the engine's binding when status answers, else the saved active playlist)."""
    from ... import api_client, version
    from ...storage import playlists
    from .. import select
    status = api_client.status()
    if status is not None:
        refusal = version.running_refusal(status, version.panel_stamp())
        if refusal is not None:
            ctx.error(refusal)
    playing = _bound(status) or playlists.active_slug(validate=True)
    rows = []
    for pick in select.playlist_rows():
        loaded = playlists.load(pick.slug)
        rows.append({"number": pick.number, "name": pick.name, "file": f"{pick.slug}.conf",
                     "count": len(str(loaded["MEMBERS"]).split()),
                     "order": settings_table.BY_NAME["order"].format(loaded["MODE"]),
                     "interval": settings_table.BY_NAME["interval"].format(loaded["INTERVAL"]),
                     "playing": pick.slug == playing})
    if ctx.json:
        _print_json(ctx, {"playlists": rows})
        return DONE
    if not rows:
        print("No playlists.", file=ctx.out)
    for row in rows:
        line = f"{row['number']}  {row['name']}  {row['file']}  {row['count']}  {row['order']}  {row['interval']}"
        print(line + ("  playing" if row["playing"] else ""), file=ctx.out)
    return DONE


def _switch(ctx: Context, word: str) -> int:
    """playlist <p>: steps 1 and 2 here (the pick, one status read, the version check and the
    schedule's state), then ACTIVE_PLAYLIST through the change runner, which binds p, manual while
    the schedule is on. The playlist already playing sends nothing; when only the store names p, the
    manual bind is sent under the sync lock with no store change."""
    from ...engine import push
    from ...storage import paths, playlists, settings
    from .. import select
    try:
        pick = select.playlist(word)
    except select.PickError as exc:
        return select.report(ctx, exc)
    first = push.read_status()
    code = _version(ctx, first)
    if code is not None:
        return code
    status = first[1]
    schedule_on = _schedule_on(status) if first[0] == "ok" else bool(settings.load()["SCHEDULE_ENABLED"])
    if schedule_on and first[0] != "ok":
        return _refuse(ctx, SWITCH_AWAY, ENGINE_DOWN)
    title = f"{pick.name or pick.slug} ({pick.number})"
    if settings_table.derived_active_playlist(status)[0] == pick.slug:
        if ctx.json:
            _print_json(ctx, {"playlist": pick.name, "setting": "playlist", "value": pick.name, "saved": False,
                              "outcome": None, "reason": ""})
        else:
            print(f"{title} is already playing.", file=ctx.out)
        return DONE
    if playlists.active_slug(validate=False) == pick.slug:
        code = _send(ctx, pick.slug, bind=True, manual=True)
        if code is not None:
            return code
        if ctx.json:
            _print_json(ctx, {"playlist": pick.name, "setting": "playlist", "value": pick.name, "saved": False,
                              "outcome": report.APPLIED, "reason": ""})
        else:
            print(f"Switched to {title}; the next wallpaper comes from it.{_takes_over(status)}", file=ctx.out)
        return DONE
    path = paths.settings_file()
    saved: list[bool] = []

    def write() -> None:
        before = path.read_bytes() if path.exists() else None
        playlists.set_active(pick.slug)
        saved.append((path.read_bytes() if path.exists() else None) != before)

    try:
        outcome = push.run_change(("settings",), write, [("active", "ACTIVE_PLAYLIST")], slug=pick.slug,
                                  manual=True, status=first, run="command")
    except push.SwitchRefused:
        return _refuse(ctx, SWITCH_AWAY, ENGINE_DOWN)
    kind, reason = outcome.kind, outcome.message or outcome.reason or ""
    if ctx.json:
        _print_json(ctx, {"playlist": pick.name, "setting": "playlist", "value": pick.name,
                          "saved": bool(saved and saved[0]), "outcome": kind, "reason": reason})
    elif kind == report.APPLIED:
        print(f"Switched to {title}; the next wallpaper comes from it.{_takes_over(status)}", file=ctx.out)
    elif kind == report.REFUSED:
        print(f"{title} is saved as your playlist, but the engine refused the switch: {reason}.", file=ctx.out)
    else:
        print(f"{title} is saved as your playlist, but the switch did not happen: {_DID_NOT_HAPPEN[kind]}.",
              file=ctx.out)
    return REFUSED if kind == report.REFUSED else DONE


def _bind(ctx: Context) -> int:
    """playlist load: the saved active playlist bound again without manual, under the sync lock; under
    an enabled schedule the engine keeps its own choice, and the receipt says so."""
    from ...storage import playlists
    from .. import select
    first, code = _engine_first(ctx)
    if code is not None:
        return code
    slug = playlists.active_slug(validate=True)
    if not slug:
        return _refuse(ctx, NO_PLAYLIST, REFUSED)
    code = _send(ctx, slug, bind=True)
    if code is not None:
        return code
    pick = next(p for p in select.playlist_rows() if p.slug == slug)
    title = f"{pick.name or pick.slug} ({pick.number})"
    keeps = _schedule_on(first[1]) and _bound(first[1]) not in ("", slug)
    if ctx.json:
        _print_json(ctx, {"playlist": pick.name, "setting": "playlist", "value": pick.name, "saved": False,
                          "outcome": report.APPLIED, "reason": "the schedule keeps its choice" if keeps else ""})
    elif keeps:
        print(f"Sent {title} to the engine; the schedule is on, so the engine keeps playing its own choice.",
              file=ctx.out)
    else:
        print(f"Sent {title} to the engine.", file=ctx.out)
    return DONE


def _playlist(ctx: Context, args: list[str]) -> int:
    if not args or args == ["list"]:
        return _list(ctx)
    if len(args) > 1:
        return _refuse(ctx, f"playlist takes one playlist, a number or a name (quote a name with spaces); "
                            f"got {' '.join(args)}", USAGE)
    if args[0] == "load":
        return _bind(ctx)
    return _switch(ctx, args[0])


def _raw(text: str) -> dict[str, tuple[int, str, str | None]]:
    """{key: (line number, value, the old spelling the value came from or None)} for each key the text
    assigns, under this build's names, the last assignment of a key winning; values are read raw,
    never default-filled."""
    from ...storage import migrate, tier_a
    out: dict[str, tuple[int, str, str | None]] = {}
    for number, line in enumerate(text.split("\n"), 1):
        raw, actions = migrate.apply_tables("playlists", tier_a.parse(line))
        for key, value in raw.items():
            old = next((a["from"] for a in actions if a.get("kind") == "alias" and a.get("key") == key), None)
            out[key] = (number, str(value), old)
    return out


def check_files(status: dict | None = None) -> tuple[list[str], list[str], list[dict], list[str]]:
    """Every playlists/*.conf checked from its raw text, never through the default-filling loader,
    and nothing written. Returns (errors, warnings, cleanups, changes): each problem names the file,
    the line and the key; the one cleanup, ACTIVE_PLAYLIST naming a missing file, is an edit for
    apply_cleanups; changes compare the playing playlist's file with what `status` reports."""
    import re
    from ... import constants as C
    from ...engine import push
    from ...engine.resolve import split_playlist_parts
    from ...storage import paths, playlists, settings
    errors: list[str] = []
    warnings: list[str] = []
    cfg = settings.load()
    held = {slug for slug in [settings_table.derived_active_playlist(status)[0], *push._scheduled()] if slug}
    names: dict[str, list[str]] = {}
    folder = paths.playlists_dir()
    for path in sorted(folder.glob("*.conf")) if folder.is_dir() else []:
        slug, where = path.stem, f"playlists/{path.name}"
        text = path.read_bytes().decode("utf-8", "replace")
        raw = _raw(text)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", slug):
            line = f"{where}: the engine does not take this file name (letters, digits, - and _, 64 at most)"
            (errors if slug in held else warnings).append(line)
        if not raw.get("NAME", (0, "", None))[1].strip():
            errors.append(f"{where}: NAME is missing")
        else:
            names.setdefault(" ".join(raw["NAME"][1].split()).casefold(), []).append(where)
        if "MODE" in raw:
            number, value, old = raw["MODE"]
            if old is not None:
                warnings.append(f"{where}:{number}: MODE {old} is an old spelling of {value}")
            elif value not in C.PLAYLIST_MODES:
                errors.append(f"{where}:{number}: MODE must be shuffle, sequential or static; got {value}")
        if "INTERVAL" in raw:
            number, value, _old = raw["INTERVAL"]
            if not re.fullmatch(r"[0-9]+", value) or not 15 <= int(value) <= 599940:
                errors.append(f"{where}:{number}: INTERVAL must be a whole number of seconds from 15 to 599940; "
                              f"got {value}")
        if "UNIT" in raw and raw["UNIT"][1] not in C.PLAYLIST_UNITS:
            errors.append(f"{where}:{raw['UNIT'][0]}: UNIT must be min or s; got {raw['UNIT'][1]}")
        if "MEMBERS" in raw:
            number, value, _old = raw["MEMBERS"]
            ids = value.split()
            for wid in [w for w in ids if not paths.is_safe_wid(w)]:
                errors.append(f"{where}:{number}: MEMBERS holds {wid}, which is not a wallpaper id")
            for wid in sorted({w for w in ids if ids.count(w) > 1}):
                errors.append(f"{where}:{number}: MEMBERS lists {wid} more than once")
            for wid in [w for w in ids if paths.is_safe_wid(w) and not paths.wallpaper_present(w, cfg)]:
                warnings.append(f"{where}:{number}: MEMBERS holds {wid}, which is not in the library")
            entries = push._playlist_payload(slug)[0]
            sent = sum(len(part) for part in split_playlist_parts(entries))
            if sent < len(entries):
                errors.append(f"{where}:{number}: MEMBERS is too large for one transfer; {len(entries) - sent} "
                              "entries would be left out")
    for files in names.values():
        if len(files) > 1:
            warnings.append(f"{', '.join(files)}: NAME is the same in {len(files)} files")
    active = str(cfg.get("ACTIVE_PLAYLIST") or "")
    cleanups: list[dict] = []
    if active and not paths.playlist_file(active).exists():
        remaining = playlists.list_playlists()
        cleanups.append({"file": "settings.conf", "key": "ACTIVE_PLAYLIST", "from": active,
                         "to": remaining[0]["slug"] if remaining else ""})
    return errors, warnings, cleanups, _changes(status)


def _changes(status: dict | None) -> list[str]:
    """The playing playlist's file against status's rotation: label, order, interval_s and count."""
    from ...engine import push
    rotation = status.get("rotation") if isinstance(status, dict) else None
    slug = settings_table.derived_active_playlist(status)[0]
    if not isinstance(rotation, dict) or not slug:
        return []
    entries, interval, order, _enabled, label = push._playlist_payload(slug)
    out = []
    for field, stored in (("label", label), ("order", order), ("interval_s", interval), ("count", len(entries))):
        if field in rotation and rotation[field] != stored:
            out.append(f"playlists/{slug}.conf: {field} is {stored} in the file and {rotation[field]} in the engine")
    return out


def apply_cleanups(cleanups: list[dict]) -> str:
    """Make check_files' cleanup under the settings lock, one settings.conf line; returns the line to
    print, "" when there was none."""
    from ...storage import lock, settings
    lines = []
    with lock.held("settings"):
        for edit in cleanups:
            settings.modify(lambda _current, edit=edit: {edit["key"]: edit["to"]})
            lines.append(f"{edit['file']}: {edit['key']} {edit['from']} -> {edit['to'] or '(none)'}, since "
                         f"playlists/{edit['from']}.conf is gone")
    return "\n".join(lines)


UNSET: dict[str, Callable[[Context], int]] = {
    "order": lambda ctx: _change(ctx, "order", None),
    "interval": lambda ctx: _change(ctx, "interval", None),
}

VERBS = (
    Verb("playlist", _playlist, next(row["what"] for row in vocabulary.COMMANDS if row["name"] == "playlist"),
         "Playlists"),
    *(Verb(row["name"], _verb(row["name"]), row["what"], "Settings") for row in vocabulary.SETTINGS
      if row["name"] in _NAMES),
)
