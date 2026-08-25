#pragma once

#include "include/cef_scheme.h"
#include <filesystem>
#include <mutex>
#include <string>

// complete Project/Wallpaper types required: the CefRefPtr refcount macro inlines
// `delete this` into every including TU, which needs ProjectUniquePtr's deleter (and
// Project's own member unique_ptrs) instantiable
#include "WallpaperEngine/Data/Model/Project.h"
#include "WallpaperEngine/Data/Model/Types.h"
#include "WallpaperEngine/Data/Model/Wallpaper.h"

namespace WallpaperEngine::Media {
class MediaSource;
}

namespace WallpaperEngine::WebBrowser::CEF {
using namespace WallpaperEngine::Data::Model;

class WPSchemeHandlerFactory : public CefSchemeHandlerFactory {
public:
    WPSchemeHandlerFactory (
	std::string workshopId, std::filesystem::path path, std::filesystem::path assetsPath,
	WallpaperEngine::Media::MediaSource& mediaSource
    );

    CefRefPtr<CefResourceHandler> Create (
	CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame, const CefString& scheme_name,
	CefRefPtr<CefRequest> request
    ) override;

private:
    const std::string m_workshopId;
    const std::filesystem::path m_path;
    const std::filesystem::path m_assetsPath;
    WallpaperEngine::Media::MediaSource& m_mediaSource;
    /** parsed on first request, owned here for CEF's whole lifetime */
    ProjectUniquePtr m_project = nullptr;
    /** Create() runs on CEF's IO thread; the lazy parse must not race itself */
    std::mutex m_loadMutex;

    IMPLEMENT_REFCOUNTING (WPSchemeHandlerFactory);
    DISALLOW_COPY_AND_ASSIGN (WPSchemeHandlerFactory);
};
} // namespace WallpaperEngine::WebBrowser::CEF
