"""The window and the tray on the change runner.

A change made while the engine is away is delivered whole by the next poll's drain, and the marker
clears. A pause emits the lane clock its lanes-set reply carries. Failed drains of one generation are
retried after 5 s, 15 s and 45 s, none follows a failed retry after the 45 s wait until a writer
raises the generation, and polls that find the engine away count nothing. The schedule follow never
runs inside the sync hold.
With sync held by another process a change returns pending, and the poll's drain returns at once.
The service switch rebuilds engine-env, the unit and daemon-reload before systemctl, fails with the
existing notice when that fails, and bundles once the engine answers; a restart takes only an engine
whose pid is a new MainPID and keeps sync while its unit stops or starts, up to its cap, and meanwhile
every other sync attempt in the window, one already waiting included, ends at once, pending or busy;
every end of the hold gives later attempts their wait again.
A manual switch while the engine is away with the schedule on writes nothing. A switch to a playlist
being deleted, made by another writer while the delete reads, waits for the delete: the delete raises
nothing and changes ACTIVE_PLAYLIST only in a change that carries the active row. A hand-broken store
line refuses the change before any request. The tray's next and pause send nothing while sync is
held elsewhere. An import pass runs one sync_all. The start-up reconcile writes engine-env only. A
settings reset sends the bundle with one re-show and rewrites engine-env. The engine is an
api_client recorder with a scripted status; the one child process gets an environment built from
scratch.

Run: PYTHONPATH=src python3 tests/test_window_sync.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import ast
import contextlib
import copy
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

_BOOT = tempfile.TemporaryDirectory(prefix="lwe-window-sync-boot-")
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = os.path.join(_BOOT.name, _sub) if _sub else _BOOT.name
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402

_APP = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])

from lwe_ui import models, tray, version  # noqa: E402
from lwe_ui.engine import daemon_unit, marker, push  # noqa: E402
from lwe_ui.storage import lock, paths, playlists, settings, tags, wp  # noqa: E402

OK = {"id": 1, "ok": True, "status": "done", "result": {}}
REFUSED = {"id": 1, "ok": False, "error": "no"}

CHILD = r"""
import sys, time
from pathlib import Path
from lwe_ui.storage import lock

with lock.held("sync"):
    Path(sys.argv[1]).touch()
    time.sleep(60)
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

    def verbs(self) -> list[str]:
        return [verb for verb, _a, _k in self.calls if verb not in ("status", "ping")]


def status(current: str = "111", speed=1.0, pid: int = 4242, **extra) -> dict:
    return {"api": 1, "version": version.panel_stamp(), "pid": pid, "speed": speed,
            "current": {"id": current, "ui_id": current, "title": ""}, "schedule": {"enabled": False},
            "lanes": [{"id": "all", "playlist": "main"}], **extra}


class FakeUnit:
    """systemd's side of lwe-engine.service for the window's restart: the ActiveState and MainPID it
    reports, the window's systemctl calls recorded, and a queued restart moving the unit to
    `on_restart` when given. state_read is set once the state is read after the restart."""

    def __init__(self, main: int, on_restart: str | None = None) -> None:
        self.main, self.state, self.on_restart = main, "active", on_restart
        self.calls: list[list[str]] = []
        self.restarted = False
        self.state_read = threading.Event()

    def run(self, args, **kwargs):
        self.calls.append(list(args))
        if "is-active" in args:
            if self.restarted:
                self.state_read.set()
            return subprocess.CompletedProcess(args, 0, self.state + "\n", "")
        if "restart" in args:
            self.restarted = True
            self.state = self.on_restart or self.state
        return subprocess.CompletedProcess(args, 0, "", "")

    def main_pid(self):
        return self.main or None


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class WindowSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="lwe-window-sync-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        for key, sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"),
                         ("XDG_DATA_HOME", "d")):
            os.environ[key] = str(self.home / sub) if sub else str(self.home)
        settings.save(settings.load())
        settings.update({"DETECT_MODE": "manual"})
        playlists.save("main", {"NAME": "Main", "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min",
                                "MEMBERS": "111 222"})
        playlists.save("night", {"NAME": "Night", "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min",
                                 "MEMBERS": "333"})
        settings.update({"ACTIVE_PLAYLIST": "main"})
        for wid in ("111", "222", "333"):
            d = paths.default_wallpapers_dir() / wid
            d.mkdir(parents=True, exist_ok=True)
            (d / "project.json").write_text('{"title": "WP %s", "type": "scene", "file": "x"}' % wid,
                                            encoding="utf-8")
            tags.set_state(wid, "WP " + wid, "good")
        self.env_writes: list[str] = []
        for name in ("write_env", "write_files"):
            patcher = mock.patch.object(daemon_unit, name,
                                        lambda *a, _n=name, **k: self.env_writes.append(_n) or "written")
            patcher.start()
            self.addCleanup(patcher.stop)
        self.backend = models.Backend()

    @contextlib.contextmanager
    def engine(self, reply: dict | None, status_class: str = "ok"):
        rec = Recorder(reply, status_class)
        with mock.patch.object(models, "api_client", rec), mock.patch.object(push, "api_client", rec), \
                mock.patch.object(tray, "api_client", rec):
            yield rec

    @contextlib.contextmanager
    def counted_syncs(self):
        runs: list[tuple] = []
        real = push.sync_all

        def counted(*args, **kwargs):
            runs.append((args, kwargs))
            return real(*args, **kwargs)
        with mock.patch.object(push, "sync_all", counted):
            yield runs

    @contextlib.contextmanager
    def held_elsewhere(self, store: str):
        ready, release = threading.Event(), threading.Event()

        def hold() -> None:
            with lock.held(store):
                ready.set()
                release.wait(30)
        holder = threading.Thread(target=hold)
        holder.start()
        try:
            self.assertTrue(ready.wait(10))
            yield
        finally:
            release.set()
            holder.join(10)

    @contextlib.contextmanager
    def systemd(self, fake: FakeUnit, ready_s: float = 0.5, cap_s: float | None = 10.0):
        """The window's restart against `fake`: its readiness waits cut to `ready_s`, its hold capped at
        `cap_s` (the real cap when None)."""
        real = push.wait_ready

        def short(old_pid=None, timeout_s=20.0):
            return real(old_pid=old_pid, timeout_s=min(timeout_s, ready_s))
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(models, "_sandboxed", lambda: False))
            stack.enter_context(mock.patch.object(models.subprocess, "run", fake.run))
            stack.enter_context(mock.patch.object(daemon_unit, "_service_main_pid", fake.main_pid))
            stack.enter_context(mock.patch.object(push, "wait_ready", short))
            if cap_s is not None:
                stack.enter_context(mock.patch.object(models, "_RESTART_CAP_S", cap_s, create=True))
            yield fake

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

    def test_an_away_change_is_delivered_whole_by_the_next_drain(self) -> None:
        with self.engine(None, "away") as rec:
            self.backend.setPaused(True)
        self.assertEqual(rec.verbs(), [])
        self.assertEqual(marker.read()["classes"], ["BUNDLE"])
        self.assertFalse(settings.load()["ROTATION_ENABLED"])
        self.backend._engine_pid_seen = 4242
        with self.engine(status()) as rec:
            self.backend.status()
        self.assertIn(("lanes_set", ([{"id": "all", "playlist": "main", "enabled": False}],), {}), rec.calls)
        for verb in ("playlist_set", "schedule_set", "set_fps", "set_parallax", "set_particles",
                     "set_fullscreen_ignore", "set_app_conditions", "set_fullscreen", "set_speed", "set_volume",
                     "set_mouse", "set_audio", "set_tuning", "set_fit", "set_skip"):
            self.assertIn(verb, rec.verbs(), f"{verb} is part of the bundle")
        self.assertEqual(marker.read()["classes"], [])

    def test_a_pause_emits_the_lane_clock_its_lanes_set_reply_carries(self) -> None:
        clocks: list[tuple[int, int]] = []
        self.backend.rotationClock.connect(lambda ms, iv: clocks.append((ms, iv)))
        with self.engine(status()) as rec:
            rec.answer("lanes_set", {"id": 1, "ok": True, "status": "done",
                                     "result": {"lanes": [{"id": "all", "next_in_ms": 250000, "interval_s": 900}]}})
            self.backend.setPaused(True)
        self.assertEqual(rec.verbs(), ["lanes_set"])
        self.assertEqual(clocks, [(250000, 900)])

    def test_failed_drains_retry_after_5_15_and_45_s_then_stop_until_a_writer_raises_the_generation(self) -> None:
        marker.ensure(("BUNDLE",))
        self.backend._engine_pid_seen = 4242
        clock = Clock()

        def poll(at: float, reply: dict | None, status_class: str = "ok") -> None:
            clock.now = 1000.0 + at
            with self.engine(reply, status_class) as rec:
                rec.answer("set_fps", REFUSED)
                self.backend.status()
        with mock.patch.object(models, "monotonic", clock), self.counted_syncs() as runs:
            seen = []
            for at, reply in ((0, status()), (4, status()), (6, None), (7, None), (8, None), (9, status()),
                              (23, status()), (24, status()), (68, status()), (69, status()), (100, status()),
                              (1000, status())):
                poll(at, reply, "ok" if reply else "away")
                seen.append(len(runs))
            self.assertEqual(seen, [1, 1, 1, 1, 1, 2, 2, 3, 3, 4, 4, 4],
                             "retries after 5 s, 15 s and 45 s, then none; the polls at 6 to 8 s find the engine away")
            with marker.writing(("BUNDLE",)):
                pass
            poll(1001, status())
            self.assertEqual(len(runs), 5, "a raised generation starts the count again")

    def test_the_schedule_follow_never_runs_inside_the_sync_hold(self) -> None:
        settings.update({"SCHEDULE": "07:00=main;20:00=night", "SCHEDULE_ENABLED": True})
        marker.ensure(("BUNDLE",))
        self.backend._engine_pid_seen = 4242
        events: list[tuple] = []
        depth = [0]
        real = lock.held

        @contextlib.contextmanager
        def recording(store, *args, **kwargs):
            events.append((store, depth[0] > 0, kwargs.get("wait_s")))
            with real(store, *args, **kwargs):
                depth[0] += store == "sync"
                try:
                    yield
                finally:
                    depth[0] -= store == "sync"
        reply = status(schedule={"enabled": True}, lanes=[{"id": "all", "playlist": "night"}])
        with mock.patch.object(lock, "held", recording), self.engine(reply):
            self.backend.status()
        self.assertEqual(settings.load()["ACTIVE_PLAYLIST"], "night", "the follow ran")
        self.assertIn("sync", [store for store, _inside, _wait in events], "the drain took sync first")
        follow = [(store, inside) for store, inside, wait in events if store == "settings" and wait == 0]
        self.assertTrue(follow)
        self.assertEqual([inside for _store, inside in follow], [False] * len(follow))

    def test_with_sync_held_by_another_process_a_change_is_pending_and_the_drain_returns_at_once(self) -> None:
        flag = self.home / "held"
        child = subprocess.Popen([sys.executable, "-c", CHILD, str(flag)], env=self._env(),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (child.kill(), child.communicate(timeout=30)))
        deadline = time.monotonic() + 30
        while not flag.exists():
            self.assertIsNone(child.poll(), "the child exited before it held sync")
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.005)
        self.backend._engine_pid_seen = 4242
        with self.engine(status()) as rec:
            start = time.monotonic()
            outcome = self.backend.save_setting("ENGINE_VOLUME", 30)
            waited = time.monotonic() - start
            self.assertEqual((outcome.kind, outcome.reason), ("pending", "busy"))
            self.assertLess(waited, 2.5)
            start = time.monotonic()
            self.backend.status()
            self.assertLess(time.monotonic() - start, 0.5, "the poll never waits for sync")
        self.assertEqual(rec.verbs(), [])
        self.assertEqual(settings.load()["ENGINE_VOLUME"], 30)
        self.assertEqual(marker.read()["classes"], ["BUNDLE"])

    def _finish_restart(self) -> None:
        worker = getattr(self.backend, "_restart_thread", None)
        if worker is not None:
            worker.join(30)
            self.assertFalse(worker.is_alive())
        _APP.processEvents()

    def _competing_drain(self) -> str:
        found = []
        thread = threading.Thread(target=lambda: found.append(push.sync_all("command", wait_s=0.2)), daemon=True)
        thread.start()
        thread.join(5)
        return f"{found[0].kind} {found[0].reason}" if found else "no answer"

    def test_the_switch_and_the_restart_row_record_the_bundle_before_they_launch(self) -> None:
        seen: list = []

        def run(args, **kwargs):
            if "enable" in args or "restart" in args:
                seen.append((args[-2], marker.read()["classes"], self._competing_drain() if "restart" in args else ""))
            return subprocess.CompletedProcess(args, 0, "inactive\n" if "is-active" in args else "", "")

        with mock.patch.object(models, "_sandboxed", lambda: False), \
                mock.patch.object(models.subprocess, "run", run), \
                mock.patch.object(push, "wait_ready", return_value=status(pid=5000)):
            with self.engine(None, "away"):
                self.assertTrue(self.backend.setMaster(True))
            self.backend._ready_timer.stop()
            with self.engine(status(pid=5000)) as rec:
                self.assertTrue(self.backend.restartMaster())
                self._finish_restart()
        self.assertEqual(seen, [("--now", ["BUNDLE"], ""), ("restart", ["BUNDLE"], "pending busy")])
        self.assertIn("lanes_set", rec.verbs())
        self.assertEqual(marker.read()["classes"], [], "the restart's own sync to the new engine clears it")

    def test_a_record_that_cannot_be_written_stops_both_window_launches(self) -> None:
        launched: list = []
        notices: list[str] = []
        self.backend.notice.connect(notices.append)

        def run(args, **kwargs):
            if "enable" in args or "restart" in args:
                launched.append(args)
            return subprocess.CompletedProcess(args, 0, "inactive\n" if "is-active" in args else "", "")

        for failure, text in ((OSError("marker disk full"),
                               "The sync record could not be written (marker disk full), so nothing was started."),
                              (lock.StoreBusy("Store busy: another writer holds marker.lock"),
                               "Store busy: another writer holds marker.lock")):
            with self.subTest(failure=type(failure).__name__):
                notices.clear()
                with mock.patch.object(models, "_sandboxed", lambda: False), \
                        mock.patch.object(models.subprocess, "run", run), \
                        mock.patch.object(marker, "ensure", side_effect=failure), self.engine(status()):
                    results = [self.backend.setMaster(True), self.backend.restartMaster()]
                    self._finish_restart()
                self.assertEqual((results, launched, notices), ([False, False], [], [text, text]))
        self.assertEqual(marker.read()["classes"], [])

    def test_a_restart_with_an_unanswered_old_engine_keeps_its_record_from_a_drain(self) -> None:
        real_read = push.read_status
        first = [True]
        drains: list[str] = []

        def read():
            if first:
                first.pop()
                return "unresponsive", None
            return real_read()

        def run(args, **kwargs):
            if "restart" in args:
                drains.append(self._competing_drain())
            return subprocess.CompletedProcess(args, 0, "", "")

        with self.engine(status(pid=4242)), mock.patch.object(models, "_sandboxed", lambda: False), \
                mock.patch.object(models.subprocess, "run", run), mock.patch.object(push, "read_status", read), \
                mock.patch.object(push, "wait_ready", return_value=None):
            self.assertTrue(self.backend.restartMaster())
            self._finish_restart()
        self.assertEqual((drains, marker.read()["classes"]), (["pending busy"], ["BUNDLE"]))

    def test_a_refused_window_restart_leaves_a_bundle_the_next_poll_clears_once(self) -> None:
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, 1 if "restart" in args else 0, "", "Job failed")

        with self.engine(status(pid=4242)) as rec, mock.patch.object(models, "_sandboxed", lambda: False), \
                mock.patch.object(models.subprocess, "run", run):
            self.assertFalse(self.backend.restartMaster())
            self._finish_restart()
            self.assertEqual(marker.read()["classes"], ["BUNDLE"])
            self.backend._drain()
            sent = len(rec.verbs())
            for _ in range(5):
                self.backend._drain()
        self.assertEqual((marker.read()["classes"], self.backend._drain_failures), ([], 0))
        self.assertGreater(sent, 0)
        self.assertEqual(len(rec.verbs()), sent, "later polls send nothing")

    def _first_read_unresponsive(self):
        real_read = push.read_status
        first = [True]

        def read():
            if first:
                first.pop()
                return "unresponsive", None
            return real_read()
        return mock.patch.object(push, "read_status", read)

    def test_a_restart_never_bundles_the_engine_it_replaces_when_the_first_status_read_fails(self) -> None:
        with self.engine(status(pid=4242)) as rec, self.systemd(FakeUnit(4242)), self._first_read_unresponsive():
            self.assertTrue(self.backend.restartMaster())
            self._finish_restart()
        self.assertEqual((rec.verbs(), marker.read()["classes"]), ([], ["BUNDLE"]))

    def test_a_restart_that_waited_for_sync_reads_the_old_main_pid_once_it_holds_it(self) -> None:
        fake = FakeUnit(4242)
        taken = threading.Event()

        def restart_elsewhere() -> None:
            with lock.held("sync"):
                taken.set()
                for _ in range(200):
                    if self.backend._restart_thread is not None:
                        break
                    time.sleep(0.01)
                rec.status_reply = status(pid=5000)
                fake.main = 5000

        with self.engine(status(pid=4242)) as rec, self.systemd(fake):
            holder = threading.Thread(target=restart_elsewhere, daemon=True)
            holder.start()
            self.assertTrue(taken.wait(5))
            self.assertTrue(self.backend.restartMaster())
            holder.join(5)
            self._finish_restart()
        self.assertEqual((holder.is_alive(), rec.verbs(), marker.read()["classes"]), (False, [], ["BUNDLE"]))

    def test_a_restart_bundles_the_new_engine_once_it_is_the_main_pid_and_never_the_old_one(self) -> None:
        fake = FakeUnit(4242)
        with self.engine(status(pid=4242)) as rec, self.systemd(fake, ready_s=5.0), self._first_read_unresponsive():
            self.assertTrue(self.backend.restartMaster())
            self.backend._restart_thread.join(0.8)
            to_old = list(rec.verbs())
            rec.status_reply = status(pid=5000)
            fake.main = 5000
            self._finish_restart()
        self.assertEqual(to_old, [])
        self.assertIn("lanes_set", rec.verbs())
        self.assertEqual(marker.read()["classes"], [])

    def test_a_restart_keeps_sync_while_its_unit_stops_so_no_drain_reaches_the_old_engine(self) -> None:
        fake = FakeUnit(4242, on_restart="deactivating")
        with self.engine(status(pid=4242)) as rec, self.systemd(fake):
            self.assertTrue(self.backend.restartMaster())
            held = fake.state_read.wait(3)
            self.backend._drain()
            during = (list(rec.verbs()), marker.read()["classes"])
            rec.status_reply = status(pid=5000)
            fake.main, fake.state = 5000, "activating"
            self._finish_restart()
        self.assertEqual((held, during), (True, ([], ["BUNDLE"])))
        self.assertIn("lanes_set", rec.verbs())
        self.assertEqual(marker.read()["classes"], [])

    def test_window_deliveries_while_a_restart_holds_sync_end_pending_at_once(self) -> None:
        parked, release = threading.Event(), threading.Event()
        took, outcomes = {}, {}

        def wait(old_pid=None, timeout_s=20.0):
            parked.set()
            release.wait(15)
            rec.status_reply = status(pid=5000)
            return status(pid=5000)

        with self.engine(status(pid=4242)) as rec, self.systemd(FakeUnit(4242)), \
                mock.patch.object(push, "wait_ready", wait):
            self.assertTrue(self.backend.restartMaster())
            try:
                self.assertTrue(parked.wait(10))
                for name, action in (("save_setting", lambda: self.backend.save_setting("ENGINE_FPS", 45)),
                                     ("deletePlaylist", lambda: self.backend.deletePlaylist("main")),
                                     ("setSessionOverride", lambda: self.backend.setSessionOverride("mute", True)),
                                     ("drain_now", self.backend.drain_now)):
                    start = time.monotonic()
                    outcomes[name] = action()
                    took[name] = time.monotonic() - start
                sent = list(rec.verbs())
            finally:
                release.set()
            self._finish_restart()
        self.assertTrue(all(seconds < 1.0 for seconds in took.values()), took)
        self.assertEqual([(outcomes[name].kind, outcomes[name].reason) for name in ("save_setting", "drain_now")],
                         [("pending", "busy")] * 2)
        self.assertEqual(sent, [])
        self.assertIn((45,), [args for verb, args, _kwargs in rec.calls if verb == "set_fps"])
        self.assertEqual(marker.read()["classes"], [])

    def test_readiness_takes_only_an_engine_whose_pid_is_a_new_non_zero_main_pid(self) -> None:
        # (status pid, MainPID, MainPID before the launch, ready)
        cases = ((4242, 4242, 4242, False), (7000, 5000, 4242, False), (5000, None, None, False),
                 (5000, 5000, 4242, True), (5000, 5000, None, True))
        for pid, main, old, ready in cases:
            with self.subTest(pid=pid, main=main, old=old):
                with self.engine(status(pid=pid)), \
                        mock.patch.object(daemon_unit, "_service_main_pid", return_value=main):
                    self.assertEqual(push.wait_ready(old_pid=old, timeout_s=0.3) is not None, ready)

    def test_engine_only_actions_and_a_second_restart_during_a_restart_are_refused_at_once(self) -> None:
        parked, release = threading.Event(), threading.Event()
        notices: list[str] = []
        self.backend.notice.connect(notices.append)
        fake = FakeUnit(4242)
        took, results = {}, {}

        def wait(old_pid=None, timeout_s=20.0):
            parked.set()
            release.wait(15)
            rec.status_reply = status(pid=5000)
            return status(pid=5000)

        with self.engine(status(pid=4242)) as rec, self.systemd(fake), mock.patch.object(push, "wait_ready", wait):
            self.assertTrue(self.backend.restartMaster())
            first = self.backend._restart_thread
            try:
                self.assertTrue(parked.wait(10))
                for name, action in (("showNow", lambda: self.backend.showNow("111")),
                                     ("rotateNext", self.backend.rotateNext),
                                     ("rotatePrev", self.backend.rotatePrev),
                                     ("setEngineSpeed", lambda: self.backend.setEngineSpeed(2.0)),
                                     ("setAnimationFrozen", lambda: self.backend.setAnimationFrozen(True)),
                                     ("restartMaster", self.backend.restartMaster)):
                    start = time.monotonic()
                    results[name] = action()
                    took[name] = time.monotonic() - start
                sent = list(rec.verbs())
            finally:
                release.set()
            first.join(30)
            self._finish_restart()
        self.assertTrue(all(seconds < 1.0 for seconds in took.values()), took)
        self.assertEqual(results, {"showNow": False, "rotateNext": False, "rotatePrev": False,
                                   "setEngineSpeed": -1.0, "setAnimationFrozen": -1.0, "restartMaster": False})
        self.assertEqual(sent, [])
        self.assertEqual(notices, [f"Store busy: another writer holds {paths.locks_dir() / 'sync.lock'}"])
        self.assertEqual(sum("restart" in call for call in fake.calls), 1, "the second click launched nothing")
        self.assertEqual((first.is_alive(), marker.read()["classes"]), (False, []))

    def test_a_delivery_already_taking_sync_when_a_restart_hold_begins_does_not_wait_for_it(self) -> None:
        go, held, release = threading.Event(), threading.Event(), threading.Event()
        original = lock.held
        errors, seen = [], []
        main = threading.current_thread()

        def restart_hold() -> None:
            try:
                if not go.wait(3):
                    raise AssertionError("the delivery never reached sync")
                with push.restart_hold():
                    held.set()
                    if not release.wait(5):
                        raise AssertionError("the hold was never released")
            except BaseException as exc:
                errors.append(repr(exc))

        @contextlib.contextmanager
        def race(store, wait_s=None, **kwargs):
            if store == "sync" and threading.current_thread() is main:
                seen.append(wait_s)
                go.set()
                self.assertTrue(held.wait(3))
            with original(store, wait_s=wait_s, **kwargs):
                yield

        with self.engine(status()):
            ticket = push.save_change(("settings",), lambda: settings.update({"ENGINE_FPS": 45}),
                                      [(push.SETTING_ROWS["ENGINE_FPS"], "ENGINE_FPS")])
            holder = threading.Thread(target=restart_hold, daemon=True)
            holder.start()
            try:
                with mock.patch.object(lock, "held", race):
                    start = time.monotonic()
                    outcome = push.deliver(ticket)
                    took = time.monotonic() - start
            finally:
                release.set()
                holder.join(5)
        self.assertEqual((seen, errors, holder.is_alive()), ([2.0], [], False), "the wait was chosen before the hold")
        self.assertEqual((outcome.kind, outcome.reason), ("pending", "busy"))
        self.assertLess(took, 1.0)

    def test_a_unit_stuck_stopping_ends_the_hold_at_the_cap_with_bundle_kept(self) -> None:
        clock = Clock()
        fake = FakeUnit(4242, on_restart="deactivating")
        waits: list[float] = []

        def wait(old_pid=None, timeout_s=20.0):
            waits.append(timeout_s)
            clock.now += timeout_s
            if len(waits) > 200:
                fake.state = "inactive"
            return None

        with self.engine(status(pid=4242)) as rec, self.systemd(fake, cap_s=None), \
                mock.patch.object(push, "wait_ready", wait), mock.patch.object(models, "monotonic", clock):
            self.assertTrue(self.backend.restartMaster())
            self._finish_restart()
        self.assertEqual((waits, clock.now - 1000.0), ([20.0] + [1.0] * 100, 120.0))
        self.assertEqual((rec.verbs(), marker.read()["classes"]), ([], ["BUNDLE"]))

    def test_a_restart_keeps_sync_while_its_unit_starts_so_no_drain_reaches_the_old_engine(self) -> None:
        fake = FakeUnit(4242, on_restart="activating")
        with self.engine(status(pid=4242)) as rec, self.systemd(fake):
            self.assertTrue(self.backend.restartMaster())
            held = fake.state_read.wait(3)
            self.backend._restart_thread.join(0.5)
            self.backend._drain()
            during = (list(rec.verbs()), marker.read()["classes"])
            rec.status_reply = status(pid=5000)
            fake.main = 5000
            self._finish_restart()
        self.assertEqual((held, during), (True, ([], ["BUNDLE"])))
        self.assertIn("lanes_set", rec.verbs())
        self.assertEqual(marker.read()["classes"], [])

    def test_every_end_of_a_restart_hold_gives_later_sync_attempts_their_wait_again(self) -> None:
        crashes: list = []
        ends = ("record fails", "record busy", "launch fails", "restart refused", "no new engine", "new engine",
                "sync fails", "readiness raises", "sync held elsewhere")
        for end in ends:
            with self.subTest(end=end):
                fake = FakeUnit(4242)
                real_run = fake.run

                def run(args, _end=end, **kwargs):
                    if "restart" in args and _end == "launch fails":
                        raise OSError("no systemctl")
                    if "restart" in args and _end == "restart refused":
                        fake.calls.append(list(args))
                        return subprocess.CompletedProcess(args, 1, "", "Job failed")
                    return real_run(args, **kwargs)

                def ready(old_pid=None, timeout_s=20.0):
                    rec.status_reply = status(pid=5000)
                    return status(pid=5000)

                with contextlib.ExitStack() as stack:
                    rec = stack.enter_context(self.engine(status(pid=4242)))
                    stack.enter_context(self.systemd(fake))
                    stack.enter_context(mock.patch.object(models.subprocess, "run", run))
                    stack.enter_context(mock.patch.object(threading, "excepthook", crashes.append))
                    if end in ("record fails", "record busy"):
                        failure = OSError("marker disk full") if end == "record fails" else lock.StoreBusy("busy")
                        stack.enter_context(mock.patch.object(marker, "ensure", side_effect=failure))
                    if end in ("new engine", "sync fails"):
                        stack.enter_context(mock.patch.object(push, "wait_ready", ready))
                    if end == "sync fails":
                        stack.enter_context(mock.patch.object(push, "sync_all", side_effect=OSError("socket gone")))
                    if end == "readiness raises":
                        stack.enter_context(mock.patch.object(push, "wait_ready", side_effect=RuntimeError("broke")))
                    if end == "sync held elsewhere":
                        stack.enter_context(mock.patch.object(lock, "LOCK_WAIT_S", 0.2))
                        stack.enter_context(self.held_elsewhere("sync"))
                    self.backend.restartMaster()
                    self._finish_restart()
                self.assertEqual((push._restart_holding.is_set(), "sync" in lock._long_held), (False, False))
        self.assertEqual([type(crash.exc_value).__name__ for crash in crashes], ["RuntimeError"])
        with self.engine(status()), mock.patch.object(lock, "LOCK_WAIT_S", 0.3), self.held_elsewhere("sync"):
            start = time.monotonic()
            outcome = self.backend.save_setting("ENGINE_FPS", 45)
            took = time.monotonic() - start
        self.assertEqual((outcome.kind, outcome.reason), ("pending", "busy"))
        self.assertGreaterEqual(took, 0.25, "the delivery waited for the plain holder again")

    def test_the_service_switch_rebuilds_first_and_bundles_once_the_engine_answers(self) -> None:
        events: list = []
        notices: list[str] = []
        self.backend.notice.connect(notices.append)

        def run(args, **kwargs):
            events.append(list(args))
            return subprocess.CompletedProcess(args, 0, "inactive\n" if "is-active" in args else "", "")

        def failing(exc):
            def write_files(*a, **k):
                events.append("write_files")
                raise exc
            return write_files
        with mock.patch.object(models, "_sandboxed", lambda: False), \
                mock.patch.object(models.subprocess, "run", run):
            for exc in (ValueError("no engine binary found"),
                        RuntimeError("systemd daemon-reload failed: exit 1; run systemctl --user daemon-reload")):
                with self.subTest(failure=type(exc).__name__):
                    events.clear()
                    notices.clear()
                    with mock.patch.object(daemon_unit, "write_files", failing(exc)):
                        self.assertFalse(self.backend.setMaster(True))
                        self.assertFalse(self.backend.restartMaster())
                    self.assertEqual(events, ["write_files", "write_files"], "no systemctl ran")
                    self.assertEqual(notices, [f"Engine service config not updated: {exc}"] * 2)
            events.clear()
            with mock.patch.object(daemon_unit, "write_files", lambda *a, **k: events.append("write_files")), \
                    self.counted_syncs() as runs:
                with self.engine(None, "away"):
                    self.assertTrue(self.backend.setMaster(True))
                enable = next(i for i, e in enumerate(events) if isinstance(e, list) and "enable" in e)
                self.assertLess(events.index("write_files"), enable, "engine-env and the unit come first")
                self.assertEqual(marker.read()["classes"], ["BUNDLE"], "the marker is set before the wait")
                self.assertTrue(self.backend._ready_timer.isActive())
                with self.engine(None, "away"):
                    self.backend._ready_tick()
                self.assertEqual(runs, [])
                with self.engine(status()):
                    self.backend._ready_tick()
                self.assertEqual(len(runs), 1, "one sync_all once the engine answers")
                self.assertFalse(self.backend._ready_timer.isActive())
                self.assertEqual(marker.read()["classes"], [])
                waited: list = []

                def ready(old_pid=None, timeout_s=20.0):
                    waited.append(old_pid)
                    return status(pid=200)

                with self.engine(status(pid=100)), mock.patch.object(push, "wait_ready", ready), \
                        mock.patch.object(daemon_unit, "_service_main_pid", return_value=90):
                    self.assertTrue(self.backend.restartMaster())
                    self._finish_restart()
                self.assertEqual(waited, [90], "the restart waits for an engine other than the old MainPID")
                self.assertEqual(len(runs), 2, "the new pid runs the bundle")
                self.assertFalse(self.backend._ready_timer.isActive())

    def test_a_manual_switch_while_away_with_the_schedule_on_writes_nothing(self) -> None:
        settings.update({"SCHEDULE": "07:00=main;20:00=night", "SCHEDULE_ENABLED": True})
        before = paths.settings_file().read_bytes()
        with self.engine(None, "away") as rec, self.assertLogs("lwe_ui.models", "WARNING") as logged:
            self.backend.setActivePlaylist("night")
            self.assertEqual(self.backend.createPlaylist("Fresh"), "")
            self.backend.deleteActivePlaylist()
        self.assertEqual(len(logged.records), 3)
        self.assertEqual(paths.settings_file().read_bytes(), before)
        self.assertEqual(sorted(p["slug"] for p in playlists.list_playlists()), ["main", "night"])
        self.assertIsNone(marker.read()["generation"])
        self.assertEqual(rec.verbs(), [])

    def test_a_switch_to_a_playlist_being_deleted_waits_for_the_delete(self) -> None:
        real_active, real_delete, real_modify, real_save = (playlists.active_slug, playlists.delete, settings.modify,
                                                            push.save_change)
        main = threading.current_thread()
        for schedule in ("", "07:00=main;20:00=night"):
            with self.subTest(schedule=schedule):
                playlists.save("night", {"NAME": "Night", "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min",
                                         "MEMBERS": "333"})
                settings.update({"ACTIVE_PLAYLIST": "main", "SCHEDULE": schedule, "SCHEDULE_ENABLED": False})
                switches: list[threading.Thread] = []
                errors: list[Exception] = []
                rows: list = []
                repointed: list = []

                def raced_read(validate=True):
                    found = real_active(validate)
                    if threading.current_thread() is main and not switches:
                        switch = threading.Thread(target=settings.update, args=({"ACTIVE_PLAYLIST": "night"},))
                        switches.append(switch)
                        switch.start()
                        switch.join(0.5)
                    return found

                def watched_delete(slug):
                    try:
                        return real_delete(slug)
                    except Exception as exc:
                        errors.append(exc)
                        raise

                def watched_modify(fn):
                    before = settings.load()["ACTIVE_PLAYLIST"]
                    out = real_modify(fn)
                    if threading.current_thread() is main and settings.load()["ACTIVE_PLAYLIST"] != before:
                        repointed.append(list(rows[-1]) if rows else [])
                    return out

                def watched_save(locks, write, change_rows, **kwargs):
                    rows.append(list(change_rows))
                    return real_save(locks, write, change_rows, **kwargs)

                with self.engine(status()), mock.patch.object(playlists, "active_slug", raced_read), \
                        mock.patch.object(playlists, "delete", watched_delete), \
                        mock.patch.object(settings, "modify", watched_modify), \
                        mock.patch.object(push, "save_change", watched_save):
                    self.backend.deletePlaylist("night")
                    switches[0].join(10)
                self.assertEqual(errors, [])
                self.assertTrue(all(("active", "ACTIVE_PLAYLIST") in r for r in repointed), repointed)

    def test_a_hand_broken_store_line_refuses_the_change_before_any_request(self) -> None:
        text = paths.settings_file().read_text(encoding="utf-8")
        paths.settings_file().write_text(text + 'ENGINE_VOLUME="40\n', encoding="utf-8")
        with self.engine(status()) as rec:
            with self.assertRaises(ValueError):
                self.backend.setSetting("ENGINE_VOLUME", 30)
        self.assertEqual(rec.verbs(), [])
        paths.settings_file().write_text(text, encoding="utf-8")
        self.backend.orderModel.setColumns(3)
        self.assertTrue(self.backend.beginDrag("222"))
        self.backend.dragOver(0)
        paths.playlist_file("main").write_text('NAME=Main\nMODE="shuffle\nINTERVAL=900\nMEMBERS="111 222\n',
                                               encoding="utf-8")
        with self.engine(status()) as rec, self.assertLogs("lwe_ui.models", "WARNING") as logged:
            self.backend.setPlaylistMode("static")
            self.assertEqual(self.backend.endDrag(True), "none")
        self.assertEqual(len(logged.records), 2)
        self.assertEqual(rec.verbs(), [])

    def test_the_trays_next_and_pause_send_nothing_while_sync_is_held_elsewhere(self) -> None:
        shell = types.SimpleNamespace(_status=lambda: status(speed=0.0))
        with self.engine(status()) as rec, mock.patch.object(lock, "LOCK_WAIT_S", 0.2), \
                self.held_elsewhere("sync"), self.assertLogs("lwe_ui.tray", "WARNING") as logged:
            tray.TrayProcess._next(shell)
            tray.TrayProcess._toggle_pause(shell)
        self.assertEqual(rec.verbs(), [])
        self.assertEqual(len(logged.records), 2)

    def test_an_import_pass_and_a_preset_repair_run_one_sync_all_each(self) -> None:
        with self.engine(status()) as rec, self.counted_syncs() as runs:
            bridge = models.ImportBridge(self.backend)
            bridge._finish(1, 1)
            self.assertEqual(len(runs), 1)
            self.assertIn("playlist_set", rec.verbs())
            bridge._on_repair_done(1)
            self.assertEqual(len(runs), 2)

    def test_the_start_up_reconcile_writes_engine_env_only(self) -> None:
        tree = ast.parse((SRC / "lwe_ui" / "app.py").read_text(encoding="utf-8"))
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and isinstance(node.func.value, ast.Name) and node.func.value.id == "daemon_unit"}
        self.assertIn("write_env", called)
        self.assertFalse(called & {"write_files", "reconcile_env"}, called)

    def test_a_settings_reset_sends_the_bundle_with_one_reshow_and_rewrites_engine_env(self) -> None:
        wp.write_keys("111", {"BG": "111", "SKIP": "7"})
        with self.engine(status(speed=0.0)) as rec:
            self.assertTrue(self.backend.resetConfig())
        verbs = rec.verbs()
        self.assertEqual(verbs.count("show"), 1)
        self.assertEqual(verbs[verbs.index("show"):], ["show", "set_tuning", "set_skip", "set_speed"])
        self.assertEqual(rec.calls[-1], ("set_speed", (0.0,), {}), "the re-show keeps the freeze")
        self.assertIn("set_fps", verbs)
        self.assertNotIn("set_speed", verbs[:verbs.index("show")], "the bundle leaves speed out at 0")
        self.assertEqual(self.env_writes, ["write_env"])
        self.assertEqual(marker.read()["classes"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
