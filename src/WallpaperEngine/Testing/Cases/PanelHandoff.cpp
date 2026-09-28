#include <catch2/catch_test_macros.hpp>

#include <chrono>
#include <csignal>
#include <cstdlib>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <spawn.h>
#include <string>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <vector>

#include "WallpaperEngine/Application/PanelHandoff.h"

using namespace WallpaperEngine::Application;

namespace {
struct Spawned {
    bool timedOut = false;
    int status = -1;
    std::string err;
};

Spawned spawnLwe (
    const std::filesystem::path& root, const std::vector<std::string>& words, std::vector<std::string> environment
) {
    std::vector<std::string> arguments = { "lwe" };
    arguments.insert (arguments.end (), words.begin (), words.end ());

    std::vector<char*> argv;
    std::vector<char*> envp;

    for (auto& argument : arguments) {
	argv.push_back (argument.data ());
    }

    for (auto& entry : environment) {
	envp.push_back (entry.data ());
    }

    argv.push_back (nullptr);
    envp.push_back (nullptr);

    const std::string program = (root / "bin" / "lwe").string ();
    const std::string out = (root / "out").string ();
    const std::string err = (root / "err").string ();
    const std::string work = (root / "work").string ();

    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init (&actions);
    posix_spawn_file_actions_addopen (&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0);
    posix_spawn_file_actions_addopen (&actions, STDOUT_FILENO, out.c_str (), O_WRONLY | O_CREAT | O_TRUNC, 0600);
    posix_spawn_file_actions_addopen (&actions, STDERR_FILENO, err.c_str (), O_WRONLY | O_CREAT | O_TRUNC, 0600);
    posix_spawn_file_actions_addchdir_np (&actions, work.c_str ());

    pid_t child = 0;
    const int spawned = posix_spawn (&child, program.c_str (), &actions, nullptr, argv.data (), envp.data ());
    posix_spawn_file_actions_destroy (&actions);
    REQUIRE (spawned == 0);

    Spawned result;
    const auto deadline = std::chrono::steady_clock::now () + std::chrono::seconds (10);

    while (waitpid (child, &result.status, WNOHANG) == 0) {
	if (std::chrono::steady_clock::now () >= deadline) {
	    result.timedOut = true;
	    kill (child, SIGKILL);
	    waitpid (child, &result.status, 0);
	    break;
	}

	std::this_thread::sleep_for (std::chrono::milliseconds (10));
    }

    std::ifstream written (err, std::ios::binary);
    result.err.assign (std::istreambuf_iterator<char> (written), std::istreambuf_iterator<char> ());
    return result;
}
} // namespace

TEST_CASE ("the panel launcher is looked for beside the lwe name, on PATH, then where install.sh puts it", "[lwe]") {
    std::vector<std::string> asked;
    const auto nothing = [&asked] (const std::string& candidate) {
	asked.push_back (candidate);
	return false;
    };

    SECTION ("every place, in order") {
	CHECK_FALSE (
	    PanelHandoff::findPanelLauncher ("/opt/lwe/bin/lwe", "/usr/local/bin:bin::/usr/bin", "/home/user", nothing)
		.has_value ()
	);
	CHECK (
	    asked
	    == std::vector<std::string> { "/opt/lwe/bin/lwe-ui", "/usr/local/bin/lwe-ui", "/usr/bin/lwe-ui",
					  "/home/user/.local/bin/lwe-ui",
					  "/home/user/.local/share/lwe-ui/venv/bin/lwe-ui" }
	);
    }

    SECTION ("a name without a slash is not looked for beside itself") {
	CHECK_FALSE (PanelHandoff::findPanelLauncher ("lwe", "/usr/bin", "/home/user", nothing).has_value ());
	CHECK (
	    asked
	    == std::vector<std::string> { "/usr/bin/lwe-ui", "/home/user/.local/bin/lwe-ui",
					  "/home/user/.local/share/lwe-ui/venv/bin/lwe-ui" }
	);
    }

    SECTION ("a relative name is looked for beside itself as typed") {
	CHECK_FALSE (PanelHandoff::findPanelLauncher ("bin/lwe", "", "", nothing).has_value ());
	CHECK (asked == std::vector<std::string> { "bin/lwe-ui" });
    }

    SECTION ("the first executable wins") {
	const auto onPath = [&asked] (const std::string& candidate) {
	    asked.push_back (candidate);
	    return candidate == "/usr/bin/lwe-ui";
	};

	CHECK (
	    PanelHandoff::findPanelLauncher ("/opt/lwe/bin/lwe", "/usr/local/bin:/usr/bin", "/home/user", onPath)
		.value_or ("")
	    == "/usr/bin/lwe-ui"
	);
	CHECK (asked == std::vector<std::string> { "/opt/lwe/bin/lwe-ui", "/usr/local/bin/lwe-ui", "/usr/bin/lwe-ui" });
    }

    SECTION ("no PATH and no HOME leave nothing to look at") {
	CHECK_FALSE (PanelHandoff::findPanelLauncher ("lwe", "", "", nothing).has_value ());
	CHECK (asked.empty ());
    }
}

TEST_CASE ("a relative or empty HOME adds no place to look for the panel launcher", "[lwe]") {
    std::vector<std::string> asked;
    const auto nothing = [&asked] (const std::string& candidate) {
	asked.push_back (candidate);
	return false;
    };

    CHECK_FALSE (PanelHandoff::findPanelLauncher ("lwe", "/usr/bin", "rel", nothing).has_value ());
    CHECK (asked == std::vector<std::string> { "/usr/bin/lwe-ui" });

    asked.clear ();
    CHECK_FALSE (PanelHandoff::findPanelLauncher ("lwe", "/usr/bin", "", nothing).has_value ());
    CHECK (asked == std::vector<std::string> { "/usr/bin/lwe-ui" });
}

TEST_CASE ("lwe with a relative HOME does not run a lwe-ui below the working folder", "[lwe]") {
    std::string pattern = (std::filesystem::temp_directory_path () / "lwe-home-XXXXXX").string ();
    REQUIRE (mkdtemp (pattern.data ()) != nullptr);
    const auto root = std::filesystem::canonical (pattern);

    struct Remove {
	std::filesystem::path root;
	~Remove () {
	    std::error_code ignored;
	    std::filesystem::remove_all (root, ignored);
	}
    } const remove { root };

    for (const char* folder : { "bin", "run", "state", "config", "work/rel/.local/bin" }) {
	std::filesystem::create_directories (root / folder);
    }

    const auto engine = std::filesystem::read_symlink ("/proc/self/exe").parent_path () / "linux-wallpaperengine";
    REQUIRE (std::filesystem::exists (engine));
    std::filesystem::create_symlink (engine, root / "bin" / "lwe");

    const auto launcher = root / "work" / "rel" / ".local" / "bin" / "lwe-ui";
    const auto record = root / "record";

    {
	std::ofstream script (launcher);
	script << "#!/bin/sh\nprintf '%s\\0' \"$0\" \"$@\" > '" << record.string () << "'\nexit 7\n";
    }

    std::filesystem::permissions (launcher, std::filesystem::perms::owner_all, std::filesystem::perm_options::replace);

    const auto spawned = spawnLwe (
	root, { "help" },
	{ "LWE_SOCKET=" + (root / "run" / "lwe" / "engine.sock").string (),
	  "XDG_RUNTIME_DIR=" + (root / "run").string (), "XDG_STATE_HOME=" + (root / "state").string (),
	  "XDG_CONFIG_HOME=" + (root / "config").string (), "HOME=rel", "PATH=" + (root / "bin").string () }
    );

    CHECK_FALSE (spawned.timedOut);
    REQUIRE (WIFEXITED (spawned.status));
    CHECK (WEXITSTATUS (spawned.status) == 1);
    CHECK (
	spawned.err
	== "lwe: help needs the LWE panel, which is not installed; without it lwe does status, off, on and --version.\n"
    );
    CHECK_FALSE (std::filesystem::exists (record));
}
