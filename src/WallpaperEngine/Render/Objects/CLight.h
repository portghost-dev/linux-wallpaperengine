#pragma once

#include "WallpaperEngine/Data/Model/Object.h"
#include "WallpaperEngine/Render/Wallpapers/CScene.h"
#include "WallpaperEngine/Scripting/ScriptableObject.h"

#include <array>

using namespace WallpaperEngine;
using namespace WallpaperEngine::Render;
using namespace WallpaperEngine::Data::Model;

namespace WallpaperEngine::Render::Objects {
class CLight final : public Scripting::ScriptableObject {
public:
    CLight (Wallpapers::CScene& scene, const Light& light);
    ~CLight () override = default;

    void render () override;

    /** native spot uniform packing: cos(inner degrees), cos(outer degrees) */
    [[nodiscard]] static glm::vec2 calculateSpotConeCosines (float innerDegrees, float outerDegrees);
    [[nodiscard]] static glm::mat4 calculateSpotShadowViewProjection (
	const glm::vec3& origin, const glm::vec3& direction, float outerDegrees, float radius
    );
    [[nodiscard]] static glm::mat4 calculateDirectionalShadowViewProjection (
	const glm::vec3& cameraEye, const glm::vec3& cameraCenter, const glm::vec3& cameraUp, float fieldOfViewDegrees,
	float aspectRatio, float zoom, float nearDistance, float farDistance, const glm::vec3& lightDirection,
	int shadowResolution
    );
    /** six +X/-X/+Y/-Y/+Z/-Z view-projections for the native 2x3 point atlas block */
    [[nodiscard]] static std::array<glm::mat4, 6>
    calculatePointShadowViewProjections (const glm::vec3& origin, float radius);
    [[nodiscard]] static glm::vec4 calculatePointShadowProjectionInfo (float radius);
    [[nodiscard]] static glm::vec3
    calculateTubeEndPosition (const glm::mat4& worldMatrix, const glm::vec3& controlPoint);
};
} // namespace WallpaperEngine::Render::Objects
