"""DevBridge: launch composition, isolation, toggles, verdict log.

SAFETY: nothing here starts a real bench process, and since the safe-build reap was deleted
there is no code path left that reaps by process name at all.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lwe-dev-test-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from lwe_ui import dev as devmod  # noqa: E402
from lwe_ui.storage import paths, settings  # noqa: E402


def main() -> None:
    app = QGuiApplication([])  # noqa: F841
    paths.ensure_dirs()

    settings.ensure_exists()
    s = settings.load()
    # a configured engine path is only honored when the file is there
    fake_engine = str(Path(_TMP) / "fake-engine")
    open(fake_engine, "w").close()
    s["ENGINE_BIN"] = fake_engine
    s["ASSETS_DIR"] = "/fake/assets"
    s["WALLPAPERS_DIR"] = "/fake/wallpapers"
    settings.save(s)

    d = devmod.DevBridge()

    assert len(d.ourToggles()) >= 6
    assert len(d.instruments()) >= 6
    assert d.subsystems()[0] == "Tour"

    d.setTarget("2114739882")
    d.solo("12")
    d.setSkipObject("44", True)
    d.setSkipObject("55", True)
    d.setSkipEffect("7", True)
    # the single bench renders to the REAL display via --screen-root (the proven
    # Wayland path); GLFW --window mode is broken on Wayland and was the old default.
    # Stub the output enumeration so the argv is deterministic; empty outputs must REFUSE.
    devmod.DevBridge._dev_outputs = lambda self: ["TEST-OUT"]
    argv = d.compose_argv()
    assert fake_engine == argv[0]
    assert "--screen-root" in argv and argv[argv.index("--screen-root") + 1] == "TEST-OUT"
    assert "--window" not in argv, "single bench must never use GLFW windowed mode"
    _saved_outs = devmod.DevBridge._dev_outputs
    devmod.DevBridge._dev_outputs = lambda self: []
    assert d.compose_argv() == [], "no output -> refuse, never launch blind"
    devmod.DevBridge._dev_outputs = _saved_outs
    argv = d.compose_argv()
    assert "object=12" in argv
    assert "skip-object=44" in argv and "skip-object=55" in argv
    assert "skip-effect=7" in argv
    assert argv[-2] == "--bg" and argv[-1].endswith("/2114739882")

    d.setSkipObject("44", False)
    assert "skip-object=44" not in d.compose_argv()

    # multi-object solo: the engine's object= filter holds ONE id (std::optional<int>), so a
    # solo SET is composed as its complement - skip every other enumerable object instead.
    _saved_objs = devmod.DevBridge.objectList
    devmod.DevBridge.objectList = lambda self: [
        {"objid": "12"}, {"objid": "13"}, {"objid": "55"}, {"objid": "77"},
        {"objid": ""}, {"objid": "camera"},   # unaddressable: must never reach the engine
    ]
    d.solo("13")
    argv = d.compose_argv()
    assert "object=12" not in argv and "object=13" not in argv, \
        "a multi-object solo must not fall back to the single-object filter"
    assert "skip-object=77" in argv, "objects outside the solo set must be skipped"
    assert "skip-object=12" not in argv and "skip-object=13" not in argv, \
        "soloed objects must never be skipped"
    assert argv.count("skip-object=55") == 1, "an explicit skip must not be emitted twice"
    assert not any(a.startswith("skip-object=") and not a[12:].isdigit() for a in argv), \
        "non-numeric ids would abort the engine at parse time (std::stoi + noreturn log)"
    # nothing enumerable to hide against -> fall back to the native filter, never a silent
    # full-scene render that reads as 'solo did nothing'
    devmod.DevBridge.objectList = lambda self: []
    assert "object=12" in d.compose_argv()
    devmod.DevBridge.objectList = _saved_objs
    d.solo("13")
    assert "object=12" in d.compose_argv()

    d.setInstrument("LWE_PARTSTATS", True)
    d.setInstrument("LWE_LIGHTDUMP", True)
    d.setFixOn("frontface", False)
    d.setFixOn("texcomp", False)
    env = d.compose_env()
    assert env["LWE_PARTSTATS"] == "1" and env["LWE_LIGHTDUMP"] == "1"
    assert env["LWE_FRONTFACE"] == "ccw" and env["LWE_TEXCOMP"] == "0"
    assert d.fixOn("frontface") is False and d.fixOn("shapes") is True

    prev = d.launchPreview()
    assert "LWE_FRONTFACE=ccw" in prev and fake_engine in prev and "object=12" in prev

    d.clearIsolation()
    iso = d.isolationState()
    assert iso["soloObjects"] == [] and iso["skipObjects"] == [] and iso["skipEffects"] == []

    d.solo("99")
    d.setFixOn("fbopool", False)
    d.logVerdict("navy ocean, continents visible")
    d.logVerdict("rim glow still hot")
    recent = d.recentVerdicts(5)
    assert len(recent) == 2
    assert recent[0]["text"] == "rim glow still hot"
    assert "solo=99" in recent[0]["cond"] and "fbopool" in recent[0]["cond"]


    assert d.isRunning() is False
    d.stopBench()
    assert d.isRunning() is False

    from lwe_ui import bench_courier as _bc
    _bc.calls = {"standdown": 0, "resume": 0}
    _bc.standdown = lambda *a, **k: (_bc.calls.__setitem__("standdown", _bc.calls["standdown"] + 1) or True)
    _bc.resume = lambda *a, **k: (_bc.calls.__setitem__("resume", _bc.calls["resume"] + 1) or True)
    _bc.wait_clear = lambda *a, **k: True
    real_wp = os.path.join(_TMP, "abwp")
    os.makedirs(os.path.join(real_wp, "abtarget"), exist_ok=True)
    s2 = settings.load(); s2["WALLPAPERS_DIR"] = real_wp; settings.save(s2)
    d.setTarget("abtarget")
    d.clearIsolation()
    d._primary_geometry = lambda: (0, 0, 2560, 1440)
    assert d._ab_spawn_geometry() == "0x0x1280x720"
    d.abReset()
    d.setABFix("B", "frontface", False)
    assert "LWE_FRONTFACE" not in d._ab_side_env("A")
    assert d._ab_side_env("B").get("LWE_FRONTFACE") == "ccw"
    # raw env lines apply LAST (the developer's final word) and skip bad keys
    d.setABEnvText("A", "LWE_SSFACTOR=0\nbad key=1\n# comment\n")
    assert d._ab_side_env("A").get("LWE_SSFACTOR") == "0"
    assert "bad key" not in d.abEnvText("A")
    d.setABEnvText("A", "")
    assert d.abRunning() is False
    d.startAB()
    assert d.abRunning() is True
    st = d.abState()
    assert st["sideA"] == "stock" and "FRONTFACE off" in st["sideB"], st
    # a standdown is state: startAB sends exactly one, and nothing renews
    assert _bc.calls["standdown"] == 1, "A/B must send exactly one standdown"
    assert not hasattr(d, "swapAB") and not hasattr(d, "abApply")
    # the single bench is refused while A/B holds the display (mutually exclusive)
    d.startBench()
    assert d.isRunning() is False
    assert d.isHolding() is True
    d.stopAB()
    assert d.abRunning() is False and d.isHolding() is False
    assert _bc.calls["resume"] == 1, "stopAB must hand the outputs back"
    # if both A/B engines exit on their own, the outputs are handed back (not held forever)
    d._primary_geometry = lambda: (0, 0, 2560, 1440)
    d.startAB()
    assert d.abRunning() is True
    d._proc_a = None; d._proc_b = None
    d._ab_finished()
    assert d.abRunning() is False, "both engines gone -> A/B must auto-release"
    assert _bc.calls["resume"] == 2, "auto-release must resume the daemon"
    # A/B refuses to launch when the monitor geometry can't be resolved (finding-5 guard)
    d._primary_geometry = lambda: None
    d.startAB()
    assert d.abRunning() is False, "A/B must refuse without a resolvable output"

    d._primary_geometry = lambda: (0, 0, 2560, 1440)
    d.stopAB()

    devmod.DevBridge._now_playing_wid = staticmethod(lambda: "abtarget")
    d.setTarget("")
    assert d._target_dir().endswith("/abtarget"), d._target_dir()
    devmod.DevBridge._now_playing_wid = staticmethod(lambda: "")
    assert d._target_dir() == "", "no engine current -> honest empty target"

    d.savePaletteState({"x": 40, "y": 60, "width": 500, "height": 480, "pinned": False, "tab": 3})
    ps = d.paletteState()
    assert ps.get("x") == 40 and ps.get("width") == 500, ps
    assert ps.get("tab") == 3 and ps.get("pinned") is False, ps

    d.setTarget("abtarget")
    started = {"n": 0}
    _orig_start = devmod.DevBridge.startBench
    devmod.DevBridge.startBench = lambda self: started.__setitem__("n", started["n"] + 1)
    try:
        d.setFixOn("frontface", False)
        assert d._relaunch_timer.isActive(), "a Tour flip must arm the debounce even when idle"
        d._relaunch_timer.stop()
        d._do_relaunch()
        assert started["n"] == 1, "idle Tour flip must start the bench through the debounce"
        d.setAutoRelaunch(False)
        d.setFixOn("frontface", True)
        assert not d._relaunch_timer.isActive(), "auto-relaunch off must disarm the Tour flip"
        d._do_relaunch()
        assert started["n"] == 1
        d.setAutoRelaunch(True)
    finally:
        devmod.DevBridge.startBench = _orig_start
    d.setFixOn("frontface", True)
    d._relaunch_timer.stop(); d._pending_start = False

    assert d.isRunning() is False
    d.solo("7")
    assert d._relaunch_timer.isActive() is False, "no bench running -> no relaunch scheduled"
    d._do_relaunch()
    assert d.isRunning() is False
    d.clearIsolation()

    tog = d.ourToggles()
    for t in tog:
        assert "commit" in t and "evidence" in t and "experimental" in t, t
        assert t["commit"] == "" and t["evidence"] == "", "census populates these; not faked"
    shapes = next(t for t in tog if t["key"] == "shapes")
    assert shapes["experimental"] is True, "LWE_SHAPES is the experimental Tour row (7a frame)"
    assert next(t for t in tog if t["key"] == "frontface")["experimental"] is False

    assert d.autoRelaunch() is True, "default ON matches the current always-relaunch behavior"
    d.setAutoRelaunch(False)
    assert d.autoRelaunch() is False
    d.setAutoRelaunch(True)

    assert d.uptimeSeconds() == 0, "idle bridge reports no uptime"
    d._run_start = time.monotonic() - 5.0
    d._ab_running = True
    assert d.uptimeSeconds() >= 5, "uptime counts seconds since the hold began"
    d._ab_running = False
    d._run_start = 0.0
    assert d.uptimeSeconds() == 0

    d.setTarget("2114739882")
    d.setRenderDebug("base-only", True)
    d.setRenderDebug("pass-log", True)
    argv2 = d.compose_argv()
    # emitted as --render-debug <flag> in declared order (base-only before pass-log)
    assert "base-only" in argv2 and "pass-log" in argv2
    bi = argv2.index("base-only")
    assert argv2[bi - 1] == "--render-debug"
    assert argv2.index("base-only") < argv2.index("pass-log"), "flags keep declared order"
    assert d.renderDebugOn("base-only") is True and d.renderDebugOn("no-solid-final") is False
    assert len(d.renderDebugFlags()) == 3
    d.setRenderDebug("base-only", False)
    assert "base-only" not in d.compose_argv()
    d.setRenderDebug("bogus-flag", True)
    assert "bogus-flag" not in d.compose_argv()

    d.queueSetProperty("bloomstrength", "0.35")
    d.queueSetProperty("exposure", "1.2")
    argv3 = d.compose_argv()
    assert "--set-property" in argv3
    assert "bloomstrength=0.35" in argv3 and "exposure=1.2" in argv3
    # a repeated name replaces the earlier value (last write wins), staying a single entry
    d.queueSetProperty("bloomstrength", "0.50")
    props = d.setProperties()
    names = [p["name"] for p in props]
    assert names.count("bloomstrength") == 1 and dict((p["name"], p["value"]) for p in props)["bloomstrength"] == "0.50"
    assert "bloomstrength=0.50" in d.compose_argv() and "bloomstrength=0.35" not in d.compose_argv()
    d.clearProperty("bloomstrength")
    assert "bloomstrength" not in [p["name"] for p in d.setProperties()]
    d.clearProperty("exposure")
    assert d.setProperties() == []
    d.setRenderDebug("pass-log", False)
    d.clearIsolation()

    d._primary_geometry = lambda: (0, 0, 2560, 1440)
    assert d._ab_spawn_geometry() == "0x0x1280x720", d._ab_spawn_geometry()
    d._primary_geometry = lambda: (100, 50, 3440, 1440)
    assert d._ab_spawn_geometry() == "0x0x1720x720"
    d._primary_geometry = lambda: None
    assert d._ab_spawn_geometry() is None, "no monitor -> no geometry -> startAB refuses"
    d._primary_geometry = lambda: (0, 0, 2560, 1440)
    assert "vertical" not in d.abState()
    assert not hasattr(d, "setABOrientation") and not hasattr(d, "abVertical")
    # loadouts persist through the palette-state file and restore on construction
    d.abReset()
    d.setABFix("B", "fbopool", False)
    d.setABEnvText("A", "LWE_TIMESCALE=2")
    d.savePaletteState({})
    d2 = devmod.DevBridge()
    assert d2.abFixOn("B", "fbopool") is False, "persisted side loadout must restore"
    assert d2.abEnvText("A") == "LWE_TIMESCALE=2"
    d.abReset()
    d.setABEnvText("A", "")
    d.savePaletteState({})

    all_envs = {i["env"] for i in d.instruments()}
    assert len(all_envs) >= 9, "instrument inventory must not shrink"
    assert all(e.startswith("LWE_") for e in all_envs), "instruments are engine env switches"
    assert all(i.get("tags") for i in d.instruments()), \
        "every instrument needs log tags - the readout filter keys on them"
    assert len(all_envs) == len(d.instruments()), "no env listed twice in the inventory"
    # each lens renders ONLY its own, and a lens with no instrument renders none
    assert "LWE_LIGHTDUMP" in [i["env"] for i in d.scopedInstruments("Lighting & Models")]
    assert "LWE_PARTSTATS" in [i["env"] for i in d.scopedInstruments("Particles")]
    assert "LWE_SHADERDUMP" in [i["env"] for i in d.scopedInstruments("Render")]
    assert [i["env"] for i in d.scopedInstruments("Bloom")], \
        "Bloom is covered by the composition/FBO instruments - it must not be empty again"
    assert d.scopedInstruments("Puppets") == [], \
        "Puppets has no env instrument (the engine gates nothing there) - it streams via tags"
    assert d.lensAlwaysOn("Puppets"), "Puppets must carry its always-on channel"
    assert not d.lensAlwaysOn("Bloom"), "Bloom is switch-driven, not always-on"
    assert d.scopedReadoutTags("Puppets"), "an always-on lens must still yield readout tags"
    assert d.scopedInstruments("Tour") == [], "Tour is not a lens"

    # every lens with entries is a subset of the inventory, and every instrument reaches at
    # least one lens. An env MAY appear under two lenses (Bloom shares with Performance and
    # Render), so this is a coverage check, not a partition.
    scoped_all = []
    for s in d.subsystems():
        scoped_all += [i["env"] for i in d.scopedInstruments(s)]
    assert set(scoped_all) <= set(all_envs), set(scoped_all) - set(all_envs)
    assert set(scoped_all) == set(all_envs), \
        f"instruments reachable from no lens: {sorted(set(all_envs) - set(scoped_all))}"
    for s in d.subsystems():
        envs = [i["env"] for i in d.scopedInstruments(s)]
        assert len(envs) == len(set(envs)), f"{s} lists an instrument twice: {envs}"
    for empty in ("Puppets", "Tour"):
        assert d.scopedInstruments(empty) == [], empty
    assert [i["env"] for i in d.scopedInstruments("Camera")] == ["LWE_CAMPROBE"]
    assert all("env" in i and "what" in i for i in d.scopedInstruments("Render"))

    d.clearEnvLines()
    d.clearIsolation()
    d._instruments = set(); d._toggles_off = set()
    assert d.envLines() == [], "editor starts empty"
    assert d.setEnvLine("LWE_POOL_HWM", "768") is True
    assert d.setEnvLine("LWE_SSFACTOR", "1.5") is True
    lines = d.envLines()
    assert [l["key"] for l in lines] == ["LWE_POOL_HWM", "LWE_SSFACTOR"], lines
    env = d.compose_env()
    assert env["LWE_POOL_HWM"] == "768" and env["LWE_SSFACTOR"] == "1.5", env
    # an invalid key (not a shell identifier) is REFUSED - never queued, never composed
    assert d.envKeyValid("2BAD") is False and d.envKeyValid("has space") is False
    assert d.envKeyValid("LWE_OK") is True and d.envKeyValid("_x1") is True
    assert d.setEnvLine("2BAD", "x") is False
    assert d.setEnvLine("has space", "x") is False
    assert "2BAD" not in [l["key"] for l in d.envLines()]
    assert "2BAD" not in d.compose_env() and "has space" not in d.compose_env()
    # a repeated key replaces its value (last write wins), staying a single entry
    assert d.setEnvLine("LWE_POOL_HWM", "512") is True
    hwm = [l for l in d.envLines() if l["key"] == "LWE_POOL_HWM"]
    assert len(hwm) == 1 and hwm[0]["value"] == "512", d.envLines()
    assert d.compose_env()["LWE_POOL_HWM"] == "512"
    # a value is coerced to a single line (newlines stripped) so it can never break the env
    assert d.setEnvLine("LWE_MULTI", "a\nb\r c") is True
    assert "\n" not in d.compose_env()["LWE_MULTI"] and "\r" not in d.compose_env()["LWE_MULTI"]
    d.removeEnvLine("LWE_SSFACTOR")
    assert "LWE_SSFACTOR" not in [l["key"] for l in d.envLines()]
    assert "LWE_SSFACTOR" not in d.compose_env()
    d.clearEnvLines()
    assert d.envLines() == [] and "LWE_POOL_HWM" not in d.compose_env()
    d.setInstrument("LWE_FBPROFILE", True)
    assert d.compose_env()["LWE_FBPROFILE"] == "1"
    d.setEnvLine("LWE_FBPROFILE", "2")
    assert d.compose_env()["LWE_FBPROFILE"] == "2", "raw editor line overrides the instrument"
    d.setInstrument("LWE_FBPROFILE", False)
    d.clearEnvLines()

    print("OK: dev bridge - inventories/compose-argv/isolation/env-toggles/launch-preview/"
          "verdict-log/AB-split/palette-state/debounce-idle/tour-provenance/"
          "auto-relaunch/uptime/render-debug/set-property/ab-orientation/"
          "scoped-instruments/env-editor all pass")


if __name__ == "__main__":
    main()
