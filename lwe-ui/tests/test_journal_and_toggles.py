"""Three things covered here, none of them previously tested.

  1. Journal follower: the log console can follow the engine SERVICE's journal, not just
     bench children the panel spawned. Its lines must arrive on their OWN signal, because
     the per-lens readout splits logLine by scoped instrument tag and a journal line
     carrying an LWE- tag would land in a lens that never ran that instrument. The
     follower must also be reaped by handle on shutdown - never by name, since
     `journalctl` is a shared binary name.
  2. Presence-only escape hatches: the engine tests some switches with
     `getenv(...) != nullptr`, so assigning "0" to turn one OFF turns it ON. Those are
     removed from the environment instead of assigned.
  3. Measured frame rate: status() reports frames/second derived from the engine's
     CUMULATIVE frame counter, and reports NOTHING until it has a baseline.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software \
     python3 tests/test_journal_and_toggles.py
"""
from __future__ import annotations

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


def test_toggle_off_values() -> None:
    """Every off-value must actually turn its switch off in the engine."""
    from lwe_ui.dev import OUR_TOGGLES

    by_key = {t["key"]: t for t in OUR_TOGGLES}

    assert by_key["frontface"]["off"] == "ccw", \
        "LWE_FRONTFACE off must be the exact string the engine compares against"

    import csv as _csv
    import pathlib as _pathlib
    # optional cross-check against a local grammar inventory, if one is present
    grammar_csv = _pathlib.Path(__file__).resolve().parent / "env-switch-grammar.csv"
    if grammar_csv.exists():
        with open(grammar_csv, encoding="utf-8") as fh:
            grammar = {r["switch"]: r["grammar"] for r in _csv.DictReader(fh)}
        for t in OUR_TOGGLES:
            g = grammar.get(t["env"])
            if g == "presence":
                assert t["off"] is None, (
                    f'{t["env"]} is presence-only in the engine, so off must be unset (None), '
                    f'not {t["off"]!r} - assigning any value turns it ON')
            elif g in ("compare", "parse", "filter") and t["off"] is None:
                assert False, (
                    f'{t["env"]} is read as {g}, so unsetting it returns the engine DEFAULT '
                    f'rather than the off state - it needs an explicit off value')
    else:
        print("   (skipped grammar cross-check: env-switch-grammar.csv not generated)")

    for t in OUR_TOGGLES:
        assert t["off"] is None or isinstance(t["off"], str), \
            f"{t['env']}: off must be a string to assign or None to unset"


def test_unset_env_partition(dev) -> None:
    """A flipped-off switch lands in exactly one of assign / unset, never both.

    Uses a SYNTHETIC presence-only toggle. The two real ones (LWE_TINTFIX, LWE_SPECFIX) were
    removed, so nothing shipped exercises the unset path any more - but the
    machinery has to keep working, because the next presence-only switch someone adds will
    silently turn ON if it does not. Testing it with a fixture is the difference between
    dead code and guarded code.
    """
    from lwe_ui import dev as devmod
    synthetic = {"key": "_synthetic_presence", "env": "LWE_SYNTHETIC_PRESENCE", "off": None,
                 "what": "test fixture: presence-only switch", "sys": "Render",
                 "commit": "", "evidence": "", "experimental": False}
    devmod.OUR_TOGGLES.append(synthetic)
    try:
        _unset_env_partition_body(dev)
    finally:
        devmod.OUR_TOGGLES.remove(synthetic)


def _unset_env_partition_body(dev) -> None:
    dev.setFixOn("_synthetic_presence", False)   # presence-only -> unset
    dev.setFixOn("frontface", False)             # value-carrying -> assign

    env = dev.compose_env()
    unset = dev.unset_env()

    assert "LWE_SYNTHETIC_PRESENCE" not in env, "a presence-only off must never be assigned"
    assert "LWE_SYNTHETIC_PRESENCE" in unset
    assert env.get("LWE_FRONTFACE") == "ccw"
    assert "LWE_FRONTFACE" not in unset
    assert not (set(env) & set(unset)), "no key may be both assigned and unset"

    preview = dev.launchPreview()
    assert "-u LWE_SYNTHETIC_PRESENCE" in preview, \
        "an unset must be visible in the launch preview"
    assert preview.startswith("env "), "an `-u` prefix is only valid shell after `env`"

    dev.setEnvLine("LWE_SYNTHETIC_PRESENCE", "1")
    assert "LWE_SYNTHETIC_PRESENCE" not in dev.unset_env(), \
        "an explicit raw env line must win over the toggle's unset"
    dev.removeEnvLine("LWE_SYNTHETIC_PRESENCE")

    dev.setFixOn("_synthetic_presence", True)
    dev.setFixOn("frontface", True)
    assert dev.unset_env() == [], "nothing is unset while every fix is on"


def test_ab_side_honours_unset(dev) -> None:
    """The A/B split must apply the same off/unset rule as the single bench.

    The unset fix landed on the bench launcher and MISSED
    this path, and the failure was silent rather than loud. `off` is None for a presence-only
    switch, and PySide6 coerces None to "" in QProcessEnvironment.insert rather than raising -
    and "" is still a non-NULL pointer to getenv, so the exhibit meant to demonstrate the fix
    OFF ran with it ON. The split then compared fix-on against fix-on and showed no difference:
    a lying toggle inside the tool built to catch lying toggles.
    """
    from PySide6.QtCore import QProcessEnvironment
    from lwe_ui import dev as devmod

    synthetic = {"key": "_synthetic_presence", "env": "LWE_SYNTHETIC_PRESENCE", "off": None,
                 "what": "test fixture: presence-only switch", "sys": "Render",
                 "commit": "", "evidence": "", "experimental": False}
    devmod.OUR_TOGGLES.append(synthetic)
    try:
        _ab_side_unset_body(dev, QProcessEnvironment)
    finally:
        devmod.OUR_TOGGLES.remove(synthetic)


def _ab_side_unset_body(dev, QProcessEnvironment) -> None:
    dev.abReset()
    dev.setABFix("B", "_synthetic_presence", False)   # presence-only -> must be unset
    dev.setABFix("B", "frontface", False)             # value-carrying -> must be assigned

    env = dev._ab_side_env("B")
    unset = dev._ab_side_unset("B")

    assert "LWE_SYNTHETIC_PRESENCE" not in env, "a presence-only off must never be assigned on an A/B side"
    assert "LWE_SYNTHETIC_PRESENCE" in unset, \
        "the A/B side must UNSET a presence-only switch it is flipping off, or the exhibit " \
        "runs with the fix on and the split silently compares fix-on against fix-on"
    assert env.get("LWE_FRONTFACE") == "ccw"
    assert "LWE_FRONTFACE" not in unset
    assert dev._ab_side_unset("A") == [], "the untouched side unsets nothing"

    # the guard that actually matters: None must never reach the child environment, because
    # it arrives as "" and "" reads as PRESENT
    qenv = QProcessEnvironment.systemEnvironment()
    for k, v in env.items():
        qenv.insert(k, v)
    for k in unset:
        qenv.remove(k)
    assert not qenv.contains("LWE_SYNTHETIC_PRESENCE"), \
        'an unset switch must be ABSENT; an empty string still reads as present to getenv'

    dev.setABEnvText("B", "LWE_SYNTHETIC_PRESENCE=1")
    assert "LWE_SYNTHETIC_PRESENCE" not in dev._ab_side_unset("B")
    dev.setABEnvText("B", "")
    dev.abReset()


def test_stderr_is_never_filtered(dev) -> None:
    """Engine diagnostics reach the console regardless of how they are worded.

    The console used to merge the engine's channels and then recover severity by searching
    each line for the word "error". 175 of the engine's 190 sLog.error messages do not
    contain it, so they were dropped silently - including every puppet diagnostic, e.g.
    "Could not parse puppet X: not an MDLV container". An error that never says "error" is
    still an error. stderr is now read on its own and passed through unfiltered.
    """
    seen: list[str] = []
    dev.logLine.connect(seen.append)

    # verbatim engine strings, from CImage.cpp:504/543/550 and PuppetModel.cpp:115
    real = [
        "Could not parse puppet models/tree.mdl: not an MDLV container",
        "Loaded puppet models/tree.mdl vertices=812 indices=2000 bones=12 clips=2 layers=1",
        "Puppet bone 3 references forward parent 5",
        "Skipping playlist with no name",
    ]

    class _FakeProc:
        def readAllStandardError(self):
            return ("\n".join(real)).encode()

    dev._proc = _FakeProc()
    try:
        dev._drain_stderr()
    finally:
        dev._proc = None

    for line in real:
        assert line in seen, (
            f"stderr must pass through unfiltered; the old allowlist dropped this: {line}")

    for line in real:
        old_allowlist = ("LWE-" in line or "LWE_" in line or "error" in line.lower()
                         or line.startswith(("FRAGSRC", "GLSL ")))
        assert not old_allowlist, \
            f"fixture stale: {line!r} would have passed the old filter, so it proves nothing"


def test_journal_signals_are_separate(dev) -> None:
    """Journal lines must not reach the readout's signal - different pane, different tags."""
    seen_log: list[str] = []
    seen_journal: list[str] = []
    dev.logLine.connect(seen_log.append)
    dev.journalLine.connect(seen_journal.append)

    dev.journalLine.emit("Aug 14 08:26:10 myhost linux-wallpaperengine[1]: LWE-MODELPASS x")
    assert seen_journal and not seen_log, \
        "a journal line must never arrive on logLine - the lens readout listens there"


def test_journal_follower_lifecycle(dev, qwait) -> None:
    """Start, receive, stop. The follower is reaped by handle and leaves nothing behind."""
    if shutil.which("journalctl") is None:
        print("   (skipped follower lifecycle: no journalctl on PATH)")
        return

    lines: list[str] = []
    dev.journalLine.connect(lines.append)

    assert dev.journalRunning() is False
    dev.startJournal()
    assert dev.journalRunning() is True, "startJournal must own a live follower"

    proc = dev._journal_proc
    dev.startJournal()
    assert dev._journal_proc is proc, "startJournal must be idempotent, not spawn a second"

    qwait(900)
    assert any("following" in ln for ln in lines), \
        "the follower must announce which unit it attached to"

    dev.stopJournal()
    assert dev.journalRunning() is False
    assert dev._journal_proc is None, "the handle is nulled before the reap, never after"
    from PySide6.QtCore import QProcess
    # PySide6 enums are not ints: `state() == 0` is False even when NotRunning
    assert proc.state() == QProcess.ProcessState.NotRunning, \
        "the follower process must be dead, not orphaned"

    dev.stopJournal()   # must tolerate a second stop


def test_journal_flood_cap(dev) -> None:
    """A heavy read is truncated and says so, the same guard the bench console has."""
    lines: list[str] = []
    dev.journalLine.connect(lines.append)

    class _FakeProc:
        def readAllStandardOutput(self):
            return ("\n".join(f"line {i}" for i in range(500))).encode()

    dev._journal_proc = _FakeProc()
    try:
        dev._drain_journal()
    finally:
        dev._journal_proc = None

    assert len(lines) == dev._JOURNAL_EMIT_MAX + 1, \
        f"expected {dev._JOURNAL_EMIT_MAX} lines plus one notice, got {len(lines)}"
    assert "journal heavy" in lines[0], "the truncation must be stated, not silent"
    assert lines[-1] == "line 499", "the TAIL is what a live monitor must keep"


def test_shutdown_reaps_the_follower(dev) -> None:
    """The follower is a child of the panel and must not outlive it."""
    if shutil.which("journalctl") is None:
        print("   (skipped shutdown reap: no journalctl on PATH)")
        return
    dev.startJournal()
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

        test_toggle_off_values()
        test_unset_env_partition(DevBridge())
        test_ab_side_honours_unset(DevBridge())
        test_stderr_is_never_filtered(DevBridge())
        test_journal_signals_are_separate(DevBridge())
        test_journal_follower_lifecycle(DevBridge(), qwait)
        test_journal_flood_cap(DevBridge())
        test_shutdown_reaps_the_follower(DevBridge())

        from lwe_ui.models import Backend
        test_measured_fps_needs_a_baseline(Backend())

        print("OK test_journal_and_toggles - journal follows the service on its own signal "
              "and is reaped by handle; presence-only switches turn off by unset; "
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
