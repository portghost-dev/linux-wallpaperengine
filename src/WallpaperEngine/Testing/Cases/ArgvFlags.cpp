#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>

#include <cstdlib>
#include <filesystem>
#include <numbers>
#include <optional>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#include <glm/vec4.hpp>

#include "WallpaperEngine/Application/ApplicationContext.h"
#include "WallpaperEngine/Application/Config.h"

extern float g_LweClassicDivisor;
extern float g_LweFalloffExp;
extern float g_LweAudioGain;
extern float g_LweAudioSmoothMs;

using namespace WallpaperEngine::Application;
using Catch::Matchers::EndsWith;
using Catch::Matchers::StartsWith;

namespace {
struct FlagGuard {
    ~FlagGuard () { Config::clearFlags (); }
};

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

	Config::clearFlags ();
	Config::reload ();
    }
};

void loadArgv (const std::vector<std::string>& extra) {
    std::vector<std::string> arguments
	= { "linux-wallpaperengine", "--daemon", "--assets-dir", std::filesystem::temp_directory_path ().string () };
    arguments.insert (arguments.end (), extra.begin (), extra.end ());

    std::vector<char*> argv;

    for (auto& argument : arguments) {
	argv.push_back (argument.data ());
    }

    argv.push_back (nullptr);

    ApplicationContext context (static_cast<int> (arguments.size ()), argv.data ());
    context.loadSettingsFromArgv ();
}

std::string refusal (const std::vector<std::string>& extra) {
    try {
	loadArgv (extra);
    } catch (const std::runtime_error& error) {
	return error.what ();
    }

    return "";
}

template <typename T> struct ArgvRow {
    const char* flag;
    const char* text;
    Knob<T> Config::* knob;
    T value;
};

template <typename T> void checkArgvRow (const ArgvRow<T>& row) {
    FlagGuard guard;
    CAPTURE (row.flag, row.text);
    CHECK_NOTHROW (loadArgv ({ row.flag, row.text }));
    CHECK ((Config::get ().*row.knob).value == row.value);
    CHECK ((Config::get ().*row.knob).raw == row.text);
    CHECK ((Config::get ().*row.knob).source == "flag");
}
} // namespace

TEST_CASE ("each engine settings flag with a valid value stores its value and text with source flag", "[argv]") {
    const auto rows = std::make_tuple (
	ArgvRow<float> { "--resclamp", "2", &Config::ssfactor, 2.0f },
	ArgvRow<float> { "--effectclamp", "0.5", &Config::clampComposites, 0.5f },
	ArgvRow<bool> { "--texturecache", "off", &Config::texcomp, false },
	ArgvRow<bool> { "--texturedetail", "full", &Config::texdetailAuto, false },
	ArgvRow<std::string> { "--videodecode", "software", &Config::hwdec, "no" },
	ArgvRow<glm::vec4> { "--color", "1 1 1 180", &Config::cc,
			     glm::vec4 (1.0f, 1.0f, 1.0f, std::numbers::pi_v<float>) },
	ArgvRow<float> { "--speed", "2", &Config::timescale, 2.0f },
	ArgvRow<int> { "--watchdog", "10m", &Config::deadman, 600 },
	ArgvRow<float> { "--lightdimming", "3", &Config::classicK, 3.0f },
	ArgvRow<float> { "--lightfalloff", "4", &Config::classicExp, 4.0f },
	ArgvRow<float> { "--audiogain", "5", &Config::audioGain, 5.0f },
	ArgvRow<float> { "--audiosmoothing", "45ms", &Config::audioSmooth, 45.0f },
	ArgvRow<std::filesystem::path> { "--socket", "/tmp/lwe-argv-flag.sock", &Config::socket,
					 "/tmp/lwe-argv-flag.sock" }
    );

    std::apply ([] (const auto&... row) { (checkArgvRow (row), ...); }, rows);
}

TEST_CASE ("the clamp flags read 0 or below as no cap through the parse", "[argv]") {
    FlagGuard guard;

    CHECK_NOTHROW (loadArgv ({ "--resclamp", "-1", "--effectclamp", "-0.5" }));
    CHECK (Config::get ().ssfactor.value == 0.0f);
    CHECK (Config::get ().ssfactor.raw == "-1");
    CHECK (Config::get ().ssfactor.source == "flag");
    CHECK (Config::get ().clampComposites.value == 0.0f);
    CHECK (Config::get ().clampComposites.raw == "-0.5");
    CHECK (Config::get ().clampComposites.source == "flag");
}

TEST_CASE ("a refused engine settings value stops the launch, naming the flag and pointing at --help", "[argv]") {
    const std::vector<std::pair<std::string, std::string>> rows = {
	{ "--resclamp", "5" },
	{ "--effectclamp", "5" },
	{ "--texturecache", "toggle" },
	{ "--texturedetail", "high" },
	{ "--videodecode", "vaapi" },
	{ "--color", "1 1 1" },
	{ "--speed", "11" },
	{ "--watchdog", "25h" },
	{ "--lightdimming", "0" },
	{ "--lightfalloff", "7" },
	{ "--audiogain", "0" },
	{ "--audiosmoothing", "501" },
	{ "--socket", "" },
    };

    for (const auto& [flag, text] : rows) {
	FlagGuard guard;
	CAPTURE (flag, text);
	const std::string message = refusal ({ flag, text });
	CHECK_THAT (message, StartsWith (flag + " "));
	CHECK_THAT (message, EndsWith (". Use linux-wallpaperengine --help for more information"));
    }
}

TEST_CASE ("the four tuning flags reach the tuning globals through the parse", "[argv]") {
    FlagGuard guard;
    EnvGuard dimming ("LWE_CLASSICK");
    EnvGuard falloff ("LWE_CLASSICEXP");
    EnvGuard gain ("LWE_AUDIOGAIN");
    EnvGuard smoothing ("LWE_AUDIOSMOOTH");

    loadArgv ({ "--lightdimming", "3", "--lightfalloff", "4", "--audiogain", "5", "--audiosmoothing", "45ms" });
    CHECK (g_LweClassicDivisor == 3.0f);
    CHECK (g_LweFalloffExp == 4.0f);
    CHECK (g_LweAudioGain == 5.0f);
    CHECK (g_LweAudioSmoothMs == 45.0f);

    Config::clearFlags ();
    CHECK (g_LweClassicDivisor == 16.0f);
    CHECK (g_LweFalloffExp == 2.0f);
    CHECK (g_LweAudioGain == 1.0f);
    CHECK (g_LweAudioSmoothMs == 90.0f);
}
