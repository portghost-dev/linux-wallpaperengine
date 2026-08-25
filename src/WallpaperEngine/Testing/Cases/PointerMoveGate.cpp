
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <optional>

#include "WallpaperEngine/Input/PointerMoveGate.h"
#include "WallpaperEngine/Testing/Input/TestingMouseInput.h"

using WallpaperEngine::Input::PointerMoveGate;
using WallpaperEngine::Testing::Input::TestingMouseInput;

namespace {
constexpr int WIDTH = 2560;
constexpr int HEIGHT = 1440;

PointerMoveGate::Decision sample (PointerMoveGate& gate, const TestingMouseInput& input) {
    return gate.update (
	input.hasPointer () ? std::optional<glm::dvec2> (input.normalized ()) : std::nullopt, WIDTH, HEIGHT
    );
}
} // namespace

TEST_CASE ("PointerMoveGate sends the first known sample and suppresses repeats") {
    PointerMoveGate gate;

    const auto first = gate.update (glm::dvec2 { 0.25, 0.75 }, WIDTH, HEIGHT);
    CHECK (first.send);
    CHECK (first.x == 640);
    CHECK (first.y == 1080);

    for (int i = 0; i < 2; ++i) {
	const auto repeat = gate.update (glm::dvec2 { 0.25, 0.75 }, WIDTH, HEIGHT);
	CHECK_FALSE (repeat.send);
	CHECK (repeat.x == 640);
	CHECK (repeat.y == 1080);
    }

    CHECK (gate.movesSent () == 1);
    CHECK (gate.movesSuppressed () == 2);
    CHECK (gate.movesUnknown () == 0);
}

TEST_CASE ("PointerMoveGate sends the first sample even at the origin") {
    PointerMoveGate gate;

    const auto first = gate.update (glm::dvec2 { 0.0, 0.0 }, WIDTH, HEIGHT);
    CHECK (first.send);
    CHECK (first.x == 0);
    CHECK (first.y == 0);
    CHECK (gate.movesSent () == 1);
}

TEST_CASE ("PointerMoveGate tracks a moving pointer") {
    PointerMoveGate gate;
    int distinct = 0;
    int firstViolation = -1;
    std::optional<glm::ivec2> previous;

    for (int i = 0; i < 720; ++i) {
	const double angle = static_cast<double> (i) * 3.14159265358979 / 180.0;
	const glm::dvec2 n { 0.5 + 0.3 * std::cos (angle), 0.5 + 0.3 * std::sin (angle) };
	const auto decision = gate.update (n, WIDTH, HEIGHT);

	const glm::ivec2 resolved (decision.x, decision.y);
	const bool changed = !previous.has_value () || *previous != resolved;
	previous = resolved;

	if (decision.send != changed && firstViolation < 0) {
	    firstViolation = i;
	}

	if (changed) {
	    distinct++;
	}
    }

    CHECK (firstViolation == -1);

    CHECK (gate.movesSent () == static_cast<uint64_t> (distinct));
    CHECK (gate.movesSent () > 400);
    CHECK (gate.movesSent () + gate.movesSuppressed () == 720);
    CHECK (gate.movesUnknown () == 0);
}

TEST_CASE ("PointerMoveGate sends nothing at all while the pointer is unknown") {
    PointerMoveGate gate;
    int sent = 0;

    for (int i = 0; i < 240; ++i) {
	if (gate.update (std::nullopt, WIDTH, HEIGHT).send) {
	    sent++;
	}
    }

    CHECK (sent == 0);
    CHECK (gate.movesSent () == 0);
    CHECK (gate.movesSuppressed () == 0);
    CHECK (gate.movesUnknown () == 240);
    CHECK_FALSE (gate.lastSent ().has_value ());
}

TEST_CASE ("PointerMoveGate does not let an unknown sample poison the dedup") {
    PointerMoveGate gate;

    CHECK (gate.update (glm::dvec2 { 0.5, 0.5 }, WIDTH, HEIGHT).send);
    CHECK (gate.lastSent ().has_value ());
    CHECK (*gate.lastSent () == glm::ivec2 (1280, 720));

    int sentWhileUnknown = 0;

    for (int i = 0; i < 30; ++i) {
	if (gate.update (std::nullopt, WIDTH, HEIGHT).send) {
	    sentWhileUnknown++;
	}
    }

    CHECK (sentWhileUnknown == 0);

    CHECK (*gate.lastSent () == glm::ivec2 (1280, 720));

    const auto returned = gate.update (glm::dvec2 { 0.1, 0.9 }, WIDTH, HEIGHT);
    CHECK (returned.send);
    CHECK (returned.x == 256);
    CHECK (returned.y == 1296);

    CHECK_FALSE (gate.update (glm::dvec2 { 0.1, 0.9 }, WIDTH, HEIGHT).send);
}

TEST_CASE ("PointerMoveGate treats a degenerate size as unknown") {
    PointerMoveGate gate;

    CHECK_FALSE (gate.update (glm::dvec2 { 0.5, 0.5 }, 0, HEIGHT).send);
    CHECK_FALSE (gate.update (glm::dvec2 { 0.5, 0.5 }, WIDTH, 0).send);
    CHECK (gate.movesUnknown () == 2);
    CHECK_FALSE (gate.lastSent ().has_value ());
}

TEST_CASE ("PointerMoveGate counters reset the way the instrument prints them") {
    PointerMoveGate gate;

    gate.update (glm::dvec2 { 0.2, 0.2 }, WIDTH, HEIGHT);
    gate.update (glm::dvec2 { 0.2, 0.2 }, WIDTH, HEIGHT);
    gate.update (std::nullopt, WIDTH, HEIGHT);
    CHECK (gate.movesSent () == 1);
    CHECK (gate.movesSuppressed () == 1);
    CHECK (gate.movesUnknown () == 1);

    gate.resetCounters ();
    CHECK (gate.movesSent () == 0);
    CHECK (gate.movesSuppressed () == 0);
    CHECK (gate.movesUnknown () == 0);

    // resetting the counters must NOT forget where the pointer is
    CHECK (gate.lastSent ().has_value ());
    CHECK_FALSE (gate.update (glm::dvec2 { 0.2, 0.2 }, WIDTH, HEIGHT).send);
}

TEST_CASE ("MouseInput reports whether its normalized value describes a real pointer") {
    TestingMouseInput input;
    PointerMoveGate gate;

    CHECK (input.hasPointer ());
    CHECK (input.normalized () == glm::dvec2 (0.5, 0.5));
    CHECK (sample (gate, input).send);

    input.setNormalized (std::nullopt);
    CHECK_FALSE (input.hasPointer ());
    CHECK (input.normalized () == glm::dvec2 (0.5, 0.5));

    int sentWhileUnknown = 0;

    for (int i = 0; i < 60; ++i) {
	if (sample (gate, input).send) {
	    sentWhileUnknown++;
	}
    }

    CHECK (sentWhileUnknown == 0);
    CHECK (gate.movesSent () == 1);
    CHECK (gate.movesUnknown () == 60);

    input.setNormalized (glm::dvec2 { 0.75, 0.25 });
    const auto back = sample (gate, input);
    CHECK (back.send);
    CHECK (back.x == 1920);
    CHECK (back.y == 360);
}
