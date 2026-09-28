#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <unistd.h>
#include <vector>

#include "WallpaperEngine/Assets/AssetLoadException.h"
#include "WallpaperEngine/Assets/AssetLocator.h"
#include "WallpaperEngine/WebBrowser/CEF/SchemeLocator.h"

using namespace WallpaperEngine::Assets;

namespace {
void write (const std::filesystem::path& path, const std::string& text) {
    std::filesystem::create_directories (path.parent_path ());
    std::ofstream (path) << text;
}

void appendUint32 (std::string& out, const uint32_t value) {
    out.append (reinterpret_cast<const char*> (&value), sizeof value);
}

// a minimal Wallpaper Engine package: sized "PKGV0001" header, then a file table of
// (sized name, offset, length), then the payload (Data/Parsers/PackageParser.cpp::parse)
void writePackage (const std::filesystem::path& path, const std::string& name, const std::string& data) {
    const std::string header = "PKGV0001";
    std::string out;
    appendUint32 (out, static_cast<uint32_t> (header.size ()));
    out += header;
    appendUint32 (out, 1);
    appendUint32 (out, static_cast<uint32_t> (name.size ()));
    out += name;
    appendUint32 (out, 0);
    appendUint32 (out, static_cast<uint32_t> (data.size ()));
    out += data;

    std::filesystem::create_directories (path.parent_path ());
    std::ofstream (path, std::ios::binary) << out;
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

TEST_CASE ("the web scheme builds its locator with the web locator", "[weblocator]") {
    const auto base
	= std::filesystem::temp_directory_path () / ("lwe-scheme-locator-test-" + std::to_string (::getpid ()));
    std::filesystem::remove_all (base);
    write (base / "home" / ".probe", "private");
    write (base / "wallpaper" / "index.html", "<html>");
    write (base / "assets" / "shaders" / "common.h", "asset");

    {
	const WorkingFolder cwd (base / "home");
	REQUIRE (std::filesystem::exists (std::filesystem::current_path () / ".probe"));

	const auto locator = WallpaperEngine::WebBrowser::CEF::schemeAssetLocator (base / "wallpaper", base / "assets");

	CHECK_THROWS_AS (locator->read (".probe"), AssetLoadException);
	CHECK (locator->readString ("index.html") == "<html>");
    }

    std::filesystem::remove_all (base);
}

TEST_CASE ("the web locator mounts a real scene.pkg but never a scene.pkg symlink", "[weblocator]") {
    const auto base = std::filesystem::temp_directory_path () / ("lwe-web-pkg-test-" + std::to_string (::getpid ()));
    std::filesystem::remove_all (base);
    write (base / "assets" / "shaders" / "common.h", "asset");
    write (base / "secret" / ".probe", "leak");
    writePackage (base / "foreign" / "other.pkg", "secret.txt", "FOREIGN-PKG-SECRET");

    SECTION ("a real scene.pkg file mounts and serves its entry") {
	write (base / "good" / "index.html", "<html>");
	writePackage (base / "good" / "scene.pkg", "ok.txt", "PKG-OK");

	const auto locator = setupWebAssetLocator ((base / "good").string (), base / "assets");

	CHECK (locator->readString ("ok.txt") == "PKG-OK");
	CHECK (locator->readString ("index.html") == "<html>");
	CHECK (locator->readString ("shaders/common.h") == "asset");
    }

    SECTION ("a scene.pkg symlink is refused and its target is never served") {
	struct Probe {
	    const char* name;
	    std::filesystem::path target;
	    std::string request;
	};

	const std::vector<Probe> probes = {
	    { "dir", base / "secret", ".probe" },
	    { "rel", "../secret", ".probe" },
	    { "root", "../../../../../../../../../../../../../../../..",
	      (base / "secret" / ".probe").string ().substr (1) },
	    { "file", base / "foreign" / "other.pkg", "secret.txt" },
	};

	for (const auto& probe : probes) {
	    const auto wallpaper = base / (std::string ("wp_") + probe.name);
	    write (wallpaper / "index.html", "<html>");
	    std::filesystem::create_symlink (probe.target, wallpaper / "scene.pkg");

	    const auto locator = setupWebAssetLocator (wallpaper.string (), base / "assets");

	    CHECK_THROWS_AS (locator->read (probe.request), AssetLoadException);
	    CHECK (locator->readString ("index.html") == "<html>");
	}
    }

    SECTION ("a scene.pkg symlink to a package inside the same folder is not mounted") {
	write (base / "inlink" / "index.html", "<html>");
	writePackage (base / "inlink" / "real.pkg", "inner.txt", "INNER");
	std::filesystem::create_symlink ("real.pkg", base / "inlink" / "scene.pkg");

	const auto locator = setupWebAssetLocator ((base / "inlink").string (), base / "assets");

	CHECK (locator->readString ("index.html") == "<html>");
	CHECK_THROWS_AS (locator->read ("inner.txt"), AssetLoadException);
    }

    SECTION ("a scene.pkg directory is not mounted as a package root") {
	write (base / "pkgdir" / "index.html", "<html>");
	write (base / "pkgdir" / "scene.pkg" / "ok.txt", "DIR-OK");

	const auto locator = setupWebAssetLocator ((base / "pkgdir").string (), base / "assets");

	CHECK_THROWS_AS (locator->read ("ok.txt"), AssetLoadException);
	CHECK (locator->readString ("scene.pkg/ok.txt") == "DIR-OK");
    }

    std::filesystem::remove_all (base);
}
