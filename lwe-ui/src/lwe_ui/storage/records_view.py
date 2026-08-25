"""Read-side helpers for the item record store (W4): the one-time migration off the legacy
tombstones map, the field->neutral-sentence composer for the manager display, the tombstoned-item
queries the manager viewer reads, and a purge that also un-gates re-import.

The anti-pessimism law governs the composer: neutral facts, never editorial - it states what
happened and who did it, and never predicts or scolds.
"""
from __future__ import annotations

from datetime import datetime

from . import paths, records, tags, tombstones

def migrate_legacy() -> int:
    """Convert the legacy tombstones.json map ({wid:{title,reason}}) into per-wid records - one
    synthesized 'deleted' event each - then retire the legacy file so this never runs twice.
    Idempotent: skips a wid that already has a record; renames the old file to .migrated on
    success (that IS the backup). Returns the number of entries migrated."""
    src = tombstones._file()
    if not src.exists():
        return 0
    data = tombstones.load()
    n = 0
    for wid, meta in data.items():
        if not paths.is_safe_wid(str(wid)) or records.has_record(str(wid)):
            continue
        reason = str((meta or {}).get("reason", ""))
        records.append(str(wid), records.make_event(
            "deleted", where="library", initiator="human",
            lineage=([reason] if reason else []), comment=None))
        n += 1
    try:
        src.rename(src.with_name(src.name + ".migrated"))
    except OSError:
        pass
    return n


_ACTION = {
    "approved": "added to the library",
    "deleted": "trashed",
    "bypassed": "re-imported past its tombstone",
    "benched_no_decision": "benched, left pending",
}
_LINEAGE = {
    "crashed": "it crashed at the bench",
    "crashed_post_fix": "it crashed even after a fix",
    "no_first_frame": "it never rendered",
    "fixes_applied": "a fix was applied",
    "recommended_trash": "the wizard recommended trashing it",
    # legacy reasons carried over by the migration
    "rejected-untested": "removed without testing",
    "ran-but-rejected": "ran, but you rejected it",
}


def _fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%b %-d")
    except (ValueError, TypeError):
        return ""


def compose(event: dict) -> str:
    """One neutral sentence from an event: date, who did it, the machine breadcrumbs, the note.
    Facts only."""
    if not event:
        return ""
    when = _fmt_date(str(event.get("when", "")))
    verb = _ACTION.get(event.get("action", ""), str(event.get("action", "")))
    if event.get("initiator") == "wizard_recommended":
        who = "you trashed it on the wizard's recommendation"
    elif event.get("action") == "approved" and event.get("machine"):
        who = "you approved it after a clean bench"
    elif event.get("action") == "approved":
        who = "you imported it untested"
    else:
        who = "you " + verb
    parts = [p for p in (when, who) if p]
    lead = ": ".join(parts) if when else who
    seen = set()
    crumbs = []
    for k in (event.get("lineage") or []):
        phrase = _LINEAGE.get(str(k))
        if phrase and phrase not in seen:
            seen.add(phrase)
            crumbs.append(phrase)
    tail = "; ".join(crumbs)
    out = lead + ("; " + tail if tail else "")
    note = event.get("comment")
    if note:
        out += "; note: " + str(note).replace("\n", " ")
    return out


def tombstoned_wids() -> list[str]:
    """wids whose HEAD event is a deletion - the manager's current-tombstones list."""
    return [w for w in records.list_wids() if (records.head(w) or {}).get("action") == "deleted"]


def summary(wid: str) -> dict:
    """Collapsed-row data: the composed latest-event line and the event count."""
    evs = records.read(wid)
    return {"wid": wid, "line": compose(evs[-1]) if evs else "", "count": len(evs)}


def timeline(wid: str) -> list[dict]:
    """Newest-first composed lines for the expand."""
    return [{"line": compose(e), "action": str(e.get("action", ""))}
            for e in reversed(records.read(wid))]


def purge_and_ungate(wid: str) -> bool:
    """Manager purge: delete the record file AND drop the tags row, so the item is no longer
    suppressed and can re-import. Wipes BOTH the suppression and the audit trail (the confirm
    must say so). Returns True if a record file was removed."""
    removed = records.purge(wid)
    try:
        tags.remove(wid)
    except Exception:
        pass
    return removed
