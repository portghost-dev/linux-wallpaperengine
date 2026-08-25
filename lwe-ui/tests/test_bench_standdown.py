"""Standdown/resume self-test for BenchBridge (bench_bridge.py).

A release is STATE on the engine side - it holds until acquire - so the contract is
strict pairing, not renewal:
  * startTest sends exactly ONE standdown (the handshake) and refuses on failure;
  * stopTest sends exactly ONE resume;
  * close mid-test resumes too (crash-safe exit path);
  * no courier call may leak after an exit path ran.

bench_courier + the QProcess factory are stubbed; nothing spawns an engine or touches a socket.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 tests/test_bench_standdown.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


class FakeProcess:
    class _Sig:
        def connect(self, *_a, **_k): return None
        def disconnect(self, *_a, **_k): return None
    finished = _Sig()
    errorOccurred = _Sig()

    def __init__(self) -> None:
        self.started = False
        self.terminated = False
        self.killed = False

    def setProcessEnvironment(self, _e): pass
    def setProgram(self, _p): pass
    def setArguments(self, _a): pass
    def start(self): self.started = True
    def state(self): return 2 if (self.started and not self.terminated and not self.killed) else 0
    def terminate(self): self.terminated = True
    def kill(self): self.killed = True
    def waitForFinished(self, _ms=0): return True


def _fail(msg: str) -> None:
    raise AssertionError(msg)


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-benchstanddown-")
    orig = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
    try:
        os.environ["HOME"] = home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "config")
        os.environ["XDG_STATE_HOME"] = os.path.join(home, "state")
        os.environ["XDG_DATA_HOME"] = os.path.join(home, "data")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

        from PySide6.QtCore import QCoreApplication
        from lwe_ui import bench_bridge, bench_courier
        from lwe_ui.storage import paths

        app = QCoreApplication.instance() or QCoreApplication(["t"])
        paths.ensure_dirs()

        calls = {"standdown": 0, "resume": 0}
        bench_courier.available = lambda: True
        bench_courier.wait_clear = lambda *a, **k: True
        bench_courier.standdown = lambda *a, **k: (calls.__setitem__("standdown", calls["standdown"] + 1) or True)
        bench_courier.resume = lambda *a, **k: (calls.__setitem__("resume", calls["resume"] + 1) or True)

        workshop = Path(home) / "data" / "workshop"
        wallpapers = Path(home) / "data" / "wallpapers"
        wid = "555"
        (workshop / wid).mkdir(parents=True, exist_ok=True)
        json.dump({"type": "scene", "file": "scene.json", "title": "Lease"},
                  open(workshop / wid / "project.json", "w"))

        def _settings_stub(self):
            return {"engine_bin": "/x/engine", "assets_dir": "/x/assets",
                    "workshop_dir": str(workshop), "wallpapers_dir": str(wallpapers),
                    "pause_on_fullscreen": False}
        bench_bridge.BenchBridge._settings = _settings_stub

        b = bench_bridge.BenchBridge(process_factory=FakeProcess)
        b._resolve_outputs = lambda: ["TEST-1"]  # a resolvable output; empty now refuses the Test
        b.open(wid, "pending")

        b.startTest()
        if calls["standdown"] != 1:
            _fail(f"startTest must send exactly ONE standdown (saw {calls['standdown']})")
        if not b.engineBusy():
            _fail("startTest must leave the bridge testing")

        # let the event loop settle; a release is state, so NO further courier traffic may occur
        deadline = time.time() + 0.3
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.005)
        if calls["standdown"] != 1:
            _fail("no renewal traffic may occur while testing (release is state, not a lease)")
        print("OK startTest sends one standdown and nothing renews")

        b.stopTest()
        if calls["resume"] != 1:
            _fail(f"stopTest must send exactly ONE resume (saw {calls['resume']})")
        print("OK stopTest resumes once")

        b.startTest()
        if calls["standdown"] != 2:
            _fail("restart must stand the daemon down again")
        b.close()
        if calls["resume"] != 2:
            _fail("close mid-test must resume (crash-safe exit path)")
        print("OK close resumes too (every exit path)")

        # a refused standdown blocks the launch
        bench_courier.standdown = lambda *a, **k: False
        b.open(wid, "pending")
        b.startTest()
        if b.engineBusy():
            _fail("a refused standdown must abort the launch")
        print("OK refused standdown aborts the launch")

        print("\nALL test_bench_standdown checks passed")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
