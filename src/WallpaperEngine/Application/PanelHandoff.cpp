#include "PanelHandoff.h"

#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <sys/stat.h>
#include <unistd.h>

namespace WallpaperEngine::Application::PanelHandoff {
std::optional<std::string> findPanelLauncher (
    const std::string& argv0, const std::string& path, const std::string& home,
    const std::function<bool (const std::string&)>& isExecutable
) {
    std::vector<std::string> candidates;

    if (const auto slash = argv0.rfind ('/'); slash != std::string::npos) {
	candidates.push_back (argv0.substr (0, slash + 1) + "lwe-ui");
    }

    for (std::string::size_type start = 0;;) {
	const auto colon = path.find (':', start);
	const auto entry = path.substr (start, colon == std::string::npos ? std::string::npos : colon - start);

	if (entry.starts_with ('/')) {
	    candidates.push_back (entry + "/lwe-ui");
	}

	if (colon == std::string::npos) {
	    break;
	}

	start = colon + 1;
    }

    if (!home.empty ()) {
	candidates.push_back (home + "/.local/bin/lwe-ui");
	candidates.push_back (home + "/.local/share/lwe-ui/venv/bin/lwe-ui");
    }

    for (const auto& candidate : candidates) {
	if (isExecutable (candidate)) {
	    return candidate;
	}
    }

    return std::nullopt;
}

bool isExecutableFile (const std::string& path) {
    struct stat info {};

    return stat (path.c_str (), &info) == 0 && S_ISREG (info.st_mode) && access (path.c_str (), X_OK) == 0;
}

int handOff (const char* argv0, const std::vector<std::string>& words, const std::string& verb) {
    const char* path = std::getenv ("PATH");
    const char* home = std::getenv ("HOME");
    const auto launcher
	= findPanelLauncher (argv0, path != nullptr ? path : "", home != nullptr ? home : "", isExecutableFile);

    if (!launcher.has_value ()) {
	std::cerr << "lwe: " << verb
		  << " needs the LWE panel, which is not installed; without it lwe does status, off, on and --version."
		  << std::endl;
	return 1;
    }

    std::error_code error;
    const auto cwd = std::filesystem::current_path (error);
    std::vector<std::string> arguments = { *launcher, "--lwe", LWE_VERSION, error ? std::string () : cwd.string () };
    arguments.insert (arguments.end (), words.begin (), words.end ());

    std::vector<char*> pointers;

    for (auto& argument : arguments) {
	pointers.push_back (argument.data ());
    }

    pointers.push_back (nullptr);

    std::vector<std::string> starting;
    std::vector<char*> variables;
    char** environment = environ;

    try {
	if (std::ifstream file ("/proc/self/environ", std::ios::binary); file) {
	    const std::string block ((std::istreambuf_iterator<char> (file)), std::istreambuf_iterator<char> ());

	    for (std::string::size_type start = 0; start < block.size ();) {
		const auto end = block.find ('\0', start);
		starting.push_back (block.substr (start, end == std::string::npos ? std::string::npos : end - start));
		start = end == std::string::npos ? block.size () : end + 1;
	    }

	    for (auto& variable : starting) {
		variables.push_back (variable.data ());
	    }

	    variables.push_back (nullptr);
	    environment = variables.data ();
	}
    } catch (const std::ios_base::failure&) {
	environment = environ;
    }

    execve (launcher->c_str (), pointers.data (), environment);

    const int reason = errno;
    std::cerr << "lwe: cannot run " << *launcher << ": " << std::strerror (reason) << std::endl;
    return 1;
}
} // namespace WallpaperEngine::Application::PanelHandoff
