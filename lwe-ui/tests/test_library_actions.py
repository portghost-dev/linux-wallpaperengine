"""library/actions.py, and the window on the same chain.

approve sets the tags state good and, given an event, appends it to the record; without one it
writes no record. untrash appends a bypassed event and drops the tags row, whether or not a folder
still holds the files. approve waits while another thread holds the records lock. The window's
approveReview goes through approve, sets good and calls nothing in api_client. The trash manager's
import still refuses an item whose Steam folder is gone and otherwise goes through untrash. models'
library_ids, _scan_dir_ids and _is_present are the catalog's.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 tests/test_library_actions.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import inspect
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.TemporaryDirectory(prefix="lwe-actions-")
_ROOT = Path(_TMP.name)
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = str(_ROOT / _sub) if _sub else str(_ROOT)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lwe_ui import api_client, models  # noqa: E402
from lwe_ui.library import actions, catalog  # noqa: E402
from lwe_ui.storage import lock, paths, records, settings, tags, wizard  # noqa: E402

LIB = _ROOT / "lib"
WORKSHOP = _ROOT / "workshop"


def setUpModule() -> None:
    paths.ensure_dirs()
    settings.ensure_exists()
    settings.save({**settings.load(), "WALLPAPERS_DIR": str(LIB), "WORKSHOP_DIR": str(WORKSHOP)})
    LIB.mkdir()
    WORKSHOP.mkdir()


def _state(wid: str) -> str | None:
    return next((r["state"] for r in tags.load() if r["id"] == wid), None)


def _trashed(wid: str, title: str) -> None:
    tags.set_state(wid, title, "bad")
    records.append(wid, records.make_event("deleted", where="library", initiator="human"))


def _folder(root: Path, wid: str, title: str) -> None:
    (root / wid).mkdir()
    (root / wid / "project.json").write_text(json.dumps({"title": title, "type": "scene", "file": "scene.json"}),
                                             encoding="utf-8")


class ActionsTest(unittest.TestCase):
    def test_approve_with_an_event_writes_the_record_and_good(self) -> None:
        tags.set_state("1300000001", "Alpha", "review")
        event = wizard.approved_untested(where="workshop")
        actions.approve("1300000001", "Alpha", event)
        self.assertEqual(_state("1300000001"), "good")
        self.assertEqual(records.read("1300000001"), [event])

    def test_approve_without_an_event_writes_no_record(self) -> None:
        tags.set_state("1300000002", "Bravo", "review")
        actions.approve("1300000002", "Bravo")
        self.assertEqual(_state("1300000002"), "good")
        self.assertFalse(paths.record_file("1300000002").exists())

    def test_untrash_with_files(self) -> None:
        _folder(WORKSHOP, "1300000003", "Charlie")
        _trashed("1300000003", "Charlie")
        actions.untrash("1300000003")
        self.assertNotIn("1300000003", tags.known_ids())
        head = records.head("1300000003")
        self.assertEqual((head["action"], head["where"], head["initiator"]), ("bypassed", "workshop", "human"))
        self.assertEqual(len(records.read("1300000003")), 2)

    def test_untrash_without_files(self) -> None:
        _trashed("1300000004", "Delta")
        actions.untrash("1300000004")
        self.assertNotIn("1300000004", tags.known_ids())
        self.assertEqual(records.head("1300000004")["action"], "bypassed")

    def test_approve_waits_while_another_thread_holds_the_records_lock(self) -> None:
        tags.set_state("1300000005", "Echo", "review")
        held, release, done = threading.Event(), threading.Event(), threading.Event()

        def holder() -> None:
            with lock.held("records"):
                held.set()
                release.wait(5)

        def approver() -> None:
            actions.approve("1300000005", "Echo", wizard.approved_untested(where="workshop"))
            done.set()

        hold = threading.Thread(target=holder)
        hold.start()
        self.assertTrue(held.wait(5))
        work = threading.Thread(target=approver)
        work.start()
        finished_while_held = done.wait(0.5)
        seen_while_held = (_state("1300000005"), paths.record_file("1300000005").exists())
        release.set()
        hold.join(5)
        work.join(5)
        self.assertFalse(finished_while_held)
        self.assertEqual(seen_while_held, ("review", False))
        self.assertTrue(done.is_set())
        self.assertEqual(_state("1300000005"), "good")
        self.assertEqual(records.head("1300000005")["action"], "approved")

    def test_models_library_ids_is_the_catalogs(self) -> None:
        self.assertIs(models.library_ids, catalog.library_ids)
        self.assertIs(models._scan_dir_ids, catalog._scan_dir_ids)
        self.assertIs(models._is_present, catalog._is_present)


class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtCore import QCoreApplication
        cls.app = QCoreApplication.instance() or QCoreApplication(["t"])
        cls.backend = models.Backend()

    def test_approve_review_sets_good_and_calls_nothing_in_api_client(self) -> None:
        _folder(LIB, "1300000006", "Foxtrot")
        tags.set_state("1300000006", "Foxtrot", "review")
        self.backend.refresh()
        calls: list[str] = []
        names = [name for name, fn in inspect.getmembers(api_client, inspect.isfunction)
                 if not name.startswith("_") and fn.__module__ == api_client.__name__]
        patches = [mock.patch.object(api_client, name, lambda *a, _name=name, **k: calls.append(_name))
                   for name in names]
        for p in patches:
            p.start()
        try:
            with mock.patch.object(actions, "approve", wraps=actions.approve) as spy:
                self.backend.approveReview("1300000006")
        finally:
            for p in patches:
                p.stop()
        self.assertIn("status", names)
        self.assertEqual(calls, [])
        spy.assert_called_once_with("1300000006", "Foxtrot")
        self.assertEqual(_state("1300000006"), "good")
        self.assertFalse(paths.record_file("1300000006").exists())

    def test_bypass_one_refuses_a_gone_steam_folder_and_otherwise_untrashes(self) -> None:
        from lwe_ui.workshop import WorkshopBridge
        bridge = WorkshopBridge(self.backend, None)
        _trashed("1300000007", "Golf")
        _folder(WORKSHOP, "1300000008", "Hotel")
        _trashed("1300000008", "Hotel")
        with mock.patch.object(actions, "untrash", wraps=actions.untrash) as spy:
            gone = bridge._bypass_one("1300000007")
            present = bridge._bypass_one("1300000008")
        self.assertFalse(gone)
        self.assertEqual(_state("1300000007"), "bad")
        self.assertEqual(records.head("1300000007")["action"], "deleted")
        self.assertTrue(present)
        spy.assert_called_once_with("1300000008")
        self.assertNotIn("1300000008", tags.known_ids())
        self.assertEqual(records.head("1300000008")["action"], "bypassed")


if __name__ == "__main__":
    unittest.main()
