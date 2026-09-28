#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <nlohmann/json.hpp>
#include <optional>
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

namespace {
std::vector<std::string> walkOf (Lane& lane, const Playlist& playlist, std::mt19937& rng, int steps) {
    std::vector<std::string> ids;

    for (int i = 0; i < steps; i++) {
	const auto pick = pickNext (lane, playlist, rng);
	REQUIRE (pick < playlist.entries.size ());
	ids.push_back (displayId (playlist.entries[pick]));
	recordShow (lane, playlist.entries[pick], true);
    }

    return ids;
}
} // namespace

TEST_CASE ("sequential plays the stored order, wraps, and skips the on-screen item once", "[lane]") {
    std::mt19937 rng (1);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (4, "sequential"), true, t0 ());

    REQUIRE (walkOf (lane, playlist, rng, 5) == std::vector<std::string> { "wp0", "wp1", "wp2", "wp3", "wp0" });
    REQUIRE (lane.cursor == 0);
    REQUIRE (nextUp (lane, playlist) == "wp1");
    // a detour onto the next-up item: the cursor holds and the pick skips it once (A11)
    recordShow (lane, playlist.entries[1], true);
    REQUIRE (lane.cursor == 0);
    REQUIRE (nextUp (lane, playlist) == "wp2");
    REQUIRE (pickNext (lane, playlist, rng) == 2);
    REQUIRE (lane.cursor == 2);
}

TEST_CASE ("a detour elsewhere leaves the cursor alone and resumes at cursor + 1", "[lane]") {
    std::mt19937 rng (1);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (5, "sequential"), true, t0 ());
    walkOf (lane, playlist, rng, 2);
    REQUIRE (lane.cursor == 1);
    recordShow (lane, playlist.entries[4], true);
    REQUIRE (lane.cursor == 1);
    REQUIRE (pickNext (lane, playlist, rng) == 2);
}

TEST_CASE ("shuffle covers every member once per cycle and pre-draws the next cycle", "[lane]") {
    std::mt19937 rng (2);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (6, "shuffle"), true, t0 ());

    auto seen = walkOf (lane, playlist, rng, 6);
    std::sort (seen.begin (), seen.end ());
    REQUIRE (seen == std::vector<std::string> { "wp0", "wp1", "wp2", "wp3", "wp4", "wp5" });
    REQUIRE (lane.cursor == 5);
    REQUIRE (lane.nextCycle.size () == 6);
    REQUIRE (nextUp (lane, playlist) != "");
    REQUIRE (nextUp (lane, playlist) == lane.nextCycle[lane.nextCycle[0] == lane.current.uiId ? 1 : 0]);

    // the next cycle: its first item is skipped once when it is the one on screen, so the
    // on-screen item plus six advances is the cycle (a member may show twice)
    const auto onScreen = lane.current.uiId;
    auto second = walkOf (lane, playlist, rng, 6);
    second.push_back (onScreen);
    std::sort (second.begin (), second.end ());
    second.erase (std::unique (second.begin (), second.end ()), second.end ());
    REQUIRE (second == seen);
    REQUIRE (lane.walk.size () == 6);
}

TEST_CASE ("random walks as shuffle and stays inside the set", "[lane]") {
    std::mt19937 rng (3);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (3, "random"), true, t0 ());
    auto seen = walkOf (lane, playlist, rng, 3);
    std::sort (seen.begin (), seen.end ());
    REQUIRE (seen == std::vector<std::string> { "wp0", "wp1", "wp2" });
}

TEST_CASE ("static never advances on the timer, next still walks, and a click seats the cursor", "[lane]") {
    std::mt19937 rng (4);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (3, "static"), true, t0 ());

    REQUIRE_FALSE (dueForAdvance (lane, playlist, t0 () + std::chrono::hours (10)));
    REQUIRE (nextInSeconds (lane, playlist, t0 ()) == -1);
    REQUIRE (pickNext (lane, playlist, rng) == 0);
    REQUIRE (pickNext (lane, playlist, rng) == 1);
    seatCursor (lane, "wp2");
    REQUIRE (lane.cursor == 2);
    REQUIRE (pickNext (lane, playlist, rng) == 0);
    seatCursor (lane, "nope");
    REQUIRE (lane.cursor == 0);
}

TEST_CASE ("static steps back through the playlist order and holds, never through history", "[lane]") {
    std::mt19937 rng (4);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (3, "static"), true, t0 ());

    // on wp1 with wp0 behind it in the walk: back is on, and the target is the walk's previous.
    // The show lands first, seating the cursor, then the books move, as the application does
    lane.current = playlist.entries[1];
    seatCursor (lane, "wp1");
    REQUIRE (backEnabled (lane, playlist));
    auto target = backTarget (lane, playlist);
    REQUIRE (target.has_value ());
    REQUIRE (target->id == "wp0");
    recordShow (lane, *target, false);
    seatCursor (lane, "wp0");
    commitBack (lane, playlist, playlist.entries[1], *target);
    REQUIRE (lane.cursor == 0);
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::hours (1)) == -1);

    // from the first item back wraps to the last
    target = backTarget (lane, playlist);
    REQUIRE (target.has_value ());
    REQUIRE (target->id == "wp2");

    // history is not the path in static: a recorded detour does not change where back goes
    lane.history.push_back (playlist.entries[1]);
    target = backTarget (lane, playlist);
    REQUIRE (target.has_value ());
    REQUIRE (target->id == "wp2");

    // one item: nothing to step to, in static as elsewhere
    Lane one;
    Playlist single;
    applySet (one, single, makePlaylist (1, "static"), true, t0 ());
    REQUIRE_FALSE (backEnabled (one, single));
    REQUIRE_FALSE (backTarget (one, single).has_value ());
}

TEST_CASE ("a push keeps the cursor's item in place and the old successor next (A6)", "[lane]") {
    std::mt19937 rng (5);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (5, "sequential"), true, t0 ());
    walkOf (lane, playlist, rng, 3); // wp2 on screen, cursor 2
    REQUIRE (nextUp (lane, playlist) == "wp3");

    // append: nothing moves
    auto grown = makePlaylist (6, "sequential");
    applySet (lane, playlist, grown, true, t0 ());
    REQUIRE (lane.cursor == 2);
    REQUIRE (nextUp (lane, playlist) == "wp3");

    // reorder: the cursor follows its item, next is the new successor
    auto reordered = makePlaylist (6, "sequential");
    std::swap (reordered.entries[2], reordered.entries[5]); // wp5 wp1 wp2 -> order wp0 wp1 wp5 wp3 wp4 wp2
    applySet (lane, playlist, reordered, true, t0 ());
    REQUIRE (lane.cursor == 5);
    REQUIRE (nextUp (lane, playlist) == "wp0");

    // remove the cursor's item: the first survivor after it plays next
    Playlist without = makePlaylist (6, "sequential");
    without.entries.erase (without.entries.begin () + 2); // wp0 wp1 wp3 wp4 wp5
    applySet (lane, playlist, without, true, t0 ());
    REQUIRE (nextUp (lane, playlist) == "wp0");
    REQUIRE (lane.cursor == 4);

    // nothing after the cursor survives: the walk wraps to its start
    Playlist front = makePlaylist (2, "sequential"); // wp0 wp1
    applySet (lane, playlist, front, true, t0 ());
    REQUIRE (lane.cursor == 1);
    REQUIRE (nextUp (lane, playlist) == "wp0");
}

TEST_CASE ("a shuffle push keeps the cycle and shuffles only the new ids onto the end", "[lane]") {
    std::mt19937 rng (6);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (6, "shuffle"), true, t0 ());
    walkOf (lane, playlist, rng, 3);
    const auto before = lane.walk;
    const int cursor = lane.cursor;

    applySet (lane, playlist, makePlaylist (8, "shuffle"), true, t0 ());
    REQUIRE (lane.cursor == cursor);
    REQUIRE (std::vector<std::string> (lane.walk.begin (), lane.walk.begin () + 6) == before);
    std::vector<std::string> tail (lane.walk.begin () + 6, lane.walk.end ());
    std::sort (tail.begin (), tail.end ());
    REQUIRE (tail == std::vector<std::string> { "wp6", "wp7" });
    REQUIRE (nextUp (lane, playlist) == before[cursor + 1]);

    // the same set again: nothing moves
    const auto walk = lane.walk;
    applySet (lane, playlist, makePlaylist (8, "shuffle"), true, t0 ());
    REQUIRE (lane.walk == walk);
    REQUIRE (lane.cursor == cursor);
}

TEST_CASE ("an order change starts a fresh walk", "[lane]") {
    std::mt19937 rng (11);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (6, "sequential"), true, t0 ());
    walkOf (lane, playlist, rng, 3); // wp2 on screen, cursor 2

    applySet (lane, playlist, makePlaylist (6, "shuffle"), true, t0 ());
    REQUIRE (lane.cursor == -1);
    auto ids = lane.walk;
    std::sort (ids.begin (), ids.end ());
    REQUIRE (ids == std::vector<std::string> { "wp0", "wp1", "wp2", "wp3", "wp4", "wp5" });

    // the on-screen item plus one cycle of advances covers the set (it may show twice)
    auto cycle = walkOf (lane, playlist, rng, 6);
    cycle.push_back ("wp2");
    std::sort (cycle.begin (), cycle.end ());
    cycle.erase (std::unique (cycle.begin (), cycle.end ()), cycle.end ());
    REQUIRE (cycle == ids);

    // back to sequential: the walk is the stored order and continues from the item on screen
    applySet (lane, playlist, makePlaylist (6, "sequential"), true, t0 ());
    REQUIRE (lane.walk == std::vector<std::string> { "wp0", "wp1", "wp2", "wp3", "wp4", "wp5" });
    REQUIRE (lane.cursor == std::stoi (lane.current.uiId.substr (2)));
}

TEST_CASE ("a stale or corrupt walk is re-seated instead of indexed", "[lane]") {
    std::mt19937 rng (7);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (4, "sequential"), true, t0 ());
    lane.walk = { "gone", "wp1", "also-gone" };
    lane.cursor = 1;
    REQUIRE (nextUp (lane, playlist) == "");
    REQUIRE (pickNext (lane, playlist, rng) == 2);
    REQUIRE (lane.walk == std::vector<std::string> { "wp0", "wp1", "wp2", "wp3" });

    lane.walk.clear ();
    lane.cursor = 9;
    REQUIRE (pickNext (lane, playlist, rng) < 4);
}

TEST_CASE ("avoid-repeat off lets the on-screen item come straight back", "[lane]") {
    std::mt19937 rng (8);
    Lane lane;
    Playlist playlist;
    auto set = makePlaylist (3, "sequential");
    set.avoidRepeat = false;
    applySet (lane, playlist, set, true, t0 ());
    walkOf (lane, playlist, rng, 1);
    recordShow (lane, playlist.entries[1], true);
    REQUIRE (pickNext (lane, playlist, rng) == 1);
}

TEST_CASE ("the same set keeps a frozen countdown across disable and enable", "[lane]") {
    Lane lane;
    Playlist playlist;
    const auto set = makePlaylist (3, "sequential");

    applySet (lane, playlist, set, true, t0 ());
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (100)) == 800);

    // a pause freezes where the countdown stood, and holds there
    applySet (lane, playlist, set, false, t0 () + std::chrono::seconds (100));
    REQUIRE (lane.frozenRemainingMs == 800 * 1000);
    REQUIRE_FALSE (lane.enabled);
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (5000)) == 800);
    // a re-push while paused keeps the frozen value; a new set while paused is the full interval
    applySet (lane, playlist, set, false, t0 () + std::chrono::seconds (150));
    REQUIRE (lane.frozenRemainingMs == 800 * 1000);
    lane.frozenRemainingMs = 300 * 1000;

    applySet (lane, playlist, set, true, t0 () + std::chrono::seconds (200));
    REQUIRE (lane.frozenRemainingMs == -1);
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (200)) == 300);

    applySet (lane, playlist, makePlaylist (4, "sequential"), true, t0 () + std::chrono::seconds (500));
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (500)) == 900);
    REQUIRE (lane.cursor == -1);
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
    Playlist none;
    lane.walk = { "a", "b" }; // back needs a walk of two
    const auto last = backTarget (lane, none);
    REQUIRE (last.has_value ());
    REQUIRE (last->id == "s148");
    const Entry before = lane.current;
    commitBack (lane, none, before, *last);
    REQUIRE (lane.history.size () == HISTORY_BOUND - 1);
    REQUIRE (lane.forward.size () == 1);
    REQUIRE (lane.current.id == "s148");
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
    walkOf (lane, playlist, rng, 5);
    recordShow (lane, playlist.entries[1], true);
    recordShow (lane, playlist.entries[2], true);
    lane.fit.zoom = 1.5f;
    lane.look.timescale = 2.0f;
    lane.look.ssfactor = 1.5f;
    lane.look.clampComposites = 0.0f;
    lane.look.texcomp = "0";
    lane.look.texdetail = "full";

    const auto laneBack = laneFromJson (toJson (lane));
    const auto playlistBack = playlistFromJson (toJson (playlist));

    REQUIRE (laneBack.walk == lane.walk);
    REQUIRE (laneBack.nextCycle == lane.nextCycle);
    REQUIRE (laneBack.cursor == lane.cursor);
    REQUIRE (laneBack.current.id == "wp2");
    REQUIRE (laneBack.history.size () == lane.history.size ());
    REQUIRE (laneBack.history.back ().id == lane.history.back ().id);
    REQUIRE (laneBack.fit.zoom == 1.5f);
    REQUIRE (laneBack.look.timescale == 2.0f);
    REQUIRE (laneBack.look.ssfactor == 1.5f);
    REQUIRE (laneBack.look.clampComposites == 0.0f);
    REQUIRE (laneBack.look.texcomp == "0");
    REQUIRE (laneBack.look.texdetail == "full");
    REQUIRE (playlistBack.entries.size () == 5);
    REQUIRE (playlistBack.entries[4].args["ui_id"] == "wp4");

    auto broken = toJson (lane);
    broken["cursor"] = 99;
    REQUIRE (laneFromJson (broken).cursor == -1);
}

TEST_CASE ("a look's two clamp numbers round-trip as null and an old res word loads as the numbers", "[lane]") {
    const Lane lane;
    const auto saved = toJson (lane);
    CHECK (saved.at ("look").at ("ssfactor").is_null ());
    CHECK (saved.at ("look").at ("clampcomposites").is_null ());
    const auto back = laneFromJson (saved);
    CHECK_FALSE (back.look.ssfactor.has_value ());
    CHECK_FALSE (back.look.clampComposites.has_value ());

    const auto loadWord = [&lane] (const std::string& word) {
	auto old = toJson (lane);
	old["look"].erase ("ssfactor");
	old["look"].erase ("clampcomposites");
	old["look"]["res"] = word;
	return laneFromJson (old).look;
    };
    CHECK (loadWord ("wallpaper").ssfactor == 0.0f);
    CHECK (loadWord ("wallpaper").clampComposites == 0.0f);
    CHECK (loadWord ("screen").ssfactor == 1.0f);
    CHECK (loadWord ("screen").clampComposites == 1.0f);
    CHECK (loadWord ("sharpfx").ssfactor == 1.0f);
    CHECK (loadWord ("sharpfx").clampComposites == 0.0f);
}

TEST_CASE ("a persisted res word in show args loads as the two clamp numbers", "[lane]") {
    const auto parse = [] (const char* text) { return nlohmann::json::parse (text); };

    CHECK (
	entryFromJson (parse (R"({"id":"1","args":{"id":"1","res":"sharpfx","speed":2}})")).args
	== parse (R"({"id":"1","speed":2,"ssfactor":1.0,"clampcomposites":0.0})")
    );
    CHECK (
	entryFromJson (parse (R"({"id":"2","res":"wallpaper"})")).args
	== parse (R"({"id":"2","ssfactor":0.0,"clampcomposites":0.0})")
    );

    Lane lane;
    Playlist playlist;
    fromLegacyState (
	parse (
	    R"({"version":1,"current":{"id":"3","args":{"id":"3","res":"screen"}},)"
	    R"("rotation":{"entries":[{"id":"4","res":"sharpfx"}]}})"
	),
	lane, playlist
    );
    REQUIRE (playlist.entries.size () == 1);
    CHECK (playlist.entries[0].args == parse (R"({"id":"4","ssfactor":1.0,"clampcomposites":0.0})"));
    CHECK (lane.current.args == parse (R"({"id":"3","ssfactor":1.0,"clampcomposites":1.0})"));

    CHECK (
	entryFromJson (parse (R"({"id":"5","args":{"res":"wallpaper","ssfactor":2}})")).args
	== parse (R"({"res":"wallpaper","ssfactor":2})")
    );
    CHECK (entryFromJson (parse (R"({"id":"6","args":{"id":"6","res":"half"}})")).args == parse (R"({"id":"6"})"));
}

TEST_CASE ("the status block names what is on screen and what comes next", "[lane]") {
    std::mt19937 rng (9);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (3, "sequential"), true, t0 ());
    // the walk advanced through the first two entries as the timer would have
    recordShow (lane, playlist.entries[pickNext (lane, playlist, rng)], true);
    recordShow (lane, playlist.entries[pickNext (lane, playlist, rng)], true);

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

TEST_CASE ("a one-item shuffle playlist keeps picking its item", "[lane]") {
    std::mt19937 rng (12);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (1, "shuffle"), true, t0 ());

    for (int i = 0; i < 4; i++) {
	REQUIRE (pickNext (lane, playlist, rng) == 0);
	recordShow (lane, playlist.entries[0], true);
	REQUIRE (nextUp (lane, playlist) == "wp0");
    }
}

TEST_CASE ("a push while the cursor sits on the last item keeps the drawn next cycle", "[lane]") {
    std::mt19937 rng (13);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (4, "shuffle"), true, t0 ());
    walkOf (lane, playlist, rng, 4);
    REQUIRE (lane.cursor == 3);
    const auto announced = nextUp (lane, playlist);
    REQUIRE (announced != "");

    applySet (lane, playlist, makePlaylist (4, "shuffle"), true, t0 ());
    REQUIRE (nextUp (lane, playlist) == announced);

    // a push that adds an id extends the current cycle with it (kept ids in old order, new ids
    // shuffled onto the end), so it plays before the drawn cycle, which grows to a full cycle too
    applySet (lane, playlist, makePlaylist (5, "shuffle"), true, t0 ());
    REQUIRE (nextUp (lane, playlist) == "wp4");
    REQUIRE (lane.walk.size () == 5);
    REQUIRE (lane.cursor == 3);
    auto cycle = lane.nextCycle;
    std::sort (cycle.begin (), cycle.end ());
    REQUIRE (cycle == std::vector<std::string> { "wp0", "wp1", "wp2", "wp3", "wp4" });

    // removing the new id again puts the cursor back on the last item and the drawn cycle next
    applySet (lane, playlist, makePlaylist (4, "shuffle"), true, t0 ());
    REQUIRE (lane.cursor == 3);
    REQUIRE (nextUp (lane, playlist) == announced);
}

TEST_CASE ("back seats the cursor only on the item just behind it", "[lane]") {
    std::mt19937 rng (14);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (4, "shuffle"), true, t0 ());
    walkOf (lane, playlist, rng, 4); // cycle 1 played, cursor 3, next cycle drawn
    const auto lastOfCycle1 = lane.current.uiId;
    Entry detour;
    detour.id = "elsewhere";
    detour.uiId = "elsewhere";
    recordShow (lane, detour, true); // a detour, so the first pick of cycle 2 is not skipped
    walkOf (lane, playlist, rng, 1); // first item of cycle 2, cursor 0
    REQUIRE (lane.cursor == 0);

    // back across the cycle boundary: the new cycle stays whole
    seatBehind (lane, lastOfCycle1);
    REQUIRE (lane.cursor == 0);

    // back inside the walk: cursor 2 -> 1 when the item behind is the one returned to
    walkOf (lane, playlist, rng, 2);
    REQUIRE (lane.cursor == 2);
    seatBehind (lane, lane.walk[1]);
    REQUIRE (lane.cursor == 1);
    seatBehind (lane, lane.walk[3]);
    REQUIRE (lane.cursor == 1);
}

TEST_CASE ("random and shuffle are one kind: switching between them keeps the walk", "[lane]") {
    std::mt19937 rng (15);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (5, "random"), true, t0 ());
    walkOf (lane, playlist, rng, 2);
    const auto walk = lane.walk;
    applySet (lane, playlist, makePlaylist (5, "shuffle"), true, t0 ());
    REQUIRE (lane.walk == walk);
    REQUIRE (lane.cursor == 1);

    // sequential and static are one kind too
    applySet (lane, playlist, makePlaylist (5, "sequential"), true, t0 ());
    REQUIRE (lane.cursor == std::stoi (lane.current.uiId.substr (2)));
    const int seated = lane.cursor;
    applySet (lane, playlist, makePlaylist (5, "static"), true, t0 ());
    REQUIRE (lane.cursor == seated);
}

TEST_CASE ("a stale next cycle is rebuilt and a fresh ordered walk starts after the on-screen item", "[lane]") {
    std::mt19937 rng (16);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (3, "shuffle"), true, t0 ());
    walkOf (lane, playlist, rng, 3);
    lane.nextCycle = { "gone", "wp0", "wp1" };
    REQUIRE (nextUp (lane, playlist) == "");
    REQUIRE (pickNext (lane, playlist, rng) < 3);

    Lane restored;
    restored.current = playlist.entries[1];
    Playlist ordered;
    applySet (restored, ordered, makePlaylist (3, "sequential"), true, t0 ());
    restored.walk.clear (); // as a state file from an older build leaves it
    restored.cursor = -1;
    REQUIRE (pickNext (restored, ordered, rng) == 2);
}

TEST_CASE ("both fit layers persist with the lane and a hand-edited value cannot escape the contract", "[lane]") {
    Lane lane;
    lane.fit = { .zoom = 1.5f, .panX = 0.25f, .panY = -0.5f };
    lane.look.fit = { .zoom = 1.2f, .panX = -1.0f, .panY = 1.0f };

    const auto restored = laneFromJson (toJson (lane));
    CHECK (restored.fit.zoom == 1.5f);
    CHECK (restored.fit.panX == 0.25f);
    CHECK (restored.fit.panY == -0.5f);
    CHECK (restored.look.fit.zoom == 1.2f);
    CHECK (restored.look.fit.panX == -1.0f);
    CHECK (restored.look.fit.panY == 1.0f);

    // out of range clamps, a wrong type is the default, a missing block is identity
    auto edited = toJson (lane);
    edited["fit"] = { { "zoom", 9.0 }, { "pan_x", -3.0 }, { "pan_y", "up" } };
    edited["look"].erase ("fit");
    const auto clamped = laneFromJson (edited);
    CHECK (clamped.fit.zoom == 2.0f);
    CHECK (clamped.fit.panX == -1.0f);
    CHECK (clamped.fit.panY == 0.0f);
    CHECK (clamped.look.fit.zoom == 1.0f);
    CHECK (clamped.look.fit.panX == 0.0f);

    edited["fit"] = "none";
    edited["look"]["fit"] = 3;
    const auto mistyped = laneFromJson (edited);
    CHECK (mistyped.fit.zoom == 1.0f);
    CHECK (mistyped.look.fit.zoom == 1.0f);
}

TEST_CASE ("a same-set push to a running lane keeps its countdown and its forward history", "[lane]") {
    Lane lane;
    Playlist playlist;
    const auto set = makePlaylist (3, "sequential");

    applySet (lane, playlist, set, true, t0 ());
    lane.forward.push_back (set.entries[2]);

    // the panel re-pushes its policy on open and after every conf save: nothing changed
    applySet (lane, playlist, set, true, t0 () + std::chrono::seconds (100));
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (100)) == 800);
    REQUIRE (lane.forward.size () == 1);

    // a different interval is a new set: the clock restarts, forward targets are gone
    auto longer = set;
    longer.intervalSeconds = 1200;
    applySet (lane, playlist, longer, true, t0 () + std::chrono::seconds (200));
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (200)) == 1200);
    REQUIRE (lane.forward.empty ());

    // a changed membership too
    lane.forward.push_back (set.entries[1]);
    applySet (lane, playlist, makePlaylist (4, "sequential"), true, t0 () + std::chrono::seconds (300));
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (300)) == 900);
    REQUIRE (lane.forward.empty ());
}

TEST_CASE ("a restart resumes a running lane's remainder less the downtime", "[lane]") {
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (3, "sequential"), true, t0 ());

    resumeCountdown (lane, playlist, 300 * 1000, 100 * 1000, t0 ());
    REQUIRE (nextInSeconds (lane, playlist, t0 ()) == 200);

    // away longer than the remainder: due on the first tick, never negative
    resumeCountdown (lane, playlist, 300 * 1000, 5000 * 1000, t0 ());
    REQUIRE (nextInSeconds (lane, playlist, t0 ()) == 0);
    REQUIRE (dueForAdvance (lane, playlist, t0 ()));

    // no remainder saved (an older state file): the full interval
    resumeCountdown (lane, playlist, -1, 100 * 1000, t0 ());
    REQUIRE (nextInSeconds (lane, playlist, t0 ()) == 900);

    // a frozen lane keeps its frozen remainder; a disabled one has no countdown
    lane.frozenRemainingMs = 250 * 1000;
    resumeCountdown (lane, playlist, 300 * 1000, 100 * 1000, t0 ());
    REQUIRE (nextInSeconds (lane, playlist, t0 ()) == 250);
    lane.frozenRemainingMs = -1;
    lane.enabled = false;
    resumeCountdown (lane, playlist, 300 * 1000, 100 * 1000, t0 ());
    REQUIRE (nextInSeconds (lane, playlist, t0 ()) == -1);
}

TEST_CASE ("leaving static is a new play; a shuffle switch and a paused re-push are not", "[lane]") {
    Lane lane;
    Playlist playlist;
    const auto set = makePlaylist (3, "sequential");
    applySet (lane, playlist, set, true, t0 ());

    // sequential to shuffle keeps the clock (the walk is redrawn, the countdown is not)
    applySet (lane, playlist, makePlaylist (3, "shuffle"), true, t0 () + std::chrono::seconds (100));
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (100)) == 800);

    // static stops the clock; coming back hours later starts a full interval, not an overdue advance
    applySet (lane, playlist, makePlaylist (3, "static"), true, t0 () + std::chrono::seconds (200));
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (200)) == -1);
    applySet (lane, playlist, set, true, t0 () + std::chrono::seconds (8000));
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (8000)) == 900);
    REQUIRE_FALSE (dueForAdvance (lane, playlist, t0 () + std::chrono::seconds (8000)));

    // the panel sends static DISABLED: still no countdown while static, still a full interval after
    applySet (lane, playlist, makePlaylist (3, "static"), false, t0 () + std::chrono::seconds (8100));
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (8100)) == -1);
    applySet (lane, playlist, set, true, t0 () + std::chrono::seconds (20000));
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (20000)) == 900);
    REQUIRE (lane.frozenRemainingMs == -1);

    // a disabled lane with no frozen value (an older state file) re-pushed the same set shows
    // the full interval, not idle
    lane.enabled = false;
    lane.frozenRemainingMs = -1;
    applySet (lane, playlist, set, false, t0 () + std::chrono::seconds (8100));
    REQUIRE (lane.frozenRemainingMs == 900 * 1000);
}

TEST_CASE ("a paused lane: an overdue pause resumes due, a new wallpaper while paused is a full interval", "[lane]") {
    Lane lane;
    Playlist playlist;
    const auto set = makePlaylist (3, "sequential");
    applySet (lane, playlist, set, true, t0 ());

    // outputs were released past the interval, then the user paused: nothing negative, due on resume
    applySet (lane, playlist, set, false, t0 () + std::chrono::seconds (5000));
    REQUIRE (lane.frozenRemainingMs == 0 * 1000);
    applySet (lane, playlist, set, true, t0 () + std::chrono::seconds (5100));
    REQUIRE (dueForAdvance (lane, playlist, t0 () + std::chrono::seconds (5100)));

    // a step lands a new wallpaper at t+6000; pause with 800 left; step again to another
    // wallpaper (next, prev or a click): the new one gets the full interval when rotation resumes
    restartCountdown (lane, playlist, t0 () + std::chrono::seconds (6000));
    applySet (lane, playlist, set, false, t0 () + std::chrono::seconds (6100));
    REQUIRE (lane.frozenRemainingMs == 800 * 1000);
    restartCountdown (lane, playlist, t0 () + std::chrono::seconds (6200));
    REQUIRE (lane.frozenRemainingMs == 900 * 1000);
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (6200)) == 900);
    applySet (lane, playlist, set, true, t0 () + std::chrono::seconds (6300));
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (6300)) == 900);

    // running: a step simply restarts
    restartCountdown (lane, playlist, t0 () + std::chrono::seconds (6400));
    REQUIRE (lane.frozenRemainingMs == -1);
    REQUIRE (nextInSeconds (lane, playlist, t0 () + std::chrono::seconds (6400)) == 900);
}

TEST_CASE ("a pause keeps the fraction of a second and the countdown is reported in milliseconds", "[lane]") {
    Lane lane;
    Playlist playlist;
    const auto set = makePlaylist (3, "sequential");
    applySet (lane, playlist, set, true, t0 ());

    const auto at = t0 () + std::chrono::milliseconds (100 * 1000 + 700);
    REQUIRE (nextInMs (lane, playlist, at) == 800 * 1000 - 700);
    REQUIRE (nextInSeconds (lane, playlist, at) == 799);

    applySet (lane, playlist, set, false, at);
    REQUIRE (lane.frozenRemainingMs == 800 * 1000 - 700);

    // resume: the clock continues from the same millisecond
    applySet (lane, playlist, set, true, at + std::chrono::seconds (30));
    REQUIRE (nextInMs (lane, playlist, at + std::chrono::seconds (30)) == 800 * 1000 - 700);

    // the state file carries the milliseconds and still reads an older whole-second file
    applySet (lane, playlist, set, false, at + std::chrono::seconds (31));
    auto j = toJson (lane);
    REQUIRE (j["frozen_remaining_ms"].get<int64_t> () == lane.frozenRemainingMs);
    REQUIRE (laneFromJson (j).frozenRemainingMs == lane.frozenRemainingMs);
    j.erase ("frozen_remaining_ms");
    j["frozen_remaining_s"] = 42;
    REQUIRE (laneFromJson (j).frozenRemainingMs == 42 * 1000);
}

TEST_CASE ("the title kept at a release names the wallpaper while no project is loaded", "[lane]") {
    ShownTitle kept;
    REQUIRE (statusTitle (kept, "1505438974", std::nullopt).empty ());

    keepTitle (kept, "1505438974", std::string ("Deep Space"));
    REQUIRE (statusTitle (kept, "1505438974", std::nullopt) == "Deep Space");
    REQUIRE (statusTitle (kept, "2105138680", std::nullopt).empty ());
    REQUIRE (statusTitle (kept, "1505438974", std::string ("Loaded")) == "Loaded");

    // a release with nothing loaded keeps the earlier title
    keepTitle (kept, "2105138680", std::nullopt);
    REQUIRE (statusTitle (kept, "1505438974", std::nullopt) == "Deep Space");
}
