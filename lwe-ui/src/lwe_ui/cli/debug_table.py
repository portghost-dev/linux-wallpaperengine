"""The engine's debugging switches as data: each switch's plain name, its engine variable, what it
accepts, its words (each mapping to a value, or to None, which removes the variable), its value rule and
bounds, and the description --help-debug prints for it. resolve() ports the engine's
DebugSwitches::resolve and ruleValue, so a value is checked here exactly as the engine checks it.
"""
from __future__ import annotations

import math
from typing import NamedTuple

ULL_MAX = 2**64 - 1


class Row(NamedTuple):
    name: str
    variable: str
    accepts: str
    words: tuple[tuple[str, str | None], ...]
    description: str
    rule: str = "Words"
    lo: int = 0
    hi: int = ULL_MAX


class Refusal(ValueError):
    """A switch or value the engine refuses, in the engine's words with lwe's names."""


# A copy of DebugSwitches.cpp's table and help text, kept equal to it by tests/test_cli_debug_table.py.
ROWS = (
    Row("animationtiming", "LWE_ANIMFRACTION", "current | legacy", (("current", None), ("legacy", "0")),
        "legacy brings back the older timing for looping sprite-sheet animation in particles."),
    Row("animationstats", "LWE_ANIMSTATS", "on | off", (("on", "1"), ("off", None)),
        "Logs frame-advance statistics for animated textures."),
    Row("audiostats", "LWE_AUDIOSTATS", "on | off", (("on", "1"), ("off", None)),
        "Logs the average sound levels handed to wallpaper scripts, every 30 script ticks."),
    Row("audit", "LWE_AUDIT", "on | off", (("on", "1"), ("off", None)),
        "Logs one-time audits: which file each texture name resolves to, pixel statistics of large "
        "compressed textures, material mask switches, and 3D model drawing state."),
    Row("texturedetailtest", "LWE_BASELEVEL_PROBE", "on | off", (("on", "1"), ("off", None)),
        "Runs a one-time test of how the graphics driver handles texture detail levels and logs pass or "
        "fail."),
    Row("shorttrailrenderer", "LWE_BILLBOARD", "normal | sprite", (("normal", None), ("sprite", "1")),
        "Draws very short rope-trail particles (length under 0.35) with the simpler sprite-trail renderer."),
    Row("cameraprobe", "LWE_CAMPROBE", "on | off", (("on", "1"), ("off", None)),
        "Logs camera position and view-matrix details, including camera moves made by scripts."),
    Row("webdebug", "LWE_CEFDEBUG", "a port from 1 to 65535 | off", (("off", None),),
        "Opens the web engine's remote debugging (devtools) port for web wallpapers. (web helper)",
        "Whole", 1, 65535),
    Row("weblog", "LWE_CEFLOG",
        "verbose | info | warning | error",
        (("verbose", "verbose"), ("info", "info"), ("warning", None), ("error", "error")),
        "How much the web engine writes to its log file (logs/cef under the lwe state folder). (web "
        "helper)"),
    Row("testresolution", "LWE_CLAMPOUTPUT",
        "WIDTHxHEIGHT, each a whole number from 1 to 2147483647 | default", (("default", None),),
        "Pretends the largest screen is this size when working out the ssfactor and clampcomposites caps, "
        "a test override.",
        "Size"),
    Row("backgroundprobe", "LWE_CLEARPROBE", "on | off", (("on", "1"), ("off", None)),
        "Logs the scene's background clear color when the scene is built and for its first three frames."),
    Row("cropoffset", "LWE_CROPOFF",
        "off | normal | reverse", (("off", None), ("normal", "1"), ("reverse", "2")),
        "Shifts image layers (except puppet-animated ones) by their authored crop offset, a layout "
        "experiment."),
    Row("cursorscripts", "LWE_CURSORDBG", "on | off", (("on", "1"), ("off", None)),
        "Logs which scripts listen for cursor events and the clickable areas of image layers."),
    Row("surfacesizes", "LWE_EGLDEBUG", "on | off", (("on", "1"), ("off", None)),
        "Logs drawing-surface sizes against screen sizes for the first four frames (Wayland)."),
    Row("sceneimage", "LWE_FBDUMP", "a path | off", (("off", None),),
        "Saves the drawn scene as a PPM image at frame 3 and again at the frame set by sceneimageframe.",
        "Text"),
    Row("sceneimageframe", "LWE_FBDUMP_FRAME", "a whole number from 4 to 2147483647", (),
        "Which frame sceneimage saves its second image at.",
        "Whole", 4, 2147483647),
    Row("bufferallocations", "LWE_FBOALLOC", "on | off", (("on", "1"), ("off", None)),
        "Logs every offscreen buffer the engine allocates, with a running total."),
    Row("buffercoverage", "LWE_FBOCOVERAGE", "on | off", (("on", "1"), ("off", None)),
        "Logs each time an image layer's working buffer is enlarged to match its size on screen."),
    Row("buffersharing", "LWE_FBOPOOL", "on | off", (("on", None), ("off", "0")),
        "off gives every layer its own working buffers instead of sharing same-size ones, which uses more "
        "video memory."),
    Row("bufferaliases", "LWE_FBOTRACE", "on | off", (("on", "1"), ("off", None)),
        "Logs each time an offscreen buffer is set up as another name for an existing one."),
    Row("brightnessprofile", "LWE_FBPROFILE", "on | off", (("on", "1"), ("off", None)),
        "Logs a coarse brightness profile of the scene at frame 150 and of the picture sent to the screen "
        "every 450 frames."),
    Row("shaderoption", "LWE_FORCECOMBO",
        "NAME=number: NAME starts with a letter or underscore, holds letters, digits and underscores, and "
        "does not start with GL_ in any letter case; number a whole number from 0 to 2147483647", (),
        "Forces one shader option (combo) to this value in every shader, a diagnostic.",
        "Combo"),
    Row("frontface", "LWE_FRONTFACE",
        "clockwise | counterclockwise", (("clockwise", None), ("counterclockwise", "ccw")),
        "counterclockwise flips which side of 3D model triangles counts as the front."),
    Row("hideparenttrails", "LWE_HIDESTPARENT", "on | off", (("on", "1"), ("off", None)),
        "Hides sprite-trail particle systems that have child systems, leaving only the children."),
    Row("imagedetails", "LWE_IMGDUMP", "on | off", (("on", "1"), ("off", None)),
        "Logs image layer placement, textures, render passes and matrices."),
    Row("imagegeometry", "LWE_IMGPROBE", "on | off", (("on", "1"), ("off", None)),
        "Logs image layer position, shape and vertex-buffer details."),
    Row("hidelight", "LWE_KILLLIGHT", "an id, a whole number from 0 to 2147483647 | off", (("off", None),),
        "Leaves the light with this id out of scene lighting, a diagnostic.",
        "Whole", 0, 2147483647),
    Row("pixelchanges", "LWE_LEDGER", "on | off", (("on", "1"), ("off", None)),
        "Logs, for the first two frames, how much the sampled pixel values under each drawn object change."),
    Row("scenedetails", "LWE_LIGHTDUMP", "on | off", (("on", "1"), ("off", None)),
        "Logs a bundle of lighting, text, script-property, animation-timeline and shader-setting details; "
        "most appear once, while one animation trace repeats as the scene runs."),
    Row("maskaudit", "LWE_MASKAUDIT", "on | off", (("on", "1"), ("off", None)),
        "Logs value statistics of one- and two-channel mask textures as they load."),
    Row("texturedetailaudit", "LWE_MIPRESIDENCY_DEBUG", "on | off", (("on", "1"), ("off", None)),
        "Logs, for each texture, whether the engine may load it at reduced detail."),
    Row("mouseevents", "LWE_MOUSEDBG", "on | off", (("on", "1"), ("off", None)),
        "Logs pointer enter and button events (Wayland) and the mouse movement sent to web wallpapers."),
    Row("mouseposition", "LWE_MOUSE_POS", "x,y, two decimals from 0 to 1 | off", (("off", None),),
        "Pins the mouse position scene wallpapers see to this spot, as fractions of the screen width and "
        "height; web wallpapers still get the real pointer.",
        "Position"),
    Row("videobuffer", "LWE_MPV_DEMUX_MB", "a whole number of MiB from 1 to 2147483647", (),
        "How much video mpv may read ahead for video wallpapers.",
        "Whole", 1, 2147483647),
    Row("videoextraframes", "LWE_MPV_EXTRA_FRAMES", "a whole number from 0 to 256", (),
        "How many extra frames mpv's hardware decoder keeps queued (only matters with hardware decoding).",
        "Whole", 0, 256),
    Row("videoframesync", "LWE_MPV_REALSYNC", "fallback | native", (("fallback", None), ("native", "1")),
        "Gives mpv the graphics driver's real frame-sync functions and turns on its direct rendering, "
        "instead of the engine's stand-ins, which exist because mpv never frees the real ones."),
    Row("videothreads", "LWE_MPV_THREADS", "a whole number from 0 to 2147483647", (),
        "How many threads mpv uses to decode video wallpapers.",
        "Whole", 0, 2147483647),
    Row("bloom", "LWE_NOBLOOM", "on | off", (("on", None), ("off", "1")),
        "off turns off the scene bloom (glow) effect."),
    Row("childanchoring", "LWE_NOCHILDRIDE", "authored | fixed", (("authored", None), ("fixed", "1")),
        "Treats every child particle system as fixed in place when correcting particle sizes for layer "
        "scaling, a diagnostic."),
    Row("buffergrowth", "LWE_NOFBOCOVERAGE", "on | off", (("on", None), ("off", "1")),
        "off stops image layer working buffers from being enlarged to their on-screen size, so they keep "
        "the authored size."),
    Row("childfade", "LWE_NOFOLLOWALPHA", "on | off", (("on", None), ("off", "1")),
        "off stops child particles from fading along with their parent particle."),
    Row("objectvolume", "LWE_NOOBJVOL", "on | off", (("on", None), ("off", "1")),
        "off plays every sound object at full volume, ignoring the volume the wallpaper's author set."),
    Row("parallelparticles", "LWE_NOPARSIM", "on | off", (("on", None), ("off", "1")),
        "off runs all particle simulation on the main thread instead of spreading it over worker threads."),
    Row("particlewarmup", "LWE_NOPREWARM", "on | off", (("on", None), ("off", "1")),
        "off turns off the 60-second warm-up simulation that particle systems with a start time get."),
    Row("ropetextureflip", "LWE_NOROPEUVFLIP", "on | off", (("on", None), ("off", "1")),
        "off turns off the engine's vertical texture flip fix for rope particle shaders."),
    Row("shaderscreensize", "LWE_NOSCREEN", "on | off", (("on", None), ("off", "1")),
        "off stops giving shaders the scene's size and aspect ratio, a diagnostic."),
    Row("spritetextureflip", "LWE_NOSPRITEVFLIP", "on | off", (("on", None), ("off", "1")),
        "off turns off the engine's vertical flip for sprite particles, so up points the other way."),
    Row("objectpixels", "LWE_OBJPROBE",
        "x0 y0 x1 y1 [skip], separated by spaces: x0, y0, x1 and y1 whole numbers from 0 to 2147483647 "
        "with x1 above x0 and y1 above y0; skip a whole number from 0 to 2147483447", (),
        "Reads the pixels in this rectangle after each object is drawn and logs their average, for 199 "
        "draws after the skip.",
        "Rectangle"),
    Row("objectpixelprecision", "LWE_OBJPROBE_FLOAT", "byte | float", (("byte", None), ("float", "1")),
        "Makes objectpixels read full-precision values instead of 8-bit ones."),
    Row("overlayfont", "LWE_OVERLAY_FONT", "a path | default", (("default", None),),
        "Font file for the corner text overlay, tried before the built-in list.",
        "Text"),
    Row("overlaysize", "LWE_OVERLAY_SIZE", "a whole number of pixels from 8 to 200", (),
        "Heading text size of the overlay at a screen height of 2160 pixels; later lines draw at two "
        "thirds of it.",
        "Whole", 8, 200),
    Row("overlaytext", "LWE_OVERLAY_TEXT", "any text", (),
        "Starting text for the corner text overlay, unless a set-overlay command has already set one.",
        "Text"),
    Row("particleallocations", "LWE_PARTALLOC", "on | off", (("on", "1"), ("off", None)),
        "Logs how much memory each particle system's buffers take."),
    Row("particlestats", "LWE_PARTSTATS", "on | off", (("on", "1"), ("off", None)),
        "Logs statistics for each particle system every 5 seconds: frame rate, spawn and death rates, peak "
        "count and average speed."),
    Row("passprobe", "LWE_PASSPROBE",
        "an object id, a whole number from 0 to 2147483647 | final", (("final", "final"),),
        "Logs pixel statistics after each render pass of the object with this id (or of the final output "
        "passes) and its texture bindings.",
        "Whole", 0, 2147483647),
    Row("passimage", "LWE_PASSPROBE_DUMP", "on | off", (("on", "1"), ("off", None)),
        "Also saves one probed pass as passprobe-post.ppm in the lwe probes folder."),
    Row("buffersharingstats", "LWE_POOL_HWM", "on | off", (("on", "1"), ("off", None)),
        "Logs a report of how well layers share working buffers."),
    Row("screentrace", "LWE_PRESENTTRACE", "on | off", (("on", "1"), ("off", None)),
        "Logs screen size, scene size, scaling mode and picture window for the first frames sent to each "
        "screen."),
    Row("ropetrailprobe", "LWE_ROPETRAILPROBE", "on | off", (("on", "1"), ("off", None)),
        "Logs rope-trail particle geometry: strip and point counts, longest segment and head position."),
    Row("scriptregistration", "LWE_SCRIPTDBG", "on | off", (("on", "1"), ("off", None)),
        "Logs which wallpaper scripts are registered or skipped."),
    Row("shaderdump", "LWE_SHADERDUMP", "on | off", (("on", "1"), ("off", None)),
        "When a fragment shader fails to compile, logs its full source with line numbers."),
    Row("shadersource", "LWE_SHADERDUMP_MATCH", "text | off", (("off", None),),
        "Saves the final source of every shader whose file name contains this text to the lwe probes "
        "folder.",
        "Text"),
    Row("shapes", "LWE_SHAPES", "on | off", (("on", None), ("off", "0")),
        "off leaves shape objects out of scenes."),
    Row("particlesizeprobe", "LWE_SIZEPROBE", "on | off", (("on", "1"), ("off", None)),
        "Reads back and logs the particle size-correction values shaders receive."),
    Row("skippedobjects", "LWE_SKIPGATE", "omit | hide", (("omit", None), ("hide", "0")),
        "hide still builds objects on the skip list (from --render-debug skip-object or a show's "
        "skip_objects) and only hides them when drawing; omit, the default, leaves them out of the scene "
        "entirely."),
    Row("modelshine", "LWE_SPECFIX", "authored | off", (("authored", None), ("off", "1")),
        "off makes 3D models fully rough (no shine), a lighting experiment."),
    Row("albedocolor", "LWE_SRGBALBEDO", "authored | srgb", (("authored", None), ("srgb", "1")),
        "Treats DXT5-compressed textures as sRGB and gamma-corrects the final picture, a color experiment."),
    Row("texturecolor", "LWE_SRGBALL", "authored | srgb", (("authored", None), ("srgb", "1")),
        "Treats all color textures as sRGB, gamma-corrects the final picture and skips the compressed "
        "texture cache, a color experiment."),
    Row("texturecachestats", "LWE_TEXCACHEDUMP", "on | off", (("on", "1"), ("off", None)),
        "Logs which textures survive a cache cleanup and when wallpapers are created and destroyed."),
    Row("testtexturelimit", "LWE_TEXCAP",
        "a size in pixels, a whole number from 256 to 2147483647 | default", (("default", None),),
        "Overrides the largest texture size the engine loads when reducing texture detail, a test "
        "override.",
        "Whole", 256, 2147483647),
    Row("timestats", "LWE_TIMESTATS", "on | off", (("on", "1"), ("off", None)),
        "Logs every 5 seconds how the wallpaper clock compares with real time."),
    Row("modeltint", "LWE_TINTFIX",
        "authored | unmasked | blue", (("authored", None), ("unmasked", "1"), ("blue", "2")),
        "unmasked turns off a tint-mask option on 3D model materials and blue paints models a fixed blue, "
        "both lighting experiments."),
    Row("trailtiming", "LWE_TRAILMODE", "spread | exact", (("spread", None), ("exact", "exact")),
        "exact places particle trail points at fixed time steps from each particle's birth instead of "
        "spreading them along the trail."),
    Row("twinkleprobe", "LWE_TWINKLEPROBE", "on | off", (("on", "1"), ("off", None)),
        "Logs the age and fade values of the first particle in each particle system every 30 frames."),
    Row("shaderinputs", "LWE_UNIFDUMP", "on | off", (("on", "1"), ("off", None)),
        "Logs each shader setting the engine fills in automatically and where its value came from."),
    Row("shaderinputvalues", "LWE_UNIFVALS", "on | off", (("on", "1"), ("off", None)),
        "Logs shader setting values and how layer color and brightness are applied."),
    Row("particlevelocity", "LWE_VELPROBE", "on | off", (("on", "1"), ("off", None)),
        "Logs the starting velocity each random-velocity initializer gives a new particle."),
    Row("webcrashlimit", "LWE_WEB_CRASHGUARD",
        "count,window,cooldown: count a whole number from 1 to 2147483647; window and cooldown whole "
        "numbers of milliseconds from 0 to 2147483647", (),
        "If the web helper dies this many times within the window, the engine waits out the cooldown "
        "before starting it again, and web wallpapers stay dark meanwhile.",
        "CrashLimit"),
    Row("webidletime", "LWE_WEB_IDLE_EXIT_MS",
        "a whole number of milliseconds from 0 to 9223372036854775807, or a whole number followed by ms or "
        "s whose milliseconds fit that range", (),
        "How long the web helper waits after its last web wallpaper closes before it exits. (web helper)",
        "Milliseconds"),
    Row("webhelpersocket", "LWE_WEB_SOCKET", "a path | default", (("default", None),),
        "Where the socket between the engine and its web helper lives.",
        "Text"),
    Row("windowtitle", "LWE_WINTITLE", "any text", (),
        "Window title when running with --window.",
        "Text"),
)

BY_NAME = {row.name: row for row in ROWS}
BY_VARIABLE = {row.variable: row for row in ROWS}


def _whole(text: str) -> int | None:
    if not text:
        return None
    value = 0
    for c in text:
        if not "0" <= c <= "9":
            return None
        digit = ord(c) - ord("0")
        value = ULL_MAX if value > (ULL_MAX - digit) // 10 else value * 10 + digit
    return value


def _is_whole(text: str, lo: int, hi: int) -> bool:
    value = _whole(text)
    return value is not None and lo <= value <= hi


def _plain_number(text: str) -> float | None:
    def digits_from(start: int) -> int:
        end = start
        while end < len(text) and "0" <= text[end] <= "9":
            end += 1
        return end - start

    at = 1 if text[:1] in ("+", "-") else 0
    whole_digits = digits_from(at)
    at += whole_digits
    point_digits = 0
    if text[at:at + 1] == ".":
        at += 1
        point_digits = digits_from(at)
        at += point_digits
    if whole_digits == 0 and point_digits == 0:
        return None
    if text[at:at + 1] in ("e", "E"):
        at += 1
        if text[at:at + 1] in ("+", "-"):
            at += 1
        exponent_digits = digits_from(at)
        if exponent_digits == 0:
            return None
        at += exponent_digits
    if at != len(text):
        return None
    value = float(text)
    if not math.isfinite(value):
        return None
    return 0.0 if value == 0.0 else value


def _fraction(text: str) -> bool:
    value = _plain_number(text)
    return value is not None and 0.0 <= value <= 1.0


def _name_character(c: str) -> bool:
    return "A" <= c <= "Z" or "a" <= c <= "z" or "0" <= c <= "9" or c == "_"


def _rule_value(row: Row, value: str) -> str | None:
    if row.rule == "Whole":
        return value if _is_whole(value, row.lo, row.hi) else None
    if row.rule == "Text":
        return value
    if row.rule == "Size":
        sides = value.split("x")
        ok = len(sides) == 2 and all(_is_whole(side, 1, 2147483647) for side in sides)
        return value if ok else None
    if row.rule == "Position":
        axes = value.split(",")
        return value if len(axes) == 2 and all(_fraction(axis) for axis in axes) else None
    if row.rule == "Rectangle":
        if value.startswith(" ") or value.endswith(" "):
            return None
        numbers = [number for number in value.split(" ") if number]
        if not 4 <= len(numbers) <= 5:
            return None
        if not all(_is_whole(number, 0, 2147483647) for number in numbers[:4]):
            return None
        if len(numbers) == 5 and not _is_whole(numbers[4], 0, 2147483447):
            return None
        x0, y0, x1, y1 = (_whole(number) for number in numbers[:4])
        return value if x1 > x0 and y1 > y0 else None
    if row.rule == "Combo":
        equals = value.find("=")
        if (equals <= 0 or "0" <= value[0] <= "9"
                or (equals >= 3 and value[0] in "Gg" and value[1] in "Ll" and value[2] == "_")
                or not all(_name_character(c) for c in value[:equals])
                or not _is_whole(value[equals + 1:], 0, 2147483647)):
            return None
        return value
    if row.rule == "CrashLimit":
        numbers = value.split(",")
        ok = (len(numbers) == 3 and _is_whole(numbers[0], 1, 2147483647)
              and _is_whole(numbers[1], 0, 2147483647) and _is_whole(numbers[2], 0, 2147483647))
        return value if ok else None
    if row.rule == "Milliseconds":
        if value.endswith("ms"):
            count = value[:-2]
            return count if _is_whole(count, 0, 9223372036854775807) else None
        if value.endswith("s"):
            count = value[:-1]
            return count + "000" if _is_whole(count, 0, 9223372036854775807 // 1000) else None
        return value if _is_whole(value, 0, 9223372036854775807) else None
    return None


def row(name: str) -> Row:
    """The row of switch `name`; an unknown name raises Refusal."""
    found = BY_NAME.get(name)
    if found is None:
        raise Refusal(f"unknown debugging switch {name}; help --debug lists them")
    return found


def resolve(name: str, value: str) -> tuple[str, str | None]:
    """(the switch's engine variable, the value to set it to, or None to remove it) for switch `name`
    given `value`. A missing name or value, an unknown switch and a value its words and rule do not
    accept raise Refusal. A word wins over the rule."""
    if not name or not value:
        raise Refusal("debug takes <switch> <value>; got " + " ".join(word for word in (name, value) if word))
    found = row(name)
    for word, set_to in found.words:
        if word == value:
            return found.variable, set_to
    set_to = _rule_value(found, value)
    if set_to is None:
        raise Refusal(f"{name} takes {found.accepts}; got {value}")
    return found.variable, set_to
