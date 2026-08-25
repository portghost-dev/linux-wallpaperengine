#include "SubprocessApp.h"
#include "SchemeName.h"

#include "include/cef_frame.h"

#include <cstring>

using namespace WallpaperEngine::WebBrowser::CEF;

SubprocessApp::SubprocessApp (std::vector<std::string> workshopIds) : m_workshopIds (std::move (workshopIds)) { }

CefRefPtr<CefRenderProcessHandler> SubprocessApp::GetRenderProcessHandler () { return this; }

const char* SubprocessApp::audioListenerShim () {
    return "(function(){"
	   "if(typeof window.wallpaperRegisterAudioListener==='function')return;"
	   "if(typeof window.__lweAudioCallback==='undefined')window.__lweAudioCallback=null;"
	   "window.wallpaperRegisterAudioListener=function(cb){window.__lweAudioCallback=cb;};"
	   "})();";
}

void SubprocessApp::OnContextCreated (
    CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame, CefRefPtr<CefV8Context> context
) {
    // Main frame only. Subframes and the extension contexts CEF creates in every fresh
    // profile have no wallpaper API and no business carrying one.
    if (frame == nullptr || !frame->IsMain ()) {
	return;
    }

    frame->ExecuteJavaScript (audioListenerShim (), frame->GetURL (), 0);
}

void SubprocessApp::OnRegisterCustomSchemes (CefRawPtr<CefSchemeRegistrar> registrar) {
    // register all the needed schemes, "wp" + the background id is going to be our scheme
    for (const auto& workshopId : this->m_workshopIds) {
	registrar->AddCustomScheme (
	    generateSchemeName (workshopId),
	    CEF_SCHEME_OPTION_STANDARD | CEF_SCHEME_OPTION_SECURE | CEF_SCHEME_OPTION_FETCH_ENABLED
	);
    }
}

std::vector<std::string> SubprocessApp::parseSchemeIds (const int argc, char** argv) {
    static constexpr const char* PREFIX = "--lwe-schemes=";
    const size_t prefixLength = std::strlen (PREFIX);
    std::vector<std::string> ids {};

    for (int i = 1; i < argc; i++) {
	if (std::strncmp (argv[i], PREFIX, prefixLength) != 0) {
	    continue;
	}

	const std::string value = argv[i] + prefixLength;
	size_t start = 0;

	while (start <= value.length ()) {
	    const size_t end = value.find (',', start);
	    const std::string id = value.substr (start, end == std::string::npos ? std::string::npos : end - start);

	    if (!id.empty ()) {
		ids.push_back (id);
	    }

	    if (end == std::string::npos) {
		break;
	    }

	    start = end + 1;
	}
    }

    return ids;
}

const std::vector<std::string>& SubprocessApp::getWorkshopIds () const { return this->m_workshopIds; }
