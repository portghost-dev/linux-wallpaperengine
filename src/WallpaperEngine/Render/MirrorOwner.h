#pragma once

#include <map>
#include <memory>
#include <set>
#include <string>

namespace WallpaperEngine::Render::MirrorOwner {
/** The screen that should own a shared wallpaper once `departed` leaves: another screen mapped
 *  to the same instance that still has a live viewport, or empty when none remains. The map keeps
 *  departed screens so a replug can present again, which is why membership alone is not enough. */
template <typename T>
std::string next (
    const std::map<std::string, std::shared_ptr<T>>& wallpapers, const std::string& departed,
    const std::set<std::string>& live
) {
    const auto it = wallpapers.find (departed);

    if (it == wallpapers.end ()) {
	return "";
    }

    for (const auto& [screen, wallpaper] : wallpapers) {
	if (screen != departed && wallpaper == it->second && live.count (screen) > 0) {
	    return screen;
	}
    }

    return "";
}
} // namespace WallpaperEngine::Render::MirrorOwner
