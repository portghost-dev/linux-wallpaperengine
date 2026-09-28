"""lwe help: each screen through cli.main in this process, byte for byte against the fixture screens
in tests/fixtures/help (help resclamp still prints its fixed page), a page made from every command, setting
and per-wallpaper row, five of them pinned in tests/fixtures/help/pages, and help --debug through a
fake engine named by ENGINE_BIN.

The environment is rebuilt from nothing before any lwe_ui import: HOME, the XDG folders, the engine
socket and PATH all point into a scratch folder, so the fake engine that help --debug starts inherits
nothing from the caller, and no real engine on PATH or in the install places can be found.

Run: PYTHONPATH=src python3 tests/test_cli_help.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import contextlib
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "help"
ROOT = Path(tempfile.mkdtemp(prefix="lwe-cli-help-"))
HOME = ROOT / "home"
CONFIG = HOME / "config"

for folder in (HOME / "bin", CONFIG / "lwe", HOME / "state", HOME / "data", HOME / "rt"):
    folder.mkdir(parents=True)
os.environ.clear()
os.environ.update({
    "HOME": str(HOME),
    "XDG_CONFIG_HOME": str(CONFIG),
    "XDG_STATE_HOME": str(HOME / "state"),
    "XDG_DATA_HOME": str(HOME / "data"),
    "XDG_RUNTIME_DIR": str(HOME / "rt"),
    "LWE_SOCKET": str(HOME / "rt" / "engine.sock"),
    "LWE_SANDBOX": "1",
    "PATH": str(HOME / "bin"),
})


class CliHelpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui import cli
        cls.cli = cli

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(ROOT, True)

    def _help(self, *words: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(["help", *words])
        return code, out.getvalue(), err.getvalue()

    def _engine(self, body: str) -> None:
        fake = ROOT / "engine"
        fake.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        fake.chmod(0o755)
        (CONFIG / "lwe" / "settings.conf").write_text(f"ENGINE_BIN={fake}\n", encoding="utf-8")

    def test_each_form_prints_its_fixed_screen(self) -> None:
        forms = [((), "main"), (("--all",), "all"), (("schedule",), "schedule"), (("files",), "files"),
                 (("resclamp",), "resclamp"), (("resolution",), "resclamp"), (("sharpness",), "resclamp"),
                 (("blur",), "resclamp"), (("video", "memory"), "resclamp")]
        for words, screen in forms:
            for flags in ((), ("-j",)):
                with self.subTest(words=words, flags=flags):
                    code, out, err = self._help(*words, *flags)
                    self.assertEqual(code, 0, err)
                    self.assertEqual(out.encode("utf-8"), (FIXTURES / f"{screen}.txt").read_bytes())
                    self.assertEqual(err, "")

    def test_debug_prints_the_engine_switch_list_under_the_lwe_header(self) -> None:
        self._engine(f"exec /bin/cat '{FIXTURES / 'engine-debug.txt'}'\n")
        code, out, err = self._help("--debug")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.encode("utf-8"), (FIXTURES / "debug.txt").read_bytes())
        self.assertEqual(err, "")

    def test_debug_without_an_engine_is_refused(self) -> None:
        (CONFIG / "lwe" / "settings.conf").write_text("", encoding="utf-8")
        code, out, err = self._help("--debug")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, "lwe help --debug needs the engine, which was not found\n")

    def test_debug_with_an_engine_that_fails_is_refused(self) -> None:
        self._engine("exit 1\n")
        code, out, err = self._help("--debug")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, "lwe help --debug: the engine did not print its switch list\n")

    def test_debug_with_the_rows_but_not_the_header_is_refused(self) -> None:
        rows = ROOT / "rows.txt"
        rows.write_text((FIXTURES / "engine-debug.txt").read_text(encoding="utf-8").split("\n\n", 1)[1], encoding="utf-8")
        self._engine(f"exec /bin/cat '{rows}'\n")
        code, out, err = self._help("--debug")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, "lwe help --debug: the engine did not print its switch list\n")

    def test_debug_with_the_list_and_a_non_zero_exit_is_refused(self) -> None:
        self._engine(f"/bin/cat '{FIXTURES / 'engine-debug.txt'}'\nexit 3\n")
        code, out, err = self._help("--debug")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, "lwe help --debug: the engine did not print its switch list\n")

    def test_debug_with_an_engine_that_outlives_the_timeout_is_refused(self) -> None:
        from lwe_ui.cli.verbs import help as help_verb
        self._engine(f"/bin/cat '{FIXTURES / 'engine-debug.txt'}'\nexec /bin/sleep 5\n")
        with mock.patch.object(help_verb, "DEBUG_TIMEOUT_S", 0.5):
            code, out, err = self._help("--debug")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, "lwe help --debug: the engine did not print its switch list\n")

    def test_every_row_gives_a_page_ending_in_one_newline_within_100_columns(self) -> None:
        from lwe_ui.cli import help_pages, vocabulary
        rows = ([(r["name"], help_pages.command_page, r) for r in vocabulary.COMMANDS]
                + [(r["name"], help_pages.setting_page, r) for r in vocabulary.SETTINGS]
                + [(r["name"], help_pages.wallpaper_page, r) for r in vocabulary.PER_WALLPAPER])
        self.assertEqual(len(rows), 99)
        for name, make, row in rows:
            with self.subTest(row=name):
                page = make(row)
                self.assertTrue(page.endswith("\n") and not page.endswith("\n\n"))
                self.assertLessEqual(max(len(line) for line in page.splitlines()), 100)

    def test_a_topic_resolves_as_a_command_then_a_setting_then_a_wallpaper_word(self) -> None:
        from lwe_ui.cli import help_pages, vocabulary
        fixed = {"schedule": "schedule", "resclamp": "resclamp"}
        topics = [r["name"] for r in vocabulary.COMMANDS if not r["name"].startswith("help")]
        topics += [r["name"] for r in vocabulary.SETTINGS]
        topics += [w for r in vocabulary.PER_WALLPAPER for w in r["name"].split(" / ")]
        for topic in topics:
            with self.subTest(topic=topic):
                code, out, err = self._help(*topic.split(" "))
                self.assertEqual((code, err), (0, ""))
                if topic in fixed:
                    self.assertEqual(out.encode("utf-8"), (FIXTURES / f"{fixed[topic]}.txt").read_bytes())
                else:
                    self.assertEqual(out, help_pages.page(topic))
        playlist = [r for r in vocabulary.COMMANDS if r["name"] == "playlist"][0]
        fullscreen = [r for r in vocabulary.SETTINGS if r["name"] == "fullscreen"][0]
        self.assertEqual(self._help("playlist")[1], help_pages.command_page(playlist))
        self.assertEqual(self._help("fullscreen")[1], help_pages.setting_page(fullscreen))

    def test_five_generated_pages_are_pinned(self) -> None:
        for topic, words in (("pause", ["pause"]), ("volume", ["volume"]), ("hide", ["hide"]),
                             ("properties", ["wallpaper", "...", "properties"]),
                             ("objects", ["wallpaper", "...", "objects"])):
            with self.subTest(topic=topic):
                code, out, err = self._help(*words)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(out.encode("utf-8"), (FIXTURES / "pages" / f"{topic}.txt").read_bytes())

    def test_socket_has_no_page_and_speed_names_the_saved_range(self) -> None:
        self.assertEqual(self._help("socket"), (3, "", "lwe help: no page for socket; lwe help lists every command and "
                                                       "lwe help --all every setting\n"))
        code, out, err = self._help("speed")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("0.1 to 10 (1 is normal; 0 freezes it and is not saved)", " ".join(out.split()))

    def test_an_unknown_topic_exits_3(self) -> None:
        code, out, err = self._help("nosuchtopic")
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertEqual(
            err, "lwe help: no page for nosuchtopic; lwe help lists every command and lwe help --all every setting\n")


if __name__ == "__main__":
    unittest.main()
