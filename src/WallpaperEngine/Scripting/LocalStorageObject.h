#pragma once
#include "quickjs.h"
#include <filesystem>
#include <map>
#include <string>

namespace WallpaperEngine::Render::Wallpapers {
class CScene;
}
namespace WallpaperEngine::Scripting {
class ScriptEngine;

class LocalStorageObject {
public:
    LocalStorageObject (ScriptEngine& engine, Render::Wallpapers::CScene& scene);
    ~LocalStorageObject ();

    JSValue getInstance () const { return m_instance; }

    [[nodiscard]] const std::string* findItem (const std::string& key) const;
    void setItem (const std::string& key, const std::string& value);
    void removeItem (const std::string& key);
    void clear ();
    [[nodiscard]] int length () const;
    [[nodiscard]] const std::string* keyAt (int n) const;

private:
    void load ();
    void save () const;

    ScriptEngine& m_engine;

    std::map<std::string, std::string> m_data;
    std::filesystem::path m_storagePath;

    JSClassID m_classId;
    JSClassDef m_definition {};
    JSValue m_instance;
};
} // namespace WallpaperEngine::Scripting
