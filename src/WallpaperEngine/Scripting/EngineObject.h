#pragma once
#include "quickjs.h"

#include <chrono>
#include <map>
#include <vector>

namespace WallpaperEngine::Render::Wallpapers {
class CScene;
}
namespace WallpaperEngine::Scripting {
class ScriptEngine;
class EngineObject {
public:
    EngineObject (ScriptEngine& engine, Render::Wallpapers::CScene& scene);
    ~EngineObject ();

    [[nodiscard]] const Render::Wallpapers::CScene& getScene () const { return m_scene; }
    [[nodiscard]] JSValue getInstance () const { return m_instance; }
    [[nodiscard]] ScriptEngine& getEngine () const { return m_engine; }
    [[nodiscard]] uint32_t getInstanceId () const { return m_instanceId; }
    uint32_t reserveNextTimeoutId (JSValue function, uint64_t duration);
    uint32_t reserveNextIntervalId (JSValue function, uint64_t duration);
    void clearTimeout (uint32_t id);
    void clearInterval (uint32_t id);
    JSValue registerAudioBuffers (int requestedResolution);

    void tick ();

private:
    void updateAudioBuffers ();

protected:
    struct Timeout {
	JSValue callback;
	std::chrono::milliseconds duration;
	std::chrono::steady_clock::time_point next;
    };

    // one live engine.registerAudioBuffers() result; the arrays are refreshed
    // from the playback recorder every tick, before script update() calls run
    struct AudioBufferLink {
	int resolution;
	JSValue average;
	JSValue left;
	JSValue right;
    };

    uint32_t m_nextTimeoutId = 0;
    uint32_t m_nextIntervalId = 0;
    std::map<uint32_t, Timeout> m_intervals;
    std::map<uint32_t, Timeout> m_timeouts;
    std::vector<AudioBufferLink> m_audioBuffers;
    Render::Wallpapers::CScene& m_scene;
    ScriptEngine& m_engine;

    uint32_t m_instanceId;
    JSClassID m_classId;
    JSClassDef m_definition;
    JSValue m_instance;
};
}