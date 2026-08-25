"""Wizard backend logic (W2): the static pre-bench census and the mapping from a bench outcome +
human action to a record event. PURE - no launch, no Qt. The live QProcess orchestrator (engine
launch + LWE-PRESENT feed + the 15s timer + standdown wiring) lands in W3 with the QML
flow it drives; here we build and test the DECISIONS it makes.

Constitution ties: approvals are always initiator "human" (F3); only a wizard-recommended DELETION
is initiator "wizard_recommended" (C4); a cancel-after-finding is initiator "human" with the wizard's
recommendation carried in the lineage (the human drove the cancel). Every builder returns a validated
records.make_event dict, so a malformed event can never be emitted.
"""
from __future__ import annotations

from pathlib import Path

from . import paths, records
from ..discovery import project as _project


def _read_deps(proj: dict) -> list[str]:
    """Workshop dependency ids from project.json (raw.dependency; a string or a tolerated list).
    Inlined (not imported from importer) to keep the census decoupled from importer internals."""
    raw = proj.get("raw") or {}
    dep = raw.get("dependency")
    if not dep:
        return []
    deps = dep if isinstance(dep, list) else [dep]
    return [str(d) for d in deps if paths.is_safe_wid(str(d))]


def _dep_present(dep_wid: str, item_dir: Path, workshop_dir) -> bool:
    """A dependency base is present if its dir exists in the workshop dir, or beside the item."""
    if workshop_dir and (Path(workshop_dir) / dep_wid).is_dir():
        return True
    return (item_dir.parent / dep_wid).is_dir()


def run_census(item_dir, workshop_dir=None) -> dict:
    """Static pre-bench census (NO launch): the wallpaper type + a dependency-presence check (16e).
    NO VRAM forecast is surfaced (F5: static size != runtime cost; a scary pre-number is the
    pessimism trap - VRAM is only ever spoken at the bench). Returns:
      { ok, wpType, missingDep, depWid }
    ok=False with missingDep=True means the base item is absent - route to the dep modal, do NOT
    bench a scene that structurally cannot render.

    This is CHEAP by design (reads only project.json): the content hash is NOT computed here so the
    P1->Run click is instant. The hash (a full-dir stat walk, seconds on a 4K scene) is deferred to
    bench time - it is only stamped for future recall, never on the critical path."""
    d = Path(item_dir)
    proj = _project.read(str(d)) if d.is_dir() else {"raw": {}, "type": ""}
    missing = [dep for dep in _read_deps(proj) if not _dep_present(dep, d, workshop_dir)]
    return {
        "ok": not missing,
        "wpType": str(proj.get("type") or ""),
        "missingDep": bool(missing),
        "depWid": missing[0] if missing else "",
    }


def _machine(verdict: str, content_hash: str, tests_failed=None, repair_attempts=None) -> dict:
    return {
        "verdict": verdict,
        "contentHash": str(content_hash or ""),
        "testsFailed": list(tests_failed or []),
        "repairAttempts": list(repair_attempts or []),
    }


def approved_via_wizard(*, content_hash: str, where: str = "workshop", comment=None) -> dict:
    """The bench ran clean and the human approved. initiator human (the wizard never recommends
    approval); machine present because a bench happened (verdict "ran")."""
    return records.make_event("approved", where=where, initiator="human",
                              machine=_machine("ran", content_hash), comment=comment)


def approved_untested(*, where: str = "workshop", comment=None) -> dict:
    """Import Untested (the P1 power-user door): no bench, machine null."""
    return records.make_event("approved", where=where, initiator="human", comment=comment)


def deleted_wizard_recommended(*, lineage, verdict: str = "crashed", content_hash: str,
                               tests_failed=None, repair_attempts=None,
                               where: str = "workshop", comment=None) -> dict:
    """The wizard recommended trash (crash / post-fix crash) and the human confirmed. initiator
    wizard_recommended; the crash trail lives in the lineage + machine half."""
    return records.make_event("deleted", where=where, initiator="wizard_recommended",
                              lineage=lineage,
                              machine=_machine(verdict, content_hash, tests_failed, repair_attempts),
                              comment=comment)


def deleted_by_user(*, where: str = "workshop", comment=None) -> dict:
    """A human-initiated trash with no bench (the hover-trash untested door). machine null."""
    return records.make_event("deleted", where=where, initiator="human", comment=comment)


def benched_no_decision(*, lineage, verdict: str = "crashed", content_hash: str,
                        tests_failed=None, repair_attempts=None, where: str = "workshop",
                        comment=None) -> dict:
    """Cancel after a bench that produced a finding. The HUMAN drove the cancel (initiator human);
    the wizard's recommendation is carried in the lineage. Preserves the finding for future recall
    and rests the blame on the user; the item stays pending. Does NOT suppress (F1)."""
    return records.make_event("benched_no_decision", where=where, initiator="human",
                              lineage=lineage,
                              machine=_machine(verdict, content_hash, tests_failed, repair_attempts),
                              comment=comment)


def bypassed(*, where: str = "workshop", comment=None) -> dict:
    """The red 'Import Tombstoned?' override: a deliberate one-shot re-import despite suppression."""
    return records.make_event("bypassed", where=where, initiator="human", comment=comment)
