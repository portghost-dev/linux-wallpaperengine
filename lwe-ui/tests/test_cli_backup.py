"""lwe backup: export, preview and import through the engine's handoff, and the restart line an import
prints.

The export, preview and import runs are child processes (_cli_env.run_lwe) whose environment is built
from nothing (_cli_env.scratch_env): PySide6 blocked, a hyprctl stub giving one screen, a systemctl stub
that records its calls and fails, and the entered folder a temp folder (an empty folder token when the
sender's folder must not be entered). The engine, where one runs, is tests/_fake_engine.py on the child's
socket. The restart line runs in this process with HOME and the XDG folders at scratch, the engine's status
and the service's MainPID faked and the process environment patched.

Run: PYTHONPATH=src python3 tests/test_cli_backup.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import contextlib
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _cli_env
import _fake_engine

ROOT = Path(tempfile.mkdtemp(prefix="lwe-cli-backup-"))
HOME = _cli_env.scratch_home(ROOT / "inproc")
PENDING = "The service is not running or is busy, so it is not applied yet."


def tearDownModule() -> None:
    shutil.rmtree(ROOT, True)


class BackupHandoffTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / self._testMethodName
        self.log = self.root / "systemctl.log"
        self.env = _cli_env.scratch_env(self.root, {"hyprctl": "echo '[{\"name\": \"DP-1\"}]'\n",
                                                    "systemctl": f"echo \"$*\" >> '{self.log}'\nexit 1\n"})
        self.sender = self.root / "sender"
        self.sender.mkdir()
        self.config = Path(self.env["XDG_CONFIG_HOME"]) / "lwe"

    def tearDown(self) -> None:
        self.assertFalse(self.log.exists(), "systemctl was run")

    def lwe(self, *words: str, folder: str | None = None) -> tuple[int, str, str]:
        return _cli_env.run_lwe(list(words), self.env, str(self.sender) if folder is None else folder)

    def settings(self, text: str) -> None:
        self.config.mkdir(parents=True, exist_ok=True)
        (self.config / "settings.conf").write_text(text, encoding="utf-8")

    def test_a_relative_export_lands_in_the_entered_folder_and_a_second_says_replaced(self) -> None:
        code, out, err = self.lwe("backup", "export", "mine.lwebackup")
        target = self.sender / "mine.lwebackup"
        self.assertEqual((code, out, err), (0, f"Exported {target}\n", ""))
        self.assertTrue(target.is_file())
        self.assertEqual(self.lwe("backup", "export", "mine.lwebackup"),
                         (0, f"Exported {target} (replaced the file that was there)\n", ""))

    def test_an_empty_folder_token_refuses_a_relative_path_and_takes_an_absolute_one(self) -> None:
        self.assertEqual(self.lwe("backup", "export", "rel.lwebackup", folder=""), (1, "", "give a full path\n"))
        target = self.root / "abs.lwebackup"
        self.assertEqual(self.lwe("backup", "export", str(target), folder="")[0], 0)
        self.assertTrue(target.is_file())

    def test_preview_writes_nothing(self) -> None:
        self.settings("ENGINE_LAYER=top\n")
        archive = self.root / "a.lwebackup"
        self.assertEqual(self.lwe("backup", "export", str(archive))[0], 0)
        self.settings("ENGINE_LAYER=bottom\n")
        before = (self.config / "settings.conf").read_bytes()
        code, out, err = self.lwe("backup", "preview", str(archive))
        self.assertEqual((code, out.splitlines()[0], err), (0, "Would restore: Restored settings", ""))
        self.assertEqual((self.config / "settings.conf").read_bytes(), before)
        self.assertFalse((Path(self.env["XDG_STATE_HOME"]) / "lwe" / "backups").exists(), "a preview takes no snapshot")

    def test_import_restores_then_syncs_the_running_engine(self) -> None:
        self.settings("ENGINE_FPS=45\n")
        archive = self.root / "a.lwebackup"
        self.assertEqual(self.lwe("backup", "export", str(archive))[0], 0)
        self.settings("ENGINE_FPS=30\n")
        with _fake_engine.FakeEngine(self.env["LWE_SOCKET"]) as engine:
            code, out, err = self.lwe("backup", "import", str(archive))
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(out.splitlines()[0], "Restored settings")
        self.assertIn("Sent to the engine.\n", out)
        self.assertIn("ENGINE_FPS=45", (self.config / "settings.conf").read_text(encoding="utf-8"))
        sent = [cmd for cmd, _args in engine.calls]
        self.assertEqual(sent[0], "status")
        self.assertGreater(len(sent), 1, "the store was delivered after the status read")

    def test_import_while_the_engine_is_away_keeps_the_pending_record(self) -> None:
        archive = self.root / "a.lwebackup"
        self.assertEqual(self.lwe("backup", "export", str(archive))[0], 0)
        code, out, err = self.lwe("backup", "import", str(archive))
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn(PENDING, out)
        marker = (Path(self.env["XDG_STATE_HOME"]) / "lwe" / "panel" / "sync-pending").read_text(encoding="utf-8")
        self.assertIn("BUNDLE", marker)
        self.assertIn("CURRENT", marker)

    def test_an_archive_on_another_layer_rewrites_engine_env_without_systemctl(self) -> None:
        self.settings("ENGINE_LAYER=top\n")
        archive = self.root / "a.lwebackup"
        self.assertEqual(self.lwe("backup", "export", str(archive))[0], 0)
        self.settings("ENGINE_LAYER=bottom\n")
        (self.config / "engine-env").write_text("LWE_ENGINE_ARGS=--screen-root DP-1\n", encoding="utf-8")
        self.assertEqual(self.lwe("backup", "import", str(archive))[0], 0)
        env_text = (self.config / "engine-env").read_text(encoding="utf-8")
        self.assertIn("--screen-root DP-1 --layer top", env_text)

    def test_refusals(self) -> None:
        not_a_backup = self.root / "notes.txt"
        not_a_backup.write_text("hello\n", encoding="utf-8")
        folder = self.root / "folder"
        folder.mkdir()
        self.assertEqual(self.lwe("backup", "import", str(not_a_backup))[0], 1)
        self.assertEqual(self.lwe("backup", "preview", str(not_a_backup))[0], 1)
        self.assertEqual(self.lwe("backup", "import", str(self.root / "missing.lwebackup")),
                         (1, "", f"{self.root / 'missing.lwebackup'} is not a file\n"))
        self.assertEqual(self.lwe("backup", "export", str(folder)), (1, "", f"cannot write {folder}: it is a folder\n"))
        self.assertFalse((self.root / "folder.part").exists())
        usage = "usage: lwe backup export|import|preview <file>\n"
        for words in (("backup",), ("backup", "export"), ("backup", "export", "a", "b"), ("backup", "frob", "a")):
            with self.subTest(words=words):
                self.assertEqual(self.lwe(*words), (3, "", usage))


class RestartLineTest(unittest.TestCase):
    """An import whose archive changes the layer names it as waiting for a service restart while the
    service's engine still runs on the old one, and says nothing when no engine process can be read."""

    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui import api_client, cli, version
        from lwe_ui.engine import daemon_unit, push
        from lwe_ui.storage import paths, settings
        cls.cli, cls.api, cls.du, cls.push, cls.paths, cls.settings = cli, api_client, daemon_unit, push, paths, settings
        cls.stamp = version.panel_stamp()

    def setUp(self) -> None:
        self.calls: list = []
        self.live: dict | None = {"LWE_ENGINE_ARGS": "--screen-root DP-1"}
        knobs = {"LWE_SSFACTOR": 1.0, "LWE_CLAMPCOMPOSITES": 1.0, "LWE_TEXCOMP": True, "LWE_TEXDETAIL": "auto",
                 "LWE_HWDEC": "no"}
        status = {"pid": 4242, "version": self.stamp,
                  "config": {name: {"value": value, "source": "default"} for name, value in knobs.items()}}
        du = self.du
        for patcher in (mock.patch.object(du.subprocess, "run", lambda argv, **kw: self.calls.append(argv)),
                        mock.patch.object(du, "enumerate_outputs", lambda: ["DP-1"]),
                        mock.patch.object(du, "_service_main_pid", lambda: 4242),
                        mock.patch.object(du, "live_engine_env", lambda: self.live),
                        mock.patch.object(self.api, "status", lambda *a, **k: status),
                        mock.patch.object(self.push, "sync_all", lambda *a, **k: self.push.Outcome("applied"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.paths.ensure_dirs()
        (self.paths.config_dir() / "settings.conf").unlink(missing_ok=True)
        self.archive = HOME / "layer-top.lwebackup"
        self.settings.update({"ENGINE_LAYER": "top"})
        self.assertEqual(self.lwe("backup", "export", str(self.archive))[0], 0)
        self.settings.update({"ENGINE_LAYER": "bottom"})
        self.du.write_env()

    def tearDown(self) -> None:
        self.assertEqual(self.calls, [], "a subprocess ran")

    def lwe(self, *words: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(list(words), sender_stamp=self.stamp)
        return code, out.getvalue(), err.getvalue()

    def test_the_restart_line_names_the_layer_while_the_engine_runs_on_the_old_one(self) -> None:
        code, out, err = self.lwe("backup", "import", str(self.archive))
        self.assertEqual((code, err), (0, ""))
        self.assertIn("\nlayer: waiting for lwe service restart.\n", out)

    def test_no_restart_line_when_no_engine_process_can_be_read(self) -> None:
        self.live = None
        code, out, err = self.lwe("backup", "import", str(self.archive))
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("waiting for lwe service restart", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
