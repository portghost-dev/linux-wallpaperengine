"""Tier A serialization: shell-sourceable KEY=value files.

The format is shell-sourceable (`set -a; source <file>; set +a`) - the output must be valid bash.
Rules:
  * one KEY=value per line, no spaces around '='.
  * a value is emitted bare when it is "shell-safe" (only [A-Za-z0-9_./:=,+-]); otherwise it is
    double-quoted with \\ " $ ` escaped, so `source` neither word-splits nor expands it.
  * keys are valid shell identifiers ([A-Za-z_][A-Za-z0-9_]*); callers guarantee this.
  * '#' comments and blank lines are ignored on read.

This module is value-only (str in / str out); typed coercion lives in the per-schema modules.
"""
from __future__ import annotations

import re

_SAFE = re.compile(r"^[A-Za-z0-9_./:=,+-]*$")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# matches:  KEY=bareword   |   KEY="double quoted, possibly escaped"
_LINE_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$')


def is_valid_key(key: str) -> bool:
    """True if `key` is a shell identifier that serialize() will accept (KEY=value).

    A PROP_<name> key built from a non-identifier property name (a dot, a dash, a
    non-ASCII letter) fails this, so callers can skip that one prop instead of letting
    serialize() raise and lose the whole write.
    """
    return bool(_KEY_RE.match(str(key)))


def quote(value: str) -> str:
    """Render a single value for a shell-sourceable file."""
    if "\n" in value or "\r" in value:
        raise ValueError(f"Tier A value may not contain newlines: {value!r}")
    if value == "":
        return ""
    if _SAFE.match(value):
        return value
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return f'"{escaped}"'


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        body = raw[1:-1]
        out, i = [], 0
        while i < len(body):
            ch = body[i]
            if ch == "\\" and i + 1 < len(body) and body[i + 1] in '\\"$`':
                out.append(body[i + 1])
                i += 2
            else:
                out.append(ch)
                i += 1
        return "".join(out)
    return raw


def serialize(data: dict[str, str], *, header: str | None = None) -> str:
    """dict[str,str] -> shell-sourceable text. Insertion order preserved."""
    lines: list[str] = []
    if header:
        lines += [f"# {ln}" if ln else "#" for ln in header.splitlines()]
    for key, value in data.items():
        if not _KEY_RE.match(key):
            raise ValueError(f"invalid shell key: {key!r}")
        lines.append(f"{key}={quote('' if value is None else str(value))}")
    return "\n".join(lines) + "\n"


def _lines(text: str) -> list[tuple[str, str]]:
    """`text` as (line, ending) pairs, split on "\\n" only, the way bash reads lines. The ending
    is "\\r\\n", "\\n", or "" for an unterminated last line; every other character stays inside
    its line."""
    parts = text.split("\n")
    out = [(part[:-1], "\r\n") if part.endswith("\r") else (part, "\n") for part in parts[:-1]]
    if parts[-1]:
        out.append((parts[-1], ""))
    return out


def _opens_quote(value: str) -> bool:
    """True when `value` leaves a quote open at the end of its line, read the way bash reads
    quotes: single, double and $'...' quotes, a backslash escape outside single quotes, and a
    comment from a # that starts a word."""
    quote, i, word_start = "", 0, False
    while i < len(value):
        ch = value[i]
        if quote == "'":
            quote = "" if ch == "'" else quote
        elif quote:
            if ch == "\\":
                i += 1
            elif ch == quote[-1]:
                quote = ""
        elif ch == "\\":
            i += 1
        elif ch == "#" and word_start:
            return False
        elif ch == "$" and value[i + 1:i + 2] == "'":
            quote, i = "$'", i + 1
        elif ch in "'\"":
            quote = ch
        word_start = not quote and ch in " \t;&|()<>"
        i += 1
    return bool(quote)


def parse(text: str) -> dict[str, str]:
    """shell-sourceable text -> dict[str,str]. Last assignment wins (matches `source`)."""
    out: dict[str, str] = {}
    for line, _ending in _lines(text):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _LINE_RE.match(s)
        if not m:
            continue
        out[m.group(1)] = _unquote(m.group(2))
    return out


def _assigned_key(line: str) -> str | None:
    """The key a line assigns, read the way parse() reads it, or None."""
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    m = _LINE_RE.match(s)
    return m.group(1) if m else None


def edit(text: str, changes: dict[str, str | None], *, header: str | None = None,
         path: object = None) -> str:
    """`text` with `changes` applied line by line. A value replaces the last assignment of its
    key in place, keeping that line's indentation and line ending, or is appended as a new
    line when the key has none; None deletes every assignment of the key. Every other line
    stays as it was. `header` starts the text only when `text` is empty. A change that would
    replace or delete a line whose value opens a quote the line does not close raises
    ValueError naming the key and `path`: the rest of that value is on the lines after it."""
    lines = _lines(text)
    keys = [_assigned_key(line) for line, _ending in lines]
    out: list[str | None] = [line + ending for line, ending in lines]
    tail: list[str] = []
    for key, value in changes.items():
        if not _KEY_RE.match(key):
            raise ValueError(f"invalid shell key: {key!r}")
        at = [i for i, k in enumerate(keys) if k == key]
        for i in (at if value is None else at[-1:]):
            if _opens_quote(_LINE_RE.match(lines[i][0].strip()).group(2)):
                where = f" in {path}" if path else ""
                raise ValueError(f"cannot edit {key}{where}: its value opens a quote that its line does not close")
        if value is None:
            for i in at:
                out[i] = None
            continue
        assignment = f"{key}={quote(str(value))}"
        if at:
            line, ending = lines[at[-1]]
            out[at[-1]] = line[:len(line) - len(line.lstrip())] + assignment + ending
        else:
            tail.append(assignment + "\n")
    kept = [line for line in out if line is not None]
    if not text and header:
        kept = [(f"# {ln}" if ln else "#") + "\n" for ln in header.splitlines()]
    if tail and kept and not kept[-1].endswith("\n"):
        kept[-1] += "\n"
    return "".join(kept + tail)
