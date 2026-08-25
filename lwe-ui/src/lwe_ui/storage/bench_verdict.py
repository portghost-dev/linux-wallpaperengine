"""Wizard bench state machine + engine-log parsers. PURE: no Qt, no GPU, no launch.

The wizard's constitution (C1): the machine claims ONLY crash-vs-alive. Freeze, accuracy, and "does
it look right" are the human's eyes. So this state machine turns a stream of launch observations into
exactly one of two verdicts - "ran" (survived the run window) or "crashed" (died first) - plus the
peak VRAM it saw. A user-closed window is inconclusive, never a crash (C5).

Health clock: the run window starts at the FIRST PRESENTED FRAME,
not at window-open, because a heavy 4K scene can compile past 15s and a launch-anchored clock would
false-fail it. The machine is FED observations (the caller supplies the timestamps), so it is fully
testable with synthetic streams - no real engine, no GPU. The live QProcess driver that feeds it lands
later, with the QML flow it drives.

DRIVER CONTRACT (C5-critical, F-adv2): the driver distinguishes a user close from a crash by
PRESENTED, not by the exit signal. When the user closes a bench window that HAD PRESENTED a frame,
the driver must call on_user_close() (inconclusive) - NEVER on_exit(). But a process that exits
before it EVER presents a frame did not run as a wallpaper, so the driver calls on_exit() (crashed)
regardless of NormalExit/CrashExit - a bad-project abort self-quits NormalExit and must still fail,
not read as a user close. on_exit() means "did not survive the run" and always yields crashed.
Likewise the driver must poll-to-ran BEFORE it terminates a healthy scene, so its own terminate (an
on_exit after done) is a no-op rather than a false crash.
"""
from __future__ import annotations

RUN_SECONDS = 15.0        # of RUNNING (post first-frame): the "tiktok attention" ceiling
LOAD_TIMEOUT = 30.0       # no first frame within this wall time => it never rendered => crashed

# The engine, run non-silent, prints one LWE-PRESENT line per presented frame; the first = liveness.
_PRESENT_MARKER = "LWE-PRESENT"
# fatal engine/runtime lines that mean the run is dead even if the process lingers a moment.
# Deliberately SPECIFIC to crash traces (F-adv1): a broad token like "aborted" would false-crash a
# benign line ("render pass aborted, retrying"), the one thing C1 forbids. Process exit is the
# primary crash signal anyway (on_exit); this log check is only a belt for a lingering fatal error.
_FATAL_MARKERS = ("terminate called", "segmentation fault", "what():", "std::bad_alloc",
                  "lwe-fatal", "core dumped")


def is_first_frame_line(line: str) -> bool:
    """True for the engine's per-frame present marker (the liveness/first-frame signal)."""
    return _PRESENT_MARKER in (line or "")


def is_fatal_line(line: str) -> bool:
    """True for a runtime line that means the scene died (crash/abort), case-insensitive."""
    low = (line or "").lower()
    return any(m in low for m in _FATAL_MARKERS)


class BenchSession:
    """Fed observations, computes crash-vs-alive. All times are caller-supplied monotonic seconds.

    Lifecycle: on_launch(t0) -> [on_first_frame(t), on_vram(mb), on_fatal_log(), on_exit(t),
    on_user_close()] with poll(t) called periodically. Terminal states set `done`; read `verdict`
    ("ran" | "crashed" | None-if-inconclusive), `reason`, `peak_mb`.
    """

    def __init__(self, run_seconds: float = RUN_SECONDS, load_timeout: float = LOAD_TIMEOUT):
        self.run_seconds = float(run_seconds)
        self.load_timeout = float(load_timeout)
        self.state = "loading"      # loading | running | done
        self.verdict = None         # "ran" | "crashed" | None (inconclusive)
        self.reason = ""            # "" | exit | fatal_log | no_first_frame | user_close
        self.inconclusive = False
        self.peak_mb = -1
        self._launch_t: float | None = None
        self._frame_t: float | None = None

    def on_launch(self, t: float) -> None:
        if self._launch_t is None:
            self._launch_t = float(t)

    def on_first_frame(self, t: float) -> None:
        """First presented frame: starts the run clock (health clock anchors HERE, not at launch)."""
        if self.state == "loading":
            self.state = "running"
            self._frame_t = float(t)

    def on_vram(self, mb) -> None:
        try:
            v = int(mb)
        except (TypeError, ValueError):
            return
        if v > self.peak_mb:
            self.peak_mb = v

    def on_fatal_log(self) -> None:
        """A fatal runtime line = crashed, even before the process exit signal arrives."""
        self._finish("crashed", "fatal_log")

    def on_exit(self, t: float | None = None) -> None:
        """The process ended. If it ended before the run window closed, it did NOT survive => crashed
        (even a 0 exit code: an engine that quits itself is not running as a wallpaper). Ignored once
        done, so the driver's OWN terminate after a "ran" verdict never records a crash - the driver
        polls-to-ran FIRST, then kills."""
        self._finish("crashed", "exit")

    def on_user_close(self) -> None:
        """A user-closed bench window is inconclusive, never a crash-fail."""
        if self.state == "done":
            return
        self.state = "done"
        self.verdict = None
        self.inconclusive = True
        self.reason = "user_close"

    def poll(self, t: float) -> None:
        """Advance time: RUN_SECONDS of running since first frame => ran; LOAD_TIMEOUT of wall time
        with no first frame => crashed (never rendered)."""
        if self.state == "done":
            return
        if self.state == "running" and self._frame_t is not None \
                and (t - self._frame_t) >= self.run_seconds:
            self._finish("ran", "")
        elif self.state == "loading" and self._launch_t is not None \
                and (t - self._launch_t) >= self.load_timeout:
            self._finish("crashed", "no_first_frame")

    def _finish(self, verdict: str, reason: str) -> None:
        if self.state == "done":
            return
        self.state = "done"
        self.verdict = verdict
        self.reason = reason

    @property
    def done(self) -> bool:
        return self.state == "done"

    @property
    def presented(self) -> bool:
        """True once the scene has presented at least one frame (was alive as a wallpaper). Used by
        the driver to tell a real load/crash from a user killing a healthy running window."""
        return self._frame_t is not None

    def running_seconds(self, t: float) -> float:
        """Seconds of RUNNING so far (0 before the first frame) - for the bench countdown UI."""
        if self._frame_t is None:
            return 0.0
        return max(0.0, float(t) - self._frame_t)

    def load_remaining(self, t: float) -> float:
        """Seconds until the load timeout while still LOADING (no first frame yet); -1 once the scene
        has presented, exited, or the session is done. Drives the Workshop bench load-lease countdown:
        a scene that has not come alive by load_timeout is declared crashed (no_first_frame) by poll(),
        so this counting to zero coincides with the backend's own kill - it never bounds a live scene."""
        if self.state != "loading" or self._launch_t is None:
            return -1.0
        return max(0.0, self.load_timeout - (float(t) - self._launch_t))
