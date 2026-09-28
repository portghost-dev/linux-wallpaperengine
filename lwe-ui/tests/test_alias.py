"""A wallpaper's alias lives in its own wp/<id>.conf as ALIAS: a backup carries it there and
back, a materialized save, Load defaults and the preset wire keep it, an import drops one that
breaks a rule or collides and names it in the receipt, foreign.promote moves a kept-aside one
into its file, and storage/alias.py holds the rules and the index.

Run: PYTHONPATH=src python3 tests/test_alias.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "backups"


def _fresh() -> Path:
    """Point HOME and the XDG folders at a new scratch folder and seed the store there."""
    home = Path(tempfile.mkdtemp(prefix="lwe-alias-"))
    for key, sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"),
                     ("XDG_DATA_HOME", "d"), ("XDG_CACHE_HOME", "k")):
        os.environ[key] = str(home / sub) if sub else str(home)
    from lwe_ui.storage import paths, settings
    paths.ensure_dirs()
    settings.ensure_exists()
    return home


_BOOT = _fresh()

from lwe_ui.storage import backup, foreign, paths, settings, tier_a, wp  # noqa: E402

CHARSET = "an alias is 1 to 64 letters, digits, dots, dashes or underscores, with no spaces"


class AliasTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = _fresh()
        self.addCleanup(shutil.rmtree, self.home, True)

    def _conf(self, wid: str, text: str) -> None:
        paths.wp_file(wid).write_text(text, encoding="utf-8")

    def _raw(self, wid: str) -> dict:
        return tier_a.parse(paths.wp_file(wid).read_text(encoding="utf-8"))

    def _archive(self, members: dict[str, str]) -> str:
        """An export of this store with the named wp members' text replaced."""
        exported, archive = self.home / "export.lwebackup", self.home / "doctored.lwebackup"
        self.assertFalse(backup.export_to(exported)["errors"])
        with zipfile.ZipFile(exported) as zin, zipfile.ZipFile(archive, "w") as zout:
            for name in zin.namelist():
                zout.writestr(name, members[name] if name in members else zin.read(name))
        return str(archive)

    def test_a_backup_import_puts_the_alias_in_its_file(self) -> None:
        self._conf("111", "BG=111\nSPEED=2.5\n")
        archive = self._archive({"wp/111.conf": "BG=111\nSPEED=2.5\nALIAS=Night\n"})
        paths.wp_file("111").unlink()
        r = backup.import_from(archive)
        self.assertEqual(r["errors"], [])
        self.assertEqual(self._raw("111"), {"BG": "111", "SPEED": "2.5", "ALIAS": "Night"})
        self.assertNotIn("overrides", foreign.load(), "nothing is kept aside")
        again = self.home / "again.lwebackup"
        backup.export_to(again)
        with zipfile.ZipFile(again) as z:
            self.assertEqual(tier_a.parse(z.read("wp/111.conf").decode("utf-8")).get("ALIAS"), "Night",
                             "the next export carries it again")

    def test_a_materialized_save_keeps_a_set_alias_and_writes_no_empty_one(self) -> None:
        wp.save("111", {**wp.load("111"), "ALIAS": "Night"})
        self.assertEqual(self._raw("111").get("ALIAS"), "Night")
        wp.save("112", wp.load("112"))
        self.assertNotIn("ALIAS", self._raw("112"), "an empty alias is never written")

    def test_load_defaults_keeps_the_alias(self) -> None:
        from lwe_ui.wp_session import SESSION
        self._conf("111", "BG=111\nSPEED=2.5\nALIAS=Night\n")
        wp.update_set("111", SESSION.defaults_changes("111"))
        self.assertEqual(self._raw("111"), {"BG": "111", "ALIAS": "Night"})

    def test_the_preset_wire_keeps_the_alias(self) -> None:
        from lwe_ui.storage import importer
        wp.write_keys("444", {"BG": "/base/444", "TYPE": "scene"})
        self._conf("555", "BG=555\nMOUSE=true\nALIAS=Night\n")
        self.assertTrue(importer._wire_preset_conf("555", {"raw": {"preset": {}}}, "444", settings.load()))
        self.assertEqual(self._raw("555"), {"BG": "/base/444", "TYPE": "scene", "ALIAS": "Night"})

    def test_an_alias_that_collides_or_breaks_a_rule_is_dropped_with_a_receipt_line(self) -> None:
        wids = ("201", "202", "203", "204", "205")
        for wid in wids:
            self._conf(wid, f"BG={wid}\nSPEED=2.5\n")
        (Path(settings.load()["WALLPAPERS_DIR"]) / "moon").mkdir(parents=True)
        aliases = {"201": "night", "202": "Night", "203": "Dusk", "204": "12345", "205": "moon"}
        archive = self._archive({f"wp/{w}.conf": f"BG={w}\nSPEED=2.5\nALIAS={a}\n" for w, a in aliases.items()})
        for wid in wids:
            paths.wp_file(wid).unlink()
        self._conf("301", "BG=301\nALIAS=dusk\n")
        r = backup.import_from(archive)
        self.assertEqual(r["errors"], [])
        self.assertEqual({d["id"]: d["reason"] for d in r["dropped"] if d["kind"] == "override-key"},
                         {"202:ALIAS": "@Night is taken by 201", "203:ALIAS": "@Dusk is taken by 301",
                          "204:ALIAS": "an alias cannot be all digits",
                          "205:ALIAS": "moon is already a wallpaper id"})
        self.assertEqual(self._raw("201"), {"BG": "201", "SPEED": "2.5", "ALIAS": "night"})
        for wid in wids[1:]:
            self.assertEqual(self._raw(wid), {"BG": wid, "SPEED": "2.5"}, f"{wid}: the rest of the file")
        self.assertEqual(self._raw("301"), {"BG": "301", "ALIAS": "dusk"}, "the file the import leaves")

    def test_an_alias_equal_to_an_id_the_archive_brings_is_dropped(self) -> None:
        self._conf("301", "BG=301\n")
        self._conf("moon", "BG=moon\n")
        archive = self._archive({"wp/301.conf": "BG=301\nALIAS=moon\n"})
        for wid in ("301", "moon"):
            paths.wp_file(wid).unlink()
        r = backup.import_from(archive)
        self.assertEqual(r["errors"], [])
        self.assertIn({"kind": "override-key", "id": "301:ALIAS", "reason": "moon is already a wallpaper id"},
                      r["dropped"])
        self.assertEqual(self._raw("301"), {"BG": "301"})

    def test_a_file_the_archive_overwrites_no_longer_claims_its_alias(self) -> None:
        self._conf("401", "BG=401\nALIAS=Night\n")
        self._conf("402", "BG=402\n")
        archive = self._archive({"wp/401.conf": "BG=401\nALIAS=Day\n", "wp/402.conf": "BG=402\nALIAS=Night\n"})
        r = backup.import_from(archive)
        self.assertEqual((r["errors"], [d for d in r["dropped"] if d["kind"] == "override-key"]), ([], []))
        self.assertEqual((self._raw("401"), self._raw("402")),
                         ({"BG": "401", "ALIAS": "Day"}, {"BG": "402", "ALIAS": "Night"}))

    def test_the_restore_checks_each_alias_again_when_it_writes(self) -> None:
        self._conf("206", "BG=206\n")
        archive = self._archive({"wp/206.conf": "BG=206\nALIAS=star\n"})
        paths.wp_file("206").unlink()
        plan = backup.preflight(archive)
        self.assertEqual([d for d in plan["dropped"] if d["kind"] == "override-key"], [])
        self._conf("302", "BG=302\nALIAS=Star\n")
        r = backup.apply(plan)
        self.assertIn({"kind": "override-key", "id": "206:ALIAS", "reason": "@star is taken by 302"},
                      r["dropped"])
        self.assertEqual(self._raw("206"), {"BG": "206"})

    def test_the_rules_and_the_index(self) -> None:
        from lwe_ui.storage import alias
        s = settings.load()
        workshop = self.home / "workshop"
        (workshop / "sun").mkdir(parents=True)
        settings.save({**s, "WORKSHOP_DIR": str(workshop)})
        (Path(s["WALLPAPERS_DIR"]) / "moon").mkdir(parents=True)
        (paths.manual_dir() / "comet").mkdir(parents=True)
        self._conf("111", "BG=111\n")
        self._conf("nebula", "BG=nebula\n")
        self._conf("112", "BG=112\nALIAS=Night\n")
        self.assertIsNone(alias.check("Sky.2_b-c", "111"))
        self.assertIsNone(alias.check("x" * 64, "111"))
        for bad in ("", "has space", "x" * 65, "a/b", "café", "٠١"):
            self.assertEqual(alias.check(bad, "111"), CHARSET, repr(bad))
        self.assertEqual(alias.check("12345", "111"), "an alias cannot be all digits")
        for taken in ("nebula", "moon", "sun", "comet"):
            self.assertEqual(alias.check(taken, "111"), f"{taken} is already a wallpaper id")
        self.assertEqual(alias.check("NIGHT", "111"), "@NIGHT is taken by 112")
        self.assertIsNone(alias.check("night", "112"), "its own alias again, in any case")
        self.assertEqual(alias.index(), {"night": "112"})
        self._conf("113", "BG=113\nALIAS=NIGHT\n")
        self.assertEqual(alias.index(), {}, "an alias two files claim names neither")
        self.assertEqual(alias.check("night", "111"), "@night is taken by 112")

    def test_an_alias_two_files_carry_is_in_claims_with_both_and_not_in_index(self) -> None:
        from lwe_ui.storage import alias
        self._conf("113", "BG=113\nALIAS=NIGHT\n")
        self._conf("112", "BG=112\nALIAS=Night\n")
        self._conf("114", "BG=114\nALIAS=dawn\n")
        self.assertEqual(alias.claims(), {"night": ["112", "113"], "dawn": ["114"]})
        self.assertEqual(alias.index(), {"dawn": "114"})

    def test_promote_moves_a_kept_aside_alias_into_its_file(self) -> None:
        self._conf("111", "BG=111\n")
        foreign.save({"overrides": {"111": {"ALIAS": "Night"}}})
        foreign.promote()
        self.assertEqual(self._raw("111"), {"BG": "111", "ALIAS": "Night"})
        self.assertEqual(foreign.load(), {})

    def test_every_corpus_generation_restores_its_aliases(self) -> None:
        for archive in sorted(FIXTURES.glob("*.lwebackup")):
            record = json.loads(archive.with_suffix(".json").read_text(encoding="utf-8"))
            with self.subTest(archive=archive.name):
                self.addCleanup(shutil.rmtree, _fresh(), True)
                r = backup.import_from(str(archive))
                self.assertEqual(r["errors"], [])
                self.assertEqual([d for d in r["dropped"] if str(d.get("id", "")).endswith(":ALIAS")], [])
                for wid, entry in record["overrides"].items():
                    self.assertEqual(self._raw(wid).get("ALIAS"), entry["keys"].get("ALIAS"), wid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
