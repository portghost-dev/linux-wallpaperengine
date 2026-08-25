"""Tombstone reasons (tombstones.json): WHY a trashed item was trashed.

The tags store stays the single source of tombstone TRUTH (state == bad drives dedup
and pool exclusion); this side store only annotates those rows with the verdict context
captured at trash time, so the future offender catalog can be built from real crash
verdicts instead of "meh, ugly" rejections. Kept out of tags.csv deliberately: the
column shape of that file is load-bearing (retired consumers parsed it).

Reasons in use:
  rejected-untested   trashed with no preview verdict on record
  crashed             the preview run died (the offender-catalog population)
  ran-but-rejected    previewed clean or heavy, rejected anyway
"""
from __future__ import annotations

import json

from . import atomic, paths

REASONS = ("rejected-untested", "crashed", "ran-but-rejected")


def _file():
    return paths.state_dir() / "tombstones.json"


def load() -> dict[str, dict]:
    p = _file()
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(d: dict[str, dict]) -> None:
    atomic.atomic_write_text(_file(), json.dumps(d, indent=1))


def record(wid: str, title: str, reason: str) -> None:
    if reason not in REASONS:
        reason = "rejected-untested"
    d = load()
    d[str(wid)] = {"title": str(title), "reason": reason}
    save(d)


def forget(wid: str) -> None:
    d = load()
    if str(wid) in d:
        del d[str(wid)]
        save(d)


def reason_of(wid: str) -> str:
    return str(load().get(str(wid), {}).get("reason", ""))
