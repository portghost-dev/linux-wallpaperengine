"""A scripted stand-in for the engine, on a socket path the test gives.

One thread accepts any number of connections in arrival order and reads one request from each, the
way api_client talks: one JSON line with id, cmd and args. It records (cmd, args) in calls and
answers from that verb's script, one step per request: done(result), fail(message) before or after
an accepted line, or silent(), which answers nothing until the client hangs up; done and fail wait
their delay before the final line. A verb with no step left answers done: status with the status
built from the fields the test sets, any other verb with an empty result. A field set to None is left
out of the status. stop() closes the socket and removes its file. Unless served=False, the engine is
recorded in the sync marker as the served engine at its start, as an engine that has already taken
the panel's settings would be.
"""
from __future__ import annotations

import json
import os
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FIELDS = ("version", "pid", "speed", "current", "rotation", "lanes", "schedule", "outputs", "config",
          "audio_smooth")


@dataclass(frozen=True)
class Step:
    kind: str
    result: Any = None
    message: str = ""
    accepted: bool = False
    delay: float = 0.0


def done(result: Any = None, accepted: bool = False, delay: float = 0.0) -> Step:
    """A done reply, after an accepted line when accepted is True; a result of None answers status
    with the built status and any other verb with {}."""
    return Step("done", result=result, accepted=accepted, delay=delay)


def fail(message: str, after_accepted: bool = False, delay: float = 0.0) -> Step:
    """An ok=false reply carrying message, before or after an accepted line."""
    return Step("fail", message=message, accepted=after_accepted, delay=delay)


def silent() -> Step:
    """No answer: the connection stays open until the client hangs up."""
    return Step("silent")


class FakeEngine:
    def __init__(self, path: str | os.PathLike, served: bool = True, **fields: Any) -> None:
        from lwe_ui import version
        from lwe_ui.engine import marker
        self.path = Path(path)
        self.calls: list[tuple[str, dict]] = []
        self.fields: dict[str, Any] = {
            "version": version.panel_stamp(),
            "pid": os.getpid(),
            "speed": 1.0,
            "current": {"id": "", "ui_id": "", "title": ""},
            "rotation": {"enabled": False, "interval_s": 900, "next_in_s": -1, "order": "shuffle", "count": 0,
                         "label": ""},
            "lanes": [],
            "schedule": {"enabled": False, "entries": [], "active": "", "held": False, "pending": ""},
            "outputs": {"state": "live", "reason": ""},
            "config": {},
            "audio_smooth": 90.0,
        }
        self.set(**fields)
        if served:
            marker.record_served(self.fields["pid"])
        self._scripts: dict[str, list[Step]] = {}
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(self.path))
        self._server.listen(16)
        self._server.settimeout(0.05)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def __enter__(self) -> FakeEngine:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def set(self, **fields: Any) -> None:
        """Set status fields; only the names in FIELDS."""
        unknown = sorted(set(fields) - set(FIELDS))
        if unknown:
            raise TypeError(f"unknown status fields: {', '.join(unknown)}")
        self.fields.update(fields)

    def script(self, cmd: str, *steps: Step) -> None:
        """Queue steps for cmd; each request of cmd takes the next one."""
        with self._lock:
            self._scripts.setdefault(cmd, []).extend(steps)

    def status(self) -> dict[str, Any]:
        return {"api": 1, **{name: value for name, value in self.fields.items() if value is not None}}

    def stop(self) -> None:
        self._stopping.set()
        self._thread.join(5)
        self._server.close()
        self.path.unlink(missing_ok=True)

    def _serve(self) -> None:
        while not self._stopping.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                self._answer(conn)

    def _answer(self, conn: socket.socket) -> None:
        conn.settimeout(5.0)
        buf = b""
        try:
            while b"\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buf += chunk
            request = json.loads(buf.split(b"\n", 1)[0])
        except (OSError, ValueError):
            return
        cmd, args, rid = str(request.get("cmd", "")), request.get("args") or {}, request.get("id", 1)
        with self._lock:
            self.calls.append((cmd, args))
            steps = self._scripts.get(cmd)
            step = steps.pop(0) if steps else done()
        try:
            self._play(conn, rid, cmd, step)
        except OSError:
            pass

    def _play(self, conn: socket.socket, rid: Any, cmd: str, step: Step) -> None:
        if step.kind == "silent":
            self._hold(conn)
            return
        if step.accepted:
            self._send(conn, {"id": rid, "ok": True, "status": "accepted"})
        if step.delay and self._stopping.wait(step.delay):
            return
        if step.kind == "fail":
            self._send(conn, {"id": rid, "ok": False, "error": step.message})
            return
        result = step.result if step.result is not None else (self.status() if cmd == "status" else {})
        self._send(conn, {"id": rid, "ok": True, "status": "done", "result": result})

    def _hold(self, conn: socket.socket) -> None:
        conn.settimeout(0.05)
        while not self._stopping.is_set():
            try:
                if not conn.recv(4096):
                    return
            except socket.timeout:
                continue
            except OSError:
                return

    @staticmethod
    def _send(conn: socket.socket, reply: dict[str, Any]) -> None:
        conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
