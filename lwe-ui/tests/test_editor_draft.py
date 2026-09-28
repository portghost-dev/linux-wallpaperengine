"""The editor's live-commit contract - what replaced the draft + Save model.

Was: the three-part draft/Save split (edit -> draft, save -> promote, close -> discard). That
model is retired whole by the editor build: every control commits on its own
gesture straight into wp/<id>.conf, there is no draft buffer, no Save verb, no dirty state and
no exit-discard. This file carries the acceptance tests that pin the replacement:

  T8  the popup and the editor write ONE store, and each sees the other's value immediately
  T9  wp/<id>.conf is the only file an editor commit writes - no draft appears anywhere
  T10 the L2 SCOPE GATE: with the editor open on a wallpaper that is not playing, a commit
      writes the conf and sends the engine NOTHING
  T14 the loaded() split: a metadata write must not fire the identity signal the object panel
      resets its filter/search/tree/collapse state on
  T21 a dead engine socket on a live-class key raises the failure grammar, never silence
  T22 a failed snapshot seat disables Revert changes and deletes no key (F16, the
      revert-into-delete hole)
  T24 wp.exists() is never a library-membership test (compile-time check)

Fully sandboxed + stubbed - NOTHING signals a live process or spawns the engine:
  * HOME + every XDG_* point at a fresh tempfile tree (config/state/data all isolated);
  * a synthetic scene wallpaper is written into the sandbox WALLPAPERS_DIR (no real library dep);
  * api_client is driven through a recording stub, so every engine verb is counted, never sent.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 tests/test_editor_draft.py
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import importlib
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_WID = "700"
_OTHER = "701"


class _ApiRecorder:
    """Stand-in for api_client: records every verb instead of reaching a socket. With on_screen
    set, status reports that wallpaper on this build's engine; otherwise the engine is away."""

    def __init__(self, available: bool = True) -> None:
        self._available = available
        self.calls: list[tuple[str, object]] = []
        self.on_screen: str | None = None
        self.refused: set[str] = set()

    def available(self) -> bool:
        return self._available

    def status(self):
        if self.on_screen is None:
            return None
        from lwe_ui import version
        return {"api": 1, "version": version.panel_stamp(), "pid": 1, "uptime_s": 100,
                "current": {"id": self.on_screen, "ui_id": self.on_screen}}

    def last_class(self):
        return "away"

    def _verb(self, name):
        def fn(arg=None, **kw):
            self.calls.append((name, kw if kw else arg))
            if name in self.refused:
                return {"ok": False, "error": "no"}
            return {"ok": True, "status": "done", "result": {}}
        return fn

    def __getattr__(self, name):
        if name.startswith("set_"):
            return self._verb(name)
        raise AttributeError(name)


class TestEditorLiveCommit(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtCore import QCoreApplication

        cls._app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="lwe-editor-live-")
        self._orig_env = {k: os.environ.get(k) for k in
                          ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
        os.environ["HOME"] = self.tmp
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp, ".config")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.tmp, ".local/state")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp, ".local/share")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

        # Reload path-aware modules under the fresh env, then the bridges last.
        import lwe_ui.constants  # noqa: F401
        for name in ("atomic", "tier_a", "paths", "settings", "wp", "meta", "tags"):
            importlib.reload(importlib.import_module(f"lwe_ui.storage.{name}"))
        for name in ("project", "pkg", "objects", "properties"):
            importlib.reload(importlib.import_module(f"lwe_ui.discovery.{name}"))
        self.session_mod = importlib.reload(importlib.import_module("lwe_ui.wp_session"))
        self.editor_mod = importlib.reload(importlib.import_module("lwe_ui.editor"))
        self.popup_mod = importlib.reload(importlib.import_module("lwe_ui.deck_popup"))

        from lwe_ui.storage import paths, settings

        paths.ensure_dirs()
        self.wallpapers = Path(paths.default_wallpapers_dir())
        for wid in (_WID, _OTHER):
            self._make_scene(wid)
        settings.ensure_exists()
        s = settings.load()
        s["WALLPAPERS_DIR"] = str(self.wallpapers)
        settings.save(s)

        self.paths = paths
        self.editor = self.editor_mod.EditorBridge()

    def _make_scene(self, wid: str) -> None:
        (self.wallpapers / wid).mkdir(parents=True, exist_ok=True)
        project = {
            "title": f"Scene {wid}",
            "type": "scene",
            "file": "scene.json",
            "general": {"properties": {
                "glow": {"type": "slider", "text": "Glow", "value": 0.5,
                         "min": 0, "max": 1, "step": 0.1},
            }},
        }
        (self.wallpapers / wid / "project.json").write_text(json.dumps(project), encoding="utf-8")
        scene = {"objects": [
            {"id": 1, "name": "Sparks", "particle": {}, "origin": "0 0 0"},
            {"id": 2, "name": "Plate", "image": "p.png", "origin": "0 0 0"},
        ]}
        (self.wallpapers / wid / "scene.json").write_text(json.dumps(scene), encoding="utf-8")

    def tearDown(self) -> None:
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _live(self, key: str, wid: str = _WID) -> str:
        from lwe_ui.storage import tier_a
        p = self.paths.wp_file(wid)
        if not p.exists():
            return "<NO-CONF>"
        return tier_a.parse(p.read_text(encoding="utf-8")).get(key, "<MISSING>")

    def test_T9_commits_write_only_the_conf(self) -> None:
        """The conf is the ONLY file an editor commit touches; no draft appears anywhere."""
        e = self.editor
        e.open(_WID)
        e.setScalingValue("fill")
        e.setProp("glow", 0.9)
        e.setObjectSkipped("1", True)

        self.assertEqual(self._live("SCALING"), "fill")
        self.assertEqual(self._live("PROP_glow"), "0.9")
        self.assertEqual(self._live("SKIP"), "1")
        drafts = list(self.paths.draft_dir().glob("*.conf"))
        self.assertEqual(drafts, [], f"an editor commit wrote a draft buffer: {drafts}")

    def test_T8_popup_and_editor_share_the_store(self) -> None:
        """A popup edit is visible in the editor immediately, and the reverse."""
        popup = self.popup_mod.DeckPopupBridge(None)
        popup.syncCurrent(_WID)
        popup.setScaling("stretch")

        e = self.editor
        e.open(_WID)
        self.assertEqual(e.scalingValue(), "stretch",
                         "the editor must read the popup's committed value with no reopen")

        e.setProp("glow", 0.25)
        self.assertEqual({p["name"]: p["value"] for p in popup.sceneProperties()}["glow"], "0.25")

    def _recorder(self, on_screen: str | None) -> _ApiRecorder:
        """The recorder as the change runner's engine too, reporting `on_screen` as shown (None:
        the engine is away); its engine is the served one."""
        from lwe_ui.engine import marker, push
        marker.record_served(1, 9900.0)
        rec = _ApiRecorder()
        rec.on_screen = on_screen
        self.editor_mod.api_client = rec
        for patcher in (mock.patch.object(push, "api_client", rec),
                        mock.patch.object(push, "_monotonic", lambda: 10_000.0)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return rec

    def test_T10_idle_wallpaper_sends_no_verb(self) -> None:
        """Editing a wallpaper that is not on screen writes the conf and sends no live verb."""
        rec = self._recorder(on_screen=_OTHER)
        e = self.editor
        e.open(_WID)

        e.setVolumeValue(40)
        self.assertEqual(self._live("VOLUME"), "40", "the conf is still written")
        self.assertEqual(rec.calls, [], "no engine verb may be sent for an idle wallpaper")

        rec.on_screen = _WID
        e.setVolumeValue(55)
        self.assertTrue(any(name == "set_volume" for name, _ in rec.calls),
                        f"a live-class commit on the playing wid must push: {rec.calls}")

    def test_T10b_relaunch_class_never_shows_an_idle_wallpaper(self) -> None:
        """A relaunch-class key on an idle wallpaper is delivered with its burst, which never
        shows it."""
        rec = self._recorder(on_screen=_OTHER)
        seen: list[list] = []
        e = self.editor
        e.commitFailed.connect(lambda keys: seen.append(list(keys)))
        e.open(_WID)
        e.setScalingValue("fit")
        self.assertTrue(e._reshow.isActive(), "the build key waits for its burst")
        e._reshow.stop()
        e._fire_reshow()
        self.assertEqual((rec.calls, seen), ([], []), "no show may be sent for an idle wallpaper")
        self.assertEqual(e._pending, set())

    def test_T21_a_refused_push_raises_the_failure_grammar(self) -> None:
        """A refused push on a live-class key is a banner event, never silence. A dead engine
        socket leaves the commit saved and pending, which is not a failure."""
        from lwe_ui.engine import marker
        rec = self._recorder(on_screen=None)
        e = self.editor
        e.open(_WID)

        seen: list[list] = []
        e.commitFailed.connect(lambda keys: seen.append(list(keys)))
        e.setVolumeValue(30)
        self.assertEqual(self._live("VOLUME"), "30", "the conf commit still stands")
        self.assertEqual(seen, [], "a pending commit is saved, not a failure")

        marker.clear(marker.read()["generation"])
        rec.on_screen = _WID
        rec.refused.add("set_volume")
        e.setVolumeValue(31)
        self.assertEqual(self._live("VOLUME"), "31", "a refused push leaves the conf saved")
        self.assertTrue(seen, "a refused push must raise commitFailed, not pass silently")
        self.assertIn("VOLUME", seen[0])

    def test_T22_failed_snapshot_disables_revert_and_deletes_nothing(self) -> None:
        """A snapshot that could not be seated must disable Revert, not delete the overrides."""
        from lwe_ui.storage import wp

        e = self.editor
        e.open(_WID)
        e.setScalingValue("fill")
        e.setProp("glow", 0.75)

        seat = self.session_mod.SESSION._seats[_WID]
        seat.valid = False

        self.assertFalse(e.snapshotValid())
        self.assertFalse(e.canRevert(), "Revert must be disabled while the snapshot is invalid")
        self.assertFalse(e.revertChanges(), "and calling it anyway must be a no-op")

        self.assertEqual(self._live("SCALING"), "fill")
        self.assertEqual(self._live("PROP_glow"), "0.75")
        self.assertEqual(wp.load_set(_WID).get("SCALING"), "fill")

    def test_T22b_revert_deletes_only_keys_absent_at_seat_time(self) -> None:
        """The revert set distinguishes "was absent" from "no record" (F16 part 3)."""
        from lwe_ui.storage import wp

        wp.update_set(_WID, {"SCALING": "fit"})
        e = self.editor
        e.open(_WID)
        e.setScalingValue("fill")
        e.setVolumeValue(70)

        self.assertTrue(e.revertChanges())
        self.assertEqual(self._live("SCALING"), "fit", "a seated value is restored, not deleted")
        self.assertEqual(self._live("VOLUME"), "<MISSING>", "an added override is deleted again")

    def test_T14_metadata_does_not_fire_the_navigation_reset(self) -> None:
        """Typing a tag must not fire wallpaperChanged - the object panel resets on that."""
        e = self.editor
        e.open(_WID)
        nav: list[int] = []
        meta_hits: list[int] = []
        e.wallpaperChanged.connect(lambda: nav.append(1))
        e.metadataChanged.connect(lambda: meta_hits.append(1))

        e.addTag("moody")
        e.setTitle("Renamed")
        e.toggleFavorite()
        self.assertEqual(nav, [], "a metadata write must not read as a wallpaper swap (H27)")
        self.assertEqual(len(meta_hits), 3)

        e.setScalingValue("fill")
        self.assertEqual(nav, [])

        e.open(_OTHER)
        self.assertEqual(len(nav), 1, "opening another wallpaper IS the navigation reset")

    def test_T14b_domain_signals_stay_narrow(self) -> None:
        """A property commit must not fire objectsEdited, and a SKIP commit not propsEdited."""
        e = self.editor
        e.open(_WID)
        props: list[int] = []
        objs: list[int] = []
        e.propsEdited.connect(lambda: props.append(1))
        e.objectsEdited.connect(lambda: objs.append(1))

        e.setProp("glow", 0.3)
        self.assertEqual((len(props), len(objs)), (1, 0), "a PROP commit is props-only (T15)")

        e.setObjectSkipped("1", True)
        self.assertEqual((len(props), len(objs)), (1, 1), "a SKIP commit is objects-only (T16)")

        e.setScalingValue("fill")
        self.assertEqual((len(props), len(objs)), (1, 1), "an unrelated commit fires neither")

    def test_T15_property_commit_touches_one_row_not_the_set(self) -> None:
        """Committing property A must move A's row, not rebuild the whole model.

        A model RESET is what destroys every delegate - including the one the user is typing
        in - so the assertion is that a commit produces a per-row dataChanged and NO reset.
        """
        e = self.editor
        e.open(_WID)
        model = e.scenePropertyModel
        self.assertEqual(model.rowCount(), 1, "the synthetic scene exposes one property")

        resets: list[int] = []
        changed: list[tuple] = []
        model.modelAboutToBeReset.connect(lambda: resets.append(1))
        model.dataChanged.connect(
            lambda tl, br, roles: changed.append((tl.row(), br.row(), list(roles))))

        e.setProp("glow", 0.9)
        self.assertEqual(resets, [], "a property commit must NOT reset the model (T15)")
        self.assertEqual(len(changed), 1, "exactly one row moved")
        self.assertEqual(changed[0][0], changed[0][1], "and it was a single-row span")
        self.assertEqual(changed[0][2], [self.editor_mod.ScenePropertyModel.ValueRole],
                         "only the value role changed - the row's identity is untouched")
        self.assertEqual(model.data(model.index(0, 0),
                                    self.editor_mod.ScenePropertyModel.ValueRole), "0.9")

        self.assertTrue(e.revertChanges())
        self.assertEqual(len(resets), 1, "a revert legitimately rebuilds the set")

        e.open(_OTHER)
        self.assertEqual(len(resets), 2, "a wallpaper swap rebuilds the set")

    def test_color_hex_roundtrip_and_refusal(self) -> None:
        """Hex parses to WE's native floats, formats back identically, and refuses garbage."""
        from lwe_ui.editor import hex_to_rgb_floats, rgb_floats_to_hex

        # every 8-bit channel round-trips exactly, in both directions
        for hx in ("#000000", "#ffffff", "#50a5c6", "#0a0b0c", "#123456"):
            floats = hex_to_rgb_floats(hx)
            self.assertIsNotNone(floats, hx)
            self.assertEqual(rgb_floats_to_hex(floats), hx)
        # storage stays WE's own shape: space-separated floats in 0..1, not a hex string
        self.assertEqual(hex_to_rgb_floats("#50A5C6"), "0.31373 0.64706 0.77647")
        self.assertEqual(rgb_floats_to_hex("0.31373 0.64706 0.77647"), "#50a5c6")
        # case-insensitive, leading # optional on entry, normalized lowercase on display
        self.assertEqual(hex_to_rgb_floats("50A5C6"), hex_to_rgb_floats("#50a5c6"))
        # a short/long/non-hex string is not a color
        for bad in ("", "#12345", "#1234567", "zzzzzz", "0.5 0.5 0.5", "#12 34 56"):
            self.assertIsNone(hex_to_rgb_floats(bad), f"{bad!r} must not parse")

    def test_color_commit_refusal_leaves_the_conf_alone(self) -> None:
        """An invalid hex raises the failure grammar and writes nothing."""
        e = self.editor
        e.open(_WID)
        e.setProp("glow", "0.5 0.5 0.5")
        before = self._live("PROP_glow")

        seen: list[list] = []
        e.commitFailed.connect(lambda keys: seen.append(list(keys)))
        self.assertFalse(e.setPropColor("glow", "not-a-colour"))
        self.assertEqual(seen, [["PROP_glow"]], "a refusal must raise the banner, not pass")
        self.assertEqual(self._live("PROP_glow"), before, "the conf must be untouched")

        self.assertTrue(e.setPropColor("glow", "#50A5C6"))
        self.assertEqual(self._live("PROP_glow"), "0.31373 0.64706 0.77647")
        self.assertEqual(e.colorHex(self._live("PROP_glow")), "#50a5c6")
        self.assertTrue(e.isMarked("PROP_glow"), "a color edit is a normal PROP_ write")

    def test_T24_conf_existence_is_not_library_membership(self) -> None:
        """Writing wp/<id>.conf early must NOT put an un-approved item in the grid."""
        from lwe_ui.storage import wp
        from lwe_ui import models

        importlib.reload(models)
        before = set(models.library_ids())
        ghost = "999999999"
        wp.update_set(ghost, {"BG": ghost, "TYPE": "scene"})
        self.assertTrue(wp.exists(ghost))
        after = set(models.library_ids())
        self.assertEqual(after - before, set(),
                         "a bare conf must never add a library member")

        self.assertIn("NEVER a library-membership test", wp.exists.__doc__ or "")

    def _ordered_rig(self, ok: bool = True, available: bool = True):
        """A shared timeline the store write and the engine push both write into, so the ORDER
        itself is observable. The backend is the real one, saving through the change runner, and
        its engine is the served one."""
        from lwe_ui import models
        from lwe_ui.engine import marker, push
        from lwe_ui.storage import settings
        marker.record_served(1, 9900.0)
        timeline: list[str] = []

        class _Api(_ApiRecorder):
            def __init__(self) -> None:
                super().__init__(available=available)
                self.log = timeline
                self.on_screen = _WID

            def _verb(self, name):
                def fn(arg=None, **kw):
                    self.log.append(f"push:{name}")
                    self.calls.append((name, kw if kw else arg))
                    return {"ok": True, "status": "done", "result": {}} if ok else {"ok": False, "error": "no"}
                return fn

            def __getattr__(self, name):
                if name in ("playlist_set", "lanes_set", "schedule_set", "show"):
                    return lambda *a, **k: {"ok": True, "status": "done", "result": {}}
                return super().__getattr__(name)

        update = settings.update

        def logged(changes, *args, **kwargs):
            timeline.extend(f"persist:{key}" for key in changes)
            return update(changes, *args, **kwargs)
        api = _Api()
        self.editor_mod.api_client = api
        self.popup_mod.api_client = api
        for target, name, value in ((push, "api_client", api), (settings, "update", logged),
                                    (push, "_monotonic", lambda: 10_000.0)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return timeline, models.Backend()

    def test_H5_globals_save_before_their_push_like_the_popup(self) -> None:
        """The capsule's global setters ship the popup's order: save first, then the change
        runner's push resolved from the store."""
        timeline, backend = self._ordered_rig()
        e = self.editor_mod.EditorBridge(backend)
        e.open(_WID)
        e.syncCurrent(_WID)

        self.assertTrue(e.setGlobalSpeed(2.0))
        self.assertEqual(timeline, ["persist:ENGINE_TIMESCALE", "push:set_speed"])

        timeline.clear()
        self.assertTrue(e.setGlobalVolume(40))
        self.assertEqual(timeline, ["persist:ENGINE_VOLUME", "push:set_volume"])

        timeline.clear()
        self.assertTrue(e.setGlobalFps("90"))
        self.assertEqual(timeline, ["persist:ENGINE_FPS", "push:set_fps"])

        # a blank is not a state: it is refused, and nothing is pushed or persisted
        timeline.clear()
        self.assertFalse(e.setGlobalFps(""))
        self.assertEqual(timeline, [])

    def test_H5_a_refused_verb_stays_saved_and_reports(self) -> None:
        """The engine's no does not undo the save: settings.conf holds the value and each door
        reports the refusal."""
        from lwe_ui.storage import settings

        timeline, backend = self._ordered_rig(ok=False)
        e = self.editor_mod.EditorBridge(backend)
        e.open(_WID)
        e.syncCurrent(_WID)

        seen: list[list] = []
        e.commitFailed.connect(lambda keys: seen.append(list(keys)))

        self.assertFalse(e.setGlobalSpeed(2.0))
        self.assertFalse(e.setGlobalVolume(40))
        self.assertFalse(e.setGlobalFps("90"))

        stored = settings.load()
        self.assertEqual((stored.get("ENGINE_TIMESCALE"), stored.get("ENGINE_VOLUME"), str(stored.get("ENGINE_FPS"))),
                         (2.0, 40, "90"), f"a refused verb leaves the value saved: {timeline}")
        self.assertEqual(seen, [["ENGINE_TIMESCALE"], ["ENGINE_VOLUME"], ["ENGINE_FPS"]],
                         "each refusal is one failure-grammar event")

    def test_H5_editor_and_popup_write_the_same_sequence(self) -> None:
        """The two surfaces carrying the same three facts must not behave differently."""
        timeline, backend = self._ordered_rig()

        e = self.editor_mod.EditorBridge(backend)
        e.open(_WID)
        e.syncCurrent(_WID)
        e.setGlobalSpeed(2.0)
        e.setGlobalVolume(40)
        e.setGlobalFps("90")
        editor_order = list(timeline)

        timeline.clear()
        popup = self.popup_mod.DeckPopupBridge(backend)
        popup.syncCurrent(_WID)
        popup.setGlobalSpeed(2.0)
        popup.setGlobalVolume(40)
        popup.setGlobalFps("90")

        self.assertEqual(editor_order, list(timeline),
                         "the editor capsule must write globals exactly as the popup does")

    def test_L1_no_draft_world_survives_in_source(self) -> None:
        """No shipped module may import or reach a draft buffer any more."""
        src = Path(_SRC) / "lwe_ui"
        self.assertFalse((src / "storage" / "draft.py").exists(),
                         "storage/draft.py must stay deleted")
        offenders = []
        for py in src.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            for needle in ("storage.draft", "draft.exists(", "draft.save(", "draft.load(",
                           "draft.delete(", "draft.seed_"):
                if needle in text:
                    offenders.append(f"{py.relative_to(src)}: {needle}")
        self.assertEqual(offenders, [], f"live draft references survive: {offenders}")


class TestEditorSurfaceContract(unittest.TestCase):
    """Acceptance tests that read the SURFACE rather than drive the bridge.

    These are statements about what the QML does and does not contain.
    They are asserted against the source because the alternative - a pixel diff - could not
    tell "the verb is gone" from "the verb is off-screen", which is exactly the failure these
    guard against.
    """

    QML = Path(_SRC) / "lwe_ui" / "qml"

    def _editor_text(self) -> str:
        return (self.QML / "EditorView.qml").read_text(encoding="utf-8")

    def _panel_text(self) -> str:
        return (self.QML / "ObjectsPanel.qml").read_text(encoding="utf-8")

    @staticmethod
    def _user_strings(text: str) -> list[str]:
        """Every string literal on a line that assigns user-visible copy."""
        import re
        out: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            # `label: "x"` on its own line, and the single-line `PRule { label: "x" }` form
            if not re.match(r"^(\w+\s*\{\s*)?"
                            r"(text|label|caption|heading|placeholderText):", stripped):
                continue
            out.extend(re.findall(r'"([^"]*)"', stripped))
        return out

    def test_T1_workspace_strings_are_verbatim(self) -> None:
        text = self._editor_text()
        for literal in ('"Scene Properties"', '"Object Exclusion"', '"Tuning"',
                        '"Knobs exposed by the original scene author."',
                        '"Disables individual scene objects."',
                        '"Per-wallpaper and advanced knobs offered by the engine."'):
            self.assertIn(literal, text, f"missing verbatim workspace string {literal}")
        self.assertIn('"Scene Properties · " + propCol.props.length', text)
        self.assertIn('panel.heading + " · " + panel.objs.length', self._panel_text())

    def test_T13_each_column_owns_a_scrollbar(self) -> None:
        text = self._editor_text()
        self.assertEqual(text.count("ScrollBar.vertical: PScrollBar {}"), 2,
                         "workspaces 1 and 3 must each attach their own overlay bar")
        self.assertIn("ScrollBar.vertical: ScrollBar {", self._panel_text(),
                      "the object list must carry its own overlay bar")
        for src in (text, self._panel_text()):
            self.assertIn("implicitWidth: 4", src)
            self.assertIn("Qt.rgba(1, 1, 1, 0.25)", src)
            self.assertIn("background: Item {}", src)

    def test_T15b_column_one_binds_the_stable_model(self) -> None:
        """The Repeater must bind the MODEL OBJECT, never a freshly-built array."""
        text = self._editor_text()
        self.assertIn("model: view.isVideo ? null : editor.scenePropertyModel", text)
        self.assertNotIn("model: propCol.props", text,
                         "binding the array back would restore the rebuild-on-commit defect")

    def test_T18_speed_face_matches_the_popup(self) -> None:
        import re
        editor = self._editor_text()
        popup = (self.QML / "DeckSettingsPopup.qml").read_text(encoding="utf-8")

        def knots(src: str) -> dict[str, str]:
            out = {}
            for name in ("speedKnotPos", "speedKnotValue", "speedDetent", "speedDetentBand"):
                m = re.search(rf"property\s+\w+\s+{name}:\s*(.+)", src)
                assert m, f"{name} missing"
                out[name] = m.group(1).strip()
            return out

        self.assertEqual(knots(editor), knots(popup),
                         "the editor's Speed zones must be byte-identical to the popup's")
        self.assertIn("speedDetentPos: speedKnotPos[1]", editor)
        self.assertIn('r.toFixed(1) : String(r)) + "x"', editor)

    def test_T20_header_verbs_and_dead_verbs(self) -> None:
        text = self._editor_text()
        panel = self._panel_text()
        self.assertEqual(text.count("PVerb {"), 2,
                         "the header must carry exactly two verbs and no others")
        self.assertEqual(text.count('label: "Revert changes"'), 1)
        self.assertEqual(text.count('label: "Load defaults"'), 1)

        for dead in ("Save", "Remove", "Test", "Stop test", "Bench preview", "Stop bench",
                     "Kill particles"):
            self.assertNotIn(f'label: "{dead}"', text, f"{dead!r} is still a verb here")
            self.assertNotIn(f'text: "{dead}"', panel, f"{dead!r} is still a control here")
        for gone in ("bench.", "TrashWizard", "editor.save(", "editor.dirty",
                     "editor.discardDraft"):
            self.assertNotIn(gone, text, f"{gone!r} must not appear on this surface")

    def test_T23_tag_removal_is_the_red_verb_only(self) -> None:
        text = self._editor_text()
        self.assertIn('text: "Remove tag?"', text)
        self.assertIn('text: "Remove"', text)
        self.assertIn("Popup.CloseOnEscape | Popup.CloseOnPressOutside", text,
                      "Esc and clicking away must cancel")
        # Enter must NOT remove: removeTag is reachable from exactly one place, the red verb
        self.assertEqual(text.count("editor.removeTag("), 1,
                         "only the red verb may remove a tag - never Enter, never the chip")

    def test_T25_no_banned_copy(self) -> None:
        strings = self._user_strings(self._editor_text()) + self._user_strings(self._panel_text())
        for s in strings:
            for dash in (" - ", " -- ", " \u2014 ", " \u2013 "):
                self.assertNotIn(dash, s, f"dash connector in user copy: {s!r}")
        joined = " ".join(strings)
        for banned in ("Freeze", "Timescale", "Kill particles"):
            self.assertNotIn(banned, joined, f"banned term {banned!r} is on this surface")
        self.assertNotIn("Theme.warning", self._editor_text(),
                         "the amber indicator (and its dot) must be gone")

    def test_L2_footer_line_is_gone(self) -> None:
        """The Tuning column ends on its last row: no closing hairline, no footer line."""
        text = self._editor_text()
        self.assertNotIn("Changes apply live.", text)
        tail = text[text.index('PRule { label: "Metadata" }'):]
        self.assertNotIn("Rectangle { width: parent.width; height: 1; color: Theme.border }", tail)

    def test_L3_quality_rule_sits_above_metadata(self) -> None:
        """The order is the rule: Quality and memory, its four rows, then Metadata. The two
        resolution limits are a slider and a chip on their conf key; the texture rows are a PDrop
        with a Global entry; the old clamp row's three words are gone."""
        text = self._editor_text()
        q = text.index('PRule { label: "Quality and memory" }')
        m = text.index('PRule { label: "Metadata" }')
        self.assertLess(q, m)
        block = text[q:m]
        for label, key in (("Render resolution limit", "SSFACTOR"), ("Effect resolution limit", "CLAMPCOMPOSITES")):
            self.assertIn(f'label: "{label}"', block)
            self.assertIn(f'ckey: "{key}"', block)
            self.assertIn(f'storeValue: view.clampShown("{key}")', block)
        for label, key in (("Texture compression", "TEXCOMP"), ("Texture detail", "TEXTURE_DETAIL")):
            self.assertIn(f'label: "{label}"', block)
            self.assertIn(f'ckey: "{key}"', block)
            self.assertIn(f'editor.globalDefaultFor("{key}")', block)
        for entry in ("Automatic", "Full"):
            self.assertIn(f'"{entry}"', block)
        for gone in ("Full clamping", "Full res effects", "All full res"):
            self.assertNotIn(gone, block)

    def test_29a_object_exclusion_header_layout(self) -> None:
        """Row 1 = title + caption LEFT, filter + bulk toggle RIGHT. Row 2 = pill + search."""
        panel = self._panel_text()
        self.assertIn("property Component headerControls: Component {", panel)
        self.assertIn("sourceComponent: panel.headerControls", panel,
                      "row 1 must load the pair at its right")
        self.assertIn("sourceComponent: active ? objPanel.headerControls : null",
                      self._editor_text(),
                      "the compact segment row must host the SAME pair (29c)")
        self.assertIn('model: ["Groups", "Tree"]', panel,
                      "the Groups|Tree pill must carry both segments unconditionally")
        self.assertNotIn('model: panel.treeAvailable ?', panel,
                         "the availability gate must stay dead")
        self.assertIn("anchors.left: viewSeg.visible ? viewSeg.right : parent.left", panel,
                      "the search must sit on the pill's row, taking the remaining width")
        self.assertIn("anchors.right: parent.right", panel)

    def test_29a_row_toggles_hug_the_right_edge(self) -> None:
        """Every VISIBLE pill sits at its row's right edge; only labels indent.

        ThemedSwitch centers its pill inside a 64px hit target, so a bare
        anchors.right leaves the pill (width-pillWidth)/2 = 19px short of the
        edge. The law is pill-flush: the container carries the NEGATIVE
        compensation margin."""
        panel = self._panel_text()
        for anchor in ("id: grpSwitch", "id: objSwitch"):
            idx = panel.index(anchor)
            block = panel[idx:idx + 700]
            self.assertIn("anchors.right: parent.right", block)
            self.assertIn("anchors.rightMargin: -((width - pillWidth) / 2)", block,
                          f"{anchor} must compensate ThemedSwitch's phantom margin "
                          "so the visible pill hugs the row's right edge (29a)")

    def test_L14_scrollbar_rides_its_own_column_edge(self) -> None:
        text = self._editor_text()
        # 3 (edge) + 4 (handle) + 9 (clear) = 16, and the viewport spans the whole column
        self.assertIn("readonly property real barGutter: 16", text)
        self.assertIn("rightPadding: 3", text)
        self.assertIn("rightPadding: 3", self._panel_text())
        # no scroll area may be pulled in by a margin - that is what pushed the bar inward
        for banned in ("anchors.rightMargin: view.compactMode ? 0 : 22",
                       "anchors.rightMargin: 22"):
            self.assertNotIn(banned, text,
                             "a viewport inset by the divider gutter puts the bar 25 px in")
        self.assertIn("width: propFlick.width - view.contentInset(true)", text)
        self.assertIn("width: parent.width - view.contentInset(false)", text)
        self.assertIn("width: objList.width - panel.barGutter", self._panel_text())

    def test_no_fullscreen_pause_row(self) -> None:
        strings = self._user_strings(self._editor_text())
        self.assertNotIn("Fullscreen pause", strings)
        self.assertNotIn("FULLSCREEN_PAUSE", self._editor_text(),
                         "the key must not be wired to any control on this surface")

    def test_color_row_is_an_editable_field(self) -> None:
        """The color row keeps its swatch but its hex readout takes an edit."""
        text = self._editor_text()
        self.assertIn("editor.setPropColor(propRow.modelData.name,", text)
        self.assertIn("editor.colorHex(String(propRow.modelData.value", text)
        self.assertIn("Keys.onEscapePressed", text)
        # the swatch follows the STORED value, never the in-progress text
        self.assertIn("color: parent.storedHex", text)

    def test_29a_audio_response_rows(self) -> None:
        """The section is the three DRAWN rows, wired to the three real engine dials."""
        from lwe_ui.editor import AUDIO_DIALS, _dial_to_quality, _quality_to_dial

        self.assertEqual([d["label"] for d in AUDIO_DIALS.values()],
                         ["Response threshold", "Glow intensity", "Glow radius"])
        self.assertEqual([d["field"] for d in AUDIO_DIALS.values()],
                         ["audio_gain", "classic_k", "classic_exp"])
        strings = self._user_strings(self._editor_text())
        self.assertNotIn("Audio processing", strings)
        self.assertNotIn("Band gain", strings)
        self.assertIn("Audio response", strings)

        for key, spec in AUDIO_DIALS.items():
            # every dial is the INVERSE of its label, so dragging right raises the label
            self.assertTrue(spec["invert"], f"{key} must map right = more of its label")
            q = _dial_to_quality(spec, spec["calibrated"])
            self.assertAlmostEqual(_quality_to_dial(spec, q), spec["calibrated"], places=2)
            self.assertAlmostEqual(_quality_to_dial(spec, 1.0), spec["lo"], places=4)
            self.assertAlmostEqual(_quality_to_dial(spec, 0.0), spec["hi"], places=4)

        # a decade-spanning dial must be log-mapped, or its real value parks against the rail
        self.assertTrue(AUDIO_DIALS["GLOW_INTENSITY"]["log"])
        self.assertGreater(_dial_to_quality(AUDIO_DIALS["GLOW_INTENSITY"], 0.7), 0.5,
                           "the calibrated 0.7 must land mid-track, near 29a's drawn 0.65")


if __name__ == "__main__":
    unittest.main(verbosity=2)
