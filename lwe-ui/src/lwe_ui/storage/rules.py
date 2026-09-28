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
from typing import Any, Callable

from . import atomic, lock, paths
from .store import Store

FILES = ("app-condition.txt", "pause-blacklist.txt", "pause-whitelist.txt")


def file_for(name: str) -> Path:
    return paths.config_dir() / name


def save(name: str, text: str) -> None:
    with lock.held("rules"):
        atomic.atomic_write_text(file_for(name), text)


def modify(name: str, fn: Callable[[str], str]) -> str:
    """Under the rules lock: fn receives the file's current text ("" when there is no file)
    and returns the text it should hold; the file is written only when that text differs.
    Returns the text. A file that exists but cannot be read raises."""
    with lock.held("rules"):
        try:
            text = file_for(name).read_bytes().decode("utf-8")
        except FileNotFoundError:
            text = ""
        new = fn(text)
        if new != text:
            save(name, new)
        return new


def add_entry(text: str, entry: str) -> str:
    """The list with `entry` appended as one line, unless a line already reads `entry` once
    stripped; an unterminated last line is terminated first. Every other line stays."""
    lines = text.splitlines(keepends=True)
    if any(line.strip() == entry for line in lines):
        return text
    if lines and lines[-1].splitlines()[0] == lines[-1]:
        text += "\n"
    return text + entry + "\n"


def remove_entry(text: str, entry: str, cap: int | None = None) -> str:
    """The list without every line that reads `entry` once stripped, or once stripped and cut to
    `cap` characters, the length a reader shows. Every other line stays."""
    def matches(line: str) -> bool:
        s = line.strip()
        return s == entry or (cap is not None and s[:cap] == entry)
    return "".join(line for line in text.splitlines(keepends=True) if not matches(line))


def _entries(text: str, name: str = "", r: dict[str, Any] | None = None) -> str:
    """A rule file as it is stored: one printable, non-empty entry per line. A line the file
    cannot hold is named in the receipt rather than vanishing."""
    out = []
    for raw in text.splitlines():
        ln = raw.rstrip("\r")
        if not ln.strip():
            continue
        if ln.isprintable():
            out.append(ln + "\n")
        elif r is not None:
            r["dropped"].append({"kind": "rule-line", "id": f"{name}:{ln!r}",
                                 "reason": "a rule file holds printable lines only"})
    return "".join(out)


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
            out[name] = _entries(z.read(member).decode("utf-8", "replace"), name, r)
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
