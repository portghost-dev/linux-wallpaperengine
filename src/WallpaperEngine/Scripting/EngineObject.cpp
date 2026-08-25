#include "EngineObject.h"
#include "ScriptEngine.h"
#include "WallpaperEngine/Audio/Drivers/Recorders/PlaybackRecorder.h"
#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/Render/Wallpapers/CScene.h"

#include <cstdlib>
#include <ranges>

using namespace WallpaperEngine::Scripting;

extern float g_Time;
extern float g_TimeLast;
extern float g_Daytime;

static uint32_t EngineInstanceId = 0;
std::map<uint32_t, EngineObject*> engineInstances;

JSValue engine_set_value (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    return JS_ThrowTypeError (ctx, "property is read-only");
}

JSValue engine_open_user_shortcut (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    return JS_UNDEFINED;
}

JSValue engine_get_frametime (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    const float delta = g_Time - g_TimeLast;
    return JS_NewFloat64 (ctx, delta > 0.0f ? delta : 1.0f / 60.0f);
}

JSValue engine_get_runtime (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    return JS_NewFloat64 (ctx, g_Time);
}

JSValue engine_get_screenresolution (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    // canvas dims: the space script-visible coordinates live in (the 3D-camera
    // script only uses this for mouse sensitivity scaling and must not throw)
    JSClassID classId;
    auto* container = static_cast<EngineObject*> (JS_GetAnyOpaque (this_val, &classId));
    if (container == nullptr) {
	return JS_UNDEFINED;
    }
    JSValue out = JS_NewObject (ctx);
    JS_SetPropertyStr (ctx, out, "x", JS_NewFloat64 (ctx, container->getScene ().getWidth ()));
    JS_SetPropertyStr (ctx, out, "y", JS_NewFloat64 (ctx, container->getScene ().getHeight ()));
    return out;
}

JSValue engine_get_canvassize (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    JSClassID classId;
    auto* container = static_cast<EngineObject*> (JS_GetAnyOpaque (this_val, &classId));

    if (container == nullptr || !container->getScene ().getCamera ().isOrthogonal ()) {
	return JS_UNDEFINED;
    }

    JSValue out = JS_NewObject (ctx);
    JS_SetPropertyStr (ctx, out, "x", JS_NewFloat64 (ctx, container->getScene ().getWidth ()));
    JS_SetPropertyStr (ctx, out, "y", JS_NewFloat64 (ctx, container->getScene ().getHeight ()));
    return out;
}

JSValue engine_get_daytime (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    return JS_NewFloat64 (ctx, g_Daytime);
}

JSValue engine_stop_interval (
    JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv, int magic, JSValueConst* func_data
) {
    if (argc != 1) {
	return JS_ThrowTypeError (ctx, "invalid arguments");
    }

    const auto it = engineInstances.find (magic);

    if (it == engineInstances.end ()) {
	return JS_ThrowTypeError (ctx, "invalid object");
    }

    int id = 0;

    JS_ToInt32 (ctx, &id, argv[0]);

    it->second->clearInterval (id);

    return JS_UNDEFINED;
}

JSValue engine_stop_timeout (
    JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv, int magic, JSValueConst* func_data
) {
    if (argc != 1) {
	return JS_ThrowTypeError (ctx, "invalid arguments");
    }

    const auto it = engineInstances.find (magic);

    if (it == engineInstances.end ()) {
	return JS_ThrowTypeError (ctx, "invalid object");
    }

    int id = 0;

    JS_ToInt32 (ctx, &id, argv[0]);

    it->second->clearTimeout (id);

    return JS_UNDEFINED;
}

JSValue engine_set_interval (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv, int magic) {
    if (argc < 1) {
	return JS_ThrowTypeError (ctx, "invalid arguments");
    }

    int delay = 0;

    if (argc > 1) {
	JS_ToInt32 (ctx, &delay, argv[1]);
    }

    JSValue function = argv[0];

    if (!JS_IsFunction (ctx, function)) {
	return JS_ThrowTypeError (ctx, "invalid arguments");
    }

    const auto it = engineInstances.find (magic);

    if (it == engineInstances.end ()) {
	return JS_ThrowTypeError (ctx, "invalid object");
    }

    int id = it->second->reserveNextIntervalId (function, delay);

    JSValue args[] = { JS_NewInt32 (ctx, id) };

    return JS_NewCFunctionData (ctx, engine_stop_interval, 2, magic, 1, args);
}

JSValue engine_set_timeout (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv, int magic) {
    if (argc < 1) {
	return JS_ThrowTypeError (ctx, "invalid arguments");
    }

    int delay = 0;

    if (argc > 1) {
	JS_ToInt32 (ctx, &delay, argv[1]);
    }

    JSValue function = argv[0];

    if (!JS_IsFunction (ctx, function)) {
	return JS_ThrowTypeError (ctx, "invalid arguments");
    }

    const auto it = engineInstances.find (magic);

    if (it == engineInstances.end ()) {
	return JS_ThrowTypeError (ctx, "invalid object");
    }

    int id = it->second->reserveNextTimeoutId (function, delay);

    JSValue args[] = { JS_NewInt32 (ctx, id) };

    return JS_NewCFunctionData (ctx, engine_stop_timeout, 2, magic, 1, args);
}

JSValue engine_register_audio_buffers (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv, int magic) {
    const auto it = engineInstances.find (magic);

    if (it == engineInstances.end ()) {
	return JS_ThrowTypeError (ctx, "invalid object");
    }

    int resolution = 16;

    if (argc >= 1) {
	JS_ToInt32 (ctx, &resolution, argv[0]);
    }

    return it->second->registerAudioBuffers (resolution);
}

EngineObject::EngineObject (ScriptEngine& engine, Render::Wallpapers::CScene& scene) :
    m_scene (scene), m_engine (engine), m_instanceId (++EngineInstanceId), m_classId (0) {
    engineInstances[this->m_instanceId] = this;
    this->m_definition = { .class_name = "IEngine" };
    JS_NewClassID (this->m_engine.getRuntime (), &this->m_classId);
    JS_NewClass (this->m_engine.getRuntime (), this->m_classId, &this->m_definition);
    this->m_instance = JS_NewObjectClass (this->m_engine.getContext (), this->m_classId);

    JS_DupValue (this->m_engine.getContext (), this->m_instance);

    // set properties
    JS_SetOpaque (this->m_instance, this);
    JS_DefinePropertyGetSet (
	this->m_engine.getContext (), this->m_instance, JS_NewAtom (this->m_engine.getContext (), "frametime"),
	JS_NewCFunction (this->m_engine.getContext (), engine_get_frametime, "get", 0),
	JS_NewCFunction (this->m_engine.getContext (), engine_set_value, "set", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyGetSet (
	this->m_engine.getContext (), this->m_instance, JS_NewAtom (this->m_engine.getContext (), "screenResolution"),
	JS_NewCFunction (this->m_engine.getContext (), engine_get_screenresolution, "get", 0),
	JS_NewCFunction (this->m_engine.getContext (), engine_set_value, "set", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyGetSet (
	this->m_engine.getContext (), this->m_instance, JS_NewAtom (this->m_engine.getContext (), "canvasSize"),
	JS_NewCFunction (this->m_engine.getContext (), engine_get_canvassize, "get", 0),
	JS_NewCFunction (this->m_engine.getContext (), engine_set_value, "set", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyGetSet (
	this->m_engine.getContext (), this->m_instance, JS_NewAtom (this->m_engine.getContext (), "runtime"),
	JS_NewCFunction (this->m_engine.getContext (), engine_get_runtime, "get", 0),
	JS_NewCFunction (this->m_engine.getContext (), engine_set_value, "set", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyGetSet (
	this->m_engine.getContext (), this->m_instance, JS_NewAtom (this->m_engine.getContext (), "timeOfDay"),
	JS_NewCFunction (this->m_engine.getContext (), engine_get_daytime, "get", 0),
	JS_NewCFunction (this->m_engine.getContext (), engine_set_value, "set", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), this->m_instance, "AUDIO_RESOLUTION_16",
	JS_NewInt32 (this->m_engine.getContext (), 16), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), this->m_instance, "AUDIO_RESOLUTION_32",
	JS_NewInt32 (this->m_engine.getContext (), 32), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), this->m_instance, "AUDIO_RESOLUTION_64",
	JS_NewInt32 (this->m_engine.getContext (), 64), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), this->m_instance, "registerAudioBuffers",
	JS_NewCFunctionMagic (
	    this->m_engine.getContext (), engine_register_audio_buffers, "registerAudioBuffers", 1,
	    JS_CFUNC_generic_magic, this->m_instanceId
	),
	JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), this->m_instance, "setInterval",
	JS_NewCFunctionMagic (
	    this->m_engine.getContext (), engine_set_interval, "setInterval", 2, JS_CFUNC_generic_magic,
	    this->m_instanceId
	),
	JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), this->m_instance, "setTimeout",
	JS_NewCFunctionMagic (
	    this->m_engine.getContext (), engine_set_timeout, "setTimeout", 2, JS_CFUNC_generic_magic,
	    this->m_instanceId
	),
	JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), this->m_instance, "openUserShortcut",
	JS_NewCFunction (this->m_engine.getContext (), engine_open_user_shortcut, "openUserShortcut", 0),
	JS_PROP_ENUMERABLE
    );
    // TODO: ADD THE REST OF THE DEFINITION!
}

EngineObject::~EngineObject () {
    // clear all the timeouts and intervals
    for (const auto& [id, timeout] : this->m_timeouts) {
	JS_FreeValue (this->m_engine.getContext (), timeout.callback);
    }
    for (const auto& [id, interval] : this->m_intervals) {
	JS_FreeValue (this->m_engine.getContext (), interval.callback);
    }
    for (const auto& link : this->m_audioBuffers) {
	JS_FreeValue (this->m_engine.getContext (), link.average);
	JS_FreeValue (this->m_engine.getContext (), link.left);
	JS_FreeValue (this->m_engine.getContext (), link.right);
    }
    this->m_audioBuffers.clear ();

    engineInstances.erase (this->m_instanceId);
    this->m_intervals.clear ();
    this->m_timeouts.clear ();

    JS_FreeValue (this->m_engine.getContext (), this->m_instance);
}

uint32_t EngineObject::reserveNextTimeoutId (JSValue function, uint64_t duration) {
    const auto id = ++this->m_nextTimeoutId;

    this->m_timeouts[id] = Timeout { .callback = function,
				     .duration = std::chrono::milliseconds (duration),
				     .next = std::chrono::steady_clock::now () + std::chrono::milliseconds (duration) };

    return id;
}

uint32_t EngineObject::reserveNextIntervalId (JSValue function, uint64_t duration) {
    const auto id = ++this->m_nextIntervalId;

    this->m_intervals[id]
	= Timeout { .callback = function,
		    .duration = std::chrono::milliseconds (duration),
		    .next = std::chrono::steady_clock::now () + std::chrono::milliseconds (duration) };

    return id;
}

void EngineObject::clearInterval (uint32_t id) {
    const auto it = this->m_intervals.find (id);

    if (it == this->m_intervals.end ()) {
	return;
    }

    JS_FreeValue (this->getEngine ().getContext (), it->second.callback);

    this->m_intervals.erase (id);
}

void EngineObject::clearTimeout (uint32_t id) {
    const auto it = this->m_timeouts.find (id);

    if (it == this->m_timeouts.end ()) {
	return;
    }

    JS_FreeValue (this->getEngine ().getContext (), it->second.callback);

    this->m_timeouts.erase (id);
}

JSValue EngineObject::registerAudioBuffers (int requestedResolution) {
    JSContext* ctx = this->m_engine.getContext ();
    const int resolution = requestedResolution <= 16 ? 16 : requestedResolution <= 32 ? 32 : 64;

    if (resolution != requestedResolution) {
	sLog.error ("registerAudioBuffers: unsupported resolution ", requestedResolution, ", snapping to ", resolution);
    }

    const AudioBufferLink link {
	.resolution = resolution,
	.average = JS_NewArray (ctx),
	.left = JS_NewArray (ctx),
	.right = JS_NewArray (ctx),
    };

    for (int i = 0; i < resolution; i++) {
	JS_SetPropertyUint32 (ctx, link.average, i, JS_NewFloat64 (ctx, 0.0));
	JS_SetPropertyUint32 (ctx, link.left, i, JS_NewFloat64 (ctx, 0.0));
	JS_SetPropertyUint32 (ctx, link.right, i, JS_NewFloat64 (ctx, 0.0));
    }

    JSValue result = JS_NewObject (ctx);

    JS_DefinePropertyValueStr (ctx, result, "average", JS_DupValue (ctx, link.average), JS_PROP_ENUMERABLE);
    JS_DefinePropertyValueStr (ctx, result, "left", JS_DupValue (ctx, link.left), JS_PROP_ENUMERABLE);
    JS_DefinePropertyValueStr (ctx, result, "right", JS_DupValue (ctx, link.right), JS_PROP_ENUMERABLE);

    this->m_audioBuffers.push_back (link);

    return result;
}

void EngineObject::updateAudioBuffers () {
    if (this->m_audioBuffers.empty ()) {
	return;
    }

    JSContext* ctx = this->m_engine.getContext ();
    const auto& recorder = this->m_scene.getAudioContext ().getRecorder ();

    for (const auto& link : this->m_audioBuffers) {
	const float* bands = link.resolution == 64 ? recorder.audio64
	    : link.resolution == 32                ? recorder.audio32
						   : recorder.audio16;

	for (int i = 0; i < link.resolution; i++) {
	    // the loopback capture is mono; left/right mirror the averaged spectrum
	    JS_SetPropertyUint32 (ctx, link.average, i, JS_NewFloat64 (ctx, bands[i]));
	    JS_SetPropertyUint32 (ctx, link.left, i, JS_NewFloat64 (ctx, bands[i]));
	    JS_SetPropertyUint32 (ctx, link.right, i, JS_NewFloat64 (ctx, bands[i]));
	}
    }

    static const bool s_audioStats = getenv ("LWE_AUDIOSTATS") != nullptr;
    static int s_audioStatsTick = 0;
    if (s_audioStats && (s_audioStatsTick++ % 30) == 0) {
	const auto& first = this->m_audioBuffers.front ();
	const float* bands = first.resolution == 64 ? recorder.audio64
	    : first.resolution == 32                ? recorder.audio32
						    : recorder.audio16;
	sLog.out (
	    "LWE-AUDIOSTATS buffers=", this->m_audioBuffers.size (), " res=", first.resolution, " avg[0..3]=", bands[0],
	    " ", bands[1], " ", bands[2], " ", bands[3]
	);
    }
}

void EngineObject::tick () {
    // refresh audio-response arrays first so update() callbacks read current data
    this->updateAudioBuffers ();

    const auto now = std::chrono::steady_clock::now ();

    // check any interval and run them if needed
    for (auto& timeout : this->m_intervals | std::views::values) {
	if (timeout.next > now) {
	    continue;
	}

	timeout.next = now + timeout.duration;

	JS_Call (this->m_engine.getContext (), timeout.callback, JS_NULL, 0, nullptr);
    }

    std::vector<uint32_t> removeTimeouts;

    // check any timeout and run them if needed
    for (auto& [id, timeout] : this->m_timeouts) {
	if (timeout.next > now) {
	    continue;
	}

	JS_Call (this->m_engine.getContext (), timeout.callback, JS_NULL, 0, nullptr);

	JS_FreeValue (this->m_engine.getContext (), timeout.callback);

	removeTimeouts.push_back (id);
    }

    for (auto id : removeTimeouts) {
	this->m_timeouts.erase (id);
    }
}