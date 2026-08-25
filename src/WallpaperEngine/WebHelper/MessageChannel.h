#pragma once

#include "Protocol.h"

#include <deque>
#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace WallpaperEngine::WebHelper {
class MessageChannel {
public:
    /** a peer that stops reading must not be able to grow our memory without bound */
    static constexpr size_t MAX_QUEUED_BYTES = 8 * 1024 * 1024;

    MessageChannel () = default;
    explicit MessageChannel (int fd);
    ~MessageChannel ();

    MessageChannel (const MessageChannel&) = delete;
    MessageChannel& operator= (const MessageChannel&) = delete;
    MessageChannel (MessageChannel&& other) noexcept;
    MessageChannel& operator= (MessageChannel&& other) noexcept;

    [[nodiscard]] bool isOpen () const { return this->m_fd >= 0; }
    [[nodiscard]] int fd () const { return this->m_fd; }
    /** bytes the socket would not take yet; a poll loop must ask for POLLOUT while true */
    [[nodiscard]] bool hasQueuedOutput () const { return !this->m_outbound.empty (); }
    [[nodiscard]] const std::string& error () const { return this->m_error; }

    bool send (const std::vector<uint8_t>& framed);

    /** push queued bytes; call when poll reports the fd writable, or opportunistically */
    bool flush ();

    /**
     * Read whatever is available and return every complete message in it. An empty result
     * with isOpen() still true just means nothing arrived. On peer close or a protocol
     * violation the channel closes itself and error() explains.
     */
    std::vector<Message> receive ();

    void close ();

private:
    int m_fd = -1;
    std::vector<uint8_t> m_inbound;
    std::deque<uint8_t> m_outbound;
    std::string m_error;
};

class MessageListener {
public:
    explicit MessageListener (std::filesystem::path socketPath);
    ~MessageListener ();

    MessageListener (const MessageListener&) = delete;
    MessageListener& operator= (const MessageListener&) = delete;

    /** create the 0700 directory, bind 0600, listen. false on failure; see error() */
    bool listen ();

    [[nodiscard]] bool isListening () const { return this->m_listenFd >= 0; }
    [[nodiscard]] int fd () const { return this->m_listenFd; }
    [[nodiscard]] const std::string& error () const { return this->m_error; }

    /** accept a pending same-uid connection, if any. Never blocks. */
    std::optional<MessageChannel> accept ();

private:
    std::filesystem::path m_socketPath;
    int m_listenFd = -1;
    bool m_ownsSocketFile = false;
    std::string m_error;
};

/**
 * Engine side: connect to a helper that is already listening.
 *
 * @return an open channel, or nullopt with `error` set. Not retried here - the respawn and
 *         crash-loop policy belongs to HelperClient's lifecycle, not to the transport.
 */
std::optional<MessageChannel> connectToHelper (const std::filesystem::path& socketPath, std::string& error);
}
