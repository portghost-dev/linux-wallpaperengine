"""Self-verification for engine/invocation.py - pure string-building, no engine process.

Run: export PYTHONPATH=src && python3 tests/test_invocation.py
"""
from __future__ import annotations

import unittest

from lwe_ui import constants as C
from lwe_ui.engine import invocation


def _rep_wp() -> dict:
    """A representative wp dict for the invocation checks."""
    return {
        "BG": "1234567890",
        "TYPE": "scene",
        "SCALING": "fill",
        "FPS": 30,
        "SPEED": 0.3,
        "CC": "1.02 1.52 2 -0.1",
        "VOLUME": 0,
        "CLAMPING": "",
        "AUTOMUTE": True,
        "AUDIO_REACTIVE": False,
        "MOUSE": False,
        "FULLSCREEN_PAUSE": "",
        "MONITORS": "all",
        "SKIP": "461 469 475",
        "props": {"schemecolor": "0.23 0 0.34"},
    }


class TestMirrorArgv(unittest.TestCase):
    def setUp(self) -> None:
        self.env, self.argv = invocation.build_mirror_argv(
            "/usr/bin/linux-wallpaperengine",
            "/assets",
            ["DP-1", "DP-2", "DP-3"],
            _rep_wp(),
        )

    def test_clamp_not_clamping(self) -> None:
        # CLAMPING is "" in the rep dict -> NO clamp flag at all; the spelling, when present,
        # must be --clamp and the string --clamping must never appear.
        self.assertNotIn("--clamping", self.argv)
        self.assertNotIn("--clamp", self.argv)

    def test_clamp_spelling_when_set(self) -> None:
        wp = _rep_wp()
        wp["CLAMPING"] = "border"
        _, argv = invocation.build_mirror_argv("/e", "/a", ["DP-1"], wp)
        self.assertIn("--clamp", argv)
        self.assertNotIn("--clamping", argv)
        self.assertEqual(argv[argv.index("--clamp") + 1], "border")

    def test_order_scaling_before_screen_root_before_bg(self) -> None:
        self.assertLess(self.argv.index("--scaling"), self.argv.index("--screen-root"))
        self.assertLess(self.argv.index("--screen-root"), self.argv.index("--bg"))

    def test_bg_is_last(self) -> None:
        self.assertEqual(self.argv[-2], "--bg")
        self.assertEqual(self.argv[-1], "1234567890")
        # --bg must be the final flag-pair: nothing after the value.
        self.assertEqual(self.argv.index("--bg"), len(self.argv) - 2)

    def test_silent_when_volume_zero(self) -> None:
        self.assertIn("--silent", self.argv)
        self.assertNotIn("--volume", self.argv)

    def test_three_screen_roots(self) -> None:
        self.assertEqual(self.argv.count("--screen-root"), 3)
        idx = [i for i, a in enumerate(self.argv) if a == "--screen-root"]
        self.assertEqual([self.argv[i + 1] for i in idx], ["DP-1", "DP-2", "DP-3"])

    def test_skip_objects(self) -> None:
        self.assertEqual(self.argv.count("--render-debug"), 3)
        rds = [self.argv[i + 1] for i, a in enumerate(self.argv) if a == "--render-debug"]
        self.assertIn("skip-object=461", rds)
        self.assertIn("skip-object=469", rds)
        self.assertIn("skip-object=475", rds)
        self.assertEqual(rds[0], "skip-object=461")

    def test_env(self) -> None:
        self.assertEqual(self.env["LWE_CC"], "1.02 1.52 2 -0.1")
        self.assertEqual(self.env["LWE_TIMESCALE"], "0.3")
        self.assertEqual(set(self.env), {"LWE_CC", "LWE_TIMESCALE"})

    def test_fps_present(self) -> None:
        self.assertIn("--fps", self.argv)
        self.assertEqual(self.argv[self.argv.index("--fps") + 1], "30")
        # --fps precedes --scaling per the proven head order.
        self.assertLess(self.argv.index("--fps"), self.argv.index("--scaling"))

    def test_scaling_value(self) -> None:
        self.assertEqual(self.argv[self.argv.index("--scaling") + 1], "fill")

    def test_assets_dir_first_after_bin(self) -> None:
        self.assertEqual(self.argv[0], "/usr/bin/linux-wallpaperengine")
        self.assertEqual(self.argv[1], "--assets-dir")
        self.assertEqual(self.argv[2], "/assets")

    def test_set_property(self) -> None:
        self.assertEqual(self.argv.count("--set-property"), 1)
        i = self.argv.index("--set-property")
        self.assertEqual(self.argv[i + 1], "schemecolor=0.23 0 0.34")

    def test_no_spurious_audio_mouse_automute(self) -> None:
        # AUTOMUTE true -> no --noautomute ; AUDIO_REACTIVE false -> --no-audio-processing ;
        # MOUSE false -> --disable-mouse. FULLSCREEN_PAUSE "" inherits the global (default
        # False) -> effective False -> --no-fullscreen-pause IS emitted (matches the watcher).
        self.assertNotIn("--noautomute", self.argv)
        self.assertIn("--no-audio-processing", self.argv)
        self.assertIn("--disable-mouse", self.argv)
        self.assertIn("--no-fullscreen-pause", self.argv)


class TestEmissionEdges(unittest.TestCase):
    def test_fps_omitted_when_empty(self) -> None:
        wp = _rep_wp()
        wp["FPS"] = ""
        _, argv = invocation.build_mirror_argv("/e", "/a", ["DP-1"], wp)
        self.assertNotIn("--fps", argv)

    def test_volume_nonzero(self) -> None:
        wp = _rep_wp()
        wp["VOLUME"] = 50
        _, argv = invocation.build_mirror_argv("/e", "/a", ["DP-1"], wp)
        self.assertNotIn("--silent", argv)
        self.assertIn("--volume", argv)
        self.assertEqual(argv[argv.index("--volume") + 1], "50")

    def test_automute_false_emits(self) -> None:
        wp = _rep_wp()
        wp["AUTOMUTE"] = False
        _, argv = invocation.build_mirror_argv("/e", "/a", ["DP-1"], wp)
        self.assertIn("--noautomute", argv)

    def test_audio_reactive_true_omits(self) -> None:
        wp = _rep_wp()
        wp["AUDIO_REACTIVE"] = True
        _, argv = invocation.build_mirror_argv("/e", "/a", ["DP-1"], wp)
        self.assertNotIn("--no-audio-processing", argv)

    def test_mouse_true_omits(self) -> None:
        wp = _rep_wp()
        wp["MOUSE"] = True
        _, argv = invocation.build_mirror_argv("/e", "/a", ["DP-1"], wp)
        self.assertNotIn("--disable-mouse", argv)

    def test_fullscreen_pause_false_emits(self) -> None:
        wp = _rep_wp()
        wp["FULLSCREEN_PAUSE"] = False
        _, argv = invocation.build_mirror_argv("/e", "/a", ["DP-1"], wp)
        self.assertIn("--no-fullscreen-pause", argv)

    def test_fullscreen_pause_true_omits(self) -> None:
        wp = _rep_wp()
        wp["FULLSCREEN_PAUSE"] = True
        _, argv = invocation.build_mirror_argv("/e", "/a", ["DP-1"], wp)
        self.assertNotIn("--no-fullscreen-pause", argv)

    def test_fsp_empty_inherits_global_false_emits(self) -> None:
        # FSP "" + global pause_on_fullscreen=False -> effective False -> emit (watcher inherit).
        wp = _rep_wp()
        wp["FULLSCREEN_PAUSE"] = ""
        _, argv = invocation.build_mirror_argv(
            "/e", "/a", ["DP-1"], wp, pause_on_fullscreen=False
        )
        self.assertIn("--no-fullscreen-pause", argv)

    def test_fsp_empty_inherits_global_true_omits(self) -> None:
        # FSP "" + global pause_on_fullscreen=True -> effective True -> emit nothing.
        wp = _rep_wp()
        wp["FULLSCREEN_PAUSE"] = ""
        _, argv = invocation.build_mirror_argv(
            "/e", "/a", ["DP-1"], wp, pause_on_fullscreen=True
        )
        self.assertNotIn("--no-fullscreen-pause", argv)

    def test_fsp_false_always_emits_regardless_of_global(self) -> None:
        # Explicit FSP "false" -> emit even when the global says pause-on-fullscreen=True.
        for g in (True, False):
            wp = _rep_wp()
            wp["FULLSCREEN_PAUSE"] = "false"
            _, argv = invocation.build_mirror_argv(
                "/e", "/a", ["DP-1"], wp, pause_on_fullscreen=g
            )
            self.assertIn("--no-fullscreen-pause", argv)

    def test_props_emitted_sorted(self) -> None:
        # Insertion order is deliberately non-alphabetical; argv must come out sorted by key
        # so it is byte-identical to the watcher's `compgen -v PROP_` (alphabetical) order.
        wp = _rep_wp()
        wp["props"] = {"zoom": "1.5", "alpha": "0.8", "brightness": "0.2"}
        _, argv = invocation.build_mirror_argv("/e", "/a", ["DP-1"], wp)
        vals = [argv[i + 1] for i, a in enumerate(argv) if a == "--set-property"]
        self.assertEqual(vals, ["alpha=0.8", "brightness=0.2", "zoom=1.5"])

    def test_bg_fallback_used_when_empty(self) -> None:
        # Empty BG -> --bg falls back to bg_fallback (the wallpaper id), like the watcher.
        wp = _rep_wp()
        wp["BG"] = ""
        _, argv = invocation.build_mirror_argv(
            "/e", "/a", ["DP-1"], wp, bg_fallback="wp42"
        )
        self.assertEqual(argv[-2], "--bg")
        self.assertEqual(argv[-1], "wp42")

    def test_bg_fallback_ignored_when_bg_present(self) -> None:
        wp = _rep_wp()
        _, argv = invocation.build_mirror_argv(
            "/e", "/a", ["DP-1"], wp, bg_fallback="wp42"
        )
        self.assertEqual(argv[-1], "1234567890")

    def test_empty_skip_no_render_debug(self) -> None:
        wp = _rep_wp()
        wp["SKIP"] = ""
        _, argv = invocation.build_mirror_argv("/e", "/a", ["DP-1"], wp)
        self.assertNotIn("--render-debug", argv)

    def test_empty_props_no_set_property(self) -> None:
        wp = _rep_wp()
        wp["props"] = {}
        _, argv = invocation.build_mirror_argv("/e", "/a", ["DP-1"], wp)
        self.assertNotIn("--set-property", argv)

    def test_string_bools_tolerated(self) -> None:
        # Raw shell-string conf values (e.g. before typed coercion) must still work.
        wp = _rep_wp()
        wp["AUTOMUTE"] = "false"
        wp["MOUSE"] = "true"
        wp["VOLUME"] = "0"
        _, argv = invocation.build_mirror_argv("/e", "/a", ["DP-1"], wp)
        self.assertIn("--noautomute", argv)
        self.assertNotIn("--disable-mouse", argv)
        self.assertIn("--silent", argv)


class TestPreviewArgv(unittest.TestCase):
    def test_window_replaces_screen_root(self) -> None:
        env, argv = invocation.build_preview_argv("/e", "/a", _rep_wp())
        self.assertIn("--window", argv)
        self.assertEqual(argv[argv.index("--window") + 1], "0x0x960x540")
        self.assertNotIn("--screen-root", argv)
        # same head/tail rules: scaling before window before bg; bg last.
        self.assertLess(argv.index("--scaling"), argv.index("--window"))
        self.assertLess(argv.index("--window"), argv.index("--bg"))
        self.assertEqual(argv[-2], "--bg")
        self.assertEqual(env, {"LWE_CC": "1.02 1.52 2 -0.1", "LWE_TIMESCALE": "0.3"})

    def test_custom_geometry(self) -> None:
        _, argv = invocation.build_preview_argv("/e", "/a", _rep_wp(), geometry="0x0x1920x1080")
        self.assertEqual(argv[argv.index("--window") + 1], "0x0x1920x1080")


if __name__ == "__main__":
    unittest.main(verbosity=2)
