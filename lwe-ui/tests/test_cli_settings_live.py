"""The live and next-wallpaper setting commands: each saves one line of settings.conf and sends the
key's targeted push through the change runner, with the runner's outcome as the receipt; speed 0 and
audiosmoothing go to the engine under sync and are never saved.

Each form runs through cli.main in this process with HOME and the XDG folders at scratch
(_cli_env.scratch_home), daemon_unit's subprocess call replaced by a recorder, one screen faked and a
fake engine on the socket _sandbox pins, showing wallpaper 111. No case runs systemctl.

Run: PYTHONPATH=src python3 tests/test_cli_settings_live.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _cli_env
import _fake_engine

ROOT = Path(tempfile.mkdtemp(prefix="lwe-cli-live-"))
HOME = _cli_env.scratch_home(ROOT / "inproc")
NOW = "applies now"
PENDING = ("the service is not running or is busy, so it is not applied yet. It can apply the next time the "
           "panel window opens or polls, an lwe command saves a setting the engine uses, lwe reload or a backup "
           "import runs, or lwe service start or restart starts the engine.")
ON_SCREEN = {"id": "111", "ui_id": "111", "title": ""}
LANES = [{"id": "all", "playlist": "main"}]


def tearDownModule() -> None:
    shutil.rmtree(ROOT, True)


class LiveSettingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui import cli, version
        from lwe_ui.engine import daemon_unit, marker
        from lwe_ui.storage import paths, playlists, settings, wp
        cls.cli, cls.stamp, cls.daemon_unit, cls.marker = cli, version.panel_stamp(), daemon_unit, marker
        cls.paths, cls.playlists, cls.settings, cls.wp = paths, playlists, settings, wp
        cls.conf = paths.settings_file()
        cls.env_path = paths.config_dir() / daemon_unit.ENV_FILE_NAME

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
        self.playlists.save("main", {"NAME": "Main", "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min",
                                     "MEMBERS": "111 222"})
        self.conf.write_text("ACTIVE_PLAYLIST=main\nENGINE_VOLUME=20\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.assertEqual(self.runs, [], "a subprocess ran")

    def engine(self, **fields) -> _fake_engine.FakeEngine:
        fields = {"current": ON_SCREEN, "lanes": LANES, **fields}
        engine = _fake_engine.FakeEngine(_sandbox.SOCKET, **fields)
        self.addCleanup(engine.stop)
        return engine

    def lwe(self, *words: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(list(words), sender_stamp=self.stamp)
        return code, out.getvalue(), err.getvalue()

    def sent(self, engine: _fake_engine.FakeEngine) -> list[tuple[str, dict]]:
        return [(cmd, args) for cmd, args in engine.calls if cmd != "status"]

    def test_volume_40_saves_and_sends_the_resolved_volume_and_the_transfers(self) -> None:
        engine = self.engine()
        self.assertEqual(self.lwe("volume", "40"), (0, f"volume 40: saved; {NOW}.\n", ""))
        self.assertEqual(self.settings.load_set()["ENGINE_VOLUME"], 40)
        verbs = [cmd for cmd, _args in self.sent(engine)]
        self.assertEqual(verbs[-1], "set-volume")
        self.assertEqual(self.sent(engine)[-1][1], {"volume": 40})
        self.assertEqual(set(verbs[:-1]), {"playlist-set"}, "the engine-held playlists' transfers, nothing else")
        self.assertEqual(self.marker.read()["classes"], [], "the marker clears when every request ended ok")

    def test_one_changed_line_a_toggle_twice_restores_and_an_unchanged_value_still_pushes(self) -> None:
        engine = self.engine()
        self.assertEqual(self.lwe("volume", "40")[0], 0)
        self.assertEqual(self.conf.read_text(encoding="utf-8"), "ACTIVE_PLAYLIST=main\nENGINE_VOLUME=40\n")
        before = self.conf.read_bytes()
        self.assertEqual(self.lwe("mute", "toggle"), (0, f"mute on: saved; {NOW}.\n", ""))
        self.assertEqual(self.lwe("mute", "toggle"), (0, f"mute off: saved; {NOW}.\n", ""))
        self.assertEqual(self.settings.load_set()["OVERRIDE_MUTE"], False)
        self.assertEqual(self.lwe("volume", "40"), (0, f"volume 40: unchanged; {NOW}.\n", ""))
        self.assertEqual([args for cmd, args in self.sent(engine) if cmd == "set-volume"],
                         [{"volume": 40}, {"volume": 0}, {"volume": 40}, {"volume": 40}])
        self.assertEqual(self.conf.read_bytes(),
                         before.replace(b"ENGINE_VOLUME=40\n", b"ENGINE_VOLUME=40\nOVERRIDE_MUTE=false\n"))

    def test_engine_away_saves_sets_the_marker_and_says_pending(self) -> None:
        self.assertEqual(self.lwe("volume", "40"), (0, f"volume 40: saved; {PENDING}\n", ""))
        self.assertEqual(self.settings.load_set()["ENGINE_VOLUME"], 40)
        self.assertIn("BUNDLE", self.marker.read()["classes"], "the marker waits for the next sync")

    def test_volume_steps_stop_at_the_edges_and_say_so(self) -> None:
        self.engine()
        self.assertEqual(self.lwe("volume", "+200"), (0, f"volume 128: saved; {NOW}.\nvolume stops at 128\n", ""))
        self.assertEqual(self.lwe("volume", "-5"), (0, f"volume 123: saved; {NOW}.\n", ""))
        self.assertEqual(self.lwe("volume", "-500"), (0, f"volume 0: saved; {NOW}.\nvolume stops at 0\n", ""))
        self.assertEqual(self.lwe("volume", "129")[0], 3)
        self.assertEqual(self.settings.load_set()["ENGINE_VOLUME"], 0)

    def test_the_wallpaper_on_screen_keeps_its_own_value(self) -> None:
        self.wp.update_set("111", {"VOLUME": 70})
        engine = self.engine()
        self.assertEqual(self.lwe("volume", "40"),
                         (0, f"volume 40: saved; {NOW}.\nthe wallpaper on screen keeps its own value "
                             "(lwe wallpaper current unset volume)\n", ""))
        self.assertEqual(self.sent(engine)[-1], ("set-volume", {"volume": 70}), "the resolved volume, never 40")

    def test_speed_0_freezes_without_saving_and_speed_below_the_range_exits_3(self) -> None:
        before = self.conf.read_bytes()
        self.assertEqual(self.lwe("speed", "0")[0], 2, "engine away")
        engine = self.engine()
        self.assertEqual(self.lwe("speed", "0"),
                         (0, "speed 0: applies now; not saved (speed load brings back the saved speed).\n", ""))
        self.assertEqual(self.sent(engine), [("set-speed", {"speed": 0.0})])
        self.assertEqual(self.lwe("speed", "0.05")[0], 3)
        self.assertEqual(self.conf.read_bytes(), before)

    def test_audiosmoothing_sends_set_tuning_and_writes_no_file(self) -> None:
        engine = self.engine()
        before = self.conf.read_bytes()
        self.assertEqual(self.lwe("audiosmoothing", "50"),
                         (0, "audiosmoothing 50: applies now; not saved yet, so a restart goes back to the start "
                             "value.\n", ""))
        self.assertEqual(self.sent(engine), [("set-tuning", {"audio_smooth": 50.0})])
        self.assertEqual(self.conf.read_bytes(), before)
        self.assertFalse(self.env_path.exists())

    def test_lightdimming_sends_set_tuning_and_rewrites_the_dial_line(self) -> None:
        engine = self.engine()
        self.assertEqual(self.lwe("lightdimming", "1.2"), (0, f"lightdimming 1.2: saved; {NOW}.\n", ""))
        self.assertEqual(self.sent(engine)[-1][0], "set-tuning")
        self.assertIn("1.2", self.daemon_unit.parse_env(self.env_path.read_text(encoding="utf-8").split("\n"))
                      .get("LWE_CLASSICK", ""))

    def test_the_config_only_defaults_take_config_and_nothing_else(self) -> None:
        self.engine()
        self.assertEqual(self.lwe("config", "audioreactivedefault", "on")[0], 0)
        self.assertIs(self.settings.load_set()["AUDIO_REACTIVE_DEFAULT"], True)
        self.assertEqual(self.lwe("audioreactivedefault", "on"),
                         (3, "", "lwe: audioreactivedefault is not a command\n"))

    def test_config_unset_deletes_every_line_and_pushes_the_default_even_when_unchanged(self) -> None:
        self.conf.write_text("ACTIVE_PLAYLIST=main\nENGINE_VOLUME=20\nENGINE_VOLUME=30\n", encoding="utf-8")
        engine = self.engine()
        self.assertEqual(self.lwe("config", "unset", "volume"), (0, f"volume 15: saved; {NOW}.\n", ""))
        self.assertEqual(self.conf.read_text(encoding="utf-8"), "ACTIVE_PLAYLIST=main\n")
        self.assertEqual(self.lwe("config", "unset", "volume"), (0, f"volume 15: unchanged; {NOW}.\n", ""))
        self.assertEqual([args for cmd, args in self.sent(engine) if cmd == "set-volume"],
                         [{"volume": 15}, {"volume": 15}], "an unchanged value still pushes")

    def test_with_a_marker_set_the_bundle_goes_first_and_the_change_last(self) -> None:
        self.marker.ensure(("BUNDLE",))
        engine = self.engine()
        self.assertEqual(self.lwe("volume", "40")[0], 0)
        verbs = [cmd for cmd, _args in self.sent(engine)]
        self.assertEqual(verbs[-1], "set-volume")
        self.assertIn("lanes-set", verbs[:-1], "the bundle went first")
        self.assertEqual(self.marker.read()["classes"], [])

    def test_with_the_engine_frozen_volume_sends_no_set_speed(self) -> None:
        engine = self.engine(speed=0.0)
        self.assertEqual(self.lwe("volume", "40")[0], 0)
        self.assertNotIn("set-speed", [cmd for cmd, _args in self.sent(engine)])

    def test_a_dial_whose_engine_env_step_failed_warns_and_exits_0(self) -> None:
        self.engine()
        with mock.patch.object(self.daemon_unit, "write_env", lambda *a, **k: "no screens"):
            self.assertEqual(self.lwe("audiogain", "5"),
                             (0, f"audiogain 5: saved; {NOW}.\n",
                              "warning: engine-env was not rewritten: no screens\n"))

    def test_a_next_wallpaper_setting_does_not_load(self) -> None:
        engine = self.engine()
        for name in ("scaling", "edge", "automute"):
            with self.subTest(name=name):
                self.assertEqual(self.lwe(name, "load"), (1, "", "applies to the next wallpaper\n"))
        self.assertEqual(engine.calls, [], "a refused load reads no status and sends nothing")
        self.assertEqual(self.lwe("scaling", "fill"),
                         (0, "scaling fill: saved; applies to the next wallpaper.\n", ""))

    def test_a_wallpaper_without_its_own_key_follows_the_saved_global(self) -> None:
        from lwe_ui.engine import resolve
        self.wp.update_set("222", {"SCALING": "fit"})
        self.engine()
        self.assertEqual(self.lwe("scaling", "fill")[0], 0)
        self.assertEqual(self.lwe("config", "audioreactivedefault", "on")[0], 0)
        self.assertNotIn("SCALING", self.wp.load_set("111"))
        self.assertEqual(resolve.resolve_show_args("111")[1]["scaling"], "fill")
        self.assertEqual(resolve.resolve_show_args("222")[1]["scaling"], "fit", "its own key wins")
        self.assertIs(resolve.resolve_show_args("111")[1]["audio_processing"], True)

    def test_a_live_key_loads_the_resolved_saved_value_and_writes_nothing(self) -> None:
        self.assertEqual(self.lwe("volume", "load")[0], 2, "engine away")
        engine = self.engine()
        before = self.conf.read_bytes()
        loaded = "the saved value, applied now; nothing written."
        self.assertEqual(self.lwe("volume", "load"), (0, f"volume 20: {loaded}\n", ""))
        self.wp.update_set("111", {"VOLUME": 70})
        self.assertEqual(self.lwe("mute", "load"), (0, f"mute 70: {loaded}\n", ""))
        self.assertEqual(self.lwe("fps", "load"), (0, f"fps 60: {loaded}\n", ""))
        self.assertEqual(self.lwe("parallax", "load"), (0, f"parallax on: {loaded}\n", ""))
        self.assertEqual(self.lwe("lightdimming", "load")[0], 0)
        self.assertEqual([(cmd, args) for cmd, args in self.sent(engine) if cmd != "set-tuning"],
                         [("set-volume", {"volume": 20}), ("set-volume", {"volume": 70}), ("set-fps", {"fps": 60}),
                          ("set-parallax", {"enabled": True})])
        self.assertEqual(self.sent(engine)[-1][0], "set-tuning")
        self.assertEqual(self.conf.read_bytes(), before)
        self.assertEqual(self.marker.read()["classes"], [], "a load sets no marker")

    def test_a_load_checks_exactly_its_lines_first_and_sends_nothing_on_an_invalid_one(self) -> None:
        engine = self.engine()
        self.conf.write_text("ACTIVE_PLAYLIST=main\nENGINE_VOLUME=abc\n", encoding="utf-8")
        self.assertEqual(self.lwe("volume", "load"),
                         (1, "", "volume load: invalid: settings.conf line 2 ENGINE_VOLUME=abc (not a whole number)\n"))
        self.conf.write_text("ACTIVE_PLAYLIST=main\nENGINE_VOLUME=20\n", encoding="utf-8")
        self.paths.wp_file("111").write_text("VOLUME=200\n", encoding="utf-8")
        self.assertEqual(self.lwe("volume", "load"),
                         (1, "", "volume load: invalid: wp/111.conf line 1 VOLUME=200 (out of range)\n"))
        self.assertEqual(self.lwe("fps", "load")[0], 0, "another key's lines are not checked")
        self.assertEqual([cmd for cmd, _args in self.sent(engine)], ["set-fps"])

    def test_speed_load_unfreezes_to_the_resolved_rate(self) -> None:
        self.conf.write_text("ACTIVE_PLAYLIST=main\nENGINE_TIMESCALE=2\n", encoding="utf-8")
        engine = self.engine(speed=0.0)
        self.assertEqual(self.lwe("speed", "load"),
                         (0, "speed 2: the saved value, applied now; nothing written.\n", ""))
        self.wp.update_set("111", {"SPEED": 1.5})
        self.assertEqual(self.lwe("speed", "load")[0], 0)
        self.assertEqual(self.sent(engine), [("set-speed", {"speed": 2.0}), ("set-speed", {"speed": 1.5})])

    def test_audiosmoothing_load_sends_the_engine_start_value(self) -> None:
        engine = self.engine(config={"LWE_AUDIOSMOOTH": {"value": 90}})
        self.assertEqual(self.lwe("audiosmoothing", "load"),
                         (0, "audiosmoothing 90: the saved value, applied now; nothing written.\n", ""))
        self.assertEqual(self.sent(engine), [("set-tuning", {"audio_smooth": 90.0})])

    def test_a_restart_class_load_reshows_the_wallpaper_on_screen_keeping_the_freeze(self) -> None:
        self.conf.write_text("ACTIVE_PLAYLIST=main\nSSFACTOR=0.5\n", encoding="utf-8")
        engine = self.engine(speed=0.0, config={"LWE_SSFACTOR": {"value": "1"}, "LWE_TEXDETAIL": {"value": "auto"}})
        before = self.conf.read_bytes()
        self.assertEqual(self.lwe("resclamp", "load"),
                         (0, "resclamp 0.5: the wallpaper on screen was shown again with it; nothing written.\n"
                             "the saved global takes effect at the next service restart (lwe service restart "
                             "applies it).\n", ""))
        verbs = [cmd for cmd, _args in self.sent(engine)]
        self.assertEqual((verbs[0], verbs[-1]), ("show", "set-speed"))
        self.assertEqual(self.sent(engine)[-1][1], {"speed": 0.0}, "the freeze is kept")
        self.assertEqual(self.lwe("texturedetail", "load"),
                         (0, "texturedetail auto: the wallpaper on screen was shown again with it; nothing written.\n",
                          ""), "no restart line when the engine started with the saved value")
        self.assertEqual(self.conf.read_bytes(), before)

    def test_a_running_engine_from_another_build_refuses_and_writes_nothing(self) -> None:
        engine = self.engine(version="0.0.1-other")
        before = self.conf.read_bytes()
        code, out, err = self.lwe("volume", "40")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("0.0.1-other", err)
        self.assertEqual(self.conf.read_bytes(), before)
        self.assertEqual(self.sent(engine), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
