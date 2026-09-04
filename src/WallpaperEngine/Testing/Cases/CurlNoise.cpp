#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include "WallpaperEngine/Render/Utils/NoiseUtils.h"

using Catch::Matchers::WithinAbs;
using namespace WallpaperEngine::Render::Utils;

namespace {
glm::vec3 finiteDifferenceCurl (const glm::dvec3& p) {
    const double e = 1e-5;
    auto field = [] (const glm::dvec3& q) {
	return glm::dvec3 (
	    perlinNoise (q.x, q.y, q.z), perlinNoise (q.x + 89.2, q.y + 33.1, q.z + 57.3),
	    perlinNoise (q.x + 100.3, q.y + 120.1, q.z + 142.2)
	);
    };
    const glm::dvec3 dx (e, 0, 0), dy (0, e, 0), dz (0, 0, e);
    const glm::dvec3 x0 = field (p - dx), x1 = field (p + dx);
    const glm::dvec3 y0 = field (p - dy), y1 = field (p + dy);
    const glm::dvec3 z0 = field (p - dz), z1 = field (p + dz);
    return glm::vec3 (
	((y1.z - y0.z) - (z1.y - z0.y)) / (2 * e), ((z1.x - z0.x) - (x1.z - x0.z)) / (2 * e),
	((x1.y - x0.y) - (y1.x - y0.x)) / (2 * e)
    );
}
} // namespace

TEST_CASE ("Single precision perlin noise matches the double version") {
    for (int i = 0; i < 200; i++) {
	const float x = -60.0f + i * 0.73f, y = 12.5f + i * 0.31f, z = -3.0f + i * 0.17f;
	glm::vec3 gradient;
	CHECK_THAT (perlinNoiseGrad (x, y, z, gradient), WithinAbs (perlinNoise (x, y, z), 1e-4));
    }
}

TEST_CASE ("Analytic curl matches the finite difference curl of the same fields") {
    for (int i = 0; i < 200; i++) {
	const glm::vec3 p (-45.0f + i * 0.47f, 8.0f + i * 0.29f, -20.0f + i * 0.61f);
	const glm::vec3 analytic = curlNoise (p);
	const glm::vec3 numeric = finiteDifferenceCurl (glm::dvec3 (p));
	CHECK_THAT (analytic.x, WithinAbs (numeric.x, 2e-3));
	CHECK_THAT (analytic.y, WithinAbs (numeric.y, 2e-3));
	CHECK_THAT (analytic.z, WithinAbs (numeric.z, 2e-3));
    }
}
