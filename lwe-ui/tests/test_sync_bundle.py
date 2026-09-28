"""sync_all: the bundle that rebuilds the engine from the store, and the reply classes it runs on.

Against the test's own socket server, request() records the four reply classes: nothing listening
is away, a silent server uncertain, ok false refused and ok true ok. sync_all sends its requests
in order; a refused playlist part stops only the requests that depend on it; an empty
schedule goes out disabled with no entries and the marker clears. While the engine reports speed 0
the bundle leaves set-speed out, and a status with no speed gets it. A CURRENT re-show whose done
never comes keeps CURRENT, and set-speed with the speed read inside sync follows the show either
way. A failed particle rebuild is a refusal that keeps the marker. A window run stops at its budget
between playlists and records what it sent, the next window run of that generation and engine pid
skips it, another pid empties the list, a stale generation records nothing, and a command run
ignores both. sync_all makes a marker with a fresh generation when none exists, and a failed run
keeps it. The engine is an api_client recorder with a scripted status unless a test names the
socket server.

Run: PYTHONPATH=src python3 tests/test_sync_bundle.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import contextlib
import copy
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _fake_engine

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from lwe_ui import api_client, version  # noqa: E402
from lwe_ui.engine import marker, push  # noqa: E402
from lwe_ui.storage import playlists, settings  # noqa: E402

OK = {"id": 1, "ok": True, "status": "done", "result": {}}
ORDER = ["playlist_set", "playlist_set", "schedule_set", "lanes_set", "set_fps", "set_parallax",
         "set_particles", "set_fullscreen_ignore", "set_app_conditions", "set_fullscreen", "set_speed",
         "set_volume", "set_mouse", "set_audio", "set_tuning", "set_fit", "set_skip"]


class Recorder:
    """A recording api_client: every api_client verb is recorded as (verb, args, kwargs) and answered
    from a per-verb script, ok by default; a step that is a class name answers None with that class,
    and a hook runs before each answer of its verb. status answers the scripted status."""

    def __init__(self, status: dict) -> None:
        self.calls: list[tuple] = []
        self.status_reply = status
        self.script: dict[str, list] = {}
        self.hooks: dict[str, object] = {}
        self._last: str | None = None

    def answer(self, verb: str, *steps) -> None:
        self.script.setdefault(verb, []).extend(steps)

    def status(self, sock=None):
        self.calls.append(("status", (), {}))
        self._last = "ok"
        return copy.deepcopy(self.status_reply)

    def last_class(self):
        return self._last

    def __getattr__(self, verb: str):
        if verb.startswith("_"):
            raise AttributeError(verb)

        def call(*args, **kwargs):
            self.calls.append((verb, args, kwargs))
            if verb in self.hooks:
                self.hooks[verb]()
            steps = self.script.get(verb)
            step = steps.pop(0) if steps else OK
            if isinstance(step, str):
                self._last = step
                return None
            self._last = "ok" if step.get("ok") else "refused"
            return step
        return call

    def verbs(self) -> list[str]:
        return [verb for verb, _a, _k in self.calls if verb != "status"]

    def playlists(self) -> list[str]:
        return [args[0] for verb, args, _k in self.calls if verb == "playlist_set"]


class Clock:
    """push's time: monotonic() reads now, sleep() moves it."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def status(speed=1.0, pid: int = 4242, current: str = "111", **extra) -> dict:
    out = {"api": 1, "version": version.panel_stamp(), "pid": pid,
           "current": {"id": current, "ui_id": current, "title": ""},
           "schedule": {"enabled": False}, "lanes": [{"id": "all", "playlist": "main"}], **extra}
    if speed is not None:
        out["speed"] = speed
    return out


class SyncBundleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="lwe-bundle-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        for key, sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"),
                         ("XDG_DATA_HOME", "d")):
            os.environ[key] = str(self.home / sub) if sub else str(self.home)
        (self.home / "rt").mkdir()
        self.sock = self.home / "rt" / "engine.sock"
        settings.save(settings.load())
        for slug, members in (("main", "111 222"), ("night", "333"), ("extra", "444")):
            playlists.save(slug, {"NAME": slug, "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min",
                                  "MEMBERS": members})
        settings.update({"ACTIVE_PLAYLIST": "main", "SCHEDULE": "07:00=main;20:00=night",
                         "SCHEDULE_ENABLED": False})

    @contextlib.contextmanager
    def engine(self, reply: dict):
        rec = Recorder(reply)
        with mock.patch.object(push, "api_client", rec):
            yield rec

    @contextlib.contextmanager
    def socket_engine(self, **fields):
        with _fake_engine.FakeEngine(self.sock, **fields) as engine, \
                mock.patch.dict(os.environ, {"LWE_SOCKET": str(self.sock)}):
            yield engine

    def test_request_records_the_four_reply_classes(self) -> None:
        with mock.patch.dict(os.environ, {"LWE_SOCKET": str(self.sock)}):
            self.assertIsNone(api_client.request("status"))
            self.assertEqual(api_client.last_class(), "away")
            self.assertEqual(push.read_status(), ("away", None))
        with self.socket_engine() as engine, mock.patch.object(api_client, "_TIMEOUT", 0.2):
            engine.script("status", _fake_engine.silent())
            engine.script("set-fps", _fake_engine.fail("fps out of range"))
            self.assertEqual(push.read_status(), ("unresponsive", None))
            self.assertEqual(api_client.last_class(), "uncertain")
            self.assertEqual(api_client.request("set-fps", {"fps": 999}),
                             {"id": 1, "ok": False, "error": "fps out of range"})
            self.assertEqual(api_client.last_class(), "refused")
            self.assertEqual(api_client.request("set-fps", {"fps": 30}), OK)
            self.assertEqual(api_client.last_class(), "ok")
            cls, reply = push.read_status()
            self.assertEqual((cls, reply["version"]), ("ok", version.panel_stamp()))

    def test_the_bundle_goes_in_order_and_a_refused_part_stops_only_its_dependents(self) -> None:
        with self.engine(status(schedule={"enabled": True})) as rec:
            outcome = push.sync_all("command")
        self.assertEqual(outcome, push.Outcome("applied"))
        self.assertEqual(rec.verbs(), ORDER)
        self.assertEqual(rec.playlists(), ["main", "night"])
        self.assertIn(("lanes_set", ([{"id": "all", "playlist": "main", "enabled": True}],), {}), rec.calls)
        self.assertEqual(marker.read()["classes"], [])
        refusal = {"id": 1, "ok": False, "error": "unknown member"}
        for refused, gone in (("night", ["schedule_set"]), ("main", ["schedule_set", "lanes_set"])):
            with self.subTest(refused=refused):
                with self.engine(status()) as rec:
                    rec.answer("playlist_set", *([refusal] if refused == "main" else [OK, refusal]))
                    outcome = push.sync_all("command")
                self.assertEqual(outcome, push.Outcome("refused", message="unknown member"))
                self.assertEqual(rec.verbs(), [verb for verb in ORDER if verb not in gone])
                self.assertEqual(rec.playlists(), ["main", "night"])
                self.assertEqual(marker.read()["classes"], ["BUNDLE"])
                marker.clear(marker.generation())

    def test_an_empty_schedule_goes_out_disabled_and_the_marker_clears(self) -> None:
        settings.update({"SCHEDULE": ""})
        with self.engine(status()) as rec:
            outcome = push.sync_all("command")
        self.assertEqual(outcome, push.Outcome("applied"))
        self.assertEqual(rec.playlists(), ["main"])
        self.assertIn(("schedule_set", (False, []), {}), rec.calls)
        self.assertEqual(marker.read()["classes"], [])

    def test_a_frozen_engine_keeps_set_speed_out_and_a_status_without_speed_gets_it(self) -> None:
        for speed, expected in ((0.0, False), (None, True), (1.0, True)):
            with self.subTest(speed=speed):
                with self.engine(status(speed=speed)) as rec:
                    push.sync_all("command")
                self.assertEqual("set_speed" in rec.verbs(), expected)

    def test_a_current_reshow_keeps_the_freeze_and_keeps_current_until_its_done(self) -> None:
        for done, kind, classes in ((_fake_engine.done(accepted=True, delay=1.0), "uncertain",
                                     ["BUNDLE", "CURRENT"]),
                                    (_fake_engine.done(accepted=True), "applied", [])):
            with self.subTest(outcome=kind):
                with self.socket_engine(speed=0.0, current={"id": "111", "ui_id": "111", "title": ""}) as engine, \
                        mock.patch.object(api_client, "_DONE_TIMEOUT", 0.2):
                    engine.script("show", done)
                    outcome = push.sync_all("command", ("BUNDLE", "CURRENT"))
                    calls = [cmd for cmd, _args in engine.calls]
                self.assertEqual(outcome.kind, kind)
                self.assertEqual(marker.read()["classes"], classes)
                self.assertEqual(calls.count("show"), 1)
                self.assertEqual(calls.count("set-speed"), 1)
                self.assertEqual(engine.calls[-1], ("set-speed", {"speed": 0.0}))
                self.assertLess(calls.index("show"), calls.index("set-speed"))

    def test_a_failed_particle_rebuild_is_refused_and_keeps_the_marker(self) -> None:
        with self.engine(status()) as rec:
            rec.answer("set_particles", {"id": 1, "ok": False, "error": "rebuild failed: out of memory"})
            outcome = push.sync_all("command")
        self.assertEqual(outcome, push.Outcome("refused", message="rebuild failed: out of memory"))
        self.assertEqual(marker.read()["classes"], ["BUNDLE"])

    def test_a_window_run_stops_at_its_budget_and_the_next_run_skips_what_it_sent(self) -> None:
        settings.update({"SCHEDULE": "07:00=night;20:00=extra"})
        clock = Clock()
        with mock.patch.object(push, "time", clock):
            with self.engine(status()) as rec, self.assertLogs("lwe_ui.engine.push", "WARNING") as logs:
                rec.hooks["playlist_set"] = lambda: clock.sleep(5)
                first = push.sync_all("window")
            self.assertEqual(first, push.Outcome("pending", reason="budget", recorded=2))
            self.assertEqual(rec.playlists(), ["main", "night"])
            self.assertEqual(len(logs.records), 1)
            self.assertEqual(marker.read()["sent"], {"pid": 4242, "playlists": ["main", "night"]})
            with self.engine(status()) as rec:
                rec.hooks["playlist_set"] = lambda: clock.sleep(5)
                second = push.sync_all("window")
            self.assertEqual(second, push.Outcome("applied"))
            self.assertEqual(rec.playlists(), ["extra"])
            self.assertEqual(marker.read()["classes"], [])

    def test_another_pid_empties_the_list_and_a_stale_generation_records_nothing(self) -> None:
        settings.update({"SCHEDULE": "07:00=night;20:00=extra"})
        clock = Clock()
        with mock.patch.object(push, "time", clock):
            with self.engine(status()) as rec:
                rec.hooks["playlist_set"] = lambda: clock.sleep(5)
                push.sync_all("window")
            with self.engine(status(pid=5151)) as rec:
                rec.hooks["playlist_set"] = lambda: clock.sleep(5)
                outcome = push.sync_all("window")
            self.assertEqual(rec.playlists(), ["main", "night"])
            self.assertEqual(outcome.recorded, 2)
            self.assertEqual(marker.read()["sent"], {"pid": 5151, "playlists": ["main", "night"]})

            def raise_generation() -> None:
                clock.sleep(5)
                with marker.writing(("BUNDLE",)):
                    pass
            with self.engine(status(pid=6161)) as rec:
                rec.hooks["playlist_set"] = raise_generation
                outcome = push.sync_all("window")
            self.assertEqual(outcome, push.Outcome("pending", reason="budget", recorded=0))
            self.assertEqual(marker.read()["sent"], {"pid": None, "playlists": []})

    def test_a_command_run_ignores_the_budget_and_the_list(self) -> None:
        settings.update({"SCHEDULE": "07:00=night;20:00=extra"})
        clock = Clock()
        with mock.patch.object(push, "time", clock):
            with self.engine(status()) as rec:
                rec.hooks["playlist_set"] = lambda: clock.sleep(5)
                push.sync_all("window")
            with self.engine(status()) as rec:
                rec.hooks["playlist_set"] = lambda: clock.sleep(5)
                outcome = push.sync_all("command")
        self.assertEqual(outcome, push.Outcome("applied"))
        self.assertEqual(rec.playlists(), ["main", "night", "extra"])
        self.assertEqual(rec.verbs()[3:], ORDER[2:])

    def test_sync_all_makes_a_fresh_generation_and_a_failed_run_keeps_it(self) -> None:
        self.assertIsNone(marker.read()["generation"])
        refusal = {"id": 1, "ok": False, "error": "no"}
        generations = []
        for script, kind in (([refusal], "refused"), ([refusal], "refused"), ([], "applied")):
            with self.engine(status()) as rec:
                rec.answer("set_fps", *script)
                outcome = push.sync_all("command")
            self.assertEqual(outcome.kind, kind)
            generations.append(marker.read()["generation"])
        self.assertIsInstance(generations[0], int)
        self.assertEqual(generations, [generations[0]] * 3)
        self.assertEqual(marker.read()["classes"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
