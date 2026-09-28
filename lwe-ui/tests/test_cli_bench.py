"""lwe bench: one wallpaper in a test window of its own, and the summary when the window closes.

Every command runs in a child whose PySide6 import raises and whose environment is built from
nothing, with WAYLAND_DISPLAY set to a value only. ENGINE_BIN names a fake engine, a script with
this Python's absolute path as its shebang, which records its argv and environment and then plays
one part: a frame and exit 0, fatal lines and exit 134, exit 1 without a frame, a frame and a fatal
line, a frame and a signal, or a frame and sleep until killed (one sleeper ignores SIGTERM). A fake
hyprctl on the scratch PATH answers monitors, getoption and clients and logs every call. The argv
has the window geometry, no socket, no daemon and the render folder last; the environment adds the
present trace and the title; each ending gets its summary and exit code; refusals launch nothing;
a stop signal ends the engine, the window is placed at most PLACE_TRIES times, and the summary still
prints; the log holds the header and the engine's lines. No real engine runs, nothing opens on a
display, and no child connects to the engine socket.

Run: PYTHONPATH=src python3 tests/test_cli_bench.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_TMP = tempfile.TemporaryDirectory(prefix="lwe-bench-")
_ROOT = Path(_TMP.name)
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = str(_ROOT / _sub) if _sub else str(_ROOT)
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from lwe_ui import version  # noqa: E402
from lwe_ui.storage import paths, settings, tags  # noqa: E402

LIB = _ROOT / "lib"
WORKSHOP = _ROOT / "workshop"
ASSETS = _ROOT / "assets"
BIN = _ROOT / "bin"
FAKE = _ROOT / "fake"
ENGINE = FAKE / "engine"
RECORD = FAKE / "record.json"
MODE = FAKE / "mode"
HYPR_LOG = FAKE / "hyprctl.log"
LOG = _ROOT / "s" / "lwe" / "logs" / "bench" / "command.log"
SOCK = _ROOT / "rt" / "engine.sock"
MARKER = "LWE-PRESENT viewport=100x100"
FATAL = "terminate called after throwing an instance of 'x'"
LATER = "what(): a later fatal line"
_RUNS: dict[str, subprocess.CompletedProcess] = {}
_RECORDS: dict[str, dict | None] = {}
_LOGS: dict[str, str | None] = {}
_LIVE: dict[str, dict] = {}
_FACTS: dict = {}

FAKE_ENGINE = """#!{python}
import json, os, signal, sys, time
with open({tmp!r}, "w") as f:
    json.dump({{"argv": sys.argv, "env": dict(os.environ), "pid": os.getpid()}}, f)
os.replace({tmp!r}, {record!r})
mode = open({mode!r}).read().strip()
print("fake engine " + mode, flush=True)
print("fake engine stderr", file=sys.stderr, flush=True)
if mode == "crash":
    print({fatal!r}, flush=True)
    print({later!r}, flush=True)
    sys.exit(134)
if mode == "noframe":
    sys.exit(1)
if mode == "stubborn":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
print({marker!r}, flush=True)
if mode == "late":
    print({fatal!r}, flush=True)
if mode == "signal":
    os.kill(os.getpid(), signal.SIGUSR1)
while mode in ("sleep", "stubborn"):
    time.sleep(60)
"""

FAKE_HYPRCTL = """#!{python}
import json, sys
args = sys.argv[1:]
with open({log!r}, "a") as f:
    f.write(" ".join(args) + "\\n")
if args == ["-j", "monitors"]:
    print(json.dumps([{{"focused": True, "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1.0,
                       "reserved": [0, 0, 0, 0]}}]))
elif args == ["getoption", "general:gaps_out", "-j"]:
    print(json.dumps({{"css": "10 10 10 10"}}))
elif args == ["-j", "clients"]:
    try:
        pid = json.load(open({record!r}))["pid"]
    except (OSError, ValueError):
        pid = 0
    print(json.dumps([{{"pid": pid, "address": "0xfake", "at": [0, 0], "floating": False}}]))
elif args[:1] == ["dispatch"]:
    print("ok")
"""


def _env(display: bool = True, **extra: str) -> dict[str, str]:
    env = {"HOME": str(_ROOT), "XDG_CONFIG_HOME": str(_ROOT / "c"), "XDG_STATE_HOME": str(_ROOT / "s"),
           "XDG_DATA_HOME": str(_ROOT / "d"), "XDG_RUNTIME_DIR": str(_ROOT / "rt"), "LWE_SOCKET": str(SOCK),
           "PATH": str(BIN), "PYTHONPATH": os.pathsep.join([str(_ROOT / "poison"), str(SRC)]),
           "LWE_SANDBOX": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    if display:
        env["WAYLAND_DISPLAY"] = "wayland-test"
    env.update(extra)
    return env


def _command(words: list[str]) -> list[str]:
    return [sys.executable, "-m", "lwe_ui", "--lwe", version.panel_stamp(), str(_ROOT / "cwd"), *words]


def _run(name: str, words: list[str], mode: str = "ok", env: dict[str, str] | None = None) -> None:
    MODE.write_text(mode, encoding="utf-8")
    RECORD.unlink(missing_ok=True)
    _RUNS[name] = subprocess.run(_command(words), env=env or _env(), cwd=_ROOT / "cwd", capture_output=True,
                                 encoding="utf-8", timeout=60)
    _RECORDS[name] = json.loads(RECORD.read_text(encoding="utf-8")) if RECORD.exists() else None
    _LOGS[name] = LOG.read_text(encoding="utf-8") if LOG.exists() else None


def _until(ready, what: str, child: subprocess.Popen, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not ready():
        if child.poll() is not None:
            raise AssertionError(f"the command ended (exit {child.returncode}) before {what}")
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.05)


def _moves() -> list[str]:
    if not HYPR_LOG.exists():
        return []
    return [line for line in HYPR_LOG.read_text(encoding="utf-8").splitlines()
            if line.startswith("dispatch hl.dsp.window.move(")]


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _live(name: str, mode: str, signum: int, moves: int = 0) -> None:
    """Start a bench of a sleeping engine, wait for its frame (and for `moves` window moves), send
    signum to the command and time how long the command takes to finish."""
    MODE.write_text(mode, encoding="utf-8")
    RECORD.unlink(missing_ok=True)
    HYPR_LOG.unlink(missing_ok=True)
    out, err = FAKE / f"{name}.out", FAKE / f"{name}.err"
    pid = 0
    code, took, error = None, 0.0, None
    with open(out, "w", encoding="utf-8") as o, open(err, "w", encoding="utf-8") as e:
        child = subprocess.Popen(_command(["bench", "1500000001"]), env=_env(), cwd=_ROOT / "cwd", stdout=o,
                                 stderr=e)
        try:
            _until(lambda: RECORD.exists() and LOG.exists() and MARKER in LOG.read_text(encoding="utf-8"),
                   "the engine's frame", child)
            pid = json.loads(RECORD.read_text(encoding="utf-8"))["pid"]
            if moves:
                _until(lambda: len(_moves()) >= moves, f"{moves} window moves", child)
                time.sleep(1.2)
            sent = time.monotonic()
            child.send_signal(signum)
            code = child.wait(timeout=15)
            took = time.monotonic() - sent
        except (AssertionError, subprocess.TimeoutExpired) as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            if not pid and RECORD.exists():
                pid = json.loads(RECORD.read_text(encoding="utf-8"))["pid"]
            if pid and not _gone(pid):
                os.kill(pid, signal.SIGKILL)
    _LIVE[name] = {"code": code, "took": took, "pid_gone": not pid or _gone(pid), "moves": _moves(),
                   "error": error, "stdout": out.read_text(encoding="utf-8"),
                   "stderr": err.read_text(encoding="utf-8")}


def _stores() -> None:
    paths.ensure_dirs()
    settings.ensure_exists()
    FAKE.mkdir()
    ENGINE.write_text(FAKE_ENGINE.format(python=sys.executable, tmp=str(FAKE / "record.tmp"),
                                         record=str(RECORD), mode=str(MODE), fatal=FATAL, later=LATER,
                                         marker=MARKER),
                      encoding="utf-8")
    ENGINE.chmod(0o755)
    settings.save({**settings.load(), "WALLPAPERS_DIR": str(LIB), "WORKSHOP_DIR": str(WORKSHOP),
                   "ENGINE_BIN": str(ENGINE), "ASSETS_DIR": str(ASSETS)})
    for wid, title in (("1500000001", "Alpha"), ("1500000003", "Charlie Base")):
        (LIB / wid).mkdir(parents=True)
        project = {"title": title, "type": "scene", "file": "scene.json"}
        (LIB / wid / "project.json").write_text(json.dumps(project), encoding="utf-8")
        (LIB / wid / "scene.json").write_text("{}", encoding="utf-8")
        tags.set_state(wid, title, "good")
    tags.set_state("1500000002", "Bravo Preset", "good")
    paths.wp_file("1500000002").write_text("BG=1500000003\n", encoding="utf-8")
    tags.set_state("1500000004", "Delta Gone", "good")
    tags.set_state("1500000005", "Echo", "bad")
    LOG.parent.rmdir()


def setUpModule() -> None:
    _stores()
    poison = _ROOT / "poison" / "PySide6"
    poison.mkdir(parents=True)
    (poison / "__init__.py").write_text('raise ImportError("PySide6 is unavailable in this test")\n',
                                        encoding="utf-8")
    for sub in ("rt", "cwd"):
        (_ROOT / sub).mkdir()
    BIN.mkdir()
    hyprctl = BIN / "hyprctl"
    hyprctl.write_text(FAKE_HYPRCTL.format(python=sys.executable, log=str(HYPR_LOG), record=str(RECORD)),
                       encoding="utf-8")
    hyprctl.chmod(0o755)
    bite = subprocess.run([sys.executable, "-c", "import PySide6"], capture_output=True, text=True,
                          env={"PYTHONPATH": str(_ROOT / "poison"), "PATH": str(BIN)}, timeout=60)
    if bite.returncode == 0 or "ImportError" not in bite.stderr:
        raise AssertionError(f"the PySide6 poison did not bite: {bite.returncode} {bite.stderr}")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(SOCK))
    listener.listen(64)
    listener.setblocking(False)
    try:
        _commands()
    finally:
        count = 0
        while True:
            try:
                conn, _ = listener.accept()
            except BlockingIOError:
                break
            conn.close()
            count += 1
        listener.close()
        _FACTS["connections"] = count


def _commands() -> None:
    _FACTS["log_folder_before"] = LOG.parent.exists()
    _run("ok", ["bench", "1500000001"])
    _FACTS["hyprctl_after_ok"] = HYPR_LOG.read_text(encoding="utf-8").splitlines() if HYPR_LOG.exists() else []
    _run("crash", ["bench", "1500000001"], "crash")
    _run("noframe", ["bench", "1500000001"], "noframe")
    _run("late", ["bench", "1500000001"], "late")
    _run("signal", ["bench", "1500000001"], "signal")
    _run("json", ["-j", "bench", "1500000001"])
    _run("preset", ["bench", "1500000002"])
    _run("x11", ["bench", "1500000001"], env=_env(display=False, DISPLAY=":99"))
    _run("no-display", ["bench", "1500000001"], env=_env(display=False))
    _run("missing", ["bench", "1500000004"])
    _run("trashed", ["bench", "1500000005"])
    for name, words in (("bare", ["bench"]), ("two", ["bench", "1500000001", "1500000003"]),
                        ("option", ["bench", "--now"])):
        _run(name, words)
    settings.save({**settings.load(), "ENGINE_BIN": "", "ASSETS_DIR": ""})
    _run("no-engine", ["bench", "1500000001"])
    settings.save({**settings.load(), "ENGINE_BIN": str(ENGINE)})
    _run("default-assets", ["bench", "1500000001"])
    _live("sigint", "sleep", signal.SIGINT, moves=5)
    _live("sigterm", "sleep", signal.SIGTERM)
    _live("sighup", "sleep", signal.SIGHUP)
    _live("stubborn", "stubborn", signal.SIGINT)


def _summary(stdout: str) -> list[str]:
    """The printed lines with the timings masked."""
    return [re.sub(r"after \d+\.\d s", "after <s> s", re.sub(r"^Ran \d+ s;", "Ran <s> s;", line))
            for line in stdout.splitlines()]


OPENED = "Opened a test window for Alpha (1500000001); close it to see the summary."


class LaunchTest(unittest.TestCase):
    def test_argv_has_the_window_no_socket_no_daemon_and_the_folder_last(self) -> None:
        argv = _RECORDS["ok"]["argv"]
        self.assertEqual(argv, [str(ENGINE), "--assets-dir", str(ASSETS), "--fps", "30", "--scaling", "default",
                                "--no-audio-processing", "--disable-mouse", "--no-fullscreen-pause", "--window",
                                "0x0x945x525", "--bg", str(LIB / "1500000001")])
        self.assertRegex(argv[argv.index("--window") + 1], r"^0x0x\d+x\d+$")
        self.assertNotIn("--api-socket", argv)
        self.assertNotIn("--daemon", argv)
        self.assertEqual(_FACTS["hyprctl_after_ok"][:2], ["-j monitors", "getoption general:gaps_out -j"])

    def test_the_environment_adds_the_present_trace_and_the_title(self) -> None:
        env = _RECORDS["ok"]["env"]
        self.assertEqual((env["LWE_PRESENTTRACE"], env["LWE_OVERLAY_TEXT"]), ("1", "Alpha"))
        self.assertEqual((env["WAYLAND_DISPLAY"], env["HOME"], env["PATH"]),
                         ("wayland-test", str(_ROOT), str(BIN)))

    def test_a_preset_benches_as_its_base(self) -> None:
        record = _RECORDS["preset"]
        self.assertEqual(_RUNS["preset"].returncode, 0, _RUNS["preset"].stderr)
        self.assertEqual(record["argv"][-2:], ["--bg", str(LIB / "1500000003")])
        self.assertEqual(record["env"]["LWE_OVERLAY_TEXT"], "Bravo Preset")

    def test_assets_come_from_the_settings_else_the_default(self) -> None:
        argv = _RECORDS["default-assets"]["argv"]
        self.assertEqual(argv[argv.index("--assets-dir") + 1], str(_ROOT / "d" / "lwe" / "assets"))

    def test_display_alone_is_a_graphical_session(self) -> None:
        self.assertEqual(_RUNS["x11"].returncode, 0, _RUNS["x11"].stderr)
        self.assertEqual(_RECORDS["x11"]["env"]["DISPLAY"], ":99")


class SummaryTest(unittest.TestCase):
    def test_a_frame_and_exit_0(self) -> None:
        r = _RUNS["ok"]
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(_summary(r.stdout), [OPENED, "First frame: after <s> s",
                                              "Ran <s> s; the window was closed (exit code 0)", f"Log: {LOG}"])

    def test_a_fatal_line_and_exit_134(self) -> None:
        r = _RUNS["crash"]
        self.assertEqual((r.returncode, r.stderr), (1, ""))
        self.assertEqual(_summary(r.stdout), [OPENED, "First frame: none", "Ran <s> s; exit code 134",
                                              f"Fatal line: {FATAL}", f"Log: {LOG}"])

    def test_exit_1_without_a_frame(self) -> None:
        r = _RUNS["noframe"]
        self.assertEqual((r.returncode, r.stderr), (1, ""))
        self.assertEqual(_summary(r.stdout), [OPENED, "First frame: none", "Ran <s> s; exit code 1",
                                              f"Log: {LOG}"])

    def test_a_frame_then_a_fatal_line(self) -> None:
        r = _RUNS["late"]
        self.assertEqual((r.returncode, r.stderr), (1, ""))
        self.assertEqual(_summary(r.stdout), [OPENED, "First frame: after <s> s",
                                              "Ran <s> s; the window was closed (exit code 0)",
                                              f"Fatal line: {FATAL}", f"Log: {LOG}"])

    def test_a_frame_then_a_signal(self) -> None:
        r = _RUNS["signal"]
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(_summary(r.stdout), [OPENED, "First frame: after <s> s", "Ran <s> s; signal 10",
                                              f"Log: {LOG}"])

    def test_json(self) -> None:
        r = _RUNS["json"]
        self.assertEqual(r.returncode, 0, r.stderr)
        result = json.loads(r.stdout)
        self.assertIsInstance(result.pop("first_frame_s"), float)
        self.assertIsInstance(result.pop("ran_s"), float)
        self.assertEqual(result, {"id": "1500000001", "title": "Alpha", "ended": "closed", "exit_code": 0,
                                  "signal": None, "fatal_line": None, "log": str(LOG)})

    def test_the_log_holds_the_header_and_the_engines_lines(self) -> None:
        self.assertFalse(_FACTS["log_folder_before"])
        header = "=== bench 1500000001 ===\n" + " ".join(_RECORDS["ok"]["argv"]) + "\n"
        self.assertEqual(_LOGS["ok"], header + f"fake engine ok\nfake engine stderr\n{MARKER}\n")
        self.assertEqual(_LOGS["crash"], header + f"fake engine crash\nfake engine stderr\n{FATAL}\n{LATER}\n")


class RefusalTest(unittest.TestCase):
    def assertRefused(self, name: str, message: str, code: int = 1) -> None:
        r = _RUNS[name]
        self.assertEqual((r.returncode, r.stdout, r.stderr), (code, "", f"lwe: {message}\n"))
        self.assertIsNone(_RECORDS[name], "an engine was launched")

    def test_no_graphical_session(self) -> None:
        self.assertRefused("no-display", "bench opens a window; no graphical session was found")

    def test_no_engine(self) -> None:
        self.assertRefused("no-engine", "the engine program was not found")

    def test_files_missing(self) -> None:
        self.assertRefused("missing", "Delta Gone (1500000004): its files are missing")

    def test_a_trashed_pick(self) -> None:
        self.assertRefused("trashed", "Echo (1500000005) is in the trash; untrash it first")

    def test_typed_wrong(self) -> None:
        for name in ("bare", "two", "option"):
            with self.subTest(run=name):
                self.assertRefused(name, "bench takes one wallpaper", 3)


class StopTest(unittest.TestCase):
    def assertStopped(self, name: str) -> dict:
        live = _LIVE[name]
        self.assertIsNone(live["error"], f"{live['error']}; stderr: {live['stderr']!r}")
        self.assertEqual(live["code"], 0, live["stderr"])
        self.assertTrue(live["pid_gone"], "the engine is still running")
        self.assertEqual(_summary(live["stdout"]), [OPENED, "First frame: after <s> s",
                                                    "Ran <s> s; stopped by lwe", f"Log: {LOG}"])
        return live

    def test_sigint_ends_the_engine_and_the_summary_prints(self) -> None:
        live = self.assertStopped("sigint")
        self.assertLess(live["took"], 3.0)
        self.assertEqual(len(live["moves"]), 5)
        self.assertTrue(all('x = 10, y = 10, window = "address:0xfake"' in m for m in live["moves"]))

    def test_sigterm_and_sighup_stop_it_too(self) -> None:
        for name in ("sigterm", "sighup"):
            with self.subTest(run=name):
                self.assertLess(self.assertStopped(name)["took"], 3.0)

    def test_an_engine_ignoring_sigterm_is_killed_after_3_s(self) -> None:
        live = self.assertStopped("stubborn")
        self.assertGreaterEqual(live["took"], 3.0)
        self.assertLess(live["took"], 10.0)


class EngineSocketTest(unittest.TestCase):
    def test_no_child_connects_to_the_engine_socket(self) -> None:
        self.assertEqual(_FACTS["connections"], 0)
        self.assertGreater(len(_RUNS) + len(_LIVE), 15)


if __name__ == "__main__":
    unittest.main(verbosity=2)
