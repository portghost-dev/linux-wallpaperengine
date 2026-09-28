"""Picking wallpapers and playlists: the grammar every command that takes one shares.

Up to five ASCII digits are a place in the list (the pool, then the Workshop rows), six to eight are
refused with both readings, nine or more are an id, @name is an alias, current is the wallpaper on
screen and any other word is an exact id or a title ignoring case; ambiguity and no match refuse,
and a refusal returns nothing. current wins over a wallpaper titled current; a trashed wallpaper on
screen is picked as trashed and a preset on screen as the preset; an unreadable version file
refuses with its message. Playlists are picked by number or by name. The engine's status and
availability are replaced for each call. A PySide6-poisoned child with an environment built from
nothing imports the catalog and the pick module.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 tests/test_cli_select.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.TemporaryDirectory(prefix="lwe-select-")
_ROOT = Path(_TMP.name)
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = str(_ROOT / _sub) if _sub else str(_ROOT)
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from lwe_ui import api_client, version  # noqa: E402
from lwe_ui.cli import Context  # noqa: E402
from lwe_ui.cli import select  # noqa: E402
from lwe_ui.cli.select import Pick, PickError, PlaylistPick  # noqa: E402
from lwe_ui.library import catalog  # noqa: E402
from lwe_ui.storage import paths, playlists, settings, tags  # noqa: E402

LIB = _ROOT / "lib"
POOL = (("123456789", "Nine"), ("1234567890", "Ten"), ("hand_made", "Hand Made"),
        ("201", "Caf\u00e9"), ("202", "Caf\u00e9"), ("abc", "Alpha"), ("301", "ABC"),
        ("1234567", "Seven"), ("401", "Deep Space"), ("501", "Echo"), ("502", "Foxtrot"),
        ("801", "Preset"))


def _item(wid: str, title: str) -> None:
    folder = LIB / wid
    folder.mkdir(parents=True)
    (folder / "project.json").write_text(json.dumps({"title": title, "type": "scene", "file": "scene.json"}),
                                         encoding="utf-8")
    (folder / "scene.pkg").write_bytes(b"x" * 16)


def _playlist(slug: str, name: str, members: str = "") -> None:
    playlists.save(slug, {"NAME": name, "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min", "MEMBERS": members})


def setUpModule() -> None:
    paths.ensure_dirs()
    settings.ensure_exists()
    settings.save({**settings.load(), "WALLPAPERS_DIR": str(LIB), "WORKSHOP_DIR": str(_ROOT / "workshop")})
    for wid, title in POOL:
        _item(wid, title)
        tags.set_state(wid, title, "good")
    _item("601", "Waiting One")
    tags.set_state("601", "Waiting One", "review")
    tags.set_state("987654321", "Gone", "bad")
    tags.set_state("702", "Zulu", "bad")
    paths.wp_file("401").write_text("ALIAS=Deep\n", encoding="utf-8")
    paths.wp_file("501").write_text("ALIAS=twin\n", encoding="utf-8")
    paths.wp_file("502").write_text("ALIAS=Twin\n", encoding="utf-8")
    paths.wp_file("801").write_text("BG=401\n", encoding="utf-8")
    _playlist("chill", "Chill Beats")
    _playlist("night-a", "Night")
    _playlist("night-b", "Night")
    _playlist("two", "2")
    _playlist("mix", "Mix", "401 555555555")


def _status(current: dict | None, version_text: str | None = None, available: bool = True):
    """Patches for api_client.status and .available: None for current means no status reply."""
    reply = None if current is None else {"version": version_text or version.panel_stamp(), "current": current}
    return (mock.patch.object(api_client, "status", lambda *a, **k: reply),
            mock.patch.object(api_client, "available", lambda *a, **k: available))


def _one_more_row(wid: str, title: str):
    """A patch for catalog.wallpaper_rows: the fixture's rows and one more pool row numbered after
    them, so the fixture's numbers stay as they are."""
    rows, unsafe = catalog.wallpaper_rows()
    extra = catalog.Row(len(rows) + 1, wid, title, "", "scene", "pool", False)
    return mock.patch.object(catalog, "wallpaper_rows", return_value=(rows + [extra], unsafe))


class WallpaperGrammarTest(unittest.TestCase):
    def refused(self, word: str, code: int, message: str, lines: list[str] | None = None, **kw) -> None:
        with self.assertRaises(PickError) as caught:
            select.wallpaper(word, **kw)
        self.assertEqual((caught.exception.code, caught.exception.message), (code, message))
        if lines is not None:
            self.assertEqual(caught.exception.lines, lines)

    def test_numbers_are_places_in_the_list(self) -> None:
        self.assertEqual(select.wallpaper("5"), Pick(5, "401", "401", "Deep Space", "Deep", "pool"))
        self.assertEqual(select.wallpaper("00002").ui_id, "abc")
        self.refused("0", 1, "there is no number 0 in the list (1 to 13)")
        self.refused("14", 1, "there is no number 14 in the list (1 to 13)")

    def test_numbers_continue_into_the_workshop_rows(self) -> None:
        self.assertEqual(select.wallpaper("13"), Pick(13, "601", "601", "Waiting One", "", "waiting"))

    def test_six_to_eight_digits_are_refused_with_both_readings(self) -> None:
        both_nothing = ["  as a list number: nothing", "  as an id: nothing"]
        for word in ("123456", "12345678"):
            with self.subTest(word=word):
                self.refused(word, 3, f"{word} has 6 to 8 digits; a list number has at most 5 and a "
                             "Workshop id 9 or 10", both_nothing)
        self.refused("1234567", 3, "1234567 has 6 to 8 digits; a list number has at most 5 and a "
                     "Workshop id 9 or 10", ["  as a list number: nothing", "  as an id: 11 = Seven (1234567)"])

    def test_nine_or_more_digits_are_an_id(self) -> None:
        self.assertEqual(select.wallpaper("123456789").number, 9)
        self.assertEqual(select.wallpaper("1234567890").number, 12)
        self.refused("111111111", 1, "nothing matches 111111111")

    def test_nine_or_more_digits_never_match_a_title(self) -> None:
        with _one_more_row("303030303", "123456789"):
            self.assertEqual(select.wallpaper("123456789").ui_id, "123456789")
        with _one_more_row("303030303", "1234567890123"):
            self.refused("1234567890123", 1, "nothing matches 1234567890123")

    def test_other_words_are_an_exact_id_or_a_title_ignoring_case(self) -> None:
        self.assertEqual(select.wallpaper("hand_made").number, 8)
        self.assertEqual(select.wallpaper("NINE").ui_id, "123456789")
        self.refused("CAF\u00c9", 1, "CAF\u00c9 matches more than one wallpaper; nothing was changed",
                     ["  3 = Caf\u00e9 (201)", "  4 = Caf\u00e9 (202)"])
        self.refused("abc", 1, "abc matches more than one wallpaper; nothing was changed",
                     ["  1 = ABC (301)", "  2 = Alpha (abc)"])

    def test_non_ascii_digits_are_titles(self) -> None:
        for word in ("\u0660\u0667", "\uff10\uff17"):
            with self.subTest(word=word):
                self.refused(word, 1, f"nothing matches {word}")

    def test_aliases(self) -> None:
        self.assertEqual(select.wallpaper("@DEEP").ui_id, "401")
        self.refused("@twin", 1, "@twin matches more than one wallpaper; nothing was changed",
                     ["  6 = Echo (501)", "  7 = Foxtrot (502)"])
        self.refused("@", 3, "@ needs an alias after it")
        self.refused("@nobody", 1, "nothing matches @nobody")

    def test_an_empty_word_is_typed_wrong(self) -> None:
        self.refused("", 3, "an empty word names nothing")

    def test_a_preset_is_shown_as_its_base(self) -> None:
        self.assertEqual(select.wallpaper("preset").engine_id, "401")

    def test_several_words_resolve_all_or_nothing_and_once_each(self) -> None:
        self.assertEqual([p.ui_id for p in select.wallpapers(["nine", "123456789", "9", "5"])],
                         ["123456789", "401"])
        with self.assertRaises(PickError) as caught:
            select.wallpapers(["1", "2", "zzz"])
        self.assertEqual(caught.exception.message, "nothing matches zzz")

    def test_trashed_and_member_ids(self) -> None:
        self.assertEqual(select.wallpaper("987654321"), Pick(None, "987654321", "987654321", "Gone", "", "trashed"))
        self.assertEqual(select.wallpapers(["555555555"], member_of=["mix"]),
                         [Pick(None, "555555555", "555555555", "555555555", "", "member")])
        self.refused("555555555", 1, "nothing matches 555555555")

    def test_the_trash_domain_has_its_own_numbers(self) -> None:
        self.assertEqual(select.wallpaper("1", domain="trash"), Pick(1, "987654321", "987654321", "Gone", "", "trashed"))
        self.assertEqual(select.wallpaper("zulu", domain="trash").number, 2)
        self.refused("nine", 1, "nine is not in the trash", domain="trash")


class CurrentTest(unittest.TestCase):
    def pick(self, current, version_text=None, available=True) -> Pick:
        status, avail = _status(current, version_text, available)
        with status, avail:
            return select.wallpaper("current")

    def refused(self, code: int, message: str, current, version_text=None, available=True) -> None:
        with self.assertRaises(PickError) as caught:
            self.pick(current, version_text, available)
        self.assertEqual((caught.exception.code, caught.exception.message), (code, message))

    def test_on_screen_takes_that_rows_number(self) -> None:
        self.assertEqual(self.pick({"ui_id": "401", "id": "401", "title": "Deep Space"}).number, 5)

    def test_an_on_screen_id_with_no_row(self) -> None:
        self.assertEqual(self.pick({"ui_id": "", "id": "888888888", "title": "Elsewhere"}),
                         Pick(None, "888888888", "888888888", "Elsewhere", "", "screen"))

    def test_nothing_on_screen_away_and_unresponsive(self) -> None:
        self.refused(1, "nothing is on screen", {"ui_id": "", "id": "", "title": ""})
        self.refused(2, "the service is not running, so current names nothing", None, available=False)
        self.refused(1, "the service is not answering", None, available=True)

    def test_an_engine_from_another_build_is_refused(self) -> None:
        text = version.running_refusal({"version": "0.0.1"}, version.panel_stamp())
        self.refused(1, text, {"ui_id": "401", "id": "401", "title": ""}, version_text="0.0.1")

    def test_an_unreadable_version_file_refuses_with_its_message(self) -> None:
        missing = _ROOT / "no-VERSION"
        with mock.patch.object(version, "STAMP_FILE", missing):
            self.refused(1, f"the panel's version file {missing} is missing or empty; reinstall with bash "
                         "install.sh", {"ui_id": "401", "id": "401", "title": ""}, version_text="0.0.1")

    def test_current_wins_over_a_wallpaper_titled_current(self) -> None:
        with _one_more_row("303030303", "current"):
            self.assertEqual(self.pick({"ui_id": "401", "id": "401", "title": "Deep Space"}).ui_id, "401")
            self.assertEqual(select.wallpaper("303030303"), Pick(14, "303030303", "303030303", "current", "", "pool"))
            self.assertEqual(select.wallpaper("14").ui_id, "303030303")

    def test_a_trashed_wallpaper_on_screen_is_trashed(self) -> None:
        self.assertEqual(self.pick({"ui_id": "987654321", "id": "987654321", "title": "Gone"}),
                         Pick(None, "987654321", "987654321", "Gone", "", "trashed"))

    def test_a_preset_on_screen_is_the_preset_not_its_base(self) -> None:
        self.assertEqual(self.pick({"ui_id": "801", "id": "401", "title": "Deep Space"}),
                         Pick(10, "801", "401", "Preset", "", "pool"))


class PlaylistTest(unittest.TestCase):
    def refused(self, word: str, message: str, lines: list[str] | None = None, code: int = 1) -> None:
        with self.assertRaises(PickError) as caught:
            select.playlist(word)
        self.assertEqual((caught.exception.code, caught.exception.message), (code, message))
        if lines is not None:
            self.assertEqual(caught.exception.lines, lines)

    def test_rows_follow_the_store_order(self) -> None:
        self.assertEqual(select.playlist_rows(), [
            PlaylistPick(1, "two", "2"), PlaylistPick(2, "chill", "Chill Beats"), PlaylistPick(3, "mix", "Mix"),
            PlaylistPick(4, "night-a", "Night"), PlaylistPick(5, "night-b", "Night")])

    def test_by_number_and_by_name(self) -> None:
        self.assertEqual(select.playlist("3").slug, "mix")
        self.assertEqual(select.playlist("  cHILL   beats ").slug, "chill")

    def test_refusals(self) -> None:
        self.refused("2", "2 matches more than one playlist",
                     ["  2 = Chill Beats (chill.conf)", "  1 = 2 (two.conf)"])
        self.refused("night", "night matches more than one playlist",
                     ["  4 = Night (night-a.conf)", "  5 = Night (night-b.conf)"])
        self.refused("chill", "no playlist matches chill")
        self.refused("", "an empty word names nothing", code=3)


class OutputTest(unittest.TestCase):
    def test_pick_line(self) -> None:
        self.assertEqual(select.pick_line(select.wallpaper("9")), "9 = Nine (123456789)")
        self.assertEqual(select.pick_line(select.wallpaper("987654321")), "Gone (987654321)")

    def test_report_in_text_mode_and_as_json(self) -> None:
        with self.assertRaises(PickError) as caught:
            select.wallpaper("caf\u00e9")
        text = Context(False, io.StringIO(), io.StringIO(), None, False)
        self.assertEqual(select.report(text, caught.exception), 1)
        self.assertEqual(text.err.getvalue(), "lwe: caf\u00e9 matches more than one wallpaper; nothing was "
                         "changed\n  3 = Caf\u00e9 (201)\n  4 = Caf\u00e9 (202)\n")
        as_json = Context(True, io.StringIO(), io.StringIO(), None, False)
        self.assertEqual(select.report(as_json, caught.exception), 1)
        self.assertEqual(as_json.err.getvalue(),
                         '{"error":"caf\u00e9 matches more than one wallpaper; nothing was changed"}\n')


class QtFreeTest(unittest.TestCase):
    def test_a_poisoned_child_imports_the_catalog_and_select(self) -> None:
        scratch = Path(tempfile.mkdtemp(prefix="lwe-select-child-"))
        self.addCleanup(shutil.rmtree, scratch, True)
        poison = scratch / "poison" / "PySide6"
        poison.mkdir(parents=True)
        (poison / "__init__.py").write_text('raise ImportError("PySide6 is unavailable in this test")\n',
                                            encoding="utf-8")
        for sub in ("bin", "rt"):
            (scratch / sub).mkdir()
        env = {"HOME": str(scratch), "XDG_CONFIG_HOME": str(scratch / "c"), "XDG_STATE_HOME": str(scratch / "s"),
               "XDG_DATA_HOME": str(scratch / "d"), "XDG_RUNTIME_DIR": str(scratch / "rt"),
               "LWE_SOCKET": str(scratch / "rt" / "engine.sock"), "PATH": str(scratch / "bin"),
               "PYTHONPATH": os.pathsep.join([str(scratch / "poison"), str(SRC)]), "LWE_SANDBOX": "1",
               "PYTHONDONTWRITEBYTECODE": "1"}
        bite = subprocess.run([sys.executable, "-c", "import PySide6"], env=env, capture_output=True,
                              text=True, timeout=60)
        self.assertNotEqual(bite.returncode, 0)
        self.assertIn("ImportError", bite.stderr)
        child = subprocess.run([sys.executable, "-c", "import lwe_ui.library.catalog, lwe_ui.cli.select"],
                               env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(child.returncode, 0, child.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
