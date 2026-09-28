#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
#include <numbers>
#include <optional>
#include <stdexcept>
#include <string>
#include <tuple>
#include <unistd.h>
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

using Settings = decltype (ApplicationContext::settings);

Settings parseArgv (const std::vector<std::string>& extra) {
    std::vector<std::string> arguments = { "linux-wallpaperengine" };
    arguments.insert (arguments.end (), extra.begin (), extra.end ());

    std::vector<char*> argv;

    for (auto& argument : arguments) {
	argv.push_back (argument.data ());
    }

    argv.push_back (nullptr);

    ApplicationContext context (static_cast<int> (arguments.size ()), argv.data ());
    context.loadSettingsFromArgv ();
    return context.settings;
}

struct TempTree {
    std::filesystem::path root;

    ~TempTree () {
	std::error_code ignored;
	std::filesystem::remove_all (root, ignored);
    }
};
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

TEST_CASE ("every added flag name has the same effect as the name it joins", "[argv]") {
    const std::string assets = std::filesystem::temp_directory_path ().string ();
    const std::string wallpaper = "/tmp/lwe-argv-wallpaper";

    struct AliasRow {
	std::vector<std::string> existing;
	std::vector<std::string> added;
	std::function<void (const Settings&)> check;
    };

    const std::vector<AliasRow> rows = {
	{ { "--daemon", "--assets-dir", assets, "--screen-root", "HDMI-A-1", "--bg", wallpaper },
	  { "--daemon", "--assets-dir", assets, "--screen", "HDMI-A-1", "--wallpaper", wallpaper },
	  [&wallpaper] (const Settings& settings) {
	      CHECK (settings.general.screenBackgrounds.at ("HDMI-A-1") == std::filesystem::path (wallpaper));
	  } },
	{ { "--daemon", "--assets-dir", assets, "--screen-span", "DP-1,DP-2", "--bg", wallpaper },
	  { "--daemon", "--assets-dir", assets, "--span", "DP-1,DP-2", "--wallpaper", wallpaper },
	  [&wallpaper] (const Settings& settings) {
	      REQUIRE (settings.general.spanGroups.size () == 1);
	      CHECK (settings.general.spanGroups.front ().screens == std::vector<std::string> { "DP-1", "DP-2" });
	      CHECK (settings.general.spanGroups.front ().background == std::filesystem::path (wallpaper));
	  } },
	{ { "--daemon", "--assets-dir", assets, "--screen-root", "HDMI-A-1", "--clamp", "border" },
	  { "--daemon", "--assets-dir", assets, "--screen-root", "HDMI-A-1", "--edge", "border" },
	  [] (const Settings& settings) {
	      CHECK (settings.general.screenClamps.at ("HDMI-A-1") == TextureFlags_ClampUVsBorder);
	  } },
	{ { "--daemon", "--assets-dir", assets, "--fullscreen-pause-only-active" },
	  { "--daemon", "--assets-dir", assets, "--fullscreen-active-only" },
	  [] (const Settings& settings) { CHECK (settings.render.pauseOnFullscreenOnlyWhenActive); } },
	{ { "--daemon", "--assets-dir", assets, "--fullscreen-pause-ignore-appid", "firefox" },
	  { "--daemon", "--assets-dir", assets, "--fullscreen-ignore", "firefox" },
	  [] (const Settings& settings) {
	      CHECK (settings.render.fullscreenPauseIgnoreAppIds == std::vector<std::string> { "firefox" });
	  } },
	{ { "--daemon", "--assets-dir", assets, "--silent" },
	  { "--daemon", "--assets-dir", assets, "--mute" },
	  [] (const Settings& settings) { CHECK_FALSE (settings.audio.enabled); } },
	{ { "--daemon", "--assets-dir", assets, "--noautomute" },
	  { "--daemon", "--assets-dir", assets, "--no-automute" },
	  [] (const Settings& settings) { CHECK_FALSE (settings.audio.automute); } },
	{ { "--daemon", "--assets-dir", assets, "--no-audio-processing" },
	  { "--daemon", "--assets-dir", assets, "--no-audioreactive" },
	  [] (const Settings& settings) { CHECK_FALSE (settings.audio.audioprocessing); } },
	{ { "--assets-dir", assets, wallpaper, "--api-socket" },
	  { "--assets-dir", assets, wallpaper, "--listen" },
	  [] (const Settings& settings) { CHECK (settings.general.apiSocket); } },
	{ { "--daemon", "--assets-dir", "/tmp/lwe-argv-assets" },
	  { "--daemon", "--assetsfolder", "/tmp/lwe-argv-assets" },
	  [] (const Settings& settings) {
	      CHECK (settings.general.assets == std::filesystem::path ("/tmp/lwe-argv-assets"));
	  } },
	{ { "--daemon", "--assets-dir", assets, "--disable-particles" },
	  { "--daemon", "--assets-dir", assets, "--no-particles" },
	  [] (const Settings& settings) { CHECK (settings.general.disableParticles); } },
	{ { "--daemon", "--assets-dir", assets, "--disable-mouse" },
	  { "--daemon", "--assets-dir", assets, "--no-mouse" },
	  [] (const Settings& settings) { CHECK_FALSE (settings.mouse.enabled); } },
	{ { "--daemon", "--assets-dir", assets, "--disable-parallax" },
	  { "--daemon", "--assets-dir", assets, "--no-parallax" },
	  [] (const Settings& settings) { CHECK (settings.mouse.disableparallax); } },
    };

    for (const auto& row : rows) {
	CAPTURE (row.existing, row.added);
	row.check (parseArgv (row.existing));
	row.check (parseArgv (row.added));
    }
}

TEST_CASE ("--steamplaylist loads a config.json playlist the way --playlist does", "[argv]") {
    const std::string assets = std::filesystem::temp_directory_path ().string ();
    const TempTree home { std::filesystem::temp_directory_path () / ("lwe-argv-home-" + std::to_string (getpid ())) };
    const auto item = home.root / "item";
    const auto wallpaperEngine = home.root / ".steam/steam/steamapps/common/wallpaper_engine";

    std::filesystem::create_directories (wallpaperEngine);
    std::filesystem::create_directories (item);
    std::ofstream (wallpaperEngine / "config.json")
	<< R"({"steamuser": {"general": {"playlists": [{"name": "argvlist", "items": [")" << item.string ()
	<< R"("]}]}}})";

    EnvGuard homeVariable ("HOME");
    setenv ("HOME", home.root.c_str (), 1);

    for (const char* name : { "--playlist", "--steamplaylist" }) {
	CAPTURE (name);
	const auto settings = parseArgv ({ "--assets-dir", assets, name, "argvlist" });
	REQUIRE (settings.general.defaultPlaylist.has_value ());
	CHECK (settings.general.defaultPlaylist->name == "argvlist");
	CHECK (settings.general.defaultPlaylist->items == std::vector<std::filesystem::path> { item });
	CHECK (settings.general.defaultBackground == item);
    }
}

TEST_CASE ("--edge and --clamp take the edge words and the old clamp words", "[argv]") {
    const std::string assets = std::filesystem::temp_directory_path ().string ();
    const std::vector<std::pair<std::string, TextureFlags>> words = {
	{ "extend", TextureFlags_ClampUVs },       { "blank", TextureFlags_ClampUVsBorder },
	{ "tile", TextureFlags_NoFlags },          { "clamp", TextureFlags_ClampUVs },
	{ "border", TextureFlags_ClampUVsBorder }, { "repeat", TextureFlags_NoFlags },
    };

    for (const auto& [word, expected] : words) {
	for (const std::string name : { "--edge", "--clamp" }) {
	    CAPTURE (name, word);
	    CHECK (parseArgv ({ "--daemon", "--assets-dir", assets, name, word }).render.window.clamp == expected);
	}
    }

    const auto screen = parseArgv ({ "--daemon", "--assets-dir", assets, "--screen", "HDMI-A-1", "--edge", "blank" });
    CHECK (screen.general.screenClamps.at ("HDMI-A-1") == TextureFlags_ClampUVsBorder);
}

TEST_CASE (
    "--fullscreen sets the policy after the parse and refuses an empty word or --no-fullscreen-pause", "[argv]"
) {
    const std::string assets = std::filesystem::temp_directory_path ().string ();

    CHECK (
	parseArgv ({ "--assets-dir", assets, "--fullscreen", "pause", "--daemon" }).render.fullscreenBehavior
	== FullscreenBehavior::Pause
    );
    CHECK (
	parseArgv ({ "--assets-dir", assets, "--daemon", "--fullscreen", "pause" }).render.fullscreenBehavior
	== FullscreenBehavior::Pause
    );

    const auto keep = parseArgv ({ "--daemon", "--assets-dir", assets, "--fullscreen", "keep" });
    CHECK (keep.render.fullscreenBehavior == FullscreenBehavior::Off);
    CHECK_FALSE (keep.render.pauseOnFullscreen);

    const auto stop = parseArgv ({ "--assets-dir", assets, "/tmp/lwe-argv-wallpaper", "--fullscreen", "stop" });
    CHECK (stop.render.fullscreenBehavior == FullscreenBehavior::Stop);
    CHECK (stop.render.pauseOnFullscreen);

    const std::string message = refusal ({ "--fullscreen", "stop", "--no-fullscreen-pause" });
    CHECK_THAT (message, StartsWith ("--fullscreen and --no-fullscreen-pause cannot be used together"));
    CHECK_THAT (message, EndsWith (". Use linux-wallpaperengine --help for more information"));

    CHECK (
	refusal ({ "--fullscreen" })
	== "--fullscreen takes keep, pause or stop; got an empty value. Use linux-wallpaperengine --help for more "
	   "information"
    );
    CHECK (
	refusal ({ "--no-fullscreen-pause", "--fullscreen" })
	== "--fullscreen takes keep, pause or stop; got an empty value. Use linux-wallpaperengine --help for more "
	   "information"
    );
}

TEST_CASE ("--debug sets or clears a debugging variable while the arguments are parsed", "[argv]") {
    EnvGuard audit ("LWE_AUDIT");
    EnvGuard bloom ("LWE_NOBLOOM");

    SECTION ("audit=on sets LWE_AUDIT to 1") {
	loadArgv ({ "--debug", "audit=on" });
	REQUIRE (getenv ("LWE_AUDIT") != nullptr);
	CHECK (std::string (getenv ("LWE_AUDIT")) == "1");
    }

    SECTION ("audit=off removes an inherited LWE_AUDIT") {
	setenv ("LWE_AUDIT", "1", 1);
	loadArgv ({ "--debug", "audit=off" });
	CHECK (getenv ("LWE_AUDIT") == nullptr);
    }

    SECTION ("bloom=off sets LWE_NOBLOOM to 1") {
	loadArgv ({ "--debug", "bloom=off" });
	REQUIRE (getenv ("LWE_NOBLOOM") != nullptr);
	CHECK (std::string (getenv ("LWE_NOBLOOM")) == "1");
    }

    SECTION ("two --debug in one argv both apply") {
	loadArgv ({ "--debug", "audit=on", "--debug", "bloom=off" });
	REQUIRE (getenv ("LWE_AUDIT") != nullptr);
	REQUIRE (getenv ("LWE_NOBLOOM") != nullptr);
	CHECK (std::string (getenv ("LWE_AUDIT")) == "1");
	CHECK (std::string (getenv ("LWE_NOBLOOM")) == "1");
    }
}

TEST_CASE ("--debug with an unknown switch stops the launch, pointing at --help-debug and --help", "[argv]") {
    CHECK (
	refusal ({ "--debug", "nosuch=on" })
	== "unknown debugging switch nosuch; --help-debug lists them. Use linux-wallpaperengine --help for more "
	   "information"
    );
}
