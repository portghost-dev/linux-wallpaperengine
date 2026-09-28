"""The change runner: every door's change as the marker, the store write, then the change's own push.

A change of ENGINE_VOLUME sends the entry refresh and one set-volume resolved for the wallpaper on
screen, and while the engine is frozen only ENGINE_TIMESCALE and a wallpaper's SPEED send a
set-speed that unfreezes it. Each row sends exactly its requests with its own verb last, and a
wallpaper not on screen gets its entry refresh and no live verb. With a marker already set the
bundle goes first and the change last, and the marker clears only when both ended ok. A death
between the marker and the store write, or between the store write and the push, leaves the marker
for the next sync_all, which delivers the stored value and clears it. A refused write sends nothing
and reaches the caller; a manual switch while the engine is away and the schedule is on is refused
before any lock; a busy sync lock or another build's engine leaves the change pending with the
marker kept. The engine is an api_client recorder with a scripted status; every child process
gets an environment built from scratch.

Run: PYTHONPATH=src python3 tests/test_change_runner.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import contextlib
import copy
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from lwe_ui import version  # noqa: E402
from lwe_ui.engine import daemon_unit, marker, push, resolve  # noqa: E402
from lwe_ui.storage import lock, paths, playlists, settings, wp  # noqa: E402

OK = {"id": 1, "ok": True, "status": "done", "result": {}}
SCHEDULE = "07:00=main;20:00=night"
BUNDLE = ["playlist_set", "playlist_set", "schedule_set", "lanes_set", "set_fps", "set_parallax",
          "set_particles", "set_fullscreen_ignore", "set_app_conditions", "set_fullscreen", "set_speed",
          "set_volume", "set_mouse", "set_audio", "set_tuning", "set_fit", "set_skip"]

CHILD = r"""
import os, sys, time
from pathlib import Path
from lwe_ui.engine import push
from lwe_ui.storage import lock, settings

job, flag = sys.argv[1], Path(sys.argv[2])
if job == "hold-sync":
    with lock.held("sync"):
        flag.touch()
        time.sleep(60)
    raise SystemExit(0)

def write():
    if job == "after":
        settings.update({"ENGINE_VOLUME": 40})
    flag.touch()
    os._exit(0)

push.run_change(("settings",), write, [("live", "ENGINE_VOLUME")])
"""


class Recorder:
    """A recording api_client: every api_client verb is recorded as (verb, args, kwargs) and answered
    from a per-verb script, ok by default; a step that is a class name answers None with that class.
    status answers the scripted status, or None with status_class."""

    def __init__(self, status: dict | None, status_class: str = "ok") -> None:
        self.calls: list[tuple] = []
        self.status_reply = status
        self.status_class = status_class
        self.script: dict[str, list] = {}
        self._last: str | None = None

    def answer(self, verb: str, *steps) -> None:
        self.script.setdefault(verb, []).extend(steps)

    def status(self, sock=None):
        self.calls.append(("status", (), {}))
        if self.status_reply is None:
            self._last = self.status_class
            return None
        self._last = "ok"
        return copy.deepcopy(self.status_reply)

    def last_class(self):
        return self._last

    def __getattr__(self, verb: str):
        if verb.startswith("_"):
            raise AttributeError(verb)

        def call(*args, **kwargs):
            self.calls.append((verb, args, kwargs))
            steps = self.script.get(verb)
            step = steps.pop(0) if steps else OK
            if isinstance(step, str):
                self._last = step
                return None
            self._last = "ok" if step.get("ok") else "refused"
            return step
        return call


def status(current: str = "111", speed=1.0, schedule_on: bool = False, **extra) -> dict:
    out = {"api": 1, "version": version.panel_stamp(), "pid": 4242,
           "current": {"id": current, "ui_id": current, "title": ""},
           "schedule": {"enabled": schedule_on}, "lanes": [{"id": "all", "playlist": "main"}], **extra}
    if speed is not None:
        out["speed"] = speed
    return out


def sent(rec: Recorder) -> list[tuple]:
    """The requests after the status reads, each as (verb, the value that tells it apart)."""
    out = []
    for verb, args, kwargs in rec.calls:
        if verb == "status":
            continue
        if verb in ("playlist_set", "set_volume", "set_speed", "set_fps", "set_parallax", "set_particles",
                    "set_audio", "set_mouse", "set_fullscreen", "set_skip", "show"):
            out.append((verb, args[0]))
        elif verb == "lanes_set":
            out.append((verb, args[0]))
        elif verb in ("set_tuning", "set_fit"):
            out.append((verb, kwargs))
        else:
            out.append((verb, args))
    return out


class ChangeRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.env_writes: list[int] = []
        patcher = mock.patch.object(daemon_unit, "write_env",
                                    lambda *a, **k: self.env_writes.append(1) or "written")
        patcher.start()
        self.addCleanup(patcher.stop)
        self._fresh()

    def _fresh(self) -> None:
        """A new home: main (111 222) active, night (333) scheduled beside it, the schedule off."""
        self.home = Path(tempfile.mkdtemp(prefix="lwe-runner-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        for key, sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"),
                         ("XDG_DATA_HOME", "d")):
            os.environ[key] = str(self.home / sub) if sub else str(self.home)
        settings.save(settings.load())
        playlists.save("main", {"NAME": "Main", "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min",
                                "MEMBERS": "111 222"})
        playlists.save("night", {"NAME": "Night", "MODE": "sequential", "INTERVAL": 600, "UNIT": "min",
                                 "MEMBERS": "333"})
        settings.update({"ACTIVE_PLAYLIST": "main", "SCHEDULE": SCHEDULE, "SCHEDULE_ENABLED": False})

    @contextlib.contextmanager
    def engine(self, reply: dict | None, status_class: str = "ok"):
        rec = Recorder(reply, status_class)
        with mock.patch.object(push, "api_client", rec):
            yield rec

    def _env(self) -> dict[str, str]:
        for d in ("bin", "rt"):
            (self.home / d).mkdir(exist_ok=True)
        return {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": os.environ["XDG_CONFIG_HOME"],
            "XDG_STATE_HOME": os.environ["XDG_STATE_HOME"],
            "XDG_DATA_HOME": os.environ["XDG_DATA_HOME"],
            "XDG_RUNTIME_DIR": str(self.home / "rt"),
            "LWE_SOCKET": str(self.home / "rt" / "engine.sock"),
            "LWE_SANDBOX": "1",
            "PATH": str(self.home / "bin"),
            "PYTHONPATH": str(SRC),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def _child(self, job: str) -> tuple[subprocess.Popen, Path]:
        flag = self.home / f"flag-{job}"
        child = subprocess.Popen([sys.executable, "-c", CHILD, job, str(flag)], env=self._env(),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self._reap, child)
        return child, flag

    def _reap(self, child: subprocess.Popen) -> None:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=30)

    def _volume(self, wid: str) -> int:
        return resolve.resolve_show_args(wid)[1]["volume"]

    def test_a_volume_change_sends_the_entry_refresh_and_one_resolved_set_volume(self) -> None:
        wp.write_keys("111", {"BG": "111", "VOLUME": "70"})
        with self.engine(status()) as rec:
            outcome = push.run_change(("settings",), lambda: settings.update({"ENGINE_VOLUME": 40}),
                                      [(push.SETTING_ROWS["ENGINE_VOLUME"], "ENGINE_VOLUME")])
        self.assertEqual(outcome, push.Outcome("applied"))
        self.assertEqual(sent(rec), [("playlist_set", "main"), ("playlist_set", "night"), ("set_volume", 70)])
        self.assertEqual(marker.read()["classes"], [])

    def test_while_frozen_only_the_global_speed_and_a_wallpaper_speed_unfreeze(self) -> None:
        cases = (
            (("live", "ENGINE_TIMESCALE"), None, lambda: settings.update({"ENGINE_TIMESCALE": 2.0})),
            (("wp_live", "SPEED"), "111", lambda: wp.update_set("111", {"SPEED": 3.0})),
            (("live", "ENGINE_VOLUME"), None, lambda: settings.update({"ENGINE_VOLUME": 30})),
            (("live", "FULLSCREEN_BEHAVIOR"), None, lambda: settings.update({"FULLSCREEN_BEHAVIOR": "pause"})),
            (("verb", "ENGINE_FPS"), None, lambda: settings.update({"ENGINE_FPS": 30})),
            (("pause", "ROTATION_ENABLED"), None, lambda: settings.update({"ROTATION_ENABLED": False})),
            (("next_show", "ENGINE_SCALING"), None, lambda: settings.update({"ENGINE_SCALING": "fill"})),
            (("tuning", "ENGINE_AUDIO_GAIN"), None, lambda: settings.update({"ENGINE_AUDIO_GAIN": 2.0})),
            (("wp_live", "VOLUME"), "111", lambda: wp.update_set("111", {"VOLUME": 20})),
            (("wp_build", "SCALING"), "111", lambda: wp.update_set("111", {"SCALING": "fit"})),
        )
        for row, wid, write in cases:
            with self.subTest(row=row):
                with self.engine(status(speed=0.0)) as rec:
                    push.run_change(("overrides",) if wid else ("settings",), write, [row], wid=wid)
                speeds = [args[0] for verb, args, _kw in rec.calls if verb == "set_speed"]
                if row[1] in ("ENGINE_TIMESCALE", "SPEED"):
                    self.assertEqual(len(speeds), 1)
                    self.assertGreater(speeds[0], 0)
                else:
                    self.assertTrue(all(value == 0 for value in speeds), speeds)

    def test_each_row_sends_exactly_its_requests_with_its_own_verb_last(self) -> None:
        def seed() -> None:
            self._fresh()
            self.env_writes.clear()
            playlists.save("other", {"NAME": "Other", "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min",
                                     "MEMBERS": "444"})
            wp.write_keys("111", {"BG": "111", "SKIP": "7 9"})
            (paths.config_dir() / "pause-blacklist.txt").write_text("steam\n", encoding="utf-8")
            (paths.config_dir() / "app-condition.txt").write_text("obs\n", encoding="utf-8")
        entries = push._schedule_entries()
        cases = (
            ("active switch", ("active", "ACTIVE_PLAYLIST"), dict(slug="night"), status(),
             lambda: settings.update({"ACTIVE_PLAYLIST": "night"}),
             [("playlist_set", "night"), ("lanes_set", [{"id": "all", "playlist": "night", "enabled": True}])]),
            ("active switch, schedule on", ("active", "ACTIVE_PLAYLIST"), dict(slug="night"),
             status(schedule_on=True), lambda: settings.update({"ACTIVE_PLAYLIST": "night"}),
             [("playlist_set", "night"),
              ("lanes_set", [{"id": "all", "playlist": "night", "enabled": True, "manual": True}])]),
            ("policy", ("policy", "MODE"), dict(slug="main"), status(),
             lambda: playlists.update("main", {"MODE": "static"}),
             [("playlist_set", "main"), ("lanes_set", [{"id": "all", "enabled": False}])]),
            ("members of the bound playlist", ("members", "MEMBERS"), dict(slug="main"), status(),
             lambda: playlists.update("main", {"MEMBERS": "111 222 555"}),
             [("playlist_set", "main"), ("lanes_set", [{"id": "all", "enabled": True}])]),
            ("members of a scheduled playlist", ("members", "MEMBERS"), dict(slug="night"), status(),
             lambda: playlists.update("night", {"MEMBERS": "333 666"}), [("playlist_set", "night")]),
            ("members of a playlist the engine does not hold", ("members", "MEMBERS"), dict(slug="other"),
             status(), lambda: playlists.update("other", {"MEMBERS": "444 777"}), []),
            ("pause", ("pause", "ROTATION_ENABLED"), {}, status(),
             lambda: settings.update({"ROTATION_ENABLED": False}), [("lanes_set", [{"id": "all", "enabled": False}])]),
            ("schedule", ("schedule", "SCHEDULE_ENABLED"), {}, status(),
             lambda: settings.update({"SCHEDULE_ENABLED": True}),
             [("playlist_set", "main"), ("playlist_set", "night"), ("schedule_set", (True, entries))]),
            ("fps", ("verb", "ENGINE_FPS"), {}, status(), lambda: settings.update({"ENGINE_FPS": 30}),
             [("set_fps", 30)]),
            ("parallax", ("verb", "OVERRIDE_PARALLAX_OFF"), {}, status(),
             lambda: settings.update({"OVERRIDE_PARALLAX_OFF": True}), [("set_parallax", False)]),
            ("particles", ("verb", "PARTICLES_DEFAULT"), {}, status(),
             lambda: settings.update({"PARTICLES_DEFAULT": False}), [("set_particles", False)]),
            ("app conditions", ("verb", "app-condition.txt"), {}, status(), lambda: None,
             [("set_app_conditions", (["obs"], "off"))]),
            ("fullscreen exceptions", ("verb", "pause-blacklist.txt"), {}, status(), lambda: None,
             [("set_fullscreen_ignore", (["steam"],))]),
            ("volume", ("live", "ENGINE_VOLUME"), {}, status(), lambda: settings.update({"ENGINE_VOLUME": 30}),
             [("playlist_set", "main"), ("playlist_set", "night"), ("set_volume", 30)]),
            ("mute", ("live", "OVERRIDE_MUTE"), {}, status(), lambda: settings.update({"OVERRIDE_MUTE": True}),
             [("playlist_set", "main"), ("playlist_set", "night"), ("set_volume", 0)]),
            ("speed", ("live", "ENGINE_TIMESCALE"), {}, status(),
             lambda: settings.update({"ENGINE_TIMESCALE": 2.5}),
             [("playlist_set", "main"), ("playlist_set", "night"), ("set_speed", 2.5)]),
            ("audio", ("live", "OVERRIDE_AUDIO_OFF"), {}, status(),
             lambda: settings.update({"OVERRIDE_AUDIO_OFF": True}),
             [("playlist_set", "main"), ("playlist_set", "night"), ("set_audio", False)]),
            ("mouse", ("live", "MOUSE_DEFAULT"), {}, status(), lambda: settings.update({"MOUSE_DEFAULT": True}),
             [("playlist_set", "main"), ("playlist_set", "night"), ("set_mouse", True)]),
            ("fullscreen", ("live", "FULLSCREEN_BEHAVIOR"), {}, status(),
             lambda: settings.update({"FULLSCREEN_BEHAVIOR": "stop"}),
             [("playlist_set", "main"), ("playlist_set", "night"), ("set_fullscreen", "stop")]),
            ("fullscreen while idle", ("live", "FULLSCREEN_BEHAVIOR"), {}, status(current=""),
             lambda: settings.update({"FULLSCREEN_BEHAVIOR": "stop"}),
             [("playlist_set", "main"), ("playlist_set", "night"), ("set_fullscreen", "stop")]),
            ("volume while idle", ("live", "ENGINE_VOLUME"), {}, status(current=""),
             lambda: settings.update({"ENGINE_VOLUME": 30}), [("playlist_set", "main"), ("playlist_set", "night")]),
            ("speed while idle", ("live", "ENGINE_TIMESCALE"), {}, status(current=""),
             lambda: settings.update({"ENGINE_TIMESCALE": 2.5}),
             [("playlist_set", "main"), ("playlist_set", "night"), ("set_speed", 2.5)]),
            ("audio gain", ("tuning", "ENGINE_AUDIO_GAIN"), {}, status(),
             lambda: settings.update({"ENGINE_AUDIO_GAIN": 2.0}),
             [("set_tuning", {"audio_gain": 2.0, "classic_k": 0.7, "classic_exp": 2.6})]),
            ("scaling", ("next_show", "ENGINE_SCALING"), {}, status(),
             lambda: settings.update({"ENGINE_SCALING": "fill"}), [("playlist_set", "main"), ("playlist_set", "night")]),
            ("layer", ("restart", "ENGINE_LAYER"), {}, status(), lambda: settings.update({"ENGINE_LAYER": "top"}),
             []),
            ("wallpaper volume", ("wp_live", "VOLUME"), dict(wid="111"), status(),
             lambda: wp.update_set("111", {"VOLUME": 25}), [("playlist_set", "main"), ("set_volume", 25)]),
            ("wallpaper speed", ("wp_live", "SPEED"), dict(wid="111"), status(),
             lambda: wp.update_set("111", {"SPEED": 1.5}), [("playlist_set", "main"), ("set_speed", 1.5)]),
            ("wallpaper fit", ("wp_live", "FIT_ZOOM"), dict(wid="111"), status(),
             lambda: wp.update_set("111", {"FIT_ZOOM": 1.5}),
             [("playlist_set", "main"),
              ("set_fit", {"layer": "wallpaper", "id": "111", "zoom": 1.5, "pan_x": 0.0, "pan_y": 0.0})]),
            ("wallpaper skips", ("wp_live", "SKIP"), dict(wid="111"), status(),
             lambda: wp.update_set("111", {"SKIP": "7 9 11"}), [("playlist_set", "main"), ("set_skip", [7, 9, 11])]),
            ("wallpaper audio gain", ("wp_live", "AUDIO_GAIN"), dict(wid="111"), status(),
             lambda: wp.update_set("111", {"AUDIO_GAIN": 5.0}),
             [("set_tuning", {"audio_gain": 5.0, "classic_k": 0.7, "classic_exp": 2.6})]),
            ("wallpaper scaling", ("wp_build", "SCALING"), dict(wid="111"), status(),
             lambda: wp.update_set("111", {"SCALING": "fit"}),
             [("playlist_set", "main"), ("show", "111"),
              ("set_tuning", {"audio_gain": 3.0, "classic_k": 0.7, "classic_exp": 2.6}),
              ("set_skip", [7, 9]), ("set_speed", 1.0)]),
            ("volume of a wallpaper not on screen", ("wp_live", "VOLUME"), dict(wid="333"), status(),
             lambda: wp.update_set("333", {"VOLUME": 25}), [("playlist_set", "night")]),
            ("scaling of a wallpaper not on screen", ("wp_build", "SCALING"), dict(wid="333"), status(),
             lambda: wp.update_set("333", {"SCALING": "fit"}), [("playlist_set", "night")]),
        )
        for name, row, where, reply, write, expected in cases:
            with self.subTest(case=name):
                seed()
                locks = ("overrides",) if "wid" in where else ("playlists",) if row[0] in ("policy", "members") \
                    else ("settings",)
                with self.engine(reply) as rec:
                    outcome = push.run_change(locks, write, [row], **where)
                self.assertEqual(sent(rec), expected)
                env = row[0] in ("tuning", "restart")
                self.assertEqual((outcome.kind, outcome.env, len(self.env_writes)),
                                 ("applied", "written" if env else None, 1 if env else 0))
                if row[0] == "restart":
                    self.assertIsNone(marker.read()["generation"])
                else:
                    self.assertEqual(marker.read()["classes"], [])

    def test_with_a_marker_set_the_bundle_goes_first_and_the_clear_waits_for_both(self) -> None:
        for name, script, cleared in (("both ok", {}, True),
                                      ("the bundle refused", {"set_fps": [{"id": 1, "ok": False, "error": "no"}]},
                                       False),
                                      ("the change refused", {"set_volume": [OK, {"id": 1, "ok": False, "error": "no"}]},
                                       False)):
            with self.subTest(case=name):
                marker.ensure(("BUNDLE",))
                with self.engine(status()) as rec:
                    for verb, steps in script.items():
                        rec.answer(verb, *steps)
                    outcome = push.run_change(("settings",), lambda: settings.update({"ENGINE_VOLUME": 30}),
                                              [("live", "ENGINE_VOLUME")])
                verbs = [verb for verb, _value in sent(rec)]
                self.assertEqual(verbs, BUNDLE + ["playlist_set", "playlist_set", "set_volume"])
                self.assertEqual(sent(rec)[-1], ("set_volume", 30))
                self.assertEqual(outcome.kind, "applied" if cleared else "refused")
                self.assertEqual(marker.read()["classes"], [] if cleared else ["BUNDLE"])

    def test_a_death_around_the_store_write_leaves_the_marker_for_the_next_sync(self) -> None:
        for job, stored in (("before", 15), ("after", 40)):
            with self.subTest(death=job):
                settings.update({"ENGINE_VOLUME": 15})
                child, flag = self._child(job)
                child.communicate(timeout=60)
                self.assertEqual(child.returncode, 0)
                self.assertTrue(flag.exists())
                self.assertEqual(marker.read()["classes"], ["BUNDLE"])
                self.assertEqual(settings.load()["ENGINE_VOLUME"], stored)
                with self.engine(status()) as rec:
                    outcome = push.sync_all("command")
                self.assertEqual(outcome.kind, "applied")
                self.assertIn(("set_volume", stored), sent(rec))
                self.assertEqual(marker.read()["classes"], [])

    def test_a_refused_write_sends_nothing_and_reaches_the_caller(self) -> None:
        def broken() -> None:
            raise ValueError("ENGINE_VOLUME: the line opens a quote it does not close")
        with self.engine(status()) as rec:
            with self.assertRaises(ValueError):
                push.run_change(("settings",), broken, [("live", "ENGINE_VOLUME")])
        self.assertEqual(sent(rec), [])
        ready, release = threading.Event(), threading.Event()

        def hold() -> None:
            with lock.held("settings"):
                ready.set()
                release.wait(10)
        holder = threading.Thread(target=hold)
        holder.start()
        try:
            self.assertTrue(ready.wait(10))
            with mock.patch.object(lock, "LOCK_WAIT_S", 0.2), self.engine(status()) as rec:
                with self.assertRaises(lock.StoreBusy):
                    push.run_change(("settings",), lambda: settings.update({"ENGINE_VOLUME": 30}),
                                    [("live", "ENGINE_VOLUME")])
        finally:
            release.set()
            holder.join(10)
        self.assertEqual(sent(rec), [])
        self.assertNotEqual(settings.load()["ENGINE_VOLUME"], 30)

    def test_a_manual_switch_while_away_with_the_schedule_on_is_refused_before_any_lock(self) -> None:
        settings.update({"SCHEDULE_ENABLED": True})
        before = paths.settings_file().read_bytes()
        taken: list[str] = []
        real_held = lock.held

        def recording(store: str, *args, **kwargs):
            taken.append(store)
            return real_held(store, *args, **kwargs)
        for status_class in ("away", "unresponsive"):
            with self.subTest(engine=status_class):
                wrote: list[int] = []
                with mock.patch.object(lock, "held", recording), self.engine(None, status_class) as rec:
                    with self.assertRaises(push.SwitchRefused):
                        push.run_change(("settings",), lambda: wrote.append(1), [("active", "ACTIVE_PLAYLIST")],
                                        slug="night", manual=True)
                self.assertEqual((taken, wrote, sent(rec)), ([], [], []))
                self.assertIsNone(marker.read()["generation"])
                self.assertEqual(paths.settings_file().read_bytes(), before)

    def test_a_busy_sync_lock_leaves_the_change_pending_after_about_two_seconds(self) -> None:
        child, flag = self._child("hold-sync")
        deadline = time.monotonic() + 30
        while not flag.exists():
            self.assertIsNone(child.poll(), "the child exited before it held sync")
            self.assertLess(time.monotonic(), deadline, "the child never held sync")
            time.sleep(0.005)
        start = time.monotonic()
        with self.engine(status()) as rec:
            outcome = push.run_change(("settings",), lambda: settings.update({"ENGINE_VOLUME": 30}),
                                      [("live", "ENGINE_VOLUME")])
        waited = time.monotonic() - start
        self.assertEqual(outcome, push.Outcome("pending", reason="busy"))
        self.assertGreaterEqual(waited, 1.9)
        self.assertLess(waited, 3.0)
        self.assertEqual(sent(rec), [])
        self.assertEqual(settings.load()["ENGINE_VOLUME"], 30)
        self.assertEqual(marker.read()["classes"], ["BUNDLE"])

    def test_another_builds_engine_inside_sync_leaves_the_change_pending(self) -> None:
        reply = status(version="0.0.1")
        with self.engine(reply) as rec:
            outcome = push.run_change(("settings",), lambda: settings.update({"ENGINE_VOLUME": 30}),
                                      [("live", "ENGINE_VOLUME")])
        refusal = version.running_refusal(reply, version.panel_stamp())
        self.assertEqual(outcome, push.Outcome("pending", reason="version", message=refusal))
        self.assertEqual([verb for verb, _a, _k in rec.calls], ["status", "status"])
        self.assertEqual(marker.read()["classes"], ["BUNDLE"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
