#include "WebInstance.h"

#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/WebBrowser/CEF/SchemeName.h"
#include "WallpaperEngine/WebBrowser/CEF/SubprocessApp.h"

#include <sstream>

#include <unistd.h>

using namespace WallpaperEngine::WebHelper::Service;
using namespace WallpaperEngine::WebBrowser::CEF;

namespace {
std::string jsEscapeString (const std::string& s) {
    std::string out;
    out.reserve (s.size ());
    for (char c : s) {
	if (c == '"') {
	    out += "\\\"";
	} else if (c == '\\') {
	    out += "\\\\";
	} else if (c == '\n') {
	    out += "\\n";
	} else if (c == '\r') {
	    out += "\\r";
	} else {
	    out += c;
	}
    }
    return out;
}
} // namespace

WebInstance::WebInstance (
    const InstanceId id, std::string workshopId, std::string file, const int width, const int height,
    const int framerate
) :
    m_id (id), m_workshopId (std::move (workshopId)), m_file (std::move (file)), m_width (width), m_height (height),
    m_framerate (framerate) { }

WebInstance::~WebInstance () { this->close (); }

bool WebInstance::open () {
    if (!this->m_frames.allocate (
	    getpid (), this->m_id, static_cast<uint32_t> (this->m_width), static_cast<uint32_t> (this->m_height)
	)) {
	sLog.error (
	    "web-service: cannot allocate frame buffer for instance ", this->m_id, ": ", this->m_frames.error ()
	);
	return false;
    }

    const std::string url = generateSchemeName (this->m_workshopId) + "://root/" + this->m_file;

    CefWindowInfo info;
    info.SetAsWindowless (0);

    CefBrowserSettings settings;
    // the engine already clamped this to CEF's documented 60 minimum
    settings.windowless_frame_rate = this->m_framerate;

    this->m_renderHandler = new WallpaperEngine::WebBrowser::CEF::RenderHandler (this);
    this->m_client = new WallpaperEngine::WebBrowser::CEF::BrowserClient (this->m_renderHandler);
    this->m_browser = CefBrowserHost::CreateBrowserSync (info, this->m_client, url, settings, nullptr, nullptr);

    if (this->m_browser == nullptr) {
	sLog.error ("web-service: CreateBrowserSync failed for instance ", this->m_id, " (", url, ")");
	this->m_frames.release ();
	this->m_client = nullptr;
	this->m_renderHandler = nullptr;

	return false;
    }

    sLog.out (
	"web-service: instance ", this->m_id, " -> ", url, " ", this->m_width, "x", this->m_height, " @",
	this->m_framerate, ", frames in ", this->m_frames.name ()
    );

    return true;
}

void WebInstance::close () {
    if (this->m_browser != nullptr) {
	this->m_browser->GetHost ()->CloseBrowser (true);
	this->m_browser = nullptr;
    }

    this->m_renderHandler = nullptr;
    this->m_client = nullptr;
}

void WebInstance::setSize (const int width, const int height) {
    const int newWidth = width > 0 ? width : this->m_width;
    const int newHeight = height > 0 ? height : this->m_height;

    if (newWidth == this->m_width && newHeight == this->m_height && this->m_frames.isOpen ()) {
	return;
    }

    if (!CefCurrentlyOn (TID_UI)) {
	sLog.error (
	    "web-service: resize for instance ", this->m_id, " ran off the UI thread; GetViewRect can now race the size"
	);
    }

    this->m_width = newWidth;
    this->m_height = newHeight;

    if (this->m_frames.isOpen ()
	&& !this->m_frames.allocate (
	    getpid (), this->m_id, static_cast<uint32_t> (this->m_width), static_cast<uint32_t> (this->m_height)
	)) {
	sLog.error ("web-service: cannot resize frame buffer for instance ", this->m_id, ": ", this->m_frames.error ());
    }

    if (this->m_browser != nullptr) {
	this->m_browser->GetHost ()->WasResized ();
    }
}

void WebInstance::execute (const std::string& script) {
    if (this->m_browser == nullptr) {
	return;
    }

    if (CefRefPtr<CefFrame> frame = this->m_browser->GetMainFrame (); frame) {
	frame->ExecuteJavaScript (script, frame->GetURL (), 0);
    }
}

void WebInstance::mouseMove (const int x, const int y) {
    if (this->m_browser == nullptr) {
	return;
    }

    // the engine already resolved normalized coordinates to pixels for THIS instance's
    // size, so nothing here has to know about viewports or monitor layout
    CefMouseEvent event;
    event.x = x;
    event.y = y;

    this->m_browser->GetHost ()->SendMouseMoveEvent (event, false);
}

void WebInstance::mouseClick (const int x, const int y, const MouseButton button, const bool released) {
    if (this->m_browser == nullptr) {
	return;
    }

    CefMouseEvent event;
    event.x = x;
    event.y = y;

    this->m_browser->GetHost ()->SendMouseClickEvent (
	event,
	button == MouseButton::Left ? CefBrowserHost::MouseButtonType::MBT_LEFT
				    : CefBrowserHost::MouseButtonType::MBT_RIGHT,
	released, 1
    );
}

std::string WebInstance::renderPropertyValue (const PropertyValue& property) {
    switch (property.kind) {
	case PropertyValue::Kind::Boolean:
	    return property.booleanValue ? "true" : "false";
	case PropertyValue::Kind::Number:
	    {
		// enough digits to round-trip a double, so a slider value the engine parsed
		// is the value the page arithmetic sees
		std::ostringstream number;
		number.precision (17);
		number << property.numberValue;
		return number.str ();
	    }
	case PropertyValue::Kind::String:
	    return "\"" + jsEscapeString (property.stringValue) + "\"";
    }

    return "null";
}

std::string WebInstance::renderApplyUserProperties (const std::vector<PropertyValue>& properties) {
    std::ostringstream js;
    js << "(function() {\n"
       << "  if (!window.wallpaperPropertyListener) return;\n"
       << "  if (!window.wallpaperPropertyListener.applyUserProperties) return;\n"
       << "  window.wallpaperPropertyListener.applyUserProperties({\n";

    bool first = true;

    for (const auto& property : properties) {
	if (!first) {
	    js << ",\n";
	}

	first = false;
	js << "    \"" << jsEscapeString (property.key) << "\": {\"value\": " << renderPropertyValue (property) << "}";
    }

    js << "\n  });\n"
       << "})();\n";

    return js.str ();
}

void WebInstance::injectProperties (const std::vector<PropertyValue>& properties) {
    this->execute (WallpaperEngine::WebBrowser::CEF::SubprocessApp::audioListenerShim ());
    this->execute (renderApplyUserProperties (properties));
}

void WebInstance::setProperty (const PropertyValue& property) {
    // Same renderer as the bulk injection, so a live single-key update cannot type a value
    // differently from the way the initial injection typed it (Protocol.h, encodeSetProperty).
    this->execute (renderApplyUserProperties ({ property }));
}

void WebInstance::injectAudio (const float* bands) {
    if (this->m_browser == nullptr) {
	return;
    }

    std::ostringstream js;
    js << "(function(){"
       << "if(!window.__lweAudioCallback)return;"
       << "var a=new Float32Array([";

    for (size_t i = 0; i < AUDIO_BANDS; ++i) {
	if (i > 0) {
	    js << ',';
	}

	js << bands[i];
    }

    for (size_t i = 0; i < AUDIO_BANDS; ++i) {
	js << ',' << bands[i];
    }

    js << "]);window.__lweAudioCallback(a);})();";

    this->execute (js.str ());
}

void WebInstance::onPaint (const void* buffer, const int width, const int height) {
    this->m_frames.publish (buffer, static_cast<uint32_t> (width), static_cast<uint32_t> (height));
}

bool WebInstance::isPageLoaded () const { return this->m_client != nullptr && this->m_client->isPageLoaded (); }

bool WebInstance::didLoadFail () const { return this->m_client != nullptr && this->m_client->didLoadFail (); }

int WebInstance::loadErrorCode () const { return this->m_client != nullptr ? this->m_client->loadErrorCode () : 0; }

std::string WebInstance::loadErrorText () const {
    return this->m_client != nullptr ? this->m_client->loadErrorText () : std::string ();
}

std::string WebInstance::failedUrl () const {
    return this->m_client != nullptr ? this->m_client->failedUrl () : std::string ();
}
