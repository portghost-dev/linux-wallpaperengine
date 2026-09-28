"""wallpaper: one wallpaper's details, its own settings and its alias.

`wallpaper <w>` prints the details; `wallpaper <w> <word>` reads the value in force: the wallpaper's
own key, else the global setting when settings.conf carries it, else the default. `wallpaper <w>
<word> <value>` saves one line of wp/<id>.conf through engine/push.py's run_change, which sends the
live verb or the re-show of the wallpaper on screen and, but for the three dials, the entry refresh
of the engine-held playlists that hold it, and prints the receipt; `wallpaper <w> unset <word>`
removes the wallpaper's own value. alias saves with no engine side. properties, objects, blink, hide
and unhide run objects.py's WALLPAPER_WORDS. Reads take no lock and write nothing.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .. import DONE, REFUSED, USAGE, Context, report, settings_table, values
from ..registry import Verb

USAGE_TEXT = ("usage: lwe wallpaper <wallpaper> [<setting> [<value>]], lwe wallpaper <wallpaper> unset <setting>, "
              "alias [<name>] or property <name> [<value>]")
WORKSHOP_LINK = "https://steamcommunity.com/sharedfiles/filedetails/?id="
B_WORDS = ("properties", "objects", "blink", "hide", "unhide")
NO_ROW = ("fps", "mute", "parallax", "particles", "mouse", "audioreactive")
COLOR_WORDS = ("brightness", "contrast", "saturation", "hue")
CLAMP_WORDS = ("resclamp", "effectclamp")
DIALS = ("lightdimming", "lightfalloff", "audiogain")
DIAL_LATER = "applies when lwe show or the panel next shows it"
COLOR_REMOVED = "CC and CC_MODE were removed together, so the wallpaper shows its own colors again"
_NEUTRAL = (1.0, 1.0, 1.0, 0.0)
_BOOL_WORDS = {"true": "true", "on": "true", "false": "false", "off": "false"}


class _Stop(Exception):
    """A refusal found inside the write: code and message."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _print_json(ctx: Context, data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")), file=ctx.out)


def _refuse(ctx: Context, message: str, code: int) -> int:
    ctx.error(message, "lwe: " + message)
    return code


def _row(key: str) -> str:
    """The change runner's row for a wp/<id>.conf key."""
    from ...engine import push
    return push.WP_ROWS.get(key) or ("wp_build" if key.startswith("PROP_") else "none")


def _authored_cc(wid: str) -> str:
    """The authored look: derive_cc over the project's preset block, else its raw keys."""
    from ...discovery import project
    from ...library import catalog
    folder = catalog.render_dir(wid)
    raw = project.read(folder).get("raw") if folder else {}
    raw = raw if isinstance(raw, dict) else {}
    preset = raw.get("preset")
    return project.derive_cc(preset if isinstance(preset, dict) else raw)


def _channels(cc: str) -> list[float]:
    parts = str(cc or "").split()
    out = []
    for i, neutral in enumerate(_NEUTRAL):
        try:
            out.append(float(parts[i]))
        except (IndexError, ValueError):
            out.append(neutral)
    return out


def _in_force(wid: str, name: str) -> tuple[Any, str, settings_table.Row]:
    """(the value in force, its source, the row that formats it) for one word of wallpaper `wid`."""
    from ...storage import settings, wp
    row = settings_table.WALLPAPER_BY_NAME[name]
    present = wp.load_set(wid)
    if name in COLOR_WORDS:
        if str(present.get("CC") or ""):
            return _channels(present["CC"])[row.field], "own", row
        return _channels(_authored_cc(wid))[row.field], "default", row
    if name in CLAMP_WORDS:
        value, source = wp.clamp_values(present, wid)[row.key]
        if source == "inherit":
            return settings.load()[row.key], source, settings_table.BY_NAME[name]
        return value, source, row
    if row.key in present and present[row.key] != "":
        return present[row.key], "own", row
    glob = settings_table.BY_NAME.get(name)
    if glob is not None and glob.wp_key == row.key:
        source = "global" if glob.key in settings.load_set() else "default"
        return settings.load()[glob.key], source, glob
    if name == "fullscreen":
        return "", "default", row
    from ... import constants as C
    return C.WP_SCHEMA[row.key]["default"], "default", row


def _read(ctx: Context, pick, name: str) -> int:
    value, source, row = _in_force(pick.ui_id, name)
    text = row.format(value)
    if ctx.json:
        _print_json(ctx, {"name": name, "value": text, "source": source})
    else:
        print(text, file=ctx.out)
    return DONE


def _screen() -> tuple[tuple[str, dict | None], str | None]:
    """One status read before any lock, (the read, the id on screen), or raises _Stop for a
    running engine from another build."""
    from ... import version
    from ...engine import push
    first = push.read_status()
    if first[0] == "ok":
        try:
            refusal = version.running_refusal(first[1], version.panel_stamp())
        except version.StampError as exc:
            refusal = str(exc)
        if refusal is not None:
            raise _Stop(REFUSED, refusal)
        current = first[1].get("current")
        return first, str(current.get("ui_id") or "") if isinstance(current, dict) else ""
    return first, None


def _change(ctx: Context, pick, name: str, key: str, write_fn, shown_fn, *, unset: bool = False) -> int:
    """Save through run_change with the overrides lock and the key's row, then print the receipt."""
    from ...engine import push
    try:
        first, screen = _screen()
    except _Stop as exc:
        return _refuse(ctx, exc.message, exc.code)
    written: dict[str, Any] = {}

    def write() -> None:
        written.update(write_fn())

    try:
        outcome = push.run_change(("overrides",), write, [(_row(key), key)], wid=pick.ui_id, status=first,
                                  run="command")
    except _Stop as exc:
        return _refuse(ctx, exc.message, exc.code)
    except ValueError as exc:
        return _refuse(ctx, str(exc), REFUSED)
    shown = shown_fn()
    applies = settings_table.NOW
    later = name in DIALS and screen != pick.ui_id
    r = report.receipt(name, shown, bool(written), "next show" if later else applies, outcome.kind,
                       outcome.message or "")
    if ctx.json:
        report.emit(ctx, r)
    elif later and outcome.kind == report.APPLIED:
        print(f"{name} {shown}: {'saved' if written else 'unchanged'}; {DIAL_LATER}.", file=ctx.out)
    else:
        report.emit(ctx, dict(r, applies=applies))
    if unset and name in COLOR_WORDS and written and not ctx.json:
        print(COLOR_REMOVED + ".", file=ctx.out)
    if outcome.reason == "version" and outcome.message:
        ctx.error(outcome.message)
    return REFUSED if outcome.kind == report.REFUSED or outcome.reason == "version" else DONE


def _shown(pick, name: str):
    def shown() -> str:
        value, _source, row = _in_force(pick.ui_id, name)
        return row.format(value)
    return shown


def _value_now(wid: str, name: str) -> Any:
    """The value in force read inside the write, for a toggle or a step: the wallpaper's own key,
    else the global setting."""
    from ...storage import settings, wp
    key = settings_table.WALLPAPER_BY_NAME[name].key
    present = wp.load_set(wid)
    if key in present and present[key] != "":
        return present[key]
    return settings.load()[settings_table.BY_NAME[name].key]


def _value(name: str, word: str) -> Callable[[str], Any]:
    """Parse `word` for `name` before any lock or request: a function of the wallpaper id giving the
    value to save, read inside the write for a toggle or a volume step. Raises values.UsageError."""
    if name == "volume" and word[:1] in ("+", "-"):
        step = values.parse_step(word)
        return lambda wid: max(0, min(128, int(_value_now(wid, name)) + step))
    parsed = settings_table.WALLPAPER_BY_NAME[name].parse(word, False)
    if parsed == settings_table.TOGGLE:
        return lambda wid: not bool(_value_now(wid, name))
    return lambda wid: parsed


def _set(ctx: Context, pick, name: str, value_of: Callable[[str], Any]) -> int:
    row = settings_table.WALLPAPER_BY_NAME[name]
    wid = pick.ui_id

    def write() -> dict[str, Any]:
        from ...storage import wp

        def fn(raw: dict[str, str]) -> dict[str, Any]:
            value = value_of(wid)
            if name in COLOR_WORDS:
                cc = str(raw.get("CC") or "") or _authored_cc(wid)
                chans = _channels(cc)
                chans[row.field] = float(value)
                new = " ".join(values.format_number(c) for c in chans)
                changes: dict[str, Any] = {}
                if raw.get("CC") != new:
                    changes["CC"] = new
                if raw.get("CC_MODE") != "custom":
                    changes["CC_MODE"] = "custom"
                return changes
            if name == "fullscreen" and value == "":
                return {row.key: None} if row.key in raw else {}
            if isinstance(value, bool):
                text = "true" if value else "false"
            elif isinstance(value, float):
                text = values.format_number(value)
            else:
                text = str(value)
            return {} if raw.get(row.key) == text else {row.key: text}

        return wp.modify_set(wid, fn)

    return _change(ctx, pick, name, row.key, write, _shown(pick, name))


def _unset(ctx: Context, pick, name: str) -> int:
    row = settings_table.WALLPAPER_BY_NAME[name]
    wid = pick.ui_id

    def write() -> dict[str, Any]:
        from ...storage import wp

        def fn(raw: dict[str, str]) -> dict[str, Any]:
            if name in COLOR_WORDS:
                return {k: None for k in ("CC", "CC_MODE") if k in raw}
            if name in CLAMP_WORDS:
                changes = wp.clamp_unset_changes(wp.load_set(wid), row.key)
                if changes[row.key] is None:
                    return changes if row.key in raw else {}
                return {} if raw.get(row.key) == "" else changes
            return {row.key: None} if row.key in raw else {}

        return wp.modify_set(wid, fn)

    key = "CC" if name in COLOR_WORDS else row.key
    return _change(ctx, pick, name, key, write, _shown(pick, name), unset=True)


def _alias(ctx: Context, pick, args: list[str], unset: bool = False) -> int:
    from ...engine import push
    from ...storage import alias, wp
    wid = pick.ui_id
    if not args and not unset:
        name = str(wp.load_set(wid).get("ALIAS") or "")
        if ctx.json:
            _print_json(ctx, {"name": "alias", "value": name, "source": "own" if name else "default"})
        else:
            print(name, file=ctx.out)
        return DONE
    if unset:
        name = ""

        def fn(raw: dict[str, str]) -> dict[str, Any]:
            return {"ALIAS": None} if "ALIAS" in raw else {}
    else:
        name = args[0]

        def fn(raw: dict[str, str]) -> dict[str, Any]:
            return {} if raw.get("ALIAS") == name else {"ALIAS": name}
    written: dict[str, Any] = {}

    def write() -> None:
        if name:
            taken = alias.check(name, wid)
            if taken is not None:
                raise _Stop(REFUSED, taken)
        written.update(wp.modify_set(wid, fn))

    try:
        push.run_change(("overrides",), write, [("none", "ALIAS")], wid=wid, run="command")
    except _Stop as exc:
        return _refuse(ctx, exc.message, exc.code)
    except ValueError as exc:
        return _refuse(ctx, str(exc), REFUSED)
    state = "saved" if written else "unchanged"
    if ctx.json:
        _print_json(ctx, report.receipt("alias", name, bool(written), settings_table.NO_ENGINE))
    elif unset:
        print(f"alias: {'removed' if written else 'unchanged'}.", file=ctx.out)
    else:
        print(f"alias {name}: {state}.", file=ctx.out)
    return DONE


def _project_property(pick, name: str) -> dict | None:
    """The project's property `name`, normalized, or None."""
    from ...discovery import project, properties
    from ...library import catalog
    folder = catalog.render_dir(pick.ui_id)
    entries = properties.normalize_all(project.read(folder)["properties"]) if folder else []
    return next((entry for entry in entries if entry["name"] == name), None)


def _hex_rgb(text: str) -> str | None:
    s = text.strip()
    s = s[1:] if s.startswith("#") else s
    if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
        return None
    return " ".join(f"{int(s[i:i + 2], 16) / 255.0:.5f}".rstrip("0").rstrip(".") or "0" for i in (0, 2, 4))


def _property_value(entry: dict, word: str) -> str:
    """The value to store for property `entry`, checked by its type; raises values.UsageError."""
    kind = entry["kind"]
    if kind == "bool":
        if word not in _BOOL_WORDS:
            raise values.UsageError(f"takes on or off; got {word}")
        return _BOOL_WORDS[word]
    if kind == "combo":
        options = [str(o["value"]) if not isinstance(o["value"], str) else o["value"]
                   for o in entry.get("options") or []]
        if word not in options:
            raise values.UsageError(f"takes {', '.join(options) or 'no value'}; got {word}")
        return word
    if kind == "slider":
        try:
            lo, hi = float(entry.get("min", 0.0)), float(entry.get("max", 1.0))
        except (TypeError, ValueError):
            lo, hi = 0.0, 1.0
        return values.format_number(values.parse_number(word, lo, hi))
    if kind == "color":
        rgb = _hex_rgb(word)
        if rgb is not None:
            return rgb
        parts = word.split()
        if len(parts) == 3:
            try:
                return " ".join(values.format_number(values.parse_number(p, 0.0, 1.0)) for p in parts)
            except values.UsageError:
                pass
        raise values.UsageError(f'takes "r g b" with each 0 to 1, or #rrggbb; got {word}')
    if "\n" in word or "\r" in word:
        raise values.UsageError("takes one line of text")
    return word


def _property(ctx: Context, pick, args: list[str], unset: bool = False) -> int:
    from ...storage import tier_a, wp
    if not args or len(args) > (1 if unset else 2):
        return _refuse(ctx, "usage: lwe wallpaper <wallpaper> property <name> [<value>]", USAGE)
    name = args[0]
    key = "PROP_" + name
    entry = _project_property(pick, name)
    if entry is None or not tier_a.is_valid_key(key):
        return _refuse(ctx, f"{name} is not a property of {pick.title} ({pick.ui_id}); "
                            "lwe wallpaper <wallpaper> properties lists them", USAGE)

    def shown() -> str:
        props = wp.load_set(pick.ui_id)["props"]
        value = props[name] if name in props else entry["value"]
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    if len(args) == 1 and not unset:
        props = wp.load_set(pick.ui_id)["props"]
        if ctx.json:
            _print_json(ctx, {"name": name, "value": shown(), "source": "own" if name in props else "default"})
        else:
            print(shown(), file=ctx.out)
        return DONE
    if unset:
        def fn(raw: dict[str, str]) -> dict[str, Any]:
            return {key: None} if key in raw else {}
    else:
        try:
            text = _property_value(entry, args[1])
        except values.UsageError as exc:
            return _refuse(ctx, f"property {name} {exc}", USAGE)

        def fn(raw: dict[str, str]) -> dict[str, Any]:
            return {} if raw.get(key) == text else {key: text}

    return _change(ctx, pick, f"property {name}", key, lambda: wp.modify_set(pick.ui_id, fn), shown)


def _details(ctx: Context, pick) -> int:
    from ... import api_client, version
    from ...discovery import project
    from ...library import catalog
    from ...storage import paths, tier_a, wp
    folder = catalog.render_dir(pick.ui_id)
    facts = project.read(folder) if folder else {}
    present = wp.load_set(pick.ui_id)
    path = paths.wp_file(pick.ui_id)
    raw = tier_a.parse(path.read_bytes().decode("utf-8")) if path.exists() else {}
    status = api_client.status()
    on_screen: bool | None = None
    if isinstance(status, dict):
        refusal = version.running_refusal(status, version.panel_stamp())
        if refusal is not None:
            ctx.error(refusal)
        current = status.get("current")
        on_screen = isinstance(current, dict) and str(current.get("ui_id") or "") == pick.ui_id
    names = {row.key: row for row in settings_table.WALLPAPER_BY_NAME.values()
             if row.field is None and row.name not in ("hide", "unhide", "property")}
    own: dict[str, str] = {}
    for key, text in raw.items():
        if key == "CC":
            chans = _channels(text)
            for word in COLOR_WORDS:
                row = settings_table.WALLPAPER_BY_NAME[word]
                own[word] = row.format(chans[row.field])
        elif key.startswith("PROP_"):
            own[f"property {key[5:]}"] = text
        elif key in names and key in present:
            own[names[key].name] = names[key].format(present[key]) if present[key] != "" else ""
        else:
            own[key] = text
    title = str(facts.get("title") or "") or pick.title
    kind = str(facts.get("type") or "") or str(present.get("TYPE") or "")
    link = WORKSHOP_LINK + pick.ui_id if pick.ui_id.isascii() and pick.ui_id.isdigit() else None
    alias_name = str(present.get("ALIAS") or "")
    if ctx.json:
        _print_json(ctx, {"id": pick.ui_id, "title": title, "alias": alias_name, "type": kind,
                          "on_screen": on_screen, "settings": own, "workshop": link})
        return DONE
    lines = [f"title: {title}", f"id: {pick.ui_id}", f"alias: {alias_name or 'none'}", f"type: {kind}",
             "on screen: " + ("unknown, the service is not answering" if on_screen is None
                              else "yes" if on_screen else "no")]
    lines.append("own settings:" if own else "own settings: none")
    lines += [f"  {word} {text}".rstrip() for word, text in own.items()]
    if link:
        lines.append(f"workshop: {link}")
    for line in lines:
        print(line, file=ctx.out)
    return DONE


def _words(ctx: Context, name: str, rest: list[str], unset: bool) -> int | None:
    """Exit 3 for a word or a form the wallpaper group does not take, before any lock or request."""
    if name in NO_ROW:
        return _refuse(ctx, f"{name} has no per-wallpaper value; lwe {name} sets it for every wallpaper", USAGE)
    if name in B_WORDS:
        if unset:
            return _refuse(ctx, f"{name} cannot be unset; {USAGE_TEXT}", USAGE)
        return None
    if name in ("alias", "property"):
        return None
    if name not in settings_table.WALLPAPER_BY_NAME:
        return _refuse(ctx, f"{name} is not a setting of one wallpaper; {USAGE_TEXT}", USAGE)
    if len(rest) > (0 if unset else 1):
        return _refuse(ctx, f"{name} takes one value; got {' '.join(rest)}", USAGE)
    return None


def _run(ctx: Context, args: list[str]) -> int:
    if not args:
        return _refuse(ctx, USAGE_TEXT, USAGE)
    unset = args[1:2] == ["unset"]
    tail = args[2:] if unset else args[1:]
    if unset and not tail:
        return _refuse(ctx, USAGE_TEXT, USAGE)
    name, rest = (tail[0], tail[1:]) if tail else (None, [])
    run = value_of = None
    if name is not None:
        refused = _words(ctx, name, rest, unset)
        if refused is not None:
            return refused
        if name in B_WORDS:
            try:
                from .objects import WALLPAPER_WORDS
            except ImportError:
                WALLPAPER_WORDS = {}
            run = WALLPAPER_WORDS.get(name)
            if run is None:
                return _refuse(ctx, f"wallpaper {name} is not available in this build", USAGE)
        elif name == "alias":
            if len(rest) > (0 if unset else 1):
                return _refuse(ctx, "usage: lwe wallpaper <wallpaper> alias [<name>]", USAGE)
            if rest:
                from ...storage import alias
                reason = alias.check(rest[0], "", taken={})
                if reason is not None:
                    return _refuse(ctx, reason, USAGE)
        elif name != "property" and rest:
            try:
                value_of = _value(name, rest[0])
            except values.UsageError as exc:
                return _refuse(ctx, f"{name} {exc}", USAGE)
    from .. import select
    try:
        pick = select.wallpaper(args[0])
    except select.PickError as exc:
        return select.report(ctx, exc)
    if not ctx.json:
        print(select.pick_line(pick), file=ctx.out)
    if name is None:
        return _details(ctx, pick)
    if run is not None:
        return run(ctx, pick, rest)
    if name == "alias":
        return _alias(ctx, pick, rest, unset)
    if name == "property":
        return _property(ctx, pick, rest, unset)
    if unset:
        return _unset(ctx, pick, name)
    if value_of is None:
        return _read(ctx, pick, name)
    return _set(ctx, pick, name, value_of)


VERBS = (
    Verb("wallpaper", _run, "One wallpaper's details: title, id, alias, its own settings and its Workshop link.",
         "One wallpaper"),
)
