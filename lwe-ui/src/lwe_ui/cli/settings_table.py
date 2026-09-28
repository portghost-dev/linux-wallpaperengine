"""One row per setting and per-wallpaper word: where the value is stored, how a typed word becomes
that value and a stored value becomes the typed word again, when a change reaches the engine, and
which verb module owns it.

A row's parse(word, cwd_entered=False) returns the value to store, or raises values.UsageError (exit
3) or values.Refused (exit 1). A switch gives True or False, or TOGGLE for the verb to flip inside
its transaction; a volume step gives a Step; color gives its four numbers with the hue in radians,
as engine-env holds it. format(value) prints a stored value in the words the setting takes, the hue
in degrees; an empty per-wallpaper value, which inherits, prints as "". Top level imports stdlib only;
derived_active_playlist imports engine/push.py, and with it the store, when it is called; read imports the store
when it is called.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from . import values, vocabulary

SETTINGS_FILE = "settings"
PLAYLIST_FILE = "playlist"
ENGINE_ENV = "engine-env"
STATUS = "status"
WALLPAPER_FILE = "wallpaper"

NOW = "now"
NEXT_WALLPAPER = "next wallpaper"
RESTART = "restart"
PANEL = "panel"
PANEL_START = "panel start"
NO_ENGINE = "none"

GLOBAL = "global"
WALLPAPER = "wallpaper"
PER_WALLPAPER_SCOPE = "per wallpaper"

PLAYLIST_VERBS = "cli/verbs/playlist.py"
SETTINGS_VERBS = "cli/verbs/settings.py"
WALLPAPER_VERBS = "cli/verbs/wallpaper.py"
OBJECTS_VERBS = "cli/verbs/objects.py"

TOGGLE = "toggle"


class Step(int):
    """A volume step, +N or -N; the verb applies it to the saved value."""


@dataclass(frozen=True)
class Row:
    """name: the word typed; form: GLOBAL (`<name> <value>`) or WALLPAPER (after `wallpaper <w>`);
    scope: the vocabulary's scope, or "per wallpaper"; place and key: where the value is stored, with
    field naming the part of a CC value; reach: when a change reaches the engine; wp_key: the key a
    wallpaper's own file overrides the setting with; config_only: typed only after config; owner:
    the verb module, relative to lwe_ui."""
    name: str
    form: str
    scope: str
    place: str
    key: str
    parse: Callable[..., Any]
    format: Callable[[Any], str]
    reach: str
    wp_key: str | None
    config_only: bool
    owner: str
    field: int | None = None


def _listed(words: list[str]) -> str:
    return ", ".join(words[:-1]) + " or " + words[-1]


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value == "")


def _choice(words: dict[str, str], empty: str = "") -> tuple[Callable[..., Any], Callable[[Any], str]]:
    """A fixed set of words, each standing for a stored value; a stored "" prints as empty."""
    back = {stored: word for word, stored in words.items()}

    def parse(word: str, cwd_entered: bool = False) -> str:
        if word not in words:
            raise values.UsageError(f"takes {_listed(list(words))}; got {word}")
        return words[word]

    def fmt(value: Any) -> str:
        return empty if _empty(value) else back.get(str(value), str(value))

    return parse, fmt


def _switch(invert: bool = False, toggle: bool = True) -> tuple[Callable[..., Any], Callable[[Any], str]]:
    """on and off, and toggle where allowed; invert for a key that stores the opposite."""
    def parse(word: str, cwd_entered: bool = False) -> Any:
        form = values.parse_switch(word, toggle)
        return TOGGLE if form == TOGGLE else (form == "on") != invert

    def fmt(value: Any) -> str:
        return "" if _empty(value) else ("on" if bool(value) != invert else "off")

    return parse, fmt


def _number(lo: float, hi: float) -> Callable[..., float]:
    return lambda word, cwd_entered=False: values.parse_number(word, lo, hi)


def _whole(lo: int, hi: int) -> Callable[..., int]:
    return lambda word, cwd_entered=False: values.parse_whole(word, lo, hi)


def _duration(bare: str, lo: int, hi: int) -> Callable[..., int]:
    return lambda word, cwd_entered=False: values.parse_duration(word, bare, lo, hi)


def _number_words(value: Any) -> str:
    return "" if _empty(value) else values.format_number(float(value))


def _whole_words(value: Any) -> str:
    return "" if _empty(value) else str(int(value))


def _duration_words(value: Any) -> str:
    return "" if _empty(value) else values.format_duration(int(value))


def _text(word: str, cwd_entered: bool = False) -> str:
    return word


def _text_words(value: Any) -> str:
    return "" if value is None else str(value)


def _volume(word: str, cwd_entered: bool = False) -> int:
    if word[:1] in ("+", "-"):
        return Step(values.parse_step(word))
    try:
        return values.parse_whole(word, 0, 128)
    except values.UsageError:
        raise values.UsageError(f"takes a whole number from 0 to 128, +N or -N; got {word}") from None


def _speed(word: str, cwd_entered: bool = False) -> float:
    value = values.parse_number(word, 0.0, 10.0)
    if 0.0 < value < 0.1:
        raise values.UsageError("takes 0.1 to 10; use 0 to freeze")
    return value


def _factor(word: str, cwd_entered: bool = False) -> float:
    return values.parse_factor(word)


def _ms(word: str, cwd_entered: bool = False) -> float:
    return values.parse_ms(word)


def _path(word: str, cwd_entered: bool = False) -> str:
    return values.resolve_path(word, cwd_entered)


def _radians(degrees: float) -> float:
    return degrees * math.pi / 180.0


def _degrees_words(radians: Any) -> str:
    return "" if _empty(radians) else values.format_number(round(float(radians) * 180.0 / math.pi, 6))


def _hue(word: str, cwd_entered: bool = False) -> float:
    return _radians(values.parse_number(word, -180.0, 180.0))


def _color(word: str, cwd_entered: bool = False) -> tuple[float, float, float, float]:
    brightness, contrast, saturation, hue = values.parse_color(word)
    return brightness, contrast, saturation, _radians(hue)


def _color_words(value: Any) -> str:
    if _empty(value):
        return ""
    brightness, contrast, saturation, hue = value
    return " ".join([*(values.format_number(float(v)) for v in (brightness, contrast, saturation)),
                     _degrees_words(hue)])


def _wp_fullscreen(word: str, cwd_entered: bool = False) -> Any:
    forms = {"on": True, "off": False, "inherit": ""}
    if word not in forms:
        raise values.UsageError(f"takes on, off or inherit; got {word}")
    return forms[word]


def _wp_fullscreen_words(value: Any) -> str:
    return "inherit" if _empty(value) else ("on" if value else "off")


_VOCABULARY = {row["name"]: row for row in vocabulary.SETTINGS}
_ORDER = _choice({"shuffle": "shuffle", "sequential": "sequential", "static": "static"})
_SCALING = _choice({"default": "default", "stretch": "stretch", "fit": "fit", "fill": "fill"})
_EDGE_WORDS = {"extend": "clamp", "blank": "border", "tile": "repeat"}
_FULLSCREEN = _choice({"keep": "off", "pause": "pause", "stop": "stop"})
_LAYER = _choice({"background": "background", "bottom": "bottom", "top": "top", "overlay": "overlay"})
_TEXTURE_DETAIL = _choice({"auto": "auto", "full": "full"})
_VIDEO_DECODE = _choice({"software": "no", "auto": "auto"})
_STORAGE = _choice({"copy": "copy", "reference": "reference"})
_DETECT = _choice({"manual": "manual", "launch": "launch", "timer": "interval", "watch": "watch"})
_FOLDER = (_path, _text_words)


def _setting(name: str, place: str, key: str, parse_format: tuple, reach: str,
             owner: str = SETTINGS_VERBS, wp_key: str | None = None) -> Row:
    parse, fmt = parse_format
    row = _VOCABULARY[name]
    return Row(name, GLOBAL, row["scope"], place, key, parse, fmt, reach, wp_key,
               row["typed"].startswith("config "), owner)


def _word(name: str, key: str, parse_format: tuple, reach: str = NOW, owner: str = WALLPAPER_VERBS,
          field: int | None = None) -> Row:
    parse, fmt = parse_format
    return Row(name, WALLPAPER, PER_WALLPAPER_SCOPE, WALLPAPER_FILE, key, parse, fmt, reach, None, False,
               owner, field)


_GLOBAL_ROWS = (
    _setting("order", PLAYLIST_FILE, "MODE", _ORDER, NOW, PLAYLIST_VERBS),
    _setting("interval", PLAYLIST_FILE, "INTERVAL", (_duration("m", 15, 599940), _duration_words), NOW,
             PLAYLIST_VERBS),
    _setting("playlist", SETTINGS_FILE, "ACTIVE_PLAYLIST", (_text, _text_words), NOW, PLAYLIST_VERBS),
    _setting("volume", SETTINGS_FILE, "ENGINE_VOLUME", (_volume, _whole_words), NOW, wp_key="VOLUME"),
    _setting("mute", SETTINGS_FILE, "OVERRIDE_MUTE", _switch(), NOW),
    _setting("audioreactive", SETTINGS_FILE, "OVERRIDE_AUDIO_OFF", _switch(invert=True), NOW),
    _setting("mouse", SETTINGS_FILE, "OVERRIDE_MOUSE_OFF", _switch(invert=True), NOW),
    _setting("parallax", SETTINGS_FILE, "OVERRIDE_PARALLAX_OFF", _switch(invert=True), NOW),
    _setting("audioreactivedefault", SETTINGS_FILE, "AUDIO_REACTIVE_DEFAULT", _switch(toggle=False), NOW),
    _setting("mousedefault", SETTINGS_FILE, "MOUSE_DEFAULT", _switch(toggle=False), NOW),
    _setting("parallaxdefault", SETTINGS_FILE, "PARALLAX_DEFAULT", _switch(toggle=False), NOW),
    _setting("automute", SETTINGS_FILE, "AUTOMUTE_DEFAULT", _switch(), NEXT_WALLPAPER, wp_key="AUTOMUTE"),
    _setting("particles", SETTINGS_FILE, "PARTICLES_DEFAULT", _switch(), NOW),
    _setting("fps", SETTINGS_FILE, "ENGINE_FPS", (_whole(1, 480), _whole_words), NOW),
    _setting("speed", SETTINGS_FILE, "ENGINE_TIMESCALE", (_speed, _number_words), NOW, wp_key="SPEED"),
    _setting("scaling", SETTINGS_FILE, "ENGINE_SCALING", _SCALING, NEXT_WALLPAPER, wp_key="SCALING"),
    _setting("edge", SETTINGS_FILE, "ENGINE_CLAMP", _choice(_EDGE_WORDS, empty="extend"), NEXT_WALLPAPER,
             wp_key="CLAMPING"),
    _setting("fullscreen", SETTINGS_FILE, "FULLSCREEN_BEHAVIOR", _FULLSCREEN, NOW),
    _setting("layer", SETTINGS_FILE, "ENGINE_LAYER", _LAYER, RESTART),
    _setting("resclamp", SETTINGS_FILE, "SSFACTOR", (_factor, _number_words), RESTART, wp_key="SSFACTOR"),
    _setting("effectclamp", SETTINGS_FILE, "CLAMPCOMPOSITES", (_factor, _number_words), RESTART,
             wp_key="CLAMPCOMPOSITES"),
    _setting("texturecache", SETTINGS_FILE, "ENGINE_TEXCOMP", _switch(), RESTART, wp_key="TEXCOMP"),
    _setting("texturedetail", SETTINGS_FILE, "TEXTURE_DETAIL", _TEXTURE_DETAIL, RESTART,
             wp_key="TEXTURE_DETAIL"),
    _setting("videodecode", SETTINGS_FILE, "ENGINE_HWDEC", _VIDEO_DECODE, RESTART),
    _setting("reviewrequired", SETTINGS_FILE, "REVIEW_REQUIRED", _switch(), PANEL),
    _setting("storage", SETTINGS_FILE, "STORAGE_POLICY", _STORAGE, PANEL),
    _setting("detect", SETTINGS_FILE, "DETECT_MODE", _DETECT, PANEL_START),
    _setting("detectevery", SETTINGS_FILE, "DETECT_INTERVAL_SEC", (_duration("s", 15, 86400), _duration_words),
             PANEL_START),
    _setting("libraryfolder", SETTINGS_FILE, "WALLPAPERS_DIR", _FOLDER, PANEL),
    _setting("workshopfolder", SETTINGS_FILE, "WORKSHOP_DIR", _FOLDER, PANEL),
    _setting("steamfolder", SETTINGS_FILE, "STEAM_DIR", _FOLDER, PANEL),
    _setting("assetsfolder", SETTINGS_FILE, "ASSETS_DIR", _FOLDER, RESTART),
    _setting("lightdimming", SETTINGS_FILE, "ENGINE_CLASSIC_K", (_number(0.01, 1000.0), _number_words), NOW,
             wp_key="CLASSIC_K"),
    _setting("lightfalloff", SETTINGS_FILE, "ENGINE_CLASSIC_EXP", (_number(0.5, 6.0), _number_words), NOW,
             wp_key="CLASSIC_EXP"),
    _setting("audiogain", SETTINGS_FILE, "ENGINE_AUDIO_GAIN", (_number(0.1, 20.0), _number_words), NOW,
             wp_key="AUDIO_GAIN"),
    _setting("audiosmoothing", STATUS, "audio_smooth", (_ms, _number_words), NOW),
    _setting("watchdog", ENGINE_ENV, "LWE_DEADMAN", (_duration("s", 0, 86400), _duration_words), RESTART),
    _setting("color", ENGINE_ENV, "LWE_CC", (_color, _color_words), RESTART),
)

_WORD_ROWS = (
    _word("zoom", "FIT_ZOOM", (_number(1.0, 2.0), _number_words)),
    _word("panx", "FIT_PAN_X", (_number(-1.0, 1.0), _number_words)),
    _word("pany", "FIT_PAN_Y", (_number(-1.0, 1.0), _number_words)),
    _word("brightness", "CC", (_number(0.0, 4.0), _number_words), field=0),
    _word("contrast", "CC", (_number(0.0, 4.0), _number_words), field=1),
    _word("saturation", "CC", (_number(0.0, 4.0), _number_words), field=2),
    _word("hue", "CC", (_hue, _degrees_words), field=3),
    _word("fullscreen", "FULLSCREEN_PAUSE", (_wp_fullscreen, _wp_fullscreen_words)),
    _word("property", "PROP_", (_text, _text_words)),
    _word("hide", "SKIP", (_text, _text_words), owner=OBJECTS_VERBS),
    _word("unhide", "SKIP", (_text, _text_words), owner=OBJECTS_VERBS),
    _word("alias", "ALIAS", (_text, _text_words), reach=NO_ENGINE),
)

_WALLPAPER_FORMS = {
    "volume": (_whole(0, 128), _whole_words),
    "speed": (_number(0.1, 10.0), _number_words),
    "edge": _choice(_EDGE_WORDS),
}


def _wallpaper_form(row: Row) -> Row:
    parse, fmt = _WALLPAPER_FORMS.get(row.name, (row.parse, row.format))
    return Row(row.name, WALLPAPER, row.scope, WALLPAPER_FILE, row.wp_key, parse, fmt, NOW, row.wp_key, False,
               WALLPAPER_VERBS)


ROWS: tuple[Row, ...] = _GLOBAL_ROWS + _WORD_ROWS + tuple(_wallpaper_form(r) for r in _GLOBAL_ROWS if r.wp_key)
BY_NAME = {row.name: row for row in ROWS if row.form == GLOBAL}
WALLPAPER_BY_NAME = {row.name: row for row in ROWS if row.form == WALLPAPER}


def derived_active_playlist(status: dict | None) -> tuple[str | None, str]:
    """The playlist order, interval and playlist act on, never written: engine/push.py's
    derived_active, the one the engine's lane is bound to when status answered with the schedule on
    and that playlist's file exists ("engine"); else the saved active playlist when its file exists
    ("saved"); else None."""
    from ..engine.push import derived_active
    return derived_active(status)


def _saved_lines() -> dict[str, tuple[int, str, str]]:
    """{key: (line number, the line as written, its value)} for the line of settings.conf that sets
    each key, the last one when a key is set twice, an old name read under its new one."""
    from ..storage import paths, settings, tier_a
    try:
        text = paths.settings_file().read_bytes().decode("utf-8")
    except OSError:
        return {}
    out: dict[str, tuple[int, str, str]] = {}
    for number, line in enumerate(text.split("\n"), 1):
        for key, value in settings.migrate_raw(tier_a.parse(line)).items():
            out[key] = (number, line.strip(), value)
    return out


def _engine_env() -> dict[str, str]:
    """engine-env's variables as the generator's parser reads them; {} when there is no file."""
    from ..engine import daemon_unit
    from ..storage import paths
    try:
        text = (paths.config_dir() / daemon_unit.ENV_FILE_NAME).read_bytes().decode("utf-8")
    except OSError:
        return {}
    return daemon_unit.parse_env(text.split("\n"))


def read(name: str, status: dict | None) -> tuple[Any, str, str | None]:
    """(the value in force, where it comes from, the saved line's problem or None) for one setting,
    `status` being the engine's status or None. Saved values, never a running one but audiosmoothing's;
    reads write nothing, and the store's warnings stay quiet since the problem names the line."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return _read(name, status)


def _read(name: str, status: dict | None) -> tuple[Any, str, str | None]:
    """read, with the store's warnings already silenced."""
    from ..storage import playlists, settings
    row = BY_NAME[name]
    if row.place == STATUS:
        if isinstance(status, dict) and status.get("audio_smooth") is not None:
            return float(status["audio_smooth"]), "running", None
        return None, "not running", None
    if row.place == ENGINE_ENV:
        raw = _engine_env().get(row.key)
        try:
            if row.name == "watchdog":
                return (int(raw) if raw is not None else 300), "engine-env", None
            numbers = tuple(float(part) for part in raw.split()) if raw is not None else (1.0, 1.0, 1.0, 0.0)
            return (numbers if len(numbers) == 4 else (1.0, 1.0, 1.0, 0.0)), "engine-env", None
        except ValueError:
            return (300 if row.name == "watchdog" else (1.0, 1.0, 1.0, 0.0)), "engine-env", None
    if row.place == PLAYLIST_FILE or row.name == "playlist":
        slug, how = derived_active_playlist(status)
        saved = playlists.load(slug) if slug else {}
        if row.name == "playlist":
            return saved.get("NAME", ""), "schedule" if how == "engine" else "settings.conf", None
        return saved.get(row.key), f"playlists/{slug}.conf" if slug else "no active playlist", None
    loaded = settings.load()
    present = settings.load_set()
    value = loaded[row.key]
    if row.key in present:
        source = "settings.conf"
    elif row.name in ("resclamp", "effectclamp") and "RENDER_RESOLUTION" in present:
        source = "RENDER_RESOLUTION"
    else:
        source = "default"
    if row.key == "FULLSCREEN_BEHAVIOR" and value == "":
        from ..engine.resolve import resolve_fullscreen_behavior
        value = resolve_fullscreen_behavior(loaded)
    problem = None
    line = _saved_lines().get(row.key)
    if line is not None:
        reason = settings.check_raw(row.key, line[2])
        if reason:
            problem = f"settings.conf line {line[0]} {line[1]} ({reason})"
    return value, source, problem
