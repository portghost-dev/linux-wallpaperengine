"""The sync marker, <state_dir>/panel/sync-pending, and its lock.

The marker lock ranks after sync, and a thread inside tags.held() or meta.held() writes that
store without blocking; held() takes the mutex before the file lock, so a thread waiting for the
mutex leaves the file lock free. A writer's marker step raises the generation, empties sent and
keeps the lock across its body, so while a child process is inside it a generation read gets
StoreBusy after about 2 s. ensure keeps the generation for classes the marker holds, raises it
for a class it did not hold, and makes a new one after a clear; a clear with a stale generation
changes nothing, and a clear whose directory fsync fails puts the record back and raises. Every
marker write syncs the file, replaces it, then syncs the directory. A malformed file, one with
another version and an empty one read as BUNDLE and CURRENT and are rewritten. A window run
records a sent playlist only for its generation and its engine, named by pid, boot and start as served
and owed are, and another engine, the same pid with another start included, empties the list at a run's
start; an unknown start records nothing. The served pid is written without raising the generation,
survives a clear, a writer's step, ensure and a sent record, and a file without it reads as none. The
served record names one engine of this boot, its start within 5 s either way, and one that still carries
"short" without boot and start reads without error and names none; an unknown start names no record. owe
ensures CURRENT and records the engine it is owed to, which every other write keeps; drop_owed takes back
only the CURRENT owe added, whatever generation a writer raised meanwhile, and never a CURRENT a writer
added, and an obligation still standing passes to the next engine owed. Every child process gets an
environment built from scratch.

Run: PYTHONPATH=src python3 tests/test_sync_marker.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from lwe_ui.engine import marker  # noqa: E402
from lwe_ui.storage import lock, meta, paths, tags  # noqa: E402

EMPTY = {"pid": None, "playlists": []}

CHILD = r"""
import sys, time
from pathlib import Path
from lwe_ui.engine import marker

with marker.writing(("BUNDLE",)):
    Path(sys.argv[1]).touch()
    time.sleep(60)
"""


class SyncMarkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="lwe-marker-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        for key, sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"),
                         ("XDG_DATA_HOME", "d")):
            os.environ[key] = str(self.home / sub) if sub else str(self.home)
        self.file = paths.panel_state_dir() / "sync-pending"

    def _env(self) -> dict[str, str]:
        for d in ("bin", "rt"):
            (self.home / d).mkdir(exist_ok=True)
        return {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": os.environ["XDG_CONFIG_HOME"],
            "XDG_STATE_HOME": os.environ["XDG_STATE_HOME"],
            "XDG_DATA_HOME": os.environ["XDG_DATA_HOME"],
            "XDG_RUNTIME_DIR": str(self.home / "rt"),
            "LWE_SOCKET": str(self.home / "rt" / "engine.sock"),
            "LWE_SANDBOX": "1",
            "PATH": str(self.home / "bin"),
            "PYTHONPATH": str(SRC),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def _reap(self, child: subprocess.Popen) -> None:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=30)

    def test_the_marker_lock_ranks_after_sync(self) -> None:
        with lock.held("marker"):
            with self.assertRaises(RuntimeError):
                with lock.held("sync"):
                    pass
        with lock.held("sync"), lock.held("marker"):
            pass

    def test_a_thread_inside_held_writes_its_store_without_blocking(self) -> None:
        def inside(held, write, failed: list) -> None:
            try:
                with held():
                    write()
            except BaseException as exc:
                failed.append(exc)
        cases = (("tags", tags.held, lambda: tags.set_state("111", "t", "good")),
                 ("meta", meta.held, lambda: meta.modify("111", lambda entry: {"favorite": True})))
        for store, held, write in cases:
            with self.subTest(store=store):
                failed: list[BaseException] = []
                thread = threading.Thread(target=inside, args=(held, write, failed), daemon=True)
                start = time.monotonic()
                thread.start()
                thread.join(10)
                self.assertFalse(thread.is_alive(), f"the thread inside {store}.held() blocked")
                self.assertEqual(failed, [])
                self.assertLess(time.monotonic() - start, 1.0)
        self.assertEqual(tags.load(), [{"id": "111", "title": "t", "state": "good"}])
        self.assertEqual(meta.get("111"), {"favorite": True})

    def test_held_waits_for_the_mutex_before_it_takes_the_file_lock(self) -> None:
        for store, module in (("tags", tags), ("meta", meta)):
            with self.subTest(store=store):
                mutex, waiting, failed = module._WRITE_LOCK, threading.Event(), []

                class Gate:
                    def acquire(self, *args, **kwargs):
                        waiting.set()
                        return mutex.acquire(*args, **kwargs)

                    def release(self):
                        mutex.release()

                def enter() -> None:
                    try:
                        with module.held():
                            pass
                    except BaseException as exc:
                        failed.append(exc)
                thread = threading.Thread(target=enter, daemon=True)
                mutex.acquire()
                try:
                    with mock.patch.object(module, "_WRITE_LOCK", Gate()):
                        thread.start()
                        self.assertTrue(waiting.wait(10))
                        with lock.held(store, wait_s=0):
                            pass
                finally:
                    mutex.release()
                thread.join(10)
                self.assertFalse(thread.is_alive())
                self.assertEqual(failed, [])

    def test_writing_raises_the_generation_and_empties_sent(self) -> None:
        before = time.time_ns()
        with marker.writing(("BUNDLE",)) as (first, existed):
            self.assertEqual(marker.read(), {"generation": first, "classes": ["BUNDLE"], "sent": EMPTY})
        self.assertGreaterEqual(first, before)
        self.assertFalse(existed)
        self.assertTrue(marker.record_sent(first, 4242, 1000.0, "main"))
        with marker.writing(("CURRENT",)) as (second, existed):
            pass
        self.assertEqual(second, first + 1)
        self.assertTrue(existed)
        self.assertEqual(marker.read(),
                         {"generation": second, "classes": ["BUNDLE", "CURRENT"], "sent": EMPTY})

    def test_ensure_keeps_the_generation_for_held_classes_and_raises_it_for_a_new_one(self) -> None:
        kept = marker.ensure(("BUNDLE",))
        self.assertTrue(marker.record_sent(kept, 4242, 1000.0, "main"))
        self.assertEqual(marker.ensure(("BUNDLE",)), kept)
        self.assertEqual(marker.read(), {"generation": kept, "classes": ["BUNDLE"],
                                         "sent": {"pid": 4242, "boot": marker.boot_id(), "start": 1000.0,
                                                  "playlists": ["main"]}})
        raised = marker.ensure(("CURRENT",))
        self.assertEqual(raised, kept + 1)
        self.assertEqual(marker.read(), {"generation": raised, "classes": ["BUNDLE", "CURRENT"], "sent": EMPTY})
        self.assertEqual(marker.ensure(("BUNDLE", "CURRENT")), raised)
        self.assertFalse(marker.clear(kept))
        self.assertTrue(marker.clear(raised))
        self.assertEqual(marker.read(), {"generation": raised, "classes": [], "sent": EMPTY})
        fresh = marker.ensure(("BUNDLE",))
        self.assertEqual(fresh, raised + 1)
        self.assertEqual(marker.read(), {"generation": fresh, "classes": ["BUNDLE"], "sent": EMPTY})

    def test_a_marker_write_syncs_the_file_replaces_it_then_syncs_the_directory(self) -> None:
        events: list[str] = []
        fsync, replace = os.fsync, os.replace

        def synced(fd: int) -> None:
            events.append("dir_fsync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file_fsync")
            fsync(fd)

        def replaced(src, dst) -> None:
            events.append("replace")
            replace(src, dst)
        with mock.patch.object(os, "fsync", synced), mock.patch.object(os, "replace", replaced):
            marker.ensure(("BUNDLE",))
        self.assertEqual(events, ["file_fsync", "replace", "dir_fsync"])

    def test_a_clear_whose_directory_fsync_fails_puts_the_record_back_and_raises(self) -> None:
        generation = marker.ensure(("BUNDLE", "CURRENT"))
        self.assertTrue(marker.record_sent(generation, 4242, 1000.0, "main"))
        before = marker.read()
        fsync = os.fsync

        def failing(fd: int) -> None:
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("injected directory fsync failure")
            fsync(fd)
        with mock.patch.object(os, "fsync", failing):
            with self.assertRaises(OSError):
                marker.clear(generation)
        self.assertEqual(marker.read(), before)

    def test_a_clear_with_a_stale_generation_changes_nothing(self) -> None:
        stale = marker.ensure(("BUNDLE",))
        with marker.writing(("BUNDLE",)):
            pass
        before = self.file.read_bytes()
        self.assertFalse(marker.clear(stale))
        self.assertEqual(self.file.read_bytes(), before)

    def test_a_malformed_marker_reads_as_bundle_and_current_and_is_rewritten(self) -> None:
        version_2 = json.dumps({"version": 2, "generation": 5, "classes": [], "sent": EMPTY})
        for name, text in (("malformed JSON", "{not json"), ("version 2", version_2), ("empty", "")):
            with self.subTest(file=name):
                self.file.parent.mkdir(parents=True, exist_ok=True)
                self.file.write_text(text, encoding="utf-8")
                state = marker.read()
                self.assertEqual((state["classes"], state["sent"]), (["BUNDLE", "CURRENT"], EMPTY))
                self.assertIsInstance(state["generation"], int)
                self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), {"version": 1, **state})

    def test_the_served_pid_never_raises_the_generation_and_survives_every_other_write(self) -> None:
        self.assertIsNone(marker.served())
        generation = marker.ensure(("BUNDLE",))
        marker.record_served(4242)
        self.assertEqual((marker.served(), marker.generation(), marker.read()["classes"]),
                         (4242, generation, ["BUNDLE"]))
        self.assertTrue(marker.record_sent(generation, 4242, 1000.0, "main"))
        marker.start_sent(generation, 5151, 2000.0)
        self.assertTrue(marker.clear(generation))
        raised = marker.ensure(("BUNDLE", "CURRENT"))
        with marker.writing(("BUNDLE",)):
            pass
        self.assertEqual(marker.served(), 4242)
        self.assertEqual(marker.generation(), raised + 1)
        doc = json.loads(self.file.read_text(encoding="utf-8"))
        del doc["served"]
        self.file.write_text(json.dumps(doc), encoding="utf-8")
        self.assertIsNone(marker.served())
        self.assertEqual(marker.generation(), raised + 1)

    def test_the_served_record_names_one_engine_of_this_boot_and_a_record_without_boot_names_none(self) -> None:
        marker.record_served(4242, 1000.0, braked=True, at=5.0)
        record = marker.served_record()
        self.assertEqual(record, {"pid": 4242, "at": 5.0, "boot": marker.boot_id(), "start": 1000.0, "braked": True})
        self.assertEqual([marker.names(record, pid, start) for pid, start in
                          ((4242, 1004.5), (4242, 994.0), (4243, 1000.0), (4242, None))],
                         [True, False, False, False])
        with mock.patch.object(marker, "boot_id", return_value="another boot"):
            self.assertFalse(marker.names(record, 4242, 1000.0))
        doc = json.loads(self.file.read_text(encoding="utf-8"))
        doc["served"] = {"pid": 4242, "at": 5.0, "short": 1}
        self.file.write_text(json.dumps(doc), encoding="utf-8")
        self.assertEqual(marker.served(), 4242)
        self.assertFalse(marker.names(marker.served_record(), 4242, None))

    def test_the_start_tolerance_is_5_s_either_way_and_an_unknown_start_names_no_record(self) -> None:
        marker.record_served(4242, 1000.0)
        record = marker.served_record()
        self.assertEqual([marker.names(record, 4242, start) for start in (995.0, 1005.0, 994.9, 1005.1)],
                         [True, True, False, False])
        marker.record_served(5000, None)
        self.assertFalse(marker.names(marker.served_record(), 5000, None), "an unknown start cannot be verified")
        self.assertFalse(marker.names(marker.served_record(), 5000, 900.0), "a record with no start names no engine")

    def test_owe_records_its_engine_every_write_keeps_it_and_drop_owed_takes_back_only_its_current(self) -> None:
        generation = marker.owe(5000, 900.0)
        owed = {"pid": 5000, "boot": marker.boot_id(), "start": 900.0, "current": generation}
        self.assertEqual((marker.read()["classes"], marker.owed_record()), (["BUNDLE", "CURRENT"], owed))
        self.assertTrue(marker.record_sent(generation, 5000, 900.0, "main"))
        marker.start_sent(generation, 5000, 900.0)
        marker.record_served(4242)
        marker.drop_owed(5001, 900.0)
        self.assertEqual((marker.read()["classes"], marker.owed_record()), (["BUNDLE", "CURRENT"], owed))
        marker.drop_owed(5000, 900.0)
        self.assertEqual((marker.read(), marker.owed_record()),
                         ({"generation": generation, "classes": ["BUNDLE"],
                           "sent": {"pid": 5000, "boot": marker.boot_id(), "start": 900.0, "playlists": ["main"]}},
                          {**owed, "current": None}))
        raised = marker.owe(5000, 900.0)
        with marker.writing(("BUNDLE",)):
            pass
        marker.drop_owed(5000, 900.0)
        self.assertEqual((raised, marker.read()["classes"]), (generation + 1, ["BUNDLE"]),
                         "a writer's BUNDLE leaves the owed CURRENT to be taken back")
        marker.owe(5000, 900.0)
        with marker.writing(("BUNDLE", "CURRENT")):
            pass
        marker.drop_owed(5000, 900.0)
        self.assertEqual((marker.read()["classes"], marker.owed_record()["current"]), (["BUNDLE", "CURRENT"], None),
                         "a writer's CURRENT is never taken back")
        self.assertTrue(marker.clear(marker.generation()))
        self.assertEqual(marker.owed_record()["pid"], 5000)

    def test_an_owed_obligation_passes_to_the_next_engine_whatever_the_generation(self) -> None:
        marker.owe(5000, 900.0)
        with marker.writing(("BUNDLE",)):
            pass
        marker.owe(5001, 950.0)
        self.assertIsNotNone(marker.owed_record()["current"], "the CURRENT owe added is still owed")
        marker.drop_owed(5001, 950.0)
        self.assertEqual(marker.read()["classes"], ["BUNDLE"])

    def test_record_sent_refuses_a_stale_generation_and_another_pid(self) -> None:
        stale = marker.ensure(("BUNDLE",))
        self.assertTrue(marker.record_sent(stale, 4242, 1000.0, "main"))
        self.assertFalse(marker.record_sent(stale, 5151, 2000.0, "night"))
        self.assertEqual(marker.read()["sent"], {"pid": 4242, "boot": marker.boot_id(), "start": 1000.0,
                                                 "playlists": ["main"]})
        with marker.writing(("BUNDLE",)):
            pass
        self.assertFalse(marker.record_sent(stale, 4242, 1000.0, "night"))
        self.assertEqual(marker.read()["sent"], EMPTY)

    def test_start_sent_empties_the_list_for_another_pid_only_in_its_generation(self) -> None:
        stale = marker.ensure(("BUNDLE",))
        self.assertTrue(marker.clear(stale))
        current = marker.ensure(("BUNDLE",))
        self.assertTrue(marker.record_sent(current, 4242, 1000.0, "main"))
        marker.start_sent(stale, 5151, 2000.0)
        marker.start_sent(current, 4242, 1000.0)
        self.assertEqual(marker.sent_for(current, 4242, 1000.0), ["main"])
        marker.start_sent(current, 5151, 2000.0)
        self.assertEqual(marker.read()["sent"], EMPTY)
        self.assertTrue(marker.record_sent(current, 5151, 2000.0, "night"))
        self.assertEqual(marker.sent_for(current, 5151, 2000.0), ["night"])

    def test_sent_names_its_engine_by_pid_boot_and_start_and_another_engine_empties_it(self) -> None:
        generation = marker.ensure(("BUNDLE",))
        self.assertTrue(marker.record_sent(generation, 4242, 1000.0, "main"))
        self.assertEqual(marker.read()["sent"], {"pid": 4242, "boot": marker.boot_id(), "start": 1000.0,
                                                 "playlists": ["main"]})
        self.assertEqual([marker.sent_for(generation, 4242, start) for start in (1004.0, 1010.0)], [["main"], []])
        self.assertFalse(marker.record_sent(generation, 4242, 1010.0, "night"), "another engine records nothing")
        with mock.patch.object(marker, "boot_id", return_value="another boot"):
            self.assertEqual(marker.sent_for(generation, 4242, 1000.0), [])
        marker.start_sent(generation, 4242, 1003.0)
        self.assertEqual(marker.sent_for(generation, 4242, 1000.0), ["main"], "the same engine keeps its list")
        marker.start_sent(generation, 4242, 1010.0)
        self.assertEqual(marker.read()["sent"], EMPTY)
        self.assertFalse(marker.record_sent(generation, 4242, None, "night"), "an unknown start records nothing")
        self.assertTrue(marker.record_sent(generation, 4242, 1010.0, "night"))
        self.assertEqual(marker.sent_for(generation, 4242, 1010.0), ["night"])

    def test_a_restart_record_is_an_ordinary_bundle_with_no_pid_tie(self) -> None:
        with self.assertRaises(TypeError):
            marker.ensure(("BUNDLE",), replacing=4242)
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps({"version": 1, "generation": 7, "classes": ["BUNDLE"],
                                         "sent": {"pid": None, "playlists": []}, "replaced": 4242}) + "\n",
                             encoding="utf-8")
        self.assertEqual(marker.read(), {"generation": 7, "classes": ["BUNDLE"], "sent": EMPTY})
        self.assertTrue(marker.clear(7))
        self.assertEqual(marker.read()["classes"], [])

    def test_a_child_inside_writing_makes_generation_raise_store_busy(self) -> None:
        inside = self.home / "inside"
        child = subprocess.Popen([sys.executable, "-c", CHILD, str(inside)], env=self._env(),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self._reap, child)
        deadline = time.monotonic() + 30
        while not inside.exists():
            self.assertIsNone(child.poll(), "the child exited before it entered writing()")
            self.assertLess(time.monotonic(), deadline, "the child never entered writing()")
            time.sleep(0.005)
        start = time.monotonic()
        with self.assertRaises(lock.StoreBusy):
            marker.generation()
        waited = time.monotonic() - start
        self.assertGreaterEqual(waited, 1.9)
        self.assertLess(waited, 3.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
