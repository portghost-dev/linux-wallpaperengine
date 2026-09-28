#include "Config.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <unistd.h>

extern float g_LweClassicDivisor;
extern float g_LweFalloffExp;
extern float g_LweAudioGain;
extern float g_LweAudioSmoothMs;

namespace WallpaperEngine::Application {
static Config::Flags flagStore;

static float lweEnvFloat (const char* name, const float fallback, const float lo, const float hi) {
    const char* env = getenv (name);
    if (env == nullptr) {
	return fallback;
    }
    return std::clamp (static_cast<float> (atof (env)), lo, hi);
}

// static initializers make the first call, before main attaches any log output, so nothing here may log
static Config fromEnvironment () {
    Config config {};

    if (const char* socketOverride = getenv ("LWE_SOCKET"); socketOverride != nullptr && *socketOverride != 0) {
	config.socket = { socketOverride, "env", socketOverride };
    } else if (const char* runtime = getenv ("XDG_RUNTIME_DIR"); runtime != nullptr && *runtime != '\0') {
	config.socket = { std::filesystem::path (runtime) / "lwe" / "engine.sock", "default", "" };
    } else {
	// no runtime dir (unusual: no logind session). /tmp is world-writable, so a
	// uid-qualified subdirectory created 0700 is the only safe shape here.
	config.socket = { std::filesystem::path ("/tmp") / ("lwe-" + std::to_string (geteuid ())) / "engine.sock",
			  "default", "" };
    }

    config.ssfactor = { 1.0f, "default", "" };

    if (const char* e = getenv ("LWE_SSFACTOR"); e && *e) {
	const float f = static_cast<float> (atof (e));

	config.ssfactor.raw = e;

	if (!std::isnan (f)) {
	    config.ssfactor.value = f > 4.0f ? 4.0f : f <= 0.0f ? 0.0f : f;
	    config.ssfactor.source = "env";
	}
    }

    config.clampComposites = { 1.0f, "default", "" };

    if (const char* e = getenv ("LWE_CLAMPCOMPOSITES"); e && *e) {
	const float f = static_cast<float> (atof (e));

	config.clampComposites.raw = e;

	if (!std::isnan (f)) {
	    config.clampComposites.value = f > 4.0f ? 4.0f : f <= 0.0f ? 0.0f : f;
	    config.clampComposites.source = "env";
	}
    }

    if (const char* e = getenv ("LWE_TEXCOMP"); e != nullptr) {
	config.texcomp = { std::string (e) != "0", "env", e };
    } else {
	config.texcomp = { true, "default", "" };
    }

    if (const char* e = getenv ("LWE_TEXDETAIL"); e != nullptr) {
	config.texdetailAuto = { std::string (e) == "auto", "env", e };
    } else {
	config.texdetailAuto = { true, "default", "" };
    }

    if (const char* hwdecMode = getenv ("LWE_HWDEC"); hwdecMode && *hwdecMode) {
	config.hwdec = { hwdecMode, "env", hwdecMode };
    } else {
	config.hwdec = { "no", "default", "" };
    }

    config.cc = { { 1.0f, 1.0f, 1.0f, 0.0f }, "default", "" };

    if (const char* e = getenv ("LWE_CC"); e != nullptr) {
	glm::vec4 cc = config.cc.value;

	config.cc.raw = e;

	if (sscanf (e, "%f %f %f %f", &cc.x, &cc.y, &cc.z, &cc.w) == 4) {
	    config.cc.value = cc;
	    config.cc.source = "env";
	}
    }

    config.timescale = { 1.0f, "default", "" };

    if (const char* e = getenv ("LWE_TIMESCALE"); e != nullptr && *e != '\0') {
	char* end = nullptr;
	const double v = strtod (e, &end);

	config.timescale.raw = e;

	if (end != e && v >= 0.0) {
	    config.timescale.value = static_cast<float> (v);
	    config.timescale.source = "env";
	}
    }

    config.deadman = { 300, "default", "" };

    if (const char* e = getenv ("LWE_DEADMAN"); e != nullptr && *e != '\0') {
	char* end = nullptr;
	const long v = strtol (e, &end, 10);

	config.deadman.raw = e;

	if (end != e && v >= 0 && v <= 86400) {
	    config.deadman.value = static_cast<int> (v);
	    config.deadman.source = "env";
	}
    }

    config.classicK = { lweEnvFloat ("LWE_CLASSICK", 16.0f, 0.01f, 1000.0f), "default", "" };

    if (const char* e = getenv ("LWE_CLASSICK"); e != nullptr) {
	config.classicK.source = "env";
	config.classicK.raw = e;
    }

    config.classicExp = { lweEnvFloat ("LWE_CLASSICEXP", 2.0f, 0.5f, 6.0f), "default", "" };

    if (const char* e = getenv ("LWE_CLASSICEXP"); e != nullptr) {
	config.classicExp.source = "env";
	config.classicExp.raw = e;
    }

    config.audioGain = { lweEnvFloat ("LWE_AUDIOGAIN", 1.0f, 0.1f, 20.0f), "default", "" };

    if (const char* e = getenv ("LWE_AUDIOGAIN"); e != nullptr) {
	config.audioGain.source = "env";
	config.audioGain.raw = e;
    }

    config.audioSmooth = { lweEnvFloat ("LWE_AUDIOSMOOTH", 90.0f, 0.0f, 500.0f), "default", "" };

    if (const char* e = getenv ("LWE_AUDIOSMOOTH"); e != nullptr) {
	config.audioSmooth.source = "env";
	config.audioSmooth.raw = e;
    }

    config.socket = flagStore.socket.value_or (config.socket);
    config.ssfactor = flagStore.ssfactor.value_or (config.ssfactor);
    config.clampComposites = flagStore.clampComposites.value_or (config.clampComposites);
    config.texcomp = flagStore.texcomp.value_or (config.texcomp);
    config.texdetailAuto = flagStore.texdetailAuto.value_or (config.texdetailAuto);
    config.hwdec = flagStore.hwdec.value_or (config.hwdec);
    config.cc = flagStore.cc.value_or (config.cc);
    config.timescale = flagStore.timescale.value_or (config.timescale);
    config.deadman = flagStore.deadman.value_or (config.deadman);
    config.classicK = flagStore.classicK.value_or (config.classicK);
    config.classicExp = flagStore.classicExp.value_or (config.classicExp);
    config.audioGain = flagStore.audioGain.value_or (config.audioGain);
    config.audioSmooth = flagStore.audioSmooth.value_or (config.audioSmooth);

    return config;
}

static Config& instance () {
    static Config config = fromEnvironment ();
    return config;
}

const Config& Config::get () { return instance (); }

void Config::reload () { instance () = fromEnvironment (); }

void applyConfigTuning () {
    const auto& config = Config::get ();

    g_LweClassicDivisor = config.classicK.value;
    g_LweFalloffExp = config.classicExp.value;
    g_LweAudioGain = config.audioGain.value;
    g_LweAudioSmoothMs = config.audioSmooth.value;
}

void Config::setFlags (const Flags& flags) {
    flagStore = flags;
    reload ();
    applyConfigTuning ();
}

void Config::clearFlags () {
    flagStore = {};
    reload ();
    applyConfigTuning ();
}
} // namespace WallpaperEngine::Application
