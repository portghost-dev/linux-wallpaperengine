#include "RenderHandler.h"

#include "WallpaperEngine/WebHelper/Service/WebInstance.h"

#include <algorithm>

using namespace WallpaperEngine::WebBrowser::CEF;

RenderHandler::RenderHandler (WallpaperEngine::WebHelper::Service::WebInstance* instance) : m_instance (instance) { }

// Required by CEF
void RenderHandler::GetViewRect (CefRefPtr<CefBrowser> browser, CefRect& rect) {
    const int width = std::max (1, this->m_instance->getWidth ());
    const int height = std::max (1, this->m_instance->getHeight ());

    rect = CefRect (0, 0, width, height);
}

// Will be executed in CEF message loop
void RenderHandler::OnPaint (
    CefRefPtr<CefBrowser> browser, PaintElementType type, const RectList& dirtyRects, const void* buffer,
    const int width, const int height
) {
    this->m_instance->onPaint (buffer, width, height);
}
