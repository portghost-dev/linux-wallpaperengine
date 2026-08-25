"""Single source of truth for schema keys, defaults, enums, theme tokens, and the
(corrected, box-verified) engine facts. Every other module imports from here; nothing
in here imports from the rest of the package, so this file has no internal dependencies.

Verified facts (see docs/findings.md) override the spec where they conflict.
"""
from __future__ import annotations

WALLPAPER_ENGINE_APPID = 431960
ENGINE_SERVICE = "lwe-engine.service"
PARTICLE_OBJECT_WARN = 50

OBJECT_TYPES = ("image", "particle", "sound", "text", "light", "model", "effect", "generic")
WALLPAPER_TYPES = ("scene", "video", "web")

ORDERS = ("shuffle", "random", "sequential")
PLAYLIST_MODES = ("shuffle", "random", "sequential", "static")
PLAYLIST_UNITS = ("min", "s")
TRANSITIONS = ("hardcut", "dimmask", "crossfade")
MONITOR_MODES = ("mirror", "per_monitor", "span")
ACTIVE_CHECKS = ("auto", "hyprland", "x11", "generic")
LOG_LEVELS = ("error", "warn", "info", "debug")
SCALINGS = ("default", "stretch", "fit", "fill")
CLAMPS = ("clamp", "border", "repeat")  # engine flag is --clamp (NOT --clamping)
# Colour-correction modes stored in wp CC_MODE. "" = the key is absent.
# "preset" is the legacy spelling of "none" and still reads as None.
CC_MODES = ("none", "preset", "custom")
ACQUIRE_METHODS = ("client", "steamcmd")
LAYERS = ("background", "bottom", "top", "overlay")  # wlr-layer-shell anchor (engine --layer)
HWDECS = ("no", "auto")  # vendor-specific decoders cut (S-12.5): universal only                     # video decode path (LWE_HWDEC)
TEXTURE_DETAILS = ("auto", "full")  # mip residency: display-matched resident chains vs authored     # (LWE_TEXDETAIL)
UI_MODES = ("normal", "advanced")
DETECT_MODES = ("manual", "launch", "interval", "watch")
DETECT_TYPES = ("all", "scene", "video")
STORAGE_POLICIES = ("copy", "reference")
PAUSE_RECOVERY_CONDITIONS = ("off", "fullscreen", "whitelist", "both")
PAUSE_RECOVERY_ACTIONS = ("pause", "close")  # pause = SIGSTOP (VRAM retained); close = kill (VRAM freed)
# daemon-era fullscreen policy (engine enum FullscreenBehavior). off = keep playing;
# pause = freeze the scene, surfaces + VRAM retained, instant resume; stop = release the
# outputs and free the resources, re-acquired when the fullscreen window clears.
FULLSCREEN_BEHAVIORS = ("off", "pause", "stop")

AUDIO_DIAL_KEYS = {
    "audio_gain": "ENGINE_AUDIO_GAIN",
    "classic_k": "ENGINE_CLASSIC_K",
    "classic_exp": "ENGINE_CLASSIC_EXP",
}
# The env var each dial is emitted as by engine/daemon_unit.build_env_content.
AUDIO_DIAL_ENV = {
    "ENGINE_AUDIO_GAIN": "LWE_AUDIOGAIN",
    "ENGINE_CLASSIC_K": "LWE_CLASSICK",
    "ENGINE_CLASSIC_EXP": "LWE_CLASSICEXP",
}

SCHEDULE_UI = False

SETTINGS_SCHEMA: dict[str, dict] = {
    "ROTATION_ENABLED": {"type": "bool", "default": True},
    "ORDER": {"type": "enum", "default": "shuffle", "choices": ORDERS},
    "INTERVAL": {"type": "int", "default": 900, "min": 60, "max": 7200},
    "SCHEDULE_ENABLED": {"type": "bool", "default": False},
    "SCHEDULE": {"type": "packed", "default": ""},  # "HH:MM=playlist-slug;HH:MM=playlist-slug"
    "ACTIVE_PLAYLIST": {"type": "str", "default": ""},  # slug under playlists/ (v1.0 named playlists)
    # engine / monitors
    "PAUSE_ON_LOCK": {"type": "bool", "default": True},
    "PAUSE_ON_FULLSCREEN": {"type": "bool", "default": False},
    "FULLSCREEN_BEHAVIOR": {"type": "enum_or_empty", "default": "", "choices": FULLSCREEN_BEHAVIORS},
    "APP_CONDITION_BEHAVIOR": {"type": "enum", "default": "off", "choices": ("off", "pause", "stop")},
    "PAUSE_RECOVERY_CONDITION": {"type": "enum", "default": "off", "choices": PAUSE_RECOVERY_CONDITIONS},
    "PAUSE_RECOVERY_ACTION": {"type": "enum", "default": "pause", "choices": PAUSE_RECOVERY_ACTIONS},
    # engine-global defaults (Settings > Engine); a per-wallpaper conf value wins where set
    "ENGINE_FPS": {"type": "int_or_empty", "default": ""},  # "" = engine default
    "ENGINE_VOLUME": {"type": "int", "default": 15, "min": 0, "max": 100},
    "ENGINE_SCALING": {"type": "enum", "default": "default", "choices": SCALINGS},
    "ENGINE_CLAMP": {"type": "enum_or_empty", "default": "", "choices": CLAMPS},
    "ENGINE_LAYER": {"type": "enum", "default": "bottom", "choices": LAYERS},
    "ENGINE_HWDEC": {"type": "enum", "default": "no", "choices": HWDECS},
    "ENGINE_TEXCOMP": {"type": "bool", "default": True},
    "TEXTURE_DETAIL": {"type": "enum", "default": "auto", "choices": TEXTURE_DETAILS},
    "ENGINE_TIMESCALE": {"type": "float", "default": 1.0},
    # Audio response dials. ENGINE-NATIVE values - the
    # same units set-tuning takes and the same units editor.AUDIO_DIALS maps to and from.
    # The 0..1 "quality" face is a control concern and is never persisted. Defaults are the
    # measured calibrated numbers, which is also what the live engine-env carries.
    "ENGINE_AUDIO_GAIN": {"type": "float", "default": 3.0},
    "ENGINE_CLASSIC_K": {"type": "float", "default": 0.7},
    "ENGINE_CLASSIC_EXP": {"type": "float", "default": 2.6},
    "AUTOMUTE_DEFAULT": {"type": "bool", "default": True},
    "AUDIO_REACTIVE_DEFAULT": {"type": "bool", "default": False},
    "MOUSE_DEFAULT": {"type": "bool", "default": False},
    "PARALLAX_DEFAULT": {"type": "bool", "default": True},
    "PARTICLES_DEFAULT": {"type": "bool", "default": True},
    "CLOSE_TO_TRAY": {"type": "bool", "default": True},
    "DETECT_MODE": {"type": "enum", "default": "watch", "choices": DETECT_MODES},
    # detection period for DETECT_MODE=interval, in SECONDS. The floor exists because
    # every pass scandirs the workshop root; 15s keeps a misconfigured box harmless.
    "DETECT_INTERVAL_SEC": {"type": "int", "default": 60, "min": 15, "max": 86400},
    "REVIEW_REQUIRED": {"type": "bool", "default": True},
    "STORAGE_POLICY": {"type": "enum", "default": "copy", "choices": STORAGE_POLICIES},
    "ENGINE_BIN": {"type": "path", "default": ""},
    "ASSETS_DIR": {"type": "path", "default": ""},
    "WALLPAPERS_DIR": {"type": "path", "default": ""},
    "WORKSHOP_DIR": {"type": "path", "default": ""},
    "STEAM_DIR": {"type": "path", "default": ""},
    # deck session overrides (v1.0 4.3 override icon row). The app writes these when the
    # deck toggles flip and resets all four to false on quit; the resolver forces the
    # matching engine flags on every launch while one is set.
    "OVERRIDE_MUTE": {"type": "bool", "default": False},
    "OVERRIDE_AUDIO_OFF": {"type": "bool", "default": False},
    "OVERRIDE_PARALLAX_OFF": {"type": "bool", "default": False},
    "OVERRIDE_MOUSE_OFF": {"type": "bool", "default": False},
}

# --------------------------------------------------------------------------------------
# wp/<id>.conf schema (Tier A). PROP_<name> keys are dynamic (not listed here).
# The config key CLAMPING is kept as the spec names it; it maps to the engine flag --clamp.
# --------------------------------------------------------------------------------------
WP_SCHEMA: dict[str, dict] = {
    "BG": {"type": "str", "default": ""},  # required in practice; payload id or abs path
    "TYPE": {"type": "enum", "default": "scene", "choices": WALLPAPER_TYPES},
    "SCALING": {"type": "enum", "default": "default", "choices": SCALINGS},
    "FPS": {"type": "int_or_empty", "default": ""},
    "SPEED": {"type": "float", "default": 1.0},
    "CC": {"type": "str", "default": "1 1 1 0"},
    "CC_MODE": {"type": "enum_or_empty", "default": "", "choices": CC_MODES},
    # per-wallpaper audio dials, engine-native units; all three absent = inherit globals
    "AUDIO_GAIN": {"type": "float", "default": 3.0},
    "CLASSIC_K": {"type": "float", "default": 0.7},
    "CLASSIC_EXP": {"type": "float", "default": 2.6},
    "VOLUME": {"type": "int", "default": 0},
    "CLAMPING": {"type": "enum_or_empty", "default": "", "choices": CLAMPS},
    "AUTOMUTE": {"type": "bool", "default": True},
    "AUDIO_REACTIVE": {"type": "bool", "default": False},
    "MOUSE": {"type": "bool", "default": False},
    "FULLSCREEN_PAUSE": {"type": "bool_or_empty", "default": ""},  # "" = inherit global
    "MONITORS": {"type": "str", "default": "all"},
    "SKIP": {"type": "str", "default": ""},  # space-separated object ids
}
WP_PROP_PREFIX = "PROP_"

# --------------------------------------------------------------------------------------
# playlists/<slug>.conf schema (Tier A). Design v1.0 4.3: a playlist = {name, membership
# set, mode, interval}. MEMBERS is a space-separated id list - shell consumers word-split it
# natively (`for id in $MEMBERS`), no jq. INTERVAL is canonical SECONDS; UNIT is the deck
# strip's persisted display unit (value entry 1-9999 in that unit -> 1..599940 s).
# --------------------------------------------------------------------------------------
PLAYLIST_SCHEMA: dict[str, dict] = {
    "NAME": {"type": "str", "default": ""},
    "MODE": {"type": "enum", "default": "shuffle", "choices": PLAYLIST_MODES},
    "INTERVAL": {"type": "int", "default": 900, "min": 1, "max": 599940},
    "UNIT": {"type": "enum", "default": "min", "choices": PLAYLIST_UNITS},
    "MEMBERS": {"type": "str", "default": ""},
}
DEFAULT_PLAYLIST_NAME = "All wallpapers"

DISCOVER_DEFAULTS = {"apiKey": "", "acquireMethod": "client", "steamcmdPath": ""}
THEME_DEFAULTS = {"preset": "True Black", "accent": "", "followSystem": False}

# --------------------------------------------------------------------------------------
# Theme presets. Full token sets ("delta" presets expanded to complete sets).
# Keys here are the canonical token names consumed by qml/Theme.qml.
# --------------------------------------------------------------------------------------
_TRUE_BLACK = {
    "base": "#000000",
    "surface": "#141414",
    "surfaceVariant": "#1E1E1E",
    "inputWell": "#0D0D0D",       # raw/mono input fields (design v1.0 1)
    "border": "#1FFFFFFF",        # rgba(255,255,255,0.12) - true alpha, composites on any surface
    "borderStrong": "#33FFFFFF",  # rgba(255,255,255,0.20)
    "hoverWash": "#0FFFFFFF",     # rgba(255,255,255,0.06)
    "activeWash": "#14FFFFFF",    # rgba(255,255,255,0.08)
    "scrimPlate": "#8C000000",    # rgba(0,0,0,0.55) - plates over imagery only
    "scrimHover": "#59000000",    # rgba(0,0,0,0.35) - hover dim over imagery
    "textPrimary": "#F2F2F2",
    "textSecondary": "#A0A0A0",
    "textTertiary": "#6E6E6E",
    "textMutedBody": "#D4D4D4",   # muted body copy
    "accent": "#7F77DD",
    "onAccent": "#0D0D12",        # dark text on the accent (design v1.0 1)
    "danger": "#E24B4A",
    "warning": "#EF9F27",
    "success": "#5DCAA5",
}
# selectionWash / dangerWash are DERIVED in theme_cfg.resolve_tokens from the final
# accent/danger (accent overrides and matugen must recompute them) - not preset keys.
THEME_PRESETS: dict[str, dict] = {
    "True Black": dict(_TRUE_BLACK),
    "Charcoal": {**_TRUE_BLACK, "base": "#121212", "surface": "#1C1C1C", "surfaceVariant": "#262626"},
    "Nord-dim": {**_TRUE_BLACK, "base": "#2E3440", "surface": "#3B4252", "surfaceVariant": "#434C5E",
                 "accent": "#88C0D0", "onAccent": "#11161C"},
    "Solarized-dark": {**_TRUE_BLACK, "base": "#002B36", "surface": "#073642", "surfaceVariant": "#0A4150",
                       "accent": "#268BD2", "onAccent": "#FFFFFF"},
}
DEFAULT_THEME_PRESET = "True Black"

# matugen (Material-You) role -> theme token (matugen lacks warning/success).
MATUGEN_ROLE_MAP = {
    "base": "background",
    "surface": "surface_container",
    "surfaceVariant": "surface_container_high",
    "border": "outline_variant",
    "borderStrong": "outline",
    "textPrimary": "on_background",
    "textSecondary": "on_surface_variant",
    "textTertiary": "outline",
    "accent": "primary",
    "onAccent": "on_primary",
    "danger": "error",
    # warning / success: no matugen role -> fall back to preset constants
}
MATUGEN_FALLBACK_TOKENS = ("warning", "success")
DEFAULT_MATUGEN_PATH = "quickshell/user/generated/colors.json"  # under $XDG_STATE_HOME / ~/.local/state

# UI spacing/type scale - consumed by Theme.qml.
WINDOW_DEFAULT = (1280, 700)
WINDOW_MIN = (1280, 700)

ENGINE_FLAGS = {
    "scaling": "--scaling",       # default|stretch|fit|fill ; place BEFORE --screen-root list (mirror)
    "screen_root": "--screen-root",
    "bg": "--bg",
    "fps": "--fps",
    "volume": "--volume",
    "silent": "--silent",
    "clamp": "--clamp",           # CORRECTED (spec said --clamping)
    "noautomute": "--noautomute",
    "no_audio": "--no-audio-processing",
    "disable_mouse": "--disable-mouse",
    "no_fullscreen_pause": "--no-fullscreen-pause",
    "set_property": "--set-property",
    "render_debug": "--render-debug",
    "assets_dir": "--assets-dir",
    "list_properties": "--list-properties",
    "dump_structure": "-z",       # long form is --dump-structure; NOTE: segfaults on exit, unused for typing
    "window": "--window",         # XxYxWxH - editor live preview
    "layer": "--layer",           # background|bottom|top|overlay (default bottom)
    "screenshot": "--screenshot",
}
SKIP_OBJECT_DEBUG = "skip-object="  # --render-debug skip-object=<id>

# env vars (the two custom engine patches)
ENV_CC = "LWE_CC"            # "b c s h"  (identity "1 1 1 0")
ENV_TIMESCALE = "LWE_TIMESCALE"  # float (identity "1")

STEAM_QUERYFILES_URL = "https://api.steampowered.com/IPublishedFileService/QueryFiles/v1/"
STEAM_QUERY_TYPE_BROWSE = 9   # RankedByTotalUniqueSubscriptions (most-popular default)
STEAM_QUERY_TYPE_SEARCH = 12  # RankedByTextSearch (requires search_text)
STEAM_QUERY_TYPE_RECENT = 1   # RankedByPublicationDate
STEAM_PER_PAGE = 30
