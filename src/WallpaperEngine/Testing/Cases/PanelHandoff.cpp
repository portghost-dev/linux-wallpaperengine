#include <catch2/catch_test_macros.hpp>

#include <string>
#include <vector>

#include "WallpaperEngine/Application/PanelHandoff.h"

using namespace WallpaperEngine::Application;

TEST_CASE ("the panel launcher is looked for beside the lwe name, on PATH, then where install.sh puts it", "[lwe]") {
    std::vector<std::string> asked;
    const auto nothing = [&asked] (const std::string& candidate) {
	asked.push_back (candidate);
	return false;
    };

    SECTION ("every place, in order") {
	CHECK_FALSE (
	    PanelHandoff::findPanelLauncher ("/opt/lwe/bin/lwe", "/usr/local/bin:bin::/usr/bin", "/home/user", nothing)
		.has_value ()
	);
	CHECK (
	    asked
	    == std::vector<std::string> { "/opt/lwe/bin/lwe-ui", "/usr/local/bin/lwe-ui", "/usr/bin/lwe-ui",
					  "/home/user/.local/bin/lwe-ui",
					  "/home/user/.local/share/lwe-ui/venv/bin/lwe-ui" }
	);
    }

    SECTION ("a name without a slash is not looked for beside itself") {
	CHECK_FALSE (PanelHandoff::findPanelLauncher ("lwe", "/usr/bin", "/home/user", nothing).has_value ());
	CHECK (
	    asked
	    == std::vector<std::string> { "/usr/bin/lwe-ui", "/home/user/.local/bin/lwe-ui",
					  "/home/user/.local/share/lwe-ui/venv/bin/lwe-ui" }
	);
    }

    SECTION ("a relative name is looked for beside itself as typed") {
	CHECK_FALSE (PanelHandoff::findPanelLauncher ("bin/lwe", "", "", nothing).has_value ());
	CHECK (asked == std::vector<std::string> { "bin/lwe-ui" });
    }

    SECTION ("the first executable wins") {
	const auto onPath = [&asked] (const std::string& candidate) {
	    asked.push_back (candidate);
	    return candidate == "/usr/bin/lwe-ui";
	};

	CHECK (
	    PanelHandoff::findPanelLauncher ("/opt/lwe/bin/lwe", "/usr/local/bin:/usr/bin", "/home/user", onPath)
		.value_or ("")
	    == "/usr/bin/lwe-ui"
	);
	CHECK (asked == std::vector<std::string> { "/opt/lwe/bin/lwe-ui", "/usr/local/bin/lwe-ui", "/usr/bin/lwe-ui" });
    }

    SECTION ("no PATH and no HOME leave nothing to look at") {
	CHECK_FALSE (PanelHandoff::findPanelLauncher ("lwe", "", "", nothing).has_value ());
	CHECK (asked.empty ());
    }
}
