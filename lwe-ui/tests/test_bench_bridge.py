"""State-machine self-test for BenchBridge (bench_bridge.py).

Everything is sandboxed + stubbed - NOTHING here spawns the real engine or signals the live
watcher:
  * HOME/XDG point at a tempfile tree (config/state/wp all isolated);
  * bench_courier.available / standdown / resume are monkeypatched to record calls;
  * the QProcess is replaced by an injected FakeProcess factory (records start/terminate/kill);
  * commit.commit / commit.reject are monkeypatched so the disk-publish backend never runs.

Each assertion targets behavior that is ABSENT before the bridge exists (seeding by source,
the benchAvailable gate, the pause-abort, reap-before-commit, no-double-resume), so the test
fails on pre-fix/absent code rather than being tautological.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 tests/test_bench_bridge.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


class FakeProcess:
    """A minimal non-detached-QProcess stand-in for the bench's injected factory.

    Records lifecycle so tests can assert the bridge reaps (terminate/kill) before commit/resume.
    Has NO `finished`/`errorOccurred` signals and no `setChildProcessModifier`, so the bridge's
    connect() calls are guarded / skipped - a real engine is never spawned.
    """

    instances: list["FakeProcess"] = []

    def __init__(self) -> None:
        self.started = False
        self.terminated = False
        self.killed = False
        self.program = ""
        self.arguments: list[str] = []
        FakeProcess.instances.append(self)

    class _Sig:
        def connect(self, *_a, **_k):
            return None

        def disconnect(self, *_a, **_k):
            return None

    finished = _Sig()
    errorOccurred = _Sig()

    def setProcessEnvironment(self, _env) -> None:
        pass

    def setProgram(self, p) -> None:
        self.program = p

    def setArguments(self, a) -> None:
        self.arguments = list(a)

    def start(self) -> None:
        self.started = True

    def state(self):
        # 0 == QProcess.ProcessState.NotRunning ; 2 == Running
        return 2 if (self.started and not self.terminated and not self.killed) else 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def waitForFinished(self, _ms=0) -> bool:
        return True


def _fail(msg: str) -> None:
    raise AssertionError(msg)


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-benchbridge-")
    orig = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
    try:
        os.environ["HOME"] = home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "config")
        os.environ["XDG_STATE_HOME"] = os.path.join(home, "state")
        os.environ["XDG_DATA_HOME"] = os.path.join(home, "data")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

        from PySide6.QtCore import QCoreApplication
        from lwe_ui import bench_bridge, bench_courier, commit as commit_mod
        from lwe_ui.storage import paths, wp

        app = QCoreApplication.instance() or QCoreApplication(["t"])
        paths.ensure_dirs()

        calls = {"pause": 0, "resume": 0, "is_v2": 0}
        v2_flag = {"val": True}
        pause_ret = {"val": True}
        bench_courier.available = lambda: (calls.__setitem__("is_v2", calls["is_v2"] + 1) or v2_flag["val"])
        # the spawn-path engines-clear wait must not poll the LIVE engine in tests
        bench_courier.wait_clear = lambda *a, **k: True
        bench_courier.standdown = lambda *a, **k: (calls.__setitem__("pause", calls["pause"] + 1) or pause_ret["val"])
        bench_courier.resume = lambda *a, **k: (calls.__setitem__("resume", calls["resume"] + 1) or True)

        workshop = Path(home) / "data" / "Steam/steamapps/workshop/content/431960"
        wallpapers = Path(home) / "data" / "wallpapers"
        pend_wid = "111"
        good_wid = "222"
        (workshop / pend_wid).mkdir(parents=True, exist_ok=True)
        json.dump({"type": "scene", "file": "scene.json", "title": "Pending One"},
                  open(workshop / pend_wid / "project.json", "w"))
        (wallpapers / good_wid).mkdir(parents=True, exist_ok=True)
        json.dump({"type": "scene", "file": "scene.json", "title": "Good Two"},
                  open(wallpapers / good_wid / "project.json", "w"))
        wp.save(good_wid, {"BG": str(wallpapers / good_wid), "TYPE": "scene", "SCALING": "fit"})

        def _settings_stub(self):
            return {
                "engine_bin": "/nonexistent/engine",
                "assets_dir": "/nonexistent/assets",
                "workshop_dir": str(workshop),
                "wallpapers_dir": str(wallpapers),
                "pause_on_fullscreen": False,
            }
        bench_bridge.BenchBridge._settings = _settings_stub

        def make_bridge():
            b = bench_bridge.BenchBridge(process_factory=FakeProcess)
            b._resolve_outputs = lambda: ["TEST-1"]
            return b

        FakeProcess.instances.clear()
        b = make_bridge()
        b.open(pend_wid, "pending")
        if not wp.exists(pend_wid):
            _fail("open(pending) must BUILD wp/<id>.conf (L-19: no pending store)")
        if b.property("source") != "pending" or b.property("commitMode") != "first":
            _fail("pending open must set source=pending, commitMode=first")
        if wp.load(pend_wid).get("BG") != str(workshop / pend_wid):
            _fail("pending conf BG must be the workshop path (renders from Steam tree)")
        if b.property("title") != "Pending One":
            _fail("title must resolve from project.json")
        print("OK T1 open(pending) builds the conf by source + resolves title")

        marker_wid = "333"
        (workshop / marker_wid).mkdir(parents=True, exist_ok=True)
        json.dump({"type": "scene", "file": "scene.json", "title": "Marker"},
                  open(workshop / marker_wid / "project.json", "w"))
        wp.save(marker_wid, {"BG": str(workshop / marker_wid), "TYPE": "scene", "SCALING": "stretch"})
        b2 = make_bridge()
        b2.open(marker_wid, "pending")
        if b2.property("scaling") != "stretch":
            _fail("open must honor an existing conf and load the in-progress session (SCALING=stretch)")
        print("OK T2 open honors an existing conf (no clobber)")

        bg = make_bridge()
        bg.open(good_wid, "good")
        if bg.property("source") != "good" or bg.property("commitMode") != "recommit":
            _fail("good open must set source=good, commitMode=recommit")
        if bg.property("scaling") != "fit":
            _fail("good re-bench must read the live wp/<id>.conf (SCALING=fit)")
        print("OK T3 open(good) re-bench seeds from the live conf")

        # ============================== T3b: an existing conf ALSO protects the GOOD re-bench
        # seed_rebench is NOT sticky (it save()s a fresh copy of the live conf), so the exists-guard
        # is what stops re-opening a good item mid-tune from clobbering in-progress edits. Pre-save a
        # conf whose SCALING differs from the schema default; open(good) must LOAD it verbatim.
        guard_wid = "444"
        (wallpapers / guard_wid).mkdir(parents=True, exist_ok=True)
        json.dump({"type": "scene", "file": "scene.json", "title": "Guard"},
                  open(wallpapers / guard_wid / "project.json", "w"))
        wp.save(guard_wid, {"BG": str(wallpapers / guard_wid), "TYPE": "scene", "SCALING": "fill"})
        wp.save(guard_wid, {"BG": str(wallpapers / guard_wid), "TYPE": "scene", "SCALING": "stretch"})
        bgg = make_bridge()
        bgg.open(guard_wid, "good")
        if bgg.property("scaling") != "stretch":
            _fail("open(good) must LOAD the live conf in place "
                  "(SCALING=stretch), NOT re-seed from the live conf (SCALING=fill)")
        print("OK T3b open(good) loads the live conf in place (no clobber)")

        FakeProcess.instances.clear()
        calls["pause"] = 0
        v2_flag["val"] = False
        bd = make_bridge()
        bd.open(pend_wid, "pending")
        if bd.property("benchAvailable") is not False:
            _fail("benchAvailable must reflect courier available()==False")
        bd.startTest()
        if bd.property("isTesting"):
            _fail("startTest must REFUSE to launch when the courier is unavailable (isTesting stays False)")
        if calls["pause"] != 0:
            _fail("startTest must not send a standdown when the courier is unavailable")
        if FakeProcess.instances:
            _fail("startTest must not spawn an engine when not v2")
        print("OK T4 startTest is gated on benchAvailable (no pause, no spawn)")

        FakeProcess.instances.clear()
        v2_flag["val"] = True
        pause_ret["val"] = False
        calls["pause"] = 0
        ba = make_bridge()
        ba.open(pend_wid, "pending")
        ba.startTest()
        if ba.property("isTesting"):
            _fail("startTest must abort (not testing) when bench_pause returns False")
        if calls["pause"] != 1:
            _fail("startTest must attempt exactly one bench_pause handshake")
        if FakeProcess.instances:
            _fail("startTest must NOT spawn an engine when the pause is denied")
        print("OK T5 startTest aborts the launch on a denied pause (PAUSE-DENIED)")

        FakeProcess.instances.clear()
        pause_ret["val"] = True
        calls["pause"] = 0
        bs = make_bridge()
        bs.open(pend_wid, "pending")
        bs.startTest()
        if not bs.property("isTesting"):
            _fail("startTest must set isTesting after a granted pause + spawn")
        if len(FakeProcess.instances) != 1 or not FakeProcess.instances[0].started:
            _fail("startTest must spawn exactly one (started) engine via the factory")
        if FakeProcess.instances[0].program != "/nonexistent/engine":
            _fail("the spawned process program must be the resolved engine_bin (argv[0])")
        print("OK T6 startTest spawns the non-detached engine on a granted pause")

        calls["resume"] = 0
        proc = FakeProcess.instances[0]
        bs.stopTest()
        if not proc.terminated:
            _fail("stopTest must reap (terminate) the test engine")
        if bs.property("isTesting"):
            _fail("stopTest must clear isTesting")
        if calls["resume"] != 1:
            _fail("stopTest must resume the watcher exactly once")
        print("OK T7 stopTest reaps the engine + resumes the watcher")

        FakeProcess.instances.clear()
        br = make_bridge()
        br.open(pend_wid, "pending")
        br.startTest()
        first = FakeProcess.instances[0]
        br.startTest()
        if not first.terminated:
            _fail("relaunch must reap the previous engine before spawning the next")
        if len(FakeProcess.instances) != 2 or not FakeProcess.instances[1].started:
            _fail("relaunch must spawn a fresh engine (single active test)")
        print("OK T8 relaunch reaps the prior engine before spawning")

        FakeProcess.instances.clear()
        commit_seen = {"commit": 0, "reject": 0, "src": None}
        commit_mod.commit = lambda wid, *, source, title, workshop_dir, wallpapers_dir: (
            commit_seen.__setitem__("commit", commit_seen["commit"] + 1)
            or commit_seen.__setitem__("src", source)
            or {"ok": True, "wid": wid})
        commit_mod.reject = lambda wid, *, title: (
            commit_seen.__setitem__("reject", commit_seen["reject"] + 1) or {"ok": True, "wid": wid})

        bc = make_bridge()
        bc.open(pend_wid, "pending")
        bc.startTest()
        proc_c = FakeProcess.instances[-1]
        calls["resume"] = 0
        done = {"ok": None, "reason": None}
        bc.committed.connect(lambda ok, reason: done.update(ok=ok, reason=reason))
        bc.commit()
        if not proc_c.terminated:
            _fail("commit must reap the test engine BEFORE the backend resumes/rebuilds rotation")
        import time
        deadline = time.time() + 5
        while done["ok"] is None and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        if done["ok"] is not True:
            _fail("commit must emit committed(True, ...) on a successful backend report")
        if commit_seen["commit"] != 1:
            _fail("commit must call commit.commit exactly once")
        if commit_seen["src"] != "pending":
            _fail("commit must pass the session source to the backend")
        if calls["resume"] != 0:
            _fail("commit must NOT resume the watcher itself (commit.py resumes internally - no double-resume)")
        if bc.property("isTesting"):
            _fail("commit must leave isTesting False")
        print("OK T9 commit reaps + calls backend once + does NOT double-resume")

        FakeProcess.instances.clear()
        bj = make_bridge()
        bj.open(good_wid, "good")
        bj.startTest()
        calls["resume"] = 0
        done2 = {"ok": None}
        bj.committed.connect(lambda ok, reason: done2.update(ok=ok))
        bj.reject()
        deadline = time.time() + 5
        while done2["ok"] is None and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        if done2["ok"] is not True:
            _fail("reject must emit committed(True, ...) on success")
        if commit_seen["reject"] != 1:
            _fail("reject must call commit.reject exactly once")
        if calls["resume"] != 0:
            _fail("reject must NOT resume itself (backend resumes)")
        print("OK T10 reject runs off-thread once + no double-resume")

        FakeProcess.instances.clear()
        bcl = make_bridge()
        bcl.open(pend_wid, "pending")
        bcl.startTest()
        calls["resume"] = 0
        proc_cl = FakeProcess.instances[-1]
        bcl.close()
        if not proc_cl.terminated:
            _fail("close must reap the test engine")
        if calls["resume"] != 1:
            _fail("close must resume the watcher when it was mid-test (crash-safe exit)")
        print("OK T11 close reaps + resumes when mid-test")

        # ===== T12: commit-FAILED keeps the panel state retryable AND resumes the orphaned pause
        # (findings #6/#7). _run_commit_reject reaps the engine + stops the lease but does NOT
        # resume (happy-path assumes commit.py resumes internally). On ok:False the backend never
        # resumed, so the bridge itself MUST resume - else rotation is frozen until the ~65s lease
        # self-heal. Pre-fix, _on_commit_done's failure branch issued no resume (resume==0).
        FakeProcess.instances.clear()
        commit_mod.commit = lambda wid, *, source, title, workshop_dir, wallpapers_dir: {
            "ok": False, "reason": "publish blew up"}
        bf = make_bridge()
        bf.open(pend_wid, "pending")
        bf.startTest()
        calls["resume"] = 0
        donef = {"ok": None, "reason": None}
        bf.committed.connect(lambda ok, reason: donef.update(ok=ok, reason=reason))
        bf.commit()
        deadline = time.time() + 5
        while donef["ok"] is None and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        if donef["ok"] is not False:
            _fail("a failed commit must emit committed(False, ...)")
        if bf.property("testState") != "commit-failed":
            _fail("a failed commit must set testState=commit-failed (keep panel open, retryable)")
        if bf.property("committing"):
            _fail("a failed commit must clear committing")
        if bf.property("lastError") == "":
            _fail("a failed commit must surface report['reason'] in lastError")
        if not wp.exists(pend_wid):
            _fail("a failed commit must leave the conf intact (retryable)")
        if calls["resume"] != 1:
            _fail("a failed commit must RESUME the orphaned watcher pause exactly once "
                  "(the backend does not resume on ok:False - finding #6)")
        print("OK T12 commit-FAILED stays retryable + resumes the orphaned pause")

        # ===== T13: a REAL QProcess crash-exit must NOT segfault the app (finding #1). The self-
        # exit handlers run inside the QProcess's own signal; dropping the sole ref there destroys
        # the QProcess + its socket notifier mid-dispatch -> SIGSEGV (exit 139). Pre-fix this whole
        # test process would crash; post-fix the bridge survives, clears isTesting, resumes once.
        # (Uses the DEFAULT real-QProcess factory; a harmless self-SIGSEGV shell, never the engine.)
        FakeProcess.instances.clear()
        calls["resume"] = 0
        bx = bench_bridge.BenchBridge()
        bx._resolve_outputs = lambda: []
        bx._bench_available = True
        bx._wid = "crashy"
        bx._build_argv = lambda: ({}, ["/bin/sh", "-c", "kill -SEGV $$"])
        bx.startTest()
        if not bx.property("isTesting"):
            _fail("real-proc startTest must mark isTesting before the engine crashes")
        deadline = time.time() + 5
        while bx.property("isTesting") and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        if bx.property("isTesting"):
            _fail("engine crash-exit must clear isTesting via the self-exit handler (no segfault)")
        if calls["resume"] != 1:
            _fail("engine crash-exit must resume the watcher exactly once")
        # let the deferred deleteLater/drain run
        for _ in range(5):
            app.processEvents()
        print("OK T13 real engine crash-exit survives (no segfault) + resumes once")

        # ===== T14: a REAL QProcess FailedToStart (missing engine binary) must NOT segfault either
        # (finding #1, errorOccurred path). Same self-exit-handler use-after-free class.
        calls["resume"] = 0
        by = bench_bridge.BenchBridge()
        by._resolve_outputs = lambda: []
        by._bench_available = True
        by._wid = "missing"
        by._build_argv = lambda: ({}, ["/nonexistent/lwe-bench-engine-xyz", "--go"])
        by.startTest()
        deadline = time.time() + 5
        while by.property("isTesting") and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        if by.property("isTesting"):
            _fail("FailedToStart must clear isTesting via _on_proc_error (no segfault)")
        if by.property("testState") != "test-error":
            _fail("FailedToStart must set testState=test-error")
        if calls["resume"] != 1:
            _fail("FailedToStart must resume the watcher exactly once")
        for _ in range(5):
            app.processEvents()
        print("OK T14 real FailedToStart survives (no segfault) + resumes once")

        print("\nALL test_bench_bridge state-machine checks passed (15/15)")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
