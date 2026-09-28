"""The show resolver: a wallpaper's conf, the global settings and the session overrides
resolved into the engine's show arguments. Plain Python over the store; no Qt import.
"""
from __future__ import annotations

import math
import os
from typing import Any

from .. import constants as C
from ..discovery import project
from ..storage import paths, settings, wp


def _wallpapers_dir() -> str:
    """Current WALLPAPERS_DIR from settings (falls back to the resolved default)."""
    try:
        return str(settings.load().get("WALLPAPERS_DIR") or paths.default_wallpapers_dir())
    except Exception:
        return str(paths.default_wallpapers_dir())


def _identity_dir(wid: str, wallpapers_dir: str) -> str:
    """Where a row's IDENTITY (title, preview, type) is read from - which is NOT always
    where it RENDERS from. A preset (dependency + preset overlay) renders through its
    base via BG, but its title/preview live in its OWN dir; reading identity from BG
    would give the base's name and preview. Order: own library copy, then
    the item's own workshop dir, then BG as the legacy fallback (a plain reference item
    whose own dir IS its render dir)."""
    own = os.path.join(wallpapers_dir, wid)
    if os.path.isdir(own):
        return own
    try:
        ws = str(settings.load().get("WORKSHOP_DIR") or paths.detect_workshop_dir())
        ws_own = os.path.join(ws, wid)
        if os.path.isdir(ws_own):
            return ws_own
    except Exception:
        pass
    try:
        bg = str(wp.load_set(wid).get("BG", "") or "")
        if bg and os.path.isdir(bg):
            return bg
    except Exception:
        pass
    return own


def _conf_true(value: Any, default: bool) -> bool:
    """Shell-parity boolean coercion for conf/settings values ('true'/'1'/'yes' family)."""
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def resolve_fullscreen_behavior(s: dict[str, Any], conf: dict[str, Any] | None = None) -> str:
    """The effective fullscreen policy as the engine spells it: off | pause | stop.

    Global FULLSCREEN_BEHAVIOR decides WHAT happens. Empty means the setting predates
    this control, so it is derived from the legacy pause-and-recovery pair - an
    existing install keeps its behavior until the user picks a mode.

    A per-wallpaper FULLSCREEN_PAUSE conf decides WHETHER this wallpaper takes part:
    false forces off, true opts in (pause when the global has nothing to say), "" or
    absent inherits. Passing conf=None asks for the global answer alone, which is what
    the live set-fullscreen push sends.
    """
    behavior = str(s.get("FULLSCREEN_BEHAVIOR") or "").strip().lower()

    if behavior not in C.FULLSCREEN_BEHAVIORS:
        legacy = (str(s.get("PAUSE_RECOVERY_ACTION") or "pause") == "pause"
                  and str(s.get("PAUSE_RECOVERY_CONDITION") or "off") in ("fullscreen", "both"))
        behavior = "pause" if legacy else "off"

    if conf is None:
        return behavior

    raw = conf.get("FULLSCREEN_PAUSE")

    if raw is None or str(raw).strip() == "":
        return behavior

    if not _conf_true(raw, False):
        return "off"

    return behavior if behavior != "off" else "pause"


def resolved_tuning(wid: str) -> dict[str, float]:
    """The three audio dials this wallpaper should run: conf override, else the globals.
    Sent via the existing set-tuning verb after a successful show."""
    out: dict[str, float] = {}
    try:
        present = wp.load_set(wid)
    except Exception:
        present = {}
    s = settings.load()
    for field, wp_key, skey, cal in (
        ("audio_gain", "AUDIO_GAIN", "ENGINE_AUDIO_GAIN", 3.0),
        ("classic_k", "CLASSIC_K", "ENGINE_CLASSIC_K", 0.7),
        ("classic_exp", "CLASSIC_EXP", "ENGINE_CLASSIC_EXP", 2.6),
    ):
        try:
            out[field] = float(present[wp_key]) if wp_key in present else float(s.get(skey, cal))
        except (TypeError, ValueError):
            out[field] = cal
    return out


def split_playlist_parts(entries: list[dict]) -> list[list[dict]]:
    """Cut `entries` into the parts one playlist-set transfer carries: every part under the
    engine's entry and byte caps, at most 64 parts. An entry too large for a part on its own
    is dropped; entries past the last part are dropped. Always at least one part."""
    import json
    parts: list[list[dict]] = []
    part: list[dict] = []
    for entry in entries:
        if len(json.dumps([entry])) > C.ENGINE_ROTATE_MAX_BYTES:
            continue
        part.append(entry)
        if len(part) > C.ENGINE_ROTATE_MAX_ENTRIES or len(json.dumps(part)) > C.ENGINE_ROTATE_MAX_BYTES:
            part.pop()
            parts.append(part)
            part = [entry]
    if part or not parts:
        parts.append(part)
    return parts[:64]


def effective_speed(wid: str, factor=None) -> float:
    """The rate the engine runs for `wid`: its conf SPEED when set, else the global speed
    (the stored ENGINE_TIMESCALE when `factor` is None), clamped to the engine's range."""
    conf_speed = wp.set_speed(wid) if wid else None
    if factor is None:
        try:
            factor = settings.load().get("ENGINE_TIMESCALE", 1.0)
        except Exception:
            factor = 1.0
    return C.resolve_speed(conf_speed, factor)


def resolve_fit(conf: dict[str, Any]) -> dict[str, float]:
    """The conf's FIT_* keys as the engine's fit object, clamped to the schema's range."""
    out: dict[str, float] = {}
    for key, name in (("FIT_ZOOM", "zoom"), ("FIT_PAN_X", "pan_x"), ("FIT_PAN_Y", "pan_y")):
        spec = C.WP_SCHEMA[key]
        try:
            value = float(conf.get(key, spec["default"]))
        except (TypeError, ValueError):
            value = float(spec["default"])
        if value != value:  # NaN never reaches the engine
            value = float(spec["default"])
        out[name] = max(float(spec["min"]), min(float(spec["max"]), value))
    return out


def resolve_show_args(wid: str) -> tuple[str, dict[str, Any]]:
    """Resolve a wallpaper's FULL per-show vocabulary: conf overrides first, engine-global
    settings fill the gaps, session overrides win last. Returns (engine_wid, kwargs for
    api_client.show).

    Every value is sent RESOLVED - the engine never sees a conf. This is the
    single resolution point: showNow uses it, and the rotation-set push reuses
    it for every playlist entry.
    """
    s = settings.load()
    conf: dict[str, Any] = {}
    try:
        conf = wp.load_set(wid)
    except Exception:
        pass  # unreadable conf must never kill a show; identity is safe (is_safe_wid gated)

    args: dict[str, Any] = {}
    engine_wid = wid

    # color correction: an absent CC is the authored look (derive_cc over the item's own
    # project.json), NOT identity - a published preset's grading IS the wallpaper
    cc = [1.0, 1.0, 1.0, 0.0]
    cc_str = str(conf.get("CC") or "")
    if not cc_str:
        try:
            raw = (project.read(_identity_dir(wid, _wallpapers_dir())) or {}).get("raw")
            if isinstance(raw, dict):
                preset = raw.get("preset")
                cc_str = project.derive_cc(preset if isinstance(preset, dict) else raw)
        except Exception:
            cc_str = ""
    try:
        parts = [float(x) for x in str(cc_str or "1 1 1 0").split()]
        if len(parts) == 4:
            cc = parts
    except (TypeError, ValueError):
        pass
    args["cc"] = cc

    args["speed"] = C.resolve_speed(wp.set_speed(wid), s.get("ENGINE_TIMESCALE"))

    raw_props = conf.get("props")
    if isinstance(raw_props, dict) and raw_props:
        args["properties"] = {str(k): str(v) for k, v in raw_props.items()}

    # presets have no project of their own: conf BG names the base the engine loads
    bg = str(conf.get("BG") or "").strip()
    if bg:
        base = os.path.basename(bg.rstrip("/"))
        if paths.is_safe_wid(base):
            engine_wid = base

    # Empty clamp = the engine's launch default.
    args["scaling"] = str(conf.get("SCALING") or s.get("ENGINE_SCALING") or "default")
    clamp = str(conf.get("CLAMPING") or s.get("ENGINE_CLAMP") or "").strip()
    if clamp:
        args["clamp"] = clamp

    # the wallpaper layer of the fit window, always sent resolved so an omitted key is
    # identity on the engine too; the lane layer is the engine's own (set_fit)
    args["fit"] = resolve_fit(conf)

    try:
        volume_present = "VOLUME" in wp.load_set(wid)
    except Exception:
        volume_present = True
    if volume_present:
        try:
            volume = int(str(conf.get("VOLUME")).strip())
        except (TypeError, ValueError):
            volume = 0
    else:
        # same units and source as the popup's global Volume row (pushed via set_volume)
        try:
            volume = int(str(s.get("ENGINE_VOLUME", 15)).strip())
        except (TypeError, ValueError):
            volume = 15
    if _conf_true(s.get("OVERRIDE_MUTE"), False):
        volume = 0
    args["volume"] = max(0, min(volume, 128))

    audio = _conf_true(conf.get("AUDIO_REACTIVE"), _conf_true(s.get("AUDIO_REACTIVE_DEFAULT"), False))
    if _conf_true(s.get("OVERRIDE_AUDIO_OFF"), False):
        audio = False
    args["audio_processing"] = audio

    mouse = _conf_true(conf.get("MOUSE"), _conf_true(s.get("MOUSE_DEFAULT"), False))
    if _conf_true(s.get("OVERRIDE_MOUSE_OFF"), False):
        mouse = False
    args["mouse"] = mouse

    args["automute"] = _conf_true(conf.get("AUTOMUTE"), _conf_true(s.get("AUTOMUTE_DEFAULT"), True))

    # fullscreen policy, resolved to the engine's three-state vocabulary. The global FULLSCREEN_BEHAVIOR says
    # WHAT happens; the per-wallpaper conf says WHETHER this wallpaper takes part ("" = inherit). A wallpaper
    # that opts in while the global is off still gets pause from that flag.
    args["fullscreen_behavior"] = resolve_fullscreen_behavior(s, conf)

    # the quality switches ride the show only when the wallpaper set them; absent means
    # the engine's launch environment, which is where the global setting already lives
    clamps = wp.clamp_values(conf, wid)
    for key, name in (("SSFACTOR", "ssfactor"), ("CLAMPCOMPOSITES", "clampcomposites")):
        value = clamps[key][0]
        if value is not None and math.isfinite(value):
            args[name] = max(0.0, min(4.0, value))
    texcomp = conf.get("TEXCOMP")
    if texcomp is not None and str(texcomp).strip() != "":
        args["texcomp"] = _conf_true(texcomp, True)
    detail = str(conf.get("TEXTURE_DETAIL") or "").strip()
    if detail in C.TEXTURE_DETAILS:
        args["texdetail"] = detail
    # alias kept so an older engine still reads a truthful boolean off the show
    args["fullscreen_pause"] = args["fullscreen_behavior"] != "off"

    skips = wp.skip_ids(conf.get("SKIP") or "")
    if skips:
        args["skip_objects"] = skips

    return engine_wid, args
