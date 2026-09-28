"""The environment rule of the command tests, in one place.

scratch_env builds a child's environment from nothing: HOME, the four XDG folders, the engine socket
and PATH under a scratch folder, LWE_SANDBOX set, and PYTHONPATH the panel source behind a PySide6
package that raises on import. scratch_home points this process's HOME and the four XDG folders at a
scratch folder, before a test's first lwe_ui call. run_lwe runs one command the way the engine run as
lwe hands it over, with the panel's own version stamp.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Mapping

SRC = Path(__file__).resolve().parent.parent / "src"


def _folders(root: Path) -> dict[str, Path]:
    home = root / "home"
    return {
        "HOME": home,
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_STATE_HOME": home / ".local" / "state",
        "XDG_DATA_HOME": home / ".local" / "share",
        "XDG_RUNTIME_DIR": root / "rt",
    }


def scratch_home(root: str | os.PathLike) -> Path:
    """Point HOME and the four XDG folders of this process under root; returns HOME."""
    folders = _folders(Path(root))
    for name, folder in folders.items():
        folder.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(folder)
    return folders["HOME"]


def scratch_env(root: str | os.PathLike,
                stubs: Mapping[str, str] | Iterable[tuple[str, str]] = ()) -> dict[str, str]:
    """A child environment under root, built from nothing; stubs are (name, shell body) pairs written
    as executables in root/bin, the whole PATH."""
    root = Path(root)
    folders = _folders(root)
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    poison = root / "poison" / "PySide6"
    poison.mkdir(parents=True, exist_ok=True)
    (poison / "__init__.py").write_text('raise ImportError("PySide6 is unavailable in this test")\n',
                                        encoding="utf-8")
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, body in dict(stubs).items():
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        stub.chmod(0o755)
    env = {name: str(folder) for name, folder in folders.items()}
    env.update({
        "LWE_SOCKET": str(folders["XDG_RUNTIME_DIR"] / "engine.sock"),
        "LWE_SANDBOX": "1",
        "PATH": str(bin_dir),
        "PYTHONPATH": os.pathsep.join([str(root / "poison"), str(SRC)]),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def run_lwe(words: list[str], env: dict[str, str], cwd_token: str) -> tuple[int, str, str]:
    """`python -m lwe_ui --lwe <panel stamp> <cwd_token> <words>` in env, from HOME;
    returns (exit code, stdout, stderr)."""
    from lwe_ui import version
    r = subprocess.run([sys.executable, "-m", "lwe_ui", "--lwe", version.panel_stamp(), cwd_token, *words],
                       env=env, cwd=env["HOME"], capture_output=True, encoding="utf-8", timeout=60)
    return r.returncode, r.stdout, r.stderr
