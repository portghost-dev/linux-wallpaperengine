#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <cstring>
#include <map>
#include <memory>
#include <string>

#include "WallpaperEngine/Scripting/ScriptTimers.h"

using WallpaperEngine::Scripting::ScriptTimers;

namespace {
struct Fixture {
    std::map<int, int> freed;
    std::map<int, std::string> freedIn;
    std::map<int, int> runs;
    std::map<int, bool> aliveWhenRun;
    std::string phase = "script";
    JSClassID sentinelClass = 0;
    JSRuntime* runtime = nullptr;
    JSContext* context = nullptr;
    std::unique_ptr<ScriptTimers> timers;

    Fixture ();
    ~Fixture ();

    void run (const char* source, const char* during);
    void tick ();
    void teardown ();

    [[nodiscard]] int freedCount (int tag) const { return freed.contains (tag) ? freed.at (tag) : 0; }
    [[nodiscard]] std::string freedPhase (int tag) const { return freedIn.contains (tag) ? freedIn.at (tag) : ""; }
    [[nodiscard]] int runCount (int tag) const { return runs.contains (tag) ? runs.at (tag) : 0; }
    [[nodiscard]] bool wasAliveWhenRun (int tag) const { return aliveWhenRun.contains (tag) && aliveWhenRun.at (tag); }
};

Fixture& fixtureOf (JSContext* ctx) { return *static_cast<Fixture*> (JS_GetContextOpaque (ctx)); }

int tagOf (const Fixture& fixture, JSValueConst value) {
    return static_cast<int> (reinterpret_cast<intptr_t> (JS_GetOpaque (value, fixture.sentinelClass)));
}

void finalizeSentinel (JSRuntime* rt, JSValueConst value) {
    auto& fixture = *static_cast<Fixture*> (JS_GetRuntimeOpaque (rt));
    const int tag = tagOf (fixture, value);

    if (fixture.freed[tag]++ == 0) {
	fixture.freedIn[tag] = fixture.phase;
    }
}

const JSClassDef sentinelDefinition = { .class_name = "Sentinel", .finalizer = finalizeSentinel };

JSValue sentinel (JSContext* ctx, JSValueConst, int, JSValueConst* argv) {
    int tag = 0;
    JS_ToInt32 (ctx, &tag, argv[0]);

    const JSValue object = JS_NewObjectClass (ctx, fixtureOf (ctx).sentinelClass);
    JS_SetOpaque (object, reinterpret_cast<void*> (static_cast<intptr_t> (tag)));

    return object;
}

JSValue ran (JSContext* ctx, JSValueConst, int, JSValueConst* argv) {
    auto& fixture = fixtureOf (ctx);
    const int tag = tagOf (fixture, argv[0]);

    fixture.runs[tag]++;
    fixture.aliveWhenRun[tag] = fixture.freedCount (tag) == 0;

    return JS_UNDEFINED;
}

JSValue setTimeout (JSContext* ctx, JSValueConst, int, JSValueConst* argv) {
    int delay = 0;
    JS_ToInt32 (ctx, &delay, argv[1]);

    return JS_NewUint32 (ctx, fixtureOf (ctx).timers->addTimeout (argv[0], static_cast<uint64_t> (delay)));
}

JSValue setInterval (JSContext* ctx, JSValueConst, int, JSValueConst* argv) {
    int delay = 0;
    JS_ToInt32 (ctx, &delay, argv[1]);

    return JS_NewUint32 (ctx, fixtureOf (ctx).timers->addInterval (argv[0], static_cast<uint64_t> (delay)));
}

JSValue clearTimeout (JSContext* ctx, JSValueConst, int, JSValueConst* argv) {
    auto& fixture = fixtureOf (ctx);
    int id = 0;
    JS_ToInt32 (ctx, &id, argv[0]);

    const std::string previous = fixture.phase;
    fixture.phase = "clear";
    fixture.timers->clearTimeout (static_cast<uint32_t> (id));
    fixture.phase = previous;

    return JS_UNDEFINED;
}

JSValue clearInterval (JSContext* ctx, JSValueConst, int, JSValueConst* argv) {
    auto& fixture = fixtureOf (ctx);
    int id = 0;
    JS_ToInt32 (ctx, &id, argv[0]);

    const std::string previous = fixture.phase;
    fixture.phase = "clear";
    fixture.timers->clearInterval (static_cast<uint32_t> (id));
    fixture.phase = previous;

    return JS_UNDEFINED;
}

Fixture::Fixture () {
    runtime = JS_NewRuntime ();
    JS_SetRuntimeOpaque (runtime, this);
    JS_NewClassID (runtime, &sentinelClass);
    JS_NewClass (runtime, sentinelClass, &sentinelDefinition);

    context = JS_NewContext (runtime);
    JS_SetContextOpaque (context, this);

    const JSValue global = JS_GetGlobalObject (context);
    JS_SetPropertyStr (context, global, "sentinel", JS_NewCFunction (context, sentinel, "sentinel", 1));
    JS_SetPropertyStr (context, global, "ran", JS_NewCFunction (context, ran, "ran", 1));
    JS_SetPropertyStr (context, global, "setTimeout", JS_NewCFunction (context, setTimeout, "setTimeout", 2));
    JS_SetPropertyStr (context, global, "setInterval", JS_NewCFunction (context, setInterval, "setInterval", 2));
    JS_SetPropertyStr (context, global, "clearTimeout", JS_NewCFunction (context, clearTimeout, "clearTimeout", 1));
    JS_SetPropertyStr (context, global, "clearInterval", JS_NewCFunction (context, clearInterval, "clearInterval", 1));
    JS_FreeValue (context, global);

    timers = std::make_unique<ScriptTimers> (context);
}

Fixture::~Fixture () {
    phase = "teardown";
    timers.reset ();
    JS_FreeContext (context);
    JS_FreeRuntime (runtime);
}

void Fixture::run (const char* source, const char* during) {
    phase = during;

    const JSValue result = JS_Eval (context, source, std::strlen (source), "<timers>", JS_EVAL_TYPE_GLOBAL);
    const bool threw = JS_IsException (result);

    if (threw) {
	JS_FreeValue (context, JS_GetException (context));
    }

    JS_FreeValue (context, result);

    REQUIRE_FALSE (threw);
}

void Fixture::tick () {
    phase = "tick";
    timers->tick ();
}

void Fixture::teardown () {
    phase = "teardown";
    timers.reset ();
}
} // namespace

TEST_CASE ("Script timers keep each callback until it fires or is cleared or torn down") {
    Fixture f;

    f.run (
	R"((() => {
	    const a = sentinel (1);
	    setTimeout (() => ran (a), 0);
	    const b = sentinel (2);
	    setTimeout (() => ran (b), 0);
	    const c = sentinel (3);
	    setTimeout (() => ran (c), 3600000);
	    const d = sentinel (4);
	    setInterval (() => ran (d), 0);
	    const e = sentinel (5);
	    setInterval (() => ran (e), 3600000);
	}) ();)",
	"script"
    );

    REQUIRE (f.freed.empty ());

    f.run ("clearTimeout (2);", "clear");
    f.tick ();
    f.tick ();
    f.run ("clearInterval (1);", "clear");
    f.tick ();

    CHECK (f.runCount (1) == 1);
    CHECK (f.freedPhase (1) == "tick");
    CHECK (f.runCount (2) == 0);
    CHECK (f.freedPhase (2) == "clear");
    CHECK (f.runCount (4) == 2);
    CHECK (f.freedPhase (4) == "clear");
    CHECK (f.freedCount (3) == 0);
    CHECK (f.freedCount (5) == 0);

    f.teardown ();

    CHECK (f.runCount (3) == 0);
    CHECK (f.freedPhase (3) == "teardown");
    CHECK (f.runCount (5) == 0);
    CHECK (f.freedPhase (5) == "teardown");

    for (int tag = 1; tag <= 5; tag++) {
	CHECK (f.freedCount (tag) == 1);
    }
}

TEST_CASE ("Script timers stay sound when callbacks clear timers during tick") {
    Fixture f;

    SECTION ("a timeout that clears itself from inside its callback") {
	f.run (
	    R"((() => {
		const s = sentinel (1);
		const id = setTimeout (() => { clearTimeout (id); ran (s); }, 0);
	    }) ();)",
	    "script"
	);

	REQUIRE (f.freed.empty ());

	f.tick ();
	f.tick ();

	CHECK (f.runCount (1) == 1);
	CHECK (f.wasAliveWhenRun (1));
	CHECK (f.freedPhase (1) == "tick");

	f.teardown ();

	CHECK (f.freedCount (1) == 1);
    }

    SECTION ("an interval that clears itself mid-call") {
	f.run (
	    R"((() => {
		const s = sentinel (2);
		const id = setInterval (() => { clearInterval (id); ran (s); }, 0);
	    }) ();)",
	    "script"
	);

	REQUIRE (f.freed.empty ());

	f.tick ();
	f.tick ();

	CHECK (f.runCount (2) == 1);
	CHECK (f.wasAliveWhenRun (2));
	CHECK (f.freedPhase (2) == "tick");

	f.teardown ();

	CHECK (f.freedCount (2) == 1);
    }

    SECTION ("a callback that clears another pending timer and one that already fired during tick") {
	f.run (
	    R"((() => {
		const a = sentinel (3);
		setTimeout (() => ran (a), 0);
		const b = sentinel (4);
		setTimeout (() => { clearTimeout (1); clearTimeout (3); clearInterval (1); ran (b); }, 0);
		const c = sentinel (5);
		setTimeout (() => ran (c), 0);
		const d = sentinel (6);
		setInterval (() => ran (d), 0);
	    }) ();)",
	    "script"
	);

	REQUIRE (f.freed.empty ());

	f.tick ();
	f.tick ();

	CHECK (f.runCount (3) == 1);
	CHECK (f.freedPhase (3) == "tick");
	CHECK (f.runCount (4) == 1);
	CHECK (f.wasAliveWhenRun (4));
	CHECK (f.freedPhase (4) == "tick");
	CHECK (f.runCount (5) == 0);
	CHECK (f.freedPhase (5) == "clear");
	CHECK (f.runCount (6) == 1);
	CHECK (f.freedPhase (6) == "clear");

	f.teardown ();

	for (int tag = 3; tag <= 6; tag++) {
	    CHECK (f.freedCount (tag) == 1);
	}
    }
}
