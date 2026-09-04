#pragma once

#include <cmath>
#include <glm/glm.hpp>

namespace WallpaperEngine::Render::Utils {

// Perlin noise permutation table
static const unsigned char PERLIN_PERM[]
    = { 151, 160, 137, 91, 90, 15, 131, 13, 201, 95, 96, 53, 194, 233, 7, 225, 140, 36, 103, 30, 69, 142, 8, 99, 37,
	240, 21, 10, 23, 190, 6, 148, 247, 120, 234, 75, 0, 26, 197, 62, 94, 252, 219, 203, 117, 35, 11, 32, 57, 177,
	33, 88, 237, 149, 56, 87, 174, 20, 125, 136, 171, 168, 68, 175, 74, 165, 71, 134, 139, 48, 27, 166, 77, 146,
	158, 231, 83, 111, 229, 122, 60, 211, 133, 230, 220, 105, 92, 41, 55, 46, 245, 40, 244, 102, 143, 54, 65, 25,
	63, 161, 1, 216, 80, 73, 209, 76, 132, 187, 208, 89, 18, 169, 200, 196, 135, 130, 116, 188, 159, 86, 164, 100,
	109, 198, 173, 186, 3, 64, 52, 217, 226, 250, 124, 123, 5, 202, 38, 147, 118, 126, 255, 82, 85, 212, 207, 206,
	59, 227, 47, 16, 58, 17, 182, 189, 28, 42, 223, 183, 170, 213, 119, 248, 152, 2, 44, 154, 163, 70, 221, 153,
	101, 155, 167, 43, 172, 9, 129, 22, 39, 253, 19, 98, 108, 110, 79, 113, 224, 232, 178, 185, 112, 104, 218, 246,
	97, 228, 251, 34, 242, 193, 238, 210, 144, 12, 191, 179, 162, 241, 81, 51, 145, 235, 249, 14, 239, 107, 49, 192,
	214, 31, 181, 199, 106, 157, 184, 84, 204, 176, 115, 121, 50, 45, 127, 4, 150, 254, 138, 236, 205, 93, 222, 114,
	67, 29, 24, 72, 243, 141, 128, 195, 78, 66, 215, 61, 156, 180,
	// Duplicate for wrapping
	151, 160, 137, 91, 90, 15, 131, 13, 201, 95, 96, 53, 194, 233, 7, 225, 140, 36, 103, 30, 69, 142, 8, 99, 37,
	240, 21, 10, 23, 190, 6, 148, 247, 120, 234, 75, 0, 26, 197, 62, 94, 252, 219, 203, 117, 35, 11, 32, 57, 177,
	33, 88, 237, 149, 56, 87, 174, 20, 125, 136, 171, 168, 68, 175, 74, 165, 71, 134, 139, 48, 27, 166, 77, 146,
	158, 231, 83, 111, 229, 122, 60, 211, 133, 230, 220, 105, 92, 41, 55, 46, 245, 40, 244, 102, 143, 54, 65, 25,
	63, 161, 1, 216, 80, 73, 209, 76, 132, 187, 208, 89, 18, 169, 200, 196, 135, 130, 116, 188, 159, 86, 164, 100,
	109, 198, 173, 186, 3, 64, 52, 217, 226, 250, 124, 123, 5, 202, 38, 147, 118, 126, 255, 82, 85, 212, 207, 206,
	59, 227, 47, 16, 58, 17, 182, 189, 28, 42, 223, 183, 170, 213, 119, 248, 152, 2, 44, 154, 163, 70, 221, 153,
	101, 155, 167, 43, 172, 9, 129, 22, 39, 253, 19, 98, 108, 110, 79, 113, 224, 232, 178, 185, 112, 104, 218, 246,
	97, 228, 251, 34, 242, 193, 238, 210, 144, 12, 191, 179, 162, 241, 81, 51, 145, 235, 249, 14, 239, 107, 49, 192,
	214, 31, 181, 199, 106, 157, 184, 84, 204, 176, 115, 121, 50, 45, 127, 4, 150, 254, 138, 236, 205, 93, 222, 114,
	67, 29, 24, 72, 243, 141, 128, 195, 78, 66, 215, 61, 156, 180 };

// Perlin noise gradient function
inline double perlinGrad (int hash, double x, double y, double z) {
    switch (hash & 0xF) {
	case 0x0:
	    return x + y;
	case 0x1:
	    return -x + y;
	case 0x2:
	    return x - y;
	case 0x3:
	    return -x - y;
	case 0x4:
	    return x + z;
	case 0x5:
	    return -x + z;
	case 0x6:
	    return x - z;
	case 0x7:
	    return -x - z;
	case 0x8:
	    return y + z;
	case 0x9:
	    return -y + z;
	case 0xA:
	    return y - z;
	case 0xB:
	    return -y - z;
	case 0xC:
	    return y + x;
	case 0xD:
	    return -y + z;
	case 0xE:
	    return y - x;
	case 0xF:
	    return -y - z;
	default:
	    return 0;
    }
}

// Perlin noise ease curve (6t^5 - 15t^4 + 10t^3)
inline double perlinEase (double t) { return t * t * t * (t * (t * 6.0 - 15.0) + 10.0); }

// Linear interpolation
inline double lerpDouble (double t, double a, double b) { return a + t * (b - a); }

// Perlin noise implementation
inline double perlinNoise (double x, double y, double z) {
    int X = static_cast<int> (std::floor (x)) & 255;
    int Y = static_cast<int> (std::floor (y)) & 255;
    int Z = static_cast<int> (std::floor (z)) & 255;

    x -= std::floor (x);
    y -= std::floor (y);
    z -= std::floor (z);

    double u = perlinEase (x);
    double v = perlinEase (y);
    double w = perlinEase (z);

    int A = PERLIN_PERM[X] + Y;
    int AA = PERLIN_PERM[A] + Z;
    int AB = PERLIN_PERM[A + 1] + Z;
    int B = PERLIN_PERM[X + 1] + Y;
    int BA = PERLIN_PERM[B] + Z;
    int BB = PERLIN_PERM[B + 1] + Z;

    return lerpDouble (
	w,
	lerpDouble (
	    v, lerpDouble (u, perlinGrad (PERLIN_PERM[AA], x, y, z), perlinGrad (PERLIN_PERM[BA], x - 1, y, z)),
	    lerpDouble (u, perlinGrad (PERLIN_PERM[AB], x, y - 1, z), perlinGrad (PERLIN_PERM[BB], x - 1, y - 1, z))
	),
	lerpDouble (
	    v,
	    lerpDouble (
		u, perlinGrad (PERLIN_PERM[AA + 1], x, y, z - 1), perlinGrad (PERLIN_PERM[BA + 1], x - 1, y, z - 1)
	    ),
	    lerpDouble (
		u, perlinGrad (PERLIN_PERM[AB + 1], x, y - 1, z - 1),
		perlinGrad (PERLIN_PERM[BB + 1], x - 1, y - 1, z - 1)
	    )
	)
    );
}

// Perlin noise vec3 (3 independent noise samples with different offsets)
inline glm::vec3 perlinNoiseVec3 (const glm::vec3& p) {
    return glm::vec3 (
	static_cast<float> (perlinNoise (p.x, p.y, p.z)),
	static_cast<float> (perlinNoise (p.x + 89.2, p.y + 33.1, p.z + 57.3)),
	static_cast<float> (perlinNoise (p.x + 100.3, p.y + 120.1, p.z + 142.2))
    );
}

// The gradient vectors behind perlinGrad, sixteen cases in the same order
static const glm::vec3 PERLIN_GRADS[16] = { { 1, 1, 0 },  { -1, 1, 0 }, { 1, -1, 0 }, { -1, -1, 0 }, { 1, 0, 1 },  { -1, 0, 1 },
					    { 1, 0, -1 }, { -1, 0, -1 }, { 0, 1, 1 }, { 0, -1, 1 },  { 0, 1, -1 }, { 0, -1, -1 },
					    { 1, 1, 0 },  { 0, -1, 1 },  { -1, 1, 0 }, { 0, -1, -1 } };

// Perlin noise with its analytic gradient, single precision
inline float perlinNoiseGrad (float x, float y, float z, glm::vec3& gradient) {
    const float fx = std::floor (x);
    const float fy = std::floor (y);
    const float fz = std::floor (z);
    const int X = static_cast<int> (fx) & 255;
    const int Y = static_cast<int> (fy) & 255;
    const int Z = static_cast<int> (fz) & 255;

    x -= fx;
    y -= fy;
    z -= fz;

    const float u = x * x * x * (x * (x * 6.0f - 15.0f) + 10.0f);
    const float v = y * y * y * (y * (y * 6.0f - 15.0f) + 10.0f);
    const float w = z * z * z * (z * (z * 6.0f - 15.0f) + 10.0f);
    const float du = 30.0f * x * x * (x * (x - 2.0f) + 1.0f);
    const float dv = 30.0f * y * y * (y * (y - 2.0f) + 1.0f);
    const float dw = 30.0f * z * z * (z * (z - 2.0f) + 1.0f);

    const int A = PERLIN_PERM[X] + Y;
    const int AA = PERLIN_PERM[A] + Z;
    const int AB = PERLIN_PERM[A + 1] + Z;
    const int B = PERLIN_PERM[X + 1] + Y;
    const int BA = PERLIN_PERM[B] + Z;
    const int BB = PERLIN_PERM[B + 1] + Z;

    const glm::vec3 ga = PERLIN_GRADS[0xF & PERLIN_PERM[AA]];
    const glm::vec3 gb = PERLIN_GRADS[0xF & PERLIN_PERM[BA]];
    const glm::vec3 gc = PERLIN_GRADS[0xF & PERLIN_PERM[AB]];
    const glm::vec3 gd = PERLIN_GRADS[0xF & PERLIN_PERM[BB]];
    const glm::vec3 ge = PERLIN_GRADS[0xF & PERLIN_PERM[AA + 1]];
    const glm::vec3 gf = PERLIN_GRADS[0xF & PERLIN_PERM[BA + 1]];
    const glm::vec3 gg = PERLIN_GRADS[0xF & PERLIN_PERM[AB + 1]];
    const glm::vec3 gh = PERLIN_GRADS[0xF & PERLIN_PERM[BB + 1]];

    const float a = glm::dot (ga, glm::vec3 (x, y, z));
    const float b = glm::dot (gb, glm::vec3 (x - 1, y, z));
    const float c = glm::dot (gc, glm::vec3 (x, y - 1, z));
    const float d = glm::dot (gd, glm::vec3 (x - 1, y - 1, z));
    const float e = glm::dot (ge, glm::vec3 (x, y, z - 1));
    const float f = glm::dot (gf, glm::vec3 (x - 1, y, z - 1));
    const float g = glm::dot (gg, glm::vec3 (x, y - 1, z - 1));
    const float h = glm::dot (gh, glm::vec3 (x - 1, y - 1, z - 1));

    const float k1 = b - a;
    const float k2 = c - a;
    const float k3 = e - a;
    const float k4 = a - b - c + d;
    const float k5 = a - c - e + g;
    const float k6 = a - b - e + f;
    const float k7 = -a + b + c - d + e - f - g + h;

    gradient = ga + u * (gb - ga) + v * (gc - ga) + w * (ge - ga) + u * v * (ga - gb - gc + gd)
	+ v * w * (ga - gc - ge + gg) + w * u * (ga - gb - ge + gf) + u * v * w * (-ga + gb + gc - gd + ge - gf - gg + gh);
    gradient.x += du * (k1 + k4 * v + k6 * w + k7 * v * w);
    gradient.y += dv * (k2 + k4 * u + k5 * w + k7 * u * w);
    gradient.z += dw * (k3 + k5 * v + k6 * u + k7 * u * v);

    return a + k1 * u + k2 * v + k3 * w + k4 * u * v + k5 * v * w + k6 * w * u + k7 * u * v * w;
}

// Curl of the three offset noise fields, from their analytic gradients
inline glm::vec3 curlNoise (const glm::vec3& p) {
    glm::vec3 g1, g2, g3;
    perlinNoiseGrad (p.x, p.y, p.z, g1);
    perlinNoiseGrad (p.x + 89.2f, p.y + 33.1f, p.z + 57.3f, g2);
    perlinNoiseGrad (p.x + 100.3f, p.y + 120.1f, p.z + 142.2f, g3);

    return { g3.y - g2.z, g1.z - g3.x, g2.x - g1.y };
}

} // namespace WallpaperEngine::Render::Utils
