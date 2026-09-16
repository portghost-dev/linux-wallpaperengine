"""Self-verification for the lwe-engine.service writer (cutover artifact).

Pure generation checks - no systemctl, no hyprctl (outputs injected). The unit and
env content are the cutover's launch shape; a silent format drift here becomes a
dead daemon at step 5, so the load-bearing lines are asserted verbatim.

Run: python3 tests/test_daemon_unit.py
"""
from __future__ import annotations

import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

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

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        return types.SimpleNamespace(returncode=self.returncode,
                                     stderr=self.stderr, stdout="")


_fake_reload = _FakeReload()
daemon_unit.subprocess.run = _fake_reload


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
        full emits nothing. A stale LWE_TEXCAP line from the brief panel-derived era is
        a MANAGED key, so a regenerate strips it rather than carrying it as foreign."""
        settings.save({"TEXTURE_DETAIL": "auto"})
        content = daemon_unit.build_env_content(outputs=["DP-1"])
        self.assertIn("LWE_TEXDETAIL=auto", content)
        self.assertNotIn("LWE_TEXCAP", content)

        stale = content + "LWE_TEXCAP=2560\n"
        regenerated = daemon_unit.build_env_content(outputs=["DP-1"], existing=stale)
        self.assertNotIn("LWE_TEXCAP", regenerated)

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
        self.assertNotIn("LWE_CLAMPCOMPOSITES", regenerated)

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

    def test_restart_pending_compares_only_the_settings_own_keys(self) -> None:
        """The clamp reaches the engine only at service start, so the row's restart verb
        shows exactly while the live process and the file disagree on the clamp keys and
        on nothing else: a foreign instrument the process carries must not hold it open."""
        live_default = {"LWE_TEXCOMP": "1", "LWE_TEXCACHEDUMP": "1"}
        file_default = "# GENERATED\nLWE_TEXCOMP=1\n"
        file_sharpfx = file_default + "LWE_CLAMPCOMPOSITES=0\n"
        file_wallpaper = file_default + "LWE_SSFACTOR=0\n"
        pending = daemon_unit.restart_pending
        self.assertFalse(pending("RENDER_RESOLUTION", live_default, file_default))
        self.assertTrue(pending("RENDER_RESOLUTION", live_default, file_sharpfx))
        self.assertTrue(pending("RENDER_RESOLUTION", live_default, file_wallpaper))
        live_sharpfx = dict(live_default, LWE_CLAMPCOMPOSITES="0")
        self.assertFalse(pending("RENDER_RESOLUTION", live_sharpfx, file_sharpfx))
        self.assertTrue(pending("RENDER_RESOLUTION", live_sharpfx, file_default),
                        "back to the default clears the line, and that is a change too")
        self.assertFalse(pending("ENGINE_LAYER", live_default, file_sharpfx),
                         "a setting with no env keys registered never pends")
        # the sandbox reports no running engine: nothing pends, the next start reads the file
        self.assertFalse(pending("RENDER_RESOLUTION", None, file_sharpfx))
        # the other rows: one env line each
        live = {"LWE_HWDEC": "no", "LWE_TEXCOMP": "1", "LWE_TEXDETAIL": "auto"}
        same = "LWE_HWDEC=no\nLWE_TEXCOMP=1\nLWE_TEXDETAIL=auto\n"
        self.assertFalse(pending("ENGINE_HWDEC", live, same))
        self.assertTrue(pending("ENGINE_HWDEC", live, same.replace("no", "auto")))
        self.assertTrue(pending("ENGINE_TEXCOMP", live, same.replace("TEXCOMP=1", "TEXCOMP=0")))
        self.assertTrue(pending("TEXTURE_DETAIL", live, same.replace("auto", "full")))
        self.assertFalse(pending("TEXTURE_DETAIL", live, same.replace("TEXCOMP=1", "TEXCOMP=0")),
                         "another row's key must not light this one")
        # the layer is one token of the argument line beside the monitor list
        live_args = {"LWE_ENGINE_ARGS": "--assets-dir /a --screen-root DP-1 --layer top"}
        self.assertFalse(pending("ENGINE_LAYER", live_args,
                                 "LWE_ENGINE_ARGS=--assets-dir /a --screen-root DP-1 --screen-root DP-2 --layer top\n"),
                         "a monitor change must not light the layer row")
        self.assertTrue(pending("ENGINE_LAYER", live_args,
                                "LWE_ENGINE_ARGS=--assets-dir /a --screen-root DP-1\n"),
                        "back to the default drops the token, and that is a change")
        self.assertTrue(pending("ENGINE_LAYER", live_args,
                                "LWE_ENGINE_ARGS=--assets-dir /a --screen-root DP-1 --layer overlay\n"))
        # one read answers every row
        both = daemon_unit.restart_pending_keys(
            dict(live, LWE_ENGINE_ARGS="--screen-root DP-1"),
            same.replace("auto", "full") + "LWE_ENGINE_ARGS=--screen-root DP-1 --layer top\n")
        self.assertEqual({k for k, v in both.items() if v}, {"TEXTURE_DETAIL", "ENGINE_LAYER"})
        self.assertEqual(set(daemon_unit.RESTART_ENV_KEYS),
                         {"ENGINE_LAYER", "ENGINE_HWDEC", "TEXTURE_DETAIL", "RENDER_RESOLUTION",
                          "ENGINE_TEXCOMP"})

    def test_parse_env_skips_comments_and_malformed_lines(self) -> None:
        got = daemon_unit.parse_env(["# c", "", "A=1", "B = x=y ", "novalue", " C=", "=D",
                                     'Q="0"', "R='a b'", 'S="x'])
        self.assertEqual(got, {"A": "1", "B": "x=y", "C": "", "": "D",
                               "Q": "0", "R": "a b", "S": '"x'})


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
