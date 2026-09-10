#include <catch2/catch_test_macros.hpp>

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <ostream>
#include <string>
#include <unistd.h>

#include "WallpaperEngine/Logging/StatePaths.h"

namespace {
std::filesystem::path freshStateRoot () {
    const auto base = std::filesystem::temp_directory_path () / ("lwe-state-test-" + std::to_string (::getpid ()));
    std::filesystem::remove_all (base);
    std::filesystem::create_directories (base);
    setenv ("XDG_STATE_HOME", base.c_str (), 1);
    return base / "lwe";
}

void touch (const std::filesystem::path& path, const std::string& text = "x") {
    std::filesystem::create_directories (path.parent_path ());
    std::ofstream (path) << text;
}

std::string slurp (const std::filesystem::path& path) {
    std::ifstream in (path);
    return std::string ((std::istreambuf_iterator<char> (in)), std::istreambuf_iterator<char> ());
}
} // namespace

TEST_CASE ("the state tree resolves under XDG_STATE_HOME", "[state]") {
    const auto root = freshStateRoot ();
    CHECK (WallpaperEngine::State::root () == root);
    CHECK (WallpaperEngine::State::engineDir () == root / "engine");
    CHECK (WallpaperEngine::State::logDir ("engine") == root / "logs" / "engine");
    CHECK (WallpaperEngine::State::logDir ("cef") == root / "logs" / "cef");
    CHECK (WallpaperEngine::State::probesDir () == root / "probes");
}

TEST_CASE ("rotate keeps N generations and drops the oldest", "[state]") {
    const auto root = freshStateRoot ();
    const auto log = root / "logs" / "engine" / "engine.log";

    for (int boot = 1; boot <= 7; boot++) {
	WallpaperEngine::State::rotate (log, 5);
	touch (log, "boot " + std::to_string (boot));
    }

    CHECK (slurp (log) == "boot 7");
    CHECK (slurp (root / "logs" / "engine" / "engine.log.1") == "boot 6");
    CHECK (slurp (root / "logs" / "engine" / "engine.log.5") == "boot 2");
    CHECK_FALSE (std::filesystem::exists (root / "logs" / "engine" / "engine.log.6"));
}

TEST_CASE ("migrateEngineFiles moves the flat layout once and overwrites nothing", "[state]") {
    const auto root = freshStateRoot ();
    touch (root / "engine-state.json", "{}");
    touch (root / "boot-history.json", "[]");
    touch (root / "texcache" / "abc.bc", "bc");
    touch (root / "cef.log", "cef");
    touch (root / "cef.log.2", "old cef");
    touch (root / "shaderdump-foo.frag.glsl", "glsl");
    touch (root / "passprobe-post.ppm", "P6");
    touch (root / "dev-slots.json", "panel"); // the panel's, not ours
    touch (root / "engine" / "boot-history.json", "already here");
    touch (root / "engine" / "texcache" / "def.bc", "panel wrote this first");

    CHECK (WallpaperEngine::State::migrateEngineFiles () == 6);
    CHECK (slurp (root / "engine" / "engine-state.json") == "{}");
    CHECK (slurp (root / "engine" / "boot-history.json") == "already here");
    CHECK (std::filesystem::exists (root / "boot-history.json"));
    CHECK (slurp (root / "engine" / "texcache" / "abc.bc") == "bc");
    CHECK (slurp (root / "engine" / "texcache" / "def.bc") == "panel wrote this first");
    CHECK_FALSE (std::filesystem::exists (root / "texcache"));
    CHECK (slurp (root / "logs" / "cef" / "cef.log") == "cef");
    CHECK (slurp (root / "logs" / "cef" / "cef.log.2") == "old cef");
    CHECK (slurp (root / "probes" / "shaderdump-foo.frag.glsl") == "glsl");
    CHECK (slurp (root / "probes" / "passprobe-post.ppm") == "P6");
    CHECK (std::filesystem::exists (root / "dev-slots.json"));
    CHECK (WallpaperEngine::State::migrateEngineFiles () == 0);
}

TEST_CASE ("the timestamped file sink stamps every line and flushes at its end", "[state]") {
    const auto root = freshStateRoot ();
    const auto log = root / "logs" / "engine" / "engine.log";
    WallpaperEngine::State::TimestampedFileBuf buf;
    REQUIRE (buf.open (log));
    std::ostream out (&buf);

    out << "first line" << std::endl;
    out << "second, in two" << " writes" << std::endl;

    const std::string text = slurp (log);
    REQUIRE (text.size () > 40);
    // "YYYY-MM-DD HH:MM:SS first line\n" - the stamp is 20 characters ending in a space
    CHECK (text[4] == '-');
    CHECK (text[10] == ' ');
    CHECK (text[19] == ' ');
    CHECK (text.substr (20, 10) == "first line");
    const auto second = text.find ('\n') + 1;
    CHECK (text[second + 19] == ' ');
    CHECK (text.substr (second + 20, 21) == "second, in two writes");
    CHECK (text.back () == '\n');
}
