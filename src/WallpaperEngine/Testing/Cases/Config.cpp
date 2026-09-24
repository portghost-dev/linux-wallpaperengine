#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <optional>
#include <string>
#include <unistd.h>

#include <glm/vec4.hpp>

#include "WallpaperEngine/Application/Config.h"

using namespace WallpaperEngine::Application;

namespace {
struct EnvGuard {
    const char* name;
    std::optional<std::string> inherited;

    explicit EnvGuard (const char* n) : name (n) {
	if (const char* v = getenv (n); v != nullptr) {
	    inherited = v;
	}

	unsetenv (n);
	Config::reload ();
    }

    ~EnvGuard () {
	if (inherited.has_value ()) {
	    setenv (name, inherited->c_str (), 1);
	} else {
	    unsetenv (name);
	}

	Config::reload ();
    }

    void set (const char* v) const {
	setenv (name, v, 1);
	Config::reload ();
    }
};
} // namespace

TEST_CASE ("Config resolves LWE_SOCKET: the override, else the runtime dir, else /tmp", "[config]") {
    EnvGuard env ("LWE_SOCKET");
    EnvGuard runtime ("XDG_RUNTIME_DIR");

    runtime.set ("/run/user/testing");
    CHECK (Config::get ().socket.value == std::filesystem::path ("/run/user/testing/lwe/engine.sock"));
    CHECK (Config::get ().socket.source == "default");

    env.set ("/tmp/lwe-config-probe.sock");
    CHECK (Config::get ().socket.value == std::filesystem::path ("/tmp/lwe-config-probe.sock"));
    CHECK (Config::get ().socket.source == "env");

    env.set ("");
    CHECK (Config::get ().socket.value == std::filesystem::path ("/run/user/testing/lwe/engine.sock"));
    CHECK (Config::get ().socket.source == "default");

    const auto fallback = std::filesystem::path ("/tmp") / ("lwe-" + std::to_string (geteuid ())) / "engine.sock";

    runtime.set ("");
    CHECK (Config::get ().socket.value == fallback);
    CHECK (Config::get ().socket.source == "default");
}

TEST_CASE ("Config reads LWE_SSFACTOR: a number at most 4, 0 or below off", "[config]") {
    EnvGuard env ("LWE_SSFACTOR");
    CHECK (Config::get ().ssfactor.value == 1.0f);
    CHECK (Config::get ().ssfactor.source == "default");

    env.set ("2.5");
    CHECK (Config::get ().ssfactor.value == 2.5f);
    CHECK (Config::get ().ssfactor.source == "env");
    CHECK (Config::get ().ssfactor.raw == "2.5");

    env.set ("");
    CHECK (Config::get ().ssfactor.value == 1.0f);
    CHECK (Config::get ().ssfactor.source == "default");

    const char* sample = "abc";
    float parsed = static_cast<float> (atof (sample));

    if (std::isnan (parsed)) {
	parsed = 1.0f;
    } else if (parsed > 4.0f) {
	parsed = 4.0f;
    }

    env.set (sample);
    CHECK (Config::get ().ssfactor.value == (parsed <= 0.0f ? 0.0f : parsed));
    CHECK (Config::get ().ssfactor.source == "env");

    env.set ("-2");
    CHECK (Config::get ().ssfactor.value == 0.0f);

    env.set ("nan");
    CHECK (Config::get ().ssfactor.source == "default");
}

TEST_CASE ("Config reads LWE_CLAMPCOMPOSITES: a number at most 4, nan read as unset", "[config]") {
    EnvGuard env ("LWE_CLAMPCOMPOSITES");
    CHECK (Config::get ().clampComposites.value == 1.0f);
    CHECK (Config::get ().clampComposites.source == "default");

    env.set ("0.5");
    CHECK (Config::get ().clampComposites.value == 0.5f);
    CHECK (Config::get ().clampComposites.source == "env");
    CHECK (Config::get ().clampComposites.raw == "0.5");

    env.set ("");
    CHECK (Config::get ().clampComposites.value == 1.0f);
    CHECK (Config::get ().clampComposites.source == "default");

    const char* sample = "nan";
    float parsed = static_cast<float> (atof (sample));

    if (std::isnan (parsed)) {
	parsed = 1.0f;
    } else if (parsed > 4.0f) {
	parsed = 4.0f;
    }

    env.set (sample);
    CHECK (Config::get ().clampComposites.value == (parsed <= 0.0f ? 0.0f : parsed));
    CHECK (Config::get ().clampComposites.source == "default");

    env.set ("-1");
    CHECK (Config::get ().clampComposites.value == 0.0f);
}

TEST_CASE ("Config reads LWE_TEXCOMP: anything but 0 is on", "[config]") {
    EnvGuard env ("LWE_TEXCOMP");
    CHECK (Config::get ().texcomp.value);
    CHECK (Config::get ().texcomp.source == "default");

    env.set ("0");
    CHECK_FALSE (Config::get ().texcomp.value);
    CHECK (Config::get ().texcomp.source == "env");

    env.set ("");
    CHECK (Config::get ().texcomp.value);
    CHECK (Config::get ().texcomp.source == "env");

    const char* sample = "no";

    env.set (sample);
    CHECK (Config::get ().texcomp.value == (std::string (sample) != "0"));
    CHECK (Config::get ().texcomp.source == "env");
}

TEST_CASE ("Config reads LWE_TEXDETAIL: only auto is auto once set", "[config]") {
    EnvGuard env ("LWE_TEXDETAIL");
    CHECK (Config::get ().texdetailAuto.value);
    CHECK (Config::get ().texdetailAuto.source == "default");

    env.set ("full");
    CHECK_FALSE (Config::get ().texdetailAuto.value);
    CHECK (Config::get ().texdetailAuto.source == "env");

    env.set ("");
    CHECK_FALSE (Config::get ().texdetailAuto.value);
    CHECK (Config::get ().texdetailAuto.source == "env");

    const char* sample = "Auto";

    env.set (sample);
    CHECK (Config::get ().texdetailAuto.value == (std::string (sample) == "auto"));
    CHECK (Config::get ().texdetailAuto.source == "env");
}

TEST_CASE ("Config reads LWE_HWDEC: any non-empty string, else no", "[config]") {
    EnvGuard env ("LWE_HWDEC");
    CHECK (Config::get ().hwdec.value == "no");
    CHECK (Config::get ().hwdec.source == "default");

    env.set ("vaapi");
    CHECK (Config::get ().hwdec.value == "vaapi");
    CHECK (Config::get ().hwdec.source == "env");

    env.set ("");
    CHECK (Config::get ().hwdec.value == "no");
    CHECK (Config::get ().hwdec.source == "default");

    const char* sample = " ";

    env.set (sample);
    CHECK (Config::get ().hwdec.value == std::string ((sample != nullptr && *sample) ? sample : "no"));
    CHECK (Config::get ().hwdec.source == "env");
}

TEST_CASE ("Config reads LWE_CC: four floats, else identity", "[config]") {
    const glm::vec4 identity = { 1.0f, 1.0f, 1.0f, 0.0f };
    EnvGuard env ("LWE_CC");
    CHECK (Config::get ().cc.value == identity);
    CHECK (Config::get ().cc.source == "default");

    env.set ("1.5 0.5 2 0.25");
    CHECK (Config::get ().cc.value == glm::vec4 (1.5f, 0.5f, 2.0f, 0.25f));
    CHECK (Config::get ().cc.source == "env");
    CHECK (Config::get ().cc.raw == "1.5 0.5 2 0.25");

    env.set ("");
    CHECK (Config::get ().cc.value == identity);
    CHECK (Config::get ().cc.source == "default");

    const char* sample = "1 2 x 4";
    glm::vec4 expected = identity;
    glm::vec4 parsed = identity;

    if (sscanf (sample, "%f %f %f %f", &parsed.x, &parsed.y, &parsed.z, &parsed.w) == 4) {
	expected = parsed;
    }

    env.set (sample);
    CHECK (Config::get ().cc.value == expected);
    CHECK (Config::get ().cc.source == "default");

    env.set ("1 2 3");
    CHECK (Config::get ().cc.value == identity);
}

TEST_CASE ("Config reads LWE_TIMESCALE: a number at or above 0, else 1", "[config]") {
    EnvGuard env ("LWE_TIMESCALE");
    CHECK (Config::get ().timescale.value == 1.0f);
    CHECK (Config::get ().timescale.source == "default");

    env.set ("2.5");
    CHECK (Config::get ().timescale.value == 2.5f);
    CHECK (Config::get ().timescale.source == "env");
    CHECK (Config::get ().timescale.raw == "2.5");

    env.set ("");
    CHECK (Config::get ().timescale.value == 1.0f);
    CHECK (Config::get ().timescale.source == "default");

    const char* sample = "-0.5";
    char* end = nullptr;
    const double v = strtod (sample, &end);

    env.set (sample);
    CHECK (Config::get ().timescale.value == (end != sample && v >= 0.0 ? static_cast<float> (v) : 1.0f));
    CHECK (Config::get ().timescale.source == "default");

    env.set ("abc");
    CHECK (Config::get ().timescale.value == 1.0f);
}

TEST_CASE ("Config reads LWE_DEADMAN: 0 to 86400 seconds, else 300", "[config]") {
    EnvGuard env ("LWE_DEADMAN");
    CHECK (Config::get ().deadman.value == 300);
    CHECK (Config::get ().deadman.source == "default");

    env.set ("60");
    CHECK (Config::get ().deadman.value == 60);
    CHECK (Config::get ().deadman.source == "env");

    env.set ("");
    CHECK (Config::get ().deadman.value == 300);
    CHECK (Config::get ().deadman.source == "default");

    const char* sample = "12abc";
    char* end = nullptr;
    const long v = strtol (sample, &end, 10);

    env.set (sample);
    CHECK (Config::get ().deadman.value == (end != sample && v >= 0 && v <= 86400 ? static_cast<int> (v) : 300));
    CHECK (Config::get ().deadman.source == "env");

    env.set ("abc");
    CHECK (Config::get ().deadman.value == 300);

    env.set ("86401");
    CHECK (Config::get ().deadman.value == 300);
}

TEST_CASE ("Config reads LWE_CLASSICK: any set value through atof, clamped 0.01 to 1000", "[config]") {
    EnvGuard env ("LWE_CLASSICK");
    CHECK (Config::get ().classicK.value == 16.0f);
    CHECK (Config::get ().classicK.source == "default");

    env.set ("32");
    CHECK (Config::get ().classicK.value == 32.0f);
    CHECK (Config::get ().classicK.source == "env");

    env.set ("");
    CHECK (Config::get ().classicK.value == 0.01f);
    CHECK (Config::get ().classicK.source == "env");

    const char* sample = "12x";

    env.set (sample);
    CHECK (Config::get ().classicK.value == std::clamp (static_cast<float> (atof (sample)), 0.01f, 1000.0f));
    CHECK (Config::get ().classicK.source == "env");
}

TEST_CASE ("Config reads LWE_CLASSICEXP: any set value through atof, clamped 0.5 to 6", "[config]") {
    EnvGuard env ("LWE_CLASSICEXP");
    CHECK (Config::get ().classicExp.value == 2.0f);
    CHECK (Config::get ().classicExp.source == "default");

    env.set ("3");
    CHECK (Config::get ().classicExp.value == 3.0f);
    CHECK (Config::get ().classicExp.source == "env");

    env.set ("");
    CHECK (Config::get ().classicExp.value == 0.5f);
    CHECK (Config::get ().classicExp.source == "env");

    const char* sample = "99";

    env.set (sample);
    CHECK (Config::get ().classicExp.value == std::clamp (static_cast<float> (atof (sample)), 0.5f, 6.0f));
    CHECK (Config::get ().classicExp.source == "env");
}

TEST_CASE ("Config reads LWE_AUDIOGAIN: any set value through atof, clamped 0.1 to 20", "[config]") {
    EnvGuard env ("LWE_AUDIOGAIN");
    CHECK (Config::get ().audioGain.value == 1.0f);
    CHECK (Config::get ().audioGain.source == "default");

    env.set ("2.5");
    CHECK (Config::get ().audioGain.value == 2.5f);
    CHECK (Config::get ().audioGain.source == "env");

    env.set ("");
    CHECK (Config::get ().audioGain.value == 0.1f);
    CHECK (Config::get ().audioGain.source == "env");

    const char* sample = "nan";
    const float parsed = std::clamp (static_cast<float> (atof (sample)), 0.1f, 20.0f);

    env.set (sample);
    CHECK (std::isnan (parsed));
    CHECK (std::isnan (Config::get ().audioGain.value));
    CHECK (Config::get ().audioGain.source == "env");
}

TEST_CASE ("Config reads LWE_AUDIOSMOOTH: any set value through atof, clamped 0 to 500", "[config]") {
    EnvGuard env ("LWE_AUDIOSMOOTH");
    CHECK (Config::get ().audioSmooth.value == 90.0f);
    CHECK (Config::get ().audioSmooth.source == "default");

    env.set ("120");
    CHECK (Config::get ().audioSmooth.value == 120.0f);
    CHECK (Config::get ().audioSmooth.source == "env");

    env.set ("");
    CHECK (Config::get ().audioSmooth.value == 0.0f);
    CHECK (Config::get ().audioSmooth.source == "env");

    const char* sample = "-5";

    env.set (sample);
    CHECK (Config::get ().audioSmooth.value == std::clamp (static_cast<float> (atof (sample)), 0.0f, 500.0f));
    CHECK (Config::get ().audioSmooth.source == "env");
}
