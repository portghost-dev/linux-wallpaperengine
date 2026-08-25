"""Self-verification for the autostart desktop entry (Backend.autostart_content /
setAutostart / reconcileAutostart in src/lwe_ui/models.py).

Headless + isolated, same discipline as the other suites: XDG_CONFIG_HOME points at a
fresh tempdir per test so the live ~/.config/autostart is never touched. The Backend
methods are borrowed onto a bare carrier class so no Qt application, library scan or
bridge graph is constructed.

Contract under test (tray-resident buildout):
  * the entry body carries `--tray` (login launches land in the tray, not a window
    over the session) and a Desktop Entry header;
  * setAutostart(True) writes exactly that body and getAutostart flips true;
    setAutostart(False) removes the file;
  * reconcileAutostart rewrites a STALE entry (older build: bare Exec, no --tray) to
    the current body, and does NOT create an entry when none exists (absent = off).

Run: export PYTHONPATH=src && python3 tests/test_autostart.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from lwe_ui import models  # noqa: E402


class _Carrier:
    """The autostart methods without Backend.__init__ (no Qt, no library scan)."""
    _autostart_file = models.Backend._autostart_file
    autostart_content = staticmethod(models.Backend.autostart_content)
    getAutostart = models.Backend.getAutostart
    setAutostart = models.Backend.setAutostart
    reconcileAutostart = models.Backend.reconcileAutostart


class AutostartTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._tmp.name
        self.b = _Carrier()
        self.fp = os.path.join(self._tmp.name, "autostart", "lwe-ui.desktop")

    def tearDown(self) -> None:
        if self._old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old_xdg
        self._tmp.cleanup()

    def test_content_shape(self) -> None:
        body = self.b.autostart_content()
        self.assertIn("[Desktop Entry]", body)
        self.assertIn("--tray", body)
        self.assertIn("Exec=", body)

    def test_toggle_writes_and_removes(self) -> None:
        self.assertFalse(self.b.getAutostart())
        self.b.setAutostart(True)
        self.assertTrue(self.b.getAutostart())
        with open(self.fp, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), self.b.autostart_content())
        self.b.setAutostart(False)
        self.assertFalse(self.b.getAutostart())
        self.assertFalse(os.path.exists(self.fp))

    def test_reconcile_repairs_stale_entry(self) -> None:
        os.makedirs(os.path.dirname(self.fp), exist_ok=True)
        stale = ("[Desktop Entry]\nType=Application\nName=LWE Control Panel\n"
                 "Exec=lwe-ui\nX-GNOME-Autostart-enabled=true\n")
        with open(self.fp, "w", encoding="utf-8") as fh:
            fh.write(stale)
        self.b.reconcileAutostart()
        with open(self.fp, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), self.b.autostart_content())

    def test_reconcile_leaves_absent_absent(self) -> None:
        self.b.reconcileAutostart()
        self.assertFalse(os.path.exists(self.fp))


if __name__ == "__main__":
    unittest.main(verbosity=2)
