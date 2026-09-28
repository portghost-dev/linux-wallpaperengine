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

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import importlib
import os
import shutil
import sys
import tempfile
import types
import unittest

from PySide6.QtTest import QTest
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
            hold_delivery=lambda owner, due: None,
            delivery_due=lambda: False,
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

    def test_fit_rows_round_trip_clamp_and_revert(self) -> None:
        wid = "1000003"
        self.wp.update_set(wid, {"SCALING": "fill"})
        self.popup.syncCurrent(wid)
        self.assertEqual(self.popup.fitValue("zoom"), "")

        self.assertTrue(self.popup.setFit("zoom", "1.5"))
        self.assertTrue(self.popup.setFit("pan_x", "-3"))
        self.assertEqual(self.wp.load_set(wid).get("FIT_ZOOM"), 1.5)
        self.assertEqual(self.wp.load_set(wid).get("FIT_PAN_X"), -1.0)
        self.assertEqual(self.popup.fitValue("zoom"), "1.5")
        self.assertTrue(self.session_mod.SESSION.is_marked(wid, "FIT_ZOOM"))
        self.assertTrue(self.session_mod.SESSION.is_marked(wid, "FIT_PAN_X"))

        # a value that is not a number is the failure grammar, never a silent default
        self.assertFalse(self.popup.setFit("pan_y", "up"))
        self.assertIn(["FIT_PAN_Y"], self.failures)
        self.assertNotIn("FIT_PAN_Y", self.wp.load_set(wid))
        self.assertFalse(self.popup.setFit("nope", "1"))

        # the empty entry deletes the key: identity is absence, not a stored 1.0
        self.assertTrue(self.popup.setFit("zoom", ""))
        self.assertNotIn("FIT_ZOOM", self.wp.load_set(wid))
        self.assertEqual(self.popup.fitValue("zoom"), "")

        self.assertTrue(self.popup.revertChanges())
        self.assertNotIn("FIT_PAN_X", self.wp.load_set(wid))
        self.assertFalse(self.popup.hasMarks())

    def test_fit_writes_go_live_through_the_wallpaper_layer_not_a_reshow(self) -> None:
        from lwe_ui import version
        wid = "1000004"
        self.wp.update_set(wid, {"SCALING": "fill"})
        self.popup.syncCurrent(wid)
        pushes: list[dict] = []
        api = self.popup_mod.api_client
        for name in ("status", "set_fit", "show", "set_tuning"):
            self.addCleanup(setattr, api, name, getattr(api, name))
        api.status = lambda *a, **k: {"api": 1, "version": version.panel_stamp(), "pid": 1,
                                      "current": {"id": wid, "ui_id": wid}}
        api.set_fit = lambda **kw: (pushes.append(dict(kw)) or {"ok": True, "status": "done"})
        api.show = lambda *a, **k: {"ok": True, "status": "done"}
        api.set_tuning = lambda **kw: {"ok": True, "status": "done"}

        self.assertTrue(self.popup.setFit("zoom", "1.5"))
        self.assertEqual(pushes, [{"layer": "wallpaper", "id": wid, "zoom": 1.5, "pan_x": 0.0, "pan_y": 0.0}])
        # the re-show timer is not armed for a fit-only write; the present pass handles it
        self.assertFalse(self.popup._reshow.isActive())
        self.assertEqual(self.popup._pending, set())

        # a build-class key beside it queues the re-show, and the fit rides the burst's delivery
        self.assertTrue(self.popup._write_wp({"SCALING": "fit", "FIT_PAN_X": 0.25}))
        self.assertEqual(len(pushes), 1)
        self.assertTrue(self.popup._reshow.isActive())
        self.assertEqual(self.popup._pending, {"SCALING", "FIT_PAN_X"})
        self.popup._reshow.stop()
        self.popup._fire_reshow()
        self.assertEqual(pushes[-1], {"layer": "wallpaper", "id": wid, "zoom": 1.5, "pan_x": 0.25, "pan_y": 0.0})

        # a refused push is the failure grammar
        api.set_fit = lambda **kw: {"ok": False, "error": "no"}
        self.popup.setFit("pan_y", "0.5")
        self.assertIn(["FIT_PAN_Y"], self.failures)

    def test_fit_preview_goes_live_without_touching_the_store(self) -> None:
        wid = "1000005"
        self.wp.update_set(wid, {"FIT_ZOOM": 1.5})
        self.popup.syncCurrent(wid)
        pushes: list[dict] = []
        self.popup_mod.api_client.available = lambda: True
        self.popup_mod.api_client.set_fit = lambda **kw: (pushes.append(dict(kw)) or {"ok": True})

        # drag steps coalesce: three in one tick make one push carrying the last value
        self.popup.previewFit("pan_x", "0.100")
        self.popup.previewFit("pan_x", "0.200")
        self.popup.previewFit("pan_y", "-0.300")
        self.assertEqual(pushes, [])
        QTest.qWait(80)
        self.assertEqual(pushes, [{"layer": "wallpaper", "id": wid, "zoom": 1.5, "pan_x": 0.2, "pan_y": -0.3}])
        self.assertNotIn("FIT_PAN_X", self.wp.load_set(wid), "a preview never writes the store")
        self.assertFalse(self.popup.hasMarks())
        self.assertFalse(self.popup._reshow.isActive())

        # a refused preview is silent: the release's commit carries the failure grammar
        self.popup_mod.api_client.set_fit = lambda **kw: {"ok": False, "error": "no"}
        self.popup.previewFit("pan_x", "0.4")
        QTest.qWait(80)
        self.assertNotIn(["FIT_PAN_X"], self.failures)
        # nonsense and a bridge with no wallpaper push nothing
        self.popup_mod.api_client.set_fit = lambda **kw: (pushes.append(dict(kw)) or {"ok": True})
        self.popup.previewFit("pan_x", "wide")
        self.popup.syncCurrent("")
        self.popup.previewFit("pan_x", "0.5")
        QTest.qWait(80)
        self.assertEqual(len(pushes), 1)

    def test_speed_and_volume_preview_send_the_verb_and_persist_nothing(self) -> None:
        wid = "1000006"
        self.wp.update_set(wid, {"SPEED": 2.0})
        self.popup.syncCurrent(wid)
        from lwe_ui.storage import settings
        before = dict(settings.load())
        sent: list[tuple] = []
        self.popup_mod.api_client.available = lambda: True
        self.popup_mod.api_client.set_speed = lambda v: (sent.append(("speed", v)) or {"ok": True})
        self.popup_mod.api_client.set_volume = lambda v: (sent.append(("volume", v)) or {"ok": True})
        # the effective rate: this wallpaper sets SPEED 2.0, so the dragged global does not
        # reach the engine; the commit would send the same number
        self.popup.previewLive("speed", 1.25)
        self.popup.previewLive("speed", 1.5)
        self.popup.previewLive("volume", 40.0)
        self.assertEqual(sent, [])
        QTest.qWait(80)
        self.assertEqual(sent, [("speed", 2.0), ("volume", 40)])
        self.assertEqual(dict(settings.load()), before, "a preview persists nothing")
        self.assertEqual(self.failures, [])
        self.popup.previewLive("fps", 60.0)
        QTest.qWait(80)
        self.assertEqual(len(sent), 2, "only Speed and Volume preview")


if __name__ == "__main__":
    unittest.main(verbosity=2)
