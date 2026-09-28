"""Qt bridge objects for the GUI (Phase 1 Library slice).

`LibraryModel` is a QAbstractListModel whose rows are the union of `tags.csv` ids and the
`WALLPAPERS_DIR` subdirectories (GENERATED, never hardcoded). Per-row
title/thumb/type come from `discovery.project.read` when the wallpaper dir is readable.

`Backend` is the single QObject exposed to QML: it owns the model, the playlist/favorite
mutations (which write through the typed storage modules), the settings getters/setters used by
the Library top bar, and a `status()` reader that tolerates the engine being down.

This module imports PySide6 (allowed for app.py/models.py only). Everything below the Qt layer
is plain stdlib + the already-written storage/discovery modules.
"""
from __future__ import annotations

import logging
import os
import time
import shutil
import subprocess
import threading
from time import monotonic
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    QSortFilterProxyModel,
    QTimer,
    QUrl,
    Property,
    Qt,
    Signal,
    Slot,
)

from . import api_client
from . import constants as C
from .discovery import project
from .engine import daemon_unit, marker, push
from .engine.resolve import (_conf_true, _identity_dir, _wallpapers_dir, effective_speed, resolve_fit,
                             resolve_fullscreen_behavior, resolve_show_args, resolved_tuning,
                             split_playlist_parts)
from .library import actions
from .library.catalog import _is_present, _scan_dir_ids, library_ids
from .library_order import LibraryOrderModel
from .storage import lock, meta, paths, playlists, rules, settings, tags, wp

# Role ids for LibraryModel. Start past Qt.UserRole so they never collide with built-ins.
_ROLE_ID = Qt.ItemDataRole.UserRole + 1
_ROLE_TITLE = Qt.ItemDataRole.UserRole + 2
_ROLE_THUMB = Qt.ItemDataRole.UserRole + 3
_ROLE_IN_PLAYLIST = Qt.ItemDataRole.UserRole + 4
_ROLE_FAVORITE = Qt.ItemDataRole.UserRole + 5
_ROLE_TYPE = Qt.ItemDataRole.UserRole + 6
_ROLE_MISSING = Qt.ItemDataRole.UserRole + 7  # good-but-absent (broken: tagged good, no files)
_ROLE_PENDING_REVIEW = Qt.ItemDataRole.UserRole + 8  # on disk, never classified good/bad
_ROLE_REFUSED = Qt.ItemDataRole.UserRole + 9  # folder name outside the id allowlist: card only

# the one reason a folder that is on disk cannot be played or listed: its name. The id is a
# path component, a word in the shell-sourced playlist files and an engine verb argument.
REFUSED_REASON = "Rename this folder: letters, digits, dot, underscore and hyphen only"


def _sandboxed() -> bool:
    """True when LWE_SANDBOX=1 (tests/_sandbox.py). Host-wide probes - the /proc engine
    scan, pgrep, systemd, nvidia-smi - then report nothing found, so a test can never read
    the machine's live daemon. Absent in production, where every probe runs."""
    return os.environ.get("LWE_SANDBOX") == "1"


class _Row:
    """One library entry: cached id + lazily-resolved project facts + live tag/meta flags."""

    __slots__ = ("id", "title", "thumb", "type", "in_playlist", "favorite", "missing",
                 "pending_review", "refused")

    def __init__(self, wid: str) -> None:
        self.id = wid
        self.title = wid
        self.thumb = ""
        self.type = ""
        self.in_playlist = False
        self.favorite = False
        self.missing = False
        self.pending_review = False
        self.refused = False


class LibraryModel(QAbstractListModel):
    """List model backing the Library card grid. Rows are computed, never hardcoded."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[_Row] = []

    # --- Qt model plumbing -----------------------------------------------------------
    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802 (Qt override)
        return {
            _ROLE_ID: QByteArray(b"id"),
            _ROLE_TITLE: QByteArray(b"title"),
            _ROLE_THUMB: QByteArray(b"thumb"),
            _ROLE_IN_PLAYLIST: QByteArray(b"inPlaylist"),
            _ROLE_FAVORITE: QByteArray(b"favorite"),
            _ROLE_TYPE: QByteArray(b"type"),
            _ROLE_MISSING: QByteArray(b"missing"),
            _ROLE_PENDING_REVIEW: QByteArray(b"pendingReview"),
            _ROLE_REFUSED: QByteArray(b"refused"),
        }

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]
        if role == _ROLE_ID:
            return row.id
        if role == _ROLE_TITLE:
            return row.title
        if role == _ROLE_THUMB:
            # file:// URL for the thumbnail, or "" so QML can show its placeholder.
            return QUrl.fromLocalFile(row.thumb).toString() if row.thumb else ""
        if role == _ROLE_IN_PLAYLIST:
            return row.in_playlist
        if role == _ROLE_FAVORITE:
            return row.favorite
        if role == _ROLE_TYPE:
            return row.type
        if role == _ROLE_MISSING:
            return row.missing
        if role == _ROLE_PENDING_REVIEW:
            return row.pending_review
        if role == _ROLE_REFUSED:
            return row.refused
        return None

    def reload(self, members: set[str] | None = None) -> None:
        """Rebuild every row from the storage/discovery sources.

        `members` is the ACTIVE playlist's membership set; the card checkbox reflects it
        (v1.0 4.1). tags good/bad stays curation state and only drives grid membership.
        """
        wallpapers_dir = _wallpapers_dir()
        dir_ids = set(_scan_dir_ids(wallpapers_dir))
        if members is None:
            members = set()
        try:
            good = tags.good_ids()
        except Exception:
            good = set()
        try:
            known = tags.known_ids()
        except Exception:
            known = set()
        try:
            review = tags.review_ids()
        except Exception:
            review = set()
        try:
            meta_all = meta.load()
        except Exception:
            meta_all = {}

        rows: list[_Row] = []
        for wid in library_ids():
            row = _Row(wid)
            # broken = good-but-absent: render source missing (not just a same-named dir gone)
            row.missing = not _is_present(wid, wallpapers_dir, dir_ids)
            # a folder the id allowlist refuses keeps its card, and the card says why
            row.refused = not paths.is_safe_wid(wid)
            # pending review = on disk and never classified, OR imported under
            # review-required (the tags `review` state)
            row.pending_review = wid not in known or wid in review
            # identity (title/preview/type) reads from the item's OWN dir, never from
            # BG - a preset's BG points at its base, and reading identity there showed
            # the base's name and preview (the reported bug). Presence still uses BG
            # (via _is_present above), so render and identity are cleanly separated.
            wdir = _identity_dir(wid, wallpapers_dir)
            try:
                proj = project.read(wdir)
            except Exception:
                proj = {}
            title = proj.get("title") if isinstance(proj, dict) else ""
            row.title = title or wid
            row.thumb = (proj.get("preview") if isinstance(proj, dict) else "") or ""
            # the type as the census stores it, one spelling: project files carry "Scene" and
            # "Web" beside "scene", and the funnel compares by the word
            row.type = str((proj.get("type") if isinstance(proj, dict) else "") or "").strip().lower()
            row.in_playlist = wid in members
            entry = meta_all.get(wid) if isinstance(meta_all, dict) else None
            row.favorite = bool(entry.get("favorite")) if isinstance(entry, dict) else False
            rows.append(row)

        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def _index_of(self, wid: str) -> int:
        for i, row in enumerate(self._rows):
            if row.id == wid:
                return i
        return -1

    def set_in_playlist(self, wid: str, on: bool) -> None:
        i = self._index_of(wid)
        if i < 0:
            return
        self._rows[i].in_playlist = on
        idx = self.index(i, 0)
        self.dataChanged.emit(idx, idx, [_ROLE_IN_PLAYLIST])

    def set_favorite(self, wid: str, on: bool) -> None:
        i = self._index_of(wid)
        if i < 0:
            return
        self._rows[i].favorite = on
        idx = self.index(i, 0)
        self.dataChanged.emit(idx, idx, [_ROLE_FAVORITE])

    def title_of(self, wid: str) -> str:
        i = self._index_of(wid)
        return self._rows[i].title if i >= 0 else wid


class LibraryFilterModel(QSortFilterProxyModel):
    """Client-side search + favorites filter over LibraryModel.

    Replaces the old QML DelegateModel `inShown`-group filter, which left the GridView with stale
    layout (gaps), dead scroll, and stale favorites because group-membership toggling does not give
    the view proper reset/insert/remove signals. A proxy model emits those signals on every
    invalidateRowsFilter() and forwards source dataChanged (so toggling a favorite updates the view
    live in favorites mode). Matching is case-insensitive on title+id (lowercased BOTH sides).
    """

    # one invalidateRowsFilter() emits a rowsRemoved/rowsInserted per contiguous run; this fires
    # once after the last of them, so a listener can apply the whole change as one update
    filterInvalidated = Signal()

    def __init__(self, source: QAbstractListModel, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.setSourceModel(source)
        self.setDynamicSortFilter(True)
        # a source edit can re-filter rows without a setter; the proxy's own slots ran first
        # (connection order), so this is the end of that batch too
        for sig in (source.dataChanged, source.rowsInserted, source.rowsRemoved):
            sig.connect(self._announce)
        self._search = ""
        self._scope = "all"      # rail: "all" | "favorites" | "review"
        self._type = "all"       # funnel: "all" | "scene" | "video" | "web" | "untyped"
        self._pl = "any"         # funnel: "any" | "in" | "out" (active playlist)

    def _invalidate(self) -> None:
        self.invalidateRowsFilter()
        self.filterInvalidated.emit()

    def _announce(self, *_args) -> None:
        self.filterInvalidated.emit()

    @Slot(str)
    def setSearchText(self, text: str) -> None:
        s = (text or "").strip().casefold()  # casefold (not lower) for correct non-ASCII matching
        if s != self._search:
            self._search = s
            self._invalidate()

    @Slot(str)
    def setScope(self, scope: str) -> None:
        s = scope or "all"
        if s != self._scope:
            self._scope = s
            self._invalidate()

    @Slot(str)
    def setFilterMode(self, mode: str) -> None:
        # legacy name for setScope, kept for existing callers/tests
        self.setScope(mode)

    @Slot(str)
    def setTypeFilter(self, t: str) -> None:
        v = t or "all"
        if v != self._type:
            self._type = v
            self._invalidate()

    @Slot(str)
    def setPlaylistFilter(self, p: str) -> None:
        v = p or "any"
        if v != self._pl:
            self._pl = v
            self._invalidate()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802 (Qt override)
        src = self.sourceModel()
        idx = src.index(row, 0, parent)
        if self._scope == "favorites" and not bool(src.data(idx, _ROLE_FAVORITE)):
            return False
        if self._scope == "review" and not bool(src.data(idx, _ROLE_PENDING_REVIEW)):
            return False
        # Workshop is a funnel: an item lives in exactly one of the two surfaces. Pending
        # (workshop) items show ONLY under the review scope, never in All/favorites.
        if self._scope != "review" and bool(src.data(idx, _ROLE_PENDING_REVIEW)):
            return False
        if self._type != "all":
            kind = str(src.data(idx, _ROLE_TYPE) or "").strip().lower()
            if (kind == "") != (self._type == "untyped") or (self._type != "untyped" and kind != self._type):
                return False
        if self._pl == "in" and not bool(src.data(idx, _ROLE_IN_PLAYLIST)):
            return False
        if self._pl == "out" and bool(src.data(idx, _ROLE_IN_PLAYLIST)):
            return False
        if self._search:
            title = str(src.data(idx, _ROLE_TITLE) or "")
            wid = str(src.data(idx, _ROLE_ID) or "")
            if self._search not in (title + " " + wid).casefold():
                return False
        return True


class Backend(QObject):
    """The single QObject exposed to QML. Owns the library model + mutations + status read."""

    statusChanged = Signal()
    engineStatsChanged = Signal()
    settingsChanged = Signal()
    countChanged = Signal()
    notice = Signal(str)

    playlistsChanged = Signal()
    # the engine's own countdown in milliseconds, straight from a lanes-set reply, so the deck
    # anchors its clock to the engine at a pause or resume instead of guessing until the next poll
    rotationClock = Signal(int, int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        try:
            playlists.ensure_default()
        except Exception:
            pass
        self._model = LibraryModel(self)
        self._model.reload(self._active_members())
        self._model.modelReset.connect(self.countChanged)
        self._filter = LibraryFilterModel(self._model, self)
        # the GridView binds to this: members first in stored order, fillers, then the pool
        self._order = LibraryOrderModel(self._filter, self._member_order, self)
        self.playlistsChanged.connect(self._order.resync)
        self._cpu_last: tuple | None = None   # (monotonic, ticks, pidset) for the CPU delta
        self._vram_total: int = -1            # cached GPU total MiB (-1 until queried)
        self._stats_last: dict = {"cpu": 0.0, "gpu": -1, "vram": -1, "rss": -1,
                                  "vramTotal": -1, "memHigh": -1, "pids": 0}
        self._stats_busy = False
        # (engine_wid, ui_wid) of the last API show that the engine accepted. Presets have
        # no project of their own - the engine renders their BASE wallpaper - so status()
        # needs this to translate engine truth back to the tile the user actually clicked.
        # engine pid last seen by status(): a CHANGE means a fresh engine whose
        # in-memory rotation set / ui_id / history are EMPTY (leak-guard recycle,
        # crash restart, cutover idle boot) - the poll re-pushes on transition
        self._engine_pid_seen: int | None = None
        self._schedule_held = False
        self._schedule_refused = False
        self._drain_generation: int | None = None
        self._drain_failures = 0
        self._drain_after = 0.0
        self._version_logged: str | None = None
        self._ready_old_pid: Any = None
        self._ready_deadline = 0.0
        self._ready_timer = QTimer(self)
        self._ready_timer.setInterval(250)
        self._ready_timer.timeout.connect(self._ready_tick)
        self._delivery_owners: set[Any] = set()
        # (monotonic, frames, pid) baseline for the measured frame rate. The engine
        # reports a CUMULATIVE frame count, so a rate needs two samples; the pid is
        # part of the key because a fresh engine restarts the counter at zero.
        self._frames_last: tuple[float, int, Any] | None = None

    def _active_slug(self) -> str:
        return push._active_slug()

    def _active_members(self) -> set[str]:
        slug = self._active_slug()
        if not slug:
            return set()
        try:
            return set(playlists.members(slug))
        except Exception:
            return set()

    def _get_library_model(self) -> LibraryModel:
        return self._model

    # Read-only QML property (the model never gets reassigned; rows update in place / via reset).
    libraryModel = Property(QObject, _get_library_model, constant=True)

    def _get_filter_model(self) -> LibraryFilterModel:
        return self._filter

    # The GridView binds to this (search + favorites filter; proper reset signals -> relayout).
    filterModel = Property(QObject, _get_filter_model, constant=True)

    def _get_order_model(self) -> LibraryOrderModel:
        return self._order

    orderModel = Property(QObject, _get_order_model, constant=True)

    def _member_order(self) -> list[str]:
        slug = self._active_slug()
        return playlists.members(slug) if slug else []

    @Slot(str, result=bool)
    def beginDrag(self, wid: str) -> bool:
        return self._order.beginDrag(wid)

    @Slot(int)
    def dragOver(self, row: int) -> None:
        self._order.dragOver(row)

    @Slot(bool, result=str)
    def endDrag(self, commit: bool) -> str:
        """Drop: store the provisional order once (reorder or insert) through the change
        runner, which sends its push. Returns the action taken: none, reorder or insert."""
        plan = self._order.dragPlan(commit)
        action = str(plan.get("action") or "none")
        slug = self._active_slug()
        if action != "none" and slug:
            inserted: list[int] = []

            def write() -> None:
                if action == "reorder":
                    playlists.reorder(slug, list(plan["members"]))
                else:
                    inserted.append(playlists.insert_member(slug, str(plan["wid"]), int(plan["index"])))
            if self._change("playlist order", ("playlists",), write, [("members", "MEMBERS")],
                            slug=slug) is None:
                action = "none"
            elif action == "insert":
                if inserted and inserted[0] >= 0:
                    self._model.set_in_playlist(str(plan["wid"]), True)
                else:
                    action = "none"
        self._order.endDrag(commit)
        if action != "none":
            self.countChanged.emit()
            self.playlistsChanged.emit()
        return action

    @Slot(str)
    def setSearch(self, text: str) -> None:
        self._filter.setSearchText(text)

    @Slot(str)
    def setFilterMode(self, mode: str) -> None:
        self._filter.setFilterMode(mode)

    @Slot()
    def refresh(self) -> None:
        """Re-scan the library from disk (after content changes)."""
        self._model.reload(self._active_members())
        self.countChanged.emit()

    @Slot(str, result=bool)
    def showNow(self, wid: str) -> bool:
        """Show this wallpaper on the desktop now (engine `show` verb, ack-only ~30ms)."""
        wid = (wid or "").strip()
        if not paths.is_safe_wid(wid):
            self.notice.emit(REFUSED_REASON)
            return False
        # card-play re-arms a stopped engine (v1.0 acceptance 7): master off is a hold,
        # not a lockout
        try:
            if self.masterState() != "active":
                self.setMaster(True)
        except Exception:
            pass
        # The RESOLVED per-wallpaper render settings ride along with every show: the API
        # hot swap cannot touch env, so without these args every swapped wallpaper kept
        # the launch wallpaper's color grade (engine burn-in incident 3, visibly wrong
        # colors).
        try:
            with push.engine_only():
                shown = push.show(wid)
            if shown:
                self.statusChanged.emit()
                return True
        except Exception:
            pass
        return False

    @Slot(result=int)
    def totalCount(self) -> int:
        return self._model.rowCount()

    @Slot(result=int)
    def playlistCount(self) -> int:
        return len(self._active_members())

    @Slot(result=int)
    def reviewCount(self) -> int:
        """Pending imports: workshop subdirs not yet known to tags (the import pipeline)."""
        try:
            wdir = str(settings.load().get("WORKSHOP_DIR") or paths.detect_workshop_dir())
            known = tags.known_ids()
            return sum(1 for d in _scan_dir_ids(wdir) if d not in known)
        except Exception:
            return 0

    @Slot(result=int)
    def pendingReviewCount(self) -> int:
        """The rail badge: everything the Review scope shows - on-disk wallpapers never
        classified good/bad PLUS imported items in the `review` tags state (which may
        have no library dir at all under the reference policy). The badge and the scope
        filter the same population by construction."""
        try:
            known = tags.known_ids()
        except Exception:
            known = set()
        try:
            review = tags.review_ids()
        except Exception:
            review = set()
        unknown_on_disk = {d for d in _scan_dir_ids(_wallpapers_dir()) if d not in known}
        return len(unknown_on_disk | review)

    @Slot(result="QVariantList")
    def playlistList(self) -> list:
        try:
            rows = playlists.list_playlists()
        except Exception:
            return []
        out = []
        for r in rows:
            out.append({
                "slug": r.get("slug", ""),
                "name": r.get("NAME") or r.get("slug", ""),
                "mode": r.get("MODE", "shuffle"),
                "interval": int(r.get("INTERVAL", 900)),
                "unit": r.get("UNIT", "min"),
                "count": len(str(r.get("MEMBERS", "")).split()),
            })
        return out

    @Slot(result="QVariantMap")
    def activePlaylist(self) -> dict:
        slug = self._active_slug()
        if not slug:
            return {"slug": "", "name": "", "mode": "shuffle",
                    "interval": 900, "unit": "min", "count": 0}
        try:
            d = playlists.load(slug)
        except Exception:
            return {"slug": slug, "name": slug, "mode": "shuffle",
                    "interval": 900, "unit": "min", "count": 0}
        return {"slug": slug, "name": d.get("NAME") or slug, "mode": d.get("MODE", "shuffle"),
                "interval": int(d.get("INTERVAL", 900)), "unit": d.get("UNIT", "min"),
                "count": len(str(d.get("MEMBERS", "")).split())}

    def _follow_engine_playlist(self, slug: str) -> None:
        """Adopt the playlist the engine's schedule switched to, grid membership included; no
        push back, the engine is the source. A busy settings store skips it until a later poll."""
        try:
            with lock.held("settings", wait_s=0):
                playlists.set_active(slug)
        except Exception:
            return
        try:
            self._model.reload(self._active_members())
        except Exception:
            pass
        self.countChanged.emit()
        self.playlistsChanged.emit()

    def _after_playlist_change(self) -> None:
        self._model.reload(self._active_members())
        self.countChanged.emit()
        self.playlistsChanged.emit()

    def _change(self, what: str, locks: tuple[str, ...], write: Any, rows: list, **kwargs: Any) -> Any:
        """One window change through the change runner; its Outcome, or None when nothing was saved.
        A refused switch or a store refusal logs one warning; any other failure returns None."""
        return self._guarded(what, lambda: push.run_change(locks, write, rows, defer_current=self.delivery_due(),
                                                           **kwargs),
                             schedule=any(row == "schedule" for row, _key in rows))

    def _guarded(self, what: str, change: Any, schedule: Any = False) -> Any:
        """Run change(), which returns an Outcome, with _change's handling: a refused switch or a
        store refusal logs one warning and returns None, as any other failure does; the Outcome goes
        to _note, with `schedule` or, when it is a function, what it returns after change()."""
        try:
            outcome = change()
        except push.SwitchRefused as exc:
            logging.getLogger(__name__).warning("%s not made: %s", what, exc)
            return None
        except (lock.StoreBusy, ValueError) as exc:
            logging.getLogger(__name__).warning("%s not saved: %s", what, exc)
            return None
        except Exception:
            return None
        self._note(outcome, schedule=schedule() if callable(schedule) else schedule)
        return outcome

    def _note(self, outcome: Any, schedule: bool = False) -> None:
        """What the window keeps from a run: the lane clock for the deck's countdown; the schedule
        cell off while the engine's last answer to schedule-set was a refusal (`schedule`: the run
        sends schedule-set); and a version refusal from inside sync, which the window has no text
        for, logged once per message."""
        if outcome.clock is not None:
            self.rotationClock.emit(*outcome.clock)
        if outcome.refused_verb == "schedule-set":
            self._schedule_refused = True
        elif schedule and outcome.kind == "applied":
            self._schedule_refused = False
        if outcome.reason == "version" and outcome.message != self._version_logged:
            self._version_logged = outcome.message
            logging.getLogger(__name__).warning("engine not updated: %s", outcome.message)

    @Slot(str)
    def setActivePlaylist(self, slug: str) -> None:
        # the user's own switch: under a schedule the engine holds it until the next boundary
        if self._change("playlist switch", ("playlists", "settings"), lambda: playlists.set_active(slug),
                        [("active", "ACTIVE_PLAYLIST")], slug=slug, manual=True) is None:
            return
        self._after_playlist_change()

    @Slot(str, result=str)
    def createPlaylist(self, name: str) -> str:
        made: list[str] = []

        def write() -> None:
            made.append(playlists.create(name))
            playlists.set_active(made[0])
        if self._change("playlist create", ("playlists", "settings"), write,
                        [("active", "ACTIVE_PLAYLIST")], manual=True) is None or not made:
            return ""
        self._after_playlist_change()
        return made[0]

    @Slot(str, result=str)
    def saveAsPlaylist(self, name: str) -> str:
        """Fork the active playlist under a new name and switch to it (deck Save as)."""
        cur_slug = self._active_slug()
        made: list[str] = []

        def write() -> None:
            cur = playlists.load(cur_slug) if cur_slug else {}
            made.append(playlists.create(name, members=str(cur.get("MEMBERS", "")).split(),
                                         mode=cur.get("MODE", "shuffle"),
                                         interval=int(cur.get("INTERVAL", 900)),
                                         unit=cur.get("UNIT", "min")))
            playlists.set_active(made[0])
        if self._change("playlist save as", ("playlists", "settings"), write,
                        [("active", "ACTIVE_PLAYLIST")], manual=True) is None or not made:
            return ""
        self._after_playlist_change()
        return made[0]

    @Slot(str)
    def renameActivePlaylist(self, name: str) -> None:
        slug = self._active_slug()
        if not slug:
            return
        if self._change("playlist rename", ("playlists",), lambda: playlists.rename(slug, name),
                        [("members", "NAME")], slug=slug) is None:
            return
        self.playlistsChanged.emit()

    @Slot(str)
    def deletePlaylist(self, slug: str) -> None:
        """Delete one playlist from its row in the menu; the active one reassigns as before."""
        slug = str(slug or "")
        if slug:
            self._delete_playlist(slug)

    @Slot()
    def deleteActivePlaylist(self) -> None:
        slug = self._active_slug()
        if slug:
            self._delete_playlist(slug)

    def _delete_playlist(self, slug: str) -> None:
        """One delete through the change runner. After the status read, the settings and playlists
        locks are held from a fresh read through the write: deleting the active playlist is a
        switch and deleting a scheduled one drops it from the schedule, so the change carries those
        rows and its marker is set before anything is written; a playlist that is neither sends
        nothing."""
        decided: list[tuple[str, str]] = []

        def change() -> Any:
            first = push.read_status()
            with lock.held("settings"), lock.held("playlists"):
                active = slug == self._active_slug()
                packed = str(self._setting("SCHEDULE", "") or "")
                scheduled = any(e.strip() and e.partition("=")[2].strip() == slug for e in packed.split(";"))
                decided.extend(([("active", "ACTIVE_PLAYLIST")] if active else [])
                               + ([("schedule", "SCHEDULE")] if scheduled else []))

                def write() -> None:
                    playlists.delete(slug)  # tombstones + reassigns the active pointer if it was active
                    if scheduled:
                        self._drop_from_schedule(slug)
                ticket = push.save_change(("playlists", "settings"), write, decided or [("none", None)],
                                          manual=active, status=first)
            return push.deliver(ticket, defer_current=self.delivery_due())
        if self._guarded("playlist delete", change,
                         schedule=lambda: any(row == "schedule" for row, _key in decided)) is None:
            return
        self._after_playlist_change()

    def _drop_from_schedule(self, slug: str) -> None:
        """A deleted playlist leaves the schedule and the schedule switches off, the same
        thing the engine does on its side; the modal then opens with the switch off."""
        def drop(cur: dict[str, Any]) -> dict[str, Any]:
            packed = str(cur.get("SCHEDULE", "") or "")
            kept = [e for e in packed.split(";") if e.strip() and e.partition("=")[2].strip() != slug]
            if len(kept) == len([e for e in packed.split(";") if e.strip()]):
                return {}
            return {"SCHEDULE": ";".join(kept), "SCHEDULE_ENABLED": False}
        try:
            changed = settings.modify(drop)
        except Exception:
            return
        if changed:
            self.settingsChanged.emit()

    @Slot(str)
    def setPlaylistMode(self, mode: str) -> None:
        slug = self._active_slug()
        if not slug:
            return
        if self._change("playlist mode", ("playlists",), lambda: playlists.update(slug, {"MODE": mode}),
                        [("policy", "MODE")], slug=slug) is None:
            return
        self.playlistsChanged.emit()
        # the deck shows the engine's word for the mode: fetch it now, not at the next poll
        self.statusChanged.emit()

    @Slot(int, str)
    def setPlaylistInterval(self, value: int, unit: str) -> None:
        slug = self._active_slug()
        if not slug:
            return
        seconds = int(value) * (60 if unit == "min" else 1)
        if self._change("playlist interval", ("playlists",),
                        lambda: playlists.update(slug, {"INTERVAL": seconds,
                                                        "UNIT": unit if unit in C.PLAYLIST_UNITS else "min"}),
                        [("policy", "INTERVAL")], slug=slug) is None:
            return
        self.playlistsChanged.emit()

    def _master_service(self) -> str:
        """The unit the master switch controls. OFF means OFF - full termination."""
        return C.ENGINE_SERVICE

    @Slot(result=str)
    def masterState(self) -> str:
        """systemd unit state for the master service: active/inactive/failed/masked/absent."""
        if _sandboxed():
            return "inactive"
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "is-active", self._master_service()],
                capture_output=True, text=True, timeout=3, check=False)
            out = (proc.stdout or "").strip()
            return out or "absent"
        except (OSError, subprocess.SubprocessError):
            return "absent"

    @Slot(bool, result=bool)
    def setMaster(self, on: bool) -> bool:
        """Start/stop whichever service is the master (off-wins ladder).

        enable/disable --now so the switch position survives a reboot; the unit
        is written WantedBy=graphical-session.target but never enabled at
        install time (daemon_unit.write_files never enables). A start first rebuilds
        engine-env, writes the unit and runs daemon-reload; a failure there runs no systemctl
        and shows the notice. A start that launched the engine bundles once it answers."""
        if _sandboxed():
            return False
        was_active = True
        if on:
            try:
                daemon_unit.write_files()
            except (ValueError, RuntimeError) as exc:
                self.notice.emit(f"Engine service config not updated: {exc}")
                return False
            was_active = self.masterState() == "active"
        args = ["enable", "--now"] if on else ["disable", "--now"]
        if on and not was_active:
            self._record_bundle(None)
        try:
            proc = subprocess.run(
                ["systemctl", "--user", *args, self._master_service()],
                capture_output=True, text=True, timeout=10, check=False)
            self.statusChanged.emit()
        except (OSError, subprocess.SubprocessError):
            return False
        if proc.returncode == 0 and not was_active:
            self._bundle_when_ready(None)
        return proc.returncode == 0

    @Slot(result=bool)
    def restartMaster(self) -> bool:
        """Queue a restart of the master service in place, so a change that reaches the
        engine only through its env file lands now. The enable state is left as it is.

        Queued, not awaited: a stop can run to the unit's stop timeout before systemd
        kills the engine, so the call returns before the engine has stopped. True means
        the job was accepted; whether the engine is back up is read from the service."""
        if _sandboxed():
            return False
        try:
            daemon_unit.write_files()
        except (ValueError, RuntimeError) as exc:
            self.notice.emit(f"Engine service config not updated: {exc}")
            return False
        cls, before = push.read_status()
        old_pid = before.get("pid") if cls == "ok" and before else None
        self._record_bundle(old_pid)
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "--no-block", "restart", self._master_service()],
                capture_output=True, text=True, timeout=10, check=False)
            self.statusChanged.emit()
        except (OSError, subprocess.SubprocessError):
            return False
        if proc.returncode == 0:
            self._bundle_when_ready(old_pid)
        return proc.returncode == 0

    def _record_bundle(self, old_pid: Any) -> None:
        """Before a service start or restart launches: BUNDLE in the sync marker, tied to the engine
        a restart replaces (old_pid), so a delivery to that engine cannot clear it."""
        try:
            marker.ensure(("BUNDLE",), replacing=old_pid if type(old_pid) is int else None)
        except OSError as exc:
            logging.getLogger(__name__).warning("sync marker not set: %s", exc)

    def _bundle_when_ready(self, old_pid: Any) -> None:
        """The bundle of a service start or restart, after _record_bundle and the launch: a status
        read every 250 ms for up to 20 s; the first ok status whose pid differs from old_pid runs
        the window's sync, and no answer leaves the marker to the drain."""
        self._ready_old_pid = old_pid
        self._ready_deadline = monotonic() + 20.0
        self._ready_timer.start()

    def _ready_tick(self) -> None:
        cls, status = push.read_status()
        if cls == "ok" and status.get("pid") != self._ready_old_pid:
            self._ready_timer.stop()
            try:
                self._note(push.sync_all("window", defer_current=self.delivery_due()), schedule=True)
            except OSError:
                pass
        elif monotonic() >= self._ready_deadline:
            self._ready_timer.stop()

    def _schedule_entries(self) -> list[dict[str, str]]:
        return push._schedule_entries()

    def _schedule_enabled(self) -> bool:
        return push._schedule_enabled()

    @staticmethod
    def _to_minute(text: str) -> int:
        try:
            h, m = str(text).split(":")
            return int(h) * 60 + int(m)
        except (TypeError, ValueError):
            return -1

    def _day_range(self) -> tuple[int, int]:
        """Where day begins and ends in minutes of the local day, from the stored times even
        when the schedule is off; the modal's defaults when nothing is stored."""
        packed = str(self._setting("SCHEDULE", "") or "")
        times = [self._to_minute(item.partition("=")[0]) for item in packed.split(";") if item.strip()]
        times = [t for t in times if t >= 0]
        if len(times) >= 2:
            return times[0], times[1]
        return 8 * 60, 20 * 60

    @staticmethod
    def _is_day_at(minute: int, day_from: int, day_to: int) -> bool:
        if day_from <= day_to:
            return day_from <= minute < day_to
        return minute >= day_from or minute < day_to

    @Slot(result=bool)
    def scheduleIsDay(self) -> bool:
        """Is the local time inside the day range now? Drives the deck cell's sun or moon."""
        now = time.localtime()
        day_from, day_to = self._day_range()
        return self._is_day_at(now.tm_hour * 60 + now.tm_min, day_from, day_to)

    @Slot(result="QVariantMap")
    def scheduleState(self) -> dict:
        """What the cell needs: whether the schedule is on, day or night now, and whether a
        manual switch is holding (from the last engine status)."""
        return {"enabled": self._schedule_enabled() and not self._schedule_refused,
                "is_day": self.scheduleIsDay(), "held": bool(self._schedule_held)}

    def _push_schedule(self) -> bool:
        return push._push_schedule()

    def _playlist_payload(self, slug: str) -> tuple[list, int, str, bool, str]:
        return push._playlist_payload(slug)

    @Slot(bool, str)
    def onItemCommitted(self, ok: bool, _reason: str) -> None:
        """A wizard publish/reject finished: refresh the library and re-push the rotation set.

        Without this push a freshly published wallpaper stayed out of the engine's rotation
        set until some unrelated action happened to push one: a playlist edit, a settings
        change, or an engine restart. It would show in the library and never come up in
        rotation.

        Wired at the composition root because BenchBridge deliberately holds no Backend
        reference.
        """
        if not ok:
            return
        try:
            self.refresh()
            self.drain_now()
        except Exception:
            pass

    def drain_now(self) -> Any:
        """The window's bundle now (sync_all), its marker ensured first so the run is recorded; its
        re-show is left to a bridge delivery that is due."""
        outcome = push.sync_all("window", defer_current=self.delivery_due())
        self._note(outcome, schedule=True)
        return outcome

    def delivery_due(self) -> bool:
        """True while a bridge holds a saved change whose delivery waits on its debounce."""
        return bool(self._delivery_owners)

    def hold_delivery(self, owner: Any, due: bool) -> None:
        """Mark, or clear, that `owner` holds a saved change waiting for its delivery."""
        if due:
            self._delivery_owners.add(owner)
        else:
            self._delivery_owners.discard(owner)

    def _drain(self) -> None:
        """The poll's drain: while the marker has classes and no readiness wait or bridge delivery
        is due, the window's bundle without waiting for sync. Failed drains of one generation are
        retried after 5 s, 15 s and 45 s, and when the retry after the 45 s wait fails, none follows
        until a writer raises the generation. A run that found sync busy, an unresponsive status or
        another build's engine counts nothing; an away one counts, as the poll has just read
        status."""
        if self._ready_timer.isActive() or self.delivery_due():
            return
        try:
            state = marker.read()
        except OSError:
            return
        if not state["classes"]:
            return
        if state["generation"] != self._drain_generation:
            self._drain_generation, self._drain_failures, self._drain_after = state["generation"], 0, 0.0
        if self._drain_failures >= 4 or monotonic() < self._drain_after:
            return
        try:
            outcome = push.sync_all("window", wait_s=0)
        except OSError:
            return
        self._note(outcome, schedule=True)
        if outcome.kind in ("refused", "uncertain") or outcome.reason == "away" \
                or (outcome.reason == "budget" and not outcome.recorded):
            self._drain_failures += 1
            if self._drain_failures < 4:
                self._drain_after = monotonic() + (5.0, 15.0, 45.0)[self._drain_failures - 1]

    def _sync_engine(self) -> None:
        self.drain_now()

    @Slot(result=bool)
    def rotateNext(self) -> bool:
        # a step is an intent to watch: it resumes a paused rotation first
        if not bool(self._setting("ROTATION_ENABLED", True)):
            try:
                self.setPaused(False)
            except lock.StoreBusy as exc:
                logging.getLogger(__name__).warning("rotation not resumed before next: %s", exc)
        try:
            with push.engine_only():
                reply = api_client.next_wallpaper()
        except Exception:
            return False
        if reply is not None and reply.get("ok"):
            self.statusChanged.emit()
            return True
        return False

    @Slot(result=bool)
    def rotatePrev(self) -> bool:
        # a step is an intent to watch: it resumes a paused rotation first
        if not bool(self._setting("ROTATION_ENABLED", True)):
            try:
                self.setPaused(False)
            except lock.StoreBusy as exc:
                logging.getLogger(__name__).warning("rotation not resumed before prev: %s", exc)
        try:
            with push.engine_only():
                reply = api_client.prev_wallpaper()
            if reply is not None and reply.get("ok"):
                self.statusChanged.emit()
                return True
            # empty history is an honest no
            return False
        except Exception:
            return False

    @Slot(bool)
    def setPaused(self, paused: bool) -> None:
        """Deck pause: hold rotation without touching the playlist mode."""
        self._set_setting("ROTATION_ENABLED", not paused)

    _SESSION_KEYS = push._SESSION_KEYS

    @Slot(str, result=bool)
    def sessionOverride(self, key: str) -> bool:
        skey = self._SESSION_KEYS.get(key)
        return bool(self._setting(skey, False)) if skey else False

    @Slot(str, result=str)
    def overrideReach(self, key: str) -> str:
        """How far a deck override reaches: "live" (the running scene) or "next" (next show).

        Same honesty contract as dev.instrumentReach - a control that looks instant and is
        not is the lying-toggle shape. _LIVE_GLOBAL_KEYS is the single source of truth: a
        key in it gets pushed to the running engine by its setting row, a key outside it
        is folded into the next show's args and lands up to a full rotation interval later.

        This is deliberately derived rather than hardcoded: landing a verb flips the deck
        markers by adding its key to that tuple, with no UI change - which is exactly what
        happened three times (set-volume, set-mouse, then set-audio, once the engine moved
        the audio processing gate from recorder construction to per-frame consumption).
        All four overrides are live now; "next" survives as the honest answer for any future
        control that lands on the next show.
        """
        skey = self._SESSION_KEYS.get(key)
        return "live" if skey in self._LIVE_GLOBAL_KEYS else "next"

    @Slot(float, result=float)
    def effectiveSpeed(self, factor: float) -> float:
        return push.effectiveSpeed(factor)

    def _current_ui_wid(self) -> str:
        return push._current_ui_wid()

    @Slot(str)
    def onWallpaperSaved(self, wid: str) -> None:
        """Composition-root hook for editor.saved - a save now REACHES the engine (sec 4.3).

        Always: re-push the rotation set, so every future show and engine-driven advance
        carries the new conf (this is the half every save was silently missing before).
        If the saved wallpaper is the one ON SCREEN: re-show it, which applies
        the scene-build-consumed values (PROP_ overrides, cc, skips) immediately - the
        live-verb class rides along in the same resolved args. The re-show rebuilds the
        scene and resets the rotation clock; that is the direct, expected consequence of
        the user's own Save on the showing wallpaper, unlike the SILENT clock reset the
        mute re-show ban targeted.
        """
        try:
            self._sync_engine()
        except Exception:
            pass
        try:
            if wid and wid == self._current_ui_wid():
                self.showNow(wid)
        except Exception:
            pass

    @Slot(float, result=float)
    def setEngineSpeed(self, speed: float) -> float:
        """The panel's session-speed control: push the EFFECTIVE rate live.

        UI range 0..10 - a deliberate narrowing of the engine's 0..20 clamp (2x headroom
        exists engine-side if ever wanted). Same contract as
        setAnimationFrozen: returns the engine-CONFIRMED value from the done reply, or
        -1.0 when the engine did not answer / rejected, so the control never invents state.
        """
        try:
            target = max(0.0, min(10.0, float(speed)))
            with push.engine_only():
                reply = api_client.set_speed(target)
            if not (isinstance(reply, dict) and reply.get("ok")):
                return -1.0
            result = reply.get("result") or {}
            return float(result.get("speed", target))
        except Exception:
            return -1.0

    @Slot(bool, result=float)
    def setAnimationFrozen(self, frozen: bool) -> float:
        """Freeze / resume the running scene's animation (deck pause/play).

        Mechanism is MANDATORY: timescale, never the engine's `pause`
        verb - `pause` halts the whole render loop, evicts render targets, and its resume
        jumps g_Time forward by the entire paused duration in one frame. set-speed 0 stops
        the accumulator with no catch-up and keeps rendering.

        Resume restores the wallpaper's RESOLVED rate (conf SPEED x ENGINE_TIMESCALE via
        resolve_show_args - the exact value the next show would send), so unfreezing never
        invents a speed. Session-scoped by construction: a rotation advance re-applies the
        entry's own speed, which unfreezes without our help.

        Returns the speed the ENGINE CONFIRMED in its done reply, or -1.0 when the engine
        did not answer / rejected. The deck glyph flips on this confirmed echo immediately
        instead of waiting out the 2 s status poll (the freeze itself lands next frame;
        making the icon wait for the poll read as a ~1-2 s lag on the click). Not
        optimistic state - the value comes from the engine's own reply, and -1 means the
        glyph does not move.

        Scene-time only: video (mpv) and web (CEF) wallpapers ignore g_Time, so on those
        this is accepted and has no visible effect.
        """
        try:
            if frozen:
                target = 0.0
            else:
                wid = self._current_ui_wid()
                if wid:
                    _, args = resolve_show_args(wid)
                    target = float(args.get("speed", 1.0))
                else:
                    target = 1.0
            with push.engine_only():
                reply = api_client.set_speed(target)
            if not (isinstance(reply, dict) and reply.get("ok")):
                return -1.0
            result = reply.get("result") or {}
            # the engine echoes the value it actually applied (post-clamp); trust that
            return float(result.get("speed", target))
        except Exception:
            return -1.0

    @Slot(str, bool)
    def setSessionOverride(self, key: str, on: bool) -> None:
        """Deck override icons: settings-persisted with the window's owner note, so quit clears
        only what this window set."""
        skey = self._SESSION_KEYS.get(key)
        if not skey:
            return
        self._set_setting(skey, bool(on), write=lambda: settings.set_window_override(skey, bool(on)))

    def restoreSessionOverrides(self) -> None:
        """App quit: clear only the session overrides this window set that nobody changed since, the
        keys whose owner note still matches (settings.clear_window_overrides), so a window exit
        never undoes another door's choice."""
        try:
            notes, cur = settings.window_notes(), settings.load()
            keys = [k for k, v in notes.items() if v and cur.get(k) == v]
            if keys:
                push.run_change(("settings",), settings.clear_window_overrides,
                                [(push.SETTING_ROWS[k], k) for k in keys], defer_current=self.delivery_due())
            elif notes:
                settings.clear_window_overrides()
        except Exception:
            return

    themeRefreshRequested = Signal()

    @Slot(str, result="QVariant")
    def getSetting(self, key: str) -> Any:
        spec = C.SETTINGS_SCHEMA.get(key)
        return self._setting(key, spec["default"] if spec else "")

    @Slot(result=str)
    def fullscreenBehavior(self) -> str:
        """The EFFECTIVE global fullscreen policy for the settings control to show.

        getSetting would hand back "" for an install that predates this control; the
        combo needs the mode that is actually in force, derived from the legacy
        pause-and-recovery pair in that case.
        """
        try:
            return resolve_fullscreen_behavior(settings.load())
        except Exception:
            return "off"

    @Slot(str, "QVariant")
    def setSetting(self, key: str, value: Any) -> None:
        if key not in C.SETTINGS_SCHEMA:
            return
        if isinstance(value, str) and value.startswith("file://"):
            value = QUrl(value).toLocalFile()  # folder pickers hand over file:// urls
        self._set_setting(key, value)  # emits settingsChanged (bound pages live-refresh off it)

    @Slot(str)
    def openPath(self, path: str) -> None:
        p = path
        if p.startswith("file://"):
            p = QUrl(p).toLocalFile()
        try:
            subprocess.Popen(["xdg-open", p], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    @Slot()
    def openLogs(self) -> None:
        self.openPath(str(paths.logs_dir()))

    def _ensure_open(self, name: str, header: str) -> None:
        fp = paths.config_dir() / name
        try:
            with lock.held("rules"):
                try:
                    fp.read_bytes()
                except FileNotFoundError:
                    rules.save(name, header)
        except OSError:
            pass
        self.openPath(str(fp))

    @Slot()
    def editWhitelist(self) -> None:
        self._ensure_open("pause-whitelist.txt",
                          "# one process pattern per line (pgrep -f); e.g. llama\n")

    @Slot()
    def editBlacklist(self) -> None:
        self._ensure_open("pause-blacklist.txt",
                          "# fullscreen app_ids exempt from pause, one per line; e.g. steam\n")

    def _list_count(self, name: str) -> int:
        """Entry count of a pause list file: non-empty, non-comment lines."""
        try:
            text = (paths.config_dir() / name).read_text(encoding="utf-8")
        except OSError:
            return 0
        return sum(1 for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#"))

    @Slot(result=int)
    def whitelistCount(self) -> int:
        return self._list_count("pause-whitelist.txt")

    @Slot(result=int)
    def blacklistCount(self) -> int:
        return self._list_count("pause-blacklist.txt")

    @Slot(result=str)
    def diskUsage(self) -> str:
        total = 0
        try:
            for root, _dirs, files in os.walk(_wallpapers_dir()):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
        except OSError:
            pass
        gb = total / (1024 ** 3)
        try:
            stones = sum(1 for r in tags.load() if r.get("state") == "bad")
        except Exception:
            stones = 0
        return f"{gb:.1f} GB · {stones} tombstones"

    #: Keys a Reset must CARRY FORWARD: the five resolved path keys, because a reset
    #: that relocates the library or the workshop root is data loss.
    _RESET_PRESERVED = ("ENGINE_BIN", "ASSETS_DIR",
                        "WALLPAPERS_DIR", "WORKSHOP_DIR", "STEAM_DIR")

    @Slot(result=bool)
    def resetConfig(self) -> bool:
        """Reset settings.conf to defaults (playlists/tags/wp confs are left alone).

        Load current -> build defaults -> carry the preserved set forward -> save.
        """
        def reset(current: dict[str, Any]) -> dict[str, Any]:
            fresh = dict(paths.default_settings())
            for key in self._RESET_PRESERVED:
                if key in current:
                    fresh[key] = current[key]
            return fresh
        def write() -> None:
            settings.replace(reset)
            try:
                daemon_unit.write_env()
            except Exception as exc:
                logging.getLogger(__name__).warning("engine env file not rebuilt after the reset: %s", exc)
        try:
            self._note(push.run_change(("settings", "env"), write, [("reload", None)],
                                       defer_current=self.delivery_due()), schedule=True)
        except Exception:
            return False
        self.settingsChanged.emit()
        return True

    def _autostart_file(self) -> str:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        return os.path.join(base, "autostart", "lwe-ui.desktop")

    @staticmethod
    def autostart_content() -> str:
        """The desired desktop-entry body. One builder shared by the toggle and the
        startup reconcile, so an entry written by an older build gets repaired to the
        current shape instead of surviving as drift.

        Exec is resolved to an absolute path when possible - XDG autostart runs before
        login shells have amended PATH, so a bare name is only a fallback. --tray makes
        a login launch land in the tray instead of opening the window over the session.
        """
        exe = shutil.which("lwe-ui") or "lwe-ui"
        return ("[Desktop Entry]\nType=Application\nName=LWE Control Panel\n"
                f"Exec={exe} --tray\nX-GNOME-Autostart-enabled=true\n")

    @Slot(result=bool)
    def getAutostart(self) -> bool:
        return os.path.isfile(self._autostart_file())

    @Slot(bool)
    def setAutostart(self, on: bool) -> None:
        fp = self._autostart_file()
        try:
            if on:
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                with open(fp, "w", encoding="utf-8") as fh:
                    fh.write(self.autostart_content())
            elif os.path.isfile(fp):
                os.remove(fp)
        except OSError:
            pass

    def reconcileAutostart(self) -> None:
        """Panel start: if the entry exists but predates the current shape (old Exec,
        missing --tray), rewrite it. Absent entry = autostart off = untouched."""
        fp = self._autostart_file()
        try:
            if os.path.isfile(fp):
                with open(fp, encoding="utf-8") as fh:
                    existing = fh.read()
                desired = self.autostart_content()
                if existing != desired:
                    with open(fp, "w", encoding="utf-8") as fh:
                        fh.write(desired)
        except OSError:
            pass



    @Slot(str, result=str)
    def thumbUrl(self, wid: str) -> str:
        """Thumbnail file URL for a wallpaper id (deck now-playing block)."""
        i = self._model._index_of(wid)
        if i < 0:
            return ""
        row = self._model._rows[i]
        return QUrl.fromLocalFile(row.thumb).toString() if row.thumb else ""

    @Slot(str, result=str)
    def titleOf(self, wid: str) -> str:
        return self._model.title_of(wid)

    @Slot(str, bool)
    def setPlaylist(self, wid: str, on: bool) -> None:
        """Toggle membership of `wid` in the ACTIVE playlist (the card checkbox)."""
        if not wid:
            return
        if not paths.is_safe_wid(wid):
            self.notice.emit(REFUSED_REASON)
            return
        slug = self._active_slug()
        if not slug:
            return
        member: list[bool] = []

        def write() -> None:
            playlists.toggle_member(slug, wid)
            now = wid in set(playlists.members(slug))
            if now != on:
                playlists.toggle_member(slug, wid)
                now = wid in set(playlists.members(slug))
            member.append(now)
        if self._change("playlist membership", ("playlists",), write, [("members", "MEMBERS")],
                        slug=slug) is None:
            return
        # the store may have refused (an unsafe id): the card shows what the store holds
        self._model.set_in_playlist(wid, member[0])
        self.countChanged.emit()
        self.playlistsChanged.emit()

    @Slot(str)
    def approveReview(self, wid: str) -> None:
        """Card approve (Review scope): the item graduates to `good` and enters rotation
        on the next rotation push. The counterpart of the trash's bad-tag."""
        if not wid:
            return
        title = self._model.title_of(wid)
        try:
            actions.approve(wid, title)
        except Exception:
            return
        self.refresh()
        self.settingsChanged.emit()

    @Slot(str)
    def trashWallpaper(self, wid: str) -> None:
        """Card trash through the one trash chain: tombstone (tags bad) and out of every playlist.
        Restorable by re-importing; the files are not deleted."""
        if not wid:
            return
        title = self._model.title_of(wid)
        if self._guarded("trash", lambda: actions.trash([(wid, title)], record=False, run="window")[1]) is None:
            return
        self.refresh()
        self.playlistsChanged.emit()

    @Slot(str)
    def toggleFavorite(self, wid: str) -> None:
        """Flip meta.favorite for a wallpaper, from a fresh read inside the meta lock."""
        if not wid:
            return
        try:
            entry = meta.modify(wid, lambda e: {"favorite": not bool(e.get("favorite"))})
        except Exception:
            return
        self._model.set_favorite(wid, entry["favorite"])

    @Slot(result=str)
    def getOrder(self) -> str:
        return str(self._setting("ORDER", "shuffle"))

    @Slot(str)
    def setOrder(self, value: str) -> None:
        try:
            self._set_setting("ORDER", value)
        except lock.StoreBusy as exc:
            logging.getLogger(__name__).warning("ORDER not saved: %s", exc)

    @Slot(result=int)
    def getInterval(self) -> int:
        try:
            return int(self._setting("INTERVAL", 900))
        except (ValueError, TypeError):
            return 900

    @Slot(int)
    def setInterval(self, value: int) -> None:
        try:
            self._set_setting("INTERVAL", int(value))
        except lock.StoreBusy as exc:
            logging.getLogger(__name__).warning("INTERVAL not saved: %s", exc)

    @Slot(result=bool)
    def getRotationEnabled(self) -> bool:
        return bool(self._setting("ROTATION_ENABLED", True))

    @Slot(bool)
    def setRotationEnabled(self, value: bool) -> None:
        try:
            self._set_setting("ROTATION_ENABLED", bool(value))
        except lock.StoreBusy as exc:
            logging.getLogger(__name__).warning("ROTATION_ENABLED not saved: %s", exc)

    @Slot(result="QStringList")
    def orderOptions(self) -> list[str]:
        return list(C.ORDERS)

    def _setting(self, key: str, default: Any) -> Any:
        return push._setting(key, default)

    _FULLSCREEN_KEYS = push._FULLSCREEN_KEYS

    _LIVE_GLOBAL_KEYS = push._LIVE_GLOBAL_KEYS

    def _fullscreen_ignore_ids(self) -> list[str]:
        return push._fullscreen_ignore_ids()

    def _app_condition_names(self) -> list[str]:
        return push._app_condition_names()

    def save_setting(self, key: str, value: Any, write: Any = None) -> Any:
        """One setting through the change runner: the settings lock (and env for a tuning or
        restart row), the store write (`write` when given), then the key's row. settingsChanged
        fires after the write."""
        row = push.SETTING_ROWS.get(key, "none")
        outcome = push.run_change(("settings", "env") if row in ("tuning", "restart") else ("settings",),
                                  write or (lambda: settings.update({key: value})), [(row, key)],
                                  defer_current=self.delivery_due())
        self.settingsChanged.emit()
        self._note(outcome, schedule=row == "schedule")
        return outcome

    def _set_setting(self, key: str, value: Any, write: Any = None) -> None:
        try:
            self.save_setting(key, value, write)
        except (lock.StoreBusy, ValueError):
            raise
        except Exception:
            return

    def _mem_high_mb(self) -> int:
        """The engine unit's MemoryHigh in MB, read from systemd once and cached; -1 when
        unreadable or infinity (the RAM bar then draws empty rather than invent a cap)."""
        cached = getattr(self, "_mem_high_cache", None)
        if cached is not None:
            return cached
        if _sandboxed():
            return -1
        val = -1
        try:
            r = subprocess.run(["systemctl", "--user", "show", "lwe-engine.service",
                                "-p", "MemoryHigh"],
                               capture_output=True, text=True, timeout=3, check=False)
            if r.returncode == 0:
                raw = r.stdout.strip().split("=", 1)[-1]
                if raw.isdigit():
                    val = int(raw) // (1024 * 1024)
        except (OSError, subprocess.SubprocessError, ValueError):
            val = -1
        self._mem_high_cache = val
        return val

    def _engine_pids(self) -> list[int]:
        """Pids of the whole engine family: the engine itself plus its CEF helpers.

        The helpers matter for honesty: on web wallpapers the CEF GPU process holds
        the bulk of the VRAM, and CEF keeps that footprint even after switching
        to a non-web wallpaper. Counting only the engine displayed a small fraction of
        the actual usage.
        """
        pids: list[int] = []
        if _sandboxed():
            return pids
        for comm in ("linux-wallpaper", "lwe-web-helper"):
            try:
                r = subprocess.run(["pgrep", "-x", comm],
                                   capture_output=True, text=True, timeout=2, check=False)
                pids += [int(p) for p in r.stdout.split() if p.strip().isdigit()]
            except (OSError, subprocess.SubprocessError, ValueError):
                continue
        return pids

    def _gpu_sample(self) -> tuple[int, int]:
        """(whole-GPU utilization %, total VRAM MiB); -1 for either when unavailable.

        ONE nvidia-smi fork for both. utilization.gpu is deliberately the WHOLE-GPU figure,
        not the engine's share: `nvidia-smi pmon`'s per-PID sm% column prints "-" on this
        hardware even while the engine renders healthily (measured), so a per-process GPU
        number has no working source. Whole-GPU also catches the compositor's share, which
        is the honest answer to "is the GPU busy" - but it means the GPU ring's SUBJECT
        differs from CPU/VRAM/FPS, which are engine-process facts. The header says so on
        hover; do not relabel it as engine load.
        """
        util, total = -1, -1
        if _sandboxed():
            return util, total
        try:
            q = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.total",
                                "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=3, check=False)
            if q.returncode == 0:
                cols = [c.strip() for c in q.stdout.strip().split("\n")[0].split(",")]
                if len(cols) >= 2 and cols[0].isdigit() and cols[1].isdigit():
                    util, total = int(cols[0]), int(cols[1])
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        return util, total

    @Slot(result="QVariantMap")
    def engineStats(self) -> dict:
        """The latest CPU/GPU/VRAM sample for the header meter cluster, and a request for
        the next one.

        The sample itself forks pgrep and nvidia-smi, so it is taken on a worker thread:
        this returns the last completed sample at once and starts the next one if none is
        running. engineStatsChanged fires when a sample lands.
        """
        if not self._stats_busy:
            self._stats_busy = True

            def work() -> None:
                try:
                    self._stats_last = self._sample_engine_stats()
                except Exception:
                    pass
                finally:
                    self._stats_busy = False
                self.engineStatsChanged.emit()

            threading.Thread(target=work, daemon=True).start()
        return dict(self._stats_last)

    @Slot(result="QVariantMap")
    def engineStatsLast(self) -> dict:
        """The last completed sample, without requesting another."""
        return dict(self._stats_last)

    def _sample_engine_stats(self) -> dict:
        """One CPU/GPU/VRAM sample. Worker thread only.

        cpu: percent of the whole machine across the engine family, delta between
        successive calls (first call and any pid change return 0.0 - a delta needs a
        baseline). gpu: WHOLE-GPU utilization percent (see _gpu_sample - different subject
        from the rest, deliberately). vram: MiB from nvidia-smi's per-process fb column,
        -1 when unavailable. vramTotal: MiB, cached after the first successful query.
        Budget: one pgrep pair, one pmon, one query-gpu per sample.
        """
        pids = self._engine_pids()
        # GPU is a whole-machine fact, so it is sampled whether or not the engine is up -
        # the ring stays honest when the master switch is off.
        gpu, vram_total = self._gpu_sample()
        if vram_total > 0:
            self._vram_total = vram_total
        out: dict[str, Any] = {"cpu": 0.0, "gpu": gpu, "vram": -1, "rss": -1,
                               "vramTotal": self._vram_total,
                               "memHigh": self._mem_high_mb(), "pids": len(pids)}
        if not pids:
            self._cpu_last = None
            return out
        # RAM: resident plus swapped MB summed across the family, one total; the drawn
        # fraction runs against MemoryHigh, whose cgroup covers exactly these processes
        out["rss"] = sum(_engine_mb_from_proc(pid) for pid in pids)
        # CPU: sum utime+stime ticks across the engine pids, delta over monotonic time
        import time as _time
        ticks = 0
        try:
            for pid in pids:
                with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as fh:
                    parts = fh.read().rsplit(")", 1)[1].split()
                ticks += int(parts[11]) + int(parts[12])  # utime, stime after comm
        except (OSError, ValueError, IndexError):
            ticks = -1
        now = _time.monotonic()
        if ticks >= 0:
            last = self._cpu_last
            if last and last[2] == set(pids) and now > last[0]:
                hz = os.sysconf("SC_CLK_TCK") or 100
                ncpu = os.cpu_count() or 1
                # share of the WHOLE machine (percent of one core read as 60% while the
                # system meter showed ~3% - divide by the core count)
                out["cpu"] = round(
                    max(0.0, (ticks - last[1]) / hz / (now - last[0]) * 100 / ncpu), 1)
            self._cpu_last = (now, ticks, set(pids))
        # VRAM: per-process framebuffer MiB via nvidia-smi pmon (works for GL apps)
        try:
            r = subprocess.run(["nvidia-smi", "pmon", "-c", "1", "-s", "m"],
                               capture_output=True, text=True, timeout=3, check=False)
            if r.returncode == 0:
                total = 0
                seen = False
                for ln in r.stdout.splitlines():
                    cols = ln.split()
                    if len(cols) >= 4 and cols[1].isdigit() and int(cols[1]) in pids:
                        seen = True
                        if cols[3].isdigit():
                            total += int(cols[3])
                if seen:
                    out["vram"] = total
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        return out

    @Slot(result="QVariantMap")
    def status(self) -> dict:
        """Best-effort runtime status, polled every 2 s by the UI.

        The engine's own socket is ground truth and the ONLY source consulted; the
        /proc scan below covers the one thing the socket cannot report about itself
        (its memory) and the down-detection when the socket is dead. Never spawns
        the engine.
        """
        result: dict[str, Any] = {
            "state": "down",
            "label": "engine down",
            "current": "",
            "engine_mb": "",
            "next_in": "",
            "monitor_mode": str(self._setting("MONITOR_MODE", "mirror")),
        }
        # the API overlay below fills state/current/next_in/interval from the socket

        # /proc fallback: measure the live engine's RSS+Swap directly (the socket does
        # not report its own memory). stdlib-only scan of /proc - no pgrep dependency.
        engine_pid = None
        try:
            cur_mb = int(str(result.get("engine_mb") or "0").strip() or "0")
        except (ValueError, TypeError):
            cur_mb = 0
        if cur_mb <= 0:
            engine_pid = _find_engine_pid()
            if engine_pid is not None:
                mb = _engine_mb_from_proc(engine_pid)
                if mb > 0:
                    result["engine_mb"] = mb
        # state derivation: a live engine process lights the dot even before the socket
        # overlay below answers.
        if result.get("state") != "up":
            if engine_pid is None:
                engine_pid = _find_engine_pid()
            if engine_pid is not None:
                result["state"] = "up"
        # Socket overlay (status v2): the engine is ground truth for what is rendering.
        # This poll is also the UI's heartbeat (feeds the engine's dead-man reflex).
        # The pid tracker below pushes policy ONCE per panel life, on first sight of
        # an engine; crash recovery is the engine's own (state restore + crash-loop
        # guard), never the panel's.
        try:
            try:
                api_client.ping()
            except Exception:
                pass
            api = api_client.status()
            if api is not None:
                result["state"] = "up"

                pid = api.get("pid")
                first_sight = False
                if pid != self._engine_pid_seen:
                    first_sight = self._engine_pid_seen is None
                    self._engine_pid_seen = pid
                    if first_sight:
                        # PANEL start: push the panel's stored policy once, so opening
                        # the panel heals a drifted or state-lost engine. Engine
                        # RE-arrivals get nothing: the engine restores its own state
                        # on boot, and an automatic re-push (or re-show) here would
                        # defeat its crash-loop guard by feeding the loop.
                        try:
                            self._note(push.sync_all("window", wait_s=0, defer_current=self.delivery_due()),
                                       schedule=True)
                        except OSError:
                            pass
                if not first_sight:
                    self._drain()

                # the fullscreen ignore-list is a FILE the user edits in their own
                # editor, so there is no save hook to hang a push on. Watch its
                # mtime here instead: an external edit lands within one poll.
                try:
                    bl = paths.config_dir() / "pause-blacklist.txt"
                    stamp = bl.stat().st_mtime_ns if bl.exists() else 0
                    if stamp != getattr(self, "_blacklist_stamp", None):
                        if getattr(self, "_blacklist_stamp", None) is not None:
                            with lock.held("sync", wait_s=0):
                                api_client.set_fullscreen_ignore(self._fullscreen_ignore_ids())
                        self._blacklist_stamp = stamp
                except Exception:
                    pass

                # Now Playing: the engine echoes the OPAQUE ui_id of whatever was
                # shown (preset tile identity survives engine-driven advances);
                # engine id / screens basename are the fallbacks.
                cur = api.get("current") or {}
                ui_id = str(cur.get("ui_id") or "")
                engine_wid = str(cur.get("id") or "")
                if not engine_wid:
                    screens = api.get("screens")
                    if isinstance(screens, dict) and screens:
                        path = str(next(iter(screens.values())) or "")
                        engine_wid = os.path.basename(path.rstrip("/"))
                if ui_id or engine_wid:
                    result["current"] = ui_id or engine_wid

                # rotation timing: the engine owns the schedule
                rot = api.get("rotation") or {}
                if isinstance(rot, dict):
                    if rot.get("next_in_s", -1) >= 0:
                        result["next_in"] = int(rot["next_in_s"])
                    if isinstance(rot.get("next_in_ms"), int) and rot["next_in_ms"] >= 0:
                        result["next_in_ms"] = int(rot["next_in_ms"])
                    result["interval"] = int(rot.get("interval_s") or 0)
                    result["playlist"] = str(rot.get("label") or "")
                    result["next_up"] = str(rot.get("next_up") or "")
                # the lane's transport view: behind, ahead, and whether back can act
                lanes = api.get("lanes")
                if isinstance(lanes, list) and lanes and isinstance(lanes[0], dict):
                    lane = lanes[0]
                    result["last"] = str(lane.get("previous") or "")
                    result["order"] = str(lane.get("order") or "")
                    result["next_up"] = str(lane.get("next") or "")
                    result["back_enabled"] = bool(lane.get("back_enabled", True))
                    # the schedule executes in the engine: while it is on, the engine's bound
                    # playlist is the active one and the panel follows it
                    sched = api.get("schedule")
                    if isinstance(sched, dict):
                        self._schedule_held = bool(sched.get("held"))
                        result["schedule_pending"] = str(sched.get("pending") or "")
                        bound = str(lane.get("playlist") or "")
                        if sched.get("enabled") and bound and bound != self._active_slug() \
                                and paths.playlist_file(bound).exists():
                            self._follow_engine_playlist(bound)

                outs = api.get("outputs")
                if isinstance(outs, dict):
                    result["outputs_state"] = str(outs.get("state") or "")
                    result["outputs_reason"] = str(outs.get("reason") or "")
                    result["deadman_s"] = int(outs.get("deadman_s") or 0)
                    result["ping_seen"] = bool(outs.get("ping_seen"))
                result["paused"] = bool(api.get("manual_pause")) or \
                    bool(api.get("fullscreen_pause"))
                result["manual_pause"] = bool(api.get("manual_pause"))
                result["fullscreen_pause"] = bool(api.get("fullscreen_pause"))
                # animation timescale (engine echoes m_timescale as `speed`); 0 == the
                # scene-time freeze. Drives the deck's wallpaper pause/play button.
                if isinstance(api.get("speed"), (int, float)):
                    result["speed"] = float(api["speed"])
                result["fullscreen_behavior"] = str(api.get("fullscreen_behavior") or "")
                if isinstance(api.get("clients"), int):
                    result["clients"] = api["clients"]
                if isinstance(api.get("uptime_s"), int):
                    result["uptime_s"] = api["uptime_s"]
                if isinstance(rot, dict) and isinstance(rot.get("count"), int):
                    result["rotation_count"] = rot["count"]
                    result["rotation_pos"] = int(rot.get("history_depth") or 0)

                # Measured frame rate. The payload's `fps` is the CAP, not the
                # achieved rate - `frames` is a cumulative counter, so the rate is
                # a delta between two polls. Absent until a baseline exists: the
                # first poll after the panel starts, and the first after any pid
                # change, report nothing rather than a number divided by an
                # unknown interval. A paused engine truthfully reads 0.
                cap = api.get("fps")
                if isinstance(cap, int) and cap > 0:
                    result["fps_cap"] = cap
                frames = api.get("frames")
                if isinstance(frames, int):
                    now_f = monotonic()
                    prev = self._frames_last
                    if (prev is not None and prev[2] == pid
                            and now_f > prev[0] and frames >= prev[1]):
                        result["fps"] = round((frames - prev[1]) / (now_f - prev[0]), 1)
                    self._frames_last = (now_f, frames, pid)
        except Exception:
            pass
        if result.get("state") == "up":
            result["label"] = "engine up"
        return result


class ThemeTokens(QObject):
    """Holds the resolved color tokens; registered as a QML singleton by app.py.

    Theme.qml (a QML-file singleton) imports the Python module this is registered under and reads
    each color via `color(key)`. Keeping the values here - rather than a root-context property -
    guarantees they are reachable from inside the QML singleton at runtime (context properties are
    not reliably visible to singletons).
    """

    changed = Signal()

    def __init__(self, tokens: dict[str, str] | None = None, parent: QObject | None = None,
                 preview_cap: int = 360) -> None:
        super().__init__(parent)
        self._tokens: dict[str, str] = dict(tokens or {})
        self._preview_cap = int(preview_cap)

    @Slot(result=int)
    def previewCap(self) -> int:
        # ONE process-wide decode cap (360 logical px x max screen DPR): the QML pixmap
        # cache keys on (file, sourceSize), so per-view caps would duplicate entries
        return self._preview_cap

    def set_tokens(self, tokens: dict[str, str]) -> None:
        self._tokens = dict(tokens or {})
        self.changed.emit()

    @Slot(str, result=str)
    @Slot(str, str, result=str)
    def color(self, key: str, fallback: str = "") -> str:
        """Return the token for `key`, or `fallback` if unset/empty."""
        val = self._tokens.get(key)
        return val if isinstance(val, str) and val else fallback


class ImportBridge(QObject):
    """The workshop detection/import surface (settings Library page, made real).

    Imports run on a worker thread - a copy-policy import moves whole wallpaper trees
    and must never freeze the GUI. DETECT_MODE drives when scans happen: launch = once
    at startup; interval = a QTimer at DETECT_INTERVAL_SEC; watch = a filesystem watch
    on WORKSHOP_DIR (5s debounce - Steam writes trees incrementally); manual = only the
    Rescan button. The mechanical pass itself lives in storage/importer.py."""

    scanFinished = Signal(int, int)   # (found, imported)
    busyChanged = Signal()
    _workDone = Signal(int, int)      # worker -> GUI thread (queued by connection)
    _repairDone = Signal(int)         # startup preset-repair worker -> GUI thread

    def __init__(self, backend: "Backend", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._busy = False
        self._thread = None
        # written by the worker thread, read in _finish; safe because _busy
        # serializes passes (one worker at a time), explicit per review L5
        self._last_resolved = 0
        self._rescan_pending = False
        # the worker must never touch the model directly: a cross-thread SIGNAL is
        # queued onto the GUI thread, a direct call is not
        self._workDone.connect(self._finish)
        self._repairDone.connect(self._on_repair_done)

        from PySide6.QtCore import QFileSystemWatcher, QTimer
        self._interval_timer = QTimer(self)
        self._interval_timer.timeout.connect(self.rescanNow)
        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_debounce = QTimer(self)
        self._fs_debounce.setSingleShot(True)
        self._fs_debounce.setInterval(5000)
        self._fs_debounce.timeout.connect(self.rescanNow)
        self._fs_watcher.directoryChanged.connect(lambda _p: self._fs_fire())

        # Workshop scope hot mode (16a): while the scope is visible the folder-watch is
        # force-armed regardless of DETECT_MODE - the user is over there BECAUSE they
        # are expecting arrivals. Scope exit re-applies the configured mode.
        self._hot = False
        # a pass triggered by a REAL filesystem event (something is landing) vs a
        # routine entry/manual/interval pass - the Workshop skeleton and "arriving"
        # caption key on this, so scope entry never flashes a phantom arrival (review F10)
        self._arrival = False

        self._apply_mode(startup=True)

        # one-shot startup self-heal (B11): pre-B10 preset confs whose BG points at a
        # payload-less dir crash the engine on launch. Repair runs regardless of
        # DETECT_MODE (a crash fix is not optional, even in manual mode) and off-thread
        # (a base may need a copy). Deferred so the window paints first.
        from PySide6.QtCore import QTimer as _QT
        _QT.singleShot(1200, self._startup_repair)

    def _startup_repair(self) -> None:
        # hold the busy flag so a manual rescan during the startup window is refused
        # (rescanNow early-returns on _busy) - no second import pass races repair
        if self._busy:
            # a scan somehow already started; retry the repair once it settles
            from PySide6.QtCore import QTimer as _QT
            _QT.singleShot(500, self._startup_repair)
            return
        self._busy = True
        self.busyChanged.emit()

        def work() -> None:
            from .storage import importer
            try:
                fixed = importer.repair_preset_confs()
            except Exception:
                import traceback
                traceback.print_exc()
                fixed = []
            self._repairDone.emit(len(fixed))

        threading.Thread(target=work, daemon=True).start()

    def _on_repair_done(self, n: int) -> None:
        self._busy = False
        self.busyChanged.emit()
        if n > 0:
            try:
                self._backend.refresh()
                self._backend.settingsChanged.emit()
            except Exception:
                pass
            try:
                self._backend.drain_now()
            except Exception:
                pass
        # only now, repair fully done, run the startup scan a mode owed (serialized so
        # the two passes never race the same base copytree - review M1/M2). A scope
        # entry that latched a rescan while repair held _busy also drains here.
        if getattr(self, "_startup_scan_due", False) or self._rescan_pending:
            self._startup_scan_due = False
            self._rescan_pending = False
            self.rescanNow()

    def _fs_fire(self) -> None:
        self._arrival = True
        self._fs_debounce.start()

    @Slot(result=bool)
    def arrivalPending(self) -> bool:
        return self._arrival

    @Slot(bool)
    def setScopeHot(self, on: bool) -> None:
        """Surgical arm/disarm: the interval timer is untouched (a full _apply_mode
        would reset its countdown on every scope visit)."""
        on = bool(on)
        if on == self._hot:
            return
        self._hot = on
        from .storage import importer, settings as _settings
        wdir = importer.workshop_dir()
        if on:
            if os.path.isdir(wdir) and wdir not in self._fs_watcher.directories():
                self._fs_watcher.addPath(wdir)
            self.rescanNow()
        else:
            try:
                mode = str(_settings.load().get("DETECT_MODE", "launch"))
            except Exception:
                mode = "launch"
            if mode != "watch" and wdir in self._fs_watcher.directories():
                self._fs_watcher.removePath(wdir)

    def _apply_mode(self, startup: bool = False) -> None:
        from .storage import importer, settings as _settings
        try:
            cfg = _settings.load()
            mode = str(cfg.get("DETECT_MODE", "launch"))
            seconds = int(cfg.get("DETECT_INTERVAL_SEC", 60) or 60)
        except Exception:
            mode, seconds = "launch", 60
        self._interval_timer.stop()
        for d in list(self._fs_watcher.directories()):
            self._fs_watcher.removePath(d)
        if mode == "interval":
            self._interval_timer.start(max(15, seconds) * 1000)
        if mode == "watch" or getattr(self, "_hot", False):
            wdir = importer.workshop_dir()
            if os.path.isdir(wdir):
                self._fs_watcher.addPath(wdir)
        if startup:
            # every non-manual mode owes ONE scan at startup, but it must run AFTER the
            # preset repair, never concurrently (two worker threads racing the same base
            # copytree - review M1/M2). _on_repair_done chains the scan when it is due;
            # repair itself always runs (a crash fix is not mode-gated).
            self._startup_scan_due = mode in ("launch", "interval", "watch")

    @Slot()
    def settingsApplied(self) -> None:
        """Re-arm timers/watches after a DETECT_MODE / interval change."""
        self._apply_mode()

    @Slot(result=bool)
    def isBusy(self) -> bool:
        return self._busy

    @Slot()
    def rescanNow(self) -> None:
        """One full detection pass off-thread; refreshes the library when anything landed.
        A call while a pass (or the startup repair) already holds the busy flag is not
        dropped - it LATCHES a pending rescan that fires when the current pass finishes.
        Without this, a scope entry during the ~1.2s startup repair silently lost its
        scan (nothing retried it), which also flaked the arrival test."""
        if self._busy:
            self._rescan_pending = True
            return
        self._busy = True
        self.busyChanged.emit()


        def work() -> None:
            from .storage import importer
            try:
                res = importer.run_scan_and_import()
            except Exception:
                import traceback
                traceback.print_exc()   # never silent: a crashed pass looked like 0/0
                res = {"found": 0, "imported": 0}
            self._last_resolved = int(res.get("resolved", 0))
            # queued back onto the GUI thread (cross-thread signal emission is queued;
            # calling _finish directly would run model resets on this worker thread)
            self._workDone.emit(int(res.get("found", 0)), int(res.get("imported", 0)))

        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()

    def _finish(self, found: int, imported: int) -> None:
        self._busy = False
        self._arrival = False
        resolved = int(getattr(self, "_last_resolved", 0))
        self._last_resolved = 0
        if imported > 0 or resolved > 0:
            try:
                self._backend.refresh()
                self._backend.settingsChanged.emit()
            except Exception:
                pass
            try:
                self._backend.drain_now()
            except Exception:
                pass
        self.busyChanged.emit()
        self.scanFinished.emit(int(found), int(imported))
        if getattr(self, "_rescan_pending", False):
            self._rescan_pending = False
            self.rescanNow()


class ThemeBridge(QObject):
    """Settings > Theme backend: preset selection, the six-role editor,
    live picker writes, Esc-revert, and reset. Every mutation resolves the active theme
    and pushes the full token set into ThemeTokens, so the whole app restyles in the
    same frame - the window IS the preview (spec intent 1)."""

    changed = Signal()

    def __init__(self, tokens: ThemeTokens, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._edit_snapshot: dict | None = None
        self._edit_key = ""

    def _push(self) -> None:
        from .storage import themes
        try:
            self._tokens.set_tokens(themes.resolve_active())
        except Exception:
            pass
        self.changed.emit()

    @Slot(result="QVariantList")
    def themeList(self) -> list:
        """Menu rows: key, name, blurb, and each theme's EFFECTIVE accent (the menu
        previews identity - the selected row's check + wash render in the theme's own
        accent)."""
        from .storage import themes
        try:
            cfg = themes.load_config()
            out = []
            for t in themes.theme_list():
                roles = themes.effective_roles(t["key"], cfg["overlays"])
                out.append({"key": t["key"], "name": t["name"], "blurb": t["blurb"],
                            "accent": roles["accent"],
                            "background": roles["background"],
                            "dark": themes.luminance(roles["background"]) < 0.5,
                            "custom": t["key"] in themes.CUSTOM_KEYS})
            return out
        except Exception:
            return []

    @Slot(result=str)
    def activeKey(self) -> str:
        from .storage import themes
        return themes.load_config()["active"]

    @Slot(result=str)
    def activeName(self) -> str:
        from .storage import themes
        key = themes.load_config()["active"]
        return next((t["name"] for t in themes.theme_list() if t["key"] == key), key)

    @Slot(result=str)
    def activeBlurb(self) -> str:
        from .storage import themes
        key = themes.load_config()["active"]
        return next((t["blurb"] for t in themes.theme_list() if t["key"] == key), "")

    @Slot(str, result=str)
    def roleValue(self, role: str) -> str:
        from .storage import themes
        cfg = themes.load_config()
        return themes.effective_roles(cfg["active"], cfg["overlays"]).get(str(role), "")

    @Slot(result=bool)
    def isDark(self) -> bool:
        from .storage import themes
        cfg = themes.load_config()
        return themes.luminance(
            themes.effective_roles(cfg["active"], cfg["overlays"])["background"]) < 0.5

    @Slot(result=bool)
    def borderFellBack(self) -> bool:
        """True when the active palette's stored border trips the fallback rule (the
        page states it instead of rendering mud silently)."""
        from .storage import themes
        cfg = themes.load_config()
        return themes.border_fell_back(themes.effective_roles(cfg["active"], cfg["overlays"]))

    @Slot(str)
    def setActive(self, key: str) -> None:
        from .storage import themes
        try:
            if str(key) not in {t["key"] for t in themes.theme_list()}:
                return
            themes.modify(lambda cfg: cfg.update(active=str(key)))
        except Exception:
            return
        self._push()

    @Slot(str, str, result=bool)
    def setRoleLive(self, role: str, text: str) -> bool:
        """One live write from the picker/hex field: parse, overlay, persist, push.
        Returns False (and changes nothing) on an unparsable value."""
        from .storage import themes
        role = str(role)
        if role not in themes.ROLES:
            return False
        parsed = themes.parse_color(text)
        if not parsed:
            return False
        def overlay(cfg: dict) -> None:
            ov = dict(cfg["overlays"].get(cfg["active"], {}))
            ov[role] = parsed
            cfg["overlays"][cfg["active"]] = ov
        try:
            themes.modify(overlay)
        except Exception:
            return False
        self._push()
        return True

    @Slot()
    def resetActive(self) -> None:
        """Reset restores the selected theme's defaults (drops its overlay entirely)."""
        from .storage import themes
        try:
            themes.modify(lambda cfg: cfg["overlays"].pop(cfg["active"], None))
        except Exception:
            return
        self._push()

    @Slot()
    def beginEdit(self) -> None:
        from .storage import themes
        cfg = themes.load_config()
        self._edit_key = cfg["active"]
        ov = cfg["overlays"].get(cfg["active"])
        self._edit_snapshot = dict(ov) if isinstance(ov, dict) else None

    @Slot()
    def revertEdit(self) -> None:
        """Esc while the picker is open: back to the exact values at open."""
        from .storage import themes
        if not self._edit_key:
            return
        def revert(cfg: dict) -> None:
            if self._edit_snapshot is None:
                cfg["overlays"].pop(self._edit_key, None)
            else:
                cfg["overlays"][self._edit_key] = dict(self._edit_snapshot)
        try:
            themes.modify(revert)
        except Exception:
            return
        self._edit_snapshot = None
        self._edit_key = ""
        self._push()

    @Slot()
    def endEdit(self) -> None:
        self._edit_snapshot = None
        self._edit_key = ""

    @Slot(result=bool)
    def eyedropperAvailable(self) -> bool:
        return shutil.which("hyprpicker") is not None

    @Slot(result=str)
    def pickScreenColor(self) -> str:
        """Run hyprpicker and return '#RRGGBB' ('' on cancel/failure). Blocks while the
        compositor picker owns the screen - the app is behind it anyway."""
        try:
            r = subprocess.run(["hyprpicker", "--format=hex", "--no-fancy"],
                               capture_output=True, text=True, timeout=60)
            from .storage import themes
            return themes.parse_color(r.stdout.strip()) or ""
        except (OSError, subprocess.SubprocessError):
            return ""


# comm is truncated to 15 chars by the kernel; "linux-wallpaperengine" -> "linux-wallpaper".
_ENGINE_COMM = "linux-wallpaper"


def _first_int(line: str) -> int:
    """First integer token on a /proc status line (e.g. 'VmRSS:\\t  12345 kB'), or 0."""
    for tok in line.split():
        if tok.isdigit():
            return int(tok)
    return 0


def _find_engine_pid() -> int | None:
    """Scan /proc for the engine process by its (15-char-truncated) comm; stdlib only."""
    if _sandboxed():
        return None
    try:
        names = os.listdir("/proc")
    except OSError:
        return None
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/comm", encoding="utf-8", errors="replace") as fh:
                comm = fh.read().strip()
        except OSError:
            continue
        if comm == _ENGINE_COMM:
            try:
                return int(name)
            except ValueError:
                continue
    return None


def _engine_mb_from_proc(pid: int) -> int:
    """Sum VmRSS+VmSwap from /proc/<pid>/status into whole MB (kB/1024). 0 on any error."""
    rss_kb = swap_kb = 0
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    rss_kb = _first_int(line)
                elif line.startswith("VmSwap:"):
                    swap_kb = _first_int(line)
    except OSError:
        return 0
    return (rss_kb + swap_kb) // 1024


