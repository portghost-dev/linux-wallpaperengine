#include "MessageChannel.h"

#include "WallpaperEngine/Logging/Log.h"

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

using namespace WallpaperEngine::WebHelper;

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

/** true if the peer on this fd shares our uid; closes and returns false otherwise */
bool authenticatePeer (const int fd) {
    ucred peer {};
    socklen_t length = sizeof (peer);

    if (getsockopt (fd, SOL_SOCKET, SO_PEERCRED, &peer, &length) != 0) {
	sLog.error ("web-helper: rejecting peer, SO_PEERCRED failed: ", std::strerror (errno));
	close (fd);
	return false;
    }

    if (peer.uid != geteuid ()) {
	sLog.error ("web-helper: rejecting peer uid ", peer.uid, " (pid ", peer.pid, "), expected ", geteuid ());
	close (fd);
	return false;
    }

    return true;
}

/**
 * Is something listening on this socket right now? Same test CommandServer uses: a
 * successful connect means a live owner and the file must not be removed, ECONNREFUSED
 * means it outlived its process. Anything ambiguous is treated as occupied.
 */
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

MessageChannel::MessageChannel (const int fd) : m_fd (fd) {
    if (this->m_fd >= 0) {
	setNonBlocking (this->m_fd);
    }
}

MessageChannel::~MessageChannel () { this->close (); }

MessageChannel::MessageChannel (MessageChannel&& other) noexcept :
    m_fd (other.m_fd), m_inbound (std::move (other.m_inbound)), m_outbound (std::move (other.m_outbound)),
    m_error (std::move (other.m_error)) {
    other.m_fd = -1;
}

MessageChannel& MessageChannel::operator= (MessageChannel&& other) noexcept {
    if (this != &other) {
	this->close ();
	this->m_fd = other.m_fd;
	this->m_inbound = std::move (other.m_inbound);
	this->m_outbound = std::move (other.m_outbound);
	this->m_error = std::move (other.m_error);
	other.m_fd = -1;
    }

    return *this;
}

void MessageChannel::close () {
    if (this->m_fd >= 0) {
	::close (this->m_fd);
	this->m_fd = -1;
    }

    this->m_inbound.clear ();
    this->m_outbound.clear ();
}

bool MessageChannel::send (const std::vector<uint8_t>& framed) {
    if (!this->isOpen ()) {
	return false;
    }

    this->m_outbound.insert (this->m_outbound.end (), framed.begin (), framed.end ());

    if (this->m_outbound.size () > MAX_QUEUED_BYTES) {
	this->m_error = "outbound queue exceeded " + std::to_string (MAX_QUEUED_BYTES) + " bytes; peer is not reading";
	this->close ();
	return false;
    }

    return this->flush ();
}

bool MessageChannel::flush () {
    if (!this->isOpen ()) {
	return false;
    }

    while (!this->m_outbound.empty ()) {
	// deque is not contiguous, so stage a chunk; 64 KiB keeps the copy trivial next to
	// a 14.7 MB frame and is well past any single control message
	uint8_t chunk[64 * 1024];
	const size_t count = std::min (sizeof (chunk), this->m_outbound.size ());
	std::copy_n (this->m_outbound.begin (), count, chunk);

	// MSG_NOSIGNAL: a peer that hangs up mid-write is a disconnect, not a SIGPIPE death
	const ssize_t wrote = ::send (this->m_fd, chunk, count, MSG_NOSIGNAL);

	if (wrote > 0) {
	    this->m_outbound.erase (this->m_outbound.begin (), this->m_outbound.begin () + wrote);
	    continue;
	}

	if (wrote < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
	    // socket is full; the rest stays queued for the next flush
	    return true;
	}

	if (wrote < 0 && errno == EINTR) {
	    continue;
	}

	this->m_error = std::string ("send() failed: ") + std::strerror (errno);
	this->close ();

	return false;
    }

    return true;
}

std::vector<Message> MessageChannel::receive () {
    std::vector<Message> messages;

    if (!this->isOpen ()) {
	return messages;
    }

    while (true) {
	uint8_t chunk[64 * 1024];
	const ssize_t got = recv (this->m_fd, chunk, sizeof (chunk), 0);

	if (got == 0) {
	    // orderly shutdown - this IS the helper-died event on the engine side
	    this->m_error = "peer closed the connection";
	    this->close ();
	    return messages;
	}

	if (got < 0) {
	    if (errno == EAGAIN || errno == EWOULDBLOCK) {
		break;
	    }

	    if (errno == EINTR) {
		continue;
	    }

	    this->m_error = std::string ("recv() failed: ") + std::strerror (errno);
	    this->close ();

	    return messages;
	}

	this->m_inbound.insert (this->m_inbound.end (), chunk, chunk + got);
    }

    while (this->m_inbound.size () >= sizeof (MessageHeader)) {
	MessageHeader header {};
	std::memcpy (&header, this->m_inbound.data (), sizeof (header));

	if (header.length > MAX_PAYLOAD_BYTES) {
	    this->m_error = "peer announced a " + std::to_string (header.length) + " byte payload; refusing";
	    this->close ();
	    return messages;
	}

	const size_t total = sizeof (MessageHeader) + header.length;

	if (this->m_inbound.size () < total) {
	    break;
	}

	Message message;
	message.type = static_cast<MessageType> (header.type);
	message.payload.assign (
	    this->m_inbound.begin () + sizeof (MessageHeader), this->m_inbound.begin () + static_cast<long> (total)
	);
	messages.push_back (std::move (message));

	this->m_inbound.erase (this->m_inbound.begin (), this->m_inbound.begin () + static_cast<long> (total));
    }

    return messages;
}

MessageListener::MessageListener (std::filesystem::path socketPath) : m_socketPath (std::move (socketPath)) { }

MessageListener::~MessageListener () {
    if (this->m_listenFd >= 0) {
	close (this->m_listenFd);
	this->m_listenFd = -1;
    }

    // only remove the socket file if this instance created it
    if (this->m_ownsSocketFile) {
	std::error_code ignored;
	std::filesystem::remove (this->m_socketPath, ignored);
    }
}

bool MessageListener::listen () {
    const std::string path = this->m_socketPath.string ();

    if (!fitsInSunPath (path)) {
	this->m_error = "socket path too long for sockaddr_un: " + path;
	return false;
    }

    std::error_code ec;
    std::filesystem::create_directories (this->m_socketPath.parent_path (), ec);

    if (ec) {
	this->m_error = "cannot create socket directory: " + ec.message ();
	return false;
    }

    // 0700 on the directory is the outer control; the socket mode below is the inner one
    std::filesystem::permissions (
	this->m_socketPath.parent_path (), std::filesystem::perms::owner_all, std::filesystem::perm_options::replace, ec
    );

    if (std::filesystem::exists (this->m_socketPath, ec)) {
	if (someoneIsListening (path)) {
	    this->m_error = "another web helper is already listening on " + path;
	    return false;
	}

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

    // backlog of 1: exactly one engine ever connects to a helper it spawned itself
    if (::listen (this->m_listenFd, 1) != 0) {
	this->m_error = std::string ("listen() failed: ") + std::strerror (errno);
	return false;
    }

    setNonBlocking (this->m_listenFd);

    return true;
}

std::optional<MessageChannel> MessageListener::accept () {
    if (this->m_listenFd < 0) {
	return std::nullopt;
    }

    const int client = ::accept (this->m_listenFd, nullptr, nullptr);

    if (client < 0) {
	return std::nullopt;
    }

    if (!authenticatePeer (client)) {
	return std::nullopt;
    }

    return MessageChannel (client);
}

std::optional<MessageChannel>
WallpaperEngine::WebHelper::connectToHelper (const std::filesystem::path& socketPath, std::string& error) {
    const std::string path = socketPath.string ();

    if (!fitsInSunPath (path)) {
	error = "socket path too long for sockaddr_un: " + path;
	return std::nullopt;
    }

    const int fd = socket (AF_UNIX, SOCK_STREAM, 0);

    if (fd < 0) {
	error = std::string ("socket() failed: ") + std::strerror (errno);
	return std::nullopt;
    }

    sockaddr_un addr {};
    addr.sun_family = AF_UNIX;
    std::strncpy (addr.sun_path, path.c_str (), sizeof (addr.sun_path) - 1);

    // connect BEFORE going non-blocking: the helper is a child we just forked, the socket
    // is local, and a blocking connect here is microseconds. MessageChannel's constructor
    // sets O_NONBLOCK afterwards, which is what the render loop actually needs.
    if (connect (fd, reinterpret_cast<sockaddr*> (&addr), sizeof (addr)) != 0) {
	error = std::string ("connect() failed: ") + std::strerror (errno);
	close (fd);
	return std::nullopt;
    }

    return MessageChannel (fd);
}
