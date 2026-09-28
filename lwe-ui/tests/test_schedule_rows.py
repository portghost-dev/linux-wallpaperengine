"""The schedule line without Qt (storage/schedule.py): rows keep their place and their empty
playlists, a field set on nothing or on one row starts from the 08:00 and 20:00 defaults, a third row
is never dropped, day or night follows the panel's rule, schedule on is refused for each named
reason, and reload's three schedule rules hold.

Run: PYTHONPATH=src python3 tests/test_schedule_rows.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import os
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

_BOOT = tempfile.TemporaryDirectory(prefix="lwe-schedrows-boot-")
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = os.path.join(_BOOT.name, _sub) if _sub else _BOOT.name

from lwe_ui.storage import paths, schedule  # noqa: E402


class ScheduleRowsTest(unittest.TestCase):
    def setUp(self) -> None:
        home = tempfile.TemporaryDirectory(prefix="lwe-schedrows-")
        self.addCleanup(home.cleanup)
        for key, sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"),
                         ("XDG_DATA_HOME", "d")):
            os.environ[key] = os.path.join(home.name, sub) if sub else home.name
        paths.playlists_dir().mkdir(parents=True)
        for slug in ("ocean", "calm", "chill.v2"):
            paths.playlist_file(slug).write_text(f"NAME={slug}\n", encoding="utf-8")

    def test_a_draft_row_keeps_its_place_and_its_empty_playlist(self) -> None:
        rows = schedule.parse_rows("08:00=;20:00=calm")
        self.assertEqual(rows, [("08:00", ""), ("20:00", "calm")])
        self.assertEqual(schedule.format_rows(rows), "08:00=;20:00=calm")

    def test_a_field_set_on_nothing_starts_from_the_defaults(self) -> None:
        rows = schedule.set_field(schedule.parse_rows(""), "day", "playlist", "ocean")
        self.assertEqual(schedule.format_rows(rows), "08:00=ocean;20:00=")

    def test_one_row_is_day_and_night_takes_the_other_default(self) -> None:
        rows = schedule.set_field(schedule.parse_rows("20:00=b"), "night", "playlist", "calm")
        self.assertEqual(schedule.format_rows(rows), "20:00=b;08:00=calm")
        rows = schedule.set_field(schedule.parse_rows("07:30=b"), "night", "playlist", "calm")
        self.assertEqual(schedule.format_rows(rows), "07:30=b;20:00=calm")

    def test_a_third_row_is_never_dropped(self) -> None:
        text = "08:00=ocean;20:00=calm;22:00=ocean"
        rows = schedule.parse_rows(text)
        self.assertEqual(len(rows), 3)
        self.assertEqual(schedule.format_rows(rows), text)
        with self.assertRaises(ValueError):
            schedule.set_field(rows, "night", "time", 21 * 60)
        self.assertEqual(schedule.format_rows(rows), text)

    def test_is_day_at_matches_the_panel_rule_over_a_grid(self) -> None:
        from lwe_ui import models
        grid = sorted(set(range(0, 24 * 60, 20)) | {24 * 60 - 1})
        ours = [schedule.is_day_at(minute, a, b) for a in grid for b in grid for minute in grid]
        panel = [models.Backend._is_day_at(minute, a, b) for a in grid for b in grid for minute in grid]
        self.assertEqual(ours, panel)

    def test_check_on_names_each_reason(self) -> None:
        def on(text: str) -> list:
            return schedule.check_on(schedule.parse_rows(text))
        self.assertEqual(on("07:30=ocean;20:00=calm"), [])
        self.assertEqual(on("07:30=ocean"), [("rows", "")])
        self.assertEqual(on("07:30=ocean;20:00=calm;22:00=ocean"), [("rows", "")])
        self.assertEqual(on("07:30=ocean;20:00"), [("rows", "")])
        self.assertEqual(on("7:30=ocean;20:00=calm"), [("time", "day")])
        self.assertEqual(on("07:30=ocean;24:00=calm"), [("time", "night")])
        self.assertEqual(on("20:00=ocean;20:00=calm"), [("same time", "")])
        self.assertEqual(on("07:30=;20:00=calm"), [("unset", "day")])
        self.assertEqual(on("07:30=chill.v2;20:00=calm"), [("name", "day")])
        self.assertEqual(on("07:30=ocean;20:00=ghost"), [("missing", "night")])
        self.assertEqual(on("07:30=calm;20:00=calm"), [("same playlist", "")])
        self.assertEqual(on("07:30=;7:00=ghost"), [("unset", "day"), ("time", "night"), ("missing", "night")])

    def test_check_reload_applies_the_three_rules(self) -> None:
        def line(text: str) -> str:
            return f"SCHEDULE={text}"
        self.assertEqual(schedule.check_reload("", False), [], "the defaults, off, are valid")
        self.assertEqual(schedule.check_reload("", True), [("rows", "", line(""))])
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.assertEqual(schedule.check_reload("07:30=ocean", enabled),
                                 [("rows", "", line("07:30=ocean"))])
                self.assertEqual(schedule.check_reload("07:30=ocean;7:00=calm", enabled),
                                 [("time", "night", line("07:30=ocean;7:00=calm"))])
                self.assertEqual(schedule.check_reload("20:00=ocean;20:00=calm", enabled),
                                 [("same time", "", line("20:00=ocean;20:00=calm"))])
                self.assertEqual(schedule.check_reload("07:30=ocean;20:00=calm", enabled), [])
        self.assertEqual(schedule.check_reload("07:30=ghost;20:00=", False), [],
                         "playlists need to exist only while the schedule is on")
        self.assertEqual(schedule.check_reload("07:30=ghost;20:00=", True),
                         [("missing", "day", line("07:30=ghost;20:00=")),
                          ("unset", "night", line("07:30=ghost;20:00="))])


if __name__ == "__main__":
    unittest.main(verbosity=2)
