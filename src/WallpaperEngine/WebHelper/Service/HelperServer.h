#pragma once

#include "WallpaperEngine/WebHelper/MessageChannel.h"
#include "WallpaperEngine/WebHelper/Protocol.h"
#include "WallpaperEngine/WebHelper/Service/WebInstance.h"
#include "WallpaperEngine/WebHelper/SpawnConfig.h"

#include <map>
#include <memory>
#include <optional>

namespace WallpaperEngine::WebHelper::Service {
class HelperServer {
public:
    explicit HelperServer (const SpawnConfig& config);

    /** bind and listen; false means the process should exit non-zero */
    bool start ();

    /**
     * One iteration: accept a pending engine, decode whatever arrived, emit any events
     * that became true. Never blocks - the CEF message loop has to keep turning.
     */
    void tick ();

    [[nodiscard]] bool engineDisconnected () const {
	return this->m_engineWasConnected && !this->m_channel.has_value ();
    }

    static constexpr int64_t DEFAULT_IDLE_EXIT_MS = 1000;

    [[nodiscard]] static int64_t idleExitGraceMs ();

    [[nodiscard]] size_t instanceCount () const { return this->m_instances.size (); }

    /**
     * Should this process exit because there is no web wallpaper left to host?
     *
     * Requires an engine to have CONNECTED first, so a helper that is still binding and
     * being connected to does not exit out from under the engine that just spawned it.
     */
    [[nodiscard]] bool shouldExitIdle () const;

    /** ms until shouldExitIdle() turns true, or -1 when no idle timer is running */
    [[nodiscard]] int64_t millisUntilIdleExit () const;

    [[nodiscard]] int listenFd () const { return this->m_listener.fd (); }
    /** the engine's connection, or -1 when nothing is connected */
    [[nodiscard]] int connectionFd () const { return this->m_channel.has_value () ? this->m_channel->fd () : -1; }
    /** true when bytes are waiting that the socket would not take; poll must ask for POLLOUT */
    [[nodiscard]] bool wantsWrite () const {
	return this->m_channel.has_value () && this->m_channel->hasQueuedOutput ();
    }

    [[nodiscard]] const std::string& error () const { return this->m_error; }

private:
    void handle (const Message& message);
    /** poll each instance's load state and emit page-loaded exactly once */
    void emitPageLoadedEvents ();
    /** emit frame-ready once per shm generation, never per frame (Protocol.h) */
    void emitFrameReadyEvents ();
    [[nodiscard]] WebInstance* find (InstanceId id);
    void updateIdleTimer ();

    const SpawnConfig& m_config;
    MessageListener m_listener;
    std::optional<MessageChannel> m_channel;
    std::map<InstanceId, std::unique_ptr<WebInstance>> m_instances;
    bool m_engineWasConnected = false;
    /** steady-clock ms when the instance count last reached zero; 0 = no timer running */
    int64_t m_idleSinceMs = 0;
    std::string m_error;
};
}
