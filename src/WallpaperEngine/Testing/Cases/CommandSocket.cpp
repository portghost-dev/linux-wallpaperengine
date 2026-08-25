#include <catch2/catch_test_macros.hpp>

#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include "WallpaperEngine/Api/CommandServer.h"

using namespace WallpaperEngine::Api;

namespace {

std::filesystem::path scratchSocket (const std::string& name) {
    return std::filesystem::temp_directory_path () / ("lwe-test-" + std::to_string (getpid ()) + "-" + name)
	/ "engine.sock";
}

void cleanup (const std::filesystem::path& socketPath) {
    std::error_code ignored;
    std::filesystem::remove_all (socketPath.parent_path (), ignored);
}

int connectTo (const std::filesystem::path& socketPath) {
    const int fd = socket (AF_UNIX, SOCK_STREAM, 0);
    REQUIRE (fd >= 0);

    sockaddr_un addr {};
    addr.sun_family = AF_UNIX;
    std::strncpy (addr.sun_path, socketPath.c_str (), sizeof (addr.sun_path) - 1);

    if (connect (fd, reinterpret_cast<sockaddr*> (&addr), sizeof (addr)) != 0) {
	close (fd);
	return -1;
    }

    return fd;
}

void writeAll (const int fd, const std::string& data) {
    size_t sent = 0;

    while (sent < data.size ()) {
	const ssize_t wrote = send (fd, data.data () + sent, data.size () - sent, MSG_NOSIGNAL);
	REQUIRE (wrote > 0);
	sent += static_cast<size_t> (wrote);
    }
}

std::vector<CommandServer::Request> drainUntil (CommandServer& server, const size_t expected) {
    std::vector<CommandServer::Request> collected;

    for (int attempt = 0; attempt < 200; attempt++) {
	for (auto& request : server.drain ()) {
	    collected.push_back (std::move (request));
	}

	if (collected.size () >= expected) {
	    break;
	}

	usleep (1000);
    }

    return collected;
}

void waitForClients (CommandServer& server, const size_t expected) {
    for (int attempt = 0; attempt < 200 && server.clientCount () < expected; attempt++) {
	server.drain ();

	if (server.clientCount () < expected) {
	    usleep (1000);
	}
    }
}
} // namespace

TEST_CASE ("CommandServer binds with restrictive permissions", "[api]") {
    const auto path = scratchSocket ("perms");
    cleanup (path);

    CommandServer server (path);
    REQUIRE (server.listen ());
    REQUIRE (server.isListening ());
    REQUIRE (std::filesystem::exists (path));

    struct stat socketStat {};
    REQUIRE (stat (path.c_str (), &socketStat) == 0);
    // 0600 exactly: no group, no other. The socket is a control channel for the desktop.
    REQUIRE ((socketStat.st_mode & 0777) == 0600);

    struct stat dirStat {};
    REQUIRE (stat (path.parent_path ().c_str (), &dirStat) == 0);
    REQUIRE ((dirStat.st_mode & 0777) == 0700);

    cleanup (path);
}

TEST_CASE ("CommandServer refuses to displace a live instance", "[api]") {
    const auto path = scratchSocket ("single");
    cleanup (path);

    CommandServer first (path);
    REQUIRE (first.listen ());

    CommandServer second (path);
    REQUIRE_FALSE (second.listen ());
    REQUIRE_FALSE (second.error ().empty ());

    const int client = connectTo (path);
    REQUIRE (client >= 0);
    close (client);

    cleanup (path);
}

TEST_CASE ("CommandServer reclaims a stale socket file", "[api]") {
    const auto path = scratchSocket ("stale");
    cleanup (path);

    {
	CommandServer previous (path);
	REQUIRE (previous.listen ());
    }

    std::filesystem::create_directories (path.parent_path ());
    { std::ofstream orphan (path); }
    REQUIRE (std::filesystem::exists (path));

    CommandServer server (path);
    REQUIRE (server.listen ());

    cleanup (path);
}

TEST_CASE ("CommandServer frames lines", "[api]") {
    const auto path = scratchSocket ("framing");
    cleanup (path);

    CommandServer server (path);
    REQUIRE (server.listen ());

    const int client = connectTo (path);
    REQUIRE (client >= 0);

    SECTION ("two commands in one write arrive as two requests") {
	writeAll (client, "{\"cmd\":\"status\"}\n{\"cmd\":\"quit\"}\n");
	const auto requests = drainUntil (server, 2);

	REQUIRE (requests.size () == 2);
	REQUIRE (requests[0].line == "{\"cmd\":\"status\"}");
	REQUIRE (requests[1].line == "{\"cmd\":\"quit\"}");
    }

    SECTION ("a command split across writes is buffered until its newline") {
	writeAll (client, "{\"cmd\":\"sta");
	REQUIRE (server.drain ().empty ());

	writeAll (client, "tus\"}\n");
	const auto requests = drainUntil (server, 1);

	REQUIRE (requests.size () == 1);
	REQUIRE (requests[0].line == "{\"cmd\":\"status\"}");
    }

    SECTION ("CRLF is tolerated so a hand-driven session behaves") {
	writeAll (client, "{\"cmd\":\"status\"}\r\n");
	const auto requests = drainUntil (server, 1);

	REQUIRE (requests.size () == 1);
	REQUIRE (requests[0].line == "{\"cmd\":\"status\"}");
    }

    SECTION ("blank lines are ignored rather than dispatched as empty commands") {
	writeAll (client, "\n\n{\"cmd\":\"status\"}\n");
	const auto requests = drainUntil (server, 1);

	REQUIRE (requests.size () == 1);
	REQUIRE (requests[0].line == "{\"cmd\":\"status\"}");
    }

    close (client);
    cleanup (path);
}

TEST_CASE ("CommandServer drops a client that never terminates its line", "[api]") {
    const auto path = scratchSocket ("overflow");
    cleanup (path);

    CommandServer server (path);
    REQUIRE (server.listen ());

    const int client = connectTo (path);
    REQUIRE (client >= 0);
    waitForClients (server, 1);
    REQUIRE (server.clientCount () == 1);

    // no newline, ever: unbounded buffering would be a memory exhaustion path for any
    // same-uid process
    const std::string flood (CommandServer::MAX_LINE_BYTES + 4096, 'x');
    writeAll (client, flood);

    for (int attempt = 0; attempt < 200 && server.clientCount () > 0; attempt++) {
	server.drain ();
	usleep (1000);
    }

    REQUIRE (server.clientCount () == 0);

    close (client);
    cleanup (path);
}

TEST_CASE ("CommandServer responds on the same connection", "[api]") {
    const auto path = scratchSocket ("respond");
    cleanup (path);

    CommandServer server (path);
    REQUIRE (server.listen ());

    const int client = connectTo (path);
    REQUIRE (client >= 0);

    writeAll (client, "{\"cmd\":\"status\"}\n");
    const auto requests = drainUntil (server, 1);
    REQUIRE (requests.size () == 1);

    server.respond (requests[0].client, "{\"ok\":true}");

    char buffer[256] = {};
    const ssize_t got = recv (client, buffer, sizeof (buffer) - 1, 0);
    REQUIRE (got > 0);
    REQUIRE (std::string (buffer) == "{\"ok\":true}\n");

    close (client);
    cleanup (path);
}

TEST_CASE ("CommandServer exposes every fd the caller must poll", "[api]") {
    const auto path = scratchSocket ("fds");
    cleanup (path);

    CommandServer server (path);
    REQUIRE (server.listen ());

    REQUIRE (server.fds ().size () == 1);

    const int client = connectTo (path);
    REQUIRE (client >= 0);
    waitForClients (server, 1);

    REQUIRE (server.fds ().size () == 2);

    close (client);
    cleanup (path);
}

TEST_CASE ("CommandServer honors the LWE_SOCKET override", "[api]") {
    setenv ("LWE_SOCKET", "/tmp/lwe-override-probe.sock", 1);
    REQUIRE (CommandServer::defaultSocketPath () == std::filesystem::path ("/tmp/lwe-override-probe.sock"));
    unsetenv ("LWE_SOCKET");

    setenv ("XDG_RUNTIME_DIR", "/run/user/testing", 1);
    REQUIRE (CommandServer::defaultSocketPath () == std::filesystem::path ("/run/user/testing/lwe/engine.sock"));
    unsetenv ("XDG_RUNTIME_DIR");
}
