#include "ScriptableObjectAdapter.h"

#include <utility>

#include "WallpaperEngine/Data/Utils/ScopeGuard.h"
#include "WallpaperEngine/Render/Objects/CImage.h"
#include "WallpaperEngine/Render/Objects/CRenderable.h"
#include "WallpaperEngine/Scripting/ScriptEngine.h"
#include "WallpaperEngine/Scripting/ScriptableObject.h"

using namespace WallpaperEngine::Data::Model;
using namespace WallpaperEngine::Data::Utils;
using namespace WallpaperEngine::Scripting::Adapters;

#define SCRIPTABLE_OPAQUE_MAGIC 0xdeadbeef

struct OpaqueScriptableObjectAdapter {
    unsigned int magic;
    ScriptableObjectAdapter& adapter;
    WallpaperEngine::Scripting::ScriptableObject& object;
};

static JSValue textureanim_op (
    JSContext* ctx, JSValueConst this_val, const int argc, JSValueConst* argv, const int magic, JSValue* func_data
) {
    JSClassID classId = 0;
    auto* container = static_cast<OpaqueScriptableObjectAdapter*> (JS_GetAnyOpaque (func_data[0], &classId));

    if (!container || container->magic != SCRIPTABLE_OPAQUE_MAGIC) {
	return JS_ThrowTypeError (ctx, "invalid layer object");
    }

    auto* renderable = dynamic_cast<WallpaperEngine::Render::Objects::CRenderable*> (&container->object);

    if (renderable == nullptr) {
	return JS_UNDEFINED;
    }

    switch (magic) {
	case 0:
	    renderable->pauseTextureAnimation ();
	    return JS_UNDEFINED;
	case 1:
	    renderable->playTextureAnimation ();
	    return JS_UNDEFINED;
	case 2:
	    {
		int64_t frame = 0;
		if (argc >= 1) {
		    JS_ToInt64 (ctx, &frame, argv[0]);
		}
		renderable->setTextureAnimationFrame (frame < 0 ? 0 : static_cast<size_t> (frame));
		return JS_UNDEFINED;
	    }
	case 3:
	    return JS_NewInt64 (ctx, static_cast<int64_t> (renderable->getTextureAnimationFrame ()));
	case 4:
	    return JS_NewInt64 (ctx, static_cast<int64_t> (renderable->getTextureAnimationFrameCount ()));
	case 5:
	    return JS_NewBool (ctx, renderable->isTextureAnimationPlaying ());
	default:
	    return JS_UNDEFINED;
    }
}

static JSValue scriptableobject_get_texture_animation (
    JSContext* ctx, JSValueConst this_val, const int argc, JSValueConst* argv, const int magic, JSValue* func_data
) {
    JSValue controller = JS_NewObject (ctx);
    JS_SetPropertyStr (ctx, controller, "pause", JS_NewCFunctionData (ctx, textureanim_op, 0, 0, 1, func_data));
    JS_SetPropertyStr (ctx, controller, "play", JS_NewCFunctionData (ctx, textureanim_op, 0, 1, 1, func_data));
    // stop = pause at the current frame (no consumer distinguishes them yet)
    JS_SetPropertyStr (ctx, controller, "stop", JS_NewCFunctionData (ctx, textureanim_op, 0, 0, 1, func_data));
    JS_SetPropertyStr (ctx, controller, "setFrame", JS_NewCFunctionData (ctx, textureanim_op, 1, 2, 1, func_data));
    JS_SetPropertyStr (ctx, controller, "getFrame", JS_NewCFunctionData (ctx, textureanim_op, 0, 3, 1, func_data));
    JS_SetPropertyStr (ctx, controller, "getFrameCount", JS_NewCFunctionData (ctx, textureanim_op, 0, 4, 1, func_data));
    JS_SetPropertyStr (ctx, controller, "isPlaying", JS_NewCFunctionData (ctx, textureanim_op, 0, 5, 1, func_data));
    return controller;
}

JSValue scriptableobject_property_get (JSContext* ctx, JSValueConst obj_val, JSAtom atom, JSValueConst receiver) {
    JSClassID classId = 0;

    auto* container = static_cast<OpaqueScriptableObjectAdapter*> (JS_GetAnyOpaque (obj_val, &classId));

    if (!container || container->magic != SCRIPTABLE_OPAQUE_MAGIC) {
	return JS_ThrowTypeError (ctx, "invalid object");
    }

    const char* name = JS_AtomToCString (ctx, atom);

    if (name == nullptr) {
	return JS_EXCEPTION;
    }

    ScopeGuard guard ([=] { JS_FreeCString (ctx, name); });

    if (strcmp (name, "size") == 0) {
	if (const auto* image = dynamic_cast<const WallpaperEngine::Render::Objects::CImage*> (&container->object)) {
	    const auto size = image->getSize ();
	    JSValue out = JS_NewObject (ctx);
	    JS_SetPropertyStr (ctx, out, "x", JS_NewFloat64 (ctx, size.x));
	    JS_SetPropertyStr (ctx, out, "y", JS_NewFloat64 (ctx, size.y));
	    return out;
	}

	return JS_UNDEFINED;
    }

    if (strcmp (name, "getTextureAnimation") == 0) {
	JSValue self = obj_val;
	return JS_NewCFunctionData (ctx, scriptableobject_get_texture_animation, 0, 0, 1, &self);
    }

    try {
	// find the property inside, otherwise return undefined
	auto& property = container->object.getProperty (name);

	return container->adapter.getEngine ().dynamicToJs (property);
    } catch (const std::exception& e) {
	return JS_UNDEFINED;
    }
}

int scriptableobject_property_set (
    JSContext* ctx, JSValueConst obj_val, JSAtom atom, JSValueConst val, JSValueConst receiver, int flags
) {
    JSClassID classId = 0;

    auto* container = static_cast<OpaqueScriptableObjectAdapter*> (JS_GetAnyOpaque (obj_val, &classId));

    if (!container || container->magic != SCRIPTABLE_OPAQUE_MAGIC) {
	JS_ThrowTypeError (ctx, "invalid object");
	return -1;
    }

    const char* name = JS_AtomToCString (ctx, atom);

    if (name == nullptr) {
	return -1;
    }

    return 0;
}

ScriptableObjectAdapter::ScriptableObjectAdapter (ScriptEngine& engine, std::string name) :
    ObjectAdapter (engine), m_exoticMethods (), m_name (std::move (name)) {
    m_exoticMethods.get_property = scriptableobject_property_get;
    this->registerType (
	{
	    .class_name = m_name.c_str (),
	    .exotic = &m_exoticMethods,
	}
    );
}

JSValue ScriptableObjectAdapter::instantiate (ScriptableObject& object) {
    JSValue result = this->ObjectAdapter::instantiate (object);
    JS_SetOpaque (
	result,
	new OpaqueScriptableObjectAdapter { .magic = SCRIPTABLE_OPAQUE_MAGIC, .adapter = *this, .object = object }
    );

    return result;
}

JSValue ScriptableObjectAdapter::instantiate (DynamicValue& value) {
    throw std::runtime_error ("Cannot create a ScriptableObject instance from a DynamicValue");
}