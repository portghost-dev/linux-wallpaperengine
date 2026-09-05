"""Self-tests for wizard_bridge.py - the import wizard flow, driven WITHOUT a GPU.

The engine launch (`_launcher`) and clock (`_clock`) are seams: we inject a no-op launcher and a
controlled clock, then drive the observation handlers (_on_line / _on_finished / _poll) directly.
That makes the phase machine and the record events provable with no real engine. The live QProcess
path is covered by manual visual testing, not this suite. The compositor queries are stubbed, so the
bench falls back to the workshop's geometry and places nothing.
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="lwe-wizb-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


class FakeBackend:
    def __init__(self):
        self.approved = []

    def approveReview(self, wid):
        self.approved.append(str(wid))


class FakeWorkshop:
    def __init__(self, item_dir):
        self._dir = str(item_dir)
        self.trashed = []

    def _resolve_dir(self, wid):
        return self._dir

    def _spawn_geometry(self):
        return "0x0x100x100"

    def trashItem(self, wid):
        self.trashed.append(str(wid))

    def trashFiles(self, wid):
        self.trashed.append(str(wid))


def main() -> None:
    from PySide6.QtGui import QGuiApplication
    from lwe_ui import wizard_bridge as WB
    from lwe_ui.storage import paths, records
    from PySide6.QtCore import QProcess

    app = QGuiApplication.instance() or QGuiApplication(["t"])  # noqa: F841
    paths.ensure_dirs()

    # no compositor in the sandbox: the bench falls back to the workshop's geometry and places
    # nothing; no socket ever comes up, so the overlay push stays silent
    WB.placement.layout = lambda query=None: None
    WB.placement.clients = lambda: []
    WB.placement.dispatch = lambda expr: True

    NORMAL = QProcess.ExitStatus.NormalExit
    CRASH = QProcess.ExitStatus.CrashExit

    item = Path(_TMP) / "wp" / "100"
    item.mkdir(parents=True)
    (item / "project.json").write_text('{"type":"scene","title":"Clean"}', encoding="utf-8")
    (item / "scene.pkg").write_bytes(b"x")

    def new_bridge(wid="100", scan=None, encoder=None, auto_skip_comp=True):
        be = FakeBackend()
        ws = FakeWorkshop(item)
        b = WB.WizardBridge(be, ws)
        clk = {"t": 0.0}
        b._clock = lambda: clk["t"]
        b._launcher = lambda argv: None
        b._async = lambda fn: fn()
        b._scanner = scan or (lambda d: {"total": 3, "eligible": 1, "cached": 0,
                                         "todo": 1, "raw_mb": 10, "bc_mb": 3,
                                         "shim": True})
        b._encoder = encoder or (lambda d, w, progress=None, cancelled=None:
                                 {"encoded": 1, "failed": 0, "total": 1})
        b.open(wid, "Clean")
        if auto_skip_comp and b.phase() == "c1":
            b.skipCompression()
        return b, be, ws, clk

    b, be, ws, clk = new_bridge()
    assert b.phase() == "p1"
    b.importUntested("just want it")
    assert be.approved == ["100"] and b.phase() == ""
    evs = records.read("100")
    assert evs[-1]["action"] == "approved" and evs[-1]["initiator"] == "human" and evs[-1]["machine"] is None

    b, be, ws, clk = new_bridge()
    b.runWizard()
    assert b.phase() == "p2", "clean census advances to the expectation-setter"

    preset = Path(_TMP) / "wp" / "200"
    preset.mkdir(parents=True)
    (preset / "project.json").write_text('{"type":"scene","dependency":"999"}', encoding="utf-8")
    (preset / "scene.pkg").write_bytes(b"x")
    bp = WB.WizardBridge(FakeBackend(), FakeWorkshop(preset))
    dep_seen = []
    bp.depNeeded.connect(lambda w, t: dep_seen.append(w))
    bp.open("200", "Preset")
    bp.runWizard()
    assert dep_seen == ["200"] and bp.phase() == "", "missing dep routes to the modal, closes the wizard"

    b, be, ws, clk = new_bridge()
    launched = []
    b._launcher = lambda argv: launched.append(list(argv))
    b.runWizard(); b.proceedToBench(); app.processEvents()
    argv = launched[-1]
    assert "--api-socket" in argv, "the bench listens so the overlay can be pushed"
    assert argv[argv.index("--window") + 1] == "0x0x100x100", "no compositor: the workshop geometry"
    assert argv[-2:] == ["--bg", str(item)] and "--screen-root" not in argv
    env = WB.bench_env("Clean")
    assert env["LWE_PRESENTTRACE"] == "1", "the first-frame marker only prints under the present trace"
    assert env["LWE_OVERLAY_TEXT"] == "Clean" and env["LWE_SOCKET"].endswith("/lwe/wizard-bench.sock")
    assert b.benchRunRemaining() == -1, "no run window before the first frame"
    clk["t"] = 2.0; b._on_line("LWE-PRESENT viewport=100x100")
    clk["t"] = 10.0
    assert b.benchRunRemaining() == 7
    text = WB.bench_overlay_text("Clean", None, 7.2, None)
    assert text.startswith("Clean\nstats pending\n\n") and \
        text.endswith("Bench Testing In Progress. Scene ends in 8 seconds.")
    assert WB.bench_overlay_text("Clean", None, None, 12.5).endswith(
        "Waiting for the first frame, 13 seconds left.")
    assert WB.bench_overlay_text("Clean", None, -0.5, None).endswith("Scene ends in 0 seconds.")
    b.close()
    assert b.phase() == ""

    b, be, ws, clk = new_bridge()
    b._launcher = lambda argv: None
    b.runWizard(); b.proceedToBench(); app.processEvents()
    stale = WB.BenchSession()
    clk["t"] = 3.0; b._on_bench_frames(stale, 300)
    assert b.benchRunRemaining() == -1, "a sample from another bench's session never lands"
    b._on_vram_sampled(stale, 900)
    assert b._session.peak_mb < 0, "a stale VRAM sample never lands"
    b._on_bench_frames(b._session, 12)
    assert b.benchRunRemaining() == 15, "frames on the socket count as the first frame"
    clk["t"] = 17.5; b._poll()
    assert b.phase() == "p3"
    clk["t"] = 18.1; b._poll()
    assert b.phase() == "pass", "the run window is anchored at the socket's first frame"
    b.close()

    # inconclusive close, then a re-bench must be allowed
    b, be, ws, clk = new_bridge()
    b._launcher = lambda argv: None
    b.runWizard(); b.proceedToBench(); app.processEvents()
    clk["t"] = 2.0; b._on_line("LWE-PRESENT viewport=100x100")
    clk["t"] = 5.0; b._on_finished(NORMAL, 0)
    assert b.phase() == "p2", "a presented window closed early is inconclusive"
    b.proceedToBench(); app.processEvents()
    assert b.phase() == "p3", "the expectation screen's Bench button works again after an inconclusive close"
    b.close()

    # a failed start, then a re-bench
    b, be, ws, clk = new_bridge()
    b._launcher = lambda argv: None
    b.runWizard(); b.proceedToBench(); app.processEvents()
    b._on_proc_error(QProcess.ProcessError.FailedToStart)
    assert b.phase() == "p2"
    b.proceedToBench(); app.processEvents()
    assert b.phase() == "p3", "the Bench button works again after a failed start"
    b.close()

    # the socket file never outlives the bench, and the window lands in its cell
    b, be, ws, clk = new_bridge()
    b._launcher = lambda argv: None
    b.runWizard(); b.proceedToBench(); app.processEvents()
    WB.bench_sock_path().parent.mkdir(parents=True, exist_ok=True)
    WB.bench_sock_path().write_text("stale")

    class FakeProc:
        def processId(self): return 4242
        def state(self): return QProcess.ProcessState.Running
        def kill(self): pass
        def deleteLater(self): pass
        class _S:
            def disconnect(self): pass
        finished = _S()
    b._proc = FakeProc()
    at = {"v": [0, 0]}
    calls = []
    WB.placement.layout = lambda query=None: {"x": 0, "y": 0, "w": 2560, "h": 1440,
                                              "reserved": [0, 40, 0, 0], "gap": 5}
    WB.placement.clients = lambda: [{"pid": 4242, "at": at["v"], "address": "0xabc", "floating": False}]
    WB.placement.dispatch = lambda expr: calls.append(expr) or True
    b._place_tick()
    assert len(calls) == 3 and b._place_tries == 1 and not b._placed, "float, resize, move"
    at["v"] = [5, 45]
    b._place_tick()
    assert b._placed and not b._place_timer.isActive(), "a verified placement ends the corrections"
    WB.placement.layout = lambda query=None: None
    b.close()
    assert not WB.bench_sock_path().exists(), "close removes the socket file"

    class PeerA:
        def alive(self, side): return side == "A"
        def engineBusy(self): return True
    b.set_engine_peers([PeerA()])
    assert b._bench_cell() == "B", "the bench steps aside when a Developer exhibit holds the top-left cell"
    b.set_engine_peers([])
    assert b._bench_cell() == "A"

    title = "Café “Night” 東京 · x"
    wire = WB.api_client.overlay_wire_text(WB.bench_overlay_text(title, None, 7.0, None))
    assert wire.isascii() and wire.startswith('Caf? "Night" ?? - x\n') and wire.endswith("Scene ends in 7 seconds.")
    long = WB.api_client.overlay_wire_text("T" * 600 + "\nCPU: 1.0%\n\nBench Testing In Progress. Scene ends in 3 seconds.")
    assert len(long) <= 512 and long.endswith("Scene ends in 3 seconds."), "an over-long block loses its heading, not its countdown"
    assert WB.bench_overlay_text("x", None, 0.4, None).endswith("Scene ends in 1 second.")

    b, be, ws, clk = new_bridge()
    grad = []
    b.graduated.connect(lambda w: grad.append(w))
    b.runWizard(); b.proceedToBench(); app.processEvents()
    assert b.phase() == "p3"
    clk["t"] = 2.0; b._on_line("LWE-PRESENT viewport=100x100")
    clk["t"] = 16.0; b._poll()
    assert b.phase() == "p3"
    clk["t"] = 17.0; b._poll()
    assert b.phase() == "pass"
    b.approve("looks great")
    assert be.approved == ["100"] and b.phase() == "" and grad == ["100"], "graduate drops it from Workshop"
    ev = records.read("100")[-1]
    assert ev["action"] == "approved" and ev["machine"]["verdict"] == "ran"
    assert ev["machine"]["contentHash"] != "", "hash stamped at bench time"

    b, be, ws, clk = new_bridge()
    b.runWizard(); b.proceedToBench(); app.processEvents()
    clk["t"] = 10.0; b._on_line("LWE-PRESENT")
    clk["t"] = 20.0; b._poll()
    assert b.phase() == "p3", "a late-first-frame scene must not be failed by wall time"
    clk["t"] = 25.0; b._poll()
    assert b.phase() == "pass"

    b, be, ws, clk = new_bridge()
    unsub = []
    b.trashedUnsub.connect(lambda w, t: unsub.append(w))
    b.runWizard(); b.proceedToBench(); app.processEvents()
    clk["t"] = 3.0; b._on_finished(CRASH)
    assert b.phase() == "fail"
    b.deny("bench crashed it")
    assert ws.trashed == ["100"] and b.phase() == ""
    assert unsub == ["100"], "deny hands off to the unsubscribe beat (else it re-downloads)"
    ev = records.read("100")[-1]
    assert ev["action"] == "deleted" and ev["initiator"] == "wizard_recommended"
    assert ev["lineage"][-1] == "recommended_trash" and ev["machine"]["verdict"] == "crashed"

    b, be, ws, clk = new_bridge()
    b.runWizard(); b.proceedToBench(); app.processEvents()
    clk["t"] = 3.0; b._on_finished(CRASH)
    assert b.phase() == "fail"
    b.cancel("not now")
    assert ws.trashed == [] and b.phase() == ""
    ev = records.read("100")[-1]
    assert ev["action"] == "benched_no_decision" and ev["initiator"] == "human"
    assert records.is_suppressed("100", present_on_disk=True) is False

    b, be, ws, clk = new_bridge()
    b.runWizard(); b.proceedToBench(); app.processEvents()
    before = len(records.read("100"))
    clk["t"] = 5.0; b._on_line("LWE-PRESENT"); b._on_finished(NORMAL)
    assert b.phase() == "p2", "a user window-close is inconclusive (C5), not a crash"
    assert len(records.read("100")) == before, "an inconclusive close writes nothing"

    b, be, ws, clk = new_bridge()
    b.runWizard(); b.proceedToBench(); app.processEvents()
    clk["t"] = 1.0; b._on_finished(NORMAL)
    assert b.phase() == "fail", "a NormalExit before the first frame is a crash, not a user close"

    b, be, ws, clk = new_bridge()
    b.runWizard(); b.proceedToBench(); app.processEvents()
    clk["t"] = 2.0; b._on_line("LWE-PRESENT")
    b._on_finished(CRASH)
    assert b.phase() == "p2", "a running window killed by the WM is inconclusive, not failed"

    b, be, ws, clk = new_bridge()
    b.runWizard(); b.proceedToBench(); app.processEvents()
    clk["t"] = 2.0; b._on_line("LWE-PRESENT")
    b._on_line("terminate called after throwing an instance of 'std::bad_alloc'")
    assert b.phase() == "fail", "a fatal line is a real crash even after presenting"

    b, be, ws, clk = new_bridge()
    b._fixable = lambda: True
    b.runWizard(); b.proceedToBench(); app.processEvents()
    clk["t"] = 3.0; b._on_finished(CRASH)
    assert b.phase() == "fixable", "with a fix available, a crash offers the retry"
    b.applyFixesAndRetry(); app.processEvents()
    assert b.phase() == "p3"
    clk["t"] = 6.0; b._on_finished(CRASH)
    assert b.phase() == "fail", "a post-fix crash is terminal, never offers the retry again"

    b, be, ws, clk = new_bridge()
    assert b.benchLoadRemaining() == -1, "no lease before a bench launches"
    b.runWizard(); b.proceedToBench(); app.processEvents()
    assert b.phase() == "p3"
    clk["t"] = 0.0
    assert b.benchLoadRemaining() == 30, "the lease starts at LOAD_TIMEOUT while loading"
    clk["t"] = 5.5
    assert b.benchLoadRemaining() == 25, "the lease ticks down (ceil of the remaining seconds)"
    clk["t"] = 6.0; b._on_line("LWE-PRESENT")
    assert b.benchLoadRemaining() == -1, "the countdown vanishes once the scene presents"
    note_seen = []
    b.note.connect(lambda m: note_seen.append(m))
    b.killBench()
    assert b.phase() == "" and note_seen, "killBench force-ends the bench and notes it"
    b.killBench()
    assert len(note_seen) == 1, "killBench off p3 is a no-op"

    b, be, ws, clk = new_bridge(auto_skip_comp=False)
    assert b.phase() == "c1", "a scene opens on the state-aware compression card"
    assert b.compFacts()["todo"] == 1 and b.compFacts()["shim"] is True
    prog = []

    def enc(d, w, progress=None, cancelled=None):
        progress(0, 1)
        prog.append((0, 1))
        progress(1, 1)
        prog.append((1, 1))
        return {"encoded": 1, "failed": 0, "total": 1}

    b._encoder = enc
    b.startCompression()
    assert prog == [(0, 1), (1, 1)], "per-texture progress fires"
    assert b.phase() == "p1", "a finished encode advances to the bench card"

    b, be, ws, clk = new_bridge(auto_skip_comp=False)
    b.skipCompression()
    assert b.phase() == "p1", "Skip advances to the bench card"
    b, be, ws, clk = new_bridge(auto_skip_comp=False,
                                scan=lambda d: {"total": 3, "eligible": 0, "cached": 0,
                                                "todo": 0, "raw_mb": 0, "bc_mb": 0,
                                                "shim": True})
    assert b.phase() == "c1"
    b.startCompression()
    assert b.phase() == "c1", "Start with zero todo is a no-op (the card shows Continue)"
    b.skipCompression()
    assert b.phase() == "p1", "Continue rides the skip transition"

    b, be, ws, clk = new_bridge(auto_skip_comp=False)

    def enc_cancelled(d, w, progress=None, cancelled=None):
        b.close()
        assert canceled(), "close must trip the cancel flag the encoder polls"
        return {"encoded": 0, "failed": 0, "total": 1}

    b._encoder = enc_cancelled
    b.startCompression()
    assert b.phase() == "", "a canceled encode stays closed - no ghost advance"

    vitem = Path(_TMP) / "wp" / "300"
    vitem.mkdir(parents=True)
    (vitem / "project.json").write_text('{"type":"video","title":"Vid"}', encoding="utf-8")
    bv = WB.WizardBridge(FakeBackend(), FakeWorkshop(vitem))
    bv._async = lambda fn: fn()
    bv._scanner = lambda d: (_ for _ in ()).throw(AssertionError("video must not scan"))
    bv.open("300", "Vid")
    assert bv.phase() == "p1", "video/web bypass the compression card"

    print("OK test_wizard_bridge - P1/import-untested, census gate + dep short-circuit, ran->approve, "
          "first-frame clock, crash->deny (wizard_recommended), cancel->benched_no_decision, "
          "user-close inconclusive, instant NormalExit crash->fail, bounded fix-retry, "
          "load-lease countdown + killBench, compression c0/c1/c2 offer/skip/progress/"
          "cancel/bypass")
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
