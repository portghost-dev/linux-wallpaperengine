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
    for f in root.rglob("*.cpp"):
        if "Testing" in f.parts:
            continue
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            for env in re.findall(r'getenv *\( *"(LWE_[A-Z0-9_]+)"', line):
                reads.setdefault(env, []).append(" ".join(x.strip() for x in lines[i:i + 4]))
    for t in toggles:
        lines = reads.get(t["env"])
        assert lines, f"{t['env']} is not read anywhere in the engine"
        joined = " ".join(lines)
        presence = "nullptr" in joined and not re.search(r'"(0|1|ccw|exact)"|\[0\] *==', joined)
        if presence:
            assert None in (t["on"], t["off"]) and "1" in (t["on"], t["off"]), \
                f"{t['env']} is presence tested: one side must be unset, the other any value"
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
    dev.consoleLine.connect(lambda src, line, err: seen.append((src, line, err)))

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
    assert [t for t, _e in tail] == real and all(e for _t, e in tail), \
        "the slot buffer keeps stderr for the tail, marked as stderr"


def test_journal_lines_are_tagged(dev) -> None:
    """Daemon journal lines share the console but carry their own source tag."""
    seen: list[tuple] = []
    dev.consoleLine.connect(lambda src, line, err: seen.append((src, line, err)))

    class _FakeProc:
        def readAllStandardOutput(self):
            return b"Aug 14 08:26:10 host linux-wallpaperengine[1]: LWE-MODELPASS x"

    dev._journal_proc = _FakeProc()
    try:
        dev._drain_journal()
    finally:
        dev._journal_proc = None
    assert seen == [("D", "Aug 14 08:26:10 host linux-wallpaperengine[1]: LWE-MODELPASS x", False)]


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


def test_journal_flood_cap(dev) -> None:
    """A heavy read is truncated and says so, the same guard the exhibit console has."""
    lines: list[str] = []
    dev.consoleLine.connect(lambda src, line, err: lines.append(line))

    class _FakeProc:
        def readAllStandardOutput(self):
            return ("\n".join(f"line {i}" for i in range(500))).encode()

    dev._journal_proc = _FakeProc()
    try:
        dev._drain_journal()
    finally:
        dev._journal_proc = None

    assert len(lines) == dev._EMIT_MAX + 1, \
        f"expected {dev._EMIT_MAX} lines plus one notice, got {len(lines)}"
    assert "lines this read" in lines[0], "the truncation must be stated, not silent"
    assert lines[-1] == "line 499", "the TAIL is what a live monitor must keep"


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
        test_journal_lines_are_tagged(DevBridge())
        test_journal_follower_lifecycle(DevBridge(), qwait)
        test_journal_flood_cap(DevBridge())
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
