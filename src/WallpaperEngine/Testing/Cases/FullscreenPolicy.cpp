#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <string>
#include <vector>

#include "WallpaperEngine/Application/ApplicationContext.h"
#include "WallpaperEngine/Application/FullscreenPolicy.h"

using namespace WallpaperEngine::Application;

namespace {
const std::string WALLPAPER = "/tmp/lwe-fullscreen-wallpaper";

bool realDetectorFor (const std::vector<std::string>& extra) {
    std::vector<std::string> arguments
	= { "linux-wallpaperengine", "--assets-dir", std::filesystem::temp_directory_path ().string () };
    arguments.insert (arguments.end (), extra.begin (), extra.end ());

    std::vector<char*> argv;

    for (auto& argument : arguments) {
	argv.push_back (argument.data ());
    }

    argv.push_back (nullptr);

    ApplicationContext context (static_cast<int> (arguments.size ()), argv.data ());
    context.loadSettingsFromArgv ();
    return FullscreenPolicy::wantsRealDetector (context.settings);
}
} // namespace

TEST_CASE ("stop is in effect only where the driver can release its outputs; elsewhere it pauses", "[fullscreen]") {
    CHECK (FullscreenPolicy::inEffect (FullscreenBehavior::Stop, false) == FullscreenBehavior::Pause);
    CHECK (FullscreenPolicy::inEffect (FullscreenBehavior::Stop, true) == FullscreenBehavior::Stop);
    CHECK (FullscreenPolicy::inEffect (FullscreenBehavior::Pause, false) == FullscreenBehavior::Pause);
    CHECK (FullscreenPolicy::inEffect (FullscreenBehavior::Pause, true) == FullscreenBehavior::Pause);
    CHECK (FullscreenPolicy::inEffect (FullscreenBehavior::Off, false) == FullscreenBehavior::Off);
    CHECK (FullscreenPolicy::inEffect (FullscreenBehavior::Off, true) == FullscreenBehavior::Off);
}

TEST_CASE ("keep and --no-fullscreen-pause build the stub detector only without the command socket", "[fullscreen]") {
    CHECK (realDetectorFor ({ "--listen", "--fullscreen", "keep", WALLPAPER }));
    CHECK (realDetectorFor ({ "--api-socket", "--fullscreen", "keep", WALLPAPER }));
    CHECK (realDetectorFor ({ "--listen", "--no-fullscreen-pause", WALLPAPER }));
    CHECK (realDetectorFor ({ "--daemon", "--fullscreen", "keep" }));
    CHECK (realDetectorFor ({ "--daemon", "--no-fullscreen-pause" }));
    CHECK (realDetectorFor ({ "--listen", WALLPAPER }));

    CHECK_FALSE (realDetectorFor ({ "--fullscreen", "keep", WALLPAPER }));
    CHECK_FALSE (realDetectorFor ({ "--no-fullscreen-pause", WALLPAPER }));
    CHECK (realDetectorFor ({ WALLPAPER }));
    CHECK (realDetectorFor ({ "--fullscreen", "pause", WALLPAPER }));
    CHECK (realDetectorFor ({ "--fullscreen", "stop", WALLPAPER }));
}
