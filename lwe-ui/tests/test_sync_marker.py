"""The sync marker, <state_dir>/panel/sync-pending, and its lock.

The marker lock ranks after sync, and a thread inside tags.held() or meta.held() writes that
store without blocking. A writer's marker step raises the generation, empties sent and keeps
the lock across its body, so while a child process is inside it a generation read gets
StoreBusy after about 2 s. ensure keeps an existing generation and makes a new one after a
clear; a clear with a stale generation changes nothing; a malformed file, one with another
version and an empty one read as BUNDLE and CURRENT and are rewritten. A window run records a
sent playlist only for its generation and pid, and another pid empties the list at a run's
start. Every child process gets an environment built from scratch.

Run: PYTHONPATH=src python3 tests/test_sync_marker.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

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

    def test_writing_raises_the_generation_and_empties_sent(self) -> None:
        before = time.time_ns()
        with marker.writing(("BUNDLE",)) as (first, existed):
            self.assertEqual(marker.read(), {"generation": first, "classes": ["BUNDLE"], "sent": EMPTY})
        self.assertGreaterEqual(first, before)
        self.assertFalse(existed)
        self.assertTrue(marker.record_sent(first, 4242, "main"))
        with marker.writing(("CURRENT",)) as (second, existed):
            pass
        self.assertEqual(second, first + 1)
        self.assertTrue(existed)
        self.assertEqual(marker.read(),
                         {"generation": second, "classes": ["BUNDLE", "CURRENT"], "sent": EMPTY})

    def test_ensure_keeps_a_generation_and_makes_a_new_one_after_a_clear(self) -> None:
        kept = marker.ensure(("BUNDLE",))
        self.assertTrue(marker.record_sent(kept, 4242, "main"))
        self.assertEqual(marker.ensure(("CURRENT",)), kept)
        self.assertEqual(marker.read(), {"generation": kept, "classes": ["BUNDLE", "CURRENT"],
                                         "sent": {"pid": 4242, "playlists": ["main"]}})
        self.assertTrue(marker.clear(kept))
        self.assertEqual(marker.read(), {"generation": kept, "classes": [], "sent": EMPTY})
        fresh = marker.ensure(("BUNDLE",))
        self.assertEqual(fresh, kept + 1)
        self.assertEqual(marker.read(), {"generation": fresh, "classes": ["BUNDLE"], "sent": EMPTY})

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

    def test_record_sent_refuses_a_stale_generation_and_another_pid(self) -> None:
        stale = marker.ensure(("BUNDLE",))
        self.assertTrue(marker.record_sent(stale, 4242, "main"))
        self.assertFalse(marker.record_sent(stale, 5151, "night"))
        self.assertEqual(marker.read()["sent"], {"pid": 4242, "playlists": ["main"]})
        with marker.writing(("BUNDLE",)):
            pass
        self.assertFalse(marker.record_sent(stale, 4242, "night"))
        self.assertEqual(marker.read()["sent"], EMPTY)

    def test_start_sent_empties_the_list_for_another_pid_only_in_its_generation(self) -> None:
        stale = marker.ensure(("BUNDLE",))
        self.assertTrue(marker.clear(stale))
        current = marker.ensure(("BUNDLE",))
        self.assertTrue(marker.record_sent(current, 4242, "main"))
        marker.start_sent(stale, 5151)
        marker.start_sent(current, 4242)
        self.assertEqual(marker.sent_for(current, 4242), ["main"])
        marker.start_sent(current, 5151)
        self.assertEqual(marker.read()["sent"], EMPTY)
        self.assertTrue(marker.record_sent(current, 5151, "night"))
        self.assertEqual(marker.sent_for(current, 5151), ["night"])

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
