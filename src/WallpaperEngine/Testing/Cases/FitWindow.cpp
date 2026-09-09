#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>
#include <glm/vec2.hpp>

#include "WallpaperEngine/Render/WallpaperState.h"

using Catch::Matchers::WithinAbs;
using WallpaperEngine::Render::WallpaperState;

namespace {
constexpr glm::ivec4 kViewport { 0, 0, 1920, 1080 };
constexpr float kEps = 1e-5f;

WallpaperState stretched (const bool vflip = false) {
    WallpaperState state (WallpaperState::TextureUVsScaling::StretchUVs, 0);
    state.updateState (kViewport, vflip, 1920, 1080);
    return state;
}

// a copy of the arithmetic CScene::updateMouse applies to a viewport fraction (fx, fy are
// fractions of the UV window's u and v spans, fy = 0 at vstart); the engine call itself needs
// a live scene, so this proves the window the cached UVs hand it, not the call
glm::vec2 mouseInWindow (const WallpaperState& state, const float fx, const float fy) {
    const auto uvs = state.getTextureUVs ();
    return { uvs.ustart + fx * (uvs.uend - uvs.ustart), uvs.vstart + fy * (uvs.vend - uvs.vstart) };
}
} // namespace

TEST_CASE ("identity zoom with no pan leaves the window alone; a full pan slides it half a screen", "[fit]") {
    auto state = stretched ();
    const auto before = state.getTextureUVs ();

    state.setFit ({ .zoom = 1.0f, .panX = 0.0f, .panY = 0.0f });
    state.updateState (kViewport, false, 1920, 1080);
    const auto same = state.getTextureUVs ();
    CHECK_THAT (same.ustart, WithinAbs (before.ustart, kEps));
    CHECK_THAT (same.uend, WithinAbs (before.uend, kEps));

    // a full pan moves the window by half of what is visible, past the picture's edge: the
    // sampler's clamp fills what slides in. The picture follows the pan, so the window goes
    // the other way: +x slides the picture right, which shows its left
    state.setFit ({ .zoom = 1.0f, .panX = -1.0f, .panY = 1.0f });
    CHECK (state.hasChanged (kViewport, false, 1920, 1080));
    state.updateState (kViewport, false, 1920, 1080);
    CHECK_FALSE (state.hasChanged (kViewport, false, 1920, 1080));

    const auto after = state.getTextureUVs ();
    CHECK_THAT (after.ustart, WithinAbs (before.ustart + 0.5f, kEps));
    CHECK_THAT (after.uend, WithinAbs (before.uend + 0.5f, kEps));
    CHECK_THAT (after.vstart, WithinAbs (before.vstart - 0.5f, kEps));
    CHECK_THAT (after.vend, WithinAbs (before.vend - 0.5f, kEps));
}

TEST_CASE ("zoom 2 shows the middle half; pan reaches a quarter each way", "[fit]") {
    auto state = stretched ();

    state.setFit ({ .zoom = 2.0f });
    state.updateState (kViewport, false, 1920, 1080);
    auto uvs = state.getTextureUVs ();
    CHECK_THAT (uvs.ustart, WithinAbs (0.25f, kEps));
    CHECK_THAT (uvs.uend, WithinAbs (0.75f, kEps));
    // v runs backwards without a flip: the viewport top samples v=1
    CHECK_THAT (uvs.vstart, WithinAbs (0.75f, kEps));
    CHECK_THAT (uvs.vend, WithinAbs (0.25f, kEps));

    // -panX moves the picture left and shows its right side, -panY moves it down and shows the top
    state.setFit ({ .zoom = 2.0f, .panX = -1.0f, .panY = -1.0f });
    state.updateState (kViewport, false, 1920, 1080);
    uvs = state.getTextureUVs ();
    CHECK_THAT (uvs.ustart, WithinAbs (0.5f, kEps));
    CHECK_THAT (uvs.uend, WithinAbs (1.0f, kEps));
    CHECK_THAT (uvs.vstart, WithinAbs (1.0f, kEps));
    CHECK_THAT (uvs.vend, WithinAbs (0.5f, kEps));

    state.setFit ({ .zoom = 2.0f, .panX = 1.0f, .panY = 1.0f });
    state.updateState (kViewport, false, 1920, 1080);
    uvs = state.getTextureUVs ();
    CHECK_THAT (uvs.ustart, WithinAbs (0.0f, kEps));
    CHECK_THAT (uvs.uend, WithinAbs (0.5f, kEps));
    CHECK_THAT (uvs.vstart, WithinAbs (0.5f, kEps));
    CHECK_THAT (uvs.vend, WithinAbs (0.0f, kEps));

    // half travel at zoom 2 is an eighth of the picture
    state.setFit ({ .zoom = 2.0f, .panX = -0.5f });
    state.updateState (kViewport, false, 1920, 1080);
    uvs = state.getTextureUVs ();
    CHECK_THAT (uvs.ustart, WithinAbs (0.375f, kEps));
    CHECK_THAT (uvs.uend, WithinAbs (0.875f, kEps));
}

TEST_CASE ("a flipped window pans toward the viewport top too", "[fit]") {
    auto state = stretched (true);

    state.setFit ({ .zoom = 2.0f, .panY = -1.0f });
    state.updateState (kViewport, true, 1920, 1080);
    const auto uvs = state.getTextureUVs ();
    // flipped: the viewport top samples v=0, so the top of the picture is the low end
    CHECK_THAT (uvs.vstart, WithinAbs (0.0f, kEps));
    CHECK_THAT (uvs.vend, WithinAbs (0.5f, kEps));
}

TEST_CASE ("the window nests inside a fill crop", "[fit]") {
    // a 2:1 picture filling a 16:9 viewport is cropped left and right first
    WallpaperState state (WallpaperState::TextureUVsScaling::ZoomFillUVs, 0);
    state.updateState (kViewport, false, 2160, 1080);
    const auto crop = state.getTextureUVs ();
    REQUIRE (crop.ustart > 0.0f);
    REQUIRE (crop.uend < 1.0f);

    state.setFit ({ .zoom = 2.0f, .panX = -1.0f });
    state.updateState (kViewport, false, 2160, 1080);
    const auto uvs = state.getTextureUVs ();
    // the pan stops at the crop's edge: nothing the fill hid comes back
    CHECK_THAT (uvs.uend, WithinAbs (crop.uend, kEps));
    CHECK_THAT (uvs.uend - uvs.ustart, WithinAbs ((crop.uend - crop.ustart) / 2.0f, kEps));
}

TEST_CASE ("fit values are clamped on the way in", "[fit]") {
    auto state = stretched ();

    state.setFit ({ .zoom = 3.0f, .panX = 4.0f, .panY = -4.0f });
    CHECK_THAT (state.getFit ().zoom, WithinAbs (2.0f, kEps));
    CHECK_THAT (state.getFit ().panX, WithinAbs (1.0f, kEps));
    CHECK_THAT (state.getFit ().panY, WithinAbs (-1.0f, kEps));

    state.setFit ({ .zoom = 0.5f });
    CHECK_THAT (state.getFit ().zoom, WithinAbs (1.0f, kEps));
}

TEST_CASE ("the two layers compose: zooms multiply, pans add, clamped", "[fit]") {
    const auto composed = WallpaperState::composeFit (
	{ .zoom = 1.2f, .panX = 0.25f, .panY = -0.5f }, { .zoom = 1.5f, .panX = 0.25f, .panY = -0.25f }
    );
    CHECK_THAT (composed.zoom, WithinAbs (1.8f, kEps));
    CHECK_THAT (composed.panX, WithinAbs (0.5f, kEps));
    CHECK_THAT (composed.panY, WithinAbs (-0.75f, kEps));

    const auto clamped = WallpaperState::composeFit (
	{ .zoom = 1.5f, .panX = 0.75f, .panY = -0.75f }, { .zoom = 1.5f, .panX = 0.75f, .panY = -0.75f }
    );
    CHECK_THAT (clamped.zoom, WithinAbs (2.0f, kEps));
    CHECK_THAT (clamped.panX, WithinAbs (1.0f, kEps));
    CHECK_THAT (clamped.panY, WithinAbs (-1.0f, kEps));

    // identity on either side is a no-op
    const auto same = WallpaperState::composeFit ({ .zoom = 1.3f, .panX = 0.1f, .panY = 0.2f }, {});
    CHECK_THAT (same.zoom, WithinAbs (1.3f, kEps));
    CHECK_THAT (same.panX, WithinAbs (0.1f, kEps));
    CHECK_THAT (same.panY, WithinAbs (0.2f, kEps));
}

TEST_CASE ("the mouse arithmetic lands inside the window the present pass shows", "[fit]") {
    auto state = stretched ();
    state.setFit ({ .zoom = 2.0f, .panX = -1.0f, .panY = -1.0f });
    state.updateState (kViewport, false, 1920, 1080);

    // the viewport centre is the window centre, three quarters across the picture
    const auto centre = mouseInWindow (state, 0.5f, 0.5f);
    CHECK_THAT (centre.x, WithinAbs (0.75f, kEps));
    CHECK_THAT (centre.y, WithinAbs (0.75f, kEps));

    // the window's corners are the UV window's corners, never outside it
    const auto atStart = mouseInWindow (state, 0.0f, 0.0f);
    CHECK_THAT (atStart.x, WithinAbs (0.5f, kEps));
    CHECK_THAT (atStart.y, WithinAbs (1.0f, kEps));
    const auto atEnd = mouseInWindow (state, 1.0f, 1.0f);
    CHECK_THAT (atEnd.x, WithinAbs (1.0f, kEps));
    CHECK_THAT (atEnd.y, WithinAbs (0.5f, kEps));
}
