#include "ScriptEngine.h"
#include "WallpaperEngine/Data/Model/Property.h"
#include "WallpaperEngine/Render/Objects/CImage.h"

#include "Adapters/ScriptableObjectAdapter.h"
#include "Modules/ColorModule.h"
#include "Modules/MathModule.h"
#include "Modules/ScriptModule.h"
#include "Modules/VectorModule.h"
#include "ScriptPropertiesObject.h"
#include "ScriptableObject.h"
#include "WallpaperEngine/Audio/AudioContext.h"
#include "WallpaperEngine/Audio/Drivers/Recorders/PlaybackRecorder.h"
#include "WallpaperEngine/Data/Utils/ScopeGuard.h"
#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/Render/CObject.h"
#include "WallpaperEngine/Render/Objects/CSound.h"
#include "WallpaperEngine/Render/Wallpapers/CScene.h"
#include "WallpaperEngine/Scripting/Builtins.generated.h"
#include "quickjs.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstring>
#include <ctime>
#include <future>
#include <optional>
#include <poll.h>
#include <ranges>
#include <signal.h>
#include <spawn.h>
#include <sstream>
#include <string_view>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

namespace WallpaperEngine::Render::Objects {
class CSound;
}
using namespace WallpaperEngine::Scripting;
using namespace WallpaperEngine::Data::Model;

extern char** environ;
extern float g_Time;
extern float g_TimeLast;

void scriptengine_dump (JSContext* ctx, JSValueConst obj) {
    JSPropertyEnum* props;
    uint32_t len;

    if (JS_GetOwnPropertyNames (ctx, &props, &len, obj, JS_GPN_STRING_MASK | JS_GPN_SYMBOL_MASK) < 0) {
	return;
    }

    for (uint32_t i = 0; i < len; ++i) {
	const char* name = JS_AtomToCString (ctx, props[i].atom);

	JSValue val = JS_GetProperty (ctx, obj, props[i].atom);

	const char* value_str = JS_ToCString (ctx, val);

	printf ("%s = %s\n", name, value_str ? value_str : "<non-string>");

	JS_FreeCString (ctx, value_str);
	JS_FreeValue (ctx, val);
	JS_FreeCString (ctx, name);
    }

    js_free (ctx, props);
}

JSModuleDef* scriptengine_module_loader (JSContext* ctx, const char* module, void* opaque) {
    const auto* scriptEngine = static_cast<ScriptEngine*> (opaque);

    const auto& modules = scriptEngine->getModules ();
    const auto it = modules.find (module);

    if (it == modules.end ()) {
	JS_ThrowReferenceError (ctx, "could not resolve module '%s'", module);
	return nullptr;
    }

    return it->second->getDefinition ();
}

JSValue ScriptEngine::dynamicToJs (DynamicValue& value) const {
    switch (value.getType ()) {
	case DynamicValue::Null:
	    return JS_NULL;
	case DynamicValue::String:
	    return JS_NewString (this->m_context, value.getString ().c_str ());
	case DynamicValue::Float:
	    return JS_NewFloat64 (this->m_context, value.getFloat ());
	case DynamicValue::Int:
	    return JS_NewInt32 (this->m_context, value.getInt ());
	case DynamicValue::Boolean:
	    return JS_NewBool (this->m_context, value.getBool ());
	case DynamicValue::Vec2:
	    return this->m_adapters.vec2->instantiate (value);
	case DynamicValue::Vec3:
	    return this->m_adapters.vec3->instantiate (value);
	case DynamicValue::Vec4:
	    return this->m_adapters.vec4->instantiate (value);
	default:
	    return JS_UNDEFINED;
    }
}

static void jsToDynamicValue (JSContext* ctx, JSValue val, DynamicValue& source) {
    if (JS_IsException (val)) {
	return;
    }

    // scalar types returned directly
    int tag = JS_VALUE_GET_TAG (val);

    if (tag == JS_TAG_UNDEFINED || tag == JS_TAG_UNINITIALIZED || tag == JS_TAG_NULL) {
	return;
    }

    if (tag == JS_TAG_INT) {
	source.update (JS_VALUE_GET_INT (val), DynamicValue::UpdateSource::Script);
	return;
    }

    if (tag == JS_TAG_BOOL) {
	source.update (static_cast<bool> (JS_VALUE_GET_BOOL (val)), DynamicValue::UpdateSource::Script);
    }

    if (JS_TAG_IS_FLOAT64 (tag)) {
	source.update (static_cast<float> (JS_VALUE_GET_FLOAT64 (val)), DynamicValue::UpdateSource::Script);
	return;
    }

    if (tag == JS_TAG_STRING) {
	const char* str = JS_ToCString (ctx, val);
	source.update (str == nullptr ? "" : str, DynamicValue::UpdateSource::Script);
	JS_FreeCString (ctx, str);
	return;
    }

    // look into the object and extract x/y/z/w properties
    if (tag == JS_TAG_OBJECT) {
	JSValue x = JS_GetPropertyStr (ctx, val, "x");
	JSValue y = JS_GetPropertyStr (ctx, val, "y");
	JSValue z = JS_GetPropertyStr (ctx, val, "z");
	JSValue w = JS_GetPropertyStr (ctx, val, "w");
	ScopeGuard guard ([=] {
	    JS_FreeValue (ctx, x);
	    JS_FreeValue (ctx, y);
	    JS_FreeValue (ctx, z);
	    JS_FreeValue (ctx, w);
	});

	if (!JS_IsNumber (x) || !JS_IsNumber (y)) {
	    sLog.exception ("Vector's x and y components must be numbers");
	}

	double xVal = 0.0f, yVal = 0.0f, zVal = 0.0f, wVal = 0.0f;

	JS_ToFloat64 (ctx, &xVal, x);
	JS_ToFloat64 (ctx, &yVal, y);

	if (!JS_IsNumber (z)) {
	    source.update (glm::vec2 (xVal, yVal), DynamicValue::UpdateSource::Script);
	    return;
	}

	JS_ToFloat64 (ctx, &zVal, z);

	if (!JS_IsNumber (w)) {
	    source.update (glm::vec3 (xVal, yVal, zVal), DynamicValue::UpdateSource::Script);
	    return;
	}

	JS_ToFloat64 (ctx, &wVal, w);

	source.update (glm::vec4 (xVal, yVal, zVal, wVal), DynamicValue::UpdateSource::Script);
    }
}

ScriptEngine::ScriptEngine (Wallpapers::CScene& scene, Media::MediaSource& mediaSource) :
    m_scene (scene), m_mediaSource (mediaSource) {
    this->m_unregisterMediaUpdateCallback
	= mediaSource.addMetadataListener ([this] (const Media::MediaSource::MediaInfo& info) {
	      this->notifyMediaUpdate (info);
	  });

    this->m_unregisterAlbumArtUpdateCallback
	= mediaSource.addAlbumArtListener ([this] (const Media::MediaSource::MediaInfo& info) {
	      // TODO: SEPARATE THESE INTO THEIR OWN UPDATES SO JS ONLY RECEIVES THE MEANINGFUL UPDATES
	      this->notifyMediaUpdate (info);
	  });

    this->m_runtime = JS_NewRuntime ();

    if (!this->m_runtime) {
	sLog.exception ("ScriptEngine: Failed to create JS runtime");
    }

    // debug leaks on termination
    JS_SetDumpFlags (this->m_runtime, JS_DUMP_LEAKS);

    this->m_context = JS_NewContext (this->m_runtime);

    if (!this->m_context) {
	JS_FreeRuntime (this->m_runtime);
	sLog.exception ("ScriptEngine: Failed to create JS context");
    }

    this->m_globalThis = JS_GetGlobalObject (this->m_context);

    this->m_adapters = {
	.vec4 = std::unique_ptr<Adapters::VectorAdapter<4>> (new Adapters::VectorAdapter<4> (*this)),
	.vec3 = std::unique_ptr<Adapters::VectorAdapter<3>> (new Adapters::VectorAdapter<3> (*this)),
	.vec2 = std::unique_ptr<Adapters::VectorAdapter<2>> (new Adapters::VectorAdapter<2> (*this)),
	.object
	= std::unique_ptr<Adapters::ScriptableObjectAdapter> (new Adapters::ScriptableObjectAdapter (*this, "ILayer")),
    };

    this->m_engineObject = std::make_unique<EngineObject> (*this, scene);
    this->m_inputObject = std::make_unique<InputObject> (*this, scene);
    this->m_sceneObject = std::make_unique<SceneObject> (*this, scene);
    this->m_consoleObject = std::make_unique<ConsoleObject> (*this, scene);
    this->m_scriptPropertiesObject = std::make_unique<ScriptPropertiesObject> (*this, scene);
    this->m_localStorageObject = std::make_unique<LocalStorageObject> (*this, scene);

    auto wemath = std::make_unique<Modules::MathModule> (*this);
    auto wecolor = std::make_unique<Modules::ColorModule> (*this);
    auto wevector = std::make_unique<Modules::VectorModule> (*this);

    this->m_modules.emplace (wemath->getName (), std::move (wemath));
    this->m_modules.emplace (wecolor->getName (), std::move (wecolor));
    this->m_modules.emplace (wevector->getName (), std::move (wevector));

    JS_SetModuleLoaderFunc (this->m_runtime, nullptr, scriptengine_module_loader, this);
    // setup scene objects and other things
    this->installBuiltins ();
    // add engine to the global
    JS_DefinePropertyValueStr (
	this->m_context, this->m_globalThis, "engine", this->m_engineObject->getInstance (), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_context, this->m_globalThis, "input", this->m_inputObject->getInstance (), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_context, this->m_globalThis, "thisScene", this->m_sceneObject->getInstance (), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_context, this->m_globalThis, "console", this->m_consoleObject->getInstance (), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_context, this->m_globalThis, "shared", JS_NewObject (this->m_context), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_context, this->m_globalThis, "localStorage", this->m_localStorageObject->getInstance (),
	JS_PROP_ENUMERABLE
    );
}

ScriptEngine::~ScriptEngine () {
    this->m_unregisterMediaUpdateCallback ();
    // The ctor registers TWO MediaSource listeners; forgetting this one left a dangling
    // album-art listener behind every destroyed scene. Harmless in the process-per-scene
    // era (process death flushed listeners), fatal under in-process hot swap: the next
    // MPRIS artUrl change called straight into the freed engine
    // (fireAlbumArtListeners -> notifyMediaUpdate -> ObjectAdapter::instantiate, SIGSEGV).
    this->m_unregisterAlbumArtUpdateCallback ();

    for (const auto& module : this->m_scriptModules | std::views::values) {
	JS_FreeValue (this->m_context, module.module);
    }

    JS_FreeValue (this->m_context, this->m_globalThis);

    this->m_adapters.vec4.reset ();
    this->m_adapters.vec3.reset ();
    this->m_adapters.vec2.reset ();
    this->m_adapters.object.reset ();

    this->m_consoleObject.reset ();
    this->m_engineObject.reset ();
    this->m_inputObject.reset ();
    this->m_sceneObject.reset ();
    this->m_scriptPropertiesObject.reset ();
    this->m_localStorageObject.reset ();
    this->m_modules.clear ();
    this->m_scriptModules.clear ();

    if (this->m_context) {
	JS_FreeContext (this->m_context);
    }
    if (this->m_runtime) {
	JS_FreeRuntime (this->m_runtime);
    }
}

/// Helper to check for and log JS exceptions
static void logJSException (JSContext* ctx, const char* context) {
    JSValue exc = JS_GetException (ctx);
    if (JS_IsUninitialized (exc)) {
	sLog.error (
	    "ScriptEngine [", context,
	    "]: exception with NO value - host callback returned bare JS_EXCEPTION without JS_Throw*"
	);
	JS_FreeValue (ctx, exc);
	return;
    }
    if (!JS_IsNull (exc) && !JS_IsUndefined (exc)) {
	const char* str = JS_ToCString (ctx, exc);
	if (str) {
	    sLog.error ("ScriptEngine [", context, "]: ", str);
	    JS_FreeCString (ctx, str);
	}
	if (JS_IsObject (exc)) {
	    JSValue stack = JS_GetPropertyStr (ctx, exc, "stack");
	    if (!JS_IsUndefined (stack) && !JS_IsException (stack)) {
		const char* stackStr = JS_ToCString (ctx, stack);
		if (stackStr) {
		    sLog.error ("ScriptEngine [", context, "] stack: ", stackStr);
		    JS_FreeCString (ctx, stackStr);
		}
	    }
	    JS_FreeValue (ctx, stack);
	}
    }
    JS_FreeValue (ctx, exc);
}

void ScriptEngine::installBuiltins () {
    if (this->m_builtinsInstalled || !this->m_context) {
	return;
    }

    JSValue result = JS_Eval (
	this->m_context, SCENE_SCRIPT_BUILTINS, strlen (SCENE_SCRIPT_BUILTINS), "<scene-script-builtins>",
	JS_EVAL_TYPE_GLOBAL
    );
    if (JS_IsException (result)) {
	logJSException (this->m_context, "installBuiltins");
    }
    JS_FreeValue (this->m_context, result);
    this->m_builtinsInstalled = true;
}

// ---------------------------------------------------------------------------
// Layer-script API (Phase 2)
// ---------------------------------------------------------------------------

void ScriptEngine::ensureLayerRegistry () {
    if (this->m_layerRegistryReady || !this->m_context) {
	return;
    }
    JSContext* ctx = this->m_context;
    JSValue globalObj = JS_GetGlobalObject (ctx);
    JS_SetPropertyStr (ctx, globalObj, "__textLayers", JS_NewObject (ctx));
    JS_FreeValue (ctx, globalObj);
    this->m_layerRegistryReady = true;
}

ScriptLayerHandle ScriptEngine::createLayerScript (
    const std::string& scriptSource, std::map<std::string, UserSettingUniquePtr>& initialScriptProps,
    const std::string& initialText
) {
    if (!this->m_context) {
	sLog.error ("ScriptEngine: No JS context available");
	return kInvalidLayerHandle;
    }

    this->ensureLayerRegistry ();

    JSContext* ctx = this->m_context;
    JSValue globalObj = JS_GetGlobalObject (ctx);

    // Seed initial scriptProperties and text as temporary globals the IIFE reads.
    JSValue seedProps = JS_NewObject (ctx);

    for (auto& [name, dynVal] : initialScriptProps) {
	JS_SetPropertyStr (ctx, seedProps, name.c_str (), this->dynamicToJs (*dynVal->value));
    }

    JS_SetPropertyStr (ctx, globalObj, "__layerSeedProps", seedProps);
    JS_SetPropertyStr (ctx, globalObj, "__layerSeedText", JS_NewString (ctx, initialText.c_str ()));

    const ScriptLayerHandle id = this->m_nextLayerId++;

    // Same stripping logic as evaluate(): WE scripts come as ES6 modules but
    // QuickJS is easier to drive as plain script evaluation.
    std::string body = scriptSource;
    size_t pos;
    while ((pos = body.find ("'use strict';")) != std::string::npos) {
	body.erase (pos, 13);
    }
    while ((pos = body.find ("\"use strict\";")) != std::string::npos) {
	body.erase (pos, 13);
    }
    while ((pos = body.find ("export ")) != std::string::npos) {
	body.erase (pos, 7);
    }

    // The IIFE gives every layer its own closure for top-level vars and
    // functions, so two layers that both define `function update()` or a
    // top-level `var scriptProperties` don't clobber each other. Lifecycle
    // hooks are captured into globalThis.__textLayers[id] so tick/destroy can
    // reach them later. `typeof init === 'function'` is safe even when
    // `init` was never declared - bare-identifier `typeof` never throws.
    std::ostringstream wrapper;
    wrapper
	<< "(function() {\n"
	<< "  var __id = " << id << ";\n"
	<< "  var __props = Object.assign({}, globalThis.__layerSeedProps || {});\n"
	<< "  var thisLayer = { text: String(globalThis.__layerSeedText || '') };\n"
	<< "  var thisScene = {\n"
	<< "    get time()        { var c = globalThis.__sceneCtx; return c ? c.time : 0; },\n"
	<< "    get currentTime() { var c = globalThis.__sceneCtx; return c ? c.time : 0; },\n"
	<< "    get dt()          { var c = globalThis.__sceneCtx; return c ? c.dt   : 0; },\n"
	<< "    get fps()         { var c = globalThis.__sceneCtx; return c ? c.fps  : 60; },\n"
	<< "  };\n"
	// Minimal WE `engine` shim. Real Wallpaper Engine exposes a broad API
	// (media events, audio buffer, user input); we provide just enough for
	// the common built-in text scripts to run without ReferenceError.
	// `frametime` is the per-frame delta in seconds (what InsertFPS reads).
	<< "  var engine = {\n"
	<< "    get frametime() { var c = globalThis.__sceneCtx; return c ? c.dt : 0; },\n"
	<< "    get time()      { var c = globalThis.__sceneCtx; return c ? c.time : 0; },\n"
	<< "  };\n"
	<< "  function createScriptProperties() {\n"
	<< "    var builder = {\n"
	<< "      addSlider:   function(o){ if (!(o.name in __props)) __props[o.name] = o.value; return builder; },\n"
	<< "      addCheckbox: function(o){ if (!(o.name in __props)) __props[o.name] = o.value; return builder; },\n"
	<< "      addCombo:    function(o){ if (!(o.name in __props)) __props[o.name] = o.value; return builder; },\n"
	<< "      addColor:    function(o){ if (!(o.name in __props)) __props[o.name] = o.value; return builder; },\n"
	<< "      addText:     function(o){ if (!(o.name in __props)) __props[o.name] = o.value; return builder; },\n"
	<< "      finish:      function(){ return __props; }\n"
	<< "    };\n"
	<< "    return builder;\n"
	<< "  }\n"
	<< body
	<< "\n"
	// `_tick` wraps the user's `update()` so both WE text conventions work:
	//   A) `export function update() { thisLayer.text = ...; }` (mutates in place)
	//   B) `export function update(value) { ...; return value; }` (returns new text)
	// We pass the current text in, and if the return value is a string we
	// adopt it as the new `thisLayer.text`. Non-string / undefined return
	// leaves `thisLayer.text` as whatever the function assigned itself.
	<< "  globalThis.__textLayers[__id] = {\n"
	<< "    thisLayer: thisLayer,\n"
	<< "    thisScene: thisScene,\n"
	<< "    _init:    (typeof init    === 'function') ? init    : null,\n"
	<< "    _destroy: (typeof destroy === 'function') ? destroy : null,\n"
	<< "    _tick:    (typeof update  === 'function')\n"
	<< "              ? function() {\n"
	<< "                  var r = update(thisLayer.text);\n"
	<< "                  if (typeof r === 'string') thisLayer.text = r;\n"
	<< "                }\n"
	<< "              : null,\n"
	<< "    _scriptProperties: (typeof scriptProperties !== 'undefined') ? scriptProperties : __props\n"
	<< "  };\n"
	<< "})();\n";

    const std::string evalScript = wrapper.str ();
    JSValue result = JS_Eval (ctx, evalScript.c_str (), evalScript.size (), "<layer-script>", JS_EVAL_TYPE_GLOBAL);

    // Unset seeds so they don't leak into the next createLayerScript call.
    JS_SetPropertyStr (ctx, globalObj, "__layerSeedProps", JS_UNDEFINED);
    JS_SetPropertyStr (ctx, globalObj, "__layerSeedText", JS_UNDEFINED);
    JS_FreeValue (ctx, globalObj);

    if (JS_IsException (result)) {
	logJSException (ctx, "createLayerScript");
	JS_FreeValue (ctx, result);
	return kInvalidLayerHandle;
    }
    JS_FreeValue (ctx, result);

    this->m_layerInitialized[id] = false;
    return id;
}

void ScriptEngine::tickLayer (ScriptLayerHandle handle, double time, double deltaTime, double fps) {
    if (!this->m_context || handle == kInvalidLayerHandle) {
	return;
    }
    JSContext* ctx = this->m_context;
    JSValue globalObj = JS_GetGlobalObject (ctx);

    JSValue sceneCtx = JS_NewObject (ctx);
    JS_SetPropertyStr (ctx, sceneCtx, "time", JS_NewFloat64 (ctx, time));
    JS_SetPropertyStr (ctx, sceneCtx, "dt", JS_NewFloat64 (ctx, deltaTime));
    JS_SetPropertyStr (ctx, sceneCtx, "fps", JS_NewFloat64 (ctx, fps));
    JS_SetPropertyStr (ctx, globalObj, "__sceneCtx", sceneCtx);

    JSValue layers = JS_GetPropertyStr (ctx, globalObj, "__textLayers");
    JSValue layerObj = JS_GetPropertyUint32 (ctx, layers, static_cast<uint32_t> (handle));
    JS_FreeValue (ctx, layers);

    if (JS_IsUndefined (layerObj) || JS_IsNull (layerObj)) {
	JS_FreeValue (ctx, layerObj);
	JS_FreeValue (ctx, globalObj);
	return;
    }

    auto callHook = [&] (const char* prop, const char* tag) {
	JSValue fn = JS_GetPropertyStr (ctx, layerObj, prop);
	if (JS_IsFunction (ctx, fn)) {
	    JSValue ret = JS_Call (ctx, fn, layerObj, 0, nullptr);
	    if (JS_IsException (ret)) {
		logJSException (ctx, tag);
	    }
	    JS_FreeValue (ctx, ret);
	}
	JS_FreeValue (ctx, fn);
    };

    auto it = this->m_layerInitialized.find (handle);
    if (it != this->m_layerInitialized.end () && !it->second) {
	callHook ("_init", "layer.init");
	it->second = true;
    }
    callHook ("_tick", "layer.update");

    JS_FreeValue (ctx, layerObj);
    JS_FreeValue (ctx, globalObj);
}

std::string ScriptEngine::layerText (ScriptLayerHandle handle) {
    if (!this->m_context || handle == kInvalidLayerHandle) {
	return {};
    }
    JSContext* ctx = this->m_context;
    JSValue globalObj = JS_GetGlobalObject (ctx);
    JSValue layers = JS_GetPropertyStr (ctx, globalObj, "__textLayers");
    JSValue layerObj = JS_GetPropertyUint32 (ctx, layers, static_cast<uint32_t> (handle));

    std::string result;
    if (!JS_IsUndefined (layerObj) && !JS_IsNull (layerObj)) {
	JSValue thisLayer = JS_GetPropertyStr (ctx, layerObj, "thisLayer");
	JSValue textVal = JS_GetPropertyStr (ctx, thisLayer, "text");
	if (!JS_IsUndefined (textVal) && !JS_IsNull (textVal)) {
	    const char* cstr = JS_ToCString (ctx, textVal);
	    if (cstr) {
		result.assign (cstr);
		JS_FreeCString (ctx, cstr);
	    }
	}
	JS_FreeValue (ctx, textVal);
	JS_FreeValue (ctx, thisLayer);
    }

    JS_FreeValue (ctx, layerObj);
    JS_FreeValue (ctx, layers);
    JS_FreeValue (ctx, globalObj);
    return result;
}

void ScriptEngine::destroyLayer (ScriptLayerHandle handle) {
    if (!this->m_context || handle == kInvalidLayerHandle) {
	return;
    }
    JSContext* ctx = this->m_context;
    JSValue globalObj = JS_GetGlobalObject (ctx);
    JSValue layers = JS_GetPropertyStr (ctx, globalObj, "__textLayers");
    JSValue layerObj = JS_GetPropertyUint32 (ctx, layers, static_cast<uint32_t> (handle));

    if (!JS_IsUndefined (layerObj) && !JS_IsNull (layerObj)) {
	JSValue fn = JS_GetPropertyStr (ctx, layerObj, "_destroy");
	if (JS_IsFunction (ctx, fn)) {
	    JSValue ret = JS_Call (ctx, fn, layerObj, 0, nullptr);
	    if (JS_IsException (ret)) {
		logJSException (ctx, "layer.destroy");
	    }
	    JS_FreeValue (ctx, ret);
	}
	JS_FreeValue (ctx, fn);
    }
    JS_FreeValue (ctx, layerObj);
    JS_FreeValue (ctx, layers);
    JS_FreeValue (ctx, globalObj);

    // Remove the entry from globalThis.__textLayers so GC can reclaim its closures.
    const std::string delScript = "delete globalThis.__textLayers[" + std::to_string (handle) + "];";
    JSValue delResult = JS_Eval (ctx, delScript.c_str (), delScript.size (), "<layer-destroy>", JS_EVAL_TYPE_GLOBAL);
    if (JS_IsException (delResult)) {
	logJSException (ctx, "layer.destroy.delete");
    }
    JS_FreeValue (ctx, delResult);

    this->m_layerInitialized.erase (handle);
}

JSValue ScriptEngine::call (JSValue module, int argc, JSValue argv[], const char* name) {
    // check if there's an update method and run it
    JSValue function = JS_GetPropertyStr (this->m_context, module, name);
    ScopeGuard guard ([&] () { JS_FreeValue (this->m_context, function); });

    if (!JS_IsFunction (this->m_context, function)) {
	return JS_UNDEFINED;
    }

    return JS_Call (this->m_context, function, module, argc, argv);
}

void ScriptEngine::queueScript (const std::string& key, DynamicValue& currentValue, ScriptableObject& object) {
    static const bool s_scriptDbg = getenv ("LWE_SCRIPTDBG") != nullptr;
    const auto source = currentValue.getScriptSource ();

    if (!source.has_value ()) {
	if (s_scriptDbg) {
	    sLog.out ("LWE-SCRIPTDBG skip(no-source) ", key);
	}
	return;
    }

    if (s_scriptDbg) {
	sLog.out ("LWE-SCRIPTDBG register ", key);
    }

    auto it = this->m_scriptModules.find (key);

    if (it != this->m_scriptModules.end ()) {
	return;
    }

    JSValue compiled = JS_Eval (
	this->m_context, source->c_str (), source->size (), key.c_str (),
	JS_EVAL_TYPE_MODULE | JS_EVAL_FLAG_COMPILE_ONLY
    );

    if (JS_IsException (compiled)) {
	sLog.error ("Script module '", key, "' failed to compile:");
	logJSException (this->m_context, key.c_str ());
	return;
    }

    auto* moduleDef = static_cast<JSModuleDef*> (JS_VALUE_GET_PTR (compiled));

    auto inserted = this->m_scriptModules.emplace (
	key,
	LoadedModule {
	    .value = currentValue,
	    .module = JS_UNDEFINED,
	    // degrees<->radians bridge for object angle scripts (see LoadedModule)
	    .degreesMirror = key.rfind ("angles_", 0) == 0 && currentValue.getType () == DynamicValue::Vec3
		? std::make_shared<DynamicValue> (glm::degrees (currentValue.getVec3 ()))
		: nullptr,
	    .object = &object,
	}
    );

    if (!inserted.second) {
	JS_FreeValue (this->m_context, compiled);
	return;
    }

    JS_SetPropertyStr (this->m_context, this->m_globalThis, "thisLayer", this->m_adapters.object->instantiate (object));

    // must point at this module BEFORE the body runs: scriptProperties binds through it
    this->m_runningModule = &inserted.first->second;

    JSValue evalResult = JS_EvalFunction (this->m_context, compiled); // consumes `compiled`

    for (int pending = 0; pending < 64; pending++) {
	JSContext* jobCtx = nullptr;
	const int state = JS_ExecutePendingJob (this->m_runtime, &jobCtx);
	if (state <= 0) {
	    if (state < 0) {
		logJSException (jobCtx != nullptr ? jobCtx : this->m_context, "module job");
	    }
	    break;
	}
    }

    if (JS_IsException (evalResult)) {
	sLog.error ("Script module '", key, "' failed to evaluate:");
	logJSException (this->m_context, key.c_str ());
	JS_FreeValue (this->m_context, evalResult);
	return;
    }

    if (JS_PromiseState (this->m_context, evalResult) == JS_PROMISE_REJECTED) {
	JSValue reason = JS_PromiseResult (this->m_context, evalResult);
	const char* str = JS_ToCString (this->m_context, reason);
	sLog.error ("Script module '", key, "' evaluation rejected: ", str != nullptr ? str : "<unknown>");
	if (str != nullptr) {
	    JS_FreeCString (this->m_context, str);
	}
	JS_FreeValue (this->m_context, reason);
	JS_FreeValue (this->m_context, evalResult);
	return;
    }

    JS_FreeValue (this->m_context, evalResult);

    inserted.first->second.module = JS_GetModuleNamespace (this->m_context, moduleDef);

    {
	static constexpr const char* CURSOR_HOOKS[]
	    = { "cursorEnter", "cursorLeave", "cursorMove", "cursorDown", "cursorUp", "cursorClick" };
	for (const char* hook : CURSOR_HOOKS) {
	    const JSValue fn = JS_GetPropertyStr (this->m_context, inserted.first->second.module, hook);
	    const bool isFunction = JS_IsFunction (this->m_context, fn);
	    JS_FreeValue (this->m_context, fn);
	    if (isFunction) {
		inserted.first->second.cursorEvents = true;
		if (getenv ("LWE_CURSORDBG") != nullptr) {
		    sLog.out ("LWE-CURSORDBG module ", key, " exports ", hook);
		}
		break;
	    }
	}
    }

    auto& mod = inserted.first->second;
    const bool bridgeAngles = mod.degreesMirror != nullptr && currentValue.getType () == DynamicValue::Vec3;
    DynamicValue& scriptSpace = bridgeAngles ? *mod.degreesMirror : currentValue;
    const auto storeBack = [&] () {
	if (!bridgeAngles) {
	    return;
	}
	if (scriptSpace.getType () == DynamicValue::Vec3) {
	    currentValue.update (glm::radians (scriptSpace.getVec3 ()), DynamicValue::UpdateSource::Script);
	} else {
	    currentValue.update (scriptSpace, DynamicValue::UpdateSource::Script);
	}
    };

    JSValue initArgs[] = { this->dynamicToJs (scriptSpace) };
    JSValue initResult = this->call (mod.module, 1, initArgs, "init");

    if (JS_IsException (initResult)) {
	sLog.error ("Script module '", key, "' init() threw:");
	logJSException (this->m_context, key.c_str ());
    } else {
	jsToDynamicValue (this->m_context, initResult, scriptSpace);
	storeBack ();
    }

    JS_FreeValue (this->m_context, initResult);
    JS_FreeValue (this->m_context, initArgs[0]);

    // check if there's an update method and run it
    JSValue args[] = { this->dynamicToJs (scriptSpace) };
    JSValue result = this->call (mod.module, 1, args, "update");

    ScopeGuard guard2 ([this, args, result] () {
	JS_FreeValue (this->m_context, result);
	JS_FreeValue (this->m_context, args[0]);
    });

    if (JS_IsException (result)) {
	return;
    }

    jsToDynamicValue (this->m_context, result, scriptSpace);
    storeBack ();
}

void ScriptEngine::unregisterScriptable (const ScriptableObject* object) {
    if (object == nullptr) {
	return;
    }

    for (auto it = this->m_scriptModules.begin (); it != this->m_scriptModules.end ();) {
	if (it->second.object != object) {
	    ++it;
	    continue;
	}

	// clear the pointer so a later getRunningModule() cannot hand out the entry
	// that is about to be erased
	if (this->m_runningModule == &it->second) {
	    this->m_runningModule = nullptr;
	}

	JS_FreeValue (this->m_context, it->second.module);
	it = this->m_scriptModules.erase (it);
    }
}

void ScriptEngine::tick () {
    // run intervals
    this->m_engineObject->tick ();

    this->dispatchCursorEvents ();

    // run any pending notifications

    // run all update methods
    for (auto& [modKey, module] : this->m_scriptModules) {
	static const bool s_traceScripts = getenv ("LWE_LIGHTDUMP") != nullptr;
	static int s_traceTick = 0;
	if (s_traceScripts && modKey.find ("angles_112") != std::string::npos && (s_traceTick++ % 90) == 0) {
	    const auto v = module.value.getVec3 ();
	    sLog.out ("LWE-SCRIPTTRACE ", modKey, " angles=(", v.x, ",", v.y, ",", v.z, ")");
	}
	this->m_runningModule = &module;

	// Object angle scripts speak DEGREES (WE convention: 0..360 wraps); the engine
	// stores RADIANS (authored 1.5708 = pi/2). Refresh the degrees mirror, hand THAT
	// to update() (the linked vector arg writes through to the mirror, never raw
	// degrees into engine radians), and convert the outcome back below.
	const bool bridgeAngles = module.degreesMirror != nullptr && module.value.getType () == DynamicValue::Vec3;
	if (bridgeAngles) {
	    module.degreesMirror->update (glm::degrees (module.value.getVec3 ()), DynamicValue::UpdateSource::Script);
	}

	JSValue args[] = { this->dynamicToJs (bridgeAngles ? *module.degreesMirror : module.value) };
	JSValue result = this->call (module.module, 1, args, "update");
	ScopeGuard guard ([result, args, this] () {
	    JS_FreeValue (this->m_context, result);
	    JS_FreeValue (this->m_context, args[0]);
	});

	if (JS_IsException (result)) {
	    static std::map<std::string, int> s_scriptDumpCounts;
	    if (s_scriptDumpCounts[modKey] < 3) {
		s_scriptDumpCounts[modKey]++;
		sLog.error ("Script update() threw (module '", modKey, "' suppressed):");
		logJSException (this->m_context, "module.update");
	    }
	    continue;
	}

	try {
	    if (bridgeAngles) {
		jsToDynamicValue (this->m_context, result, *module.degreesMirror);
		if (module.degreesMirror->getType () == DynamicValue::Vec3) {
		    module.value.update (
			glm::radians (module.degreesMirror->getVec3 ()), DynamicValue::UpdateSource::Script
		    );
		} else {
		    module.value.update (*module.degreesMirror, DynamicValue::UpdateSource::Script);
		}
	    } else {
		jsToDynamicValue (this->m_context, result, module.value);
	    }
	} catch (const std::exception&) { }
    }
}

void ScriptEngine::notifyMediaUpdate (const Media::MediaSource::MediaInfo& media) {
    JSContext* ctx = this->m_context;

    DynamicValue primaryColorValue (glm::vec3 (0.12f, 0.12f, 0.12f));
    DynamicValue secondaryColorValue (glm::vec3 (0.0f, 0.0f, 0.0f));
    DynamicValue tertiaryColorValue (glm::vec3 (0.25f, 0.25f, 0.25f));
    DynamicValue highContrastColorValue (glm::vec3 (1.0f, 1.0f, 1.0f));

    // TODO: PROCESS THESE COLORS INSTEAD OF HARDCODING THEM
    JSValue primaryColor = this->m_adapters.vec3->instantiate (primaryColorValue, true);
    JSValue secondaryColor = this->m_adapters.vec3->instantiate (secondaryColorValue, true);
    JSValue tertiaryColor = this->m_adapters.vec3->instantiate (tertiaryColorValue, true);
    JSValue highContrastColor = this->m_adapters.vec3->instantiate (highContrastColorValue, true);

    JSValue propertiesEvent = JS_NewObject (ctx);

    // set properties
    JS_SetPropertyStr (ctx, propertiesEvent, "title", JS_NewString (ctx, media.title.c_str ()));
    JS_SetPropertyStr (ctx, propertiesEvent, "artist", JS_NewString (ctx, media.artist.c_str ()));
    JS_SetPropertyStr (ctx, propertiesEvent, "albumTitle", JS_NewString (ctx, media.album.c_str ()));

    JSValue playbackEvent = JS_NewObject (ctx);

    JS_SetPropertyStr (ctx, playbackEvent, "state", JS_NewInt32 (ctx, media.playbackState));

    JSValue mediaTimelineEvent = JS_NewObject (ctx);

    JS_SetPropertyStr (ctx, mediaTimelineEvent, "position", JS_NewFloat64 (ctx, media.position));
    JS_SetPropertyStr (ctx, mediaTimelineEvent, "duration", JS_NewFloat64 (ctx, media.duration));

    JSValue mediaThumbnailEvent = JS_NewObject (ctx);

    JS_SetPropertyStr (ctx, mediaThumbnailEvent, "hasThumbnail", JS_NewBool (ctx, media.url.has_value ()));
    JS_SetPropertyStr (ctx, mediaThumbnailEvent, "primaryColor", primaryColor);
    JS_SetPropertyStr (ctx, mediaThumbnailEvent, "secondaryColor", secondaryColor);
    JS_SetPropertyStr (ctx, mediaThumbnailEvent, "tertiaryColor", tertiaryColor);
    JS_SetPropertyStr (ctx, mediaThumbnailEvent, "highContrastColor", highContrastColor);

    JSValue propertiesArgs[] = { propertiesEvent };
    JSValue playbackArgs[] = { playbackEvent };
    JSValue mediaTimelineArgs[] = { mediaTimelineEvent };
    JSValue mediaThumbnailArgs[] = { mediaThumbnailEvent };

    for (auto& module : this->m_scriptModules | std::views::values) {
	// call all methods
	JSValue result1 = this->call (module.module, 1, propertiesArgs, "mediaPropertiesChanged");
	JSValue result2 = this->call (module.module, 1, playbackArgs, "mediaPlaybackChanged");
	JSValue result3 = this->call (module.module, 1, mediaTimelineArgs, "mediaTimelineChanged");
	JSValue result4 = this->call (module.module, 1, mediaThumbnailArgs, "mediaThumbnailChanged");

	JS_FreeValue (ctx, result1);
	JS_FreeValue (ctx, result2);
	JS_FreeValue (ctx, result3);
	JS_FreeValue (ctx, result4);
    }

    // free all created objects as we don't keep a ref to them anymore
    JS_FreeValue (ctx, propertiesEvent);
    JS_FreeValue (ctx, playbackEvent);
    JS_FreeValue (ctx, mediaTimelineEvent);
    JS_FreeValue (ctx, mediaThumbnailEvent);
}
JSValue ScriptEngine::makeCursorEvent (const glm::vec3& worldPosition, const glm::vec3& localPosition) {
    DynamicValue world (worldPosition);
    DynamicValue local (localPosition);
    JSValue event = JS_NewObject (this->m_context);
    JS_SetPropertyStr (this->m_context, event, "worldPosition", this->m_adapters.vec3->instantiate (world, true));
    JS_SetPropertyStr (this->m_context, event, "localPosition", this->m_adapters.vec3->instantiate (local, true));
    return event;
}

void ScriptEngine::dispatchCursorEvents () {
    bool any = false;
    for (const auto& [key, module] : this->m_scriptModules) {
	if (module.cursorEvents) {
	    any = true;
	    break;
	}
    }
    if (!any) {
	return;
    }

    const auto* normalized = this->m_scene.getMousePositionNormalized ();
    if (normalized == nullptr) {
	return;
    }

    const auto& input = this->m_scene.getContext ().getInputContext ().getMouseInput ();
    const bool leftDown = input.leftClick () == Input::MouseClickStatus::Clicked;

    const auto sceneW = static_cast<float> (this->m_scene.getWidth ());
    const auto sceneH = static_cast<float> (this->m_scene.getHeight ());
    const glm::vec3 worldPosition
	= { normalized->x * sceneW - sceneW / 2.0f, normalized->y * sceneH - sceneH / 2.0f, 0.0f };

    const glm::vec3 cursorDelta
	= worldPosition - this->m_lastCursorWorldPosition.value_or (worldPosition + glm::vec3 (1.0f));
    const bool moved = !this->m_lastCursorWorldPosition.has_value () || glm::dot (cursorDelta, cursorDelta) > 1e-8f;

    static const bool s_cursorDbg = getenv ("LWE_CURSORDBG") != nullptr;
    static int s_cursorDbgTick = 0;
    const bool dbgTick = s_cursorDbg && (s_cursorDbgTick++ % 60) == 0;

    for (auto& [key, module] : this->m_scriptModules) {
	if (!module.cursorEvents) {
	    continue;
	}

	const auto* image = dynamic_cast<const Render::Objects::CImage*> (module.object);
	const auto localPosition = image != nullptr ? image->cursorLocalPosition (worldPosition) : std::nullopt;
	const bool inside = localPosition.has_value ();

	if (dbgTick) {
	    sLog.out (
		"LWE-CURSORDBG ", key, " norm=", normalized->x, ",", normalized->y, " world=", worldPosition.x, ",",
		worldPosition.y, " leftDown=", leftDown, " image=", image != nullptr, " inside=", inside
	    );
	}

	const auto callCursorHook = [&] (const char* hook) {
	    if (!inside && std::string_view (hook) != "cursorLeave") {
		return;
	    }
	    const JSValue fn = JS_GetPropertyStr (this->m_context, module.module, hook);
	    if (!JS_IsFunction (this->m_context, fn)) {
		JS_FreeValue (this->m_context, fn);
		return;
	    }
	    if (s_cursorDbg) {
		sLog.out ("LWE-CURSORDBG FIRE ", hook, " on ", key);
	    }
	    this->m_runningModule = &module;
	    JSValue event = this->makeCursorEvent (worldPosition, localPosition.value_or (glm::vec3 (0.0f)));
	    JSValue args[] = { event };
	    JSValue result = JS_Call (this->m_context, fn, JS_UNDEFINED, 1, args);
	    if (JS_IsException (result)) {
		sLog.error ("ScriptEngine [", key, "] ", hook, ": exception");
		JSValue exception = JS_GetException (this->m_context);
		const char* text = JS_ToCString (this->m_context, exception);
		if (text != nullptr) {
		    sLog.error ("  ", text);
		    JS_FreeCString (this->m_context, text);
		}
		JS_FreeValue (this->m_context, exception);
	    }
	    JS_FreeValue (this->m_context, result);
	    JS_FreeValue (this->m_context, event);
	    JS_FreeValue (this->m_context, fn);
	};

	if (s_cursorDbg && inside != module.cursorInside) {
	    sLog.out (
		"LWE-CURSORDBG EDGE ", key, inside ? " ENTER" : " LEAVE", " at ", worldPosition.x, ",", worldPosition.y
	    );
	}
	if (inside && !module.cursorInside) {
	    callCursorHook ("cursorEnter");
	}
	if (!inside && module.cursorInside) {
	    callCursorHook ("cursorLeave");
	}
	if (inside && moved) {
	    callCursorHook ("cursorMove");
	}
	if (inside && leftDown && !this->m_cursorLeftDown) {
	    module.cursorPressedInside = true;
	    callCursorHook ("cursorDown");
	}
	if (!leftDown && this->m_cursorLeftDown) {
	    if (inside) {
		callCursorHook ("cursorUp");
		if (module.cursorPressedInside) {
		    callCursorHook ("cursorClick");
		}
	    }
	    module.cursorPressedInside = false;
	}
	module.cursorInside = inside;
    }

    this->m_cursorLeftDown = leftDown;
    this->m_lastCursorWorldPosition = worldPosition;
}

JSValue ScriptEngine::buildUserPropertiesObject (const std::map<std::string, PropertySharedPtr>& changed) const {
    JSValue obj = JS_NewObject (this->m_context);

    for (const auto& [name, property] : changed) {
	JS_SetPropertyStr (this->m_context, obj, name.c_str (), this->dynamicToJs (*property));
    }

    return obj;
}

void ScriptEngine::notifyUserPropertiesChanged (const std::map<std::string, PropertySharedPtr>& changed) {
    if (changed.empty ()) {
	return;
    }

    for (auto& [key, loaded] : this->m_scriptModules) {
	if (loaded.object == nullptr) {
	    continue;
	}

	this->m_runningModule = &loaded;
	JS_SetPropertyStr (
	    this->m_context, this->m_globalThis, "thisLayer",
	    this->m_adapters.object->instantiate (*const_cast<ScriptableObject*> (loaded.object))
	);

	JSValue propsArgs[] = { this->buildUserPropertiesObject (changed) };
	JSValue propsResult = this->call (loaded.module, 1, propsArgs, "applyUserProperties");

	if (JS_IsException (propsResult)) {
	    logJSException (this->m_context, key.c_str ());
	}
	JS_FreeValue (this->m_context, propsResult);
	JS_FreeValue (this->m_context, propsArgs[0]);
    }

    this->m_runningModule = nullptr;
}
