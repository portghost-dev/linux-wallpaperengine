#pragma once

#include <nlohmann/json.hpp>
#include <string>

#include "WallpaperEngine/Api/Lane.h"

namespace WallpaperEngine::Application {
class BootGuard {
public:
    bool evaluate (const nlohmann::json& history);
    void wallpaperShown ();
    [[nodiscard]] bool restoreRefused () const;
    bool holdsShow (const std::string& cmd, const nlohmann::json& args);
    bool
    rotationMayAdvance (bool live, const Api::Lane& lane, const Api::Playlist& playlist, Api::Clock::time_point now);
    bool playlistTimerMayAdvance (Api::Clock::time_point nextSwitch, Api::Clock::time_point now);
    bool
    scheduleMayApply (bool live, const Api::Schedule& schedule, const std::string& missing, const Api::Playlist& bound);
    bool release (
	const std::string& cmd, const nlohmann::json& args, bool ok, Api::Lane& lane, const Api::Playlist& playlist,
	Api::Schedule& schedule, Api::Clock::time_point now
    );

private:
    bool holds ();

    bool m_restoreRefused = false;
    bool m_holdLogged = false;
};
} // namespace WallpaperEngine::Application
