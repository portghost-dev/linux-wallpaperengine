"""Single-instance guard for the panel.

One live panel per user: a QLocalServer on $XDG_RUNTIME_DIR/lwe/ui.sock. A
second launch connects, asks the owner to present its window, and exits instead
of starting a duplicate (a duplicate means two tray icons and two writers over
settings and state). Two verbs ride the socket: "show" (present the window; the
default, and what an empty or unknown payload means) and "quit" (the tray's Exit
reaching the window, so one Exit ends both processes). A dead socket left by a
crash fails the connect probe, gets removed, and ownership is taken over.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_CONNECT_TIMEOUT_MS = 500


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip() or f"/run/user/{os.getuid()}"
    return Path(runtime) / "lwe" / "ui.sock"


def tray_socket_path() -> Path:
    """The TRAY process's own guard; a second tray is two icons and two writers."""
    return socket_path().parent / "tray.sock"


def _send(path: Path | None, verb: bytes) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(str(path if path is not None else socket_path()))
    if not sock.waitForConnected(_CONNECT_TIMEOUT_MS):
        return False
    sock.write(verb + b"\n")
    sock.waitForBytesWritten(_CONNECT_TIMEOUT_MS)
    sock.disconnectFromServer()
    return True


def notify_running(path: Path | None = None) -> bool:
    """True when a live owner accepted the present request."""
    return _send(path, b"show")


def request_quit(path: Path | None = None) -> bool:
    """Ask a live owner to exit; True when one accepted the request."""
    return _send(path, b"quit")


class InstanceGuard(QObject):
    def __init__(self, on_present: Callable[[], None], parent: QObject | None = None,
                 on_quit: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self._on_present = on_present
        self._on_quit = on_quit
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._accept)

    def listen(self, path: Path | None = None) -> bool:
        path = path if path is not None else socket_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # only reached when no live owner answered the probe: the file is a crash leftover
        QLocalServer.removeServer(str(path))
        return self._server.listen(str(path))

    def close(self) -> None:
        self._server.close()

    def _accept(self) -> None:
        while (conn := self._server.nextPendingConnection()) is not None:
            conn.disconnected.connect(conn.deleteLater)
            # the verb is one short line, already in flight; a sender that wrote nothing
            # (or anything but "quit") means show
            if not conn.bytesAvailable():
                conn.waitForReadyRead(_CONNECT_TIMEOUT_MS)
            verb = bytes(conn.readAll()).strip().lower()
            conn.close()
            try:
                if verb == b"quit" and self._on_quit is not None:
                    self._on_quit()
                else:
                    self._on_present()
            except Exception:
                pass


def acquire(on_present: Callable[[], None],
            on_quit: Callable[[], None] | None = None) -> InstanceGuard | None:
    """None = another instance owns the panel and has been asked to present itself.
    Otherwise the returned guard is this process's ownership; a listen failure
    degrades to running unguarded rather than refusing to start."""
    if notify_running():
        return None
    guard = InstanceGuard(on_present, on_quit=on_quit)
    guard.listen()
    return guard
