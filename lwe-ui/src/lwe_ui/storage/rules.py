"""The app rule files: one entry per line, no schema.

`app-condition.txt` names the processes that must be running for the engine to render,
`pause-blacklist.txt` and `pause-whitelist.txt` the ones that pause it. The panel's rule
editors open these files by name; this module holds the set itself, so the backup carries a
store rather than a list of names, and a fourth rule file that skips it fails the ownership
test.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

from . import atomic, paths
from .store import Store

FILES = ("app-condition.txt", "pause-blacklist.txt", "pause-whitelist.txt")


def file_for(name: str) -> Path:
    return paths.config_dir() / name


def save(name: str, text: str) -> None:
    atomic.atomic_write_text(file_for(name), text)


def _entries(text: str) -> str:
    """A rule file as it is stored: one printable, non-empty entry per line."""
    lines = [ln.rstrip("\r") for ln in text.splitlines()]
    return "".join(ln + "\n" for ln in lines if ln.strip() and ln.isprintable())


def _backup_export(z: zipfile.ZipFile, r: dict[str, Any]) -> None:
    n = 0
    for name in FILES:
        p = file_for(name)
        if not p.is_file():
            continue
        try:
            z.writestr(f"rules/{name}", p.read_text(encoding="utf-8"))
        except OSError as exc:
            r["errors"].append({"file": name, "reason": str(exc)})
            continue
        n += 1
    r["counts"]["rules"] = n


def _backup_preflight(z: zipfile.ZipFile, r: dict[str, Any], plan: dict[str, Any],
                      cfg_after: dict[str, Any]) -> bool:
    out: dict[str, str] = {}
    for name in FILES:
        member = f"rules/{name}"
        if member in z.namelist():
            out[name] = _entries(z.read(member).decode("utf-8", "replace"))
    plan["rules"] = out
    r["counts"]["rules"] = len(out)
    return True


def _backup_apply(plan: dict[str, Any], r: dict[str, Any]) -> bool:
    for name, text in (plan.get("rules") or {}).items():
        try:
            save(name, text)
        except Exception as exc:
            r["errors"].append({"file": name, "reason": str(exc)})
    return True


BACKUP = Store("rules", FILES, _backup_export, _backup_preflight, _backup_apply)
