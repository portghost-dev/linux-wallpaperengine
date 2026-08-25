#pragma once

#include <string>
#include <vector>

#include "include/cef_app.h"

namespace WallpaperEngine::WebBrowser::CEF {
class SubprocessApp : public CefApp, public CefRenderProcessHandler {
public:
    explicit SubprocessApp (std::vector<std::string> workshopIds);

    void OnRegisterCustomSchemes (CefRawPtr<CefSchemeRegistrar> registrar) override;

    [[nodiscard]] CefRefPtr<CefRenderProcessHandler> GetRenderProcessHandler () override;

    void OnContextCreated (
	CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame, CefRefPtr<CefV8Context> context
    ) override;

    /**
     * The shim, as one self-contained IIFE. Idempotent on purpose: the browser process
     * re-runs it with inject-properties as a backstop for any context this handler did not
     * reach, and re-running it must never discard a callback the page already registered.
     */
    static const char* audioListenerShim ();

    /** parse the --lwe-schemes=<id>,<id>,... switch out of a raw argv (pre-CEF, pre-app) */
    static std::vector<std::string> parseSchemeIds (int argc, char** argv);

protected:
    [[nodiscard]] const std::vector<std::string>& getWorkshopIds () const;

private:
    std::vector<std::string> m_workshopIds;
    IMPLEMENT_REFCOUNTING (SubprocessApp);
    DISALLOW_COPY_AND_ASSIGN (SubprocessApp);
};
} // namespace WallpaperEngine::WebBrowser::CEF
