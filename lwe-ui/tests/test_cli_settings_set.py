"""The setting commands of the panel, restart and engine-env line classes: a panel setting saves one line
of settings.conf; a restart setting saves its line and rebuilds engine-env; watchdog and color are
engine-env lines with no settings.conf key. None of them sends the engine a request besides the status
read, and none runs systemctl.

Each form runs through cli.main in this process with HOME and the XDG folders at scratch
(_cli_env.scratch_home), daemon_unit's subprocess call replaced by a recorder, the screens faked and,
where a case needs one, a fake engine on the socket _sandbox pins. One class runs `layer top` and a
relative libraryfolder through the engine's handoff in a child whose environment is built from nothing
(_cli_env.scratch_env): PySide6 blocked, a hyprctl stub giving one screen and a failing systemctl stub on
PATH.

Run: PYTHONPATH=src python3 tests/test_cli_settings_set.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import contextlib
import fcntl
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import warnings
from pathlib import Path
from unittest import mock

import _cli_env
import _fake_engine

ROOT = Path(tempfile.mkdtemp(prefix="lwe-cli-set-"))
HOME = _cli_env.scratch_home(ROOT / "inproc")
RESTART = "takes effect at the next service restart (lwe service restart applies it)"
PANEL = "the panel reads it at its next scan"
PANEL_START = "a running panel picks it up when it next starts"
NOT_REBUILT = ("engine-env was not rebuilt because no screens were found; it catches up when the panel next "
               "starts or lwe service start or restart runs from your desktop session")
NOT_SAVED = ("not saved: engine-env is only rewritten where screens are found; run it from your desktop "
             "session, or open the panel once")
RESTART_ONLY = "applies only at a restart (lwe service restart)"
PANEL_ONLY = "read by the panel, not the engine"
PANEL_NAMES = ("reviewrequired", "storage", "detect", "detectevery", "libraryfolder", "workshopfolder",
               "steamfolder")


def receipt(words: str, when: str, saved: bool = True) -> str:
    return f"{words}: {'saved' if saved else 'unchanged'}; {when}.\n"


def tearDownModule() -> None:
    shutil.rmtree(ROOT, True)


class SettingSetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui import cli, version
        from lwe_ui.cli import help_text
        from lwe_ui.cli import help_pages
        from lwe_ui.engine import daemon_unit
        from lwe_ui.storage import paths
        cls.cli, cls.stamp, cls.paths, cls.daemon_unit = cli, version.panel_stamp(), paths, daemon_unit
        cls.help_text, cls.help_pages = help_text, help_pages
        cls.conf = paths.settings_file()
        cls.env_path = paths.config_dir() / daemon_unit.ENV_FILE_NAME

    def setUp(self) -> None:
        self.clear()
        self.outputs = ["DP-1"]
        self.runs: list = []
        du = self.daemon_unit
        for patcher in (mock.patch.object(du.subprocess, "run", lambda argv, **kw: self.runs.append(argv)),
                        mock.patch.object(du, "enumerate_outputs", lambda: list(self.outputs)),
                        mock.patch.object(du, "live_engine_env", lambda: None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.assertEqual(self.runs, [], "a subprocess ran")

    def clear(self) -> None:
        for folder in (self.paths.config_dir(), self.paths.state_dir()):
            shutil.rmtree(folder, True)
            folder.mkdir(parents=True)

    def engine(self, **fields) -> _fake_engine.FakeEngine:
        engine = _fake_engine.FakeEngine(_sandbox.SOCKET, served=False, **fields)
        self.addCleanup(engine.stop)
        return engine

    def lwe(self, *words: str, cwd_entered: bool = False) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(list(words), sender_stamp=self.stamp, cwd_entered=cwd_entered)
        return code, out.getvalue(), err.getvalue()

    def store(self, text: str) -> None:
        self.conf.write_text(text, encoding="utf-8")

    def seed_env(self) -> bytes:
        """engine-env as the panel writes it for the saved settings and one screen; returns its bytes."""
        self.assertEqual(self.daemon_unit.write_env(outputs=["DP-1"]), "written")
        return self.env_path.read_bytes()

    def env_lines(self) -> dict[str, str]:
        return self.daemon_unit.parse_env(self.env_path.read_text(encoding="utf-8").split("\n"))

    def snapshot(self) -> dict[str, bytes]:
        """Every file under the config and state folders but the lock sidecars."""
        out = {}
        for folder in (self.paths.config_dir(), self.paths.state_dir()):
            for path in sorted(folder.rglob("*")):
                if path.is_file() and self.paths.locks_dir() not in path.parents:
                    out[str(path)] = path.read_bytes()
        return out

    def changed(self, before: dict[str, bytes]) -> list[str]:
        after = self.snapshot()
        return sorted(Path(p).name for p in set(before) | set(after) if before.get(p) != after.get(p))

    def test_layer_top_changes_one_line_rebuilds_engine_env_and_sends_nothing(self) -> None:
        self.store("ENGINE_FPS=30\nENGINE_LAYER=bottom\nENGINE_VOLUME=40\n")
        self.seed_env()
        self.assertNotIn("--layer", self.env_lines()["LWE_ENGINE_ARGS"], "bottom is the engine's own layer")
        before = self.snapshot()
        engine = self.engine()
        self.assertEqual(self.lwe("layer", "top"), (0, receipt("layer top", RESTART), ""))
        self.assertEqual(self.conf.read_text(encoding="utf-8"), "ENGINE_FPS=30\nENGINE_LAYER=top\nENGINE_VOLUME=40\n")
        self.assertIn("--layer top", self.env_lines()["LWE_ENGINE_ARGS"])
        self.assertEqual(self.changed(before), ["engine-env", "settings.conf"])
        self.assertEqual(engine.calls, [("status", {})], "only the status read reaches the engine")
        code, out, err = self.lwe("-j", "videodecode", "auto")
        self.assertEqual((code, json.loads(out), err),
                         (0, {"setting": "videodecode", "value": "auto", "saved": True, "outcome": None,
                              "applies": "restart", "reason": ""}, ""))
        self.assertEqual(self.env_lines()["LWE_HWDEC"], "auto")

    def test_with_no_screens_the_line_is_saved_and_engine_env_stays_as_it_was(self) -> None:
        self.store("ENGINE_LAYER=bottom\n")
        env = self.seed_env()
        self.outputs = []
        self.assertEqual(self.lwe("layer", "top"), (0, f"layer top: saved; {NOT_REBUILT}.\n", ""))
        self.assertEqual(self.conf.read_text(encoding="utf-8"), "ENGINE_LAYER=top\n")
        self.assertEqual(self.env_path.read_bytes(), env)
        code, out, err = self.lwe("-j", "texturedetail", "full")
        self.assertEqual((code, json.loads(out), err),
                         (0, {"setting": "texturedetail", "value": "full", "saved": True, "outcome": None,
                              "applies": "restart", "reason": NOT_REBUILT}, ""))
        self.assertEqual(self.env_path.read_bytes(), env)

    def test_an_unchanged_value_writes_nothing_and_still_rebuilds_engine_env(self) -> None:
        self.store("ENGINE_LAYER=top\n")
        fresh = self.seed_env()
        stale = fresh.replace(b"\nLWE_HWDEC=no\n", b"\nLWE_HWDEC=stale\n")
        self.assertNotEqual(stale, fresh, "a line the next rebuild rewrites")
        self.env_path.write_bytes(stale)
        inode = self.conf.stat().st_ino
        self.assertEqual(self.lwe("layer", "top"), (0, receipt("layer top", RESTART, saved=False), ""))
        self.assertEqual((self.conf.stat().st_ino, self.conf.read_text(encoding="utf-8")),
                         (inode, "ENGINE_LAYER=top\n"))
        self.assertEqual(self.env_path.read_bytes(), fresh)
        self.assertEqual(self.lwe("watchdog", "1m")[0], 0)
        inode = self.env_path.stat().st_ino
        self.assertEqual(self.lwe("watchdog", "60"), (0, receipt("watchdog 1m", RESTART, saved=False), ""))
        self.assertEqual(self.env_path.stat().st_ino, inode, "an unchanged line writes nothing")

    def test_watchdog_is_an_engine_env_line_and_unset_puts_300_back(self) -> None:
        self.seed_env()
        before = self.snapshot()
        engine = self.engine()
        self.assertEqual(self.lwe("watchdog", "1m"), (0, receipt("watchdog 1m", RESTART), ""))
        self.assertIn("\nLWE_DEADMAN=60\n", self.env_path.read_text(encoding="utf-8"))
        self.assertEqual(self.changed(before), ["engine-env"], "watchdog has no settings.conf key")
        self.assertEqual(self.lwe("get", "watchdog"), (0, "1m\n", ""))
        self.assertEqual(self.lwe("config", "unset", "watchdog"), (0, receipt("watchdog 5m", RESTART), ""))
        self.assertIn("\nLWE_DEADMAN=300\n", self.env_path.read_text(encoding="utf-8"))
        self.assertEqual(engine.calls, [("status", {})] * 2)

    def test_with_no_screens_an_engine_env_line_is_not_saved(self) -> None:
        self.seed_env()
        self.assertEqual(self.lwe("color", "1 1 1 45")[0], 0)
        env = self.env_path.read_bytes()
        self.outputs = []
        for words in (("watchdog", "1m"), ("color", "2 1 1 0"), ("config", "unset", "color"),
                      ("config", "unset", "watchdog")):
            with self.subTest(words=words):
                self.assertEqual(self.lwe(*words), (1, "", NOT_SAVED + "\n"))
                self.assertEqual(self.env_path.read_bytes(), env)
        self.assertFalse(self.conf.exists())

    def test_color_saves_the_hue_in_radians_and_reads_back_in_degrees(self) -> None:
        self.seed_env()
        self.assertEqual(self.lwe("color", "1.2 1 1 90"), (0, receipt("color 1.2 1 1 90", RESTART), ""))
        self.assertEqual(self.env_lines()["LWE_CC"], "1.2 1 1 1.5707963267948966")
        self.assertEqual(self.lwe("get", "color"), (0, "1.2 1 1 90\n", ""))
        self.assertEqual(self.lwe("color", "1.2", "1", "1", "90"),
                         (3, "", 'color takes one quoted value, such as color "1 1 1 0"; got 4 words\n'))
        self.assertEqual(self.lwe("config", "unset", "color"), (0, receipt("color 1 1 1 0", RESTART), ""))
        self.assertNotIn("LWE_CC", self.env_lines())

    def test_detect_timer_stores_interval_with_no_status_read(self) -> None:
        engine = self.engine()
        self.store("DETECT_MODE=manual\n")
        self.assertEqual(self.lwe("detect", "timer"), (0, receipt("detect timer", PANEL_START), ""))
        self.assertEqual(self.conf.read_text(encoding="utf-8"), "DETECT_MODE=interval\n")
        self.assertEqual(self.lwe("detectevery", "90"), (0, receipt("detectevery 90s", PANEL_START), ""))
        self.assertEqual(self.lwe("storage", "reference"), (0, receipt("storage reference", PANEL), ""))
        self.assertEqual(self.lwe("config", "unset", "detect"), (0, receipt("detect watch", PANEL_START), ""))
        self.assertEqual(self.conf.read_text(encoding="utf-8"),
                         "DETECT_INTERVAL_SEC=90\nSTORAGE_POLICY=reference\n")
        self.assertEqual(engine.calls, [], "a panel setting reads no status")
        self.assertFalse(self.env_path.exists(), "a panel setting leaves engine-env alone")

    def test_toggle_flips_the_saved_value(self) -> None:
        self.store("ENGINE_TEXCOMP=false\n")
        self.assertEqual(self.lwe("texturecache", "toggle"), (0, receipt("texturecache on", RESTART), ""))
        self.assertEqual(self.lwe("reviewrequired", "toggle"), (0, receipt("reviewrequired off", PANEL), ""))
        self.assertEqual(self.lwe("get", "texturecache", "reviewrequired"), (0, "on\noff\n", ""))

    def test_a_relative_library_folder_resolves_only_in_the_entered_folder(self) -> None:
        work = ROOT / "work"
        (work / "lib").mkdir(parents=True, exist_ok=True)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(work)
        folder = os.path.join(os.path.realpath(work), "lib")
        warning = (f"warning: the engine looks for wallpapers only in {self.paths.default_wallpapers_dir()} and "
                   f"the Steam Workshop folder, so a wallpaper copied into {folder} may not show\n")
        self.assertEqual(self.lwe("libraryfolder", "lib", cwd_entered=True),
                         (0, receipt(f"libraryfolder {folder}", PANEL), warning))
        before = self.snapshot()
        self.assertEqual(self.lwe("libraryfolder", "other"), (1, "", "libraryfolder: give a full path\n"))
        self.assertEqual(self.lwe("workshopfolder", "/no/such/folder"),
                         (3, "", "workshopfolder takes an existing folder; got /no/such/folder\n"))
        self.assertEqual(self.snapshot(), before)
        library = self.paths.default_wallpapers_dir()
        library.mkdir(parents=True, exist_ok=True)
        self.assertEqual(self.lwe("libraryfolder", str(library)), (0, receipt(f"libraryfolder {library}", PANEL), ""))

    def test_a_held_settings_lock_refuses_after_the_wait_and_changes_nothing(self) -> None:
        self.store("ENGINE_LAYER=bottom\nDETECT_MODE=manual\n")
        self.seed_env()
        lock_path = self.paths.locks_dir() / "settings.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        before = self.snapshot()
        with open(lock_path, "a+", encoding="utf-8") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            for words in (("layer", "top"), ("detect", "timer")):
                with self.subTest(words=words):
                    start = time.monotonic()
                    result = self.lwe(*words)
                    waited = time.monotonic() - start
                    self.assertEqual(result, (1, "", f"Store busy: another writer holds {lock_path}\n"))
                    self.assertTrue(1.9 <= waited < 5.0, waited)
        self.assertEqual(self.snapshot(), before)

    def test_engine_env_is_rebuilt_while_the_settings_lock_is_still_held(self) -> None:
        from lwe_ui.storage import lock
        write_env, seen = self.daemon_unit.write_env, []

        def other_writer() -> None:
            try:
                with lock.held("settings", wait_s=0):
                    seen.append("settings free")
            except lock.StoreBusy:
                seen.append("settings held")

        def probe(*args, **kwargs) -> str:
            thread = threading.Thread(target=other_writer)
            thread.start()
            thread.join()
            return write_env(*args, **kwargs)

        self.store("ENGINE_LAYER=bottom\n")
        with mock.patch.object(self.daemon_unit, "write_env", probe):
            self.assertEqual(self.lwe("layer", "top")[0], 0)
        self.assertEqual(seen, ["settings held"])
        self.assertIn("--layer top", self.env_lines()["LWE_ENGINE_ARGS"])

    def test_typed_wrong_exits_3_before_any_lock_or_request(self) -> None:
        engine = self.engine()
        for words, message in ((("layer", "top", "bottom"), "layer takes one value; got top bottom"),
                               (("layer", "sideways"), "layer takes background, bottom, top or overlay; got sideways"),
                               (("watchdog", "25h"), "watchdog takes a whole number with s, m or h from 0s to 24h "
                                                     "(a bare number is seconds); got 25h"),
                               (("detectevery", "5"), "detectevery takes a whole number with s, m or h from 15s to "
                                                      "24h (a bare number is seconds); got 5"),
                               (("config", "layer", "top", "bottom"), "layer takes one value; got top bottom")):
            with self.subTest(words=words):
                self.assertEqual(self.lwe(*words), (3, "", message + "\n"))
        self.assertEqual(self.snapshot(), {})
        self.assertEqual(engine.calls, [])

    def test_an_assets_folder_the_engine_service_cannot_carry_is_refused_before_saving(self) -> None:
        self.store("ENGINE_LAYER=bottom\n")
        self.seed_env()
        folder = ROOT / "my assets"
        folder.mkdir(exist_ok=True)
        before = self.snapshot()
        reason = (f"ASSETS_DIR {str(folder)!r} contains whitespace or quotes; the engine service cannot represent it - "
                  "move the assets to a plain path or leave ASSETS_DIR empty for auto-discovery")
        self.assertEqual(self.lwe("assetsfolder", str(folder)), (3, "", f"assetsfolder: {reason}\n"))
        self.assertEqual(self.snapshot(), before)

    def test_a_write_keeps_the_store_s_snapped_value_warning_off_stderr(self) -> None:
        self.store("ENGINE_FPS=abc\nENGINE_LAYER=bottom\n")
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            for words in (("layer", "top"), ("-j", "layer", "bottom"), ("reviewrequired", "off"), ("watchdog", "1m"),
                          ("volume", "40"), ("debug", "audit", "on"), ("config", "unset", "layer")):
                with self.subTest(words=words):
                    code, _out, err = self.lwe(*words)
                    self.assertEqual((code, err), (0, ""))

    def test_a_bare_setting_prints_its_help_page(self) -> None:
        engine = self.engine()
        layer = self.help_pages.page("layer")
        self.assertEqual(self.lwe("layer"), (0, layer if layer.endswith("\n") else layer + "\n", ""))
        pages = {"layer": "the layer page\n"}
        with mock.patch.object(self.help_pages, "page", pages.get):
            self.assertEqual(self.lwe("layer"), (0, "the layer page\n", ""))
            self.assertEqual(self.lwe("detect"), (0, "detect manual | launch | timer | watch\n", ""))
            self.assertEqual(self.lwe("resclamp"), (0, self.help_text.RESCLAMP, ""))
        self.assertEqual(self.snapshot(), {})
        self.assertEqual(engine.calls, [])

    def test_config_setting_value_is_the_setting_verb(self) -> None:
        results = []
        for words in (("config", "layer", "top"), ("layer", "top")):
            self.clear()
            self.store("ENGINE_FPS=30\nENGINE_LAYER=bottom\n")
            self.seed_env()
            results.append((self.lwe(*words), self.conf.read_bytes(), self.env_path.read_bytes()))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][0], (0, receipt("layer top", RESTART), ""))

    def test_config_unset_layer_removes_every_line(self) -> None:
        self.store("ENGINE_LAYER=top\nENGINE_FPS=30\nENGINE_LAYER=overlay\n")
        self.seed_env()
        self.assertIn("--layer", self.env_lines()["LWE_ENGINE_ARGS"])
        self.assertEqual(self.lwe("config", "unset", "layer"), (0, receipt("layer bottom", RESTART), ""))
        self.assertEqual(self.conf.read_text(encoding="utf-8"), "ENGINE_FPS=30\n")
        self.assertNotIn("--layer", self.env_lines()["LWE_ENGINE_ARGS"])
        inode = self.conf.stat().st_ino
        self.assertEqual(self.lwe("config", "unset", "layer"), (0, receipt("layer bottom", RESTART, saved=False), ""))
        self.assertEqual(self.conf.stat().st_ino, inode)

    def test_config_unset_of_a_setting_that_is_not_saved_writes_no_file(self) -> None:
        self.assertEqual(self.lwe("config", "unset", "detect"),
                         (0, receipt("detect watch", PANEL_START, saved=False), ""))
        self.assertEqual(self.snapshot(), {})

    def test_config_unset_of_a_clamp_on_a_sharpfx_file_keeps_the_clamp_at_1(self) -> None:
        self.store("RENDER_RESOLUTION=sharpfx\nSSFACTOR=0.5\nCLAMPCOMPOSITES=0.5\n")
        self.seed_env()
        self.assertEqual((self.env_lines()["LWE_SSFACTOR"], self.env_lines()["LWE_CLAMPCOMPOSITES"]), ("0.5", "0.5"))
        self.assertEqual(self.lwe("config", "unset", "effectclamp"), (0, receipt("effectclamp 1", RESTART), ""))
        self.assertEqual(self.lwe("config", "unset", "resclamp"), (0, receipt("resclamp 1", RESTART), ""))
        lines = self.conf.read_text(encoding="utf-8").splitlines()
        self.assertEqual([line.split("=")[0] for line in lines], ["RENDER_RESOLUTION", "CLAMPCOMPOSITES"])
        self.assertEqual(float(lines[1].split("=")[1]), 1.0)
        self.assertEqual(self.lwe("get", "resclamp", "effectclamp"), (0, "1\n1\n", ""))
        self.assertEqual({"LWE_SSFACTOR", "LWE_CLAMPCOMPOSITES"} & set(self.env_lines()), set(), "1 writes no line")

    def test_load_and_the_unsets_with_no_default_exit_1(self) -> None:
        engine = self.engine()
        for name in ("layer", "videodecode", "assetsfolder", "watchdog", "color"):
            with self.subTest(name=name):
                self.assertEqual(self.lwe(name, "load"), (1, "", RESTART_ONLY + "\n"))
        for name in PANEL_NAMES:
            with self.subTest(name=name):
                self.assertEqual(self.lwe(name, "load"), (1, "", PANEL_ONLY + "\n"))
        self.assertEqual(self.lwe("config", "unset", "playlist"), (1, "", "playlist has no default\n"))
        self.assertEqual(self.lwe("config", "unset", "audiosmoothing"), (1, "", "audiosmoothing is not saved\n"))
        self.assertEqual(self.snapshot(), {})
        self.assertEqual(engine.calls, [])

    def test_a_running_engine_from_another_build_refuses_and_nothing_is_written(self) -> None:
        self.store("ENGINE_LAYER=bottom\nDETECT_MODE=manual\n")
        self.seed_env()
        before = self.snapshot()
        engine = self.engine(version="0.0.1-other")
        refusal = f"The running engine is 0.0.1-other but {self.stamp} is installed; run lwe service restart.\n"
        for words in (("layer", "top"), ("watchdog", "1m"), ("config", "unset", "layer")):
            with self.subTest(words=words):
                self.assertEqual(self.lwe(*words), (1, "", refusal))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(engine.calls, [("status", {})] * 3)
        self.assertEqual(self.lwe("detect", "timer")[0], 0, "a panel setting reads no status")
        self.assertEqual(len(engine.calls), 3)

    def test_a_failed_engine_env_write_is_named(self) -> None:
        self.store("ENGINE_LAYER=bottom\n")
        with mock.patch.object(self.daemon_unit, "write_env", side_effect=OSError("disk full")):
            self.assertEqual(self.lwe("layer", "top"),
                             (1, "", "saved, but the engine file could not be written: disk full\n"))
            self.assertEqual(self.lwe("watchdog", "1m"),
                             (1, "", "not saved: the engine file could not be written: disk full\n"))
        self.assertEqual(self.conf.read_text(encoding="utf-8"), "ENGINE_LAYER=top\n")
        self.assertFalse(self.env_path.exists())


class SettingHandoffTest(unittest.TestCase):
    def test_layer_top_through_the_engine_handoff_saves_and_rebuilds_engine_env_only(self) -> None:
        root = ROOT / "child"
        log = root / "systemctl.log"
        env = _cli_env.scratch_env(root, {"systemctl": f"echo \"$*\" >> '{log}'\nexit 1\n",
                                          "hyprctl": "echo '[{\"name\": \"DP-1\"}]'\n"})
        config = Path(env["XDG_CONFIG_HOME"]) / "lwe"
        config.mkdir(parents=True, exist_ok=True)
        (config / "settings.conf").write_text("ENGINE_FPS=30\nENGINE_LAYER=bottom\n", encoding="utf-8")
        handoff = root / "handoff"
        (handoff / "lib").mkdir(parents=True)
        with _fake_engine.FakeEngine(env["LWE_SOCKET"]) as engine:
            first = _cli_env.run_lwe(["layer", "top"], env, str(handoff))
            second = _cli_env.run_lwe(["libraryfolder", "lib"], env, str(handoff))
        self.assertEqual(first, (0, receipt("layer top", RESTART), ""))
        folder = os.path.join(os.path.realpath(handoff), "lib")
        self.assertEqual(second[:2], (0, receipt(f"libraryfolder {folder}", PANEL)))
        self.assertTrue(second[2].startswith("warning: the engine looks for wallpapers only in "), second[2])
        self.assertEqual((config / "settings.conf").read_text(encoding="utf-8"),
                         f"ENGINE_FPS=30\nENGINE_LAYER=top\nWALLPAPERS_DIR={folder}\n")
        text = (config / "engine-env").read_text(encoding="utf-8")
        self.assertIn("--screen-root DP-1", text)
        self.assertIn("--layer top", text)
        self.assertEqual(engine.calls, [("status", {})], "one status read, by the restart setting")
        self.assertFalse(log.exists(), "systemctl was run")


if __name__ == "__main__":
    unittest.main(verbosity=2)
