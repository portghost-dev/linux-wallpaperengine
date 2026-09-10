"""Import wizard live orchestrator: drives the BenchSession from a real windowed engine and
maps each terminal human action to a records event. Exposed to QML as `wizardBridge`.

Flow: open(wid) -> phase p1 (opt-out gate) -> runWizard() runs the static census (missing dep short-
circuits to the 16e modal via depNeeded) -> phase p2 (expectation-setter) -> proceedToBench()
launches a NON-silent windowed engine beside the live wallpaper, feeding LWE-PRESENT/fatal lines and
a poll timer into the BenchSession -> verdict phase (pass | fixable | fail). approve/deny/cancel/
importUntested each write the right record event and graduate or trash via the existing backend.

Constitution wiring: the machine only ever asserts crash-vs-alive (BenchSession); the human's eyes
judge the live bench window and click the verdict. C5 crash-vs-close keys off PRESENTED, not the exit
signal: a window that ended before it ever presented a frame is a crash (it never ran as a wallpaper),
whether it self-quit NormalExit or died by signal; only a window that presented and was then dismissed
with no fatal line is an inconclusive user close - see _on_finished.

Testability: the engine launch (`_launcher`) and the clock (`_clock`) are seams. Tests inject a fake
launcher and a controlled clock and drive _on_line/_on_finished/_poll directly, so the phase machine
and event emission are provable with no GPU. The real QProcess path is exercised by manual visual verification.
"""
from __future__ import annotations

import math
import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal, Slot

from . import api_client
from . import placement
from . import texcomp
from .dev import _assets_dir, _engine_bin, overlay_text, sample_exhibit
from .storage import paths, records, settings, wizard
from .storage.bench_verdict import BenchSession, is_fatal_line, is_first_frame_line

OVERLAY_CORNER = "top-left"


def bench_sock_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip() or f"/run/user/{os.getuid()}"
    return Path(runtime) / "lwe" / "wizard-bench.sock"


def bench_env(title: str) -> dict[str, str]:
    """The bench engine's environment: its label, its socket, and the present trace that
    prints the first-frame marker the verdict listens for."""
    return {"LWE_OVERLAY_TEXT": title or "Workshop Benching",
            "LWE_SOCKET": str(bench_sock_path()),
            "LWE_PRESENTTRACE": "1"}


def bench_overlay_text(title: str, sample: dict | None, run_remaining: float | None,
                       load_remaining: float | None) -> str:
    """The bench overlay: the item's stats block, a blank line, then the countdown. After the
    first frame it counts the run window down; before it, the load window."""
    def secs(v: float) -> str:
        n = max(0, math.ceil(v))
        return f"{n} second" if n == 1 else f"{n} seconds"

    if run_remaining is not None:
        tail = f"Bench Testing In Progress. Scene ends in {secs(run_remaining)}."
    elif load_remaining is not None:
        tail = f"Bench Testing In Progress. Waiting for the first frame, {secs(load_remaining)} left."
    else:
        tail = "Bench Testing In Progress."
    return overlay_text(title, sample) + "\n\n" + tail

_ENGINE_COMM = "linux-wallpaper"


class WizardBridge(QObject):
    phaseChanged = Signal()
    compChanged = Signal()
    depNeeded = Signal(str, str)
    note = Signal(str)
    graduated = Signal(str)
    trashedUnsub = Signal(str, str)
    # worker results carry the session they were sampled for; a late result from a finished
    # bench must never land on the next one
    _vramSampled = Signal(object, int)
    _benchFrames = Signal(object, int)

    def __init__(self, backend, workshop, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._workshop = workshop
        self._phase = ""            # "" c0 c1 c2 p1 p2 p3 pass fixable fail
        self._wid = ""
        self._title = ""
        self._wtype = ""
        self._census: dict = {}
        self._session: BenchSession | None = None
        self._lineage: list[str] = []
        self._fixed = False
        self._chash = ""
        self._saw_fatal = False
        self._proc: QProcess | None = None
        self._peers: list = []
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._poll)
        self._comp: dict = {}
        self._comp_done = 0
        self._comp_total = 0
        self._comp_cancel = False
        self._clock = time.monotonic
        self._launcher = self._qprocess_launch
        self._fixable = self._default_fixable
        self._scanner = texcomp.scan
        self._encoder = texcomp.encode_scene
        self._async = lambda fn: threading.Thread(target=fn, daemon=True).start()
        self._vram_ticks = 0
        self._vram_busy = False
        self._vramSampled.connect(self._on_vram_sampled)
        self._overlay_busy = False
        self._overlay_prev: dict = {}
        self._benchFrames.connect(self._on_bench_frames)
        self._placed = False
        self._place_tries = 0
        self._cell = "A"
        self._place_timer = QTimer(self)
        self._place_timer.setInterval(400)
        self._place_timer.timeout.connect(self._place_tick)

    @Slot(result=str)
    def phase(self) -> str:
        return self._phase

    @Slot(result=str)
    def wid(self) -> str:
        return self._wid

    @Slot(result=str)
    def wpTitle(self) -> str:
        return self._title

    @Slot(result=str)
    def wpType(self) -> str:
        """The wallpaper type (scene / video / web) for the bench banner's type pill."""
        return self._wtype

    @Slot(result=int)
    def peakMb(self) -> int:
        return self._session.peak_mb if self._session else -1

    @Slot(result=int)
    def benchLoadRemaining(self) -> int:
        """Seconds left in the Workshop bench load lease: counts down from LOAD_TIMEOUT
        while the scene is still loading, -1 once it presents / exits / the session is gone. The deck
        shows this as the 30s 'Lease expires' countdown and hides it the moment the scene comes alive."""
        s = self._session
        if s is None:
            return -1
        rem = s.load_remaining(self._clock())
        if rem < 0:
            return -1
        import math
        return int(math.ceil(rem))

    @Slot()
    def killBench(self) -> None:
        """Lock-up escape hatch: if a Workshop bench never comes alive within the load
        lease, force it down - kill the windowed preview and dismiss. The backend
        already fails a no-first-frame bench at LOAD_TIMEOUT (poll -> 'crashed'/fail verdict); this is
        the belt for a wedge where that resolution never fires. Guarded to p3 so a late call after a
        real verdict is a no-op."""
        if self._phase != "p3":
            return
        self._log_bench("killed: load-lease safety (lock-up handler)\n")
        self.note.emit("Bench closed - the preview never came up")
        self.close()

    @Slot(result=str)
    def reason(self) -> str:
        return self._session.reason if self._session else ""

    def _set_phase(self, p: str) -> None:
        self._phase = p
        self.phaseChanged.emit()

    def set_engine_peers(self, peers: list) -> None:
        self._peers = list(peers)

    @Slot(result=bool)
    def engineBusy(self) -> bool:
        return self._phase == "p3" or (self._proc is not None
                                       and self._proc.state() != QProcess.ProcessState.NotRunning)

    @Slot(str, str)
    def open(self, wid: str, title: str) -> None:
        if not paths.is_safe_wid(str(wid)):
            return
        self._teardown_bench()
        self._wid = str(wid)
        self._title = str(title)
        try:
            d0 = self._workshop._resolve_dir(self._wid)
            if d0:
                from .discovery import project as _project
                self._wtype = str(_project.read(str(d0)).get("type") or "")
            else:
                self._wtype = ""
        except Exception:
            self._wtype = ""
        self._census = {}
        self._session = None
        self._lineage = []
        self._fixed = False
        self._chash = ""
        self._saw_fatal = False
        self._comp = {}
        self._comp_done = 0
        self._comp_total = 0
        self._comp_cancel = False
        if self._wtype == "scene":
            self._set_phase("c0")
            self._start_scan()
        else:
            self._set_phase("p1")

    @Slot()
    def close(self) -> None:
        self._comp_cancel = True
        self._teardown_bench()
        self._set_phase("")

    @Slot(str)
    def importUntested(self, comment: str) -> None:
        """P1 power-user door: graduate to the library with no bench (approved-untested event)."""
        records.append(self._wid, wizard.approved_untested(where="workshop", comment=comment or None))
        self._backend.approveReview(self._wid)
        self.graduated.emit(self._wid)
        self.close()

    def _start_scan(self) -> None:
        """Off-thread pkg-header inspection; lands in c1 with the facts. Stale results
        (the modal moved on, or another wallpaper opened) are dropped."""
        wid = self._wid

        def work() -> None:
            d = self._workshop._resolve_dir(wid)
            try:
                facts = self._scanner(str(d)) if d else {}
            except Exception:
                facts = {}
            if self._wid != wid or self._phase != "c0":
                return
            self._comp = facts
            self.compChanged.emit()
            self._set_phase("c1")

        self._async(work)

    @Slot(result="QVariantMap")
    def compFacts(self) -> dict:
        return dict(self._comp)

    @Slot(result=int)
    def compDone(self) -> int:
        return self._comp_done

    @Slot(result=int)
    def compTotal(self) -> int:
        return self._comp_total

    @Slot()
    def startCompression(self) -> None:
        """All-core encode of the scan's TODO set; progress per texture; lands on the
        bench card when done. A mid-run close cancels between textures (a started
        texture completes - atomic cache writes mean no torn entries either way)."""
        if self._phase != "c1" or not self._comp.get("todo"):
            return
        wid = self._wid
        d = self._workshop._resolve_dir(wid)
        if not d:
            self.note.emit("Wallpaper files not found")
            self.close()
            return
        self._comp_done = 0
        self._comp_total = int(self._comp.get("todo") or 0)
        self._set_phase("c2")

        def on_progress(done: int, total: int) -> None:
            self._comp_done = done
            self._comp_total = total
            self.compChanged.emit()

        def work() -> None:
            try:
                self._encoder(str(d), wid, progress=on_progress,
                              cancelled=lambda: self._comp_cancel)
            except Exception:
                pass
            if self._comp_cancel or self._wid != wid or self._phase != "c2":
                return
            self._set_phase("p1")

        self._async(work)

    @Slot()
    def skipCompression(self) -> None:
        """Skip (todo>0) and Continue (nothing to do) are the same transition: on to
        the bench card. Skipped items are caught later by the engine's idle-priority
        on-demand fallback."""
        if self._phase != "c1":
            return
        self._set_phase("p1")

    @Slot()
    def runWizard(self) -> None:
        """Static census on the Run click; a missing dependency short-circuits to the 16e modal
        (do NOT bench a scene that structurally cannot render)."""
        d = self._workshop._resolve_dir(self._wid)
        if not d:
            self.note.emit("Wallpaper files not found")
            self.close()
            return
        self._census = wizard.run_census(
            d, workshop_dir=str(paths.pending_root_for(
                self._wid, settings.load().get("WORKSHOP_DIR") or paths.detect_workshop_dir())))
        if self._census.get("missingDep"):
            self.depNeeded.emit(self._wid, self._title)
            self.close()
            return
        self._set_phase("p2")

    @Slot()
    def proceedToBench(self) -> None:
        # show P3 FIRST, then do the launch a tick later so the UI repaints the "benching" screen
        # before the (brief) pause/launch work - never freeze on P2. Re-entrancy guarded so repeated
        # clicks cannot stack a second launch.
        if self._phase == "p3" or self._session is not None:
            return
        self._set_phase("p3")
        QTimer.singleShot(0, self._launch_bench)

    def _launch_bench(self) -> None:
        d = self._workshop._resolve_dir(self._wid)
        if not d:
            self.note.emit("Wallpaper files not found")
            self.close()
            return
        try:
            self._chash = records.content_hash(d)
        except Exception:
            self._chash = ""
        self._saw_fatal = False
        self._session = BenchSession()
        geo = self._spawn_geometry()
        try:
            bench_sock_path().unlink()
        except OSError:
            pass
        # NON-silent (no --silent) so the engine prints one LWE-PRESENT line per presented frame;
        # the socket is how the overlay text reaches the window while it runs.
        argv = [_engine_bin(), "--assets-dir", _assets_dir(), "--fps", "30", "--scaling", "default",
                "--no-audio-processing", "--disable-mouse", "--no-fullscreen-pause",
                "--window", geo, "--api-socket", "--bg", d]
        try:
            (paths.bench_log_file()).write_text(
                "=== bench " + self._wid + " ===\n" + " ".join(argv) + "\n", encoding="utf-8")
        except OSError:
            pass
        self._overlay_prev = {}
        self._placed = False
        self._place_tries = 0
        self._session.on_launch(self._clock())
        self._launcher(argv)
        self._timer.start()
        self._place_timer.start()

    def _bench_cell(self) -> str:
        """Top-left unless a Developer exhibit already holds it and the top-right is free, so the
        window the human judges is the bench and not an exhibit stacked on it."""
        for p in self._peers:
            try:
                if p.alive("A") and not p.alive("B"):
                    return "B"
            except Exception:
                continue
        return "A"

    def _spawn_geometry(self) -> str:
        """The bench's cell of the focused output, the same size the Developer exhibits use;
        the workshop's monitor-ratio quadrant when the compositor cannot be asked."""
        self._cell = self._bench_cell()
        geo = placement.window_geometry(self._cell, placement.layout())
        if geo:
            return geo
        try:
            return self._workshop._spawn_geometry()
        except Exception:
            return "0x0x1280x720"

    def _place_tick(self) -> None:
        """Move the bench window into the top-left cell once it maps, verifying the placement
        stuck; give up after a few tries and leave it where the user put it."""
        if self._proc is None or self._placed:
            self._place_timer.stop()
            return
        try:
            pid = int(self._proc.processId())
        except Exception:
            return
        win = next((c for c in placement.clients() if c.get("pid") == pid), None)
        if win is None:
            return
        lay = placement.layout()
        if not lay:
            self._place_timer.stop()
            return
        cell = placement.quadrant(self._cell, lay)
        if placement.placed(win, cell) or self._place_tries >= placement.PLACE_TRIES:
            self._placed = True
            self._place_timer.stop()
            return
        self._place_tries += 1
        placement.place(win, cell)

    @Slot(result=int)
    def benchRunRemaining(self) -> int:
        """Seconds left in the run window once the scene has presented; -1 before that."""
        s = self._session
        if s is None or not s.presented or s.done:
            return -1
        return max(0, math.ceil(s.run_seconds - s.running_seconds(self._clock())))

    def _push_overlay(self) -> None:
        """Once a second while the bench runs: sample the bench process on a worker thread and
        push the stats block plus the countdown through its socket. Silent until the socket is up."""
        s = self._session
        if self._proc is None or s is None or s.done or self._overlay_busy:
            return
        try:
            pid = int(self._proc.processId())
        except Exception:
            return
        if pid <= 0:
            return
        sock = bench_sock_path()
        if not api_client.available(sock):
            return
        title, prev, clock = self._title or "Workshop Benching", dict(self._overlay_prev), self._clock
        self._overlay_busy = True

        def work() -> None:
            try:
                sample, nxt = sample_exhibit(pid, sock, prev)
                if s is not self._session:
                    return
                self._overlay_prev = nxt
                if isinstance(nxt.get("frames"), int) and nxt["frames"] > 0:
                    self._benchFrames.emit(s, nxt["frames"])
                # the countdown is read right before the push, after the sampling forks
                t = clock()
                run_rem = (s.run_seconds - s.running_seconds(t)) if s.presented else None
                load_rem = s.load_remaining(t) if not s.presented else None
                api_client.set_overlay(text=bench_overlay_text(title, sample, run_rem, load_rem),
                                       corner=OVERLAY_CORNER, sock=sock)
            except Exception:
                pass
            finally:
                self._overlay_busy = False

        self._async(work)

    def _on_bench_frames(self, session: object, frames: int) -> None:
        """The socket's frame counter is the second liveness signal: a scene that has rendered
        frames has presented, whether or not its log line was seen."""
        s = self._session
        if session is s and s is not None and not s.done and not s.presented and frames > 0:
            s.on_first_frame(self._clock())

    def _qprocess_launch(self, argv: list[str]) -> None:
        proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        for k, v in bench_env(self._title).items():
            env.insert(k, v)
        proc.setProcessEnvironment(env)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._drain_output())
        proc.finished.connect(lambda code, status: self._on_finished(status, code))
        proc.errorOccurred.connect(self._on_proc_error)
        self._proc = proc
        proc.start(argv[0], argv[1:])

    def _log_bench(self, text: str) -> None:
        """Append to the per-bench diagnostic log (state/lwe/logs/bench/bench.log, truncated per bench in
        _launch_bench). Best-effort: a logging failure never affects the bench."""
        try:
            with open(paths.bench_log_file(), "a", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass

    def _drain_output(self) -> None:
        if self._proc is None:
            return
        try:
            data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        except Exception:
            return
        # mirror the bench engine's output to a log so an instant-crasher is diagnosable after the
        # window is gone
        self._log_bench(data)
        for line in data.splitlines():
            self._on_line(line)

    def _on_line(self, line: str) -> None:
        if self._session is None or self._session.done:
            return
        if is_first_frame_line(line):
            self._session.on_first_frame(self._clock())
        elif is_fatal_line(line):
            self._saw_fatal = True
            self._session.on_fatal_log()
            self._finish_bench()

    def _poll(self) -> None:
        if self._session is None:
            return
        # once a second, off the GUI thread; the result comes back through _vramSampled
        self._vram_ticks += 1
        if self._vram_ticks % 4 == 0:
            self._sample_vram()
            self._push_overlay()
        self._session.poll(self._clock())
        if self._session.done:
            self._finish_bench()

    def _on_finished(self, status=None, code=None) -> None:
        """C5 crash-vs-close keyed off PRESENTED, not the exit signal. A window that ended before it
        EVER presented a frame did not run as a wallpaper, so it is a crash - whether it self-quit
        (NormalExit, e.g. a bad-project abort like 'Projection must have a width' that exits without
        a signal) or died by signal (CrashExit). The ONLY inconclusive case is a window that
        PRESENTED and was then dismissed with no fatal line: the user watched a live scene and closed
        it (Super+Q arrives as CrashExit, a clean close as NormalExit - both inconclusive). Keying off
        `presented` avoids depending on the exit signal, which is not a reliable discriminator: the
        'Live Solar System' instant-crasher exited as a NormalExit - deduced from observed
        observation that it bounced straight back to P2, since a CrashExit before any frame would
        already have hit the on_exit crash branch - and the old code read that NormalExit as a user
        close, so an instant crash reached NO verdict. The exit CODE's value was never captured, which
        is exactly why the decision must not rest on it. One accepted edge: a user who closes a heavy
        scene mid-load
        (before its first frame) now reads as a crash, consistent with the no_first_frame rule; a
        re-bench is one click.

        The exit code/status are logged to wizard-bench.log for the record (they do not drive the
        decision) so the actual exit signature is captured on every real bench."""
        if self._session is None:
            return
        self._log_bench(f"finished: status={status} code={code} "
                        f"presented={self._session.presented} saw_fatal={self._saw_fatal}\n")
        if self._saw_fatal or not self._session.presented:
            self._session.on_exit(self._clock())
        else:
            self._session.on_user_close()
        self._finish_bench()

    def _on_proc_error(self, err) -> None:
        if err == QProcess.ProcessError.FailedToStart:
            self._teardown_bench()
            self._session = None
            self.note.emit("The engine failed to start - check Settings > Engine")
            self._set_phase("p2")

    def _sample_vram(self) -> None:
        """Best-effort peak VRAM (nvidia-smi pmon fb). Missing instrument -> peak stays -1."""
        if self._proc is None or self._session is None or self._vram_busy:
            return
        try:
            pid = int(self._proc.processId())
        except Exception:
            return
        if pid <= 0:
            return
        self._vram_busy = True
        session = self._session

        def work() -> None:
            mb = -1
            try:
                import subprocess
                r = subprocess.run(["nvidia-smi", "pmon", "-c", "1", "-s", "m"],
                                   capture_output=True, text=True, timeout=3, check=False)
                if r.returncode == 0:
                    for ln in r.stdout.splitlines():
                        parts = ln.split()
                        if len(parts) >= 4 and parts[1].isdigit() and int(parts[1]) == pid:
                            if parts[3].isdigit():
                                mb = int(parts[3])
            except Exception:
                mb = -1
            finally:
                self._vram_busy = False
            self._vramSampled.emit(session, mb)

        self._async(work)

    def _on_vram_sampled(self, session: object, mb: int) -> None:
        if session is self._session and session is not None and mb >= 0:
            self._session.on_vram(mb)

    def _finish_bench(self) -> None:
        """Verdict reached: stop the timers, kill the engine, set the phase."""
        s = self._session
        if s is None or not s.done:
            return
        self._timer.stop()
        self._place_timer.stop()
        self._kill_proc()
        if s.verdict == "ran":
            self._set_phase("pass")
        elif s.verdict == "crashed":
            if s.reason:
                self._lineage = self._lineage + ["crashed" if s.reason != "no_first_frame"
                                                 else "no_first_frame"]
            self._set_phase("fixable" if (self._fixable() and not self._fixed) else "fail")
        else:
            # inconclusive: the session is spent, so a re-bench must be allowed past the guard
            self._session = None
            self._set_phase("p2")

    def _kill_proc(self) -> None:
        if self._proc is not None:
            try:
                self._proc.finished.disconnect()
            except Exception:
                pass
            if self._proc.state() != QProcess.ProcessState.NotRunning:
                self._proc.kill()
            self._proc.deleteLater()
            self._proc = None
        try:
            bench_sock_path().unlink()
        except OSError:
            pass

    def _teardown_bench(self) -> None:
        self._timer.stop()
        self._place_timer.stop()
        self._kill_proc()

    def _default_fixable(self) -> bool:
        """Whether a deterministic fix exists for this crash. The reviewer's fix catalog (R16 etc.)
        is not wired in-repo yet, so this is False for now - a crash goes straight to 'fail'. The
        fixable branch + applyFixesAndRetry are built and ready for that catalog to land."""
        return False

    @Slot()
    def applyFixesAndRetry(self) -> None:
        """Bounded ONE-shot: apply the deterministic fix and re-bench. A second crash is terminal
        (no fix->bench loop)."""
        if self._fixed:
            self._set_phase("fail")
            return
        self._fixed = True
        self._lineage = self._lineage + ["fixes_applied"]
        self._session = None
        self._set_phase("p3")
        QTimer.singleShot(0, self._launch_bench)

    @Slot(str)
    def approve(self, comment: str) -> None:
        records.append(self._wid, wizard.approved_via_wizard(
            content_hash=self._chash, comment=comment or None))
        self._backend.approveReview(self._wid)
        self.graduated.emit(self._wid)
        self.close()

    @Slot(str)
    def deny(self, comment: str) -> None:
        wid, title = self._wid, self._title
        records.append(wid, wizard.deleted_wizard_recommended(
            lineage=(self._lineage + ["recommended_trash"]),
            content_hash=self._chash,
            repair_attempts=(["applied"] if self._fixed else []),
            comment=comment or None))
        # the wizard already wrote the RICH deletion record above, so it does the FILE work only
        # (trashFiles), never trashItem - otherwise the deletion would be double-recorded.
        self._workshop.trashFiles(wid)
        self.close()
        self.trashedUnsub.emit(wid, title)

    @Slot(str)
    def cancel(self, comment: str) -> None:
        """Cancel. If the bench produced a finding (crashed), log benched_no_decision so the finding
        is recalled and blame rests on the user; the item stays pending. A pre-bench cancel logs
        nothing."""
        if self._session and self._session.verdict == "crashed":
            records.append(self._wid, wizard.benched_no_decision(
                lineage=(self._lineage + ["recommended_trash"]),
                content_hash=self._chash,
                repair_attempts=(["applied"] if self._fixed else []),
                comment=comment or None))
        self.close()
