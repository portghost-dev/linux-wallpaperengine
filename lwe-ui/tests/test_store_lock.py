"""Store locks: one kernel flock per store, on a file under <config_dir>/locks.

Two processes writing one store lose no update under the lock, and do lose updates once the
lock is made a no-op; a writer paused between its read and its write holds another writer
off; a killed holder frees the lock; a holder that never lets go turns a write into StoreBusy
after about 2 s with the data file untouched; the lock file keeps its inode while the data
file is replaced; the rank order is enforced. Record appends and the engine unit writer take
their locks too. Every child process gets an environment built from scratch.

Run: PYTHONPATH=src python3 tests/test_store_lock.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import fcntl
import json
import os
import shutil
import signal
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

_BOOT = tempfile.TemporaryDirectory(prefix="lwe-lock-boot-")
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = os.path.join(_BOOT.name, _sub) if _sub else _BOOT.name

from lwe_ui.engine import daemon_unit  # noqa: E402
from lwe_ui.storage import atomic, lock, paths, playlists, settings  # noqa: E402

ORDER = ("foreign", "settings", "theme", "discovery", "playlists", "overrides", "tags", "meta",
         "rules", "records", "env", "sync")

# argv: job, name, count, lock or nolock, sync dir. Before each write a writer checks that its
# own previous write is still there; one that is gone is a lost update.
CHILD = r"""
import json, sys, time
from contextlib import nullcontext
from pathlib import Path
from lwe_ui.storage import lock, playlists, records, settings, tags

job, me, n, mode, sync = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], Path(sys.argv[5])
if mode == "nolock":
    lock.held = lambda store: nullcontext()
(sync / f"ready-{me}").touch()
if job == "hold":
    with lock.held(me):
        (sync / f"held-{me}").touch()
        time.sleep(60)
    raise SystemExit(0)
deadline = time.monotonic() + 20
while not (sync / "go").exists():
    if time.monotonic() > deadline:
        raise SystemExit("no go signal")
    time.sleep(0.001)
lost, last = 0, None
for i in range(n):
    if job == "toggle":
        if last is not None and (me in playlists.members("burst")) != last:
            lost += 1
        last = playlists.toggle_member("burst", me)
    elif job == "tags":
        if last is not None and next((r["state"] for r in tags.load() if r["id"] == me), None) != last:
            lost += 1
        last = "good" if i % 2 == 0 else "bad"
        tags.set_state(me, me, last)
    elif job == "active":
        if last is not None and settings.load()["ACTIVE_PLAYLIST"] != last:
            lost += 1
        last = "one" if i % 2 == 0 else "two"
        playlists.set_active(last)
    elif job == "fps":
        if last is not None and settings.load()["ENGINE_FPS"] != last:
            lost += 1
        last = 30 + i % 2
        settings.update({"ENGINE_FPS": last})
    elif job == "append":
        records.append("111", {"who": me, "seq": i, "pad": "x" * (2000 + 997 * (i % 11))})
print(json.dumps({"lost": lost}))
"""


class StoreLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="lwe-lock-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        for key, sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"),
                         ("XDG_DATA_HOME", "d")):
            os.environ[key] = str(self.home / sub) if sub else str(self.home)
        self.sync = self.home / "sync"
        for d in (self.sync, self.home / "bin", self.home / "rt"):
            d.mkdir()
        self.children: list[subprocess.Popen] = []
        self.addCleanup(self._reap)

    def _reap(self) -> None:
        for p in self.children:
            if p.poll() is None:
                p.kill()
            p.communicate(timeout=30)

    def _env(self) -> dict[str, str]:
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

    def _spawn(self, job: str, me: str, n: int = 0, mode: str = "lock") -> subprocess.Popen:
        p = subprocess.Popen([sys.executable, "-c", CHILD, job, me, str(n), mode, str(self.sync)],
                             env=self._env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
        self.children.append(p)
        return p

    def _wait_for(self, path: Path, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while not path.exists():
            self.assertLess(time.monotonic(), deadline, f"{path.name} never appeared")
            time.sleep(0.005)

    def _finish(self, p: subprocess.Popen) -> str:
        out, err = p.communicate(timeout=120)
        self.assertEqual(p.returncode, 0, err)
        return out

    def _burst(self, jobs: tuple, mode: str) -> int:
        for f in self.sync.iterdir():
            f.unlink()
        procs = [self._spawn(job, me, 200, mode) for job, me in jobs]
        for _job, me in jobs:
            self._wait_for(self.sync / f"ready-{me}")
        (self.sync / "go").touch()
        return sum(json.loads(self._finish(p).splitlines()[-1])["lost"] for p in procs)

    def test_two_process_bursts_lose_no_update(self) -> None:
        playlists.save("burst", {"NAME": "burst", "MODE": "shuffle", "INTERVAL": 900,
                                 "UNIT": "min", "MEMBERS": ""})
        settings.ensure_exists()
        pairs = ((("toggle", "a"), ("toggle", "b")),
                 (("tags", "a"), ("tags", "b")),
                 (("active", "a"), ("fps", "b")))
        for jobs in pairs:
            with self.subTest(jobs=jobs):
                self.assertEqual(self._burst(jobs, "lock"), 0)
                self.assertGreater(self._burst(jobs, "nolock"), 0,
                                   "without the lock the burst must lose updates")

    def test_a_writer_paused_between_read_and_write_holds_the_other_off(self) -> None:
        playlists.save("pause", {"NAME": "pause", "MODE": "shuffle", "INTERVAL": 900,
                                 "UNIT": "min", "MEMBERS": ""})
        real_load = playlists.load
        read_by: list[str] = []
        first_read, resume = threading.Event(), threading.Event()

        def paused_load(slug: str) -> dict:
            d = real_load(slug)
            read_by.append(threading.current_thread().name)
            if threading.current_thread().name == "first":
                first_read.set()
                resume.wait(10)
            return d

        with mock.patch.object(playlists, "load", paused_load):
            first = threading.Thread(target=playlists.toggle_member, args=("pause", "a"), name="first")
            second = threading.Thread(target=playlists.toggle_member, args=("pause", "b"), name="second")
            first.start()
            self.assertTrue(first_read.wait(10))
            second.start()
            time.sleep(0.3)
            self.assertEqual(read_by, ["first"], "the second writer read while the first held the lock")
            resume.set()
            first.join(10)
            second.join(10)
        self.assertEqual(sorted(playlists.members("pause")), ["a", "b"])

    def test_a_killed_holder_frees_the_lock(self) -> None:
        settings.ensure_exists()
        holder = self._spawn("hold", "settings")
        self._wait_for(self.sync / "held-settings")
        killer = threading.Timer(0.3, os.kill, (holder.pid, signal.SIGKILL))
        start = time.monotonic()
        killer.start()
        settings.update({"ENGINE_FPS": 45})
        waited = time.monotonic() - start
        holder.communicate(timeout=30)
        self.assertEqual(holder.returncode, -signal.SIGKILL)
        self.assertEqual(settings.load()["ENGINE_FPS"], 45)
        self.assertGreater(waited, 0.25, "the write did not wait for the holder")
        self.assertLess(waited, 2.0)

    def test_a_holder_that_never_releases_gives_store_busy(self) -> None:
        settings.update({"ENGINE_FPS": 40})
        before = paths.settings_file().read_bytes()
        self._spawn("hold", "settings")
        self._wait_for(self.sync / "held-settings")
        start = time.monotonic()
        with self.assertRaises(lock.StoreBusy) as caught:
            settings.update({"ENGINE_FPS": 50})
        waited = time.monotonic() - start
        self.assertGreaterEqual(waited, 1.9)
        self.assertLess(waited, 3.0)
        self.assertIn(str(paths.locks_dir() / "settings.lock"), str(caught.exception))
        self.assertEqual(paths.settings_file().read_bytes(), before)

    def test_the_lock_file_keeps_its_inode_while_the_data_file_is_replaced(self) -> None:
        settings.ensure_exists()
        lock_file = paths.locks_dir() / "settings.lock"
        lock_inodes: set[int] = set()
        data_inodes = [paths.settings_file().stat().st_ino]
        for i in range(100):
            settings.update({"ENGINE_FPS": 30 + i % 2})
            lock_inodes.add(lock_file.stat().st_ino)
            data_inodes.append(paths.settings_file().stat().st_ino)
        self.assertEqual(len(lock_inodes), 1)
        self.assertTrue(all(a != b for a, b in zip(data_inodes, data_inodes[1:])),
                        "every write replaces the data file")

    def test_the_rank_order_is_enforced(self) -> None:
        for i, low in enumerate(ORDER):
            for high in ORDER[i + 1:]:
                with lock.held(low), lock.held(high):
                    pass
                with lock.held(high):
                    with self.assertRaises(RuntimeError, msg=f"{low} taken while holding {high}"):
                        with lock.held(low):
                            pass

    def test_record_appends_from_two_processes_lose_and_tear_no_line(self) -> None:
        n = 150
        rec = paths.record_file("111")
        with lock.held("records"):
            procs = [self._spawn("append", me, n) for me in ("a", "b")]
            for me in ("a", "b"):
                self._wait_for(self.sync / f"ready-{me}")
            (self.sync / "go").touch()
            time.sleep(0.3)
            self.assertFalse(rec.exists(), "an append ran while another process held the records lock")
        for p in procs:
            self._finish(p)
        lines = rec.read_text(encoding="utf-8").split("\n")
        self.assertEqual(lines[-1], "")
        events = [json.loads(line) for line in lines[:-1]]
        self.assertEqual(sorted((e["who"], e["seq"]) for e in events),
                         sorted((who, i) for who in ("a", "b") for i in range(n)))

    def test_the_unit_writer_holds_the_env_lock(self) -> None:
        engine = self.home / "linux-wallpaperengine"
        engine.write_text("#!/bin/sh\n", encoding="utf-8")
        settings.update({"ENGINE_BIN": str(engine)})
        env_lock = paths.locks_dir() / "env.lock"
        seen: list[tuple[str, bool]] = []
        real_write = atomic.atomic_write_text

        def probing_write(path, text) -> None:
            fd = os.open(env_lock, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                seen.append((Path(path).name, False))
            except BlockingIOError:
                seen.append((Path(path).name, True))
            finally:
                os.close(fd)
            real_write(path, text)

        reload = mock.Mock(return_value=subprocess.CompletedProcess(["systemctl"], 0, "", ""))
        with mock.patch.object(atomic, "atomic_write_text", probing_write), \
                mock.patch.object(daemon_unit.subprocess, "run", reload):
            daemon_unit.write_files(["DP-1"])
        reload.assert_called_once()
        self.assertEqual(seen, [(daemon_unit.ENV_FILE_NAME, True), (daemon_unit.UNIT_FILE_NAME, True)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
