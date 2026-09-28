"""The bench command's engine run, without Qt: the argv it shares with the wizard's bench, one
windowed engine run whose output goes to a log, and what the run showed."""
from __future__ import annotations

import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import NamedTuple

from .. import placement
from ..storage import bench_verdict

PLACE_EVERY_S = 0.4
KILL_AFTER_S = 3.0
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class Summary(NamedTuple):
    first_frame_s: float | None
    ran_s: float
    ended: str
    exit_code: int | None
    signal: int | None
    fatal_line: str | None


def bench_argv(engine: str, assets: str, geometry: str, render_dir: str, socket: bool) -> list[str]:
    return ([engine, "--assets-dir", assets, "--fps", "30", "--scaling", "default", "--no-audio-processing",
             "--disable-mouse", "--no-fullscreen-pause", "--window", geometry]
            + (["--api-socket"] if socket else []) + ["--bg", render_dir])


class _Placer:
    """Moves the engine's window into the top-left cell once it maps, as the wizard's bench does."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.tries = 0
        self.done = False

    def tick(self) -> None:
        if self.done:
            return
        try:
            win = next((c for c in placement.clients() if c.get("pid") == self.pid), None)
            if win is None:
                return
            lay = placement.layout()
            if not lay:
                self.done = True
                return
            cell = placement.quadrant("A", lay)
            if placement.placed(win, cell) or self.tries >= placement.PLACE_TRIES:
                self.done = True
                return
            self.tries += 1
            placement.place(win, cell)
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            self.done = True


def _send(action) -> None:
    try:
        action()
    except OSError:
        pass


def run(argv: list[str], env: dict[str, str], log_path: str | Path, title: str, wid: str,
        launcher=subprocess.Popen) -> Summary:
    """Run the engine with env plus the present trace and title as its overlay text, its merged output
    appended line by line to a fresh log, until it exits. SIGINT, SIGTERM or SIGHUP send it SIGTERM,
    then SIGKILL after KILL_AFTER_S."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: dict = {"first": None, "fatal": None}
    stop_at: list[float] = []
    engine: list = []
    lock = threading.Lock()

    def stop(_signum, _frame) -> None:
        if not stop_at:
            stop_at.append(time.monotonic())
            if engine:
                _send(engine[0].terminate)

    previous = {s: signal.signal(s, stop) for s in STOP_SIGNALS}
    try:
        with open(path, "w", encoding="utf-8") as log:
            log.write(f"=== bench {wid} ===\n" + " ".join(argv) + "\n")
            log.flush()
            start = time.monotonic()
            proc = launcher(argv, env={**env, "LWE_PRESENTTRACE": "1", "LWE_OVERLAY_TEXT": title},
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            engine.append(proc)
            if stop_at:
                _send(proc.terminate)

            def read() -> None:
                for raw in iter(proc.stdout.readline, b""):
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    with lock:
                        if log.closed:
                            return
                        log.write(line + "\n")
                        log.flush()
                    if seen["first"] is None and bench_verdict.is_first_frame_line(line):
                        seen["first"] = time.monotonic() - start
                    if seen["fatal"] is None and bench_verdict.is_fatal_line(line):
                        seen["fatal"] = line

            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            placer = _Placer(proc.pid)
            try:
                while True:
                    try:
                        proc.wait(timeout=PLACE_EVERY_S)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    if stop_at:
                        if time.monotonic() - stop_at[0] >= KILL_AFTER_S:
                            _send(proc.kill)
                    else:
                        placer.tick()
            except BaseException:
                _send(proc.kill)
                proc.wait()
                raise
            ran = time.monotonic() - start
            reader.join(2.0)
            with lock:
                log.close()
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)
    code = proc.returncode
    if stop_at:
        ended = "stopped"
    elif code < 0:
        ended = "signal"
    else:
        ended = "closed" if code == 0 else "exit"
    return Summary(seen["first"], ran, ended, code if code >= 0 else None, -code if code < 0 else None,
                   seen["fatal"])
