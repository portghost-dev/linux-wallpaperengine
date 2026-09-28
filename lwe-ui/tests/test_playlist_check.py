"""The playlist files in reload: check_files and apply_cleanups.

check_files reads every playlists/*.conf raw and writes nothing. Each check fires on a crafted file and
names the file, the line and the key: NAME missing, MODE not one of the three (random only warned as an
old spelling), INTERVAL outside 15..599940 or not whole, UNIT not min or s, MEMBERS with an unsafe id or
a repeat (an id not in the library is a warning), a transfer that would leave entries out, and a file
name the engine refuses, an error when the playlist is engine-held and a warning otherwise; two files
sharing a display name are a warning. A valid tree reports nothing. ACTIVE_PLAYLIST naming a missing
file is a cleanup that check_files returns and apply_cleanups makes, changing only that line. The
playing playlist's file is compared with the status the engine reports.

Each case runs in this process with HOME and the XDG folders at scratch (_cli_env.scratch_home); no
engine, no socket, no subprocess.

Run: PYTHONPATH=src python3 tests/test_playlist_check.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _cli_env

ROOT = Path(tempfile.mkdtemp(prefix="lwe-playlist-check-"))
HOME = _cli_env.scratch_home(ROOT / "inproc")
MAIN = "NAME=Main\nMODE=shuffle\nINTERVAL=900\nUNIT=min\nMEMBERS=111\n"


def tearDownModule() -> None:
    shutil.rmtree(ROOT, True)


class PlaylistCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui.cli.verbs import playlist
        from lwe_ui.storage import paths
        cls.playlist, cls.paths = playlist, paths

    def setUp(self) -> None:
        for folder in (self.paths.config_dir(), self.paths.state_dir()):
            shutil.rmtree(folder, True)
            folder.mkdir(parents=True)
        self.library = ROOT / "library"
        shutil.rmtree(self.library, True)
        (self.library / "111").mkdir(parents=True)
        self.write("settings.conf", f"WALLPAPERS_DIR={self.library}\nACTIVE_PLAYLIST=main\n")
        self.write("playlists/main.conf", MAIN)

    def write(self, name: str, text: str) -> None:
        path = self.paths.config_dir() / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def snapshot(self) -> dict[str, bytes]:
        out = {}
        for folder in (self.paths.config_dir(), self.paths.state_dir()):
            for path in sorted(folder.rglob("*")):
                if path.is_file() and self.paths.locks_dir() not in path.parents:
                    out[str(path)] = path.read_bytes()
        return out

    def check(self, status=None) -> tuple:
        before = self.snapshot()
        result = self.playlist.check_files(status)
        self.assertEqual(self.snapshot(), before, "check_files wrote")
        return result

    def test_a_valid_tree_reports_nothing_and_writes_nothing(self) -> None:
        self.assertEqual(self.check(), ([], [], [], []))

    def test_each_error_names_the_file_the_line_and_the_key(self) -> None:
        self.write("playlists/bad.conf", "MODE=weighted\nINTERVAL=10\nUNIT=hours\nMEMBERS=111 111 a;b\n")
        errors, warnings, _cleanups, _changes = self.check()
        self.assertEqual(errors, [
            "playlists/bad.conf: NAME is missing",
            "playlists/bad.conf:1: MODE must be shuffle, sequential or static; got weighted",
            "playlists/bad.conf:2: INTERVAL must be a whole number of seconds from 15 to 599940; got 10",
            "playlists/bad.conf:3: UNIT must be min or s; got hours",
            "playlists/bad.conf:4: MEMBERS holds a;b, which is not a wallpaper id",
            "playlists/bad.conf:4: MEMBERS lists 111 more than once",
        ])
        self.assertEqual(warnings, [])

    def test_the_warnings_old_spelling_library_and_shared_name(self) -> None:
        self.write("playlists/main.conf", "NAME=Main\nMODE=random\nMEMBERS=111 222\n")
        self.write("playlists/other.conf", "NAME=main\n")
        errors, warnings, _cleanups, _changes = self.check()
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [
            "playlists/main.conf:2: MODE random is an old spelling of shuffle",
            "playlists/main.conf:3: MEMBERS holds 222, which is not in the library",
            "playlists/main.conf, playlists/other.conf: NAME is the same in 2 files",
        ])

    def test_a_file_name_the_engine_refuses_is_an_error_only_when_engine_held(self) -> None:
        self.write("playlists/chill.v2.conf", "NAME=Chill\n")
        refused = ("playlists/chill.v2.conf: the engine does not take this file name (letters, digits, - and _, "
                   "64 at most)")
        self.assertEqual(self.check()[:2], ([], [refused]))
        self.write("settings.conf", f"WALLPAPERS_DIR={self.library}\nACTIVE_PLAYLIST=chill.v2\n")
        self.assertEqual(self.check()[:2], ([refused], []))

    def test_a_transfer_that_would_leave_entries_out_is_an_error(self) -> None:
        with mock.patch("lwe_ui.engine.resolve.split_playlist_parts", lambda entries: [entries[:0]]):
            errors = self.check()[0]
        self.assertEqual(errors, ["playlists/main.conf:5: MEMBERS is too large for one transfer; 1 entries would "
                                  "be left out"])

    def test_active_playlist_naming_a_missing_file_is_a_cleanup_that_changes_only_its_line(self) -> None:
        self.write("playlists/zeta.conf", "NAME=Zeta\n")
        self.write("settings.conf", f"WALLPAPERS_DIR={self.library}\nACTIVE_PLAYLIST=gone\nENGINE_VOLUME=40\n")
        errors, warnings, cleanups, _changes = self.check()
        self.assertEqual((errors, warnings), ([], []))
        self.assertEqual(cleanups, [{"file": "settings.conf", "key": "ACTIVE_PLAYLIST", "from": "gone", "to": "main"}])
        line = self.playlist.apply_cleanups(cleanups)
        self.assertEqual(line, "settings.conf: ACTIVE_PLAYLIST gone -> main, since playlists/gone.conf is gone")
        self.assertEqual((self.paths.config_dir() / "settings.conf").read_text(encoding="utf-8"),
                         f"WALLPAPERS_DIR={self.library}\nACTIVE_PLAYLIST=main\nENGINE_VOLUME=40\n")
        self.assertEqual(self.check()[2], [])

    def test_the_playing_playlists_file_is_compared_with_status(self) -> None:
        status = {"rotation": {"label": "Main", "order": "sequential", "interval_s": 900, "count": 1},
                  "schedule": {"enabled": False}, "lanes": []}
        self.assertEqual(self.check(status)[3], ["playlists/main.conf: order is shuffle in the file and sequential "
                                                 "in the engine"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
