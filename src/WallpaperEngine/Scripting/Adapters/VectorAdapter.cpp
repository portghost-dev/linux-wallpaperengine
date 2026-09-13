#include "VectorAdapter.h"

#include "../ScriptEngine.h"
#include "WallpaperEngine/Data/Utils/SFINAE.h"
#include "WallpaperEngine/Data/Utils/ScopeGuard.h"

#include <optional>
#include <sstream>
#include <variant>

using namespace WallpaperEngine::Data::Utils;
using namespace WallpaperEngine::Data::Model;
using namespace WallpaperEngine::Scripting::Adapters;

static uint32_t VectorInstanceId = 0;
static uint32_t VectorAdapterInstanceId = 0;
static constexpr int InvalidVectorInstanceId = 0;

// magic value used to ensure the assigned opaque value we got back is valid
#define VEC_OPAQUE_MAGIC 0xdeadbee0
#define VEC_MAGIC_CHECK_EXCEPTION(container, components)                                                               \
    do {                                                                                                               \
	if (!container || container->magic != (int)(VEC_OPAQUE_MAGIC + components)) {                                  \
	    return JS_ThrowTypeError (ctx, "invalid vector object");                                                   \
	}                                                                                                              \
    } while (0)
#define VEC_MAGIC_CHECK_ERROR(container, components)                                                                   \
    do {                                                                                                               \
	if (!container || container->magic != (int)(VEC_OPAQUE_MAGIC + components)) {                                  \
	    JS_ThrowTypeError (ctx, "invalid vector object");                                                          \
	    return -1;                                                                                                 \
	}                                                                                                              \
    } while (0)

template <int components> std::map<uint32_t, VectorAdapter<components>&> vectorAdapterInstances;

template <int components> struct VectorOpaqueContainer {
    int magic;
    VectorAdapter<components>& adapter;
    DynamicValue& value;
    uint32_t id;
    uint32_t adapterInstanceId;
};

template <int components> auto vector_new () -> decltype (auto) {
    static_assert (components >= 2 && components <= 4, "Unsupported vector type");

    if constexpr (components == 2) {
	return glm::vec2 {};
    } else if constexpr (components == 3) {
	return glm::vec3 {};
    } else if constexpr (components == 4) {
	return glm::vec4 {};
    }
}

template auto vector_new<2> () -> decltype (auto);
template auto vector_new<3> () -> decltype (auto);
template auto vector_new<4> () -> decltype (auto);

template <int components> auto vector_new (float value) -> decltype (auto) {
    static_assert (components >= 2 && components <= 4, "Unsupported vector type");

    if constexpr (components == 2) {
	return glm::vec2 (value);
    } else if constexpr (components == 3) {
	return glm::vec3 (value);
    } else if constexpr (components == 4) {
	return glm::vec4 (value);
    }
}

template auto vector_new<2> (float value) -> decltype (auto);
template auto vector_new<3> (float value) -> decltype (auto);
template auto vector_new<4> (float value) -> decltype (auto);

template <int components> auto vector_get (DynamicValue& value) -> decltype (auto) {
    static_assert (components >= 2 && components <= 4, "Unsupported vector type");

    if constexpr (components == 2) {
	return value.getVec2 ();
    } else if constexpr (components == 3) {
	return value.getVec3 ();
    } else if constexpr (components == 4) {
	return value.getVec4 ();
    }
}

template <int components> using vector_t = decltype (vector_new<components> ());

/** a number fills every component, an object supplies x/y/z/w; a bad operand raises a JS TypeError */
template <int components> std::optional<vector_t<components>> vector_get (JSContext* ctx, JSValue source) {
    static_assert (components >= 2 && components <= 4, "Unsupported vector type");

    int tag = JS_VALUE_GET_TAG (source);

    if (tag == JS_TAG_INT) {
	int32_t value = 0;

	JS_ToInt32 (ctx, &value, source);

	return vector_new<components> (static_cast<float> (value));
    }

    if (JS_TAG_IS_FLOAT64 (tag)) {
	double value = 0.0f;

	JS_ToFloat64 (ctx, &value, source);

	return vector_new<components> (static_cast<float> (value));
    }

    if (tag == JS_TAG_OBJECT) {
	// check components, extract x, y, z and w and create the appropriate vector
	JSValue x = JS_GetPropertyStr (ctx, source, "x");
	JSValue y = JS_GetPropertyStr (ctx, source, "y");
	JSValue z = JS_GetPropertyStr (ctx, source, "z");
	JSValue w = JS_GetPropertyStr (ctx, source, "w");
	ScopeGuard guard ([=] {
	    JS_FreeValue (ctx, x);
	    JS_FreeValue (ctx, y);
	    JS_FreeValue (ctx, z);
	    JS_FreeValue (ctx, w);
	});

	if (!JS_IsNumber (x) || !JS_IsNumber (y)) {
	    JS_ThrowTypeError (ctx, "expected a number or a vector with numeric x and y components");
	    return std::nullopt;
	}

	// do not accept bigger vectors
	if ((components <= 2 && JS_IsNumber (z)) || (components <= 3 && JS_IsNumber (w))) {
	    JS_ThrowTypeError (ctx, "vector argument has more components than Vec%d", components);
	    return std::nullopt;
	}

	double xVal = 0.0f, yVal = 0.0f, zVal = 0.0f, wVal = 0.0f;

	JS_ToFloat64 (ctx, &xVal, x);
	JS_ToFloat64 (ctx, &yVal, y);

	if (JS_IsNumber (z)) {
	    JS_ToFloat64 (ctx, &zVal, z);
	}

	if (JS_IsNumber (w)) {
	    JS_ToFloat64 (ctx, &wVal, w);
	}

	if constexpr (components == 2) {
	    return glm::vec2 (xVal, yVal);
	} else if constexpr (components == 3) {
	    return glm::vec3 (xVal, yVal, zVal);
	} else if constexpr (components == 4) {
	    return glm::vec4 (xVal, yVal, zVal, wVal);
	}
    }

    JS_ThrowTypeError (ctx, "expected a number or a vector");
    return std::nullopt;
}

template std::optional<vector_t<2>> vector_get<2> (JSContext* ctx, JSValue source);
template std::optional<vector_t<3>> vector_get<3> (JSContext* ctx, JSValue source);
template std::optional<vector_t<4>> vector_get<4> (JSContext* ctx, JSValue source);
template auto vector_get<2> (DynamicValue& value) -> decltype (auto);
template auto vector_get<3> (DynamicValue& value) -> decltype (auto);
template auto vector_get<4> (DynamicValue& value) -> decltype (auto);

/** component index for x/y/z/w within this vector size, -1 for any other name */
template <int components> int vector_component_index (const char* name) {
    if (name[0] == '\0' || name[1] != '\0') {
	return -1;
    }
    switch (name[0]) {
	case 'x': return 0;
	case 'y': return 1;
	case 'z': return components >= 3 ? 2 : -1;
	case 'w': return components >= 4 ? 3 : -1;
	default: return -1;
    }
}

/** only x/y/z/w are own properties; every other name resolves through the prototype */
template <int components>
int vector_get_own_property (JSContext* ctx, JSPropertyDescriptor* desc, JSValueConst obj_val, JSAtom atom) {
    JSClassID classId = 0;

    auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (obj_val, &classId));

    if (!container || container->magic != (int) (VEC_OPAQUE_MAGIC + components)) {
	return 0;
    }

    const char* name = JS_AtomToCString (ctx, atom);

    if (name == nullptr) {
	return -1;
    }

    ScopeGuard guard ([=] { JS_FreeCString (ctx, name); });
    const int index = vector_component_index<components> (name);

    if (index < 0) {
	return 0;
    }

    if (desc != nullptr) {
	const auto value = vector_get<components> (container->value);
	desc->flags = JS_PROP_WRITABLE | JS_PROP_ENUMERABLE;
	desc->getter = JS_UNDEFINED;
	desc->setter = JS_UNDEFINED;
	desc->value = JS_NewFloat64 (ctx, value[index]);
    }

    return 1;
}

template int vector_get_own_property<2> (JSContext* ctx, JSPropertyDescriptor* desc, JSValueConst obj_val, JSAtom atom);
template int vector_get_own_property<3> (JSContext* ctx, JSPropertyDescriptor* desc, JSValueConst obj_val, JSAtom atom);
template int vector_get_own_property<4> (JSContext* ctx, JSPropertyDescriptor* desc, JSValueConst obj_val, JSAtom atom);

/** enumeration hook so Object.keys / for..in / JSON.stringify see the components */
template <int components>
int vector_get_own_property_names (JSContext* ctx, JSPropertyEnum** ptab, uint32_t* plen, JSValueConst obj_val) {
    static constexpr const char* NAMES[] = { "x", "y", "z", "w" };
    auto* tab = static_cast<JSPropertyEnum*> (js_malloc (ctx, sizeof (JSPropertyEnum) * components));

    if (tab == nullptr) {
	return -1;
    }

    for (int i = 0; i < components; i++) {
	tab[i].is_enumerable = true;
	tab[i].atom = JS_NewAtom (ctx, NAMES[i]);
    }

    *ptab = tab;
    *plen = components;

    return 0;
}

template int vector_get_own_property_names<2> (JSContext* ctx, JSPropertyEnum** ptab, uint32_t* plen, JSValueConst obj_val);
template int vector_get_own_property_names<3> (JSContext* ctx, JSPropertyEnum** ptab, uint32_t* plen, JSValueConst obj_val);
template int vector_get_own_property_names<4> (JSContext* ctx, JSPropertyEnum** ptab, uint32_t* plen, JSValueConst obj_val);

template <int components>
int vector_property_set (
    JSContext* ctx, JSValueConst obj_val, JSAtom atom, JSValueConst val, JSValueConst receiver, int flags
) {
    JSClassID classId = 0;
    auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (obj_val, &classId));

    VEC_MAGIC_CHECK_ERROR (container, components);

    int tag = JS_VALUE_GET_TAG (val);

    if (tag != JS_TAG_INT && !JS_TAG_IS_FLOAT64 (tag)) {
	JS_ThrowTypeError (ctx, "vector components accept numbers only");
	return -1;
    }

    const char* name = JS_AtomToCString (ctx, atom);

    if (name == nullptr) {
	return -1;
    }

    ScopeGuard guard ([=] { JS_FreeCString (ctx, name); });
    auto vec = vector_get<components> (container->value);
    void* into = nullptr;

    if (strcmp (name, "x") == 0) {
	if constexpr (
	    std::is_same_v<decltype (vec), glm::vec2> || std::is_same_v<decltype (vec), glm::vec3>
	    || std::is_same_v<decltype (vec), glm::vec4>
	) {
	    into = &vec.x;
	} else if constexpr (std::is_same_v<decltype (vec), Color>) {
	    into = &vec.r;
	}
    } else if (strcmp (name, "y") == 0) {
	if constexpr (
	    std::is_same_v<decltype (vec), glm::vec2> || std::is_same_v<decltype (vec), glm::vec3>
	    || std::is_same_v<decltype (vec), glm::vec4>
	) {
	    into = &vec.y;
	} else if constexpr (std::is_same_v<decltype (vec), Color>) {
	    into = &vec.g;
	}
    } else if constexpr (components >= 3) {
	if (strcmp (name, "z") == 0) {
	    if constexpr (std::is_same_v<decltype (vec), glm::vec3> || std::is_same_v<decltype (vec), glm::vec4>) {
		into = &vec.z;
	    } else if constexpr (std::is_same_v<decltype (vec), Color>) {
		into = &vec.b;
	    }
	} else if constexpr (components >= 4) {
	    if (strcmp (name, "w") == 0) {
		if constexpr (std::is_same_v<decltype (vec), glm::vec4>) {
		    into = &vec.w;
		} else if constexpr (std::is_same_v<decltype (vec), Color>) {
		    into = &vec.a;
		}
	    }
	}
    }

    if (into == nullptr) {
	JS_ThrowTypeError (ctx, "vector has no component '%s'", name);
	return -1;
    }

    double value = 0;

    JS_ToFloat64 (ctx, &value, val);

    *static_cast<float*> (into) = static_cast<float> (value);

    container->value.update (vec, DynamicValue::UpdateSource::Script);

    return 0;
}

template int vector_property_set<2> (
    JSContext* ctx, JSValueConst obj_val, JSAtom atom, JSValueConst val, JSValueConst receiver, int flags
);
template int vector_property_set<3> (
    JSContext* ctx, JSValueConst obj_val, JSAtom atom, JSValueConst val, JSValueConst receiver, int flags
);
template int vector_property_set<4> (
    JSContext* ctx, JSValueConst obj_val, JSAtom atom, JSValueConst val, JSValueConst receiver, int flags
);

/** a fresh anonymous vector holding `value`; the container was just created so it cannot fail its magic check */
template <int components> JSValue vector_result (VectorAdapter<components>& adapter, const vector_t<components>& value) {
    JSValue newVector = adapter.instantiate ();
    JSClassID classId = 0;
    auto* newContainer = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (newVector, &classId));

    newContainer->value.update (value, DynamicValue::UpdateSource::Initialization);

    return newVector;
}

template <int components> JSValue vector_copy (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    JSClassID classId = 0;

    auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    // create a new DynamicValue
    return container->adapter.instantiate (container->value, true);
}

template JSValue vector_copy<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_copy<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_copy<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components> JSValue vector_equals (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    if (argc == 0) {
	return JS_FALSE;
    }

    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    const auto other = vector_get<components> (ctx, argv[0]);

    if (!other.has_value ()) {
	return JS_EXCEPTION;
    }

    // equality with epsilon
    const auto diff = glm::abs (vector_get<components> (container->value) - *other);

    for (int i = 0; i < components; i++) {
	if (diff[i] > 0.00001f) {
	    return JS_FALSE;
	}
    }

    return JS_TRUE;
}

template JSValue vector_equals<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_equals<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_equals<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components> JSValue vector_length (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    JSClassID classId = 0;

    auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    return JS_NewFloat64 (ctx, glm::length (vector_get<components> (container->value)));
}

template JSValue vector_length<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_length<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_length<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components> JSValue vector_length_sqr (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    JSClassID classId = 0;

    auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    const auto value = vector_get<components> (container->value);

    return JS_NewFloat64 (ctx, glm::dot (value, value));
}

template JSValue vector_length_sqr<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_length_sqr<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_length_sqr<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components>
JSValue vector_constructor (JSContext* ctx, JSValueConst new_target, int argc, JSValueConst* argv, int magic) {
    if (argc == 0) {
	return JS_ThrowTypeError (ctx, "invalid arguments");
    }

    auto it = vectorAdapterInstances<components>.find (magic);

    if (it == vectorAdapterInstances<components>.end ()) {
	return JS_ThrowTypeError (ctx, "invalid object");
    }

    JSValue result = it->second.instantiate ();
    JSClassID classId = 0;
    auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (result, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    const auto value = vector_get<components> (ctx, argv[0]);

    if (!value.has_value ()) {
	JS_FreeValue (ctx, result);
	return JS_EXCEPTION;
    }

    container->value.update (*value, DynamicValue::UpdateSource::Initialization);

    return result;
}

template JSValue vector_constructor<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv, int magic);
template JSValue vector_constructor<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv, int magic);
template JSValue vector_constructor<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv, int magic);

template <int components> void vector_finalizer (JSRuntime* rt, JSValueConst val) {
    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (val, &classId));

    if (!container || container->magic != (int)(VEC_OPAQUE_MAGIC + components)) {
	return;
    }

    if (container->id != InvalidVectorInstanceId
	&& vectorAdapterInstances<components>.find (container->adapterInstanceId)
	    != vectorAdapterInstances<components>.end ()) {
	container->adapter.free (container->id);
    }

    delete container;
}

template void vector_finalizer<2> (JSRuntime* rt, JSValueConst val);
template void vector_finalizer<3> (JSRuntime* rt, JSValueConst val);
template void vector_finalizer<4> (JSRuntime* rt, JSValueConst val);

template <int components> JSValue vector_normalize (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    return vector_result<components> (container->adapter, glm::normalize (vector_get<components> (container->value)));
}

template JSValue vector_normalize<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_normalize<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_normalize<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components> JSValue vector_add (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    if (argc != 1) {
	return JS_ThrowTypeError (ctx, "add expects one argument");
    }

    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    const auto other = vector_get<components> (ctx, argv[0]);

    if (!other.has_value ()) {
	return JS_EXCEPTION;
    }

    const auto self = vector_get<components> (container->value);

    return vector_result<components> (container->adapter, self + *other);
}

template JSValue vector_add<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_add<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_add<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components> JSValue vector_subtract (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    if (argc != 1) {
	return JS_ThrowTypeError (ctx, "subtract expects one argument");
    }

    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    const auto other = vector_get<components> (ctx, argv[0]);

    if (!other.has_value ()) {
	return JS_EXCEPTION;
    }

    const auto self = vector_get<components> (container->value);

    return vector_result<components> (container->adapter, self - *other);
}

template JSValue vector_subtract<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_subtract<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_subtract<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components> JSValue vector_multiply (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    if (argc != 1) {
	return JS_ThrowTypeError (ctx, "multiply expects one argument");
    }

    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    const auto other = vector_get<components> (ctx, argv[0]);

    if (!other.has_value ()) {
	return JS_EXCEPTION;
    }

    const auto self = vector_get<components> (container->value);

    return vector_result<components> (container->adapter, self * *other);
}

template JSValue vector_multiply<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_multiply<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_multiply<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components> JSValue vector_divide (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    if (argc != 1) {
	return JS_ThrowTypeError (ctx, "divide expects one argument");
    }

    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    const auto other = vector_get<components> (ctx, argv[0]);

    if (!other.has_value ()) {
	return JS_EXCEPTION;
    }

    const auto self = vector_get<components> (container->value);

    return vector_result<components> (container->adapter, self / *other);
}

template JSValue vector_divide<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_divide<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_divide<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components> JSValue vector_dot (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    if (argc != 1) {
	return JS_ThrowTypeError (ctx, "dot expects one argument");
    }

    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    const auto other = vector_get<components> (ctx, argv[0]);

    if (!other.has_value ()) {
	return JS_EXCEPTION;
    }

    const auto self = vector_get<components> (container->value);

    return JS_NewFloat64 (ctx, glm::dot (self, *other));
}

template JSValue vector_dot<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_dot<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_dot<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components> JSValue vector_cross (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    if (argc != 1) {
	return JS_ThrowTypeError (ctx, "cross expects one argument");
    }

    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    const auto other = vector_get<components> (ctx, argv[0]);

    if (!other.has_value ()) {
	return JS_EXCEPTION;
    }

    const auto self = vector_get<components> (container->value);

    return vector_result<components> (container->adapter, glm::cross (self, *other));
}

template JSValue vector_cross<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components> JSValue vector_mix (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    if (argc != 2 || !JS_IsNumber (argv[1])) {
	return JS_ThrowTypeError (ctx, "mix expects a vector and a number");
    }

    double amount = 0.0f;

    JS_ToFloat64 (ctx, &amount, argv[1]);

    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    const auto other = vector_get<components> (ctx, argv[0]);

    if (!other.has_value ()) {
	return JS_EXCEPTION;
    }

    return vector_result<components> (
	container->adapter, glm::mix (vector_get<components> (container->value), *other, static_cast<float> (amount))
    );
}

template JSValue vector_mix<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_mix<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_mix<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components> JSValue vector_min (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    if (argc != 1) {
	return JS_ThrowTypeError (ctx, "min expects one argument");
    }

    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    const auto other = vector_get<components> (ctx, argv[0]);

    if (!other.has_value ()) {
	return JS_EXCEPTION;
    }

    const auto self = vector_get<components> (container->value);

    return vector_result<components> (container->adapter, glm::min (self, *other));
}

template JSValue vector_min<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_min<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_min<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components> JSValue vector_max (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    if (argc != 1) {
	return JS_ThrowTypeError (ctx, "max expects one argument");
    }

    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    const auto other = vector_get<components> (ctx, argv[0]);

    if (!other.has_value ()) {
	return JS_EXCEPTION;
    }

    const auto self = vector_get<components> (container->value);

    return vector_result<components> (container->adapter, glm::max (self, *other));
}

template JSValue vector_max<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_max<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_max<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components> JSValue vector_abs (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    return vector_result<components> (container->adapter, glm::abs (vector_get<components> (container->value)));
}

template JSValue vector_abs<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_abs<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_abs<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components> JSValue vector_sign (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    return vector_result<components> (container->adapter, glm::sign (vector_get<components> (container->value)));
}

template JSValue vector_sign<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_sign<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_sign<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components> JSValue vector_round (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    return vector_result<components> (container->adapter, glm::round (vector_get<components> (container->value)));
}

template JSValue vector_round<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_round<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_round<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components> JSValue vector_floor (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    return vector_result<components> (container->adapter, glm::floor (vector_get<components> (container->value)));
}

template JSValue vector_floor<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_floor<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_floor<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components> JSValue vector_ceil (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    return vector_result<components> (container->adapter, glm::ceil (vector_get<components> (container->value)));
}

template JSValue vector_ceil<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_ceil<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_ceil<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components>
JSValue vector_toString (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv) {
    JSClassID classId = 0;
    const auto* container = static_cast<VectorOpaqueContainer<components>*> (JS_GetAnyOpaque (this_val, &classId));

    VEC_MAGIC_CHECK_EXCEPTION (container, components);

    // space-separated so the string parses back into a vector
    const auto value = vector_get<components> (container->value);
    std::ostringstream out;

    for (int i = 0; i < components; i++) {
	if (i > 0) {
	    out << ' ';
	}
	out << value[i];
    }

    return JS_NewString (ctx, out.str ().c_str ());
}

template JSValue vector_toString<2> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_toString<3> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);
template JSValue vector_toString<4> (JSContext* ctx, JSValueConst this_val, int argc, JSValueConst* argv);

template <int components>
VectorAdapter<components>::VectorAdapter (ScriptEngine& engine) :
    ObjectAdapter (engine), m_instanceId (++VectorAdapterInstanceId), m_name ("Vec" + std::to_string (components)),
    m_exoticMethods (
	{
	    .get_own_property = vector_get_own_property<components>,
	    .get_own_property_names = vector_get_own_property_names<components>,
	    .set_property = vector_property_set<components>,
	}
    ) {
    vectorAdapterInstances<components>.emplace (this->m_instanceId, *this);
    this->registerType (
	{
	    .class_name = this->m_name.c_str (),
	    .finalizer = vector_finalizer<components>,
	    .exotic = &this->m_exoticMethods,
	}
    );

    // build the prototype for the Vector and assign the required methods
    m_prototype = JS_NewObject (this->m_engine.getContext ());

    JS_DupValue (this->m_engine.getContext (), m_prototype);

    JSValue ctor = JS_NewCFunctionMagic (
	this->m_engine.getContext (), vector_constructor<components>, this->m_name.c_str (), 1,
	JS_CFUNC_constructor_magic, this->m_instanceId
    );

    JS_SetConstructor (this->m_engine.getContext (), ctor, m_prototype);
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "copy",
	JS_NewCFunction (this->m_engine.getContext (), vector_copy<components>, "copy", 0), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "equals",
	JS_NewCFunction (this->m_engine.getContext (), vector_equals<components>, "equals", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "length",
	JS_NewCFunction (this->m_engine.getContext (), vector_length<components>, "length", 0), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "lengthSqr",
	JS_NewCFunction (this->m_engine.getContext (), vector_length_sqr<components>, "lengthSqr", 0), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "normalize",
	JS_NewCFunction (this->m_engine.getContext (), vector_normalize<components>, "normalize", 0), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "add",
	JS_NewCFunction (this->m_engine.getContext (), vector_add<components>, "add", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "subtract",
	JS_NewCFunction (this->m_engine.getContext (), vector_subtract<components>, "subtract", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "multiply",
	JS_NewCFunction (this->m_engine.getContext (), vector_multiply<components>, "multiply", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "divide",
	JS_NewCFunction (this->m_engine.getContext (), vector_divide<components>, "divide", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "dot",
	JS_NewCFunction (this->m_engine.getContext (), vector_dot<components>, "dot", 1), JS_PROP_ENUMERABLE
    );
    if constexpr (components == 3) {
	JS_DefinePropertyValueStr (
	    this->m_engine.getContext (), m_prototype, "cross",
	    JS_NewCFunction (this->m_engine.getContext (), vector_cross<components>, "cross", 1), JS_PROP_ENUMERABLE
	);
    }
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "mix",
	JS_NewCFunction (this->m_engine.getContext (), vector_mix<components>, "mix", 2), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "min",
	JS_NewCFunction (this->m_engine.getContext (), vector_min<components>, "min", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "max",
	JS_NewCFunction (this->m_engine.getContext (), vector_max<components>, "max", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "abs",
	JS_NewCFunction (this->m_engine.getContext (), vector_abs<components>, "abs", 0), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "sign",
	JS_NewCFunction (this->m_engine.getContext (), vector_sign<components>, "sign", 0), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "round",
	JS_NewCFunction (this->m_engine.getContext (), vector_round<components>, "round", 0), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "floor",
	JS_NewCFunction (this->m_engine.getContext (), vector_floor<components>, "floor", 0), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "ceil",
	JS_NewCFunction (this->m_engine.getContext (), vector_ceil<components>, "ceil", 0), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	this->m_engine.getContext (), m_prototype, "toString",
	JS_NewCFunction (this->m_engine.getContext (), vector_toString<components>, "toString", 0), JS_PROP_ENUMERABLE
    );

    JS_SetClassProto (this->m_engine.getContext (), this->m_classId, m_prototype);
    JS_FreeValue (this->m_engine.getContext (), ctor);

    // builtins.js extends this prototype with the methods it implements in JS
    JSValue global = JS_GetGlobalObject (this->m_engine.getContext ());
    JS_SetPropertyStr (
	this->m_engine.getContext (), global, ("__lweNative" + this->m_name + "Proto").c_str (),
	JS_DupValue (this->m_engine.getContext (), m_prototype)
    );
    JS_FreeValue (this->m_engine.getContext (), global);
}

template <int components> VectorAdapter<components>::~VectorAdapter () {
    vectorAdapterInstances<components>.erase (this->m_instanceId);

    JS_FreeValue (this->m_engine.getContext (), m_prototype);
}

template <int components> JSValue VectorAdapter<components>::instantiate (ScriptableObject& object) {
    throw new std::runtime_error ("Cannot create a Vector4 instance from a ScriptableObject");
}

template <int components> JSValue VectorAdapter<components>::instantiate (DynamicValue& value) {
    JSValue result = this->ObjectAdapter::instantiate (value);
    JS_SetOpaque (
	result,
	new VectorOpaqueContainer<components> {
	    .magic = VEC_OPAQUE_MAGIC + components,
	    .adapter = *this,
	    .value = value,
	    .id = InvalidVectorInstanceId,
	    .adapterInstanceId = this->m_instanceId,
	}
    );

    return result;
}

template <int components> JSValue VectorAdapter<components>::instantiate (DynamicValue& source, bool temporal) {
    auto value = std::make_unique<DynamicValue> (source);
    uint32_t id = ++VectorInstanceId;
    JSValue result = this->ObjectAdapter::instantiate (*value);
    JS_SetOpaque (
	result,
	new VectorOpaqueContainer<components> {
	    .magic = VEC_OPAQUE_MAGIC + components,
	    .adapter = *this,
	    .value = *value,
	    .id = id,
	    .adapterInstanceId = this->m_instanceId,
	}
    );

    this->m_values.emplace (id, std::move (value));

    return result;
}

template <int components> JSValue VectorAdapter<components>::instantiate () {
    auto value = std::make_unique<DynamicValue> (vector_new<components> ());
    uint32_t id = ++VectorInstanceId;
    JSValue result = this->ObjectAdapter::instantiate (*value);
    JS_SetOpaque (
	result,
	new VectorOpaqueContainer<components> {
	    .magic = VEC_OPAQUE_MAGIC + components,
	    .adapter = *this,
	    .value = *value,
	    .id = id,
	    .adapterInstanceId = this->m_instanceId,
	}
    );

    this->m_values.emplace (id, std::move (value));

    return result;
}

template <int components> void VectorAdapter<components>::free (uint32_t vectorId) {
    auto it = this->m_values.find (vectorId);

    if (it != this->m_values.end ()) {
	this->m_values.erase (it);
    }
}

namespace WallpaperEngine::Scripting::Adapters {
template class VectorAdapter<2>;
template class VectorAdapter<3>;
template class VectorAdapter<4>;
} // namespace WallpaperEngine::Scripting::Adapters