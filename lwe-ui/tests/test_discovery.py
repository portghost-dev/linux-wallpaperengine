"""Self-tests for the discovery modules (pkg / project / objects / properties).

Read-only against the real library under ~/.local/share/lwe/wallpapers/. `build_index` writes are
redirected to a tempfile.mkdtemp via XDG_STATE_HOME so nothing under the live config/data dirs is
touched. Run:

    export PYTHONPATH=src
    python3 tests/test_discovery.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lwe_ui import constants  # noqa: E402
from lwe_ui.discovery import objects, pkg, project, properties  # noqa: E402

WALLPAPERS_DIR = Path(os.path.expanduser("~/.local/share/lwe/wallpapers"))


def _find_scene_wallpaper() -> tuple[str, Path] | None:
    """First (id, dir) under the library whose project.json type is 'scene' with a scene.pkg."""
    if not WALLPAPERS_DIR.is_dir():
        return None
    for child in sorted(WALLPAPERS_DIR.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "scene.pkg").is_file():
            continue
        if project.read(child)["type"] == "scene":
            return child.name, child
    return None


class TestClassify(unittest.TestCase):
    """Pure classify() unit cases - no filesystem needed (the load-bearing guards)."""

    def test_particle_with_null_image(self):
        # Particle objects carry image:null/model:null; must NOT be read as image.
        self.assertEqual(
            objects.classify({"particle": "x", "image": None, "model": None}),
            "particle",
        )

    def test_image_is_string(self):
        self.assertEqual(objects.classify({"image": "models/foo.json"}), "image")

    def test_sound_is_list(self):
        self.assertEqual(objects.classify({"sound": ["a.mp3"]}), "sound")

    def test_text_light_shape_generic(self):
        self.assertEqual(objects.classify({"text": {}}), "text")
        self.assertEqual(objects.classify({"light": {}}), "light")
        self.assertEqual(objects.classify({"shape": {}}), "light")
        self.assertEqual(objects.classify({}), "generic")

    def test_explicit_type_wins(self):
        self.assertEqual(objects.classify({"type": "Effect", "image": "x"}), "effect")


class TestDiscoveryAgainstLibrary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        found = _find_scene_wallpaper()
        if found is None:
            raise unittest.SkipTest("no scene wallpaper with scene.pkg under the live library")
        cls.wid, cls.wdir = found

    def test_pkg_lists_scene_json(self):
        reader = pkg.PkgReader(self.wdir / "scene.pkg")
        self.assertIn("scene.json", reader.names())
        self.assertTrue(reader.header.startswith("PKGV"))
        self.assertGreater(len(reader.read("scene.json")), 0)

    def test_read_scene_json_convenience(self):
        scene = pkg.read_scene_json(self.wdir / "scene.pkg")
        self.assertIsInstance(scene, dict)
        self.assertIn("objects", scene)

    def test_project_read_type_in_enum(self):
        info = project.read(self.wdir)
        self.assertIn(info["type"], {"scene", "video", "web"})
        self.assertEqual(info["id"], self.wid)
        self.assertTrue(info["preview"])
        self.assertTrue(os.path.isabs(info["preview"]))

    def test_extract_non_empty_with_particle_or_image(self):
        objs = objects.extract(self.wdir)
        self.assertGreater(len(objs), 0)
        for o in objs:
            self.assertTrue({"objid", "name", "type"}.issubset(o.keys()))
        kinds = {o["type"] for o in objs}
        self.assertTrue(kinds & {"particle", "image"}, f"expected particle/image, got {kinds}")

    def test_build_indexes_write_to_temp_state(self):
        tmp = tempfile.mkdtemp(prefix="lwe-discovery-test-")
        prev = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = tmp
        try:
            oi = objects.build_index(self.wid, WALLPAPERS_DIR)
            pi = properties.build_index(self.wid, WALLPAPERS_DIR)
            self.assertIn("objects", oi)
            self.assertIn("properties", pi)
            for entry in oi["objects"]:
                self.assertTrue({"objid", "name", "type"}.issubset(entry.keys()))
                self.assertIn(entry["type"], constants.OBJECT_TYPES)
            for entry in pi["properties"]:
                self.assertTrue({"name", "kind", "label", "value"} <= set(entry.keys()))
                self.assertIn(entry["kind"], {"bool", "slider", "combo", "color", "text"})
                self.assertNotIn("<", entry["label"])
            self.assertTrue((Path(tmp) / "lwe" / "objindex" / f"{self.wid}.json").is_file())
            self.assertTrue((Path(tmp) / "lwe" / "propindex" / f"{self.wid}.json").is_file())
        finally:
            if prev is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = prev


def _report() -> None:
    """Human-readable proof line printed before the unittest run."""
    found = _find_scene_wallpaper()
    if found is None:
        print("REPORT: no scene wallpaper found under the live library")
        return
    wid, wdir = found
    info = project.read(wdir)
    reader = pkg.PkgReader(wdir / "scene.pkg")
    objs = objects.extract(wdir)
    counts: dict[str, int] = {}
    for o in objs:
        counts[o["type"]] = counts.get(o["type"], 0) + 1
    propidx = properties.build_index
    print(f"REPORT: scene wallpaper id={wid} type={info['type']} title={info['title']!r}")
    print(f"REPORT: pkg header={reader.header} files={len(reader.names())} "
          f"scene_json_in_pkg={'scene.json' in reader.names()}")
    print(f"REPORT: extract -> {len(objs)} objects; type counts = {counts}")
    sample = next((o for o in objs if o["type"] in ("particle", "image")), None)
    print(f"REPORT: sample object = {sample}")
    tmp = tempfile.mkdtemp(prefix="lwe-discovery-report-")
    prev = os.environ.get("XDG_STATE_HOME")
    os.environ["XDG_STATE_HOME"] = tmp
    try:
        pi = propidx(wid, WALLPAPERS_DIR)
    finally:
        if prev is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = prev
    print(f"REPORT: properties -> {len(pi['properties'])} entries; "
          f"kinds = {sorted({p['kind'] for p in pi['properties']})}")


if __name__ == "__main__":
    _report()
    print("-" * 60)
    unittest.main(verbosity=2, exit=True)
