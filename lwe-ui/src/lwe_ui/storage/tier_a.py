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


def parse(text: str) -> dict[str, str]:
    """shell-sourceable text -> dict[str,str]. Last assignment wins (matches `source`)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _LINE_RE.match(s)
        if not m:
            continue
        out[m.group(1)] = _unquote(m.group(2))
    return out
