#include "MathModule.h"

#include "WallpaperEngine/Scripting/ScriptEngine.h"

using namespace WallpaperEngine::Scripting::Modules;

#define min_f(a, b, c) (fminf (a, fminf (b, c)))
#define max_f(a, b, c) (fmaxf (a, fmaxf (b, c)))

static uint32_t MathModuleInstanceId = 0;
std::map<uint32_t, MathModule&> mathModules;

JSValue wemath_smoothstep (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv, int magic);
JSValue wemath_mix (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv, int magic);

int wemath_init (JSContext* ctx, JSModuleDef* m) {
    // runs at module instantiation (first import): the export slots declared by
    // JS_AddModuleExport in the constructor exist now and can receive values
    for (const auto& [id, module] : mathModules) {
	if (module.getDefinition () != m) {
	    continue;
	}

	JS_SetModuleExport (
	    ctx, m, "smoothStep",
	    JS_NewCFunctionMagic (ctx, wemath_smoothstep, "smoothStep", 3, JS_CFUNC_generic_magic, id)
	);
	JS_SetModuleExport (
	    ctx, m, "mix", JS_NewCFunctionMagic (ctx, wemath_mix, "mix", 3, JS_CFUNC_generic_magic, id)
	);
	JS_SetModuleExport (ctx, m, "deg2rad", JS_NewFloat64 (ctx, 0.01745329251994329576923690768489));
	JS_SetModuleExport (ctx, m, "rad2deg", JS_NewFloat64 (ctx, 57.295779513082320876798154814105));

	return 0;
    }

    JS_ThrowReferenceError (ctx, "WEMath module instantiated without a registered definition");
    return -1;
}

JSValue wemath_smoothstep (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv, int magic) {
    if (argc != 3) {
	return JS_ThrowTypeError (ctx, "invalid arguments");
    }

    if (!JS_IsNumber (argv[0]) || !JS_IsNumber (argv[1]) || !JS_IsNumber (argv[2])) {
	return JS_ThrowTypeError (ctx, "invalid arguments");
    }

    double edge0 = 0.0f;
    double edge1 = 1.0f;
    double x = 0.0f;

    JS_ToFloat64 (ctx, &edge0, argv[0]);
    JS_ToFloat64 (ctx, &edge1, argv[1]);
    JS_ToFloat64 (ctx, &x, argv[2]);

    return JS_NewFloat64 (ctx, glm::smoothstep (edge0, edge1, x));
}

JSValue wemath_mix (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv, int magic) {
    if (argc != 3) {
	return JS_ThrowTypeError (ctx, "invalid arguments");
    }

    if (!JS_IsNumber (argv[0]) || !JS_IsNumber (argv[1]) || !JS_IsNumber (argv[2])) {
	return JS_ThrowTypeError (ctx, "invalid arguments");
    }

    double a = 0.0f;
    double b = 1.0f;
    double value = 0.0f;

    JS_ToFloat64 (ctx, &a, argv[0]);
    JS_ToFloat64 (ctx, &b, argv[1]);
    JS_ToFloat64 (ctx, &value, argv[2]);

    return JS_NewFloat64 (ctx, glm::mix (a, b, value));
}

MathModule::MathModule (ScriptEngine& engine) : ScriptModule (engine, "WEMath", wemath_init) {
    this->m_instanceId = ++MathModuleInstanceId;

    // exports must be DECLARED before the module is instantiated; their values
    // are set in wemath_init, which quickjs calls at instantiation time
    JSContext* ctx = this->getEngine ().getContext ();
    JS_AddModuleExport (ctx, this->getDefinition (), "smoothStep");
    JS_AddModuleExport (ctx, this->getDefinition (), "mix");
    JS_AddModuleExport (ctx, this->getDefinition (), "deg2rad");
    JS_AddModuleExport (ctx, this->getDefinition (), "rad2deg");

    mathModules.emplace (this->m_instanceId, *this);
}

MathModule::~MathModule () { mathModules.erase (this->m_instanceId); }