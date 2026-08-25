#pragma once

#include <map>

#include "SubprocessApp.h"
#include "WPSchemeHandlerFactory.h"
#include "WallpaperEngine/WebHelper/SpawnConfig.h"
#include "include/cef_app.h"

namespace WallpaperEngine::Media {
class MediaSource;
}

namespace WallpaperEngine::WebBrowser::CEF {
class BrowserApp : public SubprocessApp, public CefBrowserProcessHandler {
public:
    BrowserApp (
	const WallpaperEngine::WebHelper::SpawnConfig& config, WallpaperEngine::Media::MediaSource& mediaSource
    );

    [[nodiscard]] CefRefPtr<CefBrowserProcessHandler> GetBrowserProcessHandler () override;

    void OnContextInitialized () override;
    void OnBeforeCommandLineProcessing (const CefString& process_type, CefRefPtr<CefCommandLine> command_line) override;
    void OnBeforeChildProcessLaunch (CefRefPtr<CefCommandLine> command_line) override;
    void OnScheduleMessagePumpWork (int64_t delay_ms) override;

    static int64_t nextPumpDueMs ();

    static void setPumpWakeFd (int fd);

protected:
    [[nodiscard]] const std::map<std::string, WPSchemeHandlerFactory*>& getHandlerFactories () const;

private:
    std::map<std::string, WPSchemeHandlerFactory*> m_handlerFactories = {};
    IMPLEMENT_REFCOUNTING (BrowserApp);
    DISALLOW_COPY_AND_ASSIGN (BrowserApp);
};
} // namespace WallpaperEngine::WebBrowser::CEF
