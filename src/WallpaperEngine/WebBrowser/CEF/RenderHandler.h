#pragma once

#include "include/cef_browser.h"
#include "include/cef_render_handler.h"

namespace WallpaperEngine::WebHelper::Service {
class WebInstance;
}

namespace WallpaperEngine::WebBrowser::CEF {
class RenderHandler : public CefRenderHandler {
public:
    explicit RenderHandler (WallpaperEngine::WebHelper::Service::WebInstance* instance);

    //! \brief
    ~RenderHandler () override = default;

    //! \brief CefRenderHandler interface
    void GetViewRect (CefRefPtr<CefBrowser> browser, CefRect& rect) override;

    void OnPaint (
	CefRefPtr<CefBrowser> browser, PaintElementType type, const RectList& dirtyRects, const void* buffer, int width,
	int height
    ) override;

    //! \brief CefBase interface
    IMPLEMENT_REFCOUNTING (RenderHandler);

private:
    WallpaperEngine::WebHelper::Service::WebInstance* m_instance = nullptr;
};
} // namespace WallpaperEngine::WebBrowser::CEF
