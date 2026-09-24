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
    /** the show's quality switches, empty = the launch environment (see settings.render.quality) */
    std::optional<float> ssfactor;
    std::optional<float> clampComposites;
    std::string texcomp;
    std::string texdetail;
    /** the wallpaper layer of the fit window, from the show's fit arg */
    Fit fit;
};

constexpr std::size_t HISTORY_BOUND = 100;

struct Lane {
    std::string id = "all";
    std::string groupKey = "all";
    std::string playlistSlug = "default";
    bool enabled = false;

    /** the walk: display ids in play order (stored order, or one shuffle cycle) */
    std::vector<std::string> walk;
    /** shuffle only: the following cycle, drawn when the cursor lands on the walk's last item */
    std::vector<std::string> nextCycle;
    /** index into walk of the item the walk last played; -1 before the first pick */
    int cursor = -1;

    std::chrono::steady_clock::time_point lastShow {};
    /** countdown freeze: disabling pauses the clock, re-enabling the same set resumes it */
    /** a paused lane's remainder in milliseconds, -1 while running; the fraction is kept */
    int64_t frozenRemainingMs = -1;

    Entry current;
    /** complete show records, so stepping back restores the look and not only the id */
    std::deque<Entry> history;
    /** ahead after stepping back; a new show discards them */
    std::deque<Entry> forward;

    Fit fit;
    Look look;
};

using Clock = std::chrono::steady_clock;

/** Next entry index, advancing the cursor. SIZE_MAX when nothing can be picked: never index on it. */
std::size_t pickNext (Lane& lane, const Playlist& playlist, std::mt19937& rng);

/** Move the cursor onto `displayId` when the walk holds it (static: a click chooses the item). */
void seatCursor (Lane& lane, const std::string& displayId);

/** Seat the cursor on `displayId` only when it is the walk item just behind the cursor. */
void seatBehind (Lane& lane, const std::string& displayId);

/** Seat the cursor on the item on screen for an ordered walk; shuffle keeps its cycle start. */
void seatOnCurrent (Lane& lane, const Playlist& playlist);

/** Replace the playlist's content and re-seat the walk by identity; the same set keeps its
 *  countdown rules. The caller owns the binding: neither slug nor lane.playlistSlug is touched. */
void applySet (Lane& lane, Playlist& playlist, const Playlist& incoming, bool enabled, Clock::time_point now);

/** True when the rotation timer should advance this lane now. Never for static. */
bool dueForAdvance (const Lane& lane, const Playlist& playlist, Clock::time_point now);

/** Seconds until the next timed advance: the frozen remainder while disabled, -1 when idle. */
int nextInSeconds (const Lane& lane, const Playlist& playlist, Clock::time_point now);
/** The countdown in milliseconds, -1 when the lane has none; the deck anchors to this. */
int64_t nextInMs (const Lane& lane, const Playlist& playlist, Clock::time_point now);

/** A new wallpaper is a new play: the clock restarts, and a paused lane's frozen remainder becomes the full interval. */
void restartCountdown (Lane& lane, const Playlist& playlist, Clock::time_point now);

/** One boundary of the schedule: from `minute` of the local day, the lane plays `slug`. */
struct ScheduleEntry {
    int minute = 0; /**< minutes since local midnight, 0..1439 */
    std::string slug;
};

/** The lane's clock-driven playlist choice. Entries keep the order they were sent in: the first
 *  is where day begins, the second where it ends; the span between them is day. */
struct Schedule {
    bool enabled = false;
    std::vector<ScheduleEntry> entries;
    bool held = false;          /**< a manual switch is in force until the next boundary */
    std::string pending;        /**< a boundary was crossed; applies when the countdown expires */
    int lastMinute = -1;        /**< the local minute the tick last saw, for boundary detection */
};

/** The entry in force at `minute`: the latest boundary at or before it, wrapping to the last. -1 when none. */
int scheduleIndexAt (const Schedule& schedule, int minute);
/** The slug in force at `minute`, "" when the schedule has no entries. */
std::string scheduleSlugAt (const Schedule& schedule, int minute);
/** Minutes until the next boundary strictly after `minute` (wrapping past midnight); -1 when none. */
int scheduleMinutesToBoundary (const Schedule& schedule, int minute);
/** True when the span from `minute` of the first entry (day) is in force at `minute`. */
bool scheduleIsDay (const Schedule& schedule, int minute);
/** One tick of the clock: clears a hold at a crossed boundary and sets or clears the pending
 *  switch against the lane's bound slug. Returns true when a boundary was crossed this tick. */
bool scheduleTick (Schedule& schedule, const std::string& boundSlug, int minute);

/** After a restart: a hold whose boundary passed during `downtimeMinutes` ends; the tick starts fresh. */
void scheduleResume (Schedule& schedule, int downtimeMinutes);

/** The same enabled flag and the same entries in the same order: a re-push, not a change. */
bool sameSchedule (const Schedule& a, const Schedule& b);

nlohmann::json toJson (const Schedule& schedule);
Schedule scheduleFromJson (const nlohmann::json& j);

/** Seat the clock after a restart: a running lane resumes its saved remainder less the downtime. */
void resumeCountdown (Lane& lane, const Playlist& playlist, int64_t remainingMs, int64_t downtimeMs, Clock::time_point now);

/** Display id the walk shows next, or empty when a fresh shuffle cycle is still to be drawn. */
std::string nextUp (const Lane& lane, const Playlist& playlist);

/** Record an applied show: the old current goes behind, forward is discarded; a re-show only refreshes. */
void recordShow (Lane& lane, const Entry& shown, bool recordHistory);

/** What back would show: history, else the previous walk item; static walks only; nothing under two items. */
std::optional<Entry> backTarget (const Lane& lane, const Playlist& playlist);

/** Books after back showed `target`; `previous` was on screen before. */
void commitBack (Lane& lane, const Playlist& playlist, const Entry& previous, const Entry& target);

/** The item ahead in forward history, if any. */
std::optional<Entry> forwardTarget (const Lane& lane);

/** Books after forward showed `target`; `previous` was on screen before. */
void commitForward (Lane& lane, const Entry& previous, const Entry& target);

/** Timer rule: a backed-up lane moves to its newest item before the walk advances. */
void jumpToEnd (Lane& lane);

/** Display id behind the current show: history, or in static the walk's previous; or empty. */
std::string previousUp (const Lane& lane, const Playlist& playlist);

/** Display id ahead: forward history, else the walk's next. */
std::string aheadUp (const Lane& lane, const Playlist& playlist);

/** On whenever the walk has two or more items. */
bool backEnabled (const Lane& lane, const Playlist& playlist);

nlohmann::json toJson (const Entry& entry);
nlohmann::json toJson (const Playlist& playlist);
nlohmann::json toJson (const Group& group);
nlohmann::json toJson (const Lane& lane);
Entry entryFromJson (const nlohmann::json& j);
Playlist playlistFromJson (const nlohmann::json& j);
Group groupFromJson (const nlohmann::json& j);
Lane laneFromJson (const nlohmann::json& j);
/** a fit from a state-file or show-arg object: missing keys default, bad types default, values clamp */
Fit fitFromJson (const nlohmann::json& j);

/** Build one lane and its playlist from a version-1 state file. */
void fromLegacyState (const nlohmann::json& state, Lane& lane, Playlist& playlist);

/** The lane's status block as reported to clients. */
nlohmann::json laneStatus (const Lane& lane, const Playlist& playlist, Clock::time_point now);
} // namespace WallpaperEngine::Api
