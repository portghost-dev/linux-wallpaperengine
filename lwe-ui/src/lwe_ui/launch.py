"""Entry point of the lwe-ui launcher and of python -m lwe_ui.

A first argument of --lwe is a command handed over by the engine run as lwe: the sender's version
stamp and working folder follow, then the command words, which go to the command entry (lwe_ui.cli)
without importing Qt. Any other start runs the panel through app.main, which handles --tray itself.
"""
from __future__ import annotations

import os
import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if len(argv) > 1 and argv[1] == "--lwe":
        from . import cli
        if len(argv) < 4:
            print("usage: lwe-ui --lwe <version> <folder> [word ...]", file=sys.stderr)
            return cli.USAGE
        stamp, folder = argv[2], argv[3]
        entered = False
        if folder and os.path.isdir(folder):
            try:
                os.chdir(folder)
                entered = True
            except OSError:
                pass
        return cli.main(argv[4:], sender_stamp=stamp, cwd_entered=entered)
    from .app import main as app_main
    return app_main()
