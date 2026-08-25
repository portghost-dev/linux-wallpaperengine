#include "WallpaperEngine/WebBrowser/CEF/SubprocessApp.h"

int main (int argc, char* argv[]) {
    const CefMainArgs mainArgs (argc, argv);
    const CefRefPtr<WallpaperEngine::WebBrowser::CEF::SubprocessApp> subprocessApp
	= new WallpaperEngine::WebBrowser::CEF::SubprocessApp (
	    WallpaperEngine::WebBrowser::CEF::SubprocessApp::parseSchemeIds (argc, argv)
	);

    return CefExecuteProcess (mainArgs, subprocessApp, nullptr);
}
