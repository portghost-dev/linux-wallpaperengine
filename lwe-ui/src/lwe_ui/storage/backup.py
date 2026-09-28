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
a file: each store reads its member through the same validators a normal load applies, a
key this build does not know is preserved in foreign.json rather than dropped, and every
clamp, snap and alias is named in the receipt. The manifest carries each store's schema, so
an importer can tell a value the other build defaulted from one the user set, and a format
newer than FORMAT is restored best effort with a note rather than refused: the only refusal
left is a file that is not an LWE backup at all.
"""
from __future__ import annotations

import datetime
import fcntl
import io
import json
import os
import socket
import zipfile
from pathlib import Path
from typing import Any

from . import foreign, paths, registry, settings, tags, themes

FORMAT = 1
EXTENSION = ".lwebackup"
MANIFEST = "manifest.json"
#: pre-restore snapshots kept under state_dir()/"backups"; the oldest beyond this are deleted
SNAPSHOTS_KEPT = 5


def default_name() -> str:
    return f"lwe-backup-{datetime.datetime.now():%Y-%m-%d-%H%M}{EXTENSION}"


def _receipt(kind: str) -> dict[str, Any]:
    return {"kind": kind, "path": "", "counts": {}, "dropped": [], "held": [],
            "reresolved": [], "followups": [], "errors": [], "adjusted": [], "preserved": [],
            "notes": []}


def schemas() -> dict[str, Any]:
    """What this build's stores hold, as the manifest carries it: JSON-safe, per store."""
    from .. import constants as C
    return json.loads(json.dumps({
        "settings": C.SETTINGS_SCHEMA,
        "overrides": {"keys": C.WP_SCHEMA, "prop_prefix": C.WP_PROP_PREFIX},
        "playlists": C.PLAYLIST_SCHEMA,
        "theme": list(themes.CONFIG_KEYS),
        "discovery": dict(C.DISCOVER_DEFAULTS),
        "tags": list(tags._VALID_STATES),
    }))


# --- export ---------------------------------------------------------------------------

def export_to(path: str | Path) -> dict[str, Any]:
    r = _receipt("export")
    r["path"] = str(path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for st in registry.STORES:
            try:
                st.export(z, r)
            except Exception as exc:
                r["errors"].append({"file": st.name, "reason": f"could not be read: {exc}"})
        r["counts"].setdefault("preserved", 0)
        manifest = {
            "format": FORMAT,
            "app": "lwe-ui",
            "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "host": socket.gethostname(),
            "counts": r["counts"],
            "machine_keys": list(settings.MACHINE_KEYS),
            "schemas": schemas(),
        }
        z.writestr(MANIFEST, json.dumps(manifest, indent=1))
    # temp beside the target then rename: a failure mid-write never leaves a truncated
    # backup where a good one was
    target = Path(path)
    tmp = target.with_name(target.name + ".part")
    try:
        tmp.write_bytes(buf.getvalue())
        os.replace(tmp, target)
    except OSError as exc:
        r["errors"].append({"file": str(target), "reason": f"could not be written: {exc}"})
        r["refused"] = True
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
        fmt = int(r["manifest"].get("format", FORMAT))
        if fmt > FORMAT:
            r["notes"].append({"kind": "newer", "format": fmt})
        current = settings.load()
        for st in registry.STORES:
            # what this build's settings would be after the import: the library every later
            # store holds its members against
            cfg_after = {**current, **(plan.get("settings") or {})}
            try:
                ok = st.preflight(z, r, plan, cfg_after)
            except Exception as exc:
                # one member the store cannot read holds that store, never the archive
                r["errors"].append({"file": st.name, "reason": f"not readable, skipped: {exc}"})
                continue
            if not ok:
                r["refused"] = True
                return r
        foreign.count_plan(plan, r)
        _referential(r, plan, current)
    return r


def _referential(r: dict[str, Any], plan: dict[str, Any], current: dict[str, Any]) -> None:
    """A planned settings value may only name a playlist the import leaves behind: one in
    the plan or already on disk. A SCHEDULE entry with an empty playlist is kept in place and
    noted; one that names no such playlist is dropped from the planned value and the schedule
    switched off, as the panel does when it loses an entry; ACTIVE_PLAYLIST is named and left
    for playlists.active_slug."""
    planned = plan.get("settings")
    if not planned:
        return

    def known(slug: str) -> bool:
        return bool(slug) and (slug in (plan.get("playlists") or {})
                               or paths.playlist_file(slug).exists())

    entries, kept = str(planned.get("SCHEDULE", "")).split(";"), []
    for entry in entries:
        if not entry.strip():
            continue
        slug = entry.split("=", 1)[1].strip() if "=" in entry else ""
        if known(slug):
            kept.append(entry)
        elif "=" in entry and not slug:
            kept.append(entry)
            r["notes"].append({"kind": "unset", "key": "SCHEDULE", "slug": ""})
        else:
            r["notes"].append({"kind": "dangling", "key": "SCHEDULE", "slug": slug})
    if "SCHEDULE" in planned:
        planned["SCHEDULE"] = ";".join(kept)
        on = planned.get("SCHEDULE_ENABLED", current.get("SCHEDULE_ENABLED", False))
        if len(kept) < len([e for e in entries if e.strip()]) and on:
            r["adjusted"].append({"kind": "snap", "store": "settings", "id": "settings.conf",
                                  "key": "SCHEDULE_ENABLED", "from": True, "to": False})
            planned["SCHEDULE_ENABLED"] = False
    active = str(planned.get("ACTIVE_PLAYLIST", ""))
    if active and not known(active):
        r["notes"].append({"kind": "dangling", "key": "ACTIVE_PLAYLIST", "slug": active})


def snapshot(r: dict[str, Any]) -> bool:
    """Export the configuration as it stands into state_dir()/backups, keeping the newest
    SNAPSHOTS_KEPT of them. False when nothing was written, which abandons the apply."""
    try:
        d = paths.state_dir() / "backups"
        d.mkdir(parents=True, exist_ok=True)
        stem = f"pre-restore-{datetime.datetime.now():%Y%m%d-%H%M%S}"
        path = d / f"{stem}{EXTENSION}"
        n = 1
        while path.exists():
            n += 1
            path = d / f"{stem}-{n}{EXTENSION}"
        er = export_to(path)
        if er["errors"]:
            # a rollback file missing a member is no rollback: name the member, write nothing
            path.unlink(missing_ok=True)
            first = er["errors"][0]
            raise RuntimeError(f"{first['file']} {first['reason']}")
        by_age = sorted(d.glob(f"pre-restore-*{EXTENSION}"), key=lambda p: p.stat().st_mtime_ns)
        for old in by_age[:-SNAPSHOTS_KEPT]:
            old.unlink(missing_ok=True)
    except Exception as exc:
        r["errors"].append({"file": "pre-restore snapshot",
                            "reason": f"The configuration could not be backed up first: {exc}"})
        r["refused"] = True
        return False
    r["notes"].append({"kind": "snapshot", "path": str(path)})
    return True


def apply(plan_receipt: dict[str, Any]) -> dict[str, Any]:
    """Write a preflight's plan through this build's stores, after a snapshot of what is
    there now and after the sync marker records BUNDLE and CURRENT with a raised generation,
    so the engine side is owed before any store changes. Returns the receipt with the plan
    removed and any write failure added to errors; the receipt is refused only when nothing
    was written."""
    r = dict(plan_receipt)
    plan = r.pop("plan", None) or {}
    if r.get("refused"):
        return r
    paths.ensure_dirs()
    lock = open(paths.state_dir() / "restore.lock", "w")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            r["errors"].append({"file": "restore", "reason": "Another restore is running."})
            r["refused"] = True
            return r
        if not snapshot(r):
            return r
        from ..engine import marker
        try:
            with marker.writing(("BUNDLE", "CURRENT")):
                pass
        except OSError as exc:
            r["errors"].append({"file": "sync-pending", "reason": f"The sync marker could not be written: {exc}"})
            r["refused"] = True
            return r
        written = 0
        for st in registry.STORES:
            if not st.apply(plan, r):
                if written == 0:
                    r["refused"] = True
                return r
            written += 1
        foreign.apply_plan(plan, r)
    finally:
        lock.close()
    return r


def import_from(path: str | Path) -> dict[str, Any]:
    return apply(preflight(path))


def receipt_line(r: dict[str, Any]) -> str:
    """The one-line receipt the Configuration row shows after an import. Once any write
    failed the line names the failure and no count, since the counts were the plan."""
    if r.get("refused") or (r.get("errors") and not r.get("counts")):
        return ""
    if r.get("errors"):
        first = r["errors"][0].get("file", "")
        return f"Restore incomplete · {len(r['errors'])} failed" + (f" ({first})" if first else "")
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
    if r.get("adjusted"):
        line += f" · {len(r['adjusted'])} adjusted"
    if c.get("preserved"):
        line += f" · {c['preserved']} kept aside"
    kinds = {n.get("kind") for n in r.get("notes", [])}
    if "no-settings" in kinds:
        line += " · no settings in it"
    if "newer" in kinds:
        line += " · from a newer version"
    return line


# --- command line -----------------------------------------------------------------------

_RECEIPT_LISTS = ("reresolved", "held", "dropped", "adjusted", "preserved", "notes",
                  "followups", "errors")


def _print_receipt(r: dict[str, Any]) -> int:
    """Print a receipt as plain lines; returns the exit code (1 when anything failed)."""
    if r.get("kind") == "export":
        print(f"Exported {r['path']}")
    elif "notes" in r and not any(n.get("kind") == "snapshot" for n in r["notes"]) and not r.get("refused"):
        print("Would restore: " + (receipt_line(r) or "nothing"))
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
    """`python3 -m lwe_ui.storage.backup export|restore|preview <file>`: the panel's backup
    without the panel, for the night it will not start; preview is the whole plan and its
    receipt with nothing written. Imports no Qt."""
    import argparse

    ap = argparse.ArgumentParser(prog="python3 -m lwe_ui.storage.backup",
                                 description="Export or restore the LWE configuration.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("export", help="write the current configuration to <file>").add_argument("file")
    sub.add_parser("restore", help="write <file>'s configuration over the current one").add_argument("file")
    sub.add_parser("preview", help="show what restoring <file> would do, writing nothing").add_argument("file")
    args = ap.parse_args(argv)
    paths.ensure_dirs()
    if args.cmd == "export":
        return _print_receipt(export_to(args.file))
    if args.cmd == "preview":
        r = preflight(args.file)
        r.pop("plan", None)
        return _print_receipt(r)
    return _print_receipt(import_from(args.file))


if __name__ == "__main__":
    raise SystemExit(main())
