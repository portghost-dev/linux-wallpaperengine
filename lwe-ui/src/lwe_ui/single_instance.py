"""Single-instance guard for the panel.

One live panel per user: a QLocalServer on $XDG_RUNTIME_DIR/lwe/ui.sock. A
second launch connects, asks the owner to present its window, and exits instead
of starting a duplicate (a duplicate means two tray icons and two writers over
settings and state). Any accepted connection counts as a present request - the
payload is ignored. A dead socket left by a crash fails the connect probe, gets
removed, and ownership is taken over.
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


def notify_running(path: Path | None = None) -> bool:
    """True when a live owner accepted the present request."""
    sock = QLocalSocket()
    sock.connectToServer(str(path if path is not None else socket_path()))
    if not sock.waitForConnected(_CONNECT_TIMEOUT_MS):
        return False
    sock.write(b"show\n")
    sock.waitForBytesWritten(_CONNECT_TIMEOUT_MS)
    sock.disconnectFromServer()
    return True


class InstanceGuard(QObject):
    def __init__(self, on_present: Callable[[], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._on_present = on_present
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
            conn.close()
            try:
                self._on_present()
            except Exception:
                pass


def acquire(on_present: Callable[[], None]) -> InstanceGuard | None:
    """None = another instance owns the panel and has been asked to present itself.
    Otherwise the returned guard is this process's ownership; a listen failure
    degrades to running unguarded rather than refusing to start."""
    if notify_running():
        return None
    guard = InstanceGuard(on_present)
    guard.listen()
    return guard
