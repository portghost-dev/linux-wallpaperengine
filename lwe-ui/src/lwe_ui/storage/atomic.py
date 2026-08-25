"""Atomic file + directory publish. Never write in place.

Serialize to <path>.tmp in the SAME directory, fsync, then os.replace(). Concurrent readers get
only after replace(), so it never observes a partial file. Directory copies use the same
temp-dir-then-rename pattern.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: str | os.PathLike, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: str | os.PathLike, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: str | os.PathLike, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def atomic_publish_dir(staged: str | os.PathLike, dest: str | os.PathLike) -> None:
    """Rename a fully-staged temp dir into place atomically (same filesystem).

    `staged` must already contain the final contents. If `dest` exists it is replaced.
    Caller is responsible for removing `staged` on failure before publish.
    """
    staged, dest = Path(staged), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        backup = dest.with_name(dest.name + ".old-" + os.urandom(4).hex())
        os.replace(dest, backup)
        try:
            os.replace(staged, dest)
        except BaseException:
            os.replace(backup, dest)
            raise
        else:
            shutil.rmtree(backup, ignore_errors=True)
    else:
        os.replace(staged, dest)
