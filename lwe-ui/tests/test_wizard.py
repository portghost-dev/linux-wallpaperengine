"""Self-tests for storage/wizard.py - the static census + outcome->record-event mapping.

Isolation: HOME + XDG_* point at a fresh tempfile tree so the records store round-trip touches
nothing live. Census runs on tmp item dirs (no launch). Event builders are checked for the
constitution invariants (F3/C4) and that a cancel-after-finding does NOT suppress (F1).
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _fresh_env(tmp: str) -> dict[str, str]:
    return {
        "HOME": tmp,
        "XDG_CONFIG_HOME": os.path.join(tmp, ".config"),
        "XDG_STATE_HOME": os.path.join(tmp, ".local/state"),
        "XDG_DATA_HOME": os.path.join(tmp, ".local/share"),
    }


def _reload():
    import lwe_ui.constants  # noqa: F401
    paths = importlib.reload(importlib.import_module("lwe_ui.storage.paths"))
    records = importlib.reload(importlib.import_module("lwe_ui.storage.records"))
    importlib.reload(importlib.import_module("lwe_ui.discovery.project"))
    wizard = importlib.reload(importlib.import_module("lwe_ui.storage.wizard"))
    return paths, records, wizard


def _mk_item(base: Path, wid: str, proj: dict) -> Path:
    d = base / wid
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps(proj), encoding="utf-8")
    (d / "scene.pkg").write_bytes(b"payload")
    return d


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="lwe-wizard-")
    orig = {k: os.environ.get(k) for k in _fresh_env(tmp)}
    try:
        os.environ.update(_fresh_env(tmp))
        paths, R, W = _reload()
        paths.ensure_dirs()

        wsdir = Path(tmp) / "workshop"
        wsdir.mkdir()

        item = _mk_item(wsdir, "100", {"type": "scene", "title": "Clean"})
        c = W.run_census(item, workshop_dir=wsdir)
        assert c["ok"] is True and c["missingDep"] is False and c["wpType"] == "scene"
        assert c["depWid"] == "" and "contentHash" not in c, "census is cheap: no hash on the P1 click"

        preset = _mk_item(wsdir, "200", {"type": "scene", "dependency": "999"})
        c2 = W.run_census(preset, workshop_dir=wsdir)
        assert c2["ok"] is False and c2["missingDep"] is True and c2["depWid"] == "999"
        assert "forecast" not in c2 and "vram" not in c2, "no static VRAM forecast surfaced (F5)"

        _mk_item(wsdir, "999", {"type": "scene", "title": "Base"})
        c3 = W.run_census(preset, workshop_dir=wsdir)
        assert c3["ok"] is True and c3["missingDep"] is False

        H = "deadbeef"
        ev = W.approved_via_wizard(content_hash=H, comment="looked great")
        assert ev["action"] == "approved" and ev["initiator"] == "human"
        assert ev["machine"]["verdict"] == "ran" and ev["machine"]["contentHash"] == H
        assert R.append("100", ev) and R.read("100")[-1]["comment"] == "looked great"

        ev = W.approved_untested()
        assert ev["action"] == "approved" and ev["machine"] is None

        ev = W.deleted_wizard_recommended(
            lineage=["crashed", "fixes_applied", "crashed_post_fix", "recommended_trash"],
            content_hash=H, tests_failed=["launch"], repair_attempts=["trailing_comma"],
            comment="died post-fix")
        assert ev["action"] == "deleted" and ev["initiator"] == "wizard_recommended"
        assert ev["lineage"][-1] == "recommended_trash" and ev["machine"]["verdict"] == "crashed"
        assert ev["machine"]["repairAttempts"] == ["trailing_comma"]

        ev = W.deleted_by_user(where="library", comment="dont want it")
        assert ev["action"] == "deleted" and ev["initiator"] == "human" and ev["machine"] is None

        ev = W.benched_no_decision(lineage=["crashed", "recommended_trash"], content_hash=H)
        assert ev["action"] == "benched_no_decision" and ev["initiator"] == "human"
        assert ev["machine"]["verdict"] == "crashed"
        assert R.append("300", ev)
        assert R.is_suppressed("300", present_on_disk=True) is False, \
            "benched_no_decision must not suppress (F1)"

        ev = W.bypassed()
        assert ev["action"] == "bypassed" and ev["initiator"] == "human"
        assert R.append("400", W.deleted_by_user(where="workshop"))
        assert R.is_suppressed("400", present_on_disk=True) is True

        print("OK test_wizard - census (clean/dep-gate/dep-present), event builders honor the "
              "constitution (F3/C4), benched_no_decision does not suppress (F1)")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
