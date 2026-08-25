"""The resident tray process (`lwe-ui --tray`).

One codebase, two entry paths: this process owns the tray icon and NOTHING else -
no QML, no models, no detection. It drives the engine over `engine.sock` with the
same stateless one-shot verbs every client uses, and it launches the full panel as
a SEPARATE process, so closing the window returns all of its memory to the OS
(in-process teardown could never go below the ~380 MB Python+Qt floor).

QT_QPA_PLATFORMTHEME is stripped before the QApplication: the platform theme
plugin drags the whole Quick/QML stack into an otherwise widgets-only process
(measured: 83 MB themed vs 54 MB stripped), and under StatusNotifierItem the HOST
shell renders the menu anyway, so the theme buys nothing here. Spawned windows get
the original environment back.

CLOSE_TO_TRAY (the "minimize to tray when closed" setting, repurposed): ON means
a window close ends only the window process and this tray stays; OFF means a
window close ends EVERYTHING - when a spawned window exits and the setting is
off, this process follows it down. Autostart launches the tray only; the two
settings are deliberately orthogonal.
"""
from __future__ import annotations

import os
import shutil
import sys
from typing import Any

from PySide6.QtCore import QEvent, QObject, QProcess, QProcessEnvironment, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import api_client
from .proctitle import set_process_name
from .storage import settings


def _fallback_icon() -> QIcon:
    """A drawn icon, so the tray never depends on a themed name being installed.

    A QSystemTrayIcon with a null icon is either invisible or a placeholder depending on the
    host shell, and an invisible tray icon with no window is an app the user cannot reach.
    """
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#7F77DD"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(6, 6, 52, 52, 14, 14)
    painter.setBrush(QColor("#0D0D12"))
    painter.drawEllipse(22, 22, 20, 20)
    painter.end()
    return QIcon(pix)


def icon() -> QIcon:
    themed = QIcon.fromTheme("preferences-desktop-wallpaper")
    return themed if not themed.isNull() else _fallback_icon()


def _window_command() -> list[str]:
    """How to launch the window process: the same entry the user launched, minus --tray.

    argv[0] is the lwe-ui console script when installed; a bare module run falls back to
    the interpreter. Either way the WINDOW decides single-instance itself over ui.sock -
    a second launch asks the live owner to present and exits, so spawning while a window
    is already open degrades to a raise.
    """
    exe = sys.argv[0]
    # isfile matters: argv[0] can be a BARE name (a launcher using `exec -a`), and
    # os.access(X_OK) says yes to a directory - a same-named dir in the cwd would
    # otherwise be handed to QProcess as the program
    if (exe and os.path.basename(exe).startswith("lwe-ui")
            and os.path.isfile(exe) and os.access(exe, os.X_OK)):
        return [exe]
    found = shutil.which("lwe-ui")
    if found:
        return [found]
    return [sys.executable, "-c", "from lwe_ui.app import main; raise SystemExit(main())"]


class TrayProcess(QObject):
    """Owns the tray icon, the quick-action menu, and the window process lifecycle."""

    def __init__(self, app: QApplication, original_env: dict[str, str],
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self._original_env = original_env
        self._window: QProcess | None = None
        self._exiting = False

        self._tray = QSystemTrayIcon(icon(), self)
        self._tray.setToolTip("LWE Control Panel")
        self._tray.activated.connect(self._on_activated)

        menu = QMenu()
        self._pause_action = QAction("Pause wallpapers", menu)
        self._pause_action.triggered.connect(self._toggle_pause)
        self._next_action = QAction("Next wallpaper", menu)
        self._next_action.triggered.connect(lambda: api_client.next_wallpaper())
        self._stop_action = QAction("Stop wallpapers", menu)
        self._stop_action.triggered.connect(self._toggle_outputs)
        menu.addAction(self._pause_action)
        menu.addAction(self._next_action)
        menu.addAction(self._stop_action)
        menu.addSeparator()
        open_action = QAction("Open LWE-UI", menu)
        open_action.triggered.connect(self.open_window)
        menu.addAction(open_action)
        menu.addSeparator()
        quit_action = QAction("Exit", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)
        # labels reflect live engine state, fetched at open (one bounded socket call);
        # a dead socket disables the engine rows rather than presenting dead verbs
        menu.aboutToShow.connect(self._refresh_menu)
        self._menu = menu
        self._tray.setContextMenu(menu)
        self._tray.show()

    # ---- engine quick actions -------------------------------------------------------

    def _status(self) -> dict[str, Any] | None:
        try:
            got = api_client.status()
            return got if isinstance(got, dict) else None
        except Exception:
            return None

    def _refresh_menu(self) -> None:
        st = self._status()
        alive = st is not None
        for action in (self._pause_action, self._next_action, self._stop_action):
            action.setEnabled(alive)
        if not alive:
            return
        paused = float(st.get("speed", 1.0) or 0.0) == 0.0
        self._pause_action.setText("Resume wallpapers" if paused else "Pause wallpapers")
        released = (st.get("outputs") or {}).get("state") == "released"
        self._stop_action.setText("Restore wallpapers" if released else "Stop wallpapers")

    def _toggle_pause(self) -> None:
        # the master-pause fact (timescale 0) - the same single fact the panel drives
        st = self._status()
        if st is None:
            return
        if float(st.get("speed", 1.0) or 0.0) == 0.0:
            try:
                speed = float(settings.load().get("ENGINE_TIMESCALE") or 1.0)
            except Exception:
                speed = 1.0
            api_client.set_speed(speed)
        else:
            api_client.set_speed(0.0)

    def _toggle_outputs(self) -> None:
        st = self._status()
        if st is None:
            return
        released = (st.get("outputs") or {}).get("state") == "released"
        api_client.request("acquire-outputs" if released else "release-outputs")

    # ---- window process lifecycle ---------------------------------------------------

    def _on_activated(self, reason: Any) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_window()

    def open_window(self) -> None:
        if self._window is not None and self._window.state() != QProcess.ProcessState.NotRunning:
            # a live child: the fresh launch below would just ping ui.sock and exit,
            # which IS the raise - but skip the process spawn and ping directly
            from . import single_instance
            if single_instance.notify_running():
                return
        command = _window_command()
        proc = QProcess(self)
        env = QProcessEnvironment()
        for key, value in self._original_env.items():
            env.insert(key, value)
        proc.setProcessEnvironment(env)
        proc.finished.connect(self._on_window_exited)
        proc.setProgram(command[0])
        proc.setArguments(command[1:])
        proc.start()
        self._window = proc

    def _on_window_exited(self, *_args: Any) -> None:
        if self._exiting:
            return
        try:
            close_to_tray = bool(settings.load().get("CLOSE_TO_TRAY"))
        except Exception:
            close_to_tray = True
        if not close_to_tray:
            # the repurposed setting, OFF: closing the window exits EVERYTHING
            self.quit()

    def quit(self) -> None:
        self._exiting = True
        if self._window is not None and self._window.state() != QProcess.ProcessState.NotRunning:
            self._window.terminate()
            self._window.waitForFinished(3000)
        tray = getattr(self, "_tray", None)
        if tray is not None:
            tray.hide()
        self._app.quit()


def main(argv: list[str] | None = None) -> int:
    """`lwe-ui --tray` lands here from app.main - the tray never imports the QML stack."""
    set_process_name("lwe-ui-tray")
    original_env = dict(os.environ)
    # strip the platform theme BEFORE the QApplication: the theme plugin loads the whole
    # Quick/QML stack into this widgets-only process (83 -> 54 MB measured without it),
    # and the SNI host renders the menu regardless
    os.environ.pop("QT_QPA_PLATFORMTHEME", None)

    app = QApplication(list(argv if argv is not None else sys.argv))
    app.setQuitOnLastWindowClosed(False)

    from . import single_instance
    if single_instance.notify_running(single_instance.tray_socket_path()):
        # a live tray already owns the icon; a second is two icons and two writers
        return 0
    guard = single_instance.InstanceGuard(lambda: None)
    guard.listen(single_instance.tray_socket_path())

    tray = TrayProcess(app, original_env)
    app.aboutToQuit.connect(guard.close)
    _holder = (tray, guard)
    return app.exec()
