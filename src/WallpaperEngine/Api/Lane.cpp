#include "Lane.h"

#include <algorithm>
#include <numeric>
#include <random>

using namespace WallpaperEngine::Api;

namespace {
std::vector<std::string> idsOf (const Playlist& playlist) {
    std::vector<std::string> ids;
    ids.reserve (playlist.entries.size ());
    for (const auto& entry : playlist.entries) {
	ids.push_back (displayId (entry));
    }
    return ids;
}

int64_t elapsedMs (const Lane& lane, Clock::time_point now) {
    return std::chrono::duration_cast<std::chrono::milliseconds> (now - lane.lastShow).count ();
}

int elapsedSeconds (const Lane& lane, Clock::time_point now) {
    return static_cast<int> (elapsedMs (lane, now) / 1000);
}

nlohmann::json floatsToJson (const std::array<float, 4>& v) { return { v[0], v[1], v[2], v[3] }; }

std::array<float, 4> floatsFromJson (const nlohmann::json& j, const std::array<float, 4>& fallback) {
    if (!j.is_array () || j.size () != 4) {
	return fallback;
    }
    std::array<float, 4> out {};
    for (std::size_t i = 0; i < 4; i++) {
	out[i] = j[i].is_number () ? j[i].get<float> () : fallback[i];
    }
    return out;
}
} // namespace

// the state file is hand-editable: a missing key is the default, a stray type or an
// out-of-range value lands on the contract's bounds instead of throwing
Fit WallpaperEngine::Api::fitFromJson (const nlohmann::json& j) {
    Fit fit;

    if (!j.is_object ()) {
	return fit;
    }

    const auto number = [&j] (const char* key, const float fallback) {
	return j.contains (key) && j[key].is_number () ? j[key].get<float> () : fallback;
    };

    fit.zoom = std::clamp (number ("zoom", 1.0f), 1.0f, 2.0f);
    fit.panX = std::clamp (number ("pan_x", 0.0f), -1.0f, 1.0f);
    fit.panY = std::clamp (number ("pan_y", 0.0f), -1.0f, 1.0f);
    return fit;
}

namespace {
void boundHistory (Lane& lane) {
    while (lane.history.size () + lane.forward.size () > HISTORY_BOUND && !lane.history.empty ()) {
	lane.history.pop_front ();
    }
}

std::size_t walkIndexOf (const Lane& lane, const std::string& id) {
    const auto it = std::find (lane.walk.begin (), lane.walk.end (), id);
    return it == lane.walk.end () ? SIZE_MAX : static_cast<std::size_t> (it - lane.walk.begin ());
}
} // namespace

std::string WallpaperEngine::Api::displayId (const std::string& id, const std::string& uiId) {
    return uiId.empty () ? id : uiId;
}

std::string WallpaperEngine::Api::displayId (const Entry& entry) { return displayId (entry.id, entry.uiId); }

namespace {
bool isShuffle (const Playlist& playlist) { return playlist.order == "shuffle" || playlist.order == "random"; }

std::vector<std::string> orderedIds (const Playlist& playlist) {
    std::vector<std::string> ids;

    for (const auto& entry : playlist.entries) {
	const auto id = displayId (entry);

	if (std::find (ids.begin (), ids.end (), id) == ids.end ()) {
	    ids.push_back (id);
	}
    }

    return ids;
}

std::size_t entryIndex (const Playlist& playlist, const std::string& id) {
    for (std::size_t i = 0; i < playlist.entries.size (); i++) {
	if (displayId (playlist.entries[i]) == id) {
	    return i;
	}
    }

    return SIZE_MAX;
}

bool walkIsStale (const Lane& lane, const Playlist& playlist) {
    if (lane.walk.empty ()) {
	return !playlist.entries.empty ();
    }

    const auto unknown = [&] (const std::string& id) { return entryIndex (playlist, id) == SIZE_MAX; };
    return std::any_of (lane.walk.begin (), lane.walk.end (), unknown)
	|| std::any_of (lane.nextCycle.begin (), lane.nextCycle.end (), unknown);
}

std::vector<std::string> drawCycle (const Playlist& playlist, std::mt19937& rng) {
    auto ids = orderedIds (playlist);
    std::shuffle (ids.begin (), ids.end (), rng);
    return ids;
}

/** Rebuild the walk for the playlist's current content, keeping the cursor's place by identity.
 *  Shuffle keeps the ids it already ordered, in the walk and in the drawn next cycle alike, and
 *  shuffles only the new ids onto their ends. */
void reseat (Lane& lane, const Playlist& playlist, std::mt19937& rng) {
    const auto oldWalk = lane.walk;
    const int oldCursor = lane.cursor;
    const auto ids = orderedIds (playlist);
    const auto survivorsThenFresh = [&] (const std::vector<std::string>& old) {
	std::vector<std::string> out;

	for (const auto& id : old) {
	    if (std::find (ids.begin (), ids.end (), id) != ids.end ()
		&& std::find (out.begin (), out.end (), id) == out.end ()) {
		out.push_back (id);
	    }
	}

	std::vector<std::string> fresh;

	for (const auto& id : ids) {
	    if (std::find (out.begin (), out.end (), id) == out.end ()) {
		fresh.push_back (id);
	    }
	}

	std::shuffle (fresh.begin (), fresh.end (), rng);
	out.insert (out.end (), fresh.begin (), fresh.end ());
	return out;
    };

    if (isShuffle (playlist)) {
	lane.walk = survivorsThenFresh (oldWalk);
	lane.nextCycle = lane.nextCycle.empty () ? std::vector<std::string> {} : survivorsThenFresh (lane.nextCycle);
    } else {
	lane.walk = ids;
	lane.nextCycle.clear ();
    }

    lane.cursor = -1;

    if (lane.walk.empty ()) {
	lane.nextCycle.clear ();
	return;
    }

    if (oldWalk.empty ()) {
	seatOnCurrent (lane, playlist);
	return;
    }

    if (oldCursor >= 0) {
	for (std::size_t j = static_cast<std::size_t> (oldCursor); j < oldWalk.size (); j++) {
	    const auto it = std::find (lane.walk.begin (), lane.walk.end (), oldWalk[j]);

	    if (it != lane.walk.end ()) {
		const int index = static_cast<int> (it - lane.walk.begin ());
		// the cursor's own item keeps its seat; a removed one yields so the first
		// survivor after it plays next
		lane.cursor = j == static_cast<std::size_t> (oldCursor) ? index : index - 1;
		break;
	    }

	    if (j + 1 == oldWalk.size ()) {
		lane.cursor = static_cast<int> (lane.walk.size ()) - 1;
	    }
	}
    }

    // the drawn next cycle must still exist when the cursor sits on the last item
    if (isShuffle (playlist) && lane.nextCycle.empty () && lane.cursor + 1 == static_cast<int> (lane.walk.size ())) {
	lane.nextCycle = drawCycle (playlist, rng);
    }
}

/** The position the walk moves to next, in walk + nextCycle coordinates. False when shuffle
 *  would first have to draw a cycle that does not exist yet. */
bool stepAhead (const Lane& lane, const Playlist& playlist, std::size_t& pos) {
    const std::size_t n = lane.walk.size ();
    const std::size_t total = n + lane.nextCycle.size ();

    if (n == 0) {
	return false;
    }

    auto forward = [&] (std::size_t from, std::size_t& to) {
	to = from + 1;

	if (to < total) {
	    return true;
	}

	if (isShuffle (playlist)) {
	    return false;
	}

	to = 0;
	return true;
    };

    const std::size_t start = lane.cursor < 0 ? total - 1 : static_cast<std::size_t> (lane.cursor);

    if (lane.cursor < 0) {
	pos = 0;
    } else if (!forward (start, pos)) {
	return false;
    }

    const auto at = [&] (std::size_t p) { return p < n ? lane.walk[p] : lane.nextCycle[p - n]; };

    if (playlist.avoidRepeat && lane.walk.size () > 1 && at (pos) == displayId (lane.current)) {
	return forward (pos, pos);
    }

    return true;
}
} // namespace

std::size_t WallpaperEngine::Api::pickNext (Lane& lane, const Playlist& playlist, std::mt19937& rng) {
    if (playlist.entries.empty ()) {
	lane.walk.clear ();
	lane.nextCycle.clear ();
	lane.cursor = -1;
	return SIZE_MAX;
    }

    if (walkIsStale (lane, playlist)) {
	reseat (lane, playlist, rng);
    }

    std::size_t pos = 0;

    if (!stepAhead (lane, playlist, pos)) {
	lane.nextCycle = drawCycle (playlist, rng);

	if (!stepAhead (lane, playlist, pos)) {
	    return SIZE_MAX;
	}
    }

    if (pos >= lane.walk.size ()) {
	pos -= lane.walk.size ();
	lane.walk = lane.nextCycle;
	lane.nextCycle.clear ();
    }

    lane.cursor = static_cast<int> (pos);

    // shuffle draws the following cycle as soon as the cursor lands on the last item
    if (isShuffle (playlist) && pos + 1 == lane.walk.size ()) {
	lane.nextCycle = drawCycle (playlist, rng);
    }

    return entryIndex (playlist, lane.walk[pos]);
}

void WallpaperEngine::Api::seatCursor (Lane& lane, const std::string& displayId) {
    const auto it = std::find (lane.walk.begin (), lane.walk.end (), displayId);

    if (it != lane.walk.end ()) {
	lane.cursor = static_cast<int> (it - lane.walk.begin ());
    }
}

void WallpaperEngine::Api::seatBehind (Lane& lane, const std::string& displayId) {
    const auto it = std::find (lane.walk.begin (), lane.walk.end (), displayId);

    if (it != lane.walk.end () && static_cast<int> (it - lane.walk.begin ()) == lane.cursor - 1) {
	lane.cursor--;
    }
}

void WallpaperEngine::Api::seatOnCurrent (Lane& lane, const Playlist& playlist) {
    if (!isShuffle (playlist)) {
	seatCursor (lane, displayId (lane.current));
    }
}

void WallpaperEngine::Api::applySet (
    Lane& lane, Playlist& playlist, const Playlist& incoming, bool enabled, Clock::time_point now
) {
    const auto previousIds = idsOf (playlist);
    const bool wasEnabled = lane.enabled;
    const int previousInterval = playlist.intervalSeconds;
    const int64_t frozen = lane.frozenRemainingMs;
    const bool wasStatic = playlist.order == "static";

    // a change between ordered and shuffled starts a fresh walk: the cycle was drawn for the old kind
    if (isShuffle (playlist) != isShuffle (incoming)) {
	lane.walk.clear ();
	lane.nextCycle.clear ();
	lane.cursor = -1;
    }

    playlist.entries = incoming.entries;
    playlist.intervalSeconds = incoming.intervalSeconds;
    playlist.order = incoming.order;
    playlist.avoidRepeat = incoming.avoidRepeat;
    playlist.label = incoming.label;

    const bool sameSet = idsOf (playlist) == previousIds && playlist.intervalSeconds == previousInterval;
    // forward targets belong to the set they were stepped back from
    if (!sameSet) {
	lane.forward.clear ();
    }

    lane.enabled = enabled && !playlist.entries.empty ();
    // a push never restarts a cycle: the walk is re-seated by identity, not rebuilt
    std::mt19937 rng (std::random_device {}());
    reseat (lane, playlist, rng);

    // leaving static is a new play: the clock did not run while the mode was static
    const bool leftStatic = wasStatic && playlist.order != "static";

    if (sameSet && lane.enabled == wasEnabled && !leftStatic) {
	// the same set in the same state is a re-push, not a new play: the clock runs on
	if (!lane.enabled && lane.frozenRemainingMs < 0) {
	    lane.frozenRemainingMs = int64_t (playlist.intervalSeconds) * 1000;
	}
    } else if (!lane.enabled) {
	// a pause is a true pause: the same set freezes where its countdown stood
	const int64_t intervalMs = int64_t (playlist.intervalSeconds) * 1000;

	if (sameSet && frozen >= 0) {
	    lane.frozenRemainingMs = frozen;
	} else if (sameSet && wasEnabled) {
	    // the fraction of a second is kept: pause and resume hand nothing back
	    lane.frozenRemainingMs = std::clamp (intervalMs - elapsedMs (lane, now), int64_t (0), intervalMs);
	} else {
	    lane.frozenRemainingMs = intervalMs;
	}
    } else if (sameSet && !wasEnabled && frozen >= 0 && !leftStatic) {
	lane.lastShow = now - std::chrono::milliseconds (int64_t (playlist.intervalSeconds) * 1000 - frozen);
	lane.frozenRemainingMs = -1;
    } else {
	lane.lastShow = now;
	lane.frozenRemainingMs = -1;
    }
}

bool WallpaperEngine::Api::dueForAdvance (const Lane& lane, const Playlist& playlist, Clock::time_point now) {
    if (!lane.enabled || playlist.entries.empty () || playlist.order == "static") {
	return false;
    }

    return elapsedSeconds (lane, now) >= playlist.intervalSeconds;
}

void WallpaperEngine::Api::restartCountdown (Lane& lane, const Playlist& playlist, Clock::time_point now) {
    lane.lastShow = now;

    if (lane.frozenRemainingMs >= 0) {
	lane.frozenRemainingMs = int64_t (playlist.intervalSeconds) * 1000;
    }
}

void WallpaperEngine::Api::resumeCountdown (
    Lane& lane, const Playlist& playlist, int64_t remainingMs, int64_t downtimeMs, Clock::time_point now
) {
    lane.lastShow = now;

    if (!lane.enabled || lane.frozenRemainingMs >= 0 || remainingMs < 0) {
	return;
    }

    // the saved remainder less the time the engine was away; an overdue lane advances on
    // the first tick
    const int64_t intervalMs = int64_t (playlist.intervalSeconds) * 1000;
    const int64_t left = std::clamp (remainingMs - std::max<int64_t> (0, downtimeMs), int64_t (0), intervalMs);
    lane.lastShow = now - std::chrono::milliseconds (intervalMs - left);
}

int WallpaperEngine::Api::nextInSeconds (const Lane& lane, const Playlist& playlist, Clock::time_point now) {
    // static has no countdown, frozen or running
    if (playlist.order == "static") {
	return -1;
    }

    const int64_t ms = nextInMs (lane, playlist, now);
    return ms < 0 ? -1 : static_cast<int> (ms / 1000);
}

int64_t WallpaperEngine::Api::nextInMs (const Lane& lane, const Playlist& playlist, Clock::time_point now) {
    if (playlist.order == "static") {
	return -1;
    }

    if (lane.frozenRemainingMs >= 0) {
	return lane.frozenRemainingMs;
    }

    if (lane.enabled) {
	return std::max<int64_t> (0, int64_t (playlist.intervalSeconds) * 1000 - elapsedMs (lane, now));
    }

    return -1;
}

std::string WallpaperEngine::Api::nextUp (const Lane& lane, const Playlist& playlist) {
    std::size_t pos = 0;

    if (playlist.entries.empty () || walkIsStale (lane, playlist) || !stepAhead (lane, playlist, pos)) {
	return "";
    }

    return pos < lane.walk.size () ? lane.walk[pos] : lane.nextCycle[pos - lane.walk.size ()];
}

void WallpaperEngine::Api::recordShow (Lane& lane, const Entry& shown, bool recordHistory) {
    // the same id again (an editor re-show) refreshes the record and leaves history alone
    if (!lane.current.id.empty () && displayId (lane.current) == displayId (shown)) {
	lane.current = shown;
	return;
    }

    if (recordHistory) {
	if (!lane.current.id.empty ()) {
	    lane.history.push_back (lane.current);
	}

	lane.forward.clear ();
	boundHistory (lane);
    }

    lane.current = shown;
}

namespace {
/** The walk item behind the cursor, skipping the id on screen once; SIZE_MAX with none. */
std::size_t walkBehind (const Lane& lane) {
    const std::size_t n = lane.walk.size ();

    if (n < 2) {
	return SIZE_MAX;
    }

    std::size_t at = lane.cursor < 0 ? n - 1 : (static_cast<std::size_t> (lane.cursor) + n - 1) % n;

    if (lane.walk[at] == displayId (lane.current)) {
	at = (at + n - 1) % n;
    }

    return at;
}
} // namespace

std::optional<Entry> WallpaperEngine::Api::backTarget (const Lane& lane, const Playlist& playlist) {
    if (playlist.order == "static" || lane.walk.size () < 2) {
	return std::nullopt;
    }

    if (!lane.history.empty ()) {
	return lane.history.back ();
    }

    const auto at = walkBehind (lane);
    const auto index = at == SIZE_MAX ? SIZE_MAX : entryIndex (playlist, lane.walk[at]);
    return index == SIZE_MAX ? std::nullopt : std::optional<Entry> (playlist.entries[index]);
}

void WallpaperEngine::Api::commitBack (Lane& lane, const Playlist&, const Entry& previous, const Entry& target) {
    if (!lane.history.empty () && displayId (lane.history.back ()) == displayId (target)) {
	if (!previous.id.empty ()) {
	    lane.forward.push_front (previous);
	}

	lane.history.pop_back ();
	lane.current = target;
	boundHistory (lane);
	seatBehind (lane, displayId (target));
	return;
    }

    // the walk stepped back: a fresh show, recorded like any other
    const auto at = walkBehind (lane);

    if (at != SIZE_MAX) {
	lane.cursor = static_cast<int> (at);
    }

    if (!previous.id.empty () && displayId (previous) != displayId (target)) {
	lane.history.push_back (previous);
    }

    lane.forward.clear ();
    lane.current = target;
    boundHistory (lane);
}

std::optional<Entry> WallpaperEngine::Api::forwardTarget (const Lane& lane) {
    if (lane.forward.empty ()) {
	return std::nullopt;
    }

    return lane.forward.front ();
}

void WallpaperEngine::Api::commitForward (Lane& lane, const Entry& previous, const Entry& target) {
    if (!previous.id.empty ()) {
	lane.history.push_back (previous);
    }

    if (!lane.forward.empty ()) {
	lane.forward.pop_front ();
    }

    lane.current = target;
    boundHistory (lane);
    const std::size_t n = lane.walk.size ();
    const auto index = walkIndexOf (lane, displayId (target));

    if (n > 0 && index != SIZE_MAX && index == (static_cast<std::size_t> (lane.cursor + 1)) % n) {
	lane.cursor = static_cast<int> (index);
    }
}

void WallpaperEngine::Api::jumpToEnd (Lane& lane) {
    // item by item, with forward's own cursor rule: a detour ahead never moves the cursor
    while (!lane.forward.empty ()) {
	const Entry ahead = lane.forward.front ();
	commitForward (lane, lane.current, ahead);
    }
}

std::string WallpaperEngine::Api::previousUp (const Lane& lane, const Playlist& playlist) {
    // what back would show: history, except in static, where back walks the playlist order;
    // the walk fallback needs a walk of two or more
    if (playlist.order != "static" && !lane.history.empty ()) {
	return displayId (lane.history.back ());
    }

    if (lane.walk.size () < 2) {
	return "";
    }

    const auto at = walkBehind (lane);
    return at == SIZE_MAX ? "" : lane.walk[at];
}

std::string WallpaperEngine::Api::aheadUp (const Lane& lane, const Playlist& playlist) {
    if (!lane.forward.empty ()) {
	return displayId (lane.forward.front ());
    }

    return nextUp (lane, playlist);
}

bool WallpaperEngine::Api::backEnabled (const Lane& lane, const Playlist& playlist) {
    return playlist.order != "static" && lane.walk.size () > 1;
}

nlohmann::json WallpaperEngine::Api::toJson (const Entry& entry) {
    return { { "id", entry.id }, { "ui_id", entry.uiId }, { "args", entry.args } };
}

nlohmann::json WallpaperEngine::Api::toJson (const Playlist& playlist) {
    nlohmann::json entries = nlohmann::json::array ();

    for (const auto& entry : playlist.entries) {
	entries.push_back (toJson (entry));
    }

    return { { "slug", playlist.slug },
	     { "entries", entries },
	     { "order", playlist.order },
	     { "interval_s", playlist.intervalSeconds },
	     { "avoid_repeat", playlist.avoidRepeat },
	     { "label", playlist.label } };
}

nlohmann::json WallpaperEngine::Api::toJson (const Group& group) {
    nlohmann::json screens = nlohmann::json::array ();

    for (const auto& screen : group.screens) {
	screens.push_back (
	    { { "make", screen.make }, { "model", screen.model }, { "serial", screen.serial }, { "name", screen.name } }
	);
    }

    return { { "key", group.key }, { "type", group.type }, { "screens", screens } };
}

nlohmann::json WallpaperEngine::Api::toJson (const Lane& lane) {
    nlohmann::json history = nlohmann::json::array ();
    nlohmann::json forward = nlohmann::json::array ();

    for (const auto& entry : lane.history) {
	history.push_back (toJson (entry));
    }

    for (const auto& entry : lane.forward) {
	forward.push_back (toJson (entry));
    }

    return { { "id", lane.id },
	     { "group", lane.groupKey },
	     { "playlist", lane.playlistSlug },
	     { "enabled", lane.enabled },
	     { "walk", lane.walk },
	     { "next_cycle", lane.nextCycle },
	     { "cursor", lane.cursor },
	     { "frozen_remaining_s", lane.frozenRemainingMs < 0 ? int64_t (-1) : lane.frozenRemainingMs / 1000 },
	     { "frozen_remaining_ms", lane.frozenRemainingMs },
	     { "current", toJson (lane.current) },
	     { "history", history },
	     { "forward", forward },
	     { "fit", { { "zoom", lane.fit.zoom }, { "pan_x", lane.fit.panX }, { "pan_y", lane.fit.panY } } },
	     { "look",
	       { { "properties", lane.look.properties },
		 { "cc", floatsToJson (lane.look.cc) },
		 { "timescale", lane.look.timescale },
		 { "skip_objects", lane.look.skipObjects },
		 { "skip_effects", lane.look.skipEffects },
		 { "volume", lane.look.volume },
		 { "audio_processing", lane.look.audioProcessing },
		 { "mouse", lane.look.mouse },
		 { "automute", lane.look.automute },
		 { "scaling", lane.look.scaling },
		 { "clamp", lane.look.clamp },
		 { "fit",
		   { { "zoom", lane.look.fit.zoom },
		     { "pan_x", lane.look.fit.panX },
		     { "pan_y", lane.look.fit.panY } } } } } };
}

Entry WallpaperEngine::Api::entryFromJson (const nlohmann::json& j) {
    Entry entry;

    if (!j.is_object ()) {
	return entry;
    }

    entry.id = j.value ("id", "");
    entry.uiId = j.value ("ui_id", "");
    entry.args = j.contains ("args") && j["args"].is_object () ? j["args"] : nlohmann::json::object ();

    // a bare show-args object (the pre-lane rotation entry) carries its identity inline
    if (entry.id.empty () && entry.args.empty () && j.contains ("id")) {
	entry.args = j;
    } else if (entry.args.empty () && !j.contains ("args")) {
	entry.args = j;
    }

    return entry;
}

Playlist WallpaperEngine::Api::playlistFromJson (const nlohmann::json& j) {
    Playlist playlist;

    if (!j.is_object ()) {
	return playlist;
    }

    playlist.slug = j.value ("slug", "default");
    playlist.order = j.value ("order", "shuffle");
    playlist.intervalSeconds = j.value ("interval_s", 900);
    playlist.avoidRepeat = j.value ("avoid_repeat", true);
    playlist.label = j.value ("label", "");

    if (j.contains ("entries") && j["entries"].is_array ()) {
	for (const auto& entry : j["entries"]) {
	    playlist.entries.push_back (entryFromJson (entry));
	}
    }

    return playlist;
}

Group WallpaperEngine::Api::groupFromJson (const nlohmann::json& j) {
    Group group;

    if (!j.is_object ()) {
	return group;
    }

    group.key = j.value ("key", "all");
    group.type = j.value ("type", "mirror");

    if (j.contains ("screens") && j["screens"].is_array ()) {
	for (const auto& screen : j["screens"]) {
	    if (!screen.is_object ()) {
		continue;
	    }

	    group.screens.push_back (
		{ .make = screen.value ("make", ""),
		  .model = screen.value ("model", ""),
		  .serial = screen.value ("serial", ""),
		  .name = screen.value ("name", "") }
	    );
	}
    }

    return group;
}

Lane WallpaperEngine::Api::laneFromJson (const nlohmann::json& j) {
    Lane lane;

    if (!j.is_object ()) {
	return lane;
    }

    lane.id = j.value ("id", "all");
    lane.groupKey = j.value ("group", "all");
    lane.playlistSlug = j.value ("playlist", "default");
    lane.enabled = j.value ("enabled", false);

    for (const auto* field : { "walk", "next_cycle" }) {
	if (j.contains (field) && j[field].is_array ()) {
	    auto& target = std::string (field) == "walk" ? lane.walk : lane.nextCycle;

	    for (const auto& id : j[field]) {
		if (id.is_string ()) {
		    target.push_back (id.get<std::string> ());
		}
	    }
	}
    }

    lane.cursor = j.value ("cursor", -1);

    if (lane.cursor >= static_cast<int> (lane.walk.size ())) {
	lane.cursor = -1;
    }
    lane.frozenRemainingMs = j.contains ("frozen_remaining_ms") && j["frozen_remaining_ms"].is_number_integer ()
	? j["frozen_remaining_ms"].get<int64_t> ()
	: (j.value ("frozen_remaining_s", -1) < 0 ? int64_t (-1) : int64_t (j.value ("frozen_remaining_s", -1)) * 1000);

    if (j.contains ("current")) {
	lane.current = entryFromJson (j["current"]);
    }

    if (j.contains ("history") && j["history"].is_array ()) {
	for (const auto& entry : j["history"]) {
	    lane.history.push_back (entryFromJson (entry));
	}

	while (lane.history.size () > HISTORY_BOUND) {
	    lane.history.pop_front ();
	}
    }

    if (j.contains ("forward") && j["forward"].is_array ()) {
	for (const auto& entry : j["forward"]) {
	    lane.forward.push_back (entryFromJson (entry));
	}
    }

    while (lane.forward.size () > HISTORY_BOUND) {
	lane.forward.pop_back ();
    }

    boundHistory (lane);

    if (j.contains ("fit")) {
	lane.fit = fitFromJson (j["fit"]);
    }

    if (j.contains ("look") && j["look"].is_object ()) {
	const auto& look = j["look"];
	lane.look.properties = look.contains ("properties") && look["properties"].is_object ()
	    ? look["properties"]
	    : nlohmann::json::object ();
	lane.look.cc = floatsFromJson (look.value ("cc", nlohmann::json::array ()), lane.look.cc);
	lane.look.timescale = look.value ("timescale", 1.0f);
	lane.look.skipObjects = look.value ("skip_objects", std::vector<int> {});
	lane.look.skipEffects = look.value ("skip_effects", std::vector<int> {});
	lane.look.volume = look.value ("volume", 15);
	lane.look.audioProcessing = look.value ("audio_processing", false);
	lane.look.mouse = look.value ("mouse", false);
	lane.look.automute = look.value ("automute", true);
	lane.look.scaling = look.value ("scaling", "");
	lane.look.clamp = look.value ("clamp", "");
	lane.look.fit = look.contains ("fit") ? fitFromJson (look["fit"]) : Fit {};
    }

    return lane;
}

void WallpaperEngine::Api::fromLegacyState (const nlohmann::json& state, Lane& lane, Playlist& playlist) {
    lane = Lane {};
    playlist = Playlist {};

    if (state.contains ("rotation") && state["rotation"].is_object ()) {
	const auto& rotation = state["rotation"];
	playlist.intervalSeconds = rotation.value ("interval_s", 900);
	playlist.order = rotation.value ("order", "shuffle");
	playlist.avoidRepeat = rotation.value ("avoid_repeat", true);
	playlist.label = rotation.value ("label", "");

	if (rotation.contains ("entries") && rotation["entries"].is_array ()) {
	    for (const auto& args : rotation["entries"]) {
		Entry entry;
		entry.id = args.value ("id", "");
		entry.uiId = args.value ("ui_id", "");
		entry.args = args;
		playlist.entries.push_back (entry);
	    }
	}

	lane.enabled = rotation.value ("enabled", false) && !playlist.entries.empty ();
	lane.frozenRemainingMs = rotation.value ("frozen_remaining_s", -1) < 0 ? int64_t (-1) : int64_t (rotation.value ("frozen_remaining_s", -1)) * 1000;
    }

    if (state.contains ("current") && state["current"].is_object ()) {
	const auto& current = state["current"];
	lane.current.id = current.value ("id", "");
	lane.current.uiId = current.value ("ui_id", "");
	lane.current.args
	    = current.contains ("args") && current["args"].is_object () ? current["args"] : nlohmann::json::object ();
    }
}

nlohmann::json WallpaperEngine::Api::laneStatus (const Lane& lane, const Playlist& playlist, Clock::time_point now) {
    // cursor: walk index of the item the walk last played; -1 before the first pick
    const int cursor = lane.cursor;

    return { { "id", lane.id },
	     { "group", lane.groupKey },
	     { "playlist", lane.playlistSlug },
	     { "order", playlist.order },
	     { "enabled", lane.enabled },
	     { "count", playlist.entries.size () },
	     { "empty", playlist.entries.empty () },
	     { "cursor", cursor },
	     { "previous", previousUp (lane, playlist) },
	     { "now", displayId (lane.current) },
	     { "next", aheadUp (lane, playlist) },
	     { "back_enabled", backEnabled (lane, playlist) },
	     { "forward_depth", lane.forward.size () },
	     { "interval_s", playlist.intervalSeconds },
	     { "next_in_s", nextInSeconds (lane, playlist, now) },
	     { "next_in_ms", nextInMs (lane, playlist, now) },
	     { "history_depth", lane.history.size () },
	     { "web_disabled", false },
	     { "fit", { { "zoom", lane.fit.zoom }, { "pan_x", lane.fit.panX }, { "pan_y", lane.fit.panY } } } };
}

int WallpaperEngine::Api::scheduleIndexAt (const Schedule& schedule, int minute) {
    if (schedule.entries.empty ()) {
	return -1;
    }

    // the latest boundary at or before the minute; before the first of the day, the last one
    // is still in force from yesterday
    int best = -1;
    int bestMinute = -1;
    int last = -1;
    int lastMinute = -1;

    for (size_t i = 0; i < schedule.entries.size (); i++) {
	const int at = schedule.entries[i].minute;

	if (at <= minute && at > bestMinute) {
	    best = static_cast<int> (i);
	    bestMinute = at;
	}

	if (at > lastMinute) {
	    last = static_cast<int> (i);
	    lastMinute = at;
	}
    }

    return best >= 0 ? best : last;
}

std::string WallpaperEngine::Api::scheduleSlugAt (const Schedule& schedule, int minute) {
    const int index = scheduleIndexAt (schedule, minute);
    return index < 0 ? "" : schedule.entries[index].slug;
}

int WallpaperEngine::Api::scheduleMinutesToBoundary (const Schedule& schedule, int minute) {
    if (schedule.entries.empty ()) {
	return -1;
    }

    int best = -1;

    for (const auto& entry : schedule.entries) {
	int ahead = entry.minute - minute;

	if (ahead <= 0) {
	    ahead += 24 * 60;
	}

	if (best < 0 || ahead < best) {
	    best = ahead;
	}
    }

    return best;
}

bool WallpaperEngine::Api::scheduleIsDay (const Schedule& schedule, int minute) {
    return scheduleIndexAt (schedule, minute) == 0;
}

bool WallpaperEngine::Api::scheduleTick (Schedule& schedule, const std::string& boundSlug, int minute) {
    // a boundary was crossed when the entry in force changed since the last tick, or when a
    // boundary minute lies inside the span the clock moved through (wrapping past midnight)
    bool crossed = false;

    // a backward step shorter than half a day is a clock correction, not a wrap: nothing crossed
    const bool stepBack = schedule.lastMinute >= 0 && minute < schedule.lastMinute && schedule.lastMinute - minute < 12 * 60;

    if (schedule.lastMinute >= 0 && schedule.lastMinute != minute && !stepBack) {
	for (const auto& entry : schedule.entries) {
	    const bool forward = schedule.lastMinute < minute;
	    const bool inside = forward ? (entry.minute > schedule.lastMinute && entry.minute <= minute)
					: (entry.minute > schedule.lastMinute || entry.minute <= minute);

	    if (inside) {
		crossed = true;
	    }
	}
    }

    schedule.lastMinute = minute;

    if (!schedule.enabled) {
	schedule.pending.clear ();
	schedule.held = false;
	return crossed;
    }

    if (crossed) {
	schedule.held = false;
    }

    const auto want = schedule.held ? boundSlug : scheduleSlugAt (schedule, minute);
    schedule.pending = (!want.empty () && want != boundSlug) ? want : "";
    return crossed;
}

void WallpaperEngine::Api::scheduleResume (Schedule& schedule, int downtimeMinutes) {
    if (schedule.held && schedule.lastMinute >= 0) {
	const int toBoundary = scheduleMinutesToBoundary (schedule, schedule.lastMinute);

	if (toBoundary >= 0 && downtimeMinutes >= toBoundary) {
	    schedule.held = false;
	}
    }

    schedule.lastMinute = -1;
}

bool WallpaperEngine::Api::sameSchedule (const Schedule& a, const Schedule& b) {
    if (a.enabled != b.enabled || a.entries.size () != b.entries.size ()) {
	return false;
    }

    for (size_t i = 0; i < a.entries.size (); i++) {
	if (a.entries[i].minute != b.entries[i].minute || a.entries[i].slug != b.entries[i].slug) {
	    return false;
	}
    }

    return true;
}

nlohmann::json WallpaperEngine::Api::toJson (const Schedule& schedule) {
    nlohmann::json entries = nlohmann::json::array ();

    for (const auto& entry : schedule.entries) {
	entries.push_back ({ { "minute", entry.minute }, { "playlist", entry.slug } });
    }

    return { { "enabled", schedule.enabled },
	     { "entries", entries },
	     { "held", schedule.held },
	     { "pending", schedule.pending },
	     { "last_minute", schedule.lastMinute } };
}

Schedule WallpaperEngine::Api::scheduleFromJson (const nlohmann::json& j) {
    Schedule schedule;

    if (!j.is_object ()) {
	return schedule;
    }

    schedule.enabled = j.value ("enabled", false);
    schedule.held = j.value ("held", false);
    schedule.pending = j.value ("pending", "");
    schedule.lastMinute = j.contains ("last_minute") && j["last_minute"].is_number_integer ()
	? std::clamp (j["last_minute"].get<int> (), -1, 24 * 60 - 1)
	: -1;

    if (j.contains ("entries") && j["entries"].is_array ()) {
	for (const auto& item : j["entries"]) {
	    if (!item.is_object () || !item.contains ("minute") || !item["minute"].is_number_integer ()
		|| !item.contains ("playlist") || !item["playlist"].is_string ()) {
		continue;
	    }

	    const int minute = item["minute"].get<int> ();

	    if (minute < 0 || minute >= 24 * 60) {
		continue;
	    }

	    schedule.entries.push_back ({ .minute = minute, .slug = item["playlist"].get<std::string> () });
	}
    }

    return schedule;
}
