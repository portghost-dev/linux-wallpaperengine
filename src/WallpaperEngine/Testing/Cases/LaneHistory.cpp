#include <catch2/catch_test_macros.hpp>

#include <algorithm>
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

Entry detourEntry (const std::string& id) {
    Entry entry;
    entry.id = id;
    entry.uiId = id;
    return entry;
}

Clock::time_point t0 () { return Clock::time_point {} + std::chrono::hours (1); }

/** The timer or next without forward history: pick and show. */
std::string advance (Lane& lane, const Playlist& playlist, std::mt19937& rng) {
    jumpToEnd (lane);
    const auto pick = pickNext (lane, playlist, rng);
    REQUIRE (pick < playlist.entries.size ());
    recordShow (lane, playlist.entries[pick], true);
    return lane.current.uiId;
}

/** Prev as the application runs it: the show lands first (no history record, the cursor
 *  seated on what is on screen), then the books move. */
std::string back (Lane& lane, const Playlist& playlist) {
    const auto target = backTarget (lane, playlist);
    REQUIRE (target.has_value ());
    const Entry before = lane.current;
    recordShow (lane, *target, false);
    seatCursor (lane, target->uiId.empty () ? target->id : target->uiId);
    commitBack (lane, playlist, before, *target);
    return lane.current.uiId;
}

std::string forward (Lane& lane, const Playlist& playlist, std::mt19937& rng) {
    const auto target = forwardTarget (lane);

    if (!target.has_value ()) {
	const auto pick = pickNext (lane, playlist, rng);
	recordShow (lane, playlist.entries[pick], true);
	return lane.current.uiId;
    }

    const Entry before = lane.current;
    commitForward (lane, before, *target);
    return lane.current.uiId;
}
} // namespace

TEST_CASE ("show k, next, back, next: back is a true back and next returns (A2)", "[history]") {
    std::mt19937 rng (1);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (4, "sequential"), true, t0 ());
    recordShow (lane, playlist.entries[1], true); // show k = wp1 (a detour: cursor holds at -1)
    seatCursor (lane, "wp1");

    REQUIRE (advance (lane, playlist, rng) == "wp2");
    REQUIRE (back (lane, playlist) == "wp1");
    REQUIRE (lane.forward.size () == 1);
    REQUIRE (aheadUp (lane, playlist) == "wp2");
    REQUIRE (forward (lane, playlist, rng) == "wp2");
    REQUIRE (lane.forward.empty ());
    REQUIRE (aheadUp (lane, playlist) == "wp3");
}

TEST_CASE ("a detour on a non-member: the timer moves on and back returns to it (A4)", "[history]") {
    std::mt19937 rng (2);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (3, "sequential"), true, t0 ());
    advance (lane, playlist, rng); // wp0, cursor 0
    recordShow (lane, detourEntry ("x"), true);
    REQUIRE (lane.cursor == 0);
    REQUIRE (advance (lane, playlist, rng) == "wp1");
    REQUIRE (back (lane, playlist) == "x");
    REQUIRE (back (lane, playlist) == "wp0");
    REQUIRE (aheadUp (lane, playlist) == "x");
}

TEST_CASE ("a re-show of the item on screen adds no history entry (A5, R13)", "[history]") {
    std::mt19937 rng (3);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (3, "sequential"), true, t0 ());
    advance (lane, playlist, rng);
    advance (lane, playlist, rng);
    back (lane, playlist);
    REQUIRE (lane.forward.size () == 1);
    Entry again = lane.current;
    again.args["speed"] = 2.0;
    recordShow (lane, again, true);
    REQUIRE (lane.history.empty ());
    REQUIRE (lane.forward.size () == 1); // forward history survives a re-show
    REQUIRE (lane.current.args["speed"] == 2.0);
    // A, B, A is a real path
    recordShow (lane, playlist.entries[1], true);
    recordShow (lane, playlist.entries[0], true);
    REQUIRE (lane.history.size () == 2);
    REQUIRE (lane.forward.empty ()); // a new show discards forward history
}

TEST_CASE ("back with nothing behind steps the walk back, wrapping; static walks too (A9)", "[history]") {
    std::mt19937 rng (4);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (3, "sequential"), true, t0 ());
    advance (lane, playlist, rng); // wp0, cursor 0
    REQUIRE (previousUp (lane, playlist) == "wp2");
    REQUIRE (back (lane, playlist) == "wp2");
    REQUIRE (lane.cursor == 2);
    REQUIRE (lane.history.size () == 1); // a walk step back is a real show
    REQUIRE (advance (lane, playlist, rng) == "wp0");

    Playlist one;
    Lane lone;
    applySet (lone, one, makePlaylist (1, "sequential"), true, t0 ());
    advance (lone, one, rng);
    REQUIRE_FALSE (backTarget (lone, one).has_value ());
    REQUIRE_FALSE (backEnabled (lone, one));
    recordShow (lone, detourEntry ("elsewhere"), true); // history behind, still one item: off
    REQUIRE_FALSE (backTarget (lone, one).has_value ());
    REQUIRE_FALSE (backEnabled (lone, one));

    Playlist fixed;
    Lane still;
    applySet (still, fixed, makePlaylist (3, "static"), true, t0 ());
    advance (still, fixed, rng);
    advance (still, fixed, rng);
    // static steps back through the walk, never through history: on wp1, back is wp0, and the
    // Last line reads the same
    REQUIRE (backTarget (still, fixed).has_value ());
    REQUIRE (backTarget (still, fixed)->id == "wp0");
    REQUIRE (backEnabled (still, fixed));
    REQUIRE (previousUp (still, fixed) == "wp0");
    // three steps back visit the walk in reverse, wrapping, and the cursor follows the screen
    REQUIRE (back (still, fixed) == "wp0");
    REQUIRE (still.cursor == 0);
    REQUIRE (back (still, fixed) == "wp2");
    REQUIRE (still.cursor == 2);
    REQUIRE (back (still, fixed) == "wp1");
    REQUIRE (still.cursor == 1);
    REQUIRE (previousUp (still, fixed) == "wp0");
    REQUIRE (aheadUp (still, fixed) == "wp2");
    REQUIRE (backEnabled (lane, playlist));
}

TEST_CASE ("the timer while backed up jumps to the end then advances (A10, R14, R25)", "[history]") {
    std::mt19937 rng (5);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (5, "sequential"), true, t0 ());
    for (int i = 0; i < 3; i++) {
	advance (lane, playlist, rng); // wp0 wp1 wp2, cursor 2
    }
    back (lane, playlist);
    back (lane, playlist);
    REQUIRE (lane.current.uiId == "wp0");
    REQUIRE (lane.forward.size () == 2);

    REQUIRE (advance (lane, playlist, rng) == "wp3"); // not wp1: the walk continues from wp2
    REQUIRE (lane.forward.empty ());
    REQUIRE (lane.history.size () == 3);
    REQUIRE (previousUp (lane, playlist) == "wp2");
}

TEST_CASE ("a push truncates forward history and the books stay bounded", "[history]") {
    std::mt19937 rng (6);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (4, "sequential"), true, t0 ());
    advance (lane, playlist, rng);
    advance (lane, playlist, rng);
    back (lane, playlist);
    REQUIRE (lane.forward.size () == 1);
    applySet (lane, playlist, makePlaylist (5, "sequential"), true, t0 ());
    REQUIRE (lane.forward.empty ());

    for (int i = 0; i < 130; i++) {
	recordShow (lane, detourEntry ("d" + std::to_string (i)), true);
    }

    for (int i = 0; i < 60; i++) {
	back (lane, playlist);
    }

    REQUIRE (lane.history.size () + lane.forward.size () <= HISTORY_BOUND);
    REQUIRE (lane.forward.size () == 60);

    const auto copy = laneFromJson (toJson (lane));
    REQUIRE (copy.forward.size () == 60);
    REQUIRE (copy.history.size () == lane.history.size ());
}

TEST_CASE ("the books are right when the show was recorded before the commit, as the app does", "[history]") {
    std::mt19937 rng (7);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (4, "sequential"), true, t0 ());
    advance (lane, playlist, rng);
    advance (lane, playlist, rng); // wp0 behind, wp1 on screen
    const auto backEntry = backTarget (lane, playlist);
    const Entry before = lane.current;
    recordShow (lane, *backEntry, false); // the application applies the show first
    commitBack (lane, playlist, before, *backEntry);
    REQUIRE (lane.current.uiId == "wp0");
    REQUIRE (lane.forward.size () == 1);
    REQUIRE (lane.forward.front ().uiId == "wp1");
    REQUIRE (lane.history.empty ());

    const auto ahead = forwardTarget (lane);
    const Entry before2 = lane.current;
    recordShow (lane, *ahead, false);
    commitForward (lane, before2, *ahead);
    REQUIRE (lane.current.uiId == "wp1");
    REQUIRE (lane.forward.empty ());
    REQUIRE (lane.history.size () == 1);
    REQUIRE (lane.history.back ().uiId == "wp0");
    REQUIRE (aheadUp (lane, playlist) == "wp2");
}

TEST_CASE ("a detour, back, then the timer: the walk continues as if forward were pressed (R10, R14)", "[history]") {
    std::mt19937 rng (8);
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (4, "sequential"), true, t0 ());
    advance (lane, playlist, rng); // wp0, cursor 0
    recordShow (lane, playlist.entries[2], true); // a click on wp2: a detour, cursor holds
    back (lane, playlist); // wp0 again, wp2 ahead
    REQUIRE (advance (lane, playlist, rng) == "wp1"); // not wp3: the detour never moved the cursor
    REQUIRE (lane.history.back ().uiId == "wp2");
}

TEST_CASE ("back never names the wallpaper on screen: the walk fallback skips it", "[history]") {
    Lane lane;
    Playlist playlist;
    applySet (lane, playlist, makePlaylist (3, "sequential"), true, t0 ());
    recordShow (lane, playlist.entries[2], true); // first show is a click on the last walk item
    REQUIRE (previousUp (lane, playlist) == "wp1");
    const auto target = backTarget (lane, playlist);
    REQUIRE (target.has_value ());
    REQUIRE (target->uiId == "wp1");
    REQUIRE (backEnabled (lane, playlist));
    const Entry before = lane.current;
    commitBack (lane, playlist, before, *target);
    REQUIRE (lane.current.uiId == "wp1");
    REQUIRE (lane.cursor == 1);
}

TEST_CASE ("a state file with oversized books is bounded on load", "[history]") {
    Lane lane;
    for (int i = 0; i < 150; i++) {
	lane.forward.push_back (detourEntry ("f" + std::to_string (i)));
	lane.history.push_back (detourEntry ("h" + std::to_string (i)));
    }
    const auto loaded = laneFromJson (toJson (lane));
    REQUIRE (loaded.history.size () + loaded.forward.size () <= HISTORY_BOUND);
}
