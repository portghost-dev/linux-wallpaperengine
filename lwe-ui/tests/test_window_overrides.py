"""The window's session overrides at quit: only what the window set, and nobody changed since, is cleared.

mute, audioreactive, mouse and parallax are saved settings the deck and the commands both set. When the
window sets one, it writes an owner note in panel state with the value it wrote; any other write that
changes the key drops the note; at quit the window clears a key only while its note still matches the
stored value, then removes the note. So `lwe mute on` survives a window quit, the window's own mute is
cleared, a command's change after the window's drops the note, and a stale note is left alone. The same
holds for parallax.

Each case drives a real Backend offscreen and the command entry in this process, with HOME and the XDG
folders at scratch, daemon_unit's subprocess call recorded and nothing listening on the sandbox socket.

Run: PYTHONPATH=src python3 tests/test_window_overrides.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _cli_env

ROOT = Path(tempfile.mkdtemp(prefix="lwe-window-overrides-"))
HOME = _cli_env.scratch_home(ROOT / "inproc")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtGui import QGuiApplication  # noqa: E402

_APP = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])

from lwe_ui import cli, models, version  # noqa: E402
from lwe_ui.engine import daemon_unit  # noqa: E402
from lwe_ui.storage import paths, settings  # noqa: E402


def tearDownModule() -> None:
    shutil.rmtree(ROOT, True)


class WindowOverridesTest(unittest.TestCase):
    def setUp(self) -> None:
        for folder in (paths.config_dir(), paths.state_dir()):
            shutil.rmtree(folder, True)
            folder.mkdir(parents=True)
        settings.save(settings.load())
        self.runs: list = []
        for patcher in (mock.patch.object(daemon_unit.subprocess, "run", lambda argv, **kw: self.runs.append(argv)),
                        mock.patch.object(daemon_unit, "enumerate_outputs", lambda: ["DP-1"]),
                        mock.patch.object(daemon_unit, "live_engine_env", lambda: None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.window = models.Backend()

    def tearDown(self) -> None:
        self.assertEqual(self.runs, [], "a subprocess ran")

    def lwe(self, *words: str) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.main(list(words), sender_stamp=version.panel_stamp())

    def test_a_command_s_mute_survives_the_window_quit(self) -> None:
        for words, key in ((("mute", "on"), "OVERRIDE_MUTE"), (("parallax", "off"), "OVERRIDE_PARALLAX_OFF")):
            with self.subTest(words=words):
                self.assertEqual(self.lwe(*words), 0)
                self.assertIs(settings.load()[key], True)
                self.window.restoreSessionOverrides()
                self.assertIs(settings.load()[key], True)
                self.assertEqual(settings.window_notes(), {})

    def test_the_window_s_own_override_is_cleared_at_quit(self) -> None:
        for name, key in (("mute", "OVERRIDE_MUTE"), ("parallax", "OVERRIDE_PARALLAX_OFF")):
            with self.subTest(override=name):
                self.window.setSessionOverride(name, True)
                self.assertEqual(settings.window_notes(), {key: True})
                self.window.restoreSessionOverrides()
                self.assertIs(settings.load()[key], False)
                self.assertEqual(settings.window_notes(), {})

    def test_a_command_after_the_window_drops_the_note_and_its_value_stays(self) -> None:
        for name, key, words in (("mute", "OVERRIDE_MUTE", ("mute", "off")),
                                 ("parallax", "OVERRIDE_PARALLAX_OFF", ("parallax", "on"))):
            with self.subTest(override=name):
                self.window.setSessionOverride(name, True)
                self.assertEqual(settings.window_notes(), {key: True})
                self.assertEqual(self.lwe(*words), 0)
                self.assertEqual(settings.window_notes(), {})
                self.window.restoreSessionOverrides()
                self.assertIs(settings.load()[key], False)
                self.assertEqual(settings.window_notes(), {})

    def test_a_window_write_that_changes_nothing_takes_no_note(self) -> None:
        self.assertEqual(self.lwe("mute", "on"), 0)
        self.window.setSessionOverride("mute", True)
        self.assertEqual(settings.window_notes(), {})
        self.window.restoreSessionOverrides()
        self.assertIs(settings.load()["OVERRIDE_MUTE"], True)

    def test_a_stale_note_whose_value_no_longer_matches_is_left_alone(self) -> None:
        self.window.setSessionOverride("mute", True)
        self.window.setSessionOverride("mute", False)
        self.assertEqual(settings.window_notes(), {"OVERRIDE_MUTE": False})
        path = paths.settings_file()
        path.write_text(path.read_text(encoding="utf-8").replace("OVERRIDE_MUTE=false", "OVERRIDE_MUTE=true"),
                        encoding="utf-8")
        self.window.restoreSessionOverrides()
        self.assertIs(settings.load()["OVERRIDE_MUTE"], True)
        self.assertEqual(settings.window_notes(), {"OVERRIDE_MUTE": False})


if __name__ == "__main__":
    unittest.main(verbosity=2)
