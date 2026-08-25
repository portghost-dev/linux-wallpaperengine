"""WorkshopBridge contract (workshop.py + the ruled trash chain + tombstone manager).

  * steam detection reads the x-scheme-handler/steam handler (PATH-shimmed here)
  * itemList = review-state rows newest-first, tile type sourced from project.json
    (conf TYPE as fallback); crash/heavy assessment now lives in the import
    wizard, not here (see test_wizard_bridge)
  * trash chain: a deletion record is written at trash time, tags -> bad, OUR copy
    deleted source-or-no-source (never a symlink, never outside the library dir),
    Steam's tree untouched
  * tombstones: list carries reasons; restore forgets the row so the item re-pends or
    reimports; clear-all empties both stores
  * trashConsequence reports the mode-correct disk truth

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 tests/test_workshop_bridge.py
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_TMP = tempfile.mkdtemp(prefix="lwe-workshop-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

_SHIM = Path(_TMP) / "bin"
_SHIM.mkdir()
os.environ["PATH"] = f"{_SHIM}:{os.environ.get('PATH', '')}"


def _set_steam_shim(present: bool) -> None:
    sh = _SHIM / "xdg-mime"
    sh.write_text("#!/bin/sh\n" + ("echo steam.desktop\n" if present else "exit 0\n"),
                  encoding="utf-8")
    sh.chmod(sh.stat().st_mode | stat.S_IEXEC)


def _mk_workshop_item(workshop: Path, wid: str, wtype: str = "scene") -> None:
    d = workshop / wid
    d.mkdir(parents=True, exist_ok=True)
    # honest per-type payloads: the completeness gate verifies the DECLARED file for
    # non-scene types and the pkg/json pair for scenes
    fname = {"video": "video.mp4", "web": "index.html"}.get(wtype, "scene.json")
    (d / "project.json").write_text(
        json.dumps({"type": wtype, "title": f"Item {wid}", "file": fname}),
        encoding="utf-8")
    if wtype == "scene":
        (d / "scene.pkg").write_bytes(b"x" * 32)
    else:
        (d / fname).write_bytes(b"v" * 32)
    (d / "preview.jpg").write_bytes(b"j" * 8)


def main() -> None:
    from PySide6.QtCore import QCoreApplication
    from lwe_ui import bench_courier
    from lwe_ui.models import Backend
    from lwe_ui.storage import importer, paths, records, settings, tags, tombstones
    from lwe_ui.workshop import WorkshopBridge, _item_page_url

    bench_courier.resume = lambda *a, **k: True

    app = QCoreApplication.instance() or QCoreApplication(["t"])  # noqa: F841
    paths.ensure_dirs()
    settings.ensure_exists()
    workshop_dir = Path(_TMP) / "workshop"
    workshop_dir.mkdir()
    s = settings.load()
    s["WORKSHOP_DIR"] = str(workshop_dir)
    settings.save(s)
    lib = Path(importer._wallpapers_dir())

    backend = Backend()
    ws = WorkshopBridge(backend, None)

    # --- engine ownership: the Workshop bridge no longer spawns an engine (the wizard owns
    #     the bench), so it never reports busy to its peers -----------------------------
    assert ws.engineBusy() is False, "the Workshop bridge holds no engine lease anymore"

    _set_steam_shim(True)
    ws.recheckSteam()
    assert ws.steamAvailable() is True
    _set_steam_shim(False)
    changes = {"n": 0}
    ws.steamChanged.connect(lambda: changes.__setitem__("n", changes["n"] + 1))
    ws.recheckSteam()
    assert ws.steamAvailable() is False and changes["n"] == 1
    _set_steam_shim(True)
    ws.recheckSteam()

    assert _item_page_url("42", True) == "steam://url/CommunityFilePage/42"
    assert _item_page_url("42", False).startswith("https://")
    assert ws.itemLinkText("42") == "steam://url/CommunityFilePage/42"
    assert ws.itemLinkText("not a wid") == "", "unsafe wids must never reach a URL"

    _mk_workshop_item(workshop_dir, "201", "scene")
    _mk_workshop_item(workshop_dir, "202", "web")
    _mk_workshop_item(workshop_dir, "203", "video")
    importer.run_scan_and_import()
    backend.refresh()
    items = ws.itemList()
    assert [i["wid"] for i in items] == ["203", "202", "201"], \
        f"newest-first from tags order: {[i['wid'] for i in items]}"
    web = next(i for i in items if i["wid"] == "202")
    assert web["wpType"] == "web", "tile type comes from project.json, not the conf default"
    assert next(i for i in items if i["wid"] == "201")["wpType"] == "scene"
    assert next(i for i in items if i["wid"] == "203")["wpType"] == "video"
    assert all(i["forecast"] == "" for i in items), "no type forecasts (web is fully supported)"
    assert all("verdict" not in i for i in items), "the tile no longer carries a verdict (chip removed)"

    d203 = ws._resolve_dir("203")
    records.append("203", records.make_event("benched_no_decision", where="workshop",
                   machine={"verdict": "crashed", "contentHash": records.content_hash(d203)}))
    backend.refresh()
    assert next(i for i in ws.itemList() if i["wid"] == "203")["crashed"] is True, \
        "a crash record for the current content surfaces the Crashed chip"
    (Path(d203) / "hash_bump.txt").write_text("edited", encoding="utf-8")
    backend.refresh()
    assert next(i for i in ws.itemList() if i["wid"] == "203")["crashed"] is False, \
        "the hash guard clears the Crashed chip when the item is edited"

    _mk_workshop_item(workshop_dir, "204", "scene")
    importer.run_scan_and_import()
    backend.refresh()

    assert (lib / "204").is_dir(), "copy policy landed the tree"
    ws.trashItem("204")
    assert "204" not in tags.review_ids() and "204" in tags.known_ids()
    assert records.head("204")["action"] == "deleted", "trashItem records a deletion"
    # the delete runs on a worker thread (F9) - poll it briefly
    deadline = time.time() + 5
    while (lib / "204").exists() and time.time() < deadline:
        time.sleep(0.05)
    assert not (lib / "204").exists(), "our copy must be deleted"
    assert (workshop_dir / "204").is_dir(), "Steam's tree is never touched"
    ws.trashItem("201")
    assert records.head("201")["action"] == "deleted"
    ws.trashItem("202", "ran but ugly")
    assert records.head("202")["action"] == "deleted"
    assert records.head("202")["comment"] == "ran but ugly", "trashItem threads the optional note"
    # tombstoned items never rescan (the boomerang guard)
    assert importer.scan_new() == [], f"tombstones must not rescan: {importer.scan_new()}"

    # symlink fence: a link at the library slot is never rmtree'd through
    real = Path(_TMP) / "elsewhere"
    real.mkdir()
    (real / "x").write_text("keep", encoding="utf-8")
    _mk_workshop_item(workshop_dir, "205", "scene")
    importer.run_scan_and_import()
    backend.refresh()
    import shutil
    shutil.rmtree(lib / "205")
    (lib / "205").symlink_to(real)
    ws.trashItem("205")
    assert (real / "x").exists(), "the symlink fence must protect the link target"

    _mk_workshop_item(workshop_dir, "206", "scene")
    importer.run_scan_and_import()
    backend.refresh()
    assert ws.trashConsequence("206")["hasCopy"] is True
    s = settings.load(); s["STORAGE_POLICY"] = "reference"; settings.save(s)
    _mk_workshop_item(workshop_dir, "207", "scene")
    importer.run_scan_and_import()
    backend.refresh()
    assert ws.trashConsequence("207")["hasCopy"] is False
    s = settings.load(); s["STORAGE_POLICY"] = "copy"; settings.save(s)

    from lwe_ui.storage import records
    rows = ws.recordList()
    by = {r["wid"]: r for r in rows}
    assert set(by) == {"201", "202", "204", "205"}, set(by)
    assert by["201"]["title"] == "Item 201"
    assert by["201"]["line"], "each row carries a composed latest-event line"
    assert records.head("201")["action"] == "deleted", "trashItem wrote a deletion record"

    # purge wipes the record AND the tags gate, so the item can reimport from its source
    ws.purgeRecord("204")
    assert "204" not in {r["wid"] for r in ws.recordList()}
    assert records.has_record("204") is False
    assert "204" not in tags.known_ids(), "purge drops the tags row -> re-import un-gated"
    assert "204" in importer.scan_new(), "an un-gated item reimports from the source"
    assert "206" in tags.review_ids(), "review rows are untouched by the record manager"

    bp = set(ws.bypassableWids())
    assert "201" in bp and "202" in bp, f"still-subscribed tombstoned items are bypassable: {bp}"
    ws.bypassImport()
    assert ws.bypassableWids() == [], "after bypass, no item is still gated+subscribed"
    assert records.head("201")["action"] == "bypassed", "bypass writes a bypassed event"
    assert "201" not in tags.known_ids(), "bypass drops the tags gate"
    assert "201" in importer.scan_new(), "an un-gated still-subscribed item reimports"

    (workshop_dir / "800").mkdir()
    (workshop_dir / "800" / "project.json").write_text(
        json.dumps({"type": "scene", "title": "Base"}), encoding="utf-8")
    (workshop_dir / "801").mkdir()
    (workshop_dir / "801" / "project.json").write_text(
        json.dumps({"type": "scene", "title": "Preset", "dependency": "800"}), encoding="utf-8")
    assert ws.dependentCount("800") == 1, "the base has one dependent preset"
    assert ws.dependentCount("801") == 0, "a leaf item has no dependents"
    assert ws.dependentCount("999999") == 0, "an unknown id has none"

    legacy = lib / "888"
    legacy.mkdir(parents=True)
    (legacy / "project.json").write_text(
        json.dumps({"type": "scene", "title": "Legacy", "file": "scene.json"}),
        encoding="utf-8")
    backend.refresh()
    ids = [i["wid"] for i in ws.itemList()]
    assert "888" in ids, "unknown-on-disk items must surface as Workshop tiles"
    assert len(ids) == backend.pendingReviewCount(), \
        f"badge {backend.pendingReviewCount()} vs tiles {len(ids)} - the invariant broke"

    assert ws.trashConsequence("888")["hasCopy"] is True, "legacy copy: danger truth"
    ws.trashItem("888")
    deadline = time.time() + 5
    while legacy.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert not legacy.exists(), "our copy is deleted even without a workshop source"
    assert ws.trashConsequence("206")["hasCopy"] is True, "source-backed copy: danger truth"
    backend.refresh()

    print("OK test_workshop_bridge - steam-detect/deep-links/population/forecast/"
          "no-engine-ownership/trash-chain/symlink-fence/consequence/tombstone-manager all hold")


if __name__ == "__main__":
    main()
