"""DeckPopupBridge - backend for the deck gear's settings popup.

One popup, one wallpaper, and that wallpaper is ALWAYS the engine's current: the surface has
no "open for wallpaper X" mode, so this bridge tracks whatever the status poll reports as
playing and re-seats itself when that changes. It is deliberately not the editor bridge -
it has no Save, and no editor state is read or written.

Three scopes, three write paths:
  * Global capsule (Pause animation, Speed, Volume, FPS) - settings.conf keys, ALL live. Each
    is saved FIRST through the change runner, which then sends its verb resolved for the
    wallpaper on screen; a value the engine refused or did not answer stays saved and the
    failure is shown. Pause animation is not handled here at all: it is
    Backend.setAnimationFrozen, the deck pause button's own mechanism, so the two doors share
    one fact and one state.
  * This-wallpaper Scaling and the scene author's PROP_ properties - wp/<id>.conf keys the
    engine consumes while BUILDING a scene, so they are RE-SHOW class: save the conf, then
    re-show the current wallpaper with its full resolved args, debounced 600 ms trailing so
    rapid edits coalesce into one show. The re-show keeps the live speed it reads first.

Write model: ONE STORE. Every wallpaper-scoped commit lands in
wp/<id>.conf and nowhere else. The write-through leg that also updated an open draft
is gone with the draft world itself - the editor now runs realtime autosave over this
same conf, so there is no second buffer left to keep in step.

Set-ness is KEY PRESENCE - wp.load_set, not wp.load. Choosing
"Global" in a menu DELETES the key; that is what makes SCALING=default expressible as a real
override, distinct from inheriting.

Marks are the keys changed during THIS play session; the marked set is the revert set.
Both live in wp_session.SESSION, shared with the editor, so a mark set on either surface
shows on the other and Revert means the same thing from either door. Marks clear when the
status poll reports a different current wallpaper - rotation advance or any swap - which is
the only mark boundary this surface has, because it only ever shows the wallpaper that is
playing.
"""
from __future__ import annotations

import os
from dataclasses import replace
from typing import Any

from PySide6.QtCore import (
    QObject,
    QTimer,
    Signal,
    Slot,
)

from . import api_client
from . import constants as C
from .discovery import project as project_disc
from .discovery import properties as properties_disc
from .engine import push
from .storage import lock, meta, paths, settings, tier_a, wp
from .models import resolve_fit
from .wp_session import SESSION

# The engine's own set-fps validation bounds, read from the dispatcher rather than guessed:
# an integer in 1..480, anything else is refused with an error reply
# (linux-wallpaperengine/src/WallpaperEngine/Api/CommandDispatcher.cpp:324-327).
FPS_MIN = 1
FPS_MAX = 480

SPEED_MIN = 0.1
SPEED_MAX = 10.0

# Debounce for the re-show class: one trailing timer shared by every key.
_RESHOW_MS = 600


def _wp_row(key: str) -> str:
    """The change runner's row for one wallpaper key; every PROP_<name> key is a build key."""
    return push.WP_ROWS.get(key) or ("wp_build" if key.startswith(C.WP_PROP_PREFIX) else "none")


def _wallpapers_dir() -> str:
    try:
        return str(settings.load().get("WALLPAPERS_DIR") or paths.default_wallpapers_dir())
    except Exception:
        return str(paths.default_wallpapers_dir())


def _render_dir(wid: str, wallpapers_dir: str) -> str:
    """The directory the wallpaper actually RENDERS from (a preset renders through its base).

    Deliberately a local eight lines rather than an import of the editor module: this surface
    is specified to stand on storage + discovery only.
    """
    try:
        bg = str(wp.load(wid).get("BG", "") or "")
    except Exception:
        bg = ""
    if bg:
        cand = bg if os.path.isabs(bg) else os.path.join(wallpapers_dir, bg)
        if os.path.isdir(cand):
            return cand
    return os.path.join(wallpapers_dir, wid)


class DeckPopupBridge(QObject):
    """The deck gear popup's model. Follows the engine's current wallpaper; never opened at one."""

    # identity / values / marks moved - QML bumps a rev and re-reads every slot
    stateChanged = Signal()
    # the scene-property SET changed (identity swap, a PROP_ commit, revert, defaults) -
    # the props model re-reads on THIS, not on stateChanged, so a speed/volume/scaling
    # commit no longer rebuilds every property delegate mid-gesture (H28 pattern)
    propsEdited = Signal()
    # one or more commits failed: the banner plus a red outline on each named control.
    # Keys are the popup's own control keys: "SCALING", "PROP_<name>", "ENGINE_FPS", ...
    commitFailed = Signal(list)

    def __init__(self, backend: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._wid: str = ""
        self._title: str = ""
        self._type: str = ""
        self._props: list[dict] = []
        self._pending: set[str] = set()
        self._tickets: list[push.Ticket] = []
        # a slider mid-drag: its steps coalesce here and go to the engine one push per tick
        self._preview = QTimer(self)
        self._preview.setSingleShot(True)
        self._preview.setInterval(33)
        self._preview.timeout.connect(self._fire_preview)
        self._preview_pending: dict[str, float] = {}
        self._reshow = QTimer(self)
        self._reshow.setSingleShot(True)
        self._reshow.setInterval(_RESHOW_MS)
        self._reshow.timeout.connect(self._fire_reshow)

    @Slot(str)
    def syncCurrent(self, wid: str) -> None:
        """Point this bridge at whatever the engine is showing. Called on every status tick.

        A CHANGE of current wallpaper is the play-session boundary: the leaving wallpaper's
        marks drop and the new one is seated in the shared session. Values persist -
        only the marks and the revert set are session-scoped.
        """
        wid = str(wid or "")
        if wid == self._wid:
            return
        # a re-show still queued for the wallpaper leaving the screen would fight the swap
        self._reshow.stop()
        self._pending.clear()
        self._tickets.clear()
        self._hold_delivery(False)
        if self._wid:
            SESSION.clear_marks(self._wid)
        self._wid = wid
        self._props = []
        self._title = ""
        self._type = ""
        if wid:
            self._load_identity(wid)
            # a seat failure is a hard state, raised now rather than at revert time
            if not SESSION.seat(wid):
                self.commitFailed.emit(["SNAPSHOT"])
        self.stateChanged.emit()
        self.propsEdited.emit()

    def _load_identity(self, wid: str) -> None:
        wallpapers_dir = _wallpapers_dir()
        try:
            from .models import _identity_dir
            ident = project_disc.read(_identity_dir(wid, wallpapers_dir))
        except Exception:
            ident = {}
        try:
            mrec = meta.get(wid)
        except Exception:
            mrec = {}
        title = str((mrec or {}).get("title") or "") or str(ident.get("title") or "")
        self._title = title or wid
        self._type = str(ident.get("type") or "")
        # scene properties come from the RENDER dir (a preset's look is the base scene's
        # authored property set), normalized by the discovery layer directly
        try:
            proj = project_disc.read(_render_dir(wid, wallpapers_dir))
            self._props = properties_disc.normalize_all(proj.get("properties"))
        except Exception:
            self._props = []

    @Slot(result=str)
    def currentWid(self) -> str:
        return self._wid

    @Slot(result=str)
    def title(self) -> str:
        return self._title

    @Slot(result=str)
    def wallpaperType(self) -> str:
        return self._type

    @Slot(result=bool)
    def hasWallpaper(self) -> bool:
        return bool(self._wid)

    def _setting(self, key: str, default: Any) -> Any:
        try:
            return settings.load().get(key, default)
        except Exception:
            return default

    def _persist_setting(self, key: str, value: Any) -> bool:
        """Write a global through the Backend so Settings pages and the rotation set follow."""
        try:
            self._backend.setSetting(key, value)
            return True
        except Exception:
            return False

    def _save_global(self, key: str, value: Any) -> bool:
        """Save one global through Backend.save_setting, the change runner. An applied or pending
        change is success; a refused or uncertain one stays saved and reports failure, and a
        refused store write saves nothing."""
        try:
            outcome = self._backend.save_setting(key, value)
        except Exception:
            self.commitFailed.emit([key])
            return False
        if outcome.kind in ("refused", "uncertain"):
            self.commitFailed.emit([key])
            return False
        self.stateChanged.emit()
        return True

    @staticmethod
    def _ok(reply: Any) -> bool:
        return bool(isinstance(reply, dict) and reply.get("ok"))

    @Slot(result=float)
    def globalSpeed(self) -> float:
        try:
            return float(self._setting("ENGINE_TIMESCALE", 1.0) or 1.0)
        except (TypeError, ValueError):
            return 1.0

    @Slot(float, result=bool)
    def setGlobalSpeed(self, value: float) -> bool:
        """Global timescale, saved first; the change runner then sends the rate resolved for the
        wallpaper on screen, its conf SPEED times the global factor, the same number
        Backend.setAnimationFrozen restores on resume. An applied or pending change returns True;
        one the engine refused or did not answer stays saved and reports failure."""
        try:
            factor = max(SPEED_MIN, min(SPEED_MAX, float(value)))
        except (TypeError, ValueError):
            self.commitFailed.emit(["ENGINE_TIMESCALE"])
            return False
        return self._save_global("ENGINE_TIMESCALE", factor)

    @Slot(result=int)
    def globalVolume(self) -> int:
        try:
            return int(self._setting("ENGINE_VOLUME", 15))
        except (TypeError, ValueError):
            return 15

    @Slot(int, result=bool)
    def setGlobalVolume(self, value: int) -> bool:
        """Global volume, saved first; the change runner then sends the volume resolved for the
        wallpaper on screen, its own VOLUME kept and mute giving 0. An applied or pending change
        returns True; one the engine refused or did not answer stays saved and reports failure."""
        try:
            vol = max(0, min(128, int(value)))
        except (TypeError, ValueError):
            self.commitFailed.emit(["ENGINE_VOLUME"])
            return False
        return self._save_global("ENGINE_VOLUME", vol)

    @Slot(result=str)
    def globalFps(self) -> str:
        """The stored cap as text; a missing value reads as the schema default."""
        default = str(C.SETTINGS_SCHEMA["ENGINE_FPS"]["default"])
        raw = self._setting("ENGINE_FPS", default)
        s = "" if raw is None else str(raw).strip()
        return s if s else default

    @Slot(result=int)
    def fpsMin(self) -> int:
        return FPS_MIN

    @Slot(result=int)
    def fpsMax(self) -> int:
        return FPS_MAX

    @Slot(str, result=bool)
    def setGlobalFps(self, text: str) -> bool:
        """The text must parse as an integer in the engine's band; there is no empty state.

        A blank, a non-integer or an out-of-band number is failure grammar, never a
        silent fall-back. A valid cap is saved first, then the change runner sends set-fps;
        an applied or pending change returns True.
        """
        s = str(text or "").strip()
        try:
            n = int(s)
        except (TypeError, ValueError):
            self.commitFailed.emit(["ENGINE_FPS"])
            return False
        if n < FPS_MIN or n > FPS_MAX:
            self.commitFailed.emit(["ENGINE_FPS"])
            return False
        return self._save_global("ENGINE_FPS", n)

    def _push(self, verb: Any, key: str, arg: Any) -> bool:
        """Run one live verb; a dead socket or a refusal is failure grammar, never silence."""
        try:
            if not api_client.available():
                self.commitFailed.emit([key])
                return False
            reply = verb(arg)
        except Exception:
            self.commitFailed.emit([key])
            return False
        if not self._ok(reply):
            self.commitFailed.emit([key])
            return False
        return True

    @Slot(result=str)
    def scalingValue(self) -> str:
        """The stored SCALING, or "" when the key is ABSENT (the row is inheriting)."""
        if not self._wid:
            return ""
        try:
            present = wp.load_set(self._wid)
        except Exception:
            return ""
        return str(present.get("SCALING") or "") if "SCALING" in present else ""

    @Slot(str, result=bool)
    def setScaling(self, value: str) -> bool:
        """"" is the explicit unset (menu entry Global) and DELETES the key; else store it."""
        s = str(value or "").strip()
        if s and s not in C.SCALINGS:
            self.commitFailed.emit(["SCALING"])
            return False
        return self._write_wp({"SCALING": s or None})

    @Slot(str, result=str)
    def fitValue(self, field: str) -> str:
        """The stored fit value for `field` (zoom, pan_x, pan_y), or "" when the key is absent."""
        key = C.FIT_FIELDS.get(str(field or ""))
        if not key or not self._wid:
            return ""
        try:
            present = wp.load_set(self._wid)
        except Exception:
            return ""
        return str(present.get(key)) if key in present else ""

    @Slot(str, str)
    def previewFit(self, field: str, text: str) -> None:
        """A slider mid-drag: show the value live through the wallpaper layer, store untouched.
        Steps coalesce to one push per timer tick; the release commits through setFit, which
        also carries the failure grammar, so a refused preview stays silent."""
        key = C.FIT_FIELDS.get(str(field or ""))
        if not key or not self._wid:
            return
        try:
            v = float(str(text or "").strip())
        except (TypeError, ValueError):
            return
        self._queue_preview(key, v)

    @Slot(str, float)
    def previewLive(self, kind: str, value: float) -> None:
        """The global Speed or Volume slider mid-drag: the verb the release will send, sent now
        with the same resolution, nothing persisted."""
        if str(kind) not in ("speed", "volume"):
            return
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        self._queue_preview(str(kind), v)

    def _queue_preview(self, key: str, v: float) -> None:
        if v != v:
            return
        self._preview_pending[key] = v
        if not self._preview.isActive():
            self._preview.start()

    def _fire_preview(self) -> None:
        pending, self._preview_pending = self._preview_pending, {}
        if not pending:
            return
        try:
            with lock.held("sync", wait_s=0):
                if not api_client.available():
                    return
                conf: dict[str, Any] = {}
                if self._wid:
                    try:
                        conf = dict(wp.load(self._wid))
                    except Exception:
                        conf = {}
                if "speed" in pending:
                    factor = max(SPEED_MIN, min(SPEED_MAX, pending.pop("speed")))
                    api_client.set_speed(C.resolve_speed(wp.set_speed(self._wid) if self._wid else None, factor))
                if "volume" in pending:
                    api_client.set_volume(max(0, min(128, int(round(pending.pop("volume"))))))
                if pending and self._wid:
                    conf.update(pending)
                    api_client.set_fit(layer="wallpaper", id=self._wid, **resolve_fit(conf))
        except Exception:
            pass

    @Slot(str, str, result=bool)
    def setFit(self, field: str, text: str) -> bool:
        """"" deletes the key (identity); else clamp into the schema range and store it."""
        key = C.FIT_FIELDS.get(str(field or ""))
        if not key:
            return False
        s = str(text or "").strip()
        if not s:
            return self._write_wp({key: None})
        try:
            v = float(s)
        except (TypeError, ValueError):
            self.commitFailed.emit([key])
            return False
        if v != v:
            self.commitFailed.emit([key])
            return False
        spec = C.WP_SCHEMA[key]
        return self._write_wp({key: max(float(spec["min"]), min(float(spec["max"]), v))})

    @Slot(result="QVariantList")
    def sceneProperties(self) -> list:
        """The scene author's own properties, in project.json order, with overrides applied."""
        overrides: dict[str, str] = {}
        if self._wid:
            try:
                overrides = wp.load_set(self._wid).get("props") or {}
            except Exception:
                overrides = {}
        out: list[dict] = []
        for entry in self._props:
            name = str(entry.get("name") or "")
            rec: dict[str, Any] = {
                "name": name,
                "key": f"{C.WP_PROP_PREFIX}{name}",
                "kind": str(entry.get("kind") or "text"),
                "label": entry.get("label") or name,
                "value": overrides[name] if name in overrides else entry.get("value"),
                "min": entry.get("min", 0),
                "max": entry.get("max", 100),
                "step": entry.get("step", 1),
                "options": entry.get("options", []),
                "condition": entry.get("condition", {}),
            }
            out.append(rec)
        return out

    @Slot(str, "QVariant", result=bool)
    def setProp(self, name: str, value: Any) -> bool:
        """Set (or, on an empty value, clear) one PROP_<name> override."""
        name = str(name or "")
        if not name:
            return False
        key = f"{C.WP_PROP_PREFIX}{name}"
        # a property name that cannot be a shell key would be warned-and-skipped inside the
        # store, which from here reads as a successful commit that did nothing. Refuse it up
        # front so the control shows the failure instead of appearing to accept the edit.
        if not tier_a.is_valid_key(key):
            self.commitFailed.emit([key])
            return False
        if isinstance(value, bool):
            sval: Any = "true" if value else "false"
        else:
            sval = "" if value is None else str(value)
        return self._write_wp({key: sval or None})

    def _commit_conf(self, changes: dict[str, Any]) -> bool:
        """Write wallpaper-scoped keys to wp/<id>.conf - the single store.

        The editor writes this same file with the same presence-preserving edit, so a popup
        commit is visible there immediately and vice versa. There is no draft buffer and no
        second write to keep in step. The write goes through the change runner: a commit without
        a build key is saved and pushed at once; one with a build key is saved now and delivered
        with its burst after the debounce.
        """
        wid = self._wid
        rows = [(_wp_row(key), key) for key in changes]
        try:
            if any(row == "wp_build" for row, _key in rows):
                self._tickets.append(push.save_change(("overrides",), lambda: wp.update_set(wid, changes),
                                                      rows, wid=wid))
                self._pending.update(key for row, key in rows if row != "none")
                self._hold_delivery(True)
                self._reshow.start()
                return True
            outcome = push.run_change(("overrides",), lambda: wp.update_set(wid, changes), rows, wid=wid,
                                      defer_current=self._backend is not None and self._backend.delivery_due())
        except Exception:
            self.commitFailed.emit(sorted(changes))
            return False
        if outcome.kind in ("refused", "uncertain"):
            self.commitFailed.emit(sorted(changes))
        return True

    def _hold_delivery(self, due: bool) -> None:
        if self._backend is not None:
            self._backend.hold_delivery(self, due)

    def _write_wp(self, changes: dict[str, Any]) -> bool:
        """Write wallpaper-scoped keys and mark them; the write applies them."""
        if not self._wid:
            return False
        if not self._commit_conf(changes):
            return False
        SESSION.mark(self._wid, changes.keys())
        self.stateChanged.emit()
        if any(k.startswith(C.WP_PROP_PREFIX) for k in changes):
            self.propsEdited.emit()
        return True

    def _fire_reshow(self) -> None:
        """Deliver the coalesced build-class edits as one burst: its entry refresh, then one
        re-show of the current wallpaper, freeze kept. The burst carries every change's rows, its
        last change's generation, and the bundle first when push.burst_existed says another change
        may be pending in the marker."""
        keys = sorted(self._pending)
        self._pending.clear()
        tickets, self._tickets = self._tickets, []
        self._hold_delivery(False)
        if not tickets:
            return
        rows = tuple(dict.fromkeys(row for t in tickets for row in t.rows))
        try:
            outcome = push.deliver(replace(tickets[-1], existed=push.burst_existed(tickets), rows=rows))
        except Exception:
            outcome = None
        if outcome is None or outcome.kind in ("refused", "uncertain"):
            self.commitFailed.emit(keys)

    @Slot(str, result=bool)
    def isMarked(self, key: str) -> bool:
        return SESSION.is_marked(self._wid, str(key or ""))

    @Slot(result=bool)
    def hasMarks(self) -> bool:
        return SESSION.has_marks(self._wid)

    @Slot(result=bool)
    def canRevert(self) -> bool:
        return SESSION.can_revert(self._wid)

    @Slot(result=bool)
    def snapshotValid(self) -> bool:
        return SESSION.is_valid(self._wid)

    @Slot(result=bool)
    def revertChanges(self) -> bool:
        """Restore every marked key to the value it had when this play session began."""
        changes = SESSION.revert_changes(self._wid)
        if changes is None:
            # marks without a valid snapshot: a revert that cannot restore must not run
            if self._wid and SESSION.has_marks(self._wid):
                self.commitFailed.emit(["SNAPSHOT"])
            return False
        if not self._commit_conf(changes):
            return False
        SESSION.clear_marks(self._wid)
        self.stateChanged.emit()
        if any(k.startswith(C.WP_PROP_PREFIX) for k in changes):
            self.propsEdited.emit()
        return True

    @Slot(result=bool)
    def loadDefaults(self) -> bool:
        """Strip every per-wallpaper override and every PROP_ key back to the shipped baseline."""
        if not self._wid:
            return False
        changes = SESSION.defaults_changes(self._wid)
        if not self._commit_conf(changes):
            return False
        SESSION.clear_marks(self._wid)
        self.stateChanged.emit()
        self.propsEdited.emit()
        return True

    @Slot(list)
    def reportFailure(self, keys: list) -> None:
        """Raise the failure grammar for a commit QML owns (the Pause animation toggle)."""
        self.commitFailed.emit([str(k) for k in keys])
