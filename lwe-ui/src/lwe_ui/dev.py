"""DevBridge - the Developer view backend: advanced scene control over two exhibit slots.

One surface, two persistent exhibit slots (A and B), three launch verbs. Launch A and
Launch B bench one slot fullscreen over a daemon standdown; Launch both runs the two slots
as windowed engines beside each other while the daemon keeps the desktop. Every
run-affecting knob is per slot: scene, binary, overlay label, feature toggles, render-debug
flags, instruments, raw env lines, property queue, and the session-scoped isolator.

Reach classes: a LIVE knob reaches the running exhibit over that exhibit's own command
socket; a RELAUNCH knob restarts only the affected slot, debounced, keeping its
presentation. The tables below state which is which and the tooltips carry it.

Table conventions. FEATURE_TOGGLES are all relaunch class; ON means the behaviour is
engaged; `on` / `off` are the env values for each state and None removes the variable,
the only off for a switch the engine tests by presence; `default_on` is the switch state
of an engine launched bare. INSTRUMENTS are env-class printers, `live` marking the ones the
engine's instrument registry can flip on a running exhibit. RAW_ENV_REFERENCE lists the
knobs that carry a value rather than a switch state; the raw env editor is their only door.

Crash residue replaces run history: each slot keeps its last exit code and the last 400
lines of that run, on disk beside the slot configuration.

Everything below the Qt layer is stdlib.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import Property, QObject, QProcess, QProcessEnvironment, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication

from . import constants as C
from . import api_client
from . import placement
from . import procstats
from .engine import daemon_unit
from .discovery import objects as objects_disc
from .discovery import project as project_disc
from .storage import atomic, paths, settings

SIDES = ("A", "B")

# The console holds this many lines for the session; the journal backlog fills it at the first
# open. A slot keeps RESIDUE_LINES of its last run as crash residue.
CONSOLE_MAX = 5000
JOURNAL_BACKLOG = 5000
RESIDUE_LINES = 2000

FEATURE_TOGGLES = [
    {"key": "realsync", "label": "Real-time video sync", "env": "LWE_MPV_REALSYNC",
     "on": "1", "off": None, "default_on": False,
     "tip": "Gives mpv its real GL fences and direct rendering instead of the leak-proof stubs.",
     "cite": "GLPlayer.cpp::useRealMpvSync"},
    {"key": "prewarm", "label": "Prewarm", "env": "LWE_NOPREWARM",
     "on": None, "off": "1", "default_on": True,
     "tip": "Runs each particle system ahead at load so the scene starts already populated.",
     "cite": "CParticle.cpp::s_noPrewarm"},
    {"key": "parsim", "label": "Parallel particles", "env": "LWE_NOPARSIM",
     "on": None, "off": "1", "default_on": True,
     "tip": "Simulates heavy particle scenes on a worker pool; off runs every system inline on the render thread.",
     "cite": "CScene.cpp::s_serial"},
    {"key": "bloom", "label": "Bloom", "env": "LWE_NOBLOOM",
     "on": None, "off": "1", "default_on": True,
     "tip": "Builds the bloom pass when the scene camera asks for it.",
     "cite": "CScene.cpp::s_noBloom"},
    {"key": "particles", "label": "Particles", "env": "LWE_NOPARTICLES",
     "on": None, "off": "1", "default_on": True,
     "tip": "Creates particle objects at load; off drops every emitter from the scene.",
     "cite": "CScene.cpp::s_noParticles"},
    {"key": "spritevflip", "label": "Sprite V-flip", "env": "LWE_NOSPRITEVFLIP",
     "on": None, "off": "1", "default_on": True,
     "tip": "Points the particle up axis down, the orientation Wallpaper Engine renders with.",
     "cite": "CParticle.cpp::s_noVFlip"},
    {"key": "ropeuvflip", "label": "Rope UV flip", "env": "LWE_NOROPEUVFLIP",
     "on": None, "off": "1", "default_on": True,
     "tip": "Rewrites the rope particle vertex shader so trail textures read top to bottom.",
     "cite": "ShaderUnit.cpp::LWE_NOROPEUVFLIP"},
    {"key": "childride", "label": "Child ride", "env": "LWE_NOCHILDRIDE",
     "on": None, "off": "1", "default_on": True,
     "tip": "Child particle systems scale their motion by the parent's world size.",
     "cite": "CParticle.cpp::s_noChildRide"},
    {"key": "followalpha", "label": "Follow alpha", "env": "LWE_NOFOLLOWALPHA",
     "on": None, "off": "1", "default_on": True,
     "tip": "Child particles inherit their parent particle's live alpha.",
     "cite": "CParticle.cpp::s_noFollowAlpha"},
    {"key": "objvolume", "label": "Object volume", "env": "LWE_NOOBJVOL",
     "on": None, "off": "1", "default_on": True,
     "tip": "Sound objects play at their authored volume instead of full volume.",
     "cite": "CSound.cpp::noObjVol"},
    {"key": "screenuniform", "label": "Screen uniform", "env": "LWE_NOSCREEN",
     "on": None, "off": "1", "default_on": True,
     "tip": "Hands shaders the g_Screen uniform (width, height, aspect).",
     "cite": "CPass.cpp::s_noScreen"},
    {"key": "fbocoverage", "label": "FBO coverage", "env": "LWE_NOFBOCOVERAGE",
     "on": None, "off": "1", "default_on": True,
     "tip": "Sizes layer composite FBOs to the scaled image coverage instead of the raw image size.",
     "cite": "CImage.cpp::s_fboCoverage"},
    # the two clamp rows exclude each other per side: one on turns the other off, both off
    # leaves nothing clamped (compose_env keeps LWE_SSFACTOR unset while the second is on)
    {"key": "resclamp", "label": "Resolution clamp", "env": "LWE_SSFACTOR",
     "on": None, "off": "0", "default_on": True, "excludes": "resclampfx",
     "tip": "Caps every framebuffer at the output size; off renders them at their authored size.",
     "cite": "CScene.cpp::clampToCap"},
    {"key": "resclampfx", "label": "Resolution clamp + effects", "env": "LWE_CLAMPCOMPOSITES",
     "on": "0", "off": None, "default_on": False, "excludes": "resclamp",
     "tip": "Caps the scene framebuffer only; layer composites and effect targets keep their "
            "authored size so effect chains stay sharp.",
     "cite": "CImage.cpp::clampComposites"},
    {"key": "frontface", "label": "Clockwise winding", "env": "LWE_FRONTFACE",
     "on": None, "off": "ccw", "default_on": True,
     "tip": "Treats clockwise model triangles as front faces; off uses counter-clockwise.",
     "cite": "CModel.cpp::s_frontFaceCCW"},
    {"key": "animfraction", "label": "Animation fraction clock", "env": "LWE_ANIMFRACTION",
     "on": None, "off": "0", "default_on": True,
     "tip": "Drives sprite sheet frames from lifetime fraction; off uses the legacy age clock.",
     "cite": "CParticle.cpp::s_legacyAnimClock"},
    {"key": "texcomp", "label": "Texture compression", "env": "LWE_TEXCOMP",
     "on": None, "off": "0", "default_on": True,
     "tip": "Uploads BC7 compressed textures from the texture cache.",
     "cite": "CTexture.cpp::uploadFromTexcache"},
    {"key": "fbopool", "label": "FBO pool", "env": "LWE_FBOPOOL",
     "on": None, "off": "0", "default_on": True,
     "tip": "Leases same-size layer composites from a per-scene pool instead of one pair per layer.",
     "cite": "CScene.cpp::poolDisabled"},
    {"key": "skipgate", "label": "Skip gate", "env": "LWE_SKIPGATE",
     "on": None, "off": "0", "default_on": True,
     "tip": "Honours the skip list while building the scene, so skipped objects are never created.",
     "cite": "CScene.cpp::skipGate"},
    {"key": "shapes", "label": "Shape objects", "env": "LWE_SHAPES",
     "on": None, "off": "0", "default_on": True,
     "tip": "Parses shape objects into the shared shape model instead of skipping them.",
     "cite": "ObjectParser.cpp::shapesEnabled"},
    {"key": "specfix", "label": "Specular fix", "env": "LWE_SPECFIX",
     "on": "1", "off": None, "default_on": False,
     "tip": "Forces model roughness to 1, which removes specular highlights.",
     "cite": "CModel.cpp::LWE_SPECFIX"},
    {"key": "srgbalbedo", "label": "sRGB albedo", "env": "LWE_SRGBALBEDO",
     "on": "1", "off": None, "default_on": False,
     "tip": "Presents the composed frame through an sRGB output transform.",
     "cite": "CWallpaper.cpp::srgbOut"},
    {"key": "srgball", "label": "sRGB all", "env": "LWE_SRGBALL",
     "on": "1", "off": None, "default_on": False,
     "tip": "sRGB output plus uncompressed sRGB texture uploads for every texture.",
     "cite": "CTexture.cpp::LWE_SRGBALL"},
    {"key": "billboard", "label": "Billboard collapse", "env": "LWE_BILLBOARD",
     "on": "1", "off": None, "default_on": False,
     "tip": "Renders short rope trails as billboards.",
     "cite": "CParticle.cpp::s_billboard"},
    {"key": "hidetrailparent", "label": "Hide trail parent", "env": "LWE_HIDESTPARENT",
     "on": "1", "off": None, "default_on": False,
     "tip": "Skips drawing a trail system that has child systems of its own.",
     "cite": "CParticle.cpp::s_hideStParent"},
]

TRAIL_MODES = ("Fluid", "Exact")
TRAIL_ENV = "LWE_TRAILMODE"

# stats overlay corners, wire value and display label; the first is the default
OVERLAY_CORNERS = (
    ("top-left", "Top left"), ("top-right", "Top right"),
    ("bottom-left", "Bottom left"), ("bottom-right", "Bottom right"),
)
OVERLAY_TICK_MS = 1000


def _overlay_unknown(reply: dict | None) -> bool:
    """True when the engine answered but does not know set-overlay (a build older than the verb)."""
    return bool(reply) and not reply.get("ok") and "unknown command" in str(reply.get("error", ""))


def overlay_text(label: str, sample: dict | None) -> str:
    """The label as a heading, then one figure per line. CPU is the share of the whole
    machine, GPU the whole GPU, RAM resident plus swapped."""
    if not sample:
        return f"{label}\nstats pending"
    fps, cpu = sample.get("fps"), sample.get("cpu")
    rss, swap = sample.get("rss", -1), sample.get("swap", -1)
    vram, gpu = sample.get("vram", -1), sample.get("gpu", -1)
    ram = "--" if rss < 0 else f"{rss} MB" + (f" + {swap} MB swap" if swap >= 0 else "")
    return "\n".join([
        label,
        "CPU: " + (f"{cpu:.1f}%" if cpu is not None else "--"),
        "GPU: " + (f"{gpu:.1f}%" if gpu >= 0 else "--"),
        "RAM: " + ram,
        "VRAM: " + (f"{vram} MB" if vram >= 0 else "--"),
        "FPS: " + (f"{fps:.1f}" if fps is not None else "--"),
    ])


def sample_exhibit(pid: int, sock: Path, prev: dict) -> tuple[dict, dict]:
    """One stats sample for a test engine: (sample, baseline for the next call). FPS and
    CPU are deltas against the previous baseline, so the first call reports neither."""
    now = time.monotonic()
    frames = None
    try:
        st = api_client.status(sock)
        if st and isinstance(st.get("frames"), int):
            frames = st["frames"]
    except Exception:
        frames = None
    ticks = procstats.cpu_ticks([pid])
    sample: dict = {"fps": None, "cpu": None}
    if prev and frames is not None and prev.get("frames") is not None \
            and now > prev["t"] and frames >= prev["frames"]:
        sample["fps"] = round((frames - prev["frames"]) / (now - prev["t"]), 1)
    if prev and ticks >= 0 and prev.get("ticks", -1) >= 0 and now > prev["t"]:
        sample["cpu"] = procstats.cpu_percent_of_machine(ticks - prev["ticks"], now - prev["t"])
    sample["rss"], sample["swap"] = procstats.rss_swap_mb([pid])
    sample["vram"] = procstats.vram_mb([pid])
    sample["gpu"] = procstats.gpu_sample()[0]
    return sample, {"t": now, "frames": frames, "ticks": ticks}
TRAIL_TIP = "Exact reproduces authored trail segments; Fluid interpolates between them."

RENDER_DEBUG_FLAGS = [
    {"key": "base-only", "label": "Render debug · base only",
     "tip": "Draws each image's base pass and skips its effect chain and blend passes.",
     "cite": "CImage.cpp::baseOnly"},
    {"key": "no-solid-final", "label": "Render debug · no solid final",
     "tip": "Skips the final composite of solid layers.",
     "cite": "CImage.cpp::noSolidFinal"},
    {"key": "pass-log", "label": "Render debug · pass log",
     "tip": "Logs every render pass as it runs.",
     "cite": "CPass.cpp::passLog"},
]

INSTRUMENTS = [
    {"env": "LWE_PRESENTTRACE", "tip": "Prints the present viewport, wallpaper size and UV window for the first frames.",
     "cite": "CWallpaper.cpp::s_presentTrace"},
    {"env": "LWE_PARTSTATS", "live": True, "tip": "Prints particle emit, live and peak counts.",
     "cite": "InstrumentRegistry.cpp::LWE_PARTSTATS"},
    {"env": "LWE_TWINKLEPROBE", "live": True, "tip": "Prints one particle's alpha as a time series.",
     "cite": "InstrumentRegistry.cpp::LWE_TWINKLEPROBE"},
    {"env": "LWE_ROPETRAILPROBE", "live": True, "tip": "Prints rope strip topology and head position.",
     "cite": "InstrumentRegistry.cpp::LWE_ROPETRAILPROBE"},
    {"env": "LWE_LIGHTDUMP", "tip": "Prints light, script, text and property traces per model pass.",
     "cite": "CModel.cpp::LWE_LIGHTDUMP"},
    {"env": "LWE_PARTALLOC", "tip": "Prints particle buffer bytes and their high-water mark.",
     "cite": "CParticle.cpp::LWE_PARTALLOC"},
    {"env": "LWE_VELPROBE", "tip": "Prints every particle's initial velocity (very high volume).",
     "cite": "CParticle.cpp::s_velProbe"},
    {"env": "LWE_SIZEPROBE", "tip": "Prints the size uniform readback against the simulated size, with compiled source.",
     "cite": "CParticle.cpp::s_sizeProbe"},
    {"env": "LWE_ANIMSTATS", "tip": "Prints the animated texture clock.",
     "cite": "CPass.cpp::s_animStats"},
    {"env": "LWE_CAMPROBE", "tip": "Prints the scripted camera pose and view-projection rows.",
     "cite": "Camera.cpp::s_camProbe"},
    {"env": "LWE_TIMESTATS", "tip": "Prints frame timing against the wall clock.",
     "cite": "WallpaperApplication.cpp::s_timeStats"},
    {"env": "LWE_FBOALLOC", "tip": "Prints bytes per FBO and the running total.",
     "cite": "FBOProvider.cpp::LWE_FBOALLOC"},
    {"env": "LWE_FBOTRACE", "tip": "Prints FBO alias wiring.",
     "cite": "FBOProvider.cpp::LWE_FBOTRACE"},
    {"env": "LWE_POOL_HWM", "value": "1", "tip": "Prints FBO pool leases and their high-water mark.",
     "cite": "CScene.cpp::reportPoolHighWater"},
    {"env": "LWE_TEXCACHEDUMP", "tip": "Prints texture cache survivors and wallpaper lifetime.",
     "cite": "TextureCache.cpp::LWE_TEXCACHEDUMP"},
    {"env": "LWE_AUDIOSTATS", "tip": "Prints the audio FFT bands.",
     "cite": "EngineObject.cpp::s_audioStats"},
    {"env": "LWE_FBPROFILE", "tip": "Prints a framebuffer luminance profile.",
     "cite": "CScene.cpp::s_fbProfile"},
    {"env": "LWE_SHADERDUMP", "tip": "Prints shader source and assembly when a shader fails.",
     "cite": "GLSLContext.cpp::LWE_SHADERDUMP"},
    {"env": "LWE_UNIFDUMP", "tip": "Prints shader uniform constants at pass setup.",
     "cite": "CPass.cpp::s_unifDump"},
    {"env": "LWE_UNIFVALS", "tip": "Prints every uniform upload (very high volume).",
     "cite": "CPass.cpp::LWE_UNIFVALS"},
    {"env": "LWE_IMGDUMP", "tip": "Prints image rectangles and pass wiring.",
     "cite": "CImage.cpp::s_imgDump"},
    {"env": "LWE_IMGPROBE", "tip": "Prints image and shape geometry, MVP and GPU buffer contents.",
     "cite": "CImage.cpp::s_imgProbe"},
    {"env": "LWE_LEDGER", "tip": "Prints a per-object render ledger for the first frames.",
     "cite": "CScene.cpp::s_ledger"},
    {"env": "LWE_CLEARPROBE", "tip": "Prints the clear colour and write mask.",
     "cite": "CScene.cpp::s_clearProbe"},
    {"env": "LWE_MASKAUDIT", "tip": "Prints mask channel statistics.",
     "cite": "CTexture.cpp::LWE_MASKAUDIT"},
    {"env": "LWE_AUDIT", "tip": "Prints texture, model and PBR audits.",
     "cite": "TextureCache.cpp::LWE_AUDIT"},
    {"env": "LWE_EGLDEBUG", "tip": "Prints the EGL surface against the viewport for the first frames.",
     "cite": "WaylandOutputViewport.cpp::LWE_EGLDEBUG"},
    {"env": "LWE_MOUSEDBG", "tip": "Prints compositor pointer delivery.",
     "cite": "WaylandOpenGLDriver.cpp::s_dbg"},
    {"env": "LWE_SCRIPTDBG", "tip": "Prints property script registration.",
     "cite": "ScriptEngine.cpp::s_scriptDbg"},
    {"env": "LWE_FBOCOVERAGE", "tip": "Prints the coverage size chosen for each layer composite FBO.",
     "cite": "CImage.cpp::s_fboCoverageLog"},
    {"env": "LWE_BASELEVEL_PROBE", "tip": "Runs the texture base level probe once at first GL upload.",
     "cite": "CTexture.cpp::LWE_BASELEVEL_PROBE"},
    {"env": "LWE_MIPRESIDENCY_DEBUG", "tip": "Prints mip residency decisions per pass.",
     "cite": "MipResidency.cpp::recordPass"},
    {"env": "LWE_CURSORDBG", "tip": "Prints the cursor transform once a second.",
     "cite": "CImage.cpp::s_cursorDbg"},
]

LIVE_INSTRUMENTS = {i["env"] for i in INSTRUMENTS if i.get("live")}

RAW_ENV_REFERENCE = [
    {"env": "LWE_KILLLIGHT", "what": "Skips one light by id", "prints": "nothing; the light is dropped",
     "cite": "CScene.cpp::s_killLight"},
    {"env": "LWE_TINTFIX", "what": "1 clears the tint mask alpha combo; 2 forces the tint colour constant",
     "prints": "nothing", "cite": "CModel.cpp::LWE_TINTFIX"},
    {"env": "LWE_CROPOFF", "what": "Applies the authored crop offset; 2 applies it negated",
     "prints": "nothing", "cite": "CImage.cpp::s_cropMode"},
    {"env": "LWE_SSFACTOR", "what": "Supersampling factor for the scene framebuffer", "prints": "nothing",
     "cite": "CScene.cpp::lwe_ssfactor"},
    {"env": "LWE_FORCECOMBO", "what": "NAME=value forces one shader combo", "prints": "nothing",
     "cite": "ShaderUnit.cpp::s_forceCombo"},
    {"env": "LWE_OBJPROBE", "what": "x0 y0 x1 y1 [skip] reads back a framebuffer rectangle per object",
     "prints": "LWE-OBJPROBE rows", "cite": "CScene.cpp::s_objProbe"},
    {"env": "LWE_OBJPROBE_FLOAT", "what": "Reads the object probe back as raw floats", "prints": "LWE-OBJPROBE rows",
     "cite": "CScene.cpp::s_floatRead"},
    {"env": "LWE_PASSPROBE", "what": "Object id or final: probes that pass's bindings and output",
     "prints": "LWE-PASSPROBE rows", "cite": "CPass.cpp::s_passProbe"},
    {"env": "LWE_PASSPROBE_DUMP", "what": "Writes the probed pass as a PPM once", "prints": "a file",
     "cite": "CPass.cpp::s_ppmDump"},
    {"env": "LWE_FBDUMP", "what": "Path prefix: writes the framebuffer as <prefix>.ppm", "prints": "LWE-FBDUMP wrote",
     "cite": "CScene.cpp::s_fbDump"},
    {"env": "LWE_FBDUMP_FRAME", "what": "Frame number for the second framebuffer dump", "prints": "nothing",
     "cite": "CScene.cpp::s_dumpFrameEnv"},
    {"env": "LWE_SHADERDUMP_MATCH", "what": "Substring: dumps every shader whose file name matches",
     "prints": "files under HOME", "cite": "ShaderUnit.cpp::s_shaderDump"},
    {"env": "LWE_MOUSE_POS", "what": "fx,fy pins the pointer at a viewport fraction", "prints": "nothing",
     "cite": "CScene.cpp::s_mousePin"},
    {"env": "LWE_CEFLOG", "what": "verbose, info or error: CEF log severity", "prints": "the CEF log",
     "cite": "WebBrowserContext.cpp::LWE_CEFLOG"},
    {"env": "LWE_CEFDEBUG", "what": "Port for the CEF remote debugger", "prints": "nothing",
     "cite": "WebBrowserContext.cpp::LWE_CEFDEBUG"},
    {"env": "LWE_MPV_THREADS", "what": "Decoder thread count for video", "prints": "nothing",
     "cite": "GLPlayer.cpp::LWE_MPV_THREADS"},
    {"env": "LWE_MPV_DEMUX_MB", "what": "Demuxer cache size in MB for video", "prints": "nothing",
     "cite": "GLPlayer.cpp::LWE_MPV_DEMUX_MB"},
    {"env": "LWE_MPV_EXTRA_FRAMES", "what": "Hardware decode ahead frames for video", "prints": "nothing",
     "cite": "GLPlayer.cpp::LWE_MPV_EXTRA_FRAMES"},
]

REACH_LIVE = "Applies live"
REACH_RELAUNCH = "Applies on relaunch"


def _engine_bin() -> str:
    try:
        return daemon_unit.resolve_engine_bin()
    except Exception:
        return str(paths.default_engine_bin())


def _assets_dir() -> str:
    try:
        return str(settings.load().get("ASSETS_DIR") or paths.default_assets_dir())
    except Exception:
        return str(paths.default_assets_dir())


def _wallpapers_dir() -> str:
    try:
        return str(settings.load().get("WALLPAPERS_DIR") or paths.default_wallpapers_dir())
    except Exception:
        return str(paths.default_wallpapers_dir())


def _probes_dir() -> Path:
    """User-supplied probe scenes: each subdir is a wallpaper (project.json + assets)."""
    return paths.data_dir() / "probes"


def _binaries_dir() -> Path:
    """Where the Binary dropdown discovers alternative engine builds."""
    return paths.data_dir() / "dev-binaries"


def _slots_file() -> Path:
    """Slot configuration + crash residue, one JSON beside the other state files."""
    return paths.state_dir() / "dev-slots.json"


def _is_shell_ident(name: str) -> bool:
    """True iff `name` is a POSIX shell identifier. Raw env keys are validated with this so a
    malformed key never enters the launch environment."""
    if not name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in name) and name.isascii()


def _now_hhmm() -> str:
    return time.strftime("%H:%M")


def _now_hhmmss() -> str:
    return time.strftime("%H:%M:%S")


def _journal_text(v) -> str:
    """A journal MESSAGE is a string, or a byte list when it was not valid UTF-8."""
    if isinstance(v, list):
        try:
            return bytes(int(b) & 0xFF for b in v).decode("utf-8", "replace")
        except (TypeError, ValueError):
            return ""
    return "" if v is None else str(v)


def _journal_fields(line: str) -> tuple[str, str, bool, str, str]:
    """One journalctl JSON record to (src, text, err, time, raw). raw is the short-iso line
    the journal would have printed, so a copy loses nothing. A line that is not a record
    (journalctl's own notices) passes through as text."""
    try:
        rec = json.loads(line)
    except ValueError:
        rec = None
    if not isinstance(rec, dict):
        return "D", line, False, _now_hhmmss(), line
    text = _journal_text(rec.get("MESSAGE"))
    try:
        prio = int(rec.get("PRIORITY", 6))
    except (TypeError, ValueError):
        prio = 6
    try:
        when = datetime.datetime.fromtimestamp(int(rec.get("__REALTIME_TIMESTAMP")) / 1e6).astimezone()
        ts = when.strftime("%H:%M:%S")
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S%z")
    except (TypeError, ValueError, OverflowError, OSError):
        ts = _now_hhmmss()
        stamp = ""
    ident = _journal_text(rec.get("SYSLOG_IDENTIFIER") or rec.get("_COMM"))
    pid = _journal_text(rec.get("_PID"))
    host = _journal_text(rec.get("_HOSTNAME"))
    head = " ".join(x for x in (stamp, host, f"{ident}[{pid}]:" if pid else f"{ident}:") if x)
    raw = f"{head} {text}" if head else text
    return "D", text, prio <= 4, ts, raw


def _safe_scene(wid: str) -> str:
    """A library wid or probe:<name>, each a single safe path segment; anything else is
    dropped so a slot can never point the engine at an arbitrary directory."""
    if wid.startswith("probe:"):
        return wid if paths.is_safe_wid(wid[6:]) else ""
    return wid if paths.is_safe_wid(wid) else ""


PANEL_OWNED_ENV = frozenset({"LWE_SOCKET", "LWE_WINTITLE", "LWE_OVERLAY_TEXT"})


def _parse_kv_lines(text: str, owned: frozenset = frozenset()) -> tuple[list[tuple[str, str]], list[str]]:
    """KEY=VALUE lines -> (accepted pairs in first-seen order, rejected raw lines). Keys must
    be shell identifiers and not panel-owned; a repeated key replaces the earlier value."""
    lines: list[tuple[str, str]] = []
    bad: list[str] = []
    for raw in str(text or "").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        k, sep, v = raw.partition("=")
        k = k.strip()
        if not sep or not _is_shell_ident(k) or k in owned:
            bad.append(raw)
            continue
        v = v.strip()
        existing = next((i for i, (ek, _v) in enumerate(lines) if ek == k), -1)
        if existing >= 0:
            lines[existing] = (k, v)
        else:
            lines.append((k, v))
    return lines, bad


class _Slot:
    """One exhibit slot: persisted configuration, crash residue, and the runtime state of
    the engine it may be running."""

    def __init__(self, side: str) -> None:
        self.side = side
        self.scene = ""
        self.binary = ""
        self.label = side
        self.toggles: dict[str, bool] = {}
        self.trail = TRAIL_MODES[0]
        self.render_debug: set[str] = set()
        self.instruments: set[str] = set()
        self.env_lines: list[tuple[str, str]] = []
        self.props: list[tuple[str, str]] = []
        self.last_code: int | None = None
        self.last_ts = ""
        self.last_tail: list[tuple[str, bool, str]] = []
        self.last_stopped = False
        self.proc: QProcess | None = None
        self.mode = ""
        self.api = True
        self.relaunching = False
        self.stopping = False
        self.gen = 0
        self.skip: set[str] = set()
        self.buf: list[tuple[str, bool, str]] = []
        self.partial: dict[str, str] = {}
        self.placed = False
        self.place_tries = 0
        self.overlay_stats = False
        self.overlay_corner = OVERLAY_CORNERS[0][0]
        self.overlay_busy = False
        self.overlay_prev: dict = {}
        self.overlay_refused = False
        self.overlay_told = False

    def to_json(self) -> dict:
        return {
            "scene": self.scene, "binary": self.binary, "label": self.label,
            "overlayStats": self.overlay_stats, "overlayCorner": self.overlay_corner,
            "toggles": dict(self.toggles), "trail": self.trail,
            "renderDebug": sorted(self.render_debug), "instruments": sorted(self.instruments),
            "env": [[k, v] for k, v in self.env_lines], "props": [[k, v] for k, v in self.props],
            "lastCode": self.last_code, "lastTs": self.last_ts,
            "lastTail": [[t, bool(e), ts] for t, e, ts in self.last_tail],
            "lastStopped": self.last_stopped, "api": self.api,
        }

    def from_json(self, d: dict) -> None:
        if not isinstance(d, dict):
            return
        self.scene = _safe_scene(str(d.get("scene") or ""))
        self.binary = str(d.get("binary") or "")
        self.label = str(d.get("label") or self.side)
        valid = {t["key"] for t in FEATURE_TOGGLES}
        tg = d.get("toggles")
        if isinstance(tg, dict):
            self.toggles = {str(k): bool(v) for k, v in tg.items() if str(k) in valid}
        trail = str(d.get("trail") or "")
        self.trail = trail if trail in TRAIL_MODES else TRAIL_MODES[0]
        flags = {f["key"] for f in RENDER_DEBUG_FLAGS}
        rd = d.get("renderDebug")
        if isinstance(rd, list):
            self.render_debug = {str(x) for x in rd if str(x) in flags}
        known = {i["env"] for i in INSTRUMENTS}
        ins = d.get("instruments")
        if isinstance(ins, list):
            self.instruments = {str(x) for x in ins if str(x) in known}
        for attr, key in (("env_lines", "env"), ("props", "props")):
            raw = d.get(key)
            if isinstance(raw, list):
                pairs = [(str(p[0]), str(p[1])) for p in raw
                         if isinstance(p, list) and len(p) == 2 and _is_shell_ident(str(p[0]))
                         and str(p[0]) not in PANEL_OWNED_ENV]
                setattr(self, attr, pairs)
        code = d.get("lastCode")
        self.last_code = int(code) if isinstance(code, int) else None
        self.last_ts = str(d.get("lastTs") or "")
        tail = d.get("lastTail")
        if isinstance(tail, list):
            self.last_tail = [(str(x[0]), bool(x[1]), str(x[2]) if len(x) > 2 else "")
                              if isinstance(x, list) and len(x) >= 2
                              else (str(x), False, "") for x in tail][-RESIDUE_LINES:]
        self.last_stopped = bool(d.get("lastStopped"))
        self.api = d.get("api") is not False
        self.overlay_stats = bool(d.get("overlayStats"))
        corner = str(d.get("overlayCorner") or "")
        self.overlay_corner = corner if corner in dict(OVERLAY_CORNERS) else OVERLAY_CORNERS[0][0]

    def alive(self) -> bool:
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    def live_control(self) -> bool:
        """True when live-class knobs can reach this exhibit: alive and API-capable."""
        return self.alive() and self.api and not self.relaunching

    def sock_path(self) -> Path:
        runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip() or f"/run/user/{os.getuid()}"
        return Path(runtime) / "lwe" / f"exhibit-{self.side.lower()}.sock"

    def state_text(self) -> str:
        if self.relaunching:
            return "relaunching…"
        if self.alive():
            return "live · windowed" if self.mode == "window" else "live · bench"
        if self.last_code and not self.last_stopped:
            return f"exit {self.last_code}"
        return "stopped"


class DevBridge(QObject):
    """Backend for the Developer view: two exhibit slots, three launch verbs, one console.

    The console buffer lives here for the session (CONSOLE_MAX lines, oldest dropped first).
    Every entry carries src (A, B or D for the daemon journal), time (HH:MM:SS at receipt, or
    the journal stamp), text (the message), raw (the unstripped line, what a copy yields) and
    err (stderr, or journal priority warning and above). consoleLines emits the entries
    appended by one read plus how many oldest entries the cap dropped; consoleReset says the
    buffer changed in the middle and the view must rebuild from consoleEntries.
    """

    stateChanged = Signal()
    consoleLines = Signal(list, int)
    consoleReset = Signal()
    consoleCountChanged = Signal()
    tailShown = Signal(str)
    runStarted = Signal(str)
    journalChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.slots: dict[str, _Slot] = {s: _Slot(s) for s in SIDES}
        self._in_flight = False
        self._b_pending = False
        self._mode = ""
        self._objects_cache: dict[str, tuple[float, list]] = {}
        self._journal_proc: QProcess | None = None
        self._journal_seen = False
        self._journal_partial = ""
        self._console: list[dict] = []
        self._console_id = 0
        self._engine_peers: list = []
        self._binary_cache: dict[str, tuple[float, bool]] = {}
        self._scene_cache: tuple[float, list] | None = None
        self._relaunch_timers: dict[str, QTimer] = {}
        for side in SIDES:
            t = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(400)
            t.timeout.connect(lambda s=side: self._do_relaunch(s))
            self._relaunch_timers[side] = t
        self._place_timer = QTimer(self)
        self._place_timer.setInterval(400)
        self._place_timer.timeout.connect(self._place_tick)
        self._overlay_timer = QTimer(self)
        self._overlay_timer.setInterval(OVERLAY_TICK_MS)
        self._overlay_timer.timeout.connect(self._overlay_tick)
        self._b_due = 0.0
        self._restore()
        self._seed_scenes()
        for side in SIDES:
            self._replay_tail(side, announce=False)

    def _restore(self) -> None:
        d = atomic.read_json(_slots_file(), default=None)
        if not isinstance(d, dict):
            return
        for side in SIDES:
            self.slots[side].from_json(d.get(side) or {})

    @staticmethod
    def _now_playing_wid() -> str:
        """The wid the daemon shows right now, or "" when nothing answers."""
        try:
            if api_client.available():
                st = api_client.status() or {}
                cur = st.get("current") or {}
                return str(cur.get("ui_id") or cur.get("id") or "")
        except Exception:
            pass
        return ""

    def _seed_scenes(self) -> bool:
        """A slot with no scene takes the wallpaper the daemon is showing, so both slots are
        always configured. Returns True when a slot changed."""
        wid = ""
        changed = False
        for side in SIDES:
            s = self.slots[side]
            if s.scene:
                continue
            if not wid:
                wid = self._now_playing_wid()
            if wid and os.path.isdir(os.path.join(_wallpapers_dir(), wid)):
                s.scene = wid
                changed = True
        if changed:
            self._persist()
        return changed

    def _persist(self) -> None:
        try:
            paths.ensure_dirs()
            atomic.atomic_write_json(_slots_file(),
                                     {"version": 1, **{s: self.slots[s].to_json() for s in SIDES}})
        except (OSError, ValueError, TypeError):
            pass

    # -- console buffer ------------------------------------------------------------------

    def _entry(self, src: str, text: str, err: bool, ts: str = "", raw: str | None = None,
               residue: bool = False) -> dict:
        self._console_id += 1
        return {"id": self._console_id, "src": src, "time": ts or _now_hhmmss(), "text": text,
                "raw": text if raw is None else raw, "err": bool(err), "residue": residue}

    def _push(self, entries: list[dict]) -> None:
        """Append to the session buffer, dropping the oldest past the cap, and tell the view."""
        if not entries:
            return
        self._console.extend(entries)
        dropped = len(self._console) - CONSOLE_MAX
        if dropped > 0:
            del self._console[:dropped]
        else:
            dropped = 0
        self.consoleLines.emit(entries, dropped)
        self.consoleCountChanged.emit()

    def _say(self, src: str, text: str, err: bool) -> None:
        self._push([self._entry(src, text, err)])

    def _clear_side(self, side: str) -> None:
        self._console = [e for e in self._console if e["src"] != side]
        self.consoleReset.emit()
        self.consoleCountChanged.emit()

    @Slot(result="QVariantList")
    def consoleEntries(self) -> list:
        return list(self._console)

    def _get_console_count(self) -> int:
        return len(self._console)

    consoleCount = Property(int, _get_console_count, notify=consoleCountChanged)

    @Slot(str)
    def setClipboard(self, text: str) -> None:
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(text)

    def _replay_tail(self, side: str, announce: bool = True) -> None:
        """Put the slot's crash residue into the console under its header, replacing the copy
        a previous replay left, so the residue shows once however often Tail is pressed."""
        s = self._slot(side)
        if s is None:
            return
        if not s.last_tail and s.last_code is None:
            return
        self._console = [e for e in self._console if not (e["src"] == side and e["residue"])]
        code = -1 if s.last_code is None else int(s.last_code)
        head = f"Last run \u00b7 exit {code} \u00b7 {s.last_ts}"
        block = [self._entry(side, head, False, ts=" " * 8, residue=True)]
        block.extend(self._entry(side, t, e, ts=ts, residue=True) for t, e, ts in s.last_tail)
        self._console.extend(block)
        del self._console[:-CONSOLE_MAX]
        self.consoleReset.emit()
        self.consoleCountChanged.emit()
        if announce:
            self.tailShown.emit(side)

    @Slot(str)
    def showTail(self, side: str) -> None:
        self._replay_tail(side)

    def _changed(self) -> None:
        self._persist()
        self.stateChanged.emit()

    def _slot(self, side: str) -> _Slot | None:
        return self.slots.get(str(side or "").upper())

    @Slot(result="QVariantList")
    def featureToggles(self) -> list:
        return [{"key": t["key"], "label": t["label"], "env": t["env"],
                 "tip": t["tip"] + "\n" + REACH_RELAUNCH} for t in FEATURE_TOGGLES]

    @Slot(result="QVariantList")
    def renderDebugFlags(self) -> list:
        return [{"key": f["key"], "label": f["label"], "tip": f["tip"] + "\n" + REACH_RELAUNCH}
                for f in RENDER_DEBUG_FLAGS]

    @Slot(result="QVariantList")
    def trailModes(self) -> list:
        return list(TRAIL_MODES)

    @Slot(result=str)
    def trailTip(self) -> str:
        return TRAIL_TIP + "\n" + REACH_RELAUNCH

    @Slot(result="QVariantList")
    def instruments(self) -> list:
        return [{"env": i["env"], "name": i["env"].removeprefix("LWE_").lower().replace("_", ""),
                 "live": bool(i.get("live")),
                 "tip": i["tip"] + "\n" + (REACH_LIVE if i.get("live") else REACH_RELAUNCH)}
                for i in INSTRUMENTS]

    @Slot(result="QVariantList")
    def rawEnvReference(self) -> list:
        return [{"env": r["env"], "what": r["what"], "prints": r["prints"]} for r in RAW_ENV_REFERENCE]

    @Slot(str, result="QVariantMap")
    def slotState(self, side: str) -> dict:
        s = self._slot(side)
        if s is None:
            return {}
        return {
            "scene": s.scene, "sceneTitle": self._scene_title(s.scene),
            "binary": s.binary, "binaryLabel": self._binary_label(s.binary),
            "label": s.label, "state": s.state_text(), "alive": s.alive(),
            "relaunching": s.relaunching, "legacy": not s.api,
            "liveControl": s.live_control(),
            "lastCode": -1 if s.last_code is None else int(s.last_code),
            "lastStopped": s.last_stopped,
            "lastTs": s.last_ts, "hasResidue": bool(s.last_tail) or s.last_code is not None,
            "trail": s.trail,
            "overlayStats": s.overlay_stats, "overlayCorner": s.overlay_corner,
            "overlayCornerLabel": dict(OVERLAY_CORNERS)[s.overlay_corner],
        }

    @Slot(str, str)
    def setScene(self, side: str, wid: str) -> None:
        """Live over the exhibit's socket for a library id; a probe is a path the show verb
        cannot name, so that case relaunches."""
        s = self._slot(side)
        if s is None:
            return
        wid = str(wid or "")
        if wid and not _safe_scene(wid):
            return
        if wid == s.scene:
            return
        s.scene = wid
        s.skip = set()
        if s.live_control() and wid and not wid.startswith("probe:"):
            try:
                reply = api_client.show(wid, sock=s.sock_path())
            except Exception:
                reply = None
            if not (reply and reply.get("ok")):
                self._schedule_relaunch(s)
        elif s.alive():
            self._schedule_relaunch(s)
        self._changed()

    @Slot(str, str)
    def setBinary(self, side: str, path: str) -> None:
        s = self._slot(side)
        if s is None:
            return
        path = str(path or "").strip()
        if path == s.binary:
            return
        s.binary = path
        s.api = self._binary_is_api(path)
        if s.alive():
            self._schedule_relaunch(s)
        self._changed()

    @Slot(str, str)
    def setLabel(self, side: str, text: str) -> None:
        s = self._slot(side)
        if s is None:
            return
        text = str(text or "").replace("\n", " ").strip() or s.side
        if text == s.label:
            return
        s.label = text
        if s.alive() and not self._push_overlay(s):
            self._schedule_relaunch(s)
        self._changed()

    @Slot(result="QVariantList")
    def overlayCorners(self) -> list:
        return [{"value": v, "label": lbl} for v, lbl in OVERLAY_CORNERS]

    @Slot(str, bool)
    def setOverlayStats(self, side: str, on: bool) -> None:
        s = self._slot(side)
        if s is None or bool(on) == s.overlay_stats:
            return
        s.overlay_stats = bool(on)
        s.overlay_prev = {}
        self._push_overlay(s)
        self._changed()
        if s.overlay_stats:
            self._overlay_tick()

    @Slot(str, str)
    def setOverlayCorner(self, side: str, corner: str) -> None:
        s = self._slot(side)
        corner = str(corner or "")
        if s is None or corner not in dict(OVERLAY_CORNERS) or corner == s.overlay_corner:
            return
        s.overlay_corner = corner
        self._push_overlay(s, corner_only=True)
        self._changed()

    @Slot(str, str, result=bool)
    def toggleOn(self, side: str, key: str) -> bool:
        s = self._slot(side)
        entry = next((t for t in FEATURE_TOGGLES if t["key"] == key), None)
        if s is None or entry is None:
            return False
        return bool(s.toggles.get(key, entry["default_on"]))

    @Slot(str, str, bool)
    def setToggle(self, side: str, key: str, on: bool) -> None:
        s = self._slot(side)
        if s is None or key not in {t["key"] for t in FEATURE_TOGGLES}:
            return
        s.toggles[key] = bool(on)
        entry = next(t for t in FEATURE_TOGGLES if t["key"] == key)
        if on and entry.get("excludes"):
            s.toggles[entry["excludes"]] = False
        if s.alive():
            self._schedule_relaunch(s)
        self._changed()

    @Slot(str, result=str)
    def trailMode(self, side: str) -> str:
        s = self._slot(side)
        return s.trail if s else TRAIL_MODES[0]

    @Slot(str, str)
    def setTrailMode(self, side: str, mode: str) -> None:
        s = self._slot(side)
        mode = str(mode or "")
        if s is None or mode not in TRAIL_MODES or mode == s.trail:
            return
        s.trail = mode
        if s.alive():
            self._schedule_relaunch(s)
        self._changed()

    @Slot(str, str, result=bool)
    def renderDebugOn(self, side: str, key: str) -> bool:
        s = self._slot(side)
        return bool(s and key in s.render_debug)

    @Slot(str, str, bool)
    def setRenderDebug(self, side: str, key: str, on: bool) -> None:
        s = self._slot(side)
        if s is None or key not in {f["key"] for f in RENDER_DEBUG_FLAGS}:
            return
        if on:
            s.render_debug.add(key)
        else:
            s.render_debug.discard(key)
        if s.alive():
            self._schedule_relaunch(s)
        self._changed()

    @Slot(str, str, result=bool)
    def instrumentOn(self, side: str, env: str) -> bool:
        s = self._slot(side)
        return bool(s and env in s.instruments)

    @Slot(str, str, bool)
    def setInstrument(self, side: str, env: str, on: bool) -> None:
        s = self._slot(side)
        if s is None or env not in {i["env"] for i in INSTRUMENTS}:
            return
        if on:
            s.instruments.add(env)
        else:
            s.instruments.discard(env)
        if s.alive():
            reply = None
            if env in LIVE_INSTRUMENTS and s.live_control():
                try:
                    reply = api_client.set_instrument(env, bool(on), sock=s.sock_path())
                except Exception:
                    reply = None
            if not (reply and reply.get("ok")):
                self._schedule_relaunch(s)
        self._changed()

    @Slot(str, result=str)
    def envText(self, side: str) -> str:
        s = self._slot(side)
        return "\n".join(f"{k}={v}" for k, v in s.env_lines) if s else ""

    @Slot(str, str, result=int)
    def setEnvText(self, side: str, text: str) -> int:
        """Replace the side's raw env lines from a KEY=VALUE block. Returns the number of
        rejected lines; a bad key never enters the launch environment."""
        s = self._slot(side)
        if s is None:
            return 0
        lines, bad = _parse_kv_lines(text, PANEL_OWNED_ENV)
        if lines != s.env_lines:
            s.env_lines = lines
            if s.alive():
                self._schedule_relaunch(s)
            self._changed()
        return len(bad)

    @Slot(str, result=str)
    def propText(self, side: str) -> str:
        s = self._slot(side)
        return "\n".join(f"{k}={v}" for k, v in s.props) if s else ""

    @Slot(str, str, result=int)
    def setPropText(self, side: str, text: str) -> int:
        """Replace the side's property queue (`--set-property name=value`, relaunch class; the
        engine has no live property verb) from a NAME=VALUE block."""
        s = self._slot(side)
        if s is None:
            return 0
        lines, bad = _parse_kv_lines(text)
        if lines != s.props:
            s.props = lines
            if s.alive():
                self._schedule_relaunch(s)
            self._changed()
        return len(bad)

    def _scene_entries(self) -> list[dict]:
        """Library entries (wid + title) from the wallpapers directory, cached on its mtime."""
        root = _wallpapers_dir()
        try:
            stamp = os.stat(root).st_mtime
        except OSError:
            return []
        if self._scene_cache and self._scene_cache[0] == stamp:
            return self._scene_cache[1]
        out: list[dict] = []
        try:
            names = sorted(os.listdir(root))
        except OSError:
            names = []
        for name in names:
            d = os.path.join(root, name)
            if not os.path.isfile(os.path.join(d, "project.json")):
                continue
            title = name
            try:
                info = project_disc.read(Path(d))
                title = str(info.get("title") or "").strip() or name
            except Exception:
                pass
            out.append({"wid": name, "title": title})
        out.sort(key=lambda e: e["title"].lower())
        self._scene_cache = (stamp, out)
        return out

    @Slot(result="QVariantList")
    def sceneChoices(self) -> list:
        """Library + probes for the Scene dropdown: [{wid, title, section}]."""
        out = [dict(e, section="library") for e in self._scene_entries()]
        for p in self.probeList():
            out.append({"wid": p["target"], "title": p["name"], "section": "probes"})
        return out

    @Slot(result="QVariantList")
    def probeList(self) -> list:
        d = _probes_dir()
        if not d.is_dir():
            return []
        return [{"name": p.name, "target": "probe:" + p.name}
                for p in sorted(d.iterdir()) if p.is_dir() and (p / "project.json").is_file()]

    def _scene_title(self, wid: str) -> str:
        if not wid:
            return ""
        if wid.startswith("probe:"):
            return wid.split(":", 1)[1]
        for e in self._scene_entries():
            if e["wid"] == wid:
                return e["title"]
        return wid

    @Slot(result="QVariantList")
    def binaryChoices(self) -> list:
        """[{label, value}]: the daemon's own binary first, then every executable in the
        dev-binaries dir, then any hand-picked slot path outside it."""
        out = [{"label": "Same as daemon", "value": ""}]
        d = _binaries_dir()
        seen: set[str] = set()
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.is_file() and os.access(p, os.X_OK):
                    out.append({"label": p.name, "value": str(p)})
                    seen.add(str(p))
        for side in SIDES:
            b = self.slots[side].binary
            if b and b not in seen:
                out.append({"label": os.path.basename(b), "value": b})
                seen.add(b)
        return out

    def _binary_label(self, path: str) -> str:
        return os.path.basename(path) if path else "Same as daemon"

    @Slot(result=str)
    def binariesDir(self) -> str:
        return str(_binaries_dir())

    def _binary_is_api(self, path: str) -> bool:
        """Whether a binary speaks the daemon API (`--api-socket` in its help). A legacy
        build does not and launches with the old command line. Probed once per (path, mtime)."""
        if not path:
            return True
        try:
            stamp = os.stat(path).st_mtime
        except OSError:
            return False
        cached = self._binary_cache.get(path)
        if cached and cached[0] == stamp:
            return cached[1]
        ok = False
        try:
            r = subprocess.run([path, "--help"], capture_output=True, text=True,
                               timeout=5, check=False)
            ok = "--api-socket" in (r.stdout + r.stderr)
        except (OSError, subprocess.SubprocessError):
            ok = False
        self._binary_cache[path] = (stamp, ok)
        return ok

    def _scene_dir(self, s: _Slot) -> str:
        if not s.scene:
            return ""
        if s.scene.startswith("probe:"):
            return str(_probes_dir() / s.scene.split(":", 1)[1])
        return os.path.join(_wallpapers_dir(), s.scene)

    def _binary_for(self, s: _Slot) -> str:
        return s.binary or _engine_bin()

    def _layout(self) -> dict | None:
        """The focused output as the compositor lays it out (placement.layout)."""
        return placement.layout(self._hyprctl)

    def _quadrant(self, side: str, layout: dict | None = None) -> tuple[int, int, int, int] | None:
        """The slot's cell of the usable area: A top-left, B top-right (placement.quadrant)."""
        lay = layout or self._layout()
        return placement.quadrant(side, lay) if lay else None

    def _window_geometry(self, side: str = "A") -> str | None:
        """--window geometry for one exhibit: its cell's size. The position part is ignored
        on Wayland; the compositor maps the window and _place_tick moves it."""
        return placement.window_geometry(side, self._layout())

    def compose_argv(self, side: str, window: str | None = None) -> list[str]:
        """Engine argv for one slot, always windowed. `window` overrides the slot's quadrant
        geometry. Returns [] when the slot has no scene or no output resolves, so callers
        refuse instead of launching blind."""
        s = self._slot(side)
        if s is None:
            return []
        d = self._scene_dir(s)
        if not d:
            return []
        argv = [self._binary_for(s), "--assets-dir", _assets_dir(), "--fps", "30",
                "--scaling", "default", "--silent", "--no-audio-processing",
                "--disable-mouse", "--no-fullscreen-pause"]
        window = window or self._window_geometry(s.side)
        if not window:
            return []
        argv += ["--window", window]
        if s.api:
            argv.append("--api-socket")
        else:
            for oid in sorted(s.skip, key=lambda x: (len(x), x)):
                argv += ["--render-debug", "skip-object=" + oid]
        for f in RENDER_DEBUG_FLAGS:
            if f["key"] in s.render_debug:
                argv += ["--render-debug", f["key"]]
        for name, value in s.props:
            argv += ["--set-property", f"{name}={value}"]
        argv += ["--bg", d]
        return argv

    def compose_env(self, side: str) -> tuple[dict[str, str], list[str]]:
        """(assign, unset) for one slot's launch environment. Every switch resolves to a value
        or an explicit unset, because the panel's own environment may carry any of these
        variables. Raw env lines apply last."""
        s = self._slot(side)
        if s is None:
            return {}, []
        env: dict[str, str] = {}
        unset: list[str] = []
        for t in FEATURE_TOGGLES:
            on = bool(s.toggles.get(t["key"], t["default_on"]))
            val = t["on"] if on else t["off"]
            if val is None:
                unset.append(t["env"])
            else:
                env[t["env"]] = val
        if self.toggleOn(side, "resclampfx"):
            # the clamp stays on for the scene target; only the composites are exempt
            env.pop("LWE_SSFACTOR", None)
            unset.append("LWE_SSFACTOR")
        if s.trail == "Exact":
            env[TRAIL_ENV] = "exact"
        else:
            unset.append(TRAIL_ENV)
        for i in INSTRUMENTS:
            if i["env"] in s.instruments:
                env[i["env"]] = str(i.get("value", "1"))
            else:
                unset.append(i["env"])
        env["LWE_OVERLAY_TEXT"] = s.label
        if s.api:
            env["LWE_SOCKET"] = str(s.sock_path())
            env["LWE_WINTITLE"] = f"lwe-exhibit-{s.side.lower()}"
        else:
            unset += ["LWE_SOCKET", "LWE_WINTITLE"]
        for k, v in s.env_lines:
            env[k] = v
        unset = [u for u in unset if u not in env]
        return env, unset

    @Slot(str, result=str)
    def launchPreview(self, side: str) -> str:
        """The composed command line as text, for the raw env door."""
        env, unset = self.compose_env(side)
        assign = " ".join(f"{k}={v}" for k, v in sorted(env.items()))
        un = " ".join(f"-u {k}" for k in sorted(unset))
        prefix = f"env {un} {assign}".strip() if un else assign
        return (prefix + " " + " ".join(self.compose_argv(side))).strip()

    @Slot(result=bool)
    def verbsBusy(self) -> bool:
        return self._in_flight

    @Slot(result=bool)
    def anyAlive(self) -> bool:
        return any(self.slots[s].alive() or self.slots[s].relaunching for s in SIDES)

    @Slot(str, result=bool)
    def alive(self, side: str) -> bool:
        s = self._slot(side)
        return bool(s and s.alive())

    @Slot(result=str)
    def runMode(self) -> str:
        """"window" while any exhibit runs, "" otherwise."""
        return self._mode

    @Slot(result=str)
    def benchMode(self) -> str:
        """Which exhibits are alive, for the deck subtitle: "", "A", "B" or "A + B"."""
        alive = [s for s in SIDES if self.slots[s].alive()]
        return " + ".join(alive)

    def set_engine_peers(self, peers: list) -> None:
        """Engine-conflict peers: symmetrical refusal with the Workshop preview and the
        wizard bench, since two engines on one display corrupt it."""
        self._engine_peers = [p for p in peers if p is not None]

    @Slot(result=bool)
    def engineBusy(self) -> bool:
        return self.anyAlive() or self._in_flight

    @Slot(str)
    def launch(self, side: str) -> None:
        """Launch A / Launch B: that slot alone, windowed in its quadrant of the focused
        output while the daemon keeps the desktop. A running exhibit on the same side
        restarts; the other side is left as it is."""
        s = self._slot(side)
        if s is None or self._in_flight:
            return
        s.api = self._binary_is_api(s.binary)
        geo = self._window_geometry(s.side)
        if not geo or not self.compose_argv(s.side, geo):
            self._say(s.side, "no scene chosen or no display output to place the window on", True)
            return
        if self._orphan_answers(s):
            return
        self._in_flight = True
        self.stateChanged.emit()
        self._relaunch_timers[s.side].stop()
        s.gen += 1
        s.relaunching = False
        if s.side == "B":
            self._b_pending = False
        self._reap(s, deliberate=True)
        self._mode = "window"
        s.placed = False
        s.place_tries = 0
        self._spawn(s, geo)
        self._in_flight = False
        if not self._place_timer.isActive():
            self._place_timer.start()
        self.stateChanged.emit()

    @Slot()
    def launchBoth(self) -> None:
        """Launch both: A and B windowed side by side while the daemon keeps the desktop.
        B follows once A's window maps, because two cold loads of one heavy scene serialise."""
        if self._in_flight:
            return
        for side in SIDES:
            self.slots[side].api = self._binary_is_api(self.slots[side].binary)
        geo = self._window_geometry("A")
        if not geo:
            self._say("A", "no display output to place the windows on", True)
            return
        missing = [side for side in SIDES if not self.compose_argv(side, geo)]
        if missing:
            self._say(missing[0], "no scene chosen", True)
            return
        if any(self._orphan_answers(self.slots[side]) for side in SIDES):
            return
        self._in_flight = True
        self.stateChanged.emit()
        self._cancel_pending()
        self._reap_all()
        self._mode = "window"
        for side in SIDES:
            self.slots[side].placed = False
            self.slots[side].place_tries = 0
        self._spawn(self.slots["A"], geo)
        self._b_pending = True
        self._b_due = time.monotonic() + 10.0
        self._place_timer.start()
        self.stateChanged.emit()

    @Slot()
    def stop(self) -> None:
        """Stop every exhibit."""
        self._cancel_pending()
        self._reap_all()
        self._mode = ""
        self._in_flight = False
        self.stateChanged.emit()

    @Slot(str)
    def stopSide(self, side: str) -> None:
        """Stop one exhibit and leave the other side as it is."""
        s = self._slot(side)
        if s is None:
            return
        self._relaunch_timers[s.side].stop()
        s.gen += 1
        s.relaunching = False
        if s.side == "B":
            self._b_pending = False
        self._reap(s, deliberate=True)
        if not self.anyAlive():
            self._place_timer.stop()
            self._mode = ""
            self._in_flight = False
        self.stateChanged.emit()

    def _orphan_answers(self, s: _Slot) -> bool:
        """True when something the slot does not own still answers on its socket."""
        if not s.api or s.alive():
            return False
        try:
            if api_client.available(s.sock_path()):
                self._say(s.side, "an exhibit this panel does not own still answers on the socket", True)
                return True
        except Exception:
            pass
        return False

    def _cancel_pending(self) -> None:
        """Drop every queued relaunch and the staggered B spawn before a verb changes state."""
        for side in SIDES:
            self._relaunch_timers[side].stop()
            self.slots[side].gen += 1
            self.slots[side].relaunching = False
        self._b_pending = False
        self._place_timer.stop()

    def _spawn(self, s: _Slot, window: str | None) -> None:
        """Spawn one slot's engine with its current argv + env. The presentation (standdown
        for bench, nothing for window) is already arranged by the caller. Channels stay
        separate: the stream is the severity the console marks."""
        argv = self.compose_argv(s.side, window)
        if not argv:
            return
        s.buf = []
        s.partial = {}
        env, unset = self.compose_env(s.side)
        proc = QProcess(self)
        qenv = QProcessEnvironment.systemEnvironment()
        for k, v in env.items():
            qenv.insert(k, v)
        for k in unset:
            qenv.remove(k)
        proc.setProcessEnvironment(qenv)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        proc.readyReadStandardOutput.connect(lambda side=s.side: self._drain(side, False))
        proc.readyReadStandardError.connect(lambda side=s.side: self._drain(side, True))
        proc.finished.connect(
            lambda code, status, side=s.side, p=proc: self._on_finished(side, p, code, status))
        proc.errorOccurred.connect(lambda err, side=s.side, p=proc: self._on_error(side, p, err))
        if s.api:
            try:
                s.sock_path().unlink()
            except OSError:
                pass
        s.proc = proc
        s.mode = "window"
        s.stopping = False
        self._clear_side(s.side)
        self.runStarted.emit(s.side)
        bar = "=" * 33
        self._say(s.side, bar, False)
        self._say(s.side, "Beginning new bench run " + time.strftime("%Y.%m.%d %H:%M:%S"), False)
        self._say(s.side, bar, False)
        proc.start(argv[0], argv[1:])
        s.relaunching = False
        if s.api and s.skip:
            self._push_skip_when_ready(s, proc, time.monotonic() + 20.0)
        if s.api:
            s.overlay_prev = {}
            s.overlay_refused = False
            s.overlay_told = False
            self._push_overlay_when_ready(s, proc, time.monotonic() + 20.0)
            if not self._overlay_timer.isActive():
                self._overlay_timer.start()

    def _push_overlay_when_ready(self, s: _Slot, proc: QProcess, deadline: float) -> None:
        """The corner and the stats text ride the socket, so they follow the launch as soon
        as the exhibit answers; the label itself is already in its environment."""
        if s.proc is not proc or not s.alive():
            return
        try:
            ready = api_client.available(s.sock_path())
        except Exception:
            ready = False
        if ready:
            self._push_overlay(s, corner_only=not s.overlay_stats)
            return
        if time.monotonic() < deadline:
            QTimer.singleShot(300, lambda: self._push_overlay_when_ready(s, proc, deadline))

    def _push_overlay(self, s: _Slot, corner_only: bool = False) -> bool:
        """Send the slot's overlay corner, and its label as the text unless the stats
        sampler owns the text. True when the exhibit took it."""
        if not s.live_control():
            return False
        try:
            if corner_only:
                reply = api_client.set_overlay(corner=s.overlay_corner, sock=s.sock_path())
            elif s.overlay_stats:
                reply = api_client.set_overlay(text=self.overlay_text(s.label, None),
                                               corner=s.overlay_corner, sock=s.sock_path())
            else:
                reply = api_client.set_overlay(text=s.label, corner=s.overlay_corner,
                                               sock=s.sock_path())
        except Exception:
            reply = None
        if _overlay_unknown(reply):
            s.overlay_refused = True
            self._tell_overlay_refused(s)
        return bool(reply and reply.get("ok"))

    def _tell_overlay_refused(self, s: _Slot) -> None:
        if not s.overlay_told:
            s.overlay_told = True
            self._say(
                s.side, "this build has no set-overlay command; the stats overlay needs an engine "
                "built from the current tree", True)

    overlay_text = staticmethod(overlay_text)
    sample_exhibit = staticmethod(sample_exhibit)

    def _overlay_tick(self) -> None:
        """Once a second per live exhibit with stats on: sample on a worker thread and push
        the text over that exhibit's socket from the same thread."""
        for side in SIDES:
            s = self.slots[side]
            if not s.overlay_stats or s.overlay_busy or not s.live_control():
                continue
            if s.overlay_refused:
                self._tell_overlay_refused(s)
                continue
            try:
                pid = int(s.proc.processId())
            except (AttributeError, TypeError, ValueError):
                continue
            if pid <= 0:
                continue
            s.overlay_busy = True
            sock, label, prev, gen = s.sock_path(), s.label, dict(s.overlay_prev), s.gen

            def work(s=s, pid=pid, sock=sock, label=label, prev=prev, gen=gen) -> None:
                try:
                    sample, nxt = sample_exhibit(pid, sock, prev)
                    # a relaunch bumps the generation: a late sample belongs to the old process
                    if s.gen != gen:
                        return
                    s.overlay_prev = nxt
                    reply = api_client.set_overlay(text=overlay_text(label, sample), sock=sock)
                    if _overlay_unknown(reply):
                        s.overlay_refused = True
                except Exception:
                    pass
                finally:
                    s.overlay_busy = False

            threading.Thread(target=work, daemon=True).start()

    def _push_skip_when_ready(self, s: _Slot, proc: QProcess, deadline: float) -> None:
        """Isolator flags at launch would keep the objects from being built at all, so an
        API exhibit gets its set over the socket as soon as it answers."""
        if s.proc is not proc or not s.alive():
            return
        try:
            ready = api_client.available(s.sock_path())
        except Exception:
            ready = False
        if ready:
            self._push_skip(s)
            return
        if time.monotonic() < deadline:
            QTimer.singleShot(300, lambda: self._push_skip_when_ready(s, proc, deadline))

    def _reap(self, s: _Slot, deliberate: bool = True) -> None:
        """Terminate one slot's engine. A deliberate stop records its real exit as residue
        marked stopped; a relaunch reap disconnects the handler so it records nothing."""
        p = s.proc
        if p is None:
            return
        s.gen += 1
        if deliberate:
            s.stopping = True
        else:
            try:
                p.finished.disconnect()
                p.errorOccurred.disconnect()
            except (RuntimeError, TypeError):
                pass
            s.proc = None
        if p.state() != QProcess.ProcessState.NotRunning:
            p.terminate()
            if not p.waitForFinished(1500):
                p.kill()
                p.waitForFinished(1000)
        if not deliberate:
            try:
                p.deleteLater()
            except RuntimeError:
                pass

    def _reap_all(self) -> None:
        for side in SIDES:
            self._reap(self.slots[side], deliberate=True)

    def _schedule_relaunch(self, s: _Slot) -> None:
        """A relaunch-class edit on a live side queues one debounced restart of that side only."""
        if not s.alive():
            return
        s.relaunching = True
        self._relaunch_timers[s.side].start()
        self.stateChanged.emit()

    def _do_relaunch(self, side: str) -> None:
        """Reap the side and respawn after a beat: a dying web engine still holds the browser
        singleton lock for a moment."""
        s = self.slots[side]
        if not s.alive():
            s.relaunching = False
            self.stateChanged.emit()
            return
        s.api = self._binary_is_api(s.binary)
        self._reap(s, deliberate=False)
        gen = s.gen
        QTimer.singleShot(300, lambda: self._respawn(s, gen))

    def _respawn(self, s: _Slot, gen: int) -> None:
        """Only the relaunch that queued this respawn may run it: a verb in between bumps
        the slot generation and the stale respawn does nothing."""
        if s.gen != gen or s.proc is not None:
            return
        geo = self._window_geometry(s.side) or "0x0x1280x720"
        s.placed = False
        s.place_tries = 0
        self._spawn(s, geo)
        if not self._place_timer.isActive():
            self._place_timer.start()
        s.relaunching = False
        self.stateChanged.emit()

    def _on_finished(self, side: str, proc: QProcess, code: int, status: object) -> None:
        """Record residue. A signal death reports the signal number; the shell convention of
        128 + signal is what the residue shows. A late finish from a process the slot no
        longer owns is ignored."""
        s = self.slots[side]
        if proc is not s.proc:
            return
        crashed = status == QProcess.ExitStatus.CrashExit
        self._retire(s, 128 + int(code) if crashed else int(code))

    def _on_error(self, side: str, proc: QProcess, err: object) -> None:
        """A start failure never reaches finished; it must still release the hold."""
        s = self.slots[side]
        if proc is not s.proc or err != QProcess.ProcessError.FailedToStart:
            return
        self._say(side, "the binary did not start", True)
        self._retire(s, 126)

    def _retire(self, s: _Slot, exit_code: int) -> None:
        s.last_code = exit_code
        s.last_stopped = s.stopping
        s.last_ts = _now_hhmm()
        s.last_tail = list(s.buf[-RESIDUE_LINES:])
        p = s.proc
        s.proc = None
        s.mode = ""
        s.stopping = False
        s.relaunching = False
        if p is not None:
            try:
                p.deleteLater()
            except RuntimeError:
                pass
        if s.api:
            try:
                s.sock_path().unlink()
            except OSError:
                pass
        if not self.anyAlive():
            try:
                self._place_timer.stop()
            except RuntimeError:
                pass
            self._b_pending = False
            self._in_flight = False
            self._mode = ""
            self._overlay_timer.stop()
        self._changed()

    def _drain(self, side: str, stderr: bool) -> None:
        s = self.slots[side]
        proc = s.proc
        if proc is None:
            return
        raw = proc.readAllStandardError() if stderr else proc.readAllStandardOutput()
        key = "err" if stderr else "out"
        data = s.partial.get(key, "") + bytes(raw).decode("utf-8", "replace")
        if data and not data.endswith("\n"):
            data, _, s.partial[key] = data.rpartition("\n")
            s.partial[key] = s.partial[key][-4096:]
        else:
            s.partial[key] = ""
        lines = [ln[:2000] for ln in data.splitlines() if ln.strip()]
        if not lines:
            return
        now = _now_hhmmss()
        s.buf.extend((ln, stderr, now) for ln in lines)
        del s.buf[:-4000]
        self._push([self._entry(side, ln, stderr, ts=now) for ln in lines])

    @Slot(str, result="QVariantList")
    def tailLines(self, side: str) -> list:
        """The retained tail of the slot's last run."""
        s = self._slot(side)
        return [{"text": t, "err": e, "time": ts} for t, e, ts in s.last_tail] if s else []

    def _journal_unit(self) -> str:
        return C.ENGINE_SERVICE

    @Slot(result=bool)
    def journalRunning(self) -> bool:
        return self._journal_proc is not None

    @Slot(bool)
    def setFollowingDaemon(self, on: bool) -> None:
        """Follow the engine service's journal into the console while the view shows. The
        first open pulls a backlog that fills the console; later opens pull none, the buffer
        already holds what was read. A SIGKILLed panel orphans the follower inside the user
        slice; logout reaps it."""
        if not on:
            self._stop_journal()
            return
        if self._seed_scenes():
            self.stateChanged.emit()
        if self._journal_proc is not None:
            return
        exe = shutil.which("journalctl")
        if not exe:
            self._say("D", "journalctl not on PATH", True)
            return
        proc = QProcess(self)
        proc.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._drain_journal)
        proc.finished.connect(self._on_journal_finished)
        self._journal_proc = proc
        backlog = str(JOURNAL_BACKLOG) if not self._journal_seen else "0"
        self._journal_seen = True
        self._journal_partial = ""
        proc.start(exe, ["--user", "-u", self._journal_unit(), "-f", "-n", backlog,
                         "--no-pager", "-o", "json"])
        self.journalChanged.emit()

    def _stop_journal(self) -> None:
        """Null the handle first (the reap can run inside the QProcess's own signal dispatch)
        and reap by handle, never by name."""
        proc, self._journal_proc = self._journal_proc, None
        if proc is None:
            return
        try:
            proc.finished.disconnect(self._on_journal_finished)
        except (RuntimeError, TypeError):
            pass
        try:
            proc.terminate()
            if not proc.waitForFinished(1000):
                proc.kill()
                proc.waitForFinished(1000)
        except RuntimeError:
            pass
        proc.deleteLater()
        self.journalChanged.emit()

    def _drain_journal(self) -> None:
        proc = self._journal_proc
        if proc is None:
            return
        data = self._journal_partial + bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
        if data and not data.endswith("\n"):
            data, _, self._journal_partial = data.rpartition("\n")
        else:
            self._journal_partial = ""
        entries = []
        for ln in data.splitlines():
            if not ln.strip():
                continue
            src, text, err, ts, raw = _journal_fields(ln)
            entries.append(self._entry("D", text, err, ts=ts, raw=raw))
        self._push(entries)

    def _on_journal_finished(self, code: int, _status: object) -> None:
        proc, self._journal_proc = self._journal_proc, None
        if proc is not None:
            proc.deleteLater()
        self.journalChanged.emit()

    @Slot(str, result="QVariantList")
    def objectList(self, side: str) -> list:
        """Objects of the slot's scene for the isolator: [{objid, name, type, parent, on}].
        `on` is the session-scoped isolator state for that side."""
        s = self._slot(side)
        if s is None:
            return []
        d = self._scene_dir(s)
        if not d or not os.path.isdir(d):
            return []
        objs = self._scene_objects(d)
        return [{"objid": str(o.get("objid", "")), "name": str(o.get("name") or ""),
                 "type": str(o.get("type") or "generic"), "parent": str(o.get("parent") or ""),
                 "on": str(o.get("objid", "")) not in s.skip} for o in objs]

    def _scene_objects(self, d: str) -> list:
        """Parsed scene objects, cached on the scene files' mtime so isolator flips never
        re-read the package."""
        stamp = 0.0
        for name in ("scene.pkg", "scene.json", "project.json"):
            try:
                stamp = max(stamp, os.stat(os.path.join(d, name)).st_mtime)
            except OSError:
                pass
        cached = self._objects_cache.get(d)
        if cached and cached[0] == stamp:
            return cached[1]
        try:
            objs = list(objects_disc.extract(d))
        except Exception:
            objs = []
        self._objects_cache[d] = (stamp, objs)
        return objs

    @Slot(str, result=bool)
    def liveControl(self, side: str) -> bool:
        s = self._slot(side)
        return bool(s and s.live_control())

    @Slot(str, result=bool)
    def isolatorEditable(self, side: str) -> bool:
        """The isolator edits a side whenever it has a scene, unless a legacy build is alive
        (nothing can reach it until it relaunches)."""
        s = self._slot(side)
        if s is None or not s.scene:
            return False
        return not (s.alive() and not s.api)

    @Slot(str, "QVariantList", bool)
    def setObjectsOn(self, side: str, ids: list, on: bool) -> None:
        """Show or hide objects on one side. Live over that side's socket while it can take
        it; otherwise the set waits for the next launch, which applies it as skip-object
        flags. Session-scoped: never written to disk or to the wallpaper conf."""
        s = self._slot(side)
        if s is None:
            return
        for oid in ids:
            oid = str(oid)
            if on:
                s.skip.discard(oid)
            else:
                s.skip.add(oid)
        if s.live_control():
            self._push_skip(s)
        self.stateChanged.emit()

    @Slot(str, str, bool)
    def setObjectOn(self, side: str, objid: str, on: bool) -> None:
        self.setObjectsOn(side, [objid], on)

    def _push_skip(self, s: _Slot) -> None:
        ids = [int(i) for i in s.skip if str(i).lstrip("-").isdigit()]
        try:
            reply = api_client.set_skip(sorted(ids), sock=s.sock_path())
        except Exception:
            reply = None
        if not (reply and reply.get("ok")):
            self._schedule_relaunch(s)

    @Slot(result=bool)
    def isHolding(self) -> bool:
        """True while any exhibit is alive; the deck holds its bench state until the last exits."""
        return self.anyAlive()

    @Slot(result=str)
    def activeTargetWid(self) -> str:
        """The scene the deck names while exhibits run: the first alive side's."""
        for side in SIDES:
            s = self.slots[side]
            if s.alive() and s.scene:
                return s.scene.split(":", 1)[1] if s.scene.startswith("probe:") else s.scene
        return ""

    @staticmethod
    def _hyprctl(args: list[str]) -> str:
        try:
            r = subprocess.run(["hyprctl", *args], capture_output=True, text=True,
                               timeout=2, check=False)
            return r.stdout if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    def _hypr_dispatch(self, expr: str) -> bool:
        """One Lua-form dispatcher; the classic flat syntax is a Lua error and does nothing."""
        return self._hyprctl(["dispatch", expr]).strip() == "ok"

    def _hyprctl_clients(self) -> list:
        out = self._hyprctl(["-j", "clients"])
        if not out:
            return []
        try:
            data = json.loads(out)
            return data if isinstance(data, list) else []
        except ValueError:
            return []

    def _place_tick(self) -> None:
        """Windowed run: spawn B once A's window maps (10 s fallback), then place each exhibit
        by pid as it maps, verifying the placement stuck. A side stops being corrected once
        verified or after 5 attempts; from then on its position is the user's."""
        if self._mode != "window" or not self.anyAlive():
            self._place_timer.stop()
            self._b_pending = False
            self._in_flight = False
            return
        a, b = self.slots["A"], self.slots["B"]
        clients = self._hyprctl_clients()
        by_pid = {c.get("pid"): c for c in clients}
        pid_a = int(a.proc.processId()) if a.proc is not None else 0
        if self._b_pending and not b.alive() and (
                by_pid.get(pid_a) is not None or time.monotonic() > self._b_due):
            self._b_pending = False
            geo = self._window_geometry("B") or "0x0x1280x720"
            self._spawn(b, geo)
            self._in_flight = False
            self.stateChanged.emit()
        if not clients:
            return
        lay = self._layout()
        if not lay:
            return
        for s in (a, b):
            if not s.alive() or s.placed:
                continue
            win = by_pid.get(int(s.proc.processId()))
            if not win:
                continue
            cell = self._quadrant(s.side, lay)
            if placement.placed(win, cell):
                s.placed = True
                continue
            if s.place_tries >= placement.PLACE_TRIES:
                s.placed = True
                continue
            s.place_tries += 1
            placement.place(win, cell, self._hypr_dispatch)
        if not self._b_pending and all(sl.placed or not sl.alive() for sl in (a, b)):
            self._place_timer.stop()

    def shutdown(self) -> None:
        """App quit: stop every exhibit so none outlives the app, hand the outputs back."""
        self.stop()
        self._stop_journal()
