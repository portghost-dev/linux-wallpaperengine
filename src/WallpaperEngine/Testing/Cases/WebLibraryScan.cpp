#include <catch2/catch_test_macros.hpp>

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <optional>
#include <sstream>
#include <string>
#include <unistd.h>
#include <vector>

#include "WallpaperEngine/Application/WallpaperApplication.h"
#include "WallpaperEngine/Logging/Log.h"

using namespace WallpaperEngine::Application;

namespace {
struct EnvGuard {
    EnvGuard (const char* variable, const std::string& value) : name (variable) {
	if (const char* current = getenv (variable); current != nullptr) {
	    this->previous = current;
	}

	setenv (variable, value.c_str (), 1);
    }

    ~EnvGuard () {
	if (this->previous.has_value ()) {
	    setenv (this->name, this->previous->c_str (), 1);
	} else {
	    unsetenv (this->name);
	}
    }

    const char* name;
    std::optional<std::string> previous;
};

void write (const std::filesystem::path& path, const std::string& text) {
    std::filesystem::create_directories (path.parent_path ());
    std::ofstream (path) << text;
}
} // namespace

TEST_CASE ("a malformed project.json makes only that wallpaper unavailable", "[weblibrary]") {
    const auto base
	= std::filesystem::temp_directory_path () / ("lwe-web-library-test-" + std::to_string (::getpid ()));
    std::filesystem::remove_all (base);
    const auto library = base / "data" / "lwe" / "wallpapers";
    write (library / "broken" / "project.json", "{");
    write (library / "good" / "project.json", R"({"type": "web", "file": "index.html", "workshopid": "4242"})");
    std::filesystem::create_directories (base / "home");

    auto* errors = new std::ostringstream ();
    sLog.addError (errors);

    std::vector<WallpaperApplication::WebLibraryEntry> found;

    {
	const EnvGuard data ("XDG_DATA_HOME", (base / "data").string ());
	const EnvGuard home ("HOME", (base / "home").string ());
	REQUIRE_NOTHROW (found = WallpaperApplication::enumerateWebBackgrounds ());
    }

    const std::string lines = errors->str ();
    errors->setstate (std::ios::badbit);

    REQUIRE (found.size () == 1);
    CHECK (found[0].workshopId == "4242");
    CHECK (found[0].path == library / "good");

    size_t mentions = 0;
    std::istringstream in (lines);

    for (std::string line; std::getline (in, line);) {
	if (line.find ((library / "broken").string ()) != std::string::npos) {
	    mentions++;
	    CHECK (line.find ("parse error") != std::string::npos);
	}
    }

    CHECK (mentions == 1);
    std::filesystem::remove_all (base);
}
