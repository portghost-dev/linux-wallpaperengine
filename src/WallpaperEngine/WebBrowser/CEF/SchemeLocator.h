#pragma once

#include <filesystem>

#include "WallpaperEngine/Assets/AssetLocator.h"

namespace WallpaperEngine::WebBrowser::CEF {
[[nodiscard]] inline Assets::AssetLocatorUniquePtr
schemeAssetLocator (const std::filesystem::path& background, const std::filesystem::path& assets) {
    return Assets::setupWebAssetLocator (background.string (), assets);
}
} // namespace WallpaperEngine::WebBrowser::CEF
