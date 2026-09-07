#include <catch2/catch_test_macros.hpp>

#include "WallpaperEngine/Api/Lane.h"

using namespace WallpaperEngine::Api;

namespace {
Schedule dayNight (int dayFrom = 8 * 60, int dayTo = 20 * 60) {
    Schedule schedule;
    schedule.enabled = true;
    schedule.entries = { { .minute = dayFrom, .slug = "day" }, { .minute = dayTo, .slug = "night" } };
    return schedule;
}
} // namespace

TEST_CASE ("the entry in force is the latest boundary at or before the minute, wrapping past midnight", "[schedule]") {
    const auto schedule = dayNight ();
    CHECK (scheduleSlugAt (schedule, 8 * 60) == "day");
    CHECK (scheduleSlugAt (schedule, 12 * 60) == "day");
    CHECK (scheduleSlugAt (schedule, 19 * 60 + 59) == "day");
    CHECK (scheduleSlugAt (schedule, 20 * 60) == "night");
    CHECK (scheduleSlugAt (schedule, 23 * 60 + 59) == "night");
    CHECK (scheduleSlugAt (schedule, 0) == "night");
    CHECK (scheduleSlugAt (schedule, 7 * 60 + 59) == "night");
    CHECK (scheduleIsDay (schedule, 12 * 60));
    CHECK_FALSE (scheduleIsDay (schedule, 3 * 60));
    CHECK (scheduleMinutesToBoundary (schedule, 12 * 60) == 8 * 60);
    CHECK (scheduleMinutesToBoundary (schedule, 20 * 60) == 12 * 60);
    CHECK (scheduleMinutesToBoundary (schedule, 23 * 60) == 9 * 60);

    // day is the span from the first entry to the second, wherever the clock puts them (R68)
    const auto nightOwl = dayNight (22 * 60, 6 * 60);
    CHECK (scheduleSlugAt (nightOwl, 23 * 60) == "day");
    CHECK (scheduleSlugAt (nightOwl, 3 * 60) == "day");
    CHECK (scheduleSlugAt (nightOwl, 12 * 60) == "night");
    CHECK (scheduleIsDay (nightOwl, 3 * 60));

    Schedule empty;
    CHECK (scheduleSlugAt (empty, 100).empty ());
    CHECK (scheduleMinutesToBoundary (empty, 100) == -1);
    CHECK_FALSE (scheduleIsDay (empty, 100));
}

TEST_CASE ("a crossed boundary sets the pending switch; the same slug clears it", "[schedule]") {
    auto schedule = dayNight ();

    // first tick after enabling, mid-day, bound to night: pending day right away (R70 applies it)
    CHECK_FALSE (scheduleTick (schedule, "night", 12 * 60));
    CHECK (schedule.pending == "day");

    // once the lane is bound to day nothing is pending
    CHECK_FALSE (scheduleTick (schedule, "day", 12 * 60 + 1));
    CHECK (schedule.pending.empty ());

    // the 20:00 boundary crossed between two ticks
    CHECK (scheduleTick (schedule, "day", 20 * 60));
    CHECK (schedule.pending == "night");

    // midnight wrap: a tick at 23:59 then 00:01 crosses no boundary of this schedule
    CHECK_FALSE (scheduleTick (schedule, "night", 23 * 60 + 59));
    CHECK_FALSE (scheduleTick (schedule, "night", 1));
    CHECK (schedule.pending.empty ());

    // a boundary sitting inside a wrap is crossed: 23:59 to 08:01 passes 08:00
    schedule.lastMinute = 23 * 60 + 59;
    CHECK (scheduleTick (schedule, "night", 8 * 60 + 1));
    CHECK (schedule.pending == "day");
}

TEST_CASE ("a manual hold keeps the lane until the next boundary, then the clock rules again", "[schedule]") {
    auto schedule = dayNight ();
    scheduleTick (schedule, "day", 12 * 60);
    REQUIRE (schedule.pending.empty ());

    // the user switches to party at 15:00: the engine marks the hold, the clock wants day but waits
    schedule.held = true;
    CHECK_FALSE (scheduleTick (schedule, "party", 15 * 60));
    CHECK (schedule.pending.empty ());
    CHECK (schedule.held);

    // 20:00 crosses: the hold ends and night is pending (R67)
    CHECK (scheduleTick (schedule, "party", 20 * 60));
    CHECK_FALSE (schedule.held);
    CHECK (schedule.pending == "night");

    // a disabled schedule holds nothing and pends nothing
    schedule.enabled = false;
    schedule.held = true;
    scheduleTick (schedule, "party", 21 * 60);
    CHECK_FALSE (schedule.held);
    CHECK (schedule.pending.empty ());
}

TEST_CASE ("the schedule round-trips through the state file and drops a bad entry", "[schedule]") {
    auto schedule = dayNight (7 * 60 + 30, 21 * 60);
    schedule.held = true;
    schedule.pending = "night";

    const auto restored = scheduleFromJson (toJson (schedule));
    REQUIRE (restored.entries.size () == 2);
    CHECK (restored.enabled);
    CHECK (restored.held);
    CHECK (restored.pending == "night");
    CHECK (restored.entries[0].minute == 7 * 60 + 30);
    CHECK (restored.entries[1].slug == "night");
    CHECK (restored.lastMinute == -1);

    auto edited = toJson (schedule);
    edited["entries"].push_back ({ { "minute", 5000 }, { "playlist", "late" } });
    edited["entries"].push_back ({ { "minute", "noon" }, { "playlist", "x" } });
    CHECK (scheduleFromJson (edited).entries.size () == 2);
    CHECK (scheduleFromJson (7).entries.empty ());
}

TEST_CASE ("a re-push of the same schedule is not a change; a backward clock step crosses nothing", "[schedule]") {
    const auto a = dayNight ();
    auto b = dayNight ();
    CHECK (sameSchedule (a, b));
    b.held = true;
    CHECK (sameSchedule (a, b));
    b.entries[1].minute = 21 * 60;
    CHECK_FALSE (sameSchedule (a, b));
    b = dayNight ();
    b.enabled = false;
    CHECK_FALSE (sameSchedule (a, b));

    // holding party at 20:30; an NTP step back to 19:50 is not a trip through midnight
    auto schedule = dayNight ();
    scheduleTick (schedule, "party", 20 * 60 + 30);
    schedule.held = true;
    scheduleTick (schedule, "party", 20 * 60 + 30);
    CHECK_FALSE (scheduleTick (schedule, "party", 19 * 60 + 50));
    CHECK (schedule.held);
    // and moving forward again over 20:00 does cross
    CHECK (scheduleTick (schedule, "party", 20 * 60 + 5));
    CHECK_FALSE (schedule.held);
}
