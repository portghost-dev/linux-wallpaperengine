"""DeckPopupBridge - backend for the deck gear's settings popup.

One popup, one wallpaper, and that wallpaper is ALWAYS the engine's current: the surface has
no "open for wallpaper X" mode, so this bridge tracks whatever the status poll reports as
playing and re-seats itself when that changes. It is deliberately not the editor bridge -
it has no Save, and no editor state is read or written.

Three scopes, three write paths:
  * Global capsule (Pause animation, Speed, Volume, FPS) - settings.conf keys, ALL live via
    an api_client verb. The verb runs FIRST and the store is written only when the engine
    confirms, so a value that did not commit is never displayed. Pause animation is not
    handled here at all: it is Backend.setAnimationFrozen, the deck pause button's own
    mechanism, so the two doors share one fact and one state.
  * This-wallpaper Scaling and the scene author's PROP_ properties - wp/<id>.conf keys the
    engine consumes while BUILDING a scene, so they are RE-SHOW class: write the conf, then
    re-show the current wallpaper with its full resolved args, debounced 600 ms trailing so
    rapid edits coalesce into one show. A show carries per-wallpaper speed
    args, so the live speed is captured before it and re-asserted after.

Write model: ONE STORE. Every wallpaper-scoped commit lands in
wp/<id>.conf and nowhere else. The write-through leg that also updated an open draft
is gone with the draft world itself - the editor now runs realtime autosave over this
same conf, so there is no second buffer left to keep in step.

Set-ness is KEY PRESENCE - wp.load_set, not wp.load. Choosing
"Global" in a menu DELETES the key; that is what makes SCALING=default expressible as a real
override, distinct from inheriting.

Marks are the keys changed during THIS play session; the marked set is the revert set.
They clear when the status poll reports a different current wallpaper - rotation advance or
any swap - which is the only mark boundary this surface has, because it only ever shows the
wallpaper that is playing.
"""
from __future__ import annotations

import os
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
from .storage import meta, paths, settings, tier_a, wp

# The engine's own set-fps validation bounds, read from the dispatcher rather than guessed:
# an integer in 1..480, anything else is refused with an error reply
# (linux-wallpaperengine/src/WallpaperEngine/Api/CommandDispatcher.cpp:324-327).
FPS_MIN = 1
FPS_MAX = 480

SPEED_MIN = 0.1
SPEED_MAX = 10.0

# Load defaults strips per-wallpaper CUSTOMIZATION (L10). BG and TYPE are identity, not
# customization - BG is how a preset names the base directory it renders from, so deleting
# it would unmake the wallpaper rather than reset it.
_IDENTITY_KEYS = ("BG", "TYPE")

# Debounce for the re-show class: one trailing timer shared by every key.
_RESHOW_MS = 600


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
    # one or more commits failed: the banner plus a red outline on each named control (L6).
    # Keys are the popup's own control keys: "SCALING", "PROP_<name>", "ENGINE_FPS", ...
    commitFailed = Signal(list)

    def __init__(self, backend: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._wid: str = ""
        self._title: str = ""
        self._type: str = ""
        self._props: list[dict] = []
        # wallpaper-scoped values as they stood when this play session began; a key mapped to
        # None was ABSENT then, so reverting it means deleting it again
        self._snapshot: dict[str, Any] = {}
        self._marks: set[str] = set()
        self._pending: set[str] = set()
        self._reshow = QTimer(self)
        self._reshow.setSingleShot(True)
        self._reshow.setInterval(_RESHOW_MS)
        self._reshow.timeout.connect(self._fire_reshow)

    @Slot(str)
    def syncCurrent(self, wid: str) -> None:
        """Point this bridge at whatever the engine is showing. Called on every status tick.

        A CHANGE of current wallpaper is the play-session boundary: the snapshot re-seats to
        the new wallpaper's stored values and every mark drops. Values persist -
        only the marks and the revert set are session-scoped.
        """
        wid = str(wid or "")
        if wid == self._wid:
            return
        # a re-show still queued for the wallpaper leaving the screen would fight the swap
        self._reshow.stop()
        self._pending.clear()
        self._wid = wid
        self._marks.clear()
        self._props = []
        self._title = ""
        self._type = ""
        self._snapshot = {}
        if wid:
            self._load_identity(wid)
            self._seat_snapshot(wid)
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

    def _seat_snapshot(self, wid: str) -> None:
        try:
            present = wp.load_set(wid)
        except Exception:
            present = {"props": {}}
        snap: dict[str, Any] = {"SCALING": present.get("SCALING") if "SCALING" in present else None}
        for name, val in (present.get("props") or {}).items():
            snap[f"{C.WP_PROP_PREFIX}{name}"] = val
        self._snapshot = snap

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
        """Global timescale. Live via set-speed; the store follows only on the engine's yes.

        The engine is told the EFFECTIVE rate (this wallpaper's conf SPEED times the global
        factor), which is exactly what the next show would send - so the same write also
        updates the rate Backend.setAnimationFrozen restores on resume. One fact, one number.
        """
        try:
            factor = max(SPEED_MIN, min(SPEED_MAX, float(value)))
        except (TypeError, ValueError):
            self.commitFailed.emit(["ENGINE_TIMESCALE"])
            return False
        conf_speed = 1.0
        if self._wid:
            try:
                conf_speed = float(wp.load(self._wid).get("SPEED") or 1.0)
            except Exception:
                conf_speed = 1.0
        if not self._push(api_client.set_speed, "ENGINE_TIMESCALE", conf_speed * factor):
            return False
        if not self._persist_setting("ENGINE_TIMESCALE", factor):
            self.commitFailed.emit(["ENGINE_TIMESCALE"])
            return False
        self.stateChanged.emit()
        return True

    @Slot(result=int)
    def globalVolume(self) -> int:
        try:
            return int(self._setting("ENGINE_VOLUME", 15))
        except (TypeError, ValueError):
            return 15

    @Slot(int, result=bool)
    def setGlobalVolume(self, value: int) -> bool:
        try:
            vol = max(0, min(100, int(value)))
        except (TypeError, ValueError):
            self.commitFailed.emit(["ENGINE_VOLUME"])
            return False
        # sent as-is, not rescaled: every other volume path in this app hands the engine the
        # stored number directly (models.resolve_show_args clamps conf VOLUME to 0..128 and
        # sends it), so rescaling here alone would make one door disagree with the rest
        if not self._push(api_client.set_volume, "ENGINE_VOLUME", vol):
            return False
        if not self._persist_setting("ENGINE_VOLUME", vol):
            self.commitFailed.emit(["ENGINE_VOLUME"])
            return False
        self.stateChanged.emit()
        return True

    @Slot(result=str)
    def globalFps(self) -> str:
        """"" for Auto, else the stored integer as text."""
        raw = self._setting("ENGINE_FPS", "")
        return "" if raw is None or str(raw).strip() == "" else str(raw).strip()

    @Slot(result=int)
    def fpsMin(self) -> int:
        return FPS_MIN

    @Slot(result=int)
    def fpsMax(self) -> int:
        return FPS_MAX

    @Slot(str, result=bool)
    def setGlobalFps(self, text: str) -> bool:
        """Auto (empty) clears the key; anything else must parse as an integer in the engine's band.

        A non-integer is failure grammar, never the silent fall-back-to-empty the editor does.
        """
        s = str(text or "").strip()
        if s == "":
            if not self._persist_setting("ENGINE_FPS", ""):
                self.commitFailed.emit(["ENGINE_FPS"])
                return False
            # nothing to push: an empty cap means "whatever the engine launched with", which
            # is a launch-time value the running engine cannot be talked back into
            self.stateChanged.emit()
            return True
        try:
            n = int(s)
        except (TypeError, ValueError):
            self.commitFailed.emit(["ENGINE_FPS"])
            return False
        if n < FPS_MIN or n > FPS_MAX:
            self.commitFailed.emit(["ENGINE_FPS"])
            return False
        if not self._push(api_client.set_fps, "ENGINE_FPS", n):
            return False
        if not self._persist_setting("ENGINE_FPS", n):
            self.commitFailed.emit(["ENGINE_FPS"])
            return False
        self.stateChanged.emit()
        return True

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
        second write to keep in step.
        """
        try:
            wp.update_set(self._wid, changes)
        except Exception:
            self.commitFailed.emit(sorted(changes))
            return False
        return True

    def _write_wp(self, changes: dict[str, Any]) -> bool:
        """Write wallpaper-scoped keys, mark them, and queue the debounced re-show."""
        if not self._wid:
            return False
        if not self._commit_conf(changes):
            return False
        self._marks.update(changes.keys())
        self._pending.update(changes.keys())
        self._reshow.start()
        self.stateChanged.emit()
        if any(k.startswith(C.WP_PROP_PREFIX) for k in changes):
            self.propsEdited.emit()
        return True

    def _fire_reshow(self) -> None:
        """Apply every coalesced wallpaper-scoped edit with one re-show of the current wallpaper."""
        keys = sorted(self._pending)
        self._pending.clear()
        wid = self._wid
        if not wid:
            return
        # a show carries this wallpaper's own resolved speed args, which would clobber a
        # session state the user set from another door (deck pause, deck speed nudge). Capture
        # the live value first and re-assert it after the swap lands.
        live_speed = None
        try:
            snap = api_client.status()
            if isinstance(snap, dict) and isinstance(snap.get("speed"), (int, float)):
                live_speed = float(snap["speed"])
        except Exception:
            live_speed = None
        try:
            # rotation entries carry their own resolved copy of the conf, so the set has to be
            # re-pushed or the next timed advance would restore the values just replaced
            self._backend._sync_engine()
        except Exception:
            pass
        try:
            ok = bool(self._backend.showNow(wid))
        except Exception:
            ok = False
        if not ok:
            self.commitFailed.emit(keys)
            return
        if live_speed is not None:
            try:
                api_client.set_speed(live_speed)
            except Exception:
                pass

    @Slot(str, result=bool)
    def isMarked(self, key: str) -> bool:
        return str(key or "") in self._marks

    @Slot(result=bool)
    def hasMarks(self) -> bool:
        return bool(self._marks)

    @Slot(result=bool)
    def revertChanges(self) -> bool:
        """Restore every marked key to the value it had when this play session began."""
        if not self._wid or not self._marks:
            return False
        changes = {key: self._snapshot.get(key) for key in self._marks}
        if not self._commit_conf(changes):
            return False
        self._marks.clear()
        self._pending.update(changes.keys())
        self._reshow.start()
        self.stateChanged.emit()
        if any(k.startswith(C.WP_PROP_PREFIX) for k in changes):
            self.propsEdited.emit()
        return True

    @Slot(result=bool)
    def loadDefaults(self) -> bool:
        """Strip every per-wallpaper override and every PROP_ key back to the shipped baseline."""
        if not self._wid:
            return False
        changes: dict[str, Any] = {
            key: None for key in C.WP_SCHEMA if key not in _IDENTITY_KEYS
        }
        try:
            for name in (wp.load_set(self._wid).get("props") or {}):
                changes[f"{C.WP_PROP_PREFIX}{name}"] = None
        except Exception:
            pass
        if not self._commit_conf(changes):
            return False
        self._marks.clear()
        self._pending.update(changes.keys())
        self._reshow.start()
        self.stateChanged.emit()
        self.propsEdited.emit()
        return True

    @Slot(list)
    def reportFailure(self, keys: list) -> None:
        """Raise the failure grammar for a commit QML owns (the Pause animation toggle)."""
        self.commitFailed.emit([str(k) for k in keys])
