#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <cstring>
#include <map>
#include <memory>
#include <sstream>
#include <string>

#include "WallpaperEngine/Logging/Log.h"
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

JSValue stopTimer (JSContext* ctx, JSValueConst, int argc, JSValueConst* argv, int interval, JSValueConst* data) {
    auto& fixture = fixtureOf (ctx);
    const uint32_t id = ScriptTimers::stopTarget (ctx, argc, argv, data);

    const std::string previous = fixture.phase;
    fixture.phase = "clear";
    if (interval) {
	fixture.timers->clearInterval (id);
    } else {
	fixture.timers->clearTimeout (id);
    }
    fixture.phase = previous;

    return JS_UNDEFINED;
}

JSValue engineTimer (JSContext* ctx, JSValueConst, int, JSValueConst* argv, int interval) {
    auto& fixture = fixtureOf (ctx);
    int delay = 0;
    JS_ToInt32 (ctx, &delay, argv[1]);

    const uint32_t id = interval ? fixture.timers->addInterval (argv[0], static_cast<uint64_t> (delay))
				 : fixture.timers->addTimeout (argv[0], static_cast<uint64_t> (delay));
    JSValue data[] = { JS_NewUint32 (ctx, id) };

    return JS_NewCFunctionData (ctx, stopTimer, 2, interval, 1, data);
}

size_t linesWith (const std::string& lines, const std::string& text) {
    size_t found = 0;
    std::istringstream in (lines);

    for (std::string line; std::getline (in, line);) {
	if (line.find (text) != std::string::npos) {
	    found++;
	}
    }

    return found;
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
    const JSValue engine = JS_NewObject (context);
    JS_SetPropertyStr (
	context, engine, "setTimeout",
	JS_NewCFunctionMagic (context, engineTimer, "setTimeout", 2, JS_CFUNC_generic_magic, 0)
    );
    JS_SetPropertyStr (
	context, engine, "setInterval",
	JS_NewCFunctionMagic (context, engineTimer, "setInterval", 2, JS_CFUNC_generic_magic, 1)
    );
    JS_SetPropertyStr (context, global, "engine", engine);
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

TEST_CASE ("A stop function cancels the timer it was returned for whatever it is called with") {
    Fixture f;

    f.run (
	R"((() => {
	    const a = sentinel (1);
	    globalThis.stopA = engine.setInterval (() => ran (a), 0);
	    const b = sentinel (2);
	    globalThis.stopB = engine.setInterval (() => ran (b), 0);
	    const c = sentinel (3);
	    globalThis.stopC = engine.setTimeout (() => ran (c), 0);
	}) ();)",
	"script"
    );

    REQUIRE (f.freed.empty ());

    f.run ("stopC ();", "script");
    f.tick ();
    f.run ("stopA ();", "script");
    f.tick ();
    f.run ("stopB (1);", "script");
    f.tick ();

    CHECK (f.runCount (3) == 0);
    CHECK (f.freedPhase (3) == "clear");
    CHECK (f.runCount (1) == 1);
    CHECK (f.freedPhase (1) == "clear");
    CHECK (f.runCount (2) == 2);
    CHECK (f.freedPhase (2) == "clear");

    JSValue bound = JS_NewUint32 (f.context, 5);
    JSValue other = JS_NewUint32 (f.context, 9);

    CHECK (ScriptTimers::stopTarget (f.context, 0, nullptr, &bound) == 5);
    CHECK (ScriptTimers::stopTarget (f.context, 1, &other, &bound) == 5);
}

TEST_CASE ("A thrown value whose text or stack throws leaves no exception pending") {
    Fixture f;
    int sentinels = 0;

    SECTION ("its text and its stack both throw strings") {
	f.run (
	    "setTimeout (() => { throw { toString () { throw \"s\"; }, get stack () { throw \"t\"; } }; }, 0);",
	    "script"
	);
    }

    SECTION ("its text and its stack both throw objects") {
	f.run (
	    R"(setTimeout (() => {
		throw { s: sentinel (1), toString () { throw { s: sentinel (2) }; }, get stack () { throw { s: sentinel (3) }; } };
	    }, 0);)",
	    "script"
	);
	sentinels = 3;
    }

    SECTION ("its text throws and its stack reads") {
	f.run (
	    R"(setTimeout (() => { throw { s: sentinel (1), toString () { throw { s: sentinel (2) }; }, stack: "plain" }; }, 0);)",
	    "script"
	);
	sentinels = 2;
    }

    SECTION ("its stack is a value whose text throws") {
	f.run (
	    R"(setTimeout (() => { throw { s: sentinel (1), stack: { toString () { throw { s: sentinel (2) }; } } }; }, 0);)",
	    "script"
	);
	sentinels = 2;
    }

    f.tick ();

    CHECK_FALSE (JS_HasException (f.context));

    for (int tag = 1; tag <= sentinels; tag++) {
	CHECK (f.freedCount (tag) == 1);
    }
}

TEST_CASE ("An interval that clears itself and starts another before throwing is reported once") {
    auto* errors = new std::ostringstream ();
    sLog.addError (errors);
    Fixture f;

    f.run (
	R"((() => {
	    const s = sentinel (1);
	    const next = () => { throw new Error ("again"); };
	    const id = setInterval (() => { clearInterval (id); setInterval (next, 0); ran (s); throw new Error ("boom"); }, 0);
	}) ();)",
	"script"
    );

    for (int i = 0; i < 7; i++) {
	f.tick ();
    }

    const std::string lines = errors->str ();
    errors->setstate (std::ios::badbit);

    CHECK (f.runCount (1) == 1);
    CHECK (f.freedCount (1) == 1);
    CHECK_FALSE (JS_HasException (f.context));
    CHECK (linesWith (lines, "[engine.setInterval]: Error: boom") == 1);
    CHECK (linesWith (lines, "[engine.setInterval]: Error: again") == 3);
}

TEST_CASE ("A throwing interval is reported three times and then no more") {
    auto* errors = new std::ostringstream ();
    sLog.addError (errors);
    Fixture f;

    f.run ("setInterval (() => { throw new Error (\"boom\"); }, 0);", "script");

    for (int i = 0; i < 6; i++) {
	f.tick ();
    }

    const std::string lines = errors->str ();
    errors->setstate (std::ios::badbit);

    CHECK (linesWith (lines, "[engine.setInterval]: Error: boom") == 3);
    CHECK_FALSE (JS_HasException (f.context));
}
