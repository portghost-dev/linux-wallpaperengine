#include "CLight.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>

#include <glm/geometric.hpp>
#include <glm/gtc/constants.hpp>
#include <glm/gtc/matrix_transform.hpp>

using namespace WallpaperEngine::Render::Objects;

CLight::CLight (Wallpapers::CScene& scene, const Light& light) :
    CObject (scene, light), ScriptableObject (scene, light) {
    // lights consume the BASE transform fields (CScene::updateLights reads origin +
    // groupAngles), so those are the instances scripts must tick
    this->registerProperty ("origin", *light.origin->value);
    this->registerProperty ("angles", *light.groupAngles->value);
    this->registerProperty ("visible", *light.groupVisible->value);
    this->registerProperty ("color", *light.color->value);
    this->registerProperty ("intensity", *light.intensity->value);
}

void CLight::render () { }

glm::vec2 CLight::calculateSpotConeCosines (const float innerDegrees, const float outerDegrees) {
    constexpr float degreesToRadians = 0.01745329251994329577f;
    return { std::cos (innerDegrees * degreesToRadians), std::cos (outerDegrees * degreesToRadians) };
}

glm::mat4 CLight::calculateSpotShadowViewProjection (
    const glm::vec3& origin, const glm::vec3& direction, const float outerDegrees, const float radius
) {
    const float directionLength = glm::length (direction);
    const glm::vec3 forward = directionLength > 1e-6f ? direction / directionLength : glm::vec3 (1.0f, 0.0f, 0.0f);
    // lookAt becomes singular when forward and up are parallel. Switch axes for
    // near-vertical authored lights while retaining a stable orientation otherwise.
    const glm::vec3 up = std::abs (glm::dot (forward, glm::vec3 (0.0f, 1.0f, 0.0f))) > 0.99f
	? glm::vec3 (0.0f, 0.0f, 1.0f)
	: glm::vec3 (0.0f, 1.0f, 0.0f);
    const float farPlane = std::max (std::abs (radius), 1.01f);
    const float nearPlane = std::clamp (farPlane * 0.001f, 0.01f, farPlane * 0.1f);
    const float fieldOfView = std::clamp (std::abs (outerDegrees) * 2.0f, 1.0f, 179.0f);

    return glm::perspective (glm::radians (fieldOfView), 1.0f, nearPlane, farPlane)
	* glm::lookAt (origin, origin + forward, up);
}

glm::mat4 CLight::calculateDirectionalShadowViewProjection (
    const glm::vec3& cameraEye, const glm::vec3& cameraCenter, const glm::vec3& cameraUp,
    const float fieldOfViewDegrees, const float aspectRatio, const float zoom, const float nearDistance,
    const float farDistance, const glm::vec3& lightDirection, const int shadowResolution
) {
    const auto normalizedOr = [] (const glm::vec3& value, const glm::vec3& fallback) {
	const float length = glm::length (value);
	return length > 1e-6f ? value / length : fallback;
    };

    const glm::vec3 cameraForward = normalizedOr (cameraCenter - cameraEye, glm::vec3 (0.0f, 0.0f, -1.0f));
    glm::vec3 cameraRight = glm::cross (cameraForward, cameraUp);
    if (glm::length (cameraRight) <= 1e-6f) {
	const glm::vec3 alternateUp
	    = std::abs (cameraForward.y) > 0.99f ? glm::vec3 (0.0f, 0.0f, 1.0f) : glm::vec3 (0.0f, 1.0f, 0.0f);
	cameraRight = glm::cross (cameraForward, alternateUp);
    }
    cameraRight = glm::normalize (cameraRight);
    const glm::vec3 correctedCameraUp = glm::normalize (glm::cross (cameraRight, cameraForward));

    const float cascadeNear = std::max (nearDistance, 0.0001f);
    const float cascadeFar = std::max (farDistance, cascadeNear + 0.0001f);
    const float safeZoom = std::max (zoom, 0.0001f);
    const float tanHalfFov = std::tan (glm::radians (std::clamp (fieldOfViewDegrees, 1.0f, 179.0f)) * 0.5f) / safeZoom;
    const float safeAspect = std::max (std::abs (aspectRatio), 0.0001f);

    std::array<glm::vec3, 8> corners {};
    int corner = 0;
    for (const float distance : { cascadeNear, cascadeFar }) {
	const glm::vec3 planeCenter = cameraEye + cameraForward * distance;
	const float halfHeight = tanHalfFov * distance;
	const float halfWidth = halfHeight * safeAspect;
	for (const float y : { -1.0f, 1.0f }) {
	    for (const float x : { -1.0f, 1.0f }) {
		corners[corner++] = planeCenter + cameraRight * (x * halfWidth) + correctedCameraUp * (y * halfHeight);
	    }
	}
    }

    glm::vec3 cascadeCenter (0.0f);
    for (const glm::vec3& point : corners) {
	cascadeCenter += point;
    }
    cascadeCenter /= static_cast<float> (corners.size ());

    float radius = 0.0f;
    for (const glm::vec3& point : corners) {
	radius = std::max (radius, glm::length (point - cascadeCenter));
    }
    radius = std::max (radius, 0.001f);

    const glm::vec3 rayDirection = normalizedOr (lightDirection, glm::vec3 (1.0f, 0.0f, 0.0f));
    const glm::vec3 lightUp = std::abs (glm::dot (rayDirection, glm::vec3 (0.0f, 1.0f, 0.0f))) > 0.99f
	? glm::vec3 (0.0f, 0.0f, 1.0f)
	: glm::vec3 (0.0f, 1.0f, 0.0f);
    const glm::vec3 lightEye = cascadeCenter - rayDirection * radius * 2.0f;
    const glm::mat4 lightView = glm::lookAt (lightEye, cascadeCenter, lightUp);

    glm::vec3 minimum (std::numeric_limits<float>::max ());
    glm::vec3 maximum (std::numeric_limits<float>::lowest ());
    for (const glm::vec3& point : corners) {
	const glm::vec3 lightPoint = glm::vec3 (lightView * glm::vec4 (point, 1.0f));
	minimum = glm::min (minimum, lightPoint);
	maximum = glm::max (maximum, lightPoint);
    }

    float halfWidth = std::max ((maximum.x - minimum.x) * 0.5f, 0.0001f);
    float halfHeight = std::max ((maximum.y - minimum.y) * 0.5f, 0.0001f);
    float centerX = (minimum.x + maximum.x) * 0.5f;
    float centerY = (minimum.y + maximum.y) * 0.5f;
    const float resolution = static_cast<float> (std::max (shadowResolution, 1));
    const float texelX = halfWidth * 2.0f / resolution;
    const float texelY = halfHeight * 2.0f / resolution;
    centerX = std::round (centerX / texelX) * texelX;
    centerY = std::round (centerY / texelY) * texelY;
    // A one-texel margin keeps snapped bounds from clipping an extreme corner.
    halfWidth += texelX;
    halfHeight += texelY;

    const float lightNear = std::max (0.0001f, -maximum.z - radius);
    const float lightFar = std::max (lightNear + 0.0001f, -minimum.z + radius);
    glm::mat4 lightProjection = glm::ortho (
	centerX - halfWidth, centerX + halfWidth, centerY - halfHeight, centerY + halfHeight, lightNear, lightFar
    );
    // Snap a fixed world-space origin to the shadow texel grid. Snapping only
    // the light-space bounds is insufficient because lightView follows the
    // camera and would otherwise reintroduce sub-texel projection movement.
    const glm::mat4 unsnapped = lightProjection * lightView;
    const glm::vec4 shadowOrigin = unsnapped * glm::vec4 (0.0f, 0.0f, 0.0f, 1.0f) * (resolution * 0.5f);
    const glm::vec2 roundingOffset
	= (glm::round (glm::vec2 (shadowOrigin)) - glm::vec2 (shadowOrigin)) * (2.0f / resolution);
    lightProjection[3][0] += roundingOffset.x;
    lightProjection[3][1] += roundingOffset.y;
    return lightProjection * lightView;
}

std::array<glm::mat4, 6> CLight::calculatePointShadowViewProjections (const glm::vec3& origin, const float radius) {
    const float farPlane = std::max (std::abs (radius), 1.01f);
    const float nearPlane = std::clamp (farPlane * 0.001f, 0.01f, farPlane * 0.1f);
    const glm::mat4 projection = glm::perspective (glm::half_pi<float> (), 1.0f, nearPlane, farPlane);

    // This is the face order hard-coded by CalculateProjectedCoordsPoint:
    // +X/-X on row 0, +Y/-Y on row 1, and +Z/-Z on row 2.
    static constexpr std::array<glm::vec3, 6> directions = {
	glm::vec3 (1.0f, 0.0f, 0.0f),  glm::vec3 (-1.0f, 0.0f, 0.0f), glm::vec3 (0.0f, 1.0f, 0.0f),
	glm::vec3 (0.0f, -1.0f, 0.0f), glm::vec3 (0.0f, 0.0f, 1.0f),  glm::vec3 (0.0f, 0.0f, -1.0f),
    };
    static constexpr std::array<glm::vec3, 6> up = {
	glm::vec3 (0.0f, 1.0f, 0.0f),  glm::vec3 (0.0f, 1.0f, 0.0f), glm::vec3 (0.0f, 0.0f, 1.0f),
	glm::vec3 (0.0f, 0.0f, -1.0f), glm::vec3 (0.0f, 1.0f, 0.0f), glm::vec3 (0.0f, 1.0f, 0.0f),
    };

    std::array<glm::mat4, 6> result {};
    for (size_t face = 0; face < result.size (); face++) {
	result[face] = projection * glm::lookAt (origin, origin + directions[face], up[face]);
    }
    return result;
}

glm::vec4 CLight::calculatePointShadowProjectionInfo (const float radius) {
    const float farPlane = std::max (std::abs (radius), 1.01f);
    const float nearPlane = std::clamp (farPlane * 0.001f, 0.01f, farPlane * 0.1f);
    const glm::mat4 projection = glm::perspective (glm::half_pi<float> (), 1.0f, nearPlane, farPlane);
    return glm::vec4 (projection[2][2], projection[3][2], projection[2][3], projection[3][3]);
}

glm::vec3 CLight::calculateTubeEndPosition (const glm::mat4& worldMatrix, const glm::vec3& controlPoint) {
    return glm::vec3 (worldMatrix * glm::vec4 (controlPoint, 1.0f));
}
