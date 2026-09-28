"""next, prev and pause through the command entry.

next and prev read status first, then send the engine's next or prev under the sync lock, held until
the final reply, and print what is on screen from one more status read; they write nothing, leave the
playlist timer alone and send no lanes-set. pause saves ROTATION_ENABLED through the change runner and
sends one lanes-set with the lane's enabled state, never set-speed; a value already saved is neither
written nor sent. The engine is always tests/_fake_engine.py on a socket the test made. "The only
request" means apart from status reads, and the marker is read as engine/marker.py defines it: it exists while it
holds a class.

In-process forms run through cli.main with HOME and the XDG folders at scratch (_cli_env.scratch_home)
and the fake engine on the socket _sandbox pins; daemon_unit's subprocess call, screens and live
environment are replaced, so it could not reach the host even if it ran. Child runs get an environment
built from nothing (_cli_env.scratch_env) with PySide6 blocked: two toggles at once, and next and prev
holding the sync lock while a delayed final reply is pending.

Run: PYTHONPATH=src python3 tests/test_cli_running.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import _cli_env
import _fake_engine
from _fake_engine import done, fail, silent

ROOT = Path(tempfile.mkdtemp(prefix="lwe-cli-running-"))
HOME = _cli_env.scratch_home(ROOT / "inproc")
DEEP = {"id": "1505438974", "ui_id": "1505438974", "title": "Deep Space"}
SHOWN = {"id": "1505438974", "ui_id": "1505438974"}
CLOCK = {"enabled": True, "interval_s": 900, "next_in_s": 422, "order": "shuffle", "count": 2, "label": "Chill"}
STATIC = {"enabled": False, "interval_s": 900, "next_in_s": -1, "order": "static", "count": 2, "label": "Chill"}
OPPORTUNITIES = ("It can apply the next time the panel window opens or polls, an lwe command saves a setting the "
                 "engine uses, lwe reload or a backup import runs, or lwe service start or restart starts the "
                 "engine.")


def pending(value: str) -> str:
    return f"pause {value}: saved; the service is not running or is busy, so it is not applied yet. {OPPORTUNITIES}\n"


def tearDownModule() -> None:
    shutil.rmtree(ROOT, True)


class RunningCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui import api_client, cli, version
        from lwe_ui.engine import daemon_unit, marker
        from lwe_ui.storage import lock, paths, playlists
        cls.api_client, cls.cli, cls.stamp = api_client, cli, version.panel_stamp()
        cls.daemon_unit, cls.marker, cls.lock, cls.paths, cls.playlists = daemon_unit, marker, lock, paths, playlists
        cls.conf = paths.settings_file()

    def setUp(self) -> None:
        for folder in (self.paths.config_dir(), self.paths.state_dir()):
            shutil.rmtree(folder, True)
            folder.mkdir(parents=True)
        self.runs: list = []
        du = self.daemon_unit
        for patcher in (mock.patch.object(du.subprocess, "run", lambda argv, **kw: self.runs.append(argv)),
                        mock.patch.object(du, "enumerate_outputs", lambda: ["DP-1"]),
                        mock.patch.object(du, "live_engine_env", lambda: None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.playlist("shuffle")
        self.store("ENGINE_FPS=30\nACTIVE_PLAYLIST=chill\nROTATION_ENABLED=true\n")

    def tearDown(self) -> None:
        self.assertEqual(self.runs, [], "a subprocess ran")

    def playlist(self, mode: str) -> None:
        self.playlists.save("chill", {"NAME": "Chill", "MODE": mode, "INTERVAL": 900, "UNIT": "min",
                                      "MEMBERS": "1505438974 1505438975"})

    def store(self, text: str) -> None:
        self.conf.write_text(text, encoding="utf-8")

    def engine(self, **fields) -> _fake_engine.FakeEngine:
        engine = _fake_engine.FakeEngine(_sandbox.SOCKET, **fields)
        self.addCleanup(engine.stop)
        return engine

    def lwe(self, *words: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(list(words), sender_stamp=self.stamp)
        return code, out.getvalue(), err.getvalue()

    @staticmethod
    def requests(engine: _fake_engine.FakeEngine) -> list:
        """Every request the engine got but the status reads."""
        return [call for call in engine.calls if call[0] != "status"]

    def marker_classes(self) -> list:
        return self.marker.read()["classes"]


class NextPrevTest(RunningCase):
    def test_next_waits_past_accepted_for_done_then_prints_what_is_on_screen(self) -> None:
        engine = self.engine(current=DEEP)
        engine.script("next", done(SHOWN, accepted=True, delay=0.3))
        before = self.conf.read_bytes()
        self.assertEqual(self.lwe("next"), (0, "Now showing Deep Space (1505438974)\n", ""))
        self.assertEqual(engine.calls, [("status", {}), ("next", {}), ("status", {})])
        self.assertEqual(self.conf.read_bytes(), before)
        self.assertEqual(self.marker_classes(), [])

    def test_a_done_without_a_status_answer_after_it_names_the_id_from_the_reply(self) -> None:
        engine = self.engine(current=DEEP)
        engine.script("status", done(engine.status()), silent())
        engine.script("next", done(SHOWN, accepted=True))
        with mock.patch.object(self.api_client, "_TIMEOUT", 0.3):
            self.assertEqual(self.lwe("next"), (0, "Now showing 1505438974 (1505438974)\n", ""))

    def test_json_gives_the_ids_and_the_title(self) -> None:
        engine = self.engine(current={"id": "1505438974", "ui_id": "preset-1", "title": "Deep Space"})
        engine.script("prev", done({"id": "1505438974", "ui_id": "preset-1"}, accepted=True))
        self.assertEqual(self.lwe("prev", "-j"),
                         (0, '{"id":"1505438974","ui_id":"preset-1","title":"Deep Space"}\n', ""))

    def test_a_refusal_after_the_ack_exits_1_with_the_reason_and_the_hint(self) -> None:
        engine = self.engine()
        engine.script("next", fail("rotation set is empty", after_accepted=True))
        self.assertEqual(self.lwe("next"), (1, "", "rotation set is empty\n"
                                             "The service has no playlist loaded; lwe reload sends yours.\n"))
        engine.script("next", fail("rotation set is empty", after_accepted=True))
        self.assertEqual(self.lwe("next", "-j"), (1, "", '{"error":"rotation set is empty"}\n'))
        self.assertEqual(self.requests(engine), [("next", {}), ("next", {})])

    def test_prev_refused_before_the_ack_exits_1(self) -> None:
        engine = self.engine()
        engine.script("prev", fail("nothing to go back to"))
        self.assertEqual(self.lwe("prev"), (1, "", "nothing to go back to\n"))
        self.assertEqual(self.requests(engine), [("prev", {})])

    def test_next_and_prev_leave_a_paused_timer_alone(self) -> None:
        self.store("ENGINE_FPS=30\nACTIVE_PLAYLIST=chill\nROTATION_ENABLED=false\n")
        before = self.conf.read_bytes()
        engine = self.engine(current=DEEP)
        for verb in ("next", "prev"):
            engine.script(verb, done(SHOWN, accepted=True))
            self.assertEqual(self.lwe(verb), (0, "Now showing Deep Space (1505438974)\n", ""))
        self.assertEqual(self.requests(engine), [("next", {}), ("prev", {})])
        self.assertEqual(self.conf.read_bytes(), before)
        self.assertEqual(self.marker_classes(), [])

    def test_engine_absent_exits_2_writing_nothing_and_leaving_no_marker(self) -> None:
        before = self.conf.read_bytes()
        for verb in ("next", "prev"):
            self.assertEqual(self.lwe(verb), (2, "", "the service is not running\n"))
        self.assertEqual(self.conf.read_bytes(), before)
        self.assertFalse((self.paths.panel_state_dir() / "sync-pending").exists())
        self.assertEqual(self.marker_classes(), [])

    def test_a_word_after_next_or_prev_exits_3_before_any_request(self) -> None:
        engine = self.engine()
        self.assertEqual(self.lwe("next", "now"), (3, "", "next takes no words; got now\n"))
        self.assertEqual(self.lwe("prev", "1", "2"), (3, "", "prev takes no words; got 1 2\n"))
        self.assertEqual(engine.calls, [])

    def test_a_running_engine_from_another_build_is_refused_before_any_request(self) -> None:
        engine = self.engine(version="9.9.9")
        before = self.conf.read_bytes()
        refusal = f"The running engine is 9.9.9 but {self.stamp} is installed; run lwe service restart.\n"
        self.assertEqual(self.lwe("next"), (1, "", refusal))
        self.assertEqual(self.lwe("pause"), (1, "", refusal))
        self.assertEqual(engine.calls, [("status", {}), ("status", {})])
        self.assertEqual(self.conf.read_bytes(), before)
        self.assertEqual(self.marker_classes(), [])

    def test_sync_held_elsewhere_refuses_as_busy(self) -> None:
        engine = self.engine()
        held, release = threading.Event(), threading.Event()

        def hold() -> None:
            with self.lock.held("sync"):
                held.set()
                release.wait(10)

        holder = threading.Thread(target=hold)
        holder.start()
        try:
            self.assertTrue(held.wait(5))
            self.assertEqual(self.lwe("next"), (1, "", "the service is busy\n"))
        finally:
            release.set()
            holder.join()
        self.assertEqual(self.requests(engine), [])

    def test_accepted_without_a_final_reply_in_time_exits_1(self) -> None:
        engine = self.engine()
        engine.script("next", done(SHOWN, accepted=True, delay=5.0))
        with mock.patch.object(self.api_client, "_DONE_TIMEOUT", 0.3):
            self.assertEqual(self.lwe("next"), (1, "", "accepted but not finished\n"))

    def test_nothing_after_sending_is_uncertain_not_accepted(self) -> None:
        engine = self.engine()
        engine.script("next", silent())
        with mock.patch.object(self.api_client, "_TIMEOUT", 0.3):
            self.assertEqual(self.lwe("next"), (1, "", "the engine did not answer in time, so it may have applied\n"))
        self.assertEqual(self.requests(engine), [("next", {})])

    def test_a_status_read_without_an_answer_exits_1(self) -> None:
        engine = self.engine()
        engine.script("status", silent())
        with mock.patch.object(self.api_client, "_TIMEOUT", 0.3):
            self.assertEqual(self.lwe("next"), (1, "", "the service is not answering\n"))
        self.assertEqual(self.requests(engine), [])


class PauseTest(RunningCase):
    def test_each_form_changes_only_its_line_and_sends_one_lanes_set(self) -> None:
        engine = self.engine()
        forms = [(("pause",), "false", False), (("pause", "off"), "true", True), (("pause", "on"), "false", False),
                 (("pause", "toggle"), "true", True), (("pause", "toggle"), "false", False)]
        for words, saved, enabled in forms:
            with self.subTest(words=words):
                engine.calls.clear()
                code, _out, err = self.lwe(*words)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(self.conf.read_text(encoding="utf-8"),
                                 f"ENGINE_FPS=30\nACTIVE_PLAYLIST=chill\nROTATION_ENABLED={saved}\n")
                self.assertEqual(self.requests(engine), [("lanes-set", {"lanes": [{"id": "all", "enabled": enabled}]})])
                self.assertEqual(self.marker_classes(), [])

    def assert_unchanged(self, engine: _fake_engine.FakeEngine, words: tuple, out: str) -> None:
        """words find the value already saved: nothing written, no marker change and no request."""
        before, sent, generation = self.conf.read_bytes(), self.requests(engine), self.marker.read()["generation"]
        self.assertEqual(self.lwe(*words), (0, out, ""))
        self.assertEqual((self.conf.read_bytes(), self.requests(engine)), (before, sent))
        self.assertEqual(self.marker.read()["generation"], generation)

    def test_receipts_and_an_unchanged_value(self) -> None:
        engine = self.engine(rotation=CLOCK)
        self.assertEqual(self.lwe("pause"), (0, "Playlist timer paused.\n", ""))
        self.assertEqual(self.lwe("pause", "off"), (0, "Playlist timer running again; next change in 7m 02s.\n", ""))
        self.assert_unchanged(engine, ("pause", "off"), "already running\n")
        self.assertEqual(self.lwe("pause", "on", "-j"), (0, '{"paused":true,"saved":true,"outcome":"applied",'
                                                          '"reason":null}\n', ""))
        self.assert_unchanged(engine, ("pause",), "already paused\n")
        self.assert_unchanged(engine, ("pause", "-j"), '{"paused":true,"saved":false,"outcome":null,"reason":null}\n')

    def test_a_frozen_engine_gets_no_set_speed(self) -> None:
        engine = self.engine(speed=0.0, current=DEEP)
        self.assertEqual(self.lwe("pause")[0], 0)
        self.assertEqual(self.lwe("pause", "off")[0], 0)
        self.assertEqual([call[0] for call in self.requests(engine)], ["lanes-set", "lanes-set"])

    def test_a_value_typed_wrong_exits_3_before_anything(self) -> None:
        engine = self.engine()
        before = self.conf.read_bytes()
        self.assertEqual(self.lwe("pause", "maybe"), (3, "", "pause takes on, off or toggle; got maybe\n"))
        self.assertEqual(self.lwe("pause", "on", "now"), (3, "", "pause takes one value; got on now\n"))
        self.assertEqual(engine.calls, [])
        self.assertEqual(self.conf.read_bytes(), before)

    def test_engine_absent_saves_and_sets_the_marker(self) -> None:
        self.assertEqual(self.lwe("pause"), (0, pending("on"), ""))
        self.assertEqual(self.conf.read_text(encoding="utf-8"),
                         "ENGINE_FPS=30\nACTIVE_PLAYLIST=chill\nROTATION_ENABLED=false\n")
        self.assertEqual(self.marker_classes(), ["BUNDLE"])
        self.assertEqual(self.lwe("pause", "off", "-j"),
                         (0, '{"paused":false,"saved":true,"outcome":"pending","reason":"away"}\n', ""))

    def test_an_engine_from_another_build_at_delivery_leaves_the_change_saved_and_pending(self) -> None:
        engine = self.engine()
        engine.script("status", done(engine.status()), done({**engine.status(), "version": "9.9.9"}))
        refusal = f"The running engine is 9.9.9 but {self.stamp} is installed; run lwe service restart.\n"
        self.assertEqual(self.lwe("pause"), (1, "", refusal))
        self.assertIn("ROTATION_ENABLED=false\n", self.conf.read_text(encoding="utf-8"))
        self.assertEqual(self.marker_classes(), ["BUNDLE"])
        self.assertEqual(self.requests(engine), [])

    def test_a_static_playlist_saves_and_says_the_timer_is_not_running(self) -> None:
        self.playlist("static")
        self.store("ENGINE_FPS=30\nACTIVE_PLAYLIST=chill\nROTATION_ENABLED=false\n")
        engine = self.engine(rotation=STATIC)
        self.assertEqual(self.lwe("pause", "off"),
                         (0, "Playlist timer on; the playlist is static, so the timer is not running anyway.\n", ""))
        self.assertIn("ROTATION_ENABLED=true\n", self.conf.read_text(encoding="utf-8"))
        self.assertEqual(self.lwe("pause", "on"),
                         (0, "Playlist timer paused; the playlist is static, so the timer is not running anyway.\n",
                          ""))
        self.assertEqual(self.requests(engine), [("lanes-set", {"lanes": [{"id": "all", "enabled": False}]})] * 2)


class ChildTest(unittest.TestCase):
    """Commands in children whose environment is built from nothing, with PySide6 blocked."""

    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui import version
        cls.stamp = version.panel_stamp()

    def lwe(self, env: dict, cwd: Path, *words: str) -> subprocess.Popen:
        return subprocess.Popen([sys.executable, "-m", "lwe_ui", "--lwe", self.stamp, str(cwd), *words], env=env,
                                cwd=env["HOME"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")

    def test_two_toggles_at_once_flip_twice(self) -> None:
        root = ROOT / "race"
        env = _cli_env.scratch_env(root)
        bite = subprocess.run([sys.executable, "-c", "import PySide6"], env=env, capture_output=True, text=True,
                              timeout=60)
        self.assertNotEqual(bite.returncode, 0)
        self.assertIn("ImportError", bite.stderr)
        conf = Path(env["XDG_CONFIG_HOME"]) / "lwe" / "settings.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text("ROTATION_ENABLED=true\n", encoding="utf-8")
        children = [self.lwe(env, root, "pause", "toggle") for _ in range(2)]
        results = sorted((child.communicate(timeout=60), child.returncode) for child in children)
        self.assertEqual(results, sorted([((pending("on"), ""), 0), ((pending("off"), ""), 0)]))
        self.assertEqual(conf.read_text(encoding="utf-8"), "ROTATION_ENABLED=true\n")
        state = json.loads((Path(env["XDG_STATE_HOME"]) / "lwe" / "panel" / "sync-pending").read_text(encoding="utf-8"))
        self.assertEqual(state["classes"], ["BUNDLE"])

    def test_next_and_prev_hold_the_sync_lock_until_the_final_reply(self) -> None:
        root = ROOT / "hold"
        env = _cli_env.scratch_env(root)
        probe = ("from lwe_ui.storage import lock\n"
                 "try:\n"
                 "    with lock.held('sync', wait_s=0):\n"
                 "        print('free')\n"
                 "except lock.StoreBusy:\n"
                 "    print('busy')\n")
        with _fake_engine.FakeEngine(env["LWE_SOCKET"], current=DEEP) as engine:
            for verb in ("next", "prev"):
                with self.subTest(verb=verb):
                    engine.script(verb, done(SHOWN, accepted=True, delay=3.0))
                    child = self.lwe(env, root, verb)
                    deadline = time.monotonic() + 20
                    while (verb, {}) not in engine.calls and time.monotonic() < deadline:
                        time.sleep(0.02)
                    during = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True,
                                            timeout=60)
                    out, err = child.communicate(timeout=60)
                    after = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True,
                                           timeout=60)
                    self.assertEqual((child.returncode, out, err), (0, "Now showing Deep Space (1505438974)\n", ""))
                    self.assertEqual((during.stdout, during.stderr), ("busy\n", ""))
                    self.assertEqual((after.stdout, after.stderr), ("free\n", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
