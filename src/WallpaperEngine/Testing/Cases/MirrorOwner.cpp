#include <catch2/catch_test_macros.hpp>

#include <map>
#include <memory>
#include <set>
#include <string>

#include "WallpaperEngine/Render/MirrorOwner.h"

using WallpaperEngine::Render::MirrorOwner::next;

namespace {
const auto SHARED = std::make_shared<int> (1);
const auto OTHER = std::make_shared<int> (2);
const std::map<std::string, std::shared_ptr<int>> WALLPAPERS
    = { { "DP-1", SHARED }, { "DP-2", SHARED }, { "DP-3", SHARED }, { "HDMI-A-1", OTHER } };
} // namespace

TEST_CASE ("a departing owner hands the shared instance to another live member", "[mirror]") {
    const std::set<std::string> live = { "DP-1", "DP-2", "DP-3", "HDMI-A-1" };

    REQUIRE (next (WALLPAPERS, "DP-1", live) == "DP-2");
    REQUIRE (next (WALLPAPERS, "DP-2", live) == "DP-1");
    REQUIRE (next (WALLPAPERS, "HDMI-A-1", live) == "");
}

TEST_CASE ("a screen that already left is never re-elected", "[mirror]") {
    // DP-1 unplugged earlier; its map entry stays so a replug can present again
    REQUIRE (next (WALLPAPERS, "DP-2", { "DP-2", "DP-3", "HDMI-A-1" }) == "DP-3");
    // the last live member leaves: nobody is left to own the instance
    REQUIRE (next (WALLPAPERS, "DP-3", { "DP-3", "HDMI-A-1" }) == "");
}

TEST_CASE ("an unknown or lone screen elects nobody", "[mirror]") {
    const std::map<std::string, std::shared_ptr<int>> lone = { { "DP-1", SHARED } };

    REQUIRE (next (lone, "DP-1", { "DP-1" }) == "");
    REQUIRE (next (lone, "DP-9", { "DP-1" }) == "");
}
