#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <fstream>
#include <string>
#include <unistd.h>

#include "WallpaperEngine/Assets/AssetLoadException.h"
#include "WallpaperEngine/Assets/AssetLocator.h"

using namespace WallpaperEngine::Assets;

namespace {
void write (const std::filesystem::path& path, const std::string& text) {
    std::filesystem::create_directories (path.parent_path ());
    std::ofstream (path) << text;
}

struct WorkingFolder {
    explicit WorkingFolder (const std::filesystem::path& folder) : previous (std::filesystem::current_path ()) {
	std::filesystem::current_path (folder);
    }
    ~WorkingFolder () { std::filesystem::current_path (this->previous); }
    const std::filesystem::path previous;
};
} // namespace

TEST_CASE ("the web locator serves the wallpaper and the assets, never the working folder", "[weblocator]") {
    const auto base
	= std::filesystem::temp_directory_path () / ("lwe-web-locator-test-" + std::to_string (::getpid ()));
    std::filesystem::remove_all (base);
    write (base / "home" / ".probe", "private");
    write (base / "wallpaper" / "index.html", "<html>");
    write (base / "assets" / "shaders" / "common.h", "asset");

    {
	const WorkingFolder cwd (base / "home");
	REQUIRE (std::filesystem::exists (std::filesystem::current_path () / ".probe"));

	const auto locator = setupWebAssetLocator ((base / "wallpaper").string (), base / "assets");

	CHECK_THROWS_AS (locator->read ("/.probe"), AssetLoadException);
	CHECK_THROWS_AS (locator->read (".probe"), AssetLoadException);
	CHECK (locator->readString ("index.html") == "<html>");
	CHECK (locator->readString ("shaders/common.h") == "asset");
    }

    std::filesystem::remove_all (base);
}
