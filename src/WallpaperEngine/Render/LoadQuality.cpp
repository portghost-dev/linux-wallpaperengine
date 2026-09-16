#include "LoadQuality.h"

#include <cstdlib>

namespace WallpaperEngine::Render::LoadQuality {
float ssfactor (const std::string& res) {
    if (res == "wallpaper") {
	return 0.0f;
    }
    if (res == "screen" || res == "sharpfx") {
	return 1.0f;
    }
    const char* e = getenv ("LWE_SSFACTOR");
    const float f = e && *e ? static_cast<float> (atof (e)) : 1.0f;
    // 0 and below is the escape hatch: no clamp at all
    return f <= 0.0f ? 0.0f : f;
}

bool clampComposites (const std::string& res) {
    if (res == "sharpfx") {
	return false;
    }
    if (res == "screen" || res == "wallpaper") {
	return true;
    }
    const char* e = getenv ("LWE_CLAMPCOMPOSITES");
    return e == nullptr || std::string (e) != "0";
}

bool texcomp (const std::string& value) {
    if (value == "1") {
	return true;
    }
    if (value == "0") {
	return false;
    }
    const char* e = getenv ("LWE_TEXCOMP");
    return e == nullptr || std::string (e) != "0";
}

bool texdetailAuto (const std::string& value) {
    if (value == "auto") {
	return true;
    }
    if (value == "full") {
	return false;
    }
    const char* e = getenv ("LWE_TEXDETAIL");
    return e == nullptr || std::string (e) == "auto";
}
} // namespace WallpaperEngine::Render::LoadQuality
