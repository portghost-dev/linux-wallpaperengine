"""The harness pin: no test file may reach the engine socket or the live daemon on this machine.

Three groups. The static one parses every test file and requires the first module-level
statement after the docstring and any __future__ imports to be `import _sandbox`, so the pin
is in place before any client code can run. The socket one confirms that, in this process,
the client resolves its socket under the sandbox directory and that nothing listens there.
The host one confirms models' host-wide probes (the /proc engine scan, pgrep, systemd,
nvidia-smi) find nothing under LWE_SANDBOX, and that the scan itself still works without it.

Run: PYTHONPATH=src python3 tests/test_harness_pin.py
"""
import _sandbox  # noqa: F401  (must stay the first project import)
import ast
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lwe_ui import api_client  # noqa: E402
from lwe_ui import models  # noqa: E402

TESTS = Path(__file__).resolve().parent


def _pin_position(tree: ast.Module) -> str:
    """"" when the pin is the first real statement, else a one-line reason."""
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    while body and isinstance(body[0], ast.ImportFrom) and body[0].module == "__future__":
        body = body[1:]
    if not body:
        return "no statements"
    first = body[0]
    if isinstance(first, ast.Import) and any(alias.name == "_sandbox" for alias in first.names):
        return ""
    return f"first statement is {type(first).__name__} at line {first.lineno}, not `import _sandbox`"


class HarnessPin(unittest.TestCase):
    def test_every_test_file_pins_before_anything_else(self) -> None:
        offenders = []
        for path in sorted(TESTS.glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            reason = _pin_position(tree)
            if reason:
                offenders.append(f"{path.name}: {reason}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_client_resolves_to_the_sandbox_socket(self) -> None:
        path = api_client.socket_path()
        self.assertEqual(str(path), _sandbox.SOCKET)
        self.assertTrue(str(path).startswith(_sandbox._DIR))
        self.assertFalse(api_client.available(), "nothing may listen on the sandbox socket")

    def test_runtime_dir_is_private(self) -> None:
        self.assertEqual(os.environ.get("XDG_RUNTIME_DIR"), _sandbox.RUNTIME_DIR)
        self.assertNotEqual(os.environ.get("XDG_RUNTIME_DIR"), f"/run/user/{os.getuid()}")


class HostProbes(unittest.TestCase):
    def test_the_flag_is_set_by_the_pin(self) -> None:
        self.assertEqual(os.environ.get("LWE_SANDBOX"), "1")

    def test_unit_probe_reports_inactive(self) -> None:
        # the gate answers before self is touched, so no Backend needs constructing
        self.assertEqual(models.Backend.masterState(None), "inactive")

    def test_proc_scan_finds_no_engine(self) -> None:
        self.assertIsNone(models._find_engine_pid(), "the /proc scan reached the live daemon")

    def test_the_scan_itself_still_works_without_the_flag(self) -> None:
        comm = Path("/proc/self/comm").read_text(encoding="utf-8").strip()
        env = {k: v for k, v in os.environ.items() if k != "LWE_SANDBOX"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(models, "_ENGINE_COMM", comm):
            self.assertIsNotNone(models._find_engine_pid(),
                                 "the production scan must still read /proc")

    def test_family_and_machine_probes_report_nothing(self) -> None:
        stub = types.SimpleNamespace()
        self.assertEqual(models.Backend._engine_pids(stub), [])
        self.assertEqual(models.Backend._gpu_sample(stub), (-1, -1))
        self.assertEqual(models.Backend._mem_high_mb(stub), -1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
