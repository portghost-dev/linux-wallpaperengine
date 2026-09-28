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


def _import(ctx: Context, path: str) -> int:
    from ...engine import daemon_unit, push
    from ...storage import backup, paths
    paths.ensure_dirs()
    r = backup.apply(backup.preflight(path))
    r.pop("plan", None)
    if r.get("refused"):
        _print(ctx, f"Refused {path}", r)
        return REFUSED
    outcome = push.sync_all("command", ("BUNDLE", "CURRENT"))
    env_state = daemon_unit.write_env()
    waiting = restart_line()
    sync = (f"The engine refused it: {outcome.message}" if outcome.kind == "refused" else _SYNC[outcome.kind])
    extra = [sync] + ([NO_SCREENS + "."] if env_state == "no screens" else []) + ([waiting] if waiting else [])
    extra += [FOLLOWUPS[f["kind"]] for f in r.get("followups") or [] if f.get("kind") in FOLLOWUPS]
    r.update(sync={"outcome": outcome.kind, "reason": outcome.reason, "message": outcome.message},
             engine_env=env_state, restart=waiting)
    _print(ctx, backup.receipt_line(r), r, extra)
    return REFUSED if r.get("errors") or outcome.kind == "refused" else DONE


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
