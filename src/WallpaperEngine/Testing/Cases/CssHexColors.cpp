#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include "WallpaperEngine/Data/Builders/ColorBuilder.h"

using Catch::Matchers::WithinAbs;
using WallpaperEngine::Data::Builders::ColorBuilder;
using WallpaperEngine::Data::Model::Color;

namespace {
void checkColor (const Color& color, float r, float g, float b, float a) {
    CHECK_THAT (color.r, WithinAbs (r, 0.002f));
    CHECK_THAT (color.g, WithinAbs (g, 0.002f));
    CHECK_THAT (color.b, WithinAbs (b, 0.002f));
    CHECK_THAT (color.a, WithinAbs (a, 0.002f));
}
} // namespace

TEST_CASE ("Six digit css hex colors keep every channel and take the alpha argument") {
    checkColor (ColorBuilder::parse ("#ff8040"), 1.0f, 128 / 255.0f, 64 / 255.0f, 1.0f);
    checkColor (ColorBuilder::parse ("#ffffff", 0.25f), 1.0f, 1.0f, 1.0f, 0.25f);
    checkColor (ColorBuilder::parse ("#000000"), 0.0f, 0.0f, 0.0f, 1.0f);
}

TEST_CASE ("Eight digit css hex colors carry their own alpha") {
    checkColor (ColorBuilder::parse ("#ff804080"), 1.0f, 128 / 255.0f, 64 / 255.0f, 128 / 255.0f);
    checkColor (ColorBuilder::parse ("#FFFFFFFF", 0.0f), 1.0f, 1.0f, 1.0f, 1.0f);
}

TEST_CASE ("Short css hex colors expand by doubling each digit") {
    checkColor (ColorBuilder::parse ("#fff", 0.5f), 1.0f, 1.0f, 1.0f, 0.5f);
    checkColor (ColorBuilder::parse ("#f00"), 1.0f, 0.0f, 0.0f, 1.0f);
    checkColor (ColorBuilder::parse ("#08f8"), 0.0f, 136 / 255.0f, 1.0f, 136 / 255.0f);
}

TEST_CASE ("Malformed css hex colors are refused instead of parsed as garbage") {
    REQUIRE_THROWS (ColorBuilder::parse ("#ggg"));
    REQUIRE_THROWS (ColorBuilder::parse ("#12345"));
    REQUIRE_THROWS (ColorBuilder::parse ("#-1234567"));
    REQUIRE_THROWS (ColorBuilder::parse ("#"));
}

TEST_CASE ("Float and byte triples are unaffected by the hex path") {
    checkColor (ColorBuilder::parse ("1 1 1", 0.5f), 1.0f, 1.0f, 1.0f, 0.5f);
    checkColor (ColorBuilder::parse ("255,128,0"), 1.0f, 128 / 255.0f, 0.0f, 1.0f);
    checkColor (ColorBuilder::parse ("0.5 0.25 0.125 0.75"), 0.5f, 0.25f, 0.125f, 0.75f);
}
