"""lwe help: each screen through cli.main in this process, byte for byte against the fixture screens
in tests/fixtures/help, and help --debug through a fake engine named by ENGINE_BIN.

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

    def test_an_unknown_topic_exits_3(self) -> None:
        code, out, err = self._help("nosuchtopic")
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertEqual(
            err, "lwe help: no page for nosuchtopic; lwe help lists every command and lwe help --all every setting\n")


if __name__ == "__main__":
    unittest.main()
