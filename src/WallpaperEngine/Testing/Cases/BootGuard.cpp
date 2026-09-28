#include <catch2/catch_test_macros.hpp>

#include <nlohmann/json.hpp>
#include <vector>

#include "WallpaperEngine/Application/BootGuard.h"

using namespace WallpaperEngine::Application;

namespace {
nlohmann::json boot (const bool survived) { return { { "t", 1 }, { "survived", survived } }; }
} // namespace

TEST_CASE ("two unsurvived boots refuse the restore and set the flag", "[bootguard]") {
    BootGuard pair;
    CHECK (pair.evaluate (nlohmann::json::array ({ boot (false), boot (false) })));
    CHECK (pair.restoreRefused ());

    BootGuard full;
    CHECK (full.evaluate (nlohmann::json::array ({ boot (true), boot (false), boot (false) })));
    CHECK (full.restoreRefused ());
}

TEST_CASE ("a successful show clears the refused flag", "[bootguard]") {
    BootGuard guard;
    REQUIRE (guard.evaluate (nlohmann::json::array ({ boot (false), boot (false) })));
    guard.wallpaperShown ();
    CHECK_FALSE (guard.restoreRefused ());
}

TEST_CASE ("a normal boot history does not refuse the restore", "[bootguard]") {
    CHECK_FALSE (BootGuard {}.restoreRefused ());

    const std::vector<nlohmann::json> histories = {
	nlohmann::json::array (),
	nlohmann::json::array ({ boot (false) }),
	nlohmann::json::array ({ boot (false), boot (true) }),
	nlohmann::json::array ({ boot (true), boot (false) }),
	nlohmann::json::array ({ boot (false), boot (false), boot (true) }),
    };

    for (const auto& history : histories) {
	BootGuard guard;
	CHECK_FALSE (guard.evaluate (history));
	CHECK_FALSE (guard.restoreRefused ());
    }
}
