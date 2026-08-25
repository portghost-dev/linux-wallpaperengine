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

import os
import shutil
import subprocess
from time import monotonic
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    QSortFilterProxyModel,
    QUrl,
    Property,
    Qt,
    Signal,
    Slot,
)

from . import api_client
from . import constants as C
from .discovery import project
from .storage import atomic, meta, paths, playlists, settings, tags, wp

# Role ids for LibraryModel. Start past Qt.UserRole so they never collide with built-ins.
_ROLE_ID = Qt.ItemDataRole.UserRole + 1
_ROLE_TITLE = Qt.ItemDataRole.UserRole + 2
_ROLE_THUMB = Qt.ItemDataRole.UserRole + 3
_ROLE_IN_PLAYLIST = Qt.ItemDataRole.UserRole + 4
_ROLE_FAVORITE = Qt.ItemDataRole.UserRole + 5
_ROLE_TYPE = Qt.ItemDataRole.UserRole + 6
_ROLE_MISSING = Qt.ItemDataRole.UserRole + 7  # good-but-absent (broken: tagged good, no files)
_ROLE_PENDING_REVIEW = Qt.ItemDataRole.UserRole + 8  # on disk, never classified good/bad


def _wallpapers_dir() -> str:
    """Current WALLPAPERS_DIR from settings (falls back to the resolved default)."""
    try:
        return str(settings.load().get("WALLPAPERS_DIR") or paths.default_wallpapers_dir())
    except Exception:
        return str(paths.default_wallpapers_dir())


def _scan_dir_ids(wallpapers_dir: str) -> list[str]:
    """Immediate subdirectory names of WALLPAPERS_DIR (each is a wallpaper id). Tolerant."""
    out: list[str] = []
    try:
        with os.scandir(wallpapers_dir) as it:
            for entry in it:
                try:
                    # dot-dirs are never wallpapers (.import-<wid> is the importer's
                    # staging area; showing it mid-copy made a half-tree approvable)
                    if entry.is_dir() and not entry.name.startswith("."):
                        out.append(entry.name)
                except OSError:
                    continue
    except (OSError, ValueError):
        return []
    return out


def _identity_dir(wid: str, wallpapers_dir: str) -> str:
    """Where a row's IDENTITY (title, preview, type) is read from - which is NOT always
    where it RENDERS from. A preset (dependency + preset overlay) renders through its
    base via BG, but its title/preview live in its OWN dir; reading identity from BG
    stole the base's name and preview (the reported bug). Order: own library copy, then
    the item's own workshop dir, then BG as the legacy fallback (a plain reference item
    whose own dir IS its render dir)."""
    own = os.path.join(wallpapers_dir, wid)
    if os.path.isdir(own):
        return own
    try:
        ws = str(settings.load().get("WORKSHOP_DIR") or paths.detect_workshop_dir())
        ws_own = os.path.join(ws, wid)
        if os.path.isdir(ws_own):
            return ws_own
    except Exception:
        pass
    try:
        bg = str(wp.load(wid).get("BG", "") or "")
        if bg and os.path.isdir(bg):
            return bg
    except Exception:
        pass
    return own


def _is_present(wid: str, wallpapers_dir: str, dir_ids: set[str]) -> bool:
    """True if the wallpaper's render source exists on disk.

    Most wallpapers render from WALLPAPERS_DIR/<id>/. A bg!=id PRESET (dependency+preset) has no
    dir of its own - its render source is the base's dir, recorded as BG in its committed
    wp/<id>.conf. So presence = a same-named dir OR a committed conf whose BG dir resolves.
    (Pre-migration, before any wp/<id>.conf exists, a preset reads as not-present until migrated.)
    """
    if wid in dir_ids:
        return True
    try:
        cfg = wp.load(wid)
    except Exception:
        return False
    bg = str(cfg.get("BG", "") or "")
    if not bg:
        return False
    cand = bg if os.path.isabs(bg) else os.path.join(wallpapers_dir, bg)
    return os.path.isdir(cand)


def library_ids() -> list[str]:
    """Grid membership: disk presence defines membership. On-disk wallpapers get a card;
    `good`-but-absent ids are surfaced as broken; `bad` ids are excluded even while their
    dir is still on disk (a copy-mode trash deletes the tree asynchronously, and the card
    must leave the grid at trash time, not when the rm finishes). Sorted, deduped."""
    dir_ids = set(_scan_dir_ids(_wallpapers_dir()))
    try:
        good = tags.good_ids()
    except Exception:
        good = set()
    try:
        review = tags.review_ids()
    except Exception:
        review = set()
    try:
        bad = {r["id"] for r in tags.load() if r.get("id") and r.get("state") == "bad"}
    except Exception:
        bad = set()
    # review ids join the grid even without a library dir (a reference-policy import
    # renders from the workshop tree via its wp-conf BG, same as bg!=id presets)
    ids = (dir_ids - bad) | good | review
    ids.discard("")
    return sorted(ids)


def _conf_true(value: Any, default: bool) -> bool:
    """Shell-parity boolean coercion for conf/settings values ('true'/'1'/'yes' family)."""
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def resolve_fullscreen_behavior(s: dict[str, Any], conf: dict[str, Any] | None = None) -> str:
    """The effective fullscreen policy as the engine spells it: off | pause | stop.

    Global FULLSCREEN_BEHAVIOR decides WHAT happens. Empty means the setting predates
    this control, so it is derived from the legacy pause-and-recovery pair - an
    existing install keeps its behavior until the user picks a mode.

    A per-wallpaper FULLSCREEN_PAUSE conf decides WHETHER this wallpaper takes part:
    false forces off, true opts in (pause when the global has nothing to say), "" or
    absent inherits. Passing conf=None asks for the global answer alone, which is what
    the live set-fullscreen push sends.
    """
    behavior = str(s.get("FULLSCREEN_BEHAVIOR") or "").strip().lower()

    if behavior not in C.FULLSCREEN_BEHAVIORS:
        legacy = (str(s.get("PAUSE_RECOVERY_ACTION") or "pause") == "pause"
                  and str(s.get("PAUSE_RECOVERY_CONDITION") or "off") in ("fullscreen", "both"))
        behavior = "pause" if legacy else "off"

    if conf is None:
        return behavior

    raw = conf.get("FULLSCREEN_PAUSE")

    if raw is None or str(raw).strip() == "":
        return behavior

    if not _conf_true(raw, False):
        return "off"

    return behavior if behavior != "off" else "pause"


def resolved_tuning(wid: str) -> dict[str, float]:
    """The three audio dials this wallpaper should run: conf override, else the globals.
    Sent via the existing set-tuning verb after a successful show."""
    out: dict[str, float] = {}
    try:
        present = wp.load_set(wid)
    except Exception:
        present = {}
    s = settings.load()
    for field, wp_key, skey, cal in (
        ("audio_gain", "AUDIO_GAIN", "ENGINE_AUDIO_GAIN", 3.0),
        ("classic_k", "CLASSIC_K", "ENGINE_CLASSIC_K", 0.7),
        ("classic_exp", "CLASSIC_EXP", "ENGINE_CLASSIC_EXP", 2.6),
    ):
        try:
            out[field] = float(present[wp_key]) if wp_key in present else float(s.get(skey, cal))
        except (TypeError, ValueError):
            out[field] = cal
    return out


def resolve_show_args(wid: str) -> tuple[str, dict[str, Any]]:
    """Resolve a wallpaper's FULL per-show vocabulary: conf overrides first, engine-global
    settings fill the gaps, session overrides win last. Returns (engine_wid, kwargs for
    api_client.show).

    Every value is sent RESOLVED - the engine never sees a conf (scope SS4). This is the
    single resolution point: showNow uses it now, the rotation-set push (leg B) reuses
    it for every playlist entry.
    """
    s = settings.load()
    conf: dict[str, Any] = {}
    try:
        conf = wp.load(wid)
    except Exception:
        pass  # unreadable conf must never kill a show; identity is safe (is_safe_wid gated)

    args: dict[str, Any] = {}
    engine_wid = wid

    # color correction: an absent CC is the authored look (derive_cc over the item's own
    # project.json), NOT identity - a published preset's grading IS the wallpaper
    cc = [1.0, 1.0, 1.0, 0.0]
    cc_str = str(conf.get("CC") or "")
    if not cc_str:
        try:
            raw = (project.read(_identity_dir(wid, _wallpapers_dir())) or {}).get("raw")
            if isinstance(raw, dict):
                preset = raw.get("preset")
                cc_str = project.derive_cc(preset if isinstance(preset, dict) else raw)
        except Exception:
            cc_str = ""
    try:
        parts = [float(x) for x in str(cc_str or "1 1 1 0").split()]
        if len(parts) == 4:
            cc = parts
    except (TypeError, ValueError):
        pass
    args["cc"] = cc

    try:
        speed = float(conf.get("SPEED") or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    try:
        speed *= float(s.get("ENGINE_TIMESCALE") or 1.0)
    except (TypeError, ValueError):
        pass
    args["speed"] = speed

    raw_props = conf.get("props")
    if isinstance(raw_props, dict) and raw_props:
        args["properties"] = {str(k): str(v) for k, v in raw_props.items()}

    # presets have no project of their own: conf BG names the base the engine loads
    bg = str(conf.get("BG") or "").strip()
    if bg:
        base = os.path.basename(bg.rstrip("/"))
        if paths.is_safe_wid(base):
            engine_wid = base

    # scaling/clamp: editor-saved confs always carry SCALING (wp.load fills the schema
    # default for the rest), so the conf value wins; the ENGINE_* globals only reach a
    # hand-written conf that clamp-omits. Empty clamp = the engine's launch default.
    args["scaling"] = str(conf.get("SCALING") or s.get("ENGINE_SCALING") or "default")
    clamp = str(conf.get("CLAMPING") or s.get("ENGINE_CLAMP") or "").strip()
    if clamp:
        args["clamp"] = clamp

    try:
        volume_present = "VOLUME" in wp.load_set(wid)
    except Exception:
        volume_present = True
    if volume_present:
        try:
            volume = int(str(conf.get("VOLUME")).strip())
        except (TypeError, ValueError):
            volume = 0
    else:
        # same units and source as the popup's global Volume row (pushed via set_volume)
        try:
            volume = int(str(s.get("ENGINE_VOLUME", 15)).strip())
        except (TypeError, ValueError):
            volume = 15
    if _conf_true(s.get("OVERRIDE_MUTE"), False):
        volume = 0
    args["volume"] = max(0, min(volume, 128))

    audio = _conf_true(conf.get("AUDIO_REACTIVE"), _conf_true(s.get("AUDIO_REACTIVE_DEFAULT"), False))
    if _conf_true(s.get("OVERRIDE_AUDIO_OFF"), False):
        audio = False
    args["audio_processing"] = audio

    mouse = _conf_true(conf.get("MOUSE"), _conf_true(s.get("MOUSE_DEFAULT"), False))
    if _conf_true(s.get("OVERRIDE_MOUSE_OFF"), False):
        mouse = False
    args["mouse"] = mouse

    args["automute"] = _conf_true(conf.get("AUTOMUTE"), _conf_true(s.get("AUTOMUTE_DEFAULT"), True))

    # fullscreen policy, resolved to the engine's three-state vocabulary. The global
    # FULLSCREEN_BEHAVIOR says WHAT happens; the per-wallpaper conf says WHETHER this
    # wallpaper takes part ("" = inherit). A wallpaper that opts in while the global is
    # off still gets the historical meaning of that flag, which is pause.
    args["fullscreen_behavior"] = resolve_fullscreen_behavior(s, conf)
    # leg-A alias, kept so an older engine still reads a truthful boolean off the show
    args["fullscreen_pause"] = args["fullscreen_behavior"] != "off"

    skips = []
    for tok in str(conf.get("SKIP") or "").split():
        try:
            skips.append(int(tok))
        except ValueError:
            continue
    if skips:
        args["skip_objects"] = skips

    return engine_wid, args


class _Row:
    """One library entry: cached id + lazily-resolved project facts + live tag/meta flags."""

    __slots__ = ("id", "title", "thumb", "type", "in_playlist", "favorite", "missing",
                 "pending_review")

    def __init__(self, wid: str) -> None:
        self.id = wid
        self.title = wid
        self.thumb = ""
        self.type = ""
        self.in_playlist = False
        self.favorite = False
        self.missing = False
        self.pending_review = False


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
            row.type = (proj.get("type") if isinstance(proj, dict) else "") or ""
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
    invalidateFilter() and forwards source dataChanged (so toggling a favorite updates the view
    live in favorites mode). Matching is case-insensitive on title+id (lowercased BOTH sides).
    """

    def __init__(self, source: QAbstractListModel, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.setSourceModel(source)
        self.setDynamicSortFilter(True)
        self._search = ""
        self._scope = "all"      # rail: "all" | "favorites" | "review"
        self._type = "all"       # funnel: "all" | "scene" | "video"
        self._pl = "any"         # funnel: "any" | "in" | "out" (active playlist)

    @Slot(str)
    def setSearchText(self, text: str) -> None:
        s = (text or "").strip().casefold()  # casefold (not lower) for correct non-ASCII matching
        if s != self._search:
            self._search = s
            self.invalidateFilter()

    @Slot(str)
    def setScope(self, scope: str) -> None:
        s = scope or "all"
        if s != self._scope:
            self._scope = s
            self.invalidateFilter()

    @Slot(str)
    def setFilterMode(self, mode: str) -> None:
        # legacy name for setScope, kept for existing callers/tests
        self.setScope(mode)

    @Slot(str)
    def setTypeFilter(self, t: str) -> None:
        v = t or "all"
        if v != self._type:
            self._type = v
            self.invalidateFilter()

    @Slot(str)
    def setPlaylistFilter(self, p: str) -> None:
        v = p or "any"
        if v != self._pl:
            self._pl = v
            self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802 (Qt override)
        src = self.sourceModel()
        idx = src.index(row, 0, parent)
        if self._scope == "favorites" and not bool(src.data(idx, _ROLE_FAVORITE)):
            return False
        if self._scope == "review" and not bool(src.data(idx, _ROLE_PENDING_REVIEW)):
            return False
        # Workshop is a funnel (A2): an item lives in exactly one of the two surfaces. Pending
        # (workshop) items show ONLY under the review scope, never in All/favorites.
        if self._scope != "review" and bool(src.data(idx, _ROLE_PENDING_REVIEW)):
            return False
        if self._type != "all" and str(src.data(idx, _ROLE_TYPE) or "") != self._type:
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
    settingsChanged = Signal()
    countChanged = Signal()
    notice = Signal(str)

    playlistsChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        try:
            playlists.ensure_default()
        except Exception:
            pass
        self._model = LibraryModel(self)
        self._model.reload(self._active_members())
        self._model.modelReset.connect(self.countChanged)
        self._filter = LibraryFilterModel(self._model, self)  # the GridView binds to this
        self._cpu_last: tuple | None = None   # (monotonic, ticks, pidset) for the CPU delta
        self._vram_total: int = -1            # cached GPU total MiB (-1 until queried)
        # (engine_wid, ui_wid) of the last API show that the engine accepted. Presets have
        # no project of their own - the engine renders their BASE wallpaper - so status()
        # needs this to translate engine truth back to the tile the user actually clicked.
        # engine pid last seen by status(): a CHANGE means a fresh engine whose
        # in-memory rotation set / ui_id / history are EMPTY (leak-guard recycle,
        # crash restart, cutover idle boot) - the poll re-pushes on transition
        self._engine_pid_seen: int | None = None
        # (monotonic, frames, pid) baseline for the measured frame rate. The engine
        # reports a CUMULATIVE frame count, so a rate needs two samples; the pid is
        # part of the key because a fresh engine restarts the counter at zero.
        self._frames_last: tuple[float, int, Any] | None = None

    def _active_slug(self) -> str:
        try:
            return playlists.active_slug()
        except Exception:
            return ""

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
            engine_wid, show_args = resolve_show_args(wid)
            reply = api_client.show(engine_wid, ui_id=wid, **show_args)
            if reply is not None and reply.get("ok"):
                try:
                    api_client.set_tuning(**resolved_tuning(wid))
                except Exception:
                    pass
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

    def _after_playlist_change(self) -> None:
        self._model.reload(self._active_members())
        self.countChanged.emit()
        self.playlistsChanged.emit()
        try:
            self._sync_engine()
        except Exception:
            pass

    @Slot(str)
    def setActivePlaylist(self, slug: str) -> None:
        try:
            playlists.set_active(slug)
        except Exception:
            return
        # manual switch under an enabled schedule holds until the next boundary (v1.0 5.1)
        try:
            if bool(settings.load().get("SCHEDULE_ENABLED")):
                atomic.atomic_write_text(paths.manual_hold_file(), "held\n")
        except Exception:
            pass
        self._after_playlist_change()

    @Slot(str, result=str)
    def createPlaylist(self, name: str) -> str:
        try:
            slug = playlists.create(name)
            playlists.set_active(slug)
        except Exception:
            return ""
        self._after_playlist_change()
        return slug

    @Slot(str, result=str)
    def saveAsPlaylist(self, name: str) -> str:
        """Fork the active playlist under a new name and switch to it (deck Save as)."""
        cur_slug = self._active_slug()
        try:
            cur = playlists.load(cur_slug) if cur_slug else {}
            slug = playlists.create(name, members=str(cur.get("MEMBERS", "")).split(),
                                    mode=cur.get("MODE", "shuffle"),
                                    interval=int(cur.get("INTERVAL", 900)),
                                    unit=cur.get("UNIT", "min"))
            playlists.set_active(slug)
        except Exception:
            return ""
        self._after_playlist_change()
        return slug

    @Slot(str)
    def renameActivePlaylist(self, name: str) -> None:
        slug = self._active_slug()
        if not slug:
            return
        try:
            playlists.rename(slug, name)
        except Exception:
            return
        self.playlistsChanged.emit()

    @Slot()
    def deleteActivePlaylist(self) -> None:
        slug = self._active_slug()
        if not slug:
            return
        try:
            playlists.delete(slug)  # tombstones + reassigns the active pointer
        except Exception:
            return
        self._after_playlist_change()

    @Slot(str)
    def setPlaylistMode(self, mode: str) -> None:
        slug = self._active_slug()
        if not slug:
            return
        try:
            d = playlists.load(slug)
            d["MODE"] = mode
            playlists.save(slug, d)  # save re-mirrors legacy keys for the active playlist
        except Exception:
            return
        self.playlistsChanged.emit()
        try:
            self._sync_engine()
        except Exception:
            pass

    @Slot(int, str)
    def setPlaylistInterval(self, value: int, unit: str) -> None:
        slug = self._active_slug()
        if not slug:
            return
        seconds = int(value) * (60 if unit == "min" else 1)
        try:
            d = playlists.load(slug)
            d["INTERVAL"] = seconds
            d["UNIT"] = unit if unit in C.PLAYLIST_UNITS else "min"
            playlists.save(slug, d)
        except Exception:
            return
        self.playlistsChanged.emit()
        try:
            self._sync_engine()
        except Exception:
            pass

    def _master_service(self) -> str:
        """The unit the master switch controls. OFF means OFF - full termination."""
        return C.ENGINE_SERVICE

    @Slot(result=str)
    def masterState(self) -> str:
        """systemd unit state for the master service: active/inactive/failed/masked/absent."""
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
        install time (daemon_unit.write_files never enables)."""
        args = ["enable", "--now"] if on else ["disable", "--now"]
        try:
            proc = subprocess.run(
                ["systemctl", "--user", *args, self._master_service()],
                capture_output=True, text=True, timeout=10, check=False)
            self.statusChanged.emit()
            return proc.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _engine_rotation_payload(self) -> tuple[list, int, str, bool, str]:
        """Resolve the ACTIVE playlist into the engine's standing rotation order:
        (entries, interval_s, order, enabled, label). Each entry is a complete
        resolved show-args object - the engine executes, never resolves."""
        slug = self._active_slug()
        d = playlists.load(slug)
        entries = []
        for wid in str(d.get("MEMBERS") or "").split():
            if not paths.is_safe_wid(wid):
                continue
            try:
                engine_wid, args = resolve_show_args(wid)
            except Exception:
                continue  # one broken conf must not sink the whole set
            entries.append({"id": engine_wid, "ui_id": wid, **args})
        mode = str(d.get("MODE") or "shuffle")
        order = mode if mode in ("shuffle", "random", "sequential") else "sequential"
        enabled = (bool(self._setting("ROTATION_ENABLED", True)) and mode != "static"
                   and bool(entries))
        try:
            interval = int(d.get("INTERVAL") or 900)
        except (TypeError, ValueError):
            interval = 900
        label = str(d.get("NAME") or slug)
        return entries, interval, order, enabled, label

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
            self._sync_engine()
        except Exception:
            pass

    def _sync_engine(self) -> None:
        """Push the standing rotation order to the engine (policy push).

        The engine owns scheduled rotation; the panel owns resolution. Never two
        schedulers. Best-effort by design: a dead socket is retried by the status
        poll's pid-change tracker, never surfaced to the caller."""
        try:
            if not api_client.available():
                return
            entries, interval, order, enabled, label = self._engine_rotation_payload()
            api_client.rotate_set(entries, interval, order, enabled, label=label)
        except Exception:
            pass

    @Slot(result=bool)
    def rotateNext(self) -> bool:
        try:
            reply = api_client.next_wallpaper()
            if reply is not None and reply.get("ok"):
                self.statusChanged.emit()
                return True
            # engine said no (empty set before the first sync) -> push and retry
            self._sync_engine()
            reply = api_client.next_wallpaper()
            if reply is not None and reply.get("ok"):
                self.statusChanged.emit()
                return True
        except Exception:
            pass
        return False

    @Slot(result=bool)
    def rotatePrev(self) -> bool:
        try:
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
        try:
            self._sync_engine()
        except Exception:
            pass

    _SESSION_KEYS = {"mute": "OVERRIDE_MUTE", "audio": "OVERRIDE_AUDIO_OFF",
                     "parallax": "OVERRIDE_PARALLAX_OFF", "mouse": "OVERRIDE_MOUSE_OFF"}

    @Slot(str, result=bool)
    def sessionOverride(self, key: str) -> bool:
        skey = self._SESSION_KEYS.get(key)
        return bool(self._setting(skey, False)) if skey else False

    @Slot(str, result=str)
    def overrideReach(self, key: str) -> str:
        """How far a deck override reaches: "live" (the running scene) or "next" (next show).

        Same honesty contract as dev.instrumentReach - a control that looks instant and is
        not is the lying-toggle shape. _LIVE_GLOBAL_KEYS is the single source of truth: a
        key in it gets pushed to the running engine by _push_live_globals, a key outside it
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

    def _current_ui_wid(self) -> str:
        """The ui_id of whatever the daemon is showing right now, "" when idle/down."""
        try:
            api = api_client.status()
            cur = (api or {}).get("current") or {}
            return str(cur.get("ui_id") or "")
        except Exception:
            return ""

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
        """Deck override icons: settings-persisted, reset on app quit."""
        skey = self._SESSION_KEYS.get(key)
        if not skey:
            return
        self._set_setting(skey, bool(on))
        try:
            self._sync_engine()
        except Exception:
            pass

    def restoreSessionOverrides(self) -> None:
        """App quit: clear every session override so nothing outlives the session."""
        try:
            cur = settings.load()
        except Exception:
            return
        changed = False
        for skey in self._SESSION_KEYS.values():
            if cur.get(skey):
                cur[skey] = False
                changed = True
        if changed:
            try:
                settings.save(cur)
                self._sync_engine()
            except Exception:
                pass

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
        try:
            self._sync_engine()
        except Exception:
            pass

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
        self.openPath(str(paths.state_dir()))

    def _ensure_open(self, name: str, header: str) -> None:
        fp = paths.config_dir() / name
        try:
            if not fp.exists():
                fp.write_text(header, encoding="utf-8")
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

    @Slot(str, result=bool)
    def exportConfig(self, dir_url: str) -> bool:
        """Copy the whole config dir into <chosen>/lwe-backup-<ts>/."""
        dest_root = dir_url
        if dest_root.startswith("file://"):
            dest_root = QUrl(dest_root).toLocalFile()
        if not dest_root or not os.path.isdir(dest_root):
            return False
        import time as _time
        dest = os.path.join(dest_root, "lwe-backup-" + _time.strftime("%Y%m%d-%H%M%S"))
        try:
            shutil.copytree(paths.config_dir(), dest)
            return True
        except OSError:
            return False

    @Slot(str, result=bool)
    def importConfig(self, dir_url: str) -> bool:
        """Copy a backup dir's contents over the config dir (files win; nothing deleted)."""
        src = dir_url
        if src.startswith("file://"):
            src = QUrl(src).toLocalFile()
        if not src or not os.path.isfile(os.path.join(src, "settings.conf")):
            return False
        try:
            shutil.copytree(src, paths.config_dir(), dirs_exist_ok=True)
        except OSError:
            return False
        self.refresh()
        self.settingsChanged.emit()
        self.playlistsChanged.emit()
        self.themeRefreshRequested.emit()
        try:
            self._sync_engine()
        except Exception:
            pass
        return True

    #: Keys a Reset must CARRY FORWARD: the five resolved path keys, because a reset
    #: that relocates the library or the workshop root is data loss.
    _RESET_PRESERVED = ("ENGINE_BIN", "ASSETS_DIR",
                        "WALLPAPERS_DIR", "WORKSHOP_DIR", "STEAM_DIR")

    @Slot(result=bool)
    def resetConfig(self) -> bool:
        """Reset settings.conf to defaults (playlists/tags/wp confs are left alone).

        Load current -> build defaults -> carry the preserved set forward -> save.
        """
        try:
            current = settings.load()
        except Exception:
            current = {}
        try:
            fresh = dict(paths.default_settings())
            for key in self._RESET_PRESERVED:
                if key in current:
                    fresh[key] = current[key]
            settings.save(fresh)
        except Exception:
            return False
        self.settingsChanged.emit()
        try:
            self._sync_engine()
        except Exception:
            pass
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
        slug = self._active_slug()
        if not slug:
            return
        try:
            now = playlists.toggle_member(slug, wid)
            if now != on:  # already in the requested state; force it
                playlists.toggle_member(slug, wid)
                now = on
        except Exception:
            return
        self._model.set_in_playlist(wid, now)
        self.countChanged.emit()
        self.playlistsChanged.emit()
        try:
            self._sync_engine()
        except Exception:
            pass

    @Slot(str)
    def approveReview(self, wid: str) -> None:
        """Card approve (Review scope): the item graduates to `good` and enters rotation
        on the next rotation push. The counterpart of the trash's bad-tag."""
        if not wid:
            return
        title = self._model.title_of(wid)
        try:
            tags.set_state(wid, title, "good")
        except Exception:
            return
        self.refresh()
        self.settingsChanged.emit()
        try:
            self._sync_engine()
        except Exception:
            pass

    @Slot(str)
    def trashWallpaper(self, wid: str) -> None:
        """Card trash: tombstone (tags bad) + drop from the active playlist. Restorable by
        re-importing; the files are not deleted."""
        if not wid:
            return
        title = self._model.title_of(wid)
        try:
            tags.set_state(wid, title, "bad")
        except Exception:
            return
        slug = self._active_slug()
        try:
            if slug and wid in playlists.members(slug):
                playlists.toggle_member(slug, wid)
        except Exception:
            pass
        self.refresh()
        self.playlistsChanged.emit()
        try:
            self._sync_engine()
        except Exception:
            pass

    @Slot(str)
    def toggleFavorite(self, wid: str) -> None:
        """Flip meta.favorite for a wallpaper and persist via meta.update."""
        if not wid:
            return
        try:
            cur = bool(meta.get(wid).get("favorite"))
        except Exception:
            cur = False
        new = not cur
        try:
            meta.update(wid, {"favorite": new})
        except Exception:
            return
        self._model.set_favorite(wid, new)

    @Slot(result=str)
    def getOrder(self) -> str:
        return str(self._setting("ORDER", "shuffle"))

    @Slot(str)
    def setOrder(self, value: str) -> None:
        self._set_setting("ORDER", value)

    @Slot(result=int)
    def getInterval(self) -> int:
        try:
            return int(self._setting("INTERVAL", 900))
        except (ValueError, TypeError):
            return 900

    @Slot(int)
    def setInterval(self, value: int) -> None:
        self._set_setting("INTERVAL", int(value))

    @Slot(result=bool)
    def getRotationEnabled(self) -> bool:
        return bool(self._setting("ROTATION_ENABLED", True))

    @Slot(bool)
    def setRotationEnabled(self, value: bool) -> None:
        self._set_setting("ROTATION_ENABLED", bool(value))

    @Slot(result="QStringList")
    def orderOptions(self) -> list[str]:
        return list(C.ORDERS)

    def _setting(self, key: str, default: Any) -> Any:
        try:
            return settings.load().get(key, default)
        except Exception:
            return default

    #: settings whose value feeds resolve_fullscreen_behavior; changing any of them has
    #: to reach the RUNNING scene, not wait for the next swap
    _FULLSCREEN_KEYS = ("FULLSCREEN_BEHAVIOR", "PAUSE_RECOVERY_ACTION", "PAUSE_RECOVERY_CONDITION")

    _LIVE_GLOBAL_KEYS = ("ENGINE_FPS", "PARALLAX_DEFAULT", "PARTICLES_DEFAULT",
                         "OVERRIDE_PARALLAX_OFF", "OVERRIDE_MUTE", "OVERRIDE_MOUSE_OFF",
                         "OVERRIDE_AUDIO_OFF", "ENGINE_TIMESCALE", "ENGINE_VOLUME",
                         "AUDIO_REACTIVE_DEFAULT", "MOUSE_DEFAULT",
                         "APP_CONDITION_BEHAVIOR")

    def _fullscreen_ignore_ids(self) -> list[str]:
        """app_ids exempt from the fullscreen policy, from the pause-blacklist file."""
        try:
            text = (paths.config_dir() / "pause-blacklist.txt").read_text(encoding="utf-8")
        except OSError:
            return []
        out = []
        for line in text.splitlines():
            entry = line.strip()
            if entry and not entry.startswith("#"):
                out.append(entry[:128])
        return out[:128]

    def _app_condition_names(self) -> list[str]:
        """Process names (comm) for the engine's running-apps condition, from the
        hand-edited list file. NOT the fullscreen exceptions list - comm names and
        window app_ids are different identifier spaces and must never merge."""
        try:
            text = (paths.config_dir() / "app-condition.txt").read_text(encoding="utf-8")
        except OSError:
            return []
        out = []
        for line in text.splitlines():
            entry = line.strip()
            if entry and not entry.startswith("#"):
                out.append(entry[:64])
        return out[:128]

    def _push_live_globals(self) -> None:
        """Push the engine-global toggles to the running engine.

        These are NOT per-wallpaper, so they never ride a show and a restarted engine
        knows nothing about them - hence this is also called from the pid-change
        reconnect. Idempotent and best-effort: a dead socket is picked up by the next
        status poll, never surfaced.

        ENGINE_FPS empty means "whatever the engine launched with", so there is nothing
        to push; it takes effect on the next service restart.
        """
        try:
            s = settings.load()
            if not api_client.available():
                return

            fps = str(s.get("ENGINE_FPS") or "").strip()
            if fps:
                try:
                    api_client.set_fps(max(1, min(480, int(fps))))
                except ValueError:
                    pass

            # ENGINE_TIMESCALE is a true global (no per-wallpaper resolve): the popup and
            # the editor have pushed set-speed since they shipped, so a Settings edit that
            # only landed on the next show made one key mean two things.
            # independently tolerant, like every other push in this method: one verb that
            # cannot answer must never cost the rest of the fan-out
            try:
                api_client.set_speed(float(s.get("ENGINE_TIMESCALE") or 1.0))
            except Exception:
                pass

            parallax = _conf_true(s.get("PARALLAX_DEFAULT"), True) and not _conf_true(
                s.get("OVERRIDE_PARALLAX_OFF"), False)
            api_client.set_parallax(parallax)
            api_client.set_particles(_conf_true(s.get("PARTICLES_DEFAULT"), True))
            api_client.set_fullscreen_ignore(self._fullscreen_ignore_ids())
            # a restarted engine restores conditions from its own state file; this push
            # covers the fresh-install boot and any hand-edit of the list file
            api_client.set_app_conditions(
                self._app_condition_names(),
                str(s.get("APP_CONDITION_BEHAVIOR") or "off"))

            # mute + mouse (v1.10 sec 4: set-volume/set-mouse ship). These are NOT globals -
            # the honest live value is the CURRENT wallpaper's resolved one, so it is
            # computed by the same resolve_show_args every show uses. No wallpaper showing
            # (idle daemon) means nothing to retune; the next show carries the override.
            wid = self._current_ui_wid()
            if wid:
                _, args = resolve_show_args(wid)
                if "volume" in args:
                    api_client.set_volume(int(args["volume"]))
                if "mouse" in args:
                    api_client.set_mouse(bool(args["mouse"]))
                if "audio_processing" in args:
                    api_client.set_audio(bool(args["audio_processing"]))
        except Exception:
            pass

    def _push_fullscreen_behavior(self) -> None:
        """Apply the fullscreen policy to the live engine.

        The verb changes the RUNNING scene: turning the mode off un-latches a pause or
        hands the outputs back at once, instead of waiting for the next swap.

        The rotation set also has to be refreshed whenever this changes, because every
        stored entry carries its own resolved copy and the next timed advance would
        otherwise restore the old policy. That is NOT done here: every caller already
        follows a settings write with _sync_engine(), and doing it in both places
        pushed 54 entries twice per change.
        """
        try:
            s = settings.load()
            if not api_client.available():
                return
            api_client.set_fullscreen(resolve_fullscreen_behavior(s))
        except Exception:
            pass

    def _set_setting(self, key: str, value: Any) -> None:
        try:
            cur = settings.load()
        except Exception:
            cur = dict(paths.default_settings())
        cur[key] = value
        try:
            settings.save(cur)
        except Exception:
            return
        self.settingsChanged.emit()
        if key in self._FULLSCREEN_KEYS:
            self._push_fullscreen_behavior()
        if key in self._LIVE_GLOBAL_KEYS:
            self._push_live_globals()

    def _mem_high_mb(self) -> int:
        """The engine unit's MemoryHigh in MB, read from systemd once and cached; -1 when
        unreadable or infinity (the RAM bar then draws empty rather than invent a cap)."""
        cached = getattr(self, "_mem_high_cache", None)
        if cached is not None:
            return cached
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
        """One CPU/GPU/VRAM sample for the header meter cluster.

        cpu: percent of the whole machine across the engine family, delta between
        successive calls (first call and any pid change return 0.0 - a delta needs a
        baseline). gpu: WHOLE-GPU utilization percent (see _gpu_sample - different subject
        from the rest, deliberately). vram: MiB from nvidia-smi's per-process fb column,
        -1 when unavailable. vramTotal: MiB, cached after the first successful query.

        COST: this used to be gated on the header cluster being visible (a peek or pinned
        chips). The meters are always-visible rings now, so it runs for the life of the
        app - hence one combined nvidia-smi fork for gpu+total rather than a separate call
        per figure. Budget: one pgrep pair, one pmon, one query-gpu per sample.
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
        # RAM: resident MB summed across the family - the drawn fraction runs against
        # MemoryHigh, whose cgroup covers exactly these processes
        try:
            page_kb = os.sysconf("SC_PAGE_SIZE") // 1024
            rss_kb = 0
            for pid in pids:
                with open(f"/proc/{pid}/statm", "r", encoding="utf-8") as fh:
                    rss_kb += int(fh.read().split()[1]) * page_kb
            out["rss"] = rss_kb // 1024
        except (OSError, ValueError, IndexError):
            pass
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
                if pid != self._engine_pid_seen:
                    first_sight = self._engine_pid_seen is None
                    self._engine_pid_seen = pid
                    if first_sight:
                        # PANEL start: push the panel's stored policy once, so opening
                        # the panel heals a drifted or state-lost engine. Engine
                        # RE-arrivals get nothing: the engine restores its own state
                        # on boot, and an automatic re-push (or re-show) here would
                        # defeat its crash-loop guard by feeding the loop.
                        self._sync_engine()
                        self._push_fullscreen_behavior()
                        self._push_live_globals()

                # the fullscreen ignore-list is a FILE the user edits in their own
                # editor, so there is no save hook to hang a push on. Watch its
                # mtime here instead: an external edit lands within one poll.
                try:
                    bl = paths.config_dir() / "pause-blacklist.txt"
                    stamp = bl.stat().st_mtime_ns if bl.exists() else 0
                    if stamp != getattr(self, "_blacklist_stamp", None):
                        if getattr(self, "_blacklist_stamp", None) is not None:
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
                    result["interval"] = int(rot.get("interval_s") or 0)
                    result["playlist"] = str(rot.get("label") or "")
                    result["next_up"] = str(rot.get("next_up") or "")

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
        import threading

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
                self._sync_engine()
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

        import threading

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
                self._sync_engine()
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
            cfg = themes.load_config()
            cfg["active"] = str(key)
            themes.save_config(cfg)
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
        try:
            cfg = themes.load_config()
            ov = dict(cfg["overlays"].get(cfg["active"], {}))
            ov[role] = parsed
            cfg["overlays"][cfg["active"]] = ov
            themes.save_config(cfg)
        except Exception:
            return False
        self._push()
        return True

    @Slot()
    def resetActive(self) -> None:
        """Reset restores the selected theme's defaults (drops its overlay entirely)."""
        from .storage import themes
        try:
            cfg = themes.load_config()
            cfg["overlays"].pop(cfg["active"], None)
            themes.save_config(cfg)
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
        try:
            cfg = themes.load_config()
            if self._edit_snapshot is None:
                cfg["overlays"].pop(self._edit_key, None)
            else:
                cfg["overlays"][self._edit_key] = dict(self._edit_snapshot)
            themes.save_config(cfg)
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


