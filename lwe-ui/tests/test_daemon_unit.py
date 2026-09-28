"""Self-verification for the lwe-engine.service writer (cutover artifact).

Pure generation checks - no systemctl, no hyprctl (outputs injected). The unit and
env content are the cutover's launch shape; a silent format drift here becomes a
dead daemon at step 5, so the load-bearing lines are asserted verbatim.

Run: python3 tests/test_daemon_unit.py
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_BOOT_HOME = tempfile.mkdtemp(prefix="lwe-unit-boot-")
for _k, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_k] = os.path.join(_BOOT_HOME, _sub) if _sub else _BOOT_HOME

from lwe_ui.engine import daemon_unit  # noqa: E402
from lwe_ui.storage import settings  # noqa: E402


class _FakeReload:
    """Hermetic systemctl: daemon-reload must never touch the host user bus."""

    def __init__(self):
        self.calls = []
        self.returncode = 0
        self.stderr = ""

    def __call__(self, args, **kw):
        self.calls.append(["systemctl", "--user", *args])
        return self.returncode, "", self.stderr


_fake_reload = _FakeReload()
daemon_unit.RUNNER = _fake_reload

PID = 4242


def engine_status(pid: int = PID, **knobs: tuple) -> dict:
    """A status reply from the engine: its pid and a config block holding the five restart knobs
    at their defaults, each knob given as (value, source) replacing or adding its entry."""
    config = {"LWE_SSFACTOR": (1.0, "default"), "LWE_CLAMPCOMPOSITES": (1.0, "default"),
              "LWE_TEXCOMP": (True, "default"), "LWE_TEXDETAIL": ("auto", "default"),
              "LWE_HWDEC": ("no", "default"), **knobs}
    return {"pid": pid, "config": {name: {"value": value, "source": source}
                                   for name, (value, source) in config.items()}}


class DaemonUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.mkdtemp(prefix="lwe-unit-")
        os.environ["HOME"] = self._home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self._home, "c")
        os.environ["XDG_STATE_HOME"] = os.path.join(self._home, "s")
        os.environ["XDG_DATA_HOME"] = os.path.join(self._home, "d")
        os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")

    def test_env_content_shape(self) -> None:
        settings.save({"ASSETS_DIR": "/data/assets", "ENGINE_HWDEC": "auto",
                       "ENGINE_TEXCOMP": False, "ENGINE_LAYER": "background"})
        content = daemon_unit.build_env_content(outputs=["DP-2", "DP-3", "DP-1"])
        lines = dict(ln.split("=", 1) for ln in content.splitlines() if "=" in ln and not ln.startswith("#"))
        self.assertEqual(
            lines["LWE_ENGINE_ARGS"],
            "--assets-dir /data/assets --screen-root DP-2 --screen-root DP-3 --screen-root DP-1"
            " --layer background",
        )
        self.assertEqual(lines["LWE_HWDEC"], "auto")
        self.assertEqual(lines["LWE_TEXCOMP"], "0")
        self.assertEqual(lines["LWE_DEADMAN"], "300")

    def test_texture_detail_env(self) -> None:
        """Mip residency: auto emits ONLY the on/off switch - the cap
        derives engine-side from live outputs, so no number can go stale in this file;
        full emits nothing. LWE_TEXCAP is the engine's testtexturelimit switch, not a key
        this generator owns, so a regenerate carries a hand-added line like any other."""
        settings.save({"TEXTURE_DETAIL": "auto"})
        content = daemon_unit.build_env_content(outputs=["DP-1"])
        self.assertIn("LWE_TEXDETAIL=auto", content)
        self.assertNotIn("LWE_TEXCAP", content)

        stale = content + "LWE_TEXCAP=2560\n"
        regenerated = daemon_unit.build_env_content(outputs=["DP-1"], existing=stale)
        self.assertIn("LWE_TEXCAP=2560\n", regenerated)

        settings.save({"TEXTURE_DETAIL": "full"})
        content = daemon_unit.build_env_content(outputs=["DP-1"])
        # the engine caps by default now, so "full" must be written explicitly
        self.assertIn("LWE_TEXDETAIL=full", content)
        self.assertNotIn("LWE_TEXCAP", content)

    def test_render_resolution_env(self) -> None:
        """The clamp's three states: the default writes nothing (the engine clamps on its own),
        sharp effects writes the composite exemption, wallpaper writes the clamp off. Both
        lines are managed, so a change of state scrubs the other."""
        settings.save({"RENDER_RESOLUTION": "screen"})
        content = daemon_unit.build_env_content(outputs=["DP-1"])
        self.assertNotIn("LWE_SSFACTOR", content)
        self.assertNotIn("LWE_CLAMPCOMPOSITES", content)

        settings.save({"RENDER_RESOLUTION": "sharpfx"})
        content = daemon_unit.build_env_content(outputs=["DP-1"])
        self.assertIn("LWE_CLAMPCOMPOSITES=0", content)
        self.assertNotIn("LWE_SSFACTOR", content)

        settings.save({"RENDER_RESOLUTION": "wallpaper"})
        regenerated = daemon_unit.build_env_content(outputs=["DP-1"], existing=content)
        self.assertIn("LWE_SSFACTOR=0", regenerated)
        self.assertEqual(regenerated.count("LWE_CLAMPCOMPOSITES"), 1)
        self.assertIn("LWE_CLAMPCOMPOSITES=0", regenerated)

    def test_reconcile_env(self) -> None:
        """Startup drift repair: a stale env rewrites on panel start; a
        current one is left alone; no-outputs never degrades the file."""
        settings.save({"TEXTURE_DETAIL": "auto",
                       "ASSETS_DIR": "/data/assets"})
        saved = daemon_unit.enumerate_outputs
        saved_bin = daemon_unit.resolve_engine_bin
        daemon_unit.enumerate_outputs = lambda: ["DP-1"]
        daemon_unit.resolve_engine_bin = lambda: "/usr/local/bin/linux-wallpaperengine"
        try:
            env_path = daemon_unit.paths.config_dir() / daemon_unit.ENV_FILE_NAME
            env_path.parent.mkdir(parents=True, exist_ok=True)
            env_path.write_text("# stale\nLWE_HWDEC=no\n", encoding="utf-8")
            self.assertTrue(daemon_unit.reconcile_env(), "stale file must repair")
            self.assertIn("LWE_TEXDETAIL=auto", env_path.read_text(encoding="utf-8"))
            self.assertFalse(daemon_unit.reconcile_env(), "current file must be a no-op")
            daemon_unit.enumerate_outputs = lambda: []
            env_path.write_text("# stale again\n", encoding="utf-8")
            self.assertFalse(daemon_unit.reconcile_env(),
                             "no outputs must never degrade the file")
        finally:
            daemon_unit.enumerate_outputs = saved
            daemon_unit.resolve_engine_bin = saved_bin

    def test_audio_dials_are_emitted(self) -> None:
        """The P0: a restarted engine comes back calibrated.

        Before this, the three dial values lived ONLY in a hand-edited engine-env, so the
        first regenerate would have reverted the engine to source defaults and destroyed
        the stored calibration. This is the sequencing law's whole point - it must be true
        BEFORE any production caller of write_files() exists.
        """
        settings.save({"ENGINE_AUDIO_GAIN": 4.5, "ENGINE_CLASSIC_K": 0.7,
                       "ENGINE_CLASSIC_EXP": 2.6})
        content = daemon_unit.build_env_content(outputs=["DP-1"])
        lines = dict(ln.split("=", 1) for ln in content.splitlines()
                     if "=" in ln and not ln.startswith("#"))
        self.assertEqual(lines["LWE_AUDIOGAIN"], "4.5")
        self.assertEqual(lines["LWE_CLASSICK"], "0.7")
        self.assertEqual(lines["LWE_CLASSICEXP"], "2.6")
        self.assertNotIn("LWE_NOPAUSEVRAM", lines,
                         "retired with the engine's pause-VRAM machinery")

    def test_audio_dials_fall_back_to_calibrated(self) -> None:
        """An install with no dial keys still emits the calibrated numbers, never 0."""
        settings.save({"ASSETS_DIR": "/a"})
        content = daemon_unit.build_env_content(outputs=["DP-1"])
        lines = dict(ln.split("=", 1) for ln in content.splitlines()
                     if "=" in ln and not ln.startswith("#"))
        self.assertEqual(lines["LWE_AUDIOGAIN"], "3")
        self.assertEqual(lines["LWE_CLASSICK"], "0.7")
        self.assertEqual(lines["LWE_CLASSICEXP"], "2.6")

    def test_foreign_env_lines_survive_a_regenerate(self) -> None:
        """The census hazard, closed.

        The four debug lines on the live box are instruments, not settings: no schema key
        describes them and no UI can put them back. Before the settings rework nothing ever
        called write_files(), so they were safe by accident; wiring the Advanced rows armed
        the deletion. The generator must now carry through every line whose key it does not
        own, and still rewrite every line it does.
        """
        settings.save({"ENGINE_HWDEC": "auto", "ENGINE_LAYER": "background"})
        existing = (
            "# GENERATED by LWE Control Panel - edit settings, not this file.\n"
            "LWE_ENGINE_ARGS=--layer bottom\n"      # managed: must be REWRITTEN
            "LWE_HWDEC=no\n"                        # managed: must be REWRITTEN
            "LWE_NOPAUSEVRAM=1\n"                   # managed but retired: must be SCRUBBED
            "# a hand-written note about the probe below\n"
            "LWE_SHADERDUMP_MATCH=godrays\n"        # foreign: must SURVIVE
            "LWE_IMGPROBE=1\n"
            "LWE_LIGHTDUMP=1\n"
            "LWE_AUDIOSTATS=1\n"
        )
        content = daemon_unit.build_env_content(outputs=["DP-1"], existing=existing)
        lines = dict(ln.split("=", 1) for ln in content.splitlines()
                     if "=" in ln and not ln.strip().startswith("#"))

        self.assertEqual(lines["LWE_SHADERDUMP_MATCH"], "godrays")
        self.assertEqual(lines["LWE_IMGPROBE"], "1")
        self.assertEqual(lines["LWE_LIGHTDUMP"], "1")
        self.assertEqual(lines["LWE_AUDIOSTATS"], "1")
        self.assertIn("a hand-written note about the probe below", content,
                      "a hand-added line's own comment rides with it")

        self.assertEqual(lines["LWE_HWDEC"], "auto")
        self.assertIn("--layer background", lines["LWE_ENGINE_ARGS"])
        self.assertEqual(content.count("LWE_HWDEC="), 1, "no duplicate managed key")
        self.assertEqual(content.count("LWE_NOPAUSEVRAM="), 0,
                         "an owned-but-retired key is scrubbed, not carried as foreign")

    def test_regenerate_is_idempotent(self) -> None:
        """Feeding the generator its own output must not grow the file or duplicate a key."""
        settings.save({"ENGINE_HWDEC": "auto"})
        first = daemon_unit.build_env_content(outputs=["DP-1"],
                                              existing="LWE_AUDIOSTATS=1\n")
        second = daemon_unit.build_env_content(outputs=["DP-1"], existing=first)
        self.assertEqual(first, second, "a regenerate of a generated file is a no-op")

    def test_default_layer_omitted(self) -> None:
        settings.save({"ASSETS_DIR": "/a"})
        content = daemon_unit.build_env_content(outputs=["DP-1"])
        self.assertNotIn("--layer", content, "bottom is the engine default; only deviations ride")

    def test_engine_bin_resolution_order(self) -> None:
        """Explicit ENGINE_BIN wins when it exists; the shim never; PATH next; no ghost fallbacks."""
        configured = os.path.join(self._home, "opt-engine")
        open(configured, "w").close()
        settings.save({"ENGINE_BIN": configured})
        self.assertEqual(daemon_unit.resolve_engine_bin(), configured,
                         "an explicit setting is the user's word")

        # a configured path that is not there is not an engine: every other branch
        # is existence-checked, and launching a missing binary fails with no diagnosis
        saved_which = daemon_unit.shutil.which
        daemon_unit.shutil.which = lambda _n: "/usr/local/bin/linux-wallpaperengine"
        try:
            settings.save({"ENGINE_BIN": "/opt/lwe/does-not-exist"})
            self.assertEqual(daemon_unit.resolve_engine_bin(),
                             "/usr/local/bin/linux-wallpaperengine",
                             "a stale configured path falls through to discovery")
        finally:
            daemon_unit.shutil.which = saved_which

        # the shim name is never accepted, even if the file exists
        shim = os.path.join(self._home, "lwe-engine-api")
        open(shim, "w").close()
        settings.save({"ENGINE_BIN": shim})
        saved_which = daemon_unit.shutil.which
        daemon_unit.shutil.which = lambda _n: None
        try:
            self.assertEqual(daemon_unit.resolve_engine_bin(), "",
                             "shim + nothing on PATH + no dev checkout = no engine")
            # an installed engine on PATH is found when the setting is empty
            settings.save({"ENGINE_BIN": ""})
            daemon_unit.shutil.which = lambda _n: "/usr/local/bin/linux-wallpaperengine"
            self.assertEqual(daemon_unit.resolve_engine_bin(),
                             "/usr/local/bin/linux-wallpaperengine")
            # menu launches have no ~/.local/bin on PATH: the install target is
            # probed directly
            daemon_unit.shutil.which = lambda _n: None
            localbin = os.path.join(self._home, ".local", "bin")
            os.makedirs(localbin, exist_ok=True)
            installed = os.path.join(localbin, "linux-wallpaperengine")
            open(installed, "w").close()
            settings.save({"ENGINE_BIN": ""})
            self.assertEqual(daemon_unit.resolve_engine_bin(), installed)
        finally:
            daemon_unit.shutil.which = saved_which

    def test_unit_refused_without_engine(self) -> None:
        """No engine anywhere: write_files refuses rather than writing a ghost unit."""
        settings.save({"ENGINE_BIN": ""})
        saved_which = daemon_unit.shutil.which
        saved_outs = daemon_unit.enumerate_outputs
        daemon_unit.shutil.which = lambda _n: None
        daemon_unit.enumerate_outputs = lambda: ["DP-1"]
        try:
            with self.assertRaises(ValueError):
                daemon_unit.write_files()
        finally:
            daemon_unit.shutil.which = saved_which
            daemon_unit.enumerate_outputs = saved_outs

    def test_fresh_home_install(self) -> None:
        """A bare HOME install writes both files, quotes the binary, escapes %."""
        settings.save(settings.load())
        saved_bin = daemon_unit.resolve_engine_bin
        saved_outs = daemon_unit.enumerate_outputs
        daemon_unit.resolve_engine_bin = lambda: "/opt/my apps/100% engine/lwe"
        daemon_unit.enumerate_outputs = lambda: ["DP-1"]
        try:
            env_path, unit_path = daemon_unit.write_files()
            self.assertTrue(os.path.exists(env_path) and os.path.exists(unit_path))
            unit = open(unit_path, encoding="utf-8").read()
            self.assertIn('ExecStart="/opt/my apps/100%% engine/lwe" --daemon', unit)
            self.assertTrue(any("daemon-reload" in " ".join(c) for c in _fake_reload.calls))
        finally:
            daemon_unit.resolve_engine_bin = saved_bin
            daemon_unit.enumerate_outputs = saved_outs

    def test_hostile_assets_dir_refused(self) -> None:
        """An assets path systemd would mis-parse is refused, never silently broken."""
        settings.save({"ASSETS_DIR": "/data/my assets"})
        saved = daemon_unit.enumerate_outputs
        daemon_unit.enumerate_outputs = lambda: ["DP-1"]
        try:
            with self.assertRaises(ValueError):
                daemon_unit.build_env_content()
        finally:
            daemon_unit.enumerate_outputs = saved

    def test_in_the_sandbox_the_reload_runs_nothing_and_fails_as_a_failed_reload(self) -> None:
        settings.save(settings.load())
        calls = []
        with mock.patch.object(daemon_unit, "RUNNER", daemon_unit._systemctl), \
                mock.patch.object(daemon_unit.subprocess, "run", lambda *a, **k: calls.append(a)), \
                mock.patch.object(daemon_unit, "enumerate_outputs", lambda: ["DP-1"]), \
                mock.patch.object(daemon_unit, "resolve_engine_bin", lambda: "/usr/local/bin/linux-wallpaperengine"):
            with self.assertRaises(RuntimeError) as caught:
                daemon_unit.write_files()
        self.assertEqual(str(caught.exception), "systemd daemon-reload failed: systemctl is not run in the test "
                                                "sandbox; run systemctl --user daemon-reload")
        self.assertEqual(calls, [])

    def test_reload_failure_is_loud(self) -> None:
        """A failed daemon-reload surfaces instead of being swallowed."""
        settings.save(settings.load())
        saved = daemon_unit.enumerate_outputs
        saved_bin = daemon_unit.resolve_engine_bin
        daemon_unit.enumerate_outputs = lambda: ["DP-1"]
        daemon_unit.resolve_engine_bin = lambda: "/usr/local/bin/linux-wallpaperengine"
        _fake_reload.returncode = 1
        _fake_reload.stderr = "Access denied"
        try:
            with self.assertRaises(RuntimeError):
                daemon_unit.write_files()
        finally:
            _fake_reload.returncode = 0
            _fake_reload.stderr = ""
            daemon_unit.enumerate_outputs = saved
            daemon_unit.resolve_engine_bin = saved_bin

    def test_unit_template_load_bearing_lines(self) -> None:
        unit = daemon_unit._UNIT_TEMPLATE.format(env_name="engine-env", engine_bin="/x/engine")
        self.assertIn('ExecStart="/x/engine" --daemon $LWE_ENGINE_ARGS', unit)
        self.assertIn("EnvironmentFile=%h/.config/lwe/engine-env", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("MemoryHigh=2G", unit)
        self.assertIn("MemoryMax=3G", unit)
        self.assertIn("WantedBy=graphical-session.target", unit)
        service = unit.split("[Service]\n", 1)[1].split("\n[", 1)[0]
        self.assertIn("RuntimeDirectory=lwe-engine\n", service)
        self.assertIn("WorkingDirectory=%t/lwe-engine\n", service)

    def test_restart_pending_compares_only_the_settings_own_keys(self) -> None:
        """The clamp reaches the engine only at service start, so each clamp row's restart verb
        shows exactly while the running engine's status and the file disagree on that row's key
        and on nothing else: another knob status reports must not hold it open."""
        self.enterContext(mock.patch.object(daemon_unit, "_service_main_pid", lambda: PID))
        status_default = engine_status(LWE_DEADMAN=(60, "env"))
        file_default = "# GENERATED\nLWE_TEXCOMP=1\n"
        file_sharpfx = file_default + "LWE_CLAMPCOMPOSITES=0\n"
        file_wallpaper = file_default + "LWE_SSFACTOR=0\n"

        def pending(key: str, status: dict | None, text: str, live: dict | None = None) -> bool:
            return daemon_unit.restart_pending(key, live or {}, text, status=status)
        self.assertFalse(pending("SSFACTOR", status_default, file_default))
        self.assertFalse(pending("CLAMPCOMPOSITES", status_default, file_default))
        self.assertEqual((pending("SSFACTOR", status_default, file_sharpfx),
                          pending("CLAMPCOMPOSITES", status_default, file_sharpfx)), (False, True),
                         "a line on LWE_CLAMPCOMPOSITES lights CLAMPCOMPOSITES only")
        self.assertEqual((pending("SSFACTOR", status_default, file_wallpaper),
                          pending("CLAMPCOMPOSITES", status_default, file_wallpaper)), (True, False),
                         "a line on LWE_SSFACTOR lights SSFACTOR only")
        status_sharpfx = engine_status(LWE_CLAMPCOMPOSITES=(0.0, "env"))
        self.assertFalse(pending("CLAMPCOMPOSITES", status_sharpfx, file_sharpfx))
        self.assertTrue(pending("CLAMPCOMPOSITES", status_sharpfx, file_default),
                        "back to the default clears the line, and that is a change too")
        self.assertFalse(pending("ENGINE_LAYER", status_default, file_sharpfx),
                         "a setting with no env keys registered never pends")
        # the sandbox reports no running engine: nothing pends, the next start reads the file
        self.assertFalse(pending("CLAMPCOMPOSITES", None, file_sharpfx))
        # the other rows: one env line each
        same = "LWE_HWDEC=no\nLWE_TEXCOMP=1\nLWE_TEXDETAIL=auto\n"
        self.assertFalse(pending("ENGINE_HWDEC", status_default, same))
        self.assertTrue(pending("ENGINE_HWDEC", status_default, same.replace("no", "auto")))
        self.assertTrue(pending("ENGINE_TEXCOMP", status_default, same.replace("TEXCOMP=1", "TEXCOMP=0")))
        self.assertTrue(pending("TEXTURE_DETAIL", status_default, same.replace("auto", "full")))
        self.assertFalse(pending("TEXTURE_DETAIL", status_default, same.replace("TEXCOMP=1", "TEXCOMP=0")),
                         "another row's key must not light this one")
        # the layer is one token of the argument line beside the monitor list
        live_args = {"LWE_ENGINE_ARGS": "--assets-dir /a --screen-root DP-1 --layer top"}
        self.assertFalse(pending("ENGINE_LAYER", status_default,
                                 "LWE_ENGINE_ARGS=--assets-dir /a --screen-root DP-1 --screen-root DP-2 --layer top\n",
                                 live_args),
                         "a monitor change must not light the layer row")
        self.assertTrue(pending("ENGINE_LAYER", status_default,
                                "LWE_ENGINE_ARGS=--assets-dir /a --screen-root DP-1\n", live_args),
                        "back to the default drops the token, and that is a change")
        self.assertTrue(pending("ENGINE_LAYER", status_default,
                                "LWE_ENGINE_ARGS=--assets-dir /a --screen-root DP-1 --layer overlay\n", live_args))
        # one read answers every row
        both = daemon_unit.restart_pending_keys(
            {"LWE_ENGINE_ARGS": "--screen-root DP-1"},
            same.replace("auto", "full") + "LWE_ENGINE_ARGS=--screen-root DP-1 --layer top\n",
            status=status_default)
        self.assertEqual({k for k, v in both.items() if v}, {"TEXTURE_DETAIL", "ENGINE_LAYER"})
        observed, pend = daemon_unit.restart_state({}, same, status=None)
        self.assertFalse(observed, "no status is not an observation")
        self.assertFalse(any(pend.values()))
        observed, pend = daemon_unit.restart_state({}, same, status=status_default)
        self.assertTrue(observed)
        self.assertEqual(set(daemon_unit.RESTART_ENV_KEYS),
                         {"ENGINE_LAYER", "ENGINE_HWDEC", "TEXTURE_DETAIL", "SSFACTOR", "CLAMPCOMPOSITES",
                          "ENGINE_TEXCOMP"})

    def test_parse_env_skips_comments_and_malformed_lines(self) -> None:
        got = daemon_unit.parse_env(["# c", "", "A=1", "B = x=y ", "novalue", " C=", "=D",
                                     'Q="0"', "R='a b'", 'S="x'])
        self.assertEqual(got, {"A": "1", "B": "x=y", "C": "", "": "D",
                               "Q": "0", "R": "a b", "S": '"x'})


HOLDER = r"""
import sys, time
from pathlib import Path
from lwe_ui.storage import lock
with lock.held("env"):
    Path(sys.argv[1]).touch()
    time.sleep(60)
"""


def _systemd_value(line: str) -> str:
    """The value systemd gives the process for one EnvironmentFile line (systemd.exec(5)):
    '...' verbatim; "..." keeps the character after a backslash for \\" \\\\ \\` \\$ and both
    characters otherwise; unquoted, a backslash keeps the next character and outer blanks go."""
    raw = line.partition("=")[2].strip(" \t\r")
    if raw[:1] == "'":
        end = raw.index("'", 1)
        assert raw[end + 1:].strip(" \t\r") == "", line
        return raw[1:end]
    out, i = [], 1 if raw[:1] == '"' else 0
    while i < len(raw):
        ch = raw[i]
        if raw[:1] == '"' and ch == '"':
            assert raw[i + 1:].strip(" \t\r") == "", line
            return "".join(out)
        if ch == "\\" and i + 1 < len(raw):
            keep_both = raw[:1] == '"' and raw[i + 1] not in '"\\`$'
            out.append(raw[i:i + 2] if keep_both else raw[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    assert raw[:1] != '"', f"unterminated double quote in {line!r}"
    return "".join(out)


class EngineEnvWriterTest(unittest.TestCase):
    """write_env, the one engine-env writer, and write_files as the service operations' rebuild."""

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="lwe-envwriter-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        scratch = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / "c"),
                   "XDG_STATE_HOME": str(self.home / "s"), "XDG_DATA_HOME": str(self.home / "d"),
                   "XDG_CACHE_HOME": str(self.home / "k")}
        self.outputs = ["DP-1"]
        for patcher in (mock.patch.dict(os.environ, scratch),
                        mock.patch.object(daemon_unit, "enumerate_outputs", lambda: list(self.outputs)),
                        mock.patch.object(daemon_unit, "resolve_engine_bin",
                                          lambda: "/usr/local/bin/linux-wallpaperengine"),
                        mock.patch.object(daemon_unit, "live_engine_env", lambda: None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        _fake_reload.calls.clear()
        self.env_path = daemon_unit.paths.config_dir() / daemon_unit.ENV_FILE_NAME
        self.unit_path = self.home / ".config" / "systemd" / "user" / daemon_unit.UNIT_FILE_NAME

    def _file(self, text: str) -> bytes:
        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        self.env_path.write_bytes(text.encode("utf-8"))
        return self.env_path.read_bytes()

    def _text(self) -> str:
        return self.env_path.read_text(encoding="utf-8")

    def test_no_screens_leaves_engine_env_as_it_was(self) -> None:
        before = self._file("# by hand\r\nLWE_ENGINE_ARGS=--screen-root DP-9\nLWE_AUDIT=0\nLWE_DEADMAN=7")
        self.outputs = []
        self.assertEqual(daemon_unit.write_env(), "no screens")
        self.assertEqual(daemon_unit.write_env({"LWE_AUDIT": "1", "LWE_DEADMAN": None}), "no screens")
        self.assertEqual(self.env_path.read_bytes(), before)
        daemon_unit.write_files()
        self.assertEqual(self.env_path.read_bytes(), before)
        self.assertTrue(self.unit_path.exists(), "the unit and daemon-reload still follow")
        self.assertEqual(_fake_reload.calls, [["systemctl", "--user", "daemon-reload"]])

    def test_the_kept_watchdog_and_a_texture_limit_line_survive_write_files(self) -> None:
        self._file("LWE_DEADMAN=60\nLWE_TEXCAP=2048\n")
        daemon_unit.write_files()
        text = self._text()
        self.assertIn("\nLWE_DEADMAN=60\n", text)
        self.assertEqual(text.count("LWE_DEADMAN="), 1)
        self.assertIn("\nLWE_TEXCAP=2048\n", text)
        for kept, lines, emitted in (("86401", None, "300"), ("60", {"LWE_DEADMAN": 90}, "90"),
                                     ("60", {"LWE_DEADMAN": None}, "300")):
            with self.subTest(kept=kept, lines=lines):
                self._file(f"LWE_DEADMAN={kept}\n")
                daemon_unit.write_env(lines)
                self.assertIn(f"\nLWE_DEADMAN={emitted}\n", self._text())

    def test_write_env_runs_no_subprocess_and_never_writes_the_unit(self) -> None:
        self._file("# the audit probe\nLWE_AUDIT=0\nLWE_IMGPROBE=1\n")
        self.assertEqual(daemon_unit.write_env({"LWE_AUDIT": "1", "LWE_NEW": "on"}), "written")
        text = self._text()
        self.assertIn("# the audit probe\nLWE_AUDIT=1\nLWE_IMGPROBE=1\nLWE_NEW=on\n", text)
        self.assertEqual(daemon_unit.write_env({"LWE_AUDIT": "1"}), "unchanged")
        self.assertEqual(daemon_unit.write_env({"LWE_AUDIT": None}), "written")
        self.assertNotIn("audit", self._text().lower(), "an unset takes the line and its comment")
        self.assertEqual(_fake_reload.calls, [])
        self.assertFalse(self.unit_path.exists())
        for owned in ("LWE_HWDEC", "LWE_ENGINE_ARGS", "LWE_AUDIOGAIN"):
            with self.assertRaises(ValueError):
                daemon_unit.write_env({owned: "1"})

    def test_line_values_round_trip_through_parse_env_and_systemd_rules(self) -> None:
        cases = (("a b", "LWE_TEST='a b'"), ("it's", "LWE_TEST=\"it's\""), ("$HOME", "LWE_TEST='$HOME'"),
                 ("it's \"$x\" \\ `y`", "LWE_TEST=\"it's \\\"\\$x\\\" \\\\ \\`y\\`\""), ("60", "LWE_TEST=60"))
        for value, written in cases:
            with self.subTest(value=value):
                daemon_unit.write_env({"LWE_TEST": value})
                line = next(ln for ln in self._text().splitlines() if ln.startswith("LWE_TEST="))
                self.assertEqual(line, written)
                self.assertEqual(daemon_unit.parse_env([line])["LWE_TEST"], value)
                self.assertEqual(_systemd_value(line), value)
                self.assertEqual(daemon_unit.write_env(), "unchanged", "a rebuild keeps the line")
        for bad in ("a\nb", "a\0b", "a\x85b"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                daemon_unit.write_env({"LWE_TEST": bad})

    def _holder_env(self) -> dict[str, str]:
        """An environment built from nothing: every folder in this test's home, and a PySide6
        that fails to import ahead of the panel's sources."""
        poison = self.home / "poison" / "PySide6"
        poison.mkdir(parents=True)
        (poison / "__init__.py").write_text("raise ImportError('PySide6 is blocked here')\n",
                                            encoding="utf-8")
        (self.home / "bin").mkdir()
        return {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / "c"),
                "XDG_STATE_HOME": str(self.home / "s"), "XDG_DATA_HOME": str(self.home / "d"),
                "XDG_CACHE_HOME": str(self.home / "k"), "XDG_RUNTIME_DIR": str(self.home / "rt"),
                "LWE_SOCKET": str(self.home / "rt" / "engine.sock"), "LWE_SANDBOX": "1",
                "PATH": str(self.home / "bin"), "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": os.pathsep.join([str(self.home / "poison"), _SRC])}

    def test_a_process_holding_env_makes_write_env_busy(self) -> None:
        """HOLDER takes the env lock in another process and touches `held`, then sleeps until
        it is killed."""
        held = self.home / "held"
        holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(held)], env=self._holder_env(),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.addCleanup(holder.communicate, timeout=30)
        self.addCleanup(holder.kill)
        deadline = time.monotonic() + 30
        while not held.exists():
            self.assertIsNone(holder.poll(), "the holder exited before it held the lock")
            self.assertLess(time.monotonic(), deadline, "the holder never held the lock")
            time.sleep(0.01)
        start = time.monotonic()
        with self.assertRaises(daemon_unit.lock.StoreBusy):
            daemon_unit.write_env()
        self.assertTrue(2.0 <= time.monotonic() - start < 5.0, time.monotonic() - start)
        self.assertFalse(self.env_path.exists())

    def test_a_failed_daemon_reload_names_the_fix(self) -> None:
        self.addCleanup(setattr, _fake_reload, "returncode", 0)
        self.addCleanup(setattr, _fake_reload, "stderr", "")
        _fake_reload.returncode, _fake_reload.stderr = 1, "Access denied"
        with self.assertRaises(RuntimeError) as caught:
            daemon_unit.write_files()
        self.assertIn("Access denied", str(caught.exception))
        self.assertIn("systemctl --user daemon-reload", str(caught.exception))

    def test_the_start_up_reconcile_writes_engine_env_only(self) -> None:
        self.assertTrue(daemon_unit.reconcile_env())
        self.assertIn("--screen-root DP-1", self._text())
        self.assertFalse(self.unit_path.exists())
        self.assertFalse(daemon_unit.reconcile_env(), "a current file is left alone")
        self.assertEqual(_fake_reload.calls, [])

    def test_a_changed_unit_still_records_one_daemon_reload(self) -> None:
        daemon_unit.write_files()
        unit = self.unit_path.read_text(encoding="utf-8")
        with mock.patch.object(daemon_unit, "resolve_engine_bin", lambda: "/opt/lwe/linux-wallpaperengine"):
            daemon_unit.write_files()
        self.assertNotEqual(self.unit_path.read_text(encoding="utf-8"), unit)
        self.assertEqual(_fake_reload.calls, [["systemctl", "--user", "daemon-reload"]] * 2)


class RestartStatusTest(unittest.TestCase):
    """restart_state against the engine's status: the five knobs read from the env file by the
    engine's own rules, the flag rule, the service pid check and the layer's argv probe. Status
    comes from a replaced api_client.status and the MainPID from a replaced _service_main_pid;
    LWE_SANDBOX stays set."""

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="lwe-restart-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        scratch = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / "c"),
                   "XDG_STATE_HOME": str(self.home / "s"), "XDG_DATA_HOME": str(self.home / "d"),
                   "XDG_CACHE_HOME": str(self.home / "k")}
        self.status: dict | None = engine_status()
        self.live: dict | None = {"LWE_ENGINE_ARGS": "--screen-root DP-1"}
        self.main_pid = self.real_main_pid = daemon_unit._service_main_pid
        for patcher in (mock.patch.dict(os.environ, scratch),
                        mock.patch.object(daemon_unit.api_client, "status", lambda *a, **k: self.status),
                        mock.patch.object(daemon_unit, "_service_main_pid", lambda: self.main_pid()),
                        mock.patch.object(daemon_unit, "live_engine_env", lambda: self.live)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.main_pid = lambda: PID
        _fake_reload.calls.clear()

    def pends(self, text: str, **knobs: tuple) -> set[str]:
        """The rows that pend for env file `text` against a status reporting `knobs`."""
        self.status = engine_status(**knobs)
        return {key for key, value in daemon_unit.restart_pending_keys(env_text=text).items() if value}

    def test_values_equal_under_the_engine_rules_do_not_pend(self) -> None:
        self.assertEqual(self.pends("LWE_TEXDETAIL=auto\nLWE_SSFACTOR=1\n"), set())
        for line, name, value in (("LWE_SSFACTOR=9", "LWE_SSFACTOR", 4.0), ("LWE_SSFACTOR=-2", "LWE_SSFACTOR", 0.0),
                                  ("LWE_SSFACTOR=abc", "LWE_SSFACTOR", 0.0),
                                  ("LWE_SSFACTOR=2.5x", "LWE_SSFACTOR", 2.5),
                                  ("LWE_SSFACTOR=nan", "LWE_SSFACTOR", 1.0), ("LWE_SSFACTOR=", "LWE_SSFACTOR", 1.0),
                                  ("LWE_CLAMPCOMPOSITES=0.5", "LWE_CLAMPCOMPOSITES", 0.5),
                                  ("LWE_TEXCOMP=", "LWE_TEXCOMP", True), ("LWE_TEXCOMP=0", "LWE_TEXCOMP", False),
                                  ("LWE_TEXDETAIL=Auto", "LWE_TEXDETAIL", "full"),
                                  ("LWE_HWDEC=", "LWE_HWDEC", "no"), ("LWE_HWDEC=auto", "LWE_HWDEC", "auto")):
            with self.subTest(line=line):
                self.assertEqual(self.pends(line + "\n", **{name: (value, "env")}), set())

    def test_a_factor_line_reads_as_the_engine_s_atof_reads_it(self) -> None:
        for text, engine in (("1_5", 1.0), ("0x2", 2.0), ("infx", 4.0), ("nanx", 1.0), ("١", 0.0),
                             ("1.3", 1.2999999523162842)):
            with self.subTest(text=text):
                self.assertEqual(self.pends(f"LWE_SSFACTOR={text}\n", LWE_SSFACTOR=(engine, "env")), set())

    def test_a_status_float_beyond_float32_does_not_raise(self) -> None:
        self.assertEqual(self.pends("LWE_SSFACTOR=2\n", LWE_SSFACTOR=(1e39, "env")), {"SSFACTOR"})

    def test_a_float_compares_at_float32(self) -> None:
        self.assertEqual(self.pends("LWE_SSFACTOR=1.3\n", LWE_SSFACTOR=(1.2999999523162842, "env")), set())

    def test_a_differing_value_pends(self) -> None:
        self.assertEqual(self.pends("LWE_SSFACTOR=1.3\n", LWE_SSFACTOR=(1.5, "env")), {"SSFACTOR"})
        self.assertEqual(self.pends("LWE_HWDEC=auto\n"), {"ENGINE_HWDEC"})
        self.assertEqual(self.pends("LWE_TEXCOMP=0\n"), {"ENGINE_TEXCOMP"})
        self.assertEqual(self.pends("LWE_TEXDETAIL=full\n"), {"TEXTURE_DETAIL"})

    def test_a_knob_given_as_a_flag_or_not_reported_never_pends(self) -> None:
        self.assertEqual(self.pends("LWE_SSFACTOR=1\n", LWE_SSFACTOR=(2.0, "flag")), set())
        self.assertEqual(self.pends("LWE_HWDEC=auto\n", LWE_HWDEC=("no", "flag")), set())
        self.status = engine_status()
        del self.status["config"]["LWE_HWDEC"]
        pending = daemon_unit.restart_pending_keys(env_text="LWE_HWDEC=auto\n")
        self.assertFalse(pending["ENGINE_HWDEC"], "a knob status does not report never pends")

    def test_no_status_or_no_config_block_is_not_observed(self) -> None:
        for status in (None, {"pid": PID}, {"pid": PID, "config": []}):
            with self.subTest(status=status):
                self.status = status
                observed, pending = daemon_unit.restart_state(env_text="LWE_HWDEC=auto\n")
                self.assertEqual((observed, any(pending.values())), (False, False))

    def test_a_status_pid_other_than_the_service_is_not_observed(self) -> None:
        self.status = engine_status(pid=PID + 1)
        self.assertEqual(daemon_unit.restart_state(env_text="LWE_HWDEC=auto\n")[0], False)
        self.status = engine_status()
        self.main_pid = lambda: None
        self.assertEqual(daemon_unit.restart_state(env_text="LWE_HWDEC=auto\n")[0], False)
        self.main_pid = self.real_main_pid
        self.assertIsNone(daemon_unit._service_main_pid(), "the sandbox gate holds")
        self.assertEqual(_fake_reload.calls, [])

    def test_the_layer_still_pends_from_the_argv_probe(self) -> None:
        self.live = {"LWE_ENGINE_ARGS": "--screen-root DP-1 --layer top"}
        self.assertEqual(self.pends("LWE_ENGINE_ARGS=--screen-root DP-1\n"), {"ENGINE_LAYER"})
        self.live = None
        observed, pending = daemon_unit.restart_state(env_text="LWE_ENGINE_ARGS=--screen-root DP-1 --layer top\n")
        self.assertEqual((observed, pending["ENGINE_LAYER"]), (True, False),
                         "an unreadable process environment leaves the layer alone")

    def test_with_nothing_given_it_reads_status_and_the_env_file(self) -> None:
        env = daemon_unit.paths.config_dir() / daemon_unit.ENV_FILE_NAME
        env.parent.mkdir(parents=True, exist_ok=True)
        env.write_text("LWE_HWDEC=auto\n", encoding="utf-8")
        observed, pending = daemon_unit.restart_state()
        self.assertEqual((observed, {key for key, value in pending.items() if value}), (True, {"ENGINE_HWDEC"}))
        env.unlink()
        self.assertEqual(daemon_unit.restart_state()[0], False, "an unreadable env file is not an observation")


def _test_cross_compositor_enumeration() -> None:
    """Output names must resolve on KDE and generic wlroots, not just Hyprland.

    With no names the env file carries no --screen-root and the daemon boots with nowhere
    to draw - "installed fine, renders nothing".
    """
    from lwe_ui.engine import daemon_unit as du

    saved = (du._outputs_hyprctl, du._outputs_kscreen, du._outputs_wlr_randr)
    try:
        du._outputs_hyprctl = lambda: []
        du._outputs_kscreen = lambda: ["DP-1", "HDMI-A-1"]
        du._outputs_wlr_randr = lambda: []
        assert du.enumerate_outputs() == ["DP-1", "HDMI-A-1"], "KDE fallback"

        du._outputs_kscreen = lambda: []
        du._outputs_wlr_randr = lambda: ["eDP-1"]
        assert du.enumerate_outputs() == ["eDP-1"], "wlr-randr fallback"

        du._outputs_hyprctl = lambda: ["DP-2"]
        assert du.enumerate_outputs() == ["DP-2"], "first source wins"

        # a source that throws must not take the chain down
        du._outputs_hyprctl = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            du.enumerate_outputs()
        except RuntimeError:
            raise AssertionError("a broken source must fall through, not propagate")
    except RuntimeError:
        raise
    finally:
        du._outputs_hyprctl, du._outputs_kscreen, du._outputs_wlr_randr = saved
    print("OK cross-compositor output enumeration (hyprland / kde / wlroots)")


if __name__ == "__main__":
    _test_cross_compositor_enumeration()
    unittest.main(verbosity=1)
