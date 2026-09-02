"""The deck popup shares wp_session.SESSION with the editor (src/lwe_ui/deck_popup.py).

Two contracts, both previously broken by a private snapshot on the popup bridge:
  * a mark set through the popup is visible to the shared session, and Revert restores the
    value the wallpaper had when it was seated;
  * a seat that could not read the conf is a hard state: it is reported when the wallpaper
    is synced, and Revert refuses rather than writing None over every marked key.

Headless + isolated: HOME and XDG_* point at a temp dir, the engine is never reached, and the
re-show timer never fires because no event loop runs.

Run: export PYTHONPATH=src && python3 tests/test_deck_popup_session.py
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class DeckPopupSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtCore import QCoreApplication

        cls._app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="lwe-popup-test-")
        self._orig_env = {k: os.environ.get(k) for k in
                          ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
        os.environ["HOME"] = self.tmp
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp, ".config")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.tmp, ".local/state")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp, ".local/share")

        import lwe_ui.constants  # noqa: F401
        for name in ("atomic", "tier_a", "paths", "settings", "wp", "meta"):
            importlib.reload(importlib.import_module(f"lwe_ui.storage.{name}"))
        self.session_mod = importlib.reload(importlib.import_module("lwe_ui.wp_session"))
        self.popup_mod = importlib.reload(importlib.import_module("lwe_ui.deck_popup"))

        from lwe_ui.storage import paths, settings, wp

        paths.ensure_dirs()
        settings.ensure_exists()
        self.wp = wp
        self.backend = types.SimpleNamespace(
            setSetting=lambda key, value: True,
            _sync_engine=lambda: None,
            showNow=lambda wid: True,
        )
        self.popup = self.popup_mod.DeckPopupBridge(self.backend)
        self.failures: list[list] = []
        self.popup.commitFailed.connect(lambda keys: self.failures.append(list(keys)))

    def tearDown(self) -> None:
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_marks_are_shared_and_revert_restores_seated_value(self) -> None:
        wid = "1000001"
        self.wp.update_set(wid, {"SCALING": "fill"})
        self.popup.syncCurrent(wid)
        self.assertEqual(self.failures, [])
        self.assertTrue(self.popup.snapshotValid())

        self.assertTrue(self.popup._write_wp({"SCALING": "fit"}))
        self.assertEqual(self.wp.load_set(wid).get("SCALING"), "fit")
        # the mark lives in the shared session, not in a popup-private set
        self.assertTrue(self.session_mod.SESSION.is_marked(wid, "SCALING"))
        self.assertTrue(self.popup.canRevert())

        self.assertTrue(self.popup.revertChanges())
        self.assertEqual(self.wp.load_set(wid).get("SCALING"), "fill")
        self.assertFalse(self.popup.hasMarks())

    def test_seat_failure_is_reported_and_revert_refuses(self) -> None:
        wid = "1000002"
        writes: list[dict] = []
        real_load_set = self.wp.load_set

        def broken_load_set(w):
            raise OSError("unreadable conf")

        self.wp.load_set = broken_load_set
        self.session_mod.wp.load_set = broken_load_set
        self.popup_mod.wp.update_set = lambda w, changes: writes.append(dict(changes))
        try:
            self.popup.syncCurrent(wid)
            self.assertIn(["SNAPSHOT"], self.failures)
            self.assertFalse(self.popup.snapshotValid())

            # a mark with no valid snapshot must never turn into a delete-everything write
            self.session_mod.SESSION.mark(wid, ["SCALING", "PROP_speed"])
            self.assertTrue(self.popup.hasMarks())
            self.assertFalse(self.popup.canRevert())
            self.assertFalse(self.popup.revertChanges())
            self.assertEqual(writes, [])
            self.assertEqual(self.failures.count(["SNAPSHOT"]), 2)
        finally:
            self.wp.load_set = real_load_set
            self.session_mod.wp.load_set = real_load_set


if __name__ == "__main__":
    unittest.main(verbosity=2)
