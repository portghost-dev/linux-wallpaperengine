"""The schedule command: bare schedule, schedule on|off and the day or night time and playlist edits.

Each form runs through cli.main in this process with HOME and the XDG folders at scratch
(_cli_env.scratch_home), daemon_unit's subprocess call replaced by a recorder and, where a case
needs one, a fake engine on the socket _sandbox pins; requests are counted apart from status reads.
One case runs bare schedule through the engine's handoff in a child whose environment is built from
nothing (_cli_env.scratch_env), PySide6 blocked. storage/schedule.py's set_field is checked for the
default of a missing row chosen after the edit, and backup export then import for a draft row.

Run: PYTHONPATH=src python3 tests/test_cli_schedule.py
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
import _fake_engine

ROOT = Path(tempfile.mkdtemp(prefix="lwe-cli-schedule-"))
HOME = _cli_env.scratch_home(ROOT / "inproc")
STAYS_OFF = "The schedule stays off. Nothing changed.\n"


def tearDownModule() -> None:
    shutil.rmtree(ROOT, True)


class ScheduleVerbTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui import cli, version
        from lwe_ui.cli.verbs import schedule as verb
        from lwe_ui.engine import daemon_unit
        from lwe_ui.storage import paths, settings
        cls.cli, cls.stamp, cls.verb, cls.daemon_unit = cli, version.panel_stamp(), verb, daemon_unit
        cls.paths, cls.settings = paths, settings
        cls.conf = paths.settings_file()

    def setUp(self) -> None:
        for folder in (self.paths.config_dir(), self.paths.state_dir()):
            shutil.rmtree(folder, True)
            folder.mkdir(parents=True)
        self.runs: list = []
        du = self.daemon_unit
        for patcher in (mock.patch.object(du.subprocess, "run", lambda argv, **kw: self.runs.append(argv)),
                        mock.patch.object(du, "enumerate_outputs", lambda: ["DP-1"]),
                        mock.patch.object(du, "live_engine_env", lambda: None),
                        mock.patch.object(self.verb, "_minute_now", lambda: 12 * 60)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.playlist("ocean", "Ocean")
        self.playlist("calm", "Calm")

    def tearDown(self) -> None:
        self.assertEqual(self.runs, [], "a subprocess ran")

    def playlist(self, slug: str, name: str) -> None:
        path = self.paths.playlist_file(slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"NAME={name}\n", encoding="utf-8")

    def engine(self, **fields) -> _fake_engine.FakeEngine:
        engine = _fake_engine.FakeEngine(_sandbox.SOCKET, **fields)
        self.addCleanup(engine.stop)
        return engine

    def lwe(self, *words: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(list(words), sender_stamp=self.stamp)
        return code, out.getvalue(), err.getvalue()

    def store(self, text: str) -> None:
        self.conf.write_text(text, encoding="utf-8")

    def snapshot(self) -> dict[str, bytes]:
        """Every file under the config and state folders but the lock sidecars."""
        out = {}
        for folder in (self.paths.config_dir(), self.paths.state_dir()):
            for path in sorted(folder.rglob("*")):
                if path.is_file() and self.paths.locks_dir() not in path.parents:
                    out[str(path)] = path.read_bytes()
        return out

    @staticmethod
    def requests(engine: _fake_engine.FakeEngine) -> list[tuple[str, dict]]:
        return [call for call in engine.calls if call[0] != "status"]

    def test_day_time_730_changes_only_its_time_and_sends_the_playlists_then_the_schedule(self) -> None:
        self.store('ENGINE_FPS=30\nSCHEDULE_ENABLED=true\nSCHEDULE="08:00=ocean;20:00=calm"\nENGINE_VOLUME=40\n')
        entries = [{"at": "08:00", "playlist": "ocean"}, {"at": "20:00", "playlist": "calm"}]
        engine = self.engine(schedule={"enabled": True, "entries": entries, "active": "ocean", "held": False,
                                       "pending": ""})
        self.assertEqual(self.lwe("schedule", "day", "time", "730"), (0, "Day starts at 07:30.\n", ""))
        self.assertEqual(self.conf.read_text(encoding="utf-8"),
                         'ENGINE_FPS=30\nSCHEDULE_ENABLED=true\nSCHEDULE="07:30=ocean;20:00=calm"\nENGINE_VOLUME=40\n')
        sent = self.requests(engine)
        self.assertEqual([(cmd, args.get("slug")) for cmd, args in sent],
                         [("playlist-set", "ocean"), ("playlist-set", "calm"), ("schedule-set", None)])
        self.assertEqual(sent[-1][1], {"enabled": True, "entries": [{"at": "07:30", "playlist": "ocean"},
                                                                    {"at": "20:00", "playlist": "calm"}]})
        self.assertNotIn("manual", json.dumps(sent))

    def test_equal_times_are_refused_on_and_off(self) -> None:
        engine = self.engine()
        for on in ("false", "true"):
            with self.subTest(enabled=on):
                self.store(f'SCHEDULE_ENABLED={on}\nSCHEDULE="08:00=ocean;20:00=calm"\n')
                before = self.snapshot()
                self.assertEqual(self.lwe("schedule", "night", "time", "8:00"),
                                 (1, "", "Day and night cannot both start at 08:00. Nothing changed.\n"))
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.requests(engine), [])

    def test_schedule_on_names_each_reason_and_writes_nothing(self) -> None:
        self.playlist("chill.v2", "Chill")
        cases = {
            "08:00=ocean;20:00=": "Night has no playlist yet (lwe schedule night playlist <playlist>).\n" + STAYS_OFF,
            "08:00=ocean;20:00=ocean": "Day and night both play Ocean (3).\n" + STAYS_OFF,
            "08:00=ocean;20:00=gone": "Night's playlist gone has no file.\n" + STAYS_OFF,
            "08:00=ocean;20:00=chill.v2": ("Night's playlist file chill.v2.conf has a name the engine refuses: use "
                                           "only letters, digits, - and _, up to 64.\n" + STAYS_OFF),
            "08:00=ocean;20:00=calm;22:00=ocean": ("This schedule has 3 entries; day and night editing needs two. "
                                                   "Nothing changed; fix the SCHEDULE line and run lwe reload.\n"),
        }
        engine = self.engine()
        for value, err in cases.items():
            with self.subTest(schedule=value):
                self.store(f'SCHEDULE_ENABLED=false\nSCHEDULE="{value}"\n')
                before = self.snapshot()
                self.assertEqual(self.lwe("schedule", "on"), (1, "", err))
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.requests(engine), [])

    def test_schedule_off_with_three_rows_changes_only_schedule_enabled(self) -> None:
        text = 'ENGINE_FPS=30\nSCHEDULE_ENABLED={}\nSCHEDULE="08:00=ocean;20:00=calm;22:00=ocean"\n'
        self.store(text.format("true"))
        engine = self.engine()
        self.assertEqual(self.lwe("schedule", "off"), (0, "Schedule off; day and night stay as you set them.\n", ""))
        self.assertEqual(self.conf.read_text(encoding="utf-8"), text.format("false"))
        self.assertNotIn("lanes-set", [cmd for cmd, _args in self.requests(engine)])

    def test_while_on_setting_night_to_days_playlist_is_refused(self) -> None:
        self.store('SCHEDULE_ENABLED=true\nSCHEDULE="08:00=ocean;20:00=calm"\n')
        before = self.snapshot()
        self.assertEqual(self.lwe("schedule", "night", "playlist", "Ocean"),
                         (1, "", "Turn the schedule off first. Nothing changed.\n"))
        self.assertEqual(self.snapshot(), before)
        self.store('SCHEDULE_ENABLED=false\nSCHEDULE="08:00=ocean;20:00=calm"\n')
        self.engine()
        self.assertEqual(self.lwe("schedule", "night", "playlist", "Ocean"), (0, "Night plays Ocean (2).\n", ""))
        self.assertEqual(self.settings.load()["SCHEDULE"], "08:00=ocean;20:00=ocean")

    def test_bare_schedule_prints_held_and_pending_and_the_saved_view_with_no_engine(self) -> None:
        self.store('SCHEDULE_ENABLED=true\nSCHEDULE="08:00=ocean;20:00=calm"\n')
        entries = [{"at": "08:00", "playlist": "ocean"}, {"at": "20:00", "playlist": "calm"}]
        engine = self.engine(schedule={"enabled": True, "entries": entries, "active": "calm", "is_day": False,
                                       "held": True, "pending": "ocean"})
        rows = "Day    08:00  Ocean (2)\nNight  20:00  Calm (1)\n"
        self.assertEqual(self.lwe("schedule"), (0, rows + "Schedule on. It is night now.\n"
                                                "A playlist picked by hand holds until the next start time.\n"
                                                "Pending: Ocean (2) plays from the next change.\n", ""))
        code, out, err = self.lwe("-j", "schedule")
        self.assertEqual((code, json.loads(out), err), (0, {
            "enabled": True, "day": {"time": "08:00", "playlist": "ocean", "number": 2},
            "night": {"time": "20:00", "playlist": "calm", "number": 1}, "now": "night", "held": True,
            "pending": "ocean"}, ""))
        self.assertEqual(self.requests(engine), [])
        engine.stop()
        self.assertEqual(self.lwe("schedule"), (0, rows + "Schedule on. It is day now.\n"
                                                "the service is not running; live state unknown\n", ""))

    def test_a_missing_or_unknown_word_exits_3_before_any_request(self) -> None:
        engine = self.engine()
        for words in (("schedule", "day"), ("schedule", "dusk", "time", "7:00")):
            with self.subTest(words=words):
                code, out, err = self.lwe(*words)
                self.assertEqual((code, out), (3, ""))
                self.assertTrue(err.startswith("usage: lwe schedule"), err)
        self.assertEqual(engine.calls, [])

    def test_schedule_off_with_the_engine_bound_to_x_writes_active_playlist_x(self) -> None:
        self.store('ACTIVE_PLAYLIST=ocean\nSCHEDULE_ENABLED=true\nSCHEDULE="08:00=ocean;20:00=calm"\n')
        entries = [{"at": "08:00", "playlist": "ocean"}, {"at": "20:00", "playlist": "calm"}]
        engine = self.engine(schedule={"enabled": True, "entries": entries, "active": "calm", "held": False,
                                       "pending": ""},
                             lanes=[{"id": "all", "playlist": "calm", "enabled": True}])
        self.assertEqual(self.lwe("schedule", "off"), (0, "Schedule off; day and night stay as you set them.\n", ""))
        saved = self.settings.load()
        self.assertEqual((saved["ACTIVE_PLAYLIST"], saved["SCHEDULE_ENABLED"]), ("calm", False))
        self.assertNotIn("lanes-set", [cmd for cmd, _args in self.requests(engine)])

    def test_a_missing_row_takes_its_default_after_the_edit(self) -> None:
        from lwe_ui.storage import schedule
        cases = [("", "day", 20 * 60, "20:00=;08:00="), ("", "night", 8 * 60, "20:00=;08:00="),
                 ("07:00=a", "day", 20 * 60, "20:00=a;08:00="), ("", "day", 9 * 60, "09:00=;20:00=")]
        for text, part, minutes, want in cases:
            with self.subTest(text=text, part=part, minutes=minutes):
                rows = schedule.set_field(schedule.parse_rows(text), part, "time", minutes)
                self.assertEqual(schedule.format_rows(rows), want)
        self.store("ENGINE_FPS=30\n")
        self.engine()
        self.assertEqual(self.lwe("schedule", "day", "time", "20:00"), (0, "Day starts at 20:00.\n", ""))
        self.assertEqual(self.settings.load()["SCHEDULE"], "20:00=;08:00=")

    def test_backup_export_then_import_keeps_a_draft_row_in_place(self) -> None:
        from lwe_ui.storage import backup
        self.store('SCHEDULE_ENABLED=false\nSCHEDULE="08:00=;20:00=calm"\n')
        archive = ROOT / "draft.lwebackup"
        self.assertEqual(backup.export_to(archive)["errors"], [])
        self.store('SCHEDULE_ENABLED=false\nSCHEDULE="09:00=ocean;21:00=calm"\n')
        r = backup.import_from(archive)
        self.assertEqual(r["errors"], [])
        self.assertEqual(self.settings.load()["SCHEDULE"], "08:00=;20:00=calm")
        self.assertIn({"kind": "unset", "key": "SCHEDULE", "slug": ""}, r["notes"])
        self.assertNotIn("dangling", [note["kind"] for note in r["notes"]])


class ScheduleHandoffTest(unittest.TestCase):
    def test_bare_schedule_in_a_child_with_pyside6_blocked(self) -> None:
        env = _cli_env.scratch_env(ROOT / "child")
        playlists = Path(env["XDG_CONFIG_HOME"]) / "lwe" / "playlists"
        playlists.mkdir(parents=True, exist_ok=True)
        (playlists / "calm.conf").write_text("NAME=Calm\n", encoding="utf-8")
        (Path(env["XDG_CONFIG_HOME"]) / "lwe" / "settings.conf").write_text(
            'SCHEDULE_ENABLED=false\nSCHEDULE="07:00=calm;19:00="\n', encoding="utf-8")
        code, out, err = _cli_env.run_lwe(["schedule"], env, env["HOME"])
        self.assertEqual((code, err), (0, ""), out)
        lines = out.splitlines()
        self.assertEqual(lines[:2], ["Day    07:00  Calm (1)", "Night  19:00  not set"])
        self.assertEqual(lines[-1], "the service is not running; live state unknown")


if __name__ == "__main__":
    unittest.main()
