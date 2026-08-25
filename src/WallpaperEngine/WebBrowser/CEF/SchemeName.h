#pragma once

#include <string>

#define WPENGINE_SCHEME "wp"

namespace WallpaperEngine::WebBrowser::CEF {
[[nodiscard]] inline std::string generateSchemeName (const std::string& workshopId) {
    return std::string (WPENGINE_SCHEME) + workshopId;
}
} // namespace WallpaperEngine::WebBrowser::CEF
