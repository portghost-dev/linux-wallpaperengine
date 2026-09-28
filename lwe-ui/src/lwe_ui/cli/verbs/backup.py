"""lwe backup: export, preview and import a settings backup through storage/backup.py's functions. An
import then rebuilds the engine from the store the way a reload does, and rebuilds engine-env."""
from __future__ import annotations

import json
import os

from .. import DONE, REFUSED, USAGE, Context, report, values
from ..registry import Verb

USAGE_TEXT = "usage: lwe backup export|import|preview <file>"
NO_SCREENS = ("engine-env was not rebuilt because no screens were found; it catches up when the panel next starts "
              "or lwe service start or restart runs from your desktop session")
FOLLOWUPS = {"relaunch": "The panel's interface scale applies at its next start.",
             "rescan": "lwe workshop looks for new downloads."}
_LISTS = ("reresolved", "held", "dropped", "adjusted", "preserved", "notes", "errors")
_SYNC = {"applied": "Sent to the engine.",
         "pending": "The service is not running or is busy, so it is not applied yet. " + report.OPPORTUNITIES,
         "uncertain": "The engine did not answer in time, so it may have applied."}
_STEPS = {"sync": "sending it to the engine", "engine-env": "rebuilding engine-env",
          "restart": "checking which settings wait for a service restart"}


def _print(ctx: Context, headline: str, r: dict, extra: list[str] = ()) -> None:
    """The receipt: its line, its lists and the lines after them; under -j the receipt alone."""
    if ctx.json:
        print(json.dumps(r, ensure_ascii=False, separators=(",", ":"), default=str), file=ctx.out)
        return
    print(headline, file=ctx.out)
    for name in _LISTS:
        for item in r.get(name) or []:
            body = ", ".join(f"{k}={v}" for k, v in item.items()) if isinstance(item, dict) else str(item)
            print(f"{name}: {body}", file=ctx.out)
    for line in extra:
        print(line, file=ctx.out)


def restart_line() -> str | None:
    """The settings whose engine-env lines differ from what the running engine reads, waiting for a
    service restart; None when none waits or no engine runs."""
    from ...engine import daemon_unit
    from ...storage import paths
    from .. import settings_table
    try:
        existing = (paths.config_dir() / daemon_unit.ENV_FILE_NAME).read_text(encoding="utf-8")
    except OSError:
        existing = None
    _, pending = daemon_unit.restart_state(env_text=daemon_unit.build_env_content([], existing))
    keys = {"RENDER_RESOLUTION": ("SSFACTOR", "CLAMPCOMPOSITES")}
    names = [row.name for key, waits in pending.items() if waits for row in settings_table.ROWS
             if row.form == settings_table.GLOBAL and row.key in keys.get(key, (key,))]
    return f"{', '.join(names)}: waiting for lwe service restart." if names else None


def _export(ctx: Context, path: str) -> int:
    from ...storage import backup, paths
    if os.path.isdir(path):
        ctx.error(f"cannot write {path}: it is a folder")
        return REFUSED
    if path.endswith(("/", os.sep)):
        ctx.error(f"cannot write {path}: it names a folder")
        return REFUSED
    paths.ensure_dirs()
    replaced = os.path.exists(path)
    r = backup.export_to(path)
    failed = bool(r.get("errors"))
    headline = f"Could not export {path}" if failed else f"Exported {path}" + (" (replaced the file that was there)"
                                                                                    if replaced else "")
    _print(ctx, headline, r)
    return REFUSED if failed else DONE


def _preview(ctx: Context, path: str) -> int:
    from ...storage import backup, paths
    paths.ensure_dirs()
    r = backup.preflight(path)
    r.pop("plan", None)
    headline = f"Refused {path}" if r.get("refused") else "Would restore: " + (backup.receipt_line(r) or "nothing")
    _print(ctx, headline, r)
    return REFUSED if r.get("refused") else DONE


def _stores(r: dict) -> tuple[list[str], list[str]]:
    """(written, failed): the stores an apply wrote and the ones an error names a member of."""
    import fnmatch
    from ...storage import registry
    files = [str(e.get("file") or "") for e in r.get("errors") or []]
    failed = [st.name for st in registry.STORES if any(fnmatch.fnmatch(f, pat) for f in files for pat in st.owns)]
    return [st.name for st in registry.STORES if st.name not in failed], failed


def _after(failures: list[dict], step: str, fn):
    """fn() for a step after the stores were written; a failure is recorded and gives None."""
    try:
        return fn()
    except Exception as exc:
        failures.append({"step": step, "reason": f"{type(exc).__name__}: {exc}"})
        return None


def _import(ctx: Context, path: str) -> int:
    from ... import api_client, version
    from ...engine import daemon_unit, push
    from ...storage import backup, paths
    status = api_client.status()
    if status is not None:
        try:
            refusal = version.running_refusal(status, version.panel_stamp())
        except version.StampError as exc:
            refusal = str(exc)
        if refusal is not None:
            ctx.error(refusal)
            return REFUSED
    paths.ensure_dirs()
    r = backup.apply(backup.preflight(path))
    r.pop("plan", None)
    if r.get("refused"):
        _print(ctx, f"Refused {path}", r)
        return REFUSED
    failures: list[dict] = []
    outcome = _after(failures, "sync", lambda: push.sync_all("command", ("BUNDLE", "CURRENT")))
    env_state = _after(failures, "engine-env", daemon_unit.write_env)
    waiting = _after(failures, "restart", restart_line)
    extra = []
    if outcome is not None:
        extra.append(f"The engine refused it: {outcome.message}" if outcome.kind == "refused"
                     else _SYNC[outcome.kind])
    extra += ([NO_SCREENS + "."] if env_state == "no screens" else []) + ([waiting] if waiting else [])
    extra += [FOLLOWUPS[f["kind"]] for f in r.get("followups") or [] if f.get("kind") in FOLLOWUPS]
    r.update(sync=None if outcome is None else {"outcome": outcome.kind, "reason": outcome.reason,
                                                "message": outcome.message},
             engine_env=env_state, restart=waiting)
    if r.get("errors") or failures:
        written, failed = _stores(r)
        snapshot = next((n.get("path") for n in r.get("notes") or [] if n.get("kind") == "snapshot"), None)
        r.update(written=written, failed=failed, failures=failures)
        extra += [f"The stores were restored, but {_STEPS[f['step']]} failed: {f['reason']}" for f in failures]
        extra.append("Restored: " + (", ".join(written) or "nothing"))
        if failed:
            extra.append("Not restored: " + ", ".join(failed))
        if snapshot:
            extra.append(f"The configuration from before this import is in {snapshot}; "
                         f"lwe backup import {snapshot} puts it back.")
    _print(ctx, backup.receipt_line(r), r, extra)
    refused = outcome is not None and outcome.kind == "refused"
    return REFUSED if r.get("errors") or failures or refused else DONE


def run(ctx: Context, args: list[str]) -> int:
    if len(args) != 2 or args[0] not in ("export", "import", "preview"):
        ctx.error(USAGE_TEXT)
        return USAGE
    try:
        path = values.resolve_path(args[1], ctx.cwd_entered)
    except values.UsageError as exc:
        ctx.error(f"backup {args[0]} {exc}")
        return USAGE
    except values.Refused as exc:
        ctx.error(str(exc))
        return REFUSED
    if args[0] != "export" and not os.path.isfile(path):
        ctx.error(f"{path} is not a file")
        return REFUSED
    return {"export": _export, "preview": _preview, "import": _import}[args[0]](ctx, path)


VERBS = (Verb("backup", run, "Saves, restores or previews a settings backup.", "Settings"),)
