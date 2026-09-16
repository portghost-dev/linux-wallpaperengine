#include <catch2/catch_test_macros.hpp>
#include <cstdlib>

#include "WallpaperEngine/Render/LoadQuality.h"
#include "WallpaperEngine/Render/TextureCache.h"

using namespace WallpaperEngine::Render;

namespace {
struct EnvGuard {
    const char* name;
    explicit EnvGuard (const char* n) : name (n) { unsetenv (n); }
    ~EnvGuard () { unsetenv (name); }
    void set (const char* v) const { setenv (name, v, 1); }
};
} // namespace

TEST_CASE ("a show's res resolves the clamp factor and the composite rule", "[quality]") {
    EnvGuard ss ("LWE_SSFACTOR");
    EnvGuard cc ("LWE_CLAMPCOMPOSITES");
    CHECK (LoadQuality::ssfactor ("screen") == 1.0f);
    CHECK (LoadQuality::ssfactor ("sharpfx") == 1.0f);
    CHECK (LoadQuality::ssfactor ("wallpaper") == 0.0f);
    CHECK (LoadQuality::clampComposites ("screen"));
    CHECK_FALSE (LoadQuality::clampComposites ("sharpfx"));
    CHECK (LoadQuality::clampComposites ("wallpaper"));
    SECTION ("empty means the launch environment") {
	CHECK (LoadQuality::ssfactor ("") == 1.0f);
	CHECK (LoadQuality::clampComposites (""));
	ss.set ("0");
	cc.set ("0");
	CHECK (LoadQuality::ssfactor ("") == 0.0f);
	CHECK_FALSE (LoadQuality::clampComposites (""));
	// the show's own value still wins over the environment
	CHECK (LoadQuality::ssfactor ("screen") == 1.0f);
	CHECK (LoadQuality::clampComposites ("screen"));
    }
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
