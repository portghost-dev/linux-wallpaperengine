#pragma once

#include <nlohmann/json.hpp>
#include <optional>
#include <string>
#include <vector>

namespace WallpaperEngine::Application::LweCommand {
enum class Action { Status, Off, On, Version, Usage, Panel };

struct Request {
    Action action = Action::Status;
    bool json = false;
    std::string verb;
};

bool isLweName (int argc, char* argv[]);
Request parse (const std::vector<std::string>& words);
std::string statusText (const nlohmann::json& status);
std::optional<std::string> versionMismatch (const nlohmann::json& status, const std::string& installed);
int run (int argc, char* argv[]);
} // namespace WallpaperEngine::Application::LweCommand
