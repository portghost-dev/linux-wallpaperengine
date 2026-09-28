"""LWE Control Panel - PySide6 entry point, two processes through one script.

`lwe-ui --tray` dispatches to `tray.main` BEFORE any heavy import: the resident
tray process must never pay for the QML stack, the models, or the bridges (in
Python the import IS the cost). Autostart writes `--tray`, so a login launch is
the tray alone.

`lwe-ui` (no flag) is the WINDOW process - the full panel. It exits on close,
returning all of its memory to the OS; with CLOSE_TO_TRAY on a tray is up for
the whole time the app runs (the window spawns one at startup when none is
alive) and outlives the window; its Exit ends the window too. `main()` for the
window:
  1. ensure config/state dirs exist + settings.conf is present,
  2. construct a QApplication, then take the single-instance guard - a second
     launch defers to the running panel (asks it to present itself) and exits 0,
     and the tray's Exit reaches the window over the same socket,
  3. build a QQmlApplicationEngine, add the bundled `qml/` dir as an import path,
  4. resolve the theme tokens and register the ThemeTokens singleton,
  5. expose the bridges as root-context properties,
  6. load `qml/Main.qml` and run the event loop.

Returns a process exit code (consumed by __main__.py).
"""
from __future__ import annotations

import ctypes
import math
import os
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QMetaObject, QObject, Q_ARG

from . import constants as C
from .proctitle import set_process_name
from .storage import foreign, paths, settings, theme_cfg, themes

_QML_DIR = Path(__file__).resolve().parent / "qml"
# URI the Python-side ThemeTokens singleton is registered under; Theme.qml imports this.
_TOKENS_URI = "LweUi.Theme"
_TOKENS_NAME = "ThemeTokens"


def _resolve_theme_tokens() -> dict[str, str]:
    """The color tokens for the Theme singleton; never raises (falls back to the default
    factory palette). v1.4: six stored roles per theme resolve to the full token set."""
    try:
        return themes.resolve_active()
    except Exception:
        try:
            return themes.resolve(themes.base_roles(themes.DEFAULT_ACTIVE))
        except Exception:
            return dict(C.THEME_PRESETS[C.DEFAULT_THEME_PRESET])


# set when the tray's Exit reached this window: the exit that follows must not put a
# fresh tray up behind it
_exit_all_requested = False


class _SearchFocusFilter(QObject):
    """A press anywhere in the window takes the cursor out of the header's search field.

    Done at the application level: a press a Flickable or a MouseArea accepts never reaches a
    passive handler below it, so a QML-side listener misses the library grid's gaps and every
    MouseArea surface. The header decides what to do with the point (nothing, inside its own
    field)."""

    def __init__(self, window: QObject, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._window = window

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt override)
        if event.type() == QEvent.Type.MouseButtonPress and obj is self._window:
            header = self._window.findChild(QObject, "headerBar")
            if header is not None:
                pos = event.scenePosition()
                # a QML function takes its arguments as variants
                QMetaObject.invokeMethod(header, "pressAt", Q_ARG("QVariant", float(pos.x())), Q_ARG("QVariant", float(pos.y())))
        return False


def install_search_focus_filter(app: QObject, window: QObject) -> QObject:
    """Attach the press listener to the app for one window; the filter is returned so the caller
    keeps it alive."""
    flt = _SearchFocusFilter(window, app)
    app.installEventFilter(flt)
    return flt


def spawn_tray_if_needed() -> bool:
    """Called as the WINDOW starts and again as it exits: make sure a resident tray is
    up when the user expects one.

    CLOSE_TO_TRAY on means the tray icon is there whenever the app runs and outlives
    the window; the rule is enforced by the tray, so a window launched directly (from
    the menu, with no tray alive) spawns one detached. Off means no tray at all. An
    exit the tray itself asked for spawns nothing: that Exit ends both.

    True = a tray was spawned (probe said none was alive)."""
    if _exit_all_requested:
        return False
    try:
        close_to_tray = bool(settings.load().get("CLOSE_TO_TRAY"))
    except Exception:
        close_to_tray = True  # unreadable settings: same default the tray applies
    if not close_to_tray:
        return False
    from . import single_instance
    if single_instance.notify_running(single_instance.tray_socket_path()):
        return False  # a live tray already watches this window
    from PySide6.QtCore import QProcess
    from .tray import _window_command
    # _window_command is "how to relaunch this entry"; --tray flips it to the tray role
    # (the interpreter fallback reads sys.argv, so the appended flag reaches it too)
    command = _window_command()
    QProcess.startDetached(command[0], command[1:] + ["--tray"])
    return True


def _settle_state_tree(process: str) -> None:
    """Move the panel's state files into the tree once, then open the panel log and record
    what moved and which dead files remain for the user to delete."""
    from . import logbook
    try:
        report = paths.migrate_state_tree()
    except Exception:
        report = {"moved": [], "skipped": [], "dead": []}
    log = logbook.install(process)
    if report["moved"]:
        log.info("state tree: moved %s", ", ".join(report["moved"]))
    if report.get("skipped"):
        log.warning("state tree: left in place, a newer copy already sits in the tree: %s",
                    ", ".join(report["skipped"]))
    if report["dead"]:
        log.info("state tree: %d dead file(s) with no writer, safe to delete: %s",
                 len(report["dead"]), ", ".join(report["dead"]))
    if process == "window":
        _sparsify_overrides_once(log)
        try:
            foreign.promote(log)
        except Exception:
            log.exception("foreign: promotion failed")


def _sparsify_overrides_once(log) -> None:
    """Strip materialised defaults from every override once, after a snapshot of the
    config, so a default an importer wrote stops reading as a pin (R125)."""
    import datetime
    from .storage import backup, wp
    marker = paths.panel_state_dir() / "overrides-sparse"
    if marker.exists():
        return
    try:
        snaps = paths.state_dir() / "backups"
        snaps.mkdir(parents=True, exist_ok=True)
        snap = snaps / f"pre-sparsify-{datetime.datetime.now():%Y%m%d-%H%M%S}{backup.EXTENSION}"
        backup.export_to(snap)
        report = wp.sparsify_overrides()
    except Exception as exc:
        log.warning("override clean-up skipped: %s", exc)
        return
    removed = sum(len(v) for v in report.values())
    log.info("overrides: %d materialised default key(s) removed from %d file(s); snapshot %s",
             removed, len(report), snap.name)
    for wid, keys in report.items():
        log.info("overrides: %s dropped %s", wid, ", ".join(keys))
    try:
        marker.write_text("1\n", encoding="utf-8")
    except OSError:
        pass


def interface_scale_factor(store: dict, env: dict) -> str | None:
    """Qt's startup scale factor for the stored Interface scale percent, or None when the
    store says 100 or the environment already carries QT_SCALE_FACTOR (a hand-set factor
    wins over the setting: it is the same knob)."""
    if str(env.get("QT_SCALE_FACTOR", "")).strip():
        return None
    spec = C.SETTINGS_SCHEMA["INTERFACE_SCALE"]
    try:
        pct = int(store.get("INTERFACE_SCALE", spec["default"]))
    except (TypeError, ValueError):
        return None
    pct = max(int(spec["min"]), min(int(spec["max"]), pct))
    if pct == 100:
        return None
    return f"{pct / 100:g}"


def apply_interface_scale(env: dict) -> None:
    """Set QT_SCALE_FACTOR from the store before the QApplication exists; Qt reads it once."""
    try:
        factor = interface_scale_factor(settings.load(), env)
    except Exception:
        return
    if factor is not None:
        env["QT_SCALE_FACTOR"] = factor


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    # the tray process: dispatch before the heavy imports below ever run
    if "--tray" in argv:
        paths.ensure_dirs()
        try:
            settings.ensure_exists()
        except OSError as exc:
            from . import logbook
            logbook.install("tray").warning("settings.conf could not be created: %s", exc)
        _settle_state_tree("tray")
        from .tray import main as tray_main
        return tray_main([a for a in argv if a != "--tray"])

    # both panel processes are the interpreter; name them so a process monitor can
    # tell them apart (and from every other python3 on the box)
    set_process_name("lwe-ui")

    from PySide6.QtCore import QCoreApplication, QTimer, QUrl
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication
    from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance

    from .bench_bridge import BenchBridge
    from .deck_popup import DeckPopupBridge
    from .dev import DevBridge
    from .editor import EditorBridge
    from .models import Backend, ImportBridge, ThemeBridge, ThemeTokens
    from .settings_bridge import SettingsBridge

    # few arenas + fixed mmap threshold, mirroring the engine (440998b8): freed
    # preview/model memory must return to the OS instead of stranding across
    # per-thread arenas. Before the QApplication so Qt's threads see the caps.
    libc = None
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.mallopt(-8, 2)           # M_ARENA_MAX
        libc.mallopt(-3, 128 * 1024)  # M_MMAP_THRESHOLD
    except (OSError, AttributeError):
        libc = None

    paths.ensure_dirs()
    try:
        settings.ensure_exists()
    except OSError as exc:
        from . import logbook
        logbook.install("window").warning("settings.conf could not be created: %s", exc)
    _settle_state_tree("window")
    apply_interface_scale(os.environ)

    QCoreApplication.setApplicationName("LWE Control Panel")
    QCoreApplication.setOrganizationName("lwe")
    # default surface format needs an alpha channel or a Window with color: "transparent"
    # renders as an opaque gray sheet (the A/B gesture overlays were visibly covering the
    # exhibits until dragged aside). Must be set BEFORE the QGuiApplication exists.
    fmt = QSurfaceFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication(argv)

    # one panel per user: a second launch asks the running one to present itself and
    # exits. The window holder is filled after load; a ping during startup is dropped.
    from . import single_instance
    _win_holder: list = []

    def _present() -> None:
        if not _win_holder:
            return
        window = _win_holder[0]
        window.show()
        try:
            window.raise_()
            window.requestActivate()
        except Exception:
            pass

    def _quit_all() -> None:
        global _exit_all_requested
        _exit_all_requested = True
        QTimer.singleShot(0, app.quit)

    guard = single_instance.acquire(_present, _quit_all)
    if guard is None:
        return 0
    app.aboutToQuit.connect(guard.close)
    # the tray is up for as long as the app runs: put one up now if none is alive
    spawn_tray_if_needed()

    # sourceSize does NOT scale with DPR (measured: a 320 cap decoded 320px at scale 2),
    # so the cap multiplies by the densest screen's DPR itself; max over screens keeps
    # the single shared value sharp when the window moves to the densest monitor
    _max_dpr = max((s.devicePixelRatio() for s in app.screens()), default=1.0)
    tokens = ThemeTokens(_resolve_theme_tokens(),
                         preview_cap=math.ceil(360 * _max_dpr))
    qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(_QML_DIR))

    backend = Backend()
    engine.rootContext().setContextProperty("backend", backend)
    # theme changes from Settings re-resolve the tokens; Theme.qml repaints on the signal
    backend.themeRefreshRequested.connect(lambda: tokens.set_tokens(_resolve_theme_tokens()))

    theme_bridge = ThemeBridge(tokens)
    engine.rootContext().setContextProperty("themeBridge", theme_bridge)

    import_bridge = ImportBridge(backend)
    engine.rootContext().setContextProperty("importBridge", import_bridge)

    settings_bridge = SettingsBridge(backend, import_bridge)
    engine.rootContext().setContextProperty("settingsBridge", settings_bridge)

    editor = EditorBridge(backend)
    engine.rootContext().setContextProperty("editor", editor)

    bench = BenchBridge()
    engine.rootContext().setContextProperty("bench", bench)
    app.aboutToQuit.connect(bench.onAboutToQuit)
    app.aboutToQuit.connect(backend.restoreSessionOverrides)
    bench.committed.connect(backend.onItemCommitted)
    deck_popup = DeckPopupBridge(backend)
    engine.rootContext().setContextProperty("deckPopup", deck_popup)

    dev = DevBridge()
    engine.rootContext().setContextProperty("dev", dev)
    app.aboutToQuit.connect(dev.shutdown)

    from .workshop import WorkshopBridge
    workshop = WorkshopBridge(backend, dev)
    engine.rootContext().setContextProperty("workshop", workshop)
    # symmetrical engine-conflict gate (one engine owner at a time; review F1). The Workshop
    # bridge no longer spawns an engine (the wizard owns the bench), so it is only a QUERIED
    # peer here - the others check its engineBusy(); it tracks no peers of its own.
    dev.set_engine_peers([workshop, bench])
    bench.set_engine_peers([workshop, dev])

    from .wizard_bridge import WizardBridge
    wizard_bridge = WizardBridge(backend, workshop)
    engine.rootContext().setContextProperty("wizardBridge", wizard_bridge)
    app.aboutToQuit.connect(wizard_bridge.close)
    # peers answer engineBusy() for the Editor bench's refusal, and tell the wizard which
    # Developer cell is free so its bench window does not land on an exhibit
    dev.set_engine_peers([workshop, bench, wizard_bridge])
    bench.set_engine_peers([workshop, dev, wizard_bridge])
    wizard_bridge.set_engine_peers([workshop, dev, bench])

    main_qml = _QML_DIR / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(main_qml)))
    if not engine.rootObjects():
        return 1
    _win_holder.append(engine.rootObjects()[0])
    _search_focus = install_search_focus_filter(app, _win_holder[0])  # noqa: F841 (kept alive)

    from .engine import daemon_unit
    try:
        if daemon_unit.reconcile_env():
            QTimer.singleShot(0, lambda: backend.notice.emit(
                "New engine options will take effect at the next engine restart"))
    except (ValueError, RuntimeError) as exc:
        _msg = f"Engine service config not updated: {exc}"
        QTimer.singleShot(0, lambda: backend.notice.emit(_msg))
    except OSError as exc:
        from . import logbook
        logbook.install("window").warning("engine env file not updated: %s", exc)

    backend.reconcileAutostart()

    # release freed heap pages after hide/view-switch (the 53->224 MB glibc ratchet
    # never returns them on its own); the delay lets the outgoing view tear down first
    if libc is not None:
        _root = engine.rootObjects()[0]
        trim_timer = QTimer(app)
        trim_timer.setSingleShot(True)
        trim_timer.setInterval(500)
        trim_timer.timeout.connect(lambda: libc.malloc_trim(0))
        # arg-swallowing lambdas: visibleChanged carries a bool, and a bare
        # trim_timer.start would resolve to start(int) and clobber the interval
        _root.visibleChanged.connect(lambda *_: trim_timer.start())
        _root.currentViewChanged.connect(lambda *_: trim_timer.start())

    # No tray in THIS process: the tray is its own process (`lwe-ui --tray`), the window
    # exits on close (default quit-on-last-window), and the tray applies the CLOSE_TO_TRAY
    # rule to that exit. The exit handler puts a tray up if the setting was switched on
    # during this run and none is alive, and stays quiet when the tray asked for the exit.
    # Fullscreen and app-condition policy live in the engine; the panel only pushes it.
    app.aboutToQuit.connect(spawn_tray_if_needed)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
