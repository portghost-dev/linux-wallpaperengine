"""Headless bench helpers - the test-mode render-source + argv math.

This module is the pure, GUI-free core of the bench's "Test" action. The real QProcess launch,
the pause-lease renewal timer, and the stop/commit/reject buttons live in the GUI; everything
here is string/path math so the conf round-trip and the *exact* test argv are
headless-verifiable.

Two facts make the bench's test "what you test is what you get":
  * The test renders the wallpaper's OWN conf parameters through the SAME argv builder the
    live launches use (engine.invocation.build_mirror_argv), so the produced argv is byte-identical
    to committed rotation (mirror order: --scaling before the --screen-root list, --clamp not
    --clamping, --bg always last).
  * Pending items aren't in the library yet, so they render straight from the Steam workshop
    tree (WORKSHOP_DIR/<id>); a re-benched good item renders from the library (WALLPAPERS_DIR/<id>).
    The conf's BG is set to that full path here (the builder accepts `--bg <path>` as well as
    `--bg <id>`); the BG is rewritten to the library `--bg <id>` reference only at first-commit.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine import invocation

# Draft `source` values (which on-disk tree the test renders from), per 2.6.
SOURCE_PENDING = "pending"   # not yet in the library -> render from WORKSHOP_DIR/<id>
SOURCE_GOOD = "good"         # already in the library  -> render from WALLPAPERS_DIR/<id>


def resolve_render_bg(
    wid: str,
    source: str,
    workshop_dir: str | Path,
    wallpapers_dir: str | Path,
) -> str:
    """Return the full --bg path the test should render from, by source.

    pending -> str(workshop_dir / wid)   (Steam subscription tree; not yet copied to the library)
    good    -> str(wallpapers_dir / wid) (the committed library copy)

    Any other `source` is rejected loudly - the caller must pass one of the two known states so a
    typo can't silently render from the wrong tree.
    """
    if source == SOURCE_PENDING:
        return str(Path(workshop_dir) / wid)
    if source == SOURCE_GOOD:
        return str(Path(wallpapers_dir) / wid)
    raise ValueError(f"unknown bench render source: {source!r} (expected 'pending' or 'good')")


def build_test_argv(
    engine_bin: str,
    assets_dir: str,
    outputs: list[str],
    wp_cfg: dict[str, Any],
    *,
    source: str,
    workshop_dir: str | Path,
    wallpapers_dir: str | Path,
    pause_on_fullscreen: bool = False,
) -> tuple[dict[str, str], list[str]]:
    """Build (env, argv) for a foreground bench test launch - the canonical mirror argv.

    `wp_cfg` is a typed wp dict (storage.wp.load shape, with a `props: dict[str,str]`). Its BG is
    overridden with the render-source path resolved per `source` (resolve_render_bg) before
    delegating to engine.invocation.build_mirror_argv - guaranteeing the test argv is IDENTICAL
    to a live launch for the same conf.

    The original `wp_cfg` is NOT mutated (a shallow copy carries the BG override), so this can be
    called repeatedly across the test/relaunch loop without side effects on the caller's dict.
    """
    # The conf's BG holds the wallpaper id-or-path; the test always renders from the resolved
    # tree path. An empty BG must NOT silently resolve to the tree ROOT - fail loud.
    bg_or_id = str(wp_cfg.get("BG") or "")
    if not bg_or_id:
        raise ValueError("build_test_argv: conf has no BG (wallpaper id) - refusing to render the workshop/library root")
    cfg = dict(wp_cfg)
    cfg["props"] = dict(wp_cfg.get("props") or {})  # isolate nested props (shallow dict() aliases it)
    cfg["BG"] = resolve_render_bg(bg_or_id, source, workshop_dir, wallpapers_dir)
    return invocation.build_mirror_argv(
        engine_bin,
        assets_dir,
        outputs,
        cfg,
        pause_on_fullscreen=pause_on_fullscreen,
    )
