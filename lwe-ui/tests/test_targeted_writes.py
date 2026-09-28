"""Targeted writes: a save changes only its own line and leaves the rest of the file as it was.

Hand-edited settings, playlist and override files keep their comments, blank lines, unknown
keys, quoting and line endings through settings.update, playlists.toggle_member and update,
and wp.update_set: exactly the edited line differs, a duplicate key resolves to its last
assignment, a delete removes every assignment, an absent key is appended, an empty value is
written as KEY= and a file whose text does not change is not rewritten. The app lists change
one line at a time, and the generated engine files are written only when they change.
Lines end only at a newline, so an edit leaves a file bash still reads; a line whose quote
runs on to the next line is refused, and the page's list doors and the override clean-up
edit line by line too.

Run: PYTHONPATH=src python3 tests/test_targeted_writes.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import difflib
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_BOOT = tempfile.TemporaryDirectory(prefix="lwe-targeted-boot-")
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = os.path.join(_BOOT.name, _sub) if _sub else _BOOT.name

from PySide6.QtCore import QCoreApplication  # noqa: E402

_APP = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])

from lwe_ui import constants as C  # noqa: E402
from lwe_ui import settings_bridge  # noqa: E402
from lwe_ui.engine import daemon_unit, push  # noqa: E402
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

    def _env(self) -> dict[str, str]:
        """An environment for bash built from scratch, every path in this test's home."""
        (self.home / "bin").mkdir(exist_ok=True)
        return {"HOME": str(self.home), "PATH": str(self.home / "bin"),
                "XDG_CONFIG_HOME": os.environ["XDG_CONFIG_HOME"],
                "XDG_STATE_HOME": os.environ["XDG_STATE_HOME"],
                "XDG_DATA_HOME": os.environ["XDG_DATA_HOME"],
                "XDG_RUNTIME_DIR": str(self.home / "rt"), "LWE_SOCKET": str(self.home / "rt" / "sock")}

    def _source(self, path: Path, script: str) -> str:
        """Run `script` in bash after sourcing `path`, from an environment built from scratch."""
        r = subprocess.run([BASH, "-c", f'set -a; . "{path}"; {script}'], env=self._env(),
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def _bash_accepts(self, path: Path) -> None:
        """`bash -n` reads `path` without a syntax error, from an environment built from scratch."""
        r = subprocess.run([BASH, "-n", str(path)], env=self._env(),
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, f"{r.stderr} in {raw(path)!r}")

    def _bridge(self) -> settings_bridge.SettingsBridge:
        """The settings page's bridge over the page's own list readers, with the engine client
        replaced so no socket is reached."""
        for name, value in (("available", False), ("set_fullscreen_ignore", None),
                            ("set_app_conditions", None)):
            patcher = mock.patch.object(settings_bridge.api_client, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return settings_bridge.SettingsBridge(types.SimpleNamespace(
            _fullscreen_ignore_ids=push._fullscreen_ignore_ids,
            _app_condition_names=push._app_condition_names,
            delivery_due=lambda: False))

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

    def test_hand_edited_edge_cases_leave_a_file_bash_reads(self) -> None:
        wf = paths.wp_file("222")
        for ch in "\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029":
            with self.subTest(char=hex(ord(ch))):
                wf.write_text("BG=222\n", encoding="utf-8")
                wp.update_set("222", {"PROP_title": f"one {ch} two"})
                wp.update_set("222", {"PROP_title": "second"})
                self._bash_accepts(wf)
                self.assertEqual(raw(wf), "BG=222\nPROP_title=second\n")
                self.assertEqual(tier_a.parse(f'A="one {ch} two"\r\nB=1'), {"A": f"one {ch} two", "B": "1"})
                self.assertEqual(tier_a.edit(f'A="one {ch} two"', {"B": "1"}), f'A="one {ch} two"\nB=1\n')
        wf = paths.wp_file("111")
        edited = "BG=111\nPROP_title=new\nVOLUME=10\n"
        for body, after in (('BG=111\nPROP_title="line one\nline two"\nVOLUME=10\n', None),
                            ('BG=111\nPROP_title="a\x0cb"\nVOLUME=10\n', edited),
                            ('BG=111\nPROP_title="a\u2028b"\nVOLUME=10\n', edited)):
            with self.subTest(body=body):
                wf.write_text(body, encoding="utf-8")
                self._bash_accepts(wf)
                refused = False
                try:
                    wp.update_set("111", {"PROP_title": "new"})
                except ValueError:
                    refused = True
                self._bash_accepts(wf)
                self.assertEqual((refused, raw(wf)), (after is None, after or body))

    def test_crlf_files_keep_crlf_through_the_override_playlist_and_list_edits(self) -> None:
        wf = paths.wp_file("333")
        wf.write_bytes(b"# notes\r\nBG=333\r\nVOLUME=20\r\n")
        wp.update_set("333", {"VOLUME": 30})
        self.assertEqual(wf.read_bytes(), b"# notes\r\nBG=333\r\nVOLUME=30\r\n")
        pf = paths.playlist_file("crlf")
        pf.write_bytes(b'# mine\r\nNAME=Crlf\r\nMODE=shuffle\r\nMEMBERS="1 2"\r\n')
        playlists.update("crlf", {"MODE": "sequential"})
        self.assertEqual(pf.read_bytes(), b'# mine\r\nNAME=Crlf\r\nMODE=sequential\r\nMEMBERS="1 2"\r\n')
        f = rules.file_for("pause-blacklist.txt")
        f.write_bytes(b"# games\r\nsteam\r\nzoom\r\n")
        rules.modify("pause-blacklist.txt", lambda text: rules.remove_entry(text, "zoom"))
        self.assertEqual(f.read_bytes(), b"# games\r\nsteam\r\n")

    def test_playlist_and_override_files_are_written_only_when_they_change(self) -> None:
        playlists.save("same", {"NAME": "Same", "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min",
                                "MEMBERS": "1"})
        pf = paths.playlist_file("same")
        inode = pf.stat().st_ino
        playlists.update("same", {"MODE": "shuffle"})
        self.assertEqual(pf.stat().st_ino, inode, "an unchanged playlist is not rewritten")
        wf = paths.wp_file("444")
        wf.write_text("BG=444\nVOLUME=20\n", encoding="utf-8")
        inode = wf.stat().st_ino
        wp.update_set("444", {"VOLUME": 20})
        self.assertEqual(wf.stat().st_ino, inode, "an unchanged override is not rewritten")

    def test_the_override_clean_up_deletes_lines_and_keeps_the_rest(self) -> None:
        wf = paths.wp_file("555")
        wf.write_bytes(b"# my notes\r\nBG=555\n\nSCALING=default\nVOLUME=20\nFUTURE_KEY=1\nSPEED=1.0\n")
        self.assertEqual(wp.sparsify_overrides(), {"555": ["SCALING", "SPEED"]})
        self.assertEqual(raw(wf), "# my notes\r\nBG=555\n\nVOLUME=20\nFUTURE_KEY=1\n")

    def test_the_page_list_doors_change_one_line(self) -> None:
        bridge = self._bridge()
        bl = rules.file_for("pause-blacklist.txt")
        bl.write_text("# games I play\nsteam\n\n# work\nzoom\n", encoding="utf-8")
        self.assertTrue(bridge.addException("mpv"))
        self.assertEqual(raw(bl), "# games I play\nsteam\n\n# work\nzoom\nmpv\n")
        self.assertTrue(bridge.removeException("zoom"))
        self.assertEqual(raw(bl), "# games I play\nsteam\n\n# work\nmpv\n")
        ac = rules.file_for("app-condition.txt")
        ac.write_text("# my apps\n\nobs\n  steam\n", encoding="utf-8")
        self.assertTrue(bridge.addAppEntry("mpv"))
        self.assertEqual(raw(ac), "# my apps\n\nobs\n  steam\nmpv\n")
        self.assertTrue(bridge.removeAppEntry("obs"))
        self.assertEqual(raw(ac), "# my apps\n\n  steam\nmpv\n")

    def test_an_edit_of_a_value_whose_quote_runs_on_is_refused(self) -> None:
        body = 'BG=666\nVOLUME="5\n6"\nPROP_title="line one\nline two"\n'
        wf = paths.wp_file("666")
        for change in ({"PROP_title": "new"}, {"PROP_title": None}):
            with self.subTest(change=change):
                wf.write_text(body, encoding="utf-8")
                with self.assertRaises(ValueError) as caught:
                    wp.update_set("666", change)
                self.assertIn("PROP_title", str(caught.exception))
                self.assertIn(str(wf), str(caught.exception))
                self.assertEqual(raw(wf), body)
        self.assertEqual(wp.sparsify_overrides(), {}, "the clean-up skips the file it cannot edit")
        self.assertEqual(raw(wf), body)
        wp.update_set("666", {"BG": "667"})
        self.assertEqual(raw(wf), body.replace("BG=666", "BG=667"))
        settings.ensure_exists()
        p = paths.settings_file()
        p.write_text(raw(p) + 'SCHEDULE="a=b;\nc=d"\n', encoding="utf-8")
        before = raw(p)
        with self.assertRaises(ValueError) as caught:
            settings.update({"SCHEDULE": ""})
        self.assertIn("SCHEDULE", str(caught.exception))
        self.assertIn(str(p), str(caught.exception))
        self.assertEqual(raw(p), before)
        pf = paths.playlist_file("runs")
        pf.write_text('NAME="My\nlist"\nMEMBERS=1\n', encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            playlists.update("runs", {"NAME": "Mine"})
        self.assertIn("NAME", str(caught.exception))
        self.assertIn(str(pf), str(caught.exception))
        self.assertEqual(raw(pf), 'NAME="My\nlist"\nMEMBERS=1\n')

    def test_a_playlist_with_no_file_drops_none_changes(self) -> None:
        pf = paths.playlist_file("ghost")
        self.assertFalse(pf.exists())
        playlists.update("ghost", {"NAME": "Ghost", "MEMBERS": None})
        self.assertNotIn("None", raw(pf))
        self.assertEqual(playlists.members("ghost"), [])
        self.assertEqual(playlists.load("ghost")["NAME"], "Ghost")

    def test_an_unset_deletes_the_old_names_of_its_key(self) -> None:
        settings.ensure_exists()
        p = paths.settings_file()
        p.write_text(raw(p).replace("DETECT_INTERVAL_SEC=60\n", "DETECT_INTERVAL_MIN=5\n"), encoding="utf-8")
        self.assertEqual(settings.load()["DETECT_INTERVAL_SEC"], 300)
        settings.update({"DETECT_INTERVAL_SEC": None})
        self.assertFalse([ln for ln in raw(p).splitlines() if ln.startswith("DETECT_INTERVAL")])
        self.assertEqual(settings.load()["DETECT_INTERVAL_SEC"], 60)
        renames = {"overrides": {"OLD_VOLUME": ("VOLUME", None)}, "playlists": {"OLD_MODE": ("MODE", None)}}
        with mock.patch.dict(C.RENAMES, renames):
            wf = paths.wp_file("777")
            wf.write_text("BG=777\nOLD_VOLUME=5\n", encoding="utf-8")
            wp.update_set("777", {"VOLUME": None})
            self.assertEqual(raw(wf), "BG=777\n")
            wf.write_text("BG=777\nVOLUME=0\nOLD_VOLUME=5\n", encoding="utf-8")
            self.assertEqual(wp.sparsify_overrides(), {"777": ["VOLUME"]})
            self.assertEqual(raw(wf), "BG=777\n")
            pf = paths.playlist_file("old")
            pf.write_text("NAME=Old\nOLD_MODE=sequential\n", encoding="utf-8")
            playlists.update("old", {"MODE": None})
            self.assertEqual(raw(pf), "NAME=Old\n")

    def test_a_remove_takes_the_entry_the_page_shows_cut_to_its_cap(self) -> None:
        bridge = self._bridge()
        ac = rules.file_for("app-condition.txt")
        ac.write_text(f"# long lines\n{'a' * 70}\n{'a' * 64}\n  {'a' * 65}\nobs\n", encoding="utf-8")
        self.assertEqual(bridge.appEntries(), ["a" * 64] * 3 + ["obs"])
        self.assertTrue(bridge.removeAppEntry("a" * 64))
        self.assertEqual(raw(ac), "# long lines\nobs\n")
        bl = rules.file_for("pause-blacklist.txt")
        bl.write_text(f"{'x' * 130}\nsteam\n", encoding="utf-8")
        self.assertEqual(bridge.exceptions(), ["x" * 128, "steam"])
        self.assertTrue(bridge.removeException("x" * 128))
        self.assertEqual(raw(bl), "steam\n")

    def test_a_modify_with_no_change_writes_nothing_when_settings_are_missing(self) -> None:
        p = paths.settings_file()
        self.assertFalse(p.exists())
        self.assertEqual(settings.modify(lambda _current: {}), {})
        self.assertEqual(settings.modify(lambda _current: None), {})
        self.assertFalse(p.exists(), "a modify that changes nothing writes no settings file")

    def test_an_edit_keeps_the_values_the_panel_showed_around_a_lone_cr(self) -> None:
        settings.ensure_exists()
        p = paths.settings_file()
        p.write_bytes((raw(p).replace("ENGINE_FPS=60\n", "") + "ENGINE_FPS=30\rENGINE_VOLUME=50\n").encode("utf-8"))
        shown = settings.load()["ENGINE_VOLUME"]
        settings.update({"ENGINE_FPS": 45})
        self.assertEqual((shown, settings.load()["ENGINE_VOLUME"]), (15, 15))
        paths.playlist_file("cr").write_bytes(b'NAME=Cr\rMEMBERS="1 2"\nMODE=shuffle\n')
        shown = playlists.members("cr")
        playlists.update("cr", {"NAME": "Renamed"})
        self.assertEqual((shown, playlists.members("cr")), ([], []))

    def test_the_discovery_command_and_the_alias_claims_keep_a_lone_cr_inside_its_line(self) -> None:
        from lwe_ui import discover_cli
        from lwe_ui.storage import alias
        settings.ensure_exists()
        p = paths.settings_file()
        shown = discover_cli._wallpapers_dir()
        p.write_bytes((raw(p) + "ENGINE_FPS=30\rWALLPAPERS_DIR=/elsewhere\n").encode("utf-8"))
        self.assertEqual(discover_cli._wallpapers_dir(), shown)
        paths.wp_file("501").write_bytes(b"BG=501\nSPEED=2\rALIAS=foo\n")
        self.assertNotIn("foo", alias.claims())

    def test_the_export_leaves_out_a_value_with_a_line_break_and_names_it(self) -> None:
        from lwe_ui.storage import backup
        settings.ensure_exists()
        paths.wp_file("7").write_bytes(b"BG=7\nSKIP=1\rX=2\n")
        archive = self.home / "export.lwebackup"
        r = backup.export_to(archive)
        self.assertEqual(r["errors"], [])
        self.assertEqual(r["dropped"], [{"kind": "override-key", "file": "wp/7.conf", "key": "SKIP",
                                         "reason": "the value holds a line break the archive cannot carry"}])
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(tier_a.parse(z.read("wp/7.conf").decode("utf-8")), {"BG": "7"})
        self.assertTrue(backup.snapshot({"errors": [], "notes": []}), "the pre-restore snapshot is written")

    def test_a_value_the_panel_wrote_with_a_double_quote_is_never_refused(self) -> None:
        wf = paths.wp_file("300")
        for value in ('say "hi"', 'a "b" \\ "c"', 'end "q"\\'):
            with self.subTest(value=value):
                wf.write_text("BG=300\n", encoding="utf-8")
                wp.update_set("300", {"PROP_title": value})
                wp.update_set("300", {"PROP_title": "second"})
                self.assertEqual(raw(wf), "BG=300\nPROP_title=second\n")

    def test_an_unset_is_refused_when_any_assignment_of_its_key_runs_on(self) -> None:
        body = 'BG=300\nPROP_k="one\ntwo"\nPROP_k=2\n'
        wf = paths.wp_file("300")
        wf.write_text(body, encoding="utf-8")
        with self.assertRaises(ValueError):
            wp.update_set("300", {"PROP_k": None})
        self.assertEqual(raw(wf), body)
        self._bash_accepts(wf)


if __name__ == "__main__":
    unittest.main(verbosity=2)
