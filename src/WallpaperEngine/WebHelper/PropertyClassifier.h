#pragma once

#include "WallpaperEngine/Data/Model/Types.h"
#include "WallpaperEngine/WebHelper/Protocol.h"

#include <optional>
#include <string>
#include <vector>

namespace WallpaperEngine::WebHelper {
/**
 * Classify one parsed property into its wire form.
 *
 * Returns nothing for properties the page must not see: PropertyText is a display-only
 * label in the Wallpaper Engine editor and carries no JS-visible value.
 */
[[nodiscard]] std::optional<PropertyValue>
classifyProperty (const std::string& key, const WallpaperEngine::Data::Model::Property& property);

/** every classifiable property of a project, in the map's order */
[[nodiscard]] std::vector<PropertyValue>
classifyProperties (const WallpaperEngine::Data::Model::Properties& properties);
}
