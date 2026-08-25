#include "WallpaperApplication.h"

#include "WallpaperEngine/Logging/InstrumentRegistry.h"

#include "Steam/FileSystem/FileSystem.h"
#include "WallpaperEngine/Application/ApplicationState.h"
#include "WallpaperEngine/Assets/AssetLoadException.h"
#include "WallpaperEngine/Audio/Drivers/Detectors/PulseAudioPlayingDetector.h"
#include "WallpaperEngine/Audio/Drivers/NullAudioDriver.h"
#include "WallpaperEngine/Audio/Drivers/SDLAudioDriver.h"
#include "WallpaperEngine/FileSystem/Container.h"
#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/Render/CTexture.h"
#include "WallpaperEngine/Render/Drivers/VideoFactories.h"
#include "WallpaperEngine/Render/FBOProvider.h"
#include "WallpaperEngine/Render/RenderContext.h"

#include "WallpaperEngine/Data/Dumpers/StringPrinter.h"
#include "WallpaperEngine/Data/Parsers/ProjectParser.h"

#include "WallpaperEngine/Data/Model/Property.h"
#include "WallpaperEngine/Data/Model/Wallpaper.h"
#include "WallpaperEngine/Render/Wallpapers/CScene.h"
#include "WallpaperEngine/Render/Wallpapers/CWeb.h"

#include "WallpaperEngine/Debugging/CallStack.h"
#include "WallpaperEngine/FileSystem/Adapters/MediaCover.h"
#include "WallpaperEngine/Media/DBusMediaSource.h"
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <set>

#ifdef __GLIBC__
#include <malloc.h>
#endif

#if DEMOMODE
#include "recording.h"
#endif /* DEMOMODE */

#include <algorithm>
#include <climits>
#include <numeric>
#include <sys/mman.h>
#include <unistd.h>
#include <utility>
#include <vector>
#define STB_IMAGE_WRITE_IMPLEMENTATION
#include <stb_image_write.h>
#include <thread>

#define FULLSCREEN_CHECK_WAIT_TIME 250

float g_Time;
float g_TimeLast;
float g_Daytime;

static float lweEnvFloat (const char* name, const float fallback, const float lo, const float hi) {
    const char* env = getenv (name);
    if (env == nullptr) {
	return fallback;
    }
    return std::clamp (static_cast<float> (atof (env)), lo, hi);
}
float g_LweClassicDivisor = lweEnvFloat ("LWE_CLASSICK", 16.0f, 0.01f, 1000.0f);
float g_LweFalloffExp = lweEnvFloat ("LWE_CLASSICEXP", 2.0f, 0.5f, 6.0f);
float g_LweAudioGain = lweEnvFloat ("LWE_AUDIOGAIN", 1.0f, 0.1f, 20.0f);

using namespace WallpaperEngine::Assets;
using namespace WallpaperEngine::Application;
using namespace WallpaperEngine::Data::Model;
using namespace WallpaperEngine::FileSystem;

/** wire spelling of the fullscreen policy; the dispatcher validates the same three words */
const char* fullscreenBehaviorName (const FullscreenBehavior behavior) {
    switch (behavior) {
	case FullscreenBehavior::Pause:
	    return "pause";
	case FullscreenBehavior::Stop:
	    return "stop";
	default:
	    return "off";
    }
}

std::optional<FullscreenBehavior> parseFullscreenBehavior (const std::string& value) {
    if (value == "off") {
	return FullscreenBehavior::Off;
    }

    if (value == "pause") {
	return FullscreenBehavior::Pause;
    }

    if (value == "stop") {
	return FullscreenBehavior::Stop;
    }

    return std::nullopt;
}

void CustomGLDebugCallback (
    GLenum source, GLenum type, GLuint id, GLenum severity, GLsizei length, const GLchar* message, const void* userParam
) {
    if (severity != GL_DEBUG_SEVERITY_HIGH) {
	return;
    }

    sLog.error ("OpenGL error: ", message, ", type: ", type, ", id: ", id);

    std::vector<WallpaperEngine::Debugging::CallStack::CallInfo> callInfo;

    WallpaperEngine::Debugging::CallStack::GetCalls (callInfo);

    for (std::vector<WallpaperEngine::Debugging::CallStack::CallInfo>::size_type i = 0; i < callInfo.size (); ++i) {
	fprintf (
	    stderr, "[%3lu] %15lu: %s in %s\n", callInfo.size () - i, callInfo[i].offset, callInfo[i].function.c_str (),
	    callInfo[i].module.c_str ()
	);
    }
}

WallpaperApplication::WallpaperApplication (ApplicationContext& context) : m_context (context) {
    if (const char* e = getenv ("LWE_CC"); e != nullptr) {
	glm::vec4 cc = this->m_colorCorrection;

	if (sscanf (e, "%f %f %f %f", &cc.x, &cc.y, &cc.z, &cc.w) == 4) {
	    this->setColorCorrection (cc);
	}
    }

    if (const char* e = getenv ("LWE_TIMESCALE"); e != nullptr && *e != '\0') {
	char* end = nullptr;
	const double v = strtod (e, &end);

	if (end != e && v >= 0.0) {
	    this->setTimescale (static_cast<float> (v));
	}
    }

    this->m_showDefaults = {
	.volume = this->m_context.settings.audio.volume,
	.audioProcessing = this->m_context.settings.audio.audioprocessing,
	.mouseEnabled = this->m_context.settings.mouse.enabled,
	.automute = this->m_context.settings.audio.automute,
	.fullscreenBehavior = this->m_context.settings.render.fullscreenBehavior,
	.screenScalings = this->m_context.settings.general.screenScalings,
	.screenClamps = this->m_context.settings.general.screenClamps,
    };

    if (const char* e = getenv ("LWE_DEADMAN"); e != nullptr && *e != '\0') {
	char* end = nullptr;
	const long v = strtol (e, &end, 10);

	if (end != e && v >= 0 && v <= 86400) {
	    this->m_deadmanSeconds = static_cast<int> (v);
	}
    }

    this->initializeSubsystems ();
    this->loadBackgrounds ();
    this->setupProperties ();
    this->setupBrowser ();
    this->initializePlaylists ();
}

void WallpaperApplication::initializeSubsystems () {
    // initialize player dbus (update every 2 seconds)
    m_mediaSource = std::make_unique<WallpaperEngine::Media::DBusMediaSource> (std::chrono::milliseconds (2000));
}

AssetLocatorUniquePtr WallpaperApplication::setupAssetLocator (const std::string& bg) const {
    return WallpaperEngine::Assets::setupAssetLocator (
	bg, this->m_context.settings.general.assets, *this->m_mediaSource
    );
}

void WallpaperApplication::loadBackgrounds () {
    if (this->m_context.settings.render.mode == ApplicationContext::NORMAL_WINDOW
	|| this->m_context.settings.render.mode == ApplicationContext::EXPLICIT_WINDOW) {
	auto path = this->m_context.settings.general.defaultBackground;

	if (this->m_context.settings.general.defaultPlaylist.has_value ()
	    && !this->m_context.settings.general.defaultPlaylist->items.empty ()) {
	    path = this->m_context.settings.general.defaultPlaylist->items.front ();
	}

	this->m_backgrounds["default"] = this->loadBackground (path);
	return;
    }

    for (const auto& [screen, path] : this->m_context.settings.general.screenBackgrounds) {
	// skip span group synthetic keys here, they're handled below
	if (screen.rfind ("span:", 0) == 0) {
	    continue;
	}
	if (path.empty ()) {
	    if (this->m_context.settings.general.defaultBackground.empty ()) {
		continue;
	    }

	    this->m_backgrounds[screen] = this->loadBackground (this->m_context.settings.general.defaultBackground);
	} else {
	    this->m_backgrounds[screen] = this->loadBackground (path);
	}
    }

    // Load one background per span group
    for (const auto& spanGroup : this->m_context.settings.general.spanGroups) {
	if (spanGroup.screens.empty ()) {
	    continue;
	}

	std::filesystem::path bgPath = spanGroup.background;
	if (bgPath.empty ()) {
	    bgPath = this->m_context.settings.general.defaultBackground;
	}

	// use the first screen's name as the group key for the loaded project
	const std::string groupKey = "span:" + spanGroup.screens.front ();
	this->m_backgrounds[groupKey] = this->loadBackground (bgPath);
    }
}

ProjectUniquePtr WallpaperApplication::loadBackground (const std::string& bg) {
    auto container = this->setupAssetLocator (bg);
    auto json = WallpaperEngine::Data::JSON::parseLenient (container->readString ("project.json"));

    if (json.find ("type") == json.end () && json.find ("dependency") != json.end ()) {
	static int s_presetDepth = 0;
	if (s_presetDepth >= 4) {
	    sLog.exception ("Preset wallpaper dependency chain too deep at ", bg);
	}

	const auto& dep = json["dependency"];
	const std::string depId = dep.is_string () ? dep.get<std::string> () : std::to_string (dep.get<int> ());
	const std::filesystem::path basePath = std::filesystem::path (bg).parent_path () / depId;
	sLog.out ("Preset wallpaper: resolving dependency ", depId, " for ", bg);

	s_presetDepth++;
	auto project = this->loadBackground (basePath.string ());
	s_presetDepth--;

	const auto preset = json.find ("preset");
	if (preset != json.end () && preset->is_object ()) {
	    for (const auto& [key, value] : preset->items ()) {
		if (value.is_null ()) {
		    continue;
		}
		const auto prop = project->properties.find (key);
		if (prop == project->properties.end ()) {
		    sLog.out ("Preset value for unknown property ignored: ", key);
		    continue;
		}
		const std::string str = value.is_string () ? value.get<std::string> () : value.dump ();
		prop->second->update (str, DynamicValue::UpdateSource::User);
	    }
	}

	return project;
    }

    // when a background is loaded, reset the screenshot variables
    // this allows taking screenshots after a background changes
    // useful for playlists
    if (this->m_context.settings.screenshot.take) {
	this->m_nextFrameScreenshot = this->m_context.settings.screenshot.delay;

	if (this->m_videoDriver != nullptr) {
	    this->m_nextFrameScreenshot += this->m_videoDriver->getFrameCounter ();
	}

	this->m_screenShotTaken = false;
    }

    auto project = WallpaperEngine::Data::Parsers::ProjectParser::parse (json, std::move (container));
    project->fromPackage = std::filesystem::exists (std::filesystem::path (bg) / "scene.pkg");
    return project;
}

std::vector<std::size_t>
WallpaperApplication::buildPlaylistOrder (const ApplicationContext::PlaylistDefinition& definition) {
    std::vector<std::size_t> order (definition.items.size ());
    std::iota (order.begin (), order.end (), 0);

    if (definition.settings.order == "random") {
	std::shuffle (order.begin (), order.end (), this->m_playlistRng);
    }

    return order;
}

void WallpaperApplication::initializePlaylists () {
    const bool hasDefaultPlaylist = this->m_context.settings.general.defaultPlaylist.has_value ();
    const bool hasScreenPlaylists = !this->m_context.settings.general.screenPlaylists.empty ();

    if (!hasDefaultPlaylist && !hasScreenPlaylists) {
	return;
    }

    const auto now = std::chrono::steady_clock::now ();

    auto registerPlaylist = [this, now] (
				const std::string& key, const ApplicationContext::PlaylistDefinition& playlist,
				std::optional<std::filesystem::path> currentPath
			    ) {
	if (playlist.items.empty ()) {
	    return;
	}

	ActivePlaylist state;

	state.definition = playlist;
	state.order = this->buildPlaylistOrder (playlist);

	if (state.order.empty ()) {
	    return;
	}

	if (currentPath.has_value ()) {
	    state.orderIndex = 0;

	    for (std::size_t i = 0; i < state.order.size (); i++) {
		if (playlist.items[state.order[i]] == currentPath.value ()) {
		    state.orderIndex = i;
		    break;
		}
	    }
	}

	const uint32_t delayMinutes = std::max<uint32_t> (1, state.definition.settings.delayMinutes);
	state.nextSwitch = now + std::chrono::minutes (delayMinutes);
	state.lastUpdate = now;

	this->m_activePlaylists.insert_or_assign (key, std::move (state));
    };

    if (hasDefaultPlaylist
	&& (this->m_context.settings.render.mode == ApplicationContext::NORMAL_WINDOW
	    || this->m_context.settings.render.mode == ApplicationContext::EXPLICIT_WINDOW)) {
	const auto& playlist = this->m_context.settings.general.defaultPlaylist.value ();
	const auto currentPath = playlist.items.empty ()
	    ? std::optional<std::filesystem::path> { this->m_context.settings.general.defaultBackground }
	    : std::optional<std::filesystem::path> { playlist.items.front () };
	registerPlaylist ("default", playlist, currentPath);
    }

    for (const auto& [screen, playlist] : this->m_context.settings.general.screenPlaylists) {
	const auto current = this->m_context.settings.general.screenBackgrounds.find (screen);
	const auto currentPath = current != this->m_context.settings.general.screenBackgrounds.end ()
	    ? std::optional<std::filesystem::path> { current->second }
	    : std::nullopt;
	registerPlaylist (screen, playlist, currentPath);
    }
}

void WallpaperApplication::ensureBrowserForProject (const Project& project) {
    if (!project.wallpaper->is<Web> ()) {
	return;
    }

    this->ensureWebHelperClient ();
}

void WallpaperApplication::ensureAudioForProject (const Project& project) {
    if (project.supportsAudioProcessing
	&& dynamic_cast<WallpaperEngine::Audio::Drivers::Recorders::PulseAudioPlaybackRecorder*> (
	       this->m_audioRecorder.get ()
	   ) == nullptr) {
	auto recorder = std::make_unique<WallpaperEngine::Audio::Drivers::Recorders::PulseAudioPlaybackRecorder> ();

	// rebuild the driver against the new recorder FIRST (the old driver's audio
	// thread may still read the old recorder until its destructor joins it), then
	// retire the old recorder. Streams belong to the outgoing scene - this runs
	// after clearWallpapers on the rebuild path, so nothing live is playing.
	if (dynamic_cast<WallpaperEngine::Audio::Drivers::SDLAudioDriver*> (this->m_audioDriver.get ()) != nullptr) {
	    this->m_audioDriver = std::make_unique<WallpaperEngine::Audio::Drivers::SDLAudioDriver> (
		this->m_context, *this->m_audioDetector, *recorder
	    );
	} else {
	    this->m_audioDriver = std::make_unique<WallpaperEngine::Audio::Drivers::NullAudioDriver> (
		this->m_context, *this->m_audioDetector, *recorder
	    );
	}

	this->m_audioRecorder = std::move (recorder);
	this->m_audioContext->setDriver (*this->m_audioDriver);
	sLog.out ("API: upgraded audio recorder for audio-reactive wallpaper");
    }

    if (!this->m_context.settings.audio.enabled) {
	return;
    }

    if (!project.wallpaper->is<Scene> ()) {
	return;
    }

    const bool hasSound = std::ranges::any_of (project.wallpaper->as<Scene> ()->objects, [] (const auto& object) {
	return object->template is<Sound> ();
    });

    if (!hasSound) {
	return;
    }

    // never downgrade: tearing down a live SDL device would kill streams already playing
    if (dynamic_cast<WallpaperEngine::Audio::Drivers::SDLAudioDriver*> (this->m_audioDriver.get ()) != nullptr) {
	return;
    }

    this->m_audioDriver = std::make_unique<WallpaperEngine::Audio::Drivers::SDLAudioDriver> (
	this->m_context, *this->m_audioDetector, *this->m_audioRecorder
    );
    this->m_audioContext->setDriver (*this->m_audioDriver);
}

bool WallpaperApplication::makeAnyViewportCurrent () const {
    if (!this->m_renderContext) {
	return false;
    }

    const auto& viewports = this->m_renderContext->getOutput ().getViewports ();

    if (viewports.empty ()) {
	return false;
    }

    viewports.begin ()->second->makeCurrent ();
    return true;
}

bool WallpaperApplication::preflightWallpaper (const std::string& path) {
    try {
	// avoid mutating state, just ensure project.json parses
	auto container = this->setupAssetLocator (path);
	const auto json = WallpaperEngine::Data::JSON::parseLenient (container->readString ("project.json"));
	if (!json.contains ("type") || !json.contains ("file")) {
	    sLog.error ("Preflight failed for ", path, ": missing required fields");
	    return false;
	}
	return true;
    } catch (const std::exception& e) {
	sLog.error ("Preflight failed for ", path, ": ", e.what ());
	return false;
    }
}

bool WallpaperApplication::selectNextCandidate (ActivePlaylist& playlist, std::size_t& outOrderIndex) {
    if (playlist.order.empty ()) {
	return false;
    }

    std::size_t attempts = 0;
    std::size_t candidateOrderIndex = outOrderIndex;

    while (attempts < playlist.order.size ()) {
	const auto candidateIndex = playlist.order[candidateOrderIndex];

	if (!playlist.failedIndices.contains (candidateIndex)) {
	    outOrderIndex = candidateOrderIndex;
	    return true;
	}

	attempts++;
	candidateOrderIndex = (candidateOrderIndex + 1) % playlist.order.size ();
    }

    return false;
}

void WallpaperApplication::advancePlaylist (
    const std::string& screen, ActivePlaylist& playlist, const std::chrono::steady_clock::time_point& now
) {
    if (playlist.order.empty ()) {
	return;
    }

    playlist.orderIndex = (playlist.orderIndex + 1) % playlist.order.size ();

    if (playlist.orderIndex == 0 && playlist.definition.settings.order == "random") {
	std::shuffle (playlist.order.begin (), playlist.order.end (), this->m_playlistRng);
    }

    std::size_t candidateOrderIndex = playlist.orderIndex;

    if (!this->selectNextCandidate (playlist, candidateOrderIndex)) {
	sLog.error ("All playlist items failed for ", screen, ", keeping current wallpaper");
	const uint32_t delayMinutes = std::max<uint32_t> (1, playlist.definition.settings.delayMinutes);
	playlist.nextSwitch = now + std::chrono::minutes (delayMinutes);
	return;
    }

    const auto candidateIndex = playlist.order[candidateOrderIndex];
    const auto& candidatePath = playlist.definition.items[candidateIndex];

    if (!this->preflightWallpaper (candidatePath.string ())) {
	playlist.failedIndices.insert (candidateIndex);

	if (!this->selectNextCandidate (playlist, candidateOrderIndex)) {
	    sLog.error ("All playlist items failed for ", screen, ", keeping current wallpaper");
	    const uint32_t delayMinutes = std::max<uint32_t> (1, playlist.definition.settings.delayMinutes);
	    playlist.nextSwitch = now + std::chrono::minutes (delayMinutes);
	    return;
	}
    }

    playlist.orderIndex = candidateOrderIndex;
    const auto& nextPath = playlist.definition.items[playlist.order[playlist.orderIndex]];

    bool loaded = false;

    try {
	if (!this->makeAnyViewportCurrent ()) {
	    sLog.error ("Cannot switch playlist on ", screen, ": no active viewport");
	    throw std::runtime_error ("No viewport available");
	}

	auto project = this->loadBackground (nextPath.string ());

	this->setupPropertiesForProject (*project);
	this->ensureBrowserForProject (*project);
	this->ensureAudioForProject (*project);

	this->m_backgrounds[screen] = std::move (project);

	const auto scalingIt = this->m_context.settings.general.screenScalings.find (screen);
	const auto clampIt = this->m_context.settings.general.screenClamps.find (screen);
	const auto scaling = scalingIt != this->m_context.settings.general.screenScalings.end ()
	    ? scalingIt->second
	    : this->m_context.settings.render.window.scalingMode;
	const auto clamp = clampIt != this->m_context.settings.general.screenClamps.end ()
	    ? clampIt->second
	    : this->m_context.settings.render.window.clamp;

	if (this->m_renderContext) {
	    this->m_renderContext->setWallpaper (
		screen,
		WallpaperEngine::Render::CWallpaper::fromWallpaper (
		    *this->m_backgrounds[screen]->wallpaper, *this->m_renderContext, *this->m_audioContext,
		    this->m_webHelper.get (), scaling, clamp
		)
	    );
	}

	this->m_context.settings.general.screenBackgrounds[screen] = nextPath;
	loaded = true;
    } catch (const std::exception& e) {
	sLog.error ("Failed to advance playlist on ", screen, ": ", e.what ());
    }

    if (!loaded) {
	playlist.failedIndices.insert (playlist.order[playlist.orderIndex]);

	// Keep current position; next timer tick will retry advancement
	sLog.error ("Failed to load wallpaper for ", screen, ", will retry on next cycle");
    }

    const uint32_t delayMinutes = std::max<uint32_t> (1, playlist.definition.settings.delayMinutes);
    playlist.nextSwitch = now + std::chrono::minutes (delayMinutes);
}

void WallpaperApplication::updatePlaylists () {
    if (this->m_activePlaylists.empty ()) {
	return;
    }

    const auto now = std::chrono::steady_clock::now ();

    for (auto& [screen, playlist] : this->m_activePlaylists) {
	playlist.lastUpdate = now;

	if (playlist.definition.settings.mode != "timer") {
	    continue;
	}

	if (playlist.definition.items.size () <= 1) {
	    continue;
	}

	if (now < playlist.nextSwitch) {
	    continue;
	}

	this->advancePlaylist (screen, playlist, now);
    }
}

void WallpaperApplication::setupPropertiesForProject (const Project& project) {
    // show properties if required
    for (const auto& [key, cur] : project.properties) {
	// update the value of the property
	auto override = this->m_context.settings.general.properties.find (key);

	if (override != this->m_context.settings.general.properties.end ()) {
	    cur->update (override->second, DynamicValue::UpdateSource::User);

	    sLog.out ("Applying override value for ", key, " = \"", override->second, "\" -> ", cur->toString ());
	}

	if (this->m_context.settings.general.onlyListProperties) {
	    sLog.out (cur->dump ());
	}
    }
}

void WallpaperApplication::setupProperties () {
    for (const auto& [background, info] : this->m_backgrounds) {
	this->setupPropertiesForProject (*info);
    }
}

const std::vector<WallpaperApplication::WebLibraryEntry>& WallpaperApplication::getWebLibrary () const {
    return this->m_webLibrary;
}

std::vector<WallpaperApplication::WebLibraryEntry> WallpaperApplication::enumerateWebBackgrounds () const {
    // same roots and precedence as resolveLibraryBackground: lwe library, then Steam
    std::vector<std::filesystem::path> roots;

    if (const char* dataHome = getenv ("XDG_DATA_HOME"); dataHome != nullptr) {
	roots.emplace_back (std::filesystem::path (dataHome) / "lwe" / "wallpapers");
    }

    if (const char* home = getenv ("HOME"); home != nullptr) {
	roots.emplace_back (std::filesystem::path (home) / ".local" / "share" / "lwe" / "wallpapers");
    }

    for (auto& root : Steam::FileSystem::workshopContentRoots (431960)) {
	roots.push_back (std::move (root));
    }

    std::vector<WebLibraryEntry> result;
    std::set<std::string> seen;

    for (const auto& root : roots) {
	std::error_code listError;

	for (const auto& entry : std::filesystem::directory_iterator (root, listError)) {
	    if (!entry.is_directory (listError)) {
		continue;
	    }

	    const auto projectFile = entry.path () / "project.json";
	    std::ifstream file (projectFile);

	    if (!file.is_open ()) {
		continue;
	    }

	    const std::string contents ((std::istreambuf_iterator<char> (file)), std::istreambuf_iterator<char> ());
	    const auto json = WallpaperEngine::Data::JSON::parseLenient (contents);

	    if (json.is_discarded () || !json.is_object ()) {
		continue;
	    }

	    const auto type = json.find ("type");

	    if (type == json.end () || !type->is_string ()) {
		continue;
	    }

	    std::string typeName = type->get<std::string> ();
	    std::ranges::transform (typeName, typeName.begin (), tolower);

	    if (typeName != "web") {
		continue;
	    }

	    // the scheme name comes from project.json's workshopid at load time; without
	    // one the runtime falls back to a process-local counter, which cannot be
	    // pre-registered - those wallpapers only ever work as the launch background
	    const auto workshopId = json.find ("workshopid");
	    std::string id;

	    if (workshopId != json.end () && workshopId->is_number ()) {
		id = std::to_string (workshopId->get<int> ());
	    } else if (workshopId != json.end () && workshopId->is_string ()) {
		id = workshopId->get<std::string> ();
	    } else {
		sLog.out ("Web wallpaper without workshopid, scheme not pre-registerable: ", entry.path ().string ());
		continue;
	    }

	    if (seen.insert (id).second) {
		result.push_back ({ .workshopId = id, .path = entry.path () });
	    }
	}
    }

    return result;
}

WallpaperEngine::WebHelper::SpawnConfig WallpaperApplication::buildWebHelperSpawnConfig () const {
    WallpaperEngine::WebHelper::SpawnConfig config;
    config.assetsDir = this->m_context.settings.general.assets;
    config.maximumFPS = this->m_context.settings.render.maximumFPS;
    config.protocolVersion = WallpaperEngine::WebHelper::PROTOCOL_VERSION;
    config.socketPath = WallpaperEngine::WebHelper::SpawnConfig::defaultSocketPath (getpid ());

    for (const auto& entry : this->m_webLibrary) {
	config.schemes.push_back ({ .workshopId = entry.workshopId, .path = entry.path });
    }

    // LWE_CEFLOG / LWE_CEFDEBUG are inherited through the environment, not encoded here.
    return config;
}

void WallpaperApplication::setupBrowser () {
    this->m_webLibrary = this->enumerateWebBackgrounds ();

    if (this->m_webLibrary.empty ()) {
	return;
    }

    sLog.out (
	"web helper: ", this->m_webLibrary.size (), " library web wallpaper scheme(s) known, browser not started"
    );

    const bool anyWebProject = std::any_of (
	this->m_backgrounds.begin (), this->m_backgrounds.end (),
	[] (const std::pair<const std::string, ProjectUniquePtr>& pair) -> bool {
	    return pair.second->wallpaper->is<Web> ();
	}
    );

    if (anyWebProject) {
	this->ensureWebHelperClient ();
    }
}

void WallpaperApplication::ensureWebHelperClient () {
    if (this->m_webHelper) {
	return;
    }

    this->m_webHelper = std::make_unique<WebHelper::HelperClient> (this->buildWebHelperSpawnConfig ());
}

void WallpaperApplication::takeScreenshot (const std::filesystem::path& filename) const {
    const int width = this->m_renderContext->getOutput ().getFullWidth ();
    const int height = this->m_renderContext->getOutput ().getFullHeight ();
    const bool vflip = this->m_renderContext->getOutput ().renderVFlip ();
    const auto& wallpapers = this->m_renderContext->getWallpapers ();

    struct ViewportCapture {
	uint8_t* buffer;
	int readWidth;
	int readHeight;
	int vpWidth;
	int vpHeight;
	int xoffset;
	float ustart, uend, vstart, vend;
    };

    std::vector<ViewportCapture> captures;
    int currentXOffset = 0;

    for (const auto& [screen, viewport] : this->m_renderContext->getOutput ().getViewports ()) {
	// activate opengl context so we can read from the framebuffer
	viewport->makeCurrent ();

	// find the wallpaper for this screen to read from its FBO
	const auto wallpaperIt = wallpapers.find (screen);
	if (wallpaperIt == wallpapers.end ()) {
	    sLog.error ("Cannot find wallpaper for screen ", screen);
	    continue;
	}

	const auto& wallpaper = wallpaperIt->second;
	const int vpWidth = viewport->viewport.z - viewport->viewport.x;
	const int vpHeight = viewport->viewport.w - viewport->viewport.y;

	// bind the wallpaper's FBO to read from it directly
	// this is more reliable than the default framebuffer on some drivers (NVIDIA/Wayland)
	glBindFramebuffer (GL_FRAMEBUFFER, wallpaper->getWallpaperFramebuffer ());

	// ensure rendering is complete before reading
	glFinish ();

	// make room for storing the pixel of this viewport
	const int readWidth = wallpaper->getWidth ();
	const int readHeight = wallpaper->getHeight ();
	const auto bufferSize = readWidth * readHeight * 3;
	auto* buffer = new uint8_t[bufferSize];

	// read the FBO data into the pixel buffer
	glPixelStorei (GL_PACK_ALIGNMENT, 1);
	if (GLEW_VERSION_4_5) {
	    glReadnPixels (0, 0, readWidth, readHeight, GL_RGB, GL_UNSIGNED_BYTE, bufferSize, buffer);
	} else {
	    glReadPixels (0, 0, readWidth, readHeight, GL_RGB, GL_UNSIGNED_BYTE, buffer);
	}

	// restore default framebuffer
	glBindFramebuffer (GL_FRAMEBUFFER, 0);

	if (const GLenum error = glGetError (); error != GL_NO_ERROR) {
	    sLog.error ("Cannot obtain pixel data for screen ", screen, ". OpenGL error: ", error);
	    delete[] buffer;
	    continue;
	}

	// Get the UV coordinates which define the visible portion based on scaling mode
	const auto [ustart, uend, vstart, vend] = wallpaper->getState ().getTextureUVs ();

	captures.push_back (
	    { buffer, readWidth, readHeight, vpWidth, vpHeight, currentXOffset, ustart, uend, vstart, vend }
	);

	if (viewport->single) {
	    currentXOffset += vpWidth;
	}
    }

    const auto extension = filename.extension ();
    const std::string extStr = extension.string ();

    // Offload pixel processing and saving to a background thread to avoid hitches
    std::thread ([captures, width, height, vflip, extStr, filename] () {
	auto* bitmap = new uint8_t[width * height * 3] { 0 };

	for (const auto& capture : captures) {
	    // copy pixels to bitmap, sampling from the UV-defined region
	    for (int y = 0; y < capture.vpHeight; y++) {
		for (int x = 0; x < capture.vpWidth; x++) {
		    // interpolate within the UV range to get source coordinates
		    const float u
			= capture.ustart + (static_cast<float> (x) / capture.vpWidth) * (capture.uend - capture.ustart);
		    const float v = capture.vstart
			+ (static_cast<float> (y) / capture.vpHeight) * (capture.vend - capture.vstart);

		    // convert UV to pixel coordinates in the source buffer
		    const int srcX = std::clamp (static_cast<int> (u * capture.readWidth), 0, capture.readWidth - 1);
		    const int srcY = std::clamp (static_cast<int> (v * capture.readHeight), 0, capture.readHeight - 1);
		    const int srcIdx = (srcY * capture.readWidth + srcX) * 3;

		    const int xfinal = x + capture.xoffset;
		    // FBO content is not flipped like default framebuffer, so invert vflip logic
		    const int yfinal = vflip ? y : (capture.vpHeight - y - 1);

		    if (yfinal >= 0 && yfinal < height && xfinal >= 0 && xfinal < width) {
			bitmap[yfinal * width * 3 + xfinal * 3] = capture.buffer[srcIdx];
			bitmap[yfinal * width * 3 + xfinal * 3 + 1] = capture.buffer[srcIdx + 1];
			bitmap[yfinal * width * 3 + xfinal * 3 + 2] = capture.buffer[srcIdx + 2];
		    }
		}
	    }
	    delete[] capture.buffer;
	}

	if (extStr == ".bmp") {
	    stbi_write_bmp (filename.c_str (), width, height, 3, bitmap);
	} else if (extStr == ".png") {
	    stbi_write_png (filename.c_str (), width, height, 3, bitmap, width * 3);
	} else if (extStr == ".jpg" || extStr == ".jpeg") {
	    stbi_write_jpg (filename.c_str (), width, height, 3, bitmap, 100);
	}

	delete[] bitmap;
    }).detach ();
}

void WallpaperApplication::setupOutput () {
    const char* XDG_SESSION_TYPE = getenv ("XDG_SESSION_TYPE");

    if (!XDG_SESSION_TYPE) {
	sLog.exception (
	    "Cannot read environment variable XDG_SESSION_TYPE, window server detection failed. Please ensure proper "
	    "values are set"
	);
    }

    sLog.debug ("Checking for window servers: ");

    for (const auto& windowServer : sVideoFactories.getRegisteredDrivers ()) {
	sLog.debug ("\t", windowServer);
    }

    this->m_videoDriver = sVideoFactories.createVideoDriver (
	this->m_context.settings.render.mode, XDG_SESSION_TYPE, this->m_context, *this
    );
    this->m_fullScreenDetector
	= sVideoFactories.createFullscreenDetector (XDG_SESSION_TYPE, this->m_context, *this->m_videoDriver);
}

void WallpaperApplication::setupAudio () {
    // ensure audioprocessing is required by any background, and we have it enabled
    const bool audioProcessingRequired = std::ranges::any_of (
	this->m_backgrounds, [] (const std::pair<const std::string, ProjectUniquePtr>& pair) -> bool {
	    return pair.second->supportsAudioProcessing;
	}
    );

    if (audioProcessingRequired && this->m_context.settings.audio.audioprocessing) {
	this->m_audioRecorder
	    = std::make_unique<WallpaperEngine::Audio::Drivers::Recorders::PulseAudioPlaybackRecorder> ();
    } else {
	this->m_audioRecorder = std::make_unique<WallpaperEngine::Audio::Drivers::Recorders::PlaybackRecorder> ();
    }

    if (this->m_context.settings.audio.automute) {
	m_audioDetector = std::make_unique<WallpaperEngine::Audio::Drivers::Detectors::PulseAudioPlayingDetector> (
	    this->m_context, *this->m_fullScreenDetector
	);
    } else {
	m_audioDetector = std::make_unique<WallpaperEngine::Audio::Drivers::Detectors::AudioPlayingDetector> (
	    this->m_context, *this->m_fullScreenDetector
	);
    }

    const bool playbackRequired = this->m_context.settings.audio.enabled
	&& std::ranges::any_of (this->m_backgrounds,
				[] (const std::pair<const std::string, ProjectUniquePtr>& pair) -> bool {
				    return pair.second->wallpaper->is<Scene> ()
					&& std::ranges::any_of (
					       pair.second->wallpaper->as<Scene> ()->objects,
					       [] (const auto& object) { return object->template is<Sound> (); }
					);
				});

    if (playbackRequired) {
	m_audioDriver = std::make_unique<WallpaperEngine::Audio::Drivers::SDLAudioDriver> (
	    this->m_context, *this->m_audioDetector, *this->m_audioRecorder
	);
    } else {
	m_audioDriver = std::make_unique<WallpaperEngine::Audio::Drivers::NullAudioDriver> (
	    this->m_context, *this->m_audioDetector, *this->m_audioRecorder
	);
    }

    // initialize audio context
    m_audioContext = std::make_unique<WallpaperEngine::Audio::AudioContext> (*m_audioDriver);
}

void WallpaperApplication::prepareOutputs () {
    // initialize render context
    m_renderContext
	= std::make_unique<WallpaperEngine::Render::RenderContext> (*m_videoDriver, *this, *this->m_mediaSource);
    this->buildWallpapers ();
}

void WallpaperApplication::buildWallpapers () {
    // create a new background for each screen

    std::map<std::string, std::vector<std::string>> mirrorGroups;
    std::vector<std::string> mirrorGroupOrder;

    for (const auto& [background, info] : this->m_backgrounds) {
	if (background.rfind ("span:", 0) == 0) {
	    continue;
	}

	const auto bgPathIt = this->m_context.settings.general.screenBackgrounds.find (background);
	const std::string effectivePath
	    = (bgPathIt != this->m_context.settings.general.screenBackgrounds.end () && !bgPathIt->second.empty ())
	    ? bgPathIt->second.string ()
	    : this->m_context.settings.general.defaultBackground.string ();

	const auto scalingIt = this->m_context.settings.general.screenScalings.find (background);
	const auto clampIt = this->m_context.settings.general.screenClamps.find (background);
	const auto scaling = scalingIt != this->m_context.settings.general.screenScalings.end ()
	    ? scalingIt->second
	    : this->m_context.settings.render.window.scalingMode;
	const auto clamp = clampIt != this->m_context.settings.general.screenClamps.end ()
	    ? clampIt->second
	    : this->m_context.settings.render.window.clamp;

	const std::string groupKey = effectivePath + "|" + std::to_string (static_cast<int> (scaling)) + "|"
	    + std::to_string (static_cast<uint32_t> (clamp));

	if (mirrorGroups.find (groupKey) == mirrorGroups.end ()) {
	    mirrorGroupOrder.push_back (groupKey);
	}
	mirrorGroups[groupKey].push_back (background);
    }

    for (const auto& groupKey : mirrorGroupOrder) {
	const auto& screens = mirrorGroups[groupKey];
	const std::string& ownerScreen = screens.front ();
	const auto& info = this->m_backgrounds.at (ownerScreen);

	const auto scalingIt = this->m_context.settings.general.screenScalings.find (ownerScreen);
	const auto clampIt = this->m_context.settings.general.screenClamps.find (ownerScreen);
	const auto scaling = scalingIt != this->m_context.settings.general.screenScalings.end ()
	    ? scalingIt->second
	    : this->m_context.settings.render.window.scalingMode;
	const auto clamp = clampIt != this->m_context.settings.general.screenClamps.end ()
	    ? clampIt->second
	    : this->m_context.settings.render.window.clamp;

	std::shared_ptr<WallpaperEngine::Render::CWallpaper> shared (
	    WallpaperEngine::Render::CWallpaper::fromWallpaper (
		*info->wallpaper, *m_renderContext, *m_audioContext, m_webHelper.get (), scaling, clamp
	    )
	);

	if (screens.size () > 1) {
	    // one shared decode context for the whole mirror group; ownerScreen drives the decode
	    shared->setMirrorOwner (ownerScreen);
	    sLog.out (
		"Mirror group: ", screens.size (), " screens share one wallpaper context (decode owner: ", ownerScreen,
		")"
	    );
	}

	for (const auto& screen : screens) {
	    m_renderContext->setWallpaper (screen, shared);
	}
    }

    // Set up span groups: one shared wallpaper per group, registered for each viewport
    for (const auto& spanGroup : this->m_context.settings.general.spanGroups) {
	if (spanGroup.screens.empty ()) {
	    continue;
	}

	const std::string groupKey = "span:" + spanGroup.screens.front ();
	const auto bgIt = this->m_backgrounds.find (groupKey);
	if (bgIt == this->m_backgrounds.end ()) {
	    continue;
	}

	// Compute the bounding box of all viewports in this span group
	const auto& viewports = m_renderContext->getOutput ().getViewports ();
	int minX = INT_MAX, minY = INT_MAX, maxX = INT_MIN, maxY = INT_MIN;
	bool anyFound = false;

	for (const auto& screenName : spanGroup.screens) {
	    const auto vpIt = viewports.find (screenName);
	    if (vpIt == viewports.end ()) {
		sLog.error ("Span group screen not found: ", screenName);
		continue;
	    }
	    anyFound = true;
	    const auto& vp = vpIt->second;
	    const int x = vp->globalPosition.x;
	    const int y = vp->globalPosition.y;
	    const int w = vp->logicalSize.x;
	    const int h = vp->logicalSize.y;
	    sLog.debug (
		"SPAN DEBUG prepareOutputs: screen '", screenName, "' globalPos=(", x, ",", y, ") logicalSize=", w, "x",
		h
	    );
	    minX = std::min (minX, x);
	    minY = std::min (minY, y);
	    maxX = std::max (maxX, x + w);
	    maxY = std::max (maxY, y + h);
	}

	if (!anyFound) {
	    sLog.error ("No viewports found for span group, skipping");
	    continue;
	}

	sLog.debug (
	    "SPAN DEBUG prepareOutputs: bounding box=(", minX, ",", minY, ",", maxX - minX, ",", maxY - minY, ")"
	);

	WallpaperEngine::Render::CWallpaper::SpanInfo spanInfo;
	spanInfo.totalBounds = { minX, minY, maxX - minX, maxY - minY };

	// Create one shared wallpaper with the span group's scaling mode
	auto sharedWallpaper = WallpaperEngine::Render::CWallpaper::fromWallpaper (
	    *bgIt->second->wallpaper, *m_renderContext, *m_audioContext, m_webHelper.get (), spanGroup.scaling,
	    spanGroup.clamp
	);

	// Convert to shared_ptr so it can be registered for multiple viewports
	std::shared_ptr<WallpaperEngine::Render::CWallpaper> shared (std::move (sharedWallpaper));
	shared->setSpanInfo (spanInfo);

	// Register the same wallpaper for each screen in the span group
	for (const auto& screenName : spanGroup.screens) {
	    m_renderContext->setWallpaper (screenName, shared);
	}
    }
}

void WallpaperApplication::setupOpenGLDebugging () {
#if !NDEBUG
    glDebugMessageCallback (CustomGLDebugCallback, nullptr);
    glEnable (GL_DEBUG_OUTPUT_SYNCHRONOUS);
#endif
}

void WallpaperApplication::setup () {
    this->setupOutput ();
    this->setupAudio ();
    this->prepareOutputs ();
    this->setupOpenGLDebugging ();
    this->setupApi ();

    if (this->m_context.settings.general.dumpStructure) {
	auto prettyPrinter = Data::Dumpers::StringPrinter ();

	for (const auto& [background, info] : this->m_renderContext->getWallpapers ()) {
	    prettyPrinter.printWallpaper (info->getWallpaperData ());
	}

	std::cout << prettyPrinter.str () << std::endl;
    }

#if DEMOMODE
    // ensure only one background is running so everything can be properly caught
    if (this->m_renderContext->getWallpapers ().size () > 1) {
	sLog.exception ("Demo mode only supports one background");
    }

    int width = this->m_renderContext->getWallpapers ().begin ()->second->getWidth ();
    int height = this->m_renderContext->getWallpapers ().begin ()->second->getHeight ();
    std::vector<uint8_t> pixels (width * height * 3);
    bool initialized = false;
    int frame = 0;
#endif /* DEMOMODE */
}

void WallpaperApplication::render () {
    static time_t seconds;
    static struct tm* timeinfo;

    if (this->m_isPaused) {
	usleep (FULLSCREEN_CHECK_WAIT_TIME);
	if ((this->m_manualPauseRequested
	     || (this->m_context.settings.render.fullscreenBehavior == FullscreenBehavior::Pause
		 && this->m_fullScreenDetector->anythingFullscreen ()))
	    && this->m_context.state.general.keepRunning) {
	    return;
	}
	m_renderContext->setPause (false);

	// account for paused duration in playlist timers
	const auto pausedNow = std::chrono::steady_clock::now ();
	const auto pausedDuration = pausedNow - this->m_pauseStart;

	for (auto& [_, playlist] : this->m_activePlaylists) {
	    if (!playlist.definition.settings.updateOnPause) {
		playlist.nextSwitch += pausedDuration;
		playlist.lastUpdate += pausedDuration;
	    }
	}

	this->m_isPaused = false;
    } else {
	// update g_Daytime
	time (&seconds);
	timeinfo = localtime (&seconds);
	g_Daytime = static_cast<float> ((timeinfo->tm_hour * 60) + timeinfo->tm_min) / (24.0f * 60.0f);

	// keep track of the previous frame's time
	g_TimeLast = g_Time;
	{
	    static float s_accum = 0.0f;
	    static float s_lastRt = m_videoDriver->getRenderTime ();
	    const float s_scale = this->m_timescale;
	    const float rt = m_videoDriver->getRenderTime ();
	    s_accum += (rt - s_lastRt) * s_scale;
	    s_lastRt = rt;
	    g_Time = s_accum;

	    static const bool s_timeStats = getenv ("LWE_TIMESTATS") != nullptr;
	    if (s_timeStats) {
		using clk = std::chrono::steady_clock;
		static const clk::time_point s_wall0 = clk::now ();
		static float s_g0 = g_Time;
		static float s_rt0 = rt;
		static uint64_t s_frames = 0;
		static double s_lastLog = 0.0;
		s_frames++;
		const double wall = std::chrono::duration<double> (clk::now () - s_wall0).count ();
		if (wall - s_lastLog >= 5.0) {
		    const double gAdv = g_Time - s_g0;
		    const double rtAdv = rt - s_rt0;
		    sLog.out (
			"LWE-TIMESTATS wall=", static_cast<float> (wall), " g_Time_adv=", static_cast<float> (gAdv),
			" renderTime_adv=", static_cast<float> (rtAdv), " scale=", s_scale,
			" ratio_g_wall=", static_cast<float> (gAdv / wall),
			" ratio_rt_wall=", static_cast<float> (rtAdv / wall), " frames=", s_frames
		    );
		    s_lastLog = wall;
		}
	    }
	}
	// update audio recorder
	m_audioDriver->update ();
	// update the media source
	m_mediaSource->update ();
	// update input information
	m_videoDriver->getInputContext ().update ();
	// process driver events
	m_videoDriver->dispatchEventQueue ();

	if (m_videoDriver->closeRequested ()) {
	    sLog.out ("Stop requested by driver");
	    this->m_context.state.general.keepRunning = false;
	}

#if DEMOMODE
	// wait for a full render cycle before actually starting
	// this gives some extra time for video and web decoders to set themselves up
	// because of size changes
	if (m_videoDriver->getFrameCounter () > (uint32_t)this->m_context.settings.render.maximumFPS) {
	    if (!initialized) {
		width = this->m_renderContext->getWallpapers ().begin ()->second->getWidth ();
		height = this->m_renderContext->getWallpapers ().begin ()->second->getHeight ();
		pixels.reserve (width * height * 3);
		init_encoder ("output.webm", width, height);
		initialized = true;
	    }

	    glBindFramebuffer (
		GL_FRAMEBUFFER, this->m_renderContext->getWallpapers ().begin ()->second->getWallpaperFramebuffer ()
	    );

	    glPixelStorei (GL_PACK_ALIGNMENT, 1);
	    glReadPixels (0, 0, width, height, GL_RGB, GL_UNSIGNED_BYTE, pixels.data ());
	    write_video_frame (pixels.data ());
	    frame++;

	    // stop after the given framecount
	    if (frame >= FRAME_COUNT) {
		this->m_context.state.general.keepRunning = false;
	    }
	}
#endif /* DEMOMODE */
	if ((this->m_manualPauseRequested
	     || (this->m_context.settings.render.fullscreenBehavior == FullscreenBehavior::Pause
		 && this->m_fullScreenDetector->anythingFullscreen ()))
	    && this->m_context.state.general.keepRunning) {
	    this->m_isPaused = true;
	    this->m_pauseStart = std::chrono::steady_clock::now ();

	    m_renderContext->setPause (true);
	    return;
	}
    }

    this->updatePlaylists ();

    if (!this->m_context.settings.screenshot.take || this->m_screenShotTaken == true) {
	return;
    }

    if (this->m_videoDriver->getFrameCounter () < this->m_nextFrameScreenshot) {
	return;
    }

    this->takeScreenshot (this->m_context.settings.screenshot.path);
    this->m_screenShotTaken = true;
}

void WallpaperApplication::cleanup () {
    sLog.out ("Stopping");

#if DEMOMODE
    close_encoder ();
#endif /* DEMOMODE */

    SDL_Quit ();
}

void WallpaperApplication::setupApi () {
    if (!this->m_context.settings.general.apiSocket) {
	return;
    }

    this->m_commandServer = std::make_unique<Api::CommandServer> (Api::CommandServer::defaultSocketPath ());

    // Loud failure is the point: if another engine already owns the socket, refusing to
    // start beats silently receiving commands meant for it (single-instance guard).
    if (!this->m_commandServer->listen ()) {
	sLog.exception ("Cannot start the API command socket: ", this->m_commandServer->error ());
    }

    sLog.out ("API listening on ", Api::CommandServer::defaultSocketPath ().string ());
}

std::vector<int> WallpaperApplication::getApiWakeFds () const {
    if (this->m_commandServer == nullptr || !this->m_commandServer->isListening ()) {
	return {};
    }

    return this->m_commandServer->fds ();
}

void WallpaperApplication::processApiRequests () {
    if (this->m_commandServer == nullptr) {
	return;
    }

    for (const auto& request : this->m_commandServer->drain ()) {
	const auto outcome = Api::CommandDispatcher::parse (request.line);

	if (!outcome.command.has_value ()) {
	    this->m_commandServer->respond (request.client, outcome.errorResponse);
	    continue;
	}

	this->handleApiCommand (request.client, *outcome.command);

	// every verb that changes durable state re-persists it; readers/diagnostics do not
	static const std::set<std::string> MUTATING_VERBS
	    = { "show",       "rotate-set",        "next",          "prev",           "pause",
		"resume",     "set-fps",           "set-speed",     "set-volume",     "set-mouse",
		"set-audio",  "set-parallax",      "set-particles", "set-fullscreen", "set-fullscreen-ignore",
		"set-tuning", "set-app-conditions" };

	if (MUTATING_VERBS.find (outcome.command->cmd) != MUTATING_VERBS.end ()) {
	    this->persistRuntimeState ();
	}
    }
}

void WallpaperApplication::handleApiCommand (int client, const Api::Command& command) {
    if (command.cmd == "status") {
	this->m_commandServer->respond (client, Api::CommandDispatcher::done (command.id, this->apiStatus ()));
	return;
    }

    if (command.cmd == "quit") {
	// respond first: once keepRunning drops the loop unwinds and never drains again
	this->m_commandServer->respond (client, Api::CommandDispatcher::done (command.id));
	sLog.out ("API: quit requested by client");
	this->m_context.state.general.keepRunning = false;
	return;
    }

    if (command.cmd == "show") {
	this->apiShow (client, command.id, command.args["id"].get<std::string> (), command.args);
	return;
    }

    if (command.cmd == "set-skip") {
	// Diagnostic scalpel: the scene render loop consults debug.skipObjects per
	// frame, so this takes effect on the next frame with no rebuild. Scene-scoped
	// by design - apiShow clears it, since object ids collide across scenes.
	auto& skips = this->m_context.settings.render.debug.skipObjects;
	skips.clear ();

	for (const auto& entry : command.args["ids"]) {
	    skips.push_back (entry.get<int> ());
	}

	sLog.out ("API: render skip set to ", skips.size (), " object id(s)");
	this->m_commandServer->respond (
	    client, Api::CommandDispatcher::done (command.id, { { "skipped", skips.size () } })
	);
	return;
    }

    if (command.cmd == "rotate-set") {
	this->apiRotateSet (client, command.id, command.args);
	return;
    }

    if (command.cmd == "next" || command.cmd == "prev") {
	// transport verbs: ack-then-done like show - a heavy scene loads for seconds
	if (command.cmd == "prev" && this->m_showHistory.empty ()) {
	    this->m_commandServer->respond (client, Api::CommandDispatcher::failure (command.id, "history is empty"));
	    return;
	}

	this->m_commandServer->respond (client, Api::CommandDispatcher::accepted (command.id));

	std::string error;
	bool ok = false;

	if (this->m_releaseReason != ReleaseReason::Live && !this->apiAcquireOutputs (error)) {
	    this->m_commandServer->respond (client, Api::CommandDispatcher::failure (command.id, error));
	    return;
	}

	if (command.cmd == "next") {
	    ok = this->apiRotationAdvance (error);
	} else {
	    // prev consumes history (watcher parity: pop, apply, never re-push)
	    const auto entry = this->m_showHistory.back ();
	    this->m_showHistory.pop_back ();
	    const auto path = resolveLibraryBackground (entry.id);

	    if (!path.has_value () || !this->preflightWallpaper (path->string ())) {
		error = "history entry no longer resolves: " + entry.id;
	    } else if (!this->makeAnyViewportCurrent ()) {
		error = "no active viewport to switch on";
	    } else if (this->applyShowCore (*path, entry.args, false, error)) {
		this->m_apiRotation.lastShow = std::chrono::steady_clock::now ();
		this->apiRotationPredraw ();
		ok = true;
	    }
	}

	if (ok) {
	    this->m_commandServer->respond (
		client,
		Api::CommandDispatcher::done (
		    command.id, { { "id", this->m_currentShow.id }, { "ui_id", this->m_currentShow.uiId } }
		)
	    );
	} else {
	    this->m_commandServer->respond (client, Api::CommandDispatcher::failure (command.id, error));
	}

	return;
    }

    if (command.cmd == "ping") {
	this->m_lastPing = std::chrono::steady_clock::now ();
	this->m_pingSeen = true;

	if (this->m_releaseReason == ReleaseReason::Deadman) {
	    std::string acquireError;

	    if (!this->apiAcquireOutputs (acquireError)) {
		sLog.error ("API: ping-triggered re-acquire failed: ", acquireError);
	    }
	}

	this->m_commandServer->respond (client, Api::CommandDispatcher::done (command.id, { { "pong", true } }));
	return;
    }

    if (command.cmd == "pause" || command.cmd == "resume") {
	const bool paused = command.cmd == "pause";
	this->m_manualPauseRequested = paused;
	sLog.out ("API: manual pause ", paused ? "SET" : "CLEARED", " by verb");
	this->m_commandServer->respond (client, Api::CommandDispatcher::done (command.id, { { "paused", paused } }));
	return;
    }

    if (command.cmd == "set-fps") {
	// the frame cap. Live for the GL path (the driver recomputes its budget every
	// pass); a CEF wallpaper's windowless_frame_rate is fixed at browser creation,
	// so web scenes adopt the new cap on their next show, not mid-scene.
	const auto fps = command.args["fps"].get<int> ();
	this->m_context.settings.render.maximumFPS = fps;
	sLog.out ("API: fps cap set to ", fps);
	this->m_commandServer->respond (client, Api::CommandDispatcher::done (command.id, { { "fps", fps } }));
	return;
    }

    if (command.cmd == "set-speed") {
	this->setTimescale (command.args["speed"].get<float> ());
	sLog.out ("API: speed set to ", this->m_timescale);
	this->m_commandServer->respond (
	    client, Api::CommandDispatcher::done (command.id, { { "speed", this->m_timescale } })
	);
	return;
    }

    if (command.cmd == "set-tuning") {
	if (command.args.contains ("classic_k")) {
	    g_LweClassicDivisor = std::clamp (command.args["classic_k"].get<float> (), 0.01f, 1000.0f);
	}
	if (command.args.contains ("classic_exp")) {
	    g_LweFalloffExp = std::clamp (command.args["classic_exp"].get<float> (), 0.5f, 6.0f);
	}
	if (command.args.contains ("audio_gain")) {
	    g_LweAudioGain = std::clamp (command.args["audio_gain"].get<float> (), 0.1f, 20.0f);
	}
	sLog.out (
	    "API: tuning classic_k=", g_LweClassicDivisor, " classic_exp=", g_LweFalloffExp,
	    " audio_gain=", g_LweAudioGain
	);
	this->m_commandServer->respond (
	    client,
	    Api::CommandDispatcher::done (
		command.id,
		{ { "classic_k", g_LweClassicDivisor },
		  { "classic_exp", g_LweFalloffExp },
		  { "audio_gain", g_LweAudioGain } }
	    )
	);
	return;
    }

    if (command.cmd == "set-volume") {
	// live for scenes: SDLAudioDriver's mix callback reads state.audio.volume per audio
	// buffer. Both stores are written, same as applyShowArgs - settings is the resolved
	// configuration, state is what the mixer actually consults. A video wallpaper's mpv
	// volume is bound at player creation, so videos adopt this on their next show.
	const auto volume = command.args["volume"].get<int> ();
	this->m_context.settings.audio.volume = volume;
	this->m_context.state.audio.volume = volume;
	if (this->m_renderContext != nullptr) {
	    this->m_renderContext->setAudioVolume (volume);
	}
	sLog.out ("API: volume set to ", volume);
	this->m_commandServer->respond (client, Api::CommandDispatcher::done (command.id, { { "volume", volume } }));
	return;
    }

    if (command.cmd == "set-mouse") {
	// live: WaylandMouseInput consults settings.mouse.enabled per event, so the very next
	// pointer event honors it. Same store applyShowArgs writes.
	const auto enabled = command.args["enabled"].get<bool> ();
	this->m_context.settings.mouse.enabled = enabled;
	sLog.out ("API: mouse input ", enabled ? "ENABLED" : "DISABLED");
	this->m_commandServer->respond (client, Api::CommandDispatcher::done (command.id, { { "mouse", enabled } }));
	return;
    }

    if (command.cmd == "set-audio") {
	const auto enabled = command.args["enabled"].get<bool> ();
	this->m_context.settings.audio.audioprocessing = enabled;
	sLog.out ("API: audio processing ", enabled ? "ENABLED" : "DISABLED");
	this->m_commandServer->respond (client, Api::CommandDispatcher::done (command.id, { { "audio", enabled } }));
	return;
    }

    if (command.cmd == "set-instrument") {
	// Only PURE LOG GATES are in the registry. A switch that decides what gets BUILT keeps
	// reading the environment, because flipping it mid-process would disagree with objects
	// already constructed - the reply says so rather than silently accepting a no-op.
	const auto name = command.args["name"].get<std::string> ();
	const auto enabled = command.args["enabled"].get<bool> ();
	if (!Logging::instrumentKnown (name)) {
	    this->m_commandServer->respond (
		client,
		Api::CommandDispatcher::failure (
		    command.id,
		    name
			+ " is not a runtime-settable instrument (it is read "
			  "at launch, or it gates something built at scene load)"
		)
	    );
	    return;
	}
	Logging::instrumentSet (name, enabled);
	sLog.out ("API: instrument ", name, enabled ? " ENABLED" : " DISABLED");
	this->m_commandServer->respond (
	    client,
	    Api::CommandDispatcher::done (
		command.id,
		{ { "instrument", name }, { "enabled", enabled }, { "instruments", Logging::instrumentsEnabled () } }
	    )
	);
	return;
    }

    if (command.cmd == "set-parallax") {
	// genuinely live: the three readers (CImage::updateScreenSpacePosition,
	// CParticle::applyParallaxToModelMatrix, CScene::renderFrame) all consult it
	// per frame, so nothing needs rebuilding
	const auto enabled = command.args["enabled"].get<bool> ();
	this->m_context.settings.mouse.disableparallax = !enabled;
	sLog.out ("API: parallax ", enabled ? "ENABLED" : "DISABLED");
	this->m_commandServer->respond (client, Api::CommandDispatcher::done (command.id, { { "parallax", enabled } }));
	return;
    }

    if (command.cmd == "set-particles") {
	// NOT live in the same sense: the flag is read while the scene is BUILT
	// (CScene skips creating particle systems entirely), so the current wallpaper
	// has to be rebuilt for the change to show. Honest about it in the reply.
	const auto enabled = command.args["enabled"].get<bool> ();
	const bool changed = this->m_context.settings.general.disableParticles == enabled;
	this->m_context.settings.general.disableParticles = !enabled;
	bool rebuilt = false;

	// rebuilding into released outputs would rebuild scenes with nowhere to draw;
	// the flag is set, and the next acquire/show picks it up
	if (changed && this->m_releaseReason == ReleaseReason::Live && !this->m_backgrounds.empty ()) {
	    try {
		this->rebuildForCurrentBackgrounds ();
		rebuilt = true;
	    } catch (const std::exception& e) {
		sLog.error ("API: rebuild after set-particles failed: ", e.what ());
		this->m_commandServer->respond (
		    client, Api::CommandDispatcher::failure (command.id, std::string ("rebuild failed: ") + e.what ())
		);
		return;
	    }
	}

	sLog.out ("API: particles ", enabled ? "ENABLED" : "DISABLED", rebuilt ? " (scene rebuilt)" : "");
	this->m_commandServer->respond (
	    client, Api::CommandDispatcher::done (command.id, { { "particles", enabled }, { "rebuilt", rebuilt } })
	);
	return;
    }

    if (command.cmd == "set-fullscreen-ignore") {
	std::vector<std::string> ignores;

	for (const auto& entry : command.args["app_ids"]) {
	    ignores.push_back (entry.get<std::string> ());
	}

	this->m_context.settings.render.fullscreenPauseIgnoreAppIds = ignores;

	if (this->m_fullScreenDetector) {
	    this->m_fullScreenDetector->recomputeRelevance ();
	}

	sLog.out ("API: fullscreen ignore-list replaced - ", ignores.size (), " app id(s)");
	this->m_commandServer->respond (
	    client, Api::CommandDispatcher::done (command.id, { { "count", ignores.size () } })
	);
	return;
    }

    if (command.cmd == "set-fullscreen") {
	const auto parsed = parseFullscreenBehavior (command.args["behavior"].get<std::string> ());

	if (!parsed.has_value ()) {
	    this->m_commandServer->respond (
		client, Api::CommandDispatcher::failure (command.id, "behavior must be off, pause or stop")
	    );
	    return;
	}

	this->m_context.settings.render.fullscreenBehavior = *parsed;
	// keep the launch default in step so a later show that OMITS the arg does not
	// resurrect the old policy
	this->m_showDefaults.fullscreenBehavior = *parsed;
	sLog.out ("API: fullscreen behavior set to ", fullscreenBehaviorName (*parsed));
	this->m_commandServer->respond (
	    client,
	    Api::CommandDispatcher::done (command.id, { { "fullscreen_behavior", fullscreenBehaviorName (*parsed) } })
	);
	return;
    }

    if (command.cmd == "set-app-conditions") {
	auto& cond = this->m_appCondition;
	cond.names.clear ();

	if (command.args.contains ("names")) {
	    for (const auto& entry : command.args["names"]) {
		cond.names.push_back (entry.get<std::string> ());
	    }
	}

	cond.behavior = command.args["behavior"].get<std::string> ();
	// zero the poll clock so the new conditions are evaluated on the next pass,
	// not up to a full poll interval later
	cond.lastPoll = {};
	sLog.out ("API: app conditions set - ", cond.names.size (), " name(s), behavior ", cond.behavior);
	this->m_commandServer->respond (
	    client,
	    Api::CommandDispatcher::done (
		command.id, { { "count", cond.names.size () }, { "behavior", cond.behavior } }
	    )
	);
	return;
    }

    if (command.cmd == "release-outputs" || command.cmd == "acquire-outputs") {
	std::string error;
	bool ok;

	// audit trail: these two verbs blank or restore the user's desktop, so the log
	// must be able to answer "who asked for that" after the fact
	sLog.out ("API: ", command.cmd, " requested by ", Api::CommandServer::peerDescription (client));

	if (command.cmd == "release-outputs") {
	    ok = this->apiReleaseOutputs (ReleaseReason::Verb, error);
	} else {
	    ok = this->apiAcquireOutputs (error);
	}

	if (ok) {
	    this->m_commandServer->respond (
		client,
		Api::CommandDispatcher::done (
		    command.id, { { "outputs", this->m_releaseReason == ReleaseReason::Live ? "live" : "released" } }
		)
	    );
	} else {
	    this->m_commandServer->respond (client, Api::CommandDispatcher::failure (command.id, error));
	}

	return;
    }

    if (command.cmd == "list-objects") {
	nlohmann::json result = nlohmann::json::object ();
	nlohmann::json objects = nlohmann::json::array ();

	for (const auto& [screen, project] : this->m_backgrounds) {
	    if (!project->wallpaper->is<Scene> ()) {
		continue;
	    }

	    for (const auto& object : project->wallpaper->as<Scene> ()->objects) {
		if (object == nullptr) {
		    continue;
		}

		nlohmann::json entry = { { "id", object->id }, { "name", object->name } };

		// image objects carry their effect chain; ids here drive show's skip_effects
		if (object->is<Image> ()) {
		    nlohmann::json effects = nlohmann::json::array ();

		    for (const auto& effect : object->as<Image> ()->effects) {
			if (effect != nullptr) {
			    effects.push_back ({ { "id", effect->id }, { "name", effect->name } });
			}
		    }

		    if (!effects.empty ()) {
			entry["effects"] = effects;
		    }
		}

		objects.push_back (entry);
	    }

	    break; // mirror groups share one scene; one screen's list is the list
	}

	result["objects"] = objects;
	result["skipped"] = this->m_context.settings.render.debug.skipObjects;
	this->m_commandServer->respond (client, Api::CommandDispatcher::done (command.id, result));
	return;
    }

    // unreachable while the dispatcher's verb whitelist matches this chain; loud if they drift
    this->m_commandServer->respond (
	client, Api::CommandDispatcher::failure (command.id, "verb accepted but not implemented: " + command.cmd)
    );
}

nlohmann::json WallpaperApplication::apiStatus () const {
    nlohmann::json screens = nlohmann::json::object ();

    for (const auto& [screen, path] : this->m_context.settings.general.screenBackgrounds) {
	if (screen.rfind ("span:", 0) == 0) {
	    continue;
	}

	screens[screen] = path.string ();
    }

    const auto uptime
	= std::chrono::duration_cast<std::chrono::seconds> (std::chrono::steady_clock::now () - this->m_startTime);

    nlohmann::json result = nlohmann::json::object ();
    result["api"] = 1;
    result["pid"] = getpid ();
    result["uptime_s"] = uptime.count ();
    result["screens"] = screens;
    result["manual_pause"] = this->m_manualPauseRequested.load ();
    result["classic_k"] = g_LweClassicDivisor;
    result["classic_exp"] = g_LweFalloffExp;
    result["audio_gain"] = g_LweAudioGain;
    result["clients"] = this->m_commandServer->clientCount ();
    result["cc"] = { this->m_colorCorrection.x, this->m_colorCorrection.y, this->m_colorCorrection.z,
		     this->m_colorCorrection.w };
    result["speed"] = this->m_timescale;

    std::string currentId = this->m_currentShow.id;

    if (currentId.empty ()) {
	for (const auto& [screen, path] : this->m_context.settings.general.screenBackgrounds) {
	    if (screen.rfind ("span:", 0) != 0 && !path.empty ()) {
		currentId = path.filename ().string ();
		break;
	    }
	}
    }

    result["current"] = { { "id", currentId }, { "ui_id", this->m_currentShow.uiId } };
    result["outputs"] = { { "state", this->m_releaseReason == ReleaseReason::Live ? "live" : "released" },
			  { "reason",
			    this->m_releaseReason == ReleaseReason::Deadman            ? "deadman"
				: this->m_releaseReason == ReleaseReason::Verb         ? "verb"
				: this->m_releaseReason == ReleaseReason::Fullscreen   ? "fullscreen"
				: this->m_releaseReason == ReleaseReason::AppCondition ? "app"
										       : "" },
			  { "deadman_s", this->m_deadmanSeconds },
			  { "ping_seen", this->m_pingSeen } };

    const auto& rot = this->m_apiRotation;
    int nextIn = -1;

    if (rot.frozenRemainingSeconds >= 0) {
	nextIn = rot.frozenRemainingSeconds;
    } else if (rot.enabled) {
	const auto elapsed
	    = std::chrono::duration_cast<std::chrono::seconds> (std::chrono::steady_clock::now () - rot.lastShow);
	nextIn = std::max (0, rot.intervalSeconds - static_cast<int> (elapsed.count ()));
    }

    std::string nextUp;

    if (rot.nextPick != SIZE_MAX && rot.nextPick < rot.entries.size ()) {
	const auto& entry = rot.entries[rot.nextPick];
	nextUp = entry.uiId.empty () ? entry.id : entry.uiId;
    }

    result["rotation"] = { { "enabled", rot.enabled },       { "interval_s", rot.intervalSeconds },
			   { "next_in_s", nextIn },          { "order", rot.order },
			   { "count", rot.entries.size () }, { "next_up", nextUp },
			   { "label", rot.label },           { "history_depth", this->m_showHistory.size () } };

    result["app_condition"] = { { "behavior", this->m_appCondition.behavior },
				{ "count", this->m_appCondition.names.size () },
				{ "engaged", this->m_appCondition.pauseEngaged || this->appConditionStopEngaged () } };
    result["volume"] = this->m_context.settings.audio.volume;
    result["audio_processing"] = this->m_context.settings.audio.audioprocessing;
    result["mouse"] = this->m_context.settings.mouse.enabled;
    result["automute"] = this->m_context.settings.audio.automute;
    result["fps"] = this->m_context.settings.render.maximumFPS;
    result["frames"] = this->m_videoDriver != nullptr ? this->m_videoDriver->getFrameCounter () : 0;
    result["parallax"] = !this->m_context.settings.mouse.disableparallax;
    result["particles"] = !this->m_context.settings.general.disableParticles;
    result["fullscreen_ignore"] = this->m_context.settings.render.fullscreenPauseIgnoreAppIds;
    result["fullscreen_behavior"] = fullscreenBehaviorName (this->m_context.settings.render.fullscreenBehavior);
    // which log instruments are live right now (set-instrument). Launch-time switches are
    // NOT listed - they are not in the registry, precisely because they cannot be changed.
    result["instruments"] = Logging::instrumentsEnabled ();
    result["fullscreen_pause"] = this->m_context.settings.render.fullscreenBehavior != FullscreenBehavior::Off;

    for (const auto& [screen, bg] : this->m_context.settings.general.screenBackgrounds) {
	if (screen.rfind ("span:", 0) == 0) {
	    continue;
	}

	const auto scalingIt = this->m_context.settings.general.screenScalings.find (screen);
	const auto scaling = scalingIt != this->m_context.settings.general.screenScalings.end ()
	    ? scalingIt->second
	    : this->m_context.settings.render.window.scalingMode;
	const auto clampIt = this->m_context.settings.general.screenClamps.find (screen);
	const auto clamp = clampIt != this->m_context.settings.general.screenClamps.end ()
	    ? clampIt->second
	    : this->m_context.settings.render.window.clamp;

	switch (scaling) {
	    case WallpaperEngine::Render::WallpaperState::TextureUVsScaling::StretchUVs:
		result["scaling"] = "stretch";
		break;
	    case WallpaperEngine::Render::WallpaperState::TextureUVsScaling::ZoomFitUVs:
		result["scaling"] = "fit";
		break;
	    case WallpaperEngine::Render::WallpaperState::TextureUVsScaling::ZoomFillUVs:
		result["scaling"] = "fill";
		break;
	    default:
		result["scaling"] = "default";
		break;
	}

	if (clamp == TextureFlags_ClampUVs) {
	    result["clamp"] = "clamp";
	} else if (clamp == TextureFlags_ClampUVsBorder) {
	    result["clamp"] = "border";
	} else {
	    result["clamp"] = "repeat";
	}

	// all-outputs show semantics: every non-span screen carries the same values
	break;
    }

    return result;
}

std::optional<std::filesystem::path> WallpaperApplication::resolveLibraryBackground (const std::string& backgroundId) {
    // the lwe library first, then the Steam workshop; nothing else, and never a raw path
    std::vector<std::filesystem::path> roots;

    if (const char* dataHome = getenv ("XDG_DATA_HOME"); dataHome != nullptr) {
	roots.emplace_back (std::filesystem::path (dataHome) / "lwe" / "wallpapers");
    }

    if (const char* home = getenv ("HOME"); home != nullptr) {
	roots.emplace_back (std::filesystem::path (home) / ".local" / "share" / "lwe" / "wallpapers");
    }

    for (const auto& root : roots) {
	const auto candidate = root / backgroundId;

	if (std::filesystem::is_directory (candidate) && std::filesystem::exists (candidate / "project.json")) {
	    return candidate;
	}
    }

    try {
	// 431960 = Wallpaper Engine's app id, same value translateBackground uses
	return Steam::FileSystem::workshopDirectory (431960, backgroundId);
    } catch (const std::exception&) {
	return std::nullopt;
    }
}

void WallpaperApplication::rebuildForCurrentBackgrounds () {
    // teardown order matters: the renderers reference project data, so they die first
    this->m_renderContext->clearWallpapers ();
    this->m_backgrounds.clear ();

    const auto evicted = this->m_renderContext->evictUnusedTextures ();

    this->loadBackgrounds ();
    this->setupProperties ();

    for (const auto& [screen, project] : this->m_backgrounds) {
	this->ensureBrowserForProject (*project);
	this->ensureAudioForProject (*project);
    }

    this->buildWallpapers ();

#ifdef __GLIBC__
    // scene churn (especially mpv/FFmpeg teardown) leaves freed-but-unreturned heap
    // pages; hand what is reclaimable back to the OS at the natural quiet point
    malloc_trim (0);
#endif

    sLog.out ("API: rebuild complete, evicted ", evicted, " cached textures");
}

void WallpaperApplication::apiShow (
    int client, int64_t requestId, const std::string& backgroundId, const nlohmann::json& args
) {
    const auto path = resolveLibraryBackground (backgroundId);

    if (!path.has_value ()) {
	this->m_commandServer->respond (
	    client, Api::CommandDispatcher::failure (requestId, "background not found in library: " + backgroundId)
	);
	return;
    }

    if (!this->preflightWallpaper (path->string ())) {
	this->m_commandServer->respond (
	    client, Api::CommandDispatcher::failure (requestId, "background failed preflight: " + backgroundId)
	);
	return;
    }

    if (this->m_releaseReason != ReleaseReason::Live) {
	std::string acquireError;

	if (!this->apiAcquireOutputs (acquireError)) {
	    this->m_commandServer->respond (client, Api::CommandDispatcher::failure (requestId, acquireError));
	    return;
	}
    }

    if (!this->makeAnyViewportCurrent ()) {
	this->m_commandServer->respond (
	    client, Api::CommandDispatcher::failure (requestId, "no active viewport to switch on")
	);
	return;
    }

    this->m_commandServer->respond (client, Api::CommandDispatcher::accepted (requestId));

    std::string error;

    if (!this->applyShowCore (*path, args, true, error)) {
	this->m_commandServer->respond (
	    client, Api::CommandDispatcher::failure (requestId, std::string ("show failed: ") + error)
	);
	return;
    }

    // a manual show restarts the rotation countdown (watcher parity: show-request
    // stamped the rotation clock) and refreshes the pre-drawn next_up
    this->m_apiRotation.lastShow = std::chrono::steady_clock::now ();

    if (this->m_apiRotation.frozenRemainingSeconds >= 0) {
	this->m_apiRotation.frozenRemainingSeconds = this->m_apiRotation.intervalSeconds;
    }

    this->apiRotationPredraw ();
    this->m_commandServer->respond (client, Api::CommandDispatcher::done (requestId, { { "path", path->string () } }));
}

bool WallpaperApplication::applyShowCore (
    const std::filesystem::path& path, const nlohmann::json& args, const bool recordHistory, std::string& error
) {
    this->m_context.settings.render.debug.skipObjects.clear ();

    if (args.contains ("skip_objects")) {
	auto& skips = this->m_context.settings.render.debug.skipObjects;

	for (const auto& entry : args["skip_objects"]) {
	    skips.push_back (entry.get<int> ());
	}

	sLog.out ("API: hiding ", skips.size (), " object id(s) for this wallpaper");
    }

    // skip_effects rides the show because effects are consumed at scene BUILD time:
    // this rebuild constructs the scene without them, and the next plain show restores
    // the full chain. Diagnostic contract, same family as set-skip.
    auto& skipFx = this->m_context.settings.render.debug.skipEffects;
    skipFx.clear ();

    if (args.contains ("skip_effects")) {
	for (const auto& entry : args["skip_effects"]) {
	    skipFx.push_back (entry.get<int> ());
	}

	sLog.out ("API: building without ", skipFx.size (), " effect id(s)");
    }

    // keep the previous assignment for rollback
    const auto previousBackgrounds = this->m_context.settings.general.screenBackgrounds;
    const auto previousDefault = this->m_context.settings.general.defaultBackground;
    const auto previousCC = this->m_colorCorrection;
    const auto previousTimescale = this->m_timescale;
    const auto previousProperties = this->m_context.settings.general.properties;
    const auto previousScalings = this->m_context.settings.general.screenScalings;
    const auto previousClamps = this->m_context.settings.general.screenClamps;
    const auto previousVolume = this->m_context.settings.audio.volume;
    const auto previousAudioProcessing = this->m_context.settings.audio.audioprocessing;
    const auto previousMouse = this->m_context.settings.mouse.enabled;
    const auto previousAutomute = this->m_context.settings.audio.automute;
    const auto previousFullscreenBehavior = this->m_context.settings.render.fullscreenBehavior;

    if (args.contains ("cc")) {
	this->setColorCorrection (
	    { args["cc"][0].get<float> (), args["cc"][1].get<float> (), args["cc"][2].get<float> (),
	      args["cc"][3].get<float> () }
	);
    }

    if (args.contains ("speed")) {
	this->setTimescale (args["speed"].get<float> ());
    }

    auto& propertyOverrides = this->m_context.settings.general.properties;
    propertyOverrides.clear ();

    if (args.contains ("properties")) {
	for (const auto& [key, value] : args["properties"].items ()) {
	    propertyOverrides[key] = value.is_string () ? value.get<std::string> () : value.dump ();
	}

	sLog.out ("API: applying ", propertyOverrides.size (), " property override(s)");
    }

    const auto boolArg = [&args] (const char* key, const bool fallback) -> bool {
	return args.contains (key) ? args[key].get<bool> () : fallback;
    };

    this->m_context.settings.audio.volume
	= args.contains ("volume") ? args["volume"].get<int> () : this->m_showDefaults.volume;
    this->m_context.state.audio.volume = this->m_context.settings.audio.volume;
    this->m_context.settings.audio.audioprocessing = boolArg ("audio_processing", this->m_showDefaults.audioProcessing);
    this->m_context.settings.mouse.enabled = boolArg ("mouse", this->m_showDefaults.mouseEnabled);
    this->m_context.settings.audio.automute = boolArg ("automute", this->m_showDefaults.automute);
    this->m_context.settings.render.fullscreenBehavior = this->m_showDefaults.fullscreenBehavior;

    if (args.contains ("fullscreen_pause")) {
	this->m_context.settings.render.fullscreenBehavior
	    = args["fullscreen_pause"].get<bool> () ? FullscreenBehavior::Pause : FullscreenBehavior::Off;
    }

    if (args.contains ("fullscreen_behavior")) {
	if (const auto parsed = parseFullscreenBehavior (args["fullscreen_behavior"].get<std::string> ());
	    parsed.has_value ()) {
	    this->m_context.settings.render.fullscreenBehavior = *parsed;
	}
    }

    // scaling/clamp are part of the mirror-group key (path|scaling|clamp), so they must
    // land in the per-screen maps BEFORE buildWallpapers re-derives the groups. One value
    // for every non-span screen (all-outputs show semantics); omitted restores the launch
    // maps wholesale. Span groups keep their own authored scaling.
    if (args.contains ("scaling")) {
	const auto& mode = args["scaling"].get<std::string> ();
	auto scaling = WallpaperEngine::Render::WallpaperState::TextureUVsScaling::DefaultUVs;

	if (mode == "stretch") {
	    scaling = WallpaperEngine::Render::WallpaperState::TextureUVsScaling::StretchUVs;
	} else if (mode == "fit") {
	    scaling = WallpaperEngine::Render::WallpaperState::TextureUVsScaling::ZoomFitUVs;
	} else if (mode == "fill") {
	    scaling = WallpaperEngine::Render::WallpaperState::TextureUVsScaling::ZoomFillUVs;
	}

	for (const auto& [screen, bg] : this->m_context.settings.general.screenBackgrounds) {
	    if (screen.rfind ("span:", 0) != 0) {
		this->m_context.settings.general.screenScalings[screen] = scaling;
	    }
	}
    } else {
	this->m_context.settings.general.screenScalings = this->m_showDefaults.screenScalings;
    }

    if (args.contains ("clamp")) {
	const auto& mode = args["clamp"].get<std::string> ();
	TextureFlags clamp = TextureFlags_NoFlags;

	if (mode == "clamp") {
	    clamp = TextureFlags_ClampUVs;
	} else if (mode == "border") {
	    clamp = TextureFlags_ClampUVsBorder;
	}

	for (const auto& [screen, bg] : this->m_context.settings.general.screenBackgrounds) {
	    if (screen.rfind ("span:", 0) != 0) {
		this->m_context.settings.general.screenClamps[screen] = clamp;
	    }
	}
    } else {
	this->m_context.settings.general.screenClamps = this->m_showDefaults.screenClamps;
    }

    try {
	for (auto& [screen, bg] : this->m_context.settings.general.screenBackgrounds) {
	    if (screen.rfind ("span:", 0) == 0) {
		continue;
	    }

	    bg = path;
	}

	this->m_context.settings.general.defaultBackground = path;

	this->rebuildForCurrentBackgrounds ();
    } catch (const std::exception& e) {
	sLog.error ("API: show failed for ", path.string (), ": ", e.what (), "; rolling back");

	this->m_context.settings.general.screenBackgrounds = previousBackgrounds;
	this->m_context.settings.general.defaultBackground = previousDefault;
	this->m_colorCorrection = previousCC;
	this->m_timescale = previousTimescale;
	this->m_context.settings.general.properties = previousProperties;
	this->m_context.settings.general.screenScalings = previousScalings;
	this->m_context.settings.general.screenClamps = previousClamps;
	this->m_context.settings.audio.volume = previousVolume;
	this->m_context.state.audio.volume = previousVolume;
	this->m_context.settings.audio.audioprocessing = previousAudioProcessing;
	this->m_context.settings.mouse.enabled = previousMouse;
	this->m_context.settings.audio.automute = previousAutomute;
	this->m_context.settings.render.fullscreenBehavior = previousFullscreenBehavior;

	try {
	    this->rebuildForCurrentBackgrounds ();
	} catch (const std::exception& rollbackError) {
	    sLog.error ("API: rollback ALSO failed: ", rollbackError.what ());
	}

	error = e.what ();
	return false;
    }

    // the swap is live: record identity + history. History keeps the FULL show record
    // (args included) so `prev` restores the wallpaper's look, not just its id. prev
    // itself applies with recordHistory=false - it consumes history, never grows it.
    if (recordHistory && !this->m_currentShow.id.empty ()) {
	this->m_showHistory.push_back (this->m_currentShow);

	while (this->m_showHistory.size () > 20) {
	    this->m_showHistory.pop_front ();
	}
    }

    this->m_currentShow = { .id = path.filename ().string (),
			    .uiId = args.contains ("ui_id") ? args["ui_id"].get<std::string> () : "",
			    .args = args };
    return true;
}

namespace {
/** avoid-repeat and next_up compare by the id the USER sees: the UI identity when the
 *  entry is a preset (several presets share one base), the engine id otherwise */
std::string displayId (const std::string& id, const std::string& uiId) { return uiId.empty () ? id : uiId; }
} // namespace

void WallpaperApplication::apiRotateSet (int client, int64_t requestId, const nlohmann::json& args) {
    this->applyRotateSet (args);
    this->m_commandServer->respond (
	client,
	Api::CommandDispatcher::done (
	    requestId, { { "count", this->m_apiRotation.entries.size () }, { "enabled", this->m_apiRotation.enabled } }
	)
    );
}

void WallpaperApplication::applyRotateSet (const nlohmann::json& args) {
    auto& rot = this->m_apiRotation;

    std::vector<std::string> previousIds;
    previousIds.reserve (rot.entries.size ());

    for (const auto& entry : rot.entries) {
	previousIds.push_back (displayId (entry.id, entry.uiId));
    }

    const bool wasEnabled = rot.enabled;
    const int previousInterval = rot.intervalSeconds;
    const int frozen = rot.frozenRemainingSeconds;

    rot.entries.clear ();

    if (args.contains ("entries")) {
	for (const auto& entry : args["entries"]) {
	    rot.entries.push_back (
		{ .id = entry["id"].get<std::string> (),
		  .uiId = entry.contains ("ui_id") ? entry["ui_id"].get<std::string> () : "",
		  .args = entry }
	    );
	}
    }

    rot.intervalSeconds = args.contains ("interval_s") ? args["interval_s"].get<int> () : 900;
    rot.order = args.contains ("order") ? args["order"].get<std::string> () : "shuffle";
    rot.avoidRepeat = !args.contains ("avoid_repeat") || args["avoid_repeat"].get<bool> ();
    rot.enabled = args.contains ("enabled") && args["enabled"].get<bool> () && !rot.entries.empty ();
    rot.label = args.contains ("label") ? args["label"].get<std::string> () : "";
    rot.perm.clear ();
    rot.permIndex = 0;
    rot.seqIndex = -1;
    rot.nextPick = SIZE_MAX;

    std::vector<std::string> newIds;
    newIds.reserve (rot.entries.size ());

    for (const auto& entry : rot.entries) {
	newIds.push_back (displayId (entry.id, entry.uiId));
    }

    const bool sameSet = newIds == previousIds && rot.intervalSeconds == previousInterval;

    if (!rot.enabled) {
	rot.frozenRemainingSeconds = sameSet && frozen >= 0 ? frozen : rot.intervalSeconds;
    } else if (sameSet && !wasEnabled && frozen >= 0) {
	rot.lastShow = std::chrono::steady_clock::now () - std::chrono::seconds (rot.intervalSeconds - frozen);
	rot.frozenRemainingSeconds = -1;
    } else {
	rot.lastShow = std::chrono::steady_clock::now ();
	rot.frozenRemainingSeconds = -1;
    }

    this->apiRotationPredraw ();
    sLog.out (
	"API: rotation set replaced - ", rot.entries.size (), " entries, interval ", rot.intervalSeconds, "s, order ",
	rot.order, rot.enabled ? ", ENABLED" : ", disabled"
    );
}

namespace {
constexpr int RUNTIME_STATE_VERSION = 1;
constexpr int BOOT_SURVIVED_SECONDS = 60;
constexpr int BOOT_HISTORY_DEPTH = 3;

// temp + rename on the same filesystem: a reader (or a crash mid-write) sees the old
// file or the new one, never a half-written one
bool atomicWriteJson (const std::filesystem::path& path, const nlohmann::json& contents) {
    std::error_code ec;
    std::filesystem::create_directories (path.parent_path (), ec);
    const std::string tmp = path.string () + ".tmp";
    std::ofstream out (tmp, std::ios::trunc);

    if (!out.is_open ()) {
	return false;
    }

    out << contents.dump (2) << '\n';
    out.flush ();

    if (!out.good ()) {
	out.close ();
	std::remove (tmp.c_str ());
	return false;
    }

    out.close ();

    return std::rename (tmp.c_str (), path.string ().c_str ()) == 0;
}

std::optional<nlohmann::json> readJsonFile (const std::filesystem::path& path) {
    std::ifstream in (path);

    if (!in.is_open ()) {
	return std::nullopt;
    }

    try {
	nlohmann::json parsed;
	in >> parsed;
	return parsed;
    } catch (const std::exception&) {
	return std::nullopt;
    }
}
} // namespace

std::filesystem::path WallpaperApplication::runtimeStateDir () {
    // state, not config: the panel owns ~/.config/lwe and the engine must never write it
    const char* xdgState = std::getenv ("XDG_STATE_HOME");

    if (xdgState != nullptr && xdgState[0] != '\0') {
	return std::filesystem::path (xdgState) / "lwe";
    }

    const char* home = std::getenv ("HOME");

    return std::filesystem::path (home != nullptr ? home : "") / ".local" / "state" / "lwe";
}

void WallpaperApplication::persistRuntimeState () const {
    nlohmann::json state = nlohmann::json::object ();
    state["version"] = RUNTIME_STATE_VERSION;
    state["current"] = { { "id", this->m_currentShow.id },
			 { "ui_id", this->m_currentShow.uiId },
			 { "args", this->m_currentShow.args } };

    nlohmann::json entries = nlohmann::json::array ();

    for (const auto& entry : this->m_apiRotation.entries) {
	entries.push_back (entry.args);
    }

    state["rotation"] = { { "entries", entries },
			  { "interval_s", this->m_apiRotation.intervalSeconds },
			  { "order", this->m_apiRotation.order },
			  { "avoid_repeat", this->m_apiRotation.avoidRepeat },
			  { "enabled", this->m_apiRotation.enabled },
			  { "label", this->m_apiRotation.label },
			  { "frozen_remaining_s", this->m_apiRotation.frozenRemainingSeconds } };
    state["paused"] = this->m_manualPauseRequested.load ();
    state["fps"] = this->m_context.settings.render.maximumFPS;
    state["volume"] = this->m_context.settings.audio.volume;
    state["audio_processing"] = this->m_context.settings.audio.audioprocessing;
    state["automute"] = this->m_context.settings.audio.automute;
    state["mouse"] = this->m_context.settings.mouse.enabled;
    state["parallax"] = !this->m_context.settings.mouse.disableparallax;
    state["particles"] = !this->m_context.settings.general.disableParticles;
    state["speed"] = this->m_timescale;
    state["cc"] = { this->m_colorCorrection.x, this->m_colorCorrection.y, this->m_colorCorrection.z,
		    this->m_colorCorrection.w };
    state["fullscreen_behavior"] = fullscreenBehaviorName (this->m_context.settings.render.fullscreenBehavior);
    state["fullscreen_ignore"] = this->m_context.settings.render.fullscreenPauseIgnoreAppIds;
    state["tuning"] = { { "classic_k", g_LweClassicDivisor },
			{ "classic_exp", g_LweFalloffExp },
			{ "audio_gain", g_LweAudioGain } };
    state["app_condition"] = { { "names", this->m_appCondition.names }, { "behavior", this->m_appCondition.behavior } };

    static bool warned = false;

    // warn once: every mutating verb retries this write, and a persistent failure
    // (read-only filesystem, quota) would otherwise flood the journal
    if (!atomicWriteJson (runtimeStateDir () / "engine-state.json", state) && !warned) {
	warned = true;
	sLog.error ("state persist failed: cannot write ", (runtimeStateDir () / "engine-state.json").string ());
    }
}

void WallpaperApplication::restoreRuntimeState () {
    if (!this->m_context.settings.general.daemonMode) {
	return;
    }

    // CLI-specified backgrounds are an explicit instruction; restore must never fight them
    for (const auto& [screen, path] : this->m_context.settings.general.screenBackgrounds) {
	if (!path.empty ()) {
	    return;
	}
    }

    const auto historyPath = runtimeStateDir () / "boot-history.json";
    auto history = readJsonFile (historyPath).value_or (nlohmann::json::array ());

    if (!history.is_array ()) {
	history = nlohmann::json::array ();
    }

    const size_t n = history.size ();
    const bool crashLooping = n >= 2 && history[n - 1].is_object () && history[n - 2].is_object ()
	&& !history[n - 1].value ("survived", true) && !history[n - 2].value ("survived", true);

    history.push_back ({ { "t", static_cast<int64_t> (std::time (nullptr)) }, { "survived", false } });

    while (history.size () > BOOT_HISTORY_DEPTH) {
	history.erase (history.begin ());
    }

    atomicWriteJson (historyPath, history);
    this->m_bootHistoryArmed = true;

    if (crashLooping) {
	sLog.error (
	    "state restore REFUSED: the last two boots died within ", BOOT_SURVIVED_SECONDS, "s of starting; ",
	    "booting idle (delete ", historyPath.string (), " to re-arm)"
	);
	return;
    }

    const auto stateFile = readJsonFile (runtimeStateDir () / "engine-state.json");

    if (!stateFile.has_value () || !stateFile->is_object ()
	|| stateFile->value ("version", 0) != RUNTIME_STATE_VERSION) {
	return;
    }

    const auto& state = *stateFile;

    try {
	if (state.contains ("tuning") && state["tuning"].is_object ()) {
	    const auto& tuning = state["tuning"];

	    if (tuning.contains ("classic_k")) {
		g_LweClassicDivisor = std::clamp (tuning["classic_k"].get<float> (), 0.01f, 1000.0f);
	    }

	    if (tuning.contains ("classic_exp")) {
		g_LweFalloffExp = std::clamp (tuning["classic_exp"].get<float> (), 0.5f, 6.0f);
	    }

	    if (tuning.contains ("audio_gain")) {
		g_LweAudioGain = std::clamp (tuning["audio_gain"].get<float> (), 0.1f, 20.0f);
	    }
	}

	if (state.contains ("fps")) {
	    this->m_context.settings.render.maximumFPS = state["fps"].get<int> ();
	}

	if (state.contains ("volume")) {
	    const auto volume = state["volume"].get<int> ();
	    this->m_context.settings.audio.volume = volume;
	    this->m_context.state.audio.volume = volume;

	    if (this->m_renderContext != nullptr) {
		this->m_renderContext->setAudioVolume (volume);
	    }
	}

	if (state.contains ("audio_processing")) {
	    this->m_context.settings.audio.audioprocessing = state["audio_processing"].get<bool> ();
	}

	if (state.contains ("automute")) {
	    this->m_context.settings.audio.automute = state["automute"].get<bool> ();
	}

	if (state.contains ("mouse")) {
	    this->m_context.settings.mouse.enabled = state["mouse"].get<bool> ();
	}

	if (state.contains ("parallax")) {
	    this->m_context.settings.mouse.disableparallax = !state["parallax"].get<bool> ();
	}

	if (state.contains ("particles")) {
	    // restore runs before the show below, so the scene BUILDS with the flag;
	    // no rebuild step is owed here
	    this->m_context.settings.general.disableParticles = !state["particles"].get<bool> ();
	}

	if (state.contains ("speed")) {
	    this->setTimescale (state["speed"].get<float> ());
	}

	if (state.contains ("cc") && state["cc"].is_array () && state["cc"].size () == 4) {
	    this->setColorCorrection (
		{ state["cc"][0].get<float> (), state["cc"][1].get<float> (), state["cc"][2].get<float> (),
		  state["cc"][3].get<float> () }
	    );
	}

	if (state.contains ("fullscreen_behavior")) {
	    // --daemon forces Off at parse time; the persisted policy wins on restore,
	    // and the launch default follows (same pairing as the set-fullscreen verb)
	    if (const auto parsed = parseFullscreenBehavior (state["fullscreen_behavior"].get<std::string> ());
		parsed.has_value ()) {
		this->m_context.settings.render.fullscreenBehavior = *parsed;
		this->m_showDefaults.fullscreenBehavior = *parsed;
	    }
	}

	if (state.contains ("fullscreen_ignore") && state["fullscreen_ignore"].is_array ()) {
	    std::vector<std::string> ignores;

	    for (const auto& entry : state["fullscreen_ignore"]) {
		ignores.push_back (entry.get<std::string> ());
	    }

	    this->m_context.settings.render.fullscreenPauseIgnoreAppIds = ignores;

	    if (this->m_fullScreenDetector) {
		this->m_fullScreenDetector->recomputeRelevance ();
	    }
	}

	if (state.contains ("app_condition") && state["app_condition"].is_object ()) {
	    const auto& cond = state["app_condition"];
	    this->m_appCondition.names.clear ();

	    for (const auto& entry : cond.value ("names", nlohmann::json::array ())) {
		if (entry.is_string ()) {
		    this->m_appCondition.names.push_back (entry.get<std::string> ());
		}
	    }

	    this->m_appCondition.behavior = cond.value ("behavior", "off");
	}

	if (state.contains ("rotation") && state["rotation"].is_object ()) {
	    const auto& rotation = state["rotation"];
	    nlohmann::json rotateArgs = nlohmann::json::object ();
	    rotateArgs["entries"] = rotation.value ("entries", nlohmann::json::array ());
	    rotateArgs["interval_s"] = rotation.value ("interval_s", 900);
	    rotateArgs["order"] = rotation.value ("order", "shuffle");
	    rotateArgs["avoid_repeat"] = rotation.value ("avoid_repeat", true);
	    rotateArgs["enabled"] = rotation.value ("enabled", false);
	    rotateArgs["label"] = rotation.value ("label", "");
	    this->applyRotateSet (rotateArgs);

	    const int frozen = rotation.value ("frozen_remaining_s", -1);

	    if (frozen >= 0 && !this->m_apiRotation.enabled) {
		this->m_apiRotation.frozenRemainingSeconds = frozen;
	    }
	}

	const std::string showId
	    = state.contains ("current") && state["current"].is_object () ? state["current"].value ("id", "") : "";

	if (!showId.empty ()) {
	    // same recipe as the prev verb: resolve, preflight, current viewport, core
	    const auto path = resolveLibraryBackground (showId);
	    std::string error;

	    if (!path.has_value () || !this->preflightWallpaper (path->string ())) {
		sLog.error ("state restore: persisted background no longer resolves: ", showId);
	    } else if (!this->makeAnyViewportCurrent ()) {
		sLog.error ("state restore: no active viewport to restore onto");
	    } else if (!this->applyShowCore (
			   *path, state["current"].value ("args", nlohmann::json::object ()), false, error
		       )) {
		sLog.error ("state restore: show failed: ", error);
	    } else {
		this->m_apiRotation.lastShow = std::chrono::steady_clock::now ();
		this->apiRotationPredraw ();
		sLog.out (
		    "state restore: showing ", showId, " (rotation ",
		    this->m_apiRotation.enabled ? "enabled" : "disabled", ", ", this->m_apiRotation.entries.size (),
		    " entries)"
		);
	    }
	}

	this->m_manualPauseRequested = state.value ("paused", false);
    } catch (const std::exception& e) {
	sLog.error ("state restore failed: ", e.what ());
    }
}

void WallpaperApplication::markBootSurvived () {
    if (this->m_bootSurvivedMarked || !this->m_bootHistoryArmed) {
	return;
    }

    const auto uptime
	= std::chrono::duration_cast<std::chrono::seconds> (std::chrono::steady_clock::now () - this->m_startTime);

    if (uptime.count () < BOOT_SURVIVED_SECONDS) {
	return;
    }

    this->m_bootSurvivedMarked = true;
    const auto historyPath = runtimeStateDir () / "boot-history.json";
    auto history = readJsonFile (historyPath).value_or (nlohmann::json::array ());

    if (history.is_array () && !history.empty () && history.back ().is_object ()) {
	history.back ()["survived"] = true;
	atomicWriteJson (historyPath, history);
    }
}

size_t WallpaperApplication::apiRotationPick () {
    auto& rot = this->m_apiRotation;
    const size_t n = rot.entries.size ();

    if (n == 0) {
	return SIZE_MAX;
    }

    if (n == 1) {
	return 0;
    }

    size_t pick = SIZE_MAX;

    if (rot.order == "sequential") {
	rot.seqIndex = (rot.seqIndex + 1) % static_cast<int> (n);
	pick = static_cast<size_t> (rot.seqIndex);
    } else if (rot.order == "random") {
	pick = this->m_playlistRng () % n;
    } else {
	if (rot.perm.size () != n || rot.permIndex >= rot.perm.size ()) {
	    rot.perm.resize (n);
	    std::iota (rot.perm.begin (), rot.perm.end (), 0);
	    std::shuffle (rot.perm.begin (), rot.perm.end (), this->m_playlistRng);
	    rot.permIndex = 0;
	}

	pick = rot.perm[rot.permIndex++];
    }

    const auto current = displayId (this->m_currentShow.id, this->m_currentShow.uiId);

    if (rot.avoidRepeat && !current.empty ()) {
	int guard = 0;

	while (guard < 8 && displayId (rot.entries[pick].id, rot.entries[pick].uiId) == current) {
	    if (rot.order == "sequential") {
		rot.seqIndex = (rot.seqIndex + 1) % static_cast<int> (n);
		pick = static_cast<size_t> (rot.seqIndex);
	    } else {
		pick = this->m_playlistRng () % n;
	    }

	    guard++;
	}
    }

    return pick;
}

void WallpaperApplication::apiRotationPredraw () {
    auto& rot = this->m_apiRotation;
    rot.nextPick = rot.entries.empty () ? SIZE_MAX : this->apiRotationPick ();
}

bool WallpaperApplication::apiRotationAdvance (std::string& error) {
    auto& rot = this->m_apiRotation;

    if (rot.entries.empty ()) {
	error = "rotation set is empty";
	return false;
    }

    if (!this->makeAnyViewportCurrent ()) {
	error = "no active viewport to switch on";
	return false;
    }

    size_t pick = rot.nextPick != SIZE_MAX ? rot.nextPick : this->apiRotationPick ();
    rot.nextPick = SIZE_MAX;

    for (size_t attempts = 0; attempts < rot.entries.size (); attempts++) {
	const auto& entry = rot.entries[pick];
	const auto path = resolveLibraryBackground (entry.id);

	if (path.has_value () && this->preflightWallpaper (path->string ())) {
	    std::string applyError;

	    if (this->applyShowCore (*path, entry.args, true, applyError)) {
		rot.lastShow = std::chrono::steady_clock::now ();
		this->apiRotationPredraw ();
		return true;
	    }

	    sLog.error ("API: rotation entry ", entry.id, " failed to apply: ", applyError, "; skipping");
	} else {
	    sLog.error ("API: rotation entry ", entry.id, " failed to resolve/preflight; skipping");
	}

	pick = this->apiRotationPick ();
    }

    error = "all rotation entries failed; keeping current wallpaper";
    return false;
}

bool WallpaperApplication::apiReleaseOutputs (const ReleaseReason reason, std::string& error) {
    if (this->m_releaseReason != ReleaseReason::Live) {
	// idempotent, but a verb-hold must not be downgraded to a deadman-hold (the
	// ping-restores-deadman rule would then steal the bench's outputs back)
	if (reason == ReleaseReason::Verb) {
	    this->m_releaseReason = ReleaseReason::Verb;
	}

	return true;
    }

    if (!this->m_videoDriver) {
	error = "no video driver";
	return false;
    }

    // GL teardown FIRST, while a surface still exists to hold the context current:
    // scenes, decoders and sole-owner cached textures all die here (VRAM freed)
    if (!this->makeAnyViewportCurrent ()) {
	error = "no active viewport";
	return false;
    }

    this->m_renderContext->clearWallpapers ();
    this->m_backgrounds.clear ();
    const auto evicted = this->m_renderContext->evictUnusedTextures ();

    if (!this->m_videoDriver->releaseOutputSurfaces ()) {
	sLog.error ("API: release-outputs unsupported or no surfaces; rebuilding");

	try {
	    this->rebuildForCurrentBackgrounds ();
	} catch (const std::exception& e) {
	    sLog.error ("API: rebuild after failed release also failed: ", e.what ());
	}

	error = "driver does not support releasing outputs";
	return false;
    }

    this->m_releaseReason = reason;
    sLog.out (
	"API: outputs RELEASED (",
	reason == ReleaseReason::Deadman            ? "dead-man"
	    : reason == ReleaseReason::Fullscreen   ? "fullscreen-stop"
	    : reason == ReleaseReason::AppCondition ? "app-condition"
						    : "verb",
	"), evicted ", evicted, " cached textures"
    );
    this->evictResidentPages ();
    return true;
}

void WallpaperApplication::evictResidentPages () const {
#ifdef __GLIBC__
    // scene teardown leaves freed-but-unreturned heap; give it back first so the
    // anonymous half of RSS is honest too
    malloc_trim (0);
#endif
    // Clean file-backed pages (our own .so, GL driver and codec libraries) are
    // reclaimable on demand and cost nothing under pressure - but they keep RSS at
    // hundreds of MB after a release, which reads as "the wallpaper engine is
    // holding memory". Product contract: a released engine must not LOOK loaded
    // either. MADV_DONTNEED drops our page tables for them NOW; the pages stay in
    // the kernel page cache (most are shared with every other GL process), so
    // re-acquire refaults them as minor faults, not disk reads.
    //
    // A mapping is only dropped when EVERY page in it is clean (Private_Dirty and
    // Shared_Dirty both zero in smaps): clean means memory is bit-identical to the
    // file, so the refault is lossless. Read-only is NOT a sufficient test - RELRO
    // segments are r--p yet hold relocated GOT/vtable data, and dropping one
    // reverts it to raw file bytes (measured: instant SIGSEGV on release).
    // (MADV_PAGEOUT does nothing here either: the kernel skips every page mapped
    // by more than one process, which is exactly what libraries are.)
    const auto rssKb = [] () -> long {
	std::ifstream status ("/proc/self/status");
	std::string line;
	while (std::getline (status, line)) {
	    long kb = 0;
	    if (sscanf (line.c_str (), "VmRSS: %ld", &kb) == 1) {
		return kb;
	    }
	}
	return 0;
    };

    const long before = rssKb ();
    std::vector<std::pair<uintptr_t, uintptr_t>> targets;

    {
	std::ifstream smaps ("/proc/self/smaps");
	std::string line;
	uintptr_t start = 0, end = 0;
	bool candidate = false;
	long dirty = 0;

	const auto flush = [&] () {
	    if (candidate && dirty == 0 && end > start) {
		targets.emplace_back (start, end);
	    }
	};

	while (std::getline (smaps, line)) {
	    uintptr_t s0 = 0, e0 = 0;
	    char perms[5] = {};

	    if (sscanf (line.c_str (), "%lx-%lx %4s", &s0, &e0, perms) == 3 && line.find (':') > line.find (' ')) {
		// new VMA header: settle the previous one first
		flush ();
		start = s0;
		end = e0;
		dirty = 0;
		// private, non-writable, file-backed, never a device mapping
		candidate = perms[1] == '-' && perms[3] == 'p' && line.find (" /") != std::string::npos
		    && line.find (" /dev/") == std::string::npos;
	    } else {
		long kb = 0;
		if (sscanf (line.c_str (), "Private_Dirty: %ld", &kb) == 1
		    || sscanf (line.c_str (), "Shared_Dirty: %ld", &kb) == 1) {
		    dirty += kb;
		}
	    }
	}

	flush ();
    }

    for (const auto& [s0, e0] : targets) {
	madvise (reinterpret_cast<void*> (s0), e0 - s0, MADV_DONTNEED);
    }

    sLog.out ("API: release eviction dropped RSS ", before / 1024, " -> ", rssKb () / 1024, " MB");
}

bool WallpaperApplication::apiAcquireOutputs (std::string& error) {
    if (this->m_releaseReason == ReleaseReason::Live) {
	return true; // idempotent
    }

    if (!this->m_videoDriver || !this->m_videoDriver->acquireOutputSurfaces ()) {
	error = "driver could not re-acquire outputs";
	return false;
    }

    this->m_releaseReason = ReleaseReason::Live;

    try {
	this->rebuildForCurrentBackgrounds ();
    } catch (const std::exception& e) {
	sLog.error ("API: rebuild after acquire failed: ", e.what ());
	error = std::string ("outputs acquired but rebuild failed: ") + e.what ();
	return false;
    }

    sLog.out ("API: outputs ACQUIRED, wallpapers rebuilt");
    return true;
}

void WallpaperApplication::tickDeadman () {
    if (this->m_deadmanSeconds <= 0 || !this->m_pingSeen || this->m_releaseReason != ReleaseReason::Live) {
	return;
    }

    const auto now = std::chrono::steady_clock::now ();
    const auto sincePing = std::chrono::duration_cast<std::chrono::seconds> (now - this->m_lastPing).count ();

    if (sincePing < this->m_deadmanSeconds || this->secondsSinceLastRender () < this->m_deadmanSeconds) {
	return;
    }

    sLog.error (
	"API: DEAD-MAN - no frames and no client heartbeat for ", this->m_deadmanSeconds, "s; releasing outputs"
    );

    std::string error;

    if (!this->apiReleaseOutputs (ReleaseReason::Deadman, error)) {
	sLog.error ("API: dead-man release failed: ", error);
	// re-arm: pretend a ping so the next window starts fresh instead of spinning
	this->m_lastPing = now;
    }
}

bool WallpaperApplication::fullscreenStopEngaged () const { return this->m_releaseReason == ReleaseReason::Fullscreen; }

bool WallpaperApplication::appConditionStopEngaged () const {
    return this->m_releaseReason == ReleaseReason::AppCondition;
}

namespace {
bool anyListedProcessRunning (const std::vector<std::string>& names) {
    std::error_code ec;

    for (const auto& entry : std::filesystem::directory_iterator ("/proc", ec)) {
	const auto pid = entry.path ().filename ().string ();

	if (pid.empty () || pid[0] < '0' || pid[0] > '9') {
	    continue;
	}

	std::ifstream comm (entry.path () / "comm");
	std::string line;

	if (comm.is_open () && std::getline (comm, line)
	    && std::find (names.begin (), names.end (), line) != names.end ()) {
	    return true;
	}
    }

    return false;
}
} // namespace

void WallpaperApplication::tickAppCondition () {
    auto& cond = this->m_appCondition;
    const auto now = std::chrono::steady_clock::now ();

    if (now - cond.lastPoll < std::chrono::seconds (3)) {
	return;
    }

    cond.lastPoll = now;

    const bool wantActive = cond.behavior != "off" && !cond.names.empty () && anyListedProcessRunning (cond.names);

    // revert side first: a standing hold whose trigger vanished, or whose behavior was
    // reconfigured out from under it, must let go before any new engagement
    if (cond.pauseEngaged && (!wantActive || cond.behavior != "pause")) {
	this->setTimescale (cond.prevTimescale);
	cond.pauseEngaged = false;
	sLog.out ("API: app-condition pause RELEASED (no listed process running)");
    }

    if (this->appConditionStopEngaged () && (!wantActive || cond.behavior != "stop")) {
	std::string error;

	if (!this->apiAcquireOutputs (error)) {
	    sLog.error ("API: app-condition re-acquire failed: ", error);
	}
    }

    if (!wantActive) {
	return;
    }

    if (cond.behavior == "pause" && !cond.pauseEngaged) {
	// the master-pause fact (timescale 0) - the same single fact the panel's header
	// pause drives; never a second pause mechanism
	cond.prevTimescale = this->m_timescale;
	this->setTimescale (0.0f);
	cond.pauseEngaged = true;
	sLog.out ("API: app-condition pause ENGAGED (listed process running)");
    } else if (cond.behavior == "stop" && this->m_releaseReason == ReleaseReason::Live) {
	// only take LIVE outputs: a bench (Verb), dead-man, or fullscreen hold owns
	// them already; retry lands on a later tick once that hold clears
	std::string error;

	if (!this->apiReleaseOutputs (ReleaseReason::AppCondition, error)) {
	    sLog.error ("API: app-condition release failed: ", error);
	}
    }
}

void WallpaperApplication::tickFullscreenGate () {
    const bool wantRelease = this->m_context.settings.render.fullscreenBehavior == FullscreenBehavior::Stop
	&& this->m_fullScreenDetector != nullptr && this->m_fullScreenDetector->anythingFullscreen ();

    if (wantRelease == this->fullscreenStopEngaged ()) {
	return;
    }

    std::string error;

    if (wantRelease) {
	// only ever take outputs that are actually live: a bench (Verb) or dead-man
	// hold owns them already, and apiReleaseOutputs would refuse to downgrade
	if (this->m_releaseReason != ReleaseReason::Live) {
	    return;
	}

	if (!this->apiReleaseOutputs (ReleaseReason::Fullscreen, error)) {
	    sLog.error ("API: fullscreen-stop release failed: ", error);
	}

	return;
    }

    if (!this->apiAcquireOutputs (error)) {
	sLog.error ("API: fullscreen-stop re-acquire failed: ", error);
    }
}

void WallpaperApplication::tickApiRotation () {
    auto& rot = this->m_apiRotation;

    // released outputs = nothing to paint on; advancing would rebuild scenes (VRAM
    // resident again) into surfaces that do not exist. The clock keeps counting - an
    // overdue advance fires on the first tick after acquire.
    if (!rot.enabled || rot.entries.empty () || this->m_releaseReason != ReleaseReason::Live) {
	return;
    }

    const auto elapsed
	= std::chrono::duration_cast<std::chrono::seconds> (std::chrono::steady_clock::now () - rot.lastShow);

    if (elapsed.count () < rot.intervalSeconds) {
	return;
    }

    std::string error;

    if (!this->apiRotationAdvance (error)) {
	sLog.error ("API: scheduled rotation advance failed: ", error);
	// re-arm for the next full interval instead of retrying every loop pass
	rot.lastShow = std::chrono::steady_clock::now ();
	return;
    }

    // a scheduled advance changes durable state (the current wallpaper) exactly like a
    // verb does; persist so a crash or restart restores what was actually on screen
    this->persistRuntimeState ();
}

void WallpaperApplication::show () {
    // seed the runtime instrument registry from the environment BEFORE any gate is read, so
    // an LWE_X=1 launch behaves exactly as it did when every gate called getenv directly
    Logging::instrumentSeedFromEnv ();
    setup ();
    this->restoreRuntimeState ();
    while (this->m_context.state.general.keepRunning) {
	this->checkPropertyReload ();
	this->processApiRequests ();
	this->markBootSurvived ();
	this->tickFullscreenGate ();
	this->tickAppCondition ();
	this->tickApiRotation ();
	this->tickDeadman ();

	if (this->m_webHelper) {
	    this->m_webHelper->pumpEvents ();
	}

	render ();
    }
    this->m_renderContext.reset ();
    cleanup ();
}

void WallpaperApplication::update (Render::Drivers::Output::OutputViewport* viewport) {
    // render the scene
    m_renderContext->render (viewport);
    this->m_lastRender.store (std::chrono::steady_clock::now ().time_since_epoch ().count ());
}

void WallpaperApplication::setColorCorrection (const glm::vec4& cc) {
    this->m_colorCorrection = { std::clamp (cc.x, 0.0f, 4.0f), std::clamp (cc.y, 0.0f, 4.0f),
				std::clamp (cc.z, 0.0f, 4.0f), std::clamp (cc.w, -6.4f, 6.4f) };
}

void WallpaperApplication::setTimescale (const float timescale) {
    this->m_timescale = std::clamp (timescale, 0.0f, 20.0f);
    if (this->m_renderContext != nullptr) {
	this->m_renderContext->setPause (this->m_isPaused || this->m_timescale == 0.0f);
	if (this->m_timescale > 0.0f) {
	    this->m_renderContext->setPlaybackSpeed (std::min (this->m_timescale, 100.0f));
	}
    }
}

double WallpaperApplication::secondsSinceLastRender () const {
    const auto last = this->m_lastRender.load ();

    if (last == 0) {
	return 0.0; // nothing rendered yet: startup's own kick covers this window
    }

    const auto now = std::chrono::steady_clock::now ().time_since_epoch ().count ();

    return std::chrono::duration<double> (std::chrono::steady_clock::duration (now - last)).count ();
}

void WallpaperApplication::signal (int signal) {
    if (signal == SIGUSR1) {
	sLog.out ("Property reload requested by signal");
	this->m_reloadPropertiesRequested = true;
	return;
    }

    if (signal == SIGUSR2) {
	this->m_manualPauseRequested = !this->m_manualPauseRequested;
	sLog.out ("Manual pause ", this->m_manualPauseRequested ? "REQUESTED" : "RELEASED", " by signal");
	return;
    }

    sLog.out ("Stop requested by signal ", signal);
    this->m_context.state.general.keepRunning = false;
}

std::string WallpaperApplication::resolveWallpaperLookupKey (const std::string& backgroundKey) const {
    if (backgroundKey.rfind ("span:", 0) != 0) {
	return backgroundKey;
    }

    for (const auto& spanGroup : this->m_context.settings.general.spanGroups) {
	if (spanGroup.screens.empty ()) {
	    continue;
	}

	if ("span:" + spanGroup.screens.front () == backgroundKey) {
	    return spanGroup.screens.front ();
	}
    }

    return backgroundKey;
}

void WallpaperApplication::checkPropertyReload () {
    if (!this->m_reloadPropertiesRequested.exchange (false)) {
	return;
    }

    if (this->m_context.settings.general.propertiesFile.empty ()) {
	sLog.error ("Property reload signaled but no --properties-file was set, ignoring");
	return;
    }

    std::ifstream file (this->m_context.settings.general.propertiesFile);

    if (!file.is_open ()) {
	sLog.error ("Could not open properties file for reload: ", this->m_context.settings.general.propertiesFile);
	return;
    }

    WallpaperEngine::Data::JSON::JSON overrides;

    try {
	file >> overrides;
    } catch (const std::exception& e) {
	sLog.error ("Failed to parse properties file: ", e.what ());
	return;
    }

    for (const auto& [screen, project] : this->m_backgrounds) {
	if (!overrides.contains (screen)) {
	    continue;
	}

	const auto& screenOverrides = overrides[screen];
	std::map<std::string, PropertySharedPtr> changed;

	for (const auto& [key, property] : project->properties) {
	    if (!screenOverrides.contains (key)) {
		continue;
	    }

	    const std::string newValue = screenOverrides[key].is_string () ? screenOverrides[key].get<std::string> ()
									   : screenOverrides[key].dump ();

	    if (newValue == property->toString ()) {
		continue;
	    }

	    property->update (newValue, DynamicValue::UpdateSource::User);
	    changed.emplace (key, property);
	}

	if (changed.empty ()) {
	    continue;
	}

	const auto wallpaperIt
	    = this->m_renderContext->getWallpapers ().find (this->resolveWallpaperLookupKey (screen));

	if (wallpaperIt == this->m_renderContext->getWallpapers ().end ()) {
	    continue;
	}

	if (auto* scene = dynamic_cast<WallpaperEngine::Render::Wallpapers::CScene*> (wallpaperIt->second.get ())) {
	    scene->getScriptEngine ().notifyUserPropertiesChanged (changed);
	    continue;
	}

	if (auto* web = dynamic_cast<WallpaperEngine::Render::Wallpapers::CWeb*> (wallpaperIt->second.get ())) {
	    for (const auto& [key, property] : changed) {
		web->notifyPropertyChanged (key);
	    }
	}
    }
}

const std::map<std::string, ProjectUniquePtr>& WallpaperApplication::getBackgrounds () const {
    return this->m_backgrounds;
}

ApplicationContext& WallpaperApplication::getContext () const { return this->m_context; }

const WallpaperEngine::Render::Drivers::Output::Output& WallpaperApplication::getOutput () const {
    return this->m_renderContext->getOutput ();
}

void WallpaperApplication::setDestinationFramebuffer (GLuint framebuffer) {
    this->m_destinationFramebuffer = framebuffer;
    // Update all wallpapers with the new destination framebuffer
    for (const auto& [screen, wallpaper] : this->m_renderContext->getWallpapers ()) {
	wallpaper->setDestinationFramebuffer (framebuffer);
    };
}

GLuint WallpaperApplication::getDestinationFramebuffer () const { return this->m_destinationFramebuffer; }