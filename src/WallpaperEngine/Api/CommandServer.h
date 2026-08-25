#pragma once

#include <cstdint>
#include <filesystem>
#include <map>
#include <string>
#include <vector>

namespace WallpaperEngine::Api {
class CommandServer {
public:
    struct Request {
	int client;
	std::string line;
    };

    static constexpr size_t MAX_LINE_BYTES = 64 * 1024;
    static constexpr size_t MAX_CLIENTS = 8;

    explicit CommandServer (std::filesystem::path socketPath);
    ~CommandServer ();

    CommandServer (const CommandServer&) = delete;
    CommandServer& operator= (const CommandServer&) = delete;

    /**
     * Create the socket directory, bind, and listen.
     *
     * Doubles as the single-instance guard. A socket file that already exists is probed
     * with connect(): if something answers, another engine owns it and this returns false
     * rather than unlinking a live instance's socket and stealing its commands. Only a
     * refused connection (nobody listening) is treated as a stale file and removed.
     *
     * @return false on failure; error() explains why.
     */
    bool listen ();

    [[nodiscard]] bool isListening () const { return this->m_listenFd >= 0; }

    /** every fd the caller must include in its poll set: the listener plus live clients */
    [[nodiscard]] std::vector<int> fds () const;

    /**
     * Accept pending connections and read whatever is available. Never blocks.
     * Partial lines are buffered until their newline arrives.
     */
    std::vector<Request> drain ();

    /** write one line back to a client; a dead client is dropped rather than raising */
    void respond (int client, const std::string& line);

    void disconnect (int client);

    [[nodiscard]] size_t clientCount () const { return this->m_clients.size (); }

    [[nodiscard]] const std::string& error () const { return this->m_error; }

    /** resolved default: $LWE_SOCKET, else $XDG_RUNTIME_DIR/lwe/engine.sock */
    static std::filesystem::path defaultSocketPath ();

    /** "pid <pid> (<comm>)" of the process on this fd - the audit half of SO_PEERCRED */
    static std::string peerDescription (int fd);

private:
    /** true if the peer on this fd shares our uid; closes and returns false otherwise */
    bool authenticatePeer (int fd);

    std::filesystem::path m_socketPath;
    int m_listenFd = -1;
    /** per-client accumulation buffer for partial lines */
    std::map<int, std::string> m_clients;
    std::string m_error;
    /** whether we created the socket file and are therefore responsible for removing it */
    bool m_ownsSocketFile = false;
};
}
