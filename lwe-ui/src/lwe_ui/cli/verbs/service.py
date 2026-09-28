"""lwe service: the engine service's state, and its start, stop, restart and autostart forms.

Every systemctl call goes through engine/daemon_unit.RUNNER. start and restart first rebuild engine-env
and the unit and run daemon-reload (write_files), then wait for the engine and bring it up to date with
the saved settings (push.sync_all). Nothing here enables with --now.
"""
from __future__ import annotations

import json

from .. import DONE, ENGINE_DOWN, REFUSED, USAGE, Context
from ..registry import Verb

LOG = "journalctl --user -u lwe-engine.service"
READY_S = 20
_SHOW = ("LoadState", "ActiveState", "SubState", "UnitFileState", "MainPID", "MemoryCurrent")


class _Stop(Exception):
    """A form that cannot go on: its message and exit code."""

    def __init__(self, message: str, code: int = REFUSED) -> None:
        super().__init__(message)
        self.code = code


def _call(args: list[str]) -> str:
    from ...engine import daemon_unit
    code, out, err = daemon_unit.RUNNER(args)
    if code != 0:
        raise _Stop((err.strip().splitlines() or [f"systemctl {args[0]} failed"])[0])
    return out


def _state() -> dict[str, str]:
    from ... import constants
    args = ["show", constants.ENGINE_SERVICE]
    for name in _SHOW:
        args += ["-p", name]
    return dict(line.split("=", 1) for line in _call(args).splitlines() if "=" in line)


def _pid(state: dict[str, str]) -> int | None:
    pid = state.get("MainPID", "")
    return int(pid) if pid.isdigit() and pid != "0" else None


def _autostart(state: dict[str, str]) -> str:
    return "on" if state.get("UnitFileState") == "enabled" else "off"


def _rebuild() -> list[str]:
    """engine-env, the unit and daemon-reload (write_files); returns the lines to add."""
    from ...engine import daemon_unit
    from ...storage import paths
    outputs = daemon_unit.enumerate_outputs()
    if not outputs and not (paths.config_dir() / daemon_unit.ENV_FILE_NAME).exists():
        raise _Stop("No screens were found and there is no engine-env yet, so the service cannot start; "
                    "run lwe service start from your desktop session.")
    try:
        daemon_unit.write_files(outputs)
    except (ValueError, RuntimeError) as exc:
        raise _Stop(str(exc)) from None
    return [] if outputs else ["No screens were found; engine-env keeps the screens it already names."]


def _launch(args: list[str], old_pid: int | None) -> tuple[list[str], int]:
    """Record the owed bundle in the sync marker, tied to the engine a restart replaces (old_pid),
    start or restart, wait for the engine and sync it; returns the outcome line and the exit code.
    The record stays until a sync to another engine ends every request ok."""
    from ...engine import marker, push
    from ...storage.lock import StoreBusy
    from .. import report
    try:
        marker.ensure(("BUNDLE",), replacing=old_pid)
    except StoreBusy:
        raise
    except OSError as exc:
        raise _Stop(f"The sync record could not be written ({exc}), so nothing was started.") from None
    _call(args)
    not_taken = "The engine has not taken your saved configuration yet"
    if push.wait_ready(old_pid=old_pid, timeout_s=READY_S) is None:
        raise _Stop(f"The service started, but the engine did not answer within {READY_S} s; {LOG} shows why. "
                    f"{not_taken}. {report.OPPORTUNITIES}")
    outcome = push.sync_all("command")
    if outcome.kind == "pending" and outcome.reason == "version":
        raise _Stop(outcome.message or "The running engine is from another build.")
    line = {
        "pending": f"{not_taken} ({outcome.reason}). {report.OPPORTUNITIES}",
        "refused": f"The engine refused part of your settings: {outcome.message}.",
        "uncertain": f"The engine did not answer in time while taking your saved configuration, so it may have "
                     f"applied. {report.OPPORTUNITIES}",
    }.get(outcome.kind)
    return ([line] if line else []), (REFUSED if outcome.kind == "refused" else DONE)


def _status(ctx: Context) -> int:
    from ... import api_client
    from ...engine import daemon_unit
    from .. import settings_table
    state = _state()
    running = state.get("ActiveState") == "active"
    set_up = state.get("LoadState") != "not-found"
    memory = state.get("MemoryCurrent", "")
    memory_bytes = int(memory) if memory.isdigit() else None
    waiting: list[str] = []
    if set_up:
        observed, pending = daemon_unit.restart_state(status=api_client.status())
        keys = {key for key, waits in pending.items() if waits}
        if observed:
            waiting = [row.name for row in settings_table.ROWS
                       if row.form == settings_table.GLOBAL and row.key in keys]
    if ctx.json:
        print(json.dumps({"running": running, "state": state.get("ActiveState", ""), "pid": _pid(state),
                          "autostart": _autostart(state) == "on", "memory_bytes": memory_bytes,
                          "waiting": waiting, "set_up": set_up}, ensure_ascii=False, separators=(",", ":")),
              file=ctx.out)
    elif not set_up:
        print("The service is not set up; lwe service start sets it up.", file=ctx.out)
    else:
        if running:
            print(f"Running: yes (pid {_pid(state)})", file=ctx.out)
        elif state.get("ActiveState") == "failed":
            print(f"Running: failed ({state.get('SubState', '')})", file=ctx.out)
        else:
            print("Running: no", file=ctx.out)
        print(f"Starts at login: {'yes' if _autostart(state) == 'on' else 'no'}", file=ctx.out)
        if memory_bytes is not None:
            print(f"Memory: {memory_bytes // (1024 * 1024)} MiB (the engine and its web helpers)", file=ctx.out)
        if waiting:
            print(f"Waiting for lwe service restart: {', '.join(waiting)}", file=ctx.out)
        print(f"Log: {LOG}", file=ctx.out)
    return DONE if running else ENGINE_DOWN


def _say(ctx: Context, done: str, lines: list[str], state: dict[str, str]) -> None:
    if ctx.json:
        print(json.dumps({"done": done, "autostart": _autostart(state) == "on", "lines": lines},
                         ensure_ascii=False, separators=(",", ":")), file=ctx.out)
    else:
        for line in lines:
            print(line, file=ctx.out)


def _act(ctx: Context, form: str, word: str | None) -> int:
    from ... import constants
    unit = constants.ENGINE_SERVICE
    state = _state()
    running = state.get("ActiveState") == "active"
    unchanged = f"Autostart is unchanged ({_autostart(state)})."
    if form == "stop":
        if state.get("ActiveState") in ("inactive", "failed"):
            _say(ctx, "not running", ["The service was not running."], state)
            return DONE
        _call(["stop", unit])
        _say(ctx, "stopped", [f"Stopped the service. {unchanged}"], state)
        return DONE
    if form == "autostart":
        if state.get("LoadState") == "not-found" and word == "off":
            _say(ctx, "not set up", ["The service is not set up, so there is nothing to change."], state)
            return DONE
        notes = _rebuild() if state.get("LoadState") == "not-found" else []
        _call(["enable" if word == "on" else "disable", unit])
        now = "running" if running else "stopped"
        will = "will" if word == "on" else "will not"
        _say(ctx, f"autostart {word}", [f"The service {will} start when you log in. It is {now} now.", *notes],
             {**state, "UnitFileState": "enabled" if word == "on" else "disabled"})
        return DONE
    notes = _rebuild()
    if form == "start" and running:
        _say(ctx, "already running", [f"The service is already running. {unchanged}", *notes], state)
        return DONE
    if form == "start":
        extra, code = _launch(["start", unit], None)
        _say(ctx, "started", [f"Started the service. {unchanged}", *notes, *extra], state)
        return code
    extra, code = _launch(["restart", unit], _pid(state))
    first = "Restarted the service; the settings marked restart now apply." if running else "Started the service."
    _say(ctx, "restarted" if running else "started", [first, *notes, *extra], state)
    return code


def run(ctx: Context, args: list[str]) -> int:
    forms = {(): "status", ("start",): "start", ("stop",): "stop", ("restart",): "restart",
             ("autostart", "on"): "autostart", ("autostart", "off"): "autostart"}
    form = forms.get(tuple(args))
    if form is None:
        ctx.error(f"lwe service: {' '.join(args)} is not a service command; lwe help service lists them")
        return USAGE
    try:
        if form == "status":
            return _status(ctx)
        return _act(ctx, form, args[1] if form == "autostart" else None)
    except _Stop as stop:
        ctx.error(str(stop))
        return stop.code


VERBS = (Verb("service", run, "Show the service, or start, stop, restart it or set its autostart", "service"),)
