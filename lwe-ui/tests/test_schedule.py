"""The schedule executes in the engine: the panel resolves and pushes it, follows the engine's
bound playlist while it runs, and marks the user's own switch as manual so the engine holds it
until the next boundary. Day is the range from row 1 to row 2.

Headless: sandboxed HOME, api_client verbs replaced by recorders, the engine never reached.

Run: export PYTHONPATH=src && python3 tests/test_schedule.py
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class ScheduleBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtCore import QCoreApplication

        cls._app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="lwe-sched-test-")
        self._orig_env = {k: os.environ.get(k) for k in
                          ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
        os.environ["HOME"] = self.tmp
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp, ".config")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.tmp, ".local/state")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp, ".local/share")
        import lwe_ui.constants  # noqa: F401
        for name in ("atomic", "tier_a", "paths", "settings", "playlists", "wp", "meta"):
            importlib.reload(importlib.import_module(f"lwe_ui.storage.{name}"))
        self.api = importlib.reload(importlib.import_module("lwe_ui.api_client"))
        self.models = importlib.reload(importlib.import_module("lwe_ui.models"))
        from lwe_ui.storage import paths, playlists, settings

        paths.ensure_dirs()
        settings.ensure_exists()
        self.paths, self.playlists, self.settings = paths, playlists, settings
        for slug, name in (("day", "Day"), ("night", "Night"), ("party", "Party")):
            playlists.create(name)
        self.calls: list[tuple] = []
        from lwe_ui import version
        self.status = {"version": version.panel_stamp(), "pid": 7, "current": {"id": "", "ui_id": ""},
                       "schedule": {"enabled": False}}
        self.api.available = lambda: True
        self.api.status = lambda: dict(self.status)
        self.api.playlist_set = lambda slug, entries, order, interval, **kw: (
            self.calls.append(("playlist-set", slug, kw.get("part", 1), kw.get("of", 1))) or {"ok": True, "status": "done"})
        self.api.schedule_set = lambda enabled, entries: (
            self.calls.append(("schedule-set", bool(enabled), list(entries))) or {"ok": True, "status": "done"})
        self.api.lanes_set = lambda lanes: (self.calls.append(("lanes-set", list(lanes))) or {"ok": True, "status": "done"})
        for verb in ("set_fps", "set_parallax", "set_particles", "set_fullscreen_ignore", "set_app_conditions",
                     "set_fullscreen", "set_speed", "set_volume", "set_mouse", "set_audio", "set_tuning",
                     "set_fit", "set_skip"):
            setattr(self.api, verb, lambda *a, **kw: {"ok": True, "status": "done"})
        self.backend = self.models.Backend()

    def tearDown(self) -> None:
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _slug(self, name: str) -> str:
        for p in self.backend.playlistList():
            if p["name"] == name:
                return p["slug"]
        raise AssertionError(name)

    def _set_schedule(self, enabled: bool, packed: str) -> None:
        s = self.settings.load()
        s["SCHEDULE_ENABLED"] = enabled
        s["SCHEDULE"] = packed
        self.settings.save(s)

    def test_push_order_playlists_then_schedule_then_lane(self) -> None:
        day, night = self._slug("Day"), self._slug("Night")
        self.playlists.set_active(day)
        self._set_schedule(True, f"08:00={day};20:00={night}")
        self.calls.clear()
        self.backend._sync_engine()
        kinds = [c[0] for c in self.calls]
        self.assertEqual(kinds, ["playlist-set", "playlist-set", "schedule-set", "lanes-set"], kinds)
        self.assertEqual(self.calls[0][1], day, "the active playlist first")
        self.assertEqual(self.calls[1][1], night, "then the other scheduled playlist")
        self.assertEqual(self.calls[2][1:], (True, [{"at": "08:00", "playlist": day},
                                                    {"at": "20:00", "playlist": night}]))
        self.assertNotIn("manual", self.calls[3][1][0], "a policy push is not a manual switch")

    def test_user_switch_is_manual_and_a_missing_playlist_is_dropped(self) -> None:
        day, night, party = self._slug("Day"), self._slug("Night"), self._slug("Party")
        self._set_schedule(True, f"08:00={day};20:00=ghost;21:00={night}")
        self.status["schedule"] = {"enabled": True}
        self.calls.clear()
        self.backend.setActivePlaylist(party)
        lanes = [c for c in self.calls if c[0] == "lanes-set"]
        self.assertEqual(len(lanes), 1)
        self.assertEqual(lanes[0][1][0].get("manual"), True, "the user's own switch is manual")
        self.assertEqual(lanes[0][1][0]["playlist"], party)
        self.calls.clear()
        self.backend._sync_engine()
        sched = [c for c in self.calls if c[0] == "schedule-set"][0]
        self.assertEqual([e["playlist"] for e in sched[2]], [day, night], "an entry naming a missing playlist is dropped")
        self.assertFalse(hasattr(self.paths, "manual_hold_file"), "the dead hold marker is gone")
        # the other doors that change the active playlist are the user's own switches too
        self.calls.clear()
        self.backend.createPlaylist("Fresh")
        self.assertEqual([c for c in self.calls if c[0] == "lanes-set"][-1][1][0].get("manual"), True)
        self.calls.clear()
        self.backend.deleteActivePlaylist()
        self.assertEqual([c for c in self.calls if c[0] == "lanes-set"][-1][1][0].get("manual"), True)

    def test_deleting_a_scheduled_playlist_drops_it_and_switches_the_schedule_off(self) -> None:
        day, night = self._slug("Day"), self._slug("Night")
        self._set_schedule(True, f"08:00={day};20:00={night}")
        self.playlists.set_active(night)
        self.backend.deleteActivePlaylist()
        s = self.settings.load()
        self.assertEqual(s["SCHEDULE"], f"08:00={day}")
        self.assertFalse(s["SCHEDULE_ENABLED"])
        self.assertFalse(self.backend.scheduleState()["enabled"])
        # deleting a playlist outside the schedule leaves it alone
        self._set_schedule(True, f"08:00={day};20:00={self._slug('Party')}")
        self.playlists.create("Spare")
        self.playlists.set_active(self._slug("Spare"))
        self.backend.deleteActivePlaylist()
        self.assertTrue(self.settings.load()["SCHEDULE_ENABLED"])

    def test_a_policy_change_made_while_the_engine_was_away_is_delivered_on_its_return(self) -> None:
        from lwe_ui.engine import marker
        day = self._slug("Day")
        self.playlists.set_active(day)
        self.api.status = lambda: None
        self.backend.setPaused(True)   # the click lands while the engine is restarting
        self.assertEqual([c for c in self.calls if c[0] == "lanes-set"], [], "nothing could be sent")
        self.assertEqual(marker.read()["classes"], ["BUNDLE"], "the marker records the change")
        self.backend._engine_pid_seen = 7   # not first sight: a re-arrival
        self.api.status = lambda: {**self.status, "state": "up",
                                   "lanes": [{"id": "all", "playlist": day, "order": "sequential"}]}
        self.backend.status()
        lanes = [c for c in self.calls if c[0] == "lanes-set"]
        self.assertEqual(len(lanes), 1, "the poll's drain delivers the missed change once")
        self.assertFalse(lanes[0][1][0]["enabled"], "the pause reached the engine")
        self.backend.status()
        self.assertEqual(len([c for c in self.calls if c[0] == "lanes-set"]), 1, "and only once")

    def test_a_row_delete_removes_a_playlist_that_is_not_active(self) -> None:
        day, night, party = self._slug("Day"), self._slug("Night"), self._slug("Party")
        self.playlists.set_active(day)
        self._set_schedule(True, f"08:00={day};20:00={night}")
        self.calls.clear()
        self.backend.deletePlaylist(night)
        self.assertEqual(self.playlists.active_slug(), day, "the active playlist stays")
        self.assertNotIn(night, [p["slug"] for p in self.backend.playlistList()])
        self.assertFalse(self.settings.load()["SCHEDULE_ENABLED"], "a scheduled playlist gone switches the schedule off")
        lanes = [c for c in self.calls if c[0] == "lanes-set"]
        self.assertFalse(any(c[1][0].get("manual") for c in lanes), "no manual switch: the active did not change")
        self.assertIn("schedule-set", [c[0] for c in self.calls], "the changed schedule is sent")
        self.backend.deletePlaylist(day)
        self.assertEqual(self.playlists.active_slug(), party, "deleting the active one reassigns")

    def test_a_refused_schedule_is_not_shown_as_on(self) -> None:
        day, night = self._slug("Day"), self._slug("Night")
        self._set_schedule(True, f"08:00={day};20:00={night}")
        self.api.schedule_set = lambda enabled, entries: {"ok": False, "error": "unknown command"}
        self.backend._sync_engine()
        self.assertFalse(self.backend.scheduleState()["enabled"], "an old engine took no schedule: the cell stays off")
        self.api.schedule_set = lambda enabled, entries: {"ok": True, "status": "done"}
        self.backend._sync_engine()
        self.assertTrue(self.backend.scheduleState()["enabled"])

    def test_disabled_schedule_is_still_pushed_as_disabled(self) -> None:
        day, night = self._slug("Day"), self._slug("Night")
        self._set_schedule(False, f"08:00={day};20:00={night}")
        self.calls.clear()
        self.backend._sync_engine()
        sched = [c for c in self.calls if c[0] == "schedule-set"][0]
        self.assertEqual(sched[1], False)
        # one entry cannot run a schedule: pushed disabled even if the switch is on
        self._set_schedule(True, f"08:00={day}")
        self.calls.clear()
        self.backend._sync_engine()
        self.assertEqual([c for c in self.calls if c[0] == "schedule-set"][0][1], False)

    def test_day_is_the_range_from_row_one_to_row_two(self) -> None:
        day, night = self._slug("Day"), self._slug("Night")
        b = self.backend
        self._set_schedule(False, f"08:00={day};20:00={night}")
        self.assertEqual(b._day_range(), (8 * 60, 20 * 60))
        self.assertTrue(b._is_day_at(12 * 60, 8 * 60, 20 * 60))
        self.assertFalse(b._is_day_at(3 * 60, 8 * 60, 20 * 60))
        self.assertFalse(b._is_day_at(20 * 60, 8 * 60, 20 * 60))
        # a night owl's day crosses midnight
        self._set_schedule(False, f"22:00={day};06:00={night}")
        self.assertEqual(b._day_range(), (22 * 60, 6 * 60))
        self.assertTrue(b._is_day_at(23 * 60, 22 * 60, 6 * 60))
        self.assertTrue(b._is_day_at(3 * 60, 22 * 60, 6 * 60))
        self.assertFalse(b._is_day_at(12 * 60, 22 * 60, 6 * 60))
        # nothing stored: the modal's defaults, and the cell still has a day and a night
        self._set_schedule(False, "")
        self.assertEqual(b._day_range(), (8 * 60, 20 * 60))
        st = b.scheduleState()
        self.assertEqual(set(st), {"enabled", "is_day", "held"})
        self.assertFalse(st["enabled"])

    def test_panel_follows_the_engine_while_the_schedule_runs(self) -> None:
        day, night = self._slug("Day"), self._slug("Night")
        self.playlists.set_active(day)
        self._set_schedule(True, f"08:00={day};20:00={night}")
        self.backend._engine_pid_seen = 4242  # not first sight: no policy push during the poll
        fake = {"pid": 4242, "state": "up", "current": {"id": "", "ui_id": ""},
                "lanes": [{"id": "all", "playlist": night, "previous": "", "order": "sequential",
                           "next": "", "back_enabled": True}],
                "schedule": {"enabled": True, "held": True, "pending": "", "active": night}}
        self.api.status = lambda: fake
        self.calls.clear()
        reloads: list = []
        self.backend._model.reload = lambda members: reloads.append(set(members))
        self.backend.status()
        self.assertEqual(self.playlists.active_slug(), night, "the engine's switch becomes the active playlist")
        self.assertEqual(len(reloads), 1, "the grid's membership marks follow the switch")
        self.assertTrue(self.backend.scheduleState()["held"])
        self.assertEqual([c for c in self.calls if c[0] == "lanes-set"], [], "following pushes nothing back")
        # with the schedule off the engine's binding is not adopted
        fake["schedule"]["enabled"] = False
        fake["lanes"][0]["playlist"] = day
        self.backend.status()
        self.assertEqual(self.playlists.active_slug(), night)


if __name__ == "__main__":
    unittest.main(verbosity=2)
