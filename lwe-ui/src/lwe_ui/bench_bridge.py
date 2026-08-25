"""BenchBridge - the bench-mode backend for the right slide-over bench panel.

A single QObject exposed to QML as the `bench` context property (app.py registers it, keeping a
Python ref alive). It is the twin of EditorBridge; it additionally owns the test engine QProcess.

NO PENDING STORE. The draft buffer is gone app-wide: an approved item ships with
its conf AS IT SITS at approval, so a pending item's `wp/<id>.conf` is BUILT here at open time
(seed_pending_conf) and tuned in place. Conf existence is not library membership - membership is
models.library_ids(), a directory scan unioned with the good/review tag states - so writing the
conf early cannot put an un-approved item in the grid.

It WRAPS the already-built + tested backend and never reimplements it:
  * storage.wp        - the presence-preserving conf reader/writer
  * bench.build_test_argv - the byte-identical mirror test argv
  * commit.commit / commit.reject - the single commit gate + tombstone
  * bench_courier     - the crash-tolerant release/acquire courier over the daemon socket

Runtime-safety rules (non-negotiable):
  * Test asks the courier to stand the daemon down FIRST and aborts the launch if the
    release is not confirmed (no handshake, no test);
  * the test engine is a NON-detached QProcess child (no setsid) so it dies with the app; it is
    REAPED (terminate->wait->kill) before commit/reject/resume/relaunch;
  * acquire is sent ONLY on stop / close / crash - NEVER on the commit/reject happy path
    (commit.py resumes internally);
  * commit/reject run OFF the GUI thread so the UI never freezes.

Injectable seam for tests: a process factory (default = a plain non-detached QProcess). Tests
sandbox HOME/XDG and stub bench_courier + the process factory, so nothing here ever touches the
real engine.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import (
    Property,
    QObject,
    QProcess,
    QProcessEnvironment,
    QRunnable,
    QThreadPool,
    QTimer,
    QUrl,
    Qt,
    Signal,
    Slot,
)

from . import bench_courier
from . import bench as bench_mod
from . import commit as commit_mod
from . import constants as C
from .engine import daemon_unit
from .discovery import objects as objects_disc
from .discovery import project as project_disc
from .editor import (
    EditorBridge,
    _as_bool,
    _prop_to_str,
    _skip_list,
    _skip_set,
    resolve_render_dir,
)
from .storage import meta, paths, settings, wp

SOURCE_PENDING = bench_mod.SOURCE_PENDING
SOURCE_GOOD = bench_mod.SOURCE_GOOD


def seed_pending_conf(wid: str, workshop_dir: str | Path) -> dict[str, Any]:
    """Build (or return the existing) wp/<id>.conf for a pending item from WP_SCHEMA defaults.

    A separate pending store is rejected: an approved item ships with its conf as it
    sits at approval, so the bench builds a working conf directly and tunes it in place. This
    is the retired buffer's pending-seed logic, retargeted at wp/<id>.conf.

    Auto-derivations: TYPE from <workshop_dir>/<wid>/project.json; BG = the full workshop path
    (str(<workshop_dir>/<wid>)) so a pending test renders from Steam's tree; CC from a
    preset `wec_*` block if project.json carries one (else identity "1 1 1 0").

    STICKY: an existing conf is returned unchanged, so an in-progress tuning session is never
    clobbered by a reopen.
    """
    if paths.wp_file(wid).exists():
        return wp.load(wid)

    wdir = Path(workshop_dir) / wid
    proj = project_disc.read(wdir)

    d: dict[str, Any] = {k: spec["default"] for k, spec in C.WP_SCHEMA.items()}
    d["props"] = {}

    wtype = proj.get("type") or ""
    if wtype in C.WALLPAPER_TYPES:
        d["TYPE"] = wtype
    # else: keep the schema default ("scene"); read() never clamps, so guard the enum here.

    d["BG"] = str(wdir)
    # color-grade keys live NESTED under project.json's `preset` block in real WE wallpapers
    # (verified: 0/53 local presets carry wec_* at top level). Fall back to the raw top level
    # for any odd pack that puts them there.
    raw = proj.get("raw") or {}
    preset = raw.get("preset")
    d["CC"] = project_disc.derive_cc(preset if isinstance(preset, dict) else raw)

    wp.save(wid, d)
    return d


def _default_process_factory() -> QProcess:
    """Build a plain, NON-detached QProcess (no setsid) so the test engine dies with the app.

    Reinforced with PR_SET_PDEATHSIG where the platform + PySide6 support it, so a hard app crash
    still tears the child down (the engine's dead-man reflex then re-acquires the outputs).
    """
    proc = QProcess()
    _install_pdeathsig(proc)
    return proc


def _install_pdeathsig(proc: QProcess) -> None:
    """Best-effort: make the child receive SIGTERM if the parent (this app) dies (Linux only)."""
    setter = getattr(proc, "setChildProcessModifier", None)
    if setter is None:
        return

    def _modifier() -> None:  # runs in the forked child, before exec
        try:
            import ctypes  # noqa: PLC0415 - only needed in the child

            PR_SET_PDEATHSIG = 1
            import signal as _signal

            ctypes.CDLL("libc.so.6").prctl(PR_SET_PDEATHSIG, _signal.SIGTERM)
        except Exception:
            pass

    try:
        setter(_modifier)
    except Exception:
        pass


class _CommitRunnable(QRunnable):
    """Run commit/reject on a worker thread; report back via the bridge's private done-signal.

    The disk copy + atomic publish + tag + index build is slow; keeping it off the GUI thread
    keeps the UI responsive during a commit.
    """

    def __init__(self, bridge: "BenchBridge", action: str, wid: str, source: str, title: str,
                 workshop_dir: str, wallpapers_dir: str) -> None:
        super().__init__()
        self._bridge = bridge
        self._action = action
        self._wid = wid
        self._source = source
        self._title = title
        self._workshop_dir = workshop_dir
        self._wallpapers_dir = wallpapers_dir

    def run(self) -> None:  # worker thread
        try:
            if self._action == "reject":
                report = commit_mod.reject(self._wid, title=self._title)
            else:
                report = commit_mod.commit(
                    self._wid,
                    source=self._source,
                    title=self._title,
                    workshop_dir=self._workshop_dir,
                    wallpapers_dir=self._wallpapers_dir,
                )
        except Exception as exc:  # noqa: BLE001 - never let a worker exception vanish silently
            report = {"ok": False, "reason": f"{self._action} raised: {exc}"}
        # queued back to the GUI thread (the bridge lives there)
        self._bridge._commitDone.emit(self._action, report)


class BenchBridge(QObject):
    """Bench-mode model + test-engine owner. One id loaded at a time; reload via open()."""

    # NOTIFY signals -------------------------------------------------------------------------
    loaded = Signal()                       # all read-only header/param props changed (new open)
    stateChanged = Signal()                 # runtime props (isTesting/benchAvailable/dirty/...)
    committed = Signal(bool, str)           # commit/reject finished: (ok, reason)
    toast = Signal(str)                     # transient user message

    # private worker->GUI marshaling signal (str action, object report)
    _commitDone = Signal(str, object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        process_factory: Callable[[], QProcess] | None = None,
    ) -> None:
        super().__init__(parent)
        self._wid: str = ""
        self._source: str = ""
        self._render_dir: str = ""
        self._proj: dict[str, Any] = {}
        self._draft: dict[str, Any] = {}
        self._meta: dict[str, Any] = {}
        self._objects: list[dict] = []
        self._props: list[dict] = []
        self._title: str = ""
        self._workshop_dir: str = ""
        self._wallpapers_dir: str = ""

        self._bench_available: bool = False
        self._is_testing: bool = False
        self._test_state: str = "idle"
        self._dirty_since_test: bool = False
        self._committing: bool = False
        self._last_error: str = ""

        self._process_factory = process_factory or _default_process_factory
        self._proc: QProcess | None = None
        # keep-alive for a QProcess retired from INSIDE its own finished/errorOccurred slot: the
        # Python ref must outlive the current signal dispatch or PySide destroys the C++ QProcess
        # (and its socket notifier) mid-event -> use-after-free SIGSEGV. Drained next loop turn.
        self._retired: list[QProcess] = []
        self._pool = QThreadPool.globalInstance()

        self._commitDone.connect(self._on_commit_done, Qt.QueuedConnection)

    def set_process_factory(self, factory: Callable[[], QProcess]) -> None:
        self._process_factory = factory

    @Slot(str, str)
    def open(self, wid: str, source: str) -> None:
        """Load `wid` for benching. Seed or read wp/<id>.conf per source, resolve the render
        dir / title / preview / objects / props, probe courier availability once, emit loaded()."""
        wid = str(wid or "")
        source = str(source or "")
        if self._is_testing:
            self._reap_process()
            self._resume_engine()

        self._wid = wid
        self._source = source
        self._dirty_since_test = False
        self._last_error = ""
        self._test_state = "idle"

        if not wid or source not in (SOURCE_PENDING, SOURCE_GOOD):
            self._draft = {}
            self._proj = {}
            self._meta = {}
            self._objects = []
            self._props = []
            self._title = wid
            self._bench_available = False
            self.loaded.emit()
            self.stateChanged.emit()
            return

        st = self._settings()
        # PER-ITEM pending root. st["workshop_dir"] is the Steam tree; a hand-added folder (the
        # Advanced import) lives in LWE's manual root instead. Resolving here, where the wid is
        # known, means the conf seed, the render dir, the test argv and the eventual commit all
        # inherit the right tree from one decision. pending_root_for falls back to the Steam root,
        # so every pre-existing id behaves exactly as before.
        self._workshop_dir = (str(paths.pending_root_for(wid, st["workshop_dir"]))
                              if source == SOURCE_PENDING else st["workshop_dir"])
        self._wallpapers_dir = st["wallpapers_dir"]

        # ---- seed the conf. A pending item has none yet, so build one (L-19); a good item
        #      already has its live conf and is tuned forward from it. Either way the working
        #      copy IS the conf - there is no second buffer to keep in step.
        try:
            if source == SOURCE_PENDING:
                self._draft = seed_pending_conf(wid, self._workshop_dir)
            else:
                self._draft = wp.load(wid)
        except Exception:
            self._draft = {}

        if source == SOURCE_PENDING:
            self._render_dir = str(Path(self._workshop_dir) / wid)
        else:
            self._render_dir = resolve_render_dir(wid, self._wallpapers_dir)

        try:
            self._proj = project_disc.read(self._render_dir)
        except Exception:
            self._proj = {}
        try:
            self._meta = meta.get(wid)
        except Exception:
            self._meta = {}

        try:
            self._objects = objects_disc.extract(self._render_dir)
        except Exception:
            self._objects = []
        try:
            self._props = EditorBridge._normalize_props(self._proj.get("properties"))
        except Exception:
            self._props = []

        # title provenance (9): project.json title, else meta title, else the id - never fatal.
        self._title = self._resolve_title(wid)

        try:
            self._bench_available = bool(bench_courier.available())
        except Exception:
            self._bench_available = False

        self.loaded.emit()
        self.stateChanged.emit()

    def _settings(self) -> dict[str, str]:
        try:
            st = settings.load()
        except Exception:
            st = {}
        return {
            "engine_bin": daemon_unit.resolve_engine_bin(),
            "assets_dir": str(st.get("ASSETS_DIR") or paths.default_assets_dir()),
            "workshop_dir": str(st.get("WORKSHOP_DIR") or paths.detect_workshop_dir()),
            "wallpapers_dir": str(st.get("WALLPAPERS_DIR") or paths.default_wallpapers_dir()),
            "pause_on_fullscreen": bool(st.get("PAUSE_ON_FULLSCREEN", False)),
        }

    def _resolve_title(self, wid: str) -> str:
        t = self._proj.get("title") if isinstance(self._proj, dict) else ""
        if t:
            return str(t)
        mt = self._meta.get("title") if isinstance(self._meta, dict) else ""
        return str(mt) if mt else wid

    def _draft_get(self, key: str) -> Any:
        if key in self._draft:
            return self._draft[key]
        spec = C.WP_SCHEMA.get(key, {})
        return spec.get("default", "")

    def _get_wid(self) -> str:
        return self._wid

    def _get_title(self) -> str:
        return self._title or self._wid

    def _get_type(self) -> str:
        ptype = str(self._proj.get("type") or "") if isinstance(self._proj, dict) else ""
        if ptype:
            return ptype
        return str(self._draft_get("TYPE") or "") or "scene"

    def _get_preview_url(self) -> str:
        prev = self._proj.get("preview") if isinstance(self._proj, dict) else ""
        if prev and os.path.isfile(prev):
            return QUrl.fromLocalFile(prev).toString()
        return ""

    def _get_source(self) -> str:
        return self._source

    def _get_commit_mode(self) -> str:
        # first-commit publishes a pending item; recommit overwrites a good item's conf in place.
        return "first" if self._source == SOURCE_PENDING else "recommit"

    def _get_render_tree(self) -> str:
        return "Steam workshop" if self._source == SOURCE_PENDING else "library"

    def _get_scaling(self) -> str:
        return str(self._draft_get("SCALING") or "default")

    def _get_fps(self) -> str:
        fps = self._draft_get("FPS")
        return "" if fps is None or fps == "" else str(fps)

    def _get_speed(self) -> str:
        return str(self._draft_get("SPEED"))

    def _get_cc(self) -> str:
        return str(self._draft_get("CC") or "1 1 1 0")

    def _get_volume(self) -> int:
        try:
            return int(self._draft_get("VOLUME"))
        except (TypeError, ValueError):
            return 0

    def _get_clamping(self) -> str:
        return str(self._draft_get("CLAMPING") or "")

    def _get_automute(self) -> bool:
        return _as_bool(self._draft_get("AUTOMUTE"), default=True)

    def _get_audio_reactive(self) -> bool:
        return _as_bool(self._draft_get("AUDIO_REACTIVE"), default=False)

    def _get_mouse(self) -> bool:
        return _as_bool(self._draft_get("MOUSE"), default=False)

    def _get_fullscreen_pause(self) -> str:
        val = self._draft_get("FULLSCREEN_PAUSE")
        if val is None or (isinstance(val, str) and val.strip() == ""):
            return ""
        if isinstance(val, bool):
            return "true" if val else "false"
        s = str(val).strip().lower()
        if s in ("true", "1", "yes", "on"):
            return "true"
        if s in ("false", "0", "no", "off"):
            return "false"
        return ""

    def _get_monitors(self) -> str:
        return str(self._draft_get("MONITORS") or "all")

    wallpaperId = Property(str, _get_wid, notify=loaded)
    title = Property(str, _get_title, notify=loaded)
    type = Property(str, _get_type, notify=loaded)
    previewUrl = Property(str, _get_preview_url, notify=loaded)
    source = Property(str, _get_source, notify=loaded)
    commitMode = Property(str, _get_commit_mode, notify=loaded)
    renderTree = Property(str, _get_render_tree, notify=loaded)
    scaling = Property(str, _get_scaling, notify=loaded)
    fps = Property(str, _get_fps, notify=loaded)
    speed = Property(str, _get_speed, notify=loaded)
    cc = Property(str, _get_cc, notify=loaded)
    volume = Property(int, _get_volume, notify=loaded)
    clamping = Property(str, _get_clamping, notify=loaded)
    automute = Property(bool, _get_automute, notify=loaded)
    audioReactive = Property(bool, _get_audio_reactive, notify=loaded)
    mouse = Property(bool, _get_mouse, notify=loaded)
    fullscreenPause = Property(str, _get_fullscreen_pause, notify=loaded)
    monitors = Property(str, _get_monitors, notify=loaded)

    def _get_bench_available(self) -> bool:
        return self._bench_available

    def _get_is_testing(self) -> bool:
        return self._is_testing

    def _get_test_state(self) -> str:
        return self._test_state

    def _get_dirty(self) -> bool:
        return self._dirty_since_test

    def _get_committing(self) -> bool:
        return self._committing

    def _get_last_error(self) -> str:
        return self._last_error


    benchAvailable = Property(bool, _get_bench_available, notify=stateChanged)
    isTesting = Property(bool, _get_is_testing, notify=stateChanged)
    testState = Property(str, _get_test_state, notify=stateChanged)
    dirtySinceTest = Property(bool, _get_dirty, notify=stateChanged)
    committing = Property(bool, _get_committing, notify=stateChanged)
    lastError = Property(str, _get_last_error, notify=stateChanged)

    @Slot(result="QVariantList")
    def objectGroups(self) -> list[dict]:
        skip = _skip_set(self._draft_get("SKIP"))
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
            real = [i for i in ids if i]
            all_skipped = bool(real) and all(i in skip for i in real)
            out.append({"type": t, "count": len(ids), "enabled": not all_skipped})
        return out

    @Slot(result=int)
    def particleCount(self) -> int:
        return sum(1 for o in self._objects if str(o.get("type")) == "particle")

    @Slot(result="QVariantList")
    def sceneProperties(self) -> list[dict]:
        overrides = self._draft.get("props") if isinstance(self._draft, dict) else {}
        if not isinstance(overrides, dict):
            overrides = {}
        out: list[dict] = []
        for entry in self._props:
            name = str(entry.get("name") or "")
            kind = str(entry.get("kind") or "text")
            value: Any = overrides[name] if name in overrides else entry.get("value")
            out.append({
                "name": name,
                "kind": kind,
                "label": entry.get("label") or name,
                "value": value,
                "min": entry.get("min", 0),
                "max": entry.get("max", 100),
                "step": entry.get("step", 1),
                "options": entry.get("options", []),
            })
        return out

    def _persist_draft(self, changes: dict[str, Any] | None = None) -> None:
        """Persist the working copy to wp/<id>.conf.

        `changes` names the keys that actually moved so the write stays presence-preserving
        (an unnamed key is never materialized); omitting it writes the whole dict, which is
        what the props path needs since a removed prop has no key left to name.
        """
        if not self._wid:
            return
        try:
            if changes is None:
                wp.save(self._wid, self._draft)
            else:
                wp.update_set(self._wid, changes)
        except Exception:
            return
        self._dirty_since_test = True
        self.stateChanged.emit()

    def _set_key(self, key: str, value: Any) -> None:
        self._draft[key] = value
        self._persist_draft({key: value})

    @Slot(str)
    def setScaling(self, value: str) -> None:
        self._set_key("SCALING", str(value))

    @Slot(str)
    def setFps(self, value: str) -> None:
        s = str(value).strip()
        if s == "":
            self._draft["FPS"] = ""
        else:
            try:
                self._draft["FPS"] = int(s)
            except (TypeError, ValueError):
                self._draft["FPS"] = ""
        self._persist_draft({"FPS": self._draft["FPS"]})

    @Slot(str)
    def setSpeed(self, value: str) -> None:
        try:
            self._draft["SPEED"] = float(str(value).strip())
        except (TypeError, ValueError):
            self._draft["SPEED"] = C.WP_SCHEMA["SPEED"]["default"]
        self._persist_draft({"SPEED": self._draft["SPEED"]})

    @Slot(str)
    def setCc(self, value: str) -> None:
        self._set_key("CC", str(value))

    @Slot(int)
    def setVolume(self, value: int) -> None:
        try:
            self._draft["VOLUME"] = int(value)
        except (TypeError, ValueError):
            self._draft["VOLUME"] = 0
        self._persist_draft({"VOLUME": self._draft["VOLUME"]})

    @Slot(str)
    def setClamping(self, value: str) -> None:
        v = str(value).strip()
        self._set_key("CLAMPING", v if v in C.CLAMPS else "")

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
    def setFullscreenPause(self, value: str) -> None:
        s = str(value).strip().lower()
        if s == "":
            self._draft["FULLSCREEN_PAUSE"] = ""
        elif s in ("true", "1", "yes", "on"):
            self._draft["FULLSCREEN_PAUSE"] = True
        elif s in ("false", "0", "no", "off"):
            self._draft["FULLSCREEN_PAUSE"] = False
        else:
            self._draft["FULLSCREEN_PAUSE"] = ""
        self._persist_draft({"FULLSCREEN_PAUSE": self._draft["FULLSCREEN_PAUSE"]})

    @Slot(str)
    def setMonitors(self, value: str) -> None:
        s = str(value).strip()
        self._set_key("MONITORS", s or "all")

    @Slot(str, "QVariant")
    def setProp(self, name: str, value: Any) -> None:
        name = str(name or "")
        if not name:
            return
        props = self._draft.get("props")
        if not isinstance(props, dict):
            props = {}
            self._draft["props"] = props
        sval = _prop_to_str(value)
        if sval == "":
            props.pop(name, None)
        else:
            props[name] = sval
        self._persist_draft()

    @Slot(str, bool)
    def setObjectGroupEnabled(self, otype: str, on: bool) -> None:
        otype = str(otype or "")
        type_ids = [str(o.get("objid") or "") for o in self._objects if str(o.get("type")) == otype]
        type_ids = [i for i in type_ids if i]
        if not type_ids:
            return
        skip = _skip_list(self._draft_get("SKIP"))
        if on:
            target = set(type_ids)
            skip = [i for i in skip if i not in target]
        else:
            have = set(skip)
            for i in type_ids:
                if i not in have:
                    skip.append(i)
                    have.add(i)
        self._set_key("SKIP", " ".join(skip))

    @Slot()
    def bulkDisableParticles(self) -> None:
        particle_ids = [str(o.get("objid") or "") for o in self._objects if str(o.get("type")) == "particle"]
        particle_ids = [i for i in particle_ids if i]
        if not particle_ids:
            return
        skip = _skip_list(self._draft_get("SKIP"))
        have = set(skip)
        for i in particle_ids:
            if i not in have:
                skip.append(i)
                have.add(i)
        self._set_key("SKIP", " ".join(skip))

    @Slot()
    def autoFromPreset(self) -> None:
        """Derive CC from the project.json preset `wec_*` block and apply it."""
        raw = self._proj.get("raw") if isinstance(self._proj, dict) else {}
        if not isinstance(raw, dict):
            raw = {}
        preset = raw.get("preset")
        source = preset if isinstance(preset, dict) else raw
        cc = project_disc.derive_cc(source)
        self._draft["CC"] = cc
        self._persist_draft({"CC": cc})
        self.loaded.emit()  # resync the cc field (its Connections{onLoaded})

    @Slot()
    def startTest(self) -> None:
        """Launch (or relaunch) the foreground bench test engine over a daemon standdown."""
        if not self._bench_available:
            self._last_error = "Bench needs the engine socket - Test is disabled."
            self._test_state = "disabled"
            self.stateChanged.emit()
            self.toast.emit(self._last_error)
            return

        # guard the argv builder BEFORE touching the daemon: a seeded non-empty BG + valid source.
        try:
            env, argv = self._build_argv()
        except ValueError as exc:
            self._last_error = f"cannot test: {exc}"
            self._test_state = "test-error"
            self.stateChanged.emit()
            self.toast.emit(self._last_error)
            return

        for peer in getattr(self, "_engine_peers", []):
            try:
                busy = peer.engineBusy()
            except Exception:
                busy = False
            if busy:
                self._last_error = "a Workshop preview is open - close it to test here."
                self._test_state = "pause-denied"
                self.stateChanged.emit()
                self.toast.emit(self._last_error)
                return

        self._reap_process()

        # standdown handshake FIRST - abort the launch if the daemon does not confirm the release.
        if not self._bench_pause():
            self._last_error = "engine did not release the outputs - test aborted."
            self._test_state = "pause-denied"
            self._bench_available = False  # a False here means no safe pause is possible
            self.stateChanged.emit()
            self.toast.emit(self._last_error)
            return

        bench_courier.wait_clear()

        proc = self._process_factory()
        self._proc = proc
        try:
            qenv = QProcessEnvironment.systemEnvironment()
            for k, v in env.items():
                qenv.insert(k, str(v))
            proc.setProcessEnvironment(qenv)
            proc.setProgram(argv[0])
            proc.setArguments([str(a) for a in argv[1:]])
            proc.finished.connect(self._on_proc_finished)
            proc.errorOccurred.connect(self._on_proc_error)
            proc.start()
        except Exception as exc:  # noqa: BLE001 - a launch failure must not desync the pause
            self._proc = None
            self._resume_engine()
            self._last_error = f"engine failed to start: {exc}"
            self._test_state = "test-error"
            self.stateChanged.emit()
            self.toast.emit(self._last_error)
            return

        self._is_testing = True
        self._dirty_since_test = False
        self._test_state = "testing"
        self._last_error = ""
        self.stateChanged.emit()

    def set_engine_peers(self, peers: list) -> None:
        self._engine_peers = [p for p in peers if p is not None]

    @Slot(result=bool)
    def engineBusy(self) -> bool:
        return self._proc is not None

    @Slot()
    def stopTest(self) -> None:
        """Reap the test engine and hand the outputs back (an explicit exit path)."""
        if not self._is_testing and self._proc is None:
            return
        self._reap_process()
        self._resume_engine()
        self._test_state = "idle"
        self.stateChanged.emit()

    @Slot()
    def commit(self) -> None:
        self._run_commit_reject("commit")

    @Slot()
    def reject(self) -> None:
        self._run_commit_reject("reject")

    def _run_commit_reject(self, action: str) -> None:
        if self._committing:
            return
        # reap the test engine BEFORE the backend resumes/rebuilds rotation (5.4). Do NOT resume
        # here - commit.py/reject() call bench_resume internally (5.5, no double-resume).
        self._reap_process()
        self._committing = True
        self._test_state = "committing" if action == "commit" else "rejecting"
        self.stateChanged.emit()
        runnable = _CommitRunnable(
            self, action, self._wid, self._source, self._title,
            self._workshop_dir, self._wallpapers_dir,
        )
        self._pool.start(runnable)

    @Slot(str, object)
    def _on_commit_done(self, action: str, report: object) -> None:  # GUI thread (queued)
        rep = report if isinstance(report, dict) else {}
        ok = bool(rep.get("ok"))
        reason = str(rep.get("reason") or "")
        self._committing = False
        self._is_testing = False
        if ok:
            self._test_state = "committed" if action == "commit" else "rejected"
        else:
            self._test_state = "commit-failed"
            self._last_error = reason or f"{action} failed"
            # the backend resumes ONLY on ok:True (commit.py/reject() bail before their internal
            # bench_resume on every failure path). The engine was already reaped in
            # _run_commit_reject, so nobody is holding a live test surface - resume the orphaned
            # release now - it is state and holds forever otherwise. No double-resume
            # risk precisely because the failed backend never resumed.
            self._resume_engine()
        self.stateChanged.emit()
        self.committed.emit(ok, reason)

    @Slot()
    def close(self) -> None:
        """Panel closed by the user: re-acquire the outputs if we were mid-test (crash-safe exit)."""
        was_testing = self._is_testing
        self._reap_process()
        if was_testing:
            self._resume_engine()
        self._test_state = "idle"
        self.stateChanged.emit()

    @Slot()
    def onAboutToQuit(self) -> None:
        """QApplication.aboutToQuit / window closeEvent hook - never leave rotation paused."""
        if self._is_testing or self._proc is not None:
            self._reap_process()
            self._resume_engine()

    def _retire_proc(self, proc: QProcess) -> None:
        """Safely dispose a QProcess that fired its own finished/errorOccurred slot.

        CRITICAL (crash-safety): these handlers run INSIDE the QProcess's own signal dispatch (a
        QSocketNotifier::event on the stack). `self._proc` is the sole Python ref, so a synchronous
        `self._proc = None` drops the refcount to zero and PySide destroys the QProcess + its
        notifier mid-event -> SIGSEGV that takes down the whole app. Since the REAL engine
        segfaults-on-exit, a CrashExit fires on essentially every normal test-exit, so
        this path is hot, not rare. Instead: disconnect the signals (so no further slot fires),
        stash the ref in a keep-alive list that outlives this dispatch, request deferred C++
        deletion, and clear the keep-alive on the next event-loop turn.
        """
        for sig in ("finished", "errorOccurred"):
            try:
                getattr(proc, sig).disconnect()
            except Exception:
                pass
        self._retired.append(proc)
        try:
            proc.deleteLater()
        except Exception:
            pass
        QTimer.singleShot(0, self._drain_retired)

    def _drain_retired(self) -> None:
        self._retired.clear()

    def _on_proc_finished(self, exit_code: int, exit_status: object = None) -> None:
        """The test engine exited on its own (not via our reap). Re-acquire the outputs.

        The engine segfaults-on-exit, so a CrashExit is informational, not necessarily a render
        failure; either way we hand the outputs back since we are no longer holding a test surface.
        """
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        self._retire_proc(proc)  # defer destruction - never drop the ref inside this own slot
        self._is_testing = False
        self._resume_engine()
        try:
            crashed = int(exit_status) == int(QProcess.ExitStatus.CrashExit)
        except Exception:
            crashed = False
        self._test_state = "test-error" if crashed else "idle"
        self.stateChanged.emit()

    def _on_proc_error(self, error: object = None) -> None:
        """QProcess FailedToStart / early crash. Reap, resume, surface the error."""
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        self._retire_proc(proc)  # defer destruction - never drop the ref inside this own slot
        self._is_testing = False
        self._resume_engine()
        self._test_state = "test-error"
        self._last_error = "the test engine failed to start or crashed early."
        self.stateChanged.emit()
        self.toast.emit(self._last_error)

    def _build_argv(self) -> tuple[dict[str, str], list[str]]:
        st = self._settings()
        outputs = self._resolve_outputs()
        if not outputs:
            # No compositor output means the mirror argv carries no --screen-root, and the engine
            # would come up windowed over the whole session (the daemon refuses in exactly this
            # case). For an interactive Test we refuse rather than spray a floating window over the
            # desktop; the caller's try/except turns this into a toast.
            raise ValueError("no display output detected (is the compositor reachable?)")
        return bench_mod.build_test_argv(
            st["engine_bin"],
            st["assets_dir"],
            outputs,
            self._draft,
            source=self._source,
            workshop_dir=st["workshop_dir"],
            wallpapers_dir=st["wallpapers_dir"],
            pause_on_fullscreen=st["pause_on_fullscreen"],
        )

    def _resolve_outputs(self) -> list[str]:
        """Best-effort compositor output names for the mirror --screen-root list.

        Empty is safe (build_test_argv only raises on empty BG / bad source); the live rendering
        path is exercised post-go-live, so a missing enumeration degrades to no --screen-root
        rather than raising.
        """
        try:
            import json
            import subprocess

            out = subprocess.run(
                ["hyprctl", "-j", "monitors"],
                capture_output=True, text=True, timeout=2, check=False,
            )
            if out.returncode == 0 and out.stdout.strip():
                data = json.loads(out.stdout)
                return [str(m["name"]) for m in data if isinstance(m, dict) and m.get("name")]
        except Exception:
            pass
        return []

    def _reap_process(self) -> None:
        """terminate -> wait -> kill the panel's own NON-detached test engine. Never re-acquires."""
        proc = self._proc
        self._proc = None
        self._is_testing = False
        if proc is None:
            return
        # disconnect first so _on_proc_finished/_on_proc_error don't double-fire a resume.
        for sig in ("finished", "errorOccurred"):
            try:
                getattr(proc, sig).disconnect()
            except Exception:
                pass
        try:
            st = proc.state()
            st_int = int(st.value) if hasattr(st, "value") else int(st)
            if st_int != 0:  # 0 == QProcess.ProcessState.NotRunning
                proc.terminate()
                if not proc.waitForFinished(2000):
                    proc.kill()
                    proc.waitForFinished(1000)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _bench_pause(self) -> bool:
        """Free the display for the test engine. True when the outputs are ours."""
        try:
            return bool(bench_courier.standdown())
        except Exception:
            return False

    def _resume_engine(self) -> bool:
        """Hand the display back to the desktop engine."""
        try:
            return bool(bench_courier.resume())
        except Exception:
            return False
