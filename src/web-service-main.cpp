
#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/WebBrowser/CEF/BrowserApp.h"
#include "WallpaperEngine/WebBrowser/WebBrowserContext.h"
#include "WallpaperEngine/WebHelper/Service/HelperServer.h"
#include "WallpaperEngine/WebHelper/Service/NullMediaSource.h"
#include "WallpaperEngine/WebHelper/SpawnConfig.h"

#include <algorithm>
#include <chrono>
#include <csignal>
#include <iostream>

#include <poll.h>
#include <sys/eventfd.h>
#include <unistd.h>

namespace {
volatile std::sig_atomic_t g_stopRequested = 0;

void requestStop (int) { g_stopRequested = 1; }

int64_t steadyNowMs () {
    return std::chrono::duration_cast<std::chrono::milliseconds> (std::chrono::steady_clock::now ().time_since_epoch ())
	.count ();
}

/**
 * Longest this process will sleep with nothing to do.
 *
 * It is a ceiling, not a period: CEF's OnScheduleMessagePumpWork shortens the timeout by
 * writing to the wake descriptor, and the socket wakes it as data arrives. What the ceiling
 * actually bounds is the response to things poll cannot observe - a stop signal, and the
 * idle-exit deadline when the grace is longer than the remaining timeout.
 */
constexpr int POLL_MAX_MS = 50;

/**
 * Floor applied when CEF did not reschedule during a pump.
 *
 * CEF only calls OnScheduleMessagePumpWork when it has work to schedule, so a quiet browser
 * leaves the deadline in the past. Under a sleep-based loop that was harmless; under a poll
 * loop it would compute a zero timeout and spin a core. Applied LOCALLY, never written back
 * to the atomic, so a genuine schedule from CEF can still shorten the wait.
 */
constexpr int64_t PUMP_IDLE_FLOOR_MS = 8;
} // namespace

int main (int argc, char* argv[]) {
    sLog.addOutput (new std::ostream (std::cout.rdbuf ()));
    sLog.addError (new std::ostream (std::cerr.rdbuf ()));

    std::signal (SIGINT, requestStop);
    std::signal (SIGTERM, requestStop);

    WallpaperEngine::WebHelper::SpawnConfig config;
    std::string configError;

    if (!WallpaperEngine::WebHelper::SpawnConfig::fromArguments (argc, argv, config, configError)) {
	sLog.error ("web-service: bad spawn config: ", configError);
	return 1;
    }

    // bind BEFORE CefInitialize: a helper that cannot listen is useless, and failing here
    // costs nothing, whereas failing after CEF has forked its zygotes leaves a mess
    WallpaperEngine::WebHelper::Service::HelperServer server (config);

    if (!server.start ()) {
	sLog.error ("web-service: cannot listen: ", server.error ());
	return 1;
    }

    WallpaperEngine::WebHelper::Service::NullMediaSource mediaSource;

    // [CEF ISOLATION step 4] The wake descriptor, created BEFORE CefInitialize so no
    // scheduled pump can be missed between init and the first poll. EFD_NONBLOCK keeps the
    // write side safe to call from any CEF thread; the loop drains it after every wait.
    const int wakeFd = eventfd (0, EFD_CLOEXEC | EFD_NONBLOCK);

    if (wakeFd < 0) {
	sLog.error ("web-service: eventfd failed; the loop will fall back to its ", POLL_MAX_MS, " ms ceiling");
    }

    WallpaperEngine::WebBrowser::CEF::BrowserApp::setPumpWakeFd (wakeFd);

    CefMainArgs mainArgs (argc, argv);
    // constructing this runs CefInitialize with windowless rendering, the scheme table
    // from the spawn config, the rotated CEF log, and lwe-web-helper as the subprocess path
    const WallpaperEngine::WebBrowser::WebBrowserContext browserContext (mainArgs, config, mediaSource);

    sLog.out ("web-service: ready");

    while (g_stopRequested == 0) {
	// CEF's message loop is the helper's own business now, and its CADENCE is CEF's:
	// external_message_pump is on, so OnScheduleMessagePumpWork (BrowserApp) tells us
	// when the next CefDoMessageLoopWork is due. Pumping on a fixed interval instead
	// starved CEF's delayed tasks - the windowless BeginFrame timer among them - and
	// froze every animated page after its first paint (measured: seq stuck at 1 for
	// 8 s, repaints only on create/resize invalidation).
	int64_t nowMs = steadyNowMs ();
	int64_t dueMs = WallpaperEngine::WebBrowser::CEF::BrowserApp::nextPumpDueMs ();

	if (dueMs <= nowMs) {
	    CefDoMessageLoopWork ();
	    nowMs = steadyNowMs ();
	    dueMs = WallpaperEngine::WebBrowser::CEF::BrowserApp::nextPumpDueMs ();

	    if (dueMs <= nowMs) {
		// CEF had nothing to schedule, so there is no deadline to honor; see
		// PUMP_IDLE_FLOOR_MS for why this is not simply zero
		dueMs = nowMs + PUMP_IDLE_FLOOR_MS;
	    }
	}

	server.tick ();

	if (server.engineDisconnected ()) {
	    sLog.out ("web-service: engine is gone, exiting");
	    break;
	}

	if (server.shouldExitIdle ()) {
	    sLog.out ("web-service: no instances left after the idle grace, exiting");
	    break;
	}

	pollfd waits[3] = {};
	nfds_t count = 0;

	if (const int fd = server.connectionFd (); fd >= 0) {
	    waits[count].fd = fd;
	    waits[count].events = static_cast<short> (POLLIN | (server.wantsWrite () ? POLLOUT : 0));
	    count++;
	} else if (const int listening = server.listenFd (); listening >= 0) {
	    // only while nobody is connected: exactly one engine ever talks to a helper it
	    // spawned itself, so a second pending connection is not something to wake for
	    waits[count].fd = listening;
	    waits[count].events = POLLIN;
	    count++;
	}

	if (wakeFd >= 0) {
	    waits[count].fd = wakeFd;
	    waits[count].events = POLLIN;
	    count++;
	}

	int timeoutMs = static_cast<int> (std::clamp<int64_t> (dueMs - steadyNowMs (), 0, POLL_MAX_MS));

	// an idle-exit deadline inside the window has to be honored too, or teardown would
	// be quantised to the poll ceiling
	if (const int64_t untilExit = server.millisUntilIdleExit ();
	    untilExit >= 0 && untilExit < static_cast<int64_t> (timeoutMs)) {
	    timeoutMs = static_cast<int> (untilExit);
	}

	if (poll (waits, count, timeoutMs) > 0 && wakeFd >= 0) {
	    uint64_t drained = 0;
	    [[maybe_unused]] const auto ignored = read (wakeFd, &drained, sizeof (drained));
	}
    }

    if (wakeFd >= 0) {
	WallpaperEngine::WebBrowser::CEF::BrowserApp::setPumpWakeFd (-1);
	close (wakeFd);
    }

    return 0;
}
