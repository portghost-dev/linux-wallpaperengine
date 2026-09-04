"""Self-verification for the step-4 engine sync layer (rotation push + transport verbs
+ pid-change reconnect). Same sandbox + stub discipline as test_api_shownow.py.

Contract under test (addendum SS4 + the leak-guard lesson):
  * the ACTIVE playlist resolves into complete show-args entries with ui_id;
  * enabled follows ROTATION_ENABLED/MODE;
  * rotateNext prefers the engine verb, pushes+retries once, then reports failure;
  * rotatePrev honors an honest engine "history empty";
  * a status() poll that sees a NEW engine pid re-pushes the rotation set.

Run: python3 tests/test_engine_sync.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_BOOT_HOME = tempfile.mkdtemp(prefix="lwe-engsync-boot-")
for _k, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_k] = os.path.join(_BOOT_HOME, _sub) if _sub else _BOOT_HOME

_API = types.ModuleType("lwe_ui.api_client")
_API.available = lambda: False
_API.show = lambda wid, wait_done=False, **kw: None
_API.status = lambda: None
_API.ping = lambda: None
_API.rotate_set = lambda *a, **kw: None
_API.next_wallpaper = lambda: None
_API.prev_wallpaper = lambda: None
_API.set_fullscreen = lambda behavior: None
_API.set_fps = lambda fps: None
_API.set_parallax = lambda enabled: None
_API.set_particles = lambda enabled: None
_API.set_fullscreen_ignore = lambda ids: None
_API.list_objects = lambda **kw: None
_API.set_skip = lambda ids, **kw: None
sys.modules["lwe_ui.api_client"] = _API

from PySide6.QtCore import QCoreApplication  # noqa: E402

_APP = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])

from lwe_ui import models  # noqa: E402
from lwe_ui.storage import playlists, settings, wp  # noqa: E402

assert models.api_client is _API

# Hermetic PATH: a host with the legacy watcher script installed must behave the
# same as a bare machine, so the watcher binary is never found here.
models.shutil.which = lambda _cmd, *a, **kw: None


class EngineSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.mkdtemp(prefix="lwe-engsync-")
        os.environ["HOME"] = self._home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self._home, "c")
        os.environ["XDG_STATE_HOME"] = os.path.join(self._home, "s")
        os.environ["XDG_DATA_HOME"] = os.path.join(self._home, "d")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

        settings.save(settings.load())
        _API.available = lambda: True
        _API.status = lambda: None
        self.pushes: list[dict] = []

        def _rotate_set(entries, interval_s, order, enabled, avoid_repeat=True, label=""):
            self.pushes.append({"entries": entries, "interval_s": interval_s, "order": order,
                                "enabled": enabled, "label": label})
            return {"id": 1, "ok": True, "status": "done"}

        _API.rotate_set = _rotate_set
        self.backend = models.Backend()

    def _seed_playlist(self, members: list[str], mode: str = "sequential", interval: int = 300) -> None:
        slug = playlists.active_slug()
        d = playlists.load(slug)
        d["MEMBERS"] = " ".join(members)
        d["MODE"] = mode
        d["INTERVAL"] = interval
        playlists.save(slug, d)
        for wid in members:
            wp.save(wid, {"SPEED": 1.5, "SCALING": "fill"})

    def test_payload_resolves_active_playlist(self) -> None:
        self._seed_playlist(["111", "222"])
        entries, interval, order, enabled, label = self.backend._engine_rotation_payload()
        self.assertEqual([e["ui_id"] for e in entries], ["111", "222"])
        self.assertEqual(interval, 300)
        self.assertEqual(order, "sequential")
        self.assertTrue(enabled)
        self.assertTrue(label)
        # entries carry the RESOLVED vocabulary, not raw conf
        self.assertEqual(entries[0]["scaling"], "fill")
        self.assertAlmostEqual(entries[0]["speed"], 1.5)

    def test_static_mode_and_rotation_toggle_disable(self) -> None:
        self._seed_playlist(["111"], mode="static")
        self.assertFalse(self.backend._engine_rotation_payload()[3], "static = no rotation")
        self._seed_playlist(["111"], mode="shuffle")
        settings.save({**settings.load(), "ROTATION_ENABLED": False})
        self.assertFalse(self.backend._engine_rotation_payload()[3], "user pause wins")

    def test_engine_owns_the_schedule(self) -> None:
        """The push carries enabled=True for a live shuffle playlist (one scheduler: the engine)."""
        self._seed_playlist(["111", "222"], mode="shuffle")
        self.backend._sync_engine()
        self.assertEqual(len(self.pushes), 1)
        self.assertTrue(self.pushes[0]["enabled"], "the engine owns the schedule")

    def test_rotate_next_uses_engine_verb(self) -> None:
        calls = []
        _API.next_wallpaper = lambda: (calls.append(1), {"id": 1, "ok": True, "status": "accepted"})[1]
        self.assertTrue(self.backend.rotateNext())
        self.assertEqual(len(calls), 1)

    def test_rotate_next_pushes_then_retries_then_reports_failure(self) -> None:
        self._seed_playlist(["111"])
        attempts = []
        _API.next_wallpaper = lambda: (attempts.append(1), {"id": 1, "ok": False, "error": "rotation set is empty"})[1]
        self.assertFalse(self.backend.rotateNext())
        self.assertEqual(len(attempts), 2, "one retry after the sync push")
        self.assertEqual(len(self.pushes), 1, "the retry was preceded by a push")

    def test_prev_honest_empty_history_is_a_no(self) -> None:
        _API.prev_wallpaper = lambda: {"id": 1, "ok": False, "error": "history is empty"}
        self.assertFalse(self.backend.rotatePrev())

    def test_first_sight_pushes_once_and_rearrival_does_not(self) -> None:
        self._seed_playlist(["111"])
        api_state = {"api": 1, "pid": 100, "uptime_s": 5, "screens": {"DP-1": "/x/111"},
                     "current": {"id": "111", "ui_id": "111"}, "rotation": {}}
        _API.status = lambda: dict(api_state)
        self.backend.status()
        first = len(self.pushes)
        self.assertGreaterEqual(first, 1, "first sighting pushes the panel's policy")
        self.backend.status()
        self.assertEqual(len(self.pushes), first, "same pid, no re-push")
        api_state["pid"] = 200
        self.backend.status()
        self.assertEqual(len(self.pushes), first,
                         "engine re-arrival must NOT re-push: the engine restores its own "
                         "state and an auto re-push would feed a crash loop")


    def test_fullscreen_behavior_derives_from_legacy_policy(self) -> None:
        # an install predating the control stores "" and must keep its old behavior
        settings.save({"PAUSE_RECOVERY_ACTION": "pause",
                       "PAUSE_RECOVERY_CONDITION": "fullscreen"})
        self.assertEqual(models.resolve_fullscreen_behavior(settings.load()), "pause")
        settings.save({"PAUSE_RECOVERY_CONDITION": "off"})
        self.assertEqual(models.resolve_fullscreen_behavior(settings.load()), "off")

    def test_fullscreen_behavior_explicit_setting_wins(self) -> None:
        settings.save({"FULLSCREEN_BEHAVIOR": "stop",
                       "PAUSE_RECOVERY_CONDITION": "off"})
        self.assertEqual(models.resolve_fullscreen_behavior(settings.load()), "stop")

    def test_fullscreen_per_wallpaper_conf_opts_in_and_out(self) -> None:
        settings.save({"FULLSCREEN_BEHAVIOR": "stop"})
        s = settings.load()
        self.assertEqual(models.resolve_fullscreen_behavior(s, {}), "stop", "absent inherits")
        self.assertEqual(models.resolve_fullscreen_behavior(s, {"FULLSCREEN_PAUSE": ""}), "stop")
        self.assertEqual(models.resolve_fullscreen_behavior(s, {"FULLSCREEN_PAUSE": "false"}), "off",
                         "an opted-out wallpaper keeps playing")
        settings.save({"FULLSCREEN_BEHAVIOR": "off"})
        self.assertEqual(models.resolve_fullscreen_behavior(settings.load(), {"FULLSCREEN_PAUSE": "true"}),
                         "pause", "opting in with nothing global means the historical meaning")

    def test_show_args_carry_behavior_and_truthful_alias(self) -> None:
        settings.save({"FULLSCREEN_BEHAVIOR": "stop"})
        wp.save("111", {"SPEED": 1.0})
        _, args = models.resolve_show_args("111")
        self.assertEqual(args["fullscreen_behavior"], "stop")
        self.assertTrue(args["fullscreen_pause"], "the leg-A alias stays truthful")
        settings.save({"FULLSCREEN_BEHAVIOR": "off"})
        _, args = models.resolve_show_args("111")
        self.assertEqual(args["fullscreen_behavior"], "off")
        self.assertFalse(args["fullscreen_pause"])

    def test_setting_the_mode_pushes_it_live_and_refreshes_rotation(self) -> None:
        self._seed_playlist(["111"])
        sent: list[str] = []
        _API.set_fullscreen = lambda behavior: sent.append(behavior) or {"ok": True}
        before = len(self.pushes)
        self.backend.setSetting("FULLSCREEN_BEHAVIOR", "off")
        self.assertEqual(sent, ["off"], "the live verb carries the new mode")
        self.assertGreater(len(self.pushes), before,
                           "stored rotation entries carry their own copy - they must be refreshed")
        self.assertEqual(self.backend.fullscreenBehavior(), "off")

    def test_legacy_pause_keys_also_push_live(self) -> None:
        self._seed_playlist(["111"])
        sent: list[str] = []
        _API.set_fullscreen = lambda behavior: sent.append(behavior) or {"ok": True}
        self.backend.setSetting("PAUSE_RECOVERY_CONDITION", "fullscreen")
        self.assertEqual(sent, ["pause"], "the derived mode changed, so it must be pushed too")


    def _capture_globals(self) -> dict:
        got: dict = {}
        _API.set_fps = lambda fps: got.__setitem__("fps", fps) or {"ok": True}
        _API.set_parallax = lambda enabled: got.__setitem__("parallax", enabled) or {"ok": True}
        _API.set_particles = lambda enabled: got.__setitem__("particles", enabled) or {"ok": True}
        _API.set_fullscreen_ignore = lambda ids: got.__setitem__("ignore", ids) or {"ok": True}
        _API.set_fullscreen = lambda behavior: got.__setitem__("behavior", behavior) or {"ok": True}
        return got

    def test_live_globals_push_effective_values(self) -> None:
        got = self._capture_globals()
        settings.save({"ENGINE_FPS": "60", "PARALLAX_DEFAULT": True,
                       "PARTICLES_DEFAULT": False, "OVERRIDE_PARALLAX_OFF": False})
        self.backend._push_live_globals()
        self.assertEqual(got["fps"], 60)
        self.assertTrue(got["parallax"])
        self.assertFalse(got["particles"], "the setting is the source of truth, not the default")

    def test_session_override_beats_parallax_default(self) -> None:
        got = self._capture_globals()
        settings.save({"PARALLAX_DEFAULT": True, "OVERRIDE_PARALLAX_OFF": True})
        self.backend._push_live_globals()
        self.assertFalse(got["parallax"], "the deck override wins over the global default")

    def test_empty_fps_pushes_nothing(self) -> None:
        got = self._capture_globals()
        settings.save({"ENGINE_FPS": ""})
        self.backend._push_live_globals()
        self.assertNotIn("fps", got, "empty means 'whatever the engine launched with'")

    def test_fps_is_clamped_to_the_engine_range(self) -> None:
        got = self._capture_globals()
        settings.save({"ENGINE_FPS": "9000"})
        self.backend._push_live_globals()
        self.assertEqual(got["fps"], 480, "the dispatcher would reject an out-of-range value")

    def test_changing_a_global_setting_pushes_it(self) -> None:
        got = self._capture_globals()
        self.backend.setSetting("PARTICLES_DEFAULT", False)
        self.assertIn("particles", got)
        self.assertFalse(got["particles"])

    def test_ignore_list_reads_the_blacklist_file(self) -> None:
        from lwe_ui.storage import paths
        p = paths.config_dir() / "pause-blacklist.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# comment\nsteam\n\n  org.mozilla.firefox  \n", encoding="utf-8")
        self.assertEqual(self.backend._fullscreen_ignore_ids(), ["steam", "org.mozilla.firefox"],
                         "comments and blank lines dropped, entries stripped")

    def test_panel_start_arms_the_globals_once(self) -> None:
        # the engine restores its own globals on restart (state persistence); the
        # panel arms them exactly once per panel life, on first sight of an engine
        got = self._capture_globals()
        self._seed_playlist(["111"])
        settings.save({**settings.load(), "PARTICLES_DEFAULT": False, "ENGINE_FPS": "24"})
        api_state = {"api": 1, "pid": 100, "uptime_s": 5, "screens": {"DP-1": "/x/111"},
                     "current": {"id": "111", "ui_id": "111"}, "rotation": {}}
        _API.status = lambda: dict(api_state)
        self.backend.status()
        self.assertEqual(got.get("fps"), 24)
        self.assertFalse(got.get("particles"))
        self.assertIn("behavior", got, "the fullscreen policy is armed too")
        got.clear()
        api_state["pid"] = 200
        self.backend.status()
        self.assertEqual(got, {}, "re-arrival pushes nothing - the engine restored itself")


    def _live_dev(self):
        """A DevBridge whose slot A reads as a live, API-capable exhibit."""
        from lwe_ui import dev as devmod
        settings.save(settings.load())
        d = devmod.DevBridge()
        devmod.DevBridge._dev_outputs = lambda self: ["TEST-OUT"]
        d.slots["A"].alive = lambda: True
        d.slots["A"].api = True
        return d

    def test_isolator_pushes_skip_to_the_exhibit_socket(self) -> None:
        d = self._live_dev()
        sent = []
        _API.set_skip = lambda ids, sock=None: sent.append((list(ids), str(sock))) or {"ok": True}
        d.setObjectsOn("A", ["12", "13"], False)
        d.setObjectOn("A", "12", True)
        self.assertEqual([s[0] for s in sent], [[12, 13], [13]])
        self.assertTrue(all(s[1].endswith("exhibit-a.sock") for s in sent),
                        "the isolator addresses the exhibit's own socket, never the daemon's")
        self.assertFalse(d.liveControl("B"))
        d.setObjectsOn("B", ["12"], False)
        self.assertEqual(len(sent), 2, "a stopped side keeps the edit for its launch, nothing is pushed")
        self.assertEqual(d.slots["B"].skip, {"12"})
        _API.set_skip = lambda ids, sock=None: {"ok": False, "error": "no"}
        d.setObjectOn("A", "13", False)
        self.assertTrue(d.slots["A"].relaunching, "a refused push falls back to a relaunch")
        d._relaunch_timers["A"].stop()
        d.slots["A"].relaunching = False
        _API.set_skip = lambda ids, **kw: None

    def test_live_instrument_flips_on_the_exhibit(self) -> None:
        d = self._live_dev()
        sent = []
        _API.set_instrument = lambda name, enabled, sock=None: sent.append((name, enabled, str(sock))) or {"ok": True}
        d.setInstrument("A", "LWE_PARTSTATS", True)
        self.assertEqual(sent, [("LWE_PARTSTATS", True, str(d.slots["A"].sock_path()))])
        self.assertFalse(d.slots["A"].relaunching, "a live instrument never relaunches")
        d.setInstrument("A", "LWE_LIGHTDUMP", True)
        self.assertTrue(d.slots["A"].relaunching, "an env-class instrument relaunches the side")
        d._relaunch_timers["A"].stop()
        d.slots["A"].relaunching = False
        del _API.set_instrument

    def test_scene_switch_is_a_show_on_the_exhibit(self) -> None:
        d = self._live_dev()
        sent = []
        _API.show = lambda wid, wait_done=False, **kw: sent.append((wid, str(kw.get("sock")))) or {"ok": True}
        d.setScene("A", "2114739882")
        self.assertEqual(sent, [("2114739882", str(d.slots["A"].sock_path()))])
        self.assertFalse(d.slots["A"].relaunching)
        d.setScene("A", "probe:cal")
        self.assertTrue(d.slots["A"].relaunching, "a probe path cannot be shown live, so it relaunches")
        d._relaunch_timers["A"].stop()
        d.slots["A"].relaunching = False
        _API.show = lambda wid, wait_done=False, **kw: None

    def test_status_never_shells_out_to_the_retired_watcher(self) -> None:
        which_calls = []
        real_which = models.shutil.which
        models.shutil.which = lambda n: which_calls.append(n) or real_which(n)
        _API.status = lambda: {"api": 1, "pid": 7, "screens": {"DP-1": "/x/111"},
                               "current": {"id": "111", "ui_id": "111"},
                               "rotation": {"next_in_s": 42, "interval_s": 900,
                                            "label": "chill", "next_up": "222"}}
        try:
            st = self.backend.status()
        finally:
            models.shutil.which = real_which
        self.assertNotIn("lwe-wallpaper", which_calls, "must not look up the retired script")
        self.assertEqual(st["state"], "up")
        self.assertEqual(st["current"], "111")
        self.assertEqual(st["next_in"], 42)
        self.assertEqual(st["interval"], 900)
        self.assertEqual(st["playlist"], "chill")

    def test_now_playing_prefers_ui_id(self) -> None:
        _API.status = lambda: {"api": 1, "pid": 100, "screens": {"DP-1": "/x/2185197772"},
                               "current": {"id": "2185197772", "ui_id": "3410648253"},
                               "rotation": {}}
        st = self.backend.status()
        self.assertEqual(st["current"], "3410648253", "the preset tile, not the base")


if __name__ == "__main__":
    unittest.main(verbosity=1)
