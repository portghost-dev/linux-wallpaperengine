"""Preset render/identity split + pre-B10 crasher repair (B11).

The bug: a preset (dependency + preset overlay, no own payload) renders THROUGH its
base via BG, but the model read its identity (title/preview/type) from BG too - so it
showed the BASE's name and preview. And pre-B10 imports left BG on the preset's own
payload-less dir, which crashes the engine at launch.

  * identity split: title/preview/type come from the item's OWN dir, never from BG;
    presence still follows BG (render source)
  * repair: a payload-less-BG preset is rewired through its base (B10 wiring)
  * repair leaves an already-rendering conf alone (never clobbers a hand-fixed CC/props)

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 tests/test_preset_identity.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_TMP = tempfile.mkdtemp(prefix="lwe-preset-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")


def _mk_scene(root: Path, wid: str, title: str) -> None:
    d = root / wid
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps(
        {"type": "scene", "title": title, "file": "scene.json"}), encoding="utf-8")
    (d / "scene.pkg").write_bytes(b"x" * 32)
    (d / "preview.jpg").write_bytes(b"base-preview")


def _mk_video(root: Path, wid: str, title: str, fname: str = "ocean.webm") -> None:
    """A video wallpaper whose payload is named per project.json (NOT literal
    video.mp4) - the shape the fixed-name payload list missed (review H1)."""
    d = root / wid
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps(
        {"type": "video", "title": title, "file": fname}), encoding="utf-8")
    (d / fname).write_bytes(b"v" * 32)
    (d / "preview.jpg").write_bytes(b"base-preview")


def _mk_preset(root: Path, wid: str, dep: str, title: str) -> None:
    d = root / wid
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps(
        {"title": title, "dependency": dep,
         "preset": {"wec_brs": 60, "parallaxstrength": 0.3}}), encoding="utf-8")
    (d / "preview.jpg").write_bytes(b"preset-preview")


def main() -> None:
    from lwe_ui import bench_courier
    from lwe_ui.discovery import project
    from lwe_ui.models import LibraryModel, _identity_dir
    from lwe_ui.storage import importer, meta, paths, settings, tags, wp


    paths.ensure_dirs()
    settings.ensure_exists()
    workshop = Path(_TMP) / "workshop"
    workshop.mkdir()
    lib = Path(importer._wallpapers_dir())
    lib.mkdir(parents=True, exist_ok=True)
    s = settings.load(); s["WORKSHOP_DIR"] = str(workshop); settings.save(s)

    # the base (renderable) lives in the library; the preset is a reference item with
    # only its own workshop dir (the real-world pre-B10 shape)
    _mk_scene(lib, "900", "Base Scene Sample")
    _mk_preset(workshop, "901", "900", "OLED Black Preset")

    tags.set_state("901", "OLED Black Preset", "good")
    d = {k: spec["default"] for k, spec in __import__(
        "lwe_ui.constants", fromlist=["WP_SCHEMA"]).WP_SCHEMA.items()}
    d["BG"] = str(workshop / "901")   # payload-less -> would crash
    d["TYPE"] = "scene"
    d["props"] = {}
    wp.save("901", d)

    # identity split: even BEFORE repair, the model must show the PRESET's identity,
    # never the base's (the resolver reads the own dir, not BG)
    idir = _identity_dir("901", str(lib))
    assert idir == str(workshop / "901"), f"identity must be the own dir: {idir}"
    model = LibraryModel()
    model.reload()
    from PySide6.QtCore import Qt
    rows = {}
    for i in range(model.rowCount()):
        idx = model.index(i, 0)
        wid = model.data(idx, Qt.ItemDataRole.UserRole + 1)
        rows[wid] = {
            "title": model.data(idx, Qt.ItemDataRole.UserRole + 2),
            "thumb": model.data(idx, Qt.ItemDataRole.UserRole + 3),
        }
    assert rows["901"]["title"] == "OLED Black Preset", \
        f"preset must keep its own title, not the base's: {rows['901']['title']}"
    assert "901" in rows["901"]["thumb"], \
        f"preset preview must be its own, not the base's: {rows['901']['thumb']}"

    fixed = importer.repair_preset_confs()
    assert fixed == ["901"], f"the crasher must be repaired: {fixed}"
    conf = wp.load("901")
    bg = conf["BG"]
    bg_dir = bg if os.path.isabs(bg) else str(lib / bg)
    assert importer._dir_has_payload(bg_dir), \
        f"after repair BG must point at a renderable dir: {bg}"
    assert conf["CC"].split()[0] == "1.2", f"the preset's own grade is applied: {conf['CC']}"
    assert conf["props"].get("parallaxstrength") == "0.3", conf["props"]
    # identity STILL the preset's own after repair (render moved, identity did not)
    model.reload()
    for i in range(model.rowCount()):
        idx = model.index(i, 0)
        if model.data(idx, Qt.ItemDataRole.UserRole + 1) == "901":
            assert model.data(idx, Qt.ItemDataRole.UserRole + 2) == "OLED Black Preset"

    assert importer.repair_preset_confs() == [], "repair must be idempotent"

    # --- repair NEVER clobbers an already-rendering (hand-fixed) conf ----------------
    _mk_scene(lib, "910", "Base Black Hole")
    _mk_preset(workshop, "911", "910", "Darkest Space")
    tags.set_state("911", "Darkest Space", "good")
    hand = {k: spec["default"] for k, spec in __import__(
        "lwe_ui.constants", fromlist=["WP_SCHEMA"]).WP_SCHEMA.items()}
    hand["BG"] = str(lib / "910")
    hand["CC"] = "1.02 1.52 2.0 -0.125"
    hand["props"] = {"time": "false"}
    wp.save("911", hand)
    assert importer.repair_preset_confs() == [], \
        "an already-rendering preset must be left alone"
    still = wp.load("911")
    assert still["CC"] == "1.02 1.52 2.0 -0.125", "manual grade must survive"
    assert still["props"] == {"time": "false"}, "manual props must survive"
    # but its stolen identity is repaired by the split, no conf change needed
    model.reload()
    for i in range(model.rowCount()):
        idx = model.index(i, 0)
        if model.data(idx, Qt.ItemDataRole.UserRole + 1) == "911":
            assert model.data(idx, Qt.ItemDataRole.UserRole + 2) == "Darkest Space", \
                "the hand-fixed preset shows its OWN name, not the base's"

    # --- H1: a preset whose BASE is a VIDEO (payload named per project.json) must be
    #         recognized as rendering, so a hand-fixed conf is NOT clobbered ----------
    _mk_video(lib, "930", "Ocean Base", fname="ocean.webm")   # NOT literal video.mp4
    _mk_preset(workshop, "931", "930", "Ocean Preset")
    tags.set_state("931", "Ocean Preset", "good")
    vhand = {k: spec["default"] for k, spec in __import__(
        "lwe_ui.constants", fromlist=["WP_SCHEMA"]).WP_SCHEMA.items()}
    vhand["BG"] = str(lib / "930")
    vhand["CC"] = "2.0 0.5 0.5 0.1"
    vhand["props"] = {"speed": "0.5"}
    wp.save("931", vhand)
    assert importer._dir_has_payload(str(lib / "930")), \
        "a video base (ocean.webm) must read as renderable - the H1 payload gap"
    assert importer.repair_preset_confs() == [], \
        "a preset on a video base already renders - repair must not touch it (H1)"
    v = wp.load("931")
    assert v["CC"] == "2.0 0.5 0.5 0.1" and v["props"] == {"speed": "0.5"}, \
        "manual grade/props on a preset-of-video must survive repair (H1 ruled law)"

    # --- copy-policy preset: own lib dir (own project.json, no payload) wins identity -
    _mk_scene(lib, "940", "Copy Base")
    # the preset was COPIED into the library: own dir has its identity but no payload
    own = lib / "941"; own.mkdir()
    (own / "project.json").write_text(json.dumps(
        {"title": "Copied Preset", "dependency": "940",
         "preset": {"wec_brs": 50}}), encoding="utf-8")
    (own / "preview.jpg").write_bytes(b"own-preview")
    tags.set_state("941", "Copied Preset", "good")
    cp = {k: spec["default"] for k, spec in __import__(
        "lwe_ui.constants", fromlist=["WP_SCHEMA"]).WP_SCHEMA.items()}
    cp["BG"] = str(lib / "940"); wp.save("941", cp)
    idir = _identity_dir("941", str(lib))
    assert idir == str(own), f"a copied preset's identity is its OWN lib dir: {idir}"
    assert project.read(idir).get("title") == "Copied Preset"

    _mk_scene(workshop, "920", "Plain Reference")
    tags.set_state("920", "Plain Reference", "good")
    ref = {k: spec["default"] for k, spec in __import__(
        "lwe_ui.constants", fromlist=["WP_SCHEMA"]).WP_SCHEMA.items()}
    ref["BG"] = str(workshop / "920")
    wp.save("920", ref)
    assert _identity_dir("920", str(lib)) == str(workshop / "920")
    assert importer.repair_preset_confs() == [], "a non-preset reference is not touched"

    print("OK test_preset_identity - identity-split/crasher-repair/no-clobber/"
          "idempotent/plain-reference all hold")


if __name__ == "__main__":
    main()
