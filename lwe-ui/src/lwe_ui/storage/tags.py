"""Tags store (tags.csv): one row per wallpaper that the user has classified.

Header is `id,title,state` where state in {good,bad}. Titles may contain commas / UTF-8, so
the stdlib `csv` module handles quoting on both read and write. A legacy 3rd column named
`tag` is tolerated on read (the migrate step renames it; we also accept it directly).
"""
from __future__ import annotations

import csv
import io

from . import atomic, paths

HEADER = ("id", "title", "state")
_VALID_STATES = ("good", "bad", "review")   # review = imported, awaiting the card verdict


def load() -> list[dict]:
    """Return rows as {"id","title","state"}. Tolerates a legacy `tag` state column.

    A headerless or unrecognized first row is treated as data (best-effort).
    """
    p = paths.tags_file()
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict] = []
    reader = csv.reader(io.StringIO(text))
    records = list(reader)
    if not records:
        return []
    first = records[0]
    norm_first = [c.strip().lower() for c in first]
    has_header = norm_first[:2] == ["id", "title"] and len(norm_first) >= 3 and norm_first[2] in ("state", "tag")
    data = records[1:] if has_header else records
    for rec in data:
        if not rec:
            continue
        rid = rec[0] if len(rec) > 0 else ""
        title = rec[1] if len(rec) > 1 else ""
        state = rec[2] if len(rec) > 2 else ""
        rows.append({"id": rid, "title": title, "state": state})
    return rows


def save(rows: list[dict]) -> None:
    """Write tags.csv with header id,title,state. csv quotes commas/UTF-8 in titles."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(HEADER)
    for r in rows:
        writer.writerow([r.get("id", ""), r.get("title", ""), r.get("state", "")])
    atomic.atomic_write_text(paths.tags_file(), buf.getvalue())


def good_ids() -> set[str]:
    return {r["id"] for r in load() if r.get("state") == "good" and r.get("id")}


def known_ids() -> set[str]:
    return {r["id"] for r in load() if r.get("id")}


def review_ids() -> set[str]:
    """Imported-but-unapproved items (the importer's landing state under review-required).
    Never rotated (the rotation pool is state==good only); rendered in the Review scope."""
    return {r["id"] for r in load() if r.get("state") == "review" and r.get("id")}


# set_state is load-modify-save; the import worker and the GUI thread both call it
# (batch import vs a user's approve/trash mid-pass). Individual writes are atomic but
# unserialized writes could silently revert each other - one module lock closes it.
_WRITE_LOCK = __import__("threading").Lock()


def set_state(id: str, title: str, state: str) -> None:
    """Upsert a row (match by id) and save. Updates the title on every call.
    States in use: good (rotates), bad (tombstone), review (imported, awaiting the
    card verdict; never rotates). Thread-safe."""
    with _WRITE_LOCK:
        _set_state_locked(id, title, state)


def remove(id: str) -> None:
    """Delete a row entirely (tombstone restore: an unknown id reimports / re-pends
    naturally, which IS the restore semantics). Thread-safe. No-op when absent."""
    with _WRITE_LOCK:
        rows = load()
        kept = [r for r in rows if r.get("id") != id]
        if len(kept) != len(rows):
            save(kept)


def save_rows(rows: list[dict]) -> None:
    """Replace the whole store under the module lock."""
    with _WRITE_LOCK:
        save(rows)


def remove_state(state: str) -> list[str]:
    """Drop every row in `state`, load-filter-save INSIDE the lock (a concurrent import
    worker's set_state must never be lost - tags.csv is watcher-load-bearing). Returns
    the removed ids (the tombstone clear-all reports what came back to life)."""
    with _WRITE_LOCK:
        rows = load()
        removed = [r["id"] for r in rows if r.get("state") == state and r.get("id")]
        if removed:
            save([r for r in rows if r.get("state") != state])
        return removed


def _set_state_locked(id: str, title: str, state: str) -> None:
    rows = load()
    found = False
    for r in rows:
        if r.get("id") == id:
            r["title"] = title
            r["state"] = state
            found = True
            break
    if not found:
        rows.append({"id": id, "title": title, "state": state})
    save(rows)
