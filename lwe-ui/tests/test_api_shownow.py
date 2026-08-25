"""Self-verification for Backend.showNow - the engine `show` verb courier.

Sandbox + stub discipline: HOME/XDG_* re-pointed per test, api_client stubbed in
sys.modules BEFORE models is imported, so no socket traffic ever leaves the test.

Contract under test:
  * valid id, engine accepts  -> True, show() called once with the RESOLVED vocabulary;
  * invalid / unsafe id       -> False, API never consulted;
  * transport failure (None) / engine rejection (ok=false) / api layer raises -> False.

Run: export PYTHONPATH=src && python3 tests/test_api_shownow.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_BOOT_HOME = tempfile.mkdtemp(prefix="lwe-apishow-boot-")
for _k, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_k] = os.path.join(_BOOT_HOME, _sub) if _sub else _BOOT_HOME

# Stub the API courier, then import models ONCE against the stub.
_API = types.ModuleType("lwe_ui.api_client")
_API.available = lambda: False
_API.show = lambda wid, wait_done=False, **kw: None
sys.modules["lwe_ui.api_client"] = _API

from PySide6.QtCore import QCoreApplication  # noqa: E402

_APP = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])

from lwe_ui import models  # noqa: E402
from lwe_ui.storage import paths, settings  # noqa: E402

assert models.api_client is _API, "stub did not intercept models' api_client import"


class ApiShowNowTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.mkdtemp(prefix="lwe-apishow-")
        os.environ["HOME"] = self._home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self._home, "c")
        os.environ["XDG_STATE_HOME"] = os.path.join(self._home, "s")
        os.environ["XDG_DATA_HOME"] = os.path.join(self._home, "d")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

        self.api_show_calls: list[str] = []
        _API.available = lambda: False
        _API.show = lambda wid, wait_done=False, **kw: (self.api_show_calls.append(wid), None)[1]

        self.backend = models.Backend()

    def _flag(self, on: bool) -> None:
        # era flags are gone; kept as a no-op seam so the vocabulary tests read unchanged
        settings.save(settings.load())

    def test_api_show_used(self) -> None:
        _API.available = lambda: True
        _API.show = lambda wid, wait_done=False, **kw: (
            self.api_show_calls.append(wid),
            {"id": 1, "ok": True, "status": "accepted"},
        )[1]
        ok = self.backend.showNow("3134543499")
        self.assertTrue(ok)
        self.assertEqual(self.api_show_calls, ["3134543499"])

    def test_invalid_id_rejected_without_api_call(self) -> None:
        _API.show = lambda wid, wait_done=False, **kw: (_ for _ in ()).throw(
            AssertionError("unsafe id must never reach the API"))
        for bad in ("", "  ", "../etc", "a b", "id;rm"):
            self.assertFalse(self.backend.showNow(bad))

    def test_transport_failure_returns_false(self) -> None:
        _API.show = lambda wid, wait_done=False, **kw: None
        self.assertFalse(self.backend.showNow("3134543499"))

    def test_engine_rejection_returns_false(self) -> None:
        _API.show = lambda wid, wait_done=False, **kw: {"id": 1, "ok": False, "error": "not in library"}
        self.assertFalse(self.backend.showNow("3134543499"))

    def test_resolved_render_settings_ride_along(self) -> None:
        """The wp conf's CC/SPEED must reach the API show."""
        from lwe_ui.storage import wp

        self._flag(True)
        wp.save("3600585591", {"CC": "1.02 1.52 2.0 -0.125664", "SPEED": 2.5})

        captured: dict = {}

        def _show(wid, wait_done=False, cc=None, speed=None, properties=None, **kw):
            captured.update(wid=wid, cc=cc, speed=speed)
            return {"id": 1, "ok": True, "status": "accepted"}

        _API.available = lambda: True
        _API.show = _show

        self.assertTrue(self.backend.showNow("3600585591"))
        self.assertEqual(captured["cc"], [1.02, 1.52, 2.0, -0.125664])
        self.assertEqual(captured["speed"], 2.5)

    def test_full_show_vocabulary_rides_along(self) -> None:
        """The WHOLE conf vocabulary reaches the API show resolved -
        scaling/clamp/volume/audio/mouse/automute/fullscreen_pause/skip_objects, with
        conf overriding globals and the global timescale factored into speed."""
        from lwe_ui.storage import wp

        self._flag(True)
        settings.save({"ENGINE_TIMESCALE": 2.0, "ENGINE_VOLUME": 40,
                       "PAUSE_RECOVERY_ACTION": "pause", "PAUSE_RECOVERY_CONDITION": "both"})
        wp.save("3134543499", {"SPEED": 1.5, "SCALING": "fill", "CLAMPING": "border",
                               "VOLUME": 80, "AUDIO_REACTIVE": True, "MOUSE": True,
                               "AUTOMUTE": False, "SKIP": "27 539"})

        captured: dict = {}

        def _show(wid, wait_done=False, **kw):
            captured.update(kw, wid=wid)
            return {"id": 1, "ok": True, "status": "accepted"}

        _API.available = lambda: True
        _API.show = _show

        self.assertTrue(self.backend.showNow("3134543499"))
        self.assertEqual(captured["scaling"], "fill")
        self.assertEqual(captured["clamp"], "border")
        self.assertEqual(captured["volume"], 80)
        self.assertTrue(captured["audio_processing"])
        self.assertTrue(captured["mouse"])
        self.assertFalse(captured["automute"])
        self.assertTrue(captured["fullscreen_pause"], "policy pause+both must inherit as ON")
        self.assertEqual(captured["skip_objects"], [27, 539])
        self.assertAlmostEqual(captured["speed"], 3.0, msg="SPEED 1.5 x global 2.0")

    def test_vocabulary_defaults_and_overrides(self) -> None:
        """Untouched conf: schema defaults ride (editor confs pin every key - SCALING
        'default', VOLUME 0 = silent, exactly what the watcher launched); session
        overrides (mute/audio-off/mouse-off) force their side OFF regardless."""
        from lwe_ui.storage import wp

        self._flag(True)
        settings.save({"AUDIO_REACTIVE_DEFAULT": True,
                       "MOUSE_DEFAULT": True, "OVERRIDE_MUTE": True,
                       "OVERRIDE_AUDIO_OFF": True, "OVERRIDE_MOUSE_OFF": True})
        wp.save("3134543499", {"VOLUME": 80, "AUDIO_REACTIVE": True, "MOUSE": True})

        captured: dict = {}

        def _show(wid, wait_done=False, **kw):
            captured.update(kw, wid=wid)
            return {"id": 1, "ok": True, "status": "accepted"}

        _API.available = lambda: True
        _API.show = _show

        self.assertTrue(self.backend.showNow("3134543499"))
        self.assertEqual(captured["scaling"], "default", "untouched conf pins schema default")
        self.assertNotIn("clamp", captured, "empty clamp must be omitted (engine default)")
        self.assertEqual(captured["volume"], 0, "OVERRIDE_MUTE wins over the conf volume")
        self.assertFalse(captured["audio_processing"], "OVERRIDE_AUDIO_OFF wins over conf true")
        self.assertFalse(captured["mouse"], "OVERRIDE_MOUSE_OFF wins over conf true")
        self.assertTrue(captured["automute"], "AUTOMUTE default is on")
        self.assertFalse(captured["fullscreen_pause"], "condition=off means no engine pause")
        self.assertNotIn("skip_objects", captured, "no conf SKIP -> omitted")

    def test_conf_volume_zero_stays_silent(self) -> None:
        """Key-presence: a conf that CARRIES VOLUME means it, zero included (zero =
        deliberately muted). The presence check is why the global fallback below is
        safe where a truthiness fallback never was."""
        from lwe_ui.storage import wp

        self._flag(True)
        settings.save({"ENGINE_VOLUME": 55})
        wp.save("3134543499", {"VOLUME": 0})

        captured: dict = {}

        def _show(wid, wait_done=False, **kw):
            captured.update(kw, wid=wid)
            return {"id": 1, "ok": True, "status": "accepted"}

        _API.available = lambda: True
        _API.show = _show

        self.assertTrue(self.backend.showNow("3134543499"))
        self.assertEqual(captured["volume"], 0, "conf VOLUME=0 means silent, not 'inherit'")

    def test_absent_volume_key_inherits_global(self) -> None:
        """Key-presence: a conf WITHOUT a VOLUME line inherits the
        global ENGINE_VOLUME. update_set VOLUME=None is the sweep's exact operation,
        so this is also the sweep's contract test."""
        from lwe_ui.storage import wp

        self._flag(True)
        settings.save({"ENGINE_VOLUME": 55})
        wp.save("3134543499", {"SPEED": 1.5})
        wp.update_set("3134543499", {"VOLUME": None})

        captured: dict = {}

        def _show(wid, wait_done=False, **kw):
            captured.update(kw, wid=wid)
            return {"id": 1, "ok": True, "status": "accepted"}

        _API.available = lambda: True
        _API.show = _show

        self.assertTrue(self.backend.showNow("3134543499"))
        self.assertEqual(captured["volume"], 55, "absent VOLUME key -> global ENGINE_VOLUME")

    def test_preset_resolves_to_base_wallpaper(self) -> None:
        """A preset's conf BG names the real wallpaper; the API gets the BASE id plus
        the preset's render settings."""
        from lwe_ui.storage import wp

        self._flag(True)
        wp.save(
            "3600585591",
            {"BG": "/somewhere/lwe/wallpapers/2981249186", "CC": "1.02 1.52 2.0 -0.125664", "SPEED": 1.0},
        )

        captured: dict = {}

        def _show(wid, wait_done=False, cc=None, speed=None, properties=None, **kw):
            captured.update(wid=wid, cc=cc)
            return {"id": 1, "ok": True, "status": "accepted"}

        _API.available = lambda: True
        _API.show = _show

        self.assertTrue(self.backend.showNow("3600585591"))
        self.assertEqual(captured["wid"], "2981249186", "engine must get the BASE id, not the preset id")
        self.assertEqual(captured["cc"], [1.02, 1.52, 2.0, -0.125664], "with the PRESET's grade")

    def test_preset_props_ride_along(self) -> None:
        """A preset IS its PROP_ overrides, so the
        conf's props dict must reach the API show as raw strings."""
        from lwe_ui.storage import wp

        self._flag(True)
        wp.save(
            "3410648253",
            {
                "BG": "/somewhere/lwe/wallpapers/2185197772",
                "props": {"schemecolor": "0 0 0", "side": "centerblack", "stars": "false"},
            },
        )

        captured: dict = {}

        def _show(wid, wait_done=False, cc=None, speed=None, properties=None, **kw):
            captured.update(wid=wid, properties=properties)
            return {"id": 1, "ok": True, "status": "accepted"}

        _API.available = lambda: True
        _API.show = _show

        self.assertTrue(self.backend.showNow("3410648253"))
        self.assertEqual(captured["wid"], "2185197772")
        self.assertEqual(
            captured["properties"],
            {"schemecolor": "0 0 0", "side": "centerblack", "stars": "false"},
            "the preset's PROP_ overrides must ride the show, raw strings",
        )

    def test_missing_conf_sends_identity(self) -> None:
        """No wp conf on disk: the click still goes through with identity settings."""
        self._flag(True)
        captured: dict = {}

        def _show(wid, wait_done=False, cc=None, speed=None, properties=None, **kw):
            captured.update(cc=cc, speed=speed)
            return {"id": 1, "ok": True, "status": "accepted"}

        _API.available = lambda: True
        _API.show = _show

        self.assertTrue(self.backend.showNow("3134543499"))
        self.assertEqual(captured["cc"], [1.0, 1.0, 1.0, 0.0])
        self.assertEqual(captured["speed"], 1.0)

    def test_api_exception_returns_false(self) -> None:
        _API.show = lambda wid, wait_done=False, **kw: (_ for _ in ()).throw(
            RuntimeError("socket exploded"))
        self.assertFalse(self.backend.showNow("3134543499"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
