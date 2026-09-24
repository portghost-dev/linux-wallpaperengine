#include "LoadQuality.h"

#include <cmath>
#include <cstdlib>

#include "WallpaperEngine/Logging/Log.h"

namespace WallpaperEngine::Render::LoadQuality {
float ssfactor (std::optional<float> perShow) {
    static bool logged = false;
    float f = 1.0f;

    if (perShow.has_value ()) {
	f = *perShow;
    } else if (const char* e = getenv ("LWE_SSFACTOR"); e && *e) {
	f = static_cast<float> (atof (e));

	if (std::isnan (f)) {
	    f = 1.0f;
	} else if (f > 4.0f) {
	    if (!logged) {
		logged = true;
		sLog.error ("LWE_SSFACTOR=", e, " is above 4; using 4");
	    }

	    f = 4.0f;
	}
    }

    // 0 and below is the escape hatch: no clamp at all
    return f <= 0.0f ? 0.0f : f;
}

float clampComposites (std::optional<float> perShow) {
    static bool logged = false;
    float f = 1.0f;

    if (perShow.has_value ()) {
	f = *perShow;
    } else if (const char* e = getenv ("LWE_CLAMPCOMPOSITES"); e && *e) {
	f = static_cast<float> (atof (e));

	if (std::isnan (f)) {
	    f = 1.0f;
	} else if (f > 4.0f) {
	    if (!logged) {
		logged = true;
		sLog.error ("LWE_CLAMPCOMPOSITES=", e, " is above 4; using 4");
	    }

	    f = 4.0f;
	}
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
