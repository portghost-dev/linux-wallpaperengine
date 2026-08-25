#pragma once

#include "include/cef_client.h"
#include "include/cef_load_handler.h"

#include <string>

namespace WallpaperEngine::WebBrowser::CEF {
// *************************************************************************
//! \brief Provide access to browser-instance-specific callbacks. A single
//! CefClient instance can be shared among any number of browsers.
// *************************************************************************
class BrowserClient : public CefClient, public CefLoadHandler {
public:
    explicit BrowserClient (CefRefPtr<CefRenderHandler> ptr);

    [[nodiscard]] CefRefPtr<CefRenderHandler> GetRenderHandler () override;
    [[nodiscard]] CefRefPtr<CefLoadHandler> GetLoadHandler () override;

    void OnLoadEnd (CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame, int httpStatusCode) override;
    void OnLoadError (
	CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame, ErrorCode errorCode, const CefString& errorText,
	const CefString& failedUrl
    ) override;

    /**
     * True only when the AUTHORED page loaded. See m_loadFailed for why this is not simply
     * "OnLoadEnd fired".
     */
    [[nodiscard]] bool isPageLoaded () const { return m_pageLoaded; }

    [[nodiscard]] bool didLoadFail () const { return m_loadFailed; }
    [[nodiscard]] int loadErrorCode () const { return m_loadErrorCode; }
    [[nodiscard]] const std::string& loadErrorText () const { return m_loadErrorText; }
    [[nodiscard]] const std::string& failedUrl () const { return m_failedUrl; }

    CefRefPtr<CefRenderHandler> m_renderHandler = nullptr;

private:
    /** shared by both callbacks; the FIRST verdict for the main frame is the one that sticks */
    void recordFailure (int errorCode, const std::string& errorText, const std::string& failedUrl);

    bool m_pageLoaded = false;

    bool m_loadFailed = false;
    int m_loadErrorCode = 0;
    std::string m_loadErrorText;
    std::string m_failedUrl;

    IMPLEMENT_REFCOUNTING (BrowserClient);
};
} // namespace WallpaperEngine::WebBrowser::CEF
