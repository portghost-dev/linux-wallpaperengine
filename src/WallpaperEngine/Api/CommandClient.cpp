#include "CommandClient.h"

#include <cerrno>
#include <chrono>
#include <cstring>
#include <poll.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>

using namespace WallpaperEngine::Api;

namespace {
using Clock = std::chrono::steady_clock;

class Descriptor {
public:
    explicit Descriptor (const int fd) : m_fd (fd) { }

    ~Descriptor () {
	if (this->m_fd >= 0) {
	    close (this->m_fd);
	}
    }

    Descriptor (const Descriptor&) = delete;
    Descriptor& operator= (const Descriptor&) = delete;

    [[nodiscard]] int get () const { return this->m_fd; }

private:
    int m_fd;
};

std::chrono::milliseconds left (const Clock::time_point deadline) {
    return std::chrono::duration_cast<std::chrono::milliseconds> (deadline - Clock::now ());
}

bool setSendTimeout (const int fd, const std::chrono::milliseconds timeout) {
    timeval limit {};
    limit.tv_sec = static_cast<time_t> (timeout.count () / 1000);
    limit.tv_usec = static_cast<suseconds_t> ((timeout.count () % 1000) * 1000);

    return setsockopt (fd, SOL_SOCKET, SO_SNDTIMEO, &limit, sizeof (limit)) == 0;
}

CommandClient::Response readReply (const std::string& line) {
    auto reply = nlohmann::json::parse (line, nullptr, false);

    if (reply.is_discarded () || !reply.is_object ()) {
	return { .outcome = CommandClient::Outcome::BadReply, .reply = nullptr, .error = 0 };
    }

    const auto ok = reply.find ("ok");

    if (ok == reply.end () || !ok->is_boolean ()) {
	return { .outcome = CommandClient::Outcome::BadReply, .reply = nullptr, .error = 0 };
    }

    const auto payload = reply.find (ok->get<bool> () ? "result" : "error");

    if (payload == reply.end () || (ok->get<bool> () ? !payload->is_object () : !payload->is_string ())) {
	return { .outcome = CommandClient::Outcome::BadReply, .reply = nullptr, .error = 0 };
    }

    return { .outcome = CommandClient::Outcome::Reply, .reply = std::move (reply), .error = 0 };
}
} // namespace

CommandClient::Response CommandClient::request (const std::filesystem::path& socketPath, const std::string& cmd) {
    const auto deadline = Clock::now () + std::chrono::milliseconds (DEADLINE_MS);
    const std::string path = socketPath.string ();
    sockaddr_un address {};

    if (path.size () >= sizeof (address.sun_path)) {
	return { .outcome = Outcome::NotRunning, .reply = nullptr, .error = ENAMETOOLONG };
    }

    address.sun_family = AF_UNIX;
    std::memcpy (address.sun_path, path.c_str (), path.size () + 1);

    const Descriptor client (socket (AF_UNIX, SOCK_STREAM, 0));

    if (client.get () < 0) {
	return { .outcome = Outcome::NotRunning, .reply = nullptr, .error = errno };
    }

    while (true) {
	const auto remaining = left (deadline);

	if (remaining.count () <= 0 || !setSendTimeout (client.get (), remaining)) {
	    return { .outcome = Outcome::NoAnswer, .reply = nullptr, .error = 0 };
	}

	if (connect (client.get (), reinterpret_cast<const sockaddr*> (&address), sizeof (address)) == 0) {
	    break;
	}

	const int error = errno;

	if (error == EINTR) {
	    continue;
	}

	if (error == EAGAIN || error == EWOULDBLOCK) {
	    return { .outcome = Outcome::NoAnswer, .reply = nullptr, .error = 0 };
	}

	return { .outcome = Outcome::NotRunning, .reply = nullptr, .error = error };
    }

    const std::string line = nlohmann::json { { "id", 1 }, { "cmd", cmd } }.dump () + "\n";
    size_t sent = 0;

    while (sent < line.size ()) {
	const auto remaining = left (deadline);

	if (remaining.count () <= 0 || !setSendTimeout (client.get (), remaining)) {
	    return { .outcome = Outcome::NoAnswer, .reply = nullptr, .error = 0 };
	}

	const ssize_t wrote = send (client.get (), line.data () + sent, line.size () - sent, MSG_NOSIGNAL);

	if (wrote < 0 && errno == EINTR) {
	    continue;
	}

	if (wrote <= 0) {
	    const bool timedOut = wrote < 0 && (errno == EAGAIN || errno == EWOULDBLOCK);

	    return { .outcome = timedOut ? Outcome::NoAnswer : Outcome::Closed, .reply = nullptr, .error = 0 };
	}

	sent += static_cast<size_t> (wrote);
    }

    std::string buffer;

    while (true) {
	if (const auto newline = buffer.find ('\n'); newline != std::string::npos) {
	    if (newline > MAX_REPLY_BYTES) {
		return { .outcome = Outcome::BadReply, .reply = nullptr, .error = 0 };
	    }

	    return readReply (buffer.substr (0, newline));
	}

	if (buffer.size () > MAX_REPLY_BYTES) {
	    return { .outcome = Outcome::BadReply, .reply = nullptr, .error = 0 };
	}

	const auto remaining = left (deadline);

	if (remaining.count () <= 0) {
	    return { .outcome = Outcome::NoAnswer, .reply = nullptr, .error = 0 };
	}

	pollfd readable { .fd = client.get (), .events = POLLIN, .revents = 0 };
	const int polled = poll (&readable, 1, static_cast<int> (remaining.count ()));

	if (polled < 0 && errno == EINTR) {
	    continue;
	}

	if (polled <= 0) {
	    return { .outcome = Outcome::NoAnswer, .reply = nullptr, .error = 0 };
	}

	char chunk[4096];
	const ssize_t got = recv (client.get (), chunk, sizeof (chunk), 0);

	if (got < 0 && errno == EINTR) {
	    continue;
	}

	if (got <= 0) {
	    return { .outcome = buffer.empty () ? Outcome::Closed : Outcome::BadReply, .reply = nullptr, .error = 0 };
	}

	buffer.append (chunk, static_cast<size_t> (got));
    }
}
