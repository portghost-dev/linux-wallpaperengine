"""lwe debug: a switch is saved as its engine variable, one line in engine-env, and nothing else happens.

Each form runs through cli.main in this process with HOME and the XDG folders at scratch
(_cli_env.scratch_home), daemon_unit's subprocess call replaced by a recorder, one screen faked and a
fake engine on the test's socket. One run goes through the engine's handoff in a child process whose
environment is built from nothing (_cli_env.scratch_env): PySide6 blocked, a hyprctl stub giving one
screen and a failing systemctl stub on PATH. No case runs systemctl or sends the engine a request.

Run: PYTHONPATH=src python3 tests/test_cli_debug.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import contextlib
import difflib
import io
import json
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import _cli_env
import _fake_engine

ROOT = Path(tempfile.mkdtemp(prefix="lwe-cli-debug-"))
HOME = _cli_env.scratch_home(ROOT / "inproc")
APPLIES = "for the next service start (lwe service restart applies it)"
NO_SCREENS = ("not saved: engine-env is only rewritten where screens are found; run it from your desktop "
              "session, or open the panel once")
USAGE = ("usage: lwe debug <switch> <value>, lwe debug <switch> or lwe debug unset <switch>; "
         "help --debug lists the switches")


def systemd_value(line: str) -> str:
    """The value systemd gives the process for one EnvironmentFile line (systemd.exec(5)):
    '...' verbatim; "..." keeps the character after a backslash for \\" \\\\ \\` \\$ and both
    characters otherwise; unquoted, a backslash keeps the next character and outer blanks go."""
    raw = line.partition("=")[2].strip(" \t\r")
    if raw[:1] == "'":
        end = raw.index("'", 1)
        assert raw[end + 1:].strip(" \t\r") == "", line
        return raw[1:end]
    out, i = [], 1 if raw[:1] == '"' else 0
    while i < len(raw):
        ch = raw[i]
        if raw[:1] == '"' and ch == '"':
            assert raw[i + 1:].strip(" \t\r") == "", line
            return "".join(out)
        if ch == "\\" and i + 1 < len(raw):
            keep_both = raw[:1] == '"' and raw[i + 1] not in '"\\`$'
            out.append(raw[i:i + 2] if keep_both else raw[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    assert raw[:1] != '"', f"unterminated double quote in {line!r}"
    return "".join(out)


def tearDownModule() -> None:
    shutil.rmtree(ROOT, True)


class DebugVerbTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui import cli, version
        from lwe_ui.cli import debug_table
        from lwe_ui.engine import daemon_unit
        cls.cli, cls.table, cls.daemon_unit, cls.stamp = cli, debug_table, daemon_unit, version.panel_stamp()
        cls.engine = _fake_engine.FakeEngine(_sandbox.SOCKET)
        cls.env_path = daemon_unit.paths.config_dir() / daemon_unit.ENV_FILE_NAME

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.stop()

    def setUp(self) -> None:
        self.env_path.unlink(missing_ok=True)
        self.outputs = ["DP-1"]
        self.calls: list = []
        du = self.daemon_unit
        for patcher in (mock.patch.object(du.subprocess, "run", lambda argv, **kw: self.calls.append(argv)),
                        mock.patch.object(du, "enumerate_outputs", lambda: list(self.outputs)),
                        mock.patch.object(du, "live_engine_env", lambda: None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.assertEqual(self.calls, [], "a subprocess ran")
        self.assertEqual(self.engine.calls, [], "the engine got a request")

    def lwe(self, *words: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(list(words), sender_stamp=self.stamp)
        return code, out.getvalue(), err.getvalue()

    def seed(self, **lines: str) -> bytes:
        """engine-env as the panel writes it, holding these lines besides its own, and a hand-written
        comment above the first; returns its bytes."""
        self.daemon_unit.write_env(lines)
        text = self.env_path.read_text(encoding="utf-8")
        first = next(iter(lines))
        self.env_path.write_text(text.replace(f"\n{first}=", f"\n# by hand\n{first}=", 1), encoding="utf-8")
        self.daemon_unit.write_env()
        return self.env_path.read_bytes()

    def test_a_switch_adds_its_line_and_leaves_every_other_line_as_it_was(self) -> None:
        before = self.seed(LWE_IMGPROBE="1")
        self.assertEqual(self.lwe("debug", "audit", "on"), (0, f"audit on: saved {APPLIES}\n", ""))
        after = self.env_path.read_bytes()
        diff = difflib.ndiff(before.decode().splitlines(True), after.decode().splitlines(True))
        self.assertEqual([d for d in diff if d[:2] in ("- ", "+ ")], ["+ LWE_AUDIT=1\n"])
        stale = after.replace(b"\nLWE_HWDEC=no\n", b"\nLWE_HWDEC=stale\n")
        self.assertNotEqual(stale, after, "a line the next rebuild would rewrite")
        self.env_path.write_bytes(stale)
        self.assertEqual(self.lwe("debug", "audit", "on"),
                         (0, "audit on: unchanged, already saved for the next service start\n", ""))
        self.assertEqual(self.env_path.read_bytes(), stale, "an unchanged switch writes nothing")
        code, out, err = self.lwe("debug", "overlaysize", "20", "-j")
        self.assertEqual((code, json.loads(out), err),
                         (0, {"switch": "overlaysize", "variable": "LWE_OVERLAY_SIZE", "value": "20",
                              "line": "LWE_OVERLAY_SIZE=20"}, ""))

    def test_off_and_unset_remove_their_lines(self) -> None:
        self.seed(LWE_AUDIT="1", LWE_OVERLAY_TEXT="x")
        self.assertEqual(self.lwe("debug", "audit", "off"), (0, f"audit off: saved {APPLIES}\n", ""))
        self.assertEqual(self.lwe("debug", "unset", "overlaytext"), (0, f"overlaytext: removed {APPLIES}\n", ""))
        text = self.env_path.read_text(encoding="utf-8")
        self.assertNotIn("LWE_AUDIT", text)
        self.assertNotIn("LWE_OVERLAY_TEXT", text)
        inode = self.env_path.stat().st_ino
        self.assertEqual(self.lwe("debug", "unset", "overlaytext"), (0, "overlaytext is not set\n", ""))
        self.assertEqual(self.lwe("-j", "debug", "unset", "audit"),
                         (0, '{"switch":"audit","variable":"LWE_AUDIT","value":null,"line":null}\n', ""))
        self.assertEqual(self.env_path.stat().st_ino, inode, "removing a switch that is not set writes nothing")

    def test_a_value_with_a_space_reads_back_through_parse_env_and_systemd_rules(self) -> None:
        self.seed(LWE_IMGPROBE="1")
        self.assertEqual(self.lwe("debug", "overlaytext", "a b"), (0, f"overlaytext a b: saved {APPLIES}\n", ""))
        line = next(ln for ln in self.env_path.read_text(encoding="utf-8").splitlines()
                    if ln.startswith("LWE_OVERLAY_TEXT="))
        self.assertEqual(line, "LWE_OVERLAY_TEXT='a b'")
        self.assertEqual(self.daemon_unit.parse_env([line])["LWE_OVERLAY_TEXT"], "a b")
        self.assertEqual(systemd_value(line), "a b")

    def test_webidletime_2s_is_written_as_2000(self) -> None:
        self.seed(LWE_IMGPROBE="1")
        self.assertEqual(self.lwe("debug", "webidletime", "2s"), (0, f"webidletime 2s: saved {APPLIES}\n", ""))
        self.assertIn("\nLWE_WEB_IDLE_EXIT_MS=2000\n", self.env_path.read_text(encoding="utf-8"))

    def test_refusals_exit_3_in_the_engine_words_and_write_nothing(self) -> None:
        before = self.seed(LWE_IMGPROBE="1")
        broken = "overlaytext: a value with a line break or NUL cannot be saved in engine-env"
        variable = "LWE_AUDIT is an engine variable; debug takes its switch, audit"
        spaces = "debug takes <switch> <value>; quote a value that has spaces"
        for words, message in ((("videoextraframes", "300"),
                                "videoextraframes takes a whole number from 0 to 256; got 300"),
                               (("nosuch", "on"), "unknown debugging switch nosuch; help --debug lists them"),
                               (("LWE_AUDIT", "1"), variable), (("unset", "LWE_AUDIT"), variable),
                               (("overlaytext", "a\nb"), broken), (("overlaytext", "a\0b"), broken),
                               ((), USAGE), (("unset",), USAGE), (("objectpixels", "0", "0", "10", "10"), spaces)):
            with self.subTest(words=words):
                self.assertEqual(self.lwe("debug", *words), (3, "", message + "\n"))
        self.assertEqual(self.env_path.read_bytes(), before)

    def test_a_set_testtexturelimit_survives_write_files(self) -> None:
        self.seed(LWE_IMGPROBE="1")
        self.assertEqual(self.lwe("debug", "testtexturelimit", "2048")[0], 0)
        reload = mock.Mock(return_value=types.SimpleNamespace(returncode=0, stderr="", stdout=""))
        engine = "/usr/local/bin/linux-wallpaperengine"
        with mock.patch.object(self.daemon_unit.subprocess, "run", reload), \
                mock.patch.object(self.daemon_unit, "resolve_engine_bin", lambda: engine):
            self.daemon_unit.write_files()
        self.assertIn("\nLWE_TEXCAP=2048\n", self.env_path.read_text(encoding="utf-8"))

    def test_no_screens_saves_nothing_and_exits_1(self) -> None:
        before = self.seed(LWE_OVERLAY_TEXT="x")
        self.outputs = []
        self.assertEqual(self.lwe("debug", "audit", "on"), (1, "", NO_SCREENS + "\n"))
        self.assertEqual(self.lwe("debug", "unset", "overlaytext"), (1, "", NO_SCREENS + "\n"))
        self.assertEqual(self.env_path.read_bytes(), before)

    def test_the_bare_form_reads_hand_added_lines_back_as_switches(self) -> None:
        self.seed(LWE_IMGPROBE="1")
        with self.env_path.open("a", encoding="utf-8") as f:
            f.write("LWE_AUDIT=1\nLWE_WEB_IDLE_EXIT_MS=2000\n")
        audit = self.table.BY_NAME["audit"]
        self.assertEqual(self.lwe("debug", "audit"),
                         (0, f"audit takes on | off\n{audit.description}\nengine-env: on\n", ""))
        code, out, err = self.lwe("debug", "-j", "webidletime")
        self.assertEqual((code, json.loads(out), err),
                         (0, {"switch": "webidletime", "variable": "LWE_WEB_IDLE_EXIT_MS",
                              "accepts": self.table.BY_NAME["webidletime"].accepts, "value": "2000"}, ""))
        self.assertTrue(self.lwe("debug", "cameraprobe")[1].endswith("\nengine-env: not set\n"))


class DebugHandoffTest(unittest.TestCase):
    def test_a_switch_through_the_engine_handoff_writes_engine_env_only(self) -> None:
        root = ROOT / "child"
        log = root / "systemctl.log"
        env = _cli_env.scratch_env(root, {"hyprctl": "echo '[{\"name\": \"DP-1\"}]'\n",
                                          "systemctl": f"echo \"$*\" >> '{log}'\nexit 1\n"})
        with _fake_engine.FakeEngine(env["LWE_SOCKET"]) as engine:
            first = _cli_env.run_lwe(["debug", "audit", "on"], env, str(root))
            second = _cli_env.run_lwe(["debug", "-j", "audit"], env, str(root))
        self.assertEqual(first, (0, f"audit on: saved {APPLIES}\n", ""))
        self.assertEqual((second[0], json.loads(second[1]), second[2]),
                         (0, {"switch": "audit", "variable": "LWE_AUDIT", "accepts": "on | off", "value": "on"}, ""))
        text = (Path(env["XDG_CONFIG_HOME"]) / "lwe" / "engine-env").read_text(encoding="utf-8")
        self.assertIn("--screen-root DP-1", text)
        self.assertIn("\nLWE_AUDIT=1\n", text)
        self.assertEqual(engine.calls, [])
        self.assertFalse(log.exists(), "systemctl was run")


if __name__ == "__main__":
    unittest.main(verbosity=2)
