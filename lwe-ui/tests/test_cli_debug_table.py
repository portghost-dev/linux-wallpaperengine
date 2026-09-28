"""cli/debug_table.py against the engine it copies: the 81 rows of the engine's switch table equal row
for row, each description equal to that switch's --help-debug entry, and the engine's own resolve() cases
reproduced as a table, with lwe's names in the refusals. The engine source is read from
../src/WallpaperEngine beside lwe-ui; the cases are those of Testing/Cases/DebugSwitches.cpp.

Run: PYTHONPATH=src python3 tests/test_cli_debug_table.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import _cli_env

ROOT = Path(tempfile.mkdtemp(prefix="lwe-cli-debug-table-"))
HOME = _cli_env.scratch_home(ROOT)
SOURCE = Path(__file__).resolve().parents[2] / "src" / "WallpaperEngine" / "Application" / "DebugSwitches.cpp"
ULL_MAX = 2**64 - 1
TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([A-Za-z_][A-Za-z0-9_:]*)|(\d+)|([{},;])|(\S)')
TAKES = "<switch> takes <accepts>; got <value>"

# (token as the engine test gives it, variable, value) for a set or, with value None, an unset;
# (token, None, refusal) for a refusal. The token splits at its first = into switch and value.
CASES = (
    ("audit=on", "LWE_AUDIT", "1"),
    ("audit=off", "LWE_AUDIT", None),
    ("audit=yes", None, TAKES),
    ("bloom=off", "LWE_NOBLOOM", "1"),
    ("bloom=on", "LWE_NOBLOOM", None),
    ("bloom=1", None, TAKES),
    ("buffersharing=off", "LWE_FBOPOOL", "0"),
    ("shapes=off", "LWE_SHAPES", "0"),
    ("skippedobjects=hide", "LWE_SKIPGATE", "0"),
    ("frontface=counterclockwise", "LWE_FRONTFACE", "ccw"),
    ("trailtiming=exact", "LWE_TRAILMODE", "exact"),
    ("buffersharingstats=on", "LWE_POOL_HWM", "1"),
    ("frontface=ccw", None, TAKES),
    ("animationtiming=legacy", "LWE_ANIMFRACTION", "0"),
    ("cropoffset=reverse", "LWE_CROPOFF", "2"),
    ("modeltint=blue", "LWE_TINTFIX", "2"),
    ("weblog=warning", "LWE_CEFLOG", None),
    ("weblog=debug", None, TAKES),
    ("webdebug=1", "LWE_CEFDEBUG", "1"),
    ("webdebug=65535", "LWE_CEFDEBUG", "65535"),
    ("webdebug=0", None, TAKES),
    ("webdebug=65536", None, TAKES),
    ("webdebug=off", "LWE_CEFDEBUG", None),
    ("overlaysize=8", "LWE_OVERLAY_SIZE", "8"),
    ("overlaysize=200", "LWE_OVERLAY_SIZE", "200"),
    ("overlaysize=7", None, TAKES),
    ("overlaysize=201", None, TAKES),
    ("sceneimageframe=4", "LWE_FBDUMP_FRAME", "4"),
    ("sceneimageframe=3", None, TAKES),
    ("sceneimageframe=2147483647", "LWE_FBDUMP_FRAME", "2147483647"),
    ("sceneimageframe=2147483648", None, TAKES),
    ("testtexturelimit=256", "LWE_TEXCAP", "256"),
    ("testtexturelimit=255", None, TAKES),
    ("testtexturelimit=default", "LWE_TEXCAP", None),
    ("testtexturelimit=2147483647", "LWE_TEXCAP", "2147483647"),
    ("testtexturelimit=2147483648", None, TAKES),
    ("hidelight=0", "LWE_KILLLIGHT", "0"),
    ("hidelight=off", "LWE_KILLLIGHT", None),
    ("hidelight=2147483647", "LWE_KILLLIGHT", "2147483647"),
    ("hidelight=2147483648", None, TAKES),
    ("videobuffer=1", "LWE_MPV_DEMUX_MB", "1"),
    ("videobuffer=0", None, TAKES),
    ("videobuffer=2147483647", "LWE_MPV_DEMUX_MB", "2147483647"),
    ("videobuffer=2147483648", None, TAKES),
    ("videoextraframes=256", "LWE_MPV_EXTRA_FRAMES", "256"),
    ("videoextraframes=257", None, TAKES),
    ("videothreads=2147483647", "LWE_MPV_THREADS", "2147483647"),
    ("videothreads=2147483648", None, TAKES),
    ("passprobe=2147483647", "LWE_PASSPROBE", "2147483647"),
    ("passprobe=2147483648", None, TAKES),
    ("overlaytext=hello world", "LWE_OVERLAY_TEXT", "hello world"),
    ("webcrashlimit=3,60000,300000", "LWE_WEB_CRASHGUARD", "3,60000,300000"),
    ("webcrashlimit=3,60000", None, TAKES),
    ("webcrashlimit=1,0,0", "LWE_WEB_CRASHGUARD", "1,0,0"),
    ("webcrashlimit=2147483647,2147483647,2147483647", "LWE_WEB_CRASHGUARD", "2147483647,2147483647,2147483647"),
    ("webcrashlimit=0,60000,300000", None, TAKES),
    ("webcrashlimit=2147483648,0,0", None, TAKES),
    ("webcrashlimit=1,9223372036854775808,0", None, TAKES),
    ("webcrashlimit=1,2147483648,0", None, TAKES),
    ("webcrashlimit=1,0,2147483648", None, TAKES),
    ("mouseposition=0.5,0.25", "LWE_MOUSE_POS", "0.5,0.25"),
    ("mouseposition=1.5,0", None, TAKES),
    ("mouseposition=off", "LWE_MOUSE_POS", None),
    ("mouseposition=0,0", "LWE_MOUSE_POS", "0,0"),
    ("mouseposition=-0.1,0", None, TAKES),
    ("mouseposition=0x0.1,0", None, TAKES),
    ("objectpixels=0 0 10 10", "LWE_OBJPROBE", "0 0 10 10"),
    ("objectpixels=0 0 10 10 2", "LWE_OBJPROBE", "0 0 10 10 2"),
    ("objectpixels=0 0 10", None, TAKES),
    ("objectpixels=0 0 2147483647 1 2147483447", "LWE_OBJPROBE", "0 0 2147483647 1 2147483447"),
    ("objectpixels=0 0 10 10 2147483448", None, TAKES),
    ("objectpixels=10 0 5 10", None, TAKES),
    ("objectpixels=0 10 10 5", None, TAKES),
    ("testresolution=3840x2160", "LWE_CLAMPOUTPUT", "3840x2160"),
    ("testresolution=0x10", None, TAKES),
    ("testresolution=default", "LWE_CLAMPOUTPUT", None),
    ("testresolution=2147483647x1", "LWE_CLAMPOUTPUT", "2147483647x1"),
    ("testresolution=2147483648x1", None, TAKES),
    ("shaderoption=FOO_BAR=1", "LWE_FORCECOMBO", "FOO_BAR=1"),
    ("shaderoption==1", None, TAKES),
    ("shaderoption=FOO=x", None, TAKES),
    ("shaderoption=_A1=2147483647", "LWE_FORCECOMBO", "_A1=2147483647"),
    ("shaderoption=A=2147483648", None, TAKES),
    ("shaderoption=1FOO=1", None, TAKES),
    ("shaderoption=GL_FOO=1", None, TAKES),
    ("shaderoption=gl_foo=1", None, TAKES),
    ("webidletime=1500", "LWE_WEB_IDLE_EXIT_MS", "1500"),
    ("webidletime=2s", "LWE_WEB_IDLE_EXIT_MS", "2000"),
    ("webidletime=1500ms", "LWE_WEB_IDLE_EXIT_MS", "1500"),
    ("webidletime=1.5s", None, TAKES),
    ("webidletime=9223372036854775807", "LWE_WEB_IDLE_EXIT_MS", "9223372036854775807"),
    ("webidletime=9223372036854775s", "LWE_WEB_IDLE_EXIT_MS", "9223372036854775000"),
    ("webidletime=9223372036854775808", None, TAKES),
    ("webidletime=9223372036854776s", None, TAKES),
    ("sceneimage=off", "LWE_FBDUMP", None),
    ("overlayfont=default", "LWE_OVERLAY_FONT", None),
    ("shadersource=off", "LWE_SHADERDUMP_MATCH", None),
    ("webhelpersocket=default", "LWE_WEB_SOCKET", None),
    ("audit", None, "debug takes <switch> <value>; got audit"),
    ("audit=", None, "debug takes <switch> <value>; got audit"),
    ("=on", None, "debug takes <switch> <value>; got on"),
    ("nosuch=on", None, "unknown debugging switch nosuch; help --debug lists them"),
    ("webdebug=0", None, "webdebug takes a port from 1 to 65535 | off; got 0"),
)


def engine_rows(text: str) -> list[tuple]:
    """The rows of DebugSwitches.cpp's table() as (name, variable, accepts, words, rule, lo, hi)."""
    start = text.index("static const std::vector<Row> rows = ") + len("static const std::vector<Row> rows = ")
    tokens: list[tuple[str, object]] = []
    for m in TOKEN.finditer(text, start):
        string, ident, number, punct, _other = m.groups()
        if punct == ";":
            break
        if string is not None:
            if tokens and tokens[-1][0] == "str":
                tokens[-1] = ("str", tokens[-1][1] + string)
            else:
                tokens.append(("str", string))
        elif ident is not None:
            tokens.append(("id", None if ident == "std::nullopt" else ident.split("::")[-1]))
        elif number is not None:
            tokens.append(("num", int(number)))
        else:
            tokens.append(("p", punct))
    at = 0

    def value():
        nonlocal at
        kind, item = tokens[at]
        at += 1
        if (kind, item) != ("p", "{"):
            return item
        items = []
        while tokens[at] != ("p", "}"):
            items.append(value())
            if tokens[at] == ("p", ","):
                at += 1
        at += 1
        return items

    rows = []
    for row in value():
        name, variable, accepts, words, *rest = row + [[]] * (4 - len(row))
        rule, lo, hi = (rest + ["Words", 0, ULL_MAX][len(rest):])[:3]
        rows.append((name, variable, accepts, tuple((w[0], w[1]) for w in words), rule, lo, hi))
    return rows


def engine_descriptions(text: str) -> dict[str, str]:
    """Each switch's description in the --help-debug text, its wrapped lines joined by spaces."""
    body = text[text.index('return R"(') + len('return R"('):text.index(')";')]
    entries: dict[str, list[str]] = {}
    current = None
    for line in body.splitlines():
        m = re.match(r"  (\w+) <[^>]*>(?: +(\S.*))?$", line)
        if m and not line.startswith(" " * 44):
            current = m.group(1)
            entries[current] = [m.group(2)] if m.group(2) else []
        elif current is not None and line.startswith(" " * 44):
            entries[current].append(line.strip())
    return {name: " ".join(parts) for name, parts in entries.items()}


def tearDownModule() -> None:
    shutil.rmtree(ROOT, True)


class DebugTableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lwe_ui.cli import debug_table
        cls.table = debug_table
        cls.source = SOURCE.read_text(encoding="utf-8")

    def test_the_rows_equal_the_engine_table_row_for_row(self) -> None:
        mine = [(r.name, r.variable, r.accepts, r.words, r.rule, r.lo, r.hi) for r in self.table.ROWS]
        theirs = engine_rows(self.source)
        self.assertEqual(len(theirs), 81)
        self.assertEqual(mine, theirs)

    def test_each_description_is_the_switch_help_text_entry(self) -> None:
        self.assertEqual({r.name: r.description for r in self.table.ROWS}, engine_descriptions(self.source))

    def test_resolve_reproduces_the_engine_cases(self) -> None:
        self.assertEqual(len(CASES), 103)
        for token, variable, expected in CASES:
            name, _, value = token.partition("=")
            with self.subTest(token=token):
                if variable is not None:
                    self.assertEqual(self.table.resolve(name, value), (variable, expected))
                    continue
                with self.assertRaises(self.table.Refusal) as caught:
                    self.table.resolve(name, value)
                if expected == TAKES:
                    expected = f"{name} takes {self.table.BY_NAME[name].accepts}; got {value}"
                self.assertEqual(str(caught.exception), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
