"""Approving a wallpaper into the pool and lifting a trash block, shared by the window and the
commands. Plain Python; no Qt. Each write takes its own store lock, one after the other."""
from __future__ import annotations

from ..storage import records, tags


def approve(wid: str, title: str, event: dict | None = None) -> None:
    """Append event to the wallpaper's record when one is given, then set its tags state to good."""
    if event is not None:
        records.append(wid, event)
    tags.set_state(wid, title, "good")


def untrash(wid: str) -> None:
    """Append a bypassed event to the wallpaper's record, then drop its tags row, so the next scan
    imports it again."""
    records.append(wid, records.make_event("bypassed", where="workshop", initiator="human"))
    tags.remove(wid)
