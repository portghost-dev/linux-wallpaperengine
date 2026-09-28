#include "ApplicationContext.h"

#include "Steam/FileSystem/FileSystem.h"
#include "WallpaperEngine/Application/Config.h"
#include "WallpaperEngine/Application/DebugSwitches.h"
#include "WallpaperEngine/Application/FlagValues.h"
#include "WallpaperEngine/Data/JSON.h"
#include "WallpaperEngine/Logging/Log.h"

#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string_view>

#include <argparse/argparse.hpp>

#define WORKSHOP_APP_ID 431960
#define APP_DIRECTORY "wallpaper_engine"

using namespace WallpaperEngine::Application;
using WallpaperEngine::Data::JSON::JSON;

std::filesystem::path ApplicationContext::resolvePlaylistItemPath (const std::string& raw) const {
    if (raw.empty ()) {
	return {};
    }

    std::string cleaned = raw;

    constexpr std::string_view windowsPrefix = "\\\\?\\";

    if (cleaned.rfind (windowsPrefix, 0) == 0) {
	cleaned = cleaned.substr (windowsPrefix.length ());
    }

    std::replace (cleaned.begin (), cleaned.end (), '\\', '/');

    if (cleaned.size () > 1 && cleaned[1] == ':') {
	cleaned = cleaned.substr (2);
    }

    if (!cleaned.empty () && cleaned.front () != '/') {
	cleaned.insert (cleaned.begin (), '/');
    }

    std::filesystem::path path = std::filesystem::path (cleaned).lexically_normal ();

    if (std::filesystem::is_regular_file (path)) {
	path = path.parent_path ();
    }

    return path;
}

std::filesystem::path ApplicationContext::configFilePath () const {
    try {
	return Steam::FileSystem::appDirectory (APP_DIRECTORY, "") / "config.json";
    } catch (std::runtime_error&) {
	sLog.exception ("Cannot locate wallpaper engine installation to read config.json");
	return {};
    }
}

std::optional<JSON> ApplicationContext::parseConfigJson (const std::filesystem::path& path) const {
    if (path.empty ()) {
	return std::nullopt;
    }

    std::ifstream configFile (path);

    if (!configFile.is_open ()) {
	sLog.exception ("Cannot open wallpaper engine config file at ", path);
	return std::nullopt;
    }

    try {
	std::ostringstream contents;
	contents << configFile.rdbuf ();
	return WallpaperEngine::Data::JSON::parseLenient (contents.str ());
    } catch (const std::exception& e) {
	sLog.error ("Failed parsing wallpaper engine config.json: ", e.what ());
	return std::nullopt;
    }
}

std::optional<ApplicationContext::PlaylistDefinition>
ApplicationContext::buildPlaylistDefinition (const JSON& playlistJson, const std::string& fallbackName) const {
    PlaylistDefinition definition;
    definition.name = playlistJson.optional<std::string> ("name", fallbackName);
    definition.settings = this->parsePlaylistSettings (playlistJson);
    definition.items
	= this->collectPlaylistItems (playlistJson, definition.name.empty () ? fallbackName : definition.name);

    if (definition.items.empty ()) {
	return std::nullopt;
    }

    if (definition.name.empty ()) {
	if (fallbackName.empty ()) {
	    sLog.error ("Skipping playlist with no name");
	    return std::nullopt;
	}

	definition.name = fallbackName;
    }

    return definition;
}

ApplicationContext::PlaylistSettings ApplicationContext::parsePlaylistSettings (const JSON& playlistJson) const {
    PlaylistSettings settings;
    const auto settingsJson = playlistJson.optional ("settings");
    settings.delayMinutes = settingsJson ? settingsJson->optional<uint32_t> ("delay", 60) : 60;
    settings.mode = settingsJson ? settingsJson->optional<std::string> ("mode", "timer") : "timer";
    settings.order = settingsJson ? settingsJson->optional<std::string> ("order", "sequential") : "sequential";
    settings.updateOnPause = settingsJson ? settingsJson->optional<bool> ("updateonpause", false) : false;
    settings.videoSequence = settingsJson ? settingsJson->optional<bool> ("videosequence", false) : false;
    return settings;
}

std::vector<std::filesystem::path>
ApplicationContext::collectPlaylistItems (const JSON& playlistJson, const std::string& name) const {
    std::vector<std::filesystem::path> items;
    const auto jsonItems = playlistJson.optional ("items");

    if (!jsonItems.has_value () || !jsonItems->is_array ()) {
	sLog.error ("Skipping playlist ", name, ": missing items");
	return items;
    }

    for (const auto& rawItem : *jsonItems) {
	if (!rawItem.is_string ()) {
	    continue;
	}

	auto resolvedPath = this->resolvePlaylistItemPath (rawItem.get<std::string> ());

	if (resolvedPath.empty ()) {
	    continue;
	}

	if (!std::filesystem::exists (resolvedPath)) {
	    sLog.error ("Skipping playlist item not found: ", resolvedPath.string ());
	    continue;
	}

	items.push_back (resolvedPath);
    }

    if (items.empty ()) {
	sLog.error ("Skipping playlist ", name, ": no usable items found");
    }

    return items;
}

void ApplicationContext::registerPlaylist (PlaylistDefinition&& definition) {
    this->m_configPlaylists.insert_or_assign (definition.name, std::move (definition));
}

void ApplicationContext::loadPlaylistsFromConfig () {
    if (this->m_loadedConfigPlaylists) {
	return;
    }

    this->m_loadedConfigPlaylists = true;

    const auto configPath = this->configFilePath ();
    const auto root = this->parseConfigJson (configPath);
    if (!root.has_value ()) {
	return;
    }

    const auto steamUser = root->optional ("steamuser");

    if (!steamUser.has_value ()) {
	sLog.exception ("Cannot find steamuser section in config.json");
    }

    auto addPlaylist = [this] (const JSON& playlistJson, const std::string& fallbackName) {
	auto definition = this->buildPlaylistDefinition (playlistJson, fallbackName);
	if (!definition) {
	    return;
	}
	this->registerPlaylist (std::move (*definition));
    };

    if (const auto general = steamUser->optional ("general")) {
	if (const auto playlists = general->optional ("playlists")) {
	    for (const auto& playlist : *playlists) {
		try {
		    addPlaylist (playlist, playlist.optional<std::string> ("name", ""));
		} catch (const std::exception& e) {
		    sLog.error ("Failed parsing playlist: ", e.what ());
		}
	    }
	}
    }

    if (const auto wallpaperConfig = steamUser->optional ("wallpaperconfig")) {
	if (const auto selected = wallpaperConfig->optional ("selectedwallpapers")) {
	    for (const auto& entry : selected->items ()) {
		const auto playlist = entry.value ().optional ("playlist");

		if (!playlist.has_value ()) {
		    continue;
		}

		try {
		    addPlaylist (*playlist, entry.key ());
		} catch (const std::exception& e) {
		    sLog.error ("Failed parsing playlist for ", entry.key (), ": ", e.what ());
		}
	    }
	}
    }
}

const ApplicationContext::PlaylistDefinition& ApplicationContext::getPlaylistFromConfig (const std::string& name) {
    if (!this->m_loadedConfigPlaylists) {
	this->loadPlaylistsFromConfig ();
    }

    const auto cur = this->m_configPlaylists.find (name);

    if (cur == this->m_configPlaylists.end ()) {
	std::string available;

	for (auto it = this->m_configPlaylists.begin (); it != this->m_configPlaylists.end (); ++it) {
	    available += it->first;
	    if (std::next (it) != this->m_configPlaylists.end ()) {
		available += ", ";
	    }
	}

	const std::string availableText = available.empty () ? "" : std::string (". Available: ") + available;

	sLog.exception ("Playlist not found in config.json: ", name, availableText);
	throw std::runtime_error ("Playlist not found: " + name);
    }

    return cur->second;
}

ApplicationContext::ApplicationContext (int argc, char* argv[]) : m_argc (argc), m_argv (argv) { }

void ApplicationContext::loadSettingsFromArgv () {
    std::string lastScreen;
    Config::Flags flags;
    std::optional<std::string> fullscreenWord;
    bool noFullscreenPause = false;

    argparse::ArgumentParser program ("linux-wallpaperengine", LWE_VERSION, argparse::default_arguments::help);

    program.add_argument ("--version")
	.help ("Prints the version and exits")
	.flag ()
	.action ([] (const std::string& value) -> void {
	    std::cout << LWE_VERSION << std::endl;
	    std::exit (0);
	});

    auto& backgroundGroup = program.add_group ("Background options");
    auto& backgroundMode = backgroundGroup.add_mutually_exclusive_group (false);

    backgroundGroup.add_argument ("background id")
	.help ("The background to use as default for screens with no background specified")
	.default_value ("")
	.action ([this] (const std::string& value) -> void {
	    if (!value.empty ()) {
		this->settings.general.defaultBackground = translateBackground (value);
	    }
	});

    backgroundMode.add_argument ("-w", "--window")
	.help ("Window geometry to use for the given screen")
	.action ([this] (const std::string& value) -> void {
	    if (this->settings.render.mode == DESKTOP_BACKGROUND) {
		sLog.exception ("Cannot run in both background and window mode");
	    }
	    if (this->settings.render.mode == EXPLICIT_WINDOW) {
		sLog.exception ("Only one window at a time can be specified in explicit window mode");
	    }

	    this->settings.render.mode = EXPLICIT_WINDOW;

	    if (value.empty ()) {
		sLog.exception ("Window geometry cannot be empty");
	    }

	    const char* str = value.c_str ();
	    const char* delim1 = strchr (str, 'x');
	    const char* delim2 = delim1 ? strchr (delim1 + 1, 'x') : nullptr;
	    const char* delim3 = delim2 ? strchr (delim2 + 1, 'x') : nullptr;

	    if (delim1 == nullptr || delim2 == nullptr || delim3 == nullptr) {
		sLog.exception ("Window geometry must be in the format: XxYxWxH");
	    }

	    this->settings.render.window.geometry.x = strtol (str, nullptr, 10);
	    this->settings.render.window.geometry.y = strtol (delim1 + 1, nullptr, 10);
	    this->settings.render.window.geometry.z = strtol (delim2 + 1, nullptr, 10);
	    this->settings.render.window.geometry.w = strtol (delim3 + 1, nullptr, 10);
	})
	.append ();
    backgroundMode.add_argument ("-r", "--screen-root", "--screen")
	.help ("The screen the following settings will have an effect on")
	.action ([this, &lastScreen] (const std::string& value) -> void {
	    if (this->settings.general.screenBackgrounds.find (value)
		!= this->settings.general.screenBackgrounds.end ()) {
		sLog.exception ("Cannot specify the same screen more than once: ", value);
	    }
	    for (const auto& group : this->settings.general.spanGroups) {
		if (std::find (group.screens.begin (), group.screens.end (), value) != group.screens.end ()) {
		    sLog.exception ("--screen-root: screen '", value, "' already belongs to a span group");
		}
	    }
	    if (this->settings.render.mode == EXPLICIT_WINDOW) {
		sLog.exception ("Cannot run in both background and window mode");
	    }

	    this->settings.render.mode = DESKTOP_BACKGROUND;
	    lastScreen = value;
	    this->settings.general.screenBackgrounds[lastScreen] = "";
	    this->settings.general.screenScalings[lastScreen] = this->settings.render.window.scalingMode;
	    this->settings.general.screenClamps[lastScreen] = this->settings.render.window.clamp;
	})
	.append ();
    backgroundGroup.add_argument ("--screen-span", "--span")
	.help ("Comma-separated list of screens to span a single wallpaper across")
	.action ([this, &lastScreen] (const std::string& value) -> void {
	    if (this->settings.render.mode == EXPLICIT_WINDOW) {
		sLog.exception ("Cannot run in both background and window mode");
	    }

	    this->settings.render.mode = DESKTOP_BACKGROUND;

	    SpanGroup group;
	    std::string screen;
	    std::istringstream ss (value);

	    while (std::getline (ss, screen, ',')) {
		if (screen.empty ()) {
		    continue;
		}
		if (this->settings.general.screenBackgrounds.find (screen)
		    != this->settings.general.screenBackgrounds.end ()) {
		    sLog.exception ("--screen-span: screen '", screen, "' is already configured individually");
		}
		// reject duplicates within this group
		if (std::find (group.screens.begin (), group.screens.end (), screen) != group.screens.end ()) {
		    sLog.exception ("--screen-span: duplicate screen name '", screen, "'");
		}
		// reject screens already claimed by another span group
		for (const auto& existing : this->settings.general.spanGroups) {
		    if (std::find (existing.screens.begin (), existing.screens.end (), screen)
			!= existing.screens.end ()) {
			sLog.exception ("--screen-span: screen '", screen, "' already belongs to another span group");
		    }
		}
		group.screens.push_back (screen);
	    }

	    if (group.screens.size () < 2) {
		sLog.exception ("--screen-span requires at least two comma-separated screen names");
	    }

	    group.scaling = this->settings.render.window.scalingMode;
	    group.clamp = this->settings.render.window.clamp;
	    this->settings.general.spanGroups.push_back (std::move (group));
	    // set lastScreen to a synthetic name so --bg/--scaling/--clamp can target this group
	    lastScreen = "span:" + value;
	    // register the synthetic name in screenBackgrounds so the rest of the pipeline sees it
	    this->settings.general.screenBackgrounds[lastScreen] = "";
	})
	.append ();
    backgroundGroup.add_argument ("-b", "--bg", "--wallpaper")
	.help ("After --screen-root or --screen-span, specifies the background to use")
	.action ([this, &lastScreen] (const std::string& value) -> void {
	    this->settings.general.screenBackgrounds[lastScreen] = translateBackground (value);
	    // set the default background to the last one used
	    this->settings.general.defaultBackground = translateBackground (value);
	    // if this targets a span group, update the group's background too
	    if (lastScreen.rfind ("span:", 0) == 0 && !this->settings.general.spanGroups.empty ()) {
		this->settings.general.spanGroups.back ().background = translateBackground (value);
	    }
	})
	.append ();
    backgroundGroup.add_argument ("--playlist", "--steamplaylist")
	.help (
	    "Uses a playlist from wallpaper engine's config.json. If used after --screen-root it is applied to that "
	    "screen, otherwise it is used in window mode."
	)
	.action ([this, &lastScreen] (const std::string& value) -> void {
	    const auto& playlist = this->getPlaylistFromConfig (value);

	    if (lastScreen.empty ()) {
		this->settings.general.defaultPlaylist = playlist;
		if (this->settings.general.defaultBackground.empty () && !playlist.items.empty ()) {
		    this->settings.general.defaultBackground = playlist.items.front ();
		}
	    } else {
		this->settings.general.screenPlaylists[lastScreen] = playlist;
		if (!playlist.items.empty ()) {
		    this->settings.general.screenBackgrounds[lastScreen] = playlist.items.front ();
		}

		if (this->settings.general.defaultBackground.empty () && !playlist.items.empty ()) {
		    this->settings.general.defaultBackground = playlist.items.front ();
		}
	    }
	})
	.append ();
    backgroundGroup.add_argument ("--scaling")
	.help (
	    "Scaling mode to use when rendering the background, this applies to the previous --window, --screen-root, "
	    "or --screen-span output, or the default background if no other background is specified"
	)
	.choices ("stretch", "fit", "fill", "default")
	.action ([this, &lastScreen] (const std::string& value) -> void {
	    WallpaperEngine::Render::WallpaperState::TextureUVsScaling mode;

	    if (value == "stretch") {
		mode = WallpaperEngine::Render::WallpaperState::TextureUVsScaling::StretchUVs;
	    } else if (value == "fit") {
		mode = WallpaperEngine::Render::WallpaperState::TextureUVsScaling::ZoomFitUVs;
	    } else if (value == "fill") {
		mode = WallpaperEngine::Render::WallpaperState::TextureUVsScaling::ZoomFillUVs;
	    } else if (value == "default") {
		mode = WallpaperEngine::Render::WallpaperState::TextureUVsScaling::DefaultUVs;
	    } else {
		sLog.exception ("Invalid scaling mode: ", value);
	    }

	    if (this->settings.render.mode == DESKTOP_BACKGROUND) {
		this->settings.general.screenScalings[lastScreen] = mode;
		// also update span group if targeting one
		if (lastScreen.rfind ("span:", 0) == 0 && !this->settings.general.spanGroups.empty ()) {
		    this->settings.general.spanGroups.back ().scaling = mode;
		}
	    } else {
		this->settings.render.window.scalingMode = mode;
	    }
	})
	.append ();
    backgroundGroup.add_argument ("--clamp", "--edge")
	.help (
	    "Clamp mode to use when rendering the background, this applies to the previous --window, --screen-root, "
	    "or --screen-span output, or the default background if no other background is specified"
	)
	.choices ("clamp", "border", "repeat", "extend", "blank", "tile")
	.action ([this, &lastScreen] (const std::string& value) -> void {
	    TextureFlags flags;

	    if (value == "clamp" || value == "extend") {
		flags = TextureFlags_ClampUVs;
	    } else if (value == "border" || value == "blank") {
		flags = TextureFlags_ClampUVsBorder;
	    } else if (value == "repeat" || value == "tile") {
		flags = TextureFlags_NoFlags;
	    } else {
		sLog.exception ("Invalid clamp mode: ", value);
	    }

	    if (this->settings.render.mode == DESKTOP_BACKGROUND) {
		this->settings.general.screenClamps[lastScreen] = flags;
		// also update span group if targeting one
		if (lastScreen.rfind ("span:", 0) == 0 && !this->settings.general.spanGroups.empty ()) {
		    this->settings.general.spanGroups.back ().clamp = flags;
		}
	    } else {
		this->settings.render.window.clamp = flags;
	    }
	})
	.append ();

    backgroundGroup.add_argument ("--layer")
	.help (
	    "Wayland-only: which wlr-layer-shell layer to anchor the wallpaper to "
	    "(background, bottom, top, overlay). Default: bottom. "
	    "Use 'background' on niri to pair with the `place-within-backdrop` layer-rule, "
	    "otherwise the wallpaper will be cloned to every workspace in the overview."
	)
	.choices ("background", "bottom", "top", "overlay")
	.default_value (std::string ("bottom"))
	.action ([this] (const std::string& value) -> void {
	    if (value == "background") {
		this->settings.render.wayland.layer = WAYLAND_LAYER_BACKGROUND;
	    } else if (value == "bottom") {
		this->settings.render.wayland.layer = WAYLAND_LAYER_BOTTOM;
	    } else if (value == "top") {
		this->settings.render.wayland.layer = WAYLAND_LAYER_TOP;
	    } else if (value == "overlay") {
		this->settings.render.wayland.layer = WAYLAND_LAYER_OVERLAY;
	    } else {
		sLog.exception ("Invalid wlr-layer-shell layer: ", value);
	    }
	});

    auto& performanceGroup = program.add_group ("Performance options");

    performanceGroup.add_argument ("-f", "--fps")
	.help ("Limits the FPS to the given number, useful to keep battery consumption low")
	.default_value (30)
	.store_into (this->settings.render.maximumFPS);

    performanceGroup.add_argument ("--no-fullscreen-pause")
	.help ("Prevents the background pausing when an app is fullscreen")
	.flag ()
	.action ([this, &noFullscreenPause] (const std::string& value) -> void {
	    this->settings.render.pauseOnFullscreen = false;
	    this->settings.render.fullscreenBehavior = FullscreenBehavior::Off;
	    noFullscreenPause = true;
	});

    performanceGroup.add_argument ("--fullscreen")
	.help ("While something is fullscreen: keep playing, pause, or stop and free the screens")
	.choices ("keep", "pause", "stop")
	.action ([&fullscreenWord] (const std::string& value) -> void { fullscreenWord = value; });

    performanceGroup.add_argument ("--fullscreen-pause-only-active", "--fullscreen-active-only")
	.help ("Wayland only: pause only when a fullscreen window is active (activated)")
	.flag ()
	.action ([this] (const std::string& value) -> void {
	    this->settings.render.pauseOnFullscreenOnlyWhenActive = true;
	});

    performanceGroup.add_argument ("--fullscreen-pause-ignore-appid", "--fullscreen-ignore")
	.help ("Wayland only: ignore fullscreen windows whose app_id contains this value (repeatable)")
	.action ([this] (const std::string& value) -> void {
	    if (!value.empty ()) {
		this->settings.render.fullscreenPauseIgnoreAppIds.push_back (value);
	    }
	})
	.append ();

    auto& audioGroup = program.add_group ("Sound settings");
    auto& audioSettingsGroup = audioGroup.add_mutually_exclusive_group (false);

    audioSettingsGroup.add_argument ("-v", "--volume")
	.help ("Volume for all the sounds in the background")
	.default_value (15)
	.store_into (this->settings.audio.volume);

    audioSettingsGroup.add_argument ("-s", "--silent", "--mute")
	.help ("Mutes all the sound the wallpaper might produce")
	.flag ()
	.action ([this] (const std::string& value) -> void { this->settings.audio.enabled = false; });

    audioGroup.add_argument ("--noautomute", "--no-automute")
	.help ("Disables the automute when an app is playing sound")
	.flag ()
	.action ([this] (const std::string& value) -> void { this->settings.audio.automute = false; });

    audioGroup.add_argument ("--no-audio-processing", "--no-audioreactive")
	.help ("Disables audio processing for backgrounds")
	.flag ()
	.action ([this] (const std::string& value) -> void { this->settings.audio.audioprocessing = false; });

    auto& apiGroup = program.add_group ("Daemon API");

    apiGroup.add_argument ("--api-socket", "--listen")
	.help ("Listen for commands on the unix socket ($LWE_SOCKET or $XDG_RUNTIME_DIR/lwe/engine.sock)")
	.flag ()
	.action ([this] (const std::string& value) -> void { this->settings.general.apiSocket = true; });

    apiGroup.add_argument ("--daemon")
	.help ("Idle-daemon mode: boot with no background (pass --screen-root per output, no --bg) and await `show`")
	.flag ()
	.action ([this] (const std::string& value) -> void {
	    this->settings.general.daemonMode = true;
	    this->settings.general.apiSocket = true;
	    // Stop, not Off: the engine releases outputs itself when something goes
	    // fullscreen (41ms release / 105ms re-acquire, measured) and needs no
	    // outside watcher. Persisted state and client verbs both override this.
	    this->settings.render.fullscreenBehavior = FullscreenBehavior::Stop;
	});

    auto& screenshotGroup = program.add_group ("Screenshot options");

    screenshotGroup.add_argument ("--screenshot")
	.help ("Takes a screenshot of the background for it's use with tools like PyWAL")
	.default_value ("")
	.action ([this] (const std::string& value) -> void {
	    this->settings.screenshot.take = true;
	    this->settings.screenshot.path = value;
	});

    screenshotGroup.add_argument ("--screenshot-delay")
	.help ("Frames to wait before taking the screenshot")
	.default_value<uint32_t> (5)
	.store_into (this->settings.screenshot.delay);

    auto& contentGroup = program.add_group ("Content options");

    contentGroup.add_argument ("--assets-dir", "--assetsfolder")
	.help ("Folder where the assets are stored")
	.default_value ("")
	.action ([this] (const std::string& value) -> void { this->settings.general.assets = value; });

    contentGroup.add_argument ("--properties-file")
	.help ("JSON file ({screen: {property: value}}) re-read on SIGUSR1 for live property reload")
	.default_value ("")
	.action ([this] (const std::string& value) -> void { this->settings.general.propertiesFile = value; });

    auto& engineGroup = program.add_group ("Engine settings");

    engineGroup.add_argument ("--resclamp")
	.help ("Scene size cap, a multiple of the largest screen, up to 4; 0 is no cap")
	.action ([&flags] (const std::string& text) -> void {
	    flags.ssfactor = Knob<float> { FlagValues::clampFactor ("--resclamp", text), "flag", text };
	});

    engineGroup.add_argument ("--effectclamp")
	.help ("Effect layer size cap, a multiple of the largest screen, up to 4; 0 is no cap")
	.action ([&flags] (const std::string& text) -> void {
	    flags.clampComposites = Knob<float> { FlagValues::clampFactor ("--effectclamp", text), "flag", text };
	});

    engineGroup.add_argument ("--texturecache")
	.help ("Use the compressed textures: on or off")
	.action ([&flags] (const std::string& text) -> void {
	    flags.texcomp = Knob<bool> { FlagValues::onOff ("--texturecache", text), "flag", text };
	});

    engineGroup.add_argument ("--texturedetail")
	.help ("Texture detail: auto or full")
	.action ([&flags] (const std::string& text) -> void {
	    flags.texdetailAuto = Knob<bool> { FlagValues::autoFull ("--texturedetail", text), "flag", text };
	});

    engineGroup.add_argument ("--videodecode")
	.help ("Video decoding: software or auto")
	.action ([&flags] (const std::string& text) -> void {
	    flags.hwdec = Knob<std::string> { FlagValues::videoDecode ("--videodecode", text), "flag", text };
	});

    engineGroup.add_argument ("--color")
	.help ("Starting color correction: \"brightness contrast saturation hue\", hue in degrees")
	.action ([&flags] (const std::string& text) -> void {
	    flags.cc = Knob<glm::vec4> { FlagValues::color ("--color", text), "flag", text };
	});

    engineGroup.add_argument ("--speed")
	.help ("Animation speed from 0 to 10, 1 is normal")
	.action ([&flags] (const std::string& text) -> void {
	    flags.timescale = Knob<float> { FlagValues::decimalInRange ("--speed", text, 0.0, 10.0), "flag", text };
	});

    engineGroup.add_argument ("--watchdog")
	.help ("Free the screens after this long with no frames and no panel; 0 is off")
	.action ([&flags] (const std::string& text) -> void {
	    flags.deadman = Knob<int> { FlagValues::watchdogSeconds ("--watchdog", text), "flag", text };
	});

    engineGroup.add_argument ("--lightdimming")
	.help ("Scene light dimming from 0.01 to 1000")
	.action ([&flags] (const std::string& text) -> void {
	    flags.classicK
		= Knob<float> { FlagValues::decimalInRange ("--lightdimming", text, 0.01, 1000.0), "flag", text };
	});

    engineGroup.add_argument ("--lightfalloff")
	.help ("Scene light falloff from 0.5 to 6")
	.action ([&flags] (const std::string& text) -> void {
	    flags.classicExp
		= Knob<float> { FlagValues::decimalInRange ("--lightfalloff", text, 0.5, 6.0), "flag", text };
	});

    engineGroup.add_argument ("--audiogain")
	.help ("Sound reaction strength from 0.1 to 20")
	.action ([&flags] (const std::string& text) -> void {
	    flags.audioGain = Knob<float> { FlagValues::decimalInRange ("--audiogain", text, 0.1, 20.0), "flag", text };
	});

    engineGroup.add_argument ("--audiosmoothing")
	.help ("Sound level smoothing in milliseconds, 0 to 500")
	.action ([&flags] (const std::string& text) -> void {
	    flags.audioSmooth = Knob<float> { FlagValues::milliseconds ("--audiosmoothing", text), "flag", text };
	});

    engineGroup.add_argument ("--socket")
	.help ("Command socket path")
	.action ([&flags] (const std::string& text) -> void {
	    flags.socket = Knob<std::filesystem::path> { FlagValues::path ("--socket", text), "flag", text };
	});

    auto& configurationGroup = program.add_group ("Wallpaper configuration options");

    configurationGroup.add_argument ("--disable-particles", "--no-particles")
	.help ("Disables particles for the backgrounds")
	.flag ()
	.action ([this] (const std::string& value) -> void { this->settings.general.disableParticles = true; });

    configurationGroup.add_argument ("--disable-mouse", "--no-mouse")
	.help ("Disables mouse interaction with the backgrounds")
	.flag ()
	.action ([this] (const std::string& value) -> void { this->settings.mouse.enabled = false; });
    configurationGroup.add_argument ("--disable-parallax", "--no-parallax")
	.help ("Disables parallax effect for the backgrounds")
	.flag ()
	.action ([this] (const std::string& value) -> void { this->settings.mouse.disableparallax = true; });

    configurationGroup.add_argument ("-l", "--list-properties")
	.help ("List all the available properties and their configuration")
	.flag ()
	.store_into (this->settings.general.onlyListProperties);

    configurationGroup.add_argument ("--set-property", "--property")
	.help ("Overrides the default value of the given property")
	.action ([this] (const std::string& value) -> void {
	    const std::string::size_type equals = value.find ('=');

	    // properties without value are treated as booleans for now
	    if (equals == std::string::npos) {
		this->settings.general.properties[value] = "1";
	    } else {
		this->settings.general.properties[value.substr (0, equals)] = value.substr (equals + 1);
	    }
	})
	.append ();

    auto& debuggingGroup = program.add_group ("Debugging options");

    debuggingGroup.add_argument ("-z", "--dump-structure")
	.help ("Dumps the structure of the backgrounds")
	.flag ()
	.store_into (this->settings.general.dumpStructure);

    debuggingGroup.add_argument ("--render-debug")
	.help (
	    "Scene render debug mode: base-only, no-solid-final, pass-log, object=<id>, skip-object=<id>, or "
	    "skip-effect=<id>. Can be repeated."
	)
	.action ([this] (const std::string& value) -> void {
	    const auto parseDebugId = [&value] (const std::string& prefix) -> std::optional<int> {
		try {
		    return std::stoi (value.substr (prefix.length ()));
		} catch (const std::invalid_argument&) {
		    sLog.exception ("Invalid numeric value for --render-debug ", value);
		} catch (const std::out_of_range&) {
		    sLog.exception ("Out-of-range numeric value for --render-debug ", value);
		}
		return std::nullopt;
	    };

	    if (value == "base-only") {
		this->settings.render.debug.baseOnly = true;
	    } else if (value == "no-solid-final") {
		this->settings.render.debug.noSolidFinal = true;
	    } else if (value == "pass-log") {
		this->settings.render.debug.passLog = true;
	    } else if (value.rfind ("object=", 0) == 0) {
		this->settings.render.debug.objectFilter = parseDebugId ("object=");
	    } else if (value.rfind ("skip-object=", 0) == 0) {
		if (const auto id = parseDebugId ("skip-object="); id.has_value ()) {
		    this->settings.render.debug.skipObjects.emplace_back (*id);
		}
	    } else if (value.rfind ("skip-effect=", 0) == 0) {
		if (const auto id = parseDebugId ("skip-effect="); id.has_value ()) {
		    this->settings.render.debug.skipEffects.emplace_back (*id);
		}
	    } else {
		sLog.exception ("Invalid render debug mode: ", value);
	    }
	})
	.append ();

    debuggingGroup.add_argument ("--debug")
	.help ("Set a debugging switch by its plain name, as switch=value; repeat for more")
	.action ([] (const std::string& token) -> void {
	    const auto action = DebugSwitches::resolve (token);

	    if (action.setTo.has_value ()) {
		setenv (action.variable.c_str (), action.setTo->c_str (), 1);
	    } else {
		unsetenv (action.variable.c_str ());
	    }
	})
	.append ();

    debuggingGroup.add_argument ("--help-debug")
	.help ("List the debugging switches and exit")
	.flag ()
	.action ([] (const std::string& value) -> void {
	    std::cout << DebugSwitches::helpText ();
	    std::exit (0);
	});

    program.add_epilog (
	"Usage examples:\n"
	"  linux-wallpaperengine --screen-root HDMI-1 --bg 2317494988 --scaling fill --clamp border\n"
	"    Runs the background 2317494988 on screen HDMI-1, scaling it to fill the screen and clamping the UVs to "
	"the border\n\n"
	"  linux-wallpaperengine 2317494988\n"
	"    Previews the background 2317494988 on a window\n\n"
	"  linux-wallpaperengine --screen-root HDMI-1 --bg 2317494988 --screen-root HDMI-2 --bg 1108150151\n"
	"    Runs two backgrounds on two screens, one on HDMI-1 and the other on HDMI-2\n\n"
	"  linux-wallpaperengine --screen-root HDMI-1 --screen-root HDMI-2 2317494988\n"
	"    Runs the background 2317494988 on two screens, one on HDMI-1 and the other on HDMI-2\n\n"
	"  linux-wallpaperengine --screen-span HDMI-1,HDMI-2 --bg 2317494988 --scaling fill\n"
	"    Spans the background 2317494988 across HDMI-1 and HDMI-2 as a single stretched wallpaper\n\n"
    );

    try {
	const auto unknownArguments = program.parse_known_args (this->m_argc, this->m_argv);

	for (const auto& argument : unknownArguments) {
	    sLog.error ("Ignoring unrecognized command line argument: ", argument);
	}

	Config::setFlags (flags);

	if (fullscreenWord.has_value ()) {
	    if (fullscreenWord->empty ()) {
		throw std::runtime_error ("--fullscreen takes keep, pause or stop; got an empty value");
	    }

	    if (noFullscreenPause) {
		throw std::runtime_error ("--fullscreen and --no-fullscreen-pause cannot be used together");
	    }

	    if (*fullscreenWord == "keep") {
		this->settings.render.pauseOnFullscreen = false;
		this->settings.render.fullscreenBehavior = FullscreenBehavior::Off;
	    } else if (*fullscreenWord == "pause") {
		this->settings.render.fullscreenBehavior = FullscreenBehavior::Pause;
	    } else if (*fullscreenWord == "stop") {
		this->settings.render.fullscreenBehavior = FullscreenBehavior::Stop;
	    }
	}

	// idle-daemon mode boots with NO backgrounds and awaits `show` over the socket
	if (this->settings.general.defaultBackground.empty () && !this->settings.general.daemonMode) {
	    throw std::runtime_error ("At least one background ID must be specified");
	}

	this->settings.audio.volume = std::max (0, std::min (this->settings.audio.volume, 128));
	this->settings.screenshot.delay
	    = std::max<uint32_t> (0, std::min<uint32_t> (this->settings.screenshot.delay, 5));

	// use std::cout on this in case logging is disabled, this way it's easy to look at what is running
	std::stringbuf buffer;
	std::ostream bufferStream (&buffer);

	bufferStream << "Running with: ";

	for (int i = 0; i < this->m_argc; i++) {
	    bufferStream << this->m_argv[i];
	    bufferStream << " ";
	}

	std::cout << buffer.str () << std::endl;
	// perform some extra validation on the inputs
	this->validateAssets ();
	this->validateScreenshot ();

	// setup application state
	this->state.general.keepRunning = true;
	this->state.audio.enabled = this->settings.audio.enabled;
	this->state.audio.volume = this->settings.audio.volume;
	this->state.mouse.enabled = this->settings.mouse.enabled;

#if DEMOMODE
	sLog.error ("WARNING: RUNNING IN DEMO MODE WILL STOP WALLPAPERS AFTER 5 SECONDS SO VIDEO CAN BE RECORDED");
	// special settings for demomode
	this->settings.render.maximumFPS = 30;
	this->settings.screenshot.take = false;
	this->settings.render.pauseOnFullscreen = false;
	this->settings.render.fullscreenBehavior = FullscreenBehavior::Off;
#endif /* DEMOMODE */
    } catch (const std::runtime_error& e) {
	throw std::runtime_error (
	    std::string (e.what ()) + ". Use " + std::string (this->m_argv[0]) + " --help for more information"
	);
    }
}

int ApplicationContext::getArgc () const { return this->m_argc; }

char** ApplicationContext::getArgv () const { return this->m_argv; }

std::filesystem::path ApplicationContext::translateBackground (const std::string& bgIdOrPath) {
    if (bgIdOrPath.find ('/') == std::string::npos) {
	return Steam::FileSystem::workshopDirectory (WORKSHOP_APP_ID, bgIdOrPath);
    }

    return bgIdOrPath;
}

void ApplicationContext::validateAssets () {
    if (!this->settings.general.assets.empty ()) {
	sLog.out (
	    "Using wallpaper engine's assets at ", this->settings.general.assets, " based on --assets-dir parameter"
	);
	return;
    }

    try {
	this->settings.general.assets = Steam::FileSystem::appDirectory (APP_DIRECTORY, "assets");
    } catch (std::runtime_error&) {
	// set current path as assets' folder
	this->settings.general.assets = std::filesystem::canonical ("/proc/self/exe").parent_path () / "assets";
    }
}

void ApplicationContext::validateScreenshot () const {
    if (!this->settings.screenshot.take) {
	return;
    }

    if (!this->settings.screenshot.path.has_extension ()) {
	sLog.exception ("Cannot determine screenshot format");
    }

    const std::string extension = this->settings.screenshot.path.extension ();

    if (extension != ".bmp" && extension != ".png" && extension != ".jpeg" && extension != ".jpg") {
	sLog.exception ("Cannot determine screenshot format, unknown extension ", extension);
    }
}
