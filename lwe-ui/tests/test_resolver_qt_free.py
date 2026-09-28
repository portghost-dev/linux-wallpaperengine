"""The show resolver and the engine pushes live in plain modules, outside the Qt module.

A child process whose PySide6 import raises imports lwe_ui.engine.resolve and
lwe_ui.engine.push and resolves a store; models re-exports the resolver; the tray's resume
sends the resolved rate of the wallpaper on screen through resolve.effective_speed; an import
pass and a preset repair re-push the rotation set. The child's environment is built from
scratch.

Run: PYTHONPATH=src python3 tests/test_resolver_qt_free.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

_BOOT = tempfile.TemporaryDirectory(prefix="lwe-resolver-boot-")
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = os.path.join(_BOOT.name, _sub) if _sub else _BOOT.name
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402

_APP = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])

from lwe_ui import api_client, models, tray  # noqa: E402
from lwe_ui.engine import push, resolve  # noqa: E402
from lwe_ui.storage import playlists, settings, wp  # noqa: E402

CHILD = r"""
import json, sys
from lwe_ui.engine import push, resolve
engine_wid, args = resolve.resolve_show_args("111")
entries, interval, order, enabled, label = push._playlist_payload("mine")
print(json.dumps({"engine_wid": engine_wid, "speed": args["speed"], "scaling": args["scaling"],
                  "members": [e["ui_id"] for e in entries],
                  "member_speeds": [e["speed"] for e in entries], "interval": interval,
                  "order": order, "qt": sorted(m for m in sys.modules if m.split(".")[0] == "PySide6")}))
"""


class ResolverQtFreeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="lwe-resolver-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        for key, sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"),
                         ("XDG_DATA_HOME", "d")):
            os.environ[key] = str(self.home / sub) if sub else str(self.home)
        for d in (self.home / "bin", self.home / "rt"):
            d.mkdir()

    def _env(self, poison: Path) -> dict[str, str]:
        return {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": os.environ["XDG_CONFIG_HOME"],
            "XDG_STATE_HOME": os.environ["XDG_STATE_HOME"],
            "XDG_DATA_HOME": os.environ["XDG_DATA_HOME"],
            "XDG_RUNTIME_DIR": str(self.home / "rt"),
            "LWE_SOCKET": str(self.home / "rt" / "engine.sock"),
            "LWE_SANDBOX": "1",
            "PATH": str(self.home / "bin"),
            "PYTHONPATH": os.pathsep.join([str(poison), str(SRC)]),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def test_the_child_resolves_the_store_with_pyside6_poisoned(self) -> None:
        pkg = self.home / "poison" / "PySide6"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(
            'raise ImportError("PySide6 is unavailable in this test")\n', encoding="utf-8")
        env = self._env(pkg.parent)
        probe = subprocess.run([sys.executable, "-c", "import PySide6"], env=env,
                               capture_output=True, text=True, timeout=60)
        self.assertNotEqual(probe.returncode, 0, "the PySide6 poison did not bite")
        self.assertIn("ImportError", probe.stderr)

        settings.save({**settings.load(), "ENGINE_TIMESCALE": 2.0})
        wp.save("111", {"SPEED": 1.5})
        playlists.save("mine", {"NAME": "Mine", "MODE": "sequential", "INTERVAL": 600,
                                "UNIT": "min", "MEMBERS": "111 222"})
        out = subprocess.run([sys.executable, "-c", CHILD], env=env, capture_output=True,
                             text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(got["qt"], [], "the child imported PySide6")
        self.assertEqual((got["engine_wid"], got["speed"]), ("111", 1.5), "the store was not read")
        self.assertEqual(got["members"], ["111", "222"])
        self.assertEqual(got["member_speeds"], [1.5, 2.0], "a wallpaper without SPEED inherits the global")
        engine_wid, args = resolve.resolve_show_args("111")
        entries, interval, order, *_ = push._playlist_payload("mine")
        self.assertEqual((got["engine_wid"], got["speed"], got["scaling"]),
                         (engine_wid, args["speed"], args["scaling"]))
        self.assertEqual((got["members"], got["interval"], got["order"]),
                         ([e["ui_id"] for e in entries], interval, order))

    def test_models_re_exports_the_resolver(self) -> None:
        self.assertIs(models.resolve_show_args, resolve.resolve_show_args)
        for name in ("_conf_true", "_identity_dir", "_wallpapers_dir", "effective_speed", "resolve_fit",
                     "resolve_fullscreen_behavior", "resolved_tuning", "split_playlist_parts"):
            self.assertIs(getattr(models, name), getattr(resolve, name), name)

    def test_tray_resume_sends_the_resolved_rate_of_the_wallpaper_on_screen(self) -> None:
        settings.save({**settings.load(), "ENGINE_TIMESCALE": 2.0})
        wp.save("111", {"SPEED": 1.5})
        sent: list[float] = []
        asked: list[str] = []
        real = resolve.effective_speed

        def spy(wid, factor=None):
            asked.append(wid)
            return real(wid, factor)

        status = {"speed": 0.0, "current": {"id": "111", "ui_id": "111"}}
        frozen_tray = types.SimpleNamespace(_status=lambda: status)
        with mock.patch.object(api_client, "set_speed", lambda speed: sent.append(speed) or {"ok": True}), \
                mock.patch.object(resolve, "effective_speed", spy):
            tray.TrayProcess._toggle_pause(frozen_tray)
            status["current"] = {"id": "222", "ui_id": "222"}
            tray.TrayProcess._toggle_pause(frozen_tray)
        self.assertEqual(asked, ["111", "222"], "the tray resolves the wallpaper on screen")
        self.assertEqual(sent, [1.5, 2.0], "its own SPEED, else the stored global")

    def test_an_import_pass_and_a_preset_repair_push_the_rotation_set(self) -> None:
        settings.save({**settings.load(), "DETECT_MODE": "manual"})
        backend = models.Backend()
        slug = playlists.active_slug()
        playlists.update(slug, {"MEMBERS": "111"})
        pushes: list[str] = []
        with mock.patch.object(api_client, "available", lambda *a, **kw: True), \
                mock.patch.object(api_client, "playlist_set",
                                  lambda name, *a, **kw: pushes.append(name) or {"ok": True}), \
                mock.patch.object(api_client, "schedule_set", lambda *a, **kw: {"ok": True}), \
                mock.patch.object(api_client, "lanes_set", lambda lanes: {"ok": True}):
            bridge = models.ImportBridge(backend)
            bridge._finish(1, 1)
            self.assertEqual(pushes, [slug], "an import pass re-pushes the rotation set")
            bridge._on_repair_done(1)
            self.assertEqual(pushes, [slug, slug], "a preset repair re-pushes it too")


if __name__ == "__main__":
    unittest.main(verbosity=1)
