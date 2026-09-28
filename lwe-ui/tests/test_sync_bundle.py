"""sync_all: the bundle that rebuilds the engine from the store, and the reply classes it runs on.

Against the test's own socket server, request() records the four reply classes: nothing listening
is away, a silent server uncertain, ok false refused and ok true ok. sync_all sends its requests
in order; a refused playlist part stops only the requests that depend on it; an empty
schedule goes out disabled with no entries and the marker clears. While the engine reports speed 0
the bundle leaves set-speed out, and a status with no speed gets it. A CURRENT re-show whose done
never comes keeps CURRENT, and set-speed with the speed read inside sync follows the show either
way. A failed particle rebuild is a refusal that keeps the marker. A window run stops at its budget
between playlists and records what it sent, the next window run of that generation and engine pid
skips it, another pid empties the list, a stale generation records nothing, a refused transfer is
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
change. While the engine reports restore_refused, automatic bundles send no show and a lanes-set with
the playlist binding but no enabled field, serve the engine as braked, clear CURRENT and keep no window
note; after an explicit show the rotation lanes-set goes and later bundles are normal, a playlist switch
sends its own and clears braked, and two quick restarts of a healthy engine get full bundles and no note.
The start is taken once per status on the engine's clock, so after a suspend or a wall-clock step the
same engine gets nothing more, a bundle that takes 6 s serves its engine once, and after a braked one
an explicit show starts the rotation; a status without uptime_s is never named served. An owed re-show
that never ran, or was refused, is kept for the next bundle, the delivery that re-shows a deferred
CURRENT takes it, so nothing re-shows again, and a bundling delivery whose marker cannot be read still
runs.
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
            self.assertEqual(marker.read()["sent"], {"pid": 4242, "playlists": ["main", "night"]})
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

    def test_a_refused_transfer_is_never_recorded_as_sent(self) -> None:
        refusal = {"id": 1, "ok": False, "error": "unknown member"}
        with self.engine(status()) as rec:
            rec.answer("playlist_set", OK, refusal)
            outcome = push.sync_all("window")
        self.assertEqual(outcome, push.Outcome("refused", message="unknown member"))
        self.assertEqual(marker.read()["sent"], {"pid": 4242, "playlists": ["main"]})
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

    def test_after_a_braked_bundle_that_takes_6_s_an_explicit_show_starts_the_rotation(self) -> None:
        clock = {"engine": 1000.0}
        with mock.patch.object(push, "_monotonic", lambda: clock["engine"]):
            with self.engine(status(pid=5000, restore_refused=True)) as rec:
                read = rec.status

                def live(sock=None):
                    rec.status_reply["uptime_s"] = int(clock["engine"] - 900.0)
                    return read(sock)
                rec.status = live
                slow = [6.0]
                rec.hooks["set_particles"] = lambda: clock.update(engine=clock["engine"] + (slow.pop() if slow else 0))
                self.assertEqual(push.sync_all("command").kind, "applied")
                sent = len(rec.calls)
                with push.engine_only():
                    self.assertEqual(push._reply_class(push.show_final("111")), "ok")
                    push.rearm_rotation()
        self.assertEqual(push.brake_notes(), [push.BRAKED])
        self.assertEqual(self.lanes(rec.calls[sent:]), [{"id": "all", "playlist": "main", "enabled": True}])
        self.assertEqual((marker.served_record()["start"], marker.served_record()["braked"]), (900.0, False))

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

    def test_while_the_engine_refused_its_restore_automatic_bundles_show_nothing_and_start_no_rotation(self) -> None:
        with self.engine(status(pid=300, restore_refused=True)) as rec:
            self.assertEqual(push.sync_all("window", defer_current=True).kind, "applied")
            self.assertEqual(marker.read()["classes"], [], "a braked run leaves no re-show to a due delivery")
            marker.ensure(("BUNDLE", "CURRENT"))
            self.assertEqual(push.sync_all("window").kind, "applied")
        self.assertNotIn("show", rec.verbs())
        self.assertEqual(self.lanes(rec.calls), [{"id": "all", "playlist": "main"}] * 2)
        self.assertEqual((marker.served(), marker.served_record()["braked"]), (300, True))
        self.assertEqual(marker.read()["classes"], [], "a braked run leaves no CURRENT")
        self.assertEqual(push.brake_notes(), [], "window runs keep no note")

    def test_after_an_explicit_show_the_rotation_goes_again_and_later_bundles_are_normal(self) -> None:
        with self.engine(status(pid=300, restore_refused=True)) as rec:
            self.assertEqual(push.sync_all("command").kind, "applied")
            self.assertEqual(push.brake_notes(), [push.BRAKED])
            braked = len(rec.calls)
            with push.engine_only():
                self.assertEqual(push._reply_class(push.show_final("111")), "ok")
                push.rearm_rotation()
            self.assertFalse(marker.served_record()["braked"], "the rotation lanes-set clears braked")
            shown = len(rec.calls)
            rec.status_reply = status(pid=300, restore_refused=False)
            marker.ensure(("BUNDLE", "CURRENT"))
            self.assertEqual(push.sync_all("command").kind, "applied")
        self.assertNotIn("show", [verb for verb, _a, _k in rec.calls[:braked]])
        self.assertEqual(self.lanes(rec.calls[braked:shown]), [{"id": "all", "playlist": "main", "enabled": True}])
        self.assertFalse(marker.served_record()["braked"])
        self.assertIn("show", [verb for verb, _a, _k in rec.calls[shown:]])
        self.assertIn({"id": "all", "playlist": "main", "enabled": True}, self.lanes(rec.calls[shown:]))
        self.assertEqual(push.brake_notes(), [])

    def test_a_playlist_switch_to_an_engine_served_under_the_brake_starts_its_rotation(self) -> None:
        with self.engine(status(pid=300, restore_refused=True)) as rec:
            self.assertEqual(push.sync_all("window").kind, "applied")
            sent = len(rec.calls)
            outcome = push.run_change(("settings",), lambda: settings.update({"ACTIVE_PLAYLIST": "night"}),
                                      [("active", "ACTIVE_PLAYLIST")], slug="night", manual=True, run="command")
        self.assertEqual(outcome.kind, "applied")
        self.assertEqual(self.lanes(rec.calls[sent:]), [{"id": "all", "playlist": "night", "enabled": True}])
        self.assertFalse(marker.served_record()["braked"])

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
