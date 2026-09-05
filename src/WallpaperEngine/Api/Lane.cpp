#include "Lane.h"

#include <algorithm>
#include <numeric>

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

int elapsedSeconds (const Lane& lane, Clock::time_point now) {
    return static_cast<int> (std::chrono::duration_cast<std::chrono::seconds> (now - lane.lastShow).count ());
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

std::string WallpaperEngine::Api::displayId (const std::string& id, const std::string& uiId) {
    return uiId.empty () ? id : uiId;
}

std::string WallpaperEngine::Api::displayId (const Entry& entry) { return displayId (entry.id, entry.uiId); }

std::size_t WallpaperEngine::Api::pickNext (Lane& lane, const Playlist& playlist, std::mt19937& rng) {
    const std::size_t n = playlist.entries.size ();

    if (n == 0) {
	return SIZE_MAX;
    }

    if (n == 1) {
	return 0;
    }

    std::size_t pick = SIZE_MAX;

    if (playlist.order == "sequential" || playlist.order == "static") {
	lane.seqIndex = (lane.seqIndex + 1) % static_cast<int> (n);
	pick = static_cast<std::size_t> (lane.seqIndex);
    } else if (playlist.order == "random") {
	pick = rng () % n;
    } else {
	if (lane.perm.size () != n || lane.permIndex >= lane.perm.size ()
	    || std::any_of (lane.perm.begin (), lane.perm.end (), [n] (std::size_t v) { return v >= n; })) {
	    lane.perm.resize (n);
	    std::iota (lane.perm.begin (), lane.perm.end (), 0);
	    std::shuffle (lane.perm.begin (), lane.perm.end (), rng);
	    lane.permIndex = 0;
	}

	pick = lane.perm[lane.permIndex++];
    }

    const auto current = displayId (lane.current);

    if (playlist.avoidRepeat && !current.empty ()) {
	int guard = 0;

	while (guard < 8 && displayId (playlist.entries[pick]) == current) {
	    if (playlist.order == "sequential" || playlist.order == "static") {
		lane.seqIndex = (lane.seqIndex + 1) % static_cast<int> (n);
		pick = static_cast<std::size_t> (lane.seqIndex);
	    } else {
		pick = rng () % n;
	    }

	    guard++;
	}
    }

    return pick;
}

void WallpaperEngine::Api::applySet (
    Lane& lane, Playlist& playlist, const Playlist& incoming, bool enabled, Clock::time_point now
) {
    const auto previousIds = idsOf (playlist);
    const bool wasEnabled = lane.enabled;
    const int previousInterval = playlist.intervalSeconds;
    const int frozen = lane.frozenRemainingSeconds;

    playlist.entries = incoming.entries;
    playlist.intervalSeconds = incoming.intervalSeconds;
    playlist.order = incoming.order;
    playlist.avoidRepeat = incoming.avoidRepeat;
    playlist.label = incoming.label;

    lane.enabled = enabled && !playlist.entries.empty ();
    lane.perm.clear ();
    lane.permIndex = 0;
    lane.seqIndex = -1;
    lane.nextPick = SIZE_MAX;

    const bool sameSet = idsOf (playlist) == previousIds && playlist.intervalSeconds == previousInterval;

    if (!lane.enabled) {
	lane.frozenRemainingSeconds = sameSet && frozen >= 0 ? frozen : playlist.intervalSeconds;
    } else if (sameSet && !wasEnabled && frozen >= 0) {
	lane.lastShow = now - std::chrono::seconds (playlist.intervalSeconds - frozen);
	lane.frozenRemainingSeconds = -1;
    } else {
	lane.lastShow = now;
	lane.frozenRemainingSeconds = -1;
    }
}

bool WallpaperEngine::Api::dueForAdvance (const Lane& lane, const Playlist& playlist, Clock::time_point now) {
    if (!lane.enabled || playlist.entries.empty () || playlist.order == "static") {
	return false;
    }

    return elapsedSeconds (lane, now) >= playlist.intervalSeconds;
}

int WallpaperEngine::Api::nextInSeconds (const Lane& lane, const Playlist& playlist, Clock::time_point now) {
    if (lane.frozenRemainingSeconds >= 0) {
	return lane.frozenRemainingSeconds;
    }

    if (lane.enabled && playlist.order != "static") {
	return std::max (0, playlist.intervalSeconds - elapsedSeconds (lane, now));
    }

    return -1;
}

std::string WallpaperEngine::Api::nextUp (const Lane& lane, const Playlist& playlist) {
    if (lane.nextPick != SIZE_MAX && lane.nextPick < playlist.entries.size ()) {
	return displayId (playlist.entries[lane.nextPick]);
    }

    return "";
}

void WallpaperEngine::Api::recordShow (Lane& lane, const Entry& shown, bool recordHistory) {
    if (recordHistory && !lane.current.id.empty ()) {
	lane.history.push_back (lane.current);

	while (lane.history.size () > HISTORY_BOUND) {
	    lane.history.pop_front ();
	}
    }

    lane.current = shown;
}

std::optional<Entry> WallpaperEngine::Api::popHistory (Lane& lane) {
    if (lane.history.empty ()) {
	return std::nullopt;
    }

    Entry entry = lane.history.back ();
    lane.history.pop_back ();
    return entry;
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

    for (const auto& entry : lane.history) {
	history.push_back (toJson (entry));
    }

    return { { "id", lane.id },
	     { "group", lane.groupKey },
	     { "playlist", lane.playlistSlug },
	     { "enabled", lane.enabled },
	     { "perm", lane.perm },
	     { "perm_index", lane.permIndex },
	     { "seq_index", lane.seqIndex },
	     { "frozen_remaining_s", lane.frozenRemainingSeconds },
	     { "current", toJson (lane.current) },
	     { "history", history },
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
		 { "clamp", lane.look.clamp } } } };
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

    if (j.contains ("perm") && j["perm"].is_array ()) {
	for (const auto& index : j["perm"]) {
	    if (index.is_number_unsigned ()) {
		lane.perm.push_back (index.get<std::size_t> ());
	    }
	}
    }

    lane.permIndex = j.value ("perm_index", static_cast<std::size_t> (0));
    lane.seqIndex = j.value ("seq_index", -1);
    lane.frozenRemainingSeconds = j.value ("frozen_remaining_s", -1);

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

    if (j.contains ("fit") && j["fit"].is_object ()) {
	const auto& fit = j["fit"];
	lane.fit.zoom = fit.value ("zoom", 1.0f);
	lane.fit.panX = fit.value ("pan_x", 0.0f);
	lane.fit.panY = fit.value ("pan_y", 0.0f);
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
	lane.frozenRemainingSeconds = rotation.value ("frozen_remaining_s", -1);
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
    // cursor: the entry index the walk shows next (an identity-anchored cursor comes with 1b)
    const std::size_t cursor = lane.nextPick == SIZE_MAX ? 0 : lane.nextPick;

    return { { "id", lane.id },
	     { "group", lane.groupKey },
	     { "playlist", lane.playlistSlug },
	     { "order", playlist.order },
	     { "enabled", lane.enabled },
	     { "count", playlist.entries.size () },
	     { "empty", playlist.entries.empty () },
	     { "cursor", cursor },
	     { "previous", lane.history.empty () ? "" : displayId (lane.history.back ()) },
	     { "now", displayId (lane.current) },
	     { "next", nextUp (lane, playlist) },
	     { "interval_s", playlist.intervalSeconds },
	     { "next_in_s", nextInSeconds (lane, playlist, now) },
	     { "history_depth", lane.history.size () },
	     { "web_disabled", false },
	     { "fit", { { "zoom", lane.fit.zoom }, { "pan_x", lane.fit.panX }, { "pan_y", lane.fit.panY } } } };
}
