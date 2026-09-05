#include <catch2/catch_test_macros.hpp>

#include <nlohmann/json.hpp>
#include <random>
#include <string>

#include "WallpaperEngine/Api/Lane.h"

using namespace WallpaperEngine::Api;

namespace {
Playlist makePlaylist (std::size_t count, const std::string& order) {
    Playlist playlist;
    playlist.slug = "default";
    playlist.order = order;
    playlist.intervalSeconds = 900;

    for (std::size_t i = 0; i < count; i++) {
	Entry entry;
	entry.id = "wp" + std::to_string (i);
	entry.uiId = entry.id;
	entry.args = { { "id", entry.id }, { "ui_id", entry.uiId } };
	playlist.entries.push_back (entry);
    }

    return playlist;
}

Clock::time_point t0 () { return Clock::time_point {} + std::chrono::hours (1); }
} // namespace

TEST_CASE ("sequential walks the playlist in order and skips the on-screen item", "[lane]") {
    std::mt19937 rng (7);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (4, "sequential"), true, t0 ());

    REQUIRE (pickNext (lane, playlist, rng) == 0);
    REQUIRE (pickNext (lane, playlist, rng) == 1);
    recordShow (lane, playlist.entries[2], true);
    // the walk lands on the item already on screen and steps past it once
    REQUIRE (pickNext (lane, playlist, rng) == 3);
}

TEST_CASE ("shuffle exhausts a permutation before drawing a new one", "[lane]") {
    std::mt19937 rng (11);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (6, "shuffle"), true, t0 ());

    std::vector<std::size_t> seen;
    for (int i = 0; i < 6; i++) {
	seen.push_back (pickNext (lane, playlist, rng));
    }

    std::sort (seen.begin (), seen.end ());
    REQUIRE (seen == std::vector<std::size_t> { 0, 1, 2, 3, 4, 5 });
    REQUIRE (lane.permIndex == 6);
    pickNext (lane, playlist, rng);
    REQUIRE (lane.permIndex == 1);
}

TEST_CASE ("static never advances on the timer but next still walks", "[lane]") {
    std::mt19937 rng (3);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (3, "static"), true, t0 ());

    REQUIRE_FALSE (dueForAdvance (lane, playlist, t0 () + std::chrono::hours (10)));
    REQUIRE (nextInSeconds (lane, playlist, t0 ()) == -1);
    REQUIRE (pickNext (lane, playlist, rng) == 0);
    REQUIRE (pickNext (lane, playlist, rng) == 1);
}

TEST_CASE ("the same set keeps a frozen countdown across disable and enable", "[lane]") {
    std::mt19937 rng (5);
    Lane lane;
    Playlist playlist;
    const auto set = makePlaylist (3, "sequential");

    applySet (lane, playlist, set, true, t0 ());
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (100)) == 800);

    // disabling the same set freezes the remainder as the full interval (as the engine did)
    applySet (lane, playlist, set, false, t0 () + std::chrono::seconds (100));
    REQUIRE (lane.frozenRemainingSeconds == 900);
    lane.frozenRemainingSeconds = 300;

    // re-enabling the same set resumes from the frozen remainder
    applySet (lane, playlist, set, true, t0 () + std::chrono::seconds (200));
    REQUIRE (lane.frozenRemainingSeconds == -1);
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (200)) == 300);

    // a different set restarts the clock
    applySet (lane, playlist, makePlaylist (4, "sequential"), true, t0 () + std::chrono::seconds (500));
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (500)) == 900);
    REQUIRE (lane.seqIndex == -1);
    REQUIRE (lane.nextPick == SIZE_MAX);
}

TEST_CASE ("history is bounded and pops in order", "[lane]") {
    Lane lane;
    const auto playlist = makePlaylist (3, "sequential");

    for (int i = 0; i < 150; i++) {
	Entry entry;
	entry.id = "s" + std::to_string (i);
	recordShow (lane, entry, true);
    }

    REQUIRE (lane.history.size () == HISTORY_BOUND);
    REQUIRE (lane.current.id == "s149");
    const auto last = popHistory (lane);
    REQUIRE (last.has_value ());
    REQUIRE (last->id == "s148");
    REQUIRE (lane.history.size () == HISTORY_BOUND - 1);
}

TEST_CASE ("a version 1 state file becomes one lane and one playlist", "[lane]") {
    const nlohmann::json v1
	= { { "version", 1 },
	    { "current", { { "id", "222" }, { "ui_id", "222" }, { "args", { { "id", "222" }, { "speed", 1.5 } } } } },
	    { "rotation",
	      { { "entries", { { { "id", "111" }, { "ui_id", "111" } }, { { "id", "222" }, { "ui_id", "222" } } } },
		{ "interval_s", 600 },
		{ "order", "sequential" },
		{ "avoid_repeat", true },
		{ "enabled", true },
		{ "label", "All Wallpapers" },
		{ "frozen_remaining_s", -1 } } } };

    Lane lane;
    Playlist playlist;
    fromLegacyState (v1, lane, playlist);

    REQUIRE (playlist.entries.size () == 2);
    REQUIRE (playlist.entries[1].uiId == "222");
    REQUIRE (playlist.entries[1].args["id"] == "222");
    REQUIRE (playlist.intervalSeconds == 600);
    REQUIRE (playlist.order == "sequential");
    REQUIRE (playlist.label == "All Wallpapers");
    REQUIRE (lane.enabled);
    REQUIRE (lane.current.id == "222");
    REQUIRE (lane.current.args["speed"] == 1.5);
}

TEST_CASE ("lane and playlist round-trip through json", "[lane]") {
    std::mt19937 rng (1);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (5, "shuffle"), true, t0 ());
    pickNext (lane, playlist, rng);
    recordShow (lane, playlist.entries[1], true);
    recordShow (lane, playlist.entries[2], true);
    lane.fit.zoom = 1.5f;
    lane.look.timescale = 2.0f;

    const auto laneBack = laneFromJson (toJson (lane));
    const auto playlistBack = playlistFromJson (toJson (playlist));

    REQUIRE (laneBack.perm == lane.perm);
    REQUIRE (laneBack.permIndex == lane.permIndex);
    REQUIRE (laneBack.current.id == "wp2");
    REQUIRE (laneBack.history.size () == 1);
    REQUIRE (laneBack.history.back ().id == "wp1");
    REQUIRE (laneBack.fit.zoom == 1.5f);
    REQUIRE (laneBack.look.timescale == 2.0f);
    REQUIRE (playlistBack.entries.size () == 5);
    REQUIRE (playlistBack.entries[4].args["ui_id"] == "wp4");
}

TEST_CASE ("the status block names what is on screen and what comes next", "[lane]") {
    std::mt19937 rng (9);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (3, "sequential"), true, t0 ());
    // the walk advanced through the first two entries as the timer would have
    recordShow (lane, playlist.entries[pickNext (lane, playlist, rng)], true);
    recordShow (lane, playlist.entries[pickNext (lane, playlist, rng)], true);
    lane.nextPick = pickNext (lane, playlist, rng);

    const auto status = laneStatus (lane, playlist, t0 () + std::chrono::seconds (60));
    REQUIRE (status["now"] == "wp1");
    REQUIRE (status["previous"] == "wp0");
    REQUIRE (status["next"] == "wp2");
    REQUIRE (status["empty"] == false);
    REQUIRE (status["next_in_s"] == 840);
    REQUIRE (status["history_depth"] == 1);

    const auto emptyStatus = laneStatus (Lane {}, Playlist {}, t0 ());
    REQUIRE (emptyStatus["empty"] == true);
    REQUIRE (emptyStatus["next_in_s"] == -1);
}

TEST_CASE ("random stays inside the set and honours avoid-repeat", "[lane]") {
    std::mt19937 rng (3);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (3, "random"), true, t0 ());
    lane.current = playlist.entries[0];

    for (int i = 0; i < 200; i++) {
	const auto pick = pickNext (lane, playlist, rng);
	REQUIRE (pick < 3);
	REQUIRE (pick != 0);
    }
}

TEST_CASE ("a corrupt permutation is redrawn instead of indexed", "[lane]") {
    std::mt19937 rng (5);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (4, "shuffle"), true, t0 ());
    lane.perm = { 7, 8, 9, 10 };
    lane.permIndex = 0;

    for (int i = 0; i < 8; i++) {
	REQUIRE (pickNext (lane, playlist, rng) < 4);
    }
}

TEST_CASE ("applySet leaves the binding to its caller", "[lane]") {
    Lane lane;
    lane.playlistSlug = "chill";
    Playlist playlist;
    playlist.slug = "chill";
    auto incoming = makePlaylist (2, "sequential");
    incoming.slug = "default";
    applySet (lane, playlist, incoming, true, t0 ());
    REQUIRE (playlist.slug == "chill");
    REQUIRE (lane.playlistSlug == "chill");
    REQUIRE (playlist.entries.size () == 2);
}
