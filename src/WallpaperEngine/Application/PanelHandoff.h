#pragma once

#include <functional>
#include <optional>
#include <string>
#include <vector>

namespace WallpaperEngine::Application::PanelHandoff {
std::optional<std::string> findPanelLauncher (
    const std::string& argv0, const std::string& path, const std::string& home,
    const std::function<bool (const std::string&)>& isExecutable
);
bool isExecutableFile (const std::string& path);
int handOff (const char* argv0, const std::vector<std::string>& words, const std::string& verb);
} // namespace WallpaperEngine::Application::PanelHandoff
