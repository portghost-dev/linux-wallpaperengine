#include "ConsoleObject.h"

#include "EngineObject.h"
#include "ScriptEngine.h"
#include "WallpaperEngine/Data/Utils/ScopeGuard.h"
#include "WallpaperEngine/Render/Wallpapers/CScene.h"

#include <chrono>
#include <map>

using namespace WallpaperEngine::Scripting;

namespace {
// wallpapers are third-party code: one throwing per frame logged ~540 lines/s, past
// journald's rate limit, drowning the engine's own diagnostics. Identical messages
// collapse to one line per window, with the suppressed count on the next emission.
constexpr auto CONSOLE_SUPPRESS_WINDOW = std::chrono::seconds (10);
constexpr size_t CONSOLE_DISTINCT_CAP = 512;

bool consoleShouldLog (const std::string& message, size_t& suppressedOut) {
    static std::map<std::string, std::pair<std::chrono::steady_clock::time_point, size_t>> seen;
    const auto now = std::chrono::steady_clock::now ();

    // bounded by distinct TEXTS; a wallpaper varying its message every call would
    // otherwise grow the map without limit
    if (seen.size () > CONSOLE_DISTINCT_CAP) {
	seen.clear ();
    }

    const auto it = seen.find (message);

    if (it == seen.end ()) {
	seen[message] = { now, 0 };
	suppressedOut = 0;
	return true;
    }

    auto& [windowStart, suppressed] = it->second;

    if (now - windowStart >= CONSOLE_SUPPRESS_WINDOW) {
	suppressedOut = suppressed;
	windowStart = now;
	suppressed = 0;
	return true;
    }

    suppressed++;
    suppressedOut = 0;
    return false;
}
} // namespace

JSValue console_log (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    if (argc < 1) {
	return JS_UNDEFINED;
    }

    std::stringstream stream;

    for (int i = 0; i < argc; i++) {
	const char* str = JS_ToCString (ctx, argv[i]);
	ScopeGuard guard ([ctx, str] { JS_FreeCString (ctx, str); });

	stream << str;
    }

    size_t suppressed = 0;

    if (!consoleShouldLog (stream.str (), suppressed)) {
	return JS_UNDEFINED;
    }

    if (suppressed > 0) {
	sLog.out (stream.str (), " [", suppressed, " identical suppressed in the last 10s]");
    } else {
	sLog.out (stream.str ());
    }

    return JS_UNDEFINED;
}

JSValue console_error (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    if (argc < 1) {
	return JS_UNDEFINED;
    }

    std::stringstream stream;

    for (int i = 0; i < argc; i++) {
	const char* str = JS_ToCString (ctx, argv[i]);
	ScopeGuard guard ([ctx, str] { JS_FreeCString (ctx, str); });

	stream << str;
    }

    size_t suppressed = 0;

    if (!consoleShouldLog (stream.str (), suppressed)) {
	return JS_UNDEFINED;
    }

    if (suppressed > 0) {
	sLog.error (stream.str (), " [", suppressed, " identical suppressed in the last 10s]");
    } else {
	sLog.error (stream.str ());
    }

    return JS_UNDEFINED;
}

ConsoleObject::ConsoleObject (ScriptEngine& engine, Render::Wallpapers::CScene& scene) :
    m_scene (scene), m_engine (engine), m_classId (0) {
    this->m_definition = { .class_name = "IConsole" };
    JS_NewClassID (this->m_engine.getRuntime (), &this->m_classId);
    JS_NewClass (this->m_engine.getRuntime (), this->m_classId, &this->m_definition);
    this->m_instance = JS_NewObjectClass (this->m_engine.getContext (), this->m_classId);

    JS_DupValue (this->m_engine.getContext (), this->m_instance);

    // set properties
    JS_SetOpaque (this->m_instance, this);
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), this->m_instance, "log",
	JS_NewCFunction (this->m_engine.getContext (), console_log, "log", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), this->m_instance, "error",
	JS_NewCFunction (this->m_engine.getContext (), console_error, "error", 1), JS_PROP_ENUMERABLE
    );
}

ConsoleObject::~ConsoleObject () { JS_FreeValue (this->m_engine.getContext (), this->m_instance); }