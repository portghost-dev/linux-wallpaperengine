"""Self-tests for storage/records.py - the per-wid append-only item RECORD store.

Isolation: HOME + all XDG_* point at a fresh tempfile tree, so paths.record_file() resolves under
it and the live ~/.local/state/lwe is NEVER touched. Runs under pytest if present, otherwise as a
plain `python3 tests/test_records.py`.

Each block states the PREDICTION it checks. The store is the
audit trail the whole wizard hangs off, so the edge cases (malformed line, unsafe wid, suppression
semantics, the make_event invariants) are guarded here, not discovered later.
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
    return paths, records


def _expect_raises(fn, *a, **k):
    try:
        fn(*a, **k)
    except ValueError:
        return True
    return False


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="lwe-records-")
    orig = {k: os.environ.get(k) for k in _fresh_env(tmp)}
    try:
        os.environ.update(_fresh_env(tmp))
        paths, R = _reload()
        paths.ensure_dirs()
        assert paths.records_dir().is_dir(), "ensure_dirs must create records/"

        wid = "2105138680"

        assert _expect_raises(R.make_event, "approved", where="workshop", initiator="wizard_recommended")
        assert _expect_raises(R.make_event, "bypassed", where="workshop", initiator="wizard_recommended")
        assert _expect_raises(R.make_event, "nope", where="workshop")
        assert _expect_raises(R.make_event, "approved", where="nowhere")
        ev_del = R.make_event("deleted", where="workshop", initiator="wizard_recommended",
                              lineage=["crashed", "fixes_applied", "crashed_post_fix"],
                              machine={"verdict": "crashed", "contentHash": "abc",
                                       "testsFailed": ["launch"], "repairAttempts": ["trailing_comma"]},
                              comment="died even after the fix")
        assert ev_del["initiator"] == "wizard_recommended" and ev_del["machine"]["verdict"] == "crashed"
        assert _expect_raises(R.make_event, "deleted", where="workshop", machine={"verdict": "meh"})

        ev_appr = R.make_event("approved", where="workshop", comment="looked great",
                               machine={"verdict": "ran", "contentHash": "abc"}, when="2026-07-20T00:00:00+00:00")
        assert R.append(wid, ev_appr) is True
        assert R.append(wid, ev_del) is True
        evs = R.read(wid)
        assert len(evs) == 2 and evs[0]["action"] == "approved" and evs[1]["action"] == "deleted"
        assert R.head(wid)["action"] == "deleted", "head is the last-appended event"
        assert R.has_record(wid) and R.list_wids() == [wid]

        with open(paths.record_file(wid), "a", encoding="utf-8") as f:
            f.write('{"action": "deleted", "when": "trunca')
        evs2 = R.read(wid)
        assert len(evs2) == 2, f"malformed trailing line must be skipped, got {len(evs2)}"

        assert R.is_suppressed(wid, present_on_disk=True) is True
        assert R.is_suppressed(wid, present_on_disk=False) is False, "unsubscribed can't re-import anyway"
        w2 = "3602874264"
        R.append(w2, R.make_event("benched_no_decision", where="workshop",
                                  lineage=["crashed"], machine={"verdict": "crashed", "contentHash": "z"}))
        assert R.is_suppressed(w2, present_on_disk=True) is False, "benched_no_decision must not suppress"
        w3 = "111"
        R.append(w3, R.make_event("approved", where="library"))
        assert R.is_suppressed(w3, present_on_disk=True) is False
        assert R.is_suppressed("999999", present_on_disk=True) is False

        assert R.purge(wid) is True
        assert R.has_record(wid) is False
        assert R.is_suppressed(wid, present_on_disk=True) is False
        assert R.purge(wid) is False, "purging a gone file is a no-op False"

        for bad in ("../etc", "a/b", "..", "", "a*b"):
            assert R.append(bad, R.make_event("deleted", where="library")) is False, f"unsafe wid {bad!r} wrote"
            assert R.read(bad) == []

        item = Path(tmp) / "item"
        item.mkdir()
        (item / "project.json").write_text('{"type":"scene"}', encoding="utf-8")
        (item / "scene.pkg").write_bytes(b"AAAA")
        h1 = R.content_hash(item)
        assert h1 == R.content_hash(item), "content_hash must be stable across calls (no change)"
        (item / "scene.pkg").write_bytes(b"AAAABBBB")
        assert R.content_hash(item) != h1, "a changed asset must change the hash"
        h2 = R.content_hash(item)
        (item / "project.json").write_text('{"type":"video"}', encoding="utf-8")
        assert R.content_hash(item) != h2, "a changed project.json must change the hash"

        assert R.append("222", {"action": "deleted", "where": "library", "when": "x"}) is True
        assert R.read("222")[0]["action"] == "deleted"

        R.append("333", R.make_event("deleted", where="library", comment="line one\nline two"))
        phys = paths.record_file("333").read_text(encoding="utf-8").split("\n")
        phys = [ln for ln in phys if ln.strip()]
        assert len(phys) == 1, "a multi-line comment must stay one physical JSONL line"
        assert R.read("333")[0]["comment"] == "line one\nline two", "newline preserved, not collapsed"
        sep_comment = "para one" + chr(0x2028) + "para two"
        R.append("444", R.make_event("deleted", where="library", comment=sep_comment))
        phys2 = [ln for ln in paths.record_file("444").read_text(encoding="utf-8").split("\n") if ln.strip()]
        assert len(phys2) == 1, "U+2028 in a comment must stay one physical line (A1)"
        assert R.read("444")[0]["comment"] == sep_comment, "U+2028 round-trips intact"
        assert paths.record_file("444").read_bytes().isascii(), "record file must be pure ASCII (A1)"

        print("OK test_records - schema/append/read/head/list/suppression/purge/unsafe-wid/"
              "content-hash/newline/U+2028 all hold")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
