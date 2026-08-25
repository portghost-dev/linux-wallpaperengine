"""Self-verification for the per-wallpaper editor backend (src/lwe_ui/editor.py).

REALTIME AUTOSAVE. Every setter commits straight into wp/<id>.conf on
its own gesture: there is no draft buffer, no Save verb, no dirty state and no exit-discard.
These tests assert the live-commit contract that replaced the draft/Save split - the conf
carries the value the moment the setter returns, and choosing `Global` DELETES the key rather
than materializing a default over it (key presence IS set-ness, spec L4).

The engine is never reachable in this harness (no socket), so the L2 scope gate holds every
commit to conf-write-only - which is exactly the behavior asserted by acceptance test T10.

Headless + isolated. NOTHING here signals a live process or spawns the engine:
  * HOME + every XDG_* point at a fresh tempfile dir, so all config/state paths resolve under it
    and the live ~/.config/lwe, ~/.local/state/lwe are NEVER touched;
  * a REAL scene wallpaper is COPIED out of ~/.local/share/lwe/wallpapers into the temp
    WALLPAPERS_DIR (read-only source, sandboxed writable copy).

This module deliberately runs the Qt object headless: EditorBridge is a QObject, so a
QCoreApplication is constructed with the offscreen platform; we drive it by calling the Slots /
property getters directly (no QML, no event loop).

Run: export PYTHONPATH=src && python3 tests/test_editor.py
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REAL_LIB = Path(os.path.expanduser("~")) / ".local/share/lwe/wallpapers"


def _pick_scene_with_particles() -> str | None:
    """Find a real scene wallpaper that has >=1 particle object (so group toggling is exercised)."""
    if not _REAL_LIB.is_dir():
        return None
    from lwe_ui.discovery import objects as objects_disc

    best: str | None = None
    for child in sorted(_REAL_LIB.iterdir()):
        if not (child / "scene.pkg").is_file():
            continue
        try:
            objs = objects_disc.extract(child)
        except Exception:
            continue
        types_present = {o["type"] for o in objs}
        if "particle" in types_present:
            return child.name
        if best is None and objs:
            best = child.name
    return best


class TestEditorAgainstRealScene(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtCore import QCoreApplication

        cls._app = QCoreApplication.instance() or QCoreApplication([])

        cls._scene_id = _pick_scene_with_particles()
        if cls._scene_id is None:
            raise unittest.SkipTest(f"no real scene wallpaper available under {_REAL_LIB}")

    def setUp(self) -> None:
        # Fresh sandbox per test; point HOME + XDG_* at it BEFORE reloading the path-aware modules.
        self.tmp = tempfile.mkdtemp(prefix="lwe-editor-test-")
        self._orig_env = {k: os.environ.get(k) for k in
                          ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
        os.environ["HOME"] = self.tmp
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp, ".config")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.tmp, ".local/state")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp, ".local/share")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

        # 4. Reload constants + every path-aware storage/discovery module so they re-read the env,
        #    then reload editor.py LAST so it binds the stub + fresh path modules.
        import lwe_ui.constants  # noqa: F401
        for name in ("atomic", "tier_a", "paths", "settings", "wp", "meta", "tags"):
            importlib.reload(importlib.import_module(f"lwe_ui.storage.{name}"))
        for name in ("project", "pkg", "objects", "properties"):
            importlib.reload(importlib.import_module(f"lwe_ui.discovery.{name}"))
        importlib.reload(importlib.import_module("lwe_ui.wp_session"))
        self.editor_mod = importlib.reload(importlib.import_module("lwe_ui.editor"))

        from lwe_ui.storage import paths, settings

        paths.ensure_dirs()
        self.wallpapers = Path(paths.default_wallpapers_dir())
        self.wallpapers.mkdir(parents=True, exist_ok=True)
        self.wid = self._scene_id
        shutil.copytree(_REAL_LIB / self.wid, self.wallpapers / self.wid)
        # write settings.conf so WALLPAPERS_DIR resolves to the sandbox copy
        settings.ensure_exists()

        self.editor = self.editor_mod.EditorBridge()

    def tearDown(self) -> None:
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _conf_text(self) -> str:
        from lwe_ui.storage import paths
        return paths.wp_file(self.wid).read_text(encoding="utf-8")

    def _conf_value(self, key: str) -> str:
        from lwe_ui.storage import tier_a
        return tier_a.parse(self._conf_text()).get(key, "<MISSING>")

    def _wp_exists(self) -> bool:
        from lwe_ui.storage import paths
        return paths.wp_file(self.wid).exists()

    def _live(self, key: str) -> str:
        """Read one key from the LIVE conf. Absent reads as <MISSING> - the inherit state."""
        from lwe_ui.storage import paths, tier_a
        p = paths.wp_file(self.wid)
        if not p.exists():
            return "<NO-CONF>"
        return tier_a.parse(p.read_text(encoding="utf-8")).get(key, "<MISSING>")

    def test_open_loads_header_and_typed_properties(self) -> None:
        e = self.editor
        e.open(self.wid)

        self.assertEqual(e.wallpaperId, self.wid)
        self.assertTrue(e.title, "title must be non-empty")
        self.assertEqual(e.type, "scene")
        self.assertTrue(e.previewUrl.startswith("file://"), f"previewUrl={e.previewUrl!r}")

        self.assertEqual(e.scaling, "default")
        self.assertEqual(e.speed, "1.0")
        self.assertEqual(e.cc, "1 1 1 0")
        self.assertEqual(e.volume, 0)
        self.assertEqual(e.clamping, "")
        self.assertIs(e.automute, True)
        self.assertIs(e.audioReactive, False)
        self.assertIs(e.mouse, False)
        self.assertEqual(e.monitors, "all")
        # resolution unknown (no meta) -> "" so QML hides the line
        self.assertEqual(e.resolution, "")

    def test_object_groups_nonempty_with_enabled_flags(self) -> None:
        e = self.editor
        e.open(self.wid)
        groups = e.objectGroups()
        self.assertTrue(groups, "objectGroups() must be non-empty for a real scene")
        for g in groups:
            self.assertIn("type", g)
            self.assertIn("count", g)
            self.assertIn("enabled", g)
            self.assertTrue(g["enabled"], "all groups enabled before any SKIP")
        ptypes = [g for g in groups if g["type"] == "particle"]
        self.assertEqual(len(ptypes), 1)
        self.assertEqual(e.particleCount(), ptypes[0]["count"])

    def test_scene_properties_are_typed_entries(self) -> None:
        e = self.editor
        e.open(self.wid)
        props = e.sceneProperties()
        self.assertTrue(props, "real scene exposes >=1 property (schemecolor)")
        valid_kinds = {"bool", "slider", "combo", "color", "text"}
        for p in props:
            self.assertIn(p["kind"], valid_kinds)
            for key in ("name", "kind", "label", "value", "min", "max", "step", "options"):
                self.assertIn(key, p)

    def test_setspeed_commits_live_and_marks(self) -> None:
        """L1: the setter IS the commit. No draft file is written anywhere."""
        from lwe_ui.storage import paths, wp
        e = self.editor
        e.open(self.wid)
        e.setSpeed("0.5")

        self.assertEqual(self._live("SPEED"), "0.5", "the value is in the conf immediately")
        self.assertEqual(float(wp.load(self.wid)["SPEED"]), 0.5)
        self.assertFalse(paths.draft_file(self.wid).exists(), "no draft file may be created")
        self.assertTrue(e.isMarked("SPEED"), "a changed control joins the marked set")
        self.assertTrue(e.hasMarks())
        self.assertTrue(e.canRevert())

    def test_no_draft_or_save_surface_survives(self) -> None:
        """The whole draft/Save vocabulary is gone from the bridge."""
        e = self.editor
        for gone in ("save", "discardDraft", "draftExists", "dirty", "setFps",
                     "overridesAllGlobal"):
            self.assertFalse(hasattr(e, gone), f"editor.{gone} must not exist any more")

    def test_revert_restores_session_start_values(self) -> None:
        """Revert restores every marked key to its session-start value; marks clear."""
        e = self.editor
        e.open(self.wid)
        e.setScalingValue("fill")
        props = e.sceneProperties()
        name = props[0]["name"]
        e.setProp(name, "0.2 0.4 0.6")
        self.assertEqual(self._live("SCALING"), "fill")
        self.assertEqual(self._live(f"PROP_{name}"), "0.2 0.4 0.6")

        self.assertTrue(e.revertChanges())
        # both keys were ABSENT at seat time, so restoring them means deleting them again -
        # not writing a plausible default over the top (F16 part 3)
        self.assertEqual(self._live("SCALING"), "<MISSING>")
        self.assertEqual(self._live(f"PROP_{name}"), "<MISSING>")
        self.assertFalse(e.hasMarks(), "marks clear on revert")
        self.assertFalse(e.canRevert(), "the verb disables once nothing is marked")

    def test_load_defaults_strips_everything_but_identity(self) -> None:
        """Load defaults strips overrides + PROP_ keys + SKIP; identity survives."""
        from lwe_ui.storage import wp
        e = self.editor
        wp.update_set(self.wid, {"BG": self.wid, "TYPE": "scene"})
        e.open(self.wid)
        name = e.sceneProperties()[0]["name"]
        e.setScalingValue("stretch")
        e.setProp(name, "0.5")
        e.setObjectGroupEnabled("particle", False)
        self.assertNotEqual(self._live("SKIP"), "<MISSING>")

        self.assertTrue(e.loadDefaults())
        self.assertEqual(self._live("SCALING"), "<MISSING>")
        self.assertEqual(self._live(f"PROP_{name}"), "<MISSING>")
        self.assertEqual(self._live("SKIP"), "<MISSING>")
        self.assertEqual(self._live("BG"), self.wid, "BG is identity, not customization")
        self.assertFalse(e.hasMarks())

    def test_assent_clears_marks_but_keeps_values(self) -> None:
        """Closing, and switching wallpaper, clear marks while values persist."""
        e = self.editor
        e.open(self.wid)
        e.setScalingValue("fit")
        self.assertTrue(e.hasMarks())

        e.closeEditor()
        self.assertFalse(e.hasMarks(), "close is assent: marks clear")
        self.assertEqual(self._live("SCALING"), "fit", "and nothing is discarded")

        e.setScalingValue("fill")
        self.assertTrue(e.hasMarks())
        e.open("")
        self.assertFalse(e.isMarked("SCALING"), "the departed wid's marks cleared")
        e.open(self.wid)
        self.assertEqual(self._live("SCALING"), "fill")

    def test_global_capsule_rows_are_never_marked(self) -> None:
        """T5: a global commit wears no mark and stays out of the revert set."""
        e = self.editor
        e.open(self.wid)
        e.setGlobalSpeed(2.0)
        self.assertFalse(e.isMarked("ENGINE_TIMESCALE"))
        self.assertFalse(e.hasMarks(), "a global edit must not arm Revert changes")

    def test_setprop_commits_live_and_reflects(self) -> None:
        e = self.editor
        e.open(self.wid)
        props = e.sceneProperties()
        name = props[0]["name"]
        e.setProp(name, "0.2 0.4 0.6")
        self.assertEqual(self._live(f"PROP_{name}"), "0.2 0.4 0.6")
        reflected = {p["name"]: p["value"] for p in e.sceneProperties()}
        self.assertEqual(reflected[name], "0.2 0.4 0.6")
        e.open(self.wid)
        reflected2 = {p["name"]: p["value"] for p in e.sceneProperties()}
        self.assertEqual(reflected2[name], "0.2 0.4 0.6")

    def test_object_group_toggle_skips_and_unskips_particles(self) -> None:
        e = self.editor
        e.open(self.wid)
        particle_ids = {o["objid"] for o in e._objects if o["type"] == "particle"}
        self.assertTrue(particle_ids, "specimen must have particle objids")

        e.setObjectGroupEnabled("particle", False)
        skip = set(self._live("SKIP").split())
        self.assertTrue(particle_ids.issubset(skip), f"SKIP={skip} missing {particle_ids - skip}")
        groups = {g["type"]: g for g in e.objectGroups()}
        self.assertIs(groups["particle"]["enabled"], False)

        e.setObjectGroupEnabled("particle", True)
        raw2 = self._live("SKIP")
        skip2 = set(raw2.split()) if raw2 not in ("<MISSING>", "<NO-CONF>") else set()
        self.assertTrue(particle_ids.isdisjoint(skip2), f"SKIP={skip2} still has particles")
        groups2 = {g["type"]: g for g in e.objectGroups()}
        self.assertIs(groups2["particle"]["enabled"], True)

    def test_bulk_disable_particles(self) -> None:
        e = self.editor
        e.open(self.wid)
        particle_ids = {o["objid"] for o in e._objects if o["type"] == "particle"}
        e.bulkDisableParticles()
        skip = set(self._live("SKIP").split())
        self.assertTrue(particle_ids.issubset(skip))

    def test_filtered_bulk_toggle_is_derived_and_scoped(self) -> None:
        """The bulk toggle flips exactly the filtered set, and its tri-state is derived."""
        e = self.editor
        e.open(self.wid)
        particle_ids = {o["objid"] for o in e._objects if o["type"] == "particle"}
        other_ids = {o["objid"] for o in e._objects if o["type"] != "particle" and o["objid"]}
        self.assertEqual(e.filteredSkipState("all")["state"], "on")

        e.setFilteredEnabled("particle", False)
        skip = set(self._live("SKIP").split())
        self.assertTrue(particle_ids.issubset(skip))
        self.assertTrue(other_ids.isdisjoint(skip), "a filtered flip must not reach other types")
        # the state is READ from SKIP every time - no snapshot memory
        self.assertEqual(e.filteredSkipState("particle")["state"], "off")
        if other_ids:
            self.assertEqual(e.filteredSkipState("all")["state"], "partial")

    def test_toggle_favorite_persists_to_meta(self) -> None:
        e = self.editor
        e.open(self.wid)
        self.assertIs(e.favorite, False)
        e.toggleFavorite()
        from lwe_ui.storage import meta
        self.assertIs(meta.get(self.wid).get("favorite"), True)
        self.assertIs(e.favorite, True)

    def test_fullscreen_pause_has_no_editor_surface(self) -> None:
        """Fullscreen pause is a GLOBAL concept: the editor edits it nowhere.

        Replaces the old inherit/omit cycle. The conf key and its resolution semantics are
        untouched - what is gone is any way to reach it from this surface, per-wallpaper row
        or capsule row alike, because 29a draws neither.
        """
        e = self.editor
        e.open(self.wid)
        for gone in ("setFullscreenPause", "fullscreenPauseValue", "fullscreenPause"):
            self.assertFalse(hasattr(e, gone), f"editor.{gone} must not exist any more")
        self.assertNotIn("fullscreenPause", e.overrideState())
        self.assertFalse(e.setBoolOverride("FULLSCREEN_PAUSE", "true"))
        self.assertEqual(self._live("FULLSCREEN_PAUSE"), "<NO-CONF>")

    def test_cc_mode_two_states(self) -> None:
        """None materializes the authored look rather than clearing it; a drag demotes."""
        e = self.editor
        e.open(self.wid)
        self.assertEqual(e.ccMode(), "none")

        e.autoFromPreset()
        self.assertEqual(e.ccMode(), "none")
        cc = self._live("CC")
        self.assertEqual(len(cc.split()), 4, f"CC must stay materialized, got {cc!r}")
        self.assertEqual(self._live("CC_MODE"), "none")

        # any manual channel edit demotes the remembered mode to custom (L-5 semantics 4)
        e.setCcChannel(0, 1.4)
        self.assertEqual(e.ccMode(), "custom")
        self.assertEqual(self._live("CC_MODE"), "custom")
        self.assertAlmostEqual(e.ccChannels()[0], 1.4, places=3)

        e.setCcMode("none")
        self.assertEqual(e.ccMode(), "none")
        self.assertEqual(len(self._live("CC").split()), 4, "None keeps the numbers")

    def test_cc_mode_round_trips(self) -> None:
        """Every selectable mode reads back as itself, and the legacy spelling reads None.

        Regression: None wrote the authored numbers with no CC_MODE, which the legacy
        classifier then read back as Custom, so the menu snapped off None immediately.
        """
        e = self.editor
        e.open(self.wid)
        for mode in ("custom", "none", "custom", "none"):
            self.assertTrue(e.setCcMode(mode), f"setCcMode({mode!r}) must commit")
            self.assertEqual(e.ccMode(), mode, f"{mode} must read back as itself")

        from lwe_ui.storage import paths
        p = paths.wp_file(self.wid)
        p.write_text(p.read_text(encoding="utf-8").replace(
            "CC_MODE=none", "CC_MODE=preset"), encoding="utf-8")
        e.open(self.wid)
        self.assertEqual(e.ccMode(), "none", "legacy preset IS the authored look")

    def test_authored_groups_against_real_scene(self) -> None:
        """authoredGroups() groups the real scene by the author's names, type-homogeneous."""
        e = self.editor
        e.open(self.wid)
        self.assertEqual(e.groupingMode(), "authored", "real scenes carry authored names")
        groups = e.authoredGroups()
        self.assertTrue(groups, "authoredGroups() must be non-empty for a real scene")
        names = set()
        for g in groups:
            for key in ("name", "type", "count", "ids", "enabled"):
                self.assertIn(key, g)
            self.assertEqual(g["count"], len(g["ids"]))
            self.assertTrue(g["enabled"], "all groups enabled before any SKIP")
            self.assertNotIn(g["type"], ("mixed",),
                             f"real name-groups are type-homogeneous, got {g['name']!r}=mixed")
            names.add(g["name"])
        self.assertEqual(len(names), len(groups), "group names are unique keys")

    def test_authored_group_toggle_cascades_to_members(self) -> None:
        """Toggling a named group off skips exactly its member ids, on removes them."""
        e = self.editor
        e.open(self.wid)
        groups = e.authoredGroups()
        target = next((g for g in groups if g["count"] > 1), groups[0])
        member_ids = {i for i in target["ids"] if i}
        e.setAuthoredGroupEnabled(target["name"], False)
        skip = set(self._live("SKIP").split())
        self.assertTrue(member_ids.issubset(skip), f"SKIP={skip} missing {member_ids - skip}")
        e.setAuthoredGroupEnabled(target["name"], True)
        skip2 = self._live("SKIP")
        skip2 = set(skip2.split()) if skip2 not in ("<MISSING>", "<NO-CONF>") else set()
        self.assertTrue(member_ids.isdisjoint(skip2), f"SKIP={skip2} still has {member_ids}")

    def test_override_rows_are_key_presence(self) -> None:
        """L4: each override row is its own key, set-ness is presence, and zero-values work.

        Replaces the D6 collapsed-quartet cycle: the quartet, the Interaction composite and
        clearOverride('interaction') are gone with the reveal grammar, so each
        of these facts now owns a row and commits on its own.
        """
        e = self.editor
        e.open(self.wid)
        ov = e.overrideState()
        for key in ("automute", "mouse", "audioReactive", "scaling", "volume"):
            self.assertFalse(ov[key]["set"], f"a fresh scene inherits {key}")

        # AUTOMUTE=false is the schema DEFAULT-adjacent value that a value test could not tell
        # from inheriting; under key presence it is a real override
        e.setAutomute(False)
        self.assertEqual(self._live("AUTOMUTE"), "false")
        self.assertTrue(e.overrideState()["automute"]["set"])
        self.assertFalse(e.overrideState()["mouse"]["set"], "unrelated rows stay inheriting")

        e.setVolumeValue(0)
        self.assertEqual(self._live("VOLUME"), "0")
        self.assertTrue(e.overrideState()["volume"]["set"])

        e.setMouse(True)
        self.assertEqual(self._live("MOUSE"), "true")

        e.clearOverride("automute")
        self.assertEqual(self._live("AUTOMUTE"), "<MISSING>")
        self.assertFalse(e.overrideState()["automute"]["set"])
        self.assertTrue(e.overrideState()["mouse"]["set"], "clearing one row leaves the rest")


if __name__ == "__main__":
    unittest.main(verbosity=2)
