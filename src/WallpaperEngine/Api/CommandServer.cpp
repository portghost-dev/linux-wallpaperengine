#include "CommandServer.h"

#include "WallpaperEngine/Logging/Log.h"

#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <poll.h>
#include <ranges>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

using namespace WallpaperEngine::Api;

namespace {
/** sun_path is a fixed 108-byte array; a path that does not fit must fail loudly */
bool fitsInSunPath (const std::string& path) {
    sockaddr_un probe {};
    return path.size () < sizeof (probe.sun_path);
}

void setNonBlocking (const int fd) {
    const int flags = fcntl (fd, F_GETFL, 0);

    if (flags >= 0) {
	fcntl (fd, F_SETFL, flags | O_NONBLOCK);
    }
}

bool someoneIsListening (const std::string& path) {
    const int fd = socket (AF_UNIX, SOCK_STREAM, 0);

    if (fd < 0) {
	return true;
    }

    sockaddr_un addr {};
    addr.sun_family = AF_UNIX;
    std::strncpy (addr.sun_path, path.c_str (), sizeof (addr.sun_path) - 1);

    const bool connected = connect (fd, reinterpret_cast<sockaddr*> (&addr), sizeof (addr)) == 0;
    const int connectErrno = errno;
    close (fd);

    return connected || connectErrno != ECONNREFUSED;
}
} // namespace

CommandServer::CommandServer (std::filesystem::path socketPath) : m_socketPath (std::move (socketPath)) { }

CommandServer::~CommandServer () {
    for (const auto& [client, buffer] : this->m_clients) {
	close (client);
    }

    this->m_clients.clear ();

    if (this->m_listenFd >= 0) {
	close (this->m_listenFd);
	this->m_listenFd = -1;
    }

    if (this->m_ownsSocketFile) {
	std::error_code ignored;
	std::filesystem::remove (this->m_socketPath, ignored);
    }
}

std::filesystem::path CommandServer::defaultSocketPath () {
    if (const char* socketOverride = getenv ("LWE_SOCKET"); socketOverride != nullptr && *socketOverride != 0) {
	return { socketOverride };
    }

    if (const char* runtime = getenv ("XDG_RUNTIME_DIR"); runtime != nullptr && *runtime != '\0') {
	return std::filesystem::path (runtime) / "lwe" / "engine.sock";
    }

    // no runtime dir (unusual: no logind session). /tmp is world-writable, so a
    // uid-qualified subdirectory created 0700 is the only safe shape here.
    return std::filesystem::path ("/tmp") / ("lwe-" + std::to_string (geteuid ())) / "engine.sock";
}

bool CommandServer::listen () {
    const std::string path = this->m_socketPath.string ();

    if (!fitsInSunPath (path)) {
	this->m_error = "socket path too long for sockaddr_un: " + path;
	return false;
    }

    std::error_code ec;
    const bool createdDir = std::filesystem::create_directories (this->m_socketPath.parent_path (), ec);

    if (ec) {
	this->m_error = "cannot create socket directory: " + ec.message ();
	return false;
    }

    // 0700 on the directory is the outer control; the socket mode below is the inner one.
    // Only applied to a directory we created: a custom socket path in a shared directory
    // (/tmp) must not have that directory's permissions rewritten
    if (createdDir) {
	std::filesystem::permissions (
	    this->m_socketPath.parent_path (), std::filesystem::perms::owner_all,
	    std::filesystem::perm_options::replace, ec
	);
    }

    if (std::filesystem::exists (this->m_socketPath, ec)) {
	if (someoneIsListening (path)) {
	    this->m_error = "another engine is already listening on " + path;
	    return false;
	}

	sLog.out ("API: removing stale socket ", path);
	std::filesystem::remove (this->m_socketPath, ec);
    }

    this->m_listenFd = socket (AF_UNIX, SOCK_STREAM, 0);

    if (this->m_listenFd < 0) {
	this->m_error = std::string ("socket() failed: ") + std::strerror (errno);
	return false;
    }

    sockaddr_un addr {};
    addr.sun_family = AF_UNIX;
    std::strncpy (addr.sun_path, path.c_str (), sizeof (addr.sun_path) - 1);

    // bind honors the umask, so create restrictively and fix the mode explicitly below
    // rather than trusting whatever the session's umask happens to be
    const mode_t previousUmask = umask (0177);
    const bool bound = bind (this->m_listenFd, reinterpret_cast<sockaddr*> (&addr), sizeof (addr)) == 0;
    umask (previousUmask);

    if (!bound) {
	this->m_error = std::string ("bind() failed: ") + std::strerror (errno);
	close (this->m_listenFd);
	this->m_listenFd = -1;
	return false;
    }

    this->m_ownsSocketFile = true;

    if (chmod (path.c_str (), S_IRUSR | S_IWUSR) != 0) {
	this->m_error = std::string ("chmod 0600 failed: ") + std::strerror (errno);
	return false;
    }

    if (::listen (this->m_listenFd, static_cast<int> (MAX_CLIENTS)) != 0) {
	this->m_error = std::string ("listen() failed: ") + std::strerror (errno);
	return false;
    }

    setNonBlocking (this->m_listenFd);
    sLog.out ("API: listening on ", path);

    return true;
}

std::string CommandServer::peerDescription (const int fd) {
    ucred peer {};
    socklen_t length = sizeof (peer);

    if (getsockopt (fd, SOL_SOCKET, SO_PEERCRED, &peer, &length) != 0) {
	return "unknown peer";
    }

    std::string comm = "?";
    std::ifstream in ("/proc/" + std::to_string (peer.pid) + "/comm");

    if (in) {
	std::getline (in, comm);
    }

    return "pid " + std::to_string (peer.pid) + " (" + comm + ")";
}

bool CommandServer::authenticatePeer (const int fd) {
    ucred peer {};
    socklen_t length = sizeof (peer);

    if (getsockopt (fd, SOL_SOCKET, SO_PEERCRED, &peer, &length) != 0) {
	sLog.error ("API: rejecting peer, SO_PEERCRED failed: ", std::strerror (errno));
	close (fd);
	return false;
    }

    if (peer.uid != geteuid ()) {
	sLog.error ("API: rejecting peer uid ", peer.uid, " (pid ", peer.pid, "), expected ", geteuid ());
	close (fd);
	return false;
    }

    return true;
}

std::vector<int> CommandServer::fds () const {
    std::vector<int> result;
    result.reserve (this->m_clients.size () + 1);

    if (this->m_listenFd >= 0) {
	result.push_back (this->m_listenFd);
    }

    for (const auto& [client, buffer] : this->m_clients) {
	result.push_back (client);
    }

    return result;
}

std::vector<CommandServer::Request> CommandServer::drain () {
    std::vector<Request> requests;

    if (this->m_listenFd < 0) {
	return requests;
    }

    // accept everything pending; the listener is non-blocking so this ends on EAGAIN
    while (true) {
	const int client = accept (this->m_listenFd, nullptr, nullptr);

	if (client < 0) {
	    break;
	}

	if (!this->authenticatePeer (client)) {
	    continue;
	}

	if (this->m_clients.size () >= MAX_CLIENTS) {
	    sLog.error ("API: refusing connection, client limit (", MAX_CLIENTS, ") reached");
	    close (client);
	    continue;
	}

	setNonBlocking (client);
	this->m_clients.emplace (client, std::string {});
    }

    std::vector<int> closed;

    for (auto& [client, buffer] : this->m_clients) {
	while (true) {
	    char chunk[4096];
	    const ssize_t got = recv (client, chunk, sizeof (chunk), 0);

	    if (got == 0) {
		closed.push_back (client);
		break;
	    }

	    if (got < 0) {
		if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
		    closed.push_back (client);
		}

		break;
	    }

	    buffer.append (chunk, static_cast<size_t> (got));

	    // a client that never sends a newline must not grow our memory without bound
	    if (buffer.size () > MAX_LINE_BYTES) {
		sLog.error ("API: dropping client, line exceeded ", MAX_LINE_BYTES, " bytes");
		closed.push_back (client);
		break;
	    }
	}

	size_t newline;

	while ((newline = buffer.find ('\n')) != std::string::npos) {
	    std::string line = buffer.substr (0, newline);
	    buffer.erase (0, newline + 1);

	    // tolerate CRLF so a hand-driven `socat`/`nc` session behaves
	    if (!line.empty () && line.back () == '\r') {
		line.pop_back ();
	    }

	    if (!line.empty ()) {
		requests.push_back ({ client, std::move (line) });
	    }
	}
    }

    for (const int client : closed) {
	this->disconnect (client);
    }

    return requests;
}

void CommandServer::respond (const int client, const std::string& line) {
    if (!this->m_clients.contains (client)) {
	return;
    }

    const std::string payload = line + "\n";
    size_t sent = 0;
    // a client that stops reading (SIGSTOP, debugger, wedged UI) fills the socket buffer
    // and turns EAGAIN into forever on a non-blocking fd; an unbounded retry here IS a
    // render-loop hang. Wait for writability in bounded slices and drop the client when
    // the total budget runs out - a slow reader loses its connection, never the engine.
    int patienceMs = 500;

    while (sent < payload.size ()) {
	const ssize_t wrote = send (client, payload.data () + sent, payload.size () - sent, MSG_NOSIGNAL);

	if (wrote <= 0) {
	    if (wrote < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
		if (patienceMs <= 0) {
		    this->disconnect (client);
		    return;
		}

		pollfd waiter { .fd = client, .events = POLLOUT, .revents = 0 };
		constexpr int SLICE_MS = 50;
		poll (&waiter, 1, SLICE_MS);
		patienceMs -= SLICE_MS;
		continue;
	    }

	    this->disconnect (client);
	    return;
	}

	sent += static_cast<size_t> (wrote);
    }
}

void CommandServer::disconnect (const int client) {
    if (const auto it = this->m_clients.find (client); it != this->m_clients.end ()) {
	close (it->first);
	this->m_clients.erase (it);
    }
}
