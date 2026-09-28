#pragma once

#include <filesystem>
#include <optional>
#include <string>

#include <glm/vec4.hpp>

namespace WallpaperEngine::Application::FlagValues {
std::optional<double> plainNumber (const std::string& text);
float clampFactor (const std::string& flag, const std::string& text);
bool onOff (const std::string& flag, const std::string& text);
bool autoFull (const std::string& flag, const std::string& text);
std::string videoDecode (const std::string& flag, const std::string& text);
glm::vec4 color (const std::string& flag, const std::string& text);
float decimalInRange (const std::string& flag, const std::string& text, double lo, double hi);
int watchdogSeconds (const std::string& flag, const std::string& text);
float milliseconds (const std::string& flag, const std::string& text);
std::filesystem::path path (const std::string& flag, const std::string& text);
} // namespace WallpaperEngine::Application::FlagValues
