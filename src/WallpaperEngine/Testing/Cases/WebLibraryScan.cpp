#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <optional>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>
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

struct RemoveOnExit {
    explicit RemoveOnExit (std::filesystem::path folder) : path (std::move (folder)) { }
    ~RemoveOnExit () {
	std::error_code error;
	std::filesystem::remove_all (this->path, error);
    }
    const std::filesystem::path path;
};
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

TEST_CASE (
    "project.json entries that are not readable regular files are skipped without stopping the boot", "[weblibrary]"
) {
    const auto base
	= std::filesystem::temp_directory_path () / ("lwe-web-badfiles-test-" + std::to_string (::getpid ()));
    std::filesystem::remove_all (base);
    const RemoveOnExit cleanup (base);
    const auto library = base / "data" / "lwe" / "wallpapers";
    std::filesystem::create_directories (base / "home");

    write (library / "good" / "project.json", R"({"type": "web", "workshopid": "4242"})");

    // a symlink to a regular file inside the folder is fine (status resolves it, not symlink_status)
    write (library / "slink" / "real.json", R"({"type": "web", "workshopid": "7777"})");
    std::filesystem::create_symlink ("real.json", library / "slink" / "project.json");

    std::filesystem::create_directories (library / "dir" / "project.json");

    std::filesystem::create_directories (library / "fifo");
    REQUIRE (::mkfifo ((library / "fifo" / "project.json").c_str (), 0644) == 0);

    std::filesystem::create_directories (library / "devlink");
    std::filesystem::create_symlink ("/dev/zero", library / "devlink" / "project.json");

    std::filesystem::create_directories (library / "big");
    { std::ofstream (library / "big" / "project.json"); }
    std::filesystem::resize_file (library / "big" / "project.json", 5 * 1024 * 1024);

    std::filesystem::create_directories (library / "memlink");
    std::filesystem::create_symlink ("/proc/self/mem", library / "memlink" / "project.json");

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

    std::vector<std::string> ids;
    for (const auto& entry : found) {
	ids.push_back (entry.workshopId);
    }
    std::ranges::sort (ids);

    REQUIRE (ids == std::vector<std::string> { "4242", "7777" });

    const auto mentions = [&lines] (const std::filesystem::path& folder) {
	size_t count = 0;
	std::istringstream in (lines);
	for (std::string line; std::getline (in, line);) {
	    if (line.find (folder.string ()) != std::string::npos) {
		count++;
	    }
	}
	return count;
    };

    CHECK (mentions (library / "dir") == 1);
    CHECK (mentions (library / "fifo") == 1);
    CHECK (mentions (library / "devlink") == 1);
    CHECK (mentions (library / "big") == 1);
    CHECK (mentions (library / "memlink") == 1);

    const auto lineFor = [&lines] (const std::filesystem::path& folder) {
	std::istringstream in (lines);
	for (std::string line; std::getline (in, line);) {
	    if (line.find (folder.string ()) != std::string::npos) {
		return line;
	    }
	}
	return std::string ();
    };

    CHECK (lineFor (library / "dir").find ("a directory, not a regular file") != std::string::npos);
    CHECK (lineFor (library / "fifo").find ("a named pipe, not a regular file") != std::string::npos);
    CHECK (lineFor (library / "devlink").find ("a device, not a regular file") != std::string::npos);
    CHECK (lineFor (library / "big").find ("larger than 4 MiB") != std::string::npos);
    CHECK (lineFor (library / "memlink").find ("cannot read its project.json") != std::string::npos);
}

TEST_CASE ("a project.json that cannot be opened is skipped with the reason", "[weblibrary]") {
    if (::geteuid () == 0) {
	SKIP ("running as root: a mode-000 file still opens, so this case cannot produce an open failure");
    }

    const auto base = std::filesystem::temp_directory_path () / ("lwe-web-locked-test-" + std::to_string (::getpid ()));
    std::filesystem::remove_all (base);
    const RemoveOnExit cleanup (base);
    const auto library = base / "data" / "lwe" / "wallpapers";
    std::filesystem::create_directories (base / "home");

    write (library / "good" / "project.json", R"({"type": "web", "workshopid": "4242"})");
    write (library / "locked" / "project.json", R"({"type": "web", "workshopid": "5555"})");
    std::filesystem::permissions (library / "locked" / "project.json", std::filesystem::perms::none);

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

    std::vector<std::string> ids;
    for (const auto& entry : found) {
	ids.push_back (entry.workshopId);
    }

    REQUIRE (ids == std::vector<std::string> { "4242" });

    size_t mentions = 0;
    std::string named;
    std::istringstream in (lines);

    for (std::string line; std::getline (in, line);) {
	if (line.find ((library / "locked").string ()) != std::string::npos) {
	    mentions++;
	    named = line;
	}
    }

    CHECK (mentions == 1);
    CHECK (named.find ("cannot open its project.json: Permission denied") != std::string::npos);
    CHECK (lines.find ("does not parse") == std::string::npos);
    CHECK (lines.find ("JSON strict parse failed") == std::string::npos);
}
