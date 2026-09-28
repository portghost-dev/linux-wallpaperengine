"""The command entry: one lwe command in a process that never imports Qt.

The first word names the verb and the rest are its arguments; -j or --json anywhere on the line
asks for JSON output. Verbs come from the modules in lwe_ui.cli.verbs (see registry), and each
returns its exit code: DONE 0, REFUSED 1, ENGINE_DOWN 2, USAGE 3. Every line goes out as UTF-8
through clean(), as the engine writes its own output, and a closed output pipe ends the command
quietly with PIPE_CLOSED.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Iterable, TextIO

from .. import version
from . import registry

DONE = 0
REFUSED = 1
ENGINE_DOWN = 2
USAGE = 3
PIPE_CLOSED = 141

_JSON_FLAGS = ("-j", "--json")
_CONTROLS = {code: "?" for code in (*range(0x20), 0x7F) if code != ord("\n")}
_NO_BYTE = re.compile(r"[\ud800-\udc7f\udd00-\udfff]")


def clean(text: str) -> str:
    """The text as the engine writes it: its original bytes read again as UTF-8, each invalid sequence
    one U+FFFD (a lone surrogate that stands for no byte is one U+FFFD as well), then every control
    character below U+0020 but the newline, and U+007F, as "?", as in the engine's status lines."""
    text = _NO_BYTE.sub("\ufffd", text).encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    return text.translate(_CONTROLS)


class _Cleaned:
    """A stream that cleans each string before the real stream writes it."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def write(self, text: str) -> int:
        return self._stream.write(clean(text))

    def writelines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def _silence_stdout() -> None:
    """Point stdout at /dev/null, so the flush at exit cannot fail on a closed pipe."""
    try:
        fd = sys.stdout.fileno()
    except (AttributeError, ValueError, OSError):
        return
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, fd)
    os.close(devnull)


@dataclass(frozen=True)
class Context:
    """What a verb gets besides its words: the output mode, the two streams, which clean each string
    they write, the version stamp of the lwe that sent the command, and whether the sender's working
    folder was entered."""
    json: bool
    out: TextIO
    err: TextIO
    sender_stamp: str | None
    cwd_entered: bool

    def error(self, message: str, line: str | None = None) -> None:
        """Print a refusal or usage error on err: in text mode the line (the message when no line
        is given), under -j {"error":message} on one line."""
        if self.json:
            print(json.dumps({"error": message}, ensure_ascii=False, separators=(",", ":")),
                  file=self.err)
        else:
            print(message if line is None else line, file=self.err)


def main(argv: list[str], *, sender_stamp: str | None = None, cwd_entered: bool = False) -> int:
    from ..storage.lock import StoreBusy
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors=stream.errors)
    words = [w for w in argv if w not in _JSON_FLAGS]
    ctx = Context(len(words) != len(argv), _Cleaned(sys.stdout), _Cleaned(sys.stderr), sender_stamp,
                  cwd_entered)
    if not words:
        ctx.error("usage: lwe [-j] <command> [value ...]")
        return USAGE
    try:
        verb = registry.discover().get(words[0])
        if verb is None:
            ctx.error(f"{words[0]} is not a command", f"lwe: {words[0]} is not a command")
            return USAGE
        if verb.name != "help":
            try:
                refusal = version.sender_refusal(ctx.sender_stamp, version.panel_stamp())
            except version.StampError as exc:
                refusal = str(exc)
            if refusal is not None:
                ctx.error(refusal)
                return REFUSED
        code = verb.run(ctx, words[1:])
        ctx.out.flush()
        return code
    except StoreBusy as exc:
        ctx.error(str(exc))
        return REFUSED
    except BrokenPipeError:
        _silence_stdout()
        return PIPE_CLOSED
    except Exception as exc:
        one_line = " ".join(str(exc).splitlines())
        ctx.error(f"internal error: {type(exc).__name__}: {one_line}")
        return REFUSED
