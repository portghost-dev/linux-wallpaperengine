"""wallpaper <w>: details, the words that read and set one wallpaper's own values, alias, and the
words objects.py runs, called in process against tests/_fake_engine.py on the sandbox socket.

One scratch store holds a scene with an authored grading (preset wec_ keys), a plain scene with its
own values, a scene whose RENDER_RESOLUTION word is sharpfx, and a scene with an alias; the playlist
main holds the first two and is the saved active playlist, so it is engine-held. Each test rewrites
the wallpaper files and removes the sync marker first. A PySide6-poisoned child with an environment
built from nothing reads one value.

Run: PYTHONPATH=src python3 tests/test_cli_wallpaper.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _cli_env

_TMP = tempfile.TemporaryDirectory(prefix="lwe-wallpaper-")
_ROOT = Path(_TMP.name)
_cli_env.scratch_home(_ROOT)
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "src"))

import _fake_engine  # noqa: E402
from lwe_ui.cli import Context, select  # noqa: E402
from lwe_ui.cli.verbs import wallpaper  # noqa: E402
from lwe_ui.engine import marker  # noqa: E402
from lwe_ui.storage import lock, paths, settings, tags, tier_a  # noqa: E402

LIB = _ROOT / "lib"
GRADED, PLAIN, SHARP, OTHER = "1200000001", "1200000002", "1200000003", "1200000004"
FILES = {
    GRADED: "",
    PLAIN: "FIT_ZOOM=1.5\nCC=1.1 1 1 0\nCC_MODE=custom\nFULLSCREEN_PAUSE=false\nPROP_rain=0.5\n",
    SHARP: "RENDER_RESOLUTION=sharpfx\nCLAMPCOMPOSITES=2\n",
    OTHER: "ALIAS=Harbor\n",
}


def _project(wid: str, title: str, extra: dict | None = None) -> None:
    folder = LIB / wid
    folder.mkdir(parents=True, exist_ok=True)
    data = {"title": title, "type": "scene", "file": "scene.json", **(extra or {})}
    (folder / "project.json").write_text(json.dumps(data), encoding="utf-8")
    (folder / "scene.json").write_text(json.dumps({"objects": [
        {"id": 1, "name": "Sky", "image": "a.json"},
        {"id": 2, "name": "Sand", "image": "b.json", "parent": 1},
    ]}), encoding="utf-8")
    tags.set_state(wid, title, "good")


def setUpModule() -> None:
    paths.ensure_dirs()
    settings.ensure_exists()
    settings.save({**settings.load(), "WALLPAPERS_DIR": str(LIB), "WORKSHOP_DIR": str(_ROOT / "workshop"),
                   "ACTIVE_PLAYLIST": "main"})
    _project(GRADED, "Graded Dunes", {"preset": {"wec_brs": 60, "wec_con": 75}})
    _project(PLAIN, "Plain Field", {"general": {"properties": {"rain": {"type": "slider", "text": "Rain",
                                                                          "value": 0.2, "min": 0, "max": 1}}}})
    _project(SHARP, "Sharp Hills")
    _project(OTHER, "Harbor Night")
    paths.playlist_file("main").write_text(f"NAME=Main\nMEMBERS={GRADED} {PLAIN}\nMODE=shuffle\nINTERVAL=900\n",
                                           encoding="utf-8")


def _conf(wid: str) -> dict:
    path = paths.wp_file(wid)
    return tier_a.parse(path.read_text(encoding="utf-8")) if path.exists() else {}


def _run(*words: str, as_json: bool = False) -> tuple[int, str, str]:
    ctx = Context(as_json, io.StringIO(), io.StringIO(), None, False)
    code = wallpaper.VERBS[0].run(ctx, list(words))
    return code, ctx.out.getvalue(), ctx.err.getvalue()


def _on_screen(wid: str, **fields) -> _fake_engine.FakeEngine:
    return _fake_engine.FakeEngine(_sandbox.SOCKET, current={"id": wid, "ui_id": wid, "title": ""}, **fields)


def _sent(engine: _fake_engine.FakeEngine) -> list[str]:
    return [cmd for cmd, _args in engine.calls if cmd != "status"]


def _pick(wid: str) -> str:
    return select.pick_line(select.wallpaper(wid)) + "\n"


class WallpaperTest(unittest.TestCase):
    def setUp(self) -> None:
        (paths.panel_state_dir() / "sync-pending").unlink(missing_ok=True)
        for wid, text in FILES.items():
            paths.wp_file(wid).unlink(missing_ok=True)
            if text:
                paths.wp_file(wid).write_text(text, encoding="utf-8")

    def test_details(self) -> None:
        with _on_screen(PLAIN):
            code, out, err = _run(PLAIN)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, _pick(PLAIN) + "".join(line + "\n" for line in [
            "title: Plain Field", f"id: {PLAIN}", "alias: none", "type: scene", "on screen: yes",
            "own settings:", "  zoom 1.5", "  brightness 1.1", "  contrast 1", "  saturation 1", "  hue 0",
            "  CC_MODE custom", "  fullscreen off", "  property rain 0.5",
            f"workshop: https://steamcommunity.com/sharedfiles/filedetails/?id={PLAIN}"]))

    def test_zoom_on_screen_sends_set_fit_and_the_entry_refresh(self) -> None:
        with _on_screen(PLAIN) as engine:
            code, out, _err = _run(PLAIN, "zoom", "1.2")
        self.assertEqual(code, 0)
        self.assertEqual(paths.wp_file(PLAIN).read_text(encoding="utf-8"),
                         FILES[PLAIN].replace("FIT_ZOOM=1.5", "FIT_ZOOM=1.2"))
        fit = [args for cmd, args in engine.calls if cmd == "set-fit"]
        self.assertEqual(len(fit), 1)
        self.assertEqual((fit[0]["layer"], fit[0]["id"], fit[0]["zoom"]), ("wallpaper", PLAIN, 1.2))
        self.assertIn("playlist-set", _sent(engine))
        self.assertEqual(out, _pick(PLAIN) + "zoom 1.2: saved; applies now.\n")

    def test_zoom_off_screen_sends_only_the_entry_refresh(self) -> None:
        with _on_screen(OTHER) as engine:
            code, _out, _err = _run(PLAIN, "zoom", "1.3")
        self.assertEqual(code, 0)
        self.assertEqual(_conf(PLAIN)["FIT_ZOOM"], "1.3")
        self.assertEqual(set(_sent(engine)), {"playlist-set"})

    def test_brightness_seeds_from_the_authored_grading_and_writes_cc_mode_custom(self) -> None:
        code, _out, _err = _run(GRADED, "brightness", "2")
        self.assertEqual(code, 0)
        self.assertEqual(_conf(GRADED), {"CC": "2 1.5 1 0", "CC_MODE": "custom"})

    def test_hue_90_is_stored_in_radians(self) -> None:
        code, _out, _err = _run(GRADED, "hue", "90")
        self.assertEqual(code, 0)
        cc = _conf(GRADED)["CC"].split()
        self.assertEqual(cc[:3], ["1.2", "1.5", "1"])
        self.assertEqual(round(float(cc[3]), 4), 1.5708)
        self.assertEqual(_run(GRADED, "hue")[1], _pick(GRADED) + "90\n")

    def test_unset_brightness_removes_cc_and_cc_mode_and_says_so(self) -> None:
        code, out, _err = _run(PLAIN, "unset", "brightness")
        self.assertEqual(code, 0)
        conf = _conf(PLAIN)
        self.assertNotIn("CC", conf)
        self.assertNotIn("CC_MODE", conf)
        self.assertIn(wallpaper.COLOR_REMOVED, out)

    def test_unset_hue_resets_only_hue_and_keeps_the_other_three(self) -> None:
        paths.wp_file(GRADED).write_text("CC=1.2 1.5 0.8 0.5\nCC_MODE=custom\n", encoding="utf-8")
        code, out, _err = _run(GRADED, "unset", "hue")
        self.assertEqual(code, 0)
        self.assertEqual(_conf(GRADED), {"CC": "1.2 1.5 0.8 0", "CC_MODE": "custom"})
        self.assertNotIn(wallpaper.COLOR_REMOVED, out)

    def test_unsetting_the_last_channel_off_the_authored_look_removes_cc_and_cc_mode(self) -> None:
        paths.wp_file(GRADED).write_text("CC=1.2 1.5 0.8 0.5\nCC_MODE=custom\n", encoding="utf-8")
        self.assertEqual(_run(GRADED, "unset", "hue")[0], 0)
        self.assertEqual(_conf(GRADED), {"CC": "1.2 1.5 0.8 0", "CC_MODE": "custom"})
        code, out, _err = _run(GRADED, "unset", "saturation")
        self.assertEqual(code, 0)
        self.assertEqual(_conf(GRADED), {})
        self.assertIn(wallpaper.COLOR_REMOVED, out)

    def test_unset_brightness_keeps_the_contrast_set_over_an_authored_grade(self) -> None:
        paths.wp_file(GRADED).write_text("CC=1.2 1 1 0\nCC_MODE=custom\n", encoding="utf-8")
        code, out, _err = _run(GRADED, "unset", "brightness")
        self.assertEqual(code, 0)
        self.assertEqual(_conf(GRADED), {"CC": "1 1 1 0", "CC_MODE": "custom"})
        self.assertEqual(_run(GRADED, "contrast")[1], _pick(GRADED) + "1\n")
        self.assertNotIn(wallpaper.COLOR_REMOVED, out)

    def test_unset_on_a_conf_the_editor_saved_as_none_changes_nothing(self) -> None:
        body = "CC=1.2 1.5 1 0\nCC_MODE=none\n"
        paths.wp_file(GRADED).write_text(body, encoding="utf-8")
        code, out, _err = _run(GRADED, "unset", "hue")
        self.assertEqual(code, 0)
        self.assertEqual(paths.wp_file(GRADED).read_text(encoding="utf-8"), body)
        self.assertIn("\nhue 0: unchanged", out)

    def test_fullscreen_inherit_deletes_the_key(self) -> None:
        code, _out, _err = _run(PLAIN, "fullscreen", "inherit")
        self.assertEqual(code, 0)
        self.assertNotIn("FULLSCREEN_PAUSE", _conf(PLAIN))
        self.assertEqual(_run(PLAIN, "speed", "0")[0], 3)

    def test_alias_rules(self) -> None:
        code, _out, err = _run(PLAIN, "alias", "harbor")
        self.assertEqual(code, 1)
        self.assertIn(OTHER, err)
        self.assertEqual(_run(PLAIN, "alias", "12345")[0], 3)
        self.assertNotIn("ALIAS", _conf(PLAIN))
        self.assertEqual(_run(PLAIN, "alias", "field")[0], 0)
        self.assertEqual(_conf(PLAIN)["ALIAS"], "field")
        self.assertEqual(_run(PLAIN, "alias", "field")[1], _pick(PLAIN) + "alias field: unchanged.\n")
        self.assertEqual(_run(PLAIN, "unset", "alias")[0], 0)
        self.assertNotIn("ALIAS", _conf(PLAIN))

    def test_alias_takes_no_sync(self) -> None:
        taken: list[str] = []
        real = lock.held

        def recorded(store, *args, **kwargs):
            taken.append(store)
            return real(store, *args, **kwargs)

        with _on_screen(PLAIN) as engine, mock.patch.object(lock, "held", recorded):
            code, _out, _err = _run(PLAIN, "alias", "field")
        self.assertEqual(code, 0)
        self.assertIn("overrides", taken)
        self.assertNotIn("sync", taken)
        self.assertNotIn("marker", taken)
        self.assertEqual(engine.calls, [])

    def test_mouse_is_refused(self) -> None:
        code, out, err = _run(PLAIN, "mouse", "off")
        self.assertEqual((code, out), (3, ""))
        self.assertIn("mouse", err)
        self.assertEqual(paths.wp_file(PLAIN).read_text(encoding="utf-8"), FILES[PLAIN])

    def test_objects_dispatch_and_exit_3_without_objects_py(self) -> None:
        code, out, _err = _run(GRADED, "objects")
        self.assertEqual((code, out), (0, _pick(GRADED) + "1  image  Sky\n  2  image  Sand\n"))
        with mock.patch.dict(sys.modules, {"lwe_ui.cli.verbs.objects": None}):
            code, out, err = _run(GRADED, "objects")
        self.assertEqual((code, out), (3, ""))
        self.assertIn("objects", err)

    def test_a_cc_change_on_screen_sets_current_and_reshows_keeping_the_freeze(self) -> None:
        classes: list[tuple] = []
        real = marker.writing

        def recorded(cls):
            classes.append(tuple(cls))
            return real(cls)

        with _on_screen(GRADED, speed=0.0) as engine, mock.patch.object(marker, "writing", recorded):
            code, _out, _err = _run(GRADED, "saturation", "1.5")
        self.assertEqual(code, 0)
        self.assertIn("CURRENT", classes[0])
        sent = _sent(engine)
        self.assertIn("show", sent)
        after = sent[sent.index("show"):]
        self.assertIn("set-speed", after)
        speeds = [args["speed"] for cmd, args in engine.calls if cmd == "set-speed"]
        self.assertEqual(speeds, [0.0])

    def test_unset_effectclamp_on_a_sharpfx_file_writes_the_empty_marker(self) -> None:
        code, _out, _err = _run(SHARP, "unset", "effectclamp")
        self.assertEqual(code, 0)
        self.assertEqual(paths.wp_file(SHARP).read_text(encoding="utf-8"),
                         "RENDER_RESOLUTION=sharpfx\nCLAMPCOMPOSITES=\n")

    def test_a_poisoned_child_reads_one_value(self) -> None:
        env = _cli_env.scratch_env(_ROOT)
        code, out, err = _cli_env.run_lwe(["wallpaper", PLAIN, "zoom"], env, str(_ROOT))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, _pick(PLAIN) + "1.5\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
