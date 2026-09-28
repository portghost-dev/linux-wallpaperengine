"""get and config: the saved settings, each with where it comes from; a read writes nothing.

Each form runs through cli.main in this process with HOME and the XDG folders at scratch
(_cli_env.scratch_home) and, where a row needs the engine, a fake engine on a socket the test makes
(_fake_engine). Every run checks that no store file changed. One run goes through the engine's
handoff in a child whose environment is built from nothing (_cli_env.scratch_env), PySide6 blocked.
No case runs systemctl or reaches another socket.

Run: PYTHONPATH=src python3 tests/test_cli_get_config.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _cli_env
import _fake_engine

ROOT = Path(tempfile.mkdtemp(prefix="lwe-cli-get-"))
HOME = _cli_env.scratch_home(ROOT / "inproc")
GET_USAGE = "usage: lwe get <setting> [<setting> ...]; lwe config lists the settings"


def tearDownModule() -> None:
    shutil.rmtree(ROOT, True)


class GetConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui import cli, version
        from lwe_ui.cli import settings_table, vocabulary
        from lwe_ui.engine import daemon_unit
        from lwe_ui.storage import paths
        cls.cli, cls.stamp, cls.paths = cli, version.panel_stamp(), paths
        cls.rows = [row["name"] for row in vocabulary.SETTINGS if row["name"] in settings_table.BY_NAME]
        cls.daemon_unit = daemon_unit

    def setUp(self) -> None:
        for folder in (self.paths.config_dir(), self.paths.state_dir()):
            shutil.rmtree(folder, True)
            folder.mkdir(parents=True)
        self.socket = ROOT / "e.sock"
        os.environ["LWE_SOCKET"] = str(self.socket)
        self.runs: list = []
        du = self.daemon_unit
        for patcher in (mock.patch.object(du.subprocess, "run", lambda argv, **kw: self.runs.append(argv)),
                        mock.patch.object(du, "enumerate_outputs", lambda: ["DP-1"]),
                        mock.patch.object(du, "live_engine_env", lambda: None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.assertEqual(self.runs, [], "a subprocess ran")

    def engine(self, **fields) -> _fake_engine.FakeEngine:
        engine = _fake_engine.FakeEngine(self.socket, **fields)
        self.addCleanup(engine.stop)
        return engine

    def store(self, name: str, text: str) -> None:
        path = self.paths.config_dir() / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _files(self) -> dict[str, bytes]:
        out = {}
        for folder in (self.paths.config_dir(), self.paths.state_dir()):
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    out[str(path)] = path.read_bytes()
        return out

    def lwe(self, *words: str) -> tuple[int, str, str]:
        before = self._files()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(list(words), sender_stamp=self.stamp)
        self.assertEqual(self._files(), before, f"{words} changed a store file")
        return code, out.getvalue(), err.getvalue()

    def playlists(self) -> None:
        self.store("playlists/main.conf", "NAME=Main\nMODE=shuffle\nINTERVAL=900\nMEMBERS=\"1 2\"\n")
        self.store("playlists/night.conf", "NAME=Night\nMODE=sequential\nINTERVAL=90\nMEMBERS=3\n")

    def test_get_prints_the_saved_values_one_per_line(self) -> None:
        self.store("settings.conf", "ENGINE_VOLUME=40\nENGINE_FPS=30\nOVERRIDE_MUTE=true\nENGINE_TIMESCALE=2\n")
        engine = self.engine(speed=0.0)
        self.assertEqual(self.lwe("get", "volume", "fps", "speed"), (0, "40\n30\n2\n", ""))
        self.assertEqual(engine.calls, [], "a saved value needs no status")

    def test_get_alone_or_an_unknown_name_prints_usage_and_nothing_else(self) -> None:
        self.assertEqual(self.lwe("get"), (3, "", GET_USAGE + "\n"))
        for name in ("socket", "schedule", "bogus"):
            with self.subTest(name=name):
                self.assertEqual(self.lwe("get", "volume", name), (3, "", f"{name} is not a setting; {GET_USAGE}\n"))
        self.assertEqual(self.lwe("config", "socket")[:2], (3, ""))

    def test_audiosmoothing_needs_the_running_engine(self) -> None:
        self.assertEqual(self.lwe("get", "audiosmoothing"),
                         (2, "", "the service is not running, so audiosmoothing has no value\n"))
        self.assertEqual(self.lwe("config", "audiosmoothing"), (0, "audiosmoothing    not running\n", ""))
        engine = self.engine(audio_smooth=120.0)
        self.assertEqual(self.lwe("get", "audiosmoothing", "order", "interval", "playlist")[0], 0)
        self.assertEqual(engine.calls, [("status", {})], "one status read serves every name")
        self.assertEqual(self.lwe("get", "audiosmoothing"), (0, "120\n", ""))
        self.assertEqual(self.lwe("config", "audiosmoothing"), (0, "audiosmoothing  120  running\n", ""))

    def test_config_names_a_saved_value_by_the_key_s_presence(self) -> None:
        self.store("settings.conf", "ENGINE_VOLUME=15\n")
        self.assertEqual(self.lwe("config", "volume"), (0, "volume  15  settings.conf\n", ""))
        self.assertEqual(self.lwe("config", "fps"), (0, "fps  60  default\n", ""))
        self.assertEqual(self.lwe("config", "watchdog"), (0, "watchdog  5m  engine-env\n", ""))

    def test_an_invalid_line_prints_the_value_in_force_and_names_the_line(self) -> None:
        self.store("settings.conf", "ENGINE_VOLUME=40\nENGINE_FPS=abc\n")
        problem = "settings.conf line 2 ENGINE_FPS=abc (not a whole number)"
        self.assertEqual(self.lwe("get", "volume", "fps"), (0, "40\n60\n", f"invalid: {problem}\n"))
        self.assertEqual(self.lwe("config", "fps"), (0, f"fps  60  settings.conf  invalid: {problem}\n", ""))

    def test_the_json_shapes(self) -> None:
        self.store("settings.conf", "ENGINE_VOLUME=40\nENGINE_FPS=30\n")
        self.assertEqual(self.lwe("-j", "get", "volume", "fps"), (0, '{"volume":"40","fps":"30"}\n', ""))
        code, out, err = self.lwe("-j", "config", "volume")
        self.assertEqual((code, json.loads(out), err),
                         (0, {"name": "volume", "value": "40", "source": "settings.conf", "invalid": None}, ""))
        code, out, err = self.lwe("config", "-j")
        rows = json.loads(out)["settings"]
        self.assertEqual((code, [row["name"] for row in rows], err), (0, self.rows, ""))
        self.assertEqual(set(rows[0]), {"name", "value", "source", "invalid"})

    def test_interval_prints_as_it_types_back(self) -> None:
        self.playlists()
        self.store("settings.conf", "ACTIVE_PLAYLIST=main\n")
        self.assertEqual(self.lwe("get", "interval", "order", "playlist"), (0, "15m\nshuffle\nMain\n", ""))
        self.assertEqual(self.lwe("config", "interval"), (0, "interval  15m  playlists/main.conf\n", ""))

    def test_an_enabled_schedule_reads_the_playlist_the_engine_is_bound_to(self) -> None:
        self.playlists()
        self.store("settings.conf", "ACTIVE_PLAYLIST=main\n")
        self.engine(schedule={"enabled": True, "entries": [], "active": "night", "held": False, "pending": ""},
                    lanes=[{"id": "all", "playlist": "night"}])
        self.assertEqual(self.lwe("get", "order", "interval"), (0, "sequential\n90s\n", ""))
        self.assertEqual(self.lwe("config", "playlist"), (0, "playlist  Night  schedule\n", ""))

    def test_the_old_clamp_word_gives_a_number_the_file_does_not_set(self) -> None:
        self.store("settings.conf", "RENDER_RESOLUTION=sharpfx\n")
        self.assertEqual(self.lwe("get", "effectclamp", "resclamp"), (0, "0\n1\n", ""))
        self.assertEqual(self.lwe("config", "effectclamp"), (0, "effectclamp  0  RENDER_RESOLUTION\n", ""))

    def test_a_clamp_number_that_is_not_finite_reads_as_absent(self) -> None:
        for body, number, source in (("SSFACTOR=inf\n", "1", "default"),
                                     ("RENDER_RESOLUTION=wallpaper\nSSFACTOR=inf\n", "0", "RENDER_RESOLUTION")):
            with self.subTest(body=body):
                self.store("settings.conf", body)
                problem = f"settings.conf line {body.count(chr(10))} SSFACTOR=inf (not a number from 0 to 4)"
                self.assertEqual(self.lwe("get", "resclamp"), (0, f"{number}\n", f"invalid: {problem}\n"))
                self.assertEqual(self.lwe("config", "resclamp"),
                                 (0, f"resclamp  {number}  {source}  invalid: {problem}\n", ""))

    def test_an_engine_from_another_build_is_named_and_the_read_goes_on(self) -> None:
        self.playlists()
        self.store("settings.conf", "ACTIVE_PLAYLIST=main\nENGINE_VOLUME=40\n")
        self.engine(version="0.9.0")
        self.assertEqual(self.lwe("get", "volume", "order"),
                         (0, "40\nshuffle\n",
                          f"The running engine is 0.9.0 but {self.stamp} is installed; run lwe service restart.\n"))


class HandoffTest(unittest.TestCase):
    def test_get_volume_through_the_engine_s_handoff_without_pyside6(self) -> None:
        env = _cli_env.scratch_env(ROOT / "child")
        settings = Path(env["XDG_CONFIG_HOME"]) / "lwe" / "settings.conf"
        settings.parent.mkdir(parents=True)
        settings.write_text("ENGINE_VOLUME=40\n", encoding="utf-8")
        self.assertEqual(_cli_env.run_lwe(["get", "volume"], env, ""), (0, "40\n", ""))
        self.assertEqual(settings.read_text(encoding="utf-8"), "ENGINE_VOLUME=40\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
