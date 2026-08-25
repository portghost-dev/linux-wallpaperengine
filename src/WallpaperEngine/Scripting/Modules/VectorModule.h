#pragma once
#include "ScriptModule.h"

namespace WallpaperEngine::Scripting {
class ScriptEngine;
}
namespace WallpaperEngine::Scripting::Modules {
class VectorModule : public ScriptModule {
public:
    VectorModule (ScriptEngine& engine);
    ~VectorModule () override;

protected:
    uint32_t m_instanceId;
};
}
