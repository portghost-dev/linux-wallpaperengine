"""cli/settings_table.py, cli/vocabulary.py and cli/report.py: one row per setting and
per-wallpaper word, their store places, words and owners; the derived active playlist, never written;
the receipt shapes; and the three data modules importing without the store or Qt.

The table and receipt checks run in this process with HOME and the XDG folders at scratch
(_cli_env.scratch_home); the import check and one `lwe help` run are child processes whose
environment is built from nothing (_cli_env.scratch_env).

Run: PYTHONPATH=src python3 tests/test_cli_settings_table.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import io
import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import _cli_env

ROOT = Path(tempfile.mkdtemp(prefix="lwe-cli-table-"))
HOME = _cli_env.scratch_home(ROOT / "inproc")
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "help"
BOTH = "global and per wallpaper"
HEADER_MODULES = {"cli/verbs/playlist.py", "cli/verbs/schedule.py", "cli/verbs/running.py",
                  "cli/verbs/settings.py", "cli/verbs/wallpaper.py", "cli/verbs/backup.py", "cli/verbs/debug.py"}
GLOBAL_WORDS = {
    "order": "static", "interval": "20m", "playlist": "ocean", "volume": "40", "mute": "on",
    "audioreactive": "off", "mouse": "on", "parallax": "off", "audioreactivedefault": "on",
    "mousedefault": "off", "parallaxdefault": "on", "automute": "off", "particles": "on", "fps": "30",
    "speed": "2.5", "scaling": "fill", "edge": "tile", "fullscreen": "keep", "layer": "top", "resclamp": "1.5",
    "effectclamp": "0.5", "texturecache": "off", "texturedetail": "full", "videodecode": "software",
    "reviewrequired": "on", "storage": "reference", "detect": "timer", "detectevery": "90s",
    "libraryfolder": "/srv/wallpapers", "workshopfolder": "/srv/workshop", "steamfolder": "/srv/steam",
    "assetsfolder": "/srv/assets", "lightdimming": "16", "lightfalloff": "2", "audiogain": "0.1",
    "audiosmoothing": "90", "watchdog": "5m", "color": "1.5 0.5 2 90",
}
WALLPAPER_WORDS = {
    "zoom": "1.25", "panx": "-0.5", "pany": "0.5", "brightness": "2", "contrast": "0.5", "saturation": "1",
    "hue": "-90", "fullscreen": "off", "property": "blue", "hide": "12", "unhide": "12", "alias": "sea",
    "volume": "40", "automute": "on", "speed": "0.5", "scaling": "fit", "edge": "blank", "resclamp": "2",
    "effectclamp": "0", "texturecache": "on", "texturedetail": "auto", "lightdimming": "0.7",
    "lightfalloff": "2.6", "audiogain": "3",
}


class SettingsTableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui.cli import report, settings_table, values, vocabulary
        cls.report, cls.table, cls.values, cls.vocabulary = report, settings_table, values, vocabulary

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(ROOT, True)

    def rows(self, form):
        return [row for row in self.table.ROWS if row.form == form]

    def test_every_setting_but_socket_has_one_global_row(self) -> None:
        names = [row["name"] for row in self.vocabulary.SETTINGS if row["name"] != "socket"]
        self.assertEqual(len(names), 38)
        self.assertEqual(sorted(row.name for row in self.rows(self.table.GLOBAL)), sorted(names))
        self.assertNotIn("socket", self.table.BY_NAME)

    def test_every_per_wallpaper_word_has_one_row(self) -> None:
        words = [word for row in self.vocabulary.PER_WALLPAPER for word in row["name"].split(" / ")]
        self.assertEqual(len(words), 12)
        own = [row.name for row in self.rows(self.table.WALLPAPER) if row.scope == self.table.PER_WALLPAPER_SCOPE]
        self.assertEqual(sorted(own), sorted(words))

    def test_the_twelve_both_scope_settings_have_one_per_wallpaper_form(self) -> None:
        both = sorted(row["name"] for row in self.vocabulary.SETTINGS if row["scope"] == BOTH)
        self.assertEqual(len(both), 12)
        forms = [row for row in self.rows(self.table.WALLPAPER) if row.scope == BOTH]
        self.assertEqual(sorted(row.name for row in forms), both)
        for row in forms:
            with self.subTest(name=row.name):
                self.assertEqual((row.place, row.key), (self.table.WALLPAPER_FILE, self.table.BY_NAME[row.name].wp_key))

    def test_a_row_has_a_per_wallpaper_key_exactly_when_its_scope_is_both(self) -> None:
        for row in self.table.ROWS:
            with self.subTest(name=row.name, form=row.form):
                self.assertEqual(row.wp_key is not None, row.scope == BOTH)

    def test_each_owner_is_a_header_module_or_objects_for_hide_and_unhide(self) -> None:
        for row in self.table.ROWS:
            with self.subTest(name=row.name, form=row.form):
                if row.name in ("hide", "unhide"):
                    self.assertEqual(row.owner, "cli/verbs/objects.py")
                else:
                    self.assertIn(row.owner, HEADER_MODULES)
        by = self.table.BY_NAME
        self.assertEqual({by[n].owner for n in ("order", "interval", "playlist")}, {"cli/verbs/playlist.py"})
        for name in ("audioreactivedefault", "mousedefault", "parallaxdefault"):
            with self.subTest(config=name):
                self.assertEqual((by[name].owner, by[name].config_only), ("cli/verbs/settings.py", True))
        self.assertEqual([row.name for row in self.table.ROWS if row.config_only],
                         ["audioreactivedefault", "mousedefault", "parallaxdefault"])

    def test_store_places(self) -> None:
        by = self.table.BY_NAME
        t = self.table
        self.assertEqual((by["resclamp"].place, by["resclamp"].key), (t.SETTINGS_FILE, "SSFACTOR"))
        self.assertEqual((by["effectclamp"].place, by["effectclamp"].key), (t.SETTINGS_FILE, "CLAMPCOMPOSITES"))
        self.assertEqual({(by[n].place, by[n].key) for n in ("order", "interval")},
                         {(t.PLAYLIST_FILE, "MODE"), (t.PLAYLIST_FILE, "INTERVAL")})
        self.assertEqual((by["watchdog"].place, by["watchdog"].key), (t.ENGINE_ENV, "LWE_DEADMAN"))
        self.assertEqual((by["color"].place, by["color"].key), (t.ENGINE_ENV, "LWE_CC"))
        self.assertEqual((by["audiosmoothing"].place, by["audiosmoothing"].key), (t.STATUS, "audio_smooth"))
        self.assertEqual({row.place for row in self.rows(t.WALLPAPER)}, {t.WALLPAPER_FILE})

    def test_words_type_back_through_parse_and_format(self) -> None:
        for form, words, index in ((self.table.GLOBAL, GLOBAL_WORDS, self.table.BY_NAME),
                                   (self.table.WALLPAPER, WALLPAPER_WORDS, self.table.WALLPAPER_BY_NAME)):
            self.assertEqual(sorted(words), sorted(index))
            for name, word in words.items():
                with self.subTest(form=form, name=name):
                    row = index[name]
                    self.assertEqual(row.format(row.parse(word)), word)

    def test_stored_values_and_their_words(self) -> None:
        by, wp = self.table.BY_NAME, self.table.WALLPAPER_BY_NAME
        self.assertIs(by["audioreactive"].parse("on"), False)
        self.assertEqual(by["audioreactive"].format(False), "on")
        self.assertEqual([wp["texturecache"].format(v) for v in ("true", "false", True, False)], ["on", "off", "on", "off"])
        self.assertEqual([by["audioreactive"].format(v) for v in ("false", "true")], ["on", "off"])
        self.assertEqual(by["mute"].parse("toggle"), self.table.TOGGLE)
        with self.assertRaises(self.values.UsageError):
            by["mousedefault"].parse("toggle")
        self.assertEqual([by["edge"].parse(w) for w in ("extend", "blank", "tile")], ["clamp", "border", "repeat"])
        self.assertEqual((by["edge"].format(""), wp["edge"].format("")), ("extend", ""))
        self.assertEqual((by["videodecode"].parse("software"), by["detect"].parse("timer")), ("no", "interval"))
        self.assertEqual((by["fullscreen"].parse("keep"), by["interval"].parse("20")), ("off", 1200))
        self.assertEqual(by["color"].parse("1 1 1 180"), (1.0, 1.0, 1.0, math.pi))
        self.assertEqual((wp["hue"].parse("180"), wp["hue"].format(math.pi)), (math.pi, "180"))
        self.assertEqual((wp["fullscreen"].parse("inherit"), wp["fullscreen"].format("")), ("", "inherit"))
        self.assertEqual(wp["brightness"].field, 0)
        self.assertEqual(wp["hue"].field, 3)

    def test_speed_takes_0_as_the_freeze_and_refuses_below_0_1(self) -> None:
        speed = self.table.BY_NAME["speed"]
        self.assertEqual([speed.parse(w) for w in ("0", "0.1", "10")], [0.0, 0.1, 10.0])
        for word in ("0.05", "0.09"):
            with self.subTest(word=word):
                with self.assertRaises(self.values.UsageError) as caught:
                    speed.parse(word)
                self.assertEqual(f"speed {caught.exception}", "speed takes 0.1 to 10; use 0 to freeze")
        with self.assertRaises(self.values.UsageError):
            speed.parse("10.5")
        with self.assertRaises(self.values.UsageError):
            self.table.WALLPAPER_BY_NAME["speed"].parse("0")

    def test_each_ranged_row_takes_its_edges_and_refuses_past_them(self) -> None:
        by, wp = self.table.BY_NAME, self.table.WALLPAPER_BY_NAME
        edges = (
            (by["fps"], ("1", "480"), ("0", "481")),
            (by["interval"], ("15s", "9999m"), ("14s", "10000m")),
            (by["detectevery"], ("15s", "24h"), ("14s", "86401s")),
            (by["watchdog"], ("0", "24h"), ("86401s",)),
            (by["lightdimming"], ("0.01", "1000"), ("0.009", "1000.1")),
            (by["lightfalloff"], ("0.5", "6"), ("0.49", "6.01")),
            (by["audiogain"], ("0.1", "20"), ("0.09", "20.1")),
            (by["volume"], ("0", "128"), ("129",)),
            (wp["volume"], ("0", "128"), ("129",)),
            (wp["speed"], ("0.1", "10"), ("0.09", "10.1")),
            (wp["zoom"], ("1", "2"), ("0.99", "2.01")),
            (wp["panx"], ("-1", "1"), ("-1.01", "1.01")),
            (wp["pany"], ("-1", "1"), ("-1.01", "1.01")),
            (wp["brightness"], ("0", "4"), ("-0.01", "4.01")),
            (wp["contrast"], ("0", "4"), ("-0.01", "4.01")),
            (wp["saturation"], ("0", "4"), ("-0.01", "4.01")),
        )
        for row, taken, refused in edges:
            for word in taken:
                with self.subTest(row=row.name, form=row.form, word=word):
                    row.parse(word)
            for word in refused:
                with self.subTest(row=row.name, form=row.form, word=word):
                    with self.assertRaises(self.values.UsageError):
                        row.parse(word)

    def test_volume_takes_0_to_128_or_a_step(self) -> None:
        volume = self.table.BY_NAME["volume"]
        self.assertEqual((volume.parse("128"), type(volume.parse("40"))), (128, int))
        self.assertIsInstance(volume.parse("+5"), self.table.Step)
        self.assertEqual((volume.parse("+5"), volume.parse("-5")), (5, -5))
        with self.assertRaises(self.values.UsageError) as caught:
            volume.parse("129")
        self.assertEqual(str(caught.exception), "takes a whole number from 0 to 128, +N or -N; got 129")
        with self.assertRaises(self.values.UsageError) as caught:
            self.table.WALLPAPER_BY_NAME["volume"].parse("+5")
        self.assertEqual(str(caught.exception), "takes a whole number from 0 to 128; got +5")

    def test_receipts(self) -> None:
        r = self.report
        receipt = r.receipt("volume", "40", True, "now", r.APPLIED)
        self.assertEqual(tuple(receipt), ("setting", "value", "saved", "outcome", "applies", "reason"))
        self.assertEqual(r.RECEIPT_KEYS, tuple(receipt))
        out = io.StringIO()
        r.emit(SimpleNamespace(json=True, out=out), r.receipt("edge", "tile", False, "next wallpaper", r.REFUSED,
                                                               "busy"))
        self.assertEqual(out.getvalue(), '{"setting":"edge","value":"tile","saved":false,"outcome":"refused",'
                                         '"applies":"next wallpaper","reason":"busy"}\n')
        self.assertEqual(list(json.loads(out.getvalue())), list(r.RECEIPT_KEYS))
        chances = r.OPPORTUNITIES
        self.assertEqual(chances, "It can apply the next time the panel window opens or polls, an lwe command saves "
                                  "a setting the engine uses, lwe reload or a backup import runs, or lwe service "
                                  "start or restart starts the engine.")
        lines = {
            ("volume", "40", True, "now", r.APPLIED, ""): "volume 40: saved; applies now.",
            ("edge", "tile", False, "next wallpaper", r.APPLIED, ""):
                "edge tile: unchanged; applies to the next wallpaper.",
            ("volume", "40", True, "now", r.PENDING, ""):
                f"volume 40: saved; the service is not running or is busy, so it is not applied yet. {chances}",
            ("volume", "40", True, "now", r.REFUSED, "volume out of range"):
                f"volume 40: saved, but the engine refused it: volume out of range. {chances}",
            ("fps", "30", True, "now", r.UNCERTAIN, ""):
                f"fps 30: saved; the engine did not answer in time, so it may have applied. {chances}",
            ("layer", "top", True, "restart", None, ""):
                "layer top: saved; takes effect at the next service restart (lwe service restart applies it).",
            ("storage", "copy", True, "panel", None, ""): "storage copy: saved; the panel reads it at its next scan.",
            ("detect", "timer", True, "panel start", None, ""):
                "detect timer: saved; a running panel picks it up when it next starts.",
        }
        for args, line in lines.items():
            with self.subTest(args=args):
                self.assertEqual(r.text(r.receipt(*args)), line)
        out = io.StringIO()
        r.emit(SimpleNamespace(json=False, out=out), r.receipt("volume", "40", True, "now", r.APPLIED))
        self.assertEqual(out.getvalue(), "volume 40: saved; applies now.\n")
        for applies in ("now", "next wallpaper"):
            with self.subTest(applies=applies):
                with self.assertRaises(ValueError):
                    r.receipt("volume", "40", False, applies)

    def test_every_reach_has_a_line_and_a_change_with_no_engine_side_claims_nothing_about_the_engine(self) -> None:
        r = self.report
        for row in self.table.ROWS:
            with self.subTest(name=row.name, form=row.form):
                outcome = r.APPLIED if row.reach in (self.table.NOW, self.table.NEXT_WALLPAPER) else None
                self.assertTrue(r.text(r.receipt(row.name, "x", True, row.reach, outcome)))
        line = r.text(r.receipt("alias", "field", True, self.table.NO_ENGINE))
        self.assertEqual(line, "alias field: saved; nothing more is needed.")
        self.assertNotIn("engine", line)
        self.assertNotIn("appl", line)

    def test_derived_active_playlist_follows_the_engine_only_while_its_schedule_binds_an_existing_file(self) -> None:
        config = HOME / ".config" / "lwe"
        (config / "playlists").mkdir(parents=True)
        for slug in ("day", "night"):
            (config / "playlists" / f"{slug}.conf").write_text(f"NAME={slug}\nMODE=shuffle\nINTERVAL=900\n",
                                                                encoding="utf-8")
        (config / "settings.conf").write_text("ACTIVE_PLAYLIST=day\n", encoding="utf-8")

        def bound(slug, enabled=True):
            return {"schedule": {"enabled": enabled}, "lanes": [{"id": "all", "playlist": slug}]}

        def files():
            return {p: p.read_bytes() for p in sorted(config.rglob("*")) if p.is_file()}

        before = files()
        derive = self.table.derived_active_playlist
        self.assertEqual(derive(bound("night")), ("night", "engine"))
        self.assertEqual(derive(bound("gone")), ("day", "saved"))
        self.assertEqual(derive(bound("../settings")), ("day", "saved"))
        self.assertEqual(derive(bound("night", enabled=False)), ("day", "saved"))
        self.assertEqual(derive(None), ("day", "saved"))
        self.assertEqual(files(), before)
        for slug in ("day", "night"):
            (config / "playlists" / f"{slug}.conf").unlink()
        self.assertEqual(derive(bound("night")), (None, "saved"))
        self.assertEqual(derive(None), (None, "saved"))

    def test_the_data_modules_import_without_the_store_or_qt(self) -> None:
        env = _cli_env.scratch_env(ROOT / "child")
        poison = subprocess.run([sys.executable, "-c", "import PySide6"], env=env, capture_output=True,
                                encoding="utf-8", timeout=60)
        self.assertNotEqual(poison.returncode, 0)
        self.assertIn("ImportError: PySide6 is unavailable in this test", poison.stderr)
        code = ("import sys\n"
                "import lwe_ui.cli.vocabulary, lwe_ui.cli.settings_table, lwe_ui.cli.values\n"
                "print(sorted(m for m in sys.modules if m.startswith(('lwe_ui.storage', 'PySide6'))))\n")
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, encoding="utf-8",
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "[]\n")

    def test_run_lwe_hands_over_with_the_panel_stamp(self) -> None:
        env = _cli_env.scratch_env(ROOT / "run")
        self.assertEqual(_cli_env.run_lwe(["help"], env, str(ROOT / "run")),
                         (0, (FIXTURES / "main.txt").read_text(encoding="utf-8"), ""))


if __name__ == "__main__":
    unittest.main()
