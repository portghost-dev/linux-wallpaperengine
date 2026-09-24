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
