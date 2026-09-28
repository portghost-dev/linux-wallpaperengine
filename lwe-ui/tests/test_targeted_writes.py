"""Targeted writes: a save changes only its own line and leaves the rest of the file as it was.

Hand-edited settings, playlist and override files keep their comments, blank lines, unknown
keys, quoting and line endings through settings.update, playlists.toggle_member and update,
and wp.update_set: exactly the edited line differs, a duplicate key resolves to its last
assignment, a delete removes every assignment, an absent key is appended, an empty value is
written as KEY= and a file whose text does not change is not rewritten. The app lists change
one line at a time, and the generated engine files are written only when they change.

Run: PYTHONPATH=src python3 tests/test_targeted_writes.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import difflib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_BOOT = tempfile.TemporaryDirectory(prefix="lwe-targeted-boot-")
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = os.path.join(_BOOT.name, _sub) if _sub else _BOOT.name

from lwe_ui.engine import daemon_unit  # noqa: E402
from lwe_ui.storage import paths, playlists, rules, settings, tier_a, wp  # noqa: E402

BASH = shutil.which("bash")


def raw(path: Path) -> str:
    """The file's text with its line endings as stored."""
    return path.read_bytes().decode("utf-8")


def changed_lines(before: str, after: str) -> list[str]:
    """The lines difflib reports removed or added, each with its line ending."""
    diff = difflib.ndiff(before.splitlines(keepends=True), after.splitlines(keepends=True))
    return [d for d in diff if d[:2] in ("- ", "+ ")]


class TargetedWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="lwe-targeted-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        for key, sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"),
                         ("XDG_DATA_HOME", "d")):
            os.environ[key] = str(self.home / sub) if sub else str(self.home)
        paths.ensure_dirs()

    def _source(self, path: Path, script: str) -> str:
        """Run `script` in bash after sourcing `path`, from an environment built from scratch."""
        (self.home / "bin").mkdir(exist_ok=True)
        env = {"HOME": str(self.home), "PATH": str(self.home / "bin"),
               "XDG_CONFIG_HOME": os.environ["XDG_CONFIG_HOME"],
               "XDG_STATE_HOME": os.environ["XDG_STATE_HOME"],
               "XDG_DATA_HOME": os.environ["XDG_DATA_HOME"],
               "XDG_RUNTIME_DIR": str(self.home / "rt"), "LWE_SOCKET": str(self.home / "rt" / "sock")}
        r = subprocess.run([BASH, "-c", f'set -a; . "{path}"; {script}'], env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_a_settings_update_changes_only_its_own_line(self) -> None:
        p = paths.settings_file()
        p.write_text("# my settings, edited by hand\n"
                     "ENGINE_FPS=30\n"
                     "\n"
                     "  ENGINE_VOLUME=40\n"
                     "MY_NOTE='kept as written'\n"
                     "ENGINE_LAYER=top\r\n"
                     "ENGINE_FPS=45", encoding="utf-8")
        steps = (
            ({"ENGINE_FPS": 50}, ["- ENGINE_FPS=45", "+ ENGINE_FPS=50"]),
            ({"ENGINE_LAYER": "background"}, ["- ENGINE_LAYER=top\r\n", "+ ENGINE_LAYER=background\r\n"]),
            ({"ENGINE_VOLUME": 20}, ["-   ENGINE_VOLUME=40\n", "+   ENGINE_VOLUME=20\n"]),
            ({"ENGINE_TIMESCALE": 2.0}, ["- ENGINE_FPS=50", "+ ENGINE_FPS=50\n", "+ ENGINE_TIMESCALE=2.0\n"]),
        )
        for changes, expected in steps:
            with self.subTest(changes=changes):
                before = raw(p)
                settings.update(changes)
                self.assertEqual(changed_lines(before, raw(p)), expected)
        text = raw(p)
        self.assertIn("ENGINE_FPS=30\n", text, "the earlier assignment stays; the last one is the value")
        loaded = settings.load()
        self.assertEqual((loaded["ENGINE_FPS"], loaded["ENGINE_LAYER"], loaded["ENGINE_VOLUME"],
                          loaded["ENGINE_TIMESCALE"]), (50, "background", 20, 2.0))
        inode = p.stat().st_ino
        settings.update({"ENGINE_FPS": 50})
        self.assertEqual(p.stat().st_ino, inode, "an unchanged text is not rewritten")
        settings.update({"ENGINE_FPS": None})
        self.assertNotIn("ENGINE_FPS", raw(p), "a delete removes every assignment")
        self.assertEqual(settings.load()["ENGINE_FPS"], 60)
        self.assertTrue(raw(p).startswith("# my settings, edited by hand\n"))

    def test_a_missing_settings_file_gets_the_full_defaults_then_the_edit(self) -> None:
        settings.ensure_exists()
        defaults = raw(paths.settings_file())
        paths.settings_file().unlink()
        settings.update({"ENGINE_FPS": 45, "ENGINE_CLAMP": None})
        self.assertEqual(changed_lines(defaults, raw(paths.settings_file())),
                         ["- ENGINE_FPS=60\n", "+ ENGINE_FPS=45\n", "- ENGINE_CLAMP=\n"])

    def test_a_playlist_edit_changes_only_its_own_line(self) -> None:
        playlists.save("hand", {"NAME": "Hand", "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min",
                                "MEMBERS": "100 200"})
        pf = paths.playlist_file("hand")
        pf.write_text("# my playlist\n\n" + raw(pf) + "OWNER=me\n", encoding="utf-8")
        before = raw(pf)
        self.assertTrue(playlists.toggle_member("hand", "300"))
        self.assertEqual(changed_lines(before, raw(pf)),
                         ['- MEMBERS="100 200"\n', '+ MEMBERS="100 200 300"\n'])
        before = raw(pf)
        playlists.update("hand", {"MODE": "sequential"})
        self.assertEqual(changed_lines(before, raw(pf)),
                         ["- MODE=shuffle\n", "+ MODE=sequential\n"])
        self.assertEqual(self._source(pf, 'for id in $MEMBERS; do echo "$id"; done; echo "$MODE $OWNER"').split(),
                         ["100", "200", "300", "sequential", "me"])
        self.assertEqual(playlists.load("hand")["MEMBERS"].split(), ["100", "200", "300"])

    def test_an_override_edit_changes_only_its_own_line(self) -> None:
        wf = paths.wp_file("111")
        wf.write_text("# my notes\n\nBG=111\nSCALING='fill'\nVOLUME=10\nVOLUME=20\nFUTURE_KEY=1\n",
                      encoding="utf-8")
        before = raw(wf)
        wp.update_set("111", {"SPEED": 2.0})
        self.assertEqual(changed_lines(before, raw(wf)), ["+ SPEED=2.0\n"])
        before = raw(wf)
        wp.update_set("111", {"VOLUME": 30})
        self.assertEqual(changed_lines(before, raw(wf)),
                         ["- VOLUME=20\n", "+ VOLUME=30\n"])
        self.assertEqual(wp.load_set("111")["VOLUME"], 30, "the duplicate resolves to the last")
        wp.update_set("111", {"VOLUME": None})
        text = raw(wf)
        self.assertNotIn("VOLUME", text)
        self.assertEqual(text, "# my notes\n\nBG=111\nSCALING='fill'\nFUTURE_KEY=1\nSPEED=2.0\n")

    def test_an_empty_value_writes_the_key_and_none_deletes_it(self) -> None:
        self.assertEqual(tier_a.edit("A=1\nB=2\nA=3\n", {"A": ""}), "A=1\nB=2\nA=\n")
        self.assertEqual(tier_a.edit("A=1\nB=2\nA=3\n", {"A": None}), "B=2\n")
        wf = paths.wp_file("111")
        wf.write_text("BG=111\nTEXTURE_DETAIL=full\n", encoding="utf-8")
        wp.update_set("111", {"TEXTURE_DETAIL": ""})
        self.assertEqual(raw(wf), "BG=111\nTEXTURE_DETAIL=\n")
        self.assertEqual(wp.load_set("111")["TEXTURE_DETAIL"], "")
        wp.update_set("111", {"TEXTURE_DETAIL": None})
        self.assertEqual(raw(wf), "BG=111\n")
        settings.ensure_exists()
        settings.update({"ENGINE_CLAMP": "border"})
        settings.update({"ENGINE_CLAMP": ""})
        lines = raw(paths.settings_file()).splitlines()
        self.assertIn("ENGINE_CLAMP=", lines)
        settings.update({"ENGINE_CLAMP": None})
        self.assertFalse([ln for ln in raw(paths.settings_file()).splitlines()
                          if ln.startswith("ENGINE_CLAMP")])

    def test_an_assignment_is_what_the_reader_reads(self) -> None:
        text = "# A=0\n  A=1\nexport B=2\nA=2 # a note\r\n"
        self.assertEqual(tier_a.parse(text), {"A": "2 # a note"})
        self.assertEqual(tier_a.edit(text, {"A": "9"}), "# A=0\n  A=1\nexport B=2\nA=9\r\n")
        self.assertEqual(tier_a.edit(text, {"B": "3"}), text + "B=3\n", "an export line is not an assignment")
        self.assertEqual(tier_a.edit("  A=1", {"A": "5"}), "  A=5", "the indentation stays")
        self.assertEqual(tier_a.edit("  A=1", {"C": "5"}), "  A=1\nC=5\n")

    def test_the_app_lists_change_one_line(self) -> None:
        name = "app-condition.txt"
        f = rules.file_for(name)
        f.write_text("# my apps\n\nsteam\n  obs\nfirefox", encoding="utf-8")
        rules.modify(name, lambda text: rules.add_entry(text, "mpv"))
        self.assertEqual(raw(f), "# my apps\n\nsteam\n  obs\nfirefox\nmpv\n")
        inode = f.stat().st_ino
        rules.modify(name, lambda text: rules.add_entry(text, "obs"))
        self.assertEqual(f.stat().st_ino, inode, "an entry already there, once stripped, writes nothing")
        f.write_text(raw(f) + "obs\n", encoding="utf-8")
        rules.modify(name, lambda text: rules.remove_entry(text, "obs"))
        self.assertEqual(raw(f), "# my apps\n\nsteam\nfirefox\nmpv\n")

    def test_the_engine_files_are_written_only_when_they_change(self) -> None:
        engine = self.home / "linux-wallpaperengine"
        engine.write_text("#!/bin/sh\n", encoding="utf-8")
        settings.update({"ENGINE_BIN": str(engine)})
        reload = mock.Mock(return_value=subprocess.CompletedProcess(["systemctl"], 0, "", ""))
        with mock.patch.object(daemon_unit.subprocess, "run", reload):
            env_path, unit_path = daemon_unit.write_files(["DP-1"])
            inodes = (Path(env_path).stat().st_ino, Path(unit_path).stat().st_ino)
            daemon_unit.write_files(["DP-1"])
            self.assertEqual((Path(env_path).stat().st_ino, Path(unit_path).stat().st_ino), inodes)
            daemon_unit.write_files(["DP-2"])
            self.assertNotEqual(Path(env_path).stat().st_ino, inodes[0], "a changed env file is written")
            self.assertEqual(Path(unit_path).stat().st_ino, inodes[1])
        self.assertEqual(reload.call_count, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
