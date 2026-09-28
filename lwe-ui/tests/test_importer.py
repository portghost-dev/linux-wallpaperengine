"""Workshop import pipeline contract (storage/importer.py + the review graduation path).

  * scan_new finds workshop dirs the library does not know; unsafe ids and filtered
    types never surface
  * copy policy: staged copy lands the tree in WALLPAPERS_DIR; BG = the library dir
  * reference policy: no copy; BG = the workshop dir; the item still joins the library
    grid via its `review` tags row (library_ids) and resolves title/thumb through BG
  * review ON -> state `review` (never in the watcher pool - good_ids excludes it);
    review OFF -> state `good`
  * CC derives from the project.json preset wec_* block; identity otherwise
  * dedup: known ids (any state, incl. bad tombstones) and existing dirs are skipped
  * approve graduates review -> good
  * no copy follows or copies a link inside the folder (the import, a held preset's copy,
    Add from folder); an imported receipt names the links left out, Add from folder logs
    them, and a project.json that is itself a link counts as none

Run: PYTHONPATH=src python3 tests/test_importer.py
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_TMP = tempfile.mkdtemp(prefix="lwe-import-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")


def _mk_workshop_item(workshop: Path, wid: str, wtype: str = "scene",
                      title: str = "", preset: dict | None = None) -> None:
    d = workshop / wid
    d.mkdir(parents=True, exist_ok=True)
    # honest payloads per type: the completeness gate verifies the declared file
    # (videos) or the scene payload really exists
    fname = "video.mp4" if wtype == "video" else "scene.json"
    pj = {"type": wtype, "title": title or f"Item {wid}", "file": fname}
    if preset:
        pj["preset"] = preset
    (d / "project.json").write_text(json.dumps(pj), encoding="utf-8")
    if wtype == "video":
        (d / fname).write_bytes(b"v" * 32)
    else:
        (d / "scene.pkg").write_bytes(b"x" * 32)
    (d / "preview.jpg").write_bytes(b"j" * 8)


_SECRETS = (b"PROBE", b"SECRET", b"PRIVATE-KEY")
_LINKS = ["folder-link", "scene.pkg", "sub/file-link"]


def _mk_linked_item(d: Path, pj: dict) -> None:
    """A wallpaper folder of real files with three links out of it: to a folder holding .probe, to a
    file (from a real subfolder), and scene.pkg to a folder standing for $HOME."""
    outside = Path(_TMP) / "outside"
    for path, data in ((outside / "folder" / ".probe", b"PROBE"), (outside / "secret.txt", b"SECRET"),
                       (outside / "home" / ".ssh" / "id_rsa", b"PRIVATE-KEY"),
                       (d / "project.json", json.dumps(pj).encode()), (d / "preview.jpg", b"j" * 8),
                       (d / "sub" / "real.txt", b"real")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    os.symlink(outside / "folder", d / "folder-link")
    os.symlink(outside / "secret.txt", d / "sub" / "file-link")
    os.symlink(outside / "home", d / "scene.pkg")


def _assert_only_real_files(copy: Path, store: Path, real: set) -> None:
    """`copy` holds exactly the real entries and no link, and no link target's bytes are in `store`."""
    assert {p.relative_to(copy).as_posix() for p in copy.rglob("*")} == real, sorted(copy.rglob("*"))
    assert not any(p.is_symlink() for p in copy.rglob("*")), "a link was copied"
    assert not any(p.read_bytes() in _SECRETS for p in store.rglob("*") if p.is_file()), \
        "a link's target was copied"


def _test_the_import_copies_no_link(importer, lib: Path, workshop: Path) -> None:
    _mk_linked_item(workshop / "301", {"type": "scene", "title": "Linked", "file": "scene.json"})
    (workshop / "301" / "scene.json").write_text("{}", encoding="utf-8")
    r = importer.import_one("301")
    _assert_only_real_files(lib / "301", lib, {"preview.jpg", "project.json", "scene.json", "sub", "sub/real.txt"})
    assert (r["action"], r["skipped_links"]) == ("imported-review", _LINKS), r


def _test_a_preset_copies_no_link(importer, lib: Path, workshop: Path) -> None:
    """Both copies through _copy_or_reference: a preset held for a missing base, one wired to base 101."""
    for wid, dep, action in (("302", "399", "imported-missing-dep"), ("303", "101", "imported-review")):
        _mk_linked_item(workshop / wid, {"title": "Linked preset", "dependency": dep, "preset": {"wec_brs": 50}})
        r = importer.import_one(wid)
        _assert_only_real_files(lib / wid, lib, {"preview.jpg", "project.json", "sub", "sub/real.txt"})
        assert (r["action"], r["skipped_links"]) == (action, _LINKS), r


def _test_add_from_folder_copies_no_link(ws, paths) -> None:
    src = Path(_TMP) / "hand" / "linked_hand"
    _mk_linked_item(src, {"type": "scene", "title": "Linked by hand", "file": "scene.json"})
    (src / "scene.json").write_text("{}", encoding="utf-8")
    with unittest.TestCase().assertLogs("lwe_ui.workshop", "INFO") as logs:
        wid = ws.addFromFolder(str(src))
    assert wid == "linked_hand", wid
    _assert_only_real_files(paths.manual_dir() / wid, paths.manual_dir(),
                            {"preview.jpg", "project.json", "scene.json", "sub", "sub/real.txt"})
    assert logs.output == [f"INFO:lwe_ui.workshop:add from folder linked_hand: link left out: {path}"
                           for path in _LINKS], logs.output


def _test_add_from_folder_logs_each_left_out_link_on_one_line(ws, paths) -> None:
    src = Path(_TMP) / "hand" / "controls_hand"
    src.mkdir(parents=True)
    (src / "project.json").write_text(json.dumps({"type": "scene", "title": "Controls", "file": "scene.json"}),
                                      encoding="utf-8")
    (src / "scene.json").write_text("{}", encoding="utf-8")
    target = Path(_TMP) / "outside" / "loose.tex"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")
    for name in ("a\nnot added: SPOOF.tex", "b\u2028c.tex", "d\u009be.tex", "f\u202eg.tex"):
        os.symlink(target, src / name)
    with unittest.TestCase().assertLogs("lwe_ui.workshop", "INFO") as logs:
        assert ws.addFromFolder(str(src)) == "controls_hand"
    assert sorted(logs.output) == [f"INFO:lwe_ui.workshop:add from folder controls_hand: link left out: {name}"
                                   for name in ("a?not added: SPOOF.tex", "b?c.tex", "d?e.tex", "f?g.tex")], \
        logs.output


def _link_project_json(d: Path) -> None:
    """`d` holding a project.json that is a link to a valid one outside it."""
    target = Path(_TMP) / "outside" / "project.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"type": "scene", "title": "Linked project", "file": "scene.json"}),
                      encoding="utf-8")
    d.mkdir(parents=True, exist_ok=True)
    os.symlink(target, d / "project.json")


def _test_the_import_takes_a_linked_project_json_as_none(importer, lib: Path, workshop: Path) -> None:
    """The item is never offered or copied, and a preset on it stays held."""
    from lwe_ui.storage import meta, tags
    _link_project_json(workshop / "304")
    (workshop / "304" / "scene.pkg").write_bytes(b"x" * 32)
    (workshop / "305").mkdir()
    (workshop / "305" / "project.json").write_text(json.dumps({"title": "Preset on 304", "dependency": "304"}),
                                                  encoding="utf-8")
    assert "304" not in importer.scan_new()
    assert importer.import_one("304")["action"] == "skipped-incomplete"
    assert importer.import_one("305")["action"] == "imported-missing-dep"
    assert importer.resolve_missing_deps() == 0 and meta.get("305").get("depMissing") is True
    assert not (lib / "304").exists() and "304" not in tags.known_ids()


def _test_add_from_folder_refuses_a_linked_project_json(ws, paths) -> None:
    src = Path(_TMP) / "hand" / "linked_project"
    _link_project_json(src)
    (src / "scene.json").write_text("{}", encoding="utf-8")
    before = sorted(p.name for p in paths.manual_dir().glob("*"))
    assert ws.addFromFolder(str(src)) == ""
    assert sorted(p.name for p in paths.manual_dir().glob("*")) == before, "a refused add writes nothing"


def main() -> None:
    from lwe_ui.storage import importer, paths, settings, tags, wp
    from lwe_ui.models import library_ids

    paths.ensure_dirs()
    settings.ensure_exists()
    workshop = Path(_TMP) / "workshop"
    workshop.mkdir(parents=True)
    s = settings.load()
    s["WORKSHOP_DIR"] = str(workshop)
    settings.save(s)
    lib = Path(importer._wallpapers_dir())

    _mk_workshop_item(workshop, "101", "scene",
                      preset={"wec_brs": 60, "wec_con": 50, "wec_sa": 40, "wec_hue": 50})
    _mk_workshop_item(workshop, "102", "video")
    _mk_workshop_item(workshop, "103", "scene")
    (workshop / "not a safe id").mkdir()
    (workshop / ".partial").mkdir()

    found = importer.scan_new()
    assert found == ["101", "102", "103"], found


    r = importer.import_one("101")
    assert r["action"] == "imported-review", r
    assert (lib / "101" / "project.json").exists(), "copy policy must land the tree"
    conf = wp.load("101")
    assert conf["BG"] == "101", "copy-policy BG is the bare wid (relocatable library)"
    assert conf["TYPE"] == "scene"
    assert conf["CC"].split()[0] == "1.2", f"wec_brs 60 -> brightness 1.2: {conf['CC']}"
    assert "101" in tags.review_ids()
    assert "101" not in tags.good_ids(), "review items must never reach the watcher pool"
    assert not (lib / ".import-101").exists(), "staging dir must not linger"

    assert "101" in library_ids()

    assert importer.import_one("101")["action"] == "skipped-duplicate"
    tags.set_state("103", "t", "bad")
    assert importer.import_one("103")["action"] == "skipped-duplicate"
    assert "103" not in importer.scan_new()

    s = settings.load()
    s["STORAGE_POLICY"] = "reference"
    s["REVIEW_REQUIRED"] = False
    settings.save(s)
    r = importer.import_one("102")
    assert r["action"] == "imported-good", r
    assert not (lib / "102").exists(), "reference policy must not copy"
    conf = wp.load("102")
    assert conf["BG"] == str(workshop / "102")
    assert "102" in tags.good_ids()
    assert "102" in library_ids(), "a referenced good item joins the grid via tags"

    # reference + review ON: visible through the review tags row despite no dir
    s = settings.load(); s["REVIEW_REQUIRED"] = True; settings.save(s)
    _mk_workshop_item(workshop, "104", "scene")
    r = importer.import_one("104")
    assert r["action"] == "imported-review", r
    assert "104" in library_ids(), "a referenced review item must be visible in the grid"

    _mk_workshop_item(workshop, "105", "scene")
    res = importer.run_scan_and_import()
    assert res["found"] == 1 and res["imported"] == 1, res

    # completeness gate: a half-downloaded tree (no project.json, or payload missing)
    # is skipped WITHOUT tagging, so the next pass retries after Steam finishes
    (workshop / "106").mkdir()
    assert "106" not in importer.scan_new(), "no project.json -> not scanned"
    r = importer.import_one("106")
    assert r["action"] == "skipped-incomplete", r
    assert "106" not in tags.known_ids(), "incomplete items must never be tagged"
    (workshop / "106" / "project.json").write_text(
        json.dumps({"type": "scene", "title": "Late", "file": "scene.json"}), encoding="utf-8")
    assert "106" not in importer.scan_new(), "scene payload still missing -> still skipped"
    (workshop / "106" / "scene.pkg").write_bytes(b"x")
    assert "106" in importer.scan_new(), "finished download -> importable"

    # title sanitization: a newline in a third-party title must not become a fake
    # tags record (the watcher's parser is line-based - the review-gate bypass)
    _mk_workshop_item(workshop, "107", "scene", title="evil\n108,x,good")
    r = importer.import_one("107")
    assert r["action"] == "imported-review", r
    assert "108" not in tags.known_ids(), "injected record must not exist"
    assert "\n" not in next(t["title"] for t in tags.load() if t["id"] == "107")

    # copy-policy BG is the BARE wid (relocatable library), and it still resolves
    s = settings.load(); s["STORAGE_POLICY"] = "copy"; settings.save(s)
    _mk_workshop_item(workshop, "109", "scene")
    assert importer.import_one("109")["action"] == "imported-review"
    assert wp.load("109")["BG"] == "109", wp.load("109")["BG"]

    from lwe_ui import bench_courier
    from PySide6.QtCore import QCoreApplication
    from lwe_ui.models import Backend
    app = QCoreApplication.instance() or QCoreApplication(["t"])  # noqa: F841
    b = Backend()
    b.approveReview("104")
    assert "104" in tags.good_ids() and "104" not in tags.review_ids()

    # the bridge round trip: worker thread -> queued completion on the GUI thread,
    # busy-guard held during the pass
    _mk_workshop_item(workshop, "110", "scene")
    from lwe_ui.models import ImportBridge
    ib = ImportBridge(b)
    done = {}
    ib.scanFinished.connect(lambda f, i: done.update(found=f, imported=i))
    ib.rescanNow()
    assert ib.isBusy() is True, "the pass must hold the busy flag"
    import time as _t
    deadline = _t.time() + 15
    while _t.time() < deadline and "found" not in done:
        QCoreApplication.processEvents()
        _t.sleep(0.02)
    assert done.get("found") == 2 and done.get("imported") == 2, done
    assert ib.isBusy() is False
    assert "110" in tags.review_ids() and "106" in tags.review_ids()

    from lwe_ui.workshop import WorkshopBridge
    try:
        _test_the_import_copies_no_link(importer, lib, workshop)
        _test_a_preset_copies_no_link(importer, lib, workshop)
        _test_the_import_takes_a_linked_project_json_as_none(importer, lib, workshop)
        ws = WorkshopBridge(b, None)
        _test_add_from_folder_copies_no_link(ws, paths)
        _test_add_from_folder_logs_each_left_out_link_on_one_line(ws, paths)
        _test_add_from_folder_refuses_a_linked_project_json(ws, paths)
    finally:
        for tree in ("outside", "hand"):
            shutil.rmtree(Path(_TMP) / tree, ignore_errors=True)

    print("OK test_importer - scan/type-filter/copy/reference/review/dedup/CC/"
          "batch/approve/completeness/sanitize/bare-bg/bridge-thread/no-links all hold")


if __name__ == "__main__":
    main()
