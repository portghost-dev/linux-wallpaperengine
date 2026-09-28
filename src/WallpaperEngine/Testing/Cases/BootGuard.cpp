#include <catch2/catch_test_macros.hpp>

#include <chrono>
#include <map>
#include <nlohmann/json.hpp>
#include <sstream>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#include "WallpaperEngine/Api/Lane.h"
#include "WallpaperEngine/Application/BootGuard.h"
#include "WallpaperEngine/Logging/Log.h"

using namespace WallpaperEngine::Application;
namespace Api = WallpaperEngine::Api;

namespace {
nlohmann::json boot (const bool survived) { return { { "t", 1 }, { "survived", survived } }; }

const std::string HOLDING = "Holding automatic wallpaper changes: the restore was refused after two quick crashes. "
			    "Show a wallpaper or pick a playlist to resume them.";
const std::string HELD_SHOW = "Held an automatic show: the restore was refused after two quick crashes.";

BootGuard held () {
    BootGuard guard;
    guard.evaluate (nlohmann::json::array ({ boot (false), boot (false) }));
    return guard;
}

struct Rotation {
    Api::Lane lane;
    Api::Playlist playlist;
    Api::Schedule schedule;
};

Rotation dueRotation (const Api::Clock::time_point now) {
    Rotation rotation;
    Api::Entry first;
    first.id = "a";
    Api::Entry second;
    second.id = "b";
    rotation.playlist.slug = "p";
    rotation.playlist.entries = { first, second };
    rotation.playlist.order = "sequential";
    rotation.playlist.intervalSeconds = 60;
    rotation.lane.playlistSlug = "p";
    rotation.lane.enabled = true;
    rotation.lane.lastShow = now - std::chrono::minutes (10);
    return rotation;
}

std::map<std::string, Api::Playlist> library () {
    std::map<std::string, Api::Playlist> playlists { { "default", Api::Playlist {} } };

    for (const std::string slug : { "main", "night" }) {
	Api::Playlist playlist;
	playlist.slug = slug;
	playlist.order = "sequential";
	playlist.intervalSeconds = 60;

	for (const std::string id : { "a", "b" }) {
	    Api::Entry entry;
	    entry.id = slug + id;
	    playlist.entries.push_back (entry);
	}

	playlists[slug] = playlist;
    }

    return playlists;
}

Api::Schedule mainByDay () {
    Api::Schedule schedule;
    schedule.enabled = true;
    schedule.entries = { { .minute = 7 * 60, .slug = "main" }, { .minute = 20 * 60, .slug = "night" } };
    return schedule;
}

nlohmann::json manualLanes (const bool manual) {
    return { { "lanes", nlohmann::json::array ({ { { "id", "all" }, { "playlist", "p" }, { "manual", manual } } }) } };
}

size_t occurrences (const std::string& text, const std::string& line) {
    size_t count = 0;

    for (auto at = text.find (line); at != std::string::npos; at = text.find (line, at + line.size ())) {
	count++;
    }

    return count;
}
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

TEST_CASE ("a held boot advances neither the lane timer nor the legacy playlist timer", "[bootguard]") {
    const auto now = Api::Clock::now ();
    const auto rotation = dueRotation (now);
    REQUIRE (Api::dueForAdvance (rotation.lane, rotation.playlist, now));

    SECTION ("held") {
	auto guard = held ();
	auto* out = new std::ostringstream ();
	sLog.addOutput (out);

	auto waiting = rotation;
	waiting.lane.lastShow = now;
	CHECK_FALSE (guard.rotationMayAdvance (true, waiting.lane, waiting.playlist, now));
	CHECK_FALSE (guard.rotationMayAdvance (false, rotation.lane, rotation.playlist, now));
	CHECK_FALSE (guard.playlistTimerMayAdvance (now + std::chrono::seconds (1), now));
	const std::string beforeStop = out->str ();

	CHECK_FALSE (guard.rotationMayAdvance (true, rotation.lane, rotation.playlist, now));
	CHECK_FALSE (guard.playlistTimerMayAdvance (now - std::chrono::seconds (1), now));
	CHECK_FALSE (guard.rotationMayAdvance (true, rotation.lane, rotation.playlist, now + std::chrono::seconds (5)));
	const std::string lines = out->str ();
	out->setstate (std::ios::badbit);

	CHECK (beforeStop.empty ());
	CHECK (occurrences (lines, HOLDING) == 1);
	CHECK (guard.restoreRefused ());
    }

    SECTION ("unheld control") {
	BootGuard guard;
	auto* out = new std::ostringstream ();
	sLog.addOutput (out);

	CHECK (guard.rotationMayAdvance (true, rotation.lane, rotation.playlist, now));
	CHECK (guard.playlistTimerMayAdvance (now - std::chrono::seconds (1), now));
	const std::string lines = out->str ();
	out->setstate (std::ios::badbit);

	CHECK (lines.empty ());
    }
}

TEST_CASE ("a held boot does not let the schedule switch playlists", "[bootguard]") {
    Api::Schedule schedule;
    schedule.enabled = true;
    schedule.entries = { { .minute = 7 * 60, .slug = "day" }, { .minute = 20 * 60, .slug = "night" } };
    Api::scheduleTick (schedule, "day", 21 * 60);
    REQUIRE (schedule.pending == "night");

    Api::Playlist bound;
    Api::Entry only;
    only.id = "a";
    bound.slug = "day";
    bound.order = "static";
    bound.entries = { only };

    SECTION ("held") {
	auto guard = held ();
	auto* out = new std::ostringstream ();
	sLog.addOutput (out);

	CHECK_FALSE (guard.scheduleMayApply (true, schedule, "", bound));
	CHECK_FALSE (guard.scheduleMayApply (true, schedule, "", bound));
	const std::string lines = out->str ();
	out->setstate (std::ios::badbit);

	CHECK (occurrences (lines, HOLDING) == 1);
	CHECK (schedule.pending == "night");
    }

    SECTION ("unheld control") {
	BootGuard guard;
	CHECK (guard.scheduleMayApply (true, schedule, "", bound));
    }
}

TEST_CASE ("a held boot answers an automatic show as held and logs each one", "[bootguard]") {
    SECTION ("held") {
	auto guard = held ();
	auto* out = new std::ostringstream ();
	sLog.addOutput (out);

	CHECK (guard.holdsShow ("show", { { "id", "a" }, { "automatic", true } }));
	CHECK (guard.holdsShow ("show", { { "id", "b" }, { "automatic", true } }));
	CHECK_FALSE (guard.holdsShow ("show", { { "id", "a" } }));
	CHECK_FALSE (guard.holdsShow ("show", { { "id", "a" }, { "automatic", false } }));
	const std::string lines = out->str ();
	out->setstate (std::ios::badbit);

	CHECK (occurrences (lines, HELD_SHOW) == 2);
	CHECK (occurrences (lines, HOLDING) == 0);
	CHECK (guard.restoreRefused ());
    }

    SECTION ("unheld control") {
	BootGuard guard;
	auto* out = new std::ostringstream ();
	sLog.addOutput (out);

	CHECK_FALSE (guard.holdsShow ("show", { { "id", "a" }, { "automatic", true } }));
	const std::string lines = out->str ();
	out->setstate (std::ios::badbit);

	CHECK (lines.empty ());
    }
}

TEST_CASE ("a held boot keeps the rotation it is sent and runs it from the release", "[bootguard]") {
    const auto now = Api::Clock::now ();
    auto guard = held ();
    Api::Lane lane;
    Api::Playlist stored;
    Api::Schedule schedule;
    auto incoming = dueRotation (now).playlist;
    Api::applySet (lane, stored, incoming, true, now - std::chrono::minutes (10));
    REQUIRE (lane.enabled);

    CHECK_FALSE (guard.rotationMayAdvance (true, lane, stored, now));
    CHECK_FALSE (guard.release (
	"lanes-set", { { "lanes", nlohmann::json::array ({ { { "id", "all" }, { "enabled", true } } }) } }, true, lane,
	stored, schedule, now
    ));
    CHECK (lane.enabled);
    CHECK (guard.restoreRefused ());

    const auto later = now + std::chrono::minutes (1);
    REQUIRE (guard.release ("show", { { "id", "a" } }, true, lane, stored, schedule, later));
    CHECK (lane.enabled);
    CHECK_FALSE (guard.rotationMayAdvance (true, lane, stored, later));
    CHECK (guard.rotationMayAdvance (true, lane, stored, later + std::chrono::seconds (60)));
}

TEST_CASE ("a show or next or prev without automatic true and a manual lanes-set release the hold", "[bootguard]") {
    const auto now = Api::Clock::now ();
    const std::vector<std::pair<std::string, nlohmann::json>> releasing = {
	{ "show", { { "id", "a" } } },         { "show", { { "id", "a" }, { "automatic", false } } },
	{ "next", nlohmann::json::object () }, { "prev", nlohmann::json::object () },
	{ "lanes-set", manualLanes (true) },
    };

    for (const auto& [cmd, args] : releasing) {
	INFO (cmd << " " << args.dump ());
	auto guard = held ();
	auto rotation = dueRotation (now);
	CHECK (guard.release (cmd, args, true, rotation.lane, rotation.playlist, rotation.schedule, now));
	CHECK_FALSE (guard.restoreRefused ());
    }
}

TEST_CASE ("a failed request or an automatic one or any other verb keeps the hold", "[bootguard]") {
    const auto now = Api::Clock::now ();
    const std::vector<std::tuple<std::string, nlohmann::json, bool>> kept = {
	{ "show", { { "id", "a" } }, false },
	{ "next", nlohmann::json::object (), false },
	{ "prev", nlohmann::json::object (), false },
	{ "lanes-set", manualLanes (true), false },
	{ "show", { { "id", "a" }, { "automatic", true } }, true },
	{ "next", { { "automatic", true } }, true },
	{ "prev", { { "automatic", true } }, true },
	{ "lanes-set", manualLanes (false), true },
	{ "playlist-set", { { "slug", "p" }, { "entries", nlohmann::json::array () } }, true },
	{ "schedule-set", { { "enabled", false }, { "entries", nlohmann::json::array () } }, true },
	{ "rotate-set", { { "entries", nlohmann::json::array () } }, true },
	{ "set-fps", { { "fps", 30 } }, true },
    };

    for (const auto& [cmd, args, ok] : kept) {
	INFO (cmd << " " << args.dump () << (ok ? " ok" : " failed"));
	auto guard = held ();
	auto rotation = dueRotation (now);
	const auto before = rotation.lane.lastShow;
	CHECK_FALSE (guard.release (cmd, args, ok, rotation.lane, rotation.playlist, rotation.schedule, now));
	CHECK (guard.restoreRefused ());
	CHECK (rotation.lane.lastShow == before);
    }
}

TEST_CASE ("a release starts a full lane interval from the release", "[bootguard]") {
    const auto now = Api::Clock::now ();
    auto rotation = dueRotation (now);

    SECTION ("a show") {
	auto guard = held ();
	REQUIRE (
	    guard.release ("show", { { "id", "a" } }, true, rotation.lane, rotation.playlist, rotation.schedule, now)
	);
	CHECK_FALSE (guard.rotationMayAdvance (true, rotation.lane, rotation.playlist, now));
	CHECK_FALSE (
	    guard.rotationMayAdvance (true, rotation.lane, rotation.playlist, now + std::chrono::seconds (59))
	);
	CHECK (guard.rotationMayAdvance (true, rotation.lane, rotation.playlist, now + std::chrono::seconds (60)));
    }

    SECTION ("a manual lanes-set") {
	auto guard = held ();
	REQUIRE (guard.release (
	    "lanes-set", manualLanes (true), true, rotation.lane, rotation.playlist, rotation.schedule, now
	));
	CHECK_FALSE (guard.rotationMayAdvance (true, rotation.lane, rotation.playlist, now));
	CHECK (guard.rotationMayAdvance (true, rotation.lane, rotation.playlist, now + std::chrono::seconds (60)));
    }

    SECTION ("unheld control") {
	BootGuard guard;
	const auto before = rotation.lane.lastShow;
	CHECK_FALSE (
	    guard.release ("show", { { "id", "a" } }, true, rotation.lane, rotation.playlist, rotation.schedule, now)
	);
	CHECK_FALSE (guard.release (
	    "show", { { "id", "a" }, { "automatic", true } }, true, rotation.lane, rotation.playlist, rotation.schedule,
	    now
	));
	CHECK (rotation.lane.lastShow == before);
	CHECK (guard.rotationMayAdvance (true, rotation.lane, rotation.playlist, now));
    }
}

TEST_CASE (
    "a held boot sent schedule-set then a policy lanes-set ends bound and enabled and rotates after the release",
    "[bootguard]"
) {
    const auto now = Api::Clock::now ();
    auto guard = held ();
    auto playlists = library ();
    Api::Lane lane;
    auto schedule = mainByDay ();
    Api::scheduleTick (schedule, lane.playlistSlug, 21 * 60);
    REQUIRE (schedule.pending == "night");

    CHECK (Api::laneSet (lane, playlists, schedule, "main", true, false, now));
    CHECK (lane.playlistSlug == "main");
    CHECK (lane.enabled);
    CHECK_FALSE (
	guard.rotationMayAdvance (true, lane, playlists.at (lane.playlistSlug), now + std::chrono::minutes (2))
    );

    const auto later = now + std::chrono::minutes (2);
    REQUIRE (guard.release ("show", { { "id", "a" } }, true, lane, playlists.at (lane.playlistSlug), schedule, later));
    CHECK_FALSE (guard.rotationMayAdvance (true, lane, playlists.at (lane.playlistSlug), later));
    CHECK (guard.rotationMayAdvance (true, lane, playlists.at (lane.playlistSlug), later + std::chrono::seconds (60)));
}

TEST_CASE ("an unheld engine with no restored state sent the same order ends bound", "[bootguard]") {
    const auto now = Api::Clock::now ();
    BootGuard guard;
    auto playlists = library ();
    Api::Lane lane;
    auto schedule = mainByDay ();
    Api::scheduleTick (schedule, lane.playlistSlug, 21 * 60);

    CHECK (Api::laneSet (lane, playlists, schedule, "main", true, false, now));
    CHECK (lane.playlistSlug == "main");
    CHECK (lane.enabled);
    CHECK (guard.rotationMayAdvance (true, lane, playlists.at (lane.playlistSlug), now + std::chrono::seconds (60)));
}

TEST_CASE ("a bound lane under the schedule keeps its binding on a policy push", "[bootguard]") {
    const auto now = Api::Clock::now ();
    auto playlists = library ();
    Api::Lane lane;
    Api::Schedule off;
    REQUIRE (Api::laneSet (lane, playlists, off, "night", true, false, now));
    auto schedule = mainByDay ();
    Api::scheduleTick (schedule, lane.playlistSlug, 12 * 60);

    CHECK_FALSE (Api::laneSet (lane, playlists, schedule, "main", true, false, now));
    CHECK (lane.playlistSlug == "night");
    CHECK (lane.enabled);
}

TEST_CASE ("a lanes-set keeps its enabled flag on a lane with no binding for the binding that follows", "[bootguard]") {
    const auto now = Api::Clock::now ();
    auto playlists = library ();
    Api::Lane lane;
    Api::Schedule off;

    CHECK (Api::laneSet (lane, playlists, off, lane.playlistSlug, true, false, now));
    CHECK (lane.playlistSlug == "default");
    CHECK (lane.enabled);

    CHECK (Api::laneSet (lane, playlists, off, "main", lane.enabled, false, now));
    CHECK (lane.playlistSlug == "main");
    CHECK (lane.enabled);
}

TEST_CASE ("a release holds a pending schedule switch on a static lane until the next boundary", "[bootguard]") {
    const auto now = Api::Clock::now ();
    auto playlists = library ();
    Api::Lane lane;
    Api::Entry scene;
    scene.id = "a";
    Api::Playlist incoming;
    incoming.order = "static";
    incoming.entries = { scene };
    Api::applySet (lane, playlists.at ("default"), incoming, false, now);
    const auto& bound = playlists.at (lane.playlistSlug);
    REQUIRE (bound.order == "static");

    const auto releaseAtNight = [&lane, &bound, now] (Api::Schedule& schedule) {
	auto guard = held ();
	Api::scheduleTick (schedule, lane.playlistSlug, 21 * 60);
	REQUIRE (schedule.pending == "night");
	CHECK_FALSE (guard.scheduleMayApply (true, schedule, "", bound));
	REQUIRE (guard.release ("show", { { "id", "x" } }, true, lane, bound, schedule, now));
	CHECK (schedule.held);
	CHECK (schedule.pending.empty ());
	Api::scheduleTick (schedule, lane.playlistSlug, 21 * 60 + 1);
	CHECK_FALSE (guard.scheduleMayApply (true, schedule, "", bound));
	return guard;
    };

    SECTION ("a schedule whose next boundary names the bound playlist") {
	Api::Schedule schedule;
	schedule.enabled = true;
	schedule.entries = { { .minute = 7 * 60, .slug = "default" }, { .minute = 20 * 60, .slug = "night" } };
	auto guard = releaseAtNight (schedule);

	CHECK (Api::scheduleTick (schedule, lane.playlistSlug, 7 * 60));
	CHECK_FALSE (schedule.held);
	CHECK (schedule.pending.empty ());
	CHECK (Api::scheduleTick (schedule, lane.playlistSlug, 20 * 60));
	CHECK (guard.scheduleMayApply (true, schedule, "", bound));
    }

    SECTION ("a next boundary that names another playlist") {
	auto schedule = mainByDay ();
	auto guard = releaseAtNight (schedule);

	CHECK (Api::scheduleTick (schedule, lane.playlistSlug, 7 * 60));
	CHECK_FALSE (schedule.held);
	CHECK (schedule.pending == "main");
	CHECK (guard.scheduleMayApply (true, schedule, "", bound));
    }
}

TEST_CASE ("a held boot holds an automatic next or prev like an automatic show", "[bootguard]") {
    SECTION ("held") {
	auto guard = held ();
	auto* out = new std::ostringstream ();
	sLog.addOutput (out);

	CHECK (guard.holdsShow ("next", { { "automatic", true } }));
	CHECK (guard.holdsShow ("prev", { { "automatic", true } }));
	CHECK_FALSE (guard.holdsShow ("next", nlohmann::json::object ()));
	CHECK_FALSE (guard.holdsShow ("prev", { { "automatic", false } }));
	const std::string lines = out->str ();
	out->setstate (std::ios::badbit);

	CHECK (occurrences (lines, HELD_SHOW) == 2);
	CHECK (guard.restoreRefused ());
    }

    SECTION ("unheld control") {
	BootGuard guard;
	auto* out = new std::ostringstream ();
	sLog.addOutput (out);

	CHECK_FALSE (guard.holdsShow ("next", { { "automatic", true } }));
	CHECK_FALSE (guard.holdsShow ("prev", { { "automatic", true } }));
	const std::string lines = out->str ();
	out->setstate (std::ios::badbit);

	CHECK (lines.empty ());
    }
}
