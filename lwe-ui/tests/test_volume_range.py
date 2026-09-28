"""Volume up to 128 in the store and at every door.

A hand-written ENGINE_VOLUME of 120 loads as it is, and 129 loads as 128 with the load's clamp
warning; the settings page takes 128 and refuses 129 through the schema. The deck popup's and the
editor's global doors save 120 and send set-volume 120 for a wallpaper on screen that sets no VOLUME
of its own, the editor's wallpaper door writes VOLUME=120, the three slider previews send 120 and
persist nothing, and 129 becomes 128 at every popup and editor door; a restored wallpaper VOLUME
keeps to 0 to 128. The engine is test_change_runner.py's api_client Recorder answering its
status(); nothing runs a subprocess.

Run: PYTHONPATH=src python3 tests/test_volume_range.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import contextlib
import os
import shutil
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

_BOOT = tempfile.TemporaryDirectory(prefix="lwe-volume-boot-")
_FOLDERS = (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d"),
            ("XDG_CACHE_HOME", "cache"))
for _key, _sub in _FOLDERS:
    os.environ[_key] = os.path.join(_BOOT.name, _sub) if _sub else _BOOT.name
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

_APP = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])

from test_change_runner import Recorder, status  # noqa: E402
from lwe_ui import constants as C, deck_popup, editor, models, settings_bridge  # noqa: E402
from lwe_ui.engine import daemon_unit, marker, push  # noqa: E402
from lwe_ui.storage import migrate, paths, playlists, settings, wp  # noqa: E402


def volumes(rec: Recorder) -> list[int]:
    """The volume of every set-volume the recorder saw, in order."""
    return [args[0] for verb, args, _kwargs in rec.calls if verb == "set_volume"]


class VolumeRangeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="lwe-volume-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        for key, sub in _FOLDERS:
            os.environ[key] = str(self.home / sub) if sub else str(self.home)
        paths.ensure_dirs()
        settings.save(settings.load())
        settings.update({"DETECT_MODE": "manual", "ENGINE_VOLUME": 20})
        playlists.save("main", {"NAME": "Main", "MODE": "shuffle", "INTERVAL": 900, "UNIT": "min",
                                "MEMBERS": "111 222"})
        settings.update({"ACTIVE_PLAYLIST": "main"})
        for wid in ("111", "222"):
            folder = paths.default_wallpapers_dir() / wid
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "project.json").write_text('{"title": "WP %s", "type": "scene", "file": "scene.json"}' % wid,
                                                 encoding="utf-8")
        self.runs: list = []
        for patcher in (mock.patch.object(daemon_unit, "enumerate_outputs", lambda: ["DP-1"]),
                        mock.patch.object(daemon_unit.subprocess, "run", lambda argv, **kw: self.runs.append(argv))):
            patcher.start()
            self.addCleanup(patcher.stop)
        marker.record_served(4242)
        self.backend = models.Backend()

    def tearDown(self) -> None:
        self.assertEqual(self.runs, [], "a subprocess ran")

    @contextlib.contextmanager
    def engine(self):
        """test_change_runner's Recorder answering its status(), in place of every bridge's api_client."""
        rec = Recorder(status())
        with mock.patch.object(push, "api_client", rec), mock.patch.object(models, "api_client", rec), \
                mock.patch.object(editor, "api_client", rec), mock.patch.object(deck_popup, "api_client", rec), \
                mock.patch.object(settings_bridge, "api_client", rec):
            yield rec

    def doors(self) -> tuple[deck_popup.DeckPopupBridge, editor.EditorBridge]:
        """The deck popup and the editor, each seated on 111, the wallpaper on screen."""
        popup = deck_popup.DeckPopupBridge(self.backend)
        popup.syncCurrent("111")
        ed = editor.EditorBridge(self.backend)
        ed.open("111")
        ed.syncCurrent("111")
        return popup, ed

    def test_a_hand_written_volume_up_to_128_loads_as_it_is_and_129_clamps_with_the_warning(self) -> None:
        for text, loaded, warned in (("120", 120, []),
                                     ("129", 128, ["settings: ENGINE_VOLUME=129 > max 128; clamping"])):
            with self.subTest(text=text):
                paths.settings_file().write_text(f"ENGINE_VOLUME={text}\n", encoding="utf-8")
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    value = settings.load()["ENGINE_VOLUME"]
                self.assertEqual((value, [str(w.message) for w in caught]), (loaded, warned))

    def test_a_restored_wallpaper_volume_keeps_0_to_128(self) -> None:
        self.assertEqual([migrate.coerce(C.WP_SCHEMA["VOLUME"], raw, False) for raw in ("120", "129", "-1")],
                         [("ok", "120", ""), ("clamp", 128, ""), ("clamp", 0, "")])

    def test_the_settings_page_takes_128_and_refuses_129(self) -> None:
        page = settings_bridge.SettingsBridge(self.backend)
        failures: list = []
        page.commitFailed.connect(lambda keys, reason: failures.append((list(keys), reason)))
        with self.engine() as rec:
            self.assertTrue(page.commit("ENGINE_VOLUME", 128))
            self.assertFalse(page.commit("ENGINE_VOLUME", 129))
        self.assertEqual(settings.load()["ENGINE_VOLUME"], 128)
        self.assertEqual(failures, [(["ENGINE_VOLUME"], "That value is outside the allowed range.")])
        self.assertEqual(volumes(rec), [128], "the refused 129 sends nothing")

    def test_the_global_doors_save_120_and_send_it_for_a_wallpaper_without_its_own_volume(self) -> None:
        popup, ed = self.doors()
        self.assertNotIn("VOLUME", wp.load_set("111"))
        for name, door in (("deck popup", popup.setGlobalVolume), ("editor", ed.setGlobalVolume)):
            with self.subTest(door=name):
                settings.update({"ENGINE_VOLUME": 20})
                with self.engine() as rec:
                    self.assertTrue(door(120))
                self.assertEqual(settings.load()["ENGINE_VOLUME"], 120)
                self.assertEqual(volumes(rec), [120])

    def test_the_editor_wallpaper_door_writes_volume_120(self) -> None:
        _popup, ed = self.doors()
        with self.engine() as rec:
            self.assertTrue(ed.setVolumeValue(120))
        self.assertIn("VOLUME=120", paths.wp_file("111").read_text(encoding="utf-8").splitlines())
        self.assertEqual(wp.load_set("111")["VOLUME"], 120)
        self.assertEqual(volumes(rec), [120])

    def test_the_three_slider_previews_send_120_and_persist_nothing(self) -> None:
        popup, ed = self.doors()
        before = (paths.settings_file().read_bytes(), dict(wp.load_set("111")))
        with self.engine() as rec:
            for bridge, kind in ((popup, "volume"), (ed, "volume"), (ed, "wp_volume")):
                bridge.previewLive(kind, 120.0)
                QTest.qWait(80)
        self.assertEqual(volumes(rec), [120, 120, 120])
        self.assertEqual((paths.settings_file().read_bytes(), dict(wp.load_set("111"))), before,
                         "a preview persists nothing")

    def test_129_becomes_128_at_every_popup_and_editor_door(self) -> None:
        popup, ed = self.doors()
        for name, door, read in (("deck popup", popup.setGlobalVolume, lambda: settings.load()["ENGINE_VOLUME"]),
                                 ("editor", ed.setGlobalVolume, lambda: settings.load()["ENGINE_VOLUME"]),
                                 ("editor wallpaper", ed.setVolumeValue, lambda: wp.load_set("111")["VOLUME"])):
            with self.subTest(door=name):
                with self.engine() as rec:
                    self.assertTrue(door(129))
                self.assertEqual((read(), volumes(rec)), (128, [128]))
        with self.engine() as rec:
            for bridge, kind in ((popup, "volume"), (ed, "volume"), (ed, "wp_volume")):
                bridge.previewLive(kind, 129.0)
                QTest.qWait(80)
        self.assertEqual(volumes(rec), [128, 128, 128])


if __name__ == "__main__":
    unittest.main(verbosity=2)
