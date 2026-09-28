#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <functional>
#include <mutex>
#include <optional>
#include <string>
#include <sys/socket.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

#include "WallpaperEngine/Api/CommandClient.h"
#include "WallpaperEngine/Api/CommandDispatcher.h"
#include "WallpaperEngine/Api/CommandServer.h"

using namespace WallpaperEngine::Api;

namespace {
using Answer = std::function<std::optional<std::string> (const std::string&)>;

class ScratchFolder {
public:
    ScratchFolder () {
	std::string pattern = (std::filesystem::temp_directory_path () / "lwe-client-XXXXXX").string ();
	REQUIRE (mkdtemp (pattern.data ()) != nullptr);
	this->path = pattern;
    }

    ~ScratchFolder () {
	std::error_code ignored;
	std::filesystem::remove_all (this->path, ignored);
    }

    ScratchFolder (const ScratchFolder&) = delete;
    ScratchFolder& operator= (const ScratchFolder&) = delete;

    std::filesystem::path path;
};

class ScratchServer {
public:
    ScratchServer (const std::filesystem::path& socketPath, Answer answer) :
	m_server (socketPath), m_answer (std::move (answer)) {
	REQUIRE (this->m_server.listen ());
	this->m_thread = std::thread ([this] { this->serve (); });
    }

    ~ScratchServer () {
	this->m_stop = true;
	this->m_thread.join ();
    }

    ScratchServer (const ScratchServer&) = delete;
    ScratchServer& operator= (const ScratchServer&) = delete;

    std::vector<std::string> lines () {
	std::lock_guard lock (this->m_mutex);
	return this->m_lines;
    }

private:
    void serve () {
	while (!this->m_stop) {
	    for (const auto& request : this->m_server.drain ()) {
		{
		    std::lock_guard lock (this->m_mutex);
		    this->m_lines.push_back (request.line);
		}

		if (const auto reply = this->m_answer (request.line); reply.has_value ()) {
		    this->m_server.respond (request.client, *reply);
		}
	    }

	    usleep (1000);
	}
    }

    CommandServer m_server;
    Answer m_answer;
    std::atomic<bool> m_stop = false;
    std::mutex m_mutex;
    std::vector<std::string> m_lines;
    std::thread m_thread;
};

std::string paddedReply (const size_t size) {
    std::string reply = CommandDispatcher::done (1, { { "pad", "" } });
    reply.insert (reply.find ("\"pad\":\"") + 7, size - reply.size (), 'x');
    return reply;
}
} // namespace

TEST_CASE ("the client sends one request line and returns the engine's reply", "[lwe]") {
    const ScratchFolder folder;
    const auto path = folder.path / "engine.sock";
    ScratchServer server (path, [] (const std::string& line) -> std::optional<std::string> {
	if (line.find ("\"acquire-outputs\"") != std::string::npos) {
	    return CommandDispatcher::failure (1, "driver could not re-acquire outputs");
	}

	return CommandDispatcher::done (1, { { "outputs", "released" } });
    });

    const auto released = CommandClient::request (path, "release-outputs");
    REQUIRE (released.outcome == CommandClient::Outcome::Reply);
    CHECK (released.reply.at ("ok") == true);
    CHECK (released.reply.at ("result") == nlohmann::json { { "outputs", "released" } });

    const auto refused = CommandClient::request (path, "acquire-outputs");
    REQUIRE (refused.outcome == CommandClient::Outcome::Reply);
    CHECK (refused.reply.at ("ok") == false);
    CHECK (refused.reply.at ("error") == "driver could not re-acquire outputs");

    CHECK (
	server.lines ()
	== std::vector<std::string> { R"({"cmd":"release-outputs","id":1})", R"({"cmd":"acquire-outputs","id":1})" }
    );
}

TEST_CASE ("the client reports not running when the socket file has no listener", "[lwe]") {
    const ScratchFolder folder;
    const auto path = folder.path / "engine.sock";
    const int stale = socket (AF_UNIX, SOCK_STREAM, 0);
    REQUIRE (stale >= 0);

    sockaddr_un address {};
    address.sun_family = AF_UNIX;
    std::strncpy (address.sun_path, path.c_str (), sizeof (address.sun_path) - 1);
    const bool bound = bind (stale, reinterpret_cast<sockaddr*> (&address), sizeof (address)) == 0;
    close (stale);
    REQUIRE (bound);

    const auto response = CommandClient::request (path, "status");
    CHECK (response.outcome == CommandClient::Outcome::NotRunning);
    CHECK (response.error == ECONNREFUSED);
}

TEST_CASE ("the client reports not running when there is no socket file", "[lwe]") {
    const ScratchFolder folder;

    const auto response = CommandClient::request (folder.path / "engine.sock", "status");
    CHECK (response.outcome == CommandClient::Outcome::NotRunning);
    CHECK (response.error == ENOENT);
}

TEST_CASE ("the client gives up three seconds after it asked", "[lwe]") {
    const ScratchFolder folder;
    const auto path = folder.path / "engine.sock";
    ScratchServer server (path, [] (const std::string&) -> std::optional<std::string> { return std::nullopt; });

    const auto asked = std::chrono::steady_clock::now ();
    const auto response = CommandClient::request (path, "status");
    const auto waited = std::chrono::steady_clock::now () - asked;

    CHECK (response.outcome == CommandClient::Outcome::NoAnswer);
    CHECK (waited >= std::chrono::milliseconds (2900));
    CHECK (waited < std::chrono::milliseconds (6000));
    CHECK (server.lines () == std::vector<std::string> { R"({"cmd":"status","id":1})" });
}

TEST_CASE ("the client reads a reply of up to 64 KiB and refuses a longer one", "[lwe]") {
    const ScratchFolder folder;
    const auto path = folder.path / "engine.sock";

    for (const size_t size : { CommandClient::MAX_REPLY_BYTES, CommandClient::MAX_REPLY_BYTES + 1 }) {
	CAPTURE (size);
	const auto reply = paddedReply (size);
	REQUIRE (reply.size () == size);
	ScratchServer server (path, [reply] (const std::string&) -> std::optional<std::string> { return reply; });

	const auto response = CommandClient::request (path, "status");
	CHECK (
	    response.outcome
	    == (size == CommandClient::MAX_REPLY_BYTES ? CommandClient::Outcome::Reply
						       : CommandClient::Outcome::BadReply)
	);
    }
}

TEST_CASE ("the client refuses a reply that is not a reply object", "[lwe]") {
    const ScratchFolder folder;
    const auto path = folder.path / "engine.sock";

    for (const std::string reply : { "this is not json", "[1,2]", R"({"id":1})", R"({"id":1,"ok":"yes","result":{}})",
				     R"({"id":1,"ok":true})", R"({"id":1,"ok":false})" }) {
	CAPTURE (reply);
	ScratchServer server (path, [reply] (const std::string&) -> std::optional<std::string> { return reply; });

	CHECK (CommandClient::request (path, "status").outcome == CommandClient::Outcome::BadReply);
    }
}
