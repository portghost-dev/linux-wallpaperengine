#pragma once
#include <atomic>

#include <chrono>
#include <deque>
#include <random>

#include "WallpaperEngine/Application/ApplicationContext.h"
#include "WallpaperEngine/Assets/AssetLocator.h"

#include "WallpaperEngine/Render/CWallpaper.h"
#include "WallpaperEngine/Render/Drivers/Detectors/FullScreenDetector.h"
#include "WallpaperEngine/Render/Drivers/GLFWOpenGLDriver.h"
#include "WallpaperEngine/Render/Drivers/Output/GLFWWindowOutput.h"
#include "WallpaperEngine/Render/RenderContext.h"

#include "WallpaperEngine/Audio/Drivers/AudioDriver.h"

#include "WallpaperEngine/Input/InputContext.h"
#include "WallpaperEngine/WebHelper/HelperClient.h"

#include "WallpaperEngine/Api/CommandDispatcher.h"
#include "WallpaperEngine/Api/CommandServer.h"
#include "WallpaperEngine/Data/Model/Types.h"
#include "WallpaperEngine/Media/MediaSource.h"

#include <set>

namespace WallpaperEngine::Application {

using namespace WallpaperEngine::Assets;
using namespace WallpaperEngine::Data::Model;
/**
 * Small wrapper class over the actual wallpaper's main application skeleton
 */
class WallpaperApplication {
public:
    explicit WallpaperApplication (ApplicationContext& context);

    /**
     * Prepares the application for rendering.
     */
    void setup ();

    /**
     * Fds the video driver must include in its event poll so a pending API command
     * wakes the loop even when no frame callbacks are arriving. Empty when the API
     * was not requested.
     */
    [[nodiscard]] std::vector<int> getApiWakeFds () const;
    /**
     * Renders a frame of the application.
     */
    void render ();
    /**
     * Cleans up all the resources used by the application.
     */
    static void cleanup ();
    /**
     * Shows the application until it's closed
     */
    void show ();
    /**
     * Handles a OS signal sent to this PID
     *
     * @param signal
     */
    void signal (int signal);
    void checkPropertyReload ();
    [[nodiscard]] std::string resolveWallpaperLookupKey (const std::string& backgroundKey) const;
    /**
     * @return Maps screens to loaded backgrounds
     */
    [[nodiscard]] const std::map<std::string, ProjectUniquePtr>& getBackgrounds () const;
    /**
     * @return The current application context
     */
    [[nodiscard]] ApplicationContext& getContext () const;
    /**
     * Renders a frame
     */
    void update (Render::Drivers::Output::OutputViewport* viewport);
    [[nodiscard]] double secondsSinceLastRender () const;

    [[nodiscard]] glm::vec4 getColorCorrection () const { return this->m_colorCorrection; }
    void setColorCorrection (const glm::vec4& cc);
    [[nodiscard]] float getTimescale () const { return this->m_timescale; }
    void setTimescale (float timescale);
    /**
     * Gets the output
     */
    [[nodiscard]] const WallpaperEngine::Render::Drivers::Output::Output& getOutput () const;
    /**
     * Sets the destination framebuffer for rendering. If not called, the default framebuffer will be used.
     */
    void setDestinationFramebuffer (GLuint framebuffer);

    /**
     * Gets the currently set destination framebuffer for rendering. If not set, returns 0 (the default framebuffer).
     */
    [[nodiscard]] GLuint getDestinationFramebuffer () const;

public:
    struct WebLibraryEntry {
	std::string workshopId;
	std::filesystem::path path;
    };

    /**
     * Every web wallpaper in the library roots (lwe wallpapers + Steam workshop),
     * cached by setupBrowser. CEF scheme registration is immutable after the helper's
     * CefInitialize, so the whole universe must travel in the spawn config for
     * hot-swapped web wallpapers to resolve. The enumeration itself touches no CEF.
     */
    [[nodiscard]] const std::vector<WebLibraryEntry>& getWebLibrary () const;

    /**
     * Sets up an asset locator for the given background
     *
     * @param bg
     */
    AssetLocatorUniquePtr setupAssetLocator (const std::string& bg) const;

private:
    [[nodiscard]] std::vector<WebLibraryEntry> enumerateWebBackgrounds () const;
    /**
     * Initializes subsystems required for application operation
     */
    void initializeSubsystems ();

    /**
     * Loads projects based off the settings
     */
    void loadBackgrounds ();
    /**
     * Loads the given project
     *
     * @param bg
     * @return
     */
    [[nodiscard]] ProjectUniquePtr loadBackground (const std::string& bg);
    /**
     * Prepares all background's values and updates their properties if required
     */
    void setupProperties ();
    /**
     * Updates the properties for the given background based on the current context
     *
     * @param project
     */
    void setupPropertiesForProject (const Project& project);
    void setupBrowser ();
    /**
     * Construct the helper CLIENT if it does not exist yet. Costs a socket path and a
     * scheme list; it starts no process. The browser is spawned by the first CWeb's
     * create() and exits when the last one is destroyed.
     */
    void ensureWebHelperClient ();
    [[nodiscard]] WallpaperEngine::WebHelper::SpawnConfig buildWebHelperSpawnConfig () const;
    /**
     * Prepares desktop environment-related things (like render, window, fullscreen detector, etc)
     */
    void setupOutput ();
    /**
     * Prepares all audio-related things (like detector, output, etc)
     */
    void setupAudio ();
    /**
     * Prepares the render-context of all the backgrounds so they can be displayed on the screen
     */
    void prepareOutputs ();
    /**
     * Prepares output debugging for all opengl errors
     */
    void setupOpenGLDebugging ();
    /** bind the command socket when --api-socket was given; loud failure by design */
    void setupApi ();
    void processApiRequests ();
    /** execute one validated command; may respond more than once (accepted, then done) */
    void handleApiCommand (int client, const Api::Command& command);
    [[nodiscard]] nlohmann::json apiStatus () const;
    /**
     * The `show` verb: all-outputs hot swap. Resolves the id against the library roots,
     * preflights it, acks, then rebuilds every screen through buildWallpapers so mirror
     * groups stay shared (rebuilding per-screen by hand would silently clone the
     * wallpaper per monitor). Evicts sole-owner texture cache entries between teardown
     * and rebuild. On failure attempts to roll the previous background back.
     */
    void apiShow (int client, int64_t requestId, const std::string& backgroundId, const nlohmann::json& args);
    /**
     * The shared core of every wallpaper swap (apiShow, rotation advance, prev): applies
     * the per-show vocabulary from args, rebuilds all screens, rolls back on failure.
     * The path must already be resolved+preflighted and a viewport made current.
     * Returns false with `error` filled instead of throwing.
     */
    bool applyShowCore (
	const std::filesystem::path& path, const nlohmann::json& args, bool recordHistory, std::string& error
    );
    void apiRotateSet (int client, int64_t requestId, const nlohmann::json& args);
    /** rotate-set core, client-free so state restore can replay a persisted set */
    void applyRotateSet (const nlohmann::json& args);
    /**
     * Runtime state persistence: the engine writes its own durable state (current show,
     * rotation set, playback/audio/policy toggles) after every mutating verb and restores
     * it on an idle daemon boot, so a service restart is invisible without any client.
     */
    void persistRuntimeState () const;
    void restoreRuntimeState ();
    /** crash-loop guard bookkeeping: flips this boot's history entry once 60s pass */
    void markBootSurvived ();
    [[nodiscard]] static std::filesystem::path runtimeStateDir ();
    bool m_bootSurvivedMarked = false;
    /** true only when THIS boot appended a history entry; the survived flip must never
     *  touch a previous boot's record */
    bool m_bootHistoryArmed = false;
    bool apiRotationAdvance (std::string& error);
    [[nodiscard]] size_t apiRotationPick ();
    void apiRotationPredraw ();
    void tickApiRotation ();
    enum class ReleaseReason { Live, Verb, Deadman, Fullscreen, AppCondition };
    bool apiReleaseOutputs (ReleaseReason reason, std::string& error);
    bool apiAcquireOutputs (std::string& error);
    /** released engines must not LOOK like they hold memory: trim the heap and ask the
     * kernel to evict our clean file-backed pages now instead of lazily on pressure */
    void evictResidentPages () const;
    /** orphan reflex: no frames AND no client heartbeat for the window => shed the outputs */
    void tickDeadman ();
    /**
     * FullscreenBehavior::Stop edge, once per main-loop pass: shed the outputs while
     * something is fullscreen and take them back the moment it clears. Derives its
     * decision from m_releaseReason (not a shadow flag) so an interleaved acquire -
     * an explicit show re-arms released outputs - self-corrects on the next pass.
     */
    void tickFullscreenGate ();
    [[nodiscard]] bool fullscreenStopEngaged () const;
    /**
     * "While one of these apps runs: pause / stop" - a PROCESS poll, not a compositor
     * event: a CLI process (llama.cpp) has no window, so /proc/PID/comm is the only
     * honest source. Same ownership law as the fullscreen gate: each mechanism only
     * ever undoes what IT engaged.
     */
    void tickAppCondition ();
    [[nodiscard]] bool appConditionStopEngaged () const;
    /** id -> wallpaper directory, searching the lwe library then the Steam workshop; nullopt if absent */
    [[nodiscard]] static std::optional<std::filesystem::path>
    resolveLibraryBackground (const std::string& backgroundId);
    void rebuildForCurrentBackgrounds ();
    /**
     * Takes an screenshot of the background and saves it to the specified path
     *
     * @param filename
     */
    void takeScreenshot (const std::filesystem::path& filename) const;

    struct ActivePlaylist {
	ApplicationContext::PlaylistDefinition definition;
	std::vector<std::size_t> order;
	std::size_t orderIndex = 0;
	std::chrono::steady_clock::time_point nextSwitch;
	std::chrono::steady_clock::time_point lastUpdate;
	std::set<std::size_t> failedIndices;
    };

    void initializePlaylists ();
    void updatePlaylists ();
    void advancePlaylist (
	const std::string& screen, ActivePlaylist& playlist, const std::chrono::steady_clock::time_point& now
    );
    bool selectNextCandidate (ActivePlaylist& playlist, std::size_t& outOrderIndex);
    bool preflightWallpaper (const std::string& path);
    std::vector<std::size_t> buildPlaylistOrder (const ApplicationContext::PlaylistDefinition& definition);
    void ensureBrowserForProject (const Project& project);
    void ensureAudioForProject (const Project& project);
    void buildWallpapers ();
    /** SIGUSR2 toggle: pause on demand (test hook, also usable by a companion control surface) */
    std::atomic<bool> m_manualPauseRequested = false;
    bool makeAnyViewportCurrent () const;

    /** The application context that contains the current app settings */
    ApplicationContext& m_context;
    /** Maps screens to backgrounds */
    std::map<std::string, ProjectUniquePtr> m_backgrounds {};
    std::map<std::string, ActivePlaylist> m_activePlaylists {};

    std::unique_ptr<WallpaperEngine::Audio::Drivers::Detectors::AudioPlayingDetector> m_audioDetector = nullptr;
    std::unique_ptr<WallpaperEngine::Audio::AudioContext> m_audioContext = nullptr;
    std::unique_ptr<WallpaperEngine::Audio::Drivers::AudioDriver> m_audioDriver = nullptr;
    std::unique_ptr<WallpaperEngine::Audio::Drivers::Recorders::PlaybackRecorder> m_audioRecorder = nullptr;
    std::unique_ptr<WallpaperEngine::Render::RenderContext> m_renderContext = nullptr;
    std::unique_ptr<WallpaperEngine::Api::CommandServer> m_commandServer = nullptr;
    const std::chrono::steady_clock::time_point m_startTime = std::chrono::steady_clock::now ();
    /** steady_clock tick count of the last serviced render, 0 until the first frame */
    std::atomic<int64_t> m_lastRender { 0 };
    /** present-pass color correction (brightness, contrast, saturation, hue radians) */
    glm::vec4 m_colorCorrection = { 1.0f, 1.0f, 1.0f, 0.0f };
    /** animation speed factor applied to g_Time accumulation */
    float m_timescale = 1.0f;
    struct {
	int volume;
	bool audioProcessing;
	bool mouseEnabled;
	bool automute;
	FullscreenBehavior fullscreenBehavior;
	std::map<std::string, WallpaperEngine::Render::WallpaperState::TextureUVsScaling> screenScalings;
	std::map<std::string, TextureFlags> screenClamps;
    } m_showDefaults {};
    struct ApiRotationEntry {
	std::string id;
	std::string uiId;
	nlohmann::json args;
    };
    struct {
	std::vector<ApiRotationEntry> entries;
	int intervalSeconds = 900;
	std::string order = "shuffle";
	bool avoidRepeat = true;
	bool enabled = false;
	std::string label;
	/** shuffle: exhaust a permutation before re-shuffling (watcher parity) */
	std::vector<size_t> perm;
	size_t permIndex = 0;
	int seqIndex = -1;
	/** pre-drawn pick consumed by the next advance; SIZE_MAX = none */
	size_t nextPick = SIZE_MAX;
	std::chrono::steady_clock::time_point lastShow {};
	/** countdown freeze (disable pauses the clock; re-enable resumes, never insta-rotates) */
	int frozenRemainingSeconds = -1;
    } m_apiRotation {};
    /** prev-history: complete show records so prev restores the LOOK, not just the id */
    std::deque<ApiRotationEntry> m_showHistory {};
    /** what is showing now: engine id, the UI's opaque identity echo, and the args
     *  as applied (so history entries can restore the full look) */
    ApiRotationEntry m_currentShow {};
    std::chrono::steady_clock::time_point m_lastPing {};
    bool m_pingSeen = false;
    struct {
	std::vector<std::string> names;
	std::string behavior = "off"; /**< off | pause | stop */
	bool pauseEngaged = false;
	float prevTimescale = 1.0f;
	std::chrono::steady_clock::time_point lastPoll {};
    } m_appCondition {};
    ReleaseReason m_releaseReason = ReleaseReason::Live;
    int m_deadmanSeconds = 300;
    std::unique_ptr<WallpaperEngine::Render::Drivers::VideoDriver> m_videoDriver = nullptr;
    std::unique_ptr<WallpaperEngine::Render::Drivers::Detectors::FullScreenDetector> m_fullScreenDetector = nullptr;
    std::unique_ptr<WallpaperEngine::WebHelper::HelperClient> m_webHelper = nullptr;
    /** filled by setupBrowser; the scheme universe CEF gets registered with */
    std::vector<WebLibraryEntry> m_webLibrary {};
    std::unique_ptr<WallpaperEngine::Media::MediaSource> m_mediaSource = nullptr;
    std::mt19937 m_playlistRng { std::random_device {}() };
    bool m_isPaused = false;
    bool m_screenShotTaken = false;
    std::atomic<bool> m_reloadPropertiesRequested { false };
    uint32_t m_nextFrameScreenshot = 0;
    std::chrono::steady_clock::time_point m_pauseStart {};
    GLuint m_destinationFramebuffer = 0;
};
} // namespace WallpaperEngine::Application
