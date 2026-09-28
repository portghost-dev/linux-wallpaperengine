"""lwe debug: a debugging switch saved for the next service start as its engine variable, one line in
engine-env. cli/debug_table.py checks the value the way the engine does, and
engine/daemon_unit.py::write_env writes the file; nothing is sent to the engine.
"""
from __future__ import annotations

import json

from .. import DONE, REFUSED, USAGE, Context, debug_table
from ..registry import Verb

USAGE_TEXT = ("usage: lwe debug <switch> <value>, lwe debug <switch> or lwe debug unset <switch>; "
              "help --debug lists the switches")
NO_SCREENS = ("not saved: engine-env is only rewritten where screens are found; run it from your desktop "
              "session, or open the panel once")
APPLIES = "for the next service start (lwe service restart applies it)"


def _print(ctx: Context, text: str, shape: dict) -> None:
    if ctx.json:
        print(json.dumps(shape, ensure_ascii=False, separators=(",", ":")), file=ctx.out)
    else:
        print(text, file=ctx.out)


def _engine_env() -> tuple[str, dict[str, str]]:
    """engine-env's text and its variables as systemd reads them; "" and {} when there is no file."""
    from ...engine import daemon_unit
    from ...storage import paths
    try:
        text = (paths.config_dir() / daemon_unit.ENV_FILE_NAME).read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    return text, daemon_unit.parse_env(text.splitlines())


def _line(text: str, variable: str) -> str | None:
    """The last line of engine-env that assigns `variable`, as written."""
    found = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#") and stripped.split("=", 1)[0].strip() == variable and "=" in stripped:
            found = stripped
    return found


def _as_switch(row: debug_table.Row, value: str | None) -> str | None:
    """An engine-env value read back as the switch's word where a word sets it, else as it is."""
    if value is None:
        return None
    return next((word for word, set_to in row.words if set_to == value), value)


def _write(variable: str, value: str | None) -> str:
    import warnings
    from ...engine import daemon_unit
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return daemon_unit.write_env({variable: value})


def _show(ctx: Context, row: debug_table.Row) -> int:
    _, env = _engine_env()
    value = _as_switch(row, env.get(row.variable))
    _print(ctx, f"{row.name} takes {row.accepts}\n{row.description}\n"
                f"engine-env: {'not set' if value is None else value}",
           {"switch": row.name, "variable": row.variable, "accepts": row.accepts, "value": value})
    return DONE


def _unset(ctx: Context, row: debug_table.Row) -> int:
    _, env = _engine_env()
    shape = {"switch": row.name, "variable": row.variable, "value": None, "line": None}
    if row.variable not in env:
        _print(ctx, f"{row.name} is not set", shape)
        return DONE
    if _write(row.variable, None) == "no screens":
        ctx.error(NO_SCREENS)
        return REFUSED
    _print(ctx, f"{row.name}: removed {APPLIES}", shape)
    return DONE


def _set(ctx: Context, row: debug_table.Row, given: str) -> int:
    variable, value = debug_table.resolve(row.name, given)
    if "\0" in given or given.splitlines() != [given]:
        ctx.error(f"{row.name}: a value with a line break or NUL cannot be saved in engine-env")
        return USAGE
    text, env = _engine_env()
    result = "unchanged" if env.get(variable) == value else _write(variable, value)
    if result == "no screens":
        ctx.error(NO_SCREENS)
        return REFUSED
    if result == "written":
        text, _ = _engine_env()
    receipt = f"saved {APPLIES}" if result == "written" else "unchanged, already saved for the next service start"
    _print(ctx, f"{row.name} {given}: {receipt}",
           {"switch": row.name, "variable": variable, "value": given, "line": _line(text, variable)})
    return DONE


def run(ctx: Context, args: list[str]) -> int:
    unset = bool(args) and args[0] == "unset"
    if not args or (unset and len(args) != 2):
        ctx.error(USAGE_TEXT)
        return USAGE
    if len(args) > 2:
        ctx.error("debug takes <switch> <value>; quote a value that has spaces")
        return USAGE
    name = args[1] if unset else args[0]
    if name in debug_table.BY_VARIABLE:
        ctx.error(f"{name} is an engine variable; debug takes its switch, {debug_table.BY_VARIABLE[name].name}")
        return USAGE
    try:
        row = debug_table.row(name)
        if unset:
            return _unset(ctx, row)
        if len(args) == 1:
            return _show(ctx, row)
        return _set(ctx, row, args[1])
    except debug_table.Refusal as exc:
        ctx.error(str(exc))
        return USAGE


VERBS = (Verb("debug", run, "Sets one debugging switch by its plain name, for the next service start",
              "Debugging"),)
