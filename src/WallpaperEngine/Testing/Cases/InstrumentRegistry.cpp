#include <catch2/catch_test_macros.hpp>

#include "WallpaperEngine/Logging/InstrumentRegistry.h"

using namespace WallpaperEngine::Logging;

TEST_CASE ("instrument registry: unknown names are rejected, not silently accepted") {
    REQUIRE (instrumentKnown ("LWE_PARTSTATS"));
    REQUIRE_FALSE (instrumentKnown ("LWE_NOT_AN_INSTRUMENT"));

    // A launch-time switch must be refused rather than accepted into a no-op. LWE_TEXCOMP
    // decides a texture upload format at construction, so flipping it live cannot mean
    // anything - the caller has to learn that from the reply, not from silence.
    REQUIRE_FALSE (instrumentKnown ("LWE_TEXCOMP"));
    REQUIRE_FALSE (instrumentSet ("LWE_TEXCOMP", true));

    REQUIRE_FALSE (instrumentOn ("LWE_NOT_AN_INSTRUMENT"));
    REQUIRE (instrumentEpoch ("LWE_NOT_AN_INSTRUMENT") == 0);
}

TEST_CASE ("instrument registry: epoch ticks on the off->on edge only") {
    REQUIRE (instrumentSet ("LWE_ROPETRAILPROBE", false));
    const auto base = instrumentEpoch ("LWE_ROPETRAILPROBE");

    SECTION ("enabling advances it") {
	REQUIRE (instrumentSet ("LWE_ROPETRAILPROBE", true));
	REQUIRE (instrumentOn ("LWE_ROPETRAILPROBE"));
	REQUIRE (instrumentEpoch ("LWE_ROPETRAILPROBE") == base + 1);
    }

    SECTION ("enabling an ALREADY-ON instrument must not advance it") {
	// the operator pressing the same switch twice must not silently wipe the window they
	// are reading - only a genuine off->on transition means "start clean"
	REQUIRE (instrumentSet ("LWE_ROPETRAILPROBE", true));
	const auto after = instrumentEpoch ("LWE_ROPETRAILPROBE");
	REQUIRE (instrumentSet ("LWE_ROPETRAILPROBE", true));
	REQUIRE (instrumentEpoch ("LWE_ROPETRAILPROBE") == after);
    }

    SECTION ("disabling never advances it") {
	REQUIRE (instrumentSet ("LWE_ROPETRAILPROBE", true));
	const auto after = instrumentEpoch ("LWE_ROPETRAILPROBE");
	REQUIRE (instrumentSet ("LWE_ROPETRAILPROBE", false));
	REQUIRE_FALSE (instrumentOn ("LWE_ROPETRAILPROBE"));
	REQUIRE (instrumentEpoch ("LWE_ROPETRAILPROBE") == after);
    }

    SECTION ("a full off/on cycle advances exactly once, so latched state clears once") {
	REQUIRE (instrumentSet ("LWE_ROPETRAILPROBE", true));
	REQUIRE (instrumentSet ("LWE_ROPETRAILPROBE", false));
	REQUIRE (instrumentSet ("LWE_ROPETRAILPROBE", true));
	REQUIRE (instrumentEpoch ("LWE_ROPETRAILPROBE") == base + 2);
    }
}

TEST_CASE ("instrument registry: instruments are independent") {
    REQUIRE (instrumentSet ("LWE_PARTSTATS", true));
    REQUIRE (instrumentSet ("LWE_TWINKLEPROBE", false));
    REQUIRE (instrumentOn ("LWE_PARTSTATS"));
    REQUIRE_FALSE (instrumentOn ("LWE_TWINKLEPROBE"));

    const auto twinkle = instrumentEpoch ("LWE_TWINKLEPROBE");
    REQUIRE (instrumentSet ("LWE_PARTSTATS", false));
    REQUIRE (instrumentSet ("LWE_PARTSTATS", true));
    REQUIRE (instrumentEpoch ("LWE_TWINKLEPROBE") == twinkle);
}

TEST_CASE ("instrument registry: the enabled list is what status reports") {
    REQUIRE (instrumentSet ("LWE_PARTSTATS", false));
    REQUIRE (instrumentSet ("LWE_TWINKLEPROBE", false));
    REQUIRE (instrumentSet ("LWE_ROPETRAILPROBE", false));
    REQUIRE (instrumentsEnabled ().empty ());

    REQUIRE (instrumentSet ("LWE_TWINKLEPROBE", true));
    REQUIRE (instrumentsEnabled () == "LWE_TWINKLEPROBE");

    REQUIRE (instrumentSet ("LWE_PARTSTATS", true));
    // table order, not insertion order - a stable string is what a UI can diff against
    REQUIRE (instrumentsEnabled () == "LWE_PARTSTATS,LWE_TWINKLEPROBE");

    REQUIRE (instrumentSet ("LWE_PARTSTATS", false));
    REQUIRE (instrumentSet ("LWE_TWINKLEPROBE", false));
}
