#include <catch2/catch_test_macros.hpp>

#include "WallpaperEngine/Render/Wallpapers/CScene.h"

using WallpaperEngine::Render::Wallpapers::parseOutputSize;

TEST_CASE ("LWE_CLAMPOUTPUT reads WxH and nothing else", "[clamp]") {
    CHECK (parseOutputSize ("2560x1440") == glm::ivec2 { 2560, 1440 });
    CHECK (parseOutputSize ("1x1") == glm::ivec2 { 1, 1 });
    CHECK (parseOutputSize (nullptr) == glm::ivec2 { 0, 0 });
    CHECK (parseOutputSize ("") == glm::ivec2 { 0, 0 });
    CHECK (parseOutputSize ("2560") == glm::ivec2 { 0, 0 });
    CHECK (parseOutputSize ("2560x") == glm::ivec2 { 0, 0 });
    CHECK (parseOutputSize ("0x1440") == glm::ivec2 { 0, 0 });
    CHECK (parseOutputSize ("-2560x1440") == glm::ivec2 { 0, 0 });
    CHECK (parseOutputSize ("2560x1440x2") == glm::ivec2 { 0, 0 });
    CHECK (parseOutputSize ("wide") == glm::ivec2 { 0, 0 });
}
