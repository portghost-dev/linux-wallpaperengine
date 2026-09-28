"""order and interval: the order and interval of the playlist that plays.

Both change the derived active playlist's file, the saved active playlist or, while the engine's
schedule is on, the playlist the engine is bound to, and never write ACTIVE_PLAYLIST. A change saves
only its MODE or INTERVAL line, UNIT staying as it was, and sends, apart from status reads, the
playlist's transfer and then lanes-set with its enabled state, with no playlist field, no manual and
no schedule-set. An unchanged value writes and sends nothing; a value typed wrong or out of range
exits 3 before any request; a playlist file removed before the write is not made again; with no
playlist at all the verbs exit 1. order load and interval load send the transfer again and write
nothing, and exit 2 while the service is not running. config unset removes every MODE or INTERVAL
line through UNSET. The receipts name the playlist; the service away, a refusal and another build's
engine each give their outcome.

Each form runs through cli.main in this process with HOME and the XDG folders at scratch
(_cli_env.scratch_home) and, where a case needs one, a fake engine on the socket _sandbox pins. One
case runs through the engine's handoff in a child whose environment is built from nothing
(_cli_env.scratch_env), PySide6 blocked. No case runs systemctl.

Run: PYTHONPATH=src python3 tests/test_cli_playlist.py
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

ROOT = Path(tempfile.mkdtemp(prefix="lwe-cli-playlist-"))
HOME = _cli_env.scratch_home(ROOT / "inproc")
MAIN = 'NAME=Main\nMODE=shuffle\nINTERVAL=900\nUNIT=min\nMEMBERS="111 222"\n'
NIGHT = "NAME=Night\nMODE=sequential\nINTERVAL=90\nUNIT=s\nMEMBERS=333\n"
SCHEDULE_ON = {"enabled": True, "entries": [], "active": "night", "held": False, "pending": ""}
NO_PLAYLIST = "No playlist is playing; lwe playlist <p> picks one\n"
OPPORTUNITIES = ("It can apply the next time the panel window opens or polls, an lwe command saves a setting "
                 "the engine uses, lwe reload or a backup import runs, or lwe service start or restart starts "
                 "the engine.")
INTERVAL_RANGE = "takes a whole number with s, m or h from 15s to 9999m (a bare number is minutes)"


def lanes(enabled: bool) -> tuple:
    return ("lanes-set", {"lanes": [{"id": "all", "enabled": enabled}]})


def tearDownModule() -> None:
    shutil.rmtree(ROOT, True)


class OrderIntervalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui import cli, version
        from lwe_ui.cli import settings_table
        from lwe_ui.engine import daemon_unit, marker
        from lwe_ui.storage import paths
        cls.cli, cls.stamp, cls.paths, cls.marker = cli, version.panel_stamp(), paths, marker
        cls.settings_table, cls.daemon_unit = settings_table, daemon_unit

    def setUp(self) -> None:
        for folder in (self.paths.config_dir(), self.paths.state_dir()):
            shutil.rmtree(folder, True)
            folder.mkdir(parents=True)
        self.runs: list = []
        du = self.daemon_unit
        for patcher in (mock.patch.object(du.subprocess, "run", lambda argv, **kw: self.runs.append(argv)),
                        mock.patch.object(du, "enumerate_outputs", lambda: ["DP-1"]),
                        mock.patch.object(du, "live_engine_env", lambda: None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.assertEqual(self.runs, [], "a subprocess ran")

    def engine(self, **fields) -> _fake_engine.FakeEngine:
        engine = _fake_engine.FakeEngine(_sandbox.SOCKET, **fields)
        self.addCleanup(engine.stop)
        return engine

    def lwe(self, *words: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(list(words), sender_stamp=self.stamp)
        return code, out.getvalue(), err.getvalue()

    def write(self, name: str, text: str) -> None:
        path = self.paths.config_dir() / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def read(self, name: str) -> str:
        return (self.paths.config_dir() / name).read_text(encoding="utf-8")

    def seed(self, active: str = "main") -> None:
        self.write("playlists/main.conf", MAIN)
        self.write("playlists/night.conf", NIGHT)
        self.write("settings.conf", f"ACTIVE_PLAYLIST={active}\n")

    def snapshot(self) -> dict[str, bytes]:
        """Every file under the config and state folders but the lock sidecars."""
        out = {}
        for folder in (self.paths.config_dir(), self.paths.state_dir()):
            for path in sorted(folder.rglob("*")):
                if path.is_file() and self.paths.locks_dir() not in path.parents:
                    out[str(path)] = path.read_bytes()
        return out

    @staticmethod
    def sent(engine: _fake_engine.FakeEngine) -> list[tuple]:
        """The requests apart from status reads; a transfer part as (verb, slug, order, interval_s, part,
        of, entry count)."""
        out = []
        for cmd, args in engine.calls:
            if cmd == "playlist-set":
                out.append((cmd, args["slug"], args["order"], args["interval_s"], args["part"], args["of"],
                            len(args["entries"])))
            elif cmd != "status":
                out.append((cmd, args))
        return out

    def test_order_static_changes_the_mode_line_and_sends_the_transfer_then_the_lane(self) -> None:
        self.seed()
        engine = self.engine()
        self.assertEqual(self.lwe("order", "static"), (0, "Order of Main set to static.\n", ""))
        self.assertEqual(self.read("playlists/main.conf"), MAIN.replace("MODE=shuffle", "MODE=static"))
        self.assertEqual((self.read("playlists/night.conf"), self.read("settings.conf")),
                         (NIGHT, "ACTIVE_PLAYLIST=main\n"))
        self.assertEqual(self.sent(engine), [("playlist-set", "main", "static", 900, 1, 1, 2), lanes(False)])
        self.assertEqual(self.marker.read()["classes"], [])

    def test_under_an_enabled_schedule_the_engines_playlist_changes_and_active_playlist_is_not_written(self) -> None:
        self.seed()
        engine = self.engine(schedule=SCHEDULE_ON, lanes=[{"id": "all", "playlist": "night"}])
        self.assertEqual(self.lwe("order", "static"), (0, "Order of Night set to static.\n", ""))
        self.assertEqual(self.read("playlists/night.conf"), NIGHT.replace("MODE=sequential", "MODE=static"))
        self.assertEqual((self.read("playlists/main.conf"), self.read("settings.conf")),
                         (MAIN, "ACTIVE_PLAYLIST=main\n"))
        self.assertEqual(self.sent(engine), [("playlist-set", "night", "static", 90, 1, 1, 1), lanes(False)])

    def test_interval_20_writes_1200_leaves_unit_and_names_the_next_change(self) -> None:
        self.seed(active="night")
        engine = self.engine()
        engine.script("lanes-set", _fake_engine.done({"lanes": [{"id": "all", "next_in_ms": 1200000,
                                                                 "interval_s": 1200}]}))
        self.assertEqual(self.lwe("interval", "20"), (0, "Interval of Night set to 20m; next change in 20m.\n", ""))
        self.assertEqual(self.read("playlists/night.conf"), NIGHT.replace("INTERVAL=90", "INTERVAL=1200"))
        self.assertEqual(self.sent(engine), [("playlist-set", "night", "sequential", 1200, 1, 1, 1), lanes(True)])
        code, out, err = self.lwe("-j", "interval", "30s")
        self.assertEqual((code, json.loads(out), err),
                         (0, {"playlist": "Night", "setting": "interval", "value": "30s", "saved": True,
                              "outcome": "applied", "reason": ""}, ""))
        self.assertEqual(self.read("playlists/night.conf"), NIGHT.replace("INTERVAL=90", "INTERVAL=30"))

    def test_a_value_typed_wrong_or_out_of_range_exits_3_before_any_request(self) -> None:
        self.seed()
        before = self.snapshot()
        engine = self.engine()
        for words, line in ((("interval", "10s"), f"interval {INTERVAL_RANGE}; got 10s"),
                            (("interval", "10000m"), f"interval {INTERVAL_RANGE}; got 10000m"),
                            (("order", "random"), "order takes shuffle, sequential or static; got random"),
                            (("order", "static", "now"), "order takes one value; got static now")):
            with self.subTest(words=words):
                self.assertEqual(self.lwe(*words), (3, "", line + "\n"))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(engine.calls, [])

    def test_an_unchanged_value_writes_and_sends_nothing(self) -> None:
        self.seed()
        before = self.snapshot()
        engine = self.engine()
        self.assertEqual(self.lwe("order", "shuffle"), (0, "Order of Main is already shuffle.\n", ""))
        self.assertEqual(self.lwe("interval", "15m"), (0, "Interval of Main is already 15m.\n", ""))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(engine.calls, [("status", {})] * 2)

    def test_a_playlist_file_removed_before_the_write_is_not_made_again(self) -> None:
        self.seed()
        path = self.paths.playlist_file("main")
        gone = (1, "", f"{path} is gone, so nothing was saved\n")
        derive = self.settings_table.derived_active_playlist

        def vanish(status):
            found = derive(status)
            path.unlink()
            return found
        engine = self.engine()
        with mock.patch.object(self.settings_table, "derived_active_playlist", vanish):
            self.assertEqual(self.lwe("order", "static"), gone)
        self.assertFalse(path.exists())
        self.assertIsNone(self.marker.read()["generation"], "the marker was set")
        self.seed()
        writing = self.marker.writing

        @contextlib.contextmanager
        def removed_inside(classes):
            with writing(classes) as ticket:
                path.unlink()
                yield ticket
        with mock.patch.object(self.marker, "writing", removed_inside):
            self.assertEqual(self.lwe("interval", "20"), gone)
        self.assertFalse(path.exists())
        self.assertEqual(self.sent(engine), [])

    def test_order_load_sends_the_transfer_again_and_writes_nothing(self) -> None:
        self.seed()
        before = self.snapshot()
        engine = self.engine()
        self.assertEqual(self.lwe("order", "load"), (0, "Order of Main sent to the engine: shuffle.\n", ""))
        self.assertEqual(self.lwe("interval", "load"), (0, "Interval of Main sent to the engine: 15m.\n", ""))
        self.assertEqual(self.sent(engine), [("playlist-set", "main", "shuffle", 900, 1, 1, 2), lanes(True)] * 2)
        self.assertEqual(self.snapshot(), before)
        engine.stop()
        self.assertEqual(self.lwe("order", "load"), (2, "", "the service is not running\n"))
        self.assertEqual(self.snapshot(), before)

    def test_config_unset_removes_every_mode_line_through_unset(self) -> None:
        self.write("playlists/main.conf", "NAME=Main\nMODE=static\nINTERVAL=1200\nMODE=sequential\nMEMBERS=111\n")
        self.write("settings.conf", "ACTIVE_PLAYLIST=main\n")
        engine = self.engine()
        self.assertEqual(self.lwe("config", "unset", "order"), (0, "Order of Main set to shuffle.\n", ""))
        self.assertEqual(self.read("playlists/main.conf"), "NAME=Main\nINTERVAL=1200\nMEMBERS=111\n")
        self.assertEqual(self.sent(engine), [("playlist-set", "main", "shuffle", 1200, 1, 1, 1), lanes(True)])
        from lwe_ui.cli.verbs.playlist import UNSET
        out, err = io.StringIO(), io.StringIO()
        code = UNSET["interval"](self.cli.Context(False, out, err, self.stamp, False))
        self.assertEqual((code, out.getvalue(), err.getvalue()), (0, "Interval of Main set to 15m.\n", ""))
        self.assertEqual(self.read("playlists/main.conf"), "NAME=Main\nMEMBERS=111\n")

    def test_with_no_playlist_at_all_the_verbs_exit_1_and_create_nothing(self) -> None:
        engine = self.engine()
        for words in (("order", "static"), ("interval", "load"), ("config", "unset", "interval")):
            with self.subTest(words=words):
                self.assertEqual(self.lwe(*words), (1, "", NO_PLAYLIST))
        self.assertEqual(self.snapshot(), {})
        self.assertEqual(self.sent(engine), [])

    def test_the_outcomes_of_the_runner_name_the_playlist(self) -> None:
        self.seed()
        self.assertEqual(self.lwe("order", "static"),
                         (0, "Order of Main set to static: saved; the service is not running or is busy, so it is not "
                             f"applied yet. {OPPORTUNITIES}\n", ""))
        self.assertEqual((self.read("playlists/main.conf"), self.marker.read()["classes"]),
                         (MAIN.replace("MODE=shuffle", "MODE=static"), ["BUNDLE"]))
        engine = self.engine()
        engine.script("playlist-set", *[_fake_engine.fail("unknown member")] * 3)
        self.assertEqual(self.lwe("order", "sequential"),
                         (1, "Order of Main set to sequential: saved, but the engine refused it: unknown member. "
                             f"{OPPORTUNITIES}\n", ""))
        self.assertEqual(self.marker.read()["classes"], ["BUNDLE"])
        engine.set(version="0.0.1-other")
        before = self.snapshot()
        self.assertEqual(self.lwe("interval", "20"),
                         (1, "", f"The running engine is 0.0.1-other but {self.stamp} is installed; run lwe service "
                                 "restart.\n"))
        self.assertEqual(self.snapshot(), before)

    def test_bare_order_prints_its_help_and_config_order_runs_the_same_code(self) -> None:
        self.seed()
        self.assertEqual(self.lwe("order"), (0, "order shuffle | sequential | static\n", ""))
        engine = self.engine()
        self.assertEqual(self.lwe("config", "order", "static"), (0, "Order of Main set to static.\n", ""))
        self.assertEqual(self.sent(engine), [("playlist-set", "main", "static", 900, 1, 1, 2), lanes(False)])


class PlaylistHandoffTest(unittest.TestCase):
    def test_order_static_through_the_engine_handoff_without_pyside6(self) -> None:
        root = ROOT / "child"
        log = root / "systemctl.log"
        env = _cli_env.scratch_env(root, {"systemctl": f"echo \"$*\" >> '{log}'\nexit 1\n"})
        config = Path(env["XDG_CONFIG_HOME"]) / "lwe"
        (config / "playlists").mkdir(parents=True)
        (config / "playlists" / "main.conf").write_text(MAIN, encoding="utf-8")
        (config / "settings.conf").write_text("ACTIVE_PLAYLIST=main\n", encoding="utf-8")
        with _fake_engine.FakeEngine(env["LWE_SOCKET"]) as engine:
            result = _cli_env.run_lwe(["order", "static"], env, "")
        self.assertEqual(result, (0, "Order of Main set to static.\n", ""))
        self.assertEqual((config / "playlists" / "main.conf").read_text(encoding="utf-8"),
                         MAIN.replace("MODE=shuffle", "MODE=static"))
        self.assertEqual([cmd for cmd, _args in engine.calls], ["status", "status", "playlist-set", "lanes-set"])
        self.assertFalse(log.exists(), "systemctl was run")


if __name__ == "__main__":
    unittest.main(verbosity=2)
