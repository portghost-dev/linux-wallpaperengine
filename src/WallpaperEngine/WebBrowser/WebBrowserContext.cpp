#include "WebBrowserContext.h"
#include "CEF/BrowserApp.h"
#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/WebBrowser/CEF/SubprocessApp.h"
#include "include/cef_app.h"
#include "include/cef_render_handler.h"
#include <filesystem>
#include <random>
#include <sstream>

using namespace WallpaperEngine::WebBrowser;

// TODO: THIS IS USED TO GENERATE A RANDOM FOLDER FOR THE CHROME PROFILE, MAYBE A DIFFERENT APPROACH WOULD BE BETTER?
namespace uuid {
static std::random_device rd;
static std::mt19937 gen (rd ());
static std::uniform_int_distribution<> dis (0, 15);
static std::uniform_int_distribution<> dis2 (8, 11);

std::string generate_uuid_v4 () {
    std::stringstream ss;
    int i;
    ss << std::hex;
    for (i = 0; i < 8; i++) {
	ss << dis (gen);
    }
    ss << "-";
    for (i = 0; i < 4; i++) {
	ss << dis (gen);
    }
    ss << "-4";
    for (i = 0; i < 3; i++) {
	ss << dis (gen);
    }
    ss << "-";
    ss << dis2 (gen);
    for (i = 0; i < 3; i++) {
	ss << dis (gen);
    }
    ss << "-";
    for (i = 0; i < 12; i++) {
	ss << dis (gen);
    };
    return ss.str ();
}
}

WebBrowserContext::WebBrowserContext (
    CefMainArgs& mainArgs, const WallpaperEngine::WebHelper::SpawnConfig& config,
    WallpaperEngine::Media::MediaSource& mediaSource
) : m_browserApplication (nullptr) {
    this->m_browserApplication = new CEF::BrowserApp (config, mediaSource);

    // safety net: in the browser process CefExecuteProcess returns -1 immediately
    const int exit_code = CefExecuteProcess (mainArgs, this->m_browserApplication, nullptr);

    if (exit_code >= 0) {
	// Sub proccess has endend, so exit
	exit (exit_code);
    }

    // Configurate Chromium
    CefSettings settings;
    this->m_cachePath = std::filesystem::temp_directory_path () / uuid::generate_uuid_v4 ();
    std::string cache_path = this->m_cachePath.string ();

    const auto moduleDir = std::filesystem::canonical ("/proc/self/exe").parent_path ();
    const std::string resourcesDir = moduleDir.string ();
    const std::string localesDir = (moduleDir / "locales").string ();

    CefString (&settings.resources_dir_path) = resourcesDir;
    CefString (&settings.locales_dir_path) = localesDir;

    {
	const char* home = getenv ("HOME");
	const std::string cefLog = std::string (home != nullptr ? home : "/tmp") + "/.local/state/lwe/cef.log";

	{
	    std::error_code ec;
	    const std::filesystem::path logPath (cefLog);
	    std::filesystem::create_directories (logPath.parent_path (), ec);

	    // walk oldest-first so each rename lands on a slot that has already been vacated
	    for (int generation = 3; generation > 0; --generation) {
		std::filesystem::path older = logPath;
		older += "." + std::to_string (generation);
		std::filesystem::path newer = logPath;

		if (generation > 1) {
		    newer += "." + std::to_string (generation - 1);
		}

		if (std::filesystem::exists (newer, ec)) {
		    // rename replaces the destination on POSIX, so generation 3 falls off the end
		    std::filesystem::rename (newer, older, ec);
		}
	    }
	}

	CefString (&settings.log_file) = cefLog;
	settings.log_severity = LOGSEVERITY_WARNING;
	if (const char* sev = getenv ("LWE_CEFLOG"); sev != nullptr) {
	    const std::string severity = sev;
	    if (severity == "verbose") {
		settings.log_severity = LOGSEVERITY_VERBOSE;
	    } else if (severity == "info") {
		settings.log_severity = LOGSEVERITY_INFO;
	    } else if (severity == "error") {
		settings.log_severity = LOGSEVERITY_ERROR;
	    }
	}
	if (const char* dbg = getenv ("LWE_CEFDEBUG"); dbg != nullptr) {
	    const int port = atoi (dbg);
	    if (port > 0 && port < 65536) {
		settings.remote_debugging_port = port;
	    }
	}
    }
    const auto helperPath = moduleDir / "lwe-web-helper";
    if (std::filesystem::exists (helperPath)) {
	CefString (&settings.browser_subprocess_path) = helperPath.string ();
    } else {
	sLog.error (
	    "lwe-web-helper missing next to the engine binary; CEF children will re-exec the engine "
	    "(process-name-based engine censuses will miscount)"
	);
    }
    cef_string_utf8_to_utf16 (cache_path.c_str (), cache_path.length (), &settings.root_cache_path);
    settings.windowless_rendering_enabled = true;
    settings.external_message_pump = 1;
#if defined(CEF_NO_SANDBOX)
    settings.no_sandbox = true;
#endif

    // spawns two new processess

    if (!CefInitialize (mainArgs, settings, this->m_browserApplication, nullptr)) {
	sLog.exception ("CefInitialize: failed");
    }
}

WebBrowserContext::~WebBrowserContext () {
    sLog.out ("Shutting down CEF");
    CefShutdown ();

    if (!this->m_cachePath.empty ()) {
	std::error_code ec;
	std::filesystem::remove_all (this->m_cachePath, ec);

	if (ec) {
	    sLog.error ("Failed to remove CEF cache directory ", this->m_cachePath.string (), ": ", ec.message ());
	}
    }
}

void WebBrowserContext::pumpMessageLoop () { CefDoMessageLoopWork (); }
