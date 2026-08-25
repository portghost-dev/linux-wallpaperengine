#include "LocalStorageObject.h"

#include "ScriptEngine.h"
#include "WallpaperEngine/Data/Model/Project.h"
#include "WallpaperEngine/Data/Utils/ScopeGuard.h"
#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/Render/Wallpapers/CScene.h"

#include <cstdlib>
#include <fstream>
#include <nlohmann/json.hpp>

using namespace WallpaperEngine::Scripting;
using WallpaperEngine::Data::Utils::ScopeGuard;

namespace {
// null when a script rebinds a method to a foreign receiver (localStorage.get.call(null));
// every caller must guard
LocalStorageObject* opaque (JSValueConst thisVal) {
    JSClassID classId;
    return static_cast<LocalStorageObject*> (JS_GetAnyOpaque (thisVal, &classId));
}

JSValue lsGet (JSContext* ctx, JSValueConst thisVal, const int argc, JSValueConst* argv) {
    if (argc < 1) {
	return JS_UNDEFINED;
    }

    const char* key = JS_ToCString (ctx, argv[0]);
    if (!key) {
	return JS_UNDEFINED;
    }
    ScopeGuard guard ([ctx, key] { JS_FreeCString (ctx, key); });

    const auto* ls = opaque (thisVal);
    if (!ls) {
	return JS_UNDEFINED;
    }
    const std::string* value = ls->findItem (key);
    if (!value) {
	return JS_UNDEFINED;
    }

    const JSValue parsed = JS_ParseJSON (ctx, value->c_str (), value->size (), "<localStorage>");
    if (JS_IsException (parsed)) {
	JS_FreeValue (ctx, JS_GetException (ctx));
	return JS_NewString (ctx, value->c_str ());
    }

    return parsed;
}

JSValue lsSet (JSContext* ctx, JSValueConst thisVal, const int argc, JSValueConst* argv) {
    if (argc < 2) {
	return JS_UNDEFINED;
    }

    const char* key = JS_ToCString (ctx, argv[0]);
    if (!key) {
	return JS_UNDEFINED;
    }
    ScopeGuard guard ([ctx, key] { JS_FreeCString (ctx, key); });

    const JSValue json = JS_JSONStringify (ctx, argv[1], JS_UNDEFINED, JS_UNDEFINED);
    if (JS_IsException (json)) {
	JS_FreeValue (ctx, JS_GetException (ctx));
	return JS_UNDEFINED;
    }

    const char* jsonStr = JS_ToCString (ctx, json);
    if (jsonStr) {
	if (auto* ls = opaque (thisVal)) {
	    ls->setItem (key, jsonStr);
	}
	JS_FreeCString (ctx, jsonStr);
    }
    JS_FreeValue (ctx, json);

    return JS_UNDEFINED;
}

JSValue lsRemove (JSContext* ctx, JSValueConst thisVal, const int argc, JSValueConst* argv) {
    if (argc < 1) {
	return JS_UNDEFINED;
    }

    const char* key = JS_ToCString (ctx, argv[0]);
    if (!key) {
	return JS_UNDEFINED;
    }
    ScopeGuard guard ([ctx, key] { JS_FreeCString (ctx, key); });

    if (auto* ls = opaque (thisVal)) {
	ls->removeItem (key);
    }
    return JS_UNDEFINED;
}

JSValue lsGetItem (JSContext* ctx, JSValueConst thisVal, const int argc, JSValueConst* argv) {
    if (argc < 1) {
	return JS_NULL;
    }

    const char* key = JS_ToCString (ctx, argv[0]);
    if (!key) {
	return JS_NULL;
    }
    ScopeGuard guard ([ctx, key] { JS_FreeCString (ctx, key); });

    const auto* ls = opaque (thisVal);
    if (!ls) {
	return JS_NULL;
    }
    const std::string* value = ls->findItem (key);
    return value ? JS_NewString (ctx, value->c_str ()) : JS_NULL;
}

JSValue lsSetItem (JSContext* ctx, JSValueConst thisVal, const int argc, JSValueConst* argv) {
    if (argc < 2) {
	return JS_UNDEFINED;
    }

    const char* key = JS_ToCString (ctx, argv[0]);
    const char* value = JS_ToCString (ctx, argv[1]);
    if (!key || !value) {
	if (key) {
	    JS_FreeCString (ctx, key);
	}
	if (value) {
	    JS_FreeCString (ctx, value);
	}
	return JS_UNDEFINED;
    }
    ScopeGuard guardKey ([ctx, key] { JS_FreeCString (ctx, key); });
    ScopeGuard guardValue ([ctx, value] { JS_FreeCString (ctx, value); });

    if (auto* ls = opaque (thisVal)) {
	ls->setItem (key, value);
    }
    return JS_UNDEFINED;
}

JSValue lsClear (JSContext* ctx, JSValueConst thisVal, int, JSValueConst*) {
    if (auto* ls = opaque (thisVal)) {
	ls->clear ();
    }
    return JS_UNDEFINED;
}

JSValue lsLength (JSContext* ctx, JSValueConst thisVal, int, JSValueConst*) {
    const auto* ls = opaque (thisVal);
    return JS_NewInt32 (ctx, ls ? ls->length () : 0);
}

JSValue lsKey (JSContext* ctx, JSValueConst thisVal, const int argc, JSValueConst* argv) {
    if (argc < 1) {
	return JS_NULL;
    }

    int n = 0;
    JS_ToInt32 (ctx, &n, argv[0]);
    const auto* ls = opaque (thisVal);
    if (!ls || n < 0 || n >= ls->length ()) {
	return JS_NULL;
    }

    const std::string* key = ls->keyAt (n);
    return key ? JS_NewString (ctx, key->c_str ()) : JS_NULL;
}
} // namespace

LocalStorageObject::LocalStorageObject (ScriptEngine& engine, Render::Wallpapers::CScene& scene) :
    m_engine (engine), m_classId (0) {
    const char* home = std::getenv ("HOME");
    const std::filesystem::path base = (home && home[0] != '\0')
	? std::filesystem::path (home) / ".local" / "share" / "lwe" / "storage"
	: std::filesystem::temp_directory_path () / "lwe" / "storage";

    std::error_code ec;
    std::filesystem::create_directories (base, ec);

    const auto& project = scene.getScene ().project;
    std::string storageId = project.workshopId.empty () ? scene.getScene ().filename : project.workshopId;
    for (char& c : storageId) {
	if (c == '/' || c == '\\') {
	    c = '_';
	}
    }
    if (storageId.empty ()) {
	storageId = "default";
    }

    this->m_storagePath = base / (storageId + ".json");
    this->load ();

    this->m_definition = { .class_name = "ILocalStorage" };
    JS_NewClassID (this->m_engine.getRuntime (), &this->m_classId);
    JS_NewClass (this->m_engine.getRuntime (), this->m_classId, &this->m_definition);

    auto* ctx = this->m_engine.getContext ();
    this->m_instance = JS_NewObjectClass (ctx, this->m_classId);
    JS_DupValue (ctx, this->m_instance);
    JS_SetOpaque (this->m_instance, this);

    JS_DefinePropertyValueStr (
	ctx, this->m_instance, "get", JS_NewCFunction (ctx, lsGet, "get", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	ctx, this->m_instance, "set", JS_NewCFunction (ctx, lsSet, "set", 2), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	ctx, this->m_instance, "remove", JS_NewCFunction (ctx, lsRemove, "remove", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	ctx, this->m_instance, "LOCATION_SCREEN", JS_NewString (ctx, "LOCATION_SCREEN"), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	ctx, this->m_instance, "LOCATION_GLOBAL", JS_NewString (ctx, "LOCATION_GLOBAL"), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	ctx, this->m_instance, "getItem", JS_NewCFunction (ctx, lsGetItem, "getItem", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	ctx, this->m_instance, "setItem", JS_NewCFunction (ctx, lsSetItem, "setItem", 2), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	ctx, this->m_instance, "removeItem", JS_NewCFunction (ctx, lsRemove, "removeItem", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	ctx, this->m_instance, "clear", JS_NewCFunction (ctx, lsClear, "clear", 0), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyValueStr (
	ctx, this->m_instance, "key", JS_NewCFunction (ctx, lsKey, "key", 1), JS_PROP_ENUMERABLE
    );
    JS_DefinePropertyGetSet (
	ctx, this->m_instance, JS_NewAtom (ctx, "length"), JS_NewCFunction (ctx, lsLength, "get", 0), JS_UNDEFINED,
	JS_PROP_ENUMERABLE
    );
}

LocalStorageObject::~LocalStorageObject () { JS_FreeValue (this->m_engine.getContext (), this->m_instance); }

const std::string* LocalStorageObject::findItem (const std::string& key) const {
    const auto it = this->m_data.find (key);
    return it != this->m_data.end () ? &it->second : nullptr;
}

void LocalStorageObject::setItem (const std::string& key, const std::string& value) {
    this->m_data[key] = value;
    this->save ();
}

void LocalStorageObject::removeItem (const std::string& key) {
    this->m_data.erase (key);
    this->save ();
}

void LocalStorageObject::clear () {
    this->m_data.clear ();
    this->save ();
}

int LocalStorageObject::length () const { return static_cast<int> (this->m_data.size ()); }

const std::string* LocalStorageObject::keyAt (const int n) const {
    auto it = this->m_data.begin ();
    std::advance (it, n);
    return it != this->m_data.end () ? &it->first : nullptr;
}

void LocalStorageObject::load () {
    std::ifstream in (this->m_storagePath);
    if (!in.good ()) {
	return;
    }

    try {
	const auto json = nlohmann::json::parse (in);
	for (const auto& [key, value] : json.items ()) {
	    if (value.is_string ()) {
		this->m_data[key] = value.get<std::string> ();
	    }
	}
    } catch (const std::exception& e) {
	sLog.error ("localStorage: could not parse ", this->m_storagePath.string (), ": ", e.what ());
    }
}

void LocalStorageObject::save () const {
    nlohmann::json json = nlohmann::json::object ();
    for (const auto& [key, value] : this->m_data) {
	json[key] = value;
    }

    const auto tmp = this->m_storagePath.string () + ".tmp";
    std::ofstream out (tmp, std::ios::trunc);
    if (!out.good ()) {
	sLog.error ("localStorage: cannot write ", tmp);
	return;
    }
    out << json.dump (1);
    out.close ();
    // a failed write (ENOSPC) must not rename a truncated file over the good store
    if (!out.good ()) {
	sLog.error ("localStorage: write failed for ", tmp);
	std::error_code rmec;
	std::filesystem::remove (tmp, rmec);
	return;
    }

    std::error_code ec;
    std::filesystem::rename (tmp, this->m_storagePath, ec);
    if (ec) {
	sLog.error ("localStorage: rename failed for ", this->m_storagePath.string (), ": ", ec.message ());
    }
}
