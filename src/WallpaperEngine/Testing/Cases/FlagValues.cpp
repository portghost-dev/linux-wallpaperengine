#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_exception.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>

#include <filesystem>
#include <numbers>
#include <stdexcept>
#include <string>

#include <glm/vec4.hpp>

#include "WallpaperEngine/Application/FlagValues.h"

using namespace WallpaperEngine::Application;
using Catch::Matchers::Message;
using Catch::Matchers::MessageMatches;
using Catch::Matchers::StartsWith;
using Catch::Matchers::WithinAbs;

namespace {
const char* const MALFORMED[] = { "", "abc", "1x", " 1", "nan", "inf" };
} // namespace

TEST_CASE ("clampFactor takes a number up to 4, where 0 or below is no cap", "[flags]") {
    CHECK (FlagValues::clampFactor ("--resclamp", "4") == 4.0f);
    CHECK (FlagValues::clampFactor ("--resclamp", "2.5") == 2.5f);
    CHECK (FlagValues::clampFactor ("--resclamp", "0.5") == 0.5f);
    CHECK (FlagValues::clampFactor ("--resclamp", "0") == 0.0f);
    CHECK (FlagValues::clampFactor ("--resclamp", "-1") == 0.0f);

    for (const char* text : MALFORMED) {
	CAPTURE (text);
	CHECK_THROWS_MATCHES (
	    FlagValues::clampFactor ("--resclamp", text), std::runtime_error,
	    MessageMatches (StartsWith ("--resclamp "))
	);
    }

    CHECK_THROWS_MATCHES (
	FlagValues::clampFactor ("--resclamp", "4.5"), std::runtime_error,
	Message ("--resclamp takes a number up to 4, where 0 or below is no cap; got 4.5")
    );
}

TEST_CASE ("onOff takes on or off", "[flags]") {
    CHECK (FlagValues::onOff ("--texturecache", "on"));
    CHECK_FALSE (FlagValues::onOff ("--texturecache", "off"));

    for (const char* text : MALFORMED) {
	CAPTURE (text);
	CHECK_THROWS_MATCHES (
	    FlagValues::onOff ("--texturecache", text), std::runtime_error,
	    MessageMatches (StartsWith ("--texturecache "))
	);
    }

    CHECK_THROWS_MATCHES (
	FlagValues::onOff ("--texturecache", "abc"), std::runtime_error,
	Message ("--texturecache takes on or off; got abc")
    );
}

TEST_CASE ("autoFull takes auto or full", "[flags]") {
    CHECK (FlagValues::autoFull ("--texturedetail", "auto"));
    CHECK_FALSE (FlagValues::autoFull ("--texturedetail", "full"));

    for (const char* text : MALFORMED) {
	CAPTURE (text);
	CHECK_THROWS_MATCHES (
	    FlagValues::autoFull ("--texturedetail", text), std::runtime_error,
	    MessageMatches (StartsWith ("--texturedetail "))
	);
    }

    CHECK_THROWS_MATCHES (
	FlagValues::autoFull ("--texturedetail", "abc"), std::runtime_error,
	Message ("--texturedetail takes auto or full; got abc")
    );
}

TEST_CASE ("videoDecode takes software or auto and gives the decoder mode", "[flags]") {
    CHECK (FlagValues::videoDecode ("--videodecode", "software") == "no");
    CHECK (FlagValues::videoDecode ("--videodecode", "auto") == "auto");

    for (const char* text : MALFORMED) {
	CAPTURE (text);
	CHECK_THROWS_MATCHES (
	    FlagValues::videoDecode ("--videodecode", text), std::runtime_error,
	    MessageMatches (StartsWith ("--videodecode "))
	);
    }

    CHECK_THROWS_MATCHES (
	FlagValues::videoDecode ("--videodecode", "abc"), std::runtime_error,
	Message ("--videodecode takes software or auto; got abc")
    );
}

TEST_CASE ("color takes four numbers with the hue in degrees and gives the hue in radians", "[flags]") {
    CHECK (FlagValues::color ("--color", "1 1 1 0") == glm::vec4 (1.0f, 1.0f, 1.0f, 0.0f));

    const glm::vec4 low = FlagValues::color ("--color", "0 0 0 -180");
    CHECK (low.x == 0.0f);
    CHECK (low.y == 0.0f);
    CHECK (low.z == 0.0f);
    CHECK_THAT (low.w, WithinAbs (-std::numbers::pi, 1e-6));

    const glm::vec4 high = FlagValues::color ("--color", "4 4 4 180");
    CHECK (high.x == 4.0f);
    CHECK (high.y == 4.0f);
    CHECK (high.z == 4.0f);
    CHECK_THAT (high.w, WithinAbs (std::numbers::pi, 1e-6));

    const glm::vec4 spaced = FlagValues::color ("--color", "1.5  0.5   2 90");
    CHECK (spaced.x == 1.5f);
    CHECK (spaced.y == 0.5f);
    CHECK (spaced.z == 2.0f);
    CHECK_THAT (spaced.w, WithinAbs (std::numbers::pi / 2.0, 1e-6));

    for (const char* text : MALFORMED) {
	CAPTURE (text);
	CHECK_THROWS_MATCHES (
	    FlagValues::color ("--color", text), std::runtime_error, MessageMatches (StartsWith ("--color "))
	);
    }

    for (const char* text : { "-0.1 1 1 0", "4.1 1 1 0", "1 -0.1 1 0", "1 4.1 1 0", "1 1 -0.1 0", "1 1 4.1 0",
			      "1 1 1 -180.5", "1 1 1 180.5" }) {
	CAPTURE (text);
	CHECK_THROWS_MATCHES (
	    FlagValues::color ("--color", text), std::runtime_error, MessageMatches (StartsWith ("--color "))
	);
    }

    CHECK_THROWS_MATCHES (
	FlagValues::color ("--color", "1 1 1 180.5"), std::runtime_error,
	Message (
	    "--color takes \"brightness contrast saturation hue\", each 0 to 4 and hue -180 to 180 degrees; got 1 1 1 "
	    "180.5"
	)
    );
}

TEST_CASE ("decimalInRange takes a number from lo to hi, both included", "[flags]") {
    CHECK (FlagValues::decimalInRange ("--speed", "0", 0.0, 10.0) == 0.0f);
    CHECK (FlagValues::decimalInRange ("--speed", "2.5", 0.0, 10.0) == 2.5f);
    CHECK (FlagValues::decimalInRange ("--speed", "10", 0.0, 10.0) == 10.0f);
    CHECK (FlagValues::decimalInRange ("--lightdimming", "0.01", 0.01, 1000.0) == 0.01f);
    CHECK (FlagValues::decimalInRange ("--lightdimming", "16", 0.01, 1000.0) == 16.0f);
    CHECK (FlagValues::decimalInRange ("--lightdimming", "1000", 0.01, 1000.0) == 1000.0f);
    CHECK (FlagValues::decimalInRange ("--lightfalloff", "0.5", 0.5, 6.0) == 0.5f);
    CHECK (FlagValues::decimalInRange ("--lightfalloff", "2", 0.5, 6.0) == 2.0f);
    CHECK (FlagValues::decimalInRange ("--lightfalloff", "6", 0.5, 6.0) == 6.0f);
    CHECK (FlagValues::decimalInRange ("--audiogain", "0.1", 0.1, 20.0) == 0.1f);
    CHECK (FlagValues::decimalInRange ("--audiogain", "1", 0.1, 20.0) == 1.0f);
    CHECK (FlagValues::decimalInRange ("--audiogain", "20", 0.1, 20.0) == 20.0f);

    for (const char* text : MALFORMED) {
	CAPTURE (text);
	CHECK_THROWS_MATCHES (
	    FlagValues::decimalInRange ("--speed", text, 0.0, 10.0), std::runtime_error,
	    MessageMatches (StartsWith ("--speed "))
	);
    }

    CHECK_THROWS_MATCHES (
	FlagValues::decimalInRange ("--speed", "-0.1", 0.0, 10.0), std::runtime_error,
	Message ("--speed takes a number from 0 to 10; got -0.1")
    );
    CHECK_THROWS_MATCHES (
	FlagValues::decimalInRange ("--speed", "10.5", 0.0, 10.0), std::runtime_error,
	Message ("--speed takes a number from 0 to 10; got 10.5")
    );
    CHECK_THROWS_MATCHES (
	FlagValues::decimalInRange ("--lightdimming", "0", 0.01, 1000.0), std::runtime_error,
	Message ("--lightdimming takes a number from 0.01 to 1000; got 0")
    );
    CHECK_THROWS_MATCHES (
	FlagValues::decimalInRange ("--lightdimming", "1000.5", 0.01, 1000.0), std::runtime_error,
	Message ("--lightdimming takes a number from 0.01 to 1000; got 1000.5")
    );
    CHECK_THROWS_MATCHES (
	FlagValues::decimalInRange ("--lightfalloff", "0.4", 0.5, 6.0), std::runtime_error,
	Message ("--lightfalloff takes a number from 0.5 to 6; got 0.4")
    );
    CHECK_THROWS_MATCHES (
	FlagValues::decimalInRange ("--lightfalloff", "6.5", 0.5, 6.0), std::runtime_error,
	Message ("--lightfalloff takes a number from 0.5 to 6; got 6.5")
    );
    CHECK_THROWS_MATCHES (
	FlagValues::decimalInRange ("--audiogain", "0.05", 0.1, 20.0), std::runtime_error,
	Message ("--audiogain takes a number from 0.1 to 20; got 0.05")
    );
    CHECK_THROWS_MATCHES (
	FlagValues::decimalInRange ("--audiogain", "20.5", 0.1, 20.0), std::runtime_error,
	Message ("--audiogain takes a number from 0.1 to 20; got 20.5")
    );
}

TEST_CASE ("watchdogSeconds takes whole seconds, or a whole number with s, m or h, up to a day", "[flags]") {
    CHECK (FlagValues::watchdogSeconds ("--watchdog", "0") == 0);
    CHECK (FlagValues::watchdogSeconds ("--watchdog", "300") == 300);
    CHECK (FlagValues::watchdogSeconds ("--watchdog", "86400") == 86400);
    CHECK (FlagValues::watchdogSeconds ("--watchdog", "30s") == 30);
    CHECK (FlagValues::watchdogSeconds ("--watchdog", "86400s") == 86400);
    CHECK (FlagValues::watchdogSeconds ("--watchdog", "5m") == 300);
    CHECK (FlagValues::watchdogSeconds ("--watchdog", "1440m") == 86400);
    CHECK (FlagValues::watchdogSeconds ("--watchdog", "1h") == 3600);
    CHECK (FlagValues::watchdogSeconds ("--watchdog", "24h") == 86400);

    for (const char* text : MALFORMED) {
	CAPTURE (text);
	CHECK_THROWS_MATCHES (
	    FlagValues::watchdogSeconds ("--watchdog", text), std::runtime_error,
	    MessageMatches (StartsWith ("--watchdog "))
	);
    }

    for (const char* text : { "-1", "86401s", "1441m", "25h" }) {
	CAPTURE (text);
	CHECK_THROWS_MATCHES (
	    FlagValues::watchdogSeconds ("--watchdog", text), std::runtime_error,
	    MessageMatches (StartsWith ("--watchdog "))
	);
    }

    CHECK_THROWS_MATCHES (
	FlagValues::watchdogSeconds ("--watchdog", "86401"), std::runtime_error,
	Message ("--watchdog takes seconds from 0 to 86400, or a number with s, m or h up to 24h; got 86401")
    );
}

TEST_CASE ("milliseconds takes a number from 0 to 500, with or without ms", "[flags]") {
    CHECK (FlagValues::milliseconds ("--audiosmoothing", "0") == 0.0f);
    CHECK (FlagValues::milliseconds ("--audiosmoothing", "90") == 90.0f);
    CHECK (FlagValues::milliseconds ("--audiosmoothing", "500") == 500.0f);
    CHECK (FlagValues::milliseconds ("--audiosmoothing", "0ms") == 0.0f);
    CHECK (FlagValues::milliseconds ("--audiosmoothing", "90ms") == 90.0f);
    CHECK (FlagValues::milliseconds ("--audiosmoothing", "2.5ms") == 2.5f);
    CHECK (FlagValues::milliseconds ("--audiosmoothing", "500ms") == 500.0f);

    for (const char* text : MALFORMED) {
	CAPTURE (text);
	CHECK_THROWS_MATCHES (
	    FlagValues::milliseconds ("--audiosmoothing", text), std::runtime_error,
	    MessageMatches (StartsWith ("--audiosmoothing "))
	);
    }

    CHECK_THROWS_MATCHES (
	FlagValues::milliseconds ("--audiosmoothing", "-1"), std::runtime_error,
	MessageMatches (StartsWith ("--audiosmoothing "))
    );
    CHECK_THROWS_MATCHES (
	FlagValues::milliseconds ("--audiosmoothing", "500.5"), std::runtime_error,
	Message ("--audiosmoothing takes milliseconds from 0 to 500; got 500.5")
    );
}

TEST_CASE ("path takes any text that is not empty", "[flags]") {
    CHECK (FlagValues::path ("--socket", "/tmp/lwe-flag.sock") == std::filesystem::path ("/tmp/lwe-flag.sock"));

    for (const char* text : { "abc", "1x", " 1", "nan", "inf" }) {
	CAPTURE (text);
	CHECK (FlagValues::path ("--socket", text) == std::filesystem::path (text));
    }

    CHECK_THROWS_MATCHES (
	FlagValues::path ("--socket", ""), std::runtime_error, Message ("--socket takes a path; got an empty value")
    );
}
