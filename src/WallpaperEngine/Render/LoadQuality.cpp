#include "LoadQuality.h"

#include <cstdlib>

#include "WallpaperEngine/Application/Config.h"
#include "WallpaperEngine/Logging/Log.h"

namespace WallpaperEngine::Render::LoadQuality {
float ssfactor (std::optional<float> perShow) {
    static bool logged = false;
    const auto& env = Application::Config::get ().ssfactor;
    float f = env.value;

    if (perShow.has_value ()) {
	f = *perShow;
    } else if (!logged && static_cast<float> (atof (env.raw.c_str ())) > 4.0f) {
	logged = true;
	sLog.error ("LWE_SSFACTOR=", env.raw, " is above 4; using 4");
    }

    // 0 and below is the escape hatch: no clamp at all
    return f <= 0.0f ? 0.0f : f;
}

float clampComposites (std::optional<float> perShow) {
    static bool logged = false;
    const auto& env = Application::Config::get ().clampComposites;
    float f = env.value;

    if (perShow.has_value ()) {
	f = *perShow;
    } else if (!logged && static_cast<float> (atof (env.raw.c_str ())) > 4.0f) {
	logged = true;
	sLog.error ("LWE_CLAMPCOMPOSITES=", env.raw, " is above 4; using 4");
    }

    return f <= 0.0f ? 0.0f : f;
}

bool texcomp (const std::string& value) {
    if (value == "1") {
	return true;
    }
    if (value == "0") {
	return false;
    }
    return Application::Config::get ().texcomp.value;
}

bool texdetailAuto (const std::string& value) {
    if (value == "auto") {
	return true;
    }
    if (value == "full") {
	return false;
    }
    return Application::Config::get ().texdetailAuto.value;
}
} // namespace WallpaperEngine::Render::LoadQuality
