"""The state tree: logs per subsystem, panel state under panel/, a one-time migration that
moves the panel's files into place and names the dead ones without deleting them, the panel
log written in real time, and a Developer slot's log appended as its lines arrive."""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket and the host probes before any lwe_ui import)
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-tree-")
    os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
    from lwe_ui.storage import paths

    state = paths.state_dir()
    # --- layout
    assert paths.logs_dir() == state / "logs"
    assert paths.log_dir("engine") == state / "logs" / "engine"
    assert paths.panel_state_dir() == state / "panel"
    assert paths.engine_state_dir() == state / "engine"
    assert paths.probes_dir() == state / "probes"
    assert paths.objindex_dir() == state / "panel" / "objindex"
    assert paths.draft_dir() == state / "panel" / "draft"
    assert paths.records_dir() == state / "panel" / "records"
    assert paths.dev_slots_file() == state / "panel" / "dev-slots.json"
    assert paths.bench_log_file() == state / "logs" / "bench" / "bench.log"
    assert paths.dev_slot_log_file("A") == state / "logs" / "developer" / "A.log"

    # --- migration: the old flat layout, panel files move, engine files stay, dead named
    state.mkdir(parents=True)
    (state / "objindex").mkdir()
    (state / "objindex" / "1.json").write_text("{}")
    (state / "dev-slots.json").write_text('{"version": 1}')
    (state / "wizard-bench.log").write_text("bench\n")
    (state / "engine-state.json").write_text("{}")
    (state / "engine-api.log").write_text("dead\n")
    (state / "show-request").write_text("1\n")
    paths.ensure_dirs()
    report = paths.migrate_state_tree()
    assert (state / "panel" / "objindex" / "1.json").exists(), "objindex moved under panel/"
    assert not (state / "objindex").exists()
    assert (state / "panel" / "dev-slots.json").exists()
    assert (state / "logs" / "bench" / "bench.log").read_text() == "bench\n"
    assert (state / "engine-state.json").exists(), "engine files stay until the engine moves them"
    assert (state / "engine-api.log").exists(), "dead files are named, never deleted"
    assert sorted(report["moved"]) == ["dev-slots.json", "objindex", "wizard-bench.log"], report
    assert report["skipped"] == [], report
    assert "engine-api.log" in report["dead"] and "show-request" in report["dead"], report
    assert paths.migrate_state_tree()["moved"] == [], "a second run moves nothing"
    (state / "propindex").mkdir()
    (state / "propindex" / "old.json").write_text("{}")
    (state / "panel" / "propindex").mkdir(parents=True, exist_ok=True)
    (state / "panel" / "propindex" / "new.json").write_text("{}")
    again = paths.migrate_state_tree()
    assert again["skipped"] == ["propindex"] and (state / "propindex" / "old.json").exists(), \
        "a non-empty destination is named, never merged or overwritten"
    for d in ("engine", "cef", "panel", "bench", "developer"):
        assert (state / "logs" / d).is_dir(), d

    # --- panel log: real file, real time
    from lwe_ui import logbook
    logger = logbook.install("window")
    logger.warning("probe line")
    assert logbook.log_file("window").name == "window.log" and logbook.log_file("tray").name == "tray.log"
    text = logbook.log_file("window").read_text(encoding="utf-8")
    assert "window INFO lwe_ui: panel window started" in text, text
    assert "window WARNING lwe_ui: probe line" in text, text
    from PySide6.QtCore import qWarning
    qWarning("qt probe")
    assert "window WARNING lwe_ui.qt: qt probe" in logbook.log_file("window").read_text(encoding="utf-8")

    # --- developer slot log: header at launch, lines appended as drained, kept after retire
    from lwe_ui import dev
    slot = dev._Slot("A")
    dev.DevBridge._slot_log_open(slot, "window")
    dev.DevBridge._slot_log_write(slot, ["first line", "second"], stderr=False, now="12:00:00")
    dev.DevBridge._slot_log_write(slot, ["bad"], stderr=True, now="12:00:01")
    dev.DevBridge._slot_log_close(slot)
    body = paths.dev_slot_log_file("A").read_text(encoding="utf-8")
    assert body.startswith("=== A window "), body
    assert "12:00:00  first line\n12:00:00  second\n12:00:01 !bad\n" in body, body
    dev.DevBridge._slot_log_open(slot, "bench")
    dev.DevBridge._slot_log_close(slot)
    assert "first line" not in paths.dev_slot_log_file("A").read_text(encoding="utf-8"), \
        "a relaunch truncates the slot log"
    print("OK state tree: layout, migration, panel log, developer slot log")


if __name__ == "__main__":
    main()
