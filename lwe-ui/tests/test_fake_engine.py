"""tests/_fake_engine.py through the panel's real client (api_client): connections served one request
each in arrival order and recorded, scripted replies, failures before and after the accepted line,
silence and late answers as no answer, status from the fields the test sets, and stop() removing the
socket.

Every socket is one this test made under a scratch folder; HOME and the XDG folders point at scratch
first (_cli_env).

Run: PYTHONPATH=src python3 tests/test_fake_engine.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import _cli_env
import _fake_engine

ROOT = Path(tempfile.mkdtemp(prefix="lwe-fake-engine-"))
HOME = _cli_env.scratch_home(ROOT)


class FakeEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui import api_client, version
        cls.api, cls.stamp = api_client, version.panel_stamp()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(ROOT, True)

    def setUp(self) -> None:
        self.sock = ROOT / "rt" / "engine.sock"
        self.engine = _fake_engine.FakeEngine(self.sock)

    def tearDown(self) -> None:
        self.engine.stop()

    def request(self, cmd, args=None, wait_done=True):
        return self.api.request(cmd, args, wait_done=wait_done, sock=self.sock)

    def test_sequential_calls_are_answered_and_recorded_in_order(self) -> None:
        self.engine.set(speed=0.5)
        self.assertEqual(self.request("set-fps", {"fps": 30}), {"id": 1, "ok": True, "status": "done", "result": {}})
        status = self.api.status(sock=self.sock)
        self.assertEqual((status["speed"], status["version"]), (0.5, self.stamp))
        for n in range(20):
            self.request("next", {"n": n})
        self.assertEqual(self.engine.calls, [("set-fps", {"fps": 30}), ("status", {})]
                         + [("next", {"n": n}) for n in range(20)])

    def test_each_request_takes_the_next_step_then_the_default(self) -> None:
        self.engine.script("next", _fake_engine.done({"id": "a"}), _fake_engine.fail("no next wallpaper"))
        self.assertEqual(self.request("next")["result"], {"id": "a"})
        self.assertEqual(self.request("next"), {"id": 1, "ok": False, "error": "no next wallpaper"})
        self.assertEqual(self.request("next")["result"], {})

    def test_a_failure_after_accepted_reads_as_the_failure(self) -> None:
        self.engine.script("show", _fake_engine.fail("scene failed to load", after_accepted=True),
                           _fake_engine.fail("scene failed to load", after_accepted=True),
                           _fake_engine.done({"shown": "x"}, accepted=True))
        self.assertEqual(self.request("show", {"id": "x"}), {"id": 1, "ok": False, "error": "scene failed to load"})
        self.assertEqual(self.request("show", {"id": "x"}, wait_done=False),
                         {"id": 1, "ok": True, "status": "accepted"})
        self.assertEqual(self.request("show", {"id": "x"})["result"], {"shown": "x"})

    def test_silence_and_a_late_answer_are_no_answer(self) -> None:
        self.engine.script("status", _fake_engine.silent(), _fake_engine.done(delay=0.6), _fake_engine.done(delay=0.05))
        with mock.patch.object(self.api, "_TIMEOUT", 0.2):
            for label in ("silent", "late"):
                with self.subTest(step=label):
                    start = time.monotonic()
                    self.assertIsNone(self.api.status(sock=self.sock))
                    self.assertLess(time.monotonic() - start, 2.0)
        self.assertEqual(self.api.status(sock=self.sock)["version"], self.stamp)
        self.assertEqual(self.engine.calls, [("status", {})] * 3)

    def test_status_is_built_from_the_fields_the_test_sets(self) -> None:
        lanes = [{"id": "all", "playlist": "night"}]
        self.engine.set(version=None, lanes=lanes, schedule={"enabled": True}, audio_smooth=40.0)
        status = self.api.status(sock=self.sock)
        self.assertNotIn("version", status)
        self.assertEqual((status["lanes"], status["schedule"], status["audio_smooth"]), (lanes, {"enabled": True}, 40.0))
        with self.assertRaises(TypeError):
            self.engine.set(schedul={})

    def test_stop_removes_the_socket(self) -> None:
        self.assertTrue(self.sock.is_socket())
        self.engine.stop()
        self.assertFalse(self.sock.exists())
        with mock.patch.object(self.api, "_TIMEOUT", 0.2):
            self.assertIsNone(self.request("status"))


if __name__ == "__main__":
    unittest.main()
