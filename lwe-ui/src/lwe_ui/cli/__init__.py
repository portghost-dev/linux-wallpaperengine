"""The command entry: one lwe command in a process that never imports Qt.

The first word names the verb and the rest are its arguments; -j or --json anywhere on the line
asks for JSON output. Verbs come from the modules in lwe_ui.cli.verbs (see registry), and each
returns its exit code: DONE 0, REFUSED 1, ENGINE_DOWN 2, USAGE 3.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import TextIO

from ..storage.lock import StoreBusy
from . import registry

DONE = 0
REFUSED = 1
ENGINE_DOWN = 2
USAGE = 3

_JSON_FLAGS = ("-j", "--json")


@dataclass(frozen=True)
class Context:
    """What a verb gets besides its words: the output mode, the two streams, the version stamp of
    the lwe that sent the command, and whether the sender's working folder was entered."""
    json: bool
    out: TextIO
    err: TextIO
    sender_stamp: str | None
    cwd_entered: bool

    def error(self, message: str, line: str | None = None) -> None:
        """Print a refusal or usage error on err: in text mode the line (the message when no line
        is given), under -j {"error":message} on one line."""
        if self.json:
            print(json.dumps({"error": message}, separators=(",", ":")), file=self.err)
        else:
            print(message if line is None else line, file=self.err)


def main(argv: list[str], *, sender_stamp: str | None = None, cwd_entered: bool = False) -> int:
    words = [w for w in argv if w not in _JSON_FLAGS]
    ctx = Context(len(words) != len(argv), sys.stdout, sys.stderr, sender_stamp, cwd_entered)
    if not words:
        ctx.error("usage: lwe [-j] <command> [value ...]")
        return USAGE
    verb = registry.discover().get(words[0])
    if verb is None:
        ctx.error(f"{words[0]} is not a command", f"lwe: {words[0]} is not a command")
        return USAGE
    try:
        return verb.run(ctx, words[1:])
    except StoreBusy as exc:
        ctx.error(str(exc))
        return REFUSED
