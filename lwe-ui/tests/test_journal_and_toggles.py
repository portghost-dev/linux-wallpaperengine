"""Three things covered here, none of them previously tested.

  1. Journal follower: the console follows the engine SERVICE's journal beside the exhibit
     streams, tagged with its own source so the picker can separate it. The follower must
     be reaped by handle on shutdown - never by name, since `journalctl` is a shared
     binary name.
  2. Presence-only escape hatches: the engine tests some switches with
     `getenv(...) != nullptr`, so assigning "0" to turn one OFF turns it ON. Those are
     removed from the environment instead of assigned.
  3. Measured frame rate: status() reports frames/second derived from the engine's
     CUMULATIVE frame counter, and reports NOTHING until it has a baseline.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software \
     python3 tests/test_journal_and_toggles.py
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def test_toggle_grammar() -> None:
    """Every switch resolves to a value or an unset on each side, never an empty string."""
    from lwe_ui.dev import FEATURE_TOGGLES, TRAIL_MODES

    for t in FEATURE_TOGGLES:
        for side in ("on", "off"):
            v = t[side]
            assert v is None or (isinstance(v, str) and v != ""), \
                f"{t['env']}: {side} must be a value to assign or None to unset"
        assert isinstance(t["default_on"], bool)
    assert TRAIL_MODES[0] == "Fluid", "the trail default ships as Fluid until ruled otherwise"
    _cross_check_engine_grammar(FEATURE_TOGGLES)


def _cross_check_engine_grammar(toggles) -> None:
    """Each off value must be the value the engine read actually compares against. Runs only
    when the engine tree sits beside the panel (the publication layout)."""
    import re
    root = Path(__file__).resolve().parent.parent.parent / "src" / "WallpaperEngine"
    if not root.is_dir():
        print("   (skipped grammar cross-check: no engine tree beside lwe-ui)")
        return
    reads: dict[str, list[str]] = {}
    files: dict[str, list[str]] = {}
    for f in root.rglob("*.cpp"):
        if "Testing" in f.parts:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for i, line in enumerate(lines):
            for env in re.findall(r'getenv *\( *"(LWE_[A-Z0-9_]+)"', line):
                reads.setdefault(env, []).append(" ".join(x.strip() for x in lines[i:i + 4]))
                files.setdefault(env, []).append(text)
    for t in toggles:
        lines = reads.get(t["env"])
        assert lines, f"{t['env']} is not read anywhere in the engine"
        joined = " ".join(lines)
        presence = "nullptr" in joined and not re.search(r'"(0|1|ccw|exact)"|\[0\] *==', joined)
        numeric = re.search(r"\bato[fi] *\(", joined) is not None
        if presence:
            assert None in (t["on"], t["off"]) and "1" in (t["on"], t["off"]), \
                f"{t['env']} is presence tested: one side must be unset, the other any value"
        elif numeric:
            # a number is parsed, so the value is compared in the file that consumes it
            value = next((v for v in (t["on"], t["off"]) if v is not None), None)
            assert value is not None and any(
                re.search(rf"(<=|==|<|>=|>) *{re.escape(value)}(\.0+f?)?\b", text)
                for text in files[t["env"]]), \
                f"{t['env']}: value {value!r} is never compared where the engine consumes it"
        else:
            value = next((v for v in (t["on"], t["off"]) if v is not None), None)
            assert value is not None and (f'"{value}"' in joined or f"'{value}'" in joined
                                          or (value == "0" and "'0'" in joined)), \
                f"{t['env']}: off value {value!r} does not appear in its engine read: {lines}"


def test_unset_env_partition(dev) -> None:
    """A flipped-off switch lands in exactly one of assign / unset, never both, per side.

    Uses a SYNTHETIC presence-only toggle so the unset path stays guarded even if every
    shipped presence switch is one day removed.
    """
    from lwe_ui import dev as devmod
    synthetic = {"key": "_synthetic_presence", "label": "Synthetic", "env": "LWE_SYNTHETIC_PRESENCE",
                 "on": "1", "off": None, "default_on": False, "tip": "test fixture", "cite": ""}
    devmod.FEATURE_TOGGLES.append(synthetic)
    try:
        _unset_env_partition_body(dev)
    finally:
        devmod.FEATURE_TOGGLES.remove(synthetic)


def _unset_env_partition_body(dev) -> None:
    dev.setToggle("A", "_synthetic_presence", False)
    dev.setToggle("A", "frontface", False)

    env, unset = dev.compose_env("A")
    assert "LWE_SYNTHETIC_PRESENCE" not in env, "a presence-only off must never be assigned"
    assert "LWE_SYNTHETIC_PRESENCE" in unset
    assert env.get("LWE_FRONTFACE") == "ccw"
    assert "LWE_FRONTFACE" not in unset
    assert not (set(env) & set(unset)), "no key may be both assigned and unset"

    preview = dev.launchPreview("A")
    assert "-u LWE_SYNTHETIC_PRESENCE" in preview, "an unset must be visible in the launch preview"
    assert preview.startswith("env "), "an `-u` prefix is only valid shell after `env`"

    dev.setEnvText("A", "LWE_SYNTHETIC_PRESENCE=1")
    _env, unset = dev.compose_env("A")
    assert "LWE_SYNTHETIC_PRESENCE" not in unset, "an explicit raw env line must win over the toggle's unset"
    dev.setEnvText("A", "")

    dev.setToggle("A", "_synthetic_presence", True)
    env, _unset = dev.compose_env("A")
    assert env.get("LWE_SYNTHETIC_PRESENCE") == "1"

    envb, unsetb = dev.compose_env("B")
    assert "LWE_FRONTFACE" not in envb and "LWE_SYNTHETIC_PRESENCE" in unsetb, \
        "side B never inherits side A's flips"


def test_stderr_is_never_filtered(dev) -> None:
    """Engine diagnostics reach the console regardless of wording, marked as stderr."""
    seen: list[tuple] = []
    dev.consoleLines.connect(lambda ents, _n: seen.extend((e["src"], e["text"], e["err"]) for e in ents))

    real = [
        "Could not parse puppet models/tree.mdl: not an MDLV container",
        "Loaded puppet models/tree.mdl vertices=812 indices=2000 bones=12 clips=2 layers=1",
        "Puppet bone 3 references forward parent 5",
        "Skipping playlist with no name",
    ]

    class _FakeProc:
        def readAllStandardError(self):
            return ("\n".join(real) + "\n").encode()

    dev.slots["A"].proc = _FakeProc()
    try:
        dev._drain("A", True)
    finally:
        dev.slots["A"].proc = None

    for line in real:
        assert ("A", line, True) in seen, f"stderr must pass through unfiltered and marked: {line}"
    tail = dev.slots["A"].buf[-4:]
    assert [t for t, _e, _ts in tail] == real and all(e for _t, e, _ts in tail), \
        "the slot buffer keeps stderr for the tail, marked as stderr"
    assert all(len(ts) == 8 for _t, _e, ts in tail), "exhibit lines are stamped HH:MM:SS at receipt"


def test_clamp_rows_exclude_each_other(dev) -> None:
    """The two clamp rows are one choice per side: turning one on turns the other off, and
    the environment carries exactly the state chosen. Both off leaves nothing clamped."""
    assert dev.toggleOn("A", "resclamp") is True and dev.toggleOn("A", "resclampfx") is False
    env, unset = dev.compose_env("A")
    assert "LWE_SSFACTOR" in unset and "LWE_CLAMPCOMPOSITES" in unset, "default: the engine clamps on its own"

    dev.setToggle("A", "resclampfx", True)
    assert dev.toggleOn("A", "resclamp") is False, "the plain clamp drops when the effects clamp goes on"
    env, unset = dev.compose_env("A")
    assert env.get("LWE_CLAMPCOMPOSITES") == "0" and "LWE_SSFACTOR" in unset and "LWE_SSFACTOR" not in env, \
        "effects clamp: composites exempt, the scene clamp still on"
    assert dev.toggleOn("B", "resclamp") is True, "side B is untouched"

    dev.setToggle("A", "resclamp", True)
    assert dev.toggleOn("A", "resclampfx") is False, "the effects clamp drops when the plain clamp goes on"
    env, unset = dev.compose_env("A")
    assert "LWE_SSFACTOR" in unset and "LWE_CLAMPCOMPOSITES" in unset

    dev.setToggle("A", "resclamp", False)
    env, unset = dev.compose_env("A")
    assert env.get("LWE_SSFACTOR") == "0" and env.get("LWE_CLAMPCOMPOSITES") == "0", "both off: nothing clamped"


def test_journal_lines_are_tagged(dev) -> None:
    """Daemon journal records share the console tagged D: the message alone shows with the
    journal's own time, the raw line keeps the stamp, host and unit, and priority warning
    and above carries the mark. A split record waits for its other half."""
    import json as _json
    import time as _time
    seen: list[dict] = []
    dev.consoleLines.connect(lambda ents, _n: seen.extend(ents))
    us = 1788973302802117
    rec = {"MESSAGE": "LWE-MODELPASS x", "PRIORITY": "6", "__REALTIME_TIMESTAMP": str(us),
           "_HOSTNAME": "host", "SYSLOG_IDENTIFIER": "linux-wallpaperengine", "_PID": "1"}
    warn = dict(rec, MESSAGE="shader warning", PRIORITY="4")
    raw_bytes = dict(rec, MESSAGE=[76, 87, 69, 255])
    blob = _json.dumps(rec) + "\n" + _json.dumps(warn) + "\n" + _json.dumps(raw_bytes) + "\n"
    head, tail = blob[:len(blob) // 2], blob[len(blob) // 2:]
    chunks = [head.encode(), tail.encode(), b"-- No entries --\n"]

    class _FakeProc:
        def readAllStandardOutput(self):
            return chunks.pop(0)

    dev._journal_proc = _FakeProc()
    try:
        dev._drain_journal()
        n_first = len(seen)
        dev._drain_journal()
        dev._drain_journal()
    finally:
        dev._journal_proc = None
    assert n_first < 3 and len(seen) == 4, (n_first, [e["text"] for e in seen])
    assert dev._journal_partial == "", "no half record lingers once its other half arrives"
    local = _time.strftime("%H:%M:%S", _time.localtime(us / 1e6))
    e0 = seen[0]
    assert (e0["src"], e0["text"], e0["err"], e0["time"]) == ("D", "LWE-MODELPASS x", False, local), e0
    assert e0["raw"].endswith(" host linux-wallpaperengine[1]: LWE-MODELPASS x") and "T" in e0["raw"], e0["raw"]
    assert seen[1]["err"] is True and seen[1]["text"] == "shader warning", "priority 4 carries the mark"
    assert seen[2]["text"].startswith("LWE") and seen[2]["err"] is False, "a byte-list message decodes"
    assert seen[3]["text"] == "-- No entries --" and seen[3]["src"] == "D", "a non-record line passes through"


def test_journal_follower_lifecycle(dev, qwait) -> None:
    """Start, stop. The follower is reaped by handle and leaves nothing behind."""
    if shutil.which("journalctl") is None:
        print("   (skipped follower lifecycle: no journalctl on PATH)")
        return

    assert dev.journalRunning() is False
    dev.setFollowingDaemon(True)
    assert dev.journalRunning() is True, "following must own a live follower"

    proc = dev._journal_proc
    dev.setFollowingDaemon(True)
    assert dev._journal_proc is proc, "following twice must not spawn a second follower"

    qwait(300)
    dev.setFollowingDaemon(False)
    assert dev.journalRunning() is False
    assert dev._journal_proc is None, "the handle is nulled before the reap, never after"
    from PySide6.QtCore import QProcess
    assert proc.state() == QProcess.ProcessState.NotRunning, \
        "the follower process must be dead, not orphaned"

    dev.setFollowingDaemon(False)


def test_journal_flood_keeps_every_line(dev) -> None:
    """A heavy read reaches the buffer whole: the read size is a batch, never a discard. Only
    the session cap drops lines, oldest first, and it says how many."""
    import json as _json
    from lwe_ui.dev import CONSOLE_MAX
    batches: list[tuple[list, int]] = []
    dev.consoleLines.connect(lambda ents, n: batches.append((list(ents), n)))

    def blob(lo, hi):
        return ("\n".join(_json.dumps({"MESSAGE": f"line {i}", "PRIORITY": "6"}) for i in range(lo, hi)) + "\n").encode()

    chunks = [blob(0, 500), blob(500, CONSOLE_MAX + 100)]

    class _FakeProc:
        def readAllStandardOutput(self):
            return chunks.pop(0)

    dev._journal_proc = _FakeProc()
    try:
        dev._drain_journal()
        assert [e["text"] for e in batches[-1][0]] == [f"line {i}" for i in range(500)], \
            "all 500 lines of one read arrive, in order"
        assert batches[-1][1] == 0 and dev.consoleCount == 500
        dev._drain_journal()
    finally:
        dev._journal_proc = None
    assert batches[-1][1] == 100, f"the cap reports the oldest lines it dropped: {batches[-1][1]}"
    held = dev.consoleEntries()
    assert len(held) == CONSOLE_MAX and held[0]["text"] == "line 100" and held[-1]["text"] == f"line {CONSOLE_MAX + 99}"


def test_shutdown_reaps_the_follower(dev) -> None:
    """The follower is a child of the panel and must not outlive it."""
    if shutil.which("journalctl") is None:
        print("   (skipped shutdown reap: no journalctl on PATH)")
        return
    dev.setFollowingDaemon(True)
    proc = dev._journal_proc
    dev.shutdown()
    from PySide6.QtCore import QProcess
    assert dev._journal_proc is None, "shutdown must stop the journal follower"
    assert proc.state() == QProcess.ProcessState.NotRunning


def test_measured_fps_needs_a_baseline(backend) -> None:
    """A cumulative counter cannot yield a rate on first sight - and must not pretend to.

    This drives the REAL Backend.status(), not a transcription of its arithmetic: a test
    that re-implements the code it checks passes even when the shipped path is wrong.
    """
    from lwe_ui import api_client, models

    payload = {"pid": 4242, "fps": 30, "frames": 1000, "current": {}, "rotation": {}}
    real_load, real_status, real_ping = (models.settings.load,
                                         api_client.status, api_client.ping)
    try:
        models.settings.load = lambda: {}
        api_client.available = lambda: True
        api_client.ping = lambda: None
        api_client.status = lambda: dict(payload)

        first = backend.status()
        assert "fps" not in first, "the first poll has no baseline and must report no rate"
        assert first["fps_cap"] == 30, "the cap is known at once - a cap is not a rate"

        payload["frames"] = 1060
        second = backend.status()
        assert "fps" in second and second["fps"] > 0, "a second sample yields the rate"

        # a pid change means a fresh engine whose frame counter restarted at zero
        payload.update(pid=9999, frames=5)
        third = backend.status()
        assert "fps" not in third, "a restarted engine invalidates the baseline"

        # a counter that ran BACKWARDS on one pid is not a negative frame rate
        stamp, _, pid = backend._frames_last
        backend._frames_last = (stamp, 10_000, pid)
        payload["frames"] = 5
        assert "fps" not in backend.status(), "a counter running backwards reports nothing"
    finally:
        models.settings.load, api_client.status, api_client.ping = (
            real_load, real_status, real_ping)


def main() -> None:
    home = tempfile.mkdtemp(prefix="lwe-journal-")
    orig = {k: os.environ.get(k)
            for k in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
                      "LWE_SPECFIX")}
    try:
        os.environ["HOME"] = home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(home, "c")
        os.environ["XDG_STATE_HOME"] = os.path.join(home, "s")
        os.environ["XDG_DATA_HOME"] = os.path.join(home, "d")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")
        os.environ.pop("LWE_SPECFIX", None)

        from PySide6.QtGui import QGuiApplication
        from PySide6.QtTest import QTest
        from lwe_ui.dev import DevBridge
        from lwe_ui.storage import paths, settings

        paths.ensure_dirs()
        settings.ensure_exists()
        app = QGuiApplication.instance() or QGuiApplication(["t"])

        def qwait(ms):
            QTest.qWait(ms)
            app.processEvents()

        test_toggle_grammar()
        test_unset_env_partition(DevBridge())
        test_stderr_is_never_filtered(DevBridge())
        test_clamp_rows_exclude_each_other(DevBridge())
        test_journal_lines_are_tagged(DevBridge())
        test_journal_follower_lifecycle(DevBridge(), qwait)
        test_journal_flood_keeps_every_line(DevBridge())
        test_shutdown_reaps_the_follower(DevBridge())

        from lwe_ui.models import Backend
        test_measured_fps_needs_a_baseline(Backend())

        print("OK test_journal_and_toggles - journal follows the service tagged D and is reaped "
              "by handle; presence-only switches turn off by unset per side; "
              "measured fps waits for a baseline")
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
