"""lwe list, workshop and scan: the library catalog printed, one child command per run.

Every run is a child `python3 -m lwe_ui --lwe <stamp> <folder> <words>` whose PySide6 import raises and
whose environment is built from nothing, over one scratch store: the catalog test's store with the
reference import as a video and one more download, so the Workshop numbers reach two digits. list
prints the pool by number with alias and missing marks and keeps the numbers under a type word;
workshop continues the numbers and marks the waiting rows; scan counts; -j prints JSON; every printed
number picks the wallpaper on its line; stray words exit 3; and the runs leave the config and state
trees as they were.

Run: PYTHONPATH=src python3 tests/test_cli_library_list.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.TemporaryDirectory(prefix="lwe-library-list-")
_ROOT = Path(_TMP.name)
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = str(_ROOT / _sub) if _sub else str(_ROOT)
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from lwe_ui import version  # noqa: E402
from lwe_ui.cli import select  # noqa: E402
from lwe_ui.storage import paths, records, settings, tags  # noqa: E402

LIB = _ROOT / "lib"
WORKSHOP = _ROOT / "workshop"
REFERENCE = _ROOT / "refsrc" / "501"
UNSAFE = ("lwe: 1 folders were left out because their names use characters other than letters, digits, "
          "dot, underscore and hyphen")
RUNS = {
    "list": ["list"], "list-json": ["-j", "list"], "list-video": ["list", "video"],
    "list-web": ["list", "web"], "workshop": ["workshop"], "workshop-json": ["workshop", "--json"],
    "scan": ["scan"], "scan-json": ["-j", "scan"], "list-two-words": ["list", "scene", "video"],
    "list-other-word": ["list", "old"], "workshop-word": ["workshop", "x"], "scan-word": ["scan", "x"],
}
LINE = re.compile(r"^ *(\d+)  .* \(([^()]*)\)(?:  @\S+)?(?:  (?:files missing|waiting for review))?$")
_RESULTS: dict[str, subprocess.CompletedProcess] = {}
_TREES: list[dict] = []


def _item(folder: Path, title: str, kind: str = "scene", payload: bool = True) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    file = {"video": "video.mp4", "web": "index.html"}.get(kind, "scene.json")
    (folder / "project.json").write_text(json.dumps({"title": title, "type": kind, "file": file}),
                                         encoding="utf-8")
    if payload:
        (folder / ("scene.pkg" if kind == "scene" else file)).write_bytes(b"x" * 16)


def _conf(wid: str, text: str) -> None:
    paths.wp_file(wid).write_text(text, encoding="utf-8")


def _store() -> None:
    paths.ensure_dirs()
    settings.ensure_exists()
    settings.save({**settings.load(), "WALLPAPERS_DIR": str(LIB), "WORKSHOP_DIR": str(WORKSHOP)})
    _item(LIB / "101", "Delta")
    tags.set_state("101", "Delta", "good")
    tags.set_state("102", "Bravo", "good")
    _item(LIB / "103", "Echo")
    tags.set_state("103", "Echo", "review")
    _item(LIB / "104", "Trashed One")
    _item(WORKSHOP / "104", "Trashed One")
    tags.set_state("104", "Trashed One", "bad")
    _item(LIB / "105", "alpha")
    tags.set_state("105", "alpha", "old")
    _item(LIB / "701", "Golf")
    _item(LIB / "bad name", "Unsafe")
    _item(WORKSHOP / "301", "Golf", "video")
    _item(WORKSHOP / "302", "Half", payload=False)
    _item(WORKSHOP / "303", "Juliet")
    _item(paths.manual_dir() / "hand_made", "Hotel", "web")
    _item(LIB / "401", "Alpha")
    tags.set_state("401", "Alpha", "good")
    _conf("401", "BG=101\n")
    _item(REFERENCE, "India", "video")
    tags.set_state("501", "India", "good")
    _conf("501", f"BG={REFERENCE}\n")
    records.append("601", records.make_event("deleted", where="library"))
    _conf("101", "ALIAS=first\nALIAS=Deep\n")
    _conf("105", "ALIAS=dEEP\n")


def _trees() -> dict:
    """Every path under the config and state trees with its kind, size, modification time and bytes."""
    out = {}
    for root in (_ROOT / "c", _ROOT / "s"):
        for folder, dirs, files in os.walk(root):
            for name in dirs + files:
                p = Path(folder) / name
                st = p.lstat()
                digest = "" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
                out[str(p.relative_to(_ROOT))] = (p.is_dir(), st.st_size, st.st_mtime_ns, digest)
    return out


def _child(args: list[str]) -> subprocess.CompletedProcess:
    env = {"HOME": str(_ROOT), "XDG_CONFIG_HOME": str(_ROOT / "c"), "XDG_STATE_HOME": str(_ROOT / "s"),
           "XDG_DATA_HOME": str(_ROOT / "d"), "XDG_RUNTIME_DIR": str(_ROOT / "rt"),
           "LWE_SOCKET": str(_ROOT / "rt" / "engine.sock"), "PATH": str(_ROOT / "bin"),
           "PYTHONPATH": os.pathsep.join([str(_ROOT / "poison"), str(SRC)]), "LWE_SANDBOX": "1",
           "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(args, env=env, cwd=_ROOT / "cwd", capture_output=True, encoding="utf-8",
                          timeout=120)


def setUpModule() -> None:
    _store()
    poison = _ROOT / "poison" / "PySide6"
    poison.mkdir(parents=True)
    (poison / "__init__.py").write_text('raise ImportError("PySide6 is unavailable in this test")\n',
                                        encoding="utf-8")
    for sub in ("bin", "rt", "cwd"):
        (_ROOT / sub).mkdir()
    bite = _child([sys.executable, "-c", "import PySide6"])
    if bite.returncode == 0 or "ImportError" not in bite.stderr:
        raise AssertionError(f"the PySide6 poison did not bite: {bite.returncode} {bite.stderr}")
    _TREES.append(_trees())
    stamp = version.panel_stamp()
    for name, words in RUNS.items():
        _RESULTS[name] = _child([sys.executable, "-m", "lwe_ui", "--lwe", stamp, str(_ROOT / "cwd"), *words])
    _TREES.append(_trees())


class LibraryListTest(unittest.TestCase):
    def done(self, name: str, stdout: str) -> None:
        r = _RESULTS[name]
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, stdout)
        self.assertEqual(r.stderr.splitlines(), [UNSAFE])

    def test_list_prints_the_pool_with_alias_and_missing_marks(self) -> None:
        self.done("list", "1  alpha (105)  @dEEP\n"
                          "2  Alpha (401)\n"
                          "3  Bravo (102)  files missing\n"
                          "4  Delta (101)  @Deep\n"
                          "5  India (501)\n")

    def test_a_type_word_keeps_the_numbers(self) -> None:
        self.done("list-video", "5  India (501)\n")
        self.done("list-web", "")

    def test_workshop_continues_the_numbers_and_marks_waiting_rows(self) -> None:
        self.done("workshop", " 6  Echo (103)  waiting for review\n"
                              " 7  Golf (301)\n"
                              " 8  Golf (701)  waiting for review\n"
                              " 9  Hotel (hand_made)\n"
                              "10  Juliet (303)\n")

    def test_scan_counts(self) -> None:
        self.done("scan", "3 new Workshop downloads, 2 waiting for review; lwe workshop lists them.\n")

    def test_json_shapes(self) -> None:
        def row(n, wid, title, alias, kind, state):
            return {"n": n, "id": wid, "title": title, "alias": alias, "type": kind, "state": state}

        for name in ("list-json", "workshop-json", "scan-json"):
            self.assertEqual(_RESULTS[name].returncode, 0, _RESULTS[name].stderr)
            self.assertEqual(_RESULTS[name].stdout.count("\n"), 1)
        self.assertEqual(json.loads(_RESULTS["list-json"].stdout), [
            row(1, "105", "alpha", "dEEP", "scene", "pool"), row(2, "401", "Alpha", "", "scene", "pool"),
            row(3, "102", "Bravo", "", "", "missing"), row(4, "101", "Delta", "Deep", "scene", "pool"),
            row(5, "501", "India", "", "video", "pool")])
        self.assertEqual(json.loads(_RESULTS["workshop-json"].stdout), [
            row(6, "103", "Echo", "", "scene", "waiting"), row(7, "301", "Golf", "", "video", "download"),
            row(8, "701", "Golf", "", "scene", "waiting"), row(9, "hand_made", "Hotel", "", "web", "download"),
            row(10, "303", "Juliet", "", "scene", "download")])
        self.assertEqual(json.loads(_RESULTS["scan-json"].stdout), {"new": 3, "waiting": 2})

    def test_every_printed_number_picks_the_wallpaper_on_its_line(self) -> None:
        lines = (_RESULTS["list"].stdout + _RESULTS["workshop"].stdout).splitlines()
        self.assertEqual(len(lines), 10)
        for line in lines:
            with self.subTest(line=line):
                number, wid = LINE.match(line).groups()
                self.assertEqual(select.wallpaper(number).ui_id, wid)

    def test_stray_words_exit_3(self) -> None:
        for name, message in (("list-two-words", "lwe: list takes scene, video or web"),
                              ("list-other-word", "lwe: list takes scene, video or web"),
                              ("workshop-word", "lwe: workshop takes no words"),
                              ("scan-word", "lwe: scan takes no words")):
            with self.subTest(run=name):
                self.assertEqual(_RESULTS[name].returncode, 3)
                self.assertEqual(_RESULTS[name].stdout, "")
                self.assertEqual(_RESULTS[name].stderr.splitlines(), [message])

    def test_the_runs_leave_config_and_state_as_they_were(self) -> None:
        self.assertEqual(_TREES[1], _TREES[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
