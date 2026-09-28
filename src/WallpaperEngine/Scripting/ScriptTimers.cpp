#include "ScriptTimers.h"
#include "ScriptEngine.h"

#include <ranges>
#include <vector>

using namespace WallpaperEngine::Scripting;

ScriptTimers::ScriptTimers (JSContext* context) : m_context (context) { }

ScriptTimers::~ScriptTimers () {
    // clear all the timeouts and intervals
    for (const auto& timeout : this->m_timeouts | std::views::values) {
	JS_FreeValue (this->m_context, timeout.callback);
    }
    for (const auto& interval : this->m_intervals | std::views::values) {
	JS_FreeValue (this->m_context, interval.callback);
    }
}

uint32_t ScriptTimers::addTimeout (JSValueConst function, uint64_t duration) {
    const auto id = ++this->m_nextTimeoutId;

    this->m_timeouts[id] = Timeout { .callback = JS_DupValue (this->m_context, function),
				     .duration = std::chrono::milliseconds (duration),
				     .next = std::chrono::steady_clock::now () + std::chrono::milliseconds (duration) };

    return id;
}

uint32_t ScriptTimers::addInterval (JSValueConst function, uint64_t duration) {
    const auto id = ++this->m_nextIntervalId;

    this->m_intervals[id]
	= Timeout { .callback = JS_DupValue (this->m_context, function),
		    .duration = std::chrono::milliseconds (duration),
		    .next = std::chrono::steady_clock::now () + std::chrono::milliseconds (duration) };

    return id;
}

void ScriptTimers::clearInterval (uint32_t id) {
    const auto it = this->m_intervals.find (id);

    if (it == this->m_intervals.end ()) {
	return;
    }

    const JSValue callback = it->second.callback;

    this->m_intervals.erase (it);

    JS_FreeValue (this->m_context, callback);
}

void ScriptTimers::clearTimeout (uint32_t id) {
    const auto it = this->m_timeouts.find (id);

    if (it == this->m_timeouts.end ()) {
	return;
    }

    const JSValue callback = it->second.callback;

    this->m_timeouts.erase (it);

    JS_FreeValue (this->m_context, callback);
}

uint32_t ScriptTimers::stopTarget (JSContext* context, int, JSValueConst*, JSValueConst* data) {
    uint32_t id = 0;

    JS_ToUint32 (context, &id, data[0]);

    return id;
}

bool ScriptTimers::runTimerCallback (JSValueConst callback, const bool report, const char* context) {
    const JSValue result = JS_Call (this->m_context, callback, JS_NULL, 0, nullptr);
    const bool threw = JS_IsException (result);

    if (threw) {
	// a callback that keeps throwing is reported a few times, not every fire
	if (report) {
	    ScriptEngine::logException (this->m_context, context);
	} else {
	    JS_FreeValue (this->m_context, JS_GetException (this->m_context));
	}
    }

    JS_FreeValue (this->m_context, result);

    return threw;
}

void ScriptTimers::tick () {
    const auto now = std::chrono::steady_clock::now ();
    std::vector<uint32_t> due;

    // check any interval and run them if needed
    for (const auto& [id, interval] : this->m_intervals) {
	if (interval.next <= now) {
	    due.push_back (id);
	}
    }

    for (const auto id : due) {
	const auto it = this->m_intervals.find (id);

	if (it == this->m_intervals.end ()) {
	    continue;
	}

	it->second.next = now + it->second.duration;

	const bool report = it->second.errorsLogged < 3;
	const JSValue callback = JS_DupValue (this->m_context, it->second.callback);
	const bool threw = this->runTimerCallback (callback, report, "engine.setInterval");

	JS_FreeValue (this->m_context, callback);

	if (threw && report) {
	    if (const auto again = this->m_intervals.find (id); again != this->m_intervals.end ()) {
		again->second.errorsLogged++;
	    }
	}
    }

    due.clear ();

    // check any timeout and run them if needed
    for (const auto& [id, timeout] : this->m_timeouts) {
	if (timeout.next <= now) {
	    due.push_back (id);
	}
    }

    for (const auto id : due) {
	const auto it = this->m_timeouts.find (id);

	if (it == this->m_timeouts.end ()) {
	    continue;
	}

	const JSValue callback = it->second.callback;

	this->m_timeouts.erase (it);
	this->runTimerCallback (callback, true, "engine.setTimeout");

	JS_FreeValue (this->m_context, callback);
    }
}
