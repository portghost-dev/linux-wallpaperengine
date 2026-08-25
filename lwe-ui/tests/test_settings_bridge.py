"""Settings bridge: store access, validation, reach, write order, verbs.

Two blocks of the previous file asserted a world this build retires, and both are recorded
here rather than silently dropped:

  RETIRED - the old watcher-based argv contract (old lines 83-118). It sourced watcher/lwe-wallpaper
  and asserted that the engine defaults reach the binary as --fps / --scaling / --layer /
  LWE_HWDEC / LWE_TEXCOMP argv. The watcher is retired now that the daemon runs the engine
  (ENGINE_DAEMON=true), the engine's launch shape now comes from the GENERATED env file, and
  no test should assert that contract any more. What it protected - those keys really
  reaching the engine - is asserted against the real mechanism instead, in
  `_test_service_restart_keys_regenerate_the_env_file` below and in test_daemon_unit.py.

  RETIRED - the note claiming setSetting does not emit settingsChanged. It did emit; the
  claim was wrong when it was written (this same file disproved it four lines later) and the
  live-refresh law S8 now depends on the emission, so it is asserted, not annotated.

Sandboxes HOME/XDG before importing lwe_ui and drives the real Backend offscreen.
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lwe-settings-test-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from lwe_ui import api_client  # noqa: E402
from lwe_ui import constants as C  # noqa: E402
from lwe_ui.storage import paths, settings  # noqa: E402
from lwe_ui.engine import daemon_unit  # noqa: E402

# hermetic systemd: regenerate commits call daemon-reload; never touch the host bus
import types as _types
daemon_unit.subprocess.run = lambda argv, **kw: _types.SimpleNamespace(
    returncode=0, stderr="", stdout="")


def _test_store_round_trips(b) -> None:
    b.setSetting("ENGINE_FPS", "45")
    assert str(b.getSetting("ENGINE_FPS")) == "45"
    b.setSetting("ENGINE_LAYER", "top")
    assert settings.load()["ENGINE_LAYER"] == "top"

    # unknown key is a no-op, never raises
    b.setSetting("NONEXISTENT_KEY", "x")
    assert "NONEXISTENT_KEY" not in settings.load()

    # file:// urls from folder pickers get localized
    b.setSetting("WORKSHOP_DIR", "file:///tmp/some/workshop")
    assert settings.load()["WORKSHOP_DIR"] == "/tmp/some/workshop"

    # S8's precondition: a write anywhere emits, so every bound page live-refreshes
    hits = {"n": 0}
    b.settingsChanged.connect(lambda: hits.__setitem__("n", hits["n"] + 1))
    b.setSetting("ENGINE_FPS", 30)
    assert hits["n"] == 1, "setSetting must emit settingsChanged exactly once"
    print("OK store round-trips + settingsChanged emission (S8 precondition)")


def _test_cut_keys_are_gone_and_load_without_error() -> None:
    """T21: all ELEVEN cut keys are absent from the schema, and a settings.conf carrying
    them loads without error and drops them on the next save (no migration script)."""
    cut = ["TRANSITION", "AVOID_REPEAT", "MONITOR_MODE", "POLL", "MEMCAP_MB", "ACTIVE_CHECK",
           "PAUSE_ON_BATTERY", "UI_MODE", "NOTIFY", "SHOW_TRAY", "LOG_LEVEL"]
    assert len(cut) == 11
    for key in cut:
        assert key not in C.SETTINGS_SCHEMA, f"{key} must leave SETTINGS_SCHEMA"

    for key in ("PAUSE_ON_LOCK", "PAUSE_RECOVERY_CONDITION", "PAUSE_RECOVERY_ACTION"):
        assert key in C.SETTINGS_SCHEMA, f"{key} loses its row, not its key"

    stale = paths.settings_file()
    stale.write_text(stale.read_text(encoding="utf-8")
                     + "\n" + "\n".join(f"{k}=x" for k in cut) + "\n", encoding="utf-8")
    loaded = settings.load()
    for key in cut:
        assert key not in loaded, f"{key} must be ignored on load"
    settings.save(loaded)
    written = paths.settings_file().read_text(encoding="utf-8")
    for key in cut:
        assert f"{key}=" not in written, f"{key} must be dropped on the next save"
    print("OK the eleven cut keys leave the schema and drop themselves from an old file")


def _test_new_keys_exist() -> None:
    for key in ("STEAM_DIR", "ENGINE_AUDIO_GAIN", "ENGINE_CLASSIC_K", "ENGINE_CLASSIC_EXP"):
        assert key in C.SETTINGS_SCHEMA, f"{key} must be in the schema"
    assert str(paths.default_settings()["STEAM_DIR"]).endswith("Steam"), \
        "STEAM_DIR resolves to a detected default, like every other path key"
    print("OK new keys STEAM_DIR + the three dial keys, with resolved defaults")


def _test_reset_preserves_the_path_keys(b) -> None:
    """T17: resetting the path keys would relocate the library - a reset must carry them."""
    cur = settings.load()
    # a configured engine path is only honored when the file is there
    engine_bin = str(Path(_TMP) / "data-engine")
    open(engine_bin, "w").close()
    cur.update({"WALLPAPERS_DIR": "/data/walls", "WORKSHOP_DIR": "/data/ws",
                "ASSETS_DIR": "/data/assets", "STEAM_DIR": "/data/steam",
                "ENGINE_BIN": engine_bin, "ENGINE_LAYER": "top",
                "ENGINE_VOLUME": 77})
    settings.save(cur)

    assert b.resetConfig() is True
    after = settings.load()
    for key, want in (("WALLPAPERS_DIR", "/data/walls"), ("WORKSHOP_DIR", "/data/ws"),
                      ("ASSETS_DIR", "/data/assets"), ("STEAM_DIR", "/data/steam"),
                      ("ENGINE_BIN", engine_bin)):
        assert after[key] == want, f"{key} is a resolved path; resetting it is data loss"
    assert after["ENGINE_LAYER"] == "bottom"
    assert after["ENGINE_VOLUME"] == C.SETTINGS_SCHEMA["ENGINE_VOLUME"]["default"]
    print("OK Reset preserves the two era keys + the five path keys, resets the rest")


def _test_export_import_round_trip(b) -> None:
    backup_root = Path(_TMP) / "backups"
    backup_root.mkdir(exist_ok=True)
    settings.save({**settings.load(), "ENGINE_LAYER": "top"})
    assert b.exportConfig(str(backup_root)) is True
    backup = sorted(backup_root.glob("lwe-backup-*"))[-1]
    settings.save({**settings.load(), "ENGINE_LAYER": "bottom"})
    assert b.importConfig(str(backup)) is True
    assert settings.load()["ENGINE_LAYER"] == "top"
    print("OK export / import round-trip")


def _test_autostart(b) -> None:
    b.setAutostart(True)
    assert b.getAutostart() is True
    b.setAutostart(False)
    assert b.getAutostart() is False
    print("OK autostart desktop entry create / remove")


def _test_bridge_validates_instead_of_clamping(sb) -> None:
    """T8: an out-of-range value is REJECTED with failure grammar, never silently clamped.

    storage.settings._validate still clamps as the last-resort guard, which is exactly why
    the bridge has to reject first: a clamp that reaches the store shows the user a number
    they did not choose and calls it success.
    """
    fails = []
    sb.commitFailed.connect(lambda keys, reason: fails.append((list(keys), reason)))

    before = settings.load()["ENGINE_VOLUME"]
    assert sb.commit("ENGINE_VOLUME", 999999) is False
    assert settings.load()["ENGINE_VOLUME"] == before, "a rejected value must not be written"
    assert fails and fails[-1][0] == ["ENGINE_VOLUME"], fails

    assert sb.commit("ENGINE_SCALING", "not-a-mode") is False
    assert sb.commit("DETECT_INTERVAL_SEC", "abc") is False
    assert sb.commit("ENGINE_VOLUME", 42) is True
    assert settings.load()["ENGINE_VOLUME"] == 42
    print("OK the bridge rejects out-of-range and bad-choice values, never clamps silently")


def _test_schedule_packing_is_validated(sb) -> None:
    """T29's validation half: HH:MM 24-hour, or a rejection - never a coerced time."""
    assert sb.commit("SCHEDULE", "08:00=day;20:00=night") is True
    assert settings.load()["SCHEDULE"] == "08:00=day;20:00=night"
    for bad in ("25:00=day", "8:00=day", "08:60=day", "0800=day", "08:00="):
        assert sb.commit("SCHEDULE", bad) is False, f"{bad} must be rejected"
    assert settings.load()["SCHEDULE"] == "08:00=day;20:00=night", "the store is unchanged"
    print("OK schedule packing validates HH:MM 24-hour and rejects the rest")


def _test_reach_is_derived_not_prose(sb, b) -> None:
    """T28: reach() answers the sec 1.1 class, and it answers it from the SAME tuple the
    push path consumes - so a verb landing later flips the answer with no UI change."""
    for key in ("ENGINE_TIMESCALE", "ENGINE_VOLUME", "AUDIO_REACTIVE_DEFAULT",
                "MOUSE_DEFAULT", "PARALLAX_DEFAULT", "PARTICLES_DEFAULT", "ENGINE_FPS"):
        assert sb.reach(key) == "LIVE", key
    for key in ("ENGINE_LAYER", "ENGINE_HWDEC", "ENGINE_TEXCOMP", "ASSETS_DIR"):
        assert sb.reach(key) == "SERVICE-RESTART", key
    for key in ("ENGINE_SCALING", "ENGINE_CLAMP", "AUTOMUTE_DEFAULT"):
        assert sb.reach(key) == "NEXT-SHOW", key
    assert sb.reach("CLOSE_TO_TRAY") == "PANEL"
    assert sb.reach("SCHEDULE") == "BOUNDARY"
    for key in C.AUDIO_DIAL_ENV:
        assert sb.reach(key) == "LIVE", key

    saved = b._LIVE_GLOBAL_KEYS
    try:
        type(b)._LIVE_GLOBAL_KEYS = tuple(k for k in saved if k != "PARALLAX_DEFAULT")
        assert sb.reach("PARALLAX_DEFAULT") != "LIVE", \
            "reach must read the push tuple, not a second copy of the truth"
    finally:
        type(b)._LIVE_GLOBAL_KEYS = saved
    print("OK reach() returns the sec 1.1 class and derives LIVE from the push tuple")


def _test_verb_first_persist_on_confirmation(sb) -> None:
    """T7 / sec 6.3 [L-22 H-5]: a LIVE key pushes its verb FIRST and persists only on
    confirmation. A rejected verb writes NOTHING, so the control re-reads a value that is
    still really in force rather than one the engine never accepted."""
    settings.save({**settings.load(), "ENGINE_VOLUME": 20})
    saved = (api_client.available, api_client.set_volume)
    try:
        api_client.available = lambda: True
        api_client.set_volume = lambda v: {"ok": False}
        assert sb.commit("ENGINE_VOLUME", 55) is False
        assert settings.load()["ENGINE_VOLUME"] == 20, "a rejected verb persists nothing"

        api_client.set_volume = lambda v: {"ok": True}
        assert sb.commit("ENGINE_VOLUME", 55) is True
        assert settings.load()["ENGINE_VOLUME"] == 55
    finally:
        api_client.available, api_client.set_volume = saved
    print("OK verb first, persist on confirmation; a rejected verb persists nothing")


def _test_service_restart_keys_regenerate_the_env_file(sb) -> None:
    """Sec 6.5 + sec 1.2, and the replacement for the retired watcher-argv block.

    These four keys reach the engine ONLY through the generated env file, and until this
    build nothing regenerated it - write_files() had no production caller anywhere in the
    tree, so they wrote settings.conf and reached the engine never. Committing one now
    regenerates, and the regenerated file still carries the audio dial lines (the sequencing
    law: U4 landed before this call site existed).
    """
    env_path = paths.config_dir() / "engine-env"
    if env_path.exists():
        env_path.unlink()
    assert sb.commit("ENGINE_LAYER", "top") is True
    assert env_path.exists(), "a SERVICE-RESTART commit must regenerate the env file"
    text = env_path.read_text(encoding="utf-8")
    assert "--layer top" in text, text
    for env_name in C.AUDIO_DIAL_ENV.values():
        assert env_name + "=" in text, \
            f"{env_name} missing - a regenerate would destroy the audio calibration"
    assert "LWE_NOPAUSEVRAM" not in text, "retired with the engine's pause-VRAM machinery"
    print("OK SERVICE-RESTART keys regenerate the env file, dials intact (sequencing law)")


def _test_dial_seeding_ladder(sb) -> None:
    """P22: live status -> the persisted key -> calibrated. U4 created the middle rung."""
    saved = api_client.status
    try:
        api_client.status = lambda: None
        settings.save({**settings.load(), "ENGINE_AUDIO_GAIN": 9.5})
        dials = {d["key"]: d for d in sb.audioDials()}
        assert abs(dials["ENGINE_AUDIO_GAIN"]["engineValue"] - 9.5) < 1e-6, \
            "with no live status the PERSISTED value seeds the dial, not a source default"
        assert abs(dials["ENGINE_CLASSIC_K"]["engineValue"] - 0.7) < 1e-6, \
            "with neither status nor a stored value, calibrated is the floor"

        api_client.status = lambda: {"audio_gain": 2.0}
        dials = {d["key"]: d for d in sb.audioDials()}
        assert abs(dials["ENGINE_AUDIO_GAIN"]["engineValue"] - 2.0) < 1e-6, \
            "a live engine outranks the store"
    finally:
        api_client.status = saved
    print("OK dial seeding ladder: status -> persisted key -> calibrated")


def _test_exceptions_editor_uses_the_existing_blacklist(sb) -> None:
    """T16 / S-7.6: the store is the EXISTING blacklist file - no new key, no xdg-open."""
    path = paths.config_dir() / "pause-blacklist.txt"
    if path.exists():
        path.unlink()
    assert sb.exceptions() == []
    assert sb.addException("steam") is True
    assert sb.addException("org.mozilla.firefox") is True
    assert sb.exceptions() == ["steam", "org.mozilla.firefox"]
    assert sb.exceptionCount() == 2
    assert sb.addException("steam") is True and sb.exceptionCount() == 2, "idempotent"
    assert sb.removeException("steam") is True
    assert sb.exceptions() == ["org.mozilla.firefox"]
    assert "pause-blacklist.txt" in str(path) and path.exists()
    print("OK exceptions add / remove against the existing blacklist file, no new key")


def _test_system_truth_reads_the_live_unit_file(sb) -> None:
    """T18 / G9: the caps come from the unit file on disk, and a missing file fabricates
    nothing - it reports the unknown state instead of a plausible number."""
    unit_dir = Path(_TMP) / ".config/systemd/user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit = unit_dir / "lwe-engine.service"
    unit.write_text("[Service]\nMemoryHigh=2G\nMemoryMax=3G\n", encoding="utf-8")
    truth = sb.systemTruth()
    assert truth["memoryHigh"] == "2G" and truth["memoryMax"] == "3G", truth

    unit.unlink()
    truth = sb.systemTruth()
    assert truth["memoryHigh"] == "" and truth["memoryMax"] == "", \
        "no unit file means no number, never a fabricated one"
    print("OK system truth parses the live unit file and fabricates nothing when it is gone")


def _test_disk_usage_and_tombstones_are_two_facts(sb) -> None:
    """The old composite `N.N GB - N tombstones` string splits into the two drawn rows."""
    usage = sb.diskUsage()
    assert "GB" in usage and "tombstone" not in usage, usage
    assert isinstance(sb.tombstoneCount(), int)
    print("OK disk usage and tombstone count are two separate facts")


def _test_open_logs_reaches_a_file_manager_never_a_terminal(sb) -> None:
    """DEFECT 3. "a file manager, never a terminal".

    It used to delegate to Backend.openPath, which spawns `xdg-open`. That resolves through
    the user's own mimeapps association for inode/directory, and on this box that lands on a
    terminal emulator - so the verb read correctly in source and did the wrong thing live.

    Two things are asserted, because "it opens the right thing" and "it cannot open the wrong
    thing" are different claims: the handler routes a file:// URL for the LOG DIRECTORY
    through QDesktopServices, and it spawns NO subprocess at all - so there is no place for a
    terminal binary to appear, now or after a future edit.
    """
    import subprocess as _sp
    import sys
    from PySide6.QtGui import QDesktopServices

    opened = []
    spawned = []
    saved_open = QDesktopServices.openUrl
    saved_popen, saved_run = _sp.Popen, _sp.run
    class _DeadInterface:
        def __init__(self, *a, **k): pass
        def isValid(self): return False
    class _DeadDBus:
        QDBusConnection = type("C", (), {"sessionBus": staticmethod(lambda: None)})
        QDBusInterface = _DeadInterface
    saved_dbus = sys.modules.get("PySide6.QtDBus")
    sys.modules["PySide6.QtDBus"] = _DeadDBus
    try:
        QDesktopServices.openUrl = lambda url: opened.append(url.toString()) or True
        _sp.Popen = lambda *a, **k: spawned.append(a) or (_ for _ in ()).throw(
            AssertionError("openLogs must not spawn a process"))
        _sp.run = lambda *a, **k: spawned.append(a) or (_ for _ in ()).throw(
            AssertionError("openLogs must not spawn a process"))

        assert sb.openLogs() is True
    finally:
        QDesktopServices.openUrl = saved_open
        _sp.Popen, _sp.run = saved_popen, saved_run
        if saved_dbus is not None:
            sys.modules["PySide6.QtDBus"] = saved_dbus
        else:
            sys.modules.pop("PySide6.QtDBus", None)

    assert len(opened) == 1, opened
    for url in opened:
        assert url.startswith("file://"), f"must be a file:// URL, got {url!r}"
        assert str(paths.state_dir()) in url, f"must open the LOG DIRECTORY, got {url!r}"
    assert spawned == [], "no subprocess, so no terminal can be reached"

    source = (Path(__file__).resolve().parent.parent
              / "src/lwe_ui/settings_bridge.py").read_text(encoding="utf-8")
    handler = source[source.index("def openLogs"):]
    handler = handler[:handler.index("\n    @Slot")]
    body = handler.split('"""')
    handler = body[0] + ("".join(body[2:]) if len(body) > 2 else "")
    for banned in ("xdg-open", "kitty", "alacritty", "konsole", "gnome-terminal",
                   "x-terminal-emulator", "foot", "wezterm", "subprocess", "Popen"):
        assert banned not in handler, f"openLogs must not mention {banned!r}"
    print("OK DEFECT-3 Open logs routes a file:// dir URL through QDesktopServices, "
          "spawns nothing, and names no terminal")


def _test_schedule_ui_flag_is_off_by_default() -> None:
    """T29's gate half: built and wired, and it does not render."""
    assert C.SCHEDULE_UI is False, \
        "the Schedule section ships gated off - nothing executes a schedule in the daemon era"
    print("OK SCHEDULE_UI gate is False by default, with a named exit")


def main() -> None:
    app = QGuiApplication([])  # noqa: F841
    paths.ensure_dirs()
    settings.ensure_exists()

    from lwe_ui.models import Backend, ImportBridge
    from lwe_ui.settings_bridge import SettingsBridge

    b = Backend()
    sb = SettingsBridge(b, ImportBridge(b))

    _test_store_round_trips(b)
    _test_cut_keys_are_gone_and_load_without_error()
    _test_new_keys_exist()
    _test_export_import_round_trip(b)
    _test_reset_preserves_the_path_keys(b)
    _test_autostart(b)

    _test_bridge_validates_instead_of_clamping(sb)
    _test_schedule_packing_is_validated(sb)
    _test_reach_is_derived_not_prose(sb, b)
    _test_verb_first_persist_on_confirmation(sb)
    _test_service_restart_keys_regenerate_the_env_file(sb)
    _test_dial_seeding_ladder(sb)
    _test_exceptions_editor_uses_the_existing_blacklist(sb)
    _test_system_truth_reads_the_live_unit_file(sb)
    _test_disk_usage_and_tombstones_are_two_facts(sb)
    _test_open_logs_reaches_a_file_manager_never_a_terminal(sb)
    _test_schedule_ui_flag_is_off_by_default()

    print("ALL settings bridge checks pass")


if __name__ == "__main__":
    main()
