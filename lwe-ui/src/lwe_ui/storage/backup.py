"""Configuration backup: one `.lwebackup` file, a zip with a manifest.

What travels is what the user authored and the app cannot regenerate: settings, theme,
discovery, playlists, per-wallpaper overrides, tags, favourites and the app rules. Nothing
generated travels (the engine env file, caches, state, logs).

This module owns the archive, not the list of what goes in it: every store declares its own
Store record and `registry.STORES` collects them, so a store added later reaches the backup
by declaring itself and is caught by test_store_ownership when it does not. Settings are
first in that order because every other store's plan is decided against the settings the
import would leave.

Durability across builds comes from the schemas, not from the files. Import never extracts
a file: each store reads its member through the same validators a normal load applies, and
unknown keys are dropped and named. The manifest carries a format version so a future
breaking change is refused with a plain reason instead of mangled.
"""
from __future__ import annotations

import datetime
import io
import json
import os
import socket
import zipfile
from pathlib import Path
from typing import Any

from . import paths, registry, settings

FORMAT = 1
EXTENSION = ".lwebackup"
MANIFEST = "manifest.json"


def default_name() -> str:
    return f"lwe-backup-{datetime.datetime.now():%Y-%m-%d-%H%M}{EXTENSION}"


def _receipt(kind: str) -> dict[str, Any]:
    return {"kind": kind, "path": "", "counts": {}, "dropped": [], "held": [],
            "reresolved": [], "followups": [], "errors": []}


# --- export ---------------------------------------------------------------------------

def export_to(path: str | Path) -> dict[str, Any]:
    r = _receipt("export")
    r["path"] = str(path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for st in registry.STORES:
            st.export(z, r)
        manifest = {
            "format": FORMAT,
            "app": "lwe-ui",
            "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "host": socket.gethostname(),
            "counts": r["counts"],
            "machine_keys": list(settings.MACHINE_KEYS),
        }
        z.writestr(MANIFEST, json.dumps(manifest, indent=1))
    # temp beside the target then rename: a failure mid-write never leaves a truncated
    # backup where a good one was
    target = Path(path)
    tmp = target.with_name(target.name + ".part")
    tmp.write_bytes(buf.getvalue())
    os.replace(tmp, target)
    return r


# --- import ---------------------------------------------------------------------------

def _read_manifest(z: zipfile.ZipFile) -> dict[str, Any]:
    try:
        m = json.loads(z.read(MANIFEST).decode("utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError):
        raise ValueError("That file is not an LWE backup.")
    if not isinstance(m, dict) or m.get("app") != "lwe-ui":
        raise ValueError("That file is not an LWE backup.")
    try:
        fmt = int(m.get("format", 0))
    except (TypeError, ValueError):
        fmt = 0
    if fmt < 1:
        raise ValueError("That file is not an LWE backup.")
    if fmt > FORMAT:
        raise ValueError("That backup was made by a newer version of LWE.")
    return m


def preflight(path: str | Path) -> dict[str, Any]:
    """Everything the import will do, decided before anything is written."""
    r = _receipt("import")
    r["path"] = str(path)
    plan: dict[str, Any] = {}
    r["plan"] = plan
    try:
        z = zipfile.ZipFile(str(path))
    except (OSError, zipfile.BadZipFile):
        r["errors"].append({"file": os.path.basename(str(path)), "reason": "That file is not an LWE backup."})
        r["refused"] = True
        return r
    with z:
        try:
            r["manifest"] = _read_manifest(z)
        except ValueError as exc:
            r["errors"].append({"file": MANIFEST, "reason": str(exc)})
            r["refused"] = True
            return r
        current = settings.load()
        for st in registry.STORES:
            # what this build's settings would be after the import: the library every later
            # store holds its members against
            cfg_after = {**current, **(plan.get("settings") or {})}
            if not st.preflight(z, r, plan, cfg_after):
                r["refused"] = True
                return r
    return r


def apply(plan_receipt: dict[str, Any]) -> dict[str, Any]:
    """Write a preflight's plan through this build's stores. Returns the receipt with the
    plan removed and any write failure added to errors."""
    r = dict(plan_receipt)
    plan = r.pop("plan", None) or {}
    if r["errors"] and not plan.get("settings"):
        return r
    paths.ensure_dirs()
    for st in registry.STORES:
        if not st.apply(plan, r):
            return r
    return r


def import_from(path: str | Path) -> dict[str, Any]:
    return apply(preflight(path))


def receipt_line(r: dict[str, Any]) -> str:
    """The one-line receipt the Configuration row shows after an import."""
    if r.get("errors") and not r.get("counts"):
        return ""
    c = r.get("counts", {})
    parts = []
    if c.get("playlists"):
        parts.append(f"{c['playlists']} playlist{'s' if c['playlists'] != 1 else ''}")
    if c.get("overrides"):
        parts.append(f"{c['overrides']} override{'s' if c['overrides'] != 1 else ''}")
    if c.get("tags"):
        parts.append(f"{c['tags']} tag{'s' if c['tags'] != 1 else ''}")
    line = "Restored " + (", ".join(parts) if parts else "settings")
    waiting = c.get("overrides_held", 0) + c.get("tags_held", 0)
    if waiting:
        line += f" · {waiting} waiting for wallpapers"
    if r.get("dropped"):
        line += f" · {len(r['dropped'])} dropped"
    return line


# --- command line -----------------------------------------------------------------------

_RECEIPT_LISTS = ("reresolved", "held", "dropped", "followups", "errors")


def _print_receipt(r: dict[str, Any]) -> int:
    """Print a receipt as plain lines; returns the exit code (1 when anything failed)."""
    if r.get("kind") == "export":
        print(f"Exported {r['path']}")
    else:
        print(receipt_line(r) or f"Refused {r['path']}")
    counts = r.get("counts") or {}
    if counts:
        print("counts: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    for name in _RECEIPT_LISTS:
        for item in r.get(name) or []:
            body = ", ".join(f"{k}={v}" for k, v in item.items()) if isinstance(item, dict) else str(item)
            print(f"{name}: {body}")
    return 1 if r.get("errors") else 0


def main(argv: list[str] | None = None) -> int:
    """`python3 -m lwe_ui.storage.backup export|restore <file>`: the panel's backup without
    the panel, for the night it will not start. Imports no Qt."""
    import argparse

    ap = argparse.ArgumentParser(prog="python3 -m lwe_ui.storage.backup",
                                 description="Export or restore the LWE configuration.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("export", help="write the current configuration to <file>").add_argument("file")
    sub.add_parser("restore", help="write <file>'s configuration over the current one").add_argument("file")
    args = ap.parse_args(argv)
    paths.ensure_dirs()
    return _print_receipt(export_to(args.file) if args.cmd == "export" else import_from(args.file))


if __name__ == "__main__":
    raise SystemExit(main())
