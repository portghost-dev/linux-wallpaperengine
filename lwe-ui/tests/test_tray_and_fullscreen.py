"""The tray PROCESS (U4 split) and the app-class contract.

The compositor fullscreen watcher (U9) moved INTO the engine, and the tray moved into
its OWN process (`lwe-ui --tray`): the window process exits on close and returns all
of its memory, the tray survives and applies the repurposed CLOSE_TO_TRAY rule to
that exit. These tests drive the TrayProcess against fakes - nothing spawns a real
window or touches a real engine socket.

Run: python3 tests/test_tray_and_fullscreen.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

_TMP = tempfile.mkdtemp(prefix="lwe-tray-test-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, ".config")
os.environ["XDG_STATE_HOME"] = os.path.join(_TMP, ".local/state")
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP, ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

from PySide6.QtCore import QEvent, QObject  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lwe_ui.storage import paths, settings  # noqa: E402

paths.ensure_dirs()
settings.ensure_exists()
_APP = QApplication.instance() or QApplication(["t"])


class TrayProcessTest(unittest.TestCase):
    def setUp(self) -> None:
        from lwe_ui.tray import TrayProcess
        self.quits: list[bool] = []

        class _FakeApp:
            def quit(inner) -> None:  # noqa: N805
                self.quits.append(True)

        self.tray = TrayProcess.__new__(TrayProcess)
        QObject.__init__(self.tray)
        self.tray._app = _FakeApp()
        self.tray._window = None
        self.tray._exiting = False
        self.tray._original_env = {}
        # only the exit-rule path is under test here; the real __init__ needs a live
        # QSystemTrayIcon, which a headless run may not have

    def test_window_exit_with_close_to_tray_on_keeps_the_tray(self) -> None:
        settings.save({**settings.load(), "CLOSE_TO_TRAY": True})
        self.tray._on_window_exited()
        self.assertEqual(self.quits, [], "on = only the window ends; the tray stays")

    def test_window_exit_with_close_to_tray_off_exits_everything(self) -> None:
        settings.save({**settings.load(), "CLOSE_TO_TRAY": False})
        self.tray._on_window_exited()
        self.assertEqual(self.quits, [True], "off = closing the window exits everything")

    def test_the_setting_is_read_per_exit_not_at_startup(self) -> None:
        settings.save({**settings.load(), "CLOSE_TO_TRAY": True})
        self.tray._on_window_exited()
        settings.save({**settings.load(), "CLOSE_TO_TRAY": False})
        self.tray._on_window_exited()
        self.assertEqual(self.quits, [True], "flipping the row takes effect without restart")

    def test_tray_exit_never_double_fires_on_child_teardown(self) -> None:
        """Exit terminates the window child; the resulting finished signal must not
        re-enter the exit rule."""
        settings.save({**settings.load(), "CLOSE_TO_TRAY": False})
        self.tray._exiting = True
        self.tray._on_window_exited()
        self.assertEqual(self.quits, [], "the exit path owns the quit; the rule stands down")

    def test_window_command_is_the_entry_minus_tray_flag(self) -> None:
        from lwe_ui import tray as tray_mod
        command = tray_mod._window_command()
        self.assertTrue(command, "a launch command must always resolve")
        self.assertNotIn("--tray", command, "the spawned process is the WINDOW")


class TrayDispatchTest(unittest.TestCase):
    def test_tray_flag_dispatches_before_heavy_imports(self) -> None:
        """The tray process must never pay for the QML stack: the --tray branch has to
        run before models/bridges are imported."""
        source = (_ROOT / "src/lwe_ui/app.py").read_text(encoding="utf-8")
        dispatch = source.index('if "--tray" in argv')
        heavy = source.index("from .models import")
        self.assertLess(dispatch, heavy, "--tray dispatch must precede the heavy imports")

    def test_autostart_flag_still_means_the_tray(self) -> None:
        """Autostart launches the TRAY only (ruling 2026-08-22); the desktop entry's
        --tray flag now IS the tray process."""
        source = (_ROOT / "src/lwe_ui/app.py").read_text(encoding="utf-8")
        self.assertIn("from .tray import main as tray_main", source)


class BehaviorEnumTest(unittest.TestCase):
    def test_full_reclaim_rides_the_existing_stop_value(self) -> None:
        """No fourth enum member - `stop` IS full reclaim, now engine-owned."""
        from lwe_ui import constants as C
        self.assertEqual(C.FULLSCREEN_BEHAVIORS, ("off", "pause", "stop"),
                         "the enum gains no member")


class AppClassTest(unittest.TestCase):
    def test_app_builds_a_qapplication(self) -> None:
        source = (_ROOT / "src/lwe_ui/app.py").read_text(encoding="utf-8")
        self.assertIn("app = QApplication(argv)", source)
        self.assertNotIn("QGuiApplication(argv)", source)
        # the WINDOW process quits on last window close (that is the whole U4 point);
        # only the TRAY process suppresses it
        self.assertNotIn("setQuitOnLastWindowClosed(False)", source)
        tray_source = (_ROOT / "src/lwe_ui/tray.py").read_text(encoding="utf-8")
        self.assertIn("setQuitOnLastWindowClosed(False)", tray_source)

    def test_the_whole_qml_tree_instantiates_under_qapplication(self) -> None:
        """The app-class change is only safe if the tree is genuinely unaffected by it."""
        from PySide6.QtCore import QUrl
        from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance
        from lwe_ui.app import (_resolve_theme_tokens, _QML_DIR, _TOKENS_URI, _TOKENS_NAME)
        from lwe_ui.models import Backend, ImportBridge, ThemeBridge, ThemeTokens
        from lwe_ui.settings_bridge import SettingsBridge

        self.assertIsInstance(QApplication.instance(), QApplication)

        tokens = ThemeTokens(_resolve_theme_tokens())
        qmlRegisterSingletonInstance(ThemeTokens, _TOKENS_URI, 1, 0, _TOKENS_NAME, tokens)
        engine = QQmlApplicationEngine()
        engine.addImportPath(str(_QML_DIR))
        errors: list[str] = []
        engine.warnings.connect(lambda es: [errors.append(e.toString()) for e in es])

        # every bridge is held in a Python reference: a context property does NOT own its
        # object, and a collected bridge shows up as "Cannot call method of null" at runtime
        backend = Backend()
        import_bridge = ImportBridge(backend)
        settings_bridge = SettingsBridge(backend, import_bridge)
        theme_bridge = ThemeBridge(tokens)
        self._keepalive = (backend, import_bridge, settings_bridge, theme_bridge, engine)
        rc = engine.rootContext()
        rc.setContextProperty("backend", backend)
        rc.setContextProperty("importBridge", import_bridge)
        rc.setContextProperty("settingsBridge", settings_bridge)
        rc.setContextProperty("themeBridge", theme_bridge)

        engine.load(QUrl.fromLocalFile(str(_QML_DIR / "SettingsView.qml")))
        self.assertEqual(len(engine.rootObjects()), 1,
                         "the settings tree must instantiate under QApplication")
        real = [e for e in errors if "Unqualified access" not in e]
        self.assertEqual(real, [], f"QML errors under QApplication: {real}")


if __name__ == "__main__":
    unittest.main(verbosity=1)
