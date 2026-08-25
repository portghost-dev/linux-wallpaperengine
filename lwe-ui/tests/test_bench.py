"""Self-verification for the bench backend: bench.py argv/env building.

Pure / headless. NOTHING here signals a live process or spawns the engine: the bench
tests are string/path math against tempfile dirs.

Run: export PYTHONPATH=src && python3 tests/test_bench.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from lwe_ui import bench  # noqa: E402
from lwe_ui import constants as C  # noqa: E402


def _draft(wid: str = "1234567890") -> dict:
    """A representative draft buffer dict (storage.wp.load shape) - BG holds the id."""
    return {
        "BG": wid,
        "TYPE": "scene",
        "SCALING": "fill",
        "FPS": 30,
        "SPEED": 0.5,
        "CC": "1 1 1 0",
        "VOLUME": 0,
        "CLAMPING": "",
        "AUTOMUTE": True,
        "AUDIO_REACTIVE": False,
        "MOUSE": False,
        "FULLSCREEN_PAUSE": "",
        "MONITORS": "all",
        "SKIP": "",
        "props": {"schemecolor": "0.2 0.4 0.6"},
    }


class TestResolveRenderBg(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="lwe-bench-test-")
        self.workshop = Path(self.tmp) / "workshop"
        self.wallpapers = Path(self.tmp) / "wallpapers"

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pending_uses_workshop_path(self) -> None:
        got = bench.resolve_render_bg("999", "pending", self.workshop, self.wallpapers)
        self.assertEqual(got, str(self.workshop / "999"))

    def test_good_uses_wallpapers_path(self) -> None:
        got = bench.resolve_render_bg("999", "good", self.workshop, self.wallpapers)
        self.assertEqual(got, str(self.wallpapers / "999"))

    def test_unknown_source_raises(self) -> None:
        with self.assertRaises(ValueError):
            bench.resolve_render_bg("999", "staged", self.workshop, self.wallpapers)

    def test_accepts_str_dirs(self) -> None:
        got = bench.resolve_render_bg("42", "pending", str(self.workshop), str(self.wallpapers))
        self.assertEqual(got, str(self.workshop / "42"))


class TestBuildTestArgv(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="lwe-bench-test-")
        self.workshop = Path(self.tmp) / "workshop"
        self.wallpapers = Path(self.tmp) / "wallpapers"
        self.wid = "1234567890"

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build(self, source: str):
        return bench.build_test_argv(
            "/usr/bin/linux-wallpaperengine",
            "/assets",
            ["DP-1", "DP-2", "DP-3"],
            _draft(self.wid),
            source=source,
            workshop_dir=self.workshop,
            wallpapers_dir=self.wallpapers,
        )

    def test_pending_bg_is_workshop_path_and_last_token(self) -> None:
        _, argv = self._build("pending")
        self.assertEqual(argv[-2], "--bg")
        self.assertEqual(argv[-1], str(self.workshop / self.wid))

    def test_good_bg_is_library_path_and_last_token(self) -> None:
        _, argv = self._build("good")
        self.assertEqual(argv[-2], "--bg")
        self.assertEqual(argv[-1], str(self.wallpapers / self.wid))

    def test_is_canonical_mirror_argv(self) -> None:
        # --scaling MUST precede the --screen-root list; --bg is last (2.6 mirror order).
        _, argv = self._build("pending")
        self.assertLess(argv.index("--scaling"), argv.index("--screen-root"))
        self.assertLess(argv.index("--screen-root"), argv.index("--bg"))
        self.assertEqual(argv[-2], "--bg")

    def test_clamp_spelling_not_clamping(self) -> None:
        # With CLAMPING set the flag is --clamp (corrected), never --clamping.
        d = _draft(self.wid)
        d["CLAMPING"] = "border"
        _, argv = bench.build_test_argv(
            "/e", "/a", ["DP-1"], d,
            source="pending", workshop_dir=self.workshop, wallpapers_dir=self.wallpapers,
        )
        self.assertIn("--clamp", argv)
        self.assertNotIn("--clamping", argv)
        self.assertEqual(argv[argv.index("--clamp") + 1], "border")

    def test_identical_to_invocation_builder(self) -> None:
        # The whole point of 2.6: build_test_argv must produce EXACTLY what the watcher's
        # invocation.build_mirror_argv produces for the same draft with BG rewritten.
        from lwe_ui.engine import invocation

        d = _draft(self.wid)
        env_t, argv_t = self._build("pending")
        ref = dict(d)
        ref["BG"] = str(self.workshop / self.wid)
        env_r, argv_r = invocation.build_mirror_argv(
            "/usr/bin/linux-wallpaperengine", "/assets", ["DP-1", "DP-2", "DP-3"], ref,
        )
        self.assertEqual(argv_t, argv_r)
        self.assertEqual(env_t, env_r)

    def test_does_not_mutate_draft(self) -> None:
        d = _draft(self.wid)
        before = d["BG"]
        bench.build_test_argv(
            "/e", "/a", ["DP-1"], d,
            source="pending", workshop_dir=self.workshop, wallpapers_dir=self.wallpapers,
        )
        self.assertEqual(d["BG"], before)

    def test_env_is_cc_and_timescale(self) -> None:
        env, _ = self._build("pending")
        self.assertEqual(env[C.ENV_CC], "1 1 1 0")
        self.assertEqual(env[C.ENV_TIMESCALE], "0.5")



if __name__ == "__main__":
    unittest.main(verbosity=2)
