"""The service switch under the test sandbox: no systemctl command runs.

With LWE_SANDBOX=1, Backend.setMaster and the re-arm step of Backend.showNow must not reach the
service manager, so a test run never enables or starts the engine unit of the machine it runs on.

Run: PYTHONPATH=src python3 tests/test_service_switch_sandbox.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HOME = tempfile.TemporaryDirectory(prefix="lwe-switch-")
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = os.path.join(_HOME.name, _sub) if _sub else _HOME.name
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QCoreApplication  # noqa: E402

_APP = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])

from lwe_ui import models  # noqa: E402


class ServiceSwitchSandboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[list[str]] = []
        recorder = mock.patch.object(subprocess, "run", self._record)
        recorder.start()
        self.addCleanup(recorder.stop)
        self.backend = models.Backend()

    def _record(self, args, *rest, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(args.split() if isinstance(args, str) else [str(a) for a in args])
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def _systemctl_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c and os.path.basename(c[0]) == "systemctl"]

    def test_switch_runs_no_systemctl(self) -> None:
        self.backend.setMaster(True)
        self.backend.setMaster(False)
        self.assertEqual(self._systemctl_calls(), [])

    def test_card_play_on_an_inactive_service_runs_no_systemctl(self) -> None:
        self.assertEqual(self.backend.masterState(), "inactive")
        self.backend.showNow("3134543499")
        self.assertEqual(self._systemctl_calls(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
