"""sync_all: the bundle that rebuilds the engine from the store, and the reply classes it runs on.

Against the test's own socket server, request() records the four reply classes: nothing listening
is away, a silent server uncertain, ok false refused and ok true ok. sync_all sends its requests
in order; a refused playlist part stops only the requests that depend on it; an empty
schedule goes out disabled with no entries and the marker clears. While the engine reports speed 0
the bundle leaves set-speed out, and a status with no speed gets it. A CURRENT re-show whose done
never comes keeps CURRENT, and set-speed with the speed read inside sync follows the show either
way. A failed particle rebuild is a refusal that keeps the marker. A window run stops at its budget
between playlists and records what it sent, the next window run of that generation and engine
skips it, another pid empties the list, and so does an engine that reuses the pid with another start,
which gets the playlists the old one took; a stale generation records nothing, a refused transfer is
never recorded, a started transfer runs to its last part past the budget, and a command run
ignores both. sync_all makes a marker with a fresh generation when none exists, and a failed run
keeps it; a writer that raises the generation during a run that ends all ok keeps the marker too.
A reload's run re-shows whatever the marker holds when it starts, also when a window reload's
budget stops its bundle, and beside an older drain it shows once and loses no CURRENT. Two drains never send at once, and an engine-only action holds
sync. A bundle with a step not ok records no served engine, and a whole one records the answering pid.
After a restart command that timed out and an ordinary drain that reached the old engine, the next
command change finds the new engine unserved and sends it the whole bundle before its own verb. Served
waits for the whole required bundle: a refused own re-show leaves it, and a deferred re-show records it
only when the build ticket shows. An engine of the same pid from another boot, or with another start,
is not served; an unreadable marker counts as none; a window bundle stopped at its budget, or a failed
owed bundle, records nothing, and an engine that keeps refusing a step is owed one re-show, not one per
change. Every re-show carries "automatic": the owed one, directly or completing a deferred re-show, a
reload's and a build change's; an explicit show never does, and a user's playlist switch sends manual
whether or not the schedule is on. A re-show the engine held sends no tail, takes back the owed CURRENT,
serves the engine when the rest of the bundle ended ok and keeps the brake note for a command run only,
not for one whose own switch released the engine; restore_refused in the status changes nothing the panel
sends, and two quick restarts of a healthy engine get full bundles and no note. The start is taken once
per status on the engine's clock, so after a suspend or a wall-clock step the same engine gets nothing
more, and a bundle that takes 6 s serves its engine once; a status without uptime_s is never named served.
An owed re-show that never ran, or was refused, is kept for the next bundle, and so is one whose set-tuning
or set-speed ended refused or uncertain, directly or in the delivery that completes a deferred re-show, so
the retry shows again; the delivery that re-shows a deferred CURRENT takes it, so nothing re-shows again, also when
the deferred run ended uncertain before its re-show and when an import runs during a due delivery of a
ticket that found a marker, and a bundling delivery whose marker cannot be read still runs. An owed
CURRENT kept past a writer's change is taken back by the next whole re-show, so a persistent refusal
reloads once, not on every change, and a slow refused run takes back the CURRENT its engine owed.
The engine is an api_client recorder with a scripted status unless a test names the socket server; its
clock is frozen at 10000 and its status reports uptime_s 100 unless a test says otherwise.

Run: PYTHONPATH=src python3 tests/test_sync_bundle.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import contextlib
import copy
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import _fake_engine

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from lwe_ui import api_client, version  # noqa: E402
from lwe_ui.engine import marker, push  # noqa: E402
from lwe_ui.storage import lock, playlists, settings, wp  # noqa: E402

OK = {"id": 1, "ok": True, "status": "done", "result": {}}
HELD = {"id": 1, "ok": True, "status": "done", "result": {"held": True}}
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


def status(speed=1.0, pid: int = 4242, current: str = "111", uptime_s: int | None = 100, **extra) -> dict:
    out = {"api": 1, "version": version.panel_stamp(), "pid": pid,
           "current": {"id": current, "ui_id": current, "title": ""},
           "schedule": {"enabled": False}, "lanes": [{"id": "all", "playlist": "main"}], **extra}
    if speed is not None:
        out["speed"] = speed
    if uptime_s is not None:
        out["uptime_s"] = uptime_s
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
        engine_clock = mock.patch.object(push, "_monotonic", lambda: 10_000.0)
        engine_clock.start()
        self.addCleanup(engine_clock.stop)
        marker.record_served(4242, 9900.0)

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
            self.assertEqual(marker.read()["sent"], {"pid": 4242, "boot": marker.boot_id(), "start": 9900.0,
                                                     "playlists": ["main", "night"]})
            with self.engine(status()) as rec:
                rec.hooks["playlist_set"] = lambda: clock.sleep(5)
                second = push.sync_all("window")
            self.assertEqual(second, push.Outcome("applied"))
            self.assertEqual(rec.playlists(), ["extra"])
            self.assertEqual(marker.read()["classes"], [])

    def test_a_window_reloads_reshow_goes_out_when_the_budget_stops_its_bundle(self) -> None:
        clock = Clock()
        with mock.patch.object(push, "time", clock):
            with self.engine(status()) as rec, self.assertLogs("lwe_ui.engine.push", "WARNING"):
                rec.hooks["playlist_set"] = lambda: clock.sleep(5)
                outcome = push.sync_all("window", ("BUNDLE", "CURRENT"))
        self.assertEqual((outcome.kind, outcome.reason), ("pending", "budget"))
        self.assertEqual(rec.verbs().count("show"), 1, rec.verbs())
        self.assertEqual(marker.read()["classes"], ["BUNDLE", "CURRENT"])

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
            self.assertEqual(marker.read()["sent"], {"pid": 5151, "boot": marker.boot_id(), "start": 9900.0,
                                                     "playlists": ["main", "night"]})

            def raise_generation() -> None:
                clock.sleep(5)
                with marker.writing(("BUNDLE",)):
                    pass
            with self.engine(status(pid=6161)) as rec:
                rec.hooks["playlist_set"] = raise_generation
                outcome = push.sync_all("window")
            self.assertEqual(outcome, push.Outcome("pending", reason="budget", recorded=0))
            self.assertEqual(marker.read()["sent"], {"pid": None, "playlists": []})

    def test_an_engine_that_reuses_the_pid_gets_the_playlists_the_old_one_took(self) -> None:
        clock = Clock()
        with mock.patch.object(push, "time", clock):
            with self.engine(status(pid=5000, uptime_s=9100)) as rec, self.assertLogs("lwe_ui.engine.push", "WARNING"):
                slow = [9]
                rec.hooks["playlist_set"] = lambda: clock.sleep(slow.pop() if slow else 0)
                first = push.sync_all("window")
                kept = marker.read()["sent"]["playlists"]
            with self.engine(status(pid=5000, uptime_s=8992)) as rec:
                second = push.sync_all("window")
        self.assertEqual(((first.kind, first.reason), kept), (("pending", "budget"), ["main"]))
        self.assertEqual((second.kind, rec.playlists(), marker.served_record()["start"]),
                         ("applied", ["main", "night"], 1008.0))

    def test_a_refused_transfer_is_never_recorded_as_sent(self) -> None:
        refusal = {"id": 1, "ok": False, "error": "unknown member"}
        with self.engine(status()) as rec:
            rec.answer("playlist_set", OK, refusal)
            outcome = push.sync_all("window")
        self.assertEqual(outcome, push.Outcome("refused", message="unknown member"))
        self.assertEqual(marker.read()["sent"], {"pid": 4242, "boot": marker.boot_id(), "start": 9900.0,
                                                 "playlists": ["main"]})
        with self.engine(status()) as rec:
            outcome = push.sync_all("window")
        self.assertEqual(outcome, push.Outcome("applied"))
        self.assertEqual(rec.playlists(), ["night"])

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

    def test_a_writer_that_raises_the_generation_during_an_ok_run_keeps_the_marker(self) -> None:
        raised: list[int] = []

        def raise_generation() -> None:
            if not raised:
                with marker.writing(("BUNDLE",)) as (generation, _existed):
                    raised.append(generation)
        with self.engine(status()) as rec:
            rec.hooks["playlist_set"] = raise_generation
            outcome = push.sync_all("command")
        self.assertEqual(outcome, push.Outcome("applied"))
        self.assertEqual(rec.verbs(), ORDER)
        self.assertEqual(marker.read()["classes"], ["BUNDLE"])
        self.assertEqual(marker.read()["generation"], raised[0])

    def test_a_reload_beside_an_older_drain_shows_once_and_loses_no_current(self) -> None:
        checked, added = threading.Event(), threading.Event()
        errors: list[BaseException] = []
        results: list[tuple] = []
        holds, ensure = push._holds_current, marker.ensure

        def held_current() -> bool:
            answer = holds()
            if threading.current_thread().name == "old-drain":
                checked.set()
                added.wait(10)
            return answer

        def ensured(classes) -> int:
            generation = ensure(classes)
            if threading.current_thread().name == "reload":
                added.set()
            return generation

        def drain(classes) -> None:
            try:
                results.append((threading.current_thread().name, push.sync_all("command", classes)))
            except BaseException as exc:
                errors.append(exc)
        with self.engine(status()) as rec, mock.patch.object(push, "_holds_current", held_current), \
                mock.patch.object(marker, "ensure", ensured):
            old = threading.Thread(target=drain, args=(("BUNDLE",),), name="old-drain")
            reload = threading.Thread(target=drain, args=(("BUNDLE", "CURRENT"),), name="reload")
            old.start()
            self.assertTrue(checked.wait(10))
            reload.start()
            old.join(10)
            reload.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(name for name, _outcome in results), ["old-drain", "reload"])
        self.assertEqual([outcome for _name, outcome in results], [push.Outcome("applied")] * 2)
        self.assertEqual(rec.verbs().count("show"), 1)
        self.assertEqual(marker.read()["classes"], [])

    def test_a_reloads_run_reshows_whatever_the_marker_holds_when_it_starts(self) -> None:
        ensure = marker.ensure

        def cleared_by_another_run(classes) -> int:
            generation = ensure(classes)
            marker.clear(generation)
            return generation
        with self.engine(status()) as rec, mock.patch.object(marker, "ensure", cleared_by_another_run):
            outcome = push.sync_all("command", ("BUNDLE", "CURRENT"))
        self.assertEqual(outcome, push.Outcome("applied"))
        self.assertEqual(rec.verbs().count("show"), 1)
        with self.engine(status()):
            ticket = push.save_change(("settings",), lambda: None, [("reload", None)], run="command")
        marker.clear(ticket.generation)
        with self.engine(status()) as rec:
            outcome = push.deliver(ticket)
        self.assertEqual(outcome, push.Outcome("applied"))
        self.assertEqual(rec.verbs().count("show"), 1)

    def test_two_drains_never_send_at_the_same_time(self) -> None:
        first, resume, reached = threading.Event(), threading.Event(), threading.Event()
        senders: list[str] = []
        errors: list[BaseException] = []
        held = lock.held

        def watched(store: str, *args, **kwargs):
            if store == "sync" and threading.current_thread().name == "B":
                reached.set()
            return held(store, *args, **kwargs)

        def transfer() -> None:
            senders.append(threading.current_thread().name)
            if senders[-1] == "B":
                reached.set()
            if len(senders) == 1:
                first.set()
                resume.wait(10)

        def drain() -> None:
            try:
                push.sync_all("command")
            except BaseException as exc:
                errors.append(exc)
        with self.engine(status()) as rec, mock.patch.object(lock, "held", watched):
            rec.hooks["playlist_set"] = transfer
            a = threading.Thread(target=drain, name="A")
            b = threading.Thread(target=drain, name="B")
            a.start()
            self.assertTrue(first.wait(10))
            b.start()
            self.assertTrue(reached.wait(10))
            while_a_sends = list(senders)
            resume.set()
            a.join(10)
            b.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(while_a_sends, ["A"])
        self.assertEqual(senders, ["A", "A", "B", "B"])

    def test_an_engine_only_action_holds_sync(self) -> None:
        busy: list[bool] = []

        def probe() -> None:
            try:
                with lock.held("sync", wait_s=0):
                    busy.append(False)
            except lock.StoreBusy:
                busy.append(True)
        with push.engine_only():
            thread = threading.Thread(target=probe)
            thread.start()
            thread.join(10)
        self.assertEqual(busy, [True])

    def test_a_bundle_with_a_step_not_ok_records_no_served_engine(self) -> None:
        with self.engine(status(pid=5151)) as rec:
            rec.answer("set_particles", {"id": 1, "ok": False, "error": "rebuild failed"})
            self.assertEqual(push.sync_all("command").kind, "refused")
        self.assertEqual(marker.served(), 4242)
        with self.engine(status(pid=5151)):
            self.assertEqual(push.sync_all("command").kind, "applied")
        self.assertEqual(marker.served(), 5151)

    def test_after_a_timed_out_restart_command_the_new_engine_is_served_by_the_next_command(self) -> None:
        from lwe_ui.cli.verbs import service
        from lwe_ui.engine import daemon_unit

        def runner(args, timeout=30.0):
            if "restart" in args:
                return 1, "", "Failed to restart lwe-engine.service: Connection timed out"
            return 0, "", ""
        with self.engine(status()) as rec, mock.patch.object(daemon_unit, "RUNNER", runner), \
                mock.patch.object(daemon_unit, "_service_main_pid", return_value=4242):
            with self.assertRaises(service._Stop):
                service._launch(["restart"])
            self.assertEqual(marker.read()["classes"], ["BUNDLE"])
            self.assertEqual(push.sync_all("command").kind, "applied", "an ordinary drain reached the old engine")
            rec.status_reply = status(pid=5000)
            sent = len(rec.calls)
            outcome = push.run_change(("settings",), lambda: settings.update({"ENGINE_FPS": 45}),
                                      [("verb", "ENGINE_FPS")], run="command")
        after = [verb for verb, _a, _k in rec.calls[sent:] if verb != "status"]
        self.assertEqual(outcome.kind, "applied")
        self.assertIn("lanes_set", after)
        self.assertEqual(after[-1], "set_fps")
        self.assertEqual((marker.served(), marker.read()["classes"]), (5000, []))

    @staticmethod
    def lanes(calls) -> list[dict]:
        return [lane for verb, args, _k in calls if verb == "lanes_set" for lane in args[0]]

    @staticmethod
    def automatic(calls) -> list:
        return [kwargs.get("automatic") for verb, _a, kwargs in calls if verb == "show"]

    def test_a_refused_own_reshow_leaves_served_and_the_intent(self) -> None:
        with self.engine(status(pid=5000)) as rec:
            rec.answer("show", {"id": 1, "ok": False, "error": "no"})
            outcome = push.run_change(("overrides",), lambda: wp.update_set("111", {"SCALING": "fill"}),
                                      [("wp_build", "SCALING")], wid="111", run="command")
        self.assertEqual((outcome.kind, rec.verbs().count("show")), ("refused", 1))
        self.assertEqual(marker.served(), 4242)
        self.assertEqual(marker.read()["classes"], ["BUNDLE", "CURRENT"])

    def test_a_deferred_reshow_records_served_only_when_the_build_ticket_shows(self) -> None:
        with self.engine(status(pid=5000)) as rec:
            ticket = push.save_change(("overrides",), lambda: wp.update_set("111", {"SCALING": "fill"}),
                                      [("wp_build", "SCALING")], wid="111")
            self.assertEqual(push.sync_all("window", defer_current=True).kind, "applied")
            self.assertEqual((marker.served(), rec.verbs().count("show")), (4242, 0))
            self.assertEqual(push.deliver(ticket).kind, "applied")
        self.assertEqual((marker.served(), rec.verbs().count("show")), (5000, 1))

    def test_an_engine_of_the_same_pid_from_another_boot_or_with_another_start_is_not_served(self) -> None:
        with self.engine(status(pid=777, uptime_s=100)):
            self.assertEqual(push.sync_all("command").kind, "applied")
        for name, uptime, boot, unserved in (("the same engine", 102, None, False),
                                             ("another boot", 100, "another-boot", True),
                                             ("another start", 10, None, True)):
            with self.subTest(case=name):
                with mock.patch.object(marker, "boot_id", return_value=boot) if boot else contextlib.nullcontext():
                    self.assertEqual(push._unserved(push._engine(status(pid=777, uptime_s=uptime))), unserved)
        self.assertEqual((marker.served(), marker.served_record()["start"]), (777, 9900.0))

    def test_after_a_suspend_or_a_wall_clock_step_the_same_engine_gets_nothing_more(self) -> None:
        clocks = {"engine": 1000.0, "wall": 50_000.0}
        sent = []
        with mock.patch.object(push, "_monotonic", lambda: clocks["engine"]), \
                mock.patch.object(push, "_now", lambda: clocks["wall"]):
            for name, engine_now, wall, uptime in (("first change", 1000.0, 50_000.0, 100),
                                                   ("10 s later", 1010.0, 50_010.0, 110),
                                                   ("after a 600 s suspend", 1012.0, 50_612.0, 112),
                                                   ("after a clock step back 3600 s", 1013.0, 47_013.0, 113)):
                clocks.update(engine=engine_now, wall=wall)
                with self.engine(status(pid=5000, uptime_s=uptime)) as rec:
                    push.run_change(("settings",), lambda fps=uptime: settings.update({"ENGINE_FPS": fps}),
                                    [("verb", "ENGINE_FPS")], run="command")
                sent.append((name, "lanes_set" in rec.verbs(), rec.verbs().count("show")))
        self.assertEqual(sent, [("first change", True, 1), ("10 s later", False, 0),
                                ("after a 600 s suspend", False, 0), ("after a clock step back 3600 s", False, 0)])

    def test_a_bundle_that_takes_6_s_serves_its_engine_once(self) -> None:
        clock = {"engine": 1000.0}
        with mock.patch.object(push, "_monotonic", lambda: clock["engine"]):
            with self.engine(status(pid=5000)) as rec:
                read = rec.status

                def live(sock=None):
                    rec.status_reply["uptime_s"] = int(clock["engine"] - 900.0)
                    return read(sock)
                rec.status = live
                slow = [6.0]
                rec.hooks["set_particles"] = lambda: clock.update(engine=clock["engine"] + (slow.pop() if slow else 0))
                runs = []
                for fps in (40, 41, 42):
                    sent = len(rec.calls)
                    outcome = push.run_change(("settings",), lambda fps=fps: settings.update({"ENGINE_FPS": fps}),
                                              [("verb", "ENGINE_FPS")], run="command")
                    runs.append((outcome.kind, "lanes_set" in [verb for verb, _a, _k in rec.calls[sent:]]))
        self.assertEqual(runs, [("applied", True), ("applied", False), ("applied", False)])
        self.assertEqual((marker.served(), marker.served_record()["start"]), (5000, 900.0))

    def test_an_engine_whose_status_gives_no_uptime_is_never_named_served(self) -> None:
        with self.engine(status(pid=5000, uptime_s=None)):
            self.assertEqual(push.sync_all("command").kind, "applied")
        self.assertEqual((marker.served(), marker.served_record()["start"]), (5000, None))
        self.assertTrue(push._unserved(push._engine(status(pid=5000, uptime_s=None))))

    def test_an_owed_reshow_that_never_ran_is_kept_for_the_next_bundle(self) -> None:
        with self.engine(status(pid=5000)) as rec:
            rec.answer("playlist_set", "uncertain")
            first = push.sync_all("command").kind
            kept = marker.read()["classes"]
            second = push.sync_all("command").kind
        self.assertEqual((first, kept, second), ("uncertain", ["BUNDLE", "CURRENT"], "applied"))
        self.assertEqual((rec.verbs().count("show"), marker.served()), (1, 5000))

    def test_a_refused_owed_reshow_is_kept_until_a_show_succeeds(self) -> None:
        with self.engine(status(pid=5000)) as rec:
            rec.answer("show", {"id": 1, "ok": False, "error": "no"})
            first = push.sync_all("command").kind
            kept = marker.read()["classes"]
            second = push.sync_all("command").kind
        self.assertEqual((first, kept, second), ("refused", ["BUNDLE", "CURRENT"], "applied"))
        self.assertEqual((rec.verbs().count("show"), marker.served()), (2, 5000))

    def test_an_owed_reshow_whose_tail_fails_is_kept_and_the_retry_shows_again(self) -> None:
        refused = {"id": 1, "ok": False, "error": "no"}
        got = []
        for verb, steps in (("set_speed", (refused,)), ("set_speed", ("uncertain",)), ("set_tuning", (OK, refused))):
            marker._file().unlink(missing_ok=True)
            marker.record_served(4242, 9900.0)
            with self.engine(status(pid=5000, speed=0)) as rec:
                rec.answer(verb, *steps)
                first = push.sync_all("command").kind
                kept = (marker.read()["classes"], marker.served())
                second = push.sync_all("command").kind
            got.append((verb, first, *kept, second, rec.verbs().count("show"), marker.served()))
        self.assertEqual(got, [("set_speed", "refused", ["BUNDLE", "CURRENT"], 4242, "applied", 2, 5000),
                               ("set_speed", "uncertain", ["BUNDLE", "CURRENT"], 4242, "applied", 2, 5000),
                               ("set_tuning", "refused", ["BUNDLE", "CURRENT"], 4242, "applied", 2, 5000)])

    def test_a_deferred_reshow_whose_set_speed_fails_keeps_current_for_the_retry(self) -> None:
        got = []
        for tail in ({"id": 1, "ok": False, "error": "no"}, "uncertain"):
            marker._file().unlink(missing_ok=True)
            marker.record_served(4242, 9900.0)
            with self.engine(status(pid=5000, speed=0)) as rec:
                ticket = push.save_change(("overrides",), lambda: wp.update_set("333", {"SCALING": "fill"}),
                                          [("wp_build", "SCALING")], wid="333")
                push.sync_all("window", defer_current=True)
                rec.answer("set_speed", tail)
                delivered = push.deliver(ticket).kind
                left = (marker.read()["classes"], marker.served())
                retry = push.sync_all("command").kind
            got.append(("set_speed", delivered, *left, retry, rec.verbs().count("show")))
        self.assertEqual(got, [("set_speed", "refused", ["CURRENT"], 4242, "applied", 2),
                               ("set_speed", "uncertain", ["CURRENT"], 4242, "applied", 2)])

    def test_an_owed_current_kept_past_a_writer_is_taken_back_by_the_next_whole_reshow(self) -> None:
        refused = {"id": 1, "ok": False, "error": "no"}
        steps = []
        with self.engine(status(pid=5000)) as rec:
            rec.answer("playlist_set", "uncertain")
            first = push.sync_all("command").kind
            steps.append(("first bundle", first, marker.read()["classes"], rec.verbs().count("show")))
            for fps in (40, 41, 42):
                rec.answer("set_particles", refused)
                before = rec.verbs().count("show")
                outcome = push.run_change(("settings",), lambda fps=fps: settings.update({"ENGINE_FPS": fps}),
                                          [("verb", "ENGINE_FPS")], run="command")
                steps.append((f"change fps {fps}", outcome.kind, marker.read()["classes"],
                              rec.verbs().count("show") - before))
        self.assertEqual(steps, [("first bundle", "uncertain", ["BUNDLE", "CURRENT"], 0),
                                 ("change fps 40", "refused", ["BUNDLE"], 1),
                                 ("change fps 41", "refused", ["BUNDLE"], 0),
                                 ("change fps 42", "refused", ["BUNDLE"], 0)])

    def test_an_uncertain_deferred_first_sight_and_the_build_delivery_show_once(self) -> None:
        with self.engine(status(pid=5000)) as rec:
            ticket = push.save_change(("overrides",), lambda: wp.update_set("333", {"SCALING": "fill"}),
                                      [("wp_build", "SCALING")], wid="333")
            rec.answer("set_particles", "uncertain")
            deferred = push.sync_all("window", defer_current=True)
            first = (deferred.kind, rec.verbs().count("show"), marker.read()["classes"])
            delivered = push.deliver(ticket)
            second = (delivered.kind, rec.verbs().count("show"), marker.read()["classes"], marker.served())
            drained = push.sync_all("window", wait_s=0)
            third = (drained.kind, rec.verbs().count("show"), marker.read()["classes"])
        self.assertEqual((first, second, third), (("uncertain", 0, ["BUNDLE", "CURRENT"]),
                                                  ("applied", 1, [], 5000), ("applied", 1, [])))

    def test_an_import_during_a_due_delivery_of_an_existed_ticket_shows_once(self) -> None:
        with self.engine(status()) as rec:
            marker.ensure(("BUNDLE",))
            ticket = push.save_change(("settings",), lambda: settings.update({"ENGINE_FPS": 40}),
                                      [("verb", "ENGINE_FPS")])
            push.sync_all("window", ("BUNDLE", "CURRENT"), defer_current=True)
            delivered = push.deliver(ticket).kind
            after = (rec.verbs().count("show"), marker.read()["classes"])
            if marker.read()["classes"]:
                push.sync_all("window", wait_s=0)
        self.assertEqual((ticket.existed, delivered, after, rec.verbs().count("show")), (True, "applied", (1, []), 1))

    def test_a_slow_refused_run_takes_back_the_current_its_engine_owed(self) -> None:
        clock = {"engine": 1000.0}
        refused = {"id": 1, "ok": False, "error": "no"}
        with mock.patch.object(push, "_monotonic", lambda: clock["engine"]):
            with self.engine(status(pid=5000)) as rec:
                read = rec.status

                def live(sock=None):
                    rec.status_reply["uptime_s"] = int(clock["engine"] - 900.0)
                    return read(sock)
                rec.status = live
                slow = [6.0]
                rec.hooks["set_particles"] = lambda: clock.update(engine=clock["engine"] + (slow.pop() if slow else 0))
                rec.answer("set_particles", refused, refused)
                first = push.sync_all("command").kind
                shown = rec.verbs().count("show")
                push.sync_all("command")
        self.assertEqual((first, shown, rec.verbs().count("show") - shown), ("refused", 1, 0))

    def test_the_delivery_that_reshows_a_deferred_current_takes_it_so_no_poll_reshows_again(self) -> None:
        with self.engine(status(pid=5000)) as rec:
            ticket = push.save_change(("overrides",), lambda: wp.update_set("333", {"SCALING": "fill"}),
                                      [("wp_build", "SCALING")], wid="333")
            self.assertEqual(push.sync_all("window", defer_current=True).kind, "applied")
            self.assertEqual((rec.verbs().count("show"), marker.read()["classes"]), (0, ["CURRENT"]))
            self.assertEqual(push.deliver(ticket).kind, "applied")
            delivered = (rec.verbs().count("show"), marker.read()["classes"], marker.served())
            push.sync_all("window", wait_s=0)
        self.assertEqual(delivered, (1, [], 5000))
        self.assertEqual(rec.verbs().count("show"), 1)

    def test_a_bundling_delivery_whose_marker_cannot_be_read_still_runs(self) -> None:
        marker.ensure(("BUNDLE",))
        with self.engine(status()) as rec:
            ticket = push.save_change(("settings",), lambda: settings.update({"ENGINE_FPS": 40}),
                                      [("verb", "ENGINE_FPS")])
            with mock.patch.object(marker, "read", side_effect=OSError("unreadable")):
                outcome = push.deliver(ticket)
        self.assertEqual((outcome.kind, "lanes_set" in rec.verbs(), rec.verbs()[-1]), ("applied", True, "set_fps"))

    def test_an_unreadable_marker_counts_the_engine_as_not_served(self) -> None:
        path = marker._file()
        path.unlink()
        path.mkdir()
        self.assertTrue(push._unserved(push._engine(status())))

    def test_a_window_bundle_stopped_at_its_budget_records_no_served_engine(self) -> None:
        clock = Clock()
        with mock.patch.object(push, "time", clock):
            with self.engine(status(pid=5000)) as rec, self.assertLogs("lwe_ui.engine.push", "WARNING"):
                rec.hooks["playlist_set"] = lambda: clock.sleep(5)
                outcome = push.sync_all("window")
        self.assertEqual((outcome.kind, outcome.reason, marker.served()), ("pending", "budget", 4242))

    def test_a_failed_owed_bundle_through_run_change_records_no_served_engine(self) -> None:
        with self.engine(status(pid=5000)) as rec:
            rec.answer("set_particles", {"id": 1, "ok": False, "error": "no"})
            outcome = push.run_change(("settings",), lambda: settings.update({"ENGINE_FPS": 40}),
                                      [("verb", "ENGINE_FPS")], run="command")
        self.assertEqual((outcome.kind, marker.served()), ("refused", 4242))

    def test_an_engine_that_keeps_refusing_a_step_is_owed_one_reshow_not_one_per_change(self) -> None:
        refused = {"id": 1, "ok": False, "error": "no"}
        with self.engine(status(pid=5000)) as rec:
            rec.answer("set_particles", refused, refused, refused)
            outcomes = [push.run_change(("settings",), lambda v=v: settings.update({"ENGINE_FPS": v}),
                                        [("verb", "ENGINE_FPS")], run="command").kind for v in (40, 41, 42)]
        self.assertEqual(outcomes, ["refused"] * 3)
        self.assertEqual(rec.verbs().count("show"), 1)
        self.assertEqual((marker.served(), marker.read()["classes"]), (4242, ["BUNDLE"]))

    def test_every_automatic_reshow_carries_automatic_and_an_explicit_show_never_does(self) -> None:
        flags = {}
        with self.engine(status(pid=5000)) as rec:
            push.sync_all("command")
            flags["owed re-show"] = self.automatic(rec.calls)
        marker._file().unlink(missing_ok=True)
        marker.record_served(4242, 9900.0)
        with self.engine(status(pid=5000)) as rec:
            ticket = push.save_change(("overrides",), lambda: wp.update_set("333", {"SCALING": "fill"}),
                                      [("wp_build", "SCALING")], wid="333")
            push.sync_all("window", defer_current=True)
            push.deliver(ticket)
            flags["deferred re-show"] = self.automatic(rec.calls)
        with self.engine(status()) as rec:
            push.sync_all("command", ("BUNDLE", "CURRENT"))
            flags["reload's re-show"] = self.automatic(rec.calls)
        with self.engine(status()) as rec:
            push.run_change(("overrides",), lambda: wp.update_set("111", {"SCALING": "fit"}),
                            [("wp_build", "SCALING")], wid="111", run="command")
            flags["build re-show"] = self.automatic(rec.calls)
        with self.engine(status()) as rec:
            with push.engine_only():
                push.show_final("111")
                push.show("222")
            flags["explicit shows"] = self.automatic(rec.calls)
        self.assertEqual(flags, {"owed re-show": [True], "deferred re-show": [True], "reload's re-show": [True],
                                 "build re-show": [True], "explicit shows": [None, None]})

    def test_a_user_playlist_switch_sends_manual_whether_or_not_the_schedule_is_on(self) -> None:
        got = []
        for schedule_on, slug in ((False, "night"), (True, "main")):
            with self.engine(status(schedule={"enabled": schedule_on})) as rec:
                push.run_change(("settings",), lambda slug=slug: settings.update({"ACTIVE_PLAYLIST": slug}),
                                [("active", "ACTIVE_PLAYLIST")], slug=slug, manual=True, run="command")
            got.append(self.lanes(rec.calls))
        self.assertEqual(got, [[{"id": "all", "playlist": "night", "enabled": True, "manual": True}],
                               [{"id": "all", "playlist": "main", "enabled": True, "manual": True}]])

    def test_a_held_reshow_sends_no_tail_drops_current_serves_and_notes_only_a_command_run(self) -> None:
        got = []
        for run, pid in (("command", 5000), ("window", 6000)):
            with self.engine(status(pid=pid)) as rec:
                rec.answer("show", HELD)
                outcome = push.sync_all(run)
            verbs = rec.verbs()
            got.append((run, outcome.kind, verbs[verbs.index("show"):], marker.read()["classes"], marker.served(),
                        push.brake_notes()))
        self.assertEqual(got, [("command", "applied", ["show"], [], 5000, [push.BRAKED]),
                               ("window", "applied", ["show"], [], 6000, [])])

    def test_a_held_reshow_in_a_bundle_with_a_refused_step_takes_back_the_owed_current(self) -> None:
        with self.engine(status(pid=5000)) as rec:
            rec.answer("set_particles", {"id": 1, "ok": False, "error": "no"})
            rec.answer("show", HELD)
            first = push.sync_all("command").kind
            verbs = rec.verbs()
            kept = (verbs[verbs.index("show"):], marker.read()["classes"], marker.served())
            second = push.sync_all("command").kind
        self.assertEqual((first, *kept, second, rec.verbs().count("show"), marker.served(), push.brake_notes()),
                         ("refused", ["show"], ["BUNDLE"], 4242, "applied", 1, 5000, []))

    def test_a_switch_whose_manual_lanes_set_releases_the_engine_keeps_no_brake_note(self) -> None:
        with self.engine(status(pid=5000, restore_refused=True)) as rec:
            rec.answer("show", HELD)
            outcome = push.run_change(("settings",), lambda: settings.update({"ACTIVE_PLAYLIST": "night"}),
                                      [("active", "ACTIVE_PLAYLIST")], slug="night", manual=True, run="command")
        self.assertEqual((outcome.kind, self.lanes(rec.calls)[-1], push.brake_notes()),
                         ("applied", {"id": "all", "playlist": "night", "enabled": True, "manual": True}, []))

    def test_restore_refused_in_the_status_changes_nothing_the_panel_sends(self) -> None:
        with self.engine(status(pid=300, restore_refused=True)) as rec:
            self.assertEqual(push.sync_all("window").kind, "applied")
        self.assertEqual((self.automatic(rec.calls), self.lanes(rec.calls)),
                         ([True], [{"id": "all", "playlist": "main", "enabled": True}]))
        self.assertEqual((sorted(marker.served_record()), marker.served(), marker.read()["classes"],
                          push.brake_notes()), (["at", "boot", "pid", "start"], 300, [], []))

    def test_two_quick_restarts_of_a_healthy_engine_get_full_bundles_and_no_note(self) -> None:
        from lwe_ui.cli.verbs import service
        from lwe_ui.engine import daemon_unit
        main, current, runs = [100], [], []

        def runner(args, timeout=30.0):
            if "restart" in args:
                main[0] += 100
                current[0].status_reply = status(pid=main[0])
            return 0, "", ""
        with mock.patch.object(daemon_unit, "RUNNER", runner), \
                mock.patch.object(daemon_unit, "_service_main_pid", lambda: main[0]), \
                mock.patch.object(push, "wait_ready", lambda old_pid=None, timeout_s=20.0: status(pid=main[0])):
            for _ in range(2):
                with self.engine(status(pid=main[0])) as rec:
                    current[:] = [rec]
                    lines, code = service._launch(["restart"])
                runs.append((code, rec.verbs().count("show"), True in [lane.get("enabled") for lane in self.lanes(rec.calls)]))
        self.assertEqual(runs, [(0, 1, True), (0, 1, True)])
        self.assertEqual((marker.served(), push.brake_notes()), (300, []))

    def test_a_started_transfer_runs_to_its_last_part_past_the_budget(self) -> None:
        clock = Clock()
        with mock.patch.object(push, "time", clock), \
                mock.patch.object(push, "split_playlist_parts", lambda entries: [entries[:1], entries[1:]]), \
                self.engine(status()) as rec, self.assertLogs("lwe_ui.engine.push", "WARNING"):
            rec.hooks["playlist_set"] = lambda: clock.sleep(9)
            outcome = push.sync_all("window")
        parts = [(args[0], kwargs["part"], kwargs["of"]) for verb, args, kwargs in rec.calls if verb == "playlist_set"]
        self.assertEqual(parts, [("main", 1, 2), ("main", 2, 2)])
        self.assertEqual(outcome, push.Outcome("pending", reason="budget", recorded=1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
