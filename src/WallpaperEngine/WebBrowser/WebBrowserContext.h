#pragma once

#include <filesystem>

#include "WallpaperEngine/WebBrowser/CEF/SchemeName.h"
#include "WallpaperEngine/WebHelper/SpawnConfig.h"
#include "include/cef_app.h"
#include "include/cef_browser_process_handler.h"
#include "include/wrapper/cef_helpers.h"

namespace WallpaperEngine::Media {
class MediaSource;
}

namespace WallpaperEngine::WebBrowser::CEF {
class BrowserApp;
}

namespace WallpaperEngine::WebBrowser {
class WebBrowserContext {
public:
    WebBrowserContext (
	CefMainArgs& mainArgs, const WallpaperEngine::WebHelper::SpawnConfig& config,
	WallpaperEngine::Media::MediaSource& mediaSource
    );
    ~WebBrowserContext ();

    void pumpMessageLoop ();

private:
    CefRefPtr<CefApp> m_browserApplication = nullptr;
    CefRefPtr<CefCommandLine> m_commandLine = nullptr;
    // Per-run CEF profile/cache directory (root_cache_path), removed once CEF has fully shut down.
    std::filesystem::path m_cachePath;
};
} // namespace WallpaperEngine::WebBrowser
