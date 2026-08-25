"""Per-wid append-only event log - the item RECORD store.

Supersedes the flat tombstones.json map: each wallpaper id gets its own JSONL file at
state/records/<wid>.jsonl, one JSON event object per line, append-only. The FULL lifecycle is
logged (approvals too) as an accountability audit trail - the record must be able to answer
"this failed at bench, why is it in the library?" with "you chose to."

An event:
  { when, action, initiator, where, lineage[], machine|null, comment|null }
  action     approved | deleted | bypassed | benched_no_decision
  initiator  human | wizard_recommended
            - approvals are ALWAYS human (the wizard never recommends approval, only reports "ran")
            - only a deletion may be wizard_recommended (and a human still confirmed it)
            - whether a bench happened at all is ORTHOGONAL: machine != null
  where      library | workshop  (the SURFACE the action was taken from, not where the item lands)
  lineage    ordered machine breadcrumbs folded into the action, e.g. ["crashed","fixes_applied"]
  machine    {verdict: ran|crashed, contentHash, testsFailed[], repairAttempts[]} or null
             (present ONLY on wizard-path events)
  comment    the human's free text (JSON escapes any newline, so the physical line stays intact)

SUPPRESSION (blocks silent auto-import) is DERIVED, not stored: an item is suppressed iff its HEAD
event action == "deleted" AND it is still present on disk (the caller supplies presence, so this
predicate stays filesystem-decoupled). benched_no_decision does NOT suppress - that item is still
pending and already visible in Workshop. Migration from the legacy tombstones store lands separately.

Single writer (the UI): each append is one O_APPEND write of a single JSON line. Reads tolerate a
malformed/partial trailing line (e.g. a torn final append) - skip it, never fatal.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import paths

ACTIONS = ("approved", "deleted", "bypassed", "benched_no_decision")
INITIATORS = ("human", "wizard_recommended")
WHERES = ("library", "workshop")
VERDICTS = ("ran", "crashed")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _norm_machine(m) -> dict | None:
    if not m:
        return None
    verdict = m.get("verdict")
    if verdict not in VERDICTS:
        raise ValueError(f"bad machine.verdict {verdict!r}")
    return {
        "verdict": verdict,
        "contentHash": str(m.get("contentHash", "")),
        "testsFailed": [str(x) for x in (m.get("testsFailed") or [])],
        "repairAttempts": [str(x) for x in (m.get("repairAttempts") or [])],
    }


def make_event(action: str, *, where: str, initiator: str = "human", lineage=None,
               machine=None, comment=None, when=None) -> dict:
    """Build a validated event. Raises ValueError on an illegal field or self-contradictory
    combination, so a caller can never write a malformed event. This is the strict door;
    append() is the dumb pipe.

    Enforced invariants:
     - action/initiator/where are enums
     - approved => initiator must be human (the wizard never recommends approval)
     - wizard_recommended => the action must be a deletion
    """
    if action not in ACTIONS:
        raise ValueError(f"bad action {action!r}")
    if initiator not in INITIATORS:
        raise ValueError(f"bad initiator {initiator!r}")
    if where not in WHERES:
        raise ValueError(f"bad where {where!r}")
    if action == "approved" and initiator != "human":
        raise ValueError("approved events must be initiator=human")
    if initiator == "wizard_recommended" and action != "deleted":
        raise ValueError("wizard_recommended only attaches to a deletion")
    return {
        "when": str(when) if when else _now_iso(),
        "action": action,
        "initiator": initiator,
        "where": where,
        "lineage": [str(x) for x in (lineage or [])],
        "machine": _norm_machine(machine),
        "comment": None if comment is None else str(comment),
    }


def append(wid: str, event: dict) -> bool:
    """Atomically append one event line to <wid>.jsonl. Returns False (no-op) for an unsafe wid
    or a non-dict event, so a bad call never corrupts a file or crashes the app."""
    if not paths.is_safe_wid(wid) or not isinstance(event, dict):
        return False
    p = paths.record_file(wid)
    p.parent.mkdir(parents=True, exist_ok=True)
    # ensure_ascii=True (A1): escapes ALL non-ASCII including U+2028/U+2029, so a comment can never
    # carry a raw line separator that split()/splitlines() would tear the event on; also keeps the
    # record file pure-ASCII. open("a") is O_APPEND (atomic offset, single writer) and writes fully
    # (A2: no short-write truncation, unlike a bare os.write).
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    return True


def read(wid: str) -> list[dict]:
    """All events for wid, in append order. A malformed/partial/blank line is skipped, never
    fatal (a torn final append must not brick the whole record)."""
    if not paths.is_safe_wid(wid):
        return []
    p = paths.record_file(wid)
    if not p.exists():
        return []
    out: list[dict] = []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    for raw in text.split("\n"):   # A1: split on real newlines only, not unicode line separators
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def head(wid: str) -> dict | None:
    """The most-recent (last-appended) event, or None."""
    evs = read(wid)
    return evs[-1] if evs else None


def has_record(wid: str) -> bool:
    return paths.is_safe_wid(wid) and paths.record_file(wid).exists()


def list_wids() -> list[str]:
    """Every wid that has a record file, sorted."""
    d = paths.records_dir()
    if not d.is_dir():
        return []
    out = []
    for f in d.iterdir():
        if f.is_file() and f.name.endswith(".jsonl"):
            wid = f.name[:-len(".jsonl")]
            if paths.is_safe_wid(wid):
                out.append(wid)
    return sorted(out)


def is_suppressed(wid: str, present_on_disk: bool) -> bool:
    """DERIVED suppression: the item's HEAD event is a deletion AND it is still on disk.
    Presence is supplied by the caller so the predicate stays filesystem-decoupled and testable.
    benched_no_decision / bypassed / approved never suppress."""
    if not present_on_disk:
        return False
    h = head(wid)
    return bool(h) and h.get("action") == "deleted"


def purge(wid: str) -> bool:
    """Escape hatch: delete the record file as if it never existed. Wipes BOTH suppression AND
    the audit trail - the caller's confirm must state so. Returns True if a file was removed."""
    if not paths.is_safe_wid(wid):
        return False
    try:
        paths.record_file(wid).unlink()
        return True
    except (FileNotFoundError, OSError):
        return False


def content_hash(item_dir: str | os.PathLike) -> str:
    """CHEAP content identity for a wallpaper dir: sha1 of project.json bytes + a sorted manifest
    of (relpath, size, mtime) for every other file. Stamped in v1 for future recall (v-next); a
    Steam update changes the manifest so a stale machine verdict is never recalled as fact. Reads
    only project.json fully - everything else is stat-only, so it stays cheap on a 4K scene."""
    d = Path(item_dir)
    h = hashlib.sha1()
    try:
        h.update((d / "project.json").read_bytes())
    except OSError:
        h.update(b"")
    # TOP-LEVEL entries only (os.scandir), NOT a recursive rglob: a 4K scene has hundreds of nested
    # asset files and walking them all froze the GUI for seconds. The top-level manifest (the .pkg,
    # the scene file, project.json) still changes on a Steam update, which is all the recall needs.
    manifest = []
    if d.is_dir():
        try:
            for e in os.scandir(d):
                if e.name == "project.json":
                    continue
                try:
                    if e.is_file(follow_symlinks=False):
                        st = e.stat(follow_symlinks=False)
                        manifest.append((e.name, st.st_size, int(st.st_mtime)))
                except OSError:
                    continue
        except OSError:
            pass
    for name, size, mtime in sorted(manifest):
        h.update(f"\x00{name}\x00{size}\x00{mtime}".encode("utf-8"))
    return h.hexdigest()
