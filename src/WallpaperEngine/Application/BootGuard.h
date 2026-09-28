#pragma once

#include <nlohmann/json.hpp>

namespace WallpaperEngine::Application {
class BootGuard {
public:
    bool evaluate (const nlohmann::json& history);
    void wallpaperShown ();
    [[nodiscard]] bool restoreRefused () const;

private:
    bool m_restoreRefused = false;
};
} // namespace WallpaperEngine::Application
