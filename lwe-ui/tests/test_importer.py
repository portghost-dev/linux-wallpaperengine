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

Run: PYTHONPATH=src python3 tests/test_importer.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
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

    print("OK test_importer - scan/type-filter/copy/reference/review/dedup/CC/"
          "batch/approve/completeness/sanitize/bare-bg/bridge-thread all hold")


if __name__ == "__main__":
    main()
