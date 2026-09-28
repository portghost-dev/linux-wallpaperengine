"""The settings page, the deck popup and the editor save first.

A volume door sends set-volume resolved for the wallpaper on screen, its own VOLUME kept and mute
giving 0, and the store already holds the value when the verb goes out. The editor's AUDIO_REACTIVE
and MOUSE send the resolved value: the global default for a file without the key, and false under
the session override. A LIVE commit made while the engine is away saves and succeeds; a refused one
saves and says the engine did not answer; an engine file that could not be written says so. Every
window door counts pending as success and a refusal as failure. A restart-class commit rewrites
engine-env and never the unit. A backup import bundles with CURRENT, then rewrites engine-env, never
the unit. Two build-key edits within the debounce send one entry refresh and one re-show, freeze
kept; a burst carries every edit's rows, so an earlier SPEED still goes out after the re-show; a poll
while a burst waits sends nothing. A child that exits after its save leaves BUNDLE and CURRENT, and
the next drain re-shows. A refused wallpaper write sends nothing. A slider preview sends nothing
while sync is held elsewhere. The editor's global dial is saved before its set-tuning. A burst that
spans another change's pending record bundles it before its own push. A burst goes out by itself
600 ms after its last edit, a later edit restarting the delay. A live change or a first-sight bundle
inside a burst's debounce leaves the re-show to the burst, which clears the marker, and so do a
backup import and the readiness bundle of a service start. A backup restore records BUNDLE and
CURRENT before its store writes and holds the marker until the last one ends, so a drain admitted
before the first store write, or before the last write, cannot clear it; an import refused by a busy
store writes no pre-restore snapshot, so five of them leave the snapshots as they were. The panel's
import keeps the recovery snapshot rules: a failed import leaves recovery.json naming its snapshot, a
retry takes no snapshot, a success removes recovery.json, and a named snapshot that is gone refuses
the import with the way to clear it. The engine is an api_client recorder with a scripted status; the
child process gets an environment built from scratch.

Run: PYTHONPATH=src python3 tests/test_bridge_sync.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import contextlib
import copy
import functools
import dataclasses
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
from unittest import mock

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

_BOOT = tempfile.TemporaryDirectory(prefix="lwe-bridge-sync-boot-")
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = os.path.join(_BOOT.name, _sub) if _sub else _BOOT.name
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402

_APP = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])

from lwe_ui import deck_popup, editor, models, settings_bridge, version  # noqa: E402
from lwe_ui.engine import daemon_unit, marker, push  # noqa: E402
from lwe_ui.storage import backup, foreign, lock, paths, playlists, registry, settings, wp  # noqa: E402

OK = {"id": 1, "ok": True, "status": "done", "result": {}}
REFUSED = {"id": 1, "ok": False, "error": "no"}

CHILD = r"""
import os, sys
from PySide6.QtCore import QCoreApplication
app = QCoreApplication(sys.argv[:1])
from lwe_ui import editor
bridge = editor.EditorBridge()
bridge.open("111")
bridge.setScalingValue("fill")
sys.stdout.flush()
os._exit(0 if bridge._tickets else 3)
"""


class Recorder:
    """Every api_client verb recorded as (verb, args, kwargs), answered from a per-verb script, a
    done ok by default; a hook sees each call first. status answers the scripted status, or None
    with status_class."""

    def __init__(self, status: dict | None, status_class: str = "ok") -> None:
        self.calls: list[tuple] = []
        self.status_reply = status
        self.status_class = status_class
        self.script: dict[str, list] = {}
        self.hooks: dict = {}
        self._last: str | None = None

    def answer(self, verb: str, *steps) -> None:
        self.script.setdefault(verb, []).extend(steps)

    def status(self, sock=None):
        self.calls.append(("status", (), {}))
        if self.status_reply is None:
            self._last = self.status_class
            return None
        self._last = "ok"
        return copy.deepcopy(self.status_reply)

    def available(self, sock=None):
        return self.status_reply is not None

    def last_class(self):
        return self._last

    def __getattr__(self, verb: str):
        if verb.startswith("_"):
            raise AttributeError(verb)

        def call(*args, **kwargs):
            self.calls.append((verb, args, kwargs))
            if verb in self.hooks:
                self.hooks[verb](*args, **kwargs)
            steps = self.script.get(verb)
            step = steps.pop(0) if steps else OK
            if isinstance(step, str):
                self._last = step
                return None
            self._last = "ok" if step.get("ok") else "refused"
            return step
        return call

    def verbs(self) -> list[str]:
        return [verb for verb, _a, _k in self.calls if verb not in ("status", "ping")]


def status(current: str = "111", speed=1.0, pid: int = 4242, **extra) -> dict:
    return {"api": 1, "version": version.panel_stamp(), "pid": pid, "speed": speed,
            "current": {"id": current, "ui_id": current, "title": ""}, "schedule": {"enabled": False},
            "lanes": [{"id": "all", "playlist": "main"}], **extra}


class BridgeSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="lwe-bridge-sync-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        for key, sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"),
                         ("XDG_DATA_HOME", "d")):
            os.environ[key] = str(self.home / sub) if sub else str(self.home)
        paths.ensure_dirs()
        settings.save(settings.load())
        settings.update({"DETECT_MODE": "manual", "ENGINE_VOLUME": 20})
        playlists.save("main", {"NAME": "Main", "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min",
                                "MEMBERS": "111 222"})
        settings.update({"ACTIVE_PLAYLIST": "main"})
        for wid in ("111", "222"):
            d = paths.default_wallpapers_dir() / wid
            d.mkdir(parents=True, exist_ok=True)
            (d / "project.json").write_text('{"title": "WP %s", "type": "scene", "file": "scene.json"}' % wid,
                                            encoding="utf-8")
        self.env_writes: list[str] = []
        for name in ("write_env", "write_files"):
            patcher = mock.patch.object(daemon_unit, name,
                                        lambda *a, _n=name, **k: self.env_writes.append(_n) or "written")
            patcher.start()
            self.addCleanup(patcher.stop)
        marker.record_served(4242)
        self.backend = models.Backend()

    @contextlib.contextmanager
    def engine(self, reply: dict | None, status_class: str = "ok"):
        rec = Recorder(reply, status_class)
        with mock.patch.object(push, "api_client", rec), mock.patch.object(models, "api_client", rec), \
                mock.patch.object(editor, "api_client", rec), mock.patch.object(deck_popup, "api_client", rec), \
                mock.patch.object(settings_bridge, "api_client", rec):
            yield rec

    @contextlib.contextmanager
    def held_elsewhere(self, store: str):
        ready, release = threading.Event(), threading.Event()

        def hold() -> None:
            with lock.held(store):
                ready.set()
                release.wait(30)
        holder = threading.Thread(target=hold)
        holder.start()
        try:
            self.assertTrue(ready.wait(10))
            yield
        finally:
            release.set()
            holder.join(10)

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
            "QT_QPA_PLATFORM": "offscreen",
        }

    def _volume_doors(self):
        page = settings_bridge.SettingsBridge(self.backend)
        popup = deck_popup.DeckPopupBridge(self.backend)
        popup.syncCurrent("111")
        ed = editor.EditorBridge(self.backend)
        ed.open("111")
        return (("settings page", lambda v: page.commit("ENGINE_VOLUME", v)),
                ("deck", popup.setGlobalVolume), ("editor", ed.setGlobalVolume))

    def test_a_volume_door_sends_the_value_resolved_for_the_wallpaper_on_screen_after_the_save(self) -> None:
        wp.update_set("111", {"VOLUME": 70})
        for name, door in self._volume_doors():
            for mute, expected in ((False, 70), (True, 0)):
                with self.subTest(door=name, mute=mute):
                    settings.update({"OVERRIDE_MUTE": mute})
                    stored: list = []
                    with self.engine(status()) as rec:
                        rec.hooks["set_volume"] = lambda v: stored.append(settings.load()["ENGINE_VOLUME"])
                        self.assertTrue(door(40 + int(mute)))
                    sent = [args[0] for verb, args, _k in rec.calls if verb == "set_volume"]
                    self.assertEqual(sent, [expected], "the resolved volume, never the bare 40")
                    self.assertEqual(stored, [40 + int(mute)], "the store holds the value when the verb goes out")

    def test_the_editors_audio_and_mouse_send_the_resolved_value(self) -> None:
        wp.update_set("111", {"AUDIO_REACTIVE": False, "MOUSE": False})
        settings.update({"AUDIO_REACTIVE_DEFAULT": True, "MOUSE_DEFAULT": True})
        ed = editor.EditorBridge(self.backend)
        ed.open("111")
        with self.engine(status()) as rec:
            self.assertTrue(ed.setBoolOverride("AUDIO_REACTIVE", ""))
            self.assertTrue(ed.setBoolOverride("MOUSE", ""))
        self.assertEqual([args for verb, args, _k in rec.calls if verb in ("set_audio", "set_mouse")],
                         [(True,), (True,)], "a file without the key gets the global default")
        settings.update({"OVERRIDE_AUDIO_OFF": True, "OVERRIDE_MOUSE_OFF": True})
        with self.engine(status()) as rec:
            self.assertTrue(ed.setBoolOverride("AUDIO_REACTIVE", "true"))
            self.assertTrue(ed.setBoolOverride("MOUSE", "true"))
        self.assertEqual([args for verb, args, _k in rec.calls if verb in ("set_audio", "set_mouse")],
                         [(False,), (False,)], "the session override wins")

    def test_an_away_live_commit_saves_and_succeeds_and_a_refused_one_says_so(self) -> None:
        page = settings_bridge.SettingsBridge(self.backend)
        failures: list = []
        page.commitFailed.connect(lambda keys, reason: failures.append((list(keys), reason)))
        with self.engine(None, "away") as rec:
            self.assertTrue(page.commit("ENGINE_VOLUME", 33))
        self.assertEqual((rec.verbs(), failures), ([], []))
        self.assertEqual(settings.load()["ENGINE_VOLUME"], 33)
        with self.engine(status()) as rec:
            rec.answer("set_volume", REFUSED)
            self.assertFalse(page.commit("ENGINE_VOLUME", 44))
        self.assertIn("set_volume", rec.verbs())
        self.assertEqual(failures, [(["ENGINE_VOLUME"], "The engine did not answer.")])
        self.assertEqual(settings.load()["ENGINE_VOLUME"], 44, "the value stays saved")

    def test_a_reset_the_engine_refused_is_a_failure_and_an_away_one_succeeds(self) -> None:
        page = settings_bridge.SettingsBridge(self.backend)
        failures: list = []
        page.commitFailed.connect(lambda keys, reason: failures.append((list(keys), reason)))
        with self.engine(status()) as rec:
            rec.answer("set_particles", {"id": 1, "ok": False, "error": "rebuild failed: out of memory"})
            self.assertFalse(page.resetConfig())
        self.assertIn("set_particles", rec.verbs())
        self.assertEqual(failures, [(["Configuration"], "The engine did not answer.")])
        with self.engine(None, "away"):
            self.assertTrue(page.resetConfig())
        self.assertEqual(len(failures), 1)

    def test_an_import_the_engine_refused_is_a_failure_and_its_restore_stays_written(self) -> None:
        page = settings_bridge.SettingsBridge(self.backend)
        failures: list = []
        page.commitFailed.connect(lambda keys, reason: failures.append((list(keys), reason)))
        order: list = []
        page.receiptChanged.connect(lambda: order.append("receipt"))
        page.commitFailed.connect(lambda keys, reason: order.append("failed"))
        settings.update({"ENGINE_FPS": 90})
        archive = self.home / "fps-90.lwebackup"
        self.assertEqual(backup.export_to(archive)["errors"], [])
        settings.update({"ENGINE_FPS": 30})
        with self.engine(status()) as rec:
            rec.answer("set_particles", REFUSED)
            self.assertFalse(page.importBackup(str(archive)))
        self.assertEqual(failures, [(["Configuration"], "The engine did not answer.")])
        self.assertEqual(order, ["receipt", "failed"], "the failure comes after the receipt")
        self.assertEqual(settings.load()["ENGINE_FPS"], 90, "the restore stays written")
        self.assertFalse((paths.state_dir() / "backups" / "recovery.json").exists(),
                         "settle had the stores' verdict, not the sync's")
        with self.engine(None, "away"):
            self.assertTrue(page.importBackup(str(archive)))
        self.assertEqual(len(failures), 1)

    def test_an_exceptions_edit_the_engine_refused_is_a_failure_and_an_away_one_succeeds(self) -> None:
        page = settings_bridge.SettingsBridge(self.backend)
        with self.engine(status()) as rec:
            rec.answer("set_fullscreen_ignore", REFUSED)
            self.assertFalse(page.addException("mpv"))
        self.assertIn("mpv", page.exceptions(), "the list stays saved")
        with self.engine(None, "away"):
            self.assertTrue(page.removeException("mpv"))

    def test_an_app_list_edit_the_engine_refused_is_a_failure_and_an_away_one_succeeds(self) -> None:
        page = settings_bridge.SettingsBridge(self.backend)
        with self.engine(status()) as rec:
            rec.answer("set_app_conditions", REFUSED)
            self.assertFalse(page.addAppEntry("mpv"))
        self.assertIn("mpv", page.appEntries(), "the list stays saved")
        with self.engine(None, "away"):
            self.assertTrue(page.removeAppEntry("mpv"))

    def test_an_engine_file_that_could_not_be_written_says_so(self) -> None:
        page = settings_bridge.SettingsBridge(self.backend)
        failures: list = []
        page.commitFailed.connect(lambda keys, reason: failures.append((list(keys), reason)))
        with mock.patch.object(daemon_unit, "write_env", lambda *a, **k: "no screens"), self.engine(status()):
            self.assertFalse(page.commit("ENGINE_LAYER", "top"))
        self.assertEqual(failures, [(["ENGINE_LAYER"], "Saved, but the engine file could not be written.")])
        self.assertEqual(settings.load()["ENGINE_LAYER"], "top", "the value stays saved")

    def test_every_window_door_counts_pending_as_success_and_a_refusal_as_failure(self) -> None:
        popup = deck_popup.DeckPopupBridge(self.backend)
        popup.syncCurrent("111")
        ed = editor.EditorBridge(self.backend)
        ed.open("111")
        failures: list = []
        popup.commitFailed.connect(lambda keys: failures.append(list(keys)))
        ed.commitFailed.connect(lambda keys: failures.append(list(keys)))

        def burst(value: str) -> bool:
            saved = ed.setScalingValue(value)
            ed._reshow.stop()
            ed._fire_reshow()
            return saved
        doors = (("deck global", "set_volume", ["ENGINE_VOLUME"], popup.setGlobalVolume, 30, 31, False),
                 ("editor global", "set_fps", ["ENGINE_FPS"], ed.setGlobalFps, "90", "91", False),
                 ("editor wallpaper live", "set_volume", ["VOLUME"], ed.setVolumeValue, 50, 51, True),
                 ("editor wallpaper build", "show", ["SCALING"], burst, "fill", "fit", True))
        for name, verb, keys, door, away, refused, saved in doors:
            with self.subTest(door=name):
                failures.clear()
                with self.engine(None, "away"):
                    self.assertTrue(door(away), "a pending change is saved and OK")
                self.assertEqual(failures, [])
                marker.clear(marker.read()["generation"])
                with self.engine(status()) as rec:
                    rec.answer(verb, *[REFUSED] * 5)
                    self.assertEqual(door(refused), saved)
                self.assertEqual(failures, [keys], "a refusal is the failure grammar")
                marker.clear(marker.read()["generation"])

    def test_a_restart_class_commit_rewrites_engine_env_and_never_the_unit(self) -> None:
        page = settings_bridge.SettingsBridge(self.backend)
        with self.engine(status()) as rec:
            self.assertTrue(page.commit("ENGINE_LAYER", "top"))
        self.assertEqual(self.env_writes, ["write_env"])
        self.assertEqual(settings.load()["ENGINE_LAYER"], "top")
        self.assertEqual(rec.verbs(), [])

    def test_a_backup_import_bundles_with_current_then_rewrites_engine_env(self) -> None:
        page = settings_bridge.SettingsBridge(self.backend)
        archive = self.home / "a.lwebackup"
        archive.write_text("{}", encoding="utf-8")
        steps: list = []
        receipt = {"counts": {}, "followups": [{"kind": "engine-restart"}]}

        def sync_all(run, classes=("BUNDLE",), wait_s=2.0, defer_current=False):
            steps.append(("sync_all", run, tuple(classes)))
            return push.Outcome("applied")
        with mock.patch.object(backup, "preflight", lambda path: {}), \
                mock.patch.object(backup, "apply", lambda plan: dict(receipt)), \
                mock.patch.object(push, "sync_all", sync_all), \
                mock.patch.object(daemon_unit, "write_env", lambda *a, **k: steps.append("write_env") or "written"):
            self.assertTrue(page.importBackup(str(archive)))
        self.assertEqual(steps, [("sync_all", "window", ("BUNDLE", "CURRENT")), "write_env"])
        self.assertNotIn("write_files", self.env_writes)

    def test_two_build_edits_within_the_debounce_send_one_entry_refresh_and_one_reshow(self) -> None:
        ed = editor.EditorBridge(self.backend)
        ed.open("111")
        popup = deck_popup.DeckPopupBridge(self.backend)
        popup.syncCurrent("111")
        for name, first, second, bridge in (
                ("editor", lambda: ed.setScalingValue("fill"), lambda: ed.setClampValue("SSFACTOR", "0.5"), ed),
                ("deck", lambda: popup.setScaling("stretch"), lambda: popup.setScaling("fit"), popup)):
            with self.subTest(door=name):
                with self.engine(status(speed=0.0)) as rec:
                    self.assertTrue(first())
                    self.assertTrue(second())
                    self.assertEqual(rec.verbs(), [], "nothing goes out inside the debounce")
                    self.assertTrue(bridge._reshow.isActive())
                    bridge._reshow.stop()
                    bridge._fire_reshow()
                verbs = rec.verbs()
                self.assertEqual(verbs.count("playlist_set"), 1, verbs)
                self.assertEqual(verbs.count("show"), 1, verbs)
                self.assertEqual(verbs, ["playlist_set", "show", "set_tuning", "set_speed"])
                self.assertEqual(rec.calls[-1], ("set_speed", (0.0,), {}), "the re-show keeps the freeze")
                self.assertEqual(marker.read()["classes"], [])

    def _burst_doors(self):
        ed = editor.EditorBridge(self.backend)
        ed.open("111")
        popup = deck_popup.DeckPopupBridge(self.backend)
        popup.syncCurrent("111")
        return (("editor", ed, lambda changes: ed._persist_draft("", changes),
                 functools.partial(ed.setClampValue, "SSFACTOR"), "0.5"),
                ("deck", popup, popup._write_wp, popup.setScaling, "stretch"))

    def test_a_burst_carries_every_edit_so_an_earlier_speed_goes_out_after_the_reshow(self) -> None:
        for name, bridge, mixed, build, value in self._burst_doors():
            with self.subTest(door=name):
                wp.update_set("111", {"SPEED": None})
                with self.engine(status(speed=1.0)) as rec:
                    self.assertTrue(mixed({"SPEED": 2.0, "SCALING": "fill"}))
                    self.assertTrue(build(value))
                    self.assertEqual(rec.verbs(), [], "both edits wait for the burst")
                    bridge._reshow.stop()
                    bridge._fire_reshow()
                self.assertEqual(rec.verbs(), ["playlist_set", "show", "set_tuning", "set_speed", "set_speed"])
                self.assertEqual(rec.calls[-1], ("set_speed", (2.0,), {}), "the earlier SPEED lands after the re-show")

    def test_a_poll_while_a_burst_waits_sends_nothing_then_the_burst_reshows_once(self) -> None:
        self.backend._engine_pid_seen = 4242
        for name, bridge, _mixed, build, value in self._burst_doors():
            with self.subTest(door=name):
                with self.engine(status()) as rec:
                    self.assertTrue(build(value))
                    self.assertTrue(self.backend.delivery_due())
                    self.backend.status()
                    self.assertEqual(rec.verbs(), [], "the poll leaves the waiting burst to its delivery")
                    bridge._reshow.stop()
                    bridge._fire_reshow()
                    self.assertFalse(self.backend.delivery_due())
                    self.backend.status()
                self.assertEqual(rec.verbs().count("show"), 1, rec.verbs())
                self.assertEqual(marker.read()["classes"], [])

    def test_a_burst_that_spans_another_pending_change_bundles_it_before_its_own_push(self) -> None:
        with self.engine(status()) as rec:
            ed = editor.EditorBridge(self.backend)
            ed.open("111")
            page = settings_bridge.SettingsBridge(self.backend)
            failed: list = []
            page.commitFailed.connect(lambda keys, reason: failed.append((list(keys), reason)))
            self.assertTrue(ed.setScalingValue("fill"))
            with self.held_elsewhere("sync"):
                self.assertTrue(page.commit("ENGINE_VOLUME", 40))
            self.assertEqual((failed, marker.read()["classes"]), ([], ["BUNDLE", "CURRENT"]))
            self.assertTrue(ed.setTextureDetailValue("full"))
            ed._reshow.stop()
            ed._fire_reshow()
            self.assertEqual([args for verb, args, _k in rec.calls if verb == "set_volume"], [(40,)])
            self.assertEqual(rec.verbs().count("show"), 1, rec.verbs())
            self.assertEqual(marker.read()["classes"], [])

    def test_a_burst_goes_out_by_itself_600_ms_after_the_last_edit(self) -> None:
        ed = editor.EditorBridge(self.backend)
        ed.open("111")
        popup = deck_popup.DeckPopupBridge(self.backend)
        popup.syncCurrent("111")

        def pump(until: float) -> None:
            while time.monotonic() < until:
                _APP.processEvents()
                time.sleep(0.005)

        for name, first, second in (
                ("editor", lambda: ed.setScalingValue("fill"), lambda: ed.setClampValue("SSFACTOR", "0.5")),
                ("deck", lambda: popup.setScaling("stretch"), lambda: popup.setScaling("fit"))):
            with self.subTest(door=name):
                with self.engine(status()) as rec:
                    start = time.monotonic()
                    self.assertTrue(first())
                    pump(start + 0.4)
                    self.assertEqual(rec.verbs(), [], "nothing goes out inside the debounce")
                    restart = time.monotonic()
                    self.assertTrue(second())
                    pump(restart + 0.45)
                    self.assertEqual(rec.verbs(), [], "a later edit restarts the delay")
                    pump(restart + 0.9)
                self.assertEqual(rec.verbs().count("show"), 1, rec.verbs())
                self.assertEqual(marker.read()["classes"], [])

    def _live_doors(self):
        ed = editor.EditorBridge(self.backend)
        ed.open("111")
        popup = deck_popup.DeckPopupBridge(self.backend)
        popup.syncCurrent("111")
        return (("editor, the wallpaper's volume", ed, lambda: ed.setScalingValue("fill"), lambda: ed.setVolumeValue(25)),
                ("editor, the wallpaper's volume again", ed, lambda: ed.setScalingValue("fill"),
                 lambda: ed.setVolumeValue(77)),
                ("deck, the global volume", popup, lambda: popup.setScaling("stretch"),
                 lambda: popup.setGlobalVolume(30)),
                ("deck, the wallpaper's volume", popup, lambda: popup.setScaling("fill"),
                 lambda: popup._write_wp({"VOLUME": 77})))

    def test_a_live_change_inside_a_burst_leaves_the_reshow_to_the_burst(self) -> None:
        self.backend._engine_pid_seen = 4242
        for name, bridge, build, live in self._live_doors():
            with self.subTest(door=name):
                marker.clear(marker.read()["generation"])
                with self.engine(status()) as rec:
                    self.assertTrue(build())
                    self.assertTrue(self.backend.delivery_due())
                    self.assertTrue(live())
                    self.assertEqual(rec.verbs().count("show"), 0, rec.verbs())
                    self.assertEqual(marker.read()["classes"], ["CURRENT"])
                    bridge._reshow.stop()
                    bridge._fire_reshow()
                    self.backend.status()
                self.assertEqual(rec.verbs().count("show"), 1, rec.verbs())
                self.assertEqual(marker.read()["classes"], [])

    def test_a_first_sight_bundle_inside_a_burst_leaves_the_reshow_to_the_burst(self) -> None:
        for name, bridge, build, _live in self._live_doors()[1::2]:
            with self.subTest(door=name):
                self.backend._engine_pid_seen = None
                marker.clear(marker.read()["generation"])
                with self.engine(status()) as rec:
                    self.assertTrue(build())
                    self.backend.status()
                    self.assertEqual(rec.verbs().count("show"), 0, rec.verbs())
                    bridge._reshow.stop()
                    bridge._fire_reshow()
                self.assertEqual(rec.verbs().count("show"), 1, rec.verbs())
                self.assertEqual(marker.read()["classes"], [])

    def test_backup_store_write_has_prior_pending_intent(self) -> None:
        wp.update_set("111", {"SCALING": "fill"})
        settings.update({"ENGINE_FPS": "90"})
        archive = self.home / "review.lwebackup"
        backup.export_to(archive)
        wp.update_set("111", {"SCALING": "fit"})
        settings.update({"ENGINE_FPS": "30"})
        page = settings_bridge.SettingsBridge(self.backend)
        real_apply = backup.apply

        def interrupted_after_store(plan):
            receipt = real_apply(plan)
            self.assertFalse(receipt.get("refused"), receipt)
            raise SystemExit("injected death after store writes and before push")

        with self.engine(status()) as rec, mock.patch.object(backup, "apply", interrupted_after_store):
            with self.assertRaises(SystemExit):
                page.importBackup(str(archive))
        self.assertEqual((settings.load()["ENGINE_FPS"], wp.load_set("111")["SCALING"], rec.verbs()),
                         (90, "fill", []))
        self.assertEqual(marker.read()["classes"], ["BUNDLE", "CURRENT"])

    def _archive(self) -> Path:
        """A backup holding ENGINE_FPS 90 and wallpaper 111 at SCALING fill, with the stores then at 30
        and fit."""
        wp.update_set("111", {"SCALING": "fill"})
        settings.update({"ENGINE_FPS": "90"})
        archive = self.home / "review.lwebackup"
        self.assertFalse(backup.export_to(archive)["errors"])
        wp.update_set("111", {"SCALING": "fit"})
        settings.update({"ENGINE_FPS": "30"})
        return archive

    def test_restore_can_be_cleared_before_writes(self) -> None:
        archive = self._archive()
        original = registry.STORES
        drained: list = []

        def before_first_store(plan, receipt):
            def drain() -> None:
                try:
                    drained.append(push.sync_all("command"))
                except lock.StoreBusy as exc:
                    drained.append(exc)
            worker = threading.Thread(target=drain)
            worker.start()
            worker.join(5)
            self.assertFalse(worker.is_alive(), "drain stuck")
            return original[0].apply(plan, receipt)

        first = dataclasses.replace(original[0], apply=before_first_store)
        with self.engine(status()) as rec, mock.patch.object(registry, "STORES", (first, *original[1:])):
            receipt = backup.apply(backup.preflight(archive))
        self.assertFalse(receipt.get("refused"), receipt)
        self.assertEqual((settings.load()["ENGINE_FPS"], wp.load_set("111")["SCALING"]), (90, "fill"))
        self.assertEqual([args for verb, args, _k in rec.calls if verb == "set_fps"], [])
        self.assertEqual(marker.read()["classes"], ["BUNDLE", "CURRENT"],
                         "restore must remain recoverable if caller dies before its push")

    def test_a_drain_before_the_restores_last_write_cannot_clear_its_marker(self) -> None:
        archive = self._archive()
        real = foreign.apply_plan
        drained: list = []

        def before_last_write(plan, receipt):
            def drain() -> None:
                try:
                    drained.append(push.sync_all("command"))
                except lock.StoreBusy as exc:
                    drained.append(exc)
            worker = threading.Thread(target=drain)
            worker.start()
            worker.join(5)
            self.assertFalse(worker.is_alive(), "drain stuck")
            return real(plan, receipt)

        with self.engine(status()) as rec, mock.patch.object(foreign, "apply_plan", before_last_write):
            receipt = backup.apply(backup.preflight(archive))
        self.assertFalse(receipt.get("refused"), receipt)
        self.assertEqual([type(d) for d in drained], [lock.StoreBusy])
        self.assertEqual([args for verb, args, _k in rec.calls if verb == "set_fps"], [])
        self.assertEqual(marker.read()["classes"], ["BUNDLE", "CURRENT"])

    def test_refused_imports_leave_the_snapshots_as_they_were(self) -> None:
        archive = self._archive()
        backups = paths.state_dir() / "backups"
        self.assertFalse(backup.apply(backup.preflight(archive)).get("refused"))
        before = sorted(p.name for p in backups.iterdir())
        self.assertEqual(len(before), 1)
        with self.held_elsewhere("rules"), mock.patch.object(lock, "LOCK_WAIT_S", 0.05):
            for _ in range(5):
                receipt = backup.apply(backup.preflight(archive))
                self.assertTrue(receipt.get("refused"), receipt)
                self.assertTrue(receipt["errors"][-1]["reason"].startswith("Store busy"), receipt["errors"])
        self.assertEqual(sorted(p.name for p in backups.iterdir()), before)

    def test_the_panels_import_keeps_the_recovery_snapshot_rules(self) -> None:
        archive = self._archive()
        backups = paths.state_dir() / "backups"
        rec = backups / "recovery.json"
        page = settings_bridge.SettingsBridge(self.backend)
        failed: list = []
        page.commitFailed.connect(lambda keys, reason: failed.append(reason))

        def unwritable(plan, receipt):
            receipt["errors"].append({"file": "rules/pause-blacklist.txt", "reason": "could not be written: full"})
            return False
        first_fails = (dataclasses.replace(registry.STORES[0], apply=unwritable), *registry.STORES[1:])
        last_fails = (*registry.STORES[:-1], dataclasses.replace(registry.STORES[-1], apply=unwritable))

        def snapshots() -> list:
            return sorted(backups.glob("pre-restore-*.lwebackup"))

        def named() -> str | None:
            return json.loads(rec.read_text(encoding="utf-8"))["snapshot"] if rec.exists() else None

        with self.engine(status()):
            with mock.patch.object(registry, "STORES", first_fails):
                self.assertFalse(page.importBackup(str(archive)), "refused after its snapshot")
            first = snapshots()
            with mock.patch.object(registry, "STORES", last_fails):
                self.assertTrue(page.importBackup(str(archive)), "the retry fails part way")
            self.assertEqual((len(first), snapshots(), named()), (1, first, first[0].name))
            self.assertTrue(page.importBackup(str(archive)))
            self.assertEqual((snapshots(), named()), (first, None), "the success took a snapshot or kept the record")
            with mock.patch.object(registry, "STORES", last_fails):
                self.assertTrue(page.importBackup(str(archive)))
            second = [p for p in snapshots() if p not in first]
            self.assertEqual(len(second), 1, "a new run of failures takes its own snapshot")
            self.assertEqual(named(), second[0].name)
            rec.write_text(json.dumps({"snapshot": "pre-restore-20200101-000000.lwebackup",
                                       "since": "2020-01-01T00:00:00"}), encoding="utf-8")
            settings.update({"ENGINE_FPS": "30"})
            self.assertFalse(page.importBackup(str(archive)))
        gone = backups / "pre-restore-20200101-000000.lwebackup"
        self.assertEqual(failed, ["could not be written: full",
                                  f"Nothing was imported: {gone}, the snapshot kept from before an earlier failed "
                                  f"import, is missing. Deleting {rec} clears this block."])
        self.assertEqual((settings.load()["ENGINE_FPS"], rec.exists()), (30, True))

    def test_the_panels_import_settles_inside_its_restore_lock(self) -> None:
        archive = self._archive()
        depth, seen = [0], []
        real_restoring, real_settle = backup.restoring, backup.settle

        @contextlib.contextmanager
        def counted():
            with real_restoring() as got:
                depth[0] += 1
                try:
                    yield got
                finally:
                    depth[0] -= 1

        def settle(r, failed):
            seen.append(depth[0])
            return real_settle(r, failed)
        page = settings_bridge.SettingsBridge(self.backend)
        with self.engine(status()), mock.patch.object(backup, "restoring", counted), \
                mock.patch.object(backup, "settle", settle):
            self.assertTrue(page.importBackup(str(archive)))
        self.assertEqual(seen, [1], "settle ran outside the panel's restore lock")

    def test_import_during_burst(self) -> None:
        archive = self._archive()
        for name in ("editor", "deck"):
            with self.subTest(door=name):
                bridge = (editor.EditorBridge(self.backend) if name == "editor"
                          else deck_popup.DeckPopupBridge(self.backend))
                if name == "editor":
                    bridge.open("111")
                    build = bridge.setScalingValue
                else:
                    bridge.syncCurrent("111")
                    build = bridge.setScaling
                page = settings_bridge.SettingsBridge(self.backend)
                with self.engine(status()) as rec:
                    self.assertTrue(build("stretch"))
                    self.assertTrue(self.backend.delivery_due())
                    self.assertTrue(page.importBackup(str(archive)))
                    before = rec.verbs().count("show")
                    bridge._reshow.stop()
                    bridge._fire_reshow()
                self.assertEqual((before, rec.verbs().count("show")), (0, 1))
                self.assertEqual(marker.read()["classes"], [])

    def test_a_launch_holds_sync_so_a_drain_in_between_ends_busy_and_the_record_stays(self) -> None:
        from lwe_ui.cli.verbs import service
        for action, answer in (("restart", status(pid=4242)), ("start", None)):
            with self.subTest(action=action):
                (paths.panel_state_dir() / "sync-pending").unlink(missing_ok=True)
                drained = []

                def launch(args):
                    thread = threading.Thread(target=lambda: drained.append(push.sync_all("command", wait_s=0.2)),
                                              daemon=True)
                    thread.start()
                    thread.join(5)
                    return ""

                with self.engine(answer, "ok" if answer else "away"), mock.patch.object(service, "_call", launch), \
                        mock.patch.object(push, "wait_ready", side_effect=KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        service._launch([action, "lwe-engine.service"])
                self.assertEqual([(o.kind, o.reason) for o in drained], [("pending", "busy")])
                self.assertEqual(marker.read()["classes"], ["BUNDLE"])

    def test_a_rejected_restart_leaves_an_ordinary_bundle_the_next_drain_clears(self) -> None:
        from lwe_ui.cli.verbs import service
        with mock.patch.object(service, "_call", side_effect=service._Stop("restart rejected")):
            with self.assertRaises(service._Stop):
                service._launch(["restart", "lwe-engine.service"])
        self.assertEqual(marker.read()["classes"], ["BUNDLE"])
        with self.engine(status(pid=4242)) as rec:
            first = push.sync_all("command").kind
            sent = len(rec.verbs())
            for _ in range(3):
                self.backend._drain()
        self.assertEqual((first, marker.read()["classes"], self.backend._drain_failures), ("applied", [], 0))
        self.assertGreater(sent, 0)
        self.assertEqual(len(rec.verbs()), sent, "later polls send nothing")

    def test_a_second_restart_during_a_restart_ends_busy_and_the_first_keeps_its_record(self) -> None:
        from lwe_ui.cli.verbs import service
        second = []
        started = threading.Event()

        def restart_again():
            try:
                service._launch(["restart", "lwe-engine.service"])
                second.append("ran")
            except lock.StoreBusy:
                second.append("busy")
            except BaseException:
                second.append("ran")

        def launch(args):
            if not started.is_set():
                started.set()
                thread = threading.Thread(target=restart_again, daemon=True)
                thread.start()
                thread.join(5)
            return ""

        with mock.patch.object(lock, "LOCK_WAIT_S", 0.2), self.engine(status(pid=4242)), \
                mock.patch.object(service, "_call", launch), \
                mock.patch.object(push, "wait_ready", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                service._launch(["restart", "lwe-engine.service"])
        self.assertEqual((second, marker.read()["classes"]), (["busy"], ["BUNDLE"]))

    def test_ready_tick_during_burst(self) -> None:
        bridge = editor.EditorBridge(self.backend)
        bridge.open("111")
        with self.engine(status()) as rec:
            self.backend._bundle_when_ready(4000)
            self.assertTrue(bridge.setScalingValue("fill"))
            self.assertTrue(self.backend.delivery_due())
            self.backend._ready_tick()
            before = rec.verbs().count("show")
            bridge._reshow.stop()
            bridge._fire_reshow()
        self.assertEqual((before, rec.verbs().count("show")), (0, 1))
        self.assertEqual(marker.read()["classes"], [])

    def test_the_editors_global_dial_is_saved_before_its_set_tuning(self) -> None:
        ed = editor.EditorBridge(self.backend)
        ed.open("111")
        stored: list = []
        with self.engine(status()) as rec:
            rec.hooks["set_tuning"] = lambda **kw: stored.append(settings.load()["ENGINE_CLASSIC_EXP"])
            self.assertTrue(ed.setAudioDial("GLOW_RADIUS", 0.5))
        value = editor._quality_to_dial(editor.AUDIO_DIALS["GLOW_RADIUS"], 0.5)
        self.assertEqual(stored, [value], "the store holds the dial when set-tuning goes out")

    def test_a_child_that_exits_after_its_save_leaves_bundle_and_current_for_the_next_drain(self) -> None:
        child = subprocess.run([sys.executable, "-c", CHILD], env=self._env(), capture_output=True,
                               text=True, timeout=120)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(wp.load_set("111").get("SCALING"), "fill")
        self.assertEqual(marker.read()["classes"], ["BUNDLE", "CURRENT"])
        self.backend._engine_pid_seen = 4242
        with self.engine(status()) as rec:
            self.backend.status()
        self.assertEqual(rec.verbs().count("show"), 1, "the next drain re-shows")
        self.assertEqual(marker.read()["classes"], [])

    def test_a_refused_wallpaper_write_sends_nothing(self) -> None:
        ed = editor.EditorBridge(self.backend)
        popup = deck_popup.DeckPopupBridge(self.backend)
        failures: list = []
        ed.commitFailed.connect(lambda keys: failures.append(list(keys)))
        popup.commitFailed.connect(lambda keys: failures.append(list(keys)))
        paths.wp_file("111").write_text('SCALING="fill\nVOLUME="40\n', encoding="utf-8")
        ed.open("111")
        popup.syncCurrent("111")
        with self.engine(status()) as rec:
            self.assertFalse(ed.setScalingValue("fit"))
            self.assertFalse(ed.setVolumeValue(50))
            self.assertFalse(popup.setScaling("stretch"))
        self.assertEqual(rec.verbs(), [])
        self.assertEqual(failures, [["SCALING"], ["VOLUME"], ["SCALING"]])
        self.assertEqual(ed._tickets + popup._tickets, [], "no burst will deliver a refused write")
        self.assertFalse(ed._reshow.isActive() or popup._reshow.isActive())

    def test_a_preview_sends_nothing_while_sync_is_held_elsewhere(self) -> None:
        ed = editor.EditorBridge(self.backend)
        ed.open("111")
        popup = deck_popup.DeckPopupBridge(self.backend)
        popup.syncCurrent("111")
        with self.engine(status()) as rec:
            with self.held_elsewhere("sync"):
                for bridge in (ed, popup):
                    bridge.previewLive("volume", 40)
                    bridge._preview.stop()
                    bridge._fire_preview()
            self.assertEqual(rec.verbs(), [], "a busy sync skips the frame")
            for bridge in (ed, popup):
                bridge.previewLive("volume", 41)
                bridge._preview.stop()
                bridge._fire_preview()
        self.assertEqual([args for verb, args, _k in rec.calls if verb == "set_volume"], [(41,), (41,)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
