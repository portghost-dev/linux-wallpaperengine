"""EditorBridge - the per-wallpaper editor backend.

A single QObject exposed to QML as the `editor` context property (app.py registers it). It is
the backing model for the editor center takeover: it loads one wallpaper for editing, exposes
the header/control read properties, the GENERATED Objects + Scene-properties regions, and every
commit path of the three workspaces.

REALTIME AUTOSAVE. There is no draft, no Save verb, no dirty state and no
exit-discard on this surface. Every control commits its value on its own commit gesture,
straight into `wp/<id>.conf` through the presence-preserving writer wp.update_set - the SAME
store the deck gear popup writes, so an edit made on either surface is visible on the other
immediately. The old draft/Save world is gone whole, along with storage/draft.py.

SET-NESS IS KEY PRESENCE. wp.load materializes every schema key to its default, so
it cannot tell "explicitly set to the default" from "inheriting" - which is what made
SCALING=default, VOLUME=0 and AUDIO_REACTIVE=false inexpressible as overrides. The override
grammar reads wp.load_set and writes wp.update_set, where choosing the `Global` menu entry
DELETES the key.

APPLY MECHANICS. Keys the realtime API can set live are pushed live; keys the engine
consumes while BUILDING a scene auto-apply through one re-show of the current wallpaper,
debounced 600 ms trailing so rapid edits coalesce. The user is never asked to reload.
  live:      SPEED, VOLUME, AUDIO_REACTIVE, MOUSE, SKIP
  relaunch:  SCALING, AUTOMUTE, CC, every PROP_<name>
SCOPE GATE: the editor can be open on a wallpaper that is NOT playing, and every engine verb
is engine-global - it would retune whatever is on screen. So a live push (and the re-show)
fires only when the edited wid is the wid the engine is currently showing; otherwise the
commit is conf-write only and no verb is sent.

MARKS + REVERT live in wp_session.SESSION, shared with every other editing
surface: a control changed this session wears the mark, the marked set IS the revert set, and
marks clear on assent (close, or switching to another wallpaper) while values persist.

FAILURE GRAMMAR. Every commit path that can fail emits commitFailed with the control
keys involved; QML draws the banner plus a red outline for 2500 ms and re-reads store truth.
Silent-failure paths are illegal on this surface.

Metadata (title / tags / favorite) is library bookkeeping, not a render override: it is written
straight to meta.json / tags.csv and is outside both the marked set and the revert set.

Render-dir resolution (presets render from a base's dir, not their own id):
    render_dir = wp.load(wid)["BG"] if that resolves to an existing directory
               else WALLPAPERS_DIR/<wid>
project.read(render_dir) supplies title/type/preview + the property defs; objects are extracted
from the same dir. Object / property indices are (re)built on demand and cached to state keyed
by `wid` via the discovery build-index helpers where the render dir == WALLPAPERS_DIR/<wid>;
for a preset (render dir != that) the cache is written directly so it stays keyed by the
editing id.
"""
from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    Property,
    QObject,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)

from . import api_client
from . import constants as C
from .discovery import objects as objects_disc
from .discovery import project as project_disc
from .discovery import properties as properties_disc
from .storage import atomic, meta, paths, settings, tier_a, wp
from .wp_session import SESSION

# The engine's own set-fps validation bounds, read from the dispatcher rather than guessed:
# an integer in 1..480, anything else is refused with an error reply.
FPS_MIN = 1
FPS_MAX = 480

# Global Speed band - the same four-zone face the popup carries (L8/L-12). The bridge owns
# the endpoints and the clamp; the zone mapping is the control's.
SPEED_MIN = 0.1
SPEED_MAX = 10.0

GAIN_MIN = 0.1
GAIN_MAX = 20.0

# The three drawn Audio response dials, each mapped to the real engine
# constant behind it. Ranges and defaults are the measured inventory [R-E1]; `calibrated` is
# the value this install actually runs when status cannot be read.
#   log=True - the range spans decades, so travel is log-smooth (a linear slider would park
#               classic_k's real 0.7 inside the first 0.07% of the track)
#   invert=True - the dial is the INVERSE of the quality its label names, so dragging right
#               increases the label and decreases the dial
AUDIO_DIALS: dict[str, dict] = {
    "RESPONSE_THRESHOLD": {
        "label": "Response threshold", "field": "audio_gain",
        "lo": 0.1, "hi": 20.0, "calibrated": 3.0, "log": True, "invert": True,
    },
    "GLOW_INTENSITY": {
        "label": "Glow intensity", "field": "classic_k",
        "lo": 0.01, "hi": 1000.0, "calibrated": 0.7, "log": True, "invert": True,
    },
    "GLOW_RADIUS": {
        "label": "Glow radius", "field": "classic_exp",
        "lo": 0.5, "hi": 6.0, "calibrated": 2.6, "log": False, "invert": True,
    },
}

# Debounce for the relaunch class (spec L2): one trailing timer shared by every key.
_RESHOW_MS = 600

# Wallpaper-scoped keys the realtime API can set on the running scene without a rebuild.
_LIVE_WP_KEYS = ("SPEED", "VOLUME", "AUDIO_REACTIVE", "MOUSE", "SKIP",
                 "AUDIO_GAIN", "CLASSIC_K", "CLASSIC_EXP")

# per-wallpaper conf key -> the engine tuning field it resolves to
_WP_DIAL_KEYS = {"audio_gain": "AUDIO_GAIN", "classic_k": "CLASSIC_K", "classic_exp": "CLASSIC_EXP"}


def _wallpapers_dir() -> str:
    """Current WALLPAPERS_DIR from settings (falls back to the resolved default). Tolerant."""
    try:
        return str(settings.load().get("WALLPAPERS_DIR") or paths.default_wallpapers_dir())
    except Exception:
        return str(paths.default_wallpapers_dir())


def resolve_render_dir(wid: str, wallpapers_dir: str) -> str:
    """The directory the wallpaper actually renders from.

    A preset (dependency + preset) carries no dir of its own; its committed wp/<id>.conf BG
    points at the base wallpaper's dir. So: BG-as-dir if it resolves, else WALLPAPERS_DIR/<wid>.
    """
    try:
        cfg = wp.load(wid)
    except Exception:
        cfg = {}
    bg = str(cfg.get("BG", "") or "")
    if bg:
        cand = bg if os.path.isabs(bg) else os.path.join(wallpapers_dir, bg)
        if os.path.isdir(cand):
            return cand
    return os.path.join(wallpapers_dir, wid)


class ScenePropertyModel(QAbstractListModel):
    """A STABLE model for the Scene Properties workspace.

    A plain `Repeater { model: editor.sceneProperties() }` hands QML a fresh array on every
    read, so the binding's value changes identity on every commit and QML destroys and
    recreates EVERY delegate - including the one the user is mid-gesture in. That loses focus
    and any inline-editor state on the very row being edited: you type into a text property,
    the commit lands, and the field you are standing in is replaced under the cursor.

    So the property SET and a property VALUE are different events here:
      * the set changes on an identity swap, a revert or a defaults strip -> reset(), and
        rebuilding every delegate is correct because they are different properties;
      * a value changes on a commit -> update_value(), which emits dataChanged for that ONE
        row and leaves every delegate, and the focus in it, exactly where it was.
    """

    NameRole = Qt.ItemDataRole.UserRole + 1
    KeyRole = Qt.ItemDataRole.UserRole + 2
    KindRole = Qt.ItemDataRole.UserRole + 3
    LabelRole = Qt.ItemDataRole.UserRole + 4
    ValueRole = Qt.ItemDataRole.UserRole + 5
    MinRole = Qt.ItemDataRole.UserRole + 6
    MaxRole = Qt.ItemDataRole.UserRole + 7
    StepRole = Qt.ItemDataRole.UserRole + 8
    OptionsRole = Qt.ItemDataRole.UserRole + 9
    ConditionRole = Qt.ItemDataRole.UserRole + 10

    _ROLE_KEYS = {
        NameRole: "name",
        KeyRole: "ckey",
        KindRole: "kind",
        LabelRole: "label",
        ValueRole: "value",
        MinRole: "pmin",
        MaxRole: "pmax",
        StepRole: "pstep",
        OptionsRole: "options",
        ConditionRole: "condition",
    }
    # the dict key each role reads out of a row record (the QML role NAME differs for the
    # numeric three, because `min`/`max` collide with JS globals inside a delegate scope)
    _RECORD_KEYS = {
        NameRole: "name", KeyRole: "key", KindRole: "kind", LabelRole: "label",
        ValueRole: "value", MinRole: "min", MaxRole: "max", StepRole: "step",
        OptionsRole: "options", ConditionRole: "condition",
    }

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []

    def roleNames(self) -> dict:
        return {role: QByteArray(name.encode()) for role, name in self._ROLE_KEYS.items()}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        key = self._RECORD_KEYS.get(role)
        return self._rows[index.row()].get(key) if key else None

    def reset(self, rows: list[dict]) -> None:
        """The property SET changed: rebuild. Only ever called on a swap/revert/defaults."""
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def update_value(self, name: str, value: Any) -> bool:
        """One property's VALUE changed: touch that row alone, keep every delegate alive."""
        name = str(name or "")
        for i, row in enumerate(self._rows):
            if row.get("name") == name:
                if row.get("value") == value:
                    return True
                row["value"] = value
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [self.ValueRole])
                return True
        return False

    def rows(self) -> list[dict]:
        """The whole record list - the condition evaluator's lookup table, not a model read."""
        return list(self._rows)


class EditorBridge(QObject):
    """The per-wallpaper editor model. One id loaded at a time; reload via open()."""

    # ---- the loaded() three-way split -------------------------------------------
    # `loaded` carried two disagreeing jobs: NOTIFY for the read-only properties, and a
    # "new wallpaper is on screen" broadcast three QML surfaces used to reset navigation
    # state. Only the identity changes are navigation resets, so they get their own signal;
    # `loaded` stays the property NOTIFY and fires from all three (property NOTIFY
    # over-firing is cheap, navigation-reset over-firing loses the user's filter and search).
    loaded = Signal()
    wallpaperChanged = Signal()      # identity swap: reset every per-workspace navigation state
    valuesRefreshed = Signal()       # values moved under the controls: re-seed them
    metadataChanged = Signal()       # title / tags / favorite only; nothing else listens

    edited = Signal()                # a value changed (fires on every commit)
    # DOMAIN-SCOPED companions to `edited` (H28/E14). `edited` fires on EVERY edit, and the
    # two big list bindings (column 1's sceneProperties(), column 2's objectList()) were hung
    # off it - so a commit on any unrelated key destroyed and recreated every delegate in
    # both columns. These fire only when THAT list's content can actually have changed.
    propsEdited = Signal()           # a PROP_<name> scene-property value changed
    objectsEdited = Signal()         # the SKIP set changed (object enable/disable)

    # one or more commits failed: the banner plus a red outline on each named control (L6).
    # Keys are the surface's own control keys: "SCALING", "PROP_<name>", "ENGINE_FPS", ...
    commitFailed = Signal(list)

    def __init__(self, backend: Any = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._wid: str = ""
        self._playing: str = ""                 # the wid the engine is showing (scope gate)
        self._render_dir: str = ""
        self._proj: dict[str, Any] = {}
        self._ident: dict[str, Any] = {}        # identity source (own dir; != render for presets)
        self._wp: dict[str, Any] = {}           # in-memory READ CACHE, re-seeded on every commit
        self._present: dict[str, Any] = {}      # presence-aware view of the same conf (L4)
        self._objects: list[dict] = []          # objindex shape: [{objid,name,type}]
        self._props: list[dict] = []            # propindex shape: [{name,kind,label,value,...}]
        self._meta: dict[str, Any] = {}
        self._props_readable: bool = True       # False = project.json unreadable, not "no props"
        # the STABLE model column 1 binds to. The bridge owns it for the bridge's
        # whole life, so QML's binding never changes identity and delegates are only ever
        # destroyed when the property SET genuinely changed.
        self._prop_model = ScenePropertyModel(self)
        self._pending: set[str] = set()         # relaunch-class keys waiting on the debounce
        self._reshow = QTimer(self)
        self._reshow.setSingleShot(True)
        self._reshow.setInterval(_RESHOW_MS)
        self._reshow.timeout.connect(self._fire_reshow)

    @Slot(str)
    def open(self, wid: str) -> None:
        """Load `wid` for editing and seat its editing session.

        Switching wallpapers without closing is an ASSENT boundary: the departed
        wid's marks clear, its values stay committed, and the arriving wid seats its own
        snapshot - or reuses the one the popup already seated for it this play session, so
        opening the editor on a wallpaper the popup just tuned does not move the revert
        target forward onto those edits.
        """
        wid = str(wid or "")
        if self._wid and self._wid != wid:
            SESSION.clear_marks(self._wid)
        self._wid = wid
        self._pending.clear()
        self._reshow.stop()
        if not wid:
            self._render_dir = ""
            self._proj = {}
            self._ident = {}
            self._wp = {}
            self._present = {}
            self._objects = []
            self._props = []
            self._meta = {}
            self._props_readable = True
            self._prop_model.reset([])
            self.wallpaperChanged.emit()
            self.loaded.emit()
            return

        wallpapers_dir = _wallpapers_dir()
        self._render_dir = resolve_render_dir(wid, wallpapers_dir)

        try:
            self._proj = project_disc.read(self._render_dir)
            self._props_readable = True
        except Exception:
            self._proj = {}
            # an unreadable project.json renders as "a scene with no properties", which is
            # indistinguishable from a bare scene - so say so instead
            self._props_readable = False
            self.commitFailed.emit(["SCENE_PROPERTIES"])
        # IDENTITY (title/preview/type) reads from the item's OWN dir, which for a
        # preset differs from the render dir (BG = the base). Reading identity off the
        # render dir showed the base's name/preview (B11). Objects + scene properties
        # still come from the render dir (the base scene is what actually renders).
        try:
            from .models import _identity_dir
            self._ident = project_disc.read(_identity_dir(wid, wallpapers_dir))
        except Exception:
            self._ident = self._proj
        self._reload_conf()
        try:
            self._meta = meta.get(wid)
        except Exception:
            self._meta = {}

        self._objects = self._build_objects(wid, wallpapers_dir)
        self._props = self._build_props(wid, wallpapers_dir)
        self._prop_model.reset(self.sceneProperties())

        # seat the revert target. A seat failure is a HARD state, surfaced NOW rather than
        # discovered at revert time when it would have deleted the user's overrides (F16).
        if not SESSION.seat(wid):
            self.commitFailed.emit(["SNAPSHOT"])

        self.wallpaperChanged.emit()
        self.loaded.emit()

    @Slot()
    def closeEditor(self) -> None:
        """Assent on leaving the surface: marks clear, every value persists.

        Nothing is confirmed and nothing is discarded - under L1 it was all committed as it
        was typed. This exists so close/Esc/rail-navigation have one door instead of three
        that each had to remember to discard.
        """
        if self._wid:
            SESSION.clear_marks(self._wid)
        self.valuesRefreshed.emit()
        self.loaded.emit()

    @Slot(str)
    def syncCurrent(self, wid: str) -> None:
        """Tell the bridge which wallpaper the ENGINE is showing (the scope-gate input).

        Fed from the same 2 s status snapshot the deck and header already read. Identity
        only: this never loads or seats anything, it just decides whether a commit is
        allowed to talk to the engine.
        """
        self._playing = str(wid or "")

    def _is_current(self) -> bool:
        """Scope gate: may this commit reach the engine at all?"""
        return bool(self._wid) and self._wid == self._playing

    def _reload_conf(self) -> None:
        """Re-seed both conf views from disk (`_wp` is a read cache, not a buffer)."""
        try:
            self._wp = wp.load(self._wid)
        except Exception:
            self._wp = {}
        try:
            self._present = wp.load_set(self._wid)
        except Exception:
            self._present = {}

    def _build_objects(self, wid: str, wallpapers_dir: str) -> list[dict]:
        """Extract typed objects from the render dir; cache to objindex keyed by `wid`.

        When the render dir is the canonical WALLPAPERS_DIR/<wid>, reuse the discovery
        build_index helper (keyed by wid). For a preset (render dir != that) build directly so
        the cache stays keyed by the EDITING id, not the base's id.
        """
        canonical = os.path.join(wallpapers_dir, wid)
        try:
            if os.path.normpath(self._render_dir) == os.path.normpath(canonical):
                return list(objects_disc.build_index(wid, wallpapers_dir).get("objects", []))
            objs = objects_disc.extract(self._render_dir)
            try:
                atomic.atomic_write_json(paths.objindex_file(wid), {"objects": objs})
            except Exception:
                pass
            return objs
        except Exception:
            try:
                return objects_disc.extract(self._render_dir)
            except Exception:
                return []

    def _build_props(self, wid: str, wallpapers_dir: str) -> list[dict]:
        """Normalize the render dir's project.json properties to propindex entries; cache by wid."""
        canonical = os.path.join(wallpapers_dir, wid)
        try:
            if os.path.normpath(self._render_dir) == os.path.normpath(canonical):
                return list(properties_disc.build_index(wid, wallpapers_dir).get("properties", []))
            entries = self._normalize_props(self._proj.get("properties"))
            try:
                atomic.atomic_write_json(paths.propindex_file(wid), {"properties": entries})
            except Exception:
                pass
            return entries
        except Exception:
            return self._normalize_props(self._proj.get("properties"))

    @staticmethod
    def _normalize_props(properties: object) -> list[dict]:
        """Run properties._normalize over a project.json `general.properties` dict."""
        entries: list[dict] = []
        if isinstance(properties, dict):
            for name, spec in properties.items():
                if not isinstance(spec, dict):
                    continue
                entry = properties_disc._normalize(name, spec)
                if entry is not None:
                    entries.append(entry)
        return entries

    # ----------------------------------------------------------------------------------
    # read-only header / control-initial-value properties (NOTIFY loaded)
    # ----------------------------------------------------------------------------------
    def _wp_get(self, key: str) -> Any:
        if key in self._wp:
            return self._wp[key]
        spec = C.WP_SCHEMA.get(key, {})
        return spec.get("default", "")

    def _get_wallpaper_id(self) -> str:
        return self._wid

    def _get_title(self) -> str:
        # a user title override in meta wins over the project.json title
        mt = self._meta.get("title") if isinstance(self._meta, dict) else ""
        if isinstance(mt, str) and mt.strip():
            return mt
        title = self._ident.get("title") if isinstance(self._ident, dict) else ""
        return title or self._wid

    def _get_type(self) -> str:
        # project.json type is the GROUND TRUTH for the badge. wp.TYPE can't be primary: storage
        # always materializes it to its 'scene' default, so it would mask a video's real type.
        ptype = str(self._ident.get("type") or "") if isinstance(self._ident, dict) else ""
        if ptype:
            return ptype
        return str(self._wp_get("TYPE") or "") or "scene"

    def _get_preview_url(self) -> str:
        prev = self._ident.get("preview") if isinstance(self._ident, dict) else ""
        if prev and os.path.isfile(prev):
            return QUrl.fromLocalFile(prev).toString()
        return ""

    def _get_resolution(self) -> str:
        # resolution is DISPLAY from meta.resolution; "" -> QML hides the line.
        res = self._meta.get("resolution") if isinstance(self._meta, dict) else ""
        return str(res) if res else ""

    def _get_scaling(self) -> str:
        return str(self._wp_get("SCALING") or "default")

    def _get_speed(self) -> str:
        return str(self._wp_get("SPEED"))

    def _get_cc(self) -> str:
        return str(self._wp_get("CC") or "1 1 1 0")

    def _get_volume(self) -> int:
        try:
            return int(self._wp_get("VOLUME"))
        except (TypeError, ValueError):
            return 0

    def _get_clamping(self) -> str:
        return str(self._wp_get("CLAMPING") or "")

    def _get_automute(self) -> bool:
        return _as_bool(self._wp_get("AUTOMUTE"), default=True)

    def _get_audio_reactive(self) -> bool:
        return _as_bool(self._wp_get("AUDIO_REACTIVE"), default=False)

    def _get_mouse(self) -> bool:
        return _as_bool(self._wp_get("MOUSE"), default=False)

    def _get_monitors(self) -> str:
        return str(self._wp_get("MONITORS") or "all")

    def _get_favorite(self) -> bool:
        return bool(self._meta.get("favorite")) if isinstance(self._meta, dict) else False

    wallpaperId = Property(str, _get_wallpaper_id, notify=loaded)
    title = Property(str, _get_title, notify=loaded)
    type = Property(str, _get_type, notify=loaded)
    previewUrl = Property(str, _get_preview_url, notify=loaded)
    resolution = Property(str, _get_resolution, notify=loaded)
    scaling = Property(str, _get_scaling, notify=loaded)
    speed = Property(str, _get_speed, notify=loaded)
    cc = Property(str, _get_cc, notify=loaded)
    volume = Property(int, _get_volume, notify=loaded)
    clamping = Property(str, _get_clamping, notify=loaded)
    automute = Property(bool, _get_automute, notify=loaded)
    audioReactive = Property(bool, _get_audio_reactive, notify=loaded)
    mouse = Property(bool, _get_mouse, notify=loaded)
    monitors = Property(str, _get_monitors, notify=loaded)
    favorite = Property(bool, _get_favorite, notify=loaded)

    # ----------------------------------------------------------------------------------
    # the commit core - write store -> mark -> apply -> failure grammar
    # ----------------------------------------------------------------------------------
    def _commit_conf(self, changes: dict[str, Any]) -> bool:
        """Write wallpaper-scoped keys to wp/<id>.conf. ONE store, no second buffer."""
        try:
            wp.update_set(self._wid, changes)
        except Exception:
            self.commitFailed.emit(sorted(changes))
            return False
        return True

    def _persist_draft(self, domain: str = "", changes: dict[str, Any] | None = None) -> bool:
        """THE live commit path (rewritten in place, name and fan-out preserved).

        Was the sticky-draft writer; is now: write the conf, mark the keys, re-seed the read
        cache, apply per L2, and fan out the domain-scoped signals. `domain` still names WHICH
        list this edit can have changed - "props" for a PROP_<name> write, "objects" for a SKIP
        write, "" for everything else - and `edited` still fires unconditionally, so no existing
        consumer changed behavior when the draft under it went away.

        `changes` is the presence-preserving key edit (None DELETES the key). When it is
        omitted the caller has already written the store itself.
        """
        if not self._wid:
            return False
        if changes:
            if not self._commit_conf(changes):
                return False
            SESSION.mark(self._wid, changes.keys())
            self._reload_conf()
            self._apply(changes)
        self.edited.emit()
        if domain == "props":
            self.propsEdited.emit()
        elif domain == "objects":
            self.objectsEdited.emit()
        return True

    def _set_key(self, key: str, value: Any) -> bool:
        """Commit one wallpaper-scoped key. `value` None deletes it (the `Global` entry)."""
        domain = ("objects" if key == "SKIP"
                  else "props" if key.startswith(C.WP_PROP_PREFIX) else "")
        ok = self._persist_draft(domain, {key: value})
        if ok and domain == "props":
            # ONE row moved, so touch one row. A model reset here would destroy the delegate
            # the user is standing in.
            name = key[len(C.WP_PROP_PREFIX):]
            resolved = value
            if value is None:
                for entry in self._props:
                    if str(entry.get("name") or "") == name:
                        resolved = entry.get("value")
                        break
            self._prop_model.update_value(name, resolved)
        return ok

    def _apply(self, changes: dict[str, Any]) -> None:
        """Route each committed key to its apply mechanism, subject to the scope gate.

        An editor open on a wallpaper that is not playing writes the conf and sends NOTHING:
        every engine verb is engine-global and would retune whatever is on screen instead.
        """
        if not self._is_current():
            return
        relaunch: list[str] = []
        for key in changes:
            if key in _LIVE_WP_KEYS:
                self._push_live(key)
            elif key in C.WP_SCHEMA or key.startswith(C.WP_PROP_PREFIX):
                # CC_MODE is a remembered-mode cache the show path never reads - the numbers
                # in CC are what render, so it needs no apply of its own.
                if key != "CC_MODE":
                    relaunch.append(key)
        if relaunch:
            self._pending.update(relaunch)
            self._reshow.start()

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
        if not (isinstance(reply, dict) and reply.get("ok")):
            self.commitFailed.emit([key])
            return False
        return True

    def _push_live(self, key: str) -> bool:
        """Push one live-class wallpaper key with its RESOLVED value."""
        if key == "SPEED":
            return self._push(api_client.set_speed, key, self._resolved_speed())
        if key == "VOLUME":
            return self._push(api_client.set_volume, key, self._resolved_volume())
        if key == "AUDIO_REACTIVE":
            return self._push(api_client.set_audio, key,
                              _as_bool(self._wp_get("AUDIO_REACTIVE"), default=False))
        if key == "MOUSE":
            return self._push(api_client.set_mouse, key,
                              _as_bool(self._wp_get("MOUSE"), default=False))
        if key == "SKIP":
            return self._push(api_client.set_skip, key, self._skip_ids())
        for field, wp_key in _WP_DIAL_KEYS.items():
            if key == wp_key:
                return self._push(lambda d: api_client.set_tuning(**d), key,
                                  {field: self._resolved_dial(field)})
        return True

    def _resolved_dial(self, field: str) -> float:
        """One tuning dial as the engine should run it: conf override, else the global."""
        spec = next(s for s in AUDIO_DIALS.values() if s["field"] == field)
        wp_key = _WP_DIAL_KEYS[field]
        if wp_key in self._present:
            try:
                return float(self._present[wp_key])
            except (TypeError, ValueError):
                pass
        try:
            return float(settings.load().get(C.AUDIO_DIAL_KEYS[field], spec["calibrated"]))
        except (TypeError, ValueError):
            return float(spec["calibrated"])

    def _resolved_speed(self) -> float:
        """conf SPEED x the global timescale - exactly the rate the next show would send."""
        try:
            conf = float(self._wp_get("SPEED") or 1.0)
        except (TypeError, ValueError):
            conf = 1.0
        return conf * self.globalSpeed()

    def _resolved_volume(self) -> int:
        """VOLUME present means it (0 included); absent inherits ENGINE_VOLUME."""
        if "VOLUME" in self._present:
            try:
                return max(0, min(128, int(self._present["VOLUME"])))
            except (TypeError, ValueError):
                return 0
        return self.globalVolume()

    def _skip_ids(self) -> list[int]:
        """The SKIP set as the engine's integer id list; non-numeric ids cannot be sent."""
        out: list[int] = []
        for tok in _skip_list(self._wp_get("SKIP")):
            try:
                out.append(int(tok))
            except (TypeError, ValueError):
                continue
        return out

    def _fire_reshow(self) -> None:
        """Apply every coalesced relaunch-class edit with one re-show of the current wallpaper."""
        keys = sorted(self._pending)
        self._pending.clear()
        wid = self._wid
        if not wid or not self._is_current():
            return
        # a show carries this wallpaper's own resolved speed args, which would clobber a
        # session state the user set from another door (deck pause, popup speed). Capture the
        # live value first and re-assert it after the swap lands. A status() failure is BY
        # DESIGN not an error here - the show's own speed args stand (F14).
        live_speed = None
        try:
            snap = api_client.status()
            if isinstance(snap, dict) and isinstance(snap.get("speed"), (int, float)):
                live_speed = float(snap["speed"])
        except Exception:
            live_speed = None
        if self._backend is not None:
            try:
                # rotation entries carry their own resolved copy of the conf, so the set has to
                # be re-pushed or the next timed advance would restore the values just replaced
                self._backend._sync_engine()
            except Exception:
                # was a bare pass: a rotation set that did not re-push silently undoes the edit
                # on the next advance, so the user has to be told
                self.commitFailed.emit(keys)
                return
        try:
            ok = bool(self._backend.showNow(wid)) if self._backend is not None else False
        except Exception:
            ok = False
        if not ok:
            # P8: the batch failed to APPLY. The conf keys are already committed and will
            # apply on the next show, so nothing is rolled back - inventing a rollback here
            # would destroy committed user intent over a transport failure.
            self.commitFailed.emit(keys)
            return
        # `show` clears the engine's skip list wholesale, so a relaunch-class commit drops the
        # live object exclusions unless they are re-pushed after the re-show completes.
        ids = self._skip_ids()
        if ids:
            self._push(api_client.set_skip, "SKIP", ids)
        if live_speed is not None:
            try:
                if not (isinstance(api_client.set_speed(live_speed), dict)):
                    self.commitFailed.emit(["SPEED"])
            except Exception:
                # was a bare pass: the wallpaper silently returns to its conf rate after an
                # edit, which reads as the speed control undoing itself (P11/F13)
                self.commitFailed.emit(["SPEED"])

    @Slot(str, result=bool)
    def isMarked(self, key: str) -> bool:
        return SESSION.is_marked(self._wid, str(key or ""))

    @Slot(result=bool)
    def hasMarks(self) -> bool:
        return SESSION.has_marks(self._wid)

    @Slot(result=bool)
    def canRevert(self) -> bool:
        """`Revert changes` is enabled only when it can actually restore."""
        return SESSION.can_revert(self._wid)

    @Slot(result=bool)
    def snapshotValid(self) -> bool:
        return SESSION.is_valid(self._wid)

    @Slot(result=bool)
    def revertChanges(self) -> bool:
        """Restore every marked key to the value it had when this editing session began.

        Wallpaper-scoped only: the per-wallpaper tuning tier, the scene properties and the
        object SKIP set. Global settings and metadata are never in the revert set.
        """
        changes = SESSION.revert_changes(self._wid)
        if changes is None:
            return False
        if not self._commit_conf(changes):
            return False
        SESSION.clear_marks(self._wid)
        self._reload_conf()
        self._apply(changes)
        # a revert can move any number of properties at once, so the SET is what changed here
        self._prop_model.reset(self.sceneProperties())
        self.valuesRefreshed.emit()
        self.loaded.emit()
        self.edited.emit()
        self.propsEdited.emit()
        self.objectsEdited.emit()
        return True

    @Slot(result=bool)
    def loadDefaults(self) -> bool:
        """Strip all per-wallpaper customization back to the shipped baseline.

        Every wp conf override key, every PROP_ key and the SKIP set go absent; BG and TYPE
        survive because they are identity, not customization. Globals are untouched.
        """
        if not self._wid:
            return False
        changes = SESSION.defaults_changes(self._wid)
        if not self._commit_conf(changes):
            return False
        SESSION.clear_marks(self._wid)
        self._reload_conf()
        self._apply(changes)
        self._prop_model.reset(self.sceneProperties())
        self.valuesRefreshed.emit()
        self.loaded.emit()
        self.edited.emit()
        self.propsEdited.emit()
        self.objectsEdited.emit()
        return True

    @Slot(list)
    def reportFailure(self, keys: list) -> None:
        """Raise the failure grammar for a commit QML owns (the Pause animation toggle)."""
        self.commitFailed.emit([str(k) for k in keys])

    # ----------------------------------------------------------------------------------
    # Global capsule - settings.conf keys, all live, never marked
    # ----------------------------------------------------------------------------------
    def _setting(self, key: str, default: Any) -> Any:
        try:
            return settings.load().get(key, default)
        except Exception:
            return default

    def _persist_setting(self, key: str, value: Any) -> bool:
        """Write a global through the Backend so Settings pages and the rotation set follow."""
        if self._backend is None:
            return False
        try:
            self._backend.setSetting(key, value)
            return True
        except Exception:
            return False

    @Slot(result=float)
    def globalSpeed(self) -> float:
        try:
            return float(self._setting("ENGINE_TIMESCALE", 1.0) or 1.0)
        except (TypeError, ValueError):
            return 1.0

    @Slot(float, result=bool)
    def setGlobalSpeed(self, value: float) -> bool:
        """Global timescale. Live via set-speed; the store follows only on the engine's yes.

        Verb first, persist on confirm - the popup's order: never display a value that did
        not commit. A persisted value the engine refused would leave the row showing a rate
        nothing is running, which is the failure this surface exists to make visible.

        The engine is told the EFFECTIVE rate (this wallpaper's conf SPEED times the global
        factor), which is exactly what the next show would send.
        """
        try:
            factor = max(SPEED_MIN, min(SPEED_MAX, float(value)))
        except (TypeError, ValueError):
            self.commitFailed.emit(["ENGINE_TIMESCALE"])
            return False
        conf_speed = 1.0
        try:
            conf_speed = float(self._wp_get("SPEED") or 1.0)
        except (TypeError, ValueError):
            conf_speed = 1.0
        if not self._push(api_client.set_speed, "ENGINE_TIMESCALE", conf_speed * factor):
            return False
        if not self._persist_setting("ENGINE_TIMESCALE", factor):
            self.commitFailed.emit(["ENGINE_TIMESCALE"])
            return False
        self.valuesRefreshed.emit()
        self.loaded.emit()
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
        # stored number directly, so rescaling here alone would make one door disagree
        if not self._push(api_client.set_volume, "ENGINE_VOLUME", vol):
            return False
        if not self._persist_setting("ENGINE_VOLUME", vol):
            self.commitFailed.emit(["ENGINE_VOLUME"])
            return False
        self.valuesRefreshed.emit()
        self.loaded.emit()
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
        """Auto (empty) clears the key; anything else must parse as an integer in 1..480.

        A non-integer or an out-of-band number is failure grammar - never the silent
        fall-back-to-Auto the old per-wallpaper FPS field did.
        """
        s = str(text or "").strip()
        if s == "":
            if not self._persist_setting("ENGINE_FPS", ""):
                self.commitFailed.emit(["ENGINE_FPS"])
                return False
            # nothing to push: an empty cap means "whatever the engine launched with", which
            # is a launch-time value the running engine cannot be talked back into
            self.valuesRefreshed.emit()
            self.loaded.emit()
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
        self.valuesRefreshed.emit()
        self.loaded.emit()
        return True


    @Slot(result="QVariantList")
    def audioDials(self) -> list:
        """The three rows: label, control key, and the current 0..1 quality read from status.

        Values are seeded from the engine's LIVE status (`audio_gain` / `classic_k` /
        `classic_exp`) and never from a source default - the calibrated numbers live in
        whatever launch environment started the engine, and no .env, unit file or script in
        this repo carries them, so a source-seeded slider would show a value nothing is
        running.
        """
        snap: dict[str, Any] = {}
        try:
            got = api_client.status()
            if isinstance(got, dict):
                snap = got
        except Exception:
            snap = {}
        custom = self.audioMode() == "custom"
        out = []
        for key, spec in AUDIO_DIALS.items():
            if custom:
                value = self._resolved_dial(spec["field"])
            else:
                raw = snap.get(spec["field"])
                value = float(raw) if isinstance(raw, (int, float)) else spec["calibrated"]
            out.append({
                "key": key,
                "label": spec["label"],
                "quality": _dial_to_quality(spec, value),
                "engineValue": value,
            })
        return out

    @Slot(result=str)
    def audioMode(self) -> str:
        """"global" | "custom" - Custom when any per-wallpaper dial key is present."""
        return "custom" if any(k in self._present for k in _WP_DIAL_KEYS.values()) else "global"

    @Slot(str, result=bool)
    def setAudioMode(self, mode: str) -> bool:
        """Global deletes the three dial keys (inherit); Custom materializes the current
        effective values so the sliders seed from what is already running."""
        mode = str(mode or "").strip().lower()
        if mode == "global":
            return self._persist_draft(
                "", {wp_key: None for wp_key in _WP_DIAL_KEYS.values()})
        if mode == "custom":
            changes = {_WP_DIAL_KEYS[field]: _prop_to_str(self._resolved_dial(field))
                       for field in _WP_DIAL_KEYS}
            return self._persist_draft("", changes)
        self.commitFailed.emit(["AUDIO_MODE"])
        return False

    @Slot(str, float, result=bool)
    def setAudioDial(self, key: str, quality: float) -> bool:
        """Set ONE dial from its 0..1 quality position.

        Custom mode writes the per-wallpaper key (live-pushed when this wallpaper is
        showing); Global mode keeps the old behavior of driving the engine-global store."""
        key = str(key or "")
        spec = AUDIO_DIALS.get(key)
        if spec is None:
            self.commitFailed.emit([key])
            return False
        try:
            value = _quality_to_dial(spec, float(quality))
        except (TypeError, ValueError):
            self.commitFailed.emit([key])
            return False
        if self.audioMode() == "custom":
            return self._persist_draft("", {_WP_DIAL_KEYS[spec["field"]]: _prop_to_str(value)})
        try:
            if not api_client.available():
                self.commitFailed.emit([key])
                return False
            reply = api_client.set_tuning(**{spec["field"]: value})
        except Exception:
            self.commitFailed.emit([key])
            return False
        if not (isinstance(reply, dict) and reply.get("ok")):
            self.commitFailed.emit([key])
            return False
        self._persist_setting(C.AUDIO_DIAL_KEYS[spec["field"]], value)
        self.valuesRefreshed.emit()
        return True

    # ----------------------------------------------------------------------------------
    # Per-wallpaper tier - key presence IS set-ness; "" is the Global entry
    # ----------------------------------------------------------------------------------
    def _present_str(self, key: str) -> str:
        """The stored value, or "" when the key is ABSENT (the row is inheriting)."""
        if key not in self._present:
            return ""
        val = self._present[key]
        if isinstance(val, bool):
            return "true" if val else "false"
        return str(val)

    @Slot(result=str)
    def scalingValue(self) -> str:
        return self._present_str("SCALING")

    @Slot(str, result=bool)
    def setScalingValue(self, value: str) -> bool:
        """"" is the explicit unset (menu entry Global) and DELETES the key; else store it."""
        s = str(value or "").strip()
        if s and s not in C.SCALINGS:
            self.commitFailed.emit(["SCALING"])
            return False
        return self._set_key("SCALING", s or None)

    @Slot(result=str)
    def speedValue(self) -> str:
        return self._present_str("SPEED")

    @Slot(float, result=bool)
    def setSpeedValue(self, value: float) -> bool:
        try:
            v = max(SPEED_MIN, min(SPEED_MAX, float(value)))
        except (TypeError, ValueError):
            self.commitFailed.emit(["SPEED"])
            return False
        return self._set_key("SPEED", v)

    @Slot(result=str)
    def volumeValue(self) -> str:
        return self._present_str("VOLUME")

    @Slot(int, result=bool)
    def setVolumeValue(self, value: int) -> bool:
        try:
            v = max(0, min(100, int(value)))
        except (TypeError, ValueError):
            self.commitFailed.emit(["VOLUME"])
            return False
        return self._set_key("VOLUME", v)

    @Slot(result=str)
    def audioReactiveValue(self) -> str:
        return self._present_str("AUDIO_REACTIVE")

    @Slot(result=str)
    def mouseValue(self) -> str:
        return self._present_str("MOUSE")

    @Slot(result=str)
    def automuteValue(self) -> str:
        return self._present_str("AUTOMUTE")

    @Slot(str, str, result=bool)
    def setBoolOverride(self, key: str, value: str) -> bool:
        """One door for the three-entry Global/On/Off menus. "" deletes the key.

        Kept as one slot rather than three near-identical setters: the rows share one
        control, one menu and one grammar, so they share one commit path too.

        FULLSCREEN_PAUSE is NOT among them. Fullscreen pause is a GLOBAL concept and the
        editor draws no row for it at all, overruling the earlier per-wallpaper disposition.
        The conf key and its resolution semantics are untouched - this surface simply does
        not edit it.
        """
        key = str(key or "")
        if key not in ("AUDIO_REACTIVE", "MOUSE", "AUTOMUTE"):
            self.commitFailed.emit([key])
            return False
        s = str(value or "").strip().lower()
        if s == "":
            return self._set_key(key, None)
        if s in ("true", "1", "yes", "on"):
            return self._set_key(key, True)
        if s in ("false", "0", "no", "off"):
            return self._set_key(key, False)
        self.commitFailed.emit([key])
        return False

    @Slot(str, result=str)
    def globalDefaultFor(self, key: str) -> str:
        """The inherited value a `Global (<value>)` menu entry displays."""
        key = str(key or "")
        if key == "SCALING":
            return str(self._setting("ENGINE_SCALING", "default"))
        if key == "SPEED":
            return str(self.globalSpeed())
        if key == "VOLUME":
            return str(self.globalVolume())
        if key == "AUDIO_REACTIVE":
            return "on" if self._setting("AUDIO_REACTIVE_DEFAULT", False) else "off"
        if key == "MOUSE":
            return "on" if self._setting("MOUSE_DEFAULT", False) else "off"
        if key == "AUTOMUTE":
            return "on" if self._setting("AUTOMUTE_DEFAULT", True) else "off"
        return ""

    @Slot(result=str)
    def ccMode(self) -> str:
        """"none" | "custom" - the DISPLAYED master state.

        None = the authored look (derive_cc materialized; identity when the wallpaper ships
        no wec_* block). None stores "none"; the legacy spelling "preset" IS the authored
        look and reads the same. A CC with no CC_MODE is Custom, which is every legacy conf.
        """
        if "CC" not in self._present and "CC_MODE" not in self._present:
            return "none"
        mode = str(self._present.get("CC_MODE") or "")
        if mode in ("none", "preset"):
            return "none"
        return "custom"

    @Slot(result="QVariantList")
    def ccChannels(self) -> list:
        """The four channel values as floats, in CC slot order (brightness/contrast/sat/hue).
        An absent CC reads as the authored look, mirroring the show path's fallback."""
        parts = str(self._wp_get("CC") or self._authored_cc()).split()
        out: list[float] = []
        for i, neutral in enumerate((1.0, 1.0, 1.0, 0.0)):
            try:
                out.append(float(parts[i]))
            except (IndexError, TypeError, ValueError):
                out.append(neutral)
        return out

    def _authored_cc(self) -> str:
        """The authored look's CC string: derive_cc over the project's preset block (or its
        raw keys), identity when the wallpaper ships no wec_* grading."""
        raw = self._proj.get("raw") if isinstance(self._proj, dict) else {}
        if not isinstance(raw, dict):
            raw = {}
        preset = raw.get("preset")
        return project_disc.derive_cc(preset if isinstance(preset, dict) else raw)

    @Slot(str, result=bool)
    def setCcMode(self, mode: str) -> bool:
        """`None` = the authored look: it MATERIALIZES derive_cc's numbers, never clears the
        correction (a published preset's grading IS the wallpaper). `Custom` seeds from the
        current effective numbers. CC stays materialized as the effective-numbers cache in
        every mode, so the show path never has to parse scene JSON.
        """
        mode = str(mode or "").strip().lower()
        if mode in ("", "none", "preset"):
            return self._persist_draft("", {"CC": self._authored_cc(), "CC_MODE": "none"})
        if mode == "custom":
            cc = " ".join(_prop_to_str(c) for c in self.ccChannels())
            return self._persist_draft("", {"CC": cc, "CC_MODE": "custom"})
        self.commitFailed.emit(["CC_MODE"])
        return False

    @Slot(int, float, result=bool)
    def setCcChannel(self, index: int, value: float) -> bool:
        """Set one channel. Any manual edit DEMOTES a remembered preset to custom."""
        try:
            idx = int(index)
            val = float(value)
        except (TypeError, ValueError):
            self.commitFailed.emit(["CC"])
            return False
        if idx < 0 or idx > 3:
            self.commitFailed.emit(["CC"])
            return False
        lo, hi = (-1.0, 1.0) if idx == 3 else (0.0, 2.0)
        val = max(lo, min(hi, val))
        chans = self.ccChannels()
        chans[idx] = val
        cc = " ".join(_prop_to_str(c) for c in chans)
        return self._persist_draft("", {"CC": cc, "CC_MODE": "custom"})

    @Slot(result="QVariantList")
    def objectGroups(self) -> list[dict]:
        """Group objects by type -> [{type, count, enabled}].

        `enabled` is True unless EVERY objid of that type is in wp.SKIP (a group toggled off =>
        all its ids skipped). Group order follows first appearance in the object list.
        """
        skip = _skip_set(self._wp_get("SKIP"))
        order: list[str] = []
        by_type: dict[str, list[str]] = {}
        for obj in self._objects:
            t = str(obj.get("type") or "generic")
            objid = str(obj.get("objid") or "")
            if t not in by_type:
                by_type[t] = []
                order.append(t)
            by_type[t].append(objid)
        out: list[dict] = []
        for t in order:
            ids = by_type[t]
            # enabled = NOT all ids skipped. Empty objid strings can't be skipped meaningfully;
            # a group is "off" only when every real objid is present in SKIP.
            real = [i for i in ids if i]
            all_skipped = bool(real) and all(i in skip for i in real)
            out.append({"type": t, "count": len(ids), "enabled": not all_skipped})
        return out

    @Slot(result=int)
    def particleCount(self) -> int:
        """Number of type=='particle' objects (the >50 scale guard's input)."""
        return sum(1 for o in self._objects if str(o.get("type")) == "particle")

    @Slot(result=bool)
    def propertiesReadable(self) -> bool:
        """False when project.json could not be read - NOT the same as "no properties"."""
        return self._props_readable

    @Slot(result="QVariantList")
    def objectList(self) -> list[dict]:
        """Per-object rows for the objects panel: id, name, class, origin, visible, skipped,
        parent. Order follows the scene graph. `skipped` reflects wp.SKIP membership."""
        skip = _skip_set(self._wp_get("SKIP"))
        out: list[dict] = []
        for obj in self._objects:
            objid = str(obj.get("objid") or "")
            out.append({
                "objid": objid,
                "name": str(obj.get("name") or ""),
                "type": str(obj.get("type") or "generic"),
                "origin": str(obj.get("origin") or ""),
                "parent": str(obj.get("parent") or ""),
                "visible": obj.get("visible", True) is not False,
                "skipped": objid in skip,
            })
        return out

    @Slot(result="QVariantList")
    def objectTypeCounts(self) -> list[dict]:
        """Present-only types with counts, for the Object Exclusion type-filter menu."""
        order: list[str] = []
        counts: dict[str, int] = {}
        for obj in self._objects:
            t = str(obj.get("type") or "generic")
            if t not in counts:
                counts[t] = 0
                order.append(t)
            counts[t] += 1
        return [{"type": t, "count": counts[t]} for t in order]

    @Slot(result=bool)
    def hasParenting(self) -> bool:
        """True when any object declares a parent (enables the objects tree mode)."""
        return any(str(o.get("parent") or "") for o in self._objects)

    @Slot(str, bool)
    def setObjectSkipped(self, objid: str, skipped: bool) -> None:
        """Toggle one object in wp.SKIP."""
        objid = str(objid or "")
        if not objid:
            return
        skip = _skip_list(self._wp_get("SKIP"))
        if skipped:
            if objid not in skip:
                skip.append(objid)
        else:
            skip = [i for i in skip if i != objid]
        self._set_key("SKIP", " ".join(skip))

    @Slot(str, result="QVariantMap")
    def filteredSkipState(self, otype: str) -> dict:
        """Tri-state for the bulk toggle over the CURRENT FILTERED VIEW.

        Derived LIVE from the SKIP list every time - the toggle has no snapshot memory, so it
        cannot disagree with the rows under it. "" or "all" is the unfiltered view, which is
        everything. `state` is "on" (nothing skipped), "off" (all skipped) or "partial".
        """
        otype = str(otype or "")
        skip = _skip_set(self._wp_get("SKIP"))
        ids = [str(o.get("objid") or "") for o in self._objects
               if not otype or otype == "all" or str(o.get("type") or "generic") == otype]
        ids = [i for i in ids if i]
        if not ids:
            return {"state": "on", "count": 0}
        skipped = sum(1 for i in ids if i in skip)
        if skipped == 0:
            state = "on"
        elif skipped == len(ids):
            state = "off"
        else:
            state = "partial"
        return {"state": state, "count": len(ids)}

    @Slot(str, bool)
    def setFilteredEnabled(self, otype: str, on: bool) -> None:
        """The ONE bulk toggle: flip exactly the current filtered view.

        No filter means everything. Ids outside the view keep their SKIP order untouched, so
        a bulk flip on one type can never disturb another type's exclusions.
        """
        otype = str(otype or "")
        target = [str(o.get("objid") or "") for o in self._objects
                  if not otype or otype == "all" or str(o.get("type") or "generic") == otype]
        target = [i for i in target if i]
        if not target:
            return
        skip = _skip_list(self._wp_get("SKIP"))
        if on:
            drop = set(target)
            skip = [i for i in skip if i not in drop]
        else:
            have = set(skip)
            for i in target:
                if i not in have:
                    skip.append(i)
                    have.add(i)
        self._set_key("SKIP", " ".join(skip))

    @Slot(result="QVariantList")
    def sceneProperties(self) -> list[dict]:
        """Typed scene-property controls, in project.json order (no sorting, no grouping).

        Each entry: {name, key, kind, label, value, min, max, step, options, condition}.
        `value` reflects the wp PROP_<name> override when set, else the project default.
        `key` is the control key the mark and failure grammars are addressed by.
        """
        overrides = self._present.get("props") if isinstance(self._present, dict) else {}
        if not isinstance(overrides, dict):
            overrides = {}
        out: list[dict] = []
        for entry in self._props:
            name = str(entry.get("name") or "")
            kind = str(entry.get("kind") or "text")
            value: Any = overrides[name] if name in overrides else entry.get("value")
            rec: dict[str, Any] = {
                "name": name,
                "key": f"{C.WP_PROP_PREFIX}{name}",
                "kind": kind,
                "label": entry.get("label") or name,
                "value": value,
                "min": entry.get("min", 0),
                "max": entry.get("max", 100),
                "step": entry.get("step", 1),
                "options": entry.get("options", []),
                "condition": entry.get("condition", {}),  # {} = always visible; else {key, target}
            }
            out.append(rec)
        return out

    def _get_prop_model(self) -> QObject:
        return self._prop_model

    # CONSTANT: the model object itself never changes, which is the whole point - only its
    # CONTENTS move, and they move per row.
    scenePropertyModel = Property(QObject, _get_prop_model, constant=True)

    @Slot(result=str)
    def workshopId(self) -> str:
        return self._wid

    @Slot(result=str)
    def currentWid(self) -> str:
        """The wallpaper this bridge is loaded on, "" when idle."""
        return self._wid

    @Slot(str, result=bool)
    def copyToClipboard(self, text: str) -> bool:
        """Put `text` on the system clipboard (the workshop-id chip's real copy).

        Returns True on a genuine copy so the QML's "copied" flash stays honest - a headless run
        with no QGuiApplication (tests) returns False and the flash is suppressed. The QtGui
        import is local so a non-GUI import of this module never pulls QtGui.
        """
        try:
            from PySide6.QtGui import QGuiApplication
            app = QGuiApplication.instance()
            if app is None:
                return False
            cb = QGuiApplication.clipboard()
            if cb is None:
                return False
            cb.setText(str(text or ""))
            return True
        except Exception:
            return False

    # ----------------------------------------------------------------------------------
    # Metadata - instant-write, outside the marked set and the revert set
    # ----------------------------------------------------------------------------------
    @Slot(result="QVariantList")
    def tags(self) -> list:
        t = self._meta.get("tags") if isinstance(self._meta, dict) else None
        return list(t) if isinstance(t, list) else []

    @Slot(str)
    def addTag(self, tag: str) -> None:
        tag = str(tag or "").strip()
        if not tag or not self._wid:
            return
        cur = self.tags()
        if tag not in cur:
            cur.append(tag)
            try:
                meta.update(self._wid, {"tags": cur})
                self._meta["tags"] = cur
            except Exception:
                self.commitFailed.emit(["TAGS"])
                return
        self.metadataChanged.emit()
        self.loaded.emit()

    @Slot(str)
    def removeTag(self, tag: str) -> None:
        cur = [t for t in self.tags() if t != tag]
        try:
            meta.update(self._wid, {"tags": cur})
            self._meta["tags"] = cur
        except Exception:
            self.commitFailed.emit(["TAGS"])
            return
        self.metadataChanged.emit()
        self.loaded.emit()

    @Slot(str)
    def setTitle(self, title: str) -> None:
        """User title override stored in meta (project.json title stays the fallback)."""
        title = str(title or "").strip()
        if not self._wid:
            return
        try:
            meta.update(self._wid, {"title": title})
            self._meta["title"] = title
        except Exception:
            self.commitFailed.emit(["TITLE"])
            return
        self.metadataChanged.emit()
        self.loaded.emit()

    @Slot()
    def toggleFavorite(self) -> None:
        """Flip meta.favorite for the loaded wallpaper."""
        if not self._wid:
            return
        cur = bool(self._meta.get("favorite")) if isinstance(self._meta, dict) else False
        new = not cur
        try:
            meta.update(self._wid, {"favorite": new})
        except Exception:
            self.commitFailed.emit(["FAVORITE"])
            return
        self._meta["favorite"] = new
        self.metadataChanged.emit()
        self.loaded.emit()

    @Slot(result="QVariantMap")
    def overrideState(self) -> dict:
        """Which per-wallpaper keys are SET vs inheriting, plus the inherited value to show.

        Set-ness is KEY PRESENCE, not a value comparison: wp.load materializes every
        schema key to its default, so a value test cannot tell SCALING=default from
        SCALING-absent, and zero-values (VOLUME=0, AUDIO_REACTIVE=false) were inexpressible
        as overrides at all. This reads the presence view instead.
        """
        out: dict[str, dict] = {}
        for name, key in (("scaling", "SCALING"), ("volume", "VOLUME"), ("speed", "SPEED"),
                          ("audioReactive", "AUDIO_REACTIVE"), ("mouse", "MOUSE"),
                          ("automute", "AUTOMUTE"), ("cc", "CC")):
            out[name] = {"set": key in self._present, "global": self.globalDefaultFor(key)}
        return out

    @Slot(str)
    def clearOverride(self, which: str) -> None:
        """Reset one per-wallpaper key back to inheriting - the `Global` menu entry's path.

        Deletes the key rather than writing a default over it, which is the whole point of
        key-presence semantics: an inherited row must be ABSENT, not materialized.
        """
        keymap = {"scaling": "SCALING", "volume": "VOLUME", "speed": "SPEED",
                  "audioReactive": "AUDIO_REACTIVE", "mouse": "MOUSE", "cc": "CC",
                  "automute": "AUTOMUTE"}
        key = keymap.get(str(which or ""))
        if not key:
            return
        changes: dict[str, Any] = {key: None}
        if key == "CC":
            changes["CC_MODE"] = None
        self._persist_draft("", changes)
        self.valuesRefreshed.emit()
        self.loaded.emit()

    @Slot(str)
    def setScaling(self, value: str) -> None:
        self.setScalingValue(value)

    @Slot(str)
    def setSpeed(self, value: str) -> None:
        try:
            self.setSpeedValue(float(str(value).strip()))
        except (TypeError, ValueError):
            self.commitFailed.emit(["SPEED"])

    @Slot(str)
    def setCc(self, value: str) -> None:
        """Raw CC vector write. A manual vector is a custom grade."""
        self._persist_draft("", {"CC": str(value), "CC_MODE": "custom"})

    @Slot(int)
    def setVolume(self, value: int) -> None:
        self.setVolumeValue(value)

    @Slot(str)
    def setClamping(self, value: str) -> None:
        v = str(value).strip()
        # only the three valid clamp values (or empty) are accepted; anything else clears it.
        self._set_key("CLAMPING", v if v in C.CLAMPS else None)

    @Slot(bool)
    def setAutomute(self, value: bool) -> None:
        self._set_key("AUTOMUTE", bool(value))

    @Slot(bool)
    def setAudioReactive(self, value: bool) -> None:
        self._set_key("AUDIO_REACTIVE", bool(value))

    @Slot(bool)
    def setMouse(self, value: bool) -> None:
        self._set_key("MOUSE", bool(value))

    @Slot(str)
    def setMonitors(self, value: str) -> None:
        s = str(value).strip()
        self._set_key("MONITORS", s or "all")

    @Slot(str, "QVariant")
    def setProp(self, name: str, value: Any) -> None:
        """Set (or, on an empty value, clear) one PROP_<name> override.

        A name that cannot survive as a PROP_<name> shell key would be warned-and-skipped
        inside the store, which from here reads as a successful commit that did nothing.
        Refuse it up front so the control shows the failure instead of appearing to accept
        the edit - a silent no-op is illegal on this surface.
        """
        name = str(name or "")
        if not name:
            return
        key = f"{C.WP_PROP_PREFIX}{name}"
        if not tier_a.is_valid_key(key):
            self.commitFailed.emit([key])
            return
        sval = _prop_to_str(value)
        self._set_key(key, sval or None)

    @Slot(str, str, result=bool)
    def setPropColor(self, name: str, text: str) -> bool:
        """Commit a colour-kind scene property from its hex readout.

        The readout stopped being a label and became the control: the row keeps its swatch
        and its geometry, but the hex is an editable mono field. Storage is unchanged - a WE
        color property is space-separated floats in 0..1, so the hex is converted on the way
        in and rebuilt on the way out, and the round trip is stable.

        A string that is not a #RRGGBB color is failure grammar (L6): the banner rises, the
        control outlines red and the conf is not touched. Silently keeping the old value
        would be indistinguishable from a commit that worked.
        """
        name = str(name or "")
        if not name:
            return False
        key = f"{C.WP_PROP_PREFIX}{name}"
        rgb = hex_to_rgb_floats(str(text))
        if rgb is None:
            self.commitFailed.emit([key])
            return False
        return bool(self._set_key(key, rgb))

    @Slot(str, result=str)
    def colorHex(self, value: str) -> str:
        """The stored floats as a lowercase #rrggbb readout (the display half of the pair)."""
        return rgb_floats_to_hex(value)

    @Slot(str, bool)
    def setObjectGroupEnabled(self, otype: str, on: bool) -> None:
        """Toggle a whole object-type group in wp.SKIP.

        on=False  -> add every objid of `otype` to wp.SKIP (remove the layer).
        on=True   -> remove every objid of `otype` from wp.SKIP.
        SKIP order is preserved for ids of other types; toggled ids are appended in object order.
        """
        self.setFilteredEnabled(str(otype or ""), on)

    @Slot(result="QVariantList")
    def authoredGroups(self) -> list[dict]:
        """Group objects by their AUTHORED name for the objects panel.

        Real scenes carry an authored `name` on every object and a given name's members share
        one engine type, so each group is {name, type, count, ids, enabled}; a name held by one
        object is a singleton group. Where a scene exposes NO usable authored names the caller
        falls back to type grouping via objectGroups(); groupingMode() reports which applies.
        `enabled` is False only when every real member id is in wp.SKIP. Group order follows
        first appearance. `type` is the members' shared engine type, or "mixed" in the
        (unobserved in real data) case of a name spanning types.
        """
        skip = _skip_set(self._wp_get("SKIP"))
        order: list[str] = []
        by_name: dict[str, dict] = {}
        for obj in self._objects:
            name = str(obj.get("name") or "")
            if not name:
                continue
            objid = str(obj.get("objid") or "")
            otype = str(obj.get("type") or "generic")
            grp = by_name.get(name)
            if grp is None:
                grp = {"ids": [], "types": set()}
                by_name[name] = grp
                order.append(name)
            grp["ids"].append(objid)
            grp["types"].add(otype)
        out: list[dict] = []
        for name in order:
            grp = by_name[name]
            ids = grp["ids"]
            types = grp["types"]
            gtype = next(iter(types)) if len(types) == 1 else "mixed"
            real = [i for i in ids if i]
            all_skipped = bool(real) and all(i in skip for i in real)
            out.append({"name": name, "type": gtype, "count": len(ids), "ids": ids,
                        "enabled": not all_skipped})
        return out

    @Slot(result=str)
    def groupingMode(self) -> str:
        """"authored" when at least one object carries an authored name, else "type".

        The objects panel groups by authored name when possible and falls back to engine-type
        grouping for a scene that exposes no names at all.
        """
        return "authored" if any(str(o.get("name") or "") for o in self._objects) else "type"

    @Slot(str, bool)
    def setAuthoredGroupEnabled(self, name: str, on: bool) -> None:
        """Toggle a whole authored-name group in wp.SKIP (the group row's cascade).

        on=False -> add every member objid of the named group to wp.SKIP (remove the layer).
        on=True  -> remove every member objid of the named group from wp.SKIP.
        Ids of objects outside the group keep their SKIP order; toggled ids append in order.
        """
        name = str(name or "")
        group_ids = [str(o.get("objid") or "") for o in self._objects
                     if str(o.get("name") or "") == name]
        self._cascade(group_ids, on)

    @Slot(str, bool)
    def setUnnamedGroupEnabled(self, otype: str, on: bool) -> None:
        """Toggle the EMPTY-NAME objects of one type in wp.SKIP (the authored-mode fallback
        group's cascade). A plain type cascade here would flip every object of the type,
        including members of named groups, on a mixed named/unnamed scene. In a fully nameless
        scene this equals the type cascade, so the fallback grouping mode routes here too."""
        otype = str(otype or "")
        group_ids = [str(o.get("objid") or "") for o in self._objects
                     if str(o.get("type") or "") == otype and not str(o.get("name") or "")]
        self._cascade(group_ids, on)

    def _cascade(self, group_ids: list[str], on: bool) -> None:
        """Add or remove one group's ids in wp.SKIP, preserving every other id's order."""
        group_ids = [i for i in group_ids if i]
        if not group_ids:
            return
        skip = _skip_list(self._wp_get("SKIP"))
        if on:
            target = set(group_ids)
            skip = [i for i in skip if i not in target]
        else:
            have = set(skip)
            for i in group_ids:
                if i not in have:
                    skip.append(i)
                    have.add(i)
        self._set_key("SKIP", " ".join(skip))

    @Slot()
    def bulkDisableParticles(self) -> None:
        """Add every particle objid to wp.SKIP (the scale guard's bulk path)."""
        self._cascade([str(o.get("objid") or "") for o in self._objects
                       if str(o.get("type")) == "particle"], False)

    @Slot()
    def autoFromPreset(self) -> None:
        """Derive CC from the project.json preset `wec_*` block and remember preset mode.

        The `Use Wallpaper Preset` master entry's path. brs/con/sa = value/50,
        hue = (value-50)/50; no preset block -> identity "1 1 1 0".
        """
        self.setCcMode("preset")
        self.valuesRefreshed.emit()
        self.loaded.emit()


# --------------------------------------------------------------------------------------
# helpers (module-level so they stay testable + free of Qt)
# --------------------------------------------------------------------------------------
def hex_to_rgb_floats(text: str) -> str | None:
    """"#50A5C6" (or "50a5c6") -> "0.313725 0.647059 0.776471". None when it is not a color.

    Case-insensitive and the leading # is optional on entry. Five decimal places is enough to
    round-trip every 8-bit channel exactly - 0.31373 * 255 lands back on 80 - while keeping
    the stored string as terse as the values WE ships.
    """
    s = str(text or "").strip().lstrip("#").strip()
    if len(s) != 6:
        return None
    try:
        chans = [int(s[i:i + 2], 16) for i in (0, 2, 4)]
    except ValueError:
        return None
    return " ".join(f"{c / 255.0:.5f}".rstrip("0").rstrip(".") or "0" for c in chans)


def rgb_floats_to_hex(value: object) -> str:
    """"0.31373 0.64706 0.77647" -> "#50a5c6". Missing or unparsable channels read as 1.0."""
    parts = str(value or "").split()
    out = "#"
    for i in range(3):
        try:
            n = float(parts[i])
        except (IndexError, TypeError, ValueError):
            n = 1.0
        out += f"{round(max(0.0, min(1.0, n)) * 255):02x}"
    return out


def _dial_to_quality(spec: dict, value: float) -> float:
    """Engine dial -> the 0..1 position of the quality its row is labeled with."""
    import math

    lo, hi = float(spec["lo"]), float(spec["hi"])
    v = max(lo, min(hi, float(value)))
    if spec["log"]:
        pos = math.log(v / lo) / math.log(hi / lo)
    else:
        pos = (v - lo) / (hi - lo)
    return round(1.0 - pos if spec["invert"] else pos, 4)


def _quality_to_dial(spec: dict, quality: float) -> float:
    """The inverse of _dial_to_quality: a slider position back to the engine's own number."""
    import math

    pos = max(0.0, min(1.0, float(quality)))
    if spec["invert"]:
        pos = 1.0 - pos
    lo, hi = float(spec["lo"]), float(spec["hi"])
    v = lo * math.pow(hi / lo, pos) if spec["log"] else lo + (hi - lo) * pos
    return round(max(lo, min(hi, v)), 6)


def _as_bool(value: object, default: bool = False) -> bool:
    """Coerce a wp value to bool, tolerating shell-string forms; `default` for None/"" inherit."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        s = value.strip().lower()
        if s == "":
            return default
        return s in ("true", "1", "yes", "on")
    return bool(value)


def _skip_list(value: object) -> list[str]:
    """SKIP space-list -> ordered list of objid strings (deduped, order-preserving)."""
    out: list[str] = []
    seen: set[str] = set()
    for tok in str(value or "").split():
        if tok and tok not in seen:
            out.append(tok)
            seen.add(tok)
    return out


def _skip_set(value: object) -> set[str]:
    return set(_skip_list(value))


def _prop_to_str(value: object) -> str:
    """Stringify a PROP value the way wp.save serializes it (bools as true/false; lists space-joined)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return " ".join(_prop_to_str(v) for v in value)
    if isinstance(value, float):
        # keep ints terse (1.0 -> "1") so "r g b" color strings stay clean
        return str(int(value)) if value == int(value) else repr(value)
    return str(value).strip()
