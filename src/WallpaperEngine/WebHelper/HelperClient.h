#pragma once

#include "MessageChannel.h"
#include "Protocol.h"
#include "SpawnConfig.h"

#include <deque>
#include <map>
#include <optional>

namespace WallpaperEngine::WebHelper {
struct InstanceState {
    bool pageLoaded = false;
    bool loadFailed = false;
    int32_t loadErrorCode = 0;
    std::string loadErrorText;
    /**
     * Which shm generation the helper has published into, from the frame-ready event.
     * 0 = nothing to map yet. The engine maps frameShmName(helperPid, id, generation) and
     * then reads the seqlock directly for every frame - the generation is the ONLY
     * per-frame-path fact that travels over the socket (FrameContract.h, Protocol.h).
     */
    uint32_t frameGeneration = 0;
    uint32_t frameWidth = 0;
    uint32_t frameHeight = 0;
};

struct ReplayRecord {
    std::string workshopId;
    std::string file;
    uint32_t width = 0;
    uint32_t height = 0;
    uint32_t framerate = 0;
    /**
     * The merged property state: seeded by inject-properties, then overwritten key by key
     * by set-property, so a live change survives a respawn.
     */
    std::map<std::string, PropertyValue> properties;
    bool propertiesPending = false;
};

enum class LifecycleState { Idle, Starting, Connected, Draining, Backoff, Cooldown };

[[nodiscard]] const char* lifecycleStateName (LifecycleState state);

struct CrashGuardSettings {
    int deaths = 4;
    int64_t windowMs = 60000;
    int64_t cooldownMs = 30000;

    /** LWE_WEB_CRASHGUARD=<deaths>,<windowMs>,<cooldownMs>; all three or none */
    static CrashGuardSettings fromEnvironment ();
};

class HelperClient {
public:
    explicit HelperClient (SpawnConfig config);
    ~HelperClient ();

    HelperClient (const HelperClient&) = delete;
    HelperClient& operator= (const HelperClient&) = delete;

    static constexpr int CONNECT_ATTEMPTS = 200;
    static constexpr int CONNECT_BACKOFF_MS = 25;

    /** respawn backoff: doubles per consecutive death, reset once a helper stays up */
    static constexpr int64_t BACKOFF_BASE_MS = 250;
    static constexpr int64_t BACKOFF_MAX_MS = 5000;
    /** a connection that lasts this long is evidence the helper is healthy again */
    static constexpr int64_t HEALTHY_CONNECTION_MS = 10000;

    /**
     * How long the engine waits for a helper that was told to go away before it stops
     * being polite. Generous next to the helper's own idle grace, because the helper only
     * starts its grace timer once it has processed the destroy.
     */
    static constexpr int64_t DRAIN_TIMEOUT_MS = 8000;

    [[nodiscard]] const SpawnConfig& config () const { return this->m_config; }
    [[nodiscard]] bool isConnected () const { return this->m_channel.has_value () && this->m_channel->isOpen (); }
    /** pid of the running helper, or -1; part of every shm object's name */
    [[nodiscard]] int helperPid () const { return this->m_helperPid; }

    [[nodiscard]] LifecycleState state () const { return this->m_state; }
    [[nodiscard]] const char* stateName () const { return lifecycleStateName (this->m_state); }
    [[nodiscard]] uint64_t spawnCount () const { return this->m_spawnCount; }
    [[nodiscard]] uint64_t unexpectedDeaths () const { return this->m_unexpectedDeaths; }
    /** how the last helper ended, in words; empty if none has ended yet */
    [[nodiscard]] const std::string& lastExitDescription () const { return this->m_lastExitDescription; }
    /** ms until the next spawn attempt is allowed; 0 when nothing is holding it back */
    [[nodiscard]] int64_t millisUntilNextAttempt () const;
    [[nodiscard]] const CrashGuardSettings& crashGuard () const { return this->m_crashGuard; }

    /**
     * Start the helper if it is not running and connect to it. Called automatically by
     * create(), which is what "on demand" means: an engine that never shows a web
     * wallpaper never starts a browser process at all.
     *
     * Blocks for up to CONNECT_ATTEMPTS * CONNECT_BACKOFF_MS while the child binds its
     * socket. That is a bounded wait on the first web wallpaper only. A RESPAWN never
     * comes through here - it is driven a single attempt at a time from pumpEvents, so a
     * helper that is crashing cannot stall the render loop once per death.
     */
    bool ensureHelper ();

    void stopHelper ();

    /** hand out the next instance id; one per CWeb, monotonic, never reused */
    [[nodiscard]] InstanceId allocateInstance ();

    void pumpEvents ();

    void create (
	InstanceId id, const std::string& workshopId, const std::string& file, uint32_t width, uint32_t height,
	uint32_t framerate
    );
    void resize (InstanceId id, uint32_t width, uint32_t height);
    void mouseMove (InstanceId id, int32_t x, int32_t y);
    void mouseClick (InstanceId id, int32_t x, int32_t y, MouseButton button, bool released);
    void injectProperties (InstanceId id, const std::vector<PropertyValue>& properties);
    /** TYPED, like the entries in injectProperties; see Protocol.h's encodeSetProperty */
    void setProperty (InstanceId id, const PropertyValue& property);
    /** `bands` must point at AUDIO_BANDS floats; sent raw, never stringified */
    void audioSpectrum (InstanceId id, const float* bands);
    void destroy (InstanceId id);

    [[nodiscard]] bool isPageLoaded (InstanceId id) const;
    /** the page ended up on an error document; property injection must NOT be attempted */
    [[nodiscard]] bool didLoadFail (InstanceId id) const;
    [[nodiscard]] const InstanceState* instance (InstanceId id) const;

private:
    /**
     * Send if connected, drop otherwise. Transient verbs (mouse, audio) describe an instant
     * that has already passed; replaying a stale pointer position or spectrum after a
     * reconnect would be wrong, not merely wasteful.
     */
    void sendTransient (const std::vector<uint8_t>& framed);
    /**
     * Send if connected, drop otherwise - the ReplayRecord is what carries stateful verbs
     * across a helper's death, so there is nothing to queue.
     */
    void sendStateful (const std::vector<uint8_t>& framed);

    void handle (const Message& message);

    bool spawnService ();
    bool tryConnect ();
    void replayState ();
    void onDisconnected ();
    void tickDrain (int64_t now);
    void scheduleRespawn (int64_t now);
    /**
     * Collect the child if it has ended. `force` SIGKILLs first and then waits, which is
     * only safe because SIGKILL cannot be caught - it is used where the child is already
     * known to be gone or is refusing to leave.
     *
     * @return true when there is no child any more; m_lastExitDescription then says how it
     *         ended. false means it is still running and this should be tried again.
     */
    bool reapChild (bool force);
    void reapShmObjects (int pid) const;
    void invalidateInstanceState ();

    SpawnConfig m_config;
    std::optional<MessageChannel> m_channel;
    std::map<InstanceId, InstanceState> m_instances;
    std::map<InstanceId, ReplayRecord> m_replay;
    InstanceId m_nextInstanceId = 1;
    /** the helper process we started, or -1 if none is running */
    int m_helperPid = -1;

    LifecycleState m_state = LifecycleState::Idle;
    CrashGuardSettings m_crashGuard;
    std::deque<int64_t> m_deaths;
    /** steady-clock ms before which no spawn may be attempted */
    int64_t m_nextAttemptMs = 0;
    int64_t m_backoffMs = BACKOFF_BASE_MS;
    /** when the current connection was established; 0 when not connected */
    int64_t m_connectedSinceMs = 0;
    /** when the current Draining phase must stop being polite */
    int64_t m_drainDeadlineMs = 0;
    bool m_backoffResetPending = false;

    uint64_t m_spawnCount = 0;
    uint64_t m_unexpectedDeaths = 0;
    std::string m_lastExitDescription;
    /** logged once, not once per frame, when verbs are going nowhere */
    bool m_warnedDisconnected = false;
};
}
