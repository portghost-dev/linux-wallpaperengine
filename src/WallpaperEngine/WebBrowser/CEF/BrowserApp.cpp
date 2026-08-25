#include "BrowserApp.h"
#include "SchemeName.h"
#include "WallpaperEngine/Logging/Log.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <unistd.h>

using namespace WallpaperEngine::WebBrowser::CEF;

namespace {
/**
 * Deadline for the next CefDoMessageLoopWork call, steady-clock ms. Zero means "due now" -
 * that is also the safe initial state, so the first loop iteration always pumps. CEF's
 * contract for OnScheduleMessagePumpWork is REPLACE, not minimum: a new request cancels
 * any pending one.
 */
std::atomic<int64_t> s_nextPumpDueMs { 0 };

std::atomic<int> s_pumpWakeFd { -1 };

int64_t steadyNowMs () {
    return std::chrono::duration_cast<std::chrono::milliseconds> (std::chrono::steady_clock::now ().time_since_epoch ())
	.count ();
}
} // namespace

void BrowserApp::OnScheduleMessagePumpWork (const int64_t delay_ms) {
    s_nextPumpDueMs.store (steadyNowMs () + std::max<int64_t> (delay_ms, 0), std::memory_order_relaxed);

    if (const int fd = s_pumpWakeFd.load (std::memory_order_relaxed); fd >= 0) {
	const uint64_t one = 1;
	[[maybe_unused]] const auto ignored = write (fd, &one, sizeof (one));
    }
}

int64_t BrowserApp::nextPumpDueMs () { return s_nextPumpDueMs.load (std::memory_order_relaxed); }

void BrowserApp::setPumpWakeFd (const int fd) { s_pumpWakeFd.store (fd, std::memory_order_relaxed); }

namespace {
std::vector<std::string> collectWorkshopIds (const WallpaperEngine::WebHelper::SpawnConfig& config) {
    std::vector<std::string> ids {};

    for (const auto& scheme : config.schemes) {
	if (std::ranges::find (ids, scheme.workshopId) == ids.end ()) {
	    ids.push_back (scheme.workshopId);
	}
    }

    return ids;
}
} // namespace

BrowserApp::BrowserApp (
    const WallpaperEngine::WebHelper::SpawnConfig& config, WallpaperEngine::Media::MediaSource& mediaSource
) : SubprocessApp (collectWorkshopIds (config)) {
    for (const auto& scheme : config.schemes) {
	this->m_handlerFactories[scheme.workshopId]
	    = new WPSchemeHandlerFactory (scheme.workshopId, scheme.path, config.assetsDir, mediaSource);
    }
}

CefRefPtr<CefBrowserProcessHandler> BrowserApp::GetBrowserProcessHandler () { return this; }

const std::map<std::string, WPSchemeHandlerFactory*>& BrowserApp::getHandlerFactories () const {
    return this->m_handlerFactories;
}

void BrowserApp::OnContextInitialized () {
    // register all the needed schemes, "wp" + the background id is going to be our scheme
    for (const auto& [workshopId, factory] : this->getHandlerFactories ()) {
	CefRegisterSchemeHandlerFactory (generateSchemeName (workshopId), static_cast<const char*> (nullptr), factory);
    }
}

void BrowserApp::OnBeforeCommandLineProcessing (const CefString& process_type, CefRefPtr<CefCommandLine> command_line) {
    command_line->AppendSwitchWithValue (
	"--disable-features",
	"IsolateOrigins,HardwareMediaKeyHandling,WebContentsOcclusion,RendererCodeIntegrityEnabled,site-per-process"
    );
    command_line->AppendSwitch ("--disable-gpu-shader-disk-cache");
    command_line->AppendSwitch ("--disable-site-isolation-trials");
    command_line->AppendSwitch ("--disable-web-security");
    command_line->AppendSwitchWithValue ("--remote-allow-origins", "*");
    command_line->AppendSwitchWithValue ("--autoplay-policy", "no-user-gesture-required");
    command_line->AppendSwitch ("--disable-background-timer-throttling");
    command_line->AppendSwitch ("--disable-backgrounding-occluded-windows");
    command_line->AppendSwitch ("--disable-background-media-suspend");
    command_line->AppendSwitch ("--disable-renderer-backgrounding");
    command_line->AppendSwitch ("--disable-test-root-certs");
    command_line->AppendSwitch ("--disable-bundled-ppapi-flash");
    command_line->AppendSwitch ("--disable-breakpad");
    command_line->AppendSwitch ("--disable-field-trial-config");
    command_line->AppendSwitch ("--no-experiments");
    // TODO: ACTIVATE THIS IF WE EVER SUPPORT MACOS OFFICIALLY
    /*
if (process_type.empty()) {
#if defined(OS_MACOSX)
  // Disable the macOS keychain prompt. Cookies will not be encrypted.
  command_line->AppendSwitch("use-mock-keychain");
#endif
}*/
}

void BrowserApp::OnBeforeChildProcessLaunch (CefRefPtr<CefCommandLine> command_line) {
    std::string joined {};

    for (const auto& workshopId : this->getWorkshopIds ()) {
	if (!joined.empty ()) {
	    joined += ',';
	}

	joined += workshopId;
    }

    command_line->AppendSwitchWithValue ("lwe-schemes", joined);
}
