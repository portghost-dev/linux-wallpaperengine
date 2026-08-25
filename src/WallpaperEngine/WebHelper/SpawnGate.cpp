#include "SpawnGate.h"

#include "WallpaperEngine/Logging/Log.h"

#include <cerrno>
#include <cstring>
#include <fstream>
#include <spawn.h>
#include <unistd.h>

extern char** environ;

using namespace WallpaperEngine::WebHelper;

namespace {
bool g_captured = false;
std::filesystem::path g_serviceBinary;
sigset_t g_startupSignalMask;
int g_threadsAtCapture = 0;

/** thread count from /proc/self/status; 0 if it cannot be read */
int readThreadCount () {
    std::ifstream status ("/proc/self/status");
    std::string line;

    while (std::getline (status, line)) {
	if (line.rfind ("Threads:", 0) == 0) {
	    try {
		return std::stoi (line.substr (8));
	    } catch (const std::exception&) {
		return 0;
	    }
	}
    }

    return 0;
}

std::filesystem::path resolveServiceBinary () {
    std::error_code ec;
    const auto self = std::filesystem::canonical ("/proc/self/exe", ec);

    if (ec) {
	return "lwe-web-service";
    }

    return self.parent_path () / "lwe-web-service";
}
} // namespace

void SpawnGate::captureAtStartup () {
    if (g_captured) {
	return;
    }

    g_serviceBinary = resolveServiceBinary ();
    g_threadsAtCapture = readThreadCount ();
    sigemptyset (&g_startupSignalMask);
    // SIG_SETMASK with a null new-set is a pure query; this is the process-wide mask
    // before any library has had a chance to block anything on the engine's behalf
    pthread_sigmask (SIG_SETMASK, nullptr, &g_startupSignalMask);
    g_captured = true;
}

bool SpawnGate::captured () { return g_captured; }

const std::filesystem::path& SpawnGate::serviceBinary () {
    if (!g_captured) {
	SpawnGate::captureAtStartup ();
    }

    return g_serviceBinary;
}

int SpawnGate::threadsAtCapture () { return g_threadsAtCapture; }

pid_t SpawnGate::spawn (const std::vector<std::string>& arguments, std::string& error) {
    if (!g_captured) {
	// A binary that forgot to place the gate still gets a working spawn, but its
	// captured mask is whatever is blocked right now, so say so rather than pretend.
	sLog.error ("web helper: SpawnGate::captureAtStartup was never called; capturing late");
	SpawnGate::captureAtStartup ();
    }

    if (std::error_code ec; !std::filesystem::exists (g_serviceBinary, ec)) {
	error = "lwe-web-service not found at " + g_serviceBinary.string ();
	return -1;
    }

    std::string program = g_serviceBinary.string ();
    std::vector<std::string> owned = arguments;
    std::vector<char*> argv;
    argv.reserve (owned.size () + 2);
    argv.push_back (program.data ());

    for (auto& argument : owned) {
	argv.push_back (argument.data ());
    }

    argv.push_back (nullptr);

    posix_spawnattr_t attributes;
    posix_spawnattr_init (&attributes);
    // RESTORE THE STARTUP MASK. Measured: the caller's blocked
    // mask survives exec, so without this a signal blocked anywhere in the engine would be
    // silently unhandleable in the browser host - including SIGTERM, which is how the
    // engine escalates a helper that will not leave.
    //
    // POSIX_SPAWN_SETSIGDEF was considered and deliberately NOT used. Signal HANDLERS do
    // not survive exec at all, so it would only affect dispositions left at SIG_IGN, and
    // the one that matters in practice is SIGPIPE: libraries in the engine's address space
    // ignore it, the child inheriting that ignore is strictly safer than the default
    // (which is death on a closed stdout), and resetting it would buy nothing.
    posix_spawnattr_setsigmask (&attributes, &g_startupSignalMask);
    posix_spawnattr_setflags (&attributes, POSIX_SPAWN_SETSIGMASK);

    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init (&actions);
    // CLOSE EVERYTHING ABOVE STDERR. The engine holds the compositor socket, DRM, the audio
    // device and the daemon's listening socket; a browser host has no business with any of
    // them, and CEF forks its zygotes out of this child, so anything left open here is
    // duplicated into every renderer as well. Measured to be necessary: a non-CLOEXEC
    // descriptor is inherited across exec. stdin/stdout/stderr stay, because that is how
    // the helper's log reaches the same journal as the engine's.
#if defined(__GLIBC__) && defined(__GLIBC_PREREQ)
#if __GLIBC_PREREQ(2, 34)
    posix_spawn_file_actions_addclosefrom_np (&actions, 3);
#define LWE_HAVE_CLOSEFROM 1
#endif
#endif
#ifndef LWE_HAVE_CLOSEFROM
    // Fallback: enumerate what is open now. Racy against another engine thread opening a
    // descriptor between here and the spawn, which is exactly why closefrom is preferred.
    {
	std::error_code ec;

	for (const auto& entry : std::filesystem::directory_iterator ("/proc/self/fd", ec)) {
	    int fd = -1;

	    try {
		fd = std::stoi (entry.path ().filename ().string ());
	    } catch (const std::exception&) {
		continue;
	    }

	    if (fd > 2) {
		posix_spawn_file_actions_addclose (&actions, fd);
	    }
	}
    }
#endif

    pid_t pid = -1;
    // environ is inherited wholesale, which is how LWE_CEFLOG, LWE_CEFDEBUG and
    // LWE_WEB_IDLE_EXIT_MS reach the helper (SpawnConfig.h: no switch encodes them)
    const int result = posix_spawn (&pid, program.c_str (), &actions, &attributes, argv.data (), environ);

    posix_spawn_file_actions_destroy (&actions);
    posix_spawnattr_destroy (&attributes);

    if (result != 0) {
	error = std::string ("posix_spawn failed: ") + std::strerror (result);
	return -1;
    }

    return pid;
}
