#!/usr/bin/env python3
"""Repo text lint: enforce the ASCII-plus-approved-display-glyphs rule.

Every character in the scanned source must be ASCII, with one exception: a small set of
approved display glyphs may appear, but ONLY inside a quoted string
- that is, only on a line where the glyph sits between the first and last quote character.
This keeps identifiers, filenames, comments, and code pure ASCII while allowing the handful
of glyphs the UI is allowed to render in string literals and log lines.

Scans src/, watcher/, and tools/. Skips tests/ and the dev/probes/ calibration fixtures
(both hold captured, non-authored data - real wallpaper-engine scene dumps carry
upstream-authored names in any language, the same reason test-fixture unicode is
legitimate), plus any file that does not decode as UTF-8 (binary assets like textures and
.pyc). Prints one "path:line:col message" line per violation and exits 1 if there are any,
else exits 0.

The approved glyphs are written as \\u escapes below so this script is itself pure ASCII and
passes its own scan.
"""
from __future__ import annotations

import sys
from pathlib import Path

_APPROVED_CODEPOINTS = (
    (0x00B7, "middle dot"),
    (0x2605, "black star"),
    (0x2606, "white star"),
    (0x2298, "circled slash"),
    (0x21BA, "undo arrow"),
    (0x2039, "single left angle quote"),
    (0x25BE, "small down triangle"),
    (0x2699, "gear (interim glyph, pending icon pass)"),
    (0x0394, "Greek capital delta"),
)
APPROVED = {chr(cp): name for cp, name in _APPROVED_CODEPOINTS}

QUOTES = ("\"", "'", "`")
SCAN_DIRS = ("src", "watcher", "tools")
# Directory basenames skipped anywhere in the path (build/test noise).
SKIP_DIR_NAMES = {"tests", "__pycache__", ".git"}
# Path fragments (posix, relative to the repo root) skipped as captured fixture data - see
# the module docstring. Matched as a path-segment sequence so it never trips on a substring.
SKIP_PATH_FRAGMENTS = (("dev", "probes"),)


def _quote_span(line: str) -> tuple[int, int]:
    """Column range strictly between the first and last quote character on the line.

    Returns (lo, hi) where a glyph at column c is inside a string iff lo < c < hi. If the
    line has fewer than two quote characters there is no string span, so returns (-1, -1)
    which admits nothing.
    """
    first = -1
    last = -1
    for i, ch in enumerate(line):
        if ch in QUOTES:
            if first == -1:
                first = i
            last = i
    if first == last:
        return (-1, -1)
    return (first, last)


def _has_fragment(parts: tuple[str, ...], fragment: tuple[str, ...]) -> bool:
    """True if `fragment` appears as a contiguous run of segments inside `parts`."""
    n = len(fragment)
    return any(parts[i:i + n] == fragment for i in range(len(parts) - n + 1))


def _iter_files(root: Path):
    for top in SCAN_DIRS:
        base = root / top
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            rel_parts = path.relative_to(root).parts
            if any(_has_fragment(rel_parts, frag) for frag in SKIP_PATH_FRAGMENTS):
                continue
            yield path


def scan(root: Path) -> list[tuple[str, int, int, str]]:
    """Return a list of (relpath, line_no, col_no, message) violations."""
    violations: list[tuple[str, int, int, str]] = []
    for path in _iter_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # Binary asset (texture, .pyc, ...) or unreadable - not source text, skip it.
            continue
        rel = str(path.relative_to(root))
        for lineno, line in enumerate(text.splitlines(), start=1):
            lo, hi = _quote_span(line)
            for col, ch in enumerate(line):
                if ord(ch) < 128:
                    continue
                if ch in APPROVED and lo < col < hi:
                    continue
                if ch in APPROVED:
                    name = APPROVED[ch]
                    msg = "approved glyph '%s' (%s) outside a quoted string" % (ch, name)
                else:
                    msg = "non-ASCII character U+%04X" % ord(ch)
                violations.append((rel, lineno, col + 1, msg))
    return violations


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent
    violations = scan(root)
    for rel, lineno, col, msg in violations:
        print("%s:%d:%d %s" % (rel, lineno, col, msg))
    if violations:
        print("check_text: %d violation(s)" % len(violations), file=sys.stderr)
        return 1
    print("check_text: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
