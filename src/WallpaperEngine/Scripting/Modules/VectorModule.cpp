#include "VectorModule.h"

#include "WallpaperEngine/Scripting/ScriptEngine.h"

#include <cmath>

using namespace WallpaperEngine::Scripting::Modules;

static uint32_t VectorModuleInstanceId = 0;
std::map<uint32_t, VectorModule&> vectorModules;

namespace {
double readComponent (JSContext* ctx, JSValueConst obj, const char* name) {
    const JSValue v = JS_GetPropertyStr (ctx, obj, name);
    double out = 0.0;
    JS_ToFloat64 (ctx, &out, v);
    JS_FreeValue (ctx, v);
    return out;
}
} // namespace

JSValue wevector_vectorangle2 (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv, int magic) {
    if (argc < 1 || !JS_IsObject (argv[0])) {
	return JS_NewFloat64 (ctx, 0.0);
    }
    const double x = readComponent (ctx, argv[0], "x");
    const double y = readComponent (ctx, argv[0], "y");
    return JS_NewFloat64 (ctx, std::atan2 (y, x) * 57.295779513082320876798154814105);
}

/** WEVector.vectorAngle3(vec3, vec3) -> angle between two 3D vectors, degrees */
JSValue wevector_vectorangle3 (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv, int magic) {
    if (argc < 2 || !JS_IsObject (argv[0]) || !JS_IsObject (argv[1])) {
	return JS_NewFloat64 (ctx, 0.0);
    }
    const double ax = readComponent (ctx, argv[0], "x");
    const double ay = readComponent (ctx, argv[0], "y");
    const double az = readComponent (ctx, argv[0], "z");
    const double bx = readComponent (ctx, argv[1], "x");
    const double by = readComponent (ctx, argv[1], "y");
    const double bz = readComponent (ctx, argv[1], "z");
    const double la = std::sqrt (ax * ax + ay * ay + az * az);
    const double lb = std::sqrt (bx * bx + by * by + bz * bz);
    if (la < 1e-12 || lb < 1e-12) {
	return JS_NewFloat64 (ctx, 0.0);
    }
    const double c = std::clamp ((ax * bx + ay * by + az * bz) / (la * lb), -1.0, 1.0);
    return JS_NewFloat64 (ctx, std::acos (c) * 57.295779513082320876798154814105);
}

int wevector_init (JSContext* ctx, JSModuleDef* m) {
    for (const auto& [id, module] : vectorModules) {
	if (module.getDefinition () != m) {
	    continue;
	}

	JS_SetModuleExport (
	    ctx, m, "vectorAngle2",
	    JS_NewCFunctionMagic (ctx, wevector_vectorangle2, "vectorAngle2", 1, JS_CFUNC_generic_magic, id)
	);
	JS_SetModuleExport (
	    ctx, m, "vectorAngle3",
	    JS_NewCFunctionMagic (ctx, wevector_vectorangle3, "vectorAngle3", 2, JS_CFUNC_generic_magic, id)
	);
    }

    return 0;
}

VectorModule::VectorModule (ScriptEngine& engine) : ScriptModule (engine, "WEVector", wevector_init) {
    this->m_instanceId = ++VectorModuleInstanceId;

    JSContext* ctx = this->getEngine ().getContext ();
    JS_AddModuleExport (ctx, this->getDefinition (), "vectorAngle2");
    JS_AddModuleExport (ctx, this->getDefinition (), "vectorAngle3");

    vectorModules.emplace (this->m_instanceId, *this);
}

VectorModule::~VectorModule () { vectorModules.erase (this->m_instanceId); }
