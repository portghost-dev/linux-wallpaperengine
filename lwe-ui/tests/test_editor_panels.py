"""EditorBridge B3 additions: object list/counts/skip, V2 conditions, override state, tags.

Sandboxes HOME/XDG, seeds a scene wallpaper with a small scene.json (objects + parented
child) and a project.json with a conditional property, then drives EditorBridge offscreen.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lwe-editor-test-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from lwe_ui.storage import paths, settings  # noqa: E402


def seed_scene() -> str:
    wid = "500"
    wdir = Path(_TMP) / ".local/share/lwe/wallpapers" / wid
    wdir.mkdir(parents=True)
    project = {
        "title": "Cond Scene",
        "type": "scene",
        "file": "scene.json",
        "general": {
            "properties": {
                "rainlayer": {"type": "bool", "text": "Rain", "value": True},
                "rainamount": {"type": "slider", "text": "Rain amount", "value": 0.5,
                               "min": 0, "max": 1, "step": 0.1,
                               "condition": "rainlayer.value == true"},
            }
        },
    }
    (wdir / "project.json").write_text(json.dumps(project), encoding="utf-8")
    scene = {
        "objects": [
            {"id": 10, "name": "Petals", "particle": {}, "origin": "960 540 0"},
            {"id": 11, "name": "Koi", "image": "koi.png", "origin": "100 100 0"},
            {"id": 12, "name": "Koi child", "image": "k2.png", "parent": 11},
            {"id": 13, "name": "Ambience", "sound": ["a.ogg"]},
        ]
    }
    (wdir / "scene.json").write_text(json.dumps(scene), encoding="utf-8")
    s = settings.load()
    s["WALLPAPERS_DIR"] = str(Path(_TMP) / ".local/share/lwe/wallpapers")
    settings.save(s)
    return wid


def main() -> None:
    app = QGuiApplication([])  # noqa: F841
    paths.ensure_dirs()
    settings.ensure_exists()
    wid = seed_scene()

    from lwe_ui.editor import EditorBridge
    ed = EditorBridge()
    ed.open(wid)

    objs = ed.objectList()
    assert len(objs) == 4, objs
    by_id = {o["objid"]: o for o in objs}
    assert by_id["10"]["type"] == "particle"
    assert by_id["12"]["parent"] == "11"
    assert all(o["skipped"] is False for o in objs)

    counts = {c["type"]: c["count"] for c in ed.objectTypeCounts()}
    assert counts == {"particle": 1, "image": 2, "sound": 1}, counts

    assert ed.hasParenting() is True

    # per-object skip round-trips through wp.SKIP
    ed.setObjectSkipped("10", True)
    assert ed.objectList()[0]["skipped"] is True
    ed.setObjectSkipped("10", False)
    assert ed.objectList()[0]["skipped"] is False

    ed.bulkDisableParticles()
    assert next(o for o in ed.objectList() if o["objid"] == "10")["skipped"] is True

    assert not hasattr(ed, "rawObjectJson"), "A2: editor.rawObjectJson must be gone"

    ed.setObjectSkipped("10", False)
    assert ed.groupingMode() == "authored"
    ag = {g["name"]: g for g in ed.authoredGroups()}
    assert set(ag) == {"Petals", "Koi", "Koi child", "Ambience"}, ag
    assert ag["Petals"]["type"] == "particle" and ag["Petals"]["count"] == 1
    assert ag["Koi"]["type"] == "image"
    assert all(g["enabled"] for g in ag.values()), "no SKIP yet -> all groups enabled"
    ed.setAuthoredGroupEnabled("Petals", False)
    assert next(g for g in ed.authoredGroups() if g["name"] == "Petals")["enabled"] is False
    assert next(o for o in ed.objectList() if o["objid"] == "10")["skipped"] is True
    ed.setAuthoredGroupEnabled("Petals", True)
    assert next(o for o in ed.objectList() if o["objid"] == "10")["skipped"] is False

    props = {p["name"]: p for p in ed.sceneProperties()}
    assert props["rainamount"]["condition"] == {"key": "rainlayer", "target": "true"}
    assert props["rainlayer"]["condition"] == {}

    ov = ed.overrideState()
    for k in ("scaling", "volume", "speed", "cc", "audioReactive", "mouse", "automute"):
        assert ov[k]["set"] is False, f"fresh scene must inherit {k}, got set={ov[k]['set']}"
    assert "fps" not in ov, "FPS is global-only: no per-wallpaper row exists (L-17)"
    assert "interaction" not in ov, "the interaction composite is gone (R8)"
    assert "fullscreenPause" not in ov, \
        "fullscreen pause is global-only: the editor edits it nowhere"
    ed.setScaling("fill")
    assert ed.overrideState()["scaling"]["set"] is True
    assert ed.overrideState()["volume"]["set"] is False, "unrelated rows stay inheriting"
    # a value that EQUALS the schema default is still a real override under key presence
    ed.setScalingValue("default")
    assert ed.overrideState()["scaling"]["set"] is True, "SCALING=default is a set override"
    ed.setVolumeValue(0)
    assert ed.overrideState()["volume"]["set"] is True, "VOLUME=0 is a set override"
    # the `Global` menu entry deletes the key rather than writing a default over it
    ed.clearOverride("scaling")
    assert ed.overrideState()["scaling"]["set"] is False
    ed.clearOverride("volume")
    assert ed.overrideState()["volume"]["set"] is False

    ed.addTag("nature")
    ed.addTag("nature")
    assert ed.tags() == ["nature"]
    ed.removeTag("nature")
    assert ed.tags() == []

    ed.setTitle("My Rain")
    assert ed.title == "My Rain"

    print("OK: editor panels - object list/counts/tree/skip/bulk/authored-groups/V2-condition/"
          "override-state/tags/title all pass")


def seed_named_group_scene(wid: str = "600") -> str:
    """A scene where three objects share one authored name (a real multi-member group) + a lone
    image, so authoredGroups() must return a 3-member particle group and a singleton image."""
    wdir = Path(_TMP) / ".local/share/lwe/wallpapers" / wid
    wdir.mkdir(parents=True, exist_ok=True)
    project = {"title": "Swarm Scene", "type": "scene", "file": "scene.json",
               "general": {"properties": {}}}
    (wdir / "project.json").write_text(json.dumps(project), encoding="utf-8")
    scene = {"objects": [
        {"id": 1, "name": "leaf_swarm", "particle": {}, "origin": "0 0 0"},
        {"id": 2, "name": "leaf_swarm", "particle": {}, "origin": "0 0 0"},
        {"id": 3, "name": "leaf_swarm", "particle": {}, "origin": "0 0 0"},
        {"id": 4, "name": "tree_base", "image": "t.png", "origin": "0 0 0"},
    ]}
    (wdir / "scene.json").write_text(json.dumps(scene), encoding="utf-8")
    return wid


def test_authored_multimember_group() -> None:
    """D3/S4: a name shared by several objects is ONE group; toggling it cascades to all members."""
    from lwe_ui.editor import EditorBridge
    wid = seed_named_group_scene("600")
    ed = EditorBridge()
    ed.open(wid)
    groups = ed.authoredGroups()
    by_name = {g["name"]: g for g in groups}
    assert set(by_name) == {"leaf_swarm", "tree_base"}, by_name
    assert by_name["leaf_swarm"]["count"] == 3, "the three same-named particles collapse to one group"
    assert by_name["leaf_swarm"]["type"] == "particle"
    assert set(by_name["leaf_swarm"]["ids"]) == {"1", "2", "3"}
    assert by_name["tree_base"]["count"] == 1 and by_name["tree_base"]["type"] == "image"
    # group order follows first appearance
    assert [g["name"] for g in groups] == ["leaf_swarm", "tree_base"]
    ed.setAuthoredGroupEnabled("leaf_swarm", False)
    skipped = {o["objid"] for o in ed.objectList() if o["skipped"]}
    assert {"1", "2", "3"}.issubset(skipped), skipped
    assert next(g for g in ed.authoredGroups() if g["name"] == "leaf_swarm")["enabled"] is False
    ed.setObjectSkipped("1", False)
    assert next(g for g in ed.authoredGroups() if g["name"] == "leaf_swarm")["enabled"] is True
    print("OK: authored multi-member group cascade")


def test_bool_override_rows_are_independent() -> None:
    """Replaces the D6 interaction-composite test, which the build retires.

    Mouse, Auto-mute and Audio-reactive each own a row with the same three-entry
    Global/On/Off menu, and each commits on its own. Setting one must not set the
    others, and choosing `Global` on one must not clear the others - the composite that used
    to tie them together (and reset all three at once) is gone.
    """
    from lwe_ui.editor import EditorBridge
    wid = seed_named_group_scene("601")
    ed = EditorBridge()
    ed.open(wid)
    keys = ("mouse", "automute", "audioReactive")
    assert all(ed.overrideState()[k]["set"] is False for k in keys)

    ed.setBoolOverride("MOUSE", "true")
    ov = ed.overrideState()
    assert ov["mouse"]["set"] is True
    assert all(ov[k]["set"] is False for k in ("automute", "audioReactive")), \
        "setting one row must not set its neighbors"

    # AUTOMUTE=false is the value a comparison-based reader called "inherit"; it is an override
    ed.setBoolOverride("AUTOMUTE", "false")
    assert ed.overrideState()["automute"]["set"] is True
    ed.setBoolOverride("AUDIO_REACTIVE", "true")
    assert ed.overrideState()["audioReactive"]["set"] is True
    assert ed.setBoolOverride("FULLSCREEN_PAUSE", "true") is False

    # `Global` on one row deletes only that key
    ed.setBoolOverride("MOUSE", "")
    ov2 = ed.overrideState()
    assert ov2["mouse"]["set"] is False
    assert ov2["automute"]["set"] is True and ov2["audioReactive"]["set"] is True, \
        "clearing one row must leave the rest alone"

    # the inherited value each menu displays comes from the globals, not a guess
    assert ed.globalDefaultFor("MOUSE") in ("on", "off")
    assert ed.globalDefaultFor("AUTOMUTE") in ("on", "off")
    print("OK: the three bool override rows commit and clear independently")


def test_unnamed_fallback_group_scoped_cascade() -> None:
    """Final-gate regression: on a MIXED scene (named group + an unnamed object of the same
    type), the fallback group's cascade must touch ONLY the empty-name objects - a plain type
    cascade also flipped the named group's members (probed: SKIP grew to '1 2 3')."""
    from lwe_ui.editor import EditorBridge
    wid = "601"
    wdir = Path(_TMP) / ".local/share/lwe/wallpapers" / wid
    wdir.mkdir(parents=True, exist_ok=True)
    project = {"title": "Mixed Scene", "type": "scene", "file": "scene.json",
               "general": {"properties": {}}}
    (wdir / "project.json").write_text(json.dumps(project), encoding="utf-8")
    scene = {"objects": [
        {"id": 1, "name": "leaf_swarm", "particle": {}, "origin": "0 0 0"},
        {"id": 2, "name": "leaf_swarm", "particle": {}, "origin": "0 0 0"},
        {"id": 3, "name": "", "particle": {}, "origin": "0 0 0"},
    ]}
    (wdir / "scene.json").write_text(json.dumps(scene), encoding="utf-8")
    ed = EditorBridge()
    ed.open(wid)
    ed.setUnnamedGroupEnabled("particle", False)
    skipped = {o["objid"] for o in ed.objectList() if o["skipped"]}
    assert skipped == {"3"}, f"fallback cascade must not touch named-group members: {skipped}"
    assert next(g for g in ed.authoredGroups() if g["name"] == "leaf_swarm")["enabled"] is True
    ed.setUnnamedGroupEnabled("particle", True)
    assert not any(o["skipped"] for o in ed.objectList())
    print("OK: unnamed fallback group cascades only over empty-name objects")


if __name__ == "__main__":
    main()
    test_authored_multimember_group()
    test_bool_override_rows_are_independent()
    test_unnamed_fallback_group_scoped_cascade()
