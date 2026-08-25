#include <csignal>
#include <cstdio>
#include <cstring>
#include <execinfo.h>
#include <iostream>
#include <unistd.h>

#ifdef __GLIBC__
#include <malloc.h>
#endif

#include "WallpaperEngine/Application/ApplicationContext.h"
#include "WallpaperEngine/Application/WallpaperApplication.h"
#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/WebHelper/SpawnGate.h"

WallpaperEngine::Application::WallpaperApplication* app;

void signalhandler (const int sig) {
    if (app == nullptr) {
	return;
    }

    app->signal (sig);
}

void crashHandler (const int sig, siginfo_t* info, void*) {
    void* frames[64];
    const int count = backtrace (frames, 64);
    char header[128];
    const int len = snprintf (
	header, sizeof (header), "=== LWE CRASH signal=%d addr=%p backtrace(%d):\n", sig,
	info != nullptr ? info->si_addr : nullptr, count
    );
    if (len > 0) {
	[[maybe_unused]] const auto _ = write (STDERR_FILENO, header, static_cast<size_t> (len));
    }
    backtrace_symbols_fd (frames, count, STDERR_FILENO);
    // restore default and re-raise so the core still drops for offline digging
    signal (sig, SIG_DFL);
    raise (sig);
}

void installCrashHandler () {
    struct sigaction sa {};
    sa.sa_sigaction = crashHandler;
    sa.sa_flags = SA_SIGINFO | SA_RESETHAND;
    sigemptyset (&sa.sa_mask);
    for (const int sig : { SIGSEGV, SIGILL, SIGBUS, SIGFPE, SIGABRT }) {
	sigaction (sig, &sa, nullptr);
    }
}

void initLogging () {
    sLog.addOutput (new std::ostream (std::cout.rdbuf ()));
    sLog.addError (new std::ostream (std::cerr.rdbuf ()));
}

int main (int argc, char* argv[]) {
#ifdef __GLIBC__
    // few arenas + fixed mmap threshold: a freed scene returns its memory to the OS
    // instead of stranding it across per-thread arenas
    mallopt (M_ARENA_MAX, 2);
    mallopt (M_MMAP_THRESHOLD, 128 * 1024);
#endif

    // first thing, before even the CEF subprocess dispatch: crashes in helpers and the
    // main engine alike must leave a trace in the journal
    installCrashHandler ();

    WallpaperEngine::WebHelper::SpawnGate::captureAtStartup ();

    try {
	initLogging ();

	WallpaperEngine::Application::ApplicationContext appContext (argc, argv);

	appContext.loadSettingsFromArgv ();

	app = new WallpaperEngine::Application::WallpaperApplication (appContext);

	// halt if the list-properties option was specified
	if (appContext.settings.general.onlyListProperties) {
	    delete app;
	    return 0;
	}

	// attach signals to gracefully stop
	std::signal (SIGINT, signalhandler);
	std::signal (SIGTERM, signalhandler);
	std::signal (SIGUSR1, signalhandler);
	std::signal (SIGUSR2, signalhandler);

	// show the wallpaper application
	app->show ();

	// remove signal handlers before destroying app
	std::signal (SIGINT, SIG_DFL);
	std::signal (SIGTERM, SIG_DFL);

	delete app;

	return 0;
    } catch (const std::exception& e) {
	std::cerr << e.what () << std::endl;
	return 1;
    }
}