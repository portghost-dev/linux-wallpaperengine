"""The panel's version stamp and the strict match with the lwe that sent a command.

panel_stamp() reads the VERSION link beside lwe_ui/version.py and parses it the way the engine's
build does; the command entry refuses a sender of another version, or a missing stamp file, before
any verb but help runs; running_refusal gives the three answers a verb that reads status needs.

The environment is rebuilt from nothing before any lwe_ui import, and a probe verb in a scratch
folder stands in for a verb that is not help.

Run: PYTHONPATH=src python3 tests/test_cli_version.py
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

TESTS = Path(__file__).resolve().parent
REPO_VERSION = TESTS.parent.parent / "VERSION"
ROOT = Path(tempfile.mkdtemp(prefix="lwe-cli-version-"))
HOME = ROOT / "home"
STAMP = REPO_VERSION.read_text(encoding="utf-8").split("\n", 1)[0]

for folder in (HOME / "bin", HOME / "config", HOME / "state", HOME / "data", HOME / "rt", ROOT / "verbs"):
    folder.mkdir(parents=True)
os.environ.clear()
os.environ.update({
    "HOME": str(HOME),
    "XDG_CONFIG_HOME": str(HOME / "config"),
    "XDG_STATE_HOME": str(HOME / "state"),
    "XDG_DATA_HOME": str(HOME / "data"),
    "XDG_RUNTIME_DIR": str(HOME / "rt"),
    "LWE_SOCKET": str(HOME / "rt" / "engine.sock"),
    "LWE_SANDBOX": "1",
    "PATH": str(HOME / "bin"),
})

PROBE = '''
from lwe_ui.cli.registry import Verb


def ran(ctx, args):
    print("ran", file=ctx.out)
    return 0


VERBS = (Verb("probe", ran, "reports that it ran", "test"),)
'''


class CliVersionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        (ROOT / "verbs" / "probe.py").write_text(PROBE, encoding="utf-8")
        import lwe_ui.cli.verbs
        lwe_ui.cli.verbs.__path__.append(str(ROOT / "verbs"))
        from lwe_ui import cli
        cls.cli = cli

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(ROOT, True)

    def _version(self):
        from lwe_ui import version
        return version

    def _main(self, words: list[str], sender: str | None) -> tuple[int, bytes, bytes]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(words, sender_stamp=sender)
        return code, out.getvalue().encode("utf-8"), err.getvalue().encode("utf-8")

    def test_the_stamp_is_the_repo_version_through_the_link(self) -> None:
        version = self._version()
        self.assertEqual(version.panel_stamp(), STAMP)
        self.assertEqual(os.readlink(version.STAMP_FILE), "../../../VERSION")
        self.assertEqual(version.STAMP_FILE.resolve(), REPO_VERSION.resolve())

    def test_each_stamp_file_edge_case_gives_its_listed_result(self) -> None:
        version = self._version()
        stamp_file = ROOT / "edge" / "VERSION"
        stamp_file.parent.mkdir()
        refused = (f"the panel's version file {stamp_file} is missing or empty; "
                   "reinstall with bash install.sh")
        rows = [
            ("1.1.0", b"1.1.0\n", "1.1.0"),
            ("empty file", b"", None),
            ("blank line", b"\n1.1.0\n", None),
            ("a space", b" \n", None),
            ("a tab", b"\t\n", None),
            ('"1.1.0 " trailing', b"1.1.0 \n", "1.1.0"),
            ("CRLF", b"1.1.0\r\n", "1.1.0"),
            ("BOM + 1.1.0", b"\xef\xbb\xbf1.1.0\n", "1.1.0"),
        ]
        for name, raw, expected in rows:
            with self.subTest(row=name):
                stamp_file.write_bytes(raw)
                with mock.patch.object(version, "STAMP_FILE", stamp_file):
                    if expected is None:
                        with self.assertRaises(version.StampError) as caught:
                            version.panel_stamp()
                        self.assertEqual(str(caught.exception), refused)
                    else:
                        self.assertEqual(version.panel_stamp(), expected)

    def test_a_sender_of_another_version_is_refused_in_text_and_json(self) -> None:
        line = (f"The engine is 9.9.9 but the panel is {STAMP}; "
                "install both from one build (bash install.sh).")
        for flags, expected in (([], line.encode("utf-8") + b"\n"),
                                (["-j"], b'{"error":"' + line.encode("utf-8") + b'"}\n')):
            with self.subTest(flags=flags):
                code, out, err = self._main(["probe", *flags], "9.9.9")
                self.assertEqual(code, 1)
                self.assertEqual(out, b"")
                self.assertEqual(err, expected)

    def test_help_is_exempt_and_no_sender_skips_the_check(self) -> None:
        version = self._version()
        main = (TESTS / "fixtures" / "help" / "main.txt").read_bytes()
        with mock.patch.object(version, "STAMP_FILE", ROOT / "missing" / "VERSION"):
            self.assertEqual(self._main(["help"], "9.9.9"), (0, main, b""))
        self.assertEqual(self._main(["help"], "9.9.9"), (0, main, b""))
        self.assertEqual(self._main(["probe"], None), (0, b"ran\n", b""))
        self.assertEqual(self._main(["probe"], STAMP), (0, b"ran\n", b""))

    def test_a_missing_stamp_file_refuses_every_verb_but_help(self) -> None:
        version = self._version()
        missing = ROOT / "missing" / "VERSION"
        with mock.patch.object(version, "STAMP_FILE", missing):
            code, out, err = self._main(["probe"], None)
        self.assertEqual(code, 1)
        self.assertEqual(out, b"")
        self.assertEqual(err, (f"the panel's version file {missing} is missing or empty; "
                               "reinstall with bash install.sh\n").encode("utf-8"))

    def test_running_refusal_gives_its_three_answers(self) -> None:
        version = self._version()
        self.assertIsNone(version.running_refusal({"version": "1.2.0"}, "1.2.0"))
        self.assertEqual(version.running_refusal({}, "1.2.0"),
                         "The running engine is older than 1.1.0 but 1.2.0 is installed; run lwe service restart.")
        self.assertEqual(version.running_refusal({"version": "1.1.0"}, "1.2.0"),
                         "The running engine is 1.1.0 but 1.2.0 is installed; run lwe service restart.")


if __name__ == "__main__":
    unittest.main()
