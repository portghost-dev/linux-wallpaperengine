#include "BrowserClient.h"

using namespace WallpaperEngine::WebBrowser::CEF;

namespace {
/**
 * Chromium's own network-error document. It is served in place of the page, in the same
 * frame, and it ends with a perfectly ordinary OnLoadEnd - which is exactly why the URL has
 * to be inspected rather than trusted.
 */
constexpr const char* ERROR_PAGE_SCHEME = "chrome-error:";
} // namespace

BrowserClient::BrowserClient (CefRefPtr<CefRenderHandler> ptr) : m_renderHandler (std::move (ptr)) { }

CefRefPtr<CefRenderHandler> BrowserClient::GetRenderHandler () { return m_renderHandler; }

CefRefPtr<CefLoadHandler> BrowserClient::GetLoadHandler () { return this; }

void BrowserClient::recordFailure (const int errorCode, const std::string& errorText, const std::string& failedUrl) {
    if (m_loadFailed) {
	return;
    }

    m_loadFailed = true;
    m_loadErrorCode = errorCode;
    m_loadErrorText = errorText;
    m_failedUrl = failedUrl;
    // a failure can arrive after a previous success on the same instance (a navigation that
    // went wrong); the instance is not loaded any more either way
    m_pageLoaded = false;
}

void BrowserClient::OnLoadEnd (CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame, const int httpStatusCode) {
    if (!frame->IsMain ()) {
	return;
    }

    const std::string url = frame->GetURL ().ToString ();

    if (url.rfind (ERROR_PAGE_SCHEME, 0) == 0) {
	// OnLoadError normally arrives first and has the real reason; this branch is the
	// backstop for an error document committed without one
	recordFailure (0, "the main frame committed Chromium's error page", url);
	return;
    }

    // Custom-scheme responses carry whatever status WPSchemeHandler set (200 today), and a
    // successful load of a 404 body is still not the wallpaper. 0 means "no HTTP status",
    // which is the normal answer for a data: URL and for some custom-scheme responses, so it
    // is explicitly NOT treated as a failure.
    if (httpStatusCode != 0 && (httpStatusCode < 200 || httpStatusCode >= 400)) {
	recordFailure (
	    httpStatusCode, "the main frame loaded with HTTP status " + std::to_string (httpStatusCode), url
	);
	return;
    }

    if (m_loadFailed) {
	return;
    }

    m_pageLoaded = true;
}

void BrowserClient::OnLoadError (
    CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame, const ErrorCode errorCode, const CefString& errorText,
    const CefString& failedUrl
) {
    if (!frame->IsMain ()) {
	return;
    }

    // ERR_ABORTED is what a navigation that was replaced or canceled reports; it is not a
    // wallpaper that failed to load, and treating it as one would make a normal in-page
    // navigation look like a dead instance.
    if (errorCode == ERR_ABORTED) {
	return;
    }

    recordFailure (static_cast<int> (errorCode), errorText.ToString (), failedUrl.ToString ());
}
