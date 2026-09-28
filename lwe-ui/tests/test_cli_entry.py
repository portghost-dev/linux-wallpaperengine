"""The command entry: `lwe-ui --lwe <version> <folder> <words>` runs a command without Qt.

Every run is a child process whose PySide6 import raises (a poison package first on PYTHONPATH,
proven to bite) and whose environment is built from scratch. A handoff with no verb, an unknown verb
or missing handoff tokens exits 3; a verb module found on the verbs path is run and its exit code
returned; two modules declaring one verb are refused, naming both; the handoff folder becomes the
working directory; --tray inside a handoff is a command word; -j and --json are exact tokens; a verb
or verb module that raises prints one internal error line and exits 1. A start without --lwe calls
app.main with sys.argv intact and imports nothing from lwe_ui.cli, also through the console entry
that pyproject.toml names.

Run: PYTHONPATH=src python3 tests/test_cli_entry.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

PROBE = '''
import os
from lwe_ui.cli.registry import Verb


def where(ctx, args):
    print(os.getcwd(), ctx.cwd_entered, ctx.sender_stamp, ctx.json, " ".join(args), sep="|",
          file=ctx.out)
    return 7


def busy(ctx, args):
    from lwe_ui.storage.lock import StoreBusy
    raise StoreBusy("Store busy: another writer holds settings.lock")


def crash(ctx, args):
    raise ValueError("broken\\nvalue")


VERBS = (Verb("where", where, "prints the working folder", "test"),
         Verb("busy", busy, "meets a busy store", "test"),
         Verb("crash", crash, "raises an unexpected error", "test"))
'''

TWIN = '''
from lwe_ui.cli.registry import Verb

VERBS = (Verb("twin", lambda ctx, args: 0, "declared twice", "test"),)
'''

RUN_WITH_VERBS = '''
import sys
import lwe_ui.cli.verbs
lwe_ui.cli.verbs.__path__.append(sys.argv[1])
from lwe_ui import launch
raise SystemExit(launch.main(["lwe-ui", *sys.argv[2:]]))
'''

DISCOVER = '''
import sys
import lwe_ui.cli.verbs
from lwe_ui.cli import registry
lwe_ui.cli.verbs.__path__.extend(sys.argv[1:])
try:
    registry.discover()
except RuntimeError as exc:
    print(exc)
else:
    print("no refusal")
'''

WITHOUT_MARKER = '''
import sys
import types
calls = []


def main(*args):
    calls.append(args)
    return 42


app = types.ModuleType("lwe_ui.app")
app.main = main
sys.modules["lwe_ui.app"] = app
from lwe_ui import launch
print([launch.main(["lwe-ui"]), launch.main(["lwe-ui", "--tray"])], calls)
'''

NORMAL_START = '''
import importlib.metadata
import json
import sys
import types
seen = []


def main(*args):
    seen.append([list(args), list(sys.argv)])
    return 42


app = types.ModuleType("lwe_ui.app")
app.main = main
sys.modules["lwe_ui.app"] = app
target, sys.argv = sys.argv[1], sys.argv[2:]
code = importlib.metadata.EntryPoint("lwe-ui", target, "console_scripts").load()()
cli = sorted(m for m in sys.modules if m == "lwe_ui.cli" or m.startswith("lwe_ui.cli."))
print(json.dumps({"code": code, "seen": seen, "cli": cli}))
'''


class CliEntryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="lwe-cli-entry-"))
        pkg = cls.root / "poison" / "PySide6"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(
            'raise ImportError("PySide6 is unavailable in this test")\n', encoding="utf-8")
        for sub in ("home/bin", "home/rt", "verbs", "start", "there"):
            (cls.root / sub).mkdir(parents=True)
        (cls.root / "verbs" / "probe.py").write_text(PROBE, encoding="utf-8")
        probe = cls._run([sys.executable, "-c", "import PySide6"])
        if probe.returncode == 0 or "ImportError" not in probe.stderr:
            raise AssertionError(f"the PySide6 poison did not bite: {probe.returncode} {probe.stderr}")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, True)

    @classmethod
    def _run(cls, args: list[str]) -> subprocess.CompletedProcess:
        home = cls.root / "home"
        env = {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / "c"),
            "XDG_STATE_HOME": str(home / "s"),
            "XDG_DATA_HOME": str(home / "d"),
            "XDG_RUNTIME_DIR": str(home / "rt"),
            "LWE_SOCKET": str(home / "rt" / "engine.sock"),
            "PATH": str(home / "bin"),
            "PYTHONPATH": os.pathsep.join([str(cls.root / "poison"), str(SRC)]),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        return subprocess.run(args, env=env, cwd=cls.root / "start", capture_output=True,
                              encoding="utf-8", timeout=60)

    def _entry(self, *words: str) -> subprocess.CompletedProcess:
        return self._run([sys.executable, "-m", "lwe_ui", *words])

    def _with_verbs(self, *words: str) -> subprocess.CompletedProcess:
        return self._run([sys.executable, "-c", RUN_WITH_VERBS, str(self.root / "verbs"), *words])

    def _normal_start(self, target: str, *argv: str) -> dict:
        r = self._run([sys.executable, "-c", NORMAL_START, target, *argv])
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_no_verb_prints_usage_and_exits_3(self) -> None:
        for flags, line in (([], "usage: lwe [-j] <command> [value ...]"),
                            (["-j"], '{"error":"usage: lwe [-j] <command> [value ...]"}')):
            with self.subTest(flags=flags):
                r = self._entry("--lwe", "1.2.0", str(self.root / "there"), *flags)
                self.assertEqual(r.returncode, 3, r.stderr)
                self.assertEqual(r.stdout, "")
                self.assertEqual(r.stderr.splitlines(), [line])

    def test_unknown_verb_exits_3(self) -> None:
        for flags, line in (([], "lwe: no-such-verb is not a command"),
                            (["--json"], '{"error":"no-such-verb is not a command"}')):
            with self.subTest(flags=flags):
                r = self._entry("--lwe", "1.2.0", str(self.root / "there"), "no-such-verb", *flags)
                self.assertEqual(r.returncode, 3, r.stderr)
                self.assertEqual(r.stdout, "")
                self.assertEqual(r.stderr.splitlines(), [line])
        r = self._entry("--lwe", "1.2.0", str(self.root / "there"), "caf\u00e9", "--json")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(r.stderr.splitlines(), ['{"error":"caf\u00e9 is not a command"}'])

    def test_a_verb_module_on_the_verbs_path_is_run_and_its_code_returned(self) -> None:
        r = self._with_verbs("--lwe", "1.2.0", str(self.root / "there"), "-j", "where", "one", "--json")
        self.assertEqual(r.returncode, 7, r.stderr)
        self.assertEqual(r.stdout.strip().split("|")[2:], ["1.2.0", "True", "one"])
        for flags, line in (([], "Store busy: another writer holds settings.lock"),
                            (["-j"], '{"error":"Store busy: another writer holds settings.lock"}')):
            with self.subTest(flags=flags):
                busy = self._with_verbs("--lwe", "1.2.0", str(self.root / "there"), "busy", *flags)
                self.assertEqual(busy.returncode, 1, busy.stderr)
                self.assertEqual(busy.stderr.splitlines(), [line])

    def test_two_modules_declaring_one_verb_are_refused(self) -> None:
        first, second = self.root / "dup-first", self.root / "dup-second"
        first.mkdir()
        second.mkdir()
        (first / "dup_b.py").write_text(TWIN, encoding="utf-8")
        (second / "dup_a.py").write_text(TWIN, encoding="utf-8")
        r = self._run([sys.executable, "-c", DISCOVER, str(first), str(second)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(),
                         "verb twin is declared by lwe_ui.cli.verbs.dup_a and lwe_ui.cli.verbs.dup_b")

    def test_missing_handoff_tokens_exit_3(self) -> None:
        for words in (["--lwe"], ["--lwe", "1.2.0"]):
            with self.subTest(words=words):
                r = self._entry(*words)
                self.assertEqual(r.returncode, 3, r.stderr)
                self.assertEqual(r.stdout, "")
                self.assertEqual(r.stderr.splitlines(),
                                 ["usage: lwe-ui --lwe <version> <folder> [word ...]"])

    def test_the_handoff_folder_becomes_the_working_directory(self) -> None:
        start = os.path.realpath(self.root / "start")
        there = os.path.realpath(self.root / "there")
        cases = [(there, there, "True"), (str(self.root / "gone"), start, "False"), ("", start, "False")]
        locked = self.root / "locked"
        locked.mkdir(mode=0o000)
        self.addCleanup(locked.chmod, 0o755)
        if os.geteuid() != 0:
            cases.append((str(locked), start, "False"))
        for folder, cwd, entered in cases:
            with self.subTest(folder=folder):
                r = self._with_verbs("--lwe", "1.2.0", folder, "where")
                self.assertEqual(r.returncode, 7, r.stderr)
                self.assertEqual(r.stdout.split("|")[:2], [cwd, entered])

    def test_without_the_marker_launch_calls_app_main(self) -> None:
        r = self._run([sys.executable, "-c", WITHOUT_MARKER])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "[42, 42] [(), ()]")

    def test_a_handoff_carrying_tray_reaches_the_command_entry(self) -> None:
        r = self._entry("--lwe", "v", "", "--tray")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertEqual(r.stderr.splitlines(), ["lwe: --tray is not a command"])

    def test_json_flags_are_exact_tokens(self) -> None:
        r = self._with_verbs("--lwe", "v", str(self.root / "there"), "where", "-jx", "--jsonx")
        self.assertEqual(r.returncode, 7, r.stderr)
        self.assertEqual(r.stdout.strip().split("|")[3:], ["False", "-jx --jsonx"])

    def test_the_normal_branch_passes_sys_argv_to_app_main_unchanged(self) -> None:
        argv = ["lwe-ui", "--tray", "-platform", "offscreen"]
        start = self._normal_start("lwe_ui.launch:main", *argv)
        self.assertEqual((start["code"], start["seen"]), (42, [[[], argv]]))

    def test_the_console_entry_is_launch_main_and_takes_the_normal_branch(self) -> None:
        pyproject = tomllib.loads((SRC.parent / "pyproject.toml").read_text(encoding="utf-8"))
        target = pyproject["project"]["scripts"]["lwe-ui"]
        self.assertEqual(target, "lwe_ui.launch:main")
        start = self._normal_start(target, "lwe-ui")
        self.assertEqual((start["code"], start["seen"]), (42, [[[], ["lwe-ui"]]]))

    def test_the_normal_branch_imports_nothing_from_cli(self) -> None:
        for argv in (["lwe-ui"], ["lwe-ui", "--tray"]):
            with self.subTest(argv=argv):
                self.assertEqual(self._normal_start("lwe_ui.launch:main", *argv)["cli"], [])

    def test_a_raising_verb_or_verb_module_prints_one_internal_error_line(self) -> None:
        broken = self.root / "broken"
        broken.mkdir()
        (broken / "bad.py").write_text('raise ValueError("broken module")\n', encoding="utf-8")
        cases = (
            ([], "verbs", "crash", "internal error: ValueError: broken value"),
            (["-j"], "verbs", "crash", '{"error":"internal error: ValueError: broken value"}'),
            ([], "broken", "where", "internal error: ValueError: broken module"),
            (["-j"], "broken", "where", '{"error":"internal error: ValueError: broken module"}'),
        )
        for flags, folder, verb, line in cases:
            with self.subTest(flags=flags, folder=folder):
                r = self._run([sys.executable, "-c", RUN_WITH_VERBS, str(self.root / folder),
                               "--lwe", "v", str(self.root / "there"), verb, *flags])
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertEqual(r.stdout, "")
                self.assertEqual(r.stderr.splitlines(), [line])


if __name__ == "__main__":
    unittest.main(verbosity=2)
