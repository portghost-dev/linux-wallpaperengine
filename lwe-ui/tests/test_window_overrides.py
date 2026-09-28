"""The window's session overrides at quit: only what the window set, and nobody wrote since, is cleared.

mute, audioreactive, mouse and parallax are saved settings the deck and the commands both set. When the
window turns one on, settings.conf names it as the window's in the same write (settings.OWNED_KEY). A
write that changes the key ends that in its own text, and so does a command or a backup import that sets
the key, even to the same value; a write that fails changes neither the value nor the ownership. At quit
the window reads its ownership under the settings lock, then turns off what it still owns and ends all of
it in one write the sync marker records first. So `lwe mute on` survives a window quit and the window's own mute is cleared;
a later write that puts the window's value back does not revive its claim; a command whose write failed
leaves the claim; an import that sets a key the window set takes it over, and one without the key leaves
it; a quit racing a restore makes no write the marker has not recorded. Panel-state files that cannot be
written change none of this, and the ownership line stays out of an export.

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
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import _cli_env

ROOT = Path(tempfile.mkdtemp(prefix="lwe-window-overrides-"))
HOME = _cli_env.scratch_home(ROOT / "inproc")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtGui import QGuiApplication  # noqa: E402

_APP = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])

from lwe_ui import cli, models, version  # noqa: E402
from lwe_ui import constants as C  # noqa: E402
from lwe_ui.engine import daemon_unit, marker  # noqa: E402
from lwe_ui.storage import atomic, backup, lock, paths, settings, tier_a  # noqa: E402


def tearDownModule() -> None:
    shutil.rmtree(ROOT, True)


@contextlib.contextmanager
def settings_write_fails():
    """Every atomic write of settings.conf raises OSError."""
    real = atomic.atomic_write_text

    def write(path, text, *args, **kwargs):
        if Path(path) == paths.settings_file():
            raise OSError("settings disk full")
        return real(path, text, *args, **kwargs)
    with mock.patch.object(atomic, "atomic_write_text", write):
        yield


@contextlib.contextmanager
def mute_line_refused():
    """An edit of settings.conf that changes the OVERRIDE_MUTE line raises ValueError; other edits go on."""
    real = tier_a.edit

    def edit(text, changes, *args, **kwargs):
        if "OVERRIDE_MUTE" in changes:
            raise ValueError("cannot edit OVERRIDE_MUTE")
        return real(text, changes, *args, **kwargs)
    with mock.patch.object(tier_a, "edit", edit):
        yield


@contextlib.contextmanager
def panel_state_read_only():
    """A file directly in panel state, the sync marker apart, can be neither written nor removed."""
    real_text, real_json, real_unlink = atomic.atomic_write_text, atomic.atomic_write_json, Path.unlink

    def check(path) -> None:
        path = Path(path)
        if path.parent == paths.panel_state_dir() and path != marker._file():
            raise OSError("panel state is read-only")

    def write_text(path, *args, **kwargs):
        check(path)
        return real_text(path, *args, **kwargs)

    def write_json(path, *args, **kwargs):
        check(path)
        return real_json(path, *args, **kwargs)

    def unlink(path, missing_ok=False):
        check(path)
        return real_unlink(path, missing_ok=missing_ok)
    with mock.patch.object(atomic, "atomic_write_text", write_text), \
            mock.patch.object(atomic, "atomic_write_json", write_json), mock.patch.object(Path, "unlink", unlink):
        yield


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

    def say(self, *words: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(words), sender_stamp=version.panel_stamp())
        return code, out.getvalue()

    def lwe(self, *words: str) -> int:
        return self.say(*words)[0]

    def archive(self, name: str) -> Path:
        target = paths.state_dir() / f"{name}.lwebackup"
        self.assertFalse(backup.export_to(target)["errors"])
        return target

    def test_a_command_s_mute_survives_the_window_quit(self) -> None:
        for words, key in ((("mute", "on"), "OVERRIDE_MUTE"), (("parallax", "off"), "OVERRIDE_PARALLAX_OFF")):
            with self.subTest(words=words):
                self.assertEqual(self.lwe(*words), 0)
                self.assertIs(settings.load()[key], True)
                self.window.restoreSessionOverrides()
                self.assertIs(settings.load()[key], True)
                self.assertEqual(settings.window_owned(), [])

    def test_the_window_s_own_override_is_cleared_at_quit(self) -> None:
        for name, key in (("mute", "OVERRIDE_MUTE"), ("parallax", "OVERRIDE_PARALLAX_OFF")):
            with self.subTest(override=name):
                self.window.setSessionOverride(name, True)
                self.assertEqual(settings.window_owned(), [key])
                self.window.restoreSessionOverrides()
                self.assertIs(settings.load()[key], False)
                self.assertEqual(settings.window_owned(), [])

    def test_a_command_after_the_window_ends_its_ownership_and_its_value_stays(self) -> None:
        for name, key, words in (("mute", "OVERRIDE_MUTE", ("mute", "off")),
                                 ("parallax", "OVERRIDE_PARALLAX_OFF", ("parallax", "on"))):
            with self.subTest(override=name):
                self.window.setSessionOverride(name, True)
                self.assertEqual(settings.window_owned(), [key])
                self.assertEqual(self.lwe(*words), 0)
                self.assertEqual(settings.window_owned(), [])
                self.window.restoreSessionOverrides()
                self.assertIs(settings.load()[key], False)

    def test_a_window_write_that_changes_nothing_takes_no_ownership(self) -> None:
        self.assertEqual(self.lwe("mute", "on"), 0)
        self.window.setSessionOverride("mute", True)
        self.assertEqual(settings.window_owned(), [])
        self.window.restoreSessionOverrides()
        self.assertIs(settings.load()["OVERRIDE_MUTE"], True)

    def test_a_value_set_by_hand_after_the_window_turned_it_off_is_left_alone(self) -> None:
        self.window.setSessionOverride("mute", True)
        self.window.setSessionOverride("mute", False)
        self.assertEqual(settings.window_owned(), [])
        path = paths.settings_file()
        path.write_text(path.read_text(encoding="utf-8").replace("OVERRIDE_MUTE=false", "OVERRIDE_MUTE=true"),
                        encoding="utf-8")
        self.window.restoreSessionOverrides()
        self.assertIs(settings.load()["OVERRIDE_MUTE"], True)

    def test_a_key_the_window_owns_that_was_set_off_by_hand_loses_its_ownership_at_quit(self) -> None:
        self.window.setSessionOverride("mute", True)
        path = paths.settings_file()
        path.write_text(path.read_text(encoding="utf-8").replace("OVERRIDE_MUTE=true", "OVERRIDE_MUTE=false"),
                        encoding="utf-8")
        self.window.restoreSessionOverrides()
        self.assertEqual((settings.load()["OVERRIDE_MUTE"], settings.window_owned()), (False, []))

    def test_a_command_with_the_same_value_takes_the_override_over(self) -> None:
        for name, key, words in (("mute", "OVERRIDE_MUTE", ("mute", "on")),
                                 ("audio", "OVERRIDE_AUDIO_OFF", ("audioreactive", "off")),
                                 ("mouse", "OVERRIDE_MOUSE_OFF", ("mouse", "off")),
                                 ("parallax", "OVERRIDE_PARALLAX_OFF", ("config", "parallax", "off"))):
            with self.subTest(override=name):
                self.window.setSessionOverride(name, True)
                self.assertEqual(settings.window_owned(), [key])
                took = self.say(*words)
                self.assertEqual((took[0], settings.window_owned()), (0, []))
                self.assertEqual(self.say(*words), took)
                self.window.restoreSessionOverrides()
                self.assertIs(settings.load()[key], True)
        self.window.setSessionOverride("mute", False)
        self.window.setSessionOverride("mute", True)
        self.assertEqual(self.lwe("config", "unset", "mute"), 0)
        self.assertEqual(settings.window_owned(), [])

    def test_a_window_write_that_fails_changes_neither_value_nor_ownership(self) -> None:
        before = paths.settings_file().read_bytes()
        with settings_write_fails():
            self.window.setSessionOverride("mute", True)
        self.assertEqual((paths.settings_file().read_bytes(), settings.window_owned()), (before, []))
        with mock.patch.object(settings.tier_a, "edit", side_effect=ValueError("line refused")):
            with self.assertRaises(ValueError):
                self.window.setSessionOverride("mute", True)
        self.assertEqual((paths.settings_file().read_bytes(), settings.window_owned()), (before, []))

    def test_a_later_write_that_puts_the_window_s_value_back_does_not_revive_its_claim(self) -> None:
        self.window.setSessionOverride("mute", True)
        with panel_state_read_only():
            for value in (False, True):
                self.window.save_setting("OVERRIDE_MUTE", value)
        self.window.restoreSessionOverrides()
        self.assertIs(settings.load()["OVERRIDE_MUTE"], True)

    def test_a_command_whose_write_fails_leaves_the_window_s_claim(self) -> None:
        for words, fault in ((("mute", "off"), settings_write_fails), (("mute", "on"), settings_write_fails),
                             (("mute", "off"), mute_line_refused)):
            with self.subTest(words=words, fault=fault.__name__):
                self.window.setSessionOverride("mute", True)
                with fault():
                    code = self.lwe(*words)
                self.window.restoreSessionOverrides()
                self.assertEqual((code, settings.load()["OVERRIDE_MUTE"]), (1, False))

    def test_a_quit_racing_a_restore_makes_no_write_the_marker_has_not_recorded(self) -> None:
        off = self.archive("mute-off")
        self.window.setSessionOverride("mute", True)
        on = self.archive("mute-on")
        restored: list = []

        def restore(target: Path) -> None:
            with panel_state_read_only():
                refused = backup.apply(backup.preflight(target)).get("refused", False)
            restored.append((refused, settings.load()["OVERRIDE_MUTE"]))
        restore(off)
        unrecorded: list = []
        real_write, real_clear = atomic.atomic_write_text, settings.clear_window_overrides

        def record(path, text, *args, **kwargs):
            if Path(path) == paths.settings_file() and "marker" not in lock._holding():
                unrecorded.append(marker.read()["classes"])
            return real_write(path, text, *args, **kwargs)

        def clear_after_a_restore() -> list:
            racer = threading.Thread(target=restore, args=(on,))
            racer.start()
            racer.join(10)
            return real_clear()
        with mock.patch.object(settings, "clear_window_overrides", clear_after_a_restore), \
                mock.patch.object(atomic, "atomic_write_text", record):
            self.window.restoreSessionOverrides()
        self.assertEqual([refused for refused, _mute in restored], [False] * len(restored))
        self.assertEqual((unrecorded, settings.load()["OVERRIDE_MUTE"]), ([], restored[-1][1]))

    def test_an_import_that_restores_the_window_s_value_takes_it_over(self) -> None:
        settings.update({"OVERRIDE_MUTE": True})
        target = self.archive("mute-on")
        for door in ("apply", "command"):
            with self.subTest(door=door):
                settings.update({"OVERRIDE_MUTE": False})
                self.window.setSessionOverride("mute", True)
                if door == "apply":
                    self.assertFalse(backup.apply(backup.preflight(target)).get("refused", False))
                else:
                    self.assertEqual(self.lwe("backup", "import", str(target)), 0)
                self.window.restoreSessionOverrides()
                self.assertIs(settings.load()["OVERRIDE_MUTE"], True)

    def test_an_import_without_the_session_keys_leaves_the_window_s_claim(self) -> None:
        full = self.archive("full")
        partial = paths.state_dir() / "partial.lwebackup"
        session = tuple(key.encode("utf-8") for key in settings.WINDOW_OVERRIDES)
        with zipfile.ZipFile(full) as source, zipfile.ZipFile(partial, "w") as dest:
            for item in source.infolist():
                data = source.read(item)
                if item.filename == settings.MEMBER:
                    data = b"".join(line for line in data.splitlines(keepends=True) if not line.startswith(session))
                dest.writestr(item, data)
        self.window.setSessionOverride("mute", True)
        self.assertFalse(backup.apply(backup.preflight(partial)).get("refused", False))
        self.window.restoreSessionOverrides()
        self.assertIs(settings.load()["OVERRIDE_MUTE"], False)

    def test_the_quit_reads_and_writes_settings_only_under_the_settings_lock(self) -> None:
        self.window.setSessionOverride("mute", True)
        seen: list = []
        real_read, real_write = Path.read_bytes, atomic.atomic_write_text

        def read(path):
            if path == paths.settings_file():
                seen.append(("read", "settings" in lock._holding()))
            return real_read(path)

        def write(path, text, *args, **kwargs):
            if Path(path) == paths.settings_file():
                seen.append(("write", {"settings", "marker"} <= lock._holding()
                             and "BUNDLE" in marker.read()["classes"]))
            return real_write(path, text, *args, **kwargs)
        with mock.patch.object(Path, "read_bytes", read), mock.patch.object(atomic, "atomic_write_text", write):
            self.window.restoreSessionOverrides()
        writes = [i for i, (kind, _held) in enumerate(seen) if kind == "write"]
        self.assertTrue(writes)
        self.assertEqual(seen[:writes[-1] + 1], [(kind, True) for kind, _held in seen[:writes[-1] + 1]])
        self.assertIs(settings.load()["OVERRIDE_MUTE"], False)

    def test_the_window_s_ownership_stays_out_of_an_export(self) -> None:
        self.window.setSessionOverride("mute", True)
        with zipfile.ZipFile(self.archive("out")) as z:
            keys = set(tier_a.parse(z.read(settings.MEMBER).decode("utf-8")))
        self.assertLessEqual(keys, set(C.SETTINGS_SCHEMA))


if __name__ == "__main__":
    unittest.main(verbosity=2)
