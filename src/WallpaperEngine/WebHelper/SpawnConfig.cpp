#include "SpawnConfig.h"
#include "Protocol.h"

#include <cstdlib>
#include <unistd.h>

using namespace WallpaperEngine::WebHelper;

namespace {
constexpr const char* ASSETS_SWITCH = "--lwe-assets-dir=";
constexpr const char* SOCKET_SWITCH = "--lwe-web-socket=";
constexpr const char* FPS_SWITCH = "--lwe-max-fps=";
constexpr const char* VERSION_SWITCH = "--lwe-protocol=";
constexpr const char* SCHEME_SWITCH = "--lwe-scheme=";

bool matches (const std::string& argument, const char* prefix, std::string& value) {
    const std::string needle (prefix);

    if (argument.rfind (needle, 0) != 0) {
	return false;
    }

    value = argument.substr (needle.size ());

    return true;
}
} // namespace

std::vector<std::string> SpawnConfig::toArguments () const {
    std::vector<std::string> arguments;

    arguments.push_back (std::string (ASSETS_SWITCH) + this->assetsDir.string ());
    arguments.push_back (std::string (SOCKET_SWITCH) + this->socketPath.string ());
    arguments.push_back (std::string (FPS_SWITCH) + std::to_string (this->maximumFPS));
    arguments.push_back (std::string (VERSION_SWITCH) + std::to_string (this->protocolVersion));

    // one switch per scheme rather than a delimited list: workshop ids are numeric but
    // wallpaper paths are arbitrary and can legitimately contain any separator we might
    // have picked
    for (const auto& scheme : this->schemes) {
	arguments.push_back (std::string (SCHEME_SWITCH) + scheme.workshopId + "=" + scheme.path.string ());
    }

    return arguments;
}

bool SpawnConfig::fromArguments (const int argc, char** argv, SpawnConfig& out, std::string& error) {
    bool sawVersion = false;
    bool sawSocket = false;

    for (int i = 1; i < argc; i++) {
	const std::string argument = argv[i];
	std::string value;

	if (matches (argument, ASSETS_SWITCH, value)) {
	    out.assetsDir = value;
	} else if (matches (argument, SOCKET_SWITCH, value)) {
	    out.socketPath = value;
	    sawSocket = true;
	} else if (matches (argument, FPS_SWITCH, value)) {
	    out.maximumFPS = std::atoi (value.c_str ());
	} else if (matches (argument, VERSION_SWITCH, value)) {
	    out.protocolVersion = static_cast<uint32_t> (std::strtoul (value.c_str (), nullptr, 10));
	    sawVersion = true;
	} else if (matches (argument, SCHEME_SWITCH, value)) {
	    const auto separator = value.find ('=');

	    if (separator == std::string::npos) {
		error = "malformed scheme entry (expected <workshopId>=<path>): " + value;
		return false;
	    }

	    out.schemes.push_back ({ .workshopId = value.substr (0, separator), .path = value.substr (separator + 1) });
	}
    }

    if (!sawSocket) {
	error = "missing required switch " + std::string (SOCKET_SWITCH);
	return false;
    }

    if (!sawVersion) {
	error = "missing required switch " + std::string (VERSION_SWITCH);
	return false;
    }

    if (out.assetsDir.empty ()) {
	error = "missing required switch " + std::string (ASSETS_SWITCH);
	return false;
    }

    // a half-upgraded install (new engine, stale helper next to it, or the reverse) must
    // fail here and say so, not fail later as an unexplained decode error
    if (out.protocolVersion != PROTOCOL_VERSION) {
	error = "protocol version mismatch: engine speaks " + std::to_string (out.protocolVersion)
	    + ", this helper speaks " + std::to_string (PROTOCOL_VERSION);
	return false;
    }

    return true;
}

std::filesystem::path SpawnConfig::defaultSocketPath (const int enginePid) {
    const std::string leaf = "web-" + std::to_string (enginePid) + ".sock";

    if (const char* socketOverride = getenv ("LWE_WEB_SOCKET"); socketOverride != nullptr && *socketOverride != '\0') {
	return { socketOverride };
    }

    if (const char* runtime = getenv ("XDG_RUNTIME_DIR"); runtime != nullptr && *runtime != '\0') {
	return std::filesystem::path (runtime) / "lwe" / leaf;
    }

    // same shape as CommandServer::defaultSocketPath's fallback: /tmp is world-writable,
    // so a uid-qualified subdirectory created 0700 is the only safe home for the socket
    return std::filesystem::path ("/tmp") / ("lwe-" + std::to_string (geteuid ())) / leaf;
}
