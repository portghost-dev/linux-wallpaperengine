"""Regression tests for the B5 adversarial-review findings.

Each case pins one finding so it cannot silently come back:
  F2  playlists.toggle_member refuses an unsafe wid (glob/space) but still allows removal
  F3  settings coerces/validates the *_or_empty schema types (garbage never reaches argv)
  F4  wp.save skips a non-shell-identifier prop name instead of dropping the whole write
  F5  the bench refuses to launch when no display output resolves (no window-over-session)
"""
import os
import sys
import tempfile
import warnings
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lwe-reviewfix-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lwe_ui.storage import paths, settings, playlists, wp, tier_a  # noqa: E402


def test_f2_toggle_member_wid_safety() -> None:
    paths.ensure_dirs()
    slug = playlists.create("safety", members=["111"])
    assert playlists.toggle_member(slug, "222") is True
    assert "222" in playlists.members(slug)
    # a glob metacharacter is refused (would glob in the watcher MEMBERS list)
    assert playlists.toggle_member(slug, "*") is False
    assert "*" not in playlists.members(slug)
    assert playlists.toggle_member(slug, "a b") is False
    assert playlists.toggle_member(slug, "../etc") is False
    # but a legacy-unsafe id that is somehow already a member can still be removed
    d = playlists.load(slug)
    d["MEMBERS"] = d["MEMBERS"] + " legacy*id"
    playlists.save(slug, d)
    assert "legacy*id" in playlists.members(slug)
    assert playlists.toggle_member(slug, "legacy*id") is False
    assert "legacy*id" not in playlists.members(slug)
    assert paths.is_safe_wid("123") and paths.is_safe_wid("my-wall_2.0")
    assert not paths.is_safe_wid("") and not paths.is_safe_wid("..") and not paths.is_safe_wid("a/b")
    print("OK F2 toggle_member refuses unsafe wids, still removes legacy ones")


def test_f3_settings_or_empty_coercion() -> None:
    settings.ensure_exists()
    s = settings.load()
    # int_or_empty: garbage collapses to "" (engine default), never carries "abc" to --fps
    s["ENGINE_FPS"] = "abc"
    # enum_or_empty: an out-of-set value collapses to "" (never a wrong --clamp)
    s["ENGINE_CLAMP"] = "xyzzy"
    settings.save(s)
    r = settings.load()
    assert r["ENGINE_FPS"] == "", repr(r["ENGINE_FPS"])
    assert r["ENGINE_CLAMP"] == "", repr(r["ENGINE_CLAMP"])
    s2 = settings.load()
    s2["ENGINE_FPS"] = "30"
    settings.save(s2)
    assert settings.load()["ENGINE_FPS"] == 30
    print("OK F3 settings *_or_empty types validate (no garbage reaches the engine argv)")


def test_f4_wp_save_skips_bad_prop_name() -> None:
    paths.ensure_dirs()
    wid = "wpfix1"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wp.save(wid, {"BG": "/x", "TYPE": "scene",
                      "props": {"good": "0.5", "bad.name": "9", "also bad": "7",
                                "newliney": "1\n2"}})
    got = wp.load(wid)
    # the whole write survived; only the non-identifier names and the newline value were dropped
    assert got["props"].get("good") == "0.5", got["props"]
    assert "bad.name" not in got["props"] and "also bad" not in got["props"], got["props"]
    assert "newliney" not in got["props"], got["props"]
    assert tier_a.is_valid_key("PROP_good")
    assert not tier_a.is_valid_key("PROP_bad.name")
    print("OK F4 wp.save skips a bad prop name, keeps the rest (no whole-write data loss)")


def test_f5_bench_refuses_without_output() -> None:
    from PySide6.QtGui import QGuiApplication
    from lwe_ui import bench_bridge, bench_courier

    app = QGuiApplication.instance() or QGuiApplication(["t"])  # noqa: F841
    bench_courier.available = lambda: True
    # the spawn-path engines-clear wait must not poll the LIVE engine in tests
    bench_courier.wait_clear = lambda *a, **k: True
    bench_courier.standdown = lambda *a, **k: True
    bench_courier.resume = lambda *a, **k: True

    seen = {"spawned": False}

    class FakeProc:
        def __init__(self, *a, **k): pass
        def setProcessEnvironment(self, *a): pass
        def setProgram(self, *a): seen.__setitem__("spawned", True)
        def setArguments(self, *a): pass
        def start(self, *a): seen.__setitem__("spawned", True)

    paths.ensure_dirs(); settings.ensure_exists()
    wdir = str(paths.default_wallpapers_dir())
    os.makedirs(os.path.join(wdir, "benchfix"), exist_ok=True)
    import json as _json
    _json.dump({"type": "scene", "file": "scene.json", "title": "bf"},
               open(os.path.join(wdir, "benchfix", "project.json"), "w"))
    from lwe_ui.storage import wp
    wp.save("benchfix", {"BG": os.path.join(wdir, "benchfix"), "TYPE": "scene", "props": {}})

    b = bench_bridge.BenchBridge(process_factory=FakeProc)
    b._resolve_outputs = lambda: []
    b._bench_available = True
    b.open("benchfix", "good")
    b.startTest()
    assert seen["spawned"] is False, "startTest must NOT spawn an engine when no output resolves"
    assert not b.isTesting, "startTest must refuse (not testing) when no output resolves"
    print("OK F5 bench refuses to launch when no display output resolves")


if __name__ == "__main__":
    test_f2_toggle_member_wid_safety()
    test_f3_settings_or_empty_coercion()
    test_f4_wp_save_skips_bad_prop_name()
    test_f5_bench_refuses_without_output()
    print("OK: all review-fix regressions pass")
