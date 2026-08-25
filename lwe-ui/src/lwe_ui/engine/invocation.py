"""Build the linux-wallpaperengine argv + env for mirror rendering and editor preview.

Pure string-building only - this module NEVER executes anything. The retired bash watcher and
the GUI editor preview both build their command lines from the same flag rules so there is a
single definition of "how we invoke the engine".

All flag spellings come from `constants.ENGINE_FLAGS` (box-verified; e.g. the clamp flag is
`--clamp`, NOT `--clamping`). The proven mirror argv order (from the original watcher,
docs/findings.md) is:

    engine_bin
    --assets-dir <assets_dir>
    [--fps <FPS>]                       # omitted when FPS == ""
    --scaling <SCALING>                 # MUST precede the --screen-root list
    (--screen-root <output>) x outputs
    --silent | --volume <N>             # VOLUME 0 -> --silent, else --volume N
    [--clamp <CLAMPING>]                # omitted when CLAMPING == ""
    [--noautomute]                      # AUTOMUTE false -> emit
    [--no-audio-processing]             # AUDIO_REACTIVE false -> emit
    [--disable-mouse]                   # MOUSE false -> emit
    [--no-fullscreen-pause]             # FULLSCREEN_PAUSE false -> emit ("" inherits, emit nothing)
    (--set-property k=v) x props
    (--render-debug skip-object=<id>) x SKIP
    --bg <BG>                           # ALWAYS last

env is the two custom-patch variables: {LWE_CC: wp.CC, LWE_TIMESCALE: wp.SPEED}.

`build_preview_argv` is identical except a single `--window XxYxWxH` replaces the
`--screen-root` list (engine live preview).
"""
from __future__ import annotations

from .. import constants as C


def _as_bool(value: object) -> bool:
    """Coerce a wp value to bool, tolerating the shell-string forms a raw conf may carry."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _tail_flags(wp: dict, pause_on_fullscreen: bool = False) -> list[str]:
    """The shared flag block that follows the screen-roots / --window and precedes --bg.

    Order: volume, clamp, noautomute, no-audio-processing, disable-mouse,
    no-fullscreen-pause, set-propertyxprops, render-debug skip-objectxSKIP.

    `pause_on_fullscreen` is the GLOBAL PAUSE_ON_FULLSCREEN setting (default false), used to
    resolve an EMPTY wp FULLSCREEN_PAUSE - matching the original launch_one inherit.
    """
    flags = C.ENGINE_FLAGS
    argv: list[str] = []

    volume = wp.get("VOLUME", C.WP_SCHEMA["VOLUME"]["default"])
    try:
        volume_n = int(volume)
    except (TypeError, ValueError):
        volume_n = 0
    if volume_n == 0:
        argv.append(flags["silent"])
    else:
        argv += [flags["volume"], str(volume_n)]

    clamping = str(wp.get("CLAMPING", C.WP_SCHEMA["CLAMPING"]["default"]) or "")
    if clamping != "":
        argv += [flags["clamp"], clamping]

    if not _as_bool(wp.get("AUTOMUTE", C.WP_SCHEMA["AUTOMUTE"]["default"])):
        argv.append(flags["noautomute"])

    if not _as_bool(wp.get("AUDIO_REACTIVE", C.WP_SCHEMA["AUDIO_REACTIVE"]["default"])):
        argv.append(flags["no_audio"])

    if not _as_bool(wp.get("MOUSE", C.WP_SCHEMA["MOUSE"]["default"])):
        argv.append(flags["disable_mouse"])

    # FULLSCREEN_PAUSE: 'true'/'false' is explicit; an EMPTY wp value inherits the GLOBAL
    # pause_on_fullscreen. Emit --no-fullscreen-pause iff the effective value is False
    # (fsp="" -> fsp=PAUSE_ON_FULLSCREEN; emit when fsp == false).
    fsp = wp.get("FULLSCREEN_PAUSE", C.WP_SCHEMA["FULLSCREEN_PAUSE"]["default"])
    if isinstance(fsp, str) and fsp.strip() == "":
        effective = pause_on_fullscreen
    elif isinstance(fsp, str) and fsp.strip().lower() in ("true", "false"):
        effective = fsp.strip().lower() == "true"
    else:
        effective = _as_bool(fsp)
    if not effective:
        argv.append(flags["no_fullscreen_pause"])

    # props -> repeated --set-property k=v (sorted, matching the original compgen -v PROP_ order)
    props = wp.get("props") or {}
    if isinstance(props, dict):
        for key, value in sorted(props.items()):
            argv += [flags["set_property"], f"{key}={value}"]

    skip = wp.get("SKIP", C.WP_SCHEMA["SKIP"]["default"]) or ""
    for obj_id in str(skip).split():
        argv += [flags["render_debug"], f"{C.SKIP_OBJECT_DEBUG}{obj_id}"]

    return argv


def _build_env(wp: dict) -> dict[str, str]:
    """The two custom engine-patch env vars: LWE_CC (color grade) and LWE_TIMESCALE (speed)."""
    cc = wp.get("CC", C.WP_SCHEMA["CC"]["default"])
    speed = wp.get("SPEED", C.WP_SCHEMA["SPEED"]["default"])
    return {
        C.ENV_CC: str(cc),
        C.ENV_TIMESCALE: str(speed),
    }


def _head_flags(engine_bin: str, assets_dir: str, wp: dict) -> list[str]:
    """engine_bin, --assets-dir, [--fps], --scaling - the block before screen-roots/--window."""
    flags = C.ENGINE_FLAGS
    argv: list[str] = [engine_bin, flags["assets_dir"], assets_dir]

    fps = wp.get("FPS", C.WP_SCHEMA["FPS"]["default"])
    fps_s = "" if fps is None else str(fps)
    if fps_s != "":
        argv += [flags["fps"], fps_s]

    # SCALING must precede the screen-root list / --window
    scaling = wp.get("SCALING", C.WP_SCHEMA["SCALING"]["default"])
    argv += [flags["scaling"], str(scaling)]

    return argv


def build_mirror_argv(
    engine_bin: str,
    assets_dir: str,
    outputs: list[str],
    wp: dict,
    pause_on_fullscreen: bool = False,
    bg_fallback: str = "",
) -> tuple[dict[str, str], list[str]]:
    """Build (env, argv) for mirror rendering (one process, one --screen-root per output).

    `wp` is a typed wp dict (storage.wp.load) with a `props: dict[str,str]`; raw shell-string
    values are tolerated. The same wallpaper renders on every output (mirror); per-monitor would
    interleave -r/-b groups, which is a multi-monitor concern, not this single-wp builder's.

    `pause_on_fullscreen` resolves an empty wp FULLSCREEN_PAUSE; `bg_fallback` (the wallpaper id)
    is used for --bg when wp BG is empty - both match the original launch_one.
    """
    flags = C.ENGINE_FLAGS
    argv = _head_flags(engine_bin, assets_dir, wp)

    for output in outputs:
        argv += [flags["screen_root"], output]

    argv += _tail_flags(wp, pause_on_fullscreen)

    # --bg is ALWAYS last; empty BG falls back to bg_fallback (the id).
    bg = str(wp.get("BG", C.WP_SCHEMA["BG"]["default"]))
    if bg == "":
        bg = bg_fallback
    argv += [flags["bg"], bg]

    return _build_env(wp), argv


def build_preview_argv(
    engine_bin: str,
    assets_dir: str,
    wp: dict,
    geometry: str = "0x0x960x540",
    pause_on_fullscreen: bool = False,
) -> tuple[dict[str, str], list[str]]:
    """Build (env, argv) for the editor live preview: a single --window XxYxWxH window.

    Identical to the mirror form except the --screen-root list is replaced by one --window.
    `pause_on_fullscreen` resolves an empty wp FULLSCREEN_PAUSE.
    """
    flags = C.ENGINE_FLAGS
    argv = _head_flags(engine_bin, assets_dir, wp)

    argv += [flags["window"], geometry]

    argv += _tail_flags(wp, pause_on_fullscreen)

    # --bg is ALWAYS last
    argv += [flags["bg"], str(wp.get("BG", C.WP_SCHEMA["BG"]["default"]))]

    return _build_env(wp), argv
