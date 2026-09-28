"""get, config and the setting commands.

get and config read the saved settings, each through cli/settings_table.py::read with where its value
comes from; reads take no lock and write nothing, and a running engine from another build is named on
stderr while the read goes on. A panel setting saves one line of settings.conf and sends nothing. A
restart setting saves its line and rebuilds engine-env through daemon_unit.write_env while still holding
the settings lock; watchdog and color are engine-env lines with no settings key. Apart from the status
read of a restart setting or an engine-env line, none of them sends the engine a request.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable

from .. import DONE, ENGINE_DOWN, REFUSED, USAGE, Context, help_text, report, settings_table, vocabulary
from ..registry import Verb
from ..values import Refused, UsageError, format_number

GET_USAGE = "usage: lwe get <setting> [<setting> ...]; lwe config lists the settings"
CONFIG_USAGE = ("usage: lwe config [<setting> [<value>]] or lwe config unset <setting>; lwe config lists the "
                "settings")
_NEEDS_STATUS = ("order", "interval", "playlist", "audiosmoothing")
_PANEL = ("reviewrequired", "storage", "detect", "detectevery", "libraryfolder", "workshopfolder", "steamfolder")
_RESTART = ("layer", "videodecode", "texturecache", "texturedetail", "assetsfolder", "resclamp", "effectclamp")
_LINES = ("watchdog", "color")
_FOLDERS = ("libraryfolder", "workshopfolder", "steamfolder", "assetsfolder")
_LOAD_REFUSED = ("layer", "videodecode", "assetsfolder", "watchdog", "color", *_PANEL)
RESTART_ONLY = "applies only at a restart (lwe service restart)"
PANEL_ONLY = "read by the panel, not the engine"
NO_SCREENS_SAVED = ("engine-env was not rebuilt because no screens were found; it catches up when the panel "
                    "next starts or lwe service start or restart runs from your desktop session")
NO_SCREENS_REFUSED = ("not saved: engine-env is only rewritten where screens are found; run it from your desktop "
                      "session, or open the panel once")


def _print_json(ctx: Context, data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")), file=ctx.out)


def _status(ctx: Context) -> tuple[dict | None, bool]:
    """One status read: (the status or None, whether something listens on the socket)."""
    from ... import api_client, version
    status = api_client.status()
    if status is None:
        return None, api_client.available()
    refusal = version.running_refusal(status, version.panel_stamp())
    if refusal is not None:
        ctx.error(refusal)
    return status, True


def _unknown(ctx: Context, name: str, usage: str) -> int:
    ctx.error(f"{name} is not a setting; {usage}")
    return USAGE


def _get(ctx: Context, args: list[str]) -> int:
    if not args:
        ctx.error(GET_USAGE)
        return USAGE
    for name in args:
        if name not in settings_table.BY_NAME:
            return _unknown(ctx, name, GET_USAGE)
    status, listening = None, False
    if any(name in _NEEDS_STATUS for name in args):
        status, listening = _status(ctx)
    if "audiosmoothing" in args and status is None:
        if not listening:
            ctx.error("the service is not running, so audiosmoothing has no value")
            return ENGINE_DOWN
        ctx.error("the service is not answering")
        return REFUSED
    values: dict[str, str] = {}
    lines: list[str] = []
    for name in args:
        value, _source, problem = settings_table.read(name, status)
        if problem is not None:
            ctx.error(f"invalid: {problem}")
        text = settings_table.BY_NAME[name].format(value)
        values[name] = text
        lines.append(text)
    if ctx.json:
        _print_json(ctx, values)
    else:
        for text in lines:
            print(text, file=ctx.out)
    return DONE


def _config(ctx: Context, args: list[str]) -> int:
    if args[:1] == ["unset"]:
        return _unset(ctx, args[1:])
    if len(args) > 1:
        return _config_set(ctx, args[0], args[1:])
    if args and args[0] not in settings_table.BY_NAME:
        return _unknown(ctx, args[0], CONFIG_USAGE)
    names = args or [row["name"] for row in vocabulary.SETTINGS if row["name"] in settings_table.BY_NAME]
    status = _status(ctx)[0] if any(name in _NEEDS_STATUS for name in names) else None
    rows = []
    for name in names:
        value, source, problem = settings_table.read(name, status)
        rows.append({"name": name, "value": settings_table.BY_NAME[name].format(value), "source": source,
                     "invalid": problem})
    if ctx.json:
        _print_json(ctx, rows[0] if args else {"settings": rows})
        return DONE
    for row in rows:
        line = f"{row['name']}  {row['value']}  {row['source']}"
        if row["invalid"] is not None:
            line += f"  invalid: {row['invalid']}"
        print(line, file=ctx.out)
    return DONE


def _refuse(ctx: Context, message: str, code: int) -> int:
    ctx.error(message)
    return code


def _running_refusal(ctx: Context) -> int | None:
    """One status read before any lock: REFUSED, named on stderr, for a running engine from another
    build; None when the engine answers from this build or does not answer."""
    from ... import api_client, version
    status = api_client.status()
    refusal = version.running_refusal(status, version.panel_stamp()) if status is not None else None
    return None if refusal is None else _refuse(ctx, refusal, REFUSED)


def _help(ctx: Context, name: str) -> int:
    """A setting typed alone: its help page."""
    from . import help as help_verb
    page = getattr(help_verb, "page", None)
    text = page(name) if callable(page) else None
    if text is None:
        values = next(row["values"] for row in vocabulary.SETTINGS if row["name"] == name)
        text = help_text.RESCLAMP if name == "resclamp" else f"{name} {values}"
    ctx.out.write(text if text.endswith("\n") else text + "\n")
    return DONE


def _write(fn: Callable[[dict], dict]) -> bool:
    """settings.modify(fn) under the settings lock; True when settings.conf changed."""
    from ...storage import lock, paths, settings
    path = paths.settings_file()
    with lock.held("settings"):
        before = path.read_bytes() if path.exists() else None
        settings.modify(fn)
        return (path.read_bytes() if path.exists() else None) != before


def _receipt(ctx: Context, name: str, saved: bool, env_state: str | None = None) -> int:
    """The receipt for the value now in force; a rebuild that found no screens says so."""
    row = settings_table.BY_NAME[name]
    shown = row.format(settings_table.read(name, None)[0])
    r = report.receipt(name, shown, saved, row.reach)
    if env_state == "no screens":
        r["reason"] = NO_SCREENS_SAVED
        if not ctx.json:
            print(f"{name} {shown}: {'saved' if saved else 'unchanged'}; {NO_SCREENS_SAVED}.", file=ctx.out)
            return DONE
    report.emit(ctx, r)
    return DONE


def _restart_write(ctx: Context, name: str, fn: Callable[[dict], dict]) -> int:
    """A restart setting: the status read, its line under the settings lock and, still holding it,
    engine-env rebuilt; nothing sent."""
    from ...engine import daemon_unit
    from ...storage import lock
    refused = _running_refusal(ctx)
    if refused is not None:
        return refused
    with lock.held("settings"):
        saved = _write(fn)
        try:
            env_state = daemon_unit.write_env()
        except (OSError, ValueError) as exc:
            state = "saved" if saved else "unchanged"
            return _refuse(ctx, f"{state}, but the engine file could not be written: {exc}", REFUSED)
    return _receipt(ctx, name, saved, env_state)


def _line_write(ctx: Context, name: str, text: str | None) -> int:
    """watchdog or color: the status read, then its engine-env line alone; nothing is saved when no
    screen is found."""
    from ...engine import daemon_unit
    from ...storage.lock import StoreBusy
    refused = _running_refusal(ctx)
    if refused is not None:
        return refused
    try:
        env_state = daemon_unit.write_env({settings_table.BY_NAME[name].key: text})
    except StoreBusy:
        raise
    except (OSError, ValueError) as exc:
        return _refuse(ctx, f"not saved: the engine file could not be written: {exc}", REFUSED)
    if env_state == "no screens":
        return _refuse(ctx, NO_SCREENS_REFUSED, REFUSED)
    return _receipt(ctx, name, env_state == "written")


def _set(ctx: Context, name: str, args: list[str]) -> int:
    row = settings_table.BY_NAME[name]
    if not args:
        return _help(ctx, name)
    if len(args) > 1:
        if name == "color":
            return _refuse(ctx, f'color takes one quoted value, such as color "1 1 1 0"; got {len(args)} words', USAGE)
        return _refuse(ctx, f"{name} takes one value; got {' '.join(args)}", USAGE)
    if args[0] == "load" and name in _LOAD_REFUSED:
        return _refuse(ctx, PANEL_ONLY if name in _PANEL else RESTART_ONLY, REFUSED)
    try:
        value = row.parse(args[0], ctx.cwd_entered)
    except UsageError as exc:
        return _refuse(ctx, f"{name} {exc}", USAGE)
    except Refused as exc:
        return _refuse(ctx, f"{name}: {exc}", REFUSED)
    if name in _FOLDERS and not os.path.isdir(value):
        return _refuse(ctx, f"{name} takes an existing folder; got {args[0]}", USAGE)
    if name in _LINES:
        if name == "watchdog":
            return _line_write(ctx, name, str(value))
        return _line_write(ctx, name, " ".join(format_number(part) for part in value))

    def change(current: dict) -> dict:
        new = (not current[row.key]) if value == settings_table.TOGGLE else value
        return {} if current[row.key] == new else {row.key: new}

    if name in _RESTART:
        return _restart_write(ctx, name, change)
    code = _receipt(ctx, name, _write(change))
    if name == "libraryfolder":
        _library_warning(ctx, value)
    return code


def _library_warning(ctx: Context, folder: str) -> None:
    """A library folder the engine does not search for wallpapers."""
    from ...storage import paths
    searched = {os.path.realpath(p) for p in (paths.default_wallpapers_dir(),
                                              os.path.expanduser("~/.local/share/lwe/wallpapers"))}
    if os.path.realpath(folder) not in searched:
        ctx.error(f"warning: the engine looks for wallpapers only in {paths.default_wallpapers_dir()} and the Steam "
                  f"Workshop folder, so a wallpaper copied into {folder} may not show")


def _config_set(ctx: Context, name: str, words: list[str]) -> int:
    if name not in settings_table.BY_NAME:
        return _unknown(ctx, name, CONFIG_USAGE)
    from ..registry import discover
    verb = discover().get(name)
    return _unknown(ctx, name, CONFIG_USAGE) if verb is None else verb.run(ctx, words)


def _unset(ctx: Context, args: list[str]) -> int:
    if len(args) != 1:
        return _refuse(ctx, CONFIG_USAGE, USAGE)
    name = args[0]
    if name == "playlist":
        return _refuse(ctx, "playlist has no default", REFUSED)
    if name == "audiosmoothing":
        return _refuse(ctx, "audiosmoothing is not saved", REFUSED)
    if name in ("order", "interval"):
        try:
            from .playlist import UNSET
        except ImportError:
            return _unknown(ctx, name, CONFIG_USAGE)
        return UNSET[name](ctx)
    if name in _LINES:
        return _line_write(ctx, name, None)
    if name not in _PANEL + _RESTART:
        return _unknown(ctx, name, CONFIG_USAGE)
    from ...storage import settings
    key = settings_table.BY_NAME[name].key

    def change(current: dict) -> dict:
        clamp = name in ("resclamp", "effectclamp")
        changes = settings.clamp_unset_changes(current, key) if clamp else {key: None}
        return changes if changes[key] is not None or key in settings.load_set() else {}

    if name in _RESTART:
        return _restart_write(ctx, name, change)
    return _receipt(ctx, name, _write(change))


def _setting_verb(name: str) -> Callable[[Context, list[str]], int]:
    return lambda ctx, args: _set(ctx, name, args)


VERBS = (
    Verb("get", _get, "Prints bare values, one per line, for scripts and status bars.", "Settings"),
    Verb("config", _config, "Lists every setting with its value and where it came from, or reads or sets one.",
         "Settings"),
    *(Verb(row["name"], _setting_verb(row["name"]), row["what"], "Settings") for row in vocabulary.SETTINGS
      if row["name"] in _PANEL + _RESTART + _LINES),
)
