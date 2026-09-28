"""lwe service: the status read and the start, stop, restart and autostart forms, in this process
through cli.main, with every systemctl call, write_files' daemon-reload included, recorded by a fake
daemon_unit.RUNNER, enumerate_outputs, push.wait_ready and push.sync_all patched,
and HOME and the XDG folders at scratch (_cli_env). Nothing reaches systemctl or an engine.

Run: PYTHONPATH=src python3 tests/test_cli_service.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _cli_env

ROOT = Path(tempfile.mkdtemp(prefix="lwe-cli-service-"))
HOME = _cli_env.scratch_home(ROOT)
CONFIG = HOME / ".config" / "lwe"
UNIT = HOME / ".config" / "systemd" / "user" / "lwe-engine.service"
LOG = "journalctl --user -u lwe-engine.service"
RUNNING = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running", "UnitFileState": "enabled",
           "MainPID": "4242", "MemoryCurrent": str(412 * 1024 * 1024)}
STOPPED = {"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead", "UnitFileState": "disabled",
           "MainPID": "0", "MemoryCurrent": "[not set]"}
FAILED = {**STOPPED, "ActiveState": "failed", "SubState": "failed"}
MISSING = {**STOPPED, "LoadState": "not-found"}
ACTIVATING = {**RUNNING, "ActiveState": "activating", "SubState": "auto-restart", "MainPID": "0"}


class FakeSystemctl:
    def __init__(self, events: list, state: dict, fail: dict) -> None:
        self.events, self.state, self.fail = events, state, fail

    def __call__(self, args: list[str], timeout: float = 30.0) -> tuple[int, str, str]:
        self.events.append(("systemctl", *args))
        if args[0] in self.fail:
            return 1, "", self.fail[args[0]]
        if args[0] == "show":
            return 0, "".join(f"{key}={value}\n" for key, value in self.state.items()), ""
        return 0, "", ""


class ServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui import api_client, cli
        from lwe_ui.engine import daemon_unit, push
        from lwe_ui.cli import report
        cls.api, cls.cli, cls.unit, cls.push = api_client, cli, daemon_unit, push
        cls.chances = report.OPPORTUNITIES
        engine = ROOT / "bin" / "linux-wallpaperengine"
        engine.parent.mkdir(parents=True, exist_ok=True)
        engine.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        engine.chmod(0o755)
        cls.engine = engine

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(ROOT, True)

    def setUp(self) -> None:
        from lwe_ui.storage import paths
        (paths.panel_state_dir() / "sync-pending").unlink(missing_ok=True)
        self.events: list = []
        CONFIG.mkdir(parents=True, exist_ok=True)
        (CONFIG / "settings.conf").write_text(f"ENGINE_BIN={self.engine}\n", encoding="utf-8")
        for path in (CONFIG / "engine-env", UNIT):
            path.unlink(missing_ok=True)

    def run_lwe(self, words, state, outputs=("DP-1",), fail=None, ready=True, outcome=None, waiting=None):
        self.wait = mock.Mock(return_value={"pid": 4343} if ready else None)
        self.sync = mock.Mock(return_value=outcome or self.push.Outcome("applied"))
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(self.unit, "RUNNER", FakeSystemctl(self.events, state, fail or {})), \
                mock.patch.object(self.unit, "enumerate_outputs", return_value=list(outputs)), \
                mock.patch.object(self.unit, "restart_state",
                                  return_value=(True, waiting) if waiting else (False, {})), \
                mock.patch.object(self.api, "status", return_value=None), \
                mock.patch.object(self.push, "wait_ready", self.wait), \
                mock.patch.object(self.push, "sync_all", self.sync), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(["service", *words])
        return code, out.getvalue(), err.getvalue()

    def kinds(self):
        return [event[1] for event in self.events]

    def assert_no_now(self):
        self.assertFalse(any("--now" in event for event in self.events))

    def test_the_status_read(self) -> None:
        cases = (
            (RUNNING, 0, f"Running: yes (pid 4242)\nStarts at login: yes\n"
                         f"Memory: 412 MiB (the engine and its web helpers)\nLog: {LOG}\n"),
            (STOPPED, 2, f"Running: no\nStarts at login: no\nLog: {LOG}\n"),
            (FAILED, 2, f"Running: failed (failed)\nStarts at login: no\nLog: {LOG}\n"),
            (MISSING, 2, "The service is not set up; lwe service start sets it up.\n"),
        )
        for state, code, text in cases:
            with self.subTest(state=state["ActiveState"], load=state["LoadState"]):
                self.events.clear()
                self.assertEqual(self.run_lwe([], state), (code, text, ""))
                self.assertEqual(self.events, [("systemctl", "show", "lwe-engine.service", "-p", "LoadState", "-p",
                                                "ActiveState", "-p", "SubState", "-p", "UnitFileState", "-p",
                                                "MainPID", "-p", "MemoryCurrent")])
        code, out, err = self.run_lwe(["-j"], RUNNING)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"running": True, "state": "active", "pid": 4242, "autostart": True,
                                           "memory_bytes": 412 * 1024 * 1024, "waiting": [], "set_up": True})

    def test_start_rebuilds_then_starts_then_syncs_once(self) -> None:
        self.assertEqual(self.run_lwe(["start"], STOPPED), (0, "Started the service. Autostart is unchanged (off).\n", ""))
        self.assertEqual(self.kinds(), ["show", "daemon-reload", "start"])
        self.assertEqual(self.events[-1], ("systemctl", "start", "lwe-engine.service"))
        self.assertTrue((CONFIG / "engine-env").is_file() and UNIT.is_file())
        self.wait.assert_called_once_with(old_pid=None, timeout_s=20)
        self.sync.assert_called_once_with("command")
        self.assert_no_now()

    def test_start_of_a_running_service_rebuilds_but_neither_starts_nor_syncs(self) -> None:
        self.assertEqual(self.run_lwe(["start"], RUNNING),
                         (0, "The service is already running. Autostart is unchanged (on).\n", ""))
        self.assertEqual(self.kinds(), ["show", "daemon-reload"])
        self.sync.assert_not_called()

    def test_stop(self) -> None:
        self.assertEqual(self.run_lwe(["stop"], RUNNING), (0, "Stopped the service. Autostart is unchanged (on).\n", ""))
        self.assertEqual(self.kinds(), ["show", "stop"])
        self.sync.assert_not_called()
        self.events.clear()
        self.assertEqual(self.run_lwe(["stop"], STOPPED), (0, "The service was not running.\n", ""))
        self.assertEqual(self.kinds(), ["show"])

    def test_restart_rebuilds_waits_for_a_new_pid_and_syncs_once(self) -> None:
        self.assertEqual(self.run_lwe(["restart"], RUNNING),
                         (0, "Restarted the service; the settings marked restart now apply.\n", ""))
        self.assertEqual(self.kinds(), ["show", "daemon-reload", "restart"])
        self.wait.assert_called_once_with(old_pid=4242, timeout_s=20)
        self.sync.assert_called_once_with("command")
        self.events.clear()
        self.assertEqual(self.run_lwe(["restart"], STOPPED), (0, "Started the service.\n", ""))
        self.assert_no_now()

    def test_autostart_enables_or_disables_without_now(self) -> None:
        self.assertEqual(self.run_lwe(["autostart", "on"], STOPPED),
                         (0, "The service will start when you log in. It is stopped now.\n", ""))
        self.assertEqual(self.events[-1], ("systemctl", "enable", "lwe-engine.service"))
        self.events.clear()
        self.assertEqual(self.run_lwe(["autostart", "off"], RUNNING),
                         (0, "The service will not start when you log in. It is running now.\n", ""))
        self.assertEqual(self.events[-1], ("systemctl", "disable", "lwe-engine.service"))
        self.events.clear()
        self.assertEqual(self.run_lwe(["autostart", "off"], MISSING),
                         (0, "The service is not set up, so there is nothing to change.\n", ""))
        self.assertEqual(self.kinds(), ["show"])
        self.events.clear()
        self.run_lwe(["autostart", "on"], MISSING)
        self.assertEqual(self.kinds(), ["show", "daemon-reload", "enable"])
        self.sync.assert_not_called()
        self.assert_no_now()

    def test_a_wrong_word_exits_3_before_any_systemctl_call(self) -> None:
        for words in (["begin"], ["autostart"], ["autostart", "maybe"], ["stop", "now"]):
            with self.subTest(words=words):
                code, out, err = self.run_lwe(words, RUNNING)
                self.assertEqual((code, out), (3, ""))
                self.assertEqual(err, f"lwe service: {' '.join(words)} is not a service command; "
                                      "lwe help service lists them\n")
        self.assertEqual(self.events, [])

    def test_no_screens(self) -> None:
        code, out, err = self.run_lwe(["start"], STOPPED, outputs=())
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, "No screens were found and there is no engine-env yet, so the service cannot start; "
                              "run lwe service start from your desktop session.\n")
        self.assertEqual(self.kinds(), ["show"])
        (CONFIG / "engine-env").write_text("LWE_ENGINE_ARGS=--screen-root DP-1\n", encoding="utf-8")
        self.events.clear()
        self.assertEqual(self.run_lwe(["start"], STOPPED, outputs=()),
                         (0, "Started the service. Autostart is unchanged (off).\n"
                             "No screens were found; engine-env keeps the screens it already names.\n", ""))
        self.assertEqual(self.kinds(), ["show", "daemon-reload", "start"])

    def test_failures(self) -> None:
        code, out, err = self.run_lwe(["start"], STOPPED, fail={"start": "Failed to start lwe-engine.service.\nmore"})
        self.assertEqual((code, out, err), (1, "", "Failed to start lwe-engine.service.\n"))
        self.sync.assert_not_called()
        code, out, err = self.run_lwe(["start"], STOPPED, ready=False)
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, f"The service started, but the engine did not answer within 20 s; {LOG} shows why. "
                              f"The engine has not taken your saved configuration yet. {self.chances}\n")
        pending = self.push.Outcome("pending", reason="away")
        self.assertEqual(self.run_lwe(["start"], STOPPED, outcome=pending),
                         (0, "Started the service. Autostart is unchanged (off).\n"
                             f"The engine has not taken your saved configuration yet (away). {self.chances}\n", ""))
        refused = self.push.Outcome("refused", message="unknown playlist")
        self.assertEqual(self.run_lwe(["restart"], RUNNING, outcome=refused)[0], 1)

    def test_stop_stops_an_activating_service(self) -> None:
        self.assertEqual(self.run_lwe(["stop"], ACTIVATING), (0, "Stopped the service. Autostart is unchanged (on).\n", ""))
        self.assertEqual(self.kinds(), ["show", "stop"])

    def test_no_answer_records_bundle_in_the_marker(self) -> None:
        from lwe_ui.engine import marker
        self.run_lwe(["start"], STOPPED, ready=False)
        self.assertIn("BUNDLE", marker.read()["classes"])
        self.sync.assert_not_called()

    def test_a_version_refusal_after_a_start_prints_the_refusal_and_exits_1(self) -> None:
        refusal = "The running engine is 1.1.0 but 1.2.0 is installed; run lwe service restart."
        version = self.push.Outcome("pending", reason="version", message=refusal)
        for words, state in ((["start"], STOPPED), (["restart"], RUNNING)):
            with self.subTest(words=words):
                self.assertEqual(self.run_lwe(words, state, outcome=version), (1, "", refusal + "\n"))

    def test_the_waiting_line_names_the_settings_that_wait_for_a_restart(self) -> None:
        code, out, err = self.run_lwe([], RUNNING, waiting={"ENGINE_TEXCOMP": True, "ENGINE_LAYER": False})
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Waiting for lwe service restart: texturecache\n", out)
        self.assertNotIn("layer", out)
        code, out, err = self.run_lwe(["-j"], RUNNING, waiting={"ENGINE_TEXCOMP": True, "ENGINE_LAYER": False})
        self.assertEqual((code, err, json.loads(out)["waiting"]), (0, "", ["texturecache"]))

    def test_an_interrupted_wait_leaves_the_owed_bundle(self) -> None:
        from lwe_ui.cli.verbs import service
        from lwe_ui.engine import marker
        from lwe_ui.storage import paths
        for action in ("start", "restart"):
            with self.subTest(action=action):
                (paths.panel_state_dir() / "sync-pending").unlink(missing_ok=True)
                calls = []

                def runner(args, **kw):
                    calls.append(args)
                    return 0, "", ""

                with mock.patch.object(self.unit, "RUNNER", runner), \
                        mock.patch.object(self.push, "wait_ready", side_effect=KeyboardInterrupt), \
                        mock.patch.object(self.push, "sync_all") as sync:
                    with self.assertRaises(KeyboardInterrupt):
                        service._launch([action, "lwe-engine.service"], None)
                self.assertEqual((calls, sync.call_count), ([[action, "lwe-engine.service"]], 0))
                self.assertIn("BUNDLE", marker.read()["classes"])

    def test_a_marker_failure_exits_1_and_launches_nothing(self) -> None:
        from lwe_ui.engine import marker
        for words, state in ((["start"], STOPPED), (["restart"], RUNNING)):
            with self.subTest(words=words):
                self.events.clear()
                with mock.patch.object(marker, "ensure", side_effect=OSError("disk full")):
                    code, out, err = self.run_lwe(words, state)
                self.assertEqual((code, out, err),
                                 (1, "", "The sync record could not be written (disk full), so nothing was started.\n"))
                self.assertNotIn(words[0], self.kinds())

    def test_a_busy_marker_prints_the_store_busy_line_and_launches_nothing(self) -> None:
        from lwe_ui.engine import marker
        from lwe_ui.storage import lock
        for words, state in ((["start"], STOPPED), (["restart"], RUNNING)):
            with self.subTest(words=words):
                self.events.clear()
                busy = lock.StoreBusy("Store busy: another writer holds marker.lock")
                with mock.patch.object(marker, "ensure", side_effect=busy):
                    self.assertEqual(self.run_lwe(words, state),
                                     (1, "", "Store busy: another writer holds marker.lock\n"))
                self.assertNotIn(words[0], self.kinds())

    def test_a_timeout_keeps_the_record_and_sends_nothing(self) -> None:
        from lwe_ui.engine import marker
        from lwe_ui.storage import paths
        for words, state in ((["start"], STOPPED), (["restart"], RUNNING)):
            for pending in ((), ("CURRENT",)):
                with self.subTest(words=words, pending=pending):
                    (paths.panel_state_dir() / "sync-pending").unlink(missing_ok=True)
                    if pending:
                        marker.ensure(pending)
                    self.assertEqual(self.run_lwe(words, state, ready=False)[0], 1)
                    self.assertEqual(set(marker.read()["classes"]), {"BUNDLE", *pending})
                    self.sync.assert_not_called()

    def test_the_lines_that_do_not_apply_name_the_receipt_opportunities(self) -> None:
        from lwe_ui.cli.verbs import service
        err = self.run_lwe(["start"], STOPPED, ready=False)[2]
        self.assertTrue(err.endswith(f" {self.chances}\n"))
        for kind, reason in (("pending", "busy"), ("uncertain", None)):
            with self.subTest(kind=kind):
                out = self.run_lwe(["restart"], RUNNING, outcome=self.push.Outcome(kind, reason=reason))[1]
                self.assertTrue(out.endswith(f" {self.chances}\n"), out)
                self.assertNotIn("lwe reload sends them", out)
        self.assertNotIn("next chance", err)
        self.assertNotIn(self.chances[:20], Path(service.__file__).read_text(encoding="utf-8"))

    def test_an_engine_bin_named_lwe_is_skipped(self) -> None:
        lwe = ROOT / "bin" / "lwe"
        lwe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        lwe.chmod(0o755)
        (CONFIG / "settings.conf").write_text(f"ENGINE_BIN={lwe}\n", encoding="utf-8")
        self.assertNotEqual(self.unit.resolve_engine_bin(), str(lwe))
        (CONFIG / "settings.conf").write_text(f"ENGINE_BIN={self.engine}\n", encoding="utf-8")
        self.assertEqual(self.unit.resolve_engine_bin(), str(self.engine))

    def test_the_default_runner_runs_nothing_in_the_sandbox(self) -> None:
        self.assertIs(self.unit.RUNNER, self.unit._systemctl)
        calls = []
        with mock.patch.object(self.unit.subprocess, "run", lambda *a, **k: calls.append(a)):
            self.assertEqual(self.unit._systemctl(["start", "lwe-engine.service"]),
                             (1, "", "systemctl is not run in the test sandbox"))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
