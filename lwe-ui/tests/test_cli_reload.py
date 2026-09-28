"""lwe reload: every file checked from its raw text; any error applies and writes nothing; otherwise what
changed since reload's own snapshot, the warnings and the cleanup are printed, the store is applied
through sync_all with the re-show, engine-env is rebuilt, and the restart line names what waits. The
reload's re-show carries automatic and its lanes-set carries enabled, also while the engine reports that it
refused its restore, when the brake note follows on stderr.

Each form runs through cli.main in this process with HOME and the XDG folders at scratch
(_cli_env.scratch_home), daemon_unit's subprocess call replaced by a recorder, one screen faked, the
service's main pid and the running engine's environment faked, and, where a case needs one, a fake engine
on the socket _sandbox pins, showing wallpaper 111. No case runs systemctl.

Run: PYTHONPATH=src python3 tests/test_cli_reload.py
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

ROOT = Path(tempfile.mkdtemp(prefix="lwe-cli-reload-"))
HOME = _cli_env.scratch_home(ROOT / "inproc")
FIRST = "no earlier reload to compare with; every file was checked"
SAME = "nothing changed since the last reload"
ON_SCREEN = {"id": "111", "ui_id": "111", "title": ""}
LANES = [{"id": "all", "playlist": "main"}]
MAIN = 'NAME=Main\nMODE=shuffle\nINTERVAL=900\nUNIT=min\nMEMBERS="111 222"\n'


def tearDownModule() -> None:
    shutil.rmtree(ROOT, True)


class ReloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui import cli, version
        from lwe_ui.engine import daemon_unit, marker
        from lwe_ui.storage import paths, settings
        cls.cli, cls.stamp, cls.daemon_unit, cls.marker = cli, version.panel_stamp(), daemon_unit, marker
        cls.paths, cls.owned_key = paths, settings.OWNED_KEY
        cls.snapshot = paths.panel_state_dir() / "reload-snapshot"
        cls.conf = paths.settings_file()
        cls.env_path = paths.config_dir() / daemon_unit.ENV_FILE_NAME

    def setUp(self) -> None:
        for folder in (self.paths.config_dir(), self.paths.state_dir(), self.paths.data_dir()):
            shutil.rmtree(folder, True)
            folder.mkdir(parents=True)
        self.runs: list = []
        self.live: dict | None = None
        du = self.daemon_unit
        for patcher in (mock.patch.object(du.subprocess, "run", lambda argv, **kw: self.runs.append(argv)),
                        mock.patch.object(du, "enumerate_outputs", lambda: ["DP-1"]),
                        mock.patch.object(du, "live_engine_env", lambda: self.live),
                        mock.patch.object(du, "_service_main_pid", lambda: os.getpid())):
            patcher.start()
            self.addCleanup(patcher.stop)
        for wid in ("111", "222"):
            folder = self.paths.default_wallpapers_dir() / wid
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "project.json").write_text(json.dumps({"title": f"WP {wid}", "type": "scene",
                                                             "file": "scene.json"}), encoding="utf-8")
        self.write("playlists/main.conf", MAIN)
        self.conf.write_text("ACTIVE_PLAYLIST=main\nENGINE_VOLUME=15\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.assertEqual(self.runs, [], "a subprocess ran")

    def write(self, name: str, text: str) -> None:
        path = self.paths.config_dir() / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

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

    def sent(self, engine: _fake_engine.FakeEngine) -> list[str]:
        return [cmd for cmd, _args in engine.calls if cmd != "status"]

    def snapshot_files(self) -> dict[str, bytes]:
        root = self.snapshot
        return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}

    def test_the_first_reload_applies_and_writes_the_snapshot(self) -> None:
        engine = self.engine()
        code, out, err = self.lwe("reload")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines(), [FIRST, "Applied."])
        self.assertEqual(sorted(self.snapshot_files()), ["playlists/main.conf", "settings.conf"])
        self.assertIn("show", self.sent(engine), "the reload re-shows the wallpaper on screen")
        self.assertEqual(self.lwe("reload")[1].splitlines(), [SAME, "Applied."])

    def test_the_reload_reshow_is_marked_automatic_and_its_lanes_set_sent_as_configured(self) -> None:
        from lwe_ui.engine import push
        engine = self.engine(served=False, restore_refused=True)
        code, out, err = self.lwe("reload")
        self.assertEqual((code, out.splitlines()[-1], err), (0, "Applied.", push.BRAKED + "\n"))
        self.assertEqual([args.get("automatic") for cmd, args in engine.calls if cmd == "show"], [True])
        self.assertIn(("lanes-set", {"lanes": [{"id": "all", "playlist": "main", "enabled": True}]}), engine.calls)
        self.assertEqual(self.marker.served(), os.getpid())

    def test_a_hand_edit_prints_what_changed_and_applies(self) -> None:
        engine = self.engine()
        self.assertEqual(self.lwe("reload")[0], 0)
        self.conf.write_text("ACTIVE_PLAYLIST=main\nENGINE_VOLUME=40\nENGINE_FPS=30\n", encoding="utf-8")
        engine.calls.clear()
        code, out, err = self.lwe("reload")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines(), ["settings.conf: volume 15 -> 40", "settings.conf: fps set to 30",
                                            "Applied."])
        self.assertIn(("set-volume", {"volume": 40}), engine.calls)

    def test_a_bad_value_in_one_playlist_refuses_everything(self) -> None:
        engine = self.engine()
        self.assertEqual(self.lwe("reload")[0], 0)
        snapshot, env = self.snapshot_files(), self.env_path.read_bytes()
        classes = self.marker.read()["classes"]
        self.write("playlists/night.conf", "NAME=Night\nMODE=shuffle\nINTERVAL=abc\n")
        self.conf.write_text("ACTIVE_PLAYLIST=main\nENGINE_VOLUME=40\n", encoding="utf-8")
        engine.calls.clear()
        code, out, err = self.lwe("reload")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err.splitlines(),
                         ["playlists/night.conf:3: INTERVAL must be a whole number of seconds from 15 to 599940; "
                          "got abc",
                          "reload refused: 1 error; nothing was applied or written"])
        self.assertEqual(self.sent(engine), [], "no verb sent")
        self.assertEqual((self.snapshot_files(), self.env_path.read_bytes(), self.marker.read()["classes"]),
                         (snapshot, env, classes))

    def test_an_unknown_key_warns_and_applies_and_the_window_line_does_not(self) -> None:
        self.engine()
        self.conf.write_text(f"ACTIVE_PLAYLIST=main\nFOO=1\n{self.owned_key}=mute\n", encoding="utf-8")
        code, out, err = self.lwe("reload")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines(), [FIRST, "warning: settings.conf:2: FOO is not a setting this version "
                                                   "knows; it is kept and not applied", "Applied."])

    def test_a_schedule_naming_a_missing_file_is_an_error_only_while_enabled(self) -> None:
        self.conf.write_text('ACTIVE_PLAYLIST=main\nSCHEDULE="07:00=main;20:00=gone"\nSCHEDULE_ENABLED=true\n',
                             encoding="utf-8")
        code, _out, err = self.lwe("reload")
        self.assertEqual(code, 1)
        self.assertIn("settings.conf:2: SCHEDULE's night playlist has no file in playlists while SCHEDULE_ENABLED "
                      "is true", err.splitlines())
        self.conf.write_text('ACTIVE_PLAYLIST=main\nSCHEDULE="07:00=main;20:00=gone"\nSCHEDULE_ENABLED=false\n',
                             encoding="utf-8")
        self.assertEqual(self.lwe("reload")[0], 0)

    def test_an_empty_disabled_schedule_passes_and_equal_times_are_an_error(self) -> None:
        self.conf.write_text('ACTIVE_PLAYLIST=main\nSCHEDULE=""\nSCHEDULE_ENABLED=false\n', encoding="utf-8")
        self.assertEqual(self.lwe("reload")[0], 0)
        self.conf.write_text('ACTIVE_PLAYLIST=main\nSCHEDULE="07:00=main;07:00=main"\nSCHEDULE_ENABLED=false\n',
                             encoding="utf-8")
        code, _out, err = self.lwe("reload")
        self.assertEqual(code, 1)
        self.assertIn("settings.conf:2: SCHEDULE's day and night start at the same time", err.splitlines())

    def test_duplicate_aliases_are_an_error(self) -> None:
        self.write("wp/111.conf", "ALIAS=Sunset\n")
        self.write("wp/222.conf", "ALIAS=sunset\n")
        code, _out, err = self.lwe("reload")
        self.assertEqual(code, 1)
        self.assertEqual(err.splitlines()[:2], ["wp/111.conf:1: @Sunset is taken by 222",
                                                "wp/222.conf:1: @sunset is taken by 111"])

    def test_lines_the_shell_reads_differently_are_errors(self) -> None:
        self.conf.write_text("ACTIVE_PLAYLIST=main\nexport ENGINE_FPS=30\nENGINE_SCALING='fill'\n"
                             "ENGINE_VOLUME=40 # louder\n", encoding="utf-8")
        code, out, err = self.lwe("reload")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err.splitlines(), [
            "settings.conf:2: an export line; lwe reads only KEY=value lines",
            "settings.conf:3: lwe reads this value as \"'fill'\" and the shell as 'fill'; write it in double quotes",
            "settings.conf:4: lwe reads this value as '40 # louder' and the shell as '40'; write it in double quotes",
            "reload refused: 3 errors; nothing was applied or written"])

    def test_engine_away_validates_prints_and_ensures_the_marker(self) -> None:
        code, out, err = self.lwe("reload")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines()[0], FIRST)
        self.assertTrue(out.splitlines()[1].startswith("Not applied yet: the service is not running or is busy."))
        self.assertEqual(sorted(self.marker.read()["classes"]), ["BUNDLE", "CURRENT"])
        self.assertEqual(sorted(self.snapshot_files()), ["playlists/main.conf", "settings.conf"])

    def test_a_deleted_active_playlist_moves_to_the_first_by_name(self) -> None:
        self.write("playlists/zed.conf", 'NAME=Alpha\nMODE=shuffle\nINTERVAL=900\nMEMBERS="111"\n')
        self.conf.write_text("ACTIVE_PLAYLIST=gone\n", encoding="utf-8")
        self.engine()
        code, out, err = self.lwe("reload")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("settings.conf: ACTIVE_PLAYLIST gone -> zed, since playlists/gone.conf is gone",
                      out.splitlines())
        self.assertIn("ACTIVE_PLAYLIST=zed", self.conf.read_text(encoding="utf-8").splitlines())

    def test_a_changed_cc_on_screen_reshows_once_keeping_the_freeze(self) -> None:
        engine = self.engine(speed=0.0)
        self.assertEqual(self.lwe("reload")[0], 0)
        self.write("wp/111.conf", 'CC="1.2 1 1 0"\n')
        engine.calls.clear()
        self.assertEqual(self.lwe("reload")[1].splitlines(), ["wp/111.conf: added", "Applied."])
        cmds = [cmd for cmd, _args in engine.calls]
        self.assertEqual(cmds.count("show"), 1)
        after = [args for cmd, args in engine.calls[cmds.index("show"):] if cmd == "set-speed"]
        self.assertEqual(after, [{"speed": 0.0}], "the freeze is kept after the re-show")

    def test_a_changed_scaling_still_reshows_once(self) -> None:
        engine = self.engine()
        self.assertEqual(self.lwe("reload")[0], 0)
        self.conf.write_text("ACTIVE_PLAYLIST=main\nENGINE_VOLUME=15\nENGINE_SCALING=fill\n", encoding="utf-8")
        engine.calls.clear()
        self.assertEqual(self.lwe("reload")[0], 0)
        self.assertEqual(self.sent(engine).count("show"), 1)

    def test_a_changed_layer_rewrites_engine_env_and_names_the_restart(self) -> None:
        self.engine()
        self.conf.write_text("ACTIVE_PLAYLIST=main\nENGINE_LAYER=top\n", encoding="utf-8")
        self.live = {"LWE_ENGINE_ARGS": "--screen-root DP-1"}
        code, out, err = self.lwe("reload")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("--layer top", self.daemon_unit.parse_env(self.env_path.read_text(encoding="utf-8")
                                                                .split("\n"))["LWE_ENGINE_ARGS"])
        self.assertEqual(out.splitlines()[-1], "layer: waiting for lwe service restart.")
        self.live = None
        self.assertEqual(self.lwe("reload")[1].splitlines()[-1], "Applied.", "with no engine environment nothing waits")

    def test_every_kind_of_file_problem_is_named(self) -> None:
        self.engine()
        self.conf.write_text("ACTIVE_PLAYLIST=main\nENGINE_VOLUME=200\nENGINE_FPS=30\nENGINE_FPS=40\n",
                             encoding="utf-8")
        self.write("wp/111.conf", 'VOLUME=300\nCC="9 1 1 0"\nPROP_nosuch=1\nFOO=2\n')
        self.write("wp/bad id.conf", "VOLUME=1\n")
        self.write("app-condition.txt", "firefox\nthis-name-is-far-too-long\n")
        self.write("pause-blacklist.txt", "".join(f"app{n}\n" for n in range(130)))
        code, out, err = self.lwe("reload")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err.splitlines(), [
            "settings.conf:2: ENGINE_VOLUME=200 is not a whole number from 0 to 128",
            "wp/111.conf:1: VOLUME=300 is outside 0 to 128",
            "wp/111.conf:2: CC brightness, contrast and saturation must each be 0 to 4",
            "wp/bad id.conf: the file name is not a wallpaper id",
            "app-condition.txt:2: the entry is longer than 15 characters",
            "reload refused: 5 errors; nothing was applied or written"])
        self.write("wp/111.conf", "PROP_nosuch=1\nFOO=2\n")
        (self.paths.wp_dir() / "bad id.conf").unlink()
        self.write("app-condition.txt", "firefox\n")
        self.conf.write_text("ACTIVE_PLAYLIST=main\nENGINE_FPS=30\nENGINE_FPS=40\n", encoding="utf-8")
        code, out, err = self.lwe("reload")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines(), [
            FIRST,
            "warning: settings.conf:3: ENGINE_FPS is set again; line 2 is not read",
            "warning: wp/111.conf:2: FOO is not a key this version knows; it is kept and not applied",
            "warning: pause-blacklist.txt:129: entries past the first 128 are not read",
            "Applied."])

    def test_a_declared_property_is_quiet_and_an_undeclared_one_warns(self) -> None:
        project = self.paths.default_wallpapers_dir() / "111" / "project.json"
        project.write_text(json.dumps({"title": "WP 111", "type": "scene", "file": "scene.json",
                                       "general": {"properties": {"speed": {"type": "slider", "value": 1}}}}),
                           encoding="utf-8")
        self.write("wp/111.conf", "PROP_speed=2\nPROP_nosuch=1\n")
        code, out, err = self.lwe("reload")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("warning: wp/111.conf:2: the wallpaper declares no property nosuch; the line is kept and sent",
                      out.splitlines())
        self.assertNotIn("speed", " ".join(line for line in out.splitlines() if line.startswith("warning")))

    def test_the_json_shape_and_a_version_mismatch(self) -> None:
        engine = self.engine()
        code, out, err = self.lwe("-j", "reload")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"changes": None, "warnings": [], "cleanups": [], "outcome": "applied",
                                           "reason": "", "message": "", "env": "written", "restart": None})
        engine.set(version="0.0.1-other")
        snapshot = self.snapshot_files()
        self.conf.write_text("ACTIVE_PLAYLIST=main\nENGINE_VOLUME=40\n", encoding="utf-8")
        code, out, err = self.lwe("reload")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("0.0.1-other", err)
        self.assertEqual(self.snapshot_files(), snapshot, "a mismatch writes nothing")

    def test_an_engine_refusal_in_sync_exits_1_with_its_message(self) -> None:
        engine = self.engine()
        engine.script("show", _fake_engine.fail("no such wallpaper"))
        code, out, err = self.lwe("reload")
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines(), [FIRST])
        self.assertIn("The engine refused it: ", err)
        self.assertIn("no such wallpaper", err)

    def test_reload_takes_no_words(self) -> None:
        self.assertEqual(self.lwe("reload", "now"), (3, "", "reload takes no words; got now\n"))
        self.assertFalse(self.snapshot.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
