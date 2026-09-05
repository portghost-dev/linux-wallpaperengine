"""The library grid's order: playlist members first in stored order, filler cells that pad the
member block to a column boundary, then the pool in library order (design spec 1.1, R1, R44).

Sits over the search/scope filter model, so what the filter hides never shows here either. The
row list is recomputed from the filter model and the stored member order; a single card moving
between the two blocks (a checkbox, a drop) is applied as one row move so the grid animates it,
and the filler block is re-padded around it. Anything larger is a reset.

The drag API holds a provisional member order while a card is lifted (R37: dragging never removes;
a pool card over a member slot is a provisional insert, a member over the pool goes home). The
owner commits the result to storage once on drop; this model never writes.
"""
from __future__ import annotations

import math
from typing import Any, Callable

from PySide6.QtCore import (
    QAbstractItemModel, QAbstractListModel, QByteArray, QModelIndex, QObject, Property, Qt,
    Signal, Slot,
)

_ROLE_ID = Qt.ItemDataRole.UserRole + 1  # LibraryModel's id role, the same number by contract
ROLE_FILLER = Qt.ItemDataRole.UserRole + 50
ROLE_MEMBER = Qt.ItemDataRole.UserRole + 51

MEMBER, FILLER, POOL = "member", "filler", "pool"


class LibraryOrderModel(QAbstractListModel):
    columnsChanged = Signal()
    memberCountChanged = Signal()
    draggingChanged = Signal()

    def __init__(self, source: QAbstractItemModel, member_order: Callable[[], list[str]],
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._source = source
        self._member_order = member_order
        self._columns = 1
        self._rows: list[tuple[str, str]] = []  # (kind, wid); fillers carry ""
        self._src_row: dict[str, int] = {}
        self._drag: dict[str, Any] | None = None
        source.modelReset.connect(self._rebuild)
        source.rowsInserted.connect(self._rebuild)
        source.rowsRemoved.connect(self._rebuild)
        source.layoutChanged.connect(self._rebuild)
        source.dataChanged.connect(self._source_changed)
        self._rebuild()

    # --- Qt model plumbing ---------------------------------------------------------------
    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802 (Qt override)
        names = dict(self._source.roleNames())
        names[ROLE_FILLER] = QByteArray(b"filler")
        names[ROLE_MEMBER] = QByteArray(b"member")
        return names

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        kind, wid = self._rows[index.row()]
        if role == ROLE_FILLER:
            return kind == FILLER
        if role == ROLE_MEMBER:
            return kind == MEMBER
        if kind == FILLER:
            return "" if role == _ROLE_ID else None
        src = self._src_row.get(wid, -1)
        if src < 0:
            return None
        return self._source.data(self._source.index(src, 0), role)

    # --- properties the grid binds -----------------------------------------------------------
    def _get_columns(self) -> int:
        return self._columns

    @Slot(int)
    def setColumns(self, columns: int) -> None:
        n = max(1, int(columns))
        if n != self._columns:
            self._columns = n
            self.columnsChanged.emit()
            self._apply(self._compute())

    columns = Property(int, _get_columns, notify=columnsChanged)

    def _get_member_count(self) -> int:
        return sum(1 for kind, _ in self._rows if kind == MEMBER)

    def _get_filler_count(self) -> int:
        return sum(1 for kind, _ in self._rows if kind == FILLER)

    def _get_hairline_index(self) -> int:
        """Row index of the first pool cell after a non-empty member block, else -1."""
        members = self._get_member_count()
        pool = sum(1 for kind, _ in self._rows if kind == POOL)
        return members + self._get_filler_count() if members and pool else -1

    memberCount = Property(int, _get_member_count, notify=memberCountChanged)
    fillerCount = Property(int, _get_filler_count, notify=memberCountChanged)
    hairlineIndex = Property(int, _get_hairline_index, notify=memberCountChanged)

    def _get_dragging(self) -> bool:
        return self._drag is not None

    dragging = Property(bool, _get_dragging, notify=draggingChanged)

    @Slot(int, result=str)
    def idAt(self, row: int) -> str:
        return self._rows[row][1] if 0 <= row < len(self._rows) else ""

    @Slot(str, result=int)
    def rowOf(self, wid: str) -> int:
        for i, (kind, w) in enumerate(self._rows):
            if kind != FILLER and w == wid:
                return i
        return -1

    def order(self) -> list[tuple[str, str]]:
        return list(self._rows)

    # --- order computation --------------------------------------------------------------
    def _filtered_ids(self) -> list[str]:
        ids = []
        for r in range(self._source.rowCount()):
            wid = self._source.data(self._source.index(r, 0), _ROLE_ID)
            if wid:
                ids.append(str(wid))
        return ids

    def _members_now(self) -> list[str]:
        if self._drag is not None:
            return list(self._drag["members"])
        try:
            return list(self._member_order())
        except Exception:
            return []

    def _compute(self) -> list[tuple[str, str]]:
        shown = self._filtered_ids()
        shown_set = set(shown)
        members = [w for w in self._members_now() if w in shown_set]
        member_set = set(members)
        pool = [w for w in shown if w not in member_set]
        # padding and the hairline exist only between two non-empty blocks (R40, R44)
        fillers = (-len(members)) % self._columns if members and pool else 0
        return ([(MEMBER, w) for w in members] + [(FILLER, "")] * fillers
                + [(POOL, w) for w in pool])

    def _rebuild(self, *_args: Any) -> None:
        if self._drag is not None:
            # the source changed under a lifted card: the drag is over, nothing is stored
            self._drag = None
            self.draggingChanged.emit()
        self._src_row = {}
        for r in range(self._source.rowCount()):
            wid = self._source.data(self._source.index(r, 0), _ROLE_ID)
            if wid:
                self._src_row[str(wid)] = r
        self.beginResetModel()
        self._rows = self._compute()
        self.endResetModel()
        self.memberCountChanged.emit()

    @Slot()
    def resync(self) -> None:
        """The stored member order changed outside a source signal (a playlist switch or edit)."""
        self._apply(self._compute())

    def _source_changed(self, top: QModelIndex, bottom: QModelIndex, roles: list) -> None:
        names = self._source.roleNames()
        touched = {bytes(names.get(r, QByteArray())).decode() for r in roles}
        if not roles or "inPlaylist" in touched:
            self._apply(self._compute())
        for src in range(top.row(), bottom.row() + 1):
            wid = self._source.data(self._source.index(src, 0), _ROLE_ID)
            row = self.rowOf(str(wid or ""))
            if row >= 0:
                idx = self.index(row, 0)
                self.dataChanged.emit(idx, idx, roles)

    def _apply(self, new_rows: list[tuple[str, str]]) -> None:
        """Move one card and re-pad the fillers when that is all that changed; else reset."""
        if new_rows == self._rows:
            return
        old_ids = [w for k, w in self._rows if k != FILLER]
        new_ids = [w for k, w in new_rows if k != FILLER]
        move = self._moved_one(old_ids, new_ids)
        if move is None:
            self.beginResetModel()
            self._rows = new_rows
            self.endResetModel()
            self.memberCountChanged.emit()
            return
        # 1. drop the old filler block
        old_fill = [i for i, (k, _) in enumerate(self._rows) if k == FILLER]
        if old_fill:
            self.beginRemoveRows(QModelIndex(), old_fill[0], old_fill[-1])
            self._rows = [r for r in self._rows if r[0] != FILLER]
            self.endRemoveRows()
        # 2. move the one card (kinds may change: member <-> pool)
        if move != (0, 0):
            src, dst = move
            qt_dst = dst + 1 if dst > src else dst
            if self.beginMoveRows(QModelIndex(), src, src, QModelIndex(), qt_dst):
                item = self._rows.pop(src)
                self._rows.insert(dst, item)
                self.endMoveRows()
        kinds = {w: k for k, w in new_rows if k != FILLER}
        for i, (k, w) in enumerate(self._rows):
            if kinds.get(w, k) != k:
                self._rows[i] = (kinds[w], w)
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [ROLE_MEMBER])
        # 3. insert the new filler block after the member block
        fillers = sum(1 for k, _ in new_rows if k == FILLER)
        if fillers:
            at = sum(1 for k, _ in new_rows if k == MEMBER)
            self.beginInsertRows(QModelIndex(), at, at + fillers - 1)
            self._rows[at:at] = [(FILLER, "")] * fillers
            self.endInsertRows()
        if self._rows != new_rows:  # the diff did not land: a reset is always correct
            self.beginResetModel()
            self._rows = new_rows
            self.endResetModel()
        self.memberCountChanged.emit()

    @staticmethod
    def _moved_one(old: list[str], new: list[str]) -> tuple[int, int] | None:
        """(src, dst) when `new` is `old` with exactly one element moved; (0, 0) when equal."""
        if old == new:
            return (0, 0)
        if len(old) != len(new):
            return None
        lo = 0
        while lo < len(old) and old[lo] == new[lo]:
            lo += 1
        hi = len(old) - 1
        while hi > lo and old[hi] == new[hi]:
            hi -= 1
        # the differing window is one element rotated to its other end
        if old[lo + 1:hi + 1] == new[lo:hi] and old[lo] == new[hi]:
            return (lo, hi)
        if old[lo:hi] == new[lo + 1:hi + 1] and old[hi] == new[lo]:
            return (hi, lo)
        return None

    # --- drag (provisional order; the owner writes storage on drop) ---------------------------
    @Slot(str, result=bool)
    def beginDrag(self, wid: str) -> bool:
        row = self.rowOf(wid)
        if self._drag is not None or row < 0:
            return False
        members = self._members_now()
        self._drag = {"wid": wid, "origin": self._rows[row][0], "origin_row": row,
                      "start": list(members), "members": list(members)}
        self.draggingChanged.emit()
        return True

    def _merge_visible(self, visible: list[str]) -> list[str]:
        """The full provisional member list for a provisional VISIBLE order: members the filter
        hides keep their absolute places, the visible ones and a newly added id fill the other
        places in the provisional order."""
        d = self._drag
        start = list(d["start"])
        shown = set(self._filtered_ids())
        hidden = {i: w for i, w in enumerate(start) if w not in shown}
        n = len(hidden) + len(visible)
        out: list[str] = []
        pending = list(visible)
        for i in range(n):
            if i in hidden:
                out.append(hidden[i])
            elif pending:
                out.append(pending.pop(0))
        out.extend(pending)
        return out

    @Slot(int)
    def dragOver(self, row: int) -> None:
        """Pointer over grid row `row` (-1: outside the grid; -2: the hairline band between the
        blocks). Rows are the filtered view; the provisional order is kept in full."""
        if self._drag is None:
            return
        d = self._drag
        wid = d["wid"]
        shown = set(self._filtered_ids())
        visible = [w for w in d["members"] if w in shown and w != wid]
        slots = len(visible) + (1 if wid in d["members"] else 0)
        pool_shown = any(w in shown and w not in d["members"] for w in self._filtered_ids())
        block_end = -(-slots // self._columns) * self._columns
        if row == -1:
            target = list(d["start"])  # outside the grid: home
        elif d["origin"] == MEMBER:
            if 0 <= row < slots:
                visible.insert(row, wid)
                target = self._merge_visible(visible)
            elif row == -2 or row < block_end or not pool_shown:
                visible.append(wid)  # the band, a padding cell, or no pool below: the end
                target = self._merge_visible(visible)
            else:
                target = list(d["start"])  # over the pool: home
        else:  # a pool card
            if 0 <= row < slots:
                visible.insert(row, wid)  # provisional add at that slot
            elif 0 <= row < block_end:
                visible.append(wid)  # a padding cell in the member block: add at the end
            target = self._merge_visible(visible)  # over the pool or the band: home
        if target != d["members"]:
            d["members"] = target
            self._apply(self._compute())

    def dragPlan(self, commit: bool) -> dict:
        """What a drop would store: {action: none|reorder|insert, wid, members, index}."""
        if self._drag is None:
            return {"action": "none"}
        d = self._drag
        wid = d["wid"]
        result: dict[str, Any] = {"action": "none", "wid": wid, "members": list(d["members"])}
        if commit and d["members"] != d["start"]:
            if d["origin"] == MEMBER:
                result["action"] = "reorder"
            elif wid in d["members"]:
                result["action"] = "insert"
                result["index"] = d["members"].index(wid)
        return result

    @Slot(bool, result="QVariant")
    def endDrag(self, commit: bool) -> dict:
        """Finish the drag and rebuild from stored order; the owner stores dragPlan() first."""
        result = self.dragPlan(commit)
        if self._drag is None:
            return result
        self._drag = None
        self.draggingChanged.emit()
        self._apply(self._compute())
        return result
