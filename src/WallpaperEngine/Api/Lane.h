#pragma once

#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <map>
#include <nlohmann/json.hpp>
#include <optional>
#include <random>
#include <string>
#include <vector>

namespace WallpaperEngine::Api {
// Rotation state as plain data with no application or GL dependency, so a unit test can
// build it. A Playlist is content, a Group is screens, a Lane binds one group to one
// playlist and owns the walk, the history, the timer, the fit and the look.

struct Entry {
    std::string id;
    std::string uiId;
    nlohmann::json args = nlohmann::json::object ();
};

/** The identity clients use for an entry: the UI id when present. */
std::string displayId (const Entry& entry);
std::string displayId (const std::string& id, const std::string& uiId);

struct Playlist {
    std::string slug = "default";
    std::vector<Entry> entries;
    std::string order = "shuffle"; /**< sequential | shuffle | random | static (random: one release, unordered) */
    int intervalSeconds = 900;
    bool avoidRepeat = true;
    std::string label;
};

struct ScreenIdentity {
    std::string make;
    std::string model;
    std::string serial;
    std::string name; /**< the connector name, a runtime alias */
};

struct Group {
    std::string key = "all";
    std::string type = "mirror"; /**< mirror | span | single */
    std::vector<ScreenIdentity> screens;
};

struct Fit {
    float zoom = 1.0f;
    float panX = 0.0f;
    float panY = 0.0f;
};

/** What a show applied besides the background, kept per lane so lanes can differ. */
struct Look {
    nlohmann::json properties = nlohmann::json::object ();
    std::array<float, 4> cc = { 1.0f, 1.0f, 1.0f, 0.0f };
    float timescale = 1.0f;
    std::vector<int> skipObjects;
    std::vector<int> skipEffects;
    int volume = 15;
    bool audioProcessing = false;
    bool mouse = false;
    bool automute = true;
    std::string scaling;
    std::string clamp;
};

constexpr std::size_t HISTORY_BOUND = 100;

struct Lane {
    std::string id = "all";
    std::string groupKey = "all";
    std::string playlistSlug = "default";
    bool enabled = false;

    /** shuffle exhausts a permutation before re-shuffling; sequential walks an index */
    std::vector<std::size_t> perm;
    std::size_t permIndex = 0;
    int seqIndex = -1;
    /** pre-drawn pick consumed by the next advance; SIZE_MAX = none */
    std::size_t nextPick = SIZE_MAX;

    std::chrono::steady_clock::time_point lastShow {};
    /** countdown freeze: disabling pauses the clock, re-enabling the same set resumes it */
    int frozenRemainingSeconds = -1;

    Entry current;
    /** complete show records, so stepping back restores the look and not only the id */
    std::deque<Entry> history;

    Fit fit;
    Look look;
};

using Clock = std::chrono::steady_clock;

/** The next index to show, advancing the lane's walk. SIZE_MAX when the playlist is empty. */
std::size_t pickNext (Lane& lane, const Playlist& playlist, std::mt19937& rng);

/** Replace the playlist's content and reset the lane's walk; the same set keeps its countdown
 *  rules. The caller owns the binding: neither slug nor lane.playlistSlug is touched. */
void applySet (Lane& lane, Playlist& playlist, const Playlist& incoming, bool enabled, Clock::time_point now);

/** True when the rotation timer should advance this lane now. Never for static. */
bool dueForAdvance (const Lane& lane, const Playlist& playlist, Clock::time_point now);

/** Seconds until the next timed advance: the frozen remainder while disabled, -1 when idle. */
int nextInSeconds (const Lane& lane, const Playlist& playlist, Clock::time_point now);

/** Display id of the pre-drawn next entry, or empty. */
std::string nextUp (const Lane& lane, const Playlist& playlist);

/** Record an applied show; the previous current goes to bounded history when asked. */
void recordShow (Lane& lane, const Entry& shown, bool recordHistory);

/** Pop the most recent history entry, if any. */
std::optional<Entry> popHistory (Lane& lane);

nlohmann::json toJson (const Entry& entry);
nlohmann::json toJson (const Playlist& playlist);
nlohmann::json toJson (const Group& group);
nlohmann::json toJson (const Lane& lane);
Entry entryFromJson (const nlohmann::json& j);
Playlist playlistFromJson (const nlohmann::json& j);
Group groupFromJson (const nlohmann::json& j);
Lane laneFromJson (const nlohmann::json& j);

/** Build one lane and its playlist from a version-1 state file. */
void fromLegacyState (const nlohmann::json& state, Lane& lane, Playlist& playlist);

/** The lane's status block as reported to clients. */
nlohmann::json laneStatus (const Lane& lane, const Playlist& playlist, Clock::time_point now);
} // namespace WallpaperEngine::Api
