"""The shared per-wallpaper editing session: one snapshot, one marked set, per wid.

Popup and editor write the SAME store (wp/<id>.conf via
the presence-preserving wp.update_set), so the revert target and the marked set cannot live
inside one surface's bridge - a mark set on one surface has to be visible on the other, and
`Revert changes` has to mean the same thing from either door. This module owns both.

Two facts per wid, and nothing else:
  * the SNAPSHOT - the wallpaper-scoped values as they stood when this editing session began
    (every WP_SCHEMA key that was present, every PROP_ key, the SKIP set). Reverting a key
    restores that value, or DELETES the key when it was absent at seat time.
  * the MARKED SET - the keys changed during this session. The marked set IS the
    revert set. Marks clear on assent (editor close / wallpaper switch, popup rotation
    advance); values persist either way.

Global settings are never in either: a global commits outright and sits outside the revert
set.

SEAT FAILURE IS A HARD STATE. The pre-existing shape swallowed a failed
snapshot read and left a plausible-looking empty dict behind; `Revert changes` then built
{key: None} for every marked key, and None DELETES the key in wp.update_set_path - so a
user who reverted after one unreadable-conf moment lost their overrides instead of
restoring them. Three defenses, all live here:
  1. a failed seat sets `valid = False` explicitly and is reported to the caller, which
     raises the failure grammar at SEAT time rather than at revert time;
  2. `revert_changes` refuses outright while a wid's snapshot is invalid, so the caller can
     disable the verb - a revert that cannot restore must not run;
  3. `None` stops meaning two things. The snapshot knows its own COVERED DOMAIN (every
     schema key plus every PROP_ key), so "recorded as absent -> delete" and "no record at
     all -> skip" are different answers instead of the same dict.get() miss.
"""
from __future__ import annotations

from typing import Any

from . import constants as C
from .storage import wp

# Identity, not customization: BG names the directory a preset renders from, so a revert or a
# defaults strip that dropped it would unmake the wallpaper rather than reset it.
IDENTITY_KEYS = ("BG", "TYPE")


def is_wallpaper_key(key: str) -> bool:
    """Is `key` inside the snapshot's covered domain (a schema key or a scene property)?"""
    key = str(key or "")
    return key in C.WP_SCHEMA or key.startswith(C.WP_PROP_PREFIX)


class _Seat:
    """One wid's snapshot + marks."""

    __slots__ = ("values", "valid", "marks")

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.valid: bool = False
        self.marks: set[str] = set()


class WallpaperSession:
    """Per-wid snapshots and marked sets, shared by every editing surface."""

    def __init__(self) -> None:
        self._seats: dict[str, _Seat] = {}

    def seat(self, wid: str, force: bool = False) -> bool:
        """Capture the session-start values for `wid`. Returns False when the read failed.

        Idempotent by design: a wid already seated this play session keeps the
        snapshot it has, so opening the editor on a wallpaper the popup already tuned does
        NOT move the revert target forward onto the popup's edits. `force` re-seats anyway.
        """
        wid = str(wid or "")
        if not wid:
            return False
        seat = self._seats.get(wid)
        if seat is not None and seat.valid and not force:
            return True
        seat = _Seat()
        self._seats[wid] = seat
        try:
            present = wp.load_set(wid)
        except Exception:
            # explicit hard state, never a plausible-looking empty dict (F16 part 1)
            seat.valid = False
            return False
        values: dict[str, Any] = {}
        for key in C.WP_SCHEMA:
            if key in present:
                values[key] = present[key]
        for name, val in (present.get("props") or {}).items():
            values[f"{C.WP_PROP_PREFIX}{name}"] = val
        seat.values = values
        seat.valid = True
        return True

    def is_valid(self, wid: str) -> bool:
        seat = self._seats.get(str(wid or ""))
        return bool(seat is not None and seat.valid)

    def snapshot(self, wid: str) -> dict[str, Any]:
        """The seated values (present keys only). Empty for an unseated or failed wid."""
        seat = self._seats.get(str(wid or ""))
        return dict(seat.values) if seat is not None and seat.valid else {}

    def mark(self, wid: str, keys) -> None:
        """Add wallpaper-scoped keys to the marked set. Global keys are silently ignored."""
        wid = str(wid or "")
        if not wid:
            return
        seat = self._seats.get(wid)
        if seat is None:
            seat = _Seat()
            self._seats[wid] = seat
        for key in keys:
            key = str(key or "")
            if key and is_wallpaper_key(key):
                seat.marks.add(key)

    def marks(self, wid: str) -> set[str]:
        seat = self._seats.get(str(wid or ""))
        return set(seat.marks) if seat is not None else set()

    def is_marked(self, wid: str, key: str) -> bool:
        seat = self._seats.get(str(wid or ""))
        return bool(seat is not None and str(key or "") in seat.marks)

    def has_marks(self, wid: str) -> bool:
        seat = self._seats.get(str(wid or ""))
        return bool(seat is not None and seat.marks)

    def clear_marks(self, wid: str) -> None:
        """Assent: values persist, marks drop. The snapshot is left seated."""
        seat = self._seats.get(str(wid or ""))
        if seat is not None:
            seat.marks.clear()

    def can_revert(self, wid: str) -> bool:
        """True when a revert would actually restore: a valid snapshot AND something marked."""
        wid = str(wid or "")
        return self.is_valid(wid) and self.has_marks(wid)

    def revert_changes(self, wid: str) -> dict[str, Any] | None:
        """The write set that restores every marked key. None when the revert must not run.

        A key recorded as PRESENT restores its seated value; a key inside the covered domain
        but absent at seat time restores to None, which deletes it - that is the correct
        undo of "the user added this override". A key the snapshot has no record of at all
        (outside the domain) is skipped rather than deleted.
        """
        wid = str(wid or "")
        if not self.can_revert(wid):
            return None
        seat = self._seats[wid]
        changes: dict[str, Any] = {}
        for key in seat.marks:
            if key in seat.values:
                changes[key] = seat.values[key]
            elif is_wallpaper_key(key):
                changes[key] = None          # recorded as absent -> deleting it IS the restore
            # else: no record and outside the domain - touch nothing
        return changes

    def defaults_changes(self, wid: str) -> dict[str, Any]:
        """The write set for `Load defaults`: strip every per-wallpaper customization.

        All schema override keys plus every PROP_ key the conf currently carries, back to
        absent. Identity keys survive; globals are never touched.
        """
        wid = str(wid or "")
        changes: dict[str, Any] = {
            key: None for key in C.WP_SCHEMA if key not in IDENTITY_KEYS
        }
        try:
            for name in (wp.load_set(wid).get("props") or {}):
                changes[f"{C.WP_PROP_PREFIX}{name}"] = None
        except Exception:
            # a conf we cannot read has no props to strip; the schema sweep above still stands
            pass
        return changes

    def forget(self, wid: str) -> None:
        """Drop a wid's seat entirely (a wallpaper that left the library)."""
        self._seats.pop(str(wid or ""), None)


# The one instance every surface consumes. A module-level singleton rather than a bridge
# member, because the whole point is that neither bridge keeps a private copy (C-1).
SESSION = WallpaperSession()
