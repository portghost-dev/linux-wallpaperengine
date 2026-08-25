"""DevBridge - the developer cockpit backend.

This is our parity tooling, productized: it composes the engine launch (env instruments +
render-debug isolation + escape-hatch A/B toggles), runs it over a daemon standdown so
it owns the layer surface, streams the LWE-* log, and keeps the append-only verdict log and
per-run history. It never fabricates a visual verdict - the log records what the operator
types.

The instrument and provenance inventories are structural here; the shipped build populates
them from the engine knob census. Everything below the Qt layer is stdlib.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal, Slot

from . import constants as C
from . import api_client
from . import bench_courier
from .engine import daemon_unit
from .discovery import objects as objects_disc
from .storage import atomic, paths, settings

OUR_TOGGLES = [
    {"key": "frontface", "env": "LWE_FRONTFACE", "off": "ccw", "what": "mesh winding fix",
     "sys": "Lighting & Models", "commit": "", "evidence": "", "experimental": False},
    {"key": "animfraction", "env": "LWE_ANIMFRACTION", "off": "0",
     "what": "animated-texture cycle law", "sys": "Particles",
     "commit": "", "evidence": "", "experimental": False},
    {"key": "texcomp", "env": "LWE_TEXCOMP", "off": "0", "what": "BC7 texture compression",
     "sys": "Performance", "commit": "", "evidence": "", "experimental": False},
    {"key": "fbopool", "env": "LWE_FBOPOOL", "off": "0", "what": "FBO composite pool",
     "sys": "Performance", "commit": "", "evidence": "", "experimental": False},
    {"key": "skipgate", "env": "LWE_SKIPGATE", "off": "0", "what": "asset skip-gate",
     "sys": "Performance", "commit": "", "evidence": "", "experimental": False},
    {"key": "shapes", "env": "LWE_SHAPES", "off": "0", "what": "VolumeLight shape objects",
     "sys": "Render", "commit": "", "evidence": "", "experimental": True},
]

# Render-debug toggle flags (design 7d): each composes as `--render-debug <flag>` into the
# argv. The engine parser accepts exactly these bare flags (plus object=/skip-object=/
# skip-effect= handled by the isolator). key -> (flag token, label).
RENDER_DEBUG_FLAGS = [
    {"key": "base-only", "what": "Base pass only"},
    {"key": "no-solid-final", "what": "Skip solid final"},
    {"key": "pass-log", "what": "Pass log"},
]

# Log-only instruments (design 6b): env var -> what it emits. Toggling one adds it to the
# launch env; its output shows in the log console AND streams into the subsystem readout
# table. "tags" are the line prefixes that instrument emits, read from the engine source
# (each guard's print statements) - the readout filter keys on them.
INSTRUMENTS = [
    {"env": "LWE_LIGHTDUMP", "what": "light + script + text + property trace",
     "tags": ["LWE-MODELPASS", "LWE-SCRIPTTRACE", "LWE-AUDIT", "LWE-LIGHTDUMP",
              "LWE-TEXTDUMP", "LWE-PROPTRACE"]},
    {"env": "LWE_PARTSTATS", "what": "particle emit/live/peak", "tags": ["LWE-PARTSTATS"]},
    {"env": "LWE_PARTALLOC", "what": "particle buffer bytes + high-water",
     "tags": ["LWE-PARTALLOC", "LWE-PARTALLOC-POOL", "LWE-PARTALLOC-FRAME"]},
    {"env": "LWE_VELPROBE", "what": "per-particle velocity init (very high volume)",
     "tags": ["LWE-VELPROBE"]},
    {"env": "LWE_SIZEPROBE", "what": "axis-comp uniform readback vs sim size, + compiled source",
     "tags": ["LWE-SIZEPROBE", "LWE-COMPPROBE", "LWE-COMPSRC"]},
    {"env": "LWE_TWINKLEPROBE", "what": "one-particle alpha time series", "tags": ["LWE-TWINKLE"]},
    {"env": "LWE_ROPETRAILPROBE", "what": "rope strip topology + head NDC",
     "tags": ["LWE-ROPETRAIL"]},
    {"env": "LWE_ANIMSTATS", "what": "animated-texture clock", "tags": ["LWE-ANIMSTATS"]},
    {"env": "LWE_CAMPROBE", "what": "scripted camera pose + view-projection rows",
     "tags": ["LWE-CAMPROBE", "LWE-CAMPROBE-GET", "LWE-VPPROBE"]},
    {"env": "LWE_TIMESTATS", "what": "frame timing vs wall clock", "tags": ["LWE-TIMESTATS"]},
    {"env": "LWE_FBOALLOC", "what": "per-FBO bytes + running total", "tags": ["LWE-FBOALLOC"]},
    {"env": "LWE_FBOTRACE", "what": "FBO alias wiring", "tags": ["LWE-FBOTRACE"]},
    {"env": "LWE_POOL_HWM", "what": "FBO pool leases + high-water (needs =1)",
     "tags": ["LWE-POOLHWM"]},
    {"env": "LWE_TEXCACHEDUMP", "what": "texture-cache survivors + wallpaper lifetime",
     "tags": ["LWE-TEXCACHE", "LWE-WPLIFE"]},
    {"env": "LWE_AUDIOSTATS", "what": "audio FFT bands", "tags": ["LWE-AUDIOSTATS"]},
    {"env": "LWE_FBPROFILE", "what": "framebuffer luminance profile",
     "tags": ["LWE-FBPROFILE", "LWE-PRESENTPROFILE"]},
    {"env": "LWE_SHADERDUMP", "what": "shader assembly on failure",
     # the engine emits FRAGSRC source dumps and "GLSL ... Failed" parse lines only -
     # there is no VERTSRC dump in the source (verified read-only)
     "tags": ["FRAGSRC", "GLSL", "LWE-SHADERDUMP"]},
    {"env": "LWE_UNIFDUMP", "what": "shader uniform constants at pass setup",
     "tags": ["LWE-UNIFDUMP"]},
    {"env": "LWE_UNIFVALS", "what": "every uniform upload (very high volume)",
     "tags": ["LWE-UNIFVALS", "LWE-COLORTRACE"]},
    {"env": "LWE_IMGDUMP", "what": "image rect + pass wiring",
     "tags": ["LWE-IMGDUMP", "LWE-UVDUMP", "LWE-PASSDUMP", "LWE-MVPDUMP"]},
    # LWE-SHAPEMVP (CImage.cpp:1392) shares this gate
    {"env": "LWE_IMGPROBE", "what": "image/shape geometry + MVP + GPU buffer contents",
     "tags": ["LWE-IMGPROBE", "LWE-SHAPEPROBE", "LWE-SHAPEBUF", "LWE-SHAPEMVP"]},
    {"env": "LWE_LEDGER", "what": "per-object render ledger, first frames",
     "tags": ["LWE-LEDGER"]},
    {"env": "LWE_CLEARPROBE", "what": "clear color + write mask", "tags": ["LWE-CLEARPROBE"]},
    {"env": "LWE_MASKAUDIT", "what": "mask-channel statistics", "tags": ["LWE-MASKAUDIT"]},
    {"env": "LWE_AUDIT", "what": "texture/model/PBR audits", "tags": ["LWE-AUDIT"]},
    {"env": "LWE_EGLDEBUG", "what": "EGL surface vs viewport, first frames",
     "tags": ["LWE-EGLSURF"]},
    {"env": "LWE_MOUSEDBG", "what": "compositor pointer delivery", "tags": ["LWE-MOUSEDBG"]},
    {"env": "LWE_SCRIPTDBG", "what": "property-script registration", "tags": ["LWE-SCRIPTDBG"]},
]

SUBSYSTEMS = ["Tour", "Lighting & Models", "Particles", "Puppets", "Bloom", "Audio",
              "Camera", "Performance", "Render"]

SUBSYSTEM_INSTRUMENTS = {
    "Lighting & Models": ["LWE_LIGHTDUMP", "LWE_AUDIT"],
    "Particles": ["LWE_PARTSTATS", "LWE_ANIMSTATS", "LWE_PARTALLOC", "LWE_VELPROBE",
                  "LWE_SIZEPROBE", "LWE_TWINKLEPROBE", "LWE_ROPETRAILPROBE"],
    "Camera": ["LWE_CAMPROBE"],
    "Audio": ["LWE_AUDIOSTATS"],
    "Performance": ["LWE_TIMESTATS", "LWE_FBOALLOC", "LWE_FBOTRACE", "LWE_POOL_HWM",
                    "LWE_TEXCACHEDUMP"],
    "Bloom": ["LWE_FBOALLOC", "LWE_FBOTRACE", "LWE_POOL_HWM", "LWE_CLEARPROBE",
              "LWE_FBPROFILE"],
    "Render": ["LWE_SHADERDUMP", "LWE_UNIFDUMP", "LWE_UNIFVALS", "LWE_IMGDUMP",
               "LWE_IMGPROBE", "LWE_FBPROFILE", "LWE_LEDGER", "LWE_CLEARPROBE",
               "LWE_MASKAUDIT", "LWE_EGLDEBUG", "LWE_MOUSEDBG", "LWE_SCRIPTDBG"],
}

LIVE_INSTRUMENTS = {"LWE_PARTSTATS", "LWE_TWINKLEPROBE", "LWE_ROPETRAILPROBE"}

ALWAYS_ON_TAGS = {
    "Puppets": [
        "Could not parse puppet",       # CImage.cpp:504
        "Could not load puppet mesh",   # CImage.cpp:557
        "Loaded puppet",                # CImage.cpp:550 - the per-load census
        "Puppet ",                      # CImage.cpp:543 + PuppetModel.cpp:115,190,200,515
    ],
}


def _engine_bin() -> str:
    try:
        return daemon_unit.resolve_engine_bin()
    except Exception:
        return str(paths.default_engine_bin())


def _assets_dir() -> str:
    try:
        return str(settings.load().get("ASSETS_DIR") or paths.default_assets_dir())
    except Exception:
        return str(paths.default_assets_dir())


def _wallpapers_dir() -> str:
    try:
        return str(settings.load().get("WALLPAPERS_DIR") or paths.default_wallpapers_dir())
    except Exception:
        return str(paths.default_wallpapers_dir())


def _probes_dir() -> Path:
    """Bundled probe/calibration scenes (app-relative dev/probes/). Each subdir
    is a wallpaper (project.json + assets); the cockpit lists them in the target picker as
    probe:<name>. The dev/ data dir sits beside the dev.py module - the module wins the import so
    there is no name clash. See dev/probes/README.md for the contract."""
    return Path(__file__).resolve().parent / "dev" / "probes"


class DevBridge(QObject):
    """Backend for the developer cockpit. Composes + runs an instrumented bench engine."""

    stateChanged = Signal()       # target / running / isolation / toggles changed
    logLine = Signal(str)         # one LWE-* (or engine) log line
    runsChanged = Signal()        # run history updated
    # The daemon's journal is a SEPARATE signal on purpose. The per-subsystem readout
    # panes listen to logLine and split each line into tag/payload by scoped instrument
    # tag; a journal line carrying an LWE- tag would land in a lens that never ran it.
    # One console, two sources, one of which must not reach the readout.
    journalLine = Signal(str)     # one line from the engine service's journal
    journalChanged = Signal()     # follower started/stopped

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._target = ""
        self._solo_objs: list[str] = []
        self._active_wid = ""
        self._skip_objs: list[str] = []
        self._skip_effs: list[str] = []
        self._live_isolation_applied = False
        self._toggles_off: set[str] = set()
        self._instruments: set[str] = set()
        self._render_debug: set[str] = set()
        self._set_props: list[tuple[str, str]] = []
        self._env_lines: list[tuple[str, str]] = []
        self._auto_relaunch = True
        self._pending_start = False
        self._proc: QProcess | None = None
        self._journal_proc: QProcess | None = None   # `journalctl -f` follower (see startJournal)
        self._run_start = 0.0              # monotonic start of the current bench (uptime clock)
        self._runs: list[dict] = []        # [{ts, code, tail}]
        self._log_buf: list[str] = []
        # A/B split surface: two engines, one screen split L/R (design 19). Mutually exclusive
        # with the single bench - both hold the same daemon standdown.
        self._proc_a: QProcess | None = None
        self._proc_b: QProcess | None = None
        self._ab_running = False
        # per-side exhibit loadouts (owner design, third A/B round): each side carries
        # its own set of fixes-off plus raw KEY=VALUE env lines; swap exchanges the two
        # configs outright (labels, borders, and window positions never move)
        self._ab_off: dict[str, set] = {"A": set(), "B": set()}
        self._ab_envlines: dict[str, list] = {"A": [], "B": []}
        self._ab_placed: set[str] = set()
        self._ab_place_tries: dict[str, int] = {}
        self._ab_b_due = 0.0
        self._ab_addr: dict[str, str] = {}
        self._ab_fullscreen: dict[str, bool] = {}
        self._ab_drag_side = ""                # side whose chip a live drag is holding still
        self._ab_drag_last = 0.0               # dead-man clock for a stuck drag freeze
        self._pal_pinned = bool(self.paletteState().get("pinned", True))
        # windowed A/B placement + chip follower (compositor-side via hyprctl): "find"
        # locates both engine windows by pid, applies quadrant placement + per-side border
        # colors once, then "follow" keeps the exhibit chips glued to their windows (the
        # demo's one-shot chips were orphaned the moment a window moved - owner finding)
        self._ab_place_timer = QTimer(self)
        self._ab_place_timer.setInterval(400)
        self._ab_place_timer.timeout.connect(self._ab_place_tick)

        self._restore_ab_loadouts()

        # 400ms debounce so a burst of isolation edits triggers ONE relaunch (design 18)
        self._relaunch_timer = QTimer(self)
        self._relaunch_timer.setSingleShot(True)
        self._relaunch_timer.setInterval(400)
        self._relaunch_timer.timeout.connect(self._do_relaunch)

    @Slot(str)
    def setTarget(self, wid: str) -> None:
        self._target = str(wid or "")
        self.stateChanged.emit()

    @staticmethod
    def _now_playing_wid() -> str:
        """The wid being shown right now, for the default "Now playing" target.

        Ask the ENGINE: its status carries the identity of what it is actually
        rendering (ui_id when the UI named a preset tile, else the resolved id).

        Read-only and tolerant: empty when nothing can answer or nothing is showing. This
        is what makes the default target real - an empty target resolves to no directory
        and the isolator lists nothing, which reads as unwired."""
        try:
            if api_client.available():
                st = api_client.status() or {}
                cur = st.get("current") or {}
                wid = str(cur.get("ui_id") or cur.get("id") or "")
                if wid:
                    return wid
        except Exception:
            pass
        return ""

    def _target_dir(self) -> str:
        wid = self._target
        if not wid:
            wid = self._now_playing_wid()
            if not wid:
                return ""
            return os.path.join(_wallpapers_dir(), wid)
        # a bundled probe target is prefixed "probe:<name>"
        if wid.startswith("probe:"):
            return str(_probes_dir() / wid.split(":", 1)[1])
        return os.path.join(_wallpapers_dir(), wid)

    @Slot(result="QVariantList")
    def probeList(self) -> list:
        d = _probes_dir()
        if not d.is_dir():
            return []
        return [{"name": p.name, "target": "probe:" + p.name}
                for p in sorted(d.iterdir()) if p.is_dir()]

    @Slot(bool)
    def setAutoRelaunch(self, on: bool) -> None:
        """Gate whether an isolation edit relaunches a live bench. Default
        ON matches the current always-relaunch behavior; off lets edits batch until manual Start."""
        self._auto_relaunch = bool(on)
        self.stateChanged.emit()

    @Slot(result=bool)
    def autoRelaunch(self) -> bool:
        return self._auto_relaunch

    @Slot(result=str)
    def activeTargetWid(self) -> str:
        """The wid (or probe dir name) the RUNNING hold launched - the deck's now-playing
        source while the bench owns the display (the daemon's current is parked)."""
        return self._active_wid if self.isHolding() else ""

    @Slot(result=int)
    def uptimeSeconds(self) -> int:
        """Whole seconds the current bench has been running (0 when idle). Drives the session-bar
        clock: 'engine up MM:SS' idle-running, 'bench holds display MM:SS' while holding."""
        if self._run_start <= 0.0 or not self.isHolding():
            return 0
        return int(time.monotonic() - self._run_start)

    def _live_isolation(self) -> bool:
        """True when the isolator should drive the RUNNING daemon instead of a bench child.

        The daemon reads its skip-list every frame, so solo/skip can be instant instead of
        costing a full relaunch per click. That only applies when we are NOT holding the
        display ourselves: a bench child or an A/B pair owns the screens, and those are
        composed from argv, so they keep the launch path.
        """
        if self.isRunning() or self._ab_running:
            return False
        try:
            return bool(api_client.available())
        except Exception:
            return False

    def _live_skip_ids(self) -> list[int]:
        """The skip-list to hand the engine for the current solo/skip state.

        `set-skip` is the only live lever - the engine's single-object `object=` filter is
        launch-time - so a solo set is expressed the same way the argv path expresses it,
        as the COMPLEMENT: skip every other enumerable object. Non-numeric ids are dropped
        (the engine parses these with std::stoi)."""
        skips = [i for i in self._skip_objs if str(i).lstrip("-").isdigit()]
        if self._solo_objs:
            solo = set(self._solo_objs)
            for obj in self.objectList():
                oid = str(obj.get("objid", ""))
                if oid.lstrip("-").isdigit() and oid not in solo and oid not in skips:
                    skips.append(oid)
        return [int(i) for i in skips]

    def _apply_isolation(self) -> None:
        """Push isolation to wherever it belongs: live to the daemon, else via relaunch."""
        if not self._live_isolation():
            self._schedule_relaunch()
            return
        try:
            api_client.set_skip(self._live_skip_ids())
            # remember that the LIVE wallpaper is currently altered, so shutdown can undo it
            self._live_isolation_applied = bool(self._solo_objs or self._skip_objs)
        except Exception:
            pass

    def _clear_live_isolation(self) -> None:
        """Hand the live wallpaper back. Never leave the desktop isolated behind us."""
        if not self._live_isolation_applied:
            return
        try:
            api_client.set_skip([])
        except Exception:
            pass
        self._live_isolation_applied = False

    @Slot(result=str)
    def isolationMode(self) -> str:
        """"live" (acting on the running wallpaper), "bench" (a held child), or "off"."""
        if self.isRunning() or self._ab_running:
            return "bench"
        return "live" if self._live_isolation() else "off"

    @Slot(str)
    def solo(self, objid: str) -> None:
        """Toggle one object's membership in the solo set; "" clears the whole set.

        Solo is a SET, not a single pick - the isolator's job is "show me these and nothing
        else", and comparing two objects against each other is the common case. The engine
        renders the set via the complement (see _argv_for)."""
        objid = str(objid or "")
        if not objid:
            self._solo_objs = []
        elif objid in self._solo_objs:
            self._solo_objs = [i for i in self._solo_objs if i != objid]
        else:
            self._solo_objs.append(objid)
            # soloing an object un-skips it: solo hides everything else and skip hides the
            # object itself, so both on one object renders nothing (the gray-screen report)
            self._skip_objs = [i for i in self._skip_objs if i != objid]
        self.stateChanged.emit()
        self._apply_isolation()

    @Slot(str, bool)
    def setSkipObject(self, objid: str, on: bool) -> None:
        objid = str(objid or "")
        if on and objid not in self._skip_objs:
            self._skip_objs.append(objid)
            # skipping a soloed object would render it nowhere: drop it from the solo set
            self._solo_objs = [i for i in self._solo_objs if i != objid]
        elif not on:
            self._skip_objs = [i for i in self._skip_objs if i != objid]
        self.stateChanged.emit()
        self._apply_isolation()

    @Slot(str, bool)
    def setSkipEffect(self, effid: str, on: bool) -> None:
        effid = str(effid or "")
        if on and effid not in self._skip_effs:
            self._skip_effs.append(effid)
        elif not on:
            self._skip_effs = [i for i in self._skip_effs if i != effid]
        self.stateChanged.emit()
        self._schedule_relaunch()

    @Slot()
    def clearIsolation(self) -> None:
        self._solo_objs = []
        self._skip_objs = []
        self._skip_effs = []
        self.stateChanged.emit()
        if self._live_isolation():
            self._clear_live_isolation()
        else:
            self._schedule_relaunch()

    @Slot(result="QVariantMap")
    def isolationState(self) -> dict:
        return {"soloObjects": list(self._solo_objs), "skipObjects": list(self._skip_objs),
                "skipEffects": list(self._skip_effs)}

    @Slot(result="QVariantList")
    def objectList(self) -> list:
        """Objects for the isolator tree (id/name/type).

        In LIVE mode the engine is the source of truth - `list-objects` returns the scene
        it is ACTUALLY showing, which is the whole point of isolating in real time. It
        reports no type, so rows come back typed "object"; a video or web wallpaper has no
        scene graph and yields [] (see isolationMode/liveObjectsNote for the honest label).
        Otherwise fall back to parsing the target directory, as the bench path always has."""
        if self._live_isolation():
            try:
                res = api_client.list_objects()
            except Exception:
                res = None
            if res is not None:
                return [{"objid": str(o.get("id", "")), "name": str(o.get("name") or ""),
                         "type": "object"}
                        for o in (res.get("objects") or [])]
        d = self._target_dir()
        if not d or not os.path.isdir(d):
            return []
        try:
            return list(objects_disc.extract(d))
        except Exception:
            return []

    @Slot(str, bool)
    def setFixOn(self, key: str, on: bool) -> None:
        """A/B a shipped fix: on = normal (fix active), off = escape-hatch env set.

        The contract here is 'flipping a toggle relaunches the engine with that
        fix off', so a flip arms the debounced relaunch and,
        when no bench is running yet, may START one (auto-relaunch permitting)."""
        if on:
            self._toggles_off.discard(key)
        else:
            self._toggles_off.add(key)
        self.stateChanged.emit()
        self._schedule_relaunch(allow_start=True)

    @Slot(str, result=bool)
    def fixOn(self, key: str) -> bool:
        return key not in self._toggles_off

    @Slot(str, bool)
    def setInstrument(self, env: str, on: bool) -> None:
        if on:
            self._instruments.add(env)
        else:
            self._instruments.discard(env)
        self.stateChanged.emit()

        # Push to the LIVE daemon when the engine can take it. This is what makes the
        # Developer area useful against the wallpaper that is actually running rather than
        # only against a bench child we spawned. The engine resets the instrument's latched
        # state on the off->on edge, so a second toggle behaves like the first.
        if env in LIVE_INSTRUMENTS:
            try:
                if api_client.available():
                    api_client.set_instrument(env, on)
            except Exception:
                pass   # a daemon that will not answer must not block the bench path

        self._schedule_relaunch()   # a live bench picks the instrument up on the debounce

    @Slot(str, result=str)
    def instrumentReach(self, env: str) -> str:
        """How far a toggle of `env` actually reaches - the panel must not imply "instant".

        "live"    settable on the running daemon, output starts immediately
        "bench"   launch-time only; takes effect on the next bench run
        Anything launch-time may ALSO be construction-only in the engine, meaning it emits
        while objects are built and so says nothing until a scene loads. That distinction is
        the engine's to report; this is the coarse one the UI can state honestly today.
        """
        return "live" if env in LIVE_INSTRUMENTS else "bench"

    @Slot(str, result=bool)
    def instrumentOn(self, env: str) -> bool:
        return env in self._instruments

    @Slot(str, bool)
    def setRenderDebug(self, key: str, on: bool) -> None:
        """Toggle a --render-debug pass flag (base-only / no-solid-final / pass-log). A live bench
        relaunches (debounced) so the flip is visible on the display, same as an isolation edit."""
        key = str(key or "")
        if key not in {f["key"] for f in RENDER_DEBUG_FLAGS}:
            return
        if on:
            self._render_debug.add(key)
        else:
            self._render_debug.discard(key)
        self.stateChanged.emit()
        self._schedule_relaunch()

    @Slot(str, result=bool)
    def renderDebugOn(self, key: str) -> bool:
        return key in self._render_debug

    @Slot(result="QVariantList")
    def renderDebugFlags(self) -> list:
        return [dict(f) for f in RENDER_DEBUG_FLAGS]

    @Slot(str, str)
    def queueSetProperty(self, name: str, value: str) -> None:
        """Queue a raw --set-property name=value (no validation - developer's honor).
        Applies on next relaunch only. A repeated name replaces the earlier queued value.
        Named queueSetProperty, not setProperty: a slot called setProperty would shadow the
        QObject built-in of the same name with different semantics, a standing footgun."""
        name = str(name or "").strip()
        value = str(value or "").strip()
        if not name:
            return
        self._set_props = [(n, v) for (n, v) in self._set_props if n != name]
        self._set_props.append((name, value))
        self.stateChanged.emit()
        self._schedule_relaunch()

    @Slot(str)
    def clearProperty(self, name: str) -> None:
        name = str(name or "").strip()
        self._set_props = [(n, v) for (n, v) in self._set_props if n != name]
        self.stateChanged.emit()
        self._schedule_relaunch()

    @Slot(result="QVariantList")
    def setProperties(self) -> list:
        """The queued raw overrides for the 7d readout, in composed order."""
        return [{"name": n, "value": v} for (n, v) in self._set_props]

    @Slot(str, str, result=bool)
    def setEnvLine(self, key: str, value: str) -> bool:
        """Queue (or replace) one raw KEY=VALUE launch-env line for the editable env block.

        The key must be a shell identifier ([A-Za-z_][A-Za-z0-9_]*) or the line is refused and
        False is returned - a bad key never enters the queue. The value is coerced to a single
        line (newlines stripped). A repeated key replaces its earlier value, staying one entry,
        in first-seen order. Applies on the NEXT relaunch (folded into compose_env), never
        hot - the cockpit's honesty rule. Returns True when the line was accepted."""
        key = str(key or "").strip()
        if not _is_shell_ident(key):
            return False
        value = str(value or "").replace("\n", " ").replace("\r", " ").strip()
        existing = next((i for i, (k, _v) in enumerate(self._env_lines) if k == key), -1)
        if existing >= 0:
            self._env_lines[existing] = (key, value)
        else:
            self._env_lines.append((key, value))
        self.stateChanged.emit()
        return True

    @Slot(str)
    def removeEnvLine(self, key: str) -> None:
        """Drop one queued env line by key. A no-op if the key is not queued."""
        key = str(key or "").strip()
        self._env_lines = [(k, v) for (k, v) in self._env_lines if k != key]
        self.stateChanged.emit()

    @Slot()
    def clearEnvLines(self) -> None:
        """Empty the queued raw env block."""
        if self._env_lines:
            self._env_lines = []
            self.stateChanged.emit()

    @Slot()
    def applyPending(self) -> None:
        """Apply queued edits to the running bench.

        The env-line setters intentionally do not each schedule a relaunch - a block edit
        would fire one per line - so the Apply button asks for the relaunch once, after the
        whole block has been set. Without this, Apply was the only control in the tab that
        appeared to do nothing.
        """
        self._schedule_relaunch()

    @Slot(result="QVariantList")
    def envLines(self) -> list:
        """The queued raw env lines for the editor, in composed (first-seen) order."""
        return [{"key": k, "value": v} for (k, v) in self._env_lines]

    @Slot(str, result=bool)
    def envKeyValid(self, key: str) -> bool:
        """Whether `key` is a valid shell identifier - lets the editor mark a bad line without
        queueing it (the same rule setEnvLine enforces)."""
        return _is_shell_ident(str(key or "").strip())

    @Slot(result="QVariantList")
    def ourToggles(self) -> list:
        return [dict(t) for t in OUR_TOGGLES]

    @Slot(result="QVariantList")
    def instruments(self) -> list:
        return [dict(i) for i in INSTRUMENTS]

    @Slot(str, result="QVariantList")
    def scopedInstruments(self, subsystem: str) -> list:
        """The census-verified log instruments scoped to one subsystem lens.

        Returns the INSTRUMENTS entries whose env is assigned to `subsystem` in
        SUBSYSTEM_INSTRUMENTS, in the map's declared order. A subsystem with no verified
        instrument returns [] so its lens draws the honest empty state rather than the whole
        nine-item list on every tab."""
        want = SUBSYSTEM_INSTRUMENTS.get(str(subsystem or ""), [])
        by_env = {i["env"]: i for i in INSTRUMENTS}
        return [dict(by_env[e]) for e in want if e in by_env]

    @Slot(str, result=bool)
    def lensAlwaysOn(self, subsystem: str) -> bool:
        """True when this lens streams engine output that no switch gates.

        The empty state has to say something different for these: telling the operator to
        "switch an instrument on" is wrong advice when there is nothing to switch.
        """
        return bool(ALWAYS_ON_TAGS.get(str(subsystem or "")))

    @Slot(result="QVariantList")
    def subsystems(self) -> list:
        return list(SUBSYSTEMS)

    @Slot(str, result="QVariantList")
    def scopedReadoutTags(self, subsystem: str) -> list:
        """The engine log-line prefixes whose output belongs on this subsystem's readout.

        Flat list of the "tags" of every instrument scoped to the lens - the QML readout
        filters the live bench log stream on them. Empty for a lens with no verified
        instrument AND no always-on channel keeps the honest empty state.

        Always-on tags come FIRST: they are literal engine strings, and the QML matcher takes
        the first tag that hits, so a generic instrument prefix must not shadow them.
        """
        tags: list[str] = list(ALWAYS_ON_TAGS.get(str(subsystem or ""), []))
        for inst in self.scopedInstruments(subsystem):
            tags.extend(inst.get("tags") or [])
        return tags

    def _dev_outputs(self) -> list[str]:
        """Compositor output names for the bench launch. LWE_DEV_MONITOR pins one;
        empty means unresolvable - refuse."""
        mon = os.environ.get("LWE_DEV_MONITOR", "")
        if mon:
            return [mon]
        try:
            out = subprocess.run(["hyprctl", "-j", "monitors"],
                                 capture_output=True, text=True, timeout=2, check=False)
            if out.returncode == 0 and out.stdout.strip():
                return [str(m["name"]) for m in json.loads(out.stdout)
                        if isinstance(m, dict) and m.get("name")]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            pass
        return []

    def _solo_argv(self) -> list[str]:
        """--render-debug flags that render the solo SET, whatever its size.

        The engine's `object=<id>` filter holds ONE id - `settings.render.debug.objectFilter`
        is a std::optional<int> (ApplicationContext.cpp), so repeating the flag just overwrites
        it, and CScene drops every object whose id differs. `skip-object=` accumulates into a
        vector instead. So one soloed object uses the native filter, and two or more are
        rendered as the COMPLEMENT: skip every other object in the scene. Same picture, no
        engine change.

        Ids go to std::stoi behind a [[noreturn]] sLog.exception, so a non-numeric id would
        abort the engine at parse time - the complement is emitted for numeric ids only (the
        filter path never enumerates, so it keeps whatever the isolator listed)."""
        solo = list(self._solo_objs)
        if not solo:
            return []
        if len(solo) == 1:
            return ["--render-debug", "object=" + solo[0]]
        others = [str(o.get("objid", "")) for o in self.objectList()]
        others = [i for i in others
                  if i.lstrip("-").isdigit() and i not in solo and i not in self._skip_objs]
        if not others:
            # nothing enumerable to hide against (unreadable scene.json / non-scene target):
            # fall back to the native single-object filter rather than silently rendering
            # everything, which would read as "solo did nothing".
            return ["--render-debug", "object=" + solo[0]]
        out: list[str] = []
        for i in others:
            out += ["--render-debug", "skip-object=" + i]
        return out

    def _argv_for(self, window: str | None) -> list[str]:
        """Engine argv for the current target + isolation. `window` forces a --window
        geometry (the A/B split halves; GLFW windowed mode is broken on
        the Wayland path, so A/B needs a redesign); None renders to the REAL display via
        --screen-root layer-shell surfaces, exactly like every live engine launch. The
        old default was a --window bench, which the engine's Wayland path cannot honor -
        it errored out, died, and the resume rolled the user a different wallpaper.
        Returns [] when no output resolves (callers refuse instead of launching blind)."""
        d = self._target_dir()
        argv = [_engine_bin(), "--assets-dir", _assets_dir(), "--fps", "30",
                "--scaling", "default", "--silent", "--no-audio-processing",
                "--disable-mouse", "--no-fullscreen-pause"]
        if window:
            argv += ["--window", window]
        else:
            outs = self._dev_outputs()
            if not outs:
                return []
            for o in outs:
                argv += ["--screen-root", o]
        argv += self._solo_argv()
        for i in self._skip_objs:
            argv += ["--render-debug", "skip-object=" + i]
        for e in self._skip_effs:
            argv += ["--render-debug", "skip-effect=" + e]
        # render-debug pass flags (design 7d): emitted in the fixed order they are declared so
        # the composed line is deterministic regardless of the set's iteration order.
        for f in RENDER_DEBUG_FLAGS:
            if f["key"] in self._render_debug:
                argv += ["--render-debug", f["key"]]
        # queued raw property overrides (design 7d, relaunch-only): --set-property name=value.
        for name, value in self._set_props:
            argv += ["--set-property", f"{name}={value}"]
        argv += ["--bg", d]
        return argv

    def compose_argv(self) -> list[str]:
        """The engine argv for the current target + isolation (testable, no side effects)."""
        return self._argv_for(None)

    def compose_env(self) -> dict[str, str]:
        """Extra environment for the run: instruments on + escape-hatches for fixes flipped off
        + the developer's raw editable env lines, which apply LAST so an explicit
        raw line is the developer's final word over an instrument/toggle default."""
        env: dict[str, str] = {}
        for e in self._instruments:
            env[e] = "1"
        off_by_key = {t["key"]: t for t in OUR_TOGGLES}
        for key in self._toggles_off:
            t = off_by_key.get(key)
            if t and t["off"] is not None:
                env[t["env"]] = t["off"]
        for k, v in self._env_lines:
            env[k] = v
        return env

    def unset_env(self) -> list[str]:
        """Variables the run must NOT carry: presence-only switches flipped off.

        compose_env can only assign, and for a switch the engine tests with
        `getenv(...) != nullptr` there is no string that means off - assigning "0" is
        still presence, so the off row turns the thing on. These are removed from the
        inherited environment instead. A raw env line naming the same key wins, as
        everywhere else: the developer's explicit word is final.
        """
        raw = {k for k, _ in self._env_lines}
        off_by_key = {t["key"]: t for t in OUR_TOGGLES}
        out = []
        for key in self._toggles_off:
            t = off_by_key.get(key)
            if t and t["off"] is None and t["env"] not in raw:
                out.append(t["env"])
        return out

    @Slot(result=str)
    def launchPreview(self) -> str:
        """The composed command line as text (the launch-flags readout / click-to-copy)."""
        env = self.compose_env()
        assign = " ".join(f"{k}={v}" for k, v in sorted(env.items()))
        # an unset is part of the launch and has to show in the preview, or the readout
        # describes a run the panel is not performing. It needs `env -u` to stay a
        # runnable line - a bare `-u KEY` in an assignment prefix is not valid shell.
        unset = " ".join(f"-u {k}" for k in sorted(self.unset_env()))
        prefix = f"env {unset} {assign}".strip() if unset else assign
        cmd = " ".join(self.compose_argv())
        return (prefix + " " + cmd).strip()

    @Slot(result=bool)
    def isRunning(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    def set_engine_peers(self, peers: list) -> None:
        """Engine-conflict peers: symmetrical refusal with the Workshop
        preview - two engines on one display causes corruption."""
        self._engine_peers = [p for p in peers if p is not None]

    @Slot(result=bool)
    def engineBusy(self) -> bool:
        return self.isRunning() or self._ab_running or self.isHolding()

    def _peer_conflict(self) -> bool:
        for p in getattr(self, "_engine_peers", []):
            try:
                if p.engineBusy():
                    return True
            except Exception:
                continue
        return False

    @Slot()
    def startBench(self) -> None:
        """Launch the instrumented engine over a daemon standdown."""
        if self.isRunning() or self._ab_running:
            return
        if self._peer_conflict():
            self.logLine.emit("dev: a Workshop preview is open - close it to bench")
            return
        d = self._target_dir()
        if not d or not os.path.isdir(d):
            self.logLine.emit("dev: no valid target selected")
            return
        if not self._argv_for(None):
            # resolve BEFORE the standdown so a refusal never churns the outputs
            self.logLine.emit("dev: no display output detected (is the compositor reachable?)")
            return
        bench_courier.standdown()
        self._run_start = time.monotonic()
        self._start_proc()

    def _start_proc(self) -> None:
        """Spawn the engine child with the current argv + env. Assumes the standdown is held."""
        d = self._target_dir()
        if not d or not os.path.isdir(d):
            return
        if not self.compose_argv():
            self.logLine.emit("dev: no display output detected; bench not started")
            return
        self._log_buf = []
        proc = QProcess(self)
        qenv = QProcessEnvironment.systemEnvironment()
        for k, v in self.compose_env().items():
            qenv.insert(k, v)
        for k in self.unset_env():
            qenv.remove(k)   # presence-only switch flipped off: only absence means off
        proc.setProcessEnvironment(qenv)
        # SEPARATE channels, not merged. The engine writes diagnostics to stderr
        # (main.cpp:22 sLog.addError -> std::cerr) and instrument output to stdout. Merging
        # them threw the severity away, and the console then tried to recover it by
        # searching each line for the word "error" - which 175 of the engine's 190
        # sLog.error messages do not contain. "Could not parse puppet X: not an MDLV
        # container" is an error that never says "error", so it was dropped silently.
        # The stream IS the severity; stop guessing from the wording.
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        proc.readyReadStandardOutput.connect(self._drain)
        proc.readyReadStandardError.connect(self._drain_stderr)
        proc.finished.connect(self._on_finished)
        argv = self.compose_argv()
        self._active_wid = os.path.basename(d.rstrip("/"))
        self._proc = proc
        proc.start(argv[0], argv[1:])
        self.stateChanged.emit()

    def _schedule_relaunch(self, allow_start: bool = False) -> None:
        """An edit while a bench is live queues ONE debounced relaunch. With
        auto-relaunch off, the edit just updates the composed argv for the next manual start.
        allow_start (the Tour toggles) also lets the debounce START a bench when none runs,
        so a flip on the idle cockpit still demos the regression live per the 7a contract."""
        if not self._auto_relaunch or self._ab_running:
            return
        if self.isRunning():
            self._relaunch_timer.start()
        elif allow_start:
            self._pending_start = True
            self._relaunch_timer.start()

    def _do_relaunch(self) -> None:
        """Reap the current engine WITHOUT re-acquiring the outputs, then relaunch with fresh
        flags - or, for a Tour flip on the idle cockpit, start the bench outright."""
        pending = self._pending_start
        self._pending_start = False
        if self.isRunning():
            self._reap_no_resume()
            # a kill()-ed engine can outlive waitForFinished by a beat; for web targets the
            # dying process still holds the browser singleton lock, so let it clear first
            bench_courier.wait_clear()
            self._start_proc()   # the release holds across the swap - no live-wallpaper flicker
            return
        if pending and self._auto_relaunch and not self._ab_running:
            d = self._target_dir()
            if d and os.path.isdir(d):
                self.startBench()
            else:
                self.logLine.emit("dev: flip queued - no resolvable target to relaunch "
                                  "(pick a target - now-playing resolves from the running engine)")

    def _reap_no_resume(self) -> None:
        """Terminate the running engine but keep the outputs released (the relaunch owns them)."""
        p = self._proc
        self._proc = None
        if p is None:
            return
        try:
            p.finished.disconnect(self._on_finished)
        except (RuntimeError, TypeError):
            pass
        p.terminate()
        if not p.waitForFinished(1000):
            p.kill()
        p.deleteLater()

    def _primary_geometry(self) -> tuple[int, int, int, int] | None:
        """(x, y, w, h) of the focused output, or None if the compositor can't be queried."""
        try:
            out = subprocess.run(["hyprctl", "-j", "monitors"],
                                 capture_output=True, text=True, timeout=2, check=False)
            if out.returncode == 0 and out.stdout.strip():
                mons = json.loads(out.stdout)
                if mons:
                    m = next((x for x in mons if x.get("focused")), mons[0])
                    return (int(m.get("x", 0)), int(m.get("y", 0)),
                            int(m["width"]), int(m["height"]))
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            pass
        return None

    def _ab_spawn_geometry(self) -> str | None:
        """The --window geometry for ONE exhibit: a monitor-ratio QUADRANT of the focused
        output. Matching the output's aspect makes the scene project exactly as it does as
        a wallpaper (a half-width full-height window cropped wildly - owner finding). The
        position part is ignored on Wayland (clients cannot place themselves); the
        compositor places both windows via _ab_place_tick."""
        geo = self._primary_geometry()
        if not geo:
            return None
        _x, _y, w, h = geo
        return f"0x0x{w // 2}x{h // 2}"

    def _hypr_dispatch(self, expr: str) -> bool:
        """Issue one Lua-form dispatcher (Hyprland 0.55+ hyprctl evaluates the dispatch
        tail as Lua: hl.dsp.window.move({...}) etc). The classic flat syntax parses as a
        Lua error and does NOTHING - measured live; the old demo's "placement" was
        actually the tiler. Returns True on "ok"; logs the first failure per session."""
        out = self._hyprctl(["dispatch", expr]).strip()
        if out == "ok":
            return True
        if not getattr(self, "_hypr_dispatch_warned", False):
            self._hypr_dispatch_warned = True
            self.logLine.emit(f"dev: hyprctl dispatch failed ({out.splitlines()[0] if out else 'no output'}); "
                              "exhibit placement needs Hyprland 0.55+")
        return False

    @staticmethod
    def _hyprctl(args: list[str]) -> str:
        """Run hyprctl, returning stdout ("" on any failure). Placement is best-effort:
        without a compositor the exhibits still spawn, just untended."""
        try:
            r = subprocess.run(["hyprctl", *args], capture_output=True, text=True,
                               timeout=2, check=False)
            return r.stdout if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    def _hyprctl_clients(self) -> list:
        out = self._hyprctl(["-j", "clients"])
        if not out:
            return []
        try:
            data = json.loads(out)
            return data if isinstance(data, list) else []
        except ValueError:
            return []

    def _ab_side_env(self, side: str) -> dict[str, str]:
        """The launch env for one exhibit: shared instruments + that side's fixes-off set
        + its raw env lines, which apply LAST (the developer's final word).

        Presence-only switches are EXCLUDED here and returned by _ab_side_unset instead -
        see that method for why assigning them is worse than useless.
        """
        env = {e: "1" for e in self._instruments}
        by_key = {t["key"]: t for t in OUR_TOGGLES}
        for key in self._ab_off.get(side, set()):
            t = by_key.get(key)
            if t and t["off"] is not None:
                env[t["env"]] = t["off"]
        for k, v in self._ab_envlines.get(side, []):
            env[k] = v
        return env

    def _ab_side_unset(self, side: str) -> list[str]:
        """Variables this exhibit must NOT carry: presence-only switches flipped off.

        The A/B path once missed the unset fix that landed on the single bench, and the
        failure was silent rather than loud: `off` is None for a presence-only switch, and
        PySide6 coerces None to "" in QProcessEnvironment.insert rather than raising. An empty
        string is still a non-NULL pointer to `getenv`, so the exhibit meant to show the fix
        OFF ran with it ON - and the split compared fix-on against fix-on and reported no
        difference. A lying toggle inside the tool built to catch lying toggles.
        """
        raw = {k for k, _ in self._ab_envlines.get(side, [])}
        by_key = {t["key"]: t for t in OUR_TOGGLES}
        return [t["env"] for key in self._ab_off.get(side, set())
                if (t := by_key.get(key)) and t["off"] is None and t["env"] not in raw]

    def _spawn_ab(self, window: str, env: dict[str, str],
                  unset: "list[str] | None" = None) -> QProcess:
        # A/B is a visual side-by-side; its engines are not streamed to the log console (the
        # single-bench console reads self._proc, which is None during A/B).
        proc = QProcess(self)
        qenv = QProcessEnvironment.systemEnvironment()
        for k, v in env.items():
            qenv.insert(k, v)
        for k in (unset or []):
            qenv.remove(k)   # presence-only switch flipped off: only absence means off
        proc.setProcessEnvironment(qenv)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.finished.connect(self._ab_finished)
        argv = self._argv_for(window)
        proc.start(argv[0], argv[1:])
        return proc

    def _ab_launch(self) -> None:
        """Spawn exhibit A at quadrant geometry; B follows from the placement tick once A's
        window maps (10s fallback) - two cold loads of the same heavy scene serialize, so
        parallel spawning left one window missing for ~25s (measured). Side identity is
        FIXED (A is always the left-placed, A-labeled window); swap moves the FIX STATE
        between sides, so the labels and borders never lie."""
        geo = self._ab_spawn_geometry() or "0x0x1280x720"
        self._proc_a = self._spawn_ab(geo, self._ab_side_env("A"),
                                      self._ab_side_unset("A"))
        self._proc_b = None
        self._ab_b_due = time.monotonic() + 10.0
        self._ab_placed = set()
        self._ab_place_tries = {}
        self._ab_place_timer.start()

    def _ab_spawn_b(self) -> None:
        if not self._ab_running or self._proc_b is not None:
            return
        geo = self._ab_spawn_geometry() or "0x0x1280x720"
        self._proc_b = self._spawn_ab(geo, self._ab_side_env("B"),
                                      self._ab_side_unset("B"))

    def _ab_place_tick(self) -> None:
        """One loop, three duties: (1) place each exhibit AS IT MAPS (a heavy scene makes
        the second window take 20s+; two cold loads of the same scene serialize), (2)
        VERIFY the placement stuck and retry - Hyprland's own initial-float placement can
        land after ours and override it (measured live: dispatched, then found at the
        default center) - and (3) keep the exhibit chips glued to their windows. A side
        stops being corrected once verified (or after 5 attempts): from then on its
        position belongs to the user."""
        if not self._ab_running:
            self._ab_place_timer.stop()
            return
        # stuck-freeze dead-man FIRST (before any early return): no drag traffic for 2s
        # means the release was eaten (fullscreen mid-press steals the grab) - clear so
        # the glue resumes
        if self._ab_drag_side and time.monotonic() - self._ab_drag_last > 2.0:
            self._ab_drag_side = ""
        clients = self._hyprctl_clients()
        pid_a = int(self._proc_a.processId()) if self._proc_a is not None else 0
        pid_b = int(self._proc_b.processId()) if self._proc_b is not None else 0
        by_pid = {c.get("pid"): c for c in clients}
        # staggered B: spawn once A's window is up (or on the fallback clock, so a
        # missing compositor never strands side B entirely)
        if self._proc_b is None and (by_pid.get(pid_a) is not None
                                     or time.monotonic() > self._ab_b_due):
            self._ab_spawn_b()
            pid_b = int(self._proc_b.processId()) if self._proc_b is not None else 0
        if not clients:
            return
        geo = self._primary_geometry()
        for side, win in (("A", by_pid.get(pid_a)), ("B", by_pid.get(pid_b))):
            if win:
                self._ab_addr[side] = str(win.get("address", ""))
                self._ab_fullscreen[side] = bool(win.get("fullscreen"))

        for side, win in (("A", by_pid.get(pid_a)), ("B", by_pid.get(pid_b))):
            if not (geo and win) or side in self._ab_placed:
                continue
            mx, my, mw, mh = geo
            qw, qh = mw // 2, mh // 2
            qy = my + mh // 4
            qx = mx if side == "A" else mx + qw
            at = win.get("at") or [0, 0]
            if abs(int(at[0]) - qx) <= 4 and abs(int(at[1]) - qy) <= 4:
                self._ab_placed.add(side)
                self.logLine.emit(f"dev: exhibit {side} placed at {qx},{qy} ({qw}x{qh})")
                continue
            attempts = self._ab_place_tries.get(side, 0)
            if attempts >= 5:
                self._ab_placed.add(side)
                self.logLine.emit(f"dev: exhibit {side} kept its own position")
                continue
            self._ab_place_tries[side] = attempts + 1
            col = "7f77dd" if side == "A" else "ef9f27"
            addr = f"address:{win['address']}"
            # fixed-size windows auto-float; only correct one that is somehow tiled
            if not win.get("floating"):
                self._hypr_dispatch(f'hl.dsp.window.float({{ window = "{addr}" }})')
            self._hypr_dispatch(
                f'hl.dsp.window.resize({{ x = {qw}, y = {qh}, window = "{addr}" }})')
            self._hypr_dispatch(
                f'hl.dsp.window.move({{ x = {qx}, y = {qy}, window = "{addr}" }})')
            # side-fast borders, compositor-attached: they survive any user drag
            for prop, val in (("active_border_color", f"rgb({col})"),
                              ("inactive_border_color", f"rgb({col})"),
                              ("border_size", "3")):
                self._hypr_dispatch(
                    f'hl.dsp.window.set_prop({{ prop = "{prop}", value = "{val}", '
                    f'window = "{addr}" }})')

        # glue the gesture overlays (full window area) and the chips (corner tags) to
        # their exhibits; re-top every tick regardless of movement - a click on the
        # engine window raises it above them (the on-top hint is client-side fiction
        # on Wayland), and half a second of buried is the acceptable worst case
        titled = {c.get("title"): c for c in clients
                  if str(c.get("title", "")).startswith(("lwe-chip-", "lwe-overlay-"))}
        for side, win in (("A", by_pid.get(pid_a)), ("B", by_pid.get(pid_b))):
            if not win or side == self._ab_drag_side:
                continue
            at = win.get("at") or [0, 0]
            size = win.get("size") or [0, 0]
            overlay = titled.get(f"lwe-overlay-{side}")
            if overlay:
                oaddr = f"address:{overlay['address']}"
                moved = False
                if not overlay.get("floating"):
                    self._hypr_dispatch(f'hl.dsp.window.float({{ window = "{oaddr}" }})')
                    moved = True
                oat = overlay.get("at") or [0, 0]
                osz = overlay.get("size") or [0, 0]
                if [int(osz[0]), int(osz[1])] != [int(size[0]), int(size[1])]:
                    self._hypr_dispatch(
                        f'hl.dsp.window.resize({{ x = {int(size[0])}, y = {int(size[1])}, '
                        f'window = "{oaddr}" }})')
                    moved = True
                if abs(int(oat[0]) - int(at[0])) > 2 or abs(int(oat[1]) - int(at[1])) > 2:
                    self._hypr_dispatch(
                        f'hl.dsp.window.move({{ x = {int(at[0])}, y = {int(at[1])}, '
                        f'window = "{oaddr}" }})')
                    moved = True
                # re-top ONLY after an actual move: the every-tick alter_zorder beat the
                # palette's pin and flashed stacking twice a second
                if moved:
                    self._hypr_dispatch(
                        f'hl.dsp.window.alter_zorder({{ mode = "top", window = "{oaddr}" }})')
            chip = titled.get(f"lwe-chip-{side}")
            if chip:
                caddr = f"address:{chip['address']}"
                cmoved = False
                if not chip.get("floating"):
                    self._hypr_dispatch(f'hl.dsp.window.float({{ window = "{caddr}" }})')
                    cmoved = True
                cx, cy = int(at[0]) + 14, int(at[1]) + 14
                cat = chip.get("at") or [0, 0]
                if abs(int(cat[0]) - cx) > 2 or abs(int(cat[1]) - cy) > 2:
                    self._hypr_dispatch(
                        f'hl.dsp.window.move({{ x = {cx}, y = {cy}, window = "{caddr}" }})')
                    cmoved = True
                if cmoved:
                    self._hypr_dispatch(
                        f'hl.dsp.window.alter_zorder({{ mode = "top", window = "{caddr}" }})')

        # the palette's pin is a Qt hint the compositor ignores; enforce it for real while
        # exhibits exist (they are what was overtaking it). Desired state arrives from the
        # QML pin toggle; clients shows the live pinned flag.
        if self._pal_pinned:
            for c in clients:
                t = str(c.get("title", ""))
                if t.startswith("Developer tools") and not c.get("pinned"):
                    self._hypr_dispatch(
                        f'hl.dsp.window.pin({{ window = "address:{c["address"]}" }})')
                    break

    def _ab_finished(self, *_args: object) -> None:
        """One A/B engine exited on its own. If neither side is left running, hand the
        outputs back - a released daemon holds its release forever otherwise."""
        if not self._ab_running:
            return

        def alive(p: QProcess | None) -> bool:
            return p is not None and p.state() != QProcess.ProcessState.NotRunning

        if not alive(self._proc_a) and not alive(self._proc_b):
            self.stopAB()

    def _reap_ab(self) -> None:
        """Terminate both A/B engines (keeps the outputs released; stopAB resumes). Disconnect the
        death handler first so a deliberate reap does not fire the auto-release."""
        for attr in ("_proc_a", "_proc_b"):
            p = getattr(self, attr, None)
            setattr(self, attr, None)
            if p is None:
                continue
            try:
                p.finished.disconnect(self._ab_finished)
            except (RuntimeError, TypeError):
                pass
            p.terminate()
            if not p.waitForFinished(1000):
                p.kill()
            p.deleteLater()

    @Slot()
    def startAB(self) -> None:
        """Launch the two exhibits with their configured per-side loadouts (matrix
        design: no single compare key - each side carries its own
        fixes-off set and raw env lines, edited on the Fix on/off sub-tab)."""
        if self._ab_running or self.isRunning():
            return
        if self._peer_conflict():
            self.logLine.emit("dev: a Workshop preview is open - close it to run A/B")
            return
        d = self._target_dir()
        if not d or not os.path.isdir(d):
            self.logLine.emit("dev: no valid target selected")
            return
        if not self._ab_spawn_geometry():
            # same guard as the single bench: never launch windowed over an unknown surface.
            self.logLine.emit("dev: no display output detected; A/B needs a resolvable monitor")
            return
        bench_courier.standdown()
        self._run_start = time.monotonic()
        self._ab_running = True
        d = self._target_dir()
        self._active_wid = os.path.basename(d.rstrip("/")) if d else ""
        self._ab_launch()
        self.stateChanged.emit()

    @Slot()
    def stopAB(self) -> None:
        """Reap both A/B engines and resume live rotation."""
        was = self._ab_running
        self._ab_place_timer.stop()
        self._ab_addr = {}
        self._ab_fullscreen = {}
        self._reap_ab()
        if was:
            self._ab_running = False
            self._run_start = 0.0
            bench_courier.resume()
        self.stateChanged.emit()

    @Slot(result=bool)
    def abRunning(self) -> bool:
        return self._ab_running

    @Slot(str, int, int)
    def exhibitDragBy(self, side: str, dx: int, dy: int) -> None:
        """Move one exhibit by a delta (the chip is the drag handle; the engine window
        cannot host gestures - frozen repo - so the chip's mouse drives compositor-side
        relative moves). Cross-monitor works because coordinates are global layout space.
        No-ops while that side is fullscreen."""
        side = str(side or "").upper()
        self._ab_drag_last = time.monotonic()
        addr = self._ab_addr.get(side)
        if not addr or self._ab_fullscreen.get(side):
            return
        self._hypr_dispatch(
            f'hl.dsp.window.move({{ x = {int(dx)}, y = {int(dy)}, relative = true, '
            f'window = "address:{addr}" }})')

    @Slot(str, bool)
    def exhibitDragActive(self, side: str, on: bool) -> None:
        """While a chip drag is live the follower must NOT re-park that side's chip: a
        mid-drag repark shifts the chip under the cursor, the local mouse frame rebases,
        and the next delta cancels the move (oscillation). The chip holds still as the
        handle; on release the follower snaps it onto the exhibit."""
        self._ab_drag_side = str(side or "").upper() if on else ""
        self._ab_drag_last = time.monotonic()

    @Slot(bool)
    def setPalettePinned(self, pinned: bool) -> None:
        """The palette's pin toggle, mirrored so the follower can enforce it compositor-
        side (the Qt stays-on-top hint is fiction on Wayland)."""
        self._pal_pinned = bool(pinned)

    @Slot(str)
    def exhibitToggleFullscreen(self, side: str) -> None:
        """Double-click on a chip: toggle that exhibit fullscreen on whatever monitor it
        sits on (double-click again to return - the chip stays on top as the way back)."""
        side = str(side or "").upper()
        addr = self._ab_addr.get(side)
        if not addr:
            return
        self._hypr_dispatch(
            f'hl.dsp.window.fullscreen({{ mode = "maximized", window = "address:{addr}" }})')

    def _ab_side_desc(self, side: str) -> str:
        """One terse line describing a side as deltas vs stock: 'stock', or the offs by
        short env name plus an env-line count ('FRONTFACE off · TINTFIX off · +2 env')."""
        parts = []
        for t in OUR_TOGGLES:
            if t["key"] in self._ab_off.get(side, set()):
                parts.append(t["env"].removeprefix("LWE_") + " off")
        n_env = len(self._ab_envlines.get(side, []))
        if n_env:
            parts.append(f"+{n_env} env")
        return " \u00b7 ".join(parts) if parts else "stock"

    @Slot(result="QVariantMap")
    def abState(self) -> dict:
        """Per-side descriptions for the palette + deck 'Playing on A/B' readout."""
        return {"running": self._ab_running,
                "sideA": self._ab_side_desc("A"), "sideB": self._ab_side_desc("B")}

    @Slot(str, str, bool)
    def setABFix(self, side: str, key: str, on: bool) -> None:
        """Set one fix's state on one side (the Fix on/off matrix). Running exhibits keep
        their launched env until Apply relaunches them - relaunching per flip would cost
        the double scene load every click."""
        side = str(side or "").upper()
        if side not in self._ab_off or key not in {t["key"] for t in OUR_TOGGLES}:
            return
        if on:
            self._ab_off[side].discard(key)
        else:
            self._ab_off[side].add(key)
        self.stateChanged.emit()

    @Slot(str, str, result=bool)
    def abFixOn(self, side: str, key: str) -> bool:
        return key not in self._ab_off.get(str(side or "").upper(), set())

    @Slot(str, str)
    def setABEnvText(self, side: str, text: str) -> None:
        """Raw KEY=VALUE lines for one side (power users: anything we set by hand on the
        command line while debugging). Keys must be shell identifiers; bad lines are
        dropped with a log note, same contract as the cockpit env editor."""
        side = str(side or "").upper()
        if side not in self._ab_envlines:
            return
        lines, bad = [], []
        for raw in str(text or "").splitlines():
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            k, sep, v = raw.partition("=")
            k = k.strip()
            if not sep or not k.isidentifier():
                bad.append(raw)
                continue
            lines.append((k, v.strip()))
        self._ab_envlines[side] = lines
        if bad:
            self.logLine.emit(f"dev: exhibit {side} env - ignored {len(bad)} bad line(s): "
                              + "; ".join(bad[:3]))
        self.stateChanged.emit()

    @Slot(str, result=str)
    def abEnvText(self, side: str) -> str:
        return "\n".join(f"{k}={v}"
                          for k, v in self._ab_envlines.get(str(side or "").upper(), []))

    @Slot()
    def abReset(self) -> None:
        """Both sides back to stock (all fixes on, no env lines) - one click out of any
        leftover experiment state."""
        self._ab_off = {"A": set(), "B": set()}
        self._ab_envlines = {"A": [], "B": []}
        self.stateChanged.emit()

    @Slot(result=bool)
    def isHolding(self) -> bool:
        """True while any dev hold owns the outputs (single bench or A/B); drives the cockpit dot."""
        return self.isRunning() or self._ab_running

    @Slot()
    def stopHold(self) -> None:
        """Release whichever dev hold is active (the click-to-release bail-out)."""
        if self._ab_running:
            self.stopAB()
        elif self.isRunning():
            self.stopBench()

    # per-drain emission cap: a per-frame instrument (LIGHTDUMP prints per model per frame)
    # floods hundreds of lines a chunk; each emit crosses into QML and touches two ListModels,
    # which froze the GUI the moment such a bench started. The console/readout are live
    # monitors, not recorders - the full text still lands in _log_buf for the run tail.
    _LOG_EMIT_MAX = 40

    def _drain(self) -> None:
        if self._proc is None:
            return
        data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        matched: list[str] = []
        for line in data.splitlines():
            self._log_buf.append(line)
            # shader-dump lines carry neither an LWE- tag nor the word "error" (sLog.error
            # writes the raw buffer), so without their own prefixes here the SHADERDUMP
            # instrument's output never reached the console or readout at all
            if ("LWE-" in line or "LWE_" in line or "error" in line.lower()
                    or line.startswith(("FRAGSRC", "GLSL "))):
                matched.append(line)
        del self._log_buf[:-4000]   # bound the run buffer too (tail keeps the last 200)
        dropped = len(matched) - self._LOG_EMIT_MAX
        if dropped > 0:
            self.logLine.emit(f"dev: log stream heavy - showing last {self._LOG_EMIT_MAX} "
                              f"of {len(matched)} lines this read")
            matched = matched[-self._LOG_EMIT_MAX:]
        for line in matched:
            self.logLine.emit(line)

    def _drain_stderr(self) -> None:
        """Everything the engine writes to stderr, unfiltered.

        No allowlist here on purpose. stdout carries instrument output, which is high-volume
        and worth scoping to LWE-* tags; stderr carries diagnostics, and a diagnostic the
        panel decided not to show is the failure this whole path existed to prevent. The
        flood cap still applies - a monitor that freezes the GUI shows nothing either.
        """
        proc = self._proc
        if proc is None:
            return
        data = bytes(proc.readAllStandardError()).decode("utf-8", "replace")
        lines = [ln for ln in data.splitlines() if ln.strip()]
        if not lines:
            return
        self._log_buf.extend(lines)
        del self._log_buf[:-4000]
        dropped = len(lines) - self._LOG_EMIT_MAX
        if dropped > 0:
            self.logLine.emit(f"dev: stderr heavy - showing last {self._LOG_EMIT_MAX} "
                              f"of {len(lines)} lines this read")
            lines = lines[-self._LOG_EMIT_MAX:]
        for line in lines:
            self.logLine.emit(line)

    def _on_finished(self, code: int, _status: object) -> None:
        tail = "\n".join(self._log_buf[-200:])
        self._runs.insert(0, {"ts": _now_hhmmss(), "code": int(code), "tail": tail})
        self._runs = self._runs[:20]
        self._proc = None
        self._run_start = 0.0
        bench_courier.resume()
        self.runsChanged.emit()
        self.stateChanged.emit()

    @Slot()
    def stopBench(self) -> None:
        self._relaunch_timer.stop()
        if self._proc is not None and self.isRunning():
            self._proc.terminate()
            if not self._proc.waitForFinished(1500):
                self._proc.kill()

    _JOURNAL_EMIT_MAX = 40

    def _journal_unit(self) -> str:
        """The unit to follow. Mirrors Backend._master_service so both surfaces name the
        same service."""
        return C.ENGINE_SERVICE

    @Slot(result=bool)
    def journalRunning(self) -> bool:
        return self._journal_proc is not None

    @Slot()
    def startJournal(self) -> None:
        """Follow the engine service's journal into the log console.

        Started only on request. DevView is instantiated once and never destroyed
        (Main.qml mounts it directly, not through a Loader), so an auto-start would leave
        a follower running for the whole session behind whatever view is showing.

        Abnormal-exit caveat, accepted rather than engineered around: if the panel is SIGKILLed
        the follower is orphaned, and because this pane is expected to be quiet it may not write
        for a long time, so its SIGPIPE death can be slow. The clean fix is PR_SET_PDEATHSIG via
        QProcess.setChildProcessModifier, which this PySide6 build does not expose (checked, not
        assumed); a wrapper process to set it would add a process rather than remove one. The
        orphan is bounded by the login session - it is inside the user slice, so logout reaps it
       - and one idle journalctl costs nothing. Revisit if the API appears.
        """
        if self._journal_proc is not None:
            return
        exe = shutil.which("journalctl")
        if not exe:
            self.journalLine.emit("dev: journalctl not on PATH - cannot follow the engine log")
            return
        unit = self._journal_unit()
        proc = QProcess(self)
        # a clean environment: the instrument vars from compose_env() belong to a bench
        # engine, and journalctl must not inherit them
        proc.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._drain_journal)
        proc.finished.connect(self._on_journal_finished)
        self._journal_proc = proc
        # -n 200 bounds the backlog: without it the first read replays the whole boot in
        # one chunk, which is the flood the console's emit cap exists to prevent.
        proc.start(exe, ["--user", "-u", unit, "-f", "-n", "200",
                         "--no-pager", "-o", "short-iso"])
        self.journalLine.emit(f"dev: following {unit}")
        self.journalChanged.emit()

    @Slot()
    def stopJournal(self) -> None:
        """Stop the follower. Null the handle FIRST: the reap can run inside the
        QProcess's own signal dispatch, and dropping the last reference synchronously
        there is a use-after-free (the pattern BenchBridge._retire_proc documents).

        Reaped by HANDLE, never by name - `journalctl` is a shared binary name and a
        name-based reap would kill the user's unrelated terminals.
        """
        proc, self._journal_proc = self._journal_proc, None
        if proc is None:
            return
        try:
            proc.finished.disconnect(self._on_journal_finished)
        except (RuntimeError, TypeError):
            pass
        try:
            proc.terminate()
            if not proc.waitForFinished(1000):
                proc.kill()
                proc.waitForFinished(1000)   # SIGKILL is async - collect it, or the
                                             # handle reports Running over a dead pid
        except RuntimeError:
            pass
        proc.deleteLater()
        self.journalChanged.emit()

    def _drain_journal(self) -> None:
        proc = self._journal_proc
        if proc is None:
            return
        data = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
        lines = [ln for ln in data.splitlines() if ln.strip()]
        dropped = len(lines) - self._JOURNAL_EMIT_MAX
        if dropped > 0:
            self.journalLine.emit(f"dev: journal heavy - showing last "
                                  f"{self._JOURNAL_EMIT_MAX} of {len(lines)} lines this read")
            lines = lines[-self._JOURNAL_EMIT_MAX:]
        for line in lines:
            self.journalLine.emit(line)

    def _on_journal_finished(self, code: int, _status: object) -> None:
        # deleteLater, not a bare drop: this runs inside the QProcess's own signal dispatch,
        # so releasing the last reference synchronously here is a use-after-free (the reason
        # stopJournal nulls the handle first). Without it, every unexpected journalctl exit
        # left one QProcess parented to this bridge for the life of the session.
        proc, self._journal_proc = self._journal_proc, None
        if proc is not None:
            proc.deleteLater()
        self.journalLine.emit(f"dev: journal follower exited ({int(code)})")
        self.journalChanged.emit()


    @Slot(result="QVariantList")
    def runHistory(self) -> list:
        return [{"ts": r["ts"], "code": r["code"]} for r in self._runs]

    @Slot(int, result=str)
    def runTail(self, index: int) -> str:
        if 0 <= index < len(self._runs):
            return self._runs[index]["tail"]
        return ""

    @Slot(str)
    def logVerdict(self, text: str) -> None:
        """Append-only dev verdict log: datetime, target, run conditions, free text."""
        text = str(text or "").strip()
        if not text:
            return
        cond = []
        if self._solo_objs:
            cond.append("solo=" + ",".join(self._solo_objs))
        if self._skip_objs:
            cond.append("skip=" + ",".join(self._skip_objs))
        if self._toggles_off:
            cond.append("off=" + ",".join(sorted(self._toggles_off)))
        line = "%s\t%s\t%s\t%s\n" % (
            _now_full(), self._target or "now-playing", " ".join(cond) or "-", text)
        try:
            fp = paths.state_dir() / "dev-verdicts.log"
            with open(fp, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass

    @Slot(int, result="QVariantList")
    def recentVerdicts(self, n: int) -> list:
        fp = paths.state_dir() / "dev-verdicts.log"
        try:
            lines = fp.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for ln in lines[-int(n):]:
            parts = ln.split("\t")
            if len(parts) >= 4:
                out.append({"time": parts[0], "scene": parts[1], "cond": parts[2], "text": parts[3]})
        out.reverse()
        return out

    def _palette_file(self) -> Path:
        return paths.state_dir() / "dev-palette.json"

    @Slot(result="QVariantMap")
    def paletteState(self) -> dict:
        """Persisted tools-palette geometry / pin / tab. Empty = use defaults."""
        try:
            d = json.loads(self._palette_file().read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    @Slot("QVariantMap")
    def savePaletteState(self, st: dict) -> None:
        """Persist palette geometry/pin/tab so it survives an app restart. This is
        GUI-only Tier B state, so JSON is fine and there is no engine push side effect
        (unlike the schema-bound settings bridge). The A/B side
        loadouts ride along (bridge state, bridge-persisted; Reset is the one-click way
        back to stock)."""
        try:
            paths.ensure_dirs()
            d = dict(st)
            d["abOffA"] = sorted(self._ab_off.get("A", set()))
            d["abOffB"] = sorted(self._ab_off.get("B", set()))
            d["abEnvA"] = self.abEnvText("A")
            d["abEnvB"] = self.abEnvText("B")
            atomic.atomic_write_text(self._palette_file(), json.dumps(d))
        except (OSError, ValueError, TypeError):
            pass

    def _restore_ab_loadouts(self) -> None:
        """Called once at construction: reload the persisted side loadouts."""
        st = self.paletteState()
        valid = {t["key"] for t in OUR_TOGGLES}
        for side, key in (("A", "abOffA"), ("B", "abOffB")):
            vals = st.get(key)
            if isinstance(vals, list):
                self._ab_off[side] = {str(v) for v in vals if str(v) in valid}
        for side, key in (("A", "abEnvA"), ("B", "abEnvB")):
            txt = st.get(key)
            if isinstance(txt, str) and txt:
                self.setABEnvText(side, txt)

    def shutdown(self) -> None:
        """App quit: stop any bench or A/B engine so none outlive the app."""
        self._relaunch_timer.stop()
        self._clear_live_isolation()
        self.stopBench()
        self.stopAB()
        self.stopJournal()


def _is_shell_ident(name: str) -> bool:
    """True iff `name` is a POSIX shell identifier: a leading letter or underscore followed by
    letters, digits, or underscores. Used to validate raw env-editor keys so a
    malformed key never enters the launch environment."""
    if not name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in name) and name.isascii()


def _now_hhmmss() -> str:
    return time.strftime("%H:%M:%S")


def _now_full() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
