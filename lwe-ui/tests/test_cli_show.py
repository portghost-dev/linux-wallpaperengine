"""show <w> through the command entry.

show picks one wallpaper by the rules in cli/select.py and prints the pick line; once status answers it sends the
wallpaper through push.show_final under the sync lock, held until the load finishes, and set-tuning only
after a done ok. It starts no service and writes nothing. The engine is always tests/_fake_engine.py on a
socket the test made. A complete download in the scratch Workshop folder, numbered after the pool, is
refused before anything is sent.

In-process forms run through cli.main with HOME and the XDG folders at scratch (_cli_env.scratch_home), a
scratch library named in settings.conf and the fake engine on the socket _sandbox pins; daemon_unit's
subprocess call, screens and live environment are replaced. Child runs get an environment built from
nothing (_cli_env.scratch_env) with PySide6 blocked: a stopped service with a recording systemctl first on
PATH, and show holding the sync lock while a delayed final reply is pending.

Run: PYTHONPATH=src python3 tests/test_cli_show.py
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
from _fake_engine import done, fail

ROOT = Path(tempfile.mkdtemp(prefix="lwe-cli-show-"))
HOME = _cli_env.scratch_home(ROOT / "inproc")
POOL = (("1505438971", "Aurora"), ("1505438974", "Deep Space"), ("1505438976", "Twin"), ("1505438977", "Twin"))
LINE = "2 = Deep Space (1505438974)"


def item(folder: Path, title: str) -> None:
    folder.mkdir(parents=True)
    (folder / "project.json").write_text(json.dumps({"title": title, "type": "scene", "file": "scene.json"}),
                                         encoding="utf-8")
    (folder / "scene.pkg").write_bytes(b"x" * 16)


def tearDownModule() -> None:
    shutil.rmtree(ROOT, True)


class ShowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui import api_client, cli, version
        from lwe_ui.engine import daemon_unit, resolve
        from lwe_ui.storage import lock, paths, settings, tags
        cls.api_client, cls.cli, cls.stamp = api_client, cli, version.panel_stamp()
        cls.daemon_unit, cls.resolve, cls.lock, cls.paths = daemon_unit, resolve, lock, paths
        paths.ensure_dirs()
        settings.ensure_exists()
        settings.save({**settings.load(), "WALLPAPERS_DIR": str(ROOT / "lib"), "WORKSHOP_DIR": str(ROOT / "ws")})
        for wid, title in POOL:
            item(ROOT / "lib" / wid, title)
            tags.set_state(wid, title, "good")
        item(ROOT / "ws" / "1505438990", "Nebula")

    def setUp(self) -> None:
        self.runs: list = []
        du = self.daemon_unit
        for patcher in (mock.patch.object(du.subprocess, "run", lambda argv, **kw: self.runs.append(argv)),
                        mock.patch.object(du, "enumerate_outputs", lambda: ["DP-1"]),
                        mock.patch.object(du, "live_engine_env", lambda: None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.before = self.snapshot()

    def tearDown(self) -> None:
        self.assertEqual(self.runs, [], "a subprocess ran")
        self.assertEqual(self.snapshot(), self.before, "show wrote a file")

    def snapshot(self) -> dict:
        """Every file under the config and state folders but the lock sidecars."""
        out = {}
        for folder in (self.paths.config_dir(), self.paths.state_dir()):
            for path in sorted(folder.rglob("*")):
                if path.is_file() and self.paths.locks_dir() not in path.parents:
                    out[str(path)] = path.read_bytes()
        return out

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
        return [call for call in engine.calls if call[0] != "status"]

    def test_show_2_sends_one_show_waits_for_done_then_set_tuning_and_prints_the_pick_line(self) -> None:
        engine = self.engine()
        engine.script("show", done(accepted=True, delay=0.3))
        self.assertEqual(self.lwe("show", "2"), (0, LINE + "\n", ""))
        sent = self.requests(engine)
        self.assertEqual([cmd for cmd, _args in sent], ["show", "set-tuning"])
        show = sent[0][1]
        self.assertEqual((show["id"], show["ui_id"]), ("1505438974", "1505438974"))
        self.assertEqual(show["speed"], self.resolve.resolve_show_args("1505438974")[1]["speed"])

    def test_a_download_not_in_the_pool_is_refused_and_nothing_is_sent(self) -> None:
        engine = self.engine()
        self.assertEqual(self.lwe("show", "5"), (1, "5 = Nebula (1505438990)\n", "not in your pool yet; lwe add 5 "
                                                                               "brings it in, lwe bench 5 tries it\n"))
        self.assertEqual(engine.calls, [])

    def test_show_alone_and_two_words_exit_3(self) -> None:
        engine = self.engine()
        self.assertEqual(self.lwe("show"), (3, "", "show takes one wallpaper; got 0 words\n"))
        self.assertEqual(self.lwe("show", "1", "2"), (3, "", "show takes one wallpaper; got 2 words\n"))
        self.assertEqual(engine.calls, [])

    def test_an_ambiguous_title_exits_1_listing_both(self) -> None:
        engine = self.engine()
        self.assertEqual(self.lwe("show", "twin"), (1, "", "lwe: twin matches more than one wallpaper; nothing was "
                                                          "changed\n  3 = Twin (1505438976)\n  4 = Twin (1505438977)\n"))
        self.assertEqual(engine.calls, [])

    def test_an_engine_refusal_exits_1_with_the_reason_and_sends_no_set_tuning(self) -> None:
        engine = self.engine()
        engine.script("show", fail("background failed preflight"))
        self.assertEqual(self.lwe("show", "2"), (1, LINE + "\n", "background failed preflight\n"))
        self.assertEqual([cmd for cmd, _args in self.requests(engine)], ["show"])

    def test_released_outputs_say_the_screens_are_back_on(self) -> None:
        engine = self.engine(outputs={"state": "released", "reason": "verb"})
        engine.script("show", done(accepted=True), done(accepted=True))
        self.assertEqual(self.lwe("show", "2"), (0, LINE + " (screens back on)\n", ""))
        self.assertEqual(self.lwe("show", "-j", "deep space"),
                         (0, '{"number":2,"id":"1505438974","title":"Deep Space","screens_back_on":true}\n', ""))

    def test_accepted_without_a_done_in_time_exits_1(self) -> None:
        engine = self.engine()
        engine.script("show", done(accepted=True, delay=5.0))
        with mock.patch.object(self.api_client, "_DONE_TIMEOUT", 0.3):
            self.assertEqual(self.lwe("show", "2"), (1, LINE + "\n", "accepted but not finished\n"))
        self.assertEqual([cmd for cmd, _args in self.requests(engine)], ["show"])

    def test_engine_absent_or_from_another_build_or_busy_sends_no_show(self) -> None:
        self.assertEqual(self.lwe("show", "2"), (2, LINE + "\n", "the service is not running\n"))
        engine = self.engine(version="9.9.9")
        self.assertEqual(self.lwe("show", "2"), (1, LINE + "\n", f"The running engine is 9.9.9 but {self.stamp} is "
                                                                  "installed; run lwe service restart.\n"))
        engine.set(version=self.stamp)
        held, release = threading.Event(), threading.Event()

        def hold() -> None:
            with self.lock.held("sync"):
                held.set()
                release.wait(10)

        holder = threading.Thread(target=hold)
        holder.start()
        try:
            self.assertTrue(held.wait(5))
            self.assertEqual(self.lwe("show", "2"), (1, LINE + "\n", "the service is busy\n"))
        finally:
            release.set()
            holder.join()
        self.assertEqual(self.requests(engine), [])


class ChildTest(unittest.TestCase):
    """show in children whose environment is built from nothing, with PySide6 blocked; the id names a
    library folder the tags do not know, a Workshop row, so no tags file is needed."""

    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui import version
        cls.stamp = version.panel_stamp()

    def scratch(self, name: str, stubs: dict) -> tuple[Path, dict]:
        root = ROOT / name
        env = _cli_env.scratch_env(root, stubs)
        conf = Path(env["XDG_CONFIG_HOME"]) / "lwe" / "settings.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text(f"WALLPAPERS_DIR={root / 'lib'}\nWORKSHOP_DIR={root / 'ws'}\n", encoding="utf-8")
        item(root / "lib" / "1505438974", "Deep Space")
        return root, env

    def lwe(self, env: dict, root: Path, *words: str) -> subprocess.Popen:
        return subprocess.Popen([sys.executable, "-m", "lwe_ui", "--lwe", self.stamp, str(root), *words], env=env,
                                cwd=env["HOME"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")

    def test_a_stopped_service_exits_2_and_systemctl_is_never_called(self) -> None:
        log = ROOT / "systemctl.log"
        root, env = self.scratch("away", {"systemctl": f"echo \"$*\" >> '{log}'\nexit 1\n"})
        bite = subprocess.run([sys.executable, "-c", "import PySide6"], env=env, capture_output=True, text=True,
                              timeout=60)
        self.assertNotEqual(bite.returncode, 0)
        self.assertIn("ImportError", bite.stderr)
        child = self.lwe(env, root, "show", "1505438974")
        out, err = child.communicate(timeout=60)
        self.assertEqual((child.returncode, out, err), (2, "1 = Deep Space (1505438974)\n", "the service is not running\n"))
        self.assertFalse(log.exists(), "systemctl was called")

    def test_show_holds_the_sync_lock_until_the_final_reply(self) -> None:
        root, env = self.scratch("hold", {})
        probe = ("from lwe_ui.storage import lock\n"
                 "try:\n"
                 "    with lock.held('sync', wait_s=0):\n"
                 "        print('free')\n"
                 "except lock.StoreBusy:\n"
                 "    print('busy')\n")
        with _fake_engine.FakeEngine(env["LWE_SOCKET"]) as engine:
            engine.script("show", done(accepted=True, delay=3.0))
            child = self.lwe(env, root, "show", "1505438974")
            deadline = time.monotonic() + 20
            while not any(cmd == "show" for cmd, _args in engine.calls) and time.monotonic() < deadline:
                time.sleep(0.02)
            during = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True, timeout=60)
            out, err = child.communicate(timeout=60)
            after = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual((child.returncode, out, err), (0, "1 = Deep Space (1505438974)\n", ""))
        self.assertEqual((during.stdout, after.stdout), ("busy\n", "free\n"))
        self.assertEqual([cmd for cmd, _args in engine.calls if cmd != "status"], ["show", "set-tuning"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
