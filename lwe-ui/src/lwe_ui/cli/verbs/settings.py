"""get and config: the saved settings, each read through cli/settings_table.py::read with where its
value comes from. Reads take no lock and write nothing; a running engine from another build is named
on stderr and the read goes on.
"""
from __future__ import annotations

import json

from .. import DONE, ENGINE_DOWN, REFUSED, USAGE, Context, settings_table, vocabulary
from ..registry import Verb

GET_USAGE = "usage: lwe get <setting> [<setting> ...]; lwe config lists the settings"
CONFIG_USAGE = "usage: lwe config [<setting>]; lwe config lists the settings"
_NEEDS_STATUS = ("order", "interval", "playlist", "audiosmoothing")


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
    if len(args) > 1:
        ctx.error(CONFIG_USAGE)
        return USAGE
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


VERBS = (
    Verb("get", _get, "Prints bare values, one per line, for scripts and status bars.", "Settings"),
    Verb("config", _config, "Lists every setting with its value and where it came from, or reads or sets one.",
         "Settings"),
)
