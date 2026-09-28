#include "DebugSwitches.h"

#include "WallpaperEngine/Application/FlagValues.h"

#include <algorithm>
#include <stdexcept>

namespace {
using WallpaperEngine::Application::DebugSwitches::Row;
using WallpaperEngine::Application::DebugSwitches::Rule;
using WallpaperEngine::Application::FlagValues::plainNumber;

std::optional<unsigned long long> whole (const std::string& text) {
    if (text.empty ()) {
	return std::nullopt;
    }

    constexpr unsigned long long most = std::numeric_limits<unsigned long long>::max ();
    unsigned long long value = 0;

    for (const char c : text) {
	if (c < '0' || c > '9') {
	    return std::nullopt;
	}

	const unsigned long long digit = c - '0';
	value = value > (most - digit) / 10 ? most : value * 10 + digit;
    }

    return value;
}

bool isWhole (const std::string& text, const unsigned long long lo, const unsigned long long hi) {
    const auto value = whole (text);

    return value.has_value () && *value >= lo && *value <= hi;
}

bool fraction (const std::string& text) {
    const auto value = plainNumber (text);

    return value.has_value () && *value >= 0.0 && *value <= 1.0;
}

bool nameCharacter (const char c) {
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_';
}

std::vector<std::string> split (const std::string& text, const char separator) {
    std::vector<std::string> parts;
    std::string::size_type start = 0;

    for (auto end = text.find (separator); end != std::string::npos; end = text.find (separator, start)) {
	parts.push_back (text.substr (start, end - start));
	start = end + 1;
    }

    parts.push_back (text.substr (start));
    return parts;
}

std::optional<std::string> ruleValue (const Row& row, const std::string& value) {
    switch (row.rule) {
	case Rule::Words:
	    return std::nullopt;
	case Rule::Whole:
	    return isWhole (value, row.lo, row.hi) ? std::optional<std::string> (value) : std::nullopt;
	case Rule::Text:
	    return value;
	case Rule::Size:
	    {
		const auto sides = split (value, 'x');

		if (sides.size () != 2 || !isWhole (sides[0], 1, 2147483647) || !isWhole (sides[1], 1, 2147483647)) {
		    return std::nullopt;
		}

		return value;
	    }
	case Rule::Position:
	    {
		const auto axes = split (value, ',');

		if (axes.size () != 2 || !fraction (axes[0]) || !fraction (axes[1])) {
		    return std::nullopt;
		}

		return value;
	    }
	case Rule::Rectangle:
	    {
		if (value.starts_with (' ') || value.ends_with (' ')) {
		    return std::nullopt;
		}

		auto numbers = split (value, ' ');
		std::erase (numbers, std::string ());

		if (numbers.size () < 4 || numbers.size () > 5) {
		    return std::nullopt;
		}

		for (std::size_t i = 0; i < 4; i++) {
		    if (!isWhole (numbers[i], 0, 2147483647)) {
			return std::nullopt;
		    }
		}

		if (numbers.size () == 5 && !isWhole (numbers[4], 0, 2147483447)) {
		    return std::nullopt;
		}

		if (*whole (numbers[2]) <= *whole (numbers[0]) || *whole (numbers[3]) <= *whole (numbers[1])) {
		    return std::nullopt;
		}

		return value;
	    }
	case Rule::Combo:
	    {
		const auto equals = value.find ('=');

		if (equals == 0 || equals == std::string::npos || (value.front () >= '0' && value.front () <= '9')
		    || (equals >= 3 && (value[0] == 'G' || value[0] == 'g') && (value[1] == 'L' || value[1] == 'l')
			&& value[2] == '_')
		    || !std::all_of (value.begin (), value.begin () + equals, nameCharacter)
		    || !isWhole (value.substr (equals + 1), 0, 2147483647)) {
		    return std::nullopt;
		}

		return value;
	    }
	case Rule::CrashLimit:
	    {
		const auto numbers = split (value, ',');

		if (numbers.size () != 3 || !isWhole (numbers[0], 1, 2147483647) || !isWhole (numbers[1], 0, 2147483647)
		    || !isWhole (numbers[2], 0, 2147483647)) {
		    return std::nullopt;
		}

		return value;
	    }
	case Rule::Milliseconds:
	    {
		if (value.ends_with ("ms")) {
		    const std::string count = value.substr (0, value.size () - 2);
		    return isWhole (count, 0, 9223372036854775807) ? std::optional<std::string> (count) : std::nullopt;
		}

		if (value.ends_with ('s')) {
		    const std::string count = value.substr (0, value.size () - 1);
		    return isWhole (count, 0, 9223372036854775807 / 1000) ? std::optional<std::string> (count + "000")
									  : std::nullopt;
		}

		return isWhole (value, 0, 9223372036854775807) ? std::optional<std::string> (value) : std::nullopt;
	    }
    }

    return std::nullopt;
}
} // namespace

namespace WallpaperEngine::Application::DebugSwitches {
const std::vector<Row>& table () {
    static const std::vector<Row> rows = {
	{ "animationtiming",
	  "LWE_ANIMFRACTION",
	  "current | legacy",
	  { { "current", std::nullopt }, { "legacy", "0" } } },
	{ "animationstats", "LWE_ANIMSTATS", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "audiostats", "LWE_AUDIOSTATS", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "audit", "LWE_AUDIT", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "texturedetailtest", "LWE_BASELEVEL_PROBE", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "shorttrailrenderer", "LWE_BILLBOARD", "normal | sprite", { { "normal", std::nullopt }, { "sprite", "1" } } },
	{ "cameraprobe", "LWE_CAMPROBE", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "webdebug",
	  "LWE_CEFDEBUG",
	  "a port from 1 to 65535 | off",
	  { { "off", std::nullopt } },
	  Rule::Whole,
	  1,
	  65535 },
	{ "weblog",
	  "LWE_CEFLOG",
	  "verbose | info | warning | error",
	  { { "verbose", "verbose" }, { "info", "info" }, { "warning", std::nullopt }, { "error", "error" } } },
	{ "testresolution",
	  "LWE_CLAMPOUTPUT",
	  "WIDTHxHEIGHT, each a whole number from 1 to 2147483647 | default",
	  { { "default", std::nullopt } },
	  Rule::Size },
	{ "backgroundprobe", "LWE_CLEARPROBE", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "cropoffset",
	  "LWE_CROPOFF",
	  "off | normal | reverse",
	  { { "off", std::nullopt }, { "normal", "1" }, { "reverse", "2" } } },
	{ "cursorscripts", "LWE_CURSORDBG", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "surfacesizes", "LWE_EGLDEBUG", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "sceneimage", "LWE_FBDUMP", "a path | off", { { "off", std::nullopt } }, Rule::Text },
	{ "sceneimageframe",
	  "LWE_FBDUMP_FRAME",
	  "a whole number from 4 to 2147483647",
	  {},
	  Rule::Whole,
	  4,
	  2147483647 },
	{ "bufferallocations", "LWE_FBOALLOC", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "buffercoverage", "LWE_FBOCOVERAGE", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "buffersharing", "LWE_FBOPOOL", "on | off", { { "on", std::nullopt }, { "off", "0" } } },
	{ "bufferaliases", "LWE_FBOTRACE", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "brightnessprofile", "LWE_FBPROFILE", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "shaderoption",
	  "LWE_FORCECOMBO",
	  "NAME=number: NAME starts with a letter or underscore, holds letters, digits and underscores, and does not "
	  "start with GL_ in any letter case; number a whole number from 0 to 2147483647",
	  {},
	  Rule::Combo },
	{ "frontface",
	  "LWE_FRONTFACE",
	  "clockwise | counterclockwise",
	  { { "clockwise", std::nullopt }, { "counterclockwise", "ccw" } } },
	{ "hideparenttrails", "LWE_HIDESTPARENT", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "imagedetails", "LWE_IMGDUMP", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "imagegeometry", "LWE_IMGPROBE", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "hidelight",
	  "LWE_KILLLIGHT",
	  "an id, a whole number from 0 to 2147483647 | off",
	  { { "off", std::nullopt } },
	  Rule::Whole,
	  0,
	  2147483647 },
	{ "pixelchanges", "LWE_LEDGER", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "scenedetails", "LWE_LIGHTDUMP", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "maskaudit", "LWE_MASKAUDIT", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "texturedetailaudit", "LWE_MIPRESIDENCY_DEBUG", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "mouseevents", "LWE_MOUSEDBG", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "mouseposition",
	  "LWE_MOUSE_POS",
	  "x,y, two decimals from 0 to 1 | off",
	  { { "off", std::nullopt } },
	  Rule::Position },
	{ "videobuffer",
	  "LWE_MPV_DEMUX_MB",
	  "a whole number of MiB from 1 to 2147483647",
	  {},
	  Rule::Whole,
	  1,
	  2147483647 },
	{ "videoextraframes", "LWE_MPV_EXTRA_FRAMES", "a whole number from 0 to 256", {}, Rule::Whole, 0, 256 },
	{ "videoframesync",
	  "LWE_MPV_REALSYNC",
	  "fallback | native",
	  { { "fallback", std::nullopt }, { "native", "1" } } },
	{ "videothreads", "LWE_MPV_THREADS", "a whole number from 0 to 2147483647", {}, Rule::Whole, 0, 2147483647 },
	{ "bloom", "LWE_NOBLOOM", "on | off", { { "on", std::nullopt }, { "off", "1" } } },
	{ "childanchoring", "LWE_NOCHILDRIDE", "authored | fixed", { { "authored", std::nullopt }, { "fixed", "1" } } },
	{ "buffergrowth", "LWE_NOFBOCOVERAGE", "on | off", { { "on", std::nullopt }, { "off", "1" } } },
	{ "childfade", "LWE_NOFOLLOWALPHA", "on | off", { { "on", std::nullopt }, { "off", "1" } } },
	{ "objectvolume", "LWE_NOOBJVOL", "on | off", { { "on", std::nullopt }, { "off", "1" } } },
	{ "parallelparticles", "LWE_NOPARSIM", "on | off", { { "on", std::nullopt }, { "off", "1" } } },
	{ "particlewarmup", "LWE_NOPREWARM", "on | off", { { "on", std::nullopt }, { "off", "1" } } },
	{ "ropetextureflip", "LWE_NOROPEUVFLIP", "on | off", { { "on", std::nullopt }, { "off", "1" } } },
	{ "shaderscreensize", "LWE_NOSCREEN", "on | off", { { "on", std::nullopt }, { "off", "1" } } },
	{ "spritetextureflip", "LWE_NOSPRITEVFLIP", "on | off", { { "on", std::nullopt }, { "off", "1" } } },
	{ "objectpixels",
	  "LWE_OBJPROBE",
	  "x0 y0 x1 y1 [skip], separated by spaces: x0, y0, x1 and y1 whole numbers from 0 to 2147483647 with x1 above "
	  "x0 and y1 above y0; skip a whole number from 0 to 2147483447",
	  {},
	  Rule::Rectangle },
	{ "objectpixelprecision",
	  "LWE_OBJPROBE_FLOAT",
	  "byte | float",
	  { { "byte", std::nullopt }, { "float", "1" } } },
	{ "overlayfont", "LWE_OVERLAY_FONT", "a path | default", { { "default", std::nullopt } }, Rule::Text },
	{ "overlaysize", "LWE_OVERLAY_SIZE", "a whole number of pixels from 8 to 200", {}, Rule::Whole, 8, 200 },
	{ "overlaytext", "LWE_OVERLAY_TEXT", "any text", {}, Rule::Text },
	{ "particleallocations", "LWE_PARTALLOC", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "particlestats", "LWE_PARTSTATS", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "passprobe",
	  "LWE_PASSPROBE",
	  "an object id, a whole number from 0 to 2147483647 | final",
	  { { "final", "final" } },
	  Rule::Whole,
	  0,
	  2147483647 },
	{ "passimage", "LWE_PASSPROBE_DUMP", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "buffersharingstats", "LWE_POOL_HWM", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "screentrace", "LWE_PRESENTTRACE", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "ropetrailprobe", "LWE_ROPETRAILPROBE", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "scriptregistration", "LWE_SCRIPTDBG", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "shaderdump", "LWE_SHADERDUMP", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "shadersource", "LWE_SHADERDUMP_MATCH", "text | off", { { "off", std::nullopt } }, Rule::Text },
	{ "shapes", "LWE_SHAPES", "on | off", { { "on", std::nullopt }, { "off", "0" } } },
	{ "particlesizeprobe", "LWE_SIZEPROBE", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "skippedobjects", "LWE_SKIPGATE", "omit | hide", { { "omit", std::nullopt }, { "hide", "0" } } },
	{ "modelshine", "LWE_SPECFIX", "authored | off", { { "authored", std::nullopt }, { "off", "1" } } },
	{ "albedocolor", "LWE_SRGBALBEDO", "authored | srgb", { { "authored", std::nullopt }, { "srgb", "1" } } },
	{ "texturecolor", "LWE_SRGBALL", "authored | srgb", { { "authored", std::nullopt }, { "srgb", "1" } } },
	{ "texturecachestats", "LWE_TEXCACHEDUMP", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "testtexturelimit",
	  "LWE_TEXCAP",
	  "a size in pixels, a whole number from 256 to 2147483647 | default",
	  { { "default", std::nullopt } },
	  Rule::Whole,
	  256,
	  2147483647 },
	{ "timestats", "LWE_TIMESTATS", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "modeltint",
	  "LWE_TINTFIX",
	  "authored | unmasked | blue",
	  { { "authored", std::nullopt }, { "unmasked", "1" }, { "blue", "2" } } },
	{ "trailtiming", "LWE_TRAILMODE", "spread | exact", { { "spread", std::nullopt }, { "exact", "exact" } } },
	{ "twinkleprobe", "LWE_TWINKLEPROBE", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "shaderinputs", "LWE_UNIFDUMP", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "shaderinputvalues", "LWE_UNIFVALS", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "particlevelocity", "LWE_VELPROBE", "on | off", { { "on", "1" }, { "off", std::nullopt } } },
	{ "webcrashlimit",
	  "LWE_WEB_CRASHGUARD",
	  "count,window,cooldown: count a whole number from 1 to 2147483647; window and cooldown whole numbers of "
	  "milliseconds from 0 to 2147483647",
	  {},
	  Rule::CrashLimit },
	{ "webidletime",
	  "LWE_WEB_IDLE_EXIT_MS",
	  "a whole number of milliseconds from 0 to 9223372036854775807, or a whole number followed by ms or s whose "
	  "milliseconds fit that range",
	  {},
	  Rule::Milliseconds },
	{ "webhelpersocket", "LWE_WEB_SOCKET", "a path | default", { { "default", std::nullopt } }, Rule::Text },
	{ "windowtitle", "LWE_WINTITLE", "any text", {}, Rule::Text },
    };

    return rows;
}

Action resolve (const std::string& token) {
    const auto equals = token.find ('=');

    if (equals == std::string::npos || equals == 0 || equals + 1 == token.size ()) {
	throw std::runtime_error ("--debug takes <switch>=<value>; got " + token);
    }

    const std::string name = token.substr (0, equals);
    const std::string value = token.substr (equals + 1);
    const auto& rows = table ();
    const auto row = std::ranges::find (rows, name, &Row::name);

    if (row == rows.end ()) {
	throw std::runtime_error ("unknown debugging switch " + name + "; --help-debug lists them");
    }

    for (const auto& choice : row->words) {
	if (choice.word == value) {
	    return { row->variable, choice.setTo };
	}
    }

    if (const auto setTo = ruleValue (*row, value); setTo.has_value ()) {
	return { row->variable, setTo };
    }

    throw std::runtime_error (name + " takes " + row->accepts + "; got " + value);
}

const char* helpText () {
    return R"(Unsupported debugging switches. They can change or disappear in any release.
Set one at launch with --debug <switch>=<value>; repeat it for more.
Each name is an alias; the engine's own variable still works.

  animationtiming <current|legacy>          legacy brings back the older timing for looping
                                            sprite-sheet animation in particles.
  animationstats <on|off>                   Logs frame-advance statistics for animated textures.
  audiostats <on|off>                       Logs the average sound levels handed to wallpaper
                                            scripts, every 30 script ticks.
  audit <on|off>                            Logs one-time audits: which file each texture name
                                            resolves to, pixel statistics of large compressed
                                            textures, material mask switches, and 3D model drawing
                                            state.
  texturedetailtest <on|off>                Runs a one-time test of how the graphics driver handles
                                            texture detail levels and logs pass or fail.
  shorttrailrenderer <normal|sprite>        Draws very short rope-trail particles (length under
                                            0.35) with the simpler sprite-trail renderer.
  cameraprobe <on|off>                      Logs camera position and view-matrix details, including
                                            camera moves made by scripts.
  webdebug <a port|off>                     Opens the web engine's remote debugging (devtools) port
                                            for web wallpapers. (web helper)
  weblog <verbose|info|warning|error>       How much the web engine writes to its log file (logs/cef
                                            under the lwe state folder). (web helper)
  testresolution <WIDTHxHEIGHT|default>     Pretends the largest screen is this size when working
                                            out the ssfactor and clampcomposites caps, a test
                                            override.
  backgroundprobe <on|off>                  Logs the scene's background clear color when the scene
                                            is built and for its first three frames.
  cropoffset <off|normal|reverse>           Shifts image layers (except puppet-animated ones) by
                                            their authored crop offset, a layout experiment.
  cursorscripts <on|off>                    Logs which scripts listen for cursor events and the
                                            clickable areas of image layers.
  surfacesizes <on|off>                     Logs drawing-surface sizes against screen sizes for the
                                            first four frames (Wayland).
  sceneimage <a path|off>                   Saves the drawn scene as a PPM image at frame 3 and
                                            again at the frame set by sceneimageframe.
  sceneimageframe <a whole number, 4 or more>
                                            Which frame sceneimage saves its second image at.
  bufferallocations <on|off>                Logs every offscreen buffer the engine allocates, with a
                                            running total.
  buffercoverage <on|off>                   Logs each time an image layer's working buffer is
                                            enlarged to match its size on screen.
  buffersharing <on|off>                    off gives every layer its own working buffers instead of
                                            sharing same-size ones, which uses more video memory.
  bufferaliases <on|off>                    Logs each time an offscreen buffer is set up as another
                                            name for an existing one.
  brightnessprofile <on|off>                Logs a coarse brightness profile of the scene at frame
                                            150 and of the picture sent to the screen every 450
                                            frames.
  shaderoption <NAME=number>                Forces one shader option (combo) to this value in every
                                            shader, a diagnostic.
  frontface <clockwise|counterclockwise>    counterclockwise flips which side of 3D model triangles
                                            counts as the front.
  hideparenttrails <on|off>                 Hides sprite-trail particle systems that have child
                                            systems, leaving only the children.
  imagedetails <on|off>                     Logs image layer placement, textures, render passes and
                                            matrices.
  imagegeometry <on|off>                    Logs image layer position, shape and vertex-buffer
                                            details.
  hidelight <an id|off>                     Leaves the light with this id out of scene lighting, a
                                            diagnostic.
  pixelchanges <on|off>                     Logs, for the first two frames, how much the sampled
                                            pixel values under each drawn object change.
  scenedetails <on|off>                     Logs a bundle of lighting, text, script-property,
                                            animation-timeline and shader-setting details; most
                                            appear once, while one animation trace repeats as the
                                            scene runs.
  maskaudit <on|off>                        Logs value statistics of one- and two-channel mask
                                            textures as they load.
  texturedetailaudit <on|off>               Logs, for each texture, whether the engine may load it
                                            at reduced detail.
  mouseevents <on|off>                      Logs pointer enter and button events (Wayland) and the
                                            mouse movement sent to web wallpapers.
  mouseposition <x,y|off>                   Pins the mouse position scene wallpapers see to this
                                            spot, as fractions of the screen width and height; web
                                            wallpapers still get the real pointer.
  videobuffer <a size in MiB>               How much video mpv may read ahead for video wallpapers.
  videoextraframes <0 to 256>               How many extra frames mpv's hardware decoder keeps
                                            queued (only matters with hardware decoding).
  videoframesync <fallback|native>          Gives mpv the graphics driver's real frame-sync
                                            functions and turns on its direct rendering, instead of
                                            the engine's stand-ins, which exist because mpv never
                                            frees the real ones.
  videothreads <a number>                   How many threads mpv uses to decode video wallpapers.
  bloom <on|off>                            off turns off the scene bloom (glow) effect.
  childanchoring <authored|fixed>           Treats every child particle system as fixed in place
                                            when correcting particle sizes for layer scaling, a
                                            diagnostic.
  buffergrowth <on|off>                     off stops image layer working buffers from being
                                            enlarged to their on-screen size, so they keep the
                                            authored size.
  childfade <on|off>                        off stops child particles from fading along with their
                                            parent particle.
  objectvolume <on|off>                     off plays every sound object at full volume, ignoring
                                            the volume the wallpaper's author set.
  parallelparticles <on|off>                off runs all particle simulation on the main thread
                                            instead of spreading it over worker threads.
  particlewarmup <on|off>                   off turns off the 60-second warm-up simulation that
                                            particle systems with a start time get.
  ropetextureflip <on|off>                  off turns off the engine's vertical texture flip fix for
                                            rope particle shaders.
  shaderscreensize <on|off>                 off stops giving shaders the scene's size and aspect
                                            ratio, a diagnostic.
  spritetextureflip <on|off>                off turns off the engine's vertical flip for sprite
                                            particles, so up points the other way.
  objectpixels <x0 y0 x1 y1 [skip], in quotes>
                                            Reads the pixels in this rectangle after each object is
                                            drawn and logs their average, for 199 draws after the
                                            skip.
  objectpixelprecision <byte|float>         Makes objectpixels read full-precision values instead of
                                            8-bit ones.
  overlayfont <a path|default>              Font file for the corner text overlay, tried before the
                                            built-in list.
  overlaysize <a size in pixels>            Heading text size of the overlay at a screen height of
                                            2160 pixels; later lines draw at two thirds of it.
  overlaytext <text>                        Starting text for the corner text overlay, unless a
                                            set-overlay command has already set one.
  particleallocations <on|off>              Logs how much memory each particle system's buffers
                                            take.
  particlestats <on|off>                    Logs statistics for each particle system every 5
                                            seconds: frame rate, spawn and death rates, peak count
                                            and average speed.
  passprobe <an object id|final>            Logs pixel statistics after each render pass of the
                                            object with this id (or of the final output passes) and
                                            its texture bindings.
  passimage <on|off>                        Also saves one probed pass as passprobe-post.ppm in the
                                            lwe probes folder.
  buffersharingstats <on|off>               Logs a report of how well layers share working buffers.
  screentrace <on|off>                      Logs screen size, scene size, scaling mode and picture
                                            window for the first frames sent to each screen.
  ropetrailprobe <on|off>                   Logs rope-trail particle geometry: strip and point
                                            counts, longest segment and head position.
  scriptregistration <on|off>               Logs which wallpaper scripts are registered or skipped.
  shaderdump <on|off>                       When a fragment shader fails to compile, logs its full
                                            source with line numbers.
  shadersource <text|off>                   Saves the final source of every shader whose file name
                                            contains this text to the lwe probes folder.
  shapes <on|off>                           off leaves shape objects out of scenes.
  particlesizeprobe <on|off>                Reads back and logs the particle size-correction values
                                            shaders receive.
  skippedobjects <omit|hide>                hide still builds objects on the skip list (from
                                            --render-debug skip-object or a show's skip_objects) and
                                            only hides them when drawing; omit, the default, leaves
                                            them out of the scene entirely.
  modelshine <authored|off>                 off makes 3D models fully rough (no shine), a lighting
                                            experiment.
  albedocolor <authored|srgb>               Treats DXT5-compressed textures as sRGB and
                                            gamma-corrects the final picture, a color experiment.
  texturecolor <authored|srgb>              Treats all color textures as sRGB, gamma-corrects the
                                            final picture and skips the compressed texture cache, a
                                            color experiment.
  texturecachestats <on|off>                Logs which textures survive a cache cleanup and when
                                            wallpapers are created and destroyed.
  testtexturelimit <a size in pixels|default>
                                            Overrides the largest texture size the engine loads when
                                            reducing texture detail, a test override.
  timestats <on|off>                        Logs every 5 seconds how the wallpaper clock compares
                                            with real time.
  modeltint <authored|unmasked|blue>        unmasked turns off a tint-mask option on 3D model
                                            materials and blue paints models a fixed blue, both
                                            lighting experiments.
  trailtiming <spread|exact>                exact places particle trail points at fixed time steps
                                            from each particle's birth instead of spreading them
                                            along the trail.
  twinkleprobe <on|off>                     Logs the age and fade values of the first particle in
                                            each particle system every 30 frames.
  shaderinputs <on|off>                     Logs each shader setting the engine fills in
                                            automatically and where its value came from.
  shaderinputvalues <on|off>                Logs shader setting values and how layer color and
                                            brightness are applied.
  particlevelocity <on|off>                 Logs the starting velocity each random-velocity
                                            initializer gives a new particle.
  webcrashlimit <count,window,cooldown>     If the web helper dies this many times within the
                                            window, the engine waits out the cooldown before
                                            starting it again, and web wallpapers stay dark
                                            meanwhile.
  webidletime <a duration, for example 1000ms>
                                            How long the web helper waits after its last web
                                            wallpaper closes before it exits. (web helper)
  webhelpersocket <a path|default>          Where the socket between the engine and its web helper
                                            lives.
  windowtitle <text>                        Window title when running with --window.
)";
}
} // namespace WallpaperEngine::Application::DebugSwitches
