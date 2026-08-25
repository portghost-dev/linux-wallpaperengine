#include "WPSchemeHandlerFactory.h"
#include "WPSchemeHandler.h"
#include "WallpaperEngine/Assets/AssetLocator.h"
#include "WallpaperEngine/Data/Parsers/ProjectParser.h"
#include "WallpaperEngine/Logging/Log.h"
#include "include/wrapper/cef_helpers.h"

using namespace WallpaperEngine::WebBrowser::CEF;

WPSchemeHandlerFactory::WPSchemeHandlerFactory (
    std::string workshopId, std::filesystem::path path, std::filesystem::path assetsPath,
    WallpaperEngine::Media::MediaSource& mediaSource
) :
    m_workshopId (std::move (workshopId)), m_path (std::move (path)), m_assetsPath (std::move (assetsPath)),
    m_mediaSource (mediaSource) { }

CefRefPtr<CefResourceHandler> WPSchemeHandlerFactory::Create (
    CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame, const CefString& scheme_name,
    CefRefPtr<CefRequest> request
) {
    CEF_REQUIRE_IO_THREAD ();

    // lazy parse, once, on CEF's IO thread. setupAssetLocator only builds fresh
    // containers and ProjectParser is pure, so this is safe off the engine thread.
    std::lock_guard lock (this->m_loadMutex);

    if (this->m_project == nullptr) {
	try {
	    auto locator = WallpaperEngine::Assets::setupAssetLocator (
		this->m_path.string (), this->m_assetsPath, this->m_mediaSource
	    );
	    const auto json = WallpaperEngine::Data::JSON::parseLenient (locator->readString ("project.json"));
	    this->m_project = WallpaperEngine::Data::Parsers::ProjectParser::parse (json, std::move (locator));
	} catch (const std::exception& e) {
	    sLog.error ("Cannot load web wallpaper for scheme wp", this->m_workshopId, ": ", e.what ());
	    return nullptr; // CEF serves an error page; the helper stays alive
	}
    }

    return new WPSchemeHandler (*this->m_project);
}
