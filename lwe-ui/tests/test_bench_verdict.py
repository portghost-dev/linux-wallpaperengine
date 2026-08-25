"""Self-tests for storage/bench_verdict.py - the wizard bench state machine.

Pure: no filesystem, no Qt, no GPU. Each block states the PREDICTION it checks. The whole
point of the machine is that it is fed synthetic observation streams, so crash-vs-alive and the
first-frame health clock are provable here without ever launching an engine.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def main() -> None:
    from lwe_ui.storage.bench_verdict import BenchSession, is_first_frame_line, is_fatal_line

    assert is_first_frame_line("LWE-PRESENT viewport=2560x1440 wpRes=3840x2160")
    assert not is_first_frame_line("Resolving require module: LightingV1")
    assert is_fatal_line("terminate called after throwing an instance of 'std::bad_alloc'")
    assert is_fatal_line("Segmentation fault (core dumped)")
    assert not is_fatal_line("LWE-PRESENT viewport=2560x1440")
    assert not is_fatal_line("render pass aborted, retrying"), "F-adv1: 'aborted' must not false-crash"

    s = BenchSession(run_seconds=15.0)
    s.on_launch(0.0)
    s.on_first_frame(2.0)
    s.on_vram(2041); s.on_vram(1800); s.on_vram(1900)
    s.poll(16.0)
    assert not s.done, "must not be done at 14s of running"
    s.poll(17.0)
    assert s.done and s.verdict == "ran" and s.reason == "" and s.peak_mb == 2041

    # --- FIRST-FRAME CLOCK ANCHORING (the anti-false-fail guarantee) -------------------------
    # PREDICT: a heavy scene whose first frame is late (t=10, compiled past a launch-anchored 15s)
    # must NOT be judged until 15s of RUNNING; at launch+20 it is still running, ran only at frame+15.
    s = BenchSession(run_seconds=15.0)
    s.on_launch(0.0)
    s.on_first_frame(10.0)
    s.poll(20.0)
    assert not s.done, "a launch-anchored clock would have wrongly failed this heavy scene"
    s.poll(25.0)
    assert s.done and s.verdict == "ran", "ran only after 15s of RUNNING, not 15s of wall time"

    s = BenchSession()
    s.on_launch(0.0)
    s.on_exit(3.0)
    assert s.done and s.verdict == "crashed" and s.reason == "exit"

    s = BenchSession()
    s.on_launch(0.0); s.on_first_frame(2.0); s.on_exit(9.0)
    assert s.done and s.verdict == "crashed" and s.reason == "exit"

    s = BenchSession()
    s.on_launch(0.0); s.on_first_frame(2.0); s.on_fatal_log()
    assert s.done and s.verdict == "crashed" and s.reason == "fatal_log"

    s = BenchSession(load_timeout=30.0)
    s.on_launch(0.0)
    s.poll(29.0); assert not s.done, "still loading at 29s"
    s.poll(30.0); assert s.done and s.verdict == "crashed" and s.reason == "no_first_frame"

    s = BenchSession()
    s.on_launch(0.0); s.on_first_frame(2.0); s.on_user_close()
    assert s.done and s.verdict is None and s.inconclusive and s.reason == "user_close"

    s = BenchSession(run_seconds=15.0)
    s.on_launch(0.0); s.on_first_frame(1.0); s.poll(16.0)
    assert s.verdict == "ran"
    s.on_exit(16.1)
    assert s.verdict == "ran", "our own terminate after ran must never record a crash"

    s = BenchSession()
    s.on_vram(None); s.on_vram("x"); s.on_vram(500); s.on_vram(300)
    assert s.peak_mb == 500

    s = BenchSession()
    s.on_launch(0.0)
    assert s.running_seconds(5.0) == 0.0
    s.on_first_frame(2.0)
    assert s.running_seconds(7.0) == 5.0

    s = BenchSession(load_timeout=30.0)
    assert s.load_remaining(0.0) == -1.0, "no lease before launch"
    s.on_launch(0.0)
    assert s.load_remaining(0.0) == 30.0 and s.load_remaining(5.0) == 25.0
    assert s.load_remaining(40.0) == 0.0, "floors at 0, never negative"
    s.on_first_frame(6.0)
    assert s.load_remaining(7.0) == -1.0, "the countdown vanishes once the scene presents"
    s2 = BenchSession(load_timeout=30.0)
    s2.on_launch(0.0); s2.poll(30.0)
    assert s2.load_remaining(31.0) == -1.0, "no lease once the session is done"

    print("OK test_bench_verdict - crash-vs-alive, first-frame clock anchoring, load timeout, "
          "load_remaining lease, user-close inconclusive, ran-not-overwritten, parsers all hold")


if __name__ == "__main__":
    main()
