"""Store locks: one kernel flock per store, on a file under <config_dir>/locks.

Two processes writing one store lose no update under the lock; a writer paused between its
read and its write holds another writer off; a killed holder frees the lock; a holder that
never lets go turns a write into StoreBusy after about 2 s with the data file untouched; the
lock file keeps its inode while the data file is replaced; the rank order is enforced. Every
store writer takes its store's lock, and the panel's callers read inside it: the theme and
metadata editors, the bench, the rule file opener, playlist delete and first start. A busy
store is reported, logged at start, and never waited on by the status poll. Every child
process gets an environment built from scratch.

Run: PYTHONPATH=src python3 tests/test_store_lock.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import ast
import contextlib
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
import types
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

_BOOT = tempfile.TemporaryDirectory(prefix="lwe-lock-boot-")
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = os.path.join(_BOOT.name, _sub) if _sub else _BOOT.name
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402

_APP = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])

from lwe_ui.engine import daemon_unit  # noqa: E402
from lwe_ui.storage import atomic, lock, meta, paths, playlists, rules, settings, wp  # noqa: E402

ORDER = ("foreign", "settings", "theme", "discovery", "playlists", "overrides", "tags", "meta",
         "rules", "records", "env", "sync")

# argv: job, name, count, sync dir. Before each write a writer checks that its own previous
# write is still there; one that is gone is a lost update.
CHILD = r"""
import json, sys, time
from pathlib import Path
from lwe_ui.storage import lock, playlists, records, settings, tags

job, me, n, sync = sys.argv[1], sys.argv[2], int(sys.argv[3]), Path(sys.argv[4])
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

    def _spawn(self, job: str, me: str, n: int = 0) -> subprocess.Popen:
        p = subprocess.Popen([sys.executable, "-c", CHILD, job, me, str(n), str(self.sync)],
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

    @contextlib.contextmanager
    def _held_elsewhere(self, store: str, seconds: float | None = None):
        """Hold `store`'s lock from another thread until the with block ends, or for `seconds`."""
        ready, release = threading.Event(), threading.Event()

        def hold() -> None:
            with lock.held(store):
                ready.set()
                release.wait(30 if seconds is None else seconds)
        holder = threading.Thread(target=hold)
        holder.start()
        try:
            self.assertTrue(ready.wait(10))
            yield
        finally:
            release.set()
            holder.join(10)

    def _paused(self, thread_name: str):
        """A pause point for one named thread: returns (reached, resume, pause)."""
        reached, resume = threading.Event(), threading.Event()

        def pause() -> None:
            if threading.current_thread().name == thread_name:
                reached.set()
                resume.wait(10)
        return reached, resume, pause

    def _burst(self, jobs: tuple) -> int:
        for f in self.sync.iterdir():
            f.unlink()
        procs = [self._spawn(job, me, 2000) for job, me in jobs]
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
                self.assertEqual(self._burst(jobs), 0)

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
            try:
                self.assertTrue(first_read.wait(10))
                second.start()
                time.sleep(0.3)
                self.assertEqual(read_by, ["first"], "the second writer read while the first held the lock")
            finally:
                resume.set()
                first.join(10)
                if second.ident is not None:
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

        reload = mock.Mock(return_value=(0, "", ""))
        with mock.patch.object(atomic, "atomic_write_text", probing_write), \
                mock.patch.object(daemon_unit, "RUNNER", reload):
            daemon_unit.write_files(["DP-1"])
        reload.assert_called_once()
        self.assertEqual(seen, [(daemon_unit.ENV_FILE_NAME, True), (daemon_unit.UNIT_FILE_NAME, True)])

    def test_every_writer_takes_its_stores_lock(self) -> None:
        from lwe_ui.editor import EditorBridge
        from lwe_ui.storage import discover_cfg, foreign, records, tags, themes
        wp.write_keys("111", {"BG": "111", "SPEED": "1.0"})
        records.append("111", {"event": 1})
        meta.update("111", {"tags": ["base", "gone"]})
        editor = EditorBridge()
        editor._wid, editor._meta = "111", meta.get("111")
        receipt: dict = {"errors": []}
        cases = (
            ("overrides", "wp.update_set", wp.update_set_path, lambda: wp.update_set("111", {"SPEED": 2.0})),
            ("overrides", "wp.write_keys", wp.write_keys, lambda: wp.write_keys("112", {"BG": "112"})),
            ("overrides", "wp.modify_set", wp.modify_set,
             lambda: wp.modify_set("111", lambda present: {"VOLUME": 5})),
            ("overrides", "wp.save", wp.save_path, lambda: wp.save("113", wp.load("113"))),
            ("overrides", "wp.sparsify_overrides", wp.sparsify_overrides, wp.sparsify_overrides),
            ("overrides", "overrides restore", wp._backup_apply,
             lambda: wp._backup_apply({"overrides": {"114": {"BG": "114"}}}, receipt)),
            ("rules", "rules.save", rules.save, lambda: rules.save("app-condition.txt", "steam\n")),
            ("rules", "rules.modify", rules.modify,
             lambda: rules.modify("app-condition.txt", lambda text: text + "obs\n")),
            ("rules", "rules restore", rules.save,
             lambda: rules._backup_apply({"rules": {"pause-blacklist.txt": "x\n"}}, receipt)),
            ("meta", "meta.update", meta.update, lambda: meta.update("111", {"note": "n"})),
            ("meta", "meta.modify", meta.modify, lambda: meta.modify("111", lambda entry: {"favorite": True})),
            ("meta", "meta restore", meta._backup_apply,
             lambda: meta._backup_apply({"meta": {"111": {"title": "t"}}}, receipt)),
            ("meta", "editor.removeTag", meta.modify, lambda: editor.removeTag("gone")),
            ("theme", "themes.save_config", themes.save_config,
             lambda: themes.save_config(themes.load_config())),
            ("theme", "themes.modify", themes.modify, lambda: themes.modify(lambda cfg: None)),
            ("theme", "theme restore", themes._backup_apply,
             lambda: themes._backup_apply({"theme": {"active": "dark"}}, receipt)),
            ("discovery", "discover_cfg.save", discover_cfg.save,
             lambda: discover_cfg.save(discover_cfg.load())),
            ("discovery", "discovery restore", discover_cfg._backup_apply,
             lambda: discover_cfg._backup_apply({"discovery": {"apiKey": "k"}}, receipt)),
            ("foreign", "foreign.promote", foreign.promote, foreign.promote),
            ("foreign", "foreign.apply_plan", foreign.apply_plan,
             lambda: foreign.apply_plan({"foreign": {"settings": {"settings.conf": {"LATER_KEY": "1"}}}},
                                        receipt)),
            ("playlists", "playlists.create", playlists.create, lambda: playlists.create("Made")),
            ("playlists", "playlists.delete", playlists.delete, lambda: playlists.delete("made")),
            ("settings", "settings.replace", settings.replace, lambda: settings.replace(lambda current: current)),
            ("settings", "settings.ensure_exists", settings.ensure_exists,
             lambda: (paths.settings_file().unlink(missing_ok=True), settings.ensure_exists())),
            ("records", "records.purge", records.purge, lambda: records.purge("111")),
        )
        helpers = {meta._locked.__wrapped__.__code__, tags._locked.__wrapped__.__code__,
                   contextlib._GeneratorContextManager.__enter__.__code__}
        taken: list[tuple[str, object]] = []
        real_held = lock.held

        def recording(store: str, *args, **kwargs):
            frame = sys._getframe(1)
            while frame.f_code in helpers:
                frame = frame.f_back
            taken.append((store, frame.f_code))
            return real_held(store, *args, **kwargs)

        with mock.patch.object(lock, "held", recording):
            for store, name, owner, call in cases:
                with self.subTest(writer=name):
                    taken.clear()
                    call()
                    self.assertIn((store, owner.__code__), taken,
                                  f"{name} does not take the {store} lock in {owner.__qualname__} itself")
        self.assertEqual(receipt["errors"], [])

    def test_the_theme_writers_read_inside_the_lock(self) -> None:
        from lwe_ui.models import ThemeBridge, ThemeTokens
        from lwe_ui.storage import themes
        restored = {"gridGraphite": {"accent": "#112233"}}
        real_load = themes.load_config
        for name in ("setActive", "setRoleLive", "resetActive", "revertEdit"):
            with self.subTest(writer=name):
                themes.save_config({"active": "dark", "overlays": {"dark": {"accent": "#445566"}}})
                bridge = ThemeBridge(ThemeTokens(themes.resolve_active()))
                bridge.beginEdit()
                writer = {"setActive": lambda: bridge.setActive("neonDusk"),
                          "setRoleLive": lambda: bridge.setRoleLive("accent", "#abcdef"),
                          "resetActive": bridge.resetActive,
                          "revertEdit": bridge.revertEdit}[name]
                reached, resume, pause = self._paused("window")

                def paused_load() -> dict:
                    cfg = real_load()
                    pause()
                    return cfg
                with mock.patch.object(themes, "load_config", paused_load):
                    window = threading.Thread(target=writer, name="window")
                    restore = threading.Thread(target=themes._backup_apply,
                                               args=({"theme": {"overlays": restored}}, {"errors": []}))
                    window.start()
                    try:
                        self.assertTrue(reached.wait(10))
                        restore.start()
                        time.sleep(0.3)
                    finally:
                        resume.set()
                        window.join(10)
                        if restore.ident is not None:
                            restore.join(10)
                self.assertEqual(real_load()["overlays"].get("gridGraphite"), restored["gridGraphite"],
                                 f"{name} saved a theme it read before the restore")

    def test_the_bench_saves_only_the_property_it_changed(self) -> None:
        from lwe_ui import bench_bridge
        wp.write_keys("111", {"BG": "111", "SPEED": "1.0"})
        bench = bench_bridge.BenchBridge()
        bench._wid, bench._draft = "111", wp.load("111")
        wp.update_set("111", {"SPEED": 3.0})
        bench.setProp("glow", "on")
        present = wp.load_set("111")
        self.assertEqual((present["SPEED"], present["props"]), (3.0, {"glow": "on"}))
        bench.setProp("glow", "")
        self.assertEqual(wp.load_set("111")["props"], {})

    def test_seeding_a_pending_conf_holds_the_overrides_lock(self) -> None:
        from lwe_ui import bench_bridge
        workshop = self.home / "workshop"
        (workshop / "222").mkdir(parents=True)
        (workshop / "222" / "project.json").write_text('{"type": "scene", "title": "t"}', encoding="utf-8")
        real_read = bench_bridge.project_disc.read
        reached, resume, pause = self._paused("seeder")

        def paused_read(path, *args, **kwargs):
            pause()
            return real_read(path, *args, **kwargs)
        with mock.patch.object(bench_bridge.project_disc, "read", paused_read):
            seeder = threading.Thread(target=bench_bridge.seed_pending_conf, args=("222", str(workshop)),
                                      name="seeder")
            writer = threading.Thread(target=wp.update_set, args=("222", {"VOLUME": 40}))
            seeder.start()
            try:
                self.assertTrue(reached.wait(10))
                writer.start()
                time.sleep(0.3)
            finally:
                resume.set()
                seeder.join(10)
                if writer.ident is not None:
                    writer.join(10)
        self.assertEqual(wp.load_set("222")["VOLUME"], 40, "the seed overwrote a write made meanwhile")

    def test_metadata_edits_start_from_a_fresh_read(self) -> None:
        from lwe_ui.editor import EditorBridge
        meta.update("111", {"tags": ["base"], "favorite": False})
        editor = EditorBridge()
        editor._wid, editor._meta = "111", meta.get("111")
        meta.update("111", {"tags": ["base", "remote"], "favorite": True})
        editor.addTag("local")
        self.assertEqual(meta.get("111")["tags"], ["base", "remote", "local"])
        editor.removeTag("base")
        self.assertEqual(meta.get("111")["tags"], ["remote", "local"])
        editor.toggleFavorite()
        self.assertFalse(meta.get("111")["favorite"], "the toggle flips the stored value")
        self.assertEqual(editor.tags(), ["remote", "local"])

    def test_two_favorite_toggles_from_the_grid_both_land(self) -> None:
        from lwe_ui import models
        backend = models.Backend()
        meta.update("111", {"favorite": False})
        real_load = meta.load
        reached, resume, pause = self._paused("first")

        def paused_load() -> dict:
            data = real_load()
            pause()
            return data
        with mock.patch.object(meta, "load", paused_load):
            first = threading.Thread(target=backend.toggleFavorite, args=("111",), name="first")
            second = threading.Thread(target=backend.toggleFavorite, args=("111",))
            first.start()
            try:
                self.assertTrue(reached.wait(10))
                second.start()
                time.sleep(0.3)
            finally:
                resume.set()
                first.join(10)
                if second.ident is not None:
                    second.join(10)
        self.assertFalse(meta.get("111")["favorite"], "two toggles must cancel out")

    def test_opening_a_missing_rule_file_takes_the_rules_lock(self) -> None:
        from lwe_ui import models
        backend = models.Backend()
        opened: list[str] = []
        backend.openPath = opened.append
        f = rules.file_for("pause-blacklist.txt")
        with self._held_elsewhere("rules", seconds=0.3):
            start = time.monotonic()
            backend.editBlacklist()
            waited = time.monotonic() - start
        self.assertGreater(waited, 0.25, "the file was created without the rules lock")
        self.assertTrue(f.read_text(encoding="utf-8").startswith("# "))
        self.assertEqual(opened, [str(f)])
        f.write_text("", encoding="utf-8")
        backend.editBlacklist()
        self.assertEqual(f.read_text(encoding="utf-8"), "", "an existing file is left as it is")

    def test_deleting_the_active_playlist_keeps_a_newer_switch(self) -> None:
        for slug in ("victim", "alpha", "beta"):
            playlists.save(slug, {"NAME": slug, "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min",
                                  "MEMBERS": ""})
        playlists.set_active("victim")
        real_list = playlists.list_playlists
        reached, resume, pause = self._paused("deleter")

        def paused_list() -> list:
            rows = real_list()
            pause()
            return rows
        with mock.patch.object(playlists, "list_playlists", paused_list):
            deleter = threading.Thread(target=playlists.delete, args=("victim",), name="deleter")
            switcher = threading.Thread(target=playlists.set_active, args=("beta",))
            deleter.start()
            try:
                self.assertTrue(reached.wait(10))
                switcher.start()
                time.sleep(0.3)
            finally:
                resume.set()
                deleter.join(10)
                if switcher.ident is not None:
                    switcher.join(10)
        self.assertEqual(settings.load()["ACTIVE_PLAYLIST"], "beta")

    def test_two_first_starts_make_one_default_playlist(self) -> None:
        from lwe_ui.storage import tags
        real_good = tags.good_ids
        reached, resume, pause = self._paused("first")

        def paused_good() -> set:
            pause()
            return real_good()
        results: dict[str, str] = {}
        with mock.patch.object(tags, "good_ids", paused_good):
            first = threading.Thread(target=lambda: results.update(first=playlists.ensure_default()),
                                     name="first")
            second = threading.Thread(target=lambda: results.update(second=playlists.ensure_default()))
            first.start()
            try:
                self.assertTrue(reached.wait(10))
                second.start()
                time.sleep(0.3)
            finally:
                resume.set()
                first.join(10)
                if second.ident is not None:
                    second.join(10)
        made = [row["slug"] for row in playlists.list_playlists()]
        self.assertEqual(len(made), 1, made)
        self.assertEqual((results["first"], results["second"]), (made[0], made[0]))
        self.assertEqual(settings.load()["ACTIVE_PLAYLIST"], made[0])

    def test_a_busy_settings_store_reaches_the_caller(self) -> None:
        from lwe_ui import models, settings_bridge
        backend = models.Backend()
        page = settings_bridge.SettingsBridge(backend)
        failures: list = []
        page.commitFailed.connect(lambda keys, reason: failures.append((list(keys), reason)))
        with mock.patch.object(lock, "LOCK_WAIT_S", 0.2), self._held_elsewhere("settings"):
            self.assertFalse(page.commit("CLOSE_TO_TRAY", False))
            with self.assertRaises(lock.StoreBusy):
                backend.setPaused(True)
            with self.assertRaises(lock.StoreBusy):
                backend.setSessionOverride("mute", True)
        self.assertEqual(failures, [(["CLOSE_TO_TRAY"], "Settings could not be saved.")])

    def test_the_panel_start_logs_a_busy_store_and_goes_on(self) -> None:
        from lwe_ui import app, logbook

        class Reached(Exception):
            pass
        busy = lock.StoreBusy("Store busy: another writer holds settings.lock")
        for argv in (["lwe-ui", "--tray"], ["lwe-ui"]):
            with self.subTest(argv=argv):
                logger = mock.Mock()
                with mock.patch.object(app.settings, "ensure_exists", side_effect=busy), \
                        mock.patch.object(logbook, "install", return_value=logger), \
                        mock.patch.object(app, "_settle_state_tree", side_effect=Reached), \
                        mock.patch.object(app, "set_process_name"):
                    with self.assertRaises(Reached):
                        app.main(list(argv))
                logger.warning.assert_called_once()
        tree = ast.parse(Path(app.__file__).read_text(encoding="utf-8"))
        tries = [node for node in ast.walk(tree) if isinstance(node, ast.Try)
                 and any("write_env" in ast.unparse(stmt) for stmt in node.body)]
        self.assertEqual(len(tries), 1)
        caught = " ".join(ast.unparse(h.type) for h in tries[0].handlers if h.type is not None)
        self.assertIn("OSError", caught, "a busy env lock at start must be logged, not fatal")

    def test_the_wizard_logs_a_busy_record_store_and_goes_on(self) -> None:
        from lwe_ui import wizard_bridge
        done: list[str] = []
        receiver = types.SimpleNamespace(
            _wid="111", _title="t", _chash="h", _lineage=[], _fixed=False,
            _session=types.SimpleNamespace(verdict="crashed"),
            _backend=types.SimpleNamespace(approveReview=lambda wid: done.append("approved")),
            _workshop=types.SimpleNamespace(trashFiles=lambda wid: done.append("trashed")),
            graduated=types.SimpleNamespace(emit=lambda wid: done.append("graduated")),
            trashedUnsub=types.SimpleNamespace(emit=lambda wid, title: done.append("unsubscribed")),
            close=lambda: done.append("closed"))
        busy = lock.StoreBusy("Store busy: another writer holds records.lock")
        with mock.patch.object(wizard_bridge.records, "append", side_effect=busy), \
                self.assertLogs("lwe_ui.wizard", level="WARNING") as logged:
            for slot in ("importUntested", "approve", "deny", "cancel"):
                getattr(wizard_bridge.WizardBridge, slot)(receiver, "")
        self.assertEqual(len(logged.records), 4)
        self.assertEqual(done, ["approved", "graduated", "closed", "approved", "graduated", "closed",
                                "trashed", "closed", "unsubscribed", "closed"])

    def test_the_status_poll_never_waits_on_a_busy_settings_store(self) -> None:
        from lwe_ui import models
        backend = models.Backend()
        playlists.save("night", {"NAME": "night", "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min",
                                 "MEMBERS": ""})
        before = settings.load()["ACTIVE_PLAYLIST"]
        with self._held_elsewhere("settings"):
            start = time.monotonic()
            backend._follow_engine_playlist("night")
            waited = time.monotonic() - start
            with self.assertRaises(lock.StoreBusy):
                with lock.held("settings", wait_s=0):
                    pass
        self.assertLess(waited, 0.5, "the poll waited on a busy store")
        self.assertEqual(settings.load()["ACTIVE_PLAYLIST"], before, "a busy poll changes nothing")
        backend._follow_engine_playlist("night")
        self.assertEqual(settings.load()["ACTIVE_PLAYLIST"], "night", "a later poll adopts it")

    def test_next_and_prev_step_while_the_settings_store_is_busy(self) -> None:
        from lwe_ui import api_client, models
        backend = models.Backend()
        settings.update({"ROTATION_ENABLED": False})
        steps: list[str] = []
        with mock.patch.object(api_client, "next_wallpaper", lambda: steps.append("next") or {"ok": True}), \
                mock.patch.object(api_client, "prev_wallpaper", lambda: steps.append("prev") or {"ok": True}), \
                mock.patch.object(lock, "LOCK_WAIT_S", 0.2), self._held_elsewhere("settings"), \
                self.assertLogs("lwe_ui", level="WARNING") as logged:
            self.assertTrue(backend.rotateNext())
            self.assertTrue(backend.rotatePrev())
        self.assertEqual(steps, ["next", "prev"], "a busy settings store must not block the step")
        self.assertEqual(len(logged.records), 2)
        for record in logged.records:
            self.assertIn("settings.lock", record.getMessage())
        self.assertFalse(settings.load()["ROTATION_ENABLED"], "the busy resume saved nothing")

    def test_a_purge_that_cannot_take_the_records_lock_ungates_nothing(self) -> None:
        from lwe_ui.storage import records, records_view, tags
        records.append("111", {"action": "deleted"})
        tags.set_state("111", "t", "bad")
        with mock.patch.object(lock, "LOCK_WAIT_S", 0.2), self._held_elsewhere("records"), \
                self.assertLogs("lwe_ui", level="WARNING") as logged:
            self.assertFalse(records_view.purge_and_ungate("111"))
            self.assertIn("111", tags.known_ids(), "a purge that removed no record must keep the tags row")
        self.assertEqual(len(logged.records), 1)
        self.assertTrue(paths.record_file("111").exists())

    def test_the_rotation_setters_log_a_busy_settings_store(self) -> None:
        from lwe_ui import models
        backend = models.Backend()
        keys = ("ORDER", "INTERVAL", "ROTATION_ENABLED")
        before = {key: settings.load()[key] for key in keys}
        with mock.patch.object(lock, "LOCK_WAIT_S", 0.2), self._held_elsewhere("settings"), \
                self.assertLogs("lwe_ui", level="WARNING") as logged:
            backend.setOrder("sequential")
            backend.setInterval(600)
            backend.setRotationEnabled(False)
        messages = [record.getMessage() for record in logged.records]
        self.assertEqual(len(messages), 3, messages)
        for key, message in zip(keys, messages):
            self.assertIn(key, message)
        self.assertEqual({key: settings.load()[key] for key in keys}, before, "a busy store saves nothing")

    def test_a_window_delete_holds_settings_so_a_busy_settings_store_refuses_it_cleanly(self) -> None:
        from lwe_ui import models
        from lwe_ui.engine import marker
        for slug in ("keep", "beta", "gamma"):
            playlists.save(slug, {"NAME": slug, "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min",
                                  "MEMBERS": ""})
        playlists.set_active("keep")
        backend = models.Backend()
        changed: list[int] = []
        backend.playlistsChanged.connect(lambda: changed.append(1))
        pushes: list[bool] = []
        backend._sync_engine = lambda manual=False: pushes.append(manual)
        with mock.patch.object(lock, "LOCK_WAIT_S", 0.3), self._held_elsewhere("settings"), \
                self.assertLogs("lwe_ui", level="WARNING") as logged:
            start = time.monotonic()
            playlists.delete("beta")
            waited = time.monotonic() - start
            backend.deletePlaylist("gamma")
        self.assertLess(waited, 0.25, "the store's delete of a playlist that is not active took the settings lock")
        self.assertFalse(paths.playlist_file("beta").exists())
        self.assertEqual(len(logged.records), 1)
        self.assertTrue(paths.playlist_file("gamma").exists(),
                        "the window's delete takes settings first: nothing was deleted")
        self.assertEqual((len(changed), pushes), (0, []), "nothing saved, refreshed or sent")
        self.assertEqual(settings.load()["ACTIVE_PLAYLIST"], "keep")
        self.assertEqual(marker.read()["classes"], [], "no pending record was left for a delete that did not happen")

    def test_deleting_the_active_playlist_while_settings_is_busy_deletes_nothing_and_reports(self) -> None:
        from lwe_ui import models
        for slug in ("victim", "spare", "alpha"):
            playlists.save(slug, {"NAME": slug, "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min",
                                  "MEMBERS": ""})
        backend = models.Backend()
        changed: list[int] = []
        backend.playlistsChanged.connect(lambda: changed.append(1))
        pushes: list[bool] = []
        backend._sync_engine = lambda manual=False: pushes.append(manual)
        for slug, delete in (("victim", lambda: backend.deletePlaylist("victim")),
                             ("spare", backend.deleteActivePlaylist)):
            with self.subTest(slug=slug):
                playlists.set_active(slug)
                changed.clear()
                pushes.clear()
                with mock.patch.object(lock, "LOCK_WAIT_S", 0.3), self._held_elsewhere("settings"), \
                        self.assertLogs("lwe_ui", level="WARNING") as logged:
                    delete()
                self.assertEqual(len(logged.records), 1)
                self.assertTrue(paths.playlist_file(slug).exists(), "settings is taken first: nothing was deleted")
                self.assertEqual((len(changed), pushes), (0, []), "nothing saved, refreshed or sent")
                self.assertEqual(settings.load()["ACTIVE_PLAYLIST"], slug, "the busy store was not written")

    def test_the_rules_editor_opens_a_file_that_is_not_utf8(self) -> None:
        from lwe_ui import models
        backend = models.Backend()
        opened: list[str] = []
        backend.openPath = opened.append
        f = rules.file_for("pause-blacklist.txt")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"caf\xe9\n")
        before = (f.read_bytes(), f.stat().st_ino, f.stat().st_mtime_ns)
        backend.editBlacklist()
        self.assertEqual(opened, [str(f)])
        self.assertEqual((f.read_bytes(), f.stat().st_ino, f.stat().st_mtime_ns), before, "the open wrote the file")

    def test_meta_and_tags_writers_wait_at_most_the_lock_budget_in_total(self) -> None:
        from lwe_ui.storage import tags
        budget = 0.8
        cases = (("meta", lambda: meta.update("111", {"note": "worker"}),
                  lambda: meta.modify("111", lambda entry: {"note": "window"})),
                 ("tags", lambda: tags.set_state("111", "t", "good"), lambda: tags.remove("111")))
        real_held = lock.held
        for store, worker_call, window_call in cases:
            with self.subTest(store=store):
                entered = threading.Event()
                busy: list[BaseException] = []

                def held(name: str, *args, **kwargs):
                    if name == store and threading.current_thread().name == "worker":
                        entered.set()
                    return real_held(name, *args, **kwargs)

                def run_worker() -> None:
                    try:
                        worker_call()
                    except lock.StoreBusy as exc:
                        busy.append(exc)
                with mock.patch.object(lock, "LOCK_WAIT_S", budget), self._held_elsewhere(store), \
                        mock.patch.object(lock, "held", held):
                    worker = threading.Thread(target=run_worker, name="worker")
                    worker.start()
                    try:
                        self.assertTrue(entered.wait(10), "the worker never reached the file lock")
                        start = time.monotonic()
                        with self.assertRaises(lock.StoreBusy):
                            window_call()
                        waited = time.monotonic() - start
                    finally:
                        worker.join(10)
                self.assertLess(waited, budget * 1.5, f"the window thread waited {waited:.2f} s for {store}")
                self.assertEqual(len(busy), 1, "the worker met the busy store as well")

    def test_meta_and_tags_share_one_deadline_across_the_thread_mutex_and_the_file_lock(self) -> None:
        from lwe_ui.storage import tags
        budget = 0.8
        cases = (("meta", meta._WRITE_LOCK, lambda: meta.modify("111", lambda entry: {"note": "window"})),
                 ("tags", tags._WRITE_LOCK, lambda: tags.remove("111")))
        for store, mutex, window_call in cases:
            for hold_s, limit in ((0.4, 1.0), (1.6, 1.2)):
                with self.subTest(store=store, hold_s=hold_s):
                    taken, release = threading.Event(), threading.Event()

                    def hold_mutex() -> None:
                        with mutex:
                            taken.set()
                            release.wait(hold_s)
                    with mock.patch.object(lock, "LOCK_WAIT_S", budget), self._held_elsewhere(store):
                        holder = threading.Thread(target=hold_mutex)
                        holder.start()
                        try:
                            self.assertTrue(taken.wait(10), "the holder never took the thread mutex")
                            start = time.monotonic()
                            with self.assertRaises(lock.StoreBusy):
                                window_call()
                            waited = time.monotonic() - start
                        finally:
                            release.set()
                            holder.join(10)
                    self.assertLess(waited, limit, f"the window thread waited {waited:.2f} s for {store}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
