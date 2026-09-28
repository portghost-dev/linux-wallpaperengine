#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <poll.h>
#include <set>
#include <spawn.h>
#include <sstream>
#include <string>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

#include "WallpaperEngine/Application/HelpText.h"

using namespace WallpaperEngine::Application;
using Catch::Matchers::ContainsSubstring;
using Catch::Matchers::StartsWith;

namespace {
bool hasSpelling (const std::string& text, const std::string& spelling) {
    for (auto at = text.find (spelling); at != std::string::npos; at = text.find (spelling, at + 1)) {
	const auto end = at + spelling.size ();
	const bool before = at == 0 || std::string (" ,(\n").find (text[at - 1]) != std::string::npos;
	const bool after = end == text.size () || std::string (" ,.)\n").find (text[end]) != std::string::npos;

	if (before && after) {
	    return true;
	}
    }

    return false;
}
} // namespace

TEST_CASE ("the engine help text starts with the usage line", "[help]") {
    CHECK_THAT (std::string (engineHelpText ()), StartsWith ("Usage: linux-wallpaperengine [options] [wallpaper]"));
}

TEST_CASE ("the engine help text keeps the literal the panel's developer view looks for", "[help]") {
    CHECK_THAT (std::string (engineHelpText ()), ContainsSubstring ("--api-socket"));
}

TEST_CASE ("the engine help text names every spelling the parser registers", "[help]") {
    const std::string text = engineHelpText ();

    for (const char* spelling : {
	     "-h",
	     "--help",
	     "--version",
	     "[wallpaper]",
	     "-w",
	     "--window",
	     "-r",
	     "--screen-root",
	     "--screen",
	     "--screen-span",
	     "--span",
	     "-b",
	     "--bg",
	     "--wallpaper",
	     "--playlist",
	     "--steamplaylist",
	     "--scaling",
	     "--clamp",
	     "--edge",
	     "--layer",
	     "-f",
	     "--fps",
	     "--no-fullscreen-pause",
	     "--fullscreen",
	     "--fullscreen-pause-only-active",
	     "--fullscreen-active-only",
	     "--fullscreen-pause-ignore-appid",
	     "--fullscreen-ignore",
	     "-v",
	     "--volume",
	     "-s",
	     "--silent",
	     "--mute",
	     "--noautomute",
	     "--no-automute",
	     "--no-audio-processing",
	     "--no-audioreactive",
	     "--api-socket",
	     "--listen",
	     "--daemon",
	     "--screenshot",
	     "--screenshot-delay",
	     "--assets-dir",
	     "--assetsfolder",
	     "--properties-file",
	     "--resclamp",
	     "--effectclamp",
	     "--texturecache",
	     "--texturedetail",
	     "--videodecode",
	     "--color",
	     "--speed",
	     "--watchdog",
	     "--lightdimming",
	     "--lightfalloff",
	     "--audiogain",
	     "--audiosmoothing",
	     "--socket",
	     "--disable-particles",
	     "--no-particles",
	     "--disable-mouse",
	     "--no-mouse",
	     "--disable-parallax",
	     "--no-parallax",
	     "-l",
	     "--list-properties",
	     "--set-property",
	     "--property",
	     "-z",
	     "--dump-structure",
	     "--render-debug",
	     "--debug",
	     "--help-debug",
	 }) {
	CAPTURE (spelling);
	CHECK (hasSpelling (text, spelling));
    }
}

TEST_CASE ("no line of the engine help text is longer than 100 characters", "[help]") {
    std::istringstream lines (engineHelpText ());
    std::string line;

    while (std::getline (lines, line)) {
	CAPTURE (line);
	CHECK (line.size () <= 100);
    }
}

TEST_CASE ("each entry of the engine help text lists exactly the spellings of its own argument", "[help]") {
    const std::vector<std::set<std::string>> arguments = {
	{ "-h", "--help" },
	{ "--version" },
	{ "-w", "--window" },
	{ "-r", "--screen-root", "--screen" },
	{ "--screen-span", "--span" },
	{ "-b", "--bg", "--wallpaper" },
	{ "--playlist", "--steamplaylist" },
	{ "--scaling" },
	{ "--clamp", "--edge" },
	{ "--layer" },
	{ "-f", "--fps" },
	{ "--no-fullscreen-pause" },
	{ "--fullscreen" },
	{ "--fullscreen-pause-only-active", "--fullscreen-active-only" },
	{ "--fullscreen-pause-ignore-appid", "--fullscreen-ignore" },
	{ "-v", "--volume" },
	{ "-s", "--silent", "--mute" },
	{ "--noautomute", "--no-automute" },
	{ "--no-audio-processing", "--no-audioreactive" },
	{ "--api-socket", "--listen" },
	{ "--daemon" },
	{ "--screenshot" },
	{ "--screenshot-delay" },
	{ "--assets-dir", "--assetsfolder" },
	{ "--properties-file" },
	{ "--resclamp" },
	{ "--effectclamp" },
	{ "--texturecache" },
	{ "--texturedetail" },
	{ "--videodecode" },
	{ "--color" },
	{ "--speed" },
	{ "--watchdog" },
	{ "--lightdimming" },
	{ "--lightfalloff" },
	{ "--audiogain" },
	{ "--audiosmoothing" },
	{ "--socket" },
	{ "--disable-particles", "--no-particles" },
	{ "--disable-mouse", "--no-mouse" },
	{ "--disable-parallax", "--no-parallax" },
	{ "-l", "--list-properties" },
	{ "--set-property", "--property" },
	{ "-z", "--dump-structure" },
	{ "--render-debug" },
	{ "--debug" },
	{ "--help-debug" },
    };

    std::vector<std::set<std::string>> entries;
    std::istringstream lines (engineHelpText ());
    std::string line;

    while (std::getline (lines, line)) {
	if (!line.starts_with ("  -")) {
	    continue;
	}

	const std::string names = line.substr (2, std::min (line.find (" <", 2), line.find ("  ", 2)) - 2);
	std::set<std::string> spellings;

	for (std::string::size_type start = 0; start != std::string::npos;) {
	    const auto comma = names.find (", ", start);

	    spellings.insert (names.substr (start, comma == std::string::npos ? std::string::npos : comma - start));
	    start = comma == std::string::npos ? std::string::npos : comma + 2;
	}

	entries.push_back (spellings);
    }

    CHECK (entries.size () == arguments.size ());

    for (const auto& argument : arguments) {
	CAPTURE (argument);
	CHECK (std::ranges::count (entries, argument) == 1);
    }
}

TEST_CASE ("the engine binary prints the engine help text for -h and --help and exits with status 0", "[help]") {
    const auto engine = std::filesystem::read_symlink ("/proc/self/exe").parent_path () / "linux-wallpaperengine";

    if (!std::filesystem::exists (engine)) {
	FAIL ("the engine binary is missing: " << engine.string ());
    }

    for (const std::string flag : { "-h", "--help" }) {
	CAPTURE (flag);
	int ends[2];
	REQUIRE (pipe2 (ends, O_CLOEXEC) == 0);

	posix_spawn_file_actions_t actions;
	posix_spawn_file_actions_init (&actions);
	posix_spawn_file_actions_adddup2 (&actions, ends[1], STDOUT_FILENO);
	posix_spawn_file_actions_addopen (&actions, STDERR_FILENO, "/dev/null", O_WRONLY, 0);

	std::string program = engine.string ();
	std::string option = flag;
	char* argv[] = { program.data (), option.data (), nullptr };
	pid_t child = 0;
	const int spawned = posix_spawn (&child, program.c_str (), &actions, nullptr, argv, environ);

	posix_spawn_file_actions_destroy (&actions);
	close (ends[1]);

	if (spawned != 0) {
	    close (ends[0]);
	    FAIL ("posix_spawn failed for " << program << ": " << std::strerror (spawned));
	}

	std::string output;
	bool timedOut = false;
	const auto deadline = std::chrono::steady_clock::now () + std::chrono::seconds (10);
	char buffer[4096];

	for (;;) {
	    const auto left
		= std::chrono::duration_cast<std::chrono::milliseconds> (deadline - std::chrono::steady_clock::now ());

	    if (left.count () <= 0) {
		timedOut = true;
		break;
	    }

	    pollfd readable { ends[0], POLLIN, 0 };
	    const int polled = poll (&readable, 1, static_cast<int> (left.count ()));

	    if (polled == 0) {
		timedOut = true;
		break;
	    }

	    if (polled < 0) {
		if (errno == EINTR) {
		    continue;
		}

		break;
	    }

	    const ssize_t count = read (ends[0], buffer, sizeof buffer);

	    if (count > 0) {
		output.append (buffer, static_cast<std::size_t> (count));
	    } else if (count == 0 || errno != EINTR) {
		break;
	    }
	}

	close (ends[0]);

	if (timedOut) {
	    kill (child, SIGKILL);
	}

	int status = 0;

	while (waitpid (child, &status, 0) < 0 && errno == EINTR) { }

	CHECK_FALSE (timedOut);
	CHECK (WIFEXITED (status));
	CHECK (WEXITSTATUS (status) == 0);
	CHECK (output == engineHelpText ());
    }
}
