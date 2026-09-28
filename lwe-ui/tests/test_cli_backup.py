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
import datetime
import fcntl
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
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

    def restored_with_a_failure(self) -> tuple[int, str, str, Path]:
        """Import the archive at self.archive; returns the run and the one pre-restore snapshot."""
        code, out, err = self.lwe("backup", "import", str(self.archive))
        snapshots = sorted((Path(self.env["XDG_STATE_HOME"]) / "lwe" / "backups").glob("pre-restore-*"))
        self.assertEqual(len(snapshots), 1, out)
        return code, out, err, snapshots[0]

    def assertWayBack(self, out: str, snapshot: Path) -> None:
        lines = out.splitlines()
        self.assertIn("Restored: settings, theme, discovery, playlists, overrides, tags, meta, rules", lines)
        self.assertIn(f"The configuration from before this import is in {snapshot}; lwe backup import {snapshot} "
                      "puts it back.", lines)

    def test_an_engine_env_that_cannot_be_built_after_the_restore_prints_the_receipt_and_the_way_back(self) -> None:
        assets = Path(self.env["HOME"]) / "My Assets"
        assets.mkdir()
        self.settings(f"ASSETS_DIR={assets}\n")
        self.archive = self.root / "a.lwebackup"
        self.assertEqual(self.lwe("backup", "export", str(self.archive))[0], 0)
        self.settings("ASSETS_DIR=\n")
        code, out, err, snapshot = self.restored_with_a_failure()
        self.assertEqual((code, err), (1, ""), out)
        self.assertEqual(out.splitlines()[0], "Restored settings")
        self.assertIn("\nThe stores were restored, but rebuilding engine-env failed: ValueError: ", out)
        self.assertWayBack(out, snapshot)

    def test_an_engine_env_that_cannot_be_written_after_the_restore_prints_the_receipt_and_the_way_back(self) -> None:
        self.settings("ENGINE_FPS=45\n")
        self.archive = self.root / "a.lwebackup"
        self.assertEqual(self.lwe("backup", "export", str(self.archive))[0], 0)
        self.settings("ENGINE_FPS=30\n")
        (self.config / "engine-env").mkdir()
        code, out, err, snapshot = self.restored_with_a_failure()
        self.assertEqual((code, err), (1, ""), out)
        self.assertIn("\nThe stores were restored, but rebuilding engine-env failed: IsADirectoryError: ", out)
        self.assertIn("ENGINE_FPS=45", (self.config / "settings.conf").read_text(encoding="utf-8"))
        self.assertWayBack(out, snapshot)

    def test_another_build_refuses_the_import(self) -> None:
        self.settings("ENGINE_FPS=45\n")
        archive = self.root / "a.lwebackup"
        self.assertEqual(self.lwe("backup", "export", str(archive))[0], 0)
        self.settings("ENGINE_FPS=30\n")
        before = (self.config / "settings.conf").read_bytes()
        with _fake_engine.FakeEngine(self.env["LWE_SOCKET"], version="0.0.0-other-build") as engine:
            code, out, err = self.lwe("backup", "import", str(archive))
        self.assertEqual((code, out), (1, ""), err)
        self.assertIn("0.0.0-other-build", err)
        self.assertEqual((self.config / "settings.conf").read_bytes(), before)
        self.assertFalse((Path(self.env["XDG_STATE_HOME"]) / "lwe" / "backups").exists(), "a snapshot was taken")
        self.assertEqual([cmd for cmd, _args in engine.calls], ["status"])

    def test_an_engine_refusal_of_the_restore_exits_1(self) -> None:
        self.settings("ENGINE_FPS=45\n")
        archive = self.root / "a.lwebackup"
        self.assertEqual(self.lwe("backup", "export", str(archive))[0], 0)
        with _fake_engine.FakeEngine(self.env["LWE_SOCKET"]) as engine:
            engine.script("schedule-set", _fake_engine.fail("no schedule here"))
            code, out, err = self.lwe("backup", "import", str(archive))
        self.assertEqual((code, err), (1, ""), out)
        self.assertIn("\nThe engine refused it: no schedule here\n", out)

    def test_an_export_to_a_path_ending_in_a_slash_is_refused(self) -> None:
        self.assertEqual(self.lwe("backup", "export", "newdir/"),
                         (1, "", f"cannot write {self.sender}/newdir/: it names a folder\n"))
        self.assertFalse((self.sender / "newdir").exists())

    def backups(self) -> Path:
        return Path(self.env["XDG_STATE_HOME"]) / "lwe" / "backups"

    def recovery(self) -> dict | None:
        rec = self.backups() / "recovery.json"
        return json.loads(rec.read_text(encoding="utf-8")) if rec.exists() else None

    def failing_archive(self, then: str) -> Path:
        """An archive whose ASSETS_DIR names a folder with a space, so every import of it fails at
        engine-env after the stores are written; settings.conf is then `then`."""
        assets = Path(self.env["HOME"]) / "My Assets"
        assets.mkdir()
        self.settings(f"ASSETS_DIR={assets}\nENGINE_FPS=45\n")
        archive = self.root / "a.lwebackup"
        self.assertEqual(self.lwe("backup", "export", str(archive))[0], 0)
        self.settings(then)
        return archive

    def written(self) -> dict[str, bytes]:
        """Every file under the lwe config and state folders but the restore lock, with its bytes."""
        tree = {}
        for base in (self.config, Path(self.env["XDG_STATE_HOME"]) / "lwe"):
            for f in sorted(base.rglob("*")):
                if f.is_file() and f.name != "restore.lock":
                    tree[str(f)] = f.read_bytes()
        return tree

    def test_three_failing_imports_keep_one_snapshot_that_recovery_json_names_and_pruning_keeps(self) -> None:
        archive = self.failing_archive("ASSETS_DIR=\nENGINE_FPS=30\n")
        self.backups().mkdir(parents=True)
        for n in range(6):
            older = self.backups() / f"pre-restore-20200101-00000{n}.lwebackup"
            shutil.copy(archive, older)
            os.utime(older, (1_600_000_000 + n, 1_600_000_000 + n))
        runs = [self.lwe("backup", "import", str(archive)) for _ in range(3)]
        self.assertEqual([code for code, _out, _err in runs], [1, 1, 1], runs[0][1])
        taken = [p for p in self.backups().glob("pre-restore-*.lwebackup")
                 if not p.name.startswith("pre-restore-2020")]
        self.assertEqual(len(taken), 1, [p.name for p in self.backups().iterdir()])
        record = self.recovery()
        self.assertEqual(record["snapshot"], taken[0].name)
        self.assertIsInstance(datetime.datetime.fromisoformat(record["since"]), datetime.datetime)
        way_back = (f"The configuration from before this import is in {taken[0]}; "
                    f"lwe backup import {taken[0]} puts it back.")
        self.assertEqual([way_back in out.splitlines() for _code, out, _err in runs], [True, False, False])
        os.utime(taken[0], (1_500_000_000, 1_500_000_000))
        folders = {name: self.env[name] for name in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
        with mock.patch.dict(os.environ, folders):
            from lwe_ui.storage import backup
            self.assertTrue(backup.snapshot({"errors": [], "notes": []}))
        self.assertTrue(taken[0].exists(), "the named snapshot outlived the pruning though it is the oldest")
        self.assertEqual(len(list(self.backups().glob("pre-restore-*.lwebackup"))), backup.SNAPSHOTS_KEPT + 1)

    def test_a_successful_import_removes_recovery_json(self) -> None:
        self.settings("ENGINE_FPS=45\n")
        archive = self.root / "a.lwebackup"
        self.assertEqual(self.lwe("backup", "export", str(archive))[0], 0)
        self.backups().mkdir(parents=True)
        named = self.backups() / "pre-restore-20200101-000000.lwebackup"
        shutil.copy(archive, named)
        headless = [sys.executable, "-m", "lwe_ui.storage.backup", "restore", str(archive)]
        for door in ("lwe backup import", "python3 -m lwe_ui.storage.backup restore"):
            with self.subTest(door=door):
                (self.backups() / "recovery.json").write_text(
                    json.dumps({"snapshot": named.name, "since": "2020-01-01T00:00:00"}), encoding="utf-8")
                if door.startswith("lwe"):
                    code, out = self.lwe("backup", "import", str(archive))[:2]
                else:
                    run = subprocess.run(headless, env=self.env, cwd=self.env["HOME"], capture_output=True,
                                         encoding="utf-8", timeout=60)
                    code, out = run.returncode, run.stdout
                self.assertEqual((code, self.recovery()), (0, None), out)
                self.assertEqual(list(self.backups().glob("pre-restore-*")), [named], "a snapshot was taken")

    def test_a_successful_import_of_the_named_snapshot_removes_recovery_json(self) -> None:
        archive = self.failing_archive("ASSETS_DIR=\nENGINE_FPS=30\n")
        self.assertEqual(self.lwe("backup", "import", str(archive))[0], 1)
        named = self.backups() / self.recovery()["snapshot"]
        code, out, err = self.lwe("backup", "import", str(named))
        self.assertEqual((code, err), (0, ""), out)
        self.assertIsNone(self.recovery())
        self.assertIn("ENGINE_FPS=30", (self.config / "settings.conf").read_text(encoding="utf-8"))
        self.assertEqual(list(self.backups().glob("pre-restore-*")), [named])

    def test_a_named_snapshot_that_is_gone_refuses_the_import_writes_nothing_and_keeps_recovery_json(self) -> None:
        self.settings("ENGINE_FPS=45\n")
        archive = self.root / "a.lwebackup"
        self.assertEqual(self.lwe("backup", "export", str(archive))[0], 0)
        self.settings("ENGINE_FPS=30\n")
        not_a_backup = self.root / "notes.txt"
        not_a_backup.write_text("hello\n", encoding="utf-8")
        self.assertEqual(self.lwe("backup", "import", str(not_a_backup))[0], 1)
        self.assertIsNone(self.recovery(), "a refusal that took no snapshot wrote recovery.json")
        self.backups().mkdir(parents=True, exist_ok=True)
        rec = self.backups() / "recovery.json"
        rec.write_text(json.dumps({"snapshot": "pre-restore-20200101-000000.lwebackup",
                                   "since": "2020-01-01T00:00:00"}), encoding="utf-8")
        before = self.written()
        code, out, err = self.lwe("backup", "import", str(archive))
        gone = self.backups() / "pre-restore-20200101-000000.lwebackup"
        self.assertEqual((code, out, err), (1, f"Refused {archive}\nerrors: file=recovery.json, reason=Nothing was "
                                               f"imported: {gone}, the snapshot kept from before an earlier failed "
                                               f"import, is missing. Deleting {rec} clears this block.\n", ""))
        self.assertEqual(self.written(), before)
        code, out, _err = self.lwe("backup", "preview", str(archive))
        self.assertEqual((code, out.splitlines()[0][:15], self.written()), (0, "Would restore: ", before))

    def test_an_unreadable_recovery_json_refuses_the_import_the_same_way(self) -> None:
        self.settings("ENGINE_FPS=45\n")
        archive = self.root / "a.lwebackup"
        self.assertEqual(self.lwe("backup", "export", str(archive))[0], 0)
        self.settings("ENGINE_FPS=30\n")
        self.backups().mkdir(parents=True)
        rec = self.backups() / "recovery.json"
        rec.write_text("{not json", encoding="utf-8")
        before = self.written()
        code, out, err = self.lwe("backup", "import", str(archive))
        self.assertEqual((code, out, err), (1, f"Refused {archive}\nerrors: file=recovery.json, reason=Nothing was "
                                               f"imported: {rec} cannot be read, so the snapshot it keeps from before "
                                               f"an earlier failed import cannot be found. Deleting {rec} clears this "
                                               "block.\n", ""))
        self.assertEqual(self.written(), before)

    def test_a_record_that_names_no_pre_restore_snapshot_refuses_the_import_as_unreadable(self) -> None:
        self.settings("ENGINE_FPS=45\n")
        archive = self.root / "a.lwebackup"
        self.assertEqual(self.lwe("backup", "export", str(archive))[0], 0)
        self.settings("ENGINE_FPS=30\n")
        (self.backups() / "sub").mkdir(parents=True)
        shutil.copy(archive, self.backups() / "sub" / "pre-restore-20200101-000000.lwebackup")
        shutil.copy(archive, self.backups() / "pre-restore-20200101-000000.lwebackup.old")
        rec = self.backups() / "recovery.json"
        for name in ("recovery.json", "sub/pre-restore-20200101-000000.lwebackup",
                     "pre-restore-20200101-000000.lwebackup.old"):
            with self.subTest(name=name):
                rec.write_text(json.dumps({"snapshot": name, "since": "2020-01-01T00:00:00"}), encoding="utf-8")
                before = self.written()
                code, out, err = self.lwe("backup", "import", str(archive))
                self.assertEqual((code, out, err), (1, f"Refused {archive}\nerrors: file=recovery.json, reason=Nothing "
                                                       f"was imported: {rec} cannot be read, so the snapshot it keeps "
                                                       f"from before an earlier failed import cannot be found. Deleting "
                                                       f"{rec} clears this block.\n", ""))
                self.assertEqual(self.written(), before)
        second = self.backups() / "pre-restore-20200101-000000-2.lwebackup"
        shutil.copy(archive, second)
        rec.write_text(json.dumps({"snapshot": second.name, "since": "2020-01-01T00:00:00"}), encoding="utf-8")
        self.assertEqual(self.lwe("backup", "import", str(archive))[0], 0, "a snapshot's counter name is accepted")
        self.assertIsNone(self.recovery())

    def test_a_record_whose_snapshot_is_no_real_backup_refuses_the_import_as_unreadable(self) -> None:
        self.settings("ENGINE_FPS=45\n")
        archive = self.root / "a.lwebackup"
        self.assertEqual(self.lwe("backup", "export", str(archive))[0], 0)
        self.settings("ENGINE_FPS=30\n")
        (self.root / "notes.txt").write_text("not a backup\n", encoding="utf-8")
        (self.root / "adir").mkdir()
        self.backups().mkdir(parents=True)
        snapshot = self.backups() / "pre-restore-20200101-000000.lwebackup"
        rec = self.backups() / "recovery.json"
        for label in ("link to a text file", "link to a folder", "dangling link", "file of other content",
                      "link to a real backup"):
            with self.subTest(label=label):
                if os.path.lexists(snapshot):
                    snapshot.unlink()
                if label == "file of other content":
                    snapshot.write_text("not a backup either\n", encoding="utf-8")
                else:
                    snapshot.symlink_to({"link to a text file": self.root / "notes.txt",
                                         "link to a folder": self.root / "adir",
                                         "dangling link": self.root / "gone",
                                         "link to a real backup": archive}[label])
                rec.write_text(json.dumps({"snapshot": snapshot.name, "since": "2020-01-01T00:00:00"}),
                               encoding="utf-8")
                before = self.written()
                code, out, err = self.lwe("backup", "import", str(archive))
                unreadable = (f"Refused {archive}\nerrors: file=recovery.json, reason=Nothing was imported: {rec} "
                              f"cannot be read, so the snapshot it keeps from before an earlier failed import cannot "
                              f"be found. Deleting {rec} clears this block.\n")
                self.assertEqual((code, out, err), (1, unreadable, ""))
                self.assertEqual(self.written(), before)

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


class RefusedAfterTheSnapshotTest(unittest.TestCase):
    """The command's refused import, in this process: a refusal that comes after the pre-restore snapshot
    (the first store could not be written) leaves recovery.json naming that snapshot."""

    def test_a_refusal_after_the_snapshot_leaves_recovery_json_naming_it(self) -> None:
        import dataclasses
        from lwe_ui import cli, version
        from lwe_ui.storage import backup, paths, registry
        root = ROOT / self._testMethodName / "home"
        folders = {"HOME": str(root), "XDG_CONFIG_HOME": str(root / ".config"),
                   "XDG_STATE_HOME": str(root / ".local" / "state"), "XDG_DATA_HOME": str(root / ".local" / "share")}

        def unwritable(plan, receipt):
            receipt["errors"].append({"file": "settings.conf", "reason": "could not be written: full"})
            return False
        first_fails = (dataclasses.replace(registry.STORES[0], apply=unwritable), *registry.STORES[1:])
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, folders):
            paths.ensure_dirs()
            archive = root / "a.lwebackup"
            self.assertFalse(backup.export_to(archive)["errors"])
            with mock.patch.object(registry, "STORES", first_fails), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                code = cli.main(["backup", "import", str(archive)], sender_stamp=version.panel_stamp())
            backups = paths.state_dir() / "backups"
            taken = list(backups.glob("pre-restore-*.lwebackup"))
            record = json.loads((backups / "recovery.json").read_text(encoding="utf-8"))
        self.assertEqual((code, err.getvalue()), (1, ""), out.getvalue())
        self.assertTrue(out.getvalue().startswith(f"Refused {archive}\n"), out.getvalue())
        self.assertEqual((len(taken), record["snapshot"]), (1, taken[0].name))


class RestoreLockTest(unittest.TestCase):
    """The restore lock (storage/backup.py restoring), in this process with its folders at scratch and the
    screens faked: a nested use on one thread takes one flock, another thread meets the refusal, and an
    import that overlaps another's settle is refused, so the import that runs after it has the last word.
    Each test starts at most one thread: a daemon, joined with a timeout."""

    def setUp(self) -> None:
        from lwe_ui.engine import daemon_unit
        from lwe_ui.storage import backup, paths
        self.backup, self.paths = backup, paths
        self.root = ROOT / self._testMethodName / "home"
        folders = {"HOME": str(self.root), "XDG_CONFIG_HOME": str(self.root / ".config"),
                   "XDG_STATE_HOME": str(self.root / ".local" / "state"),
                   "XDG_DATA_HOME": str(self.root / ".local" / "share")}
        for patcher in (mock.patch.dict(os.environ, folders),
                        mock.patch.object(daemon_unit, "enumerate_outputs", lambda: ["DP-1"])):
            patcher.start()
            self.addCleanup(patcher.stop)
        paths.ensure_dirs()
        self.rec = paths.state_dir() / "backups" / "recovery.json"

    def archives(self) -> tuple[Path, Path]:
        """(clean, failing): the failing one's ASSETS_DIR names a folder with a space, so its import fails
        at engine-env after the stores are written."""
        conf = self.paths.config_dir() / "settings.conf"
        conf.write_text("ASSETS_DIR=\nENGINE_FPS=45\n", encoding="utf-8")
        clean = self.root / "clean.lwebackup"
        self.assertFalse(self.backup.export_to(clean)["errors"])
        assets = self.root / "My Assets"
        assets.mkdir()
        conf.write_text(f"ASSETS_DIR={assets}\n", encoding="utf-8")
        failing = self.root / "failing.lwebackup"
        self.assertFalse(self.backup.export_to(failing)["errors"])
        self.fresh()
        return clean, failing

    def fresh(self) -> None:
        """Settings with no ASSETS_DIR and ENGINE_FPS 30, and no recovery.json."""
        (self.paths.config_dir() / "settings.conf").write_text("ASSETS_DIR=\nENGINE_FPS=30\n", encoding="utf-8")
        self.rec.unlink(missing_ok=True)

    def door(self, archive: Path) -> tuple[int, str]:
        """lwe backup import through its verb, with its own streams: (exit code, what it printed)."""
        from lwe_ui.cli import Context
        from lwe_ui.cli.verbs import backup as verbs
        out = io.StringIO()
        code = verbs._import(Context(False, out, out, None, False), str(archive))
        return code, out.getvalue()

    def test_an_import_that_overlaps_another_ones_settle_is_refused_and_the_later_import_decides(self) -> None:
        clean, failing = self.archives()
        real = self.backup.settle
        for first, later in ((failing, clean), (clean, failing)):
            with self.subTest(first=first.name, later=later.name):
                self.fresh()
                at_settle, release = threading.Event(), threading.Event()
                runs: dict = {}

                def paused(r, failed):
                    if threading.current_thread().name == "first":
                        at_settle.set()
                        release.wait(10)
                    return real(r, failed)

                with mock.patch.object(self.backup, "settle", paused):
                    thread = threading.Thread(target=lambda: runs.update(first=self.door(first)), name="first",
                                              daemon=True)
                    thread.start()
                    try:
                        self.assertTrue(at_settle.wait(10), "the first import never reached its settle")
                        runs["overlap"] = runs["later"] = self.door(later)
                    finally:
                        release.set()
                        thread.join(10)
                    if "Another restore is running." in runs["overlap"][1]:
                        runs["later"] = self.door(later)
                record = json.loads(self.rec.read_text(encoding="utf-8")) if self.rec.exists() else None
                if later is clean:
                    self.assertIsNone(record, "a later success left a record")
                else:
                    lines = runs["later"][1].splitlines()
                    taken = next(line.split(" is in ", 1)[1].split(";", 1)[0] for line in lines
                                 if line.startswith("The configuration from before this import is in "))
                    self.assertEqual((record or {}).get("snapshot"), Path(taken).name,
                                     "a later failure lost its record")
                self.assertFalse(thread.is_alive())
                self.assertEqual((runs["first"][0], runs["overlap"][0]), (1 if first is failing else 0, 1))
                self.assertIn("errors: file=restore, reason=Another restore is running.\n", runs["overlap"][1])

    def settle_depths(self) -> tuple[contextlib.ExitStack, list[int]]:
        """Patches that count the restoring() uses open on this thread and record that count at each
        settle; the stack undoes them."""
        depth, seen = [0], []
        real_restoring, real_settle = self.backup.restoring, self.backup.settle

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
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(self.backup, "restoring", counted))
        stack.enter_context(mock.patch.object(self.backup, "settle", settle))
        return stack, seen

    def test_the_command_and_the_headless_restore_settle_inside_their_restore_lock(self) -> None:
        clean, _failing = self.archives()
        stack, seen = self.settle_depths()
        with stack, contextlib.redirect_stdout(io.StringIO()):
            codes = [self.door(clean)[0], self.backup.main(["restore", str(clean)])]
        self.assertEqual((codes, seen), ([0, 0], [1, 1]))

    def test_a_restore_lock_that_cannot_be_opened_fails_cleanly_at_the_command_and_headless_doors(self) -> None:
        clean, _failing = self.archives()
        lock = self.paths.state_dir() / "restore.lock"
        lock.mkdir()
        refusal = f"That backup could not be restored: [Errno 21] Is a directory: '{lock}'\n"
        self.assertEqual(self.door(clean), (1, refusal))
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = self.backup.main(["restore", str(clean)])
        self.assertEqual((code, out.getvalue(), err.getvalue()), (1, "", refusal))
        self.assertEqual((self.paths.config_dir() / "settings.conf").read_text(encoding="utf-8"),
                         "ASSETS_DIR=\nENGINE_FPS=30\n", "nothing was restored")

    def test_a_restore_lock_that_is_a_link_or_a_fifo_fails_cleanly_at_the_command_and_headless_doors(self) -> None:
        clean, _failing = self.archives()
        lock = self.paths.state_dir() / "restore.lock"
        sentinel = self.root / "sentinel.txt"
        sentinel.write_text("SENTINEL\n", encoding="utf-8")

        def headless() -> tuple[int, str, str]:
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                code = self.backup.main(["restore", str(clean)])
            return code, out.getvalue(), err.getvalue()
        for kind, refusal in (("link", f"[Errno 40] Too many levels of symbolic links: '{lock}'"),
                              ("fifo", f"[Errno 22] not a regular file: '{lock}'")):
            with self.subTest(kind=kind):
                lock.unlink(missing_ok=True)
                if kind == "link":
                    lock.symlink_to(sentinel)
                else:
                    os.mkfifo(lock)
                runs: dict = {}
                thread = threading.Thread(target=lambda: runs.update(command=self.door(clean), headless=headless()),
                                          daemon=True)
                thread.start()
                thread.join(10)
                self.assertFalse(thread.is_alive(), "a door blocked on restore.lock")
                line = f"That backup could not be restored: {refusal}\n"
                self.assertEqual((runs["command"], runs["headless"]), ((1, line), (1, "", line)))
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "SENTINEL\n", "the link's target was written")
                self.assertEqual((self.paths.config_dir() / "settings.conf").read_text(encoding="utf-8"),
                                 "ASSETS_DIR=\nENGINE_FPS=30\n", "nothing was restored")

    def test_the_headless_restore_prints_what_it_restored_while_a_record_exists(self) -> None:
        clean, _failing = self.archives()
        self.rec.parent.mkdir(parents=True, exist_ok=True)
        named = self.rec.parent / "pre-restore-20200101-000000.lwebackup"
        shutil.copy(clean, named)
        self.rec.write_text(json.dumps({"snapshot": named.name, "since": "2020-01-01T00:00:00"}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = self.backup.main(["restore", str(clean)])
        self.assertEqual((code, out.getvalue().splitlines()[0], self.rec.exists()), (0, "Restored settings", False))

    def test_a_record_that_cannot_be_removed_after_a_successful_import_is_a_receipt_error(self) -> None:
        clean, _failing = self.archives()
        backups = self.rec.parent
        backups.mkdir(parents=True, exist_ok=True)
        named = backups / "pre-restore-20200101-000000.lwebackup"
        shutil.copy(clean, named)
        self.rec.write_text(json.dumps({"snapshot": named.name, "since": "2020-01-01T00:00:00"}), encoding="utf-8")
        backups.chmod(0o500)
        self.addCleanup(backups.chmod, 0o700)
        error = f"errors: file=recovery.json, reason=could not be removed: [Errno 13] Permission denied: '{self.rec}'"
        headline = "Restored · recovery.json could not be removed"
        code, printed = self.door(clean)
        self.assertEqual((code, printed.splitlines()[0]), (1, headline), printed)
        self.assertIn(error, printed.splitlines())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = self.backup.main(["restore", str(clean)])
        self.assertEqual((code, out.getvalue().splitlines()[0]), (1, headline), out.getvalue())
        self.assertIn(error, out.getvalue().splitlines())
        self.assertTrue(self.rec.exists())

    def test_a_nested_use_on_one_thread_takes_one_flock(self) -> None:
        clean, _failing = self.archives()
        taken: list = []
        real = fcntl.flock

        def counted(f, op):
            if getattr(f, "name", "").endswith("restore.lock"):
                taken.append(op)
            return real(f, op)

        with mock.patch.object(self.backup.fcntl, "flock", counted):
            with self.backup.restoring() as outer, self.backup.restoring() as inner:
                receipt = self.backup.apply(self.backup.preflight(clean))
        self.assertEqual((outer, inner, receipt.get("refused"), taken),
                         (True, True, None, [fcntl.LOCK_EX | fcntl.LOCK_NB]))

    def test_a_second_thread_meets_the_refusal(self) -> None:
        clean, _failing = self.archives()
        taken, release = threading.Event(), threading.Event()
        held: list = []

        def hold() -> None:
            with self.backup.restoring() as got:
                held.append(got)
                taken.set()
                release.wait(10)

        thread = threading.Thread(target=hold, daemon=True)
        thread.start()
        try:
            self.assertTrue(taken.wait(10))
            with self.backup.restoring() as mine:
                receipt = self.backup.apply(self.backup.preflight(clean))
        finally:
            release.set()
            thread.join(10)
        self.assertEqual((held, mine, thread.is_alive()), ([True], False, False))
        self.assertEqual((receipt.get("refused"), receipt["errors"]),
                         (True, [{"file": "restore", "reason": "Another restore is running."}]))


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

    def test_the_import_syncs_then_rebuilds_engine_env_then_reads_what_waits(self) -> None:
        order: list[str] = []
        write_env, restart_state = self.du.write_env, self.du.restart_state

        def sync_all(*args, **kwargs):
            order.append("sync_all")
            return self.push.Outcome("applied")

        def env(*args, **kwargs):
            order.append("write_env")
            return write_env(*args, **kwargs)

        def waits(*args, **kwargs):
            order.append("restart_state")
            return restart_state(*args, **kwargs)

        with mock.patch.object(self.push, "sync_all", sync_all), mock.patch.object(self.du, "write_env", env), \
                mock.patch.object(self.du, "restart_state", waits):
            code, _out, err = self.lwe("backup", "import", str(self.archive))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(order, ["sync_all", "write_env", "restart_state"])

    def test_the_restart_line_names_exactly_the_clamps_that_wait(self) -> None:
        for changes, names in (({"SSFACTOR": 2.0}, "resclamp"),
                               ({"SSFACTOR": 2.0, "CLAMPCOMPOSITES": 0.0}, "resclamp, effectclamp")):
            with self.subTest(names=names):
                self.settings.update(changes)
                archive = HOME / f"clamps-{len(changes)}.lwebackup"
                self.assertEqual(self.lwe("backup", "export", str(archive))[0], 0)
                self.settings.update({"SSFACTOR": 1.0, "CLAMPCOMPOSITES": 1.0})
                self.du.write_env()
                code, out, err = self.lwe("backup", "import", str(archive))
                self.assertEqual((code, err), (0, ""))
                self.assertIn(f"\n{names}: waiting for lwe service restart.\n", out)

    def test_no_restart_line_when_no_engine_process_can_be_read(self) -> None:
        self.live = None
        code, out, err = self.lwe("backup", "import", str(self.archive))
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("waiting for lwe service restart", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
