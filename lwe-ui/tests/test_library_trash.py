"""library/actions.py's trash chain and playlist membership, and the window on the same chain.

With push.save_change replaced in actions' namespace by a recorder, a trash writes the deleted record, tags
the id bad and takes it out of every playlist that holds it: a (active) and b (scheduled) both lose
it. It clears the missing-base mark, and an id already bad is left alone. The tail purges only the
cache rows the id owns, removes LWE's copy, never follows a symlink at the library slot and leaves a
reference import's Steam folder; the dependents of a base are counted. The recorder sees one change
naming exactly the changed playlists. A playlist changed between the read and the lock is re-read
inside it, and a change no read expected is followed by sync_all. The membership changes append the
missing ids at the end in argument order, never touch another playlist, and take out only present
ids. The window's trash (WorkshopBridge.trashItem through an offscreen Backend) and actions.trash
plus trash_tail leave identical stores, the window's trash goes through actions.trash, its outcome
reaches _note and a busy store is logged.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 tests/test_library_trash.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.TemporaryDirectory(prefix="lwe-trash-")
_ROOT = Path(_TMP.name)
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = str(_ROOT / _sub) if _sub else str(_ROOT)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lwe_ui import models, texcomp  # noqa: E402
from lwe_ui.engine import marker, push  # noqa: E402
from lwe_ui.library import actions  # noqa: E402
from lwe_ui.storage import lock, meta, paths, playlists, records, settings, tags, wp  # noqa: E402

_HOMES: list[tempfile.TemporaryDirectory] = []


def _home() -> Path:
    """A fresh store: settings with LIB and WORKSHOP, playlists a (active, 111 222), b (scheduled,
    111 333) and c (444)."""
    tmp = tempfile.TemporaryDirectory(prefix="lwe-trash-home-")
    _HOMES.append(tmp)
    home = Path(tmp.name)
    for key, sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
        os.environ[key] = str(home / sub) if sub else str(home)
    paths.ensure_dirs()
    settings.ensure_exists()
    settings.update({"WALLPAPERS_DIR": str(home / "lib"), "WORKSHOP_DIR": str(home / "workshop"),
                     "ACTIVE_PLAYLIST": "a", "SCHEDULE": "07:00=a;20:00=b"})
    for slug, name, members in (("a", "Chill", "111 222"), ("b", "Night", "111 333"), ("c", "Other", "444")):
        playlists.save(slug, {"NAME": name, "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min", "MEMBERS": members})
    (home / "lib").mkdir()
    (home / "workshop").mkdir()
    return home


def _scene(folder: Path, title: str, extra: dict | None = None) -> None:
    folder.mkdir(parents=True)
    (folder / "project.json").write_text(json.dumps({"title": title, "type": "scene", "file": "scene.json",
                                                     **(extra or {})}), encoding="utf-8")
    (folder / "scene.json").write_text("{}", encoding="utf-8")


def _state(wid: str) -> str | None:
    return next((r["state"] for r in tags.load() if r["id"] == wid), None)


class Recorder:
    """The change runner's entry as actions calls it: save_change runs for real (the store locks, the
    marker and the write) and is recorded; deliver and sync_all record their calls and answer
    applied, sending nothing."""

    def __init__(self) -> None:
        self.changes: list[tuple] = []
        self.delivered: list[tuple] = []
        self.synced: list[str] = []

    def save_change(self, locks, write, rows, **kwargs):
        self.changes.append((tuple(locks), list(rows), kwargs))
        return push.save_change(locks, write, rows, **kwargs)

    def deliver(self, ticket):
        self.delivered.append(ticket.rows)
        return push.Outcome("applied")

    def sync_all(self, run, *args, **kwargs):
        self.synced.append(run)
        return push.Outcome("applied")


class ChainTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = _home()
        self.rec = Recorder()
        patcher = mock.patch.object(actions, "push", self.rec)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_trash_records_tags_and_leaves_every_playlist_in_one_change(self) -> None:
        meta.update("111", {"depMissing": True, "depWid": "999"})
        results, outcome = actions.trash([("111", "Alpha")], comment="too bright")
        self.assertEqual(outcome, push.Outcome("applied"))
        self.assertEqual([(r["id"], r["already"], r["left"]) for r in results],
                         [("111", False, ["Chill", "Night"])])
        head = records.head("111")
        self.assertEqual((head["action"], head["where"], head["initiator"], head["comment"]),
                         ("deleted", "library", "human", "too bright"))
        self.assertEqual(_state("111"), "bad")
        self.assertEqual((playlists.members("a"), playlists.members("b"), playlists.members("c")),
                         (["222"], ["333"], ["444"]))
        self.assertIs(meta.get("111").get("depMissing"), False)
        self.assertEqual(len(self.rec.changes), 1)
        locks, rows, kwargs = self.rec.changes[0]
        self.assertEqual((locks, kwargs["run"]), (("playlists", "tags", "meta", "records"), "window"))
        self.assertEqual(self.rec.delivered, [(("members", "a"), ("members", "b"))])

    def test_without_a_record_and_for_an_id_already_bad(self) -> None:
        tags.set_state("222", "Bravo", "bad")
        results, _outcome = actions.trash([("222", "Bravo"), ("333", "Charlie")], record=False, run="command")
        self.assertEqual([(r["id"], r["already"], r["left"]) for r in results],
                         [("222", True, []), ("333", False, ["Night"])])
        self.assertEqual(playlists.members("a"), ["111", "222"])
        self.assertFalse(paths.record_file("333").exists())
        self.assertEqual(self.rec.changes[0][2]["run"], "command")
        self.assertEqual(self.rec.delivered, [(("members", "b"),)])

    def test_the_tail_purges_owned_cache_rows_and_removes_only_a_real_copy(self) -> None:
        cache = Path(texcomp.CACHE)
        cache.mkdir(parents=True, exist_ok=True)
        for key, owner in (("own", "555"), ("other", "888")):
            (cache / f"{key}.bc").write_bytes(b"x")
            (cache / f"{key}.meta").write_text(json.dumps({"wallpaper": owner}), encoding="utf-8")
        _scene(self.home / "lib" / "555", "Echo")
        outside = self.home / "elsewhere"
        outside.mkdir()
        (outside / "keep").write_text("keep", encoding="utf-8")
        (self.home / "lib" / "666").symlink_to(outside)
        _scene(self.home / "workshop" / "777", "Golf")
        wp.write_keys("777", {"BG": str(self.home / "workshop" / "777")})
        results, _outcome = actions.trash([("555", "Echo"), ("666", "Foxtrot"), ("777", "Golf")])
        self.assertEqual([r["copy_deletable"] for r in results], [True, False, False])
        self.assertEqual([actions.trash_tail(w) for w in ("555", "666", "777")], [True, False, False])
        self.assertFalse((self.home / "lib" / "555").exists())
        self.assertEqual(sorted(p.name for p in cache.iterdir() if p.stem in ("own", "other")),
                         ["other.bc", "other.meta"])
        self.assertTrue((self.home / "lib" / "666").is_symlink())
        self.assertTrue((outside / "keep").exists())
        self.assertTrue((self.home / "workshop" / "777" / "project.json").exists())

    def test_the_dependents_of_a_base_are_counted(self) -> None:
        _scene(self.home / "workshop" / "800", "Base")
        _scene(self.home / "workshop" / "801", "Preset one", {"dependency": "800"})
        _scene(self.home / "workshop" / "802", "Preset two", {"dependency": ["800", "900"]})
        results, _outcome = actions.trash([("800", "Base"), ("801", "Preset one")])
        self.assertEqual([r["dependents"] for r in results], [2, 0])

    def test_a_playlist_changed_between_the_read_and_the_lock_is_re_read_inside_it(self) -> None:
        holding = actions._holding

        def raced(wids):
            found = holding(wids)
            playlists.update("c", {"MEMBERS": "444 111"})
            return found
        with mock.patch.object(actions, "_holding", raced):
            results, _outcome = actions.trash([("111", "Alpha")])
        self.assertEqual(results[0]["left"], ["Chill", "Night", "Other"])
        self.assertEqual(playlists.members("c"), ["444"])
        self.assertEqual(self.rec.changes[0][1], [("members", "a"), ("members", "b")])
        self.assertEqual(self.rec.delivered, [(("members", "a"), ("members", "b"), ("members", "c"))])

    def test_a_change_no_read_expected_is_followed_by_sync_all(self) -> None:
        def raced(wids):
            playlists.update("c", {"MEMBERS": "444 999"})
            return []
        with mock.patch.object(actions, "_holding", raced):
            results, outcome = actions.trash([("999", "Zulu")], run="command")
        self.assertEqual((results[0]["left"], outcome), (["Other"], push.Outcome("applied")))
        self.assertEqual((self.rec.changes[0][1], self.rec.delivered, self.rec.synced),
                         ([("none", None)], [], ["command"]))
        self.assertEqual(playlists.members("c"), ["444"])

    def test_an_id_in_no_playlist_is_a_change_with_no_engine_side(self) -> None:
        results, _outcome = actions.trash([("999", "Zulu")])
        self.assertEqual((results[0]["left"], _state("999")), ([], "bad"))
        self.assertEqual((self.rec.changes[0][1], self.rec.delivered, self.rec.synced),
                         ([("none", None)], [(("none", None),)], []))
        self.assertIsNone(marker.read()["generation"])

    def test_membership_changes_append_at_the_end_and_take_out_only_present_ids(self) -> None:
        pairs, _outcome = actions.add_to_playlists(["c", "a"], ["555", "111", "666"], run="command")
        self.assertEqual((playlists.members("c"), playlists.members("a"), playlists.members("b")),
                         (["444", "555", "111", "666"], ["111", "222", "555", "666"], ["111", "333"]))
        self.assertEqual([(p["slug"], p["id"], p["result"]) for p in pairs],
                         [("c", "555", "appended"), ("c", "111", "appended"), ("c", "666", "appended"),
                          ("a", "555", "appended"), ("a", "111", "already"), ("a", "666", "appended")])
        self.assertEqual(pairs[0]["name"], "Other")
        self.assertEqual(self.rec.delivered[-1], (("members", "c"), ("members", "a")))
        pairs, _outcome = actions.remove_from_playlists(["b", "c"], ["333", "777"])
        self.assertEqual((playlists.members("b"), playlists.members("c")), (["111"], ["444", "555", "111", "666"]))
        self.assertEqual([(p["slug"], p["id"], p["result"]) for p in pairs],
                         [("b", "333", "removed"), ("b", "777", "absent"), ("c", "333", "absent"),
                          ("c", "777", "absent")])
        self.assertEqual(self.rec.changes[-1][1], [("members", "b")])
        self.assertEqual(self.rec.delivered[-1], (("members", "b"),))
        pairs, _outcome = actions.add_to_playlists(["a"], ["111"])
        self.assertEqual((pairs[0]["result"], self.rec.changes[-1][1], self.rec.delivered[-1]),
                         ("already", [("none", None)], (("none", None),)))


class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtCore import QCoreApplication
        cls.app = QCoreApplication.instance() or QCoreApplication(["t"])

    def _chain(self, window: bool) -> Path:
        home = _home()
        _scene(home / "lib" / "111", "Alpha")
        tags.set_state("111", "Alpha", "good")
        meta.update("111", {"depMissing": True})
        if window:
            from lwe_ui.workshop import WorkshopBridge
            backend = models.Backend()
            WorkshopBridge(backend, None).trashItem("111")
            deadline = time.monotonic() + 10
            while (home / "lib" / "111").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
        else:
            actions.trash([("111", "Alpha")], record=True)
            actions.trash_tail("111")
        return home

    def test_the_window_and_actions_leave_identical_stores(self) -> None:
        stores = []
        for window in (True, False):
            home = self._chain(window)
            files = {str(p.relative_to(home / "c")): p.read_bytes()
                     for p in sorted((home / "c" / "lwe").rglob("*"))
                     if p.is_file() and (p.name in ("tags.csv", "meta.json") or p.parent.name == "playlists")}
            events = [{k: v for k, v in e.items() if k != "when"} for e in records.read("111")]
            stores.append((files, events, (home / "lib" / "111").exists()))
        self.assertEqual(stores[0], stores[1])
        files, events, copy = stores[0]
        self.assertEqual(sorted(files), ["lwe/meta.json", "lwe/playlists/a.conf", "lwe/playlists/b.conf",
                                         "lwe/playlists/c.conf", "lwe/tags.csv"])
        self.assertEqual(([e["action"] for e in events], copy), (["deleted"], False))
        self.assertNotIn(b"111", files["lwe/playlists/b.conf"].split(b"MEMBERS=")[1])

    def test_the_window_trash_goes_through_actions_and_its_outcome_reaches_note(self) -> None:
        _home()
        backend = models.Backend()
        clocks: list[tuple] = []
        backend.rotationClock.connect(lambda ms, iv: clocks.append((ms, iv)))
        outcome = push.Outcome("applied", clock=(5000, 60))
        with mock.patch.object(actions, "trash", return_value=([], outcome)) as trash:
            backend.trashWallpaper("111")
        trash.assert_called_once_with([("111", "111")], record=False, run="window")
        self.assertEqual(clocks, [(5000, 60)])
        with mock.patch.object(actions, "trash", side_effect=lock.StoreBusy("busy")), \
                self.assertLogs("lwe_ui.models", "WARNING") as logs, \
                mock.patch.object(backend, "refresh") as refresh:
            backend.trashWallpaper("111")
        self.assertEqual(logs.output, ["WARNING:lwe_ui.models:trash not saved: busy"])
        refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
