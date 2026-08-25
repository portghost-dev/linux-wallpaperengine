"""Self-tests for storage/records_view.py (W4a) - migration off the legacy tombstones map, the
neutral-sentence composer, the manager queries, and purge-that-ungates. Pure Python, sandboxed env.
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="lwe-rview-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _reload():
    import lwe_ui.constants  # noqa: F401
    for m in ("paths", "atomic", "tags", "tombstones", "records", "records_view"):
        mod = importlib.reload(importlib.import_module(f"lwe_ui.storage.{m}"))
    from lwe_ui.storage import paths, tags, tombstones, records, records_view
    return paths, tags, tombstones, records, records_view


def main() -> None:
    paths, tags, tombstones, records, RV = _reload()
    paths.ensure_dirs()

    tombstones.record("100", "Old Scene", "crashed")
    tombstones.record("200", "Other", "rejected-untested")
    assert tombstones._file().exists()
    n = RV.migrate_legacy()
    assert n == 2, f"migrated 2, got {n}"
    assert records.head("100")["action"] == "deleted" and records.head("100")["lineage"] == ["crashed"]
    assert not tombstones._file().exists(), "legacy file retired"
    assert tombstones._file().with_name(tombstones._file().name + ".migrated").exists(), "backup kept"
    assert RV.migrate_legacy() == 0

    ev_wiz = records.make_event("deleted", where="workshop", initiator="wizard_recommended",
                                lineage=["crashed", "fixes_applied", "crashed_post_fix", "recommended_trash"],
                                machine={"verdict": "crashed", "contentHash": "h"},
                                comment="looked broken", when="2026-07-20T00:00:00+00:00")
    line = RV.compose(ev_wiz)
    assert "trashed it on the wizard's recommendation" in line
    assert "it crashed at the bench" in line and "recommended trashing it" in line
    assert "note: looked broken" in line
    assert "Jul 20" in line

    ev_appr = records.make_event("approved", where="workshop", machine={"verdict": "ran", "contentHash": "h"})
    assert "approved it after a clean bench" in RV.compose(ev_appr)
    ev_unt = records.make_event("approved", where="workshop")
    assert "imported it untested" in RV.compose(ev_unt)
    assert RV.compose({}) == "", "empty event -> empty line"

    records.append("300", records.make_event("approved", where="workshop"))
    tw = RV.tombstoned_wids()
    assert set(tw) == {"100", "200"} and "300" not in tw, "only deleted-head items are tombstoned"
    s = RV.summary("100")
    assert s["wid"] == "100" and s["count"] == 1 and s["line"]
    records.append("100", records.make_event("bypassed", where="workshop"))
    records.append("100", records.make_event("deleted", where="workshop", initiator="human"))
    tl = RV.timeline("100")
    assert len(tl) == 3 and tl[0]["action"] == "deleted" and tl[-1]["action"] == "deleted"
    assert "100" in RV.tombstoned_wids(), "head is deleted again -> still tombstoned"

    tags.set_state("200", "Other", "bad")
    assert RV.purge_and_ungate("200") is True
    assert records.has_record("200") is False, "record purged"
    assert "200" not in RV.tombstoned_wids()
    import csv
    rows = list(csv.DictReader(open(paths.tags_file(), encoding="utf-8"))) if paths.tags_file().exists() else []
    assert not any(r.get("id") == "200" for r in rows), "tags row dropped -> re-import un-gated"

    print("OK test_records_view - migration (backup+retire+idempotent), composer neutral facts, "
          "tombstoned_wids/summary/timeline, purge_and_ungate")
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
