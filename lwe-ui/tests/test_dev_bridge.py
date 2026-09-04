"""DevBridge: two exhibit slots, launch composition, env partition, residue, isolator.

SAFETY: every engine here is a shell script under the sandbox HOME; the compositor
queries are stubbed so no window is placed and no daemon is touched.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 tests/test_dev_bridge.py
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lwe-dev-test-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
for _k in ("LWE_NOBLOOM", "LWE_PRESENTTRACE", "LWE_TRAILMODE"):
    os.environ.pop(_k, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QProcess  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from lwe_ui import dev as devmod  # noqa: E402
from lwe_ui.storage import paths, settings  # noqa: E402

FAKE_ENGINE = """#!/bin/sh
if [ "$1" = "--help" ]; then echo "usage: --api-socket"; exit 0; fi
echo "LWE-PRESENT viewport=1x1 overlay=$LWE_OVERLAY_TEXT sock=$LWE_SOCKET bloom=${LWE_NOBLOOM:-unset}"
echo "Could not parse puppet x: not an MDLV container" >&2
if [ -n "$FAKE_SEGV" ]; then kill -SEGV $$; fi
if [ -n "$FAKE_SLEEP" ]; then sleep 30; fi
exit ${FAKE_EXIT:-0}
"""

LEGACY_ENGINE = """#!/bin/sh
if [ "$1" = "--help" ]; then echo "usage: --window --bg"; exit 0; fi
exit 0
"""


def _write_exe(path: str, body: str) -> str:
    with open(path, "w") as fh:
        fh.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


def _scene(root: str, wid: str, title: str, objects: list) -> None:
    d = os.path.join(root, wid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "project.json"), "w") as fh:
        json.dump({"title": title, "type": "scene", "file": "scene.json"}, fh)
    with open(os.path.join(d, "scene.json"), "w") as fh:
        json.dump({"objects": objects}, fh)


def _wait_finished(app, d, side, ms=4000) -> None:
    for _ in range(ms // 20):
        app.processEvents()
        QTest.qWait(20)
        if not d.alive(side):
            return
    raise AssertionError(f"slot {side} never finished")


def test_tables(d) -> None:
    keys = [t["key"] for t in devmod.FEATURE_TOGGLES]
    assert len(keys) == len(set(keys)), "toggle keys must be unique"
    for t in devmod.FEATURE_TOGGLES:
        assert (t["on"] is None) != (t["off"] is None) or (t["on"] and t["off"]), \
            f"{t['env']}: a switch needs a value on at least one side"
        assert t["tip"] and not t["tip"].endswith("\n")
    names = [i["name"] for i in d.instruments()]
    assert len(names) == len(set(names)), "instrument display names must be unique"
    assert "presenttrace" in names, "LWE_PRESENTTRACE is mandatory for the instrument reader"
    live = {i["env"] for i in d.instruments() if i["live"]}
    assert live == {"LWE_PARTSTATS", "LWE_TWINKLEPROBE", "LWE_ROPETRAILPROBE"}
    for i in d.instruments():
        assert i["tip"].endswith("Applies live" if i["live"] else "Applies on relaunch")
    for t in d.featureToggles():
        assert t["tip"].endswith("Applies on relaunch")
    assert d.trailModes() == ["Fluid", "Exact"]
    assert len(d.rawEnvReference()) >= 15


def test_compose(d, fake_engine, wp_root) -> None:
    d.setScene("A", "111")
    d.setLabel("A", "A · realsync off")
    d.setToggle("A", "bloom", False)
    d.setToggle("A", "realsync", True)
    d.setInstrument("A", "LWE_PRESENTTRACE", True)
    d.setTrailMode("A", "Exact")
    d.setRenderDebug("A", "pass-log", True)
    assert d.setEnvText("A", "LWE_KILLLIGHT=5\nbad key=1\n") == 1, "one rejected line"
    assert d.setPropText("A", "rain=0.5") == 0

    argv = d.compose_argv("A")
    assert argv[0] == fake_engine
    assert argv[argv.index("--window") + 1] == "0x0x1272x692", "the slot's quadrant size"
    assert "--screen-root" not in argv, "exhibits are windows, never the outputs"
    assert d._quadrant("A") == (5, 45, 1272, 692) and d._quadrant("B") == (1282, 45, 1272, 692), \
        "top-left and top-right of the usable area, the bar's reserved strip and the gaps kept clear"
    assert "--api-socket" in argv
    assert argv[argv.index("--render-debug") + 1] == "pass-log"
    assert argv[argv.index("--set-property") + 1] == "rain=0.5"
    assert argv[-2:] == ["--bg", os.path.join(wp_root, "111")]

    win = d.compose_argv("A", "0x0x100x100")
    assert win[win.index("--window") + 1] == "0x0x100x100", "an explicit geometry wins"

    env, unset = d.compose_env("A")
    assert env["LWE_NOBLOOM"] == "1", "bloom off assigns the kill switch"
    assert env["LWE_MPV_REALSYNC"] == "1", "realsync on assigns its switch"
    assert env["LWE_TRAILMODE"] == "exact"
    assert env["LWE_PRESENTTRACE"] == "1"
    assert env["LWE_OVERLAY_TEXT"] == "A · realsync off"
    assert env["LWE_SOCKET"].endswith("/lwe/exhibit-a.sock")
    assert env["LWE_WINTITLE"] == "lwe-exhibit-a"
    assert env["LWE_KILLLIGHT"] == "5", "raw lines apply last"
    assert "LWE_NOPREWARM" in unset and "LWE_PARTSTATS" in unset, "every off switch is an explicit unset"
    assert not (set(env) & set(unset)), "no key may be both assigned and unset"

    assert d.setEnvText("A", "LWE_SOCKET=/tmp/x\nLWE_KILLLIGHT=5") == 1, "panel-owned keys are rejected"
    assert d.compose_env("A")[0]["LWE_SOCKET"].endswith("exhibit-a.sock")
    d.setScene("A", "../etc")
    assert d.slotState("A")["scene"] == "111", "an unsafe wid never lands in a slot"
    d.setScene("A", "111")
    d.setEnvText("A", "LWE_KILLLIGHT=5\nLWE_NOPREWARM=1")
    env, unset = d.compose_env("A")
    assert env["LWE_NOPREWARM"] == "1" and "LWE_NOPREWARM" not in unset, \
        "an explicit raw env line wins over the toggle's unset"

    preview = d.launchPreview("A")
    assert preview.startswith("env -u ") and "LWE_NOBLOOM=1" in preview and fake_engine in preview

    envb, _ = d.compose_env("B")
    assert "LWE_NOBLOOM" not in envb and envb["LWE_OVERLAY_TEXT"] == "B", "sides are independent"

    d.setScene("B", "probe:cal1")
    assert d.compose_argv("B", "0x0x1x1")[-1] == str(paths.data_dir() / "probes" / "cal1")
    assert d.slotState("B")["sceneTitle"] == "cal1"


def test_empty_slots_take_the_now_playing_scene(d) -> None:
    from lwe_ui import api_client
    real_avail, real_status = api_client.available, api_client.status
    api_client.available = lambda sock=None: True
    api_client.status = lambda sock=None: {"current": {"id": "222"}}
    try:
        d.setScene("B", "")
        assert d.slotState("B")["scene"] == ""
        d.setFollowingDaemon(False)
        assert d._seed_scenes() is True and d.slotState("B")["scene"] == "222"
        assert d._seed_scenes() is False, "a configured slot is never overwritten"
        d.setScene("B", "")
        api_client.status = lambda sock=None: {"current": {"id": "nope"}}
        assert d._seed_scenes() is False and d.slotState("B")["scene"] == "", \
            "a wid outside the library never seeds a slot"
    finally:
        api_client.available, api_client.status = real_avail, real_status
        d.setScene("B", "probe:cal1")


def test_choices(d) -> None:
    scenes = d.sceneChoices()
    assert [s["wid"] for s in scenes if s["section"] == "library"] == ["111", "222"]
    assert scenes[0]["title"] == "Dead Space"
    assert [s["title"] for s in scenes if s["section"] == "probes"] == ["cal1"]
    bins = d.binaryChoices()
    assert bins[0] == {"label": "Same as daemon", "value": ""}
    assert any(b["label"] == "alt-engine" for b in bins), "the dev-binaries dir is discovered"


def test_overlay(d) -> None:
    sent: list = []
    real_request = devmod.api_client.request

    def fake_request(cmd, args=None, wait_done=True, sock=None):
        sent.append((cmd, dict(args or {}), str(sock)))
        return {"id": 1, "ok": True, "status": "done", "result": {}}

    devmod.api_client.request = fake_request
    try:
        st = d.slotState("A")
        assert st["overlayStats"] is False and st["overlayCorner"] == "top-left"
        assert st["overlayCornerLabel"] == "Top left"
        assert [c["value"] for c in d.overlayCorners()] == \
            ["top-left", "top-right", "bottom-left", "bottom-right"]

        d.setOverlayCorner("A", "middle")
        assert d.slotState("A")["overlayCorner"] == "top-left", "unknown corners are refused"
        d.setOverlayCorner("A", "bottom-right")
        d.setOverlayStats("A", True)
        assert not sent, "a slot with no exhibit pushes nothing"

        s = d.slots["A"]
        s.alive = lambda: True
        s.live_control = lambda: True
        d.setOverlayCorner("A", "top-right")
        assert sent[-1][0] == "set-overlay" and sent[-1][1] == {"corner": "top-right"}, \
            "a corner change rides alone"
        assert sent[-1][2].endswith("/lwe/exhibit-a.sock")
        d.setOverlayStats("A", False)
        assert sent[-1][1] == {"text": s.label, "corner": "top-right"}, \
            "stats off restores the label as the text"
        d.setLabel("A", "A · live label")
        assert sent[-1][1]["text"] == "A · live label" and not s.relaunching, \
            "a label edit on a live exhibit rides the socket instead of a relaunch"
        d.setOverlayStats("A", True)
        assert sent[-1][1]["text"] == "A · live label\nstats pending", \
            "stats on shows the pending text until the first sample lands"
        del s.live_control
        del s.alive

        text = devmod.DevBridge.overlay_text("A", {"fps": 59.94, "cpu": 3.14, "rss": 412,
                                                   "swap": 40, "vram": 1210, "gpu": 41})
        assert text.split("\n") == ["A", "CPU: 3.1%", "GPU: 41.0%", "RAM: 412 MB + 40 MB swap",
                                    "VRAM: 1210 MB", "FPS: 59.9"]
        text = devmod.DevBridge.overlay_text("A", {"fps": None, "cpu": None, "rss": -1,
                                                   "swap": -1, "vram": -1, "gpu": -1})
        assert text.split("\n")[1:] == ["CPU: --", "GPU: --", "RAM: --", "VRAM: --", "FPS: --"]

        sample, nxt = devmod.DevBridge.sample_exhibit(os.getpid(), "/nonexistent.sock", {})
        assert sample["fps"] is None and sample["cpu"] is None, "no baseline, no rates"
        assert sample["rss"] > 0 and sample["swap"] >= 0 and nxt["ticks"] >= 0 and nxt["frames"] is None
        sample, _ = devmod.DevBridge.sample_exhibit(os.getpid(), "/nonexistent.sock", nxt)
        assert sample["cpu"] is not None and sample["cpu"] >= 0.0
    finally:
        devmod.api_client.request = real_request
    d.setOverlayStats("A", False)
    d.setLabel("A", "A · realsync off")


def test_persistence(d) -> None:
    d2 = devmod.DevBridge()
    st = d2.slotState("A")
    assert st["label"] == "A · realsync off" and st["scene"] == "111"
    assert st["overlayCorner"] == "top-right" and st["overlayStats"] is False
    assert d2.trailMode("A") == "Exact"
    assert d2.toggleOn("A", "bloom") is False and d2.toggleOn("A", "prewarm") is True
    assert d2.instrumentOn("A", "LWE_PRESENTTRACE") is True
    assert d2.envText("A") == "LWE_KILLLIGHT=5\nLWE_NOPREWARM=1"
    assert d2.propText("A") == "rain=0.5"
    assert d2.slotState("B")["scene"] == "probe:cal1"


def test_refusals(d) -> None:
    seen = []
    d.consoleLine.connect(lambda s, t, e: seen.append((s, t, e)))
    d.setScene("B", "")
    d.launch("B")
    assert seen and seen[-1][0] == "B" and seen[-1][2] is True, "a refused launch says so on stderr"
    assert d.anyAlive() is False and d.runMode() == ""
    d.stop()
    assert d.isHolding() is False and d.benchMode() == ""


def test_launch_and_residue(app, d) -> None:
    lines = []
    d.consoleLine.connect(lambda s, t, e: lines.append((s, t, e)))
    os.environ["FAKE_EXIT"] = "3"
    d.launch("A")
    assert d.runMode() == "window"
    _wait_finished(app, d, "A")
    st = d.slotState("A")
    assert st["lastCode"] == 3 and st["state"] == "exit 3", st
    assert d.runMode() == "" and d.isHolding() is False
    out = [t for s, t, e in lines if s == "A" and not e]
    err = [t for s, t, e in lines if s == "A" and e]
    assert out[0] == "=" * 33 and out[1].startswith("Beginning new bench run 20") and out[2] == "=" * 33, out[:3]
    assert any("overlay=A · realsync off" in t and "exhibit-a.sock" in t and "bloom=1" in t for t in out), out
    assert any("Could not parse puppet" in t for t in err), "stderr reaches the console marked"
    tail = d.tailLines("A")
    assert any("LWE-PRESENT" in t["text"] and not t["err"] for t in tail)
    assert any("puppet" in t["text"] and t["err"] for t in tail), "the tail keeps the severity"

    d2 = devmod.DevBridge()
    assert d2.slotState("A")["lastCode"] == 3 and d2.tailLines("A") == tail, "residue survives restart"

    os.environ.pop("FAKE_EXIT")
    os.environ["FAKE_SEGV"] = "1"
    d.launch("A")
    _wait_finished(app, d, "A")
    assert d.slotState("A")["lastCode"] == 139, "a segfault reads as the shell's 139"
    os.environ.pop("FAKE_SEGV")


def test_legacy_binary(app, d, legacy_engine) -> None:
    d.setBinary("B", legacy_engine)
    d.setScene("B", "222")
    d.launch("B")
    st = d.slotState("B")
    assert st["legacy"] is True and st["liveControl"] is False
    argv = d.compose_argv("B")
    assert "--api-socket" not in argv, "a legacy build launches on the old command line"
    env, unset = d.compose_env("B")
    assert "LWE_SOCKET" not in env and "LWE_SOCKET" in unset
    _wait_finished(app, d, "B")
    assert d.slotState("B")["lastCode"] == 0
    d.setBinary("B", "")


def test_isolator(d) -> None:
    objs = d.objectList("A")
    assert [o["objid"] for o in objs] == ["453", "475", "512"]
    assert objs[2]["parent"] == "475" and objs[0]["type"] == "particle"
    assert all(o["on"] for o in objs)
    assert d.isolatorEditable("A") is True
    d.setObjectsOn("A", ["453", "512"], False)
    d.setObjectOn("A", "512", True)
    assert [o["on"] for o in d.objectList("A")] == [False, True, True], "a stopped side keeps the edit"
    assert "skip-object=453" not in d.compose_argv("A"), \
        "an API build gets the set over its socket after launch, never as build-time flags"
    d.slots["A"].api = False
    argv = d.compose_argv("A")
    assert argv[argv.index("--render-debug") + 1] == "skip-object=453", "a legacy build takes launch flags"
    d.slots["A"].api = True
    d.setScene("A", "222")
    assert d.slots["A"].skip == set(), "the set is per scene"
    d.setScene("A", "111")
    d.setObjectsOn("A", ["453"], True)


def test_stop_reaps_everything(app, d) -> None:
    os.environ["FAKE_SLEEP"] = "1"
    slot = d.slots["A"]
    d.launch("A")
    proc = slot.proc
    assert proc is not None
    d.setObjectsOn("A", ["453"], False)
    d.stop()
    assert proc.state() == QProcess.ProcessState.NotRunning
    assert d.anyAlive() is False and d.slots["A"].skip == {"453"}, "a stop keeps the isolator set"
    d.setObjectsOn("A", ["453"], True)
    st = d.slotState("A")
    assert st["state"] == "stopped" and st["lastStopped"] is True and st["lastCode"] == 143, \
        "a deliberate stop records the real exit but reads as stopped"
    os.environ.pop("FAKE_SLEEP")


def test_relaunch_changes_the_pid(app, d) -> None:
    os.environ["FAKE_SLEEP"] = "1"
    d.launch("A")
    pid1 = int(d.slots["A"].proc.processId())
    d.setToggle("A", "bloom", True)
    assert d.slots["A"].relaunching and d.isHolding(), "a relaunch-class edit queues a restart"
    for _ in range(60):
        app.processEvents()
        QTest.qWait(25)
        if d.alive("A") and not d.slots["A"].relaunching and int(d.slots["A"].proc.processId()) != pid1:
            break
    assert d.alive("A") and int(d.slots["A"].proc.processId()) != pid1, "the side came back on a new pid"
    assert d.runMode() == "window"
    d.stop()
    os.environ.pop("FAKE_SLEEP")


def test_launch_b_beside_a(app, d) -> None:
    os.environ["FAKE_SLEEP"] = "1"
    d.launch("A")
    pid_a = int(d.slots["A"].proc.processId())
    d.launch("B")
    assert d.alive("A") and d.alive("B"), "Launch B leaves A running"
    assert int(d.slots["A"].proc.processId()) == pid_a, "A was not restarted by B's launch"
    assert d.benchMode() == "A + B" and d.runMode() == "window"
    d.launch("A")
    assert d.alive("A") and d.alive("B") and int(d.slots["A"].proc.processId()) != pid_a, \
        "Launch A restarts A alone"
    d.stopSide("A")
    assert not d.alive("A") and d.alive("B") and d.runMode() == "window", "a side stops alone"
    d.stopSide("B")
    assert not d.anyAlive() and d.runMode() == "", "the last side out clears the run"
    d.launchBoth()
    assert d.alive("A") and not d.alive("B"), "B follows A, it does not race it"
    d._b_due = 0.0
    for _ in range(80):
        app.processEvents()
        QTest.qWait(25)
        if d.alive("A") and d.alive("B"):
            break
    assert d.alive("A") and d.alive("B"), "the fallback spawns B once A's window has had its chance"
    d.stop()
    assert not d.anyAlive() and d.runMode() == ""
    os.environ.pop("FAKE_SLEEP")


def test_failed_start_retires(app, d, wp_root) -> None:
    dead = os.path.join(_TMP, "not-executable")
    with open(dead, "w") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    d.setBinary("A", dead)
    assert d.slotState("A")["legacy"] is True, "a binary that cannot answer --help is not an API build"
    d.launch("A")
    for _ in range(50):
        app.processEvents()
        QTest.qWait(20)
        if not d.alive("A") and d.slotState("A")["lastCode"] == 126:
            break
    st = d.slotState("A")
    assert st["lastCode"] == 126 and st["alive"] is False, st
    assert d.runMode() == "", "a start failure leaves no run behind"
    d.setBinary("A", "")


def main() -> None:
    app = QGuiApplication([])
    paths.ensure_dirs()
    settings.ensure_exists()

    wp_root = os.path.join(_TMP, "wallpapers")
    _scene(wp_root, "111", "Dead Space", [
        {"id": 453, "name": "Dust motes", "particle": "x"},
        {"id": 475, "name": "Fog 1", "image": "x"},
        {"id": 512, "name": "Warp core", "image": "x", "parent": 475},
    ])
    _scene(wp_root, "222", "Ocean", [])
    _scene(str(paths.data_dir() / "probes"), "cal1", "cal1", [])
    bins = paths.data_dir() / "dev-binaries"
    bins.mkdir(parents=True, exist_ok=True)
    _write_exe(str(bins / "alt-engine"), FAKE_ENGINE)
    fake_engine = _write_exe(os.path.join(_TMP, "fake-engine"), FAKE_ENGINE)
    legacy_engine = _write_exe(os.path.join(_TMP, "legacy-engine"), LEGACY_ENGINE)

    s = settings.load()
    s["ENGINE_BIN"] = fake_engine
    s["ASSETS_DIR"] = "/fake/assets"
    s["WALLPAPERS_DIR"] = wp_root
    settings.save(s)

    devmod.DevBridge._layout = lambda self: {"x": 0, "y": 0, "w": 2560, "h": 1440,
                                             "reserved": [0, 40, 0, 0], "gap": 5}
    devmod.DevBridge._hyprctl_clients = lambda self: []
    devmod.DevBridge._hypr_dispatch = lambda self, expr: True

    d = devmod.DevBridge()
    test_tables(d)
    test_compose(d, fake_engine, wp_root)
    test_empty_slots_take_the_now_playing_scene(d)
    test_choices(d)
    test_overlay(d)
    test_persistence(d)
    test_refusals(d)
    test_launch_and_residue(app, d)
    test_legacy_binary(app, d, legacy_engine)
    test_isolator(d)
    test_stop_reaps_everything(app, d)
    test_relaunch_changes_the_pid(app, d)
    test_launch_b_beside_a(app, d)
    test_failed_start_retires(app, d, wp_root)
    d.shutdown()
    print("OK test_dev_bridge - two slots compose, partition env, persist, record residue, "
          "launch a legacy build on the old line, and stop clean")


if __name__ == "__main__":
    main()
