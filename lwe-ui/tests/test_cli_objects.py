"""wallpaper <w> properties and objects: the entries of objects.WALLPAPER_WORDS, called in process with
picks from select.

One scratch store holds the scene of tests/fixtures/cli/scene-objects (parents, a part its author hid,
a shape, particles named rain) with its own SKIP and PROP_ values, a preset of it with other values, a
video, a pick whose files are missing and a scene whose parents loop. Parts print as a tree in scene
order; a filter keeps the parts it matches and their ancestors; SKIP and the author's visibility mark
parts; a preset reads its base's scene with its own values; a video has no parts; properties show the
value in force and mark the wallpaper's own; nothing is written, the editor's caches included. A
PySide6-poisoned child with an environment built from nothing imports both modules.

Run: PYTHONPATH=src python3 tests/test_cli_objects.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _cli_env

_TMP = tempfile.TemporaryDirectory(prefix="lwe-objects-")
_ROOT = Path(_TMP.name)
_cli_env.scratch_home(_ROOT)
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "src"))

from lwe_ui.cli import Context, select  # noqa: E402
from lwe_ui.cli.verbs.objects import WALLPAPER_WORDS  # noqa: E402
from lwe_ui.library import scene  # noqa: E402
from lwe_ui.storage import paths, settings, tags  # noqa: E402

FIXTURE = TESTS / "fixtures" / "cli" / "scene-objects"
LIB = _ROOT / "lib"
SCENE, PRESET, VIDEO, MISSING, LOOP = "1100000001", "1100000002", "1100000003", "1100000004", "1100000005"

TREE = [
    "1  image  Harbor",
    "  2  image  Boats",
    "    3  particle  Light Rain",
    "      9  particle  Drip",
    "  4  light  Lamp glow  hidden by you",
    "5  image  Watermark  hidden by its author  hidden by you",
    "  6  particle  Sparks",
    "7  text  Clock",
    "8  sound  Rainfall sound",
]


def _json_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def setUpModule() -> None:
    paths.ensure_dirs()
    settings.ensure_exists()
    settings.save({**settings.load(), "WALLPAPERS_DIR": str(LIB), "WORKSHOP_DIR": str(_ROOT / "workshop")})
    shutil.copytree(FIXTURE, LIB / SCENE)
    tags.set_state(SCENE, "Night Harbor", "good")
    paths.wp_file(SCENE).write_text("SKIP=4 5 x 999\nPROP_rainamount=0.8\n", encoding="utf-8")
    tags.set_state(PRESET, "Harbor Storm", "good")
    paths.wp_file(PRESET).write_text(f"BG={SCENE}\nSKIP=2\nPROP_mood=storm\n", encoding="utf-8")
    _json_file(LIB / VIDEO / "project.json", {"title": "Waves", "type": "video", "file": "waves.mp4"})
    (LIB / VIDEO / "waves.mp4").write_bytes(b"x" * 16)
    tags.set_state(VIDEO, "Waves", "good")
    tags.set_state(MISSING, "Gone Harbor", "good")
    _json_file(LIB / LOOP / "project.json", {"title": "Loops", "type": "scene", "file": "scene.json"})
    _json_file(LIB / LOOP / "scene.json", {"objects": [
        {"id": 21, "name": "Ring A", "image": "a.json", "parent": 22},
        {"id": 22, "name": "Ring B", "image": "b.json", "parent": 21},
        {"id": 23, "name": "Self", "image": "c.json", "parent": 23},
        {"id": 24, "name": "Hanger", "image": "d.json", "parent": 22},
    ]})
    tags.set_state(LOOP, "Loops", "good")


def _run(word: str, wid: str, *args: str, as_json: bool = False) -> tuple[int, str, str]:
    ctx = Context(as_json, io.StringIO(), io.StringIO(), None, False)
    code = WALLPAPER_WORDS[word](ctx, select.wallpaper(wid), list(args))
    return code, ctx.out.getvalue(), ctx.err.getvalue()


def _lines(*lines: str) -> str:
    return "".join(line + "\n" for line in lines)


def _files(root: Path) -> dict:
    """Every path under root with its kind, size and modification time."""
    out = {}
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(folder) / name
            st = path.lstat()
            out[str(path.relative_to(root))] = (path.is_dir(), st.st_size, st.st_mtime_ns)
    return out


class ObjectsTest(unittest.TestCase):
    def test_parts_print_as_a_tree_in_scene_order(self) -> None:
        self.assertEqual(_run("objects", SCENE), (0, _lines(*TREE), ""))

    def test_a_filter_keeps_the_parts_it_matches_and_their_ancestors(self) -> None:
        by_type = _lines(TREE[0], TREE[1], TREE[2], TREE[3], TREE[5], TREE[6])
        self.assertEqual(_run("objects", SCENE, "particle"), (0, by_type, ""))
        self.assertEqual(_run("objects", SCENE, "PARTICLE"), (0, by_type, ""))
        self.assertEqual(_run("objects", SCENE, "rain"), (0, _lines(TREE[0], TREE[1], TREE[2], TREE[8]), ""))

    def test_a_preset_reads_its_base_scene_with_its_own_skip(self) -> None:
        self.assertEqual(_run("objects", PRESET), (0, _lines(
            "1  image  Harbor",
            "  2  image  Boats  hidden by you",
            "    3  particle  Light Rain",
            "      9  particle  Drip",
            "  4  light  Lamp glow",
            "5  image  Watermark  hidden by its author",
            "  6  particle  Sparks",
            "7  text  Clock",
            "8  sound  Rainfall sound"), ""))

    def test_a_parent_cycle_is_cut_at_the_first_repeat(self) -> None:
        self.assertEqual(_run("objects", LOOP), (0, _lines(
            "21  image  Ring A",
            "  22  image  Ring B",
            "    24  image  Hanger",
            "23  image  Self"), ""))

    def test_json_lists_each_part_with_its_parent_and_depth(self) -> None:
        code, out, err = _run("objects", SCENE, "rain", as_json=True)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, '[{"id":"1","type":"image","name":"Harbor","parent":"","depth":0,'
                         '"author_hidden":false,"you_hidden":false},'
                         '{"id":"2","type":"image","name":"Boats","parent":"1","depth":1,'
                         '"author_hidden":false,"you_hidden":false},'
                         '{"id":"3","type":"particle","name":"Light Rain","parent":"2","depth":2,'
                         '"author_hidden":false,"you_hidden":false},'
                         '{"id":"8","type":"sound","name":"Rainfall sound","parent":"","depth":0,'
                         '"author_hidden":false,"you_hidden":false}]\n')

    def test_refusals(self) -> None:
        self.assertEqual(_run("objects", VIDEO), (1, "", "lwe: only scenes have parts\n"))
        self.assertEqual(_run("objects", MISSING),
                         (1, "", "lwe: Gone Harbor (1100000004): its files are missing\n"))
        self.assertEqual(_run("objects", SCENE, "rain", "boats"),
                         (3, "", "lwe: objects takes at most one filter word\n"))
        self.assertEqual(_run("objects", VIDEO, as_json=True), (1, "", '{"error":"only scenes have parts"}\n'))


class PropertiesTest(unittest.TestCase):
    def test_each_knob_shows_the_value_in_force_and_marks_yours(self) -> None:
        self.assertEqual(_run("properties", SCENE), (0, _lines(
            "schemecolor  ui_browse_properties_scheme_color: 0.2 0.3 0.8",
            "rainamount  Rain amount: 0.8 (yours)  range 0 to 1, step 0.1",
            "showclock  Show clock: true",
            "mood  Mood: calm  choices: calm, storm",
            "caption  Caption: Harbor"), ""))

    def test_a_preset_reads_its_base_properties_with_its_own_values(self) -> None:
        self.assertEqual(_run("properties", PRESET), (0, _lines(
            "schemecolor  ui_browse_properties_scheme_color: 0.2 0.3 0.8",
            "rainamount  Rain amount: 0.5  range 0 to 1, step 0.1",
            "showclock  Show clock: true",
            "mood  Mood: storm (yours)  choices: calm, storm",
            "caption  Caption: Harbor"), ""))

    def test_json_lists_the_knobs(self) -> None:
        code, out, err = _run("properties", SCENE, as_json=True)
        self.assertEqual((code, err), (0, ""))
        knobs = json.loads(out)
        self.assertEqual([k["name"] for k in knobs], ["schemecolor", "rainamount", "showclock", "mood", "caption"])
        self.assertEqual(knobs[1], {"name": "rainamount", "label": "Rain amount", "kind": "slider", "value": "0.8",
                                    "yours": True, "options": None, "min": 0, "max": 1, "step": 0.1})
        self.assertEqual(knobs[3]["options"], [{"label": "Calm", "value": "calm"}, {"label": "Storm", "value": "storm"}])
        self.assertEqual((knobs[2]["value"], knobs[2]["yours"]), (True, False))

    def test_no_properties_and_refusals(self) -> None:
        self.assertEqual(_run("properties", VIDEO), (0, "no properties\n", ""))
        self.assertEqual(_run("properties", VIDEO, as_json=True), (0, "[]\n", ""))
        self.assertEqual(_run("properties", MISSING),
                         (1, "", "lwe: Gone Harbor (1100000004): its files are missing\n"))
        self.assertEqual(_run("properties", SCENE, "extra"), (3, "", "lwe: properties takes no words\n"))


class ReadsOnlyTest(unittest.TestCase):
    def test_skip_ids_keeps_ascii_digit_tokens(self) -> None:
        self.assertEqual(scene.skip_ids("4 x 999 -1 +2 \u0663 0012\t7"), [4, 999, 12, 7])

    def test_nothing_is_written(self) -> None:
        before = _files(_ROOT)
        for wid in (SCENE, PRESET, VIDEO, MISSING, LOOP):
            for word in ("objects", "properties"):
                for as_json in (False, True):
                    _run(word, wid, as_json=as_json)
        self.assertEqual(list(paths.objindex_dir().iterdir()), [])
        self.assertEqual(list(paths.propindex_dir().iterdir()), [])
        self.assertEqual(_files(_ROOT), before)

    def test_a_poisoned_child_imports_both_modules(self) -> None:
        scratch = Path(tempfile.mkdtemp(prefix="lwe-objects-child-"))
        self.addCleanup(shutil.rmtree, scratch, True)
        env = _cli_env.scratch_env(scratch)
        bite = subprocess.run([sys.executable, "-c", "import PySide6"], env=env, capture_output=True,
                              text=True, timeout=60)
        self.assertNotEqual(bite.returncode, 0)
        self.assertIn("ImportError", bite.stderr)
        child = subprocess.run([sys.executable, "-c", "import lwe_ui.cli.verbs.objects, lwe_ui.library.scene"],
                               env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(child.returncode, 0, child.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
