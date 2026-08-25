"""Missing-dependency pipeline: detector, held import,
hands-free resolve, preview gate.

Observed WE behavior this encodes: preset publications carry project.json `dependency`
(the base item's workshop id) + a flat `preset` property map and NO payload of their
own - they render THROUGH the base item.

  * scan: a payload-less preset passes the completeness gate and the type filter
  * dep present same pass: the base imports FIRST (count honesty), the preset's conf
    wires through it (BG = base's BG, TYPE = base's, CC from wec_*, props overlay)
  * dep missing: held import - review state REGARDLESS of REVIEW_REQUIRED, meta
    marker, itemList row flags it, preview refuses
  * resolve: the base arriving later completes the import hands-free (resolved count,
    conf rewired, marker cleared, name filled)
  * badge parity holds throughout

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 tests/test_workshop_deps.py
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_TMP = tempfile.mkdtemp(prefix="lwe-deps-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

_SHIM = Path(_TMP) / "bin"
_SHIM.mkdir()
os.environ["PATH"] = f"{_SHIM}:{os.environ.get('PATH', '')}"
sh = _SHIM / "xdg-mime"
sh.write_text("#!/bin/sh\necho steam.desktop\n", encoding="utf-8")
sh.chmod(sh.stat().st_mode | stat.S_IEXEC)


def _mk_scene(workshop: Path, wid: str, title: str = "") -> None:
    d = workshop / wid
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps(
        {"type": "scene", "title": title or f"Item {wid}", "file": "scene.json"}),
        encoding="utf-8")
    (d / "scene.pkg").write_bytes(b"x" * 32)
    (d / "preview.jpg").write_bytes(b"j" * 8)


def _mk_preset(workshop: Path, wid: str, dep: str, title: str = "") -> None:
    """The observed shape verbatim: dependency + preset map, no type/file/payload."""
    d = workshop / wid
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps({
        "title": title or f"Preset {wid}",
        "dependency": dep,
        "preset": {"_d0": None, "parallaxstrength": 0.2, "side": "centerblack",
                   "stars": False, "wec_brs": 60, "wec_con": 50, "wec_sa": 50,
                   "wec_hue": 50},
        "preview": "preview.jpg",
    }), encoding="utf-8")
    (d / "preview.jpg").write_bytes(b"j" * 8)


def main() -> None:
    from PySide6.QtCore import QCoreApplication
    from lwe_ui import bench_courier
    from lwe_ui.models import Backend
    from lwe_ui.storage import importer, meta, paths, settings, tags, wp
    from lwe_ui.workshop import WorkshopBridge

    bench_courier.resume = lambda *a, **k: True

    app = QCoreApplication.instance() or QCoreApplication(["t"])  # noqa: F841
    paths.ensure_dirs()
    settings.ensure_exists()
    workshop_dir = Path(_TMP) / "workshop"
    workshop_dir.mkdir()
    s = settings.load()
    s["WORKSHOP_DIR"] = str(workshop_dir)
    settings.save(s)

    backend = Backend()
    ws = WorkshopBridge(backend, None)

    _mk_scene(workshop_dir, "500", "Base scene")
    _mk_preset(workshop_dir, "401", "500", "Preset over base")
    res = importer.run_scan_and_import()
    assert res["found"] == 2 and res["imported"] == 2, res
    conf = wp.load("401")
    assert conf["BG"] == "500", f"preset renders through the base's BG: {conf['BG']}"
    assert conf["TYPE"] == "scene", "preset TYPE = the base's type"
    assert conf["CC"].split()[0] == "1.2", f"wec_brs 60 -> brightness 1.2: {conf['CC']}"
    assert conf["props"].get("parallaxstrength") == "0.2", conf["props"]
    assert conf["props"].get("side") == "centerblack"
    assert conf["props"].get("stars") == "false", \
        "bools reach the engine lowercase, never Python-cased"
    assert "wec_brs" not in conf["props"], "wec_* belongs to CC, never props"
    assert "_d0" not in conf["props"]
    m = meta.get("401")
    assert not m.get("depMissing") and m.get("depName") == "Base scene", m
    assert "401" in tags.review_ids() and "500" in tags.review_ids()

    s = settings.load(); s["REVIEW_REQUIRED"] = False; settings.save(s)
    _mk_preset(workshop_dir, "402", "600", "Orphan preset")
    res = importer.run_scan_and_import()
    assert res["imported"] == 1 and res["resolved"] == 0, res
    assert "402" in tags.review_ids(), \
        "a held preset lands in review even with review OFF - it cannot render"
    m = meta.get("402")
    assert m.get("depMissing") is True and m.get("depWid") == "600", m
    backend.refresh()
    row = next(i for i in ws.itemList() if i["wid"] == "402")
    assert row["depMissing"] is True and row["depWid"] == "600", row
    assert ws.depInfo("402")["missing"] is True

    assert len(ws.itemList()) == backend.pendingReviewCount()

    s = settings.load(); s["DETECT_TYPES"] = "video"; settings.save(s)
    _mk_preset(workshop_dir, "403", "601", "Filtered-era preset")
    assert "403" in importer.scan_new(), "the type filter must not eat presets"
    res = importer.run_scan_and_import()
    assert any(r["wid"] == "403" and r["action"] == "imported-missing-dep"
               for r in res["results"]), res["results"]
    s = settings.load(); s["DETECT_TYPES"] = "all"; settings.save(s)

    _mk_scene(workshop_dir, "600", "Late base")
    res = importer.run_scan_and_import()
    assert res["resolved"] == 1, res
    conf = wp.load("402")
    assert conf["BG"] == "600" and conf["TYPE"] == "scene", conf
    m = meta.get("402")
    assert not m.get("depMissing") and m.get("depName") == "Late base", m
    assert "600" in tags.good_ids(), \
        "the arriving base itself imports (review is OFF here, so it lands good)"
    # NOTE: review was OFF when 402 was held; resolution rewires the conf but the
    # held review state STAYS until the user decides - resolution is not approval
    assert "402" in tags.review_ids()
    backend.refresh()
    row = next(i for i in ws.itemList() if i["wid"] == "402")
    assert row["depMissing"] is False, "the chip clears on resolve"

    _mk_preset(workshop_dir, "410", "410", "Selfdep")
    res = importer.run_scan_and_import()
    acts = {r["wid"]: r["action"] for r in res["results"]}
    assert acts.get("410") == "imported-missing-dep", acts

    _mk_preset(workshop_dir, "411", "412")
    _mk_preset(workshop_dir, "412", "411")
    _mk_scene(workshop_dir, "700", "Sibling")
    res = importer.run_scan_and_import()
    acts = {r["wid"]: r["action"] for r in res["results"]}
    assert acts.get("411") == "imported-missing-dep", acts
    assert acts.get("412") == "imported-missing-dep", \
        "a held preset must never count as a usable base (H2 via placeholder conf)"
    assert str(acts.get("700", "")).startswith("imported"), \
        "one poisoned graph must never eat the pass for siblings"
    # cycles can never resolve - and must not crash the resolve fixpoint either
    assert importer.run_scan_and_import()["resolved"] == 0

    # chained preset P -> Q -> B(absent): BOTH hold; when B lands the chain collapses
    # to the base in ONE pass (fixpoint), P renders through B
    _mk_preset(workshop_dir, "420", "421", "Chain P")
    _mk_preset(workshop_dir, "421", "800", "Chain Q")
    res = importer.run_scan_and_import()
    acts = {r["wid"]: r["action"] for r in res["results"]}
    assert acts.get("420") == "imported-missing-dep", \
        "P must hold while its dep chain is unresolved (H2a)"
    assert acts.get("421") == "imported-missing-dep"
    _mk_scene(workshop_dir, "800", "Chain base")
    res = importer.run_scan_and_import()
    assert res["resolved"] == 2, f"fixpoint must collapse the chain in one pass: {res}"
    assert wp.load("421")["BG"] == "800"
    assert wp.load("420")["BG"] == "800", \
        "P renders through the chain's real base, never through Q's preset dir"

    half = workshop_dir / "810"
    half.mkdir()
    (half / "project.json").write_text(json.dumps(
        {"type": "scene", "title": "Half base", "file": "scene.json"}), encoding="utf-8")
    _mk_preset(workshop_dir, "430", "810", "Eager preset")
    res = importer.run_scan_and_import()
    acts = {r["wid"]: r["action"] for r in res["results"]}
    assert acts.get("430") == "imported-missing-dep", \
        "a mid-download base is not present - the preset must hold (H2b)"
    (half / "scene.pkg").write_bytes(b"x" * 32)
    res = importer.run_scan_and_import()
    assert res["resolved"] == 1 and wp.load("430")["BG"] == "810", res

    _mk_preset(workshop_dir, "440", "820", "Doomed preset")
    importer.run_scan_and_import()
    backend.refresh()
    ws.trashItem("440")
    assert meta.get("440").get("depMissing") is not True, "trash must clear the marker"
    _mk_scene(workshop_dir, "820", "Late for doomed")
    res = importer.run_scan_and_import()
    assert not any(r["wid"] == "440" for r in res["results"] if "resolved" in r), res
    assert "440" in tags.known_ids() and "440" not in tags.review_ids()

    meta.update("999", {"depMissing": True, "depWid": "820"})
    res = importer.run_scan_and_import()
    assert res["resolved"] == 0, "a dead wid must never count as resolved"
    assert meta.get("999").get("depMissing") is not True, "stale marker must clear"
    from lwe_ui.storage import paths as _paths
    assert not (_paths.wp_dir() / "999.conf").exists(), \
        "no conf may be conjured for a wid with no tags row"

    evil = workshop_dir / "830"
    evil.mkdir()
    (evil / "project.json").write_text(json.dumps(
        {"type": "scene", "title": "Evil\nbase\x1b[31mred", "file": "scene.json"}),
        encoding="utf-8")
    (evil / "scene.pkg").write_bytes(b"x" * 32)
    _mk_preset(workshop_dir, "450", "830", "Innocent preset")
    importer.run_scan_and_import()
    nm = meta.get("450").get("depName", "")
    assert "\n" not in nm and "\x1b" not in nm, f"unsanitized dep name: {nm!r}"

    print("OK test_workshop_deps - detector/held-import/same-pass-wiring/type-filter/"
          "resolve/preview-gate/badge-parity/cycles/chains/half-base/zombies/"
          "hostile-titles all hold")


if __name__ == "__main__":
    main()
