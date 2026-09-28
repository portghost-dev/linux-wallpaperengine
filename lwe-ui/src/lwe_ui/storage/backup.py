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

import contextlib
import datetime
import errno
import fcntl
import io
import json
import os
import re
import socket
import stat
import sys
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from . import foreign, meta, paths, registry, settings, tags, themes
from . import lock as store_lock

FORMAT = 1
EXTENSION = ".lwebackup"
MANIFEST = "manifest.json"
#: pre-restore snapshots kept under state_dir()/"backups"; the oldest beyond this are deleted
SNAPSHOTS_KEPT = 5
#: the name snapshot() gives a pre-restore snapshot: its time, and a counter when that name is taken
_SNAPSHOT_NAME = re.compile(r"pre-restore-[0-9]{8}-[0-9]{6}(?:-[1-9][0-9]*)?" + re.escape(EXTENSION))
#: beside the snapshots: names the snapshot an import that failed left as the way back (settle)
RECOVERY = "recovery.json"
#: settle's reason when a successful import cannot remove RECOVERY
_NOT_REMOVED = "could not be removed"


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


_restore = threading.local()


@contextlib.contextmanager
def restoring() -> Iterator[bool]:
    """state_dir()/restore.lock for the body of the with statement, taken without waiting: True while
    this thread holds it, False when another restore holds it. A door holds it from apply through its
    later steps and settle, so no other import runs in between; a nested use on the same thread shares
    the outermost one and takes no second flock. The lock opens without following a link or blocking
    (created 0600) and must be a regular file; anything else raises OSError."""
    outer = getattr(_restore, "held", None)
    if outer is not None:
        yield outer
        return
    paths.ensure_dirs()
    path = paths.state_dir() / "restore.lock"
    with open(path, "rb", buffering=0,
              opener=lambda name, flags: os.open(name, flags | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)) as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise OSError(errno.EINVAL, "not a regular file", str(path))
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            got = True
        except OSError:
            got = False
        _restore.held = got
        try:
            yield got
        finally:
            _restore.held = None


def _recovery() -> tuple[Path, str | None] | None:
    """recovery.json's path and the snapshot file name it keeps, or None when there is no
    recovery.json; the name is None when the file cannot be read or names anything but a file of
    the backups folder named as snapshot() names its pre-restore snapshots (_SNAPSHOT_NAME), or when that
    name is in the folder as anything but a regular file that opens as a backup (_opens_as_backup)."""
    rec = paths.state_dir() / "backups" / RECOVERY
    if not os.path.lexists(rec):
        return None
    try:
        name = json.loads(rec.read_text(encoding="utf-8")).get("snapshot")
    except (OSError, ValueError, AttributeError):
        return rec, None
    if not isinstance(name, str) or not _SNAPSHOT_NAME.fullmatch(name):
        return rec, None
    if os.path.lexists(rec.parent / name) and not _opens_as_backup(rec.parent / name):
        return rec, None
    return rec, name


def _opens_as_backup(path: Path) -> bool:
    """Whether path is a regular file, not a link, that opens as a backup the way preflight opens one."""
    try:
        with open(path, "rb", opener=lambda name, flags: os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK)) as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return False
            with zipfile.ZipFile(f) as z:
                _read_manifest(z)
    except (OSError, zipfile.BadZipFile, ValueError):
        return False
    return True


def settle(r: dict[str, Any], failed: bool) -> None:
    """An import's last step, from the door that ran it, still inside that door's restoring(), with
    the door's verdict: an import that failed after taking its pre-restore snapshot makes that
    snapshot the recovery snapshot, named with the time in recovery.json, written atomically; one
    that failed without taking a snapshot leaves recovery.json as it is; one that succeeded removes
    it. A recovery.json that cannot be written or removed is added to the receipt's errors."""
    rec = paths.state_dir() / "backups" / RECOVERY
    if not failed:
        try:
            rec.unlink(missing_ok=True)
        except OSError as exc:
            r.setdefault("errors", []).append({"file": RECOVERY, "reason": f"{_NOT_REMOVED}: {exc}"})
        return
    taken = next((n.get("path") for n in r.get("notes") or [] if n.get("kind") == "snapshot"), None)
    if not taken:
        return
    tmp = rec.with_name(rec.name + ".part")
    try:
        tmp.write_text(json.dumps({"snapshot": Path(taken).name,
                                   "since": datetime.datetime.now().isoformat(timespec="seconds")}),
                       encoding="utf-8")
        os.replace(tmp, rec)
    except OSError as exc:
        r.setdefault("errors", []).append({"file": RECOVERY, "reason": f"could not be written: {exc}"})


def snapshot(r: dict[str, Any]) -> bool:
    """Export the configuration as it stands into state_dir()/backups, keeping the newest
    SNAPSHOTS_KEPT of them and the one recovery.json names. False when nothing was written, which
    abandons the apply."""
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
        kept = (_recovery() or (None, None))[1]
        by_age = sorted((p for p in d.glob(f"pre-restore-*{EXTENSION}") if p.name != kept),
                        key=lambda p: p.stat().st_mtime_ns)
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
    """Write a preflight's plan through this build's stores, under restoring(): another restore
    holding it refuses the import. A recovery.json whose snapshot is missing, or that cannot be read,
    refuses it next, with nothing written. The locks of the stores it writes (each Store's name is its
    lock's) are taken next, in rank order, then the sync marker records BUNDLE and CURRENT with a
    raised generation, and only then is the pre-restore snapshot of what is there now written, unless
    recovery.json names one, which holds the state from before the first failed import; a busy store
    lock or a marker that cannot be set refuses the import before anything is written, the snapshot
    included. The marker stays held until the last store write ends, so the engine side is owed
    before any store changes and no clearer can clear it first; it is released before the store
    locks. Returns the receipt with the plan removed and any write failure added to errors; the
    receipt is refused only when nothing was written. The door that ran the import then calls
    settle, still inside its restoring()."""
    r = dict(plan_receipt)
    plan = r.pop("plan", None) or {}
    if r.get("refused"):
        return r
    with restoring() as ours:
        if not ours:
            r["errors"].append({"file": "restore", "reason": "Another restore is running."})
            r["refused"] = True
            return r
        recovery = _recovery()
        if recovery is not None:
            rec, name = recovery
            if name is None:
                reason = (f"Nothing was imported: {rec} cannot be read, so the snapshot it keeps from before an "
                          f"earlier failed import cannot be found. Deleting {rec} clears this block.")
            elif not (rec.parent / name).is_file():
                reason = (f"Nothing was imported: {rec.parent / name}, the snapshot kept from before an earlier "
                          f"failed import, is missing. Deleting {rec} clears this block.")
            else:
                reason = None
            if reason is not None:
                r["errors"].append({"file": RECOVERY, "reason": reason})
                r["refused"] = True
                return r
        from ..engine import marker
        with contextlib.ExitStack() as held:
            try:
                for name in sorted({"foreign", *(st.name for st in registry.STORES)}, key=store_lock._ORDER.index):
                    held.enter_context(tags.held() if name == "tags" else meta.held() if name == "meta"
                                       else store_lock.held(name))
            except store_lock.StoreBusy as exc:
                r["errors"].append({"file": "restore", "reason": str(exc)})
                r["refused"] = True
                return r
            try:
                held.enter_context(marker.writing(("BUNDLE", "CURRENT")))
            except OSError as exc:
                r["errors"].append({"file": "sync-pending", "reason": f"The sync marker could not be written: {exc}"})
                r["refused"] = True
                return r
            if recovery is None and not snapshot(r):
                return r
            written = 0
            for st in registry.STORES:
                if not st.apply(plan, r):
                    if written == 0:
                        r["refused"] = True
                    return r
                written += 1
            foreign.apply_plan(plan, r)
    return r


def import_from(path: str | Path) -> dict[str, Any]:
    return apply(preflight(path))


def receipt_line(r: dict[str, Any]) -> str:
    """The one-line receipt the Configuration row shows after an import. Once any write
    failed the line names the failure and no count, since the counts were the plan."""
    if r.get("refused") or (r.get("errors") and not r.get("counts")):
        return ""
    errors = r.get("errors") or []
    if (len(errors) == 1 and errors[0].get("file") == RECOVERY
            and str(errors[0].get("reason", "")).startswith(_NOT_REMOVED + ":")):
        return f"Restored · {RECOVERY} could not be removed"
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


def _print_receipt(r: dict[str, Any], dry_run: bool = False) -> int:
    """Print a receipt as plain lines, a dry run's as what a restore would do; returns the exit code (1
    when anything failed)."""
    if r.get("kind") == "export":
        print(f"Exported {r['path']}")
    elif dry_run and not r.get("refused"):
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
        return _print_receipt(r, dry_run=True)
    with contextlib.ExitStack() as held:
        try:
            held.enter_context(restoring())
        except OSError as exc:
            print(f"That backup could not be restored: {exc}", file=sys.stderr)
            return 1
        r = import_from(args.file)
        settle(r, bool(r.get("errors")))
    return _print_receipt(r)


if __name__ == "__main__":
    raise SystemExit(main())
