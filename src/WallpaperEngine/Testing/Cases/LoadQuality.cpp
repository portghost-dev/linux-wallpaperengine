#include <catch2/catch_test_macros.hpp>
#include <cstdlib>
#include <sstream>
#include <string>

#include "WallpaperEngine/Application/Config.h"
#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/Render/LoadQuality.h"
#include "WallpaperEngine/Render/TextureCache.h"

using namespace WallpaperEngine::Render;

namespace {
struct EnvGuard {
    const char* name;
    explicit EnvGuard (const char* n) : name (n) {
	unsetenv (n);
	WallpaperEngine::Application::Config::reload ();
    }
    ~EnvGuard () {
	unsetenv (name);
	WallpaperEngine::Application::Config::reload ();
    }
    void set (const char* v) const {
	setenv (name, v, 1);
	WallpaperEngine::Application::Config::reload ();
    }
};

// attached before any test runs, since the above-4 line prints once per process from whichever test reads first;
// never freed, because the logger keeps the pointer
std::ostringstream* const g_errorLines = [] () {
    auto* stream = new std::ostringstream ();
    sLog.addError (stream);
    return stream;
}();
} // namespace

TEST_CASE ("a show's scene and effect factors win over the environment, read as numbers", "[quality]") {
    EnvGuard ss ("LWE_SSFACTOR");
    EnvGuard cc ("LWE_CLAMPCOMPOSITES");
    CHECK (LoadQuality::ssfactor (std::nullopt) == 1.0f);
    CHECK (LoadQuality::clampComposites (std::nullopt) == 1.0f);

    ss.set ("1.5");
    cc.set ("0.5");
    CHECK (LoadQuality::ssfactor (std::nullopt) == 1.5f);
    CHECK (LoadQuality::clampComposites (std::nullopt) == 0.5f);
    CHECK (LoadQuality::ssfactor (2.5f) == 2.5f);
    CHECK (LoadQuality::clampComposites (3.0f) == 3.0f);

    CHECK (LoadQuality::ssfactor (0.0f) == 0.0f);
    CHECK (LoadQuality::clampComposites (-1.0f) == 0.0f);
    ss.set ("-2");
    cc.set ("0");
    CHECK (LoadQuality::ssfactor (std::nullopt) == 0.0f);
    CHECK (LoadQuality::clampComposites (std::nullopt) == 0.0f);

    ss.set ("6");
    cc.set ("4.5");
    CHECK (LoadQuality::ssfactor (std::nullopt) == 4.0f);
    CHECK (LoadQuality::clampComposites (std::nullopt) == 4.0f);
}

TEST_CASE ("the texture cache keys raw and full-chain requests apart, engine textures never", "[quality]") {
    CHECK (TextureCache::cacheKey ("materials/a.tex", true, true) == "materials/a.tex");
    CHECK (TextureCache::cacheKey ("materials/a.tex", false, true) == "materials/a.tex|raw");
    CHECK (TextureCache::cacheKey ("materials/a.tex", true, false) == "materials/a.tex|full");
    CHECK (TextureCache::cacheKey ("materials/a.tex", false, false) == "materials/a.tex|raw|full");
    // the album art pair is stored under its plain name and must resolve under every choice
    CHECK (TextureCache::cacheKey ("$mediaThumbnail", false, false) == "$mediaThumbnail");
    CHECK (TextureCache::cacheKey ("$mediaPreviousThumbnail", true, false) == "$mediaPreviousThumbnail");
}

TEST_CASE ("a show's texcomp and texdetail resolve with the environment as default", "[quality]") {
    EnvGuard tc ("LWE_TEXCOMP");
    EnvGuard td ("LWE_TEXDETAIL");
    CHECK (LoadQuality::texcomp ("1"));
    CHECK_FALSE (LoadQuality::texcomp ("0"));
    CHECK (LoadQuality::texcomp (""));
    tc.set ("0");
    CHECK_FALSE (LoadQuality::texcomp (""));
    CHECK (LoadQuality::texcomp ("1"));

    CHECK (LoadQuality::texdetailAuto ("auto"));
    CHECK_FALSE (LoadQuality::texdetailAuto ("full"));
    CHECK (LoadQuality::texdetailAuto (""));
    td.set ("full");
    CHECK_FALSE (LoadQuality::texdetailAuto (""));
    CHECK (LoadQuality::texdetailAuto ("auto"));
}

TEST_CASE ("an environment factor above 4 logs one line per variable across reads", "[quality]") {
    EnvGuard ss ("LWE_SSFACTOR");
    EnvGuard cc ("LWE_CLAMPCOMPOSITES");
    ss.set ("6");
    cc.set ("4.5");

    for (int read = 0; read < 2; read++) {
	CHECK (LoadQuality::ssfactor (std::nullopt) == 4.0f);
	CHECK (LoadQuality::clampComposites (std::nullopt) == 4.0f);
    }

    const std::string lines = g_errorLines->str ();
    // the logger cannot drop a stream, so this one stops taking lines
    g_errorLines->setstate (std::ios::badbit);

    const auto occurrences = [&lines] (const std::string& name) {
	const std::string prefix = name + "=";
	const std::string suffix = " is above 4; using 4";
	std::istringstream in (lines);
	size_t count = 0;

	for (std::string line; std::getline (in, line);) {
	    if (line.size () >= prefix.size () + suffix.size () && line.starts_with (prefix)
		&& line.ends_with (suffix)) {
		count++;
	    }
	}

	return count;
    };

    CHECK (occurrences ("LWE_SSFACTOR") == 1);
    CHECK (occurrences ("LWE_CLAMPCOMPOSITES") == 1);
}
