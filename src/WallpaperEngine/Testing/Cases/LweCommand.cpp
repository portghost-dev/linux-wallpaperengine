#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iterator>
#include <mutex>
#include <optional>
#include <poll.h>
#include <spawn.h>
#include <string>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

#include "WallpaperEngine/Api/CommandDispatcher.h"
#include "WallpaperEngine/Api/CommandServer.h"
#include "WallpaperEngine/Application/LweCommand.h"

using namespace WallpaperEngine::Application;
using WallpaperEngine::Api::CommandDispatcher;
using WallpaperEngine::Api::CommandServer;
using Action = LweCommand::Action;

namespace {
using Answer = std::function<std::optional<std::string> (const std::string&)>;

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

struct Finished {
    bool timedOut = false;
    int status = -1;
    std::string out;
    std::string err;
};

class ScratchLwe {
public:
    ScratchLwe () {
	std::string pattern = (std::filesystem::temp_directory_path () / "lwe-name-XXXXXX").string ();
	REQUIRE (mkdtemp (pattern.data ()) != nullptr);
	this->root = std::filesystem::canonical (pattern);

	for (const char* folder : { "bin", "run", "state", "config", "home", "work" }) {
	    std::filesystem::create_directories (this->root / folder);
	}

	const auto engine = std::filesystem::read_symlink ("/proc/self/exe").parent_path () / "linux-wallpaperengine";
	REQUIRE (std::filesystem::exists (engine));
	std::filesystem::create_symlink (engine, this->root / "bin" / "lwe");
    }

    ~ScratchLwe () {
	std::error_code ignored;
	std::filesystem::remove_all (this->root, ignored);
    }

    ScratchLwe (const ScratchLwe&) = delete;
    ScratchLwe& operator= (const ScratchLwe&) = delete;

    [[nodiscard]] std::filesystem::path socket () const { return this->root / "run" / "lwe" / "engine.sock"; }

    [[nodiscard]] std::filesystem::path work () const { return this->root / "work"; }

    [[nodiscard]] std::vector<std::string> environment () const {
	return {
	    "LWE_SOCKET=" + this->socket ().string (),
	    "XDG_RUNTIME_DIR=" + (this->root / "run").string (),
	    "XDG_STATE_HOME=" + (this->root / "state").string (),
	    "XDG_CONFIG_HOME=" + (this->root / "config").string (),
	    "HOME=" + (this->root / "home").string (),
	    "PATH=" + (this->root / "bin").string (),
	};
    }

    [[nodiscard]] Finished
    run (const std::vector<std::string>& words, const std::vector<std::string>& extra = {}) const {
	std::vector<std::string> variables = this->environment ();
	variables.insert (variables.end (), extra.begin (), extra.end ());
	std::vector<std::string> arguments = { "lwe" };
	arguments.insert (arguments.end (), words.begin (), words.end ());

	std::vector<char*> envp;
	std::vector<char*> argv;

	for (auto& entry : variables) {
	    envp.push_back (entry.data ());
	}

	for (auto& argument : arguments) {
	    argv.push_back (argument.data ());
	}

	envp.push_back (nullptr);
	argv.push_back (nullptr);

	int out[2];
	int err[2];
	REQUIRE (pipe2 (out, O_CLOEXEC) == 0);
	REQUIRE (pipe2 (err, O_CLOEXEC) == 0);

	posix_spawn_file_actions_t actions;
	posix_spawn_file_actions_init (&actions);
	posix_spawn_file_actions_addopen (&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0);
	posix_spawn_file_actions_adddup2 (&actions, out[1], STDOUT_FILENO);
	posix_spawn_file_actions_adddup2 (&actions, err[1], STDERR_FILENO);
	posix_spawn_file_actions_addchdir_np (&actions, this->work ().c_str ());

	const std::string program = (this->root / "bin" / "lwe").string ();
	pid_t child = 0;
	const int spawned = posix_spawn (&child, program.c_str (), &actions, nullptr, argv.data (), envp.data ());

	posix_spawn_file_actions_destroy (&actions);
	close (out[1]);
	close (err[1]);

	if (spawned != 0) {
	    close (out[0]);
	    close (err[0]);
	    FAIL ("posix_spawn failed for " << program << ": " << std::strerror (spawned));
	}

	Finished finished;
	pollfd readers[2]
	    = { { .fd = out[0], .events = POLLIN, .revents = 0 }, { .fd = err[0], .events = POLLIN, .revents = 0 } };
	std::string* sinks[2] = { &finished.out, &finished.err };
	const auto deadline = std::chrono::steady_clock::now () + std::chrono::seconds (10);

	while (readers[0].fd >= 0 || readers[1].fd >= 0) {
	    const auto left
		= std::chrono::duration_cast<std::chrono::milliseconds> (deadline - std::chrono::steady_clock::now ());

	    if (left.count () <= 0) {
		finished.timedOut = true;
		break;
	    }

	    const int polled = poll (readers, 2, static_cast<int> (left.count ()));

	    if (polled < 0 && errno == EINTR) {
		continue;
	    }

	    if (polled <= 0) {
		finished.timedOut = polled == 0;
		break;
	    }

	    for (int stream = 0; stream < 2; stream++) {
		if (readers[stream].fd < 0 || readers[stream].revents == 0) {
		    continue;
		}

		char buffer[4096];
		const ssize_t count = read (readers[stream].fd, buffer, sizeof (buffer));

		if (count > 0) {
		    sinks[stream]->append (buffer, static_cast<size_t> (count));
		} else if (count == 0 || errno != EINTR) {
		    close (readers[stream].fd);
		    readers[stream].fd = -1;
		}
	    }
	}

	for (const auto& reader : readers) {
	    if (reader.fd >= 0) {
		close (reader.fd);
	    }
	}

	if (finished.timedOut) {
	    kill (child, SIGKILL);
	}

	while (waitpid (child, &finished.status, 0) < 0 && errno == EINTR) { }

	return finished;
    }

    std::filesystem::path root;
};

nlohmann::json cannedStatus (const std::string& version) {
    return { { "version", version },
	     { "current", { { "id", "1505438974" }, { "ui_id", "1505438974" }, { "title", "Deep Space" } } },
	     { "rotation",
	       { { "enabled", true },
		 { "label", "All Wallpapers" },
		 { "order", "sequential" },
		 { "interval_s", 900 },
		 { "next_in_s", 422 } } },
	     { "outputs", { { "state", "live" }, { "reason", "" } } },
	     { "volume", 15 },
	     { "speed", 1.0 },
	     { "fps", 60 },
	     { "mouse", true },
	     { "parallax", true },
	     { "particles", true } };
}

std::string liveText (const std::string& version) {
    const std::string settings = "settings    volume 15, speed 1, fps 60, mouse on, parallax on, particles on\n";

    return std::string ("on screen   Deep Space (1505438974)\n")
	+ "playlist    All Wallpapers, sequential, next in 7m 02s\n" + "screens     on\n" + "version     " + version
	+ "\n" + settings;
}

class ClosingServer {
public:
    explicit ClosingServer (const std::filesystem::path& socketPath) : m_path (socketPath) {
	std::filesystem::create_directories (socketPath.parent_path ());
	this->m_listener = ::socket (AF_UNIX, SOCK_STREAM, 0);
	REQUIRE (this->m_listener >= 0);

	sockaddr_un address {};
	address.sun_family = AF_UNIX;
	std::strncpy (address.sun_path, socketPath.c_str (), sizeof (address.sun_path) - 1);
	REQUIRE (bind (this->m_listener, reinterpret_cast<sockaddr*> (&address), sizeof (address)) == 0);
	REQUIRE (listen (this->m_listener, 8) == 0);

	this->m_thread = std::thread ([this] {
	    pollfd readable { .fd = this->m_listener, .events = POLLIN, .revents = 0 };

	    if (poll (&readable, 1, 10000) == 1) {
		if (const int client = accept (this->m_listener, nullptr, nullptr); client >= 0) {
		    close (client);
		}
	    }
	});
    }

    ~ClosingServer () {
	this->m_thread.join ();
	close (this->m_listener);
	std::error_code ignored;
	std::filesystem::remove (this->m_path, ignored);
    }

    ClosingServer (const ClosingServer&) = delete;
    ClosingServer& operator= (const ClosingServer&) = delete;

private:
    std::filesystem::path m_path;
    int m_listener = -1;
    std::thread m_thread;
};

Answer engine (const std::string& version, const bool refuseAcquire = false) {
    const auto status = CommandDispatcher::done (1, cannedStatus (version));

    return [status, refuseAcquire] (const std::string& line) -> std::optional<std::string> {
	if (line.find ("\"release-outputs\"") != std::string::npos) {
	    return CommandDispatcher::done (1, { { "outputs", "released" } });
	}

	if (line.find ("\"acquire-outputs\"") != std::string::npos) {
	    return refuseAcquire ? CommandDispatcher::failure (1, "driver could not re-acquire outputs")
				 : CommandDispatcher::done (1, { { "outputs", "live" } });
	}

	return status;
    };
}

void writeScript (const std::filesystem::path& path, const std::string& body) {
    std::filesystem::create_directories (path.parent_path ());

    {
	std::ofstream script (path);
	script << "#!/bin/sh\n" << body;
    }

    std::filesystem::permissions (path, std::filesystem::perms::owner_all, std::filesystem::perm_options::replace);
}

std::vector<std::string> recordedFields (const std::filesystem::path& record) {
    std::ifstream recorded (record, std::ios::binary);
    const std::string content ((std::istreambuf_iterator<char> (recorded)), std::istreambuf_iterator<char> ());
    std::vector<std::string> fields;

    for (std::string::size_type start = 0; start < content.size ();) {
	const auto end = content.find ('\0', start);

	if (end == std::string::npos) {
	    break;
	}

	fields.push_back (content.substr (start, end - start));
	start = end + 1;
    }

    return fields;
}

bool named (const std::vector<const char*>& words) {
    std::vector<char*> argv;

    for (const char* word : words) {
	argv.push_back (const_cast<char*> (word));
    }

    argv.push_back (nullptr);
    return LweCommand::isLweName (static_cast<int> (words.size ()), argv.data ());
}
} // namespace

TEST_CASE ("lwe reads its own verbs, takes -j anywhere and leaves every other verb to the panel", "[lwe]") {
    SECTION ("the name") {
	CHECK (named ({ "lwe" }));
	CHECK (named ({ "./lwe", "status" }));
	CHECK (named ({ "/usr/bin/lwe" }));
	CHECK_FALSE (named ({ "linux-wallpaperengine" }));
	CHECK_FALSE (named ({ "/usr/bin/lwe-ui" }));
	CHECK_FALSE (named ({ "/usr/bin/lwe/" }));
	CHECK_FALSE (named ({ "xlwe" }));
	CHECK_FALSE (named ({}));

	char* noName[] = { nullptr, nullptr };
	CHECK_FALSE (LweCommand::isLweName (1, noName));
    }

    SECTION ("the words") {
	struct Row {
	    std::vector<std::string> words;
	    Action action;
	    bool json;
	    std::string verb;
	};

	const std::vector<Row> rows = {
	    { {}, Action::Status, false, "status" },
	    { { "status" }, Action::Status, false, "status" },
	    { { "-j" }, Action::Status, true, "status" },
	    { { "status", "--json" }, Action::Status, true, "status" },
	    { { "off" }, Action::Off, false, "off" },
	    { { "on" }, Action::On, false, "on" },
	    { { "-j", "on" }, Action::On, true, "on" },
	    { { "off", "now" }, Action::Usage, false, "off" },
	    { { "status", "x" }, Action::Usage, false, "status" },
	    { { "on", "-j", "x" }, Action::Usage, true, "on" },
	    { { "--version" }, Action::Version, false, "--version" },
	    { { "--version", "-j" }, Action::Version, true, "--version" },
	    { { "--version", "now" }, Action::Usage, false, "--version" },
	    { { "version" }, Action::Panel, false, "version" },
	    { { "show", "3" }, Action::Panel, false, "show" },
	    { { "-j", "show", "Deep Space" }, Action::Panel, true, "show" },
	    { { "help" }, Action::Panel, false, "help" },
	    { { "--help" }, Action::Panel, false, "--help" },
	    { { "-J" }, Action::Panel, false, "-J" },
	    { { "Off" }, Action::Panel, false, "Off" },
	};

	for (const auto& row : rows) {
	    CAPTURE (row.words);
	    const auto request = LweCommand::parse (row.words);
	    CHECK (request.action == row.action);
	    CHECK (request.json == row.json);
	    CHECK (request.verb == row.verb);
	}
    }
}

TEST_CASE ("lwe status shows what is on screen, the playlist, the screens, the version and the settings", "[lwe]") {
    auto status = cannedStatus ("1.2.0");
    const std::string head = "on screen   Deep Space (1505438974)\n";
    const std::string timer = "playlist    All Wallpapers, sequential, next in 7m 02s\n";
    const std::string tail
	= "version     1.2.0\nsettings    volume 15, speed 1, fps 60, mouse on, parallax on, particles on\n";

    SECTION ("live") {
	CHECK (LweCommand::statusText (status) == liveText ("1.2.0"));

	status["rotation"]["next_in_s"] = 3725;
	CHECK (LweCommand::statusText (status).find ("next in 1h 02m 05s\n") != std::string::npos);
	status["rotation"]["next_in_s"] = 42;
	CHECK (LweCommand::statusText (status).find ("next in 42s\n") != std::string::npos);
    }

    SECTION ("released") {
	for (const auto& [reason, shown] : std::vector<std::pair<std::string, std::string>> {
		 { "verb", "off (lwe off)" },
		 { "fullscreen", "off (fullscreen app)" },
		 { "deadman", "off (watchdog)" },
		 { "app", "off (running-app rule)" },
	     }) {
	    CAPTURE (reason);
	    status["outputs"] = { { "state", "released" }, { "reason", reason } };
	    CHECK (LweCommand::statusText (status) == head + timer + "screens     " + shown + "\n" + tail);
	}
    }

    SECTION ("paused") {
	status["rotation"]["enabled"] = false;
	CHECK (
	    LweCommand::statusText (status)
	    == head + "playlist    All Wallpapers, sequential, paused\nscreens     on\n" + tail
	);
    }

    SECTION ("static") {
	status["rotation"]["order"] = "static";
	status["rotation"]["next_in_s"] = -1;
	CHECK (LweCommand::statusText (status) == head + "playlist    All Wallpapers, static\nscreens     on\n" + tail);
    }

    SECTION ("no version field") {
	status.erase ("version");
	CHECK (LweCommand::statusText (status) == liveText ("older than 1.1.0"));
    }

    SECTION ("control characters") {
	status["current"]["title"] = "Deep\x1b]52;c;ZWNobyBoaQ==\x07 Space";
	status["rotation"]["label"] = "All\x7fWallpapers\x1f";
	CHECK (
	    LweCommand::statusText (status)
	    == "on screen   Deep?]52;c;ZWNobyBoaQ==? Space (1505438974)\n"
	       "playlist    All?Wallpapers?, sequential, next in 7m 02s\nscreens     on\n"
		+ tail
	);

	status["current"]["title"] = std::string ("D") + "\xc3\xa9" + "j" + "\xc3\xa0" + " vu";
	CHECK (LweCommand::statusText (status).starts_with ("on screen   D\xc3\xa9j\xc3\xa0 vu (1505438974)\n"));
    }
}

TEST_CASE ("a running engine with another version gets the restart line", "[lwe]") {
    auto status = cannedStatus ("1.2.0");

    CHECK_FALSE (LweCommand::versionMismatch (status, "1.2.0").has_value ());
    CHECK (
	LweCommand::versionMismatch (status, "1.3.0").value_or ("")
	== "The running engine is 1.2.0 but 1.3.0 is installed; run lwe service restart."
    );

    status.erase ("version");
    CHECK (
	LweCommand::versionMismatch (status, "1.2.0").value_or ("")
	== "The running engine is older than 1.1.0 but 1.2.0 is installed; run lwe service restart."
    );
}

TEST_CASE ("lwe --version prints the version stamp without an engine", "[lwe]") {
    const ScratchLwe lwe;

    const auto finished = lwe.run ({ "--version" });
    CHECK_FALSE (finished.timedOut);
    REQUIRE (WIFEXITED (finished.status));
    CHECK (WEXITSTATUS (finished.status) == 0);
    CHECK (finished.out == std::string (LWE_VERSION) + "\n");
    CHECK (finished.err.empty ());
}

TEST_CASE ("lwe status asks the engine on its socket and prints the answer", "[lwe]") {
    const ScratchLwe lwe;
    const auto reply = CommandDispatcher::done (1, cannedStatus (LWE_VERSION));
    ScratchServer server (lwe.socket (), [reply] (const std::string&) -> std::optional<std::string> { return reply; });

    const auto finished = lwe.run ({ "status" });
    CHECK_FALSE (finished.timedOut);
    REQUIRE (WIFEXITED (finished.status));
    CHECK (WEXITSTATUS (finished.status) == 0);
    CHECK (finished.out == liveText (LWE_VERSION));
    CHECK (finished.err.empty ());
    CHECK (server.lines () == std::vector<std::string> { R"({"cmd":"status","id":1})" });
}

TEST_CASE ("lwe off with no engine running exits 2", "[lwe]") {
    const ScratchLwe lwe;

    const auto finished = lwe.run ({ "off" });
    CHECK_FALSE (finished.timedOut);
    REQUIRE (WIFEXITED (finished.status));
    CHECK (WEXITSTATUS (finished.status) == 2);
    CHECK (finished.out.empty ());
    CHECK (
	finished.err == "lwe: the engine is not running (" + lwe.socket ().string () + ": No such file or directory)\n"
    );
}

TEST_CASE ("lwe off with a word after it exits 3", "[lwe]") {
    const ScratchLwe lwe;

    const auto finished = lwe.run ({ "off", "now" });
    CHECK_FALSE (finished.timedOut);
    REQUIRE (WIFEXITED (finished.status));
    CHECK (WEXITSTATUS (finished.status) == 3);
    CHECK (finished.out.empty ());
    CHECK (finished.err == "lwe: off takes no value\n");
}

TEST_CASE ("lwe runs the panel launcher it finds on PATH for every other verb", "[lwe]") {
    const ScratchLwe lwe;
    const auto launcher = lwe.root / "bin" / "lwe-ui";
    const auto record = lwe.root / "record";

    {
	std::ofstream script (launcher);
	script << "#!/bin/sh\nprintf '%s\\0' \"$0\" \"$(pwd -P)\" \"$@\" > '" << record.string () << "'\nexit 7\n";
    }

    std::filesystem::permissions (launcher, std::filesystem::perms::owner_all, std::filesystem::perm_options::replace);

    const auto finished = lwe.run ({ "show", "Deep Space", "-j" });
    CHECK_FALSE (finished.timedOut);
    REQUIRE (WIFEXITED (finished.status));
    CHECK (WEXITSTATUS (finished.status) == 7);

    std::ifstream recorded (record, std::ios::binary);
    const std::string content ((std::istreambuf_iterator<char> (recorded)), std::istreambuf_iterator<char> ());
    std::vector<std::string> fields;

    for (std::string::size_type start = 0; start < content.size ();) {
	const auto end = content.find ('\0', start);

	if (end == std::string::npos) {
	    break;
	}

	fields.push_back (content.substr (start, end - start));
	start = end + 1;
    }

    const auto work = lwe.work ().string ();
    CHECK (
	fields
	== std::vector<std::string> { launcher.string (), work, "--lwe", LWE_VERSION, work, "show", "Deep Space", "-j" }
    );
}

TEST_CASE ("lwe names what works without the panel when no launcher is installed", "[lwe]") {
    const ScratchLwe lwe;

    const auto finished = lwe.run ({ "show", "3" });
    CHECK_FALSE (finished.timedOut);
    REQUIRE (WIFEXITED (finished.status));
    CHECK (WEXITSTATUS (finished.status) == 1);
    CHECK (finished.out.empty ());
    CHECK (
	finished.err
	== "lwe: show needs the LWE panel, which is not installed; without it lwe does status, off, on and --version.\n"
    );
}

TEST_CASE ("lwe status against an engine of another version warns and still prints the status", "[lwe]") {
    const ScratchLwe lwe;
    ScratchServer server (lwe.socket (), engine ("1.1.0"));

    const auto finished = lwe.run ({ "status" });
    CHECK_FALSE (finished.timedOut);
    REQUIRE (WIFEXITED (finished.status));
    CHECK (WEXITSTATUS (finished.status) == 0);
    CHECK (finished.out == liveText ("1.1.0"));
    CHECK (
	finished.err
	== "The running engine is 1.1.0 but " + std::string (LWE_VERSION) + " is installed; run lwe service restart.\n"
    );
    CHECK (server.lines () == std::vector<std::string> { R"({"cmd":"status","id":1})" });
}

TEST_CASE ("lwe off against an engine of another version refuses before sending anything", "[lwe]") {
    const ScratchLwe lwe;
    ScratchServer server (lwe.socket (), engine ("1.1.0"));

    const auto finished = lwe.run ({ "off" });
    CHECK_FALSE (finished.timedOut);
    REQUIRE (WIFEXITED (finished.status));
    CHECK (WEXITSTATUS (finished.status) == 1);
    CHECK (finished.out.empty ());
    CHECK (
	finished.err
	== "The running engine is 1.1.0 but " + std::string (LWE_VERSION) + " is installed; run lwe service restart.\n"
    );
    CHECK (server.lines () == std::vector<std::string> { R"({"cmd":"status","id":1})" });
}

TEST_CASE ("lwe off and on against a matching engine ask for status and then send their verb", "[lwe]") {
    const ScratchLwe lwe;
    ScratchServer server (lwe.socket (), engine (LWE_VERSION));

    const auto off = lwe.run ({ "off" });
    CHECK_FALSE (off.timedOut);
    REQUIRE (WIFEXITED (off.status));
    CHECK (WEXITSTATUS (off.status) == 0);
    CHECK (off.out == "Wallpaper off on every screen now; not saved: lwe on or a service restart brings it back.\n");
    CHECK (off.err.empty ());

    const auto on = lwe.run ({ "on" });
    CHECK_FALSE (on.timedOut);
    REQUIRE (WIFEXITED (on.status));
    CHECK (WEXITSTATUS (on.status) == 0);
    CHECK (on.out == "Wallpaper back on every screen now; on is the normal state, so nothing is saved.\n");
    CHECK (on.err.empty ());

    CHECK (
	server.lines ()
	== std::vector<std::string> { R"({"cmd":"status","id":1})", R"({"cmd":"release-outputs","id":1})",
				      R"({"cmd":"status","id":1})", R"({"cmd":"acquire-outputs","id":1})" }
    );
}

TEST_CASE ("lwe -j prints the engine's status object on one line", "[lwe]") {
    const ScratchLwe lwe;
    ScratchServer server (lwe.socket (), engine (LWE_VERSION));

    const auto finished = lwe.run ({ "-j" });
    CHECK_FALSE (finished.timedOut);
    REQUIRE (WIFEXITED (finished.status));
    CHECK (WEXITSTATUS (finished.status) == 0);
    CHECK (finished.out == cannedStatus (LWE_VERSION).dump () + "\n");
    CHECK (finished.err.empty ());
}

TEST_CASE ("lwe status against an engine that never answers exits 2", "[lwe]") {
    const ScratchLwe lwe;
    ScratchServer server (lwe.socket (), [] (const std::string&) -> std::optional<std::string> {
	return std::nullopt;
    });

    const auto finished = lwe.run ({ "status" });
    CHECK_FALSE (finished.timedOut);
    REQUIRE (WIFEXITED (finished.status));
    CHECK (WEXITSTATUS (finished.status) == 2);
    CHECK (finished.out.empty ());
    CHECK (finished.err == "lwe: the engine did not answer within 3 seconds\n");
}

TEST_CASE ("lwe status against a reply it cannot read exits 2", "[lwe]") {
    const ScratchLwe lwe;
    ScratchServer server (lwe.socket (), [] (const std::string&) -> std::optional<std::string> {
	return "this is not json";
    });

    const auto finished = lwe.run ({ "status" });
    CHECK_FALSE (finished.timedOut);
    REQUIRE (WIFEXITED (finished.status));
    CHECK (WEXITSTATUS (finished.status) == 2);
    CHECK (finished.out.empty ());
    CHECK (finished.err == "lwe: the engine sent a reply lwe cannot read\n");
}

TEST_CASE ("lwe on exits 1 when the engine refuses", "[lwe]") {
    const ScratchLwe lwe;
    ScratchServer server (lwe.socket (), engine (LWE_VERSION, true));

    const auto finished = lwe.run ({ "on" });
    CHECK_FALSE (finished.timedOut);
    REQUIRE (WIFEXITED (finished.status));
    CHECK (WEXITSTATUS (finished.status) == 1);
    CHECK (finished.out.empty ());
    CHECK (finished.err == "lwe: the engine refused: driver could not re-acquire outputs\n");
    CHECK (
	server.lines ()
	== std::vector<std::string> { R"({"cmd":"status","id":1})", R"({"cmd":"acquire-outputs","id":1})" }
    );
}

TEST_CASE ("lwe skips a directory named lwe-ui on the search path", "[lwe]") {
    const ScratchLwe lwe;
    const auto record = lwe.root / "record";
    std::filesystem::create_directories (lwe.root / "bin" / "lwe-ui");
    writeScript (
	lwe.root / "home" / ".local" / "bin" / "lwe-ui", "printf '%s\\0' \"$0\" > '" + record.string () + "'\nexit 7\n"
    );

    const auto finished = lwe.run ({ "show", "3" });
    CHECK_FALSE (finished.timedOut);
    REQUIRE (WIFEXITED (finished.status));
    CHECK (WEXITSTATUS (finished.status) == 7);
    CHECK (finished.err.empty ());
    CHECK (
	recordedFields (record)
	== std::vector<std::string> { (lwe.root / "home" / ".local" / "bin" / "lwe-ui").string () }
    );
}

TEST_CASE ("lwe hands the panel launcher the environment it started with", "[lwe]") {
    const ScratchLwe lwe;
    const auto record = lwe.root / "environment";
    writeScript (lwe.root / "bin" / "lwe-ui", "/bin/cat /proc/$$/environ > '" + record.string () + "'\n");

    SECTION ("the caller set no SDL variable") {
	const auto finished = lwe.run ({ "show", "3" });
	CHECK_FALSE (finished.timedOut);
	REQUIRE (WIFEXITED (finished.status));
	CHECK (WEXITSTATUS (finished.status) == 0);
	CHECK (recordedFields (record) == lwe.environment ());
    }

    SECTION ("the caller set SDL_VIDEO_DRIVER") {
	const auto finished = lwe.run ({ "show", "3" }, { "SDL_VIDEO_DRIVER=wayland" });
	CHECK_FALSE (finished.timedOut);
	REQUIRE (WIFEXITED (finished.status));
	CHECK (WEXITSTATUS (finished.status) == 0);

	auto expected = lwe.environment ();
	expected.emplace_back ("SDL_VIDEO_DRIVER=wayland");
	CHECK (recordedFields (record) == expected);
    }
}

TEST_CASE ("lwe status against an engine that closes the connection exits 2 with its own line", "[lwe]") {
    const ScratchLwe lwe;
    const ClosingServer server (lwe.socket ());

    const auto finished = lwe.run ({ "status" });
    CHECK_FALSE (finished.timedOut);
    REQUIRE (WIFEXITED (finished.status));
    CHECK (WEXITSTATUS (finished.status) == 2);
    CHECK (finished.out.empty ());
    CHECK (finished.err == "lwe: the engine closed the connection without answering; it may have too many clients.\n");
}

TEST_CASE ("lwe --version with a word after it exits 3", "[lwe]") {
    const ScratchLwe lwe;

    const auto finished = lwe.run ({ "--version", "now" });
    CHECK_FALSE (finished.timedOut);
    REQUIRE (WIFEXITED (finished.status));
    CHECK (WEXITSTATUS (finished.status) == 3);
    CHECK (finished.out.empty ());
    CHECK (finished.err == "lwe: --version takes no value\n");

    const auto json = lwe.run ({ "-j", "--version", "now" });
    CHECK_FALSE (json.timedOut);
    REQUIRE (WIFEXITED (json.status));
    CHECK (WEXITSTATUS (json.status) == 3);
    CHECK (json.out.empty ());
    CHECK (
	json.err
	== R"({"error":"--version takes no value"})"
	   "\n"
    );
}
