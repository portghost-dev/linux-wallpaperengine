"""LWE Control Panel - PySide6 entry point, two processes through one script.

`lwe-ui --tray` dispatches to `tray.main` BEFORE any heavy import: the resident
tray process must never pay for the QML stack, the models, or the bridges (in
Python the import IS the cost). Autostart writes `--tray`, so a login launch is
the tray alone.

`lwe-ui` (no flag) is the WINDOW process - the full panel. It exits on close,
returning all of its memory to the OS; the tray (if the user keeps one) watches
that exit and applies the CLOSE_TO_TRAY rule. `main()` for the window:
  1. ensure config/state dirs exist + settings.conf is present,
  2. construct a QApplication, then take the single-instance guard - a second
     launch defers to the running panel (asks it to present itself) and exits 0,
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

from . import constants as C
from .proctitle import set_process_name
from .storage import paths, settings, theme_cfg, themes

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


def spawn_tray_if_needed() -> bool:
    """Called as the WINDOW exits: hand off to a resident tray when the user expects one.

    CLOSE_TO_TRAY promises "closing the window minimizes to the tray", but the rule is
    enforced by the tray watching the window - with no tray alive (the user exited the
    icon, then opened the window directly) the setting was silently inert and a close
    ended everything. So the closing window respawns a detached tray. Exit on the icon
    still means exit; the tray only returns once the user opens and closes the panel.

    True = a tray was spawned (probe said none was alive)."""
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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    # the tray process: dispatch before the heavy imports below ever run
    if "--tray" in argv:
        paths.ensure_dirs()
        settings.ensure_exists()
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
    settings.ensure_exists()

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

    guard = single_instance.acquire(_present)
    if guard is None:
        return 0
    app.aboutToQuit.connect(guard.close)

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
    # the wizard bench joins the one-engine-at-a-time conflict gate: its 4K bench must not
    # launch alongside a dev-bench / A-B / preview engine (two-engine GPU-crash risk).
    dev.set_engine_peers([workshop, bench, wizard_bridge])
    bench.set_engine_peers([workshop, dev, wizard_bridge])
    wizard_bridge.set_engine_peers([workshop, dev, bench])

    main_qml = _QML_DIR / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(main_qml)))
    if not engine.rootObjects():
        return 1
    _win_holder.append(engine.rootObjects()[0])

    from .engine import daemon_unit
    try:
        if daemon_unit.reconcile_env():
            QTimer.singleShot(0, lambda: backend.notice.emit(
                "New engine options will take effect at the next engine restart"))
    except (ValueError, RuntimeError) as exc:
        _msg = f"Engine service config not updated: {exc}"
        QTimer.singleShot(0, lambda: backend.notice.emit(_msg))

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
    # exits on close (default quit-on-last-window), and the tray applies the repurposed
    # CLOSE_TO_TRAY rule to that exit. If no tray is alive at close time, the exit
    # handler spawns one, so CLOSE_TO_TRAY holds no matter how the window was launched.
    # Fullscreen and app-condition policy live in the engine; the panel only pushes it.
    app.aboutToQuit.connect(spawn_tray_if_needed)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
