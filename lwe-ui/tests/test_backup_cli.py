"""The headless restore: `python3 -m lwe_ui.storage.backup export|restore <file>` carries the
backup without Qt, so a configuration can be restored the night the panel will not start.

Every CLI run here happens in a subprocess whose PySide6 import raises (a poison package
first on PYTHONPATH), and the poison is proven to bite before anything is claimed from it.
A file that is not a backup must be refused with exit code 1 and nothing written.

Run: QT_QPA_PLATFORM=offscreen PYTHONPATH=src python3 tests/test_backup_cli.py
"""
import _sandbox  # noqa: F401  (must stay the first project import)
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _poison_dir(root: Path) -> Path:
    """A PySide6 package that raises on import, first on the child's PYTHONPATH."""
    pkg = root / "poison" / "PySide6"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        'raise ImportError("PySide6 is unavailable in this test")\n', encoding="utf-8")
    return pkg.parent


def _env(cfg: Path, state: Path, poison: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(cfg)
    env["XDG_STATE_HOME"] = str(state)
    env["XDG_DATA_HOME"] = str(state / "data")
    env["PYTHONPATH"] = os.pathsep.join([str(poison), str(SRC)])
    return env


def _cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "lwe_ui.storage.backup", *args],
                          capture_output=True, text=True, env=env, timeout=120)


def main() -> None:
    home = Path(tempfile.mkdtemp(prefix="lwe-backup-cli-"))
    try:
        cfg, state = home / "c", home / "s"
        poison = _poison_dir(home)
        env = _env(cfg, state, poison)

        # measure the measurer: the poison must actually break the import in the child
        probe = subprocess.run([sys.executable, "-c", "import PySide6"],
                               capture_output=True, text=True, env=env, timeout=60)
        assert probe.returncode != 0 and "ImportError" in probe.stderr, \
            f"the PySide6 poison did not bite: rc={probe.returncode} {probe.stderr}"

        os.environ["XDG_CONFIG_HOME"] = str(cfg)
        os.environ["XDG_STATE_HOME"] = str(state)
        os.environ["XDG_DATA_HOME"] = str(state / "data")
        from lwe_ui.storage import paths, playlists, settings, tags

        paths.ensure_dirs()
        settings.ensure_exists()
        lib = home / "walls"
        (lib / "111").mkdir(parents=True)
        settings.save({**settings.load(), "ENGINE_LAYER": "top", "INTERFACE_SCALE": 125,
                       "WALLPAPERS_DIR": str(lib)})
        playlists.save("mine", {"NAME": "Mine", "MODE": "shuffle", "INTERVAL": 600,
                                "UNIT": "min", "MEMBERS": "111"})
        tags.set_state("111", "One", "good")

        archive = home / "conf.lwebackup"
        out = _cli(env, "export", str(archive))
        assert out.returncode == 0, f"export failed: {out.returncode}\n{out.stdout}\n{out.stderr}"
        assert "Traceback" not in out.stderr, out.stderr
        assert out.stdout.startswith(f"Exported {archive}"), out.stdout
        assert "counts:" in out.stdout and "playlists=1" in out.stdout, out.stdout
        assert archive.is_file(), "the CLI wrote no archive"

        # drift the live config, then restore it from the archive with no Qt in the child
        settings.save({**settings.load(), "ENGINE_LAYER": "background", "INTERFACE_SCALE": 100})
        playlists.delete("mine")
        tags.save([])
        back = _cli(env, "restore", str(archive))
        assert back.returncode == 0, f"restore failed: {back.returncode}\n{back.stdout}\n{back.stderr}"
        assert "Traceback" not in back.stderr, back.stderr
        assert back.stdout.startswith("Restored"), back.stdout
        s = settings.load()
        assert s["ENGINE_LAYER"] == "top" and int(s["INTERFACE_SCALE"]) == 125, s
        assert "mine" in [p.get("slug") for p in playlists.list_playlists()], "the playlist came back"
        assert any(row.get("id") == "111" for row in tags.load()), "the tag came back"

        # a file that is not a backup is refused, named, and changes nothing
        junk = home / "notes.txt"
        junk.write_text("not a zip\n", encoding="utf-8")
        before = settings.load()
        bad = _cli(env, "restore", str(junk))
        assert bad.returncode == 1, f"a refusal must exit 1, got {bad.returncode}\n{bad.stdout}"
        assert "Refused" in bad.stdout and "errors:" in bad.stdout, bad.stdout
        assert settings.load() == before, "a refused restore wrote settings"

        # the CLI's own guard rails
        assert _cli(env, "restore").returncode != 0, "restore with no file must not run"
        assert "export" in _cli(env, "--help").stdout

        print("OK test_backup_cli - export and restore ran with PySide6 poisoned, settings, "
              "playlist and tag restored, a non-backup refused with exit 1")
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
