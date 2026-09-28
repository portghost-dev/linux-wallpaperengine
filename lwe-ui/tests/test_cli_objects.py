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
KNOBS, KNOBS_PRESET, KNOBS_ODD, SKIPS = "1100000006", "1100000007", "1100000008", "1100000009"
KNOBS_TEXT, KNOBS_EXP = "1100000010", "1100000011"
KNOBS_ONE, KNOBS_SPACE, AUTHORED, AUTHORED_OWN = "1100000012", "1100000013", "1100000014", "1100000015"
#: an author's bool "value" as JSON gives it, and the value the engine reads from it
#: (PropertyParser.cpp::parseBoolean through JSON.h optional<bool> and coerceNumericString<bool>)
AUTHORED_BOOLS = [
    (True, True), (False, False), (None, False), (1, False), (0, False), (1.0, False), (-1, False), (2, False),
    ("true", True), ("false", False), ("1", True), ("0", False), ("1.0", True), ("0.0", False), ("2", True),
    ("-1", True), ("True", False), ("TRUE", False), ("", False), (" 1", True), ("1abc", True), ("abc", False),
    ("nan", False), ("inf", False), ("-inf", False), ("0x10", True), ("0x0", False), ("1e2", True),
    ("1e-400", False), ("+1", True), (".5", True), ("yes", False), ("on", False), ([1], False), ({"a": 1}, False),
]

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
    _json_file(LIB / KNOBS / "project.json", {"title": "Knobs", "type": "scene", "file": "scene.json", "general": {
        "properties": {"speedx": {"type": "slider", "text": "Speed", "value": 1, "min": 0, "max": 10, "step": 1},
                       "flag": {"type": "bool", "text": "Flag", "value": False},
                       "tint": {"type": "combo", "text": "Tint", "value": 2,
                                "options": [{"label": "One", "value": 1}, {"label": "Two", "value": 2}]}}}})
    _json_file(LIB / KNOBS / "scene.json", {"objects": []})
    tags.set_state(KNOBS, "Knobs", "good")
    paths.wp_file(KNOBS_PRESET).write_text(f"BG={KNOBS}\nPROP_speedx=5\nPROP_flag=true\nPROP_tint=1\n",
                                           encoding="utf-8")
    tags.set_state(KNOBS_PRESET, "Knobs Preset", "good")
    paths.wp_file(KNOBS_ODD).write_text(f"BG={KNOBS}\nPROP_speedx=fast\nPROP_flag=maybe\n", encoding="utf-8")
    tags.set_state(KNOBS_ODD, "Knobs Odd", "good")
    paths.wp_file(KNOBS_TEXT).write_text(f"BG={KNOBS}\nPROP_speedx=+5\nPROP_flag=True\n", encoding="utf-8")
    tags.set_state(KNOBS_TEXT, "Knobs Text", "good")
    paths.wp_file(KNOBS_EXP).write_text(f"BG={KNOBS}\nPROP_speedx=1e2\n", encoding="utf-8")
    tags.set_state(KNOBS_EXP, "Knobs Exp", "good")
    paths.wp_file(KNOBS_ONE).write_text(f"BG={KNOBS}\nPROP_flag=1\n", encoding="utf-8")
    tags.set_state(KNOBS_ONE, "Knobs One", "good")
    paths.wp_file(KNOBS_SPACE).write_text(f'BG={KNOBS}\nPROP_flag=" true"\n', encoding="utf-8")
    tags.set_state(KNOBS_SPACE, "Knobs Space", "good")
    _json_file(LIB / AUTHORED / "project.json", {"title": "Authored", "type": "scene", "file": "scene.json",
                                                 "general": {"properties": {
                                                     "flag": {"type": "bool", "text": "Flag", "value": "2"}}}})
    _json_file(LIB / AUTHORED / "scene.json", {"objects": []})
    tags.set_state(AUTHORED, "Authored", "good")
    paths.wp_file(AUTHORED_OWN).write_text(f"BG={AUTHORED}\nPROP_flag=2\n", encoding="utf-8")
    tags.set_state(AUTHORED_OWN, "Authored Own", "good")


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

    def test_a_filter_matches_the_type_exactly(self) -> None:
        self.assertEqual(_run("objects", SCENE, "part"), (0, "", ""))
        self.assertEqual(_run("objects", SCENE, "part", as_json=True), (0, "[]\n", ""))

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
        self.assertEqual(knobs[1], {"name": "rainamount", "label": "Rain amount", "kind": "slider", "value": 0.8,
                                    "yours": True, "options": None, "min": 0, "max": 1, "step": 0.1})
        self.assertEqual(knobs[3]["options"], [{"label": "Calm", "value": "calm"}, {"label": "Storm", "value": "storm"}])
        self.assertEqual((knobs[2]["value"], knobs[2]["yours"]), (True, False))

    def test_json_gives_each_kind_one_type_whether_or_not_it_is_yours(self) -> None:
        def values(wid: str) -> list:
            code, out, err = _run("properties", wid, as_json=True)
            self.assertEqual((code, err), (0, ""))
            return [(k["name"], k["value"], k["yours"]) for k in json.loads(out)]

        self.assertEqual(values(KNOBS), [("speedx", 1, False), ("flag", False, False), ("tint", "2", False)])
        self.assertEqual(values(KNOBS_PRESET), [("speedx", 5, True), ("flag", True, True), ("tint", "1", True)])
        self.assertEqual(values(KNOBS_ODD),
                         [("speedx", "fast", True), ("flag", False, True), ("tint", "2", False)])
        self.assertEqual(_run("properties", KNOBS_PRESET)[1].splitlines()[0],
                         "speedx  Speed: 5 (yours)  range 0 to 10, step 1")

    def test_a_bool_is_on_only_when_its_text_is_true_or_1_as_the_engine_reads_it(self) -> None:
        code, out, err = _run("properties", KNOBS_TEXT, as_json=True)
        self.assertEqual((code, err, json.loads(out)[1]["value"]), (0, "", False))
        self.assertEqual(_run("properties", KNOBS_TEXT)[1].splitlines()[1], "flag  Flag: True (yours)")

    def test_an_override_bool_is_on_only_for_exactly_true_or_1(self) -> None:
        for wid, on in ((KNOBS_ONE, True), (KNOBS_SPACE, False)):
            with self.subTest(wid=wid):
                code, out, err = _run("properties", wid, as_json=True)
                self.assertEqual((code, err, json.loads(out)[1]["value"]), (0, "", on))

    def test_an_authored_bool_reads_as_the_engine_reads_it(self) -> None:
        for value, engine in AUTHORED_BOOLS:
            with self.subTest(value=value):
                self.assertIs(scene._typed("bool", value, value), engine)
        self.assertEqual([json.loads(_run("properties", wid, as_json=True)[1])[0]["value"]
                          for wid in (AUTHORED, AUTHORED_OWN)], [True, False],
                         "the author's 2 reads on, an override text 2 off")

    def test_the_text_line_shows_the_value_as_written_and_json_the_typed_value(self) -> None:
        for wid, text, typed in ((KNOBS_TEXT, "+5", 5), (KNOBS_EXP, "1e2", 100.0)):
            with self.subTest(text=text):
                self.assertEqual(_run("properties", wid)[1].splitlines()[0],
                                 f"speedx  Speed: {text} (yours)  range 0 to 10, step 1")
                value = json.loads(_run("properties", wid, as_json=True)[1])[0]["value"]
                self.assertEqual((value, type(value)), (typed, type(typed)))

    def test_no_properties_and_refusals(self) -> None:
        self.assertEqual(_run("properties", VIDEO), (0, "no properties\n", ""))
        self.assertEqual(_run("properties", VIDEO, as_json=True), (0, "[]\n", ""))
        self.assertEqual(_run("properties", MISSING),
                         (1, "", "lwe: Gone Harbor (1100000004): its files are missing\n"))
        self.assertEqual(_run("properties", SCENE, "extra"), (3, "", "lwe: properties takes no words\n"))


class ReadsOnlyTest(unittest.TestCase):
    def test_one_token_rule_keeps_only_the_ids_the_engine_takes(self) -> None:
        from lwe_ui.engine import resolve
        from lwe_ui.storage import wp
        huge = "1" + "0" * 4999
        for text, ids in (("+5 \u0663 1_0 05 7", [5, 7]), ("-3", []), (huge, []), ("1000000 1000001", [1000000]),
                          ("00000005", [5])):
            with self.subTest(text=text[:24]):
                self.assertEqual(scene.skip_ids(text), ids)
                wp.update_set(SKIPS, {"SKIP": text})
                self.assertEqual(resolve.resolve_show_args(SKIPS)[1].get("skip_objects", []), ids)

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
