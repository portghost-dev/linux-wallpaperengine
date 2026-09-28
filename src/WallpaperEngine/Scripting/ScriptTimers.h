#pragma once
#include "quickjs.h"

#include <chrono>
#include <cstdint>
#include <map>

namespace WallpaperEngine::Scripting {
class ScriptTimers {
public:
    explicit ScriptTimers (JSContext* context);
    ~ScriptTimers ();
    ScriptTimers (const ScriptTimers&) = delete;
    ScriptTimers& operator= (const ScriptTimers&) = delete;

    uint32_t addTimeout (JSValueConst function, uint64_t duration);
    uint32_t addInterval (JSValueConst function, uint64_t duration);
    void clearTimeout (uint32_t id);
    void clearInterval (uint32_t id);
    void tick ();

private:
    struct Timeout {
	JSValue callback;
	std::chrono::milliseconds duration;
	std::chrono::steady_clock::time_point next;
	int errorsLogged = 0;
    };

    bool runTimerCallback (JSValueConst callback, bool report, const char* context);

    JSContext* m_context;
    uint32_t m_nextTimeoutId = 0;
    uint32_t m_nextIntervalId = 0;
    std::map<uint32_t, Timeout> m_intervals;
    std::map<uint32_t, Timeout> m_timeouts;
};
} // namespace WallpaperEngine::Scripting
