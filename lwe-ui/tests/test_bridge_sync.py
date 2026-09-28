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
while sync is held elsewhere. The editor's global dial is saved before its set-tuning. The engine
is an api_client recorder with a scripted status; the child process gets an environment built from
scratch.

Run: PYTHONPATH=src python3 tests/test_bridge_sync.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import contextlib
import copy
import os
import shutil
import subprocess
import sys
import tempfile
import threading
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
from lwe_ui.storage import backup, lock, paths, playlists, settings, wp  # noqa: E402

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

        def sync_all(run, classes=("BUNDLE",), wait_s=2.0):
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
                ("editor", lambda: ed.setScalingValue("fill"), lambda: ed.setRenderResolutionValue("sharpfx"), ed),
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
        return (("editor", ed, lambda changes: ed._persist_draft("", changes), ed.setRenderResolutionValue, "sharpfx"),
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
