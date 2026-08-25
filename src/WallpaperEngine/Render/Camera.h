#pragma once

#include "WallpaperEngine/Render/Wallpapers/CScene.h"
#include <glm/mat4x4.hpp>
#include <glm/vec2.hpp>
#include <glm/vec3.hpp>

#include "WallpaperEngine/Data/Model/Wallpaper.h"

namespace WallpaperEngine::Render::Wallpapers {
class CScene;
}

namespace WallpaperEngine::Render {
using namespace WallpaperEngine::Data::Model;

class Camera {
public:
    Camera (Wallpapers::CScene& scene, const SceneData::Camera& camera);
    ~Camera ();

    void setOrthogonalProjection (const float width, const float height);
    /** Perspective-camera scenes (general.orthogonalprojection == null): fov-driven
     *  projection + the authored eye/center/up view. width/height only set aspect
     *  and the layout dims. */
    void setPerspectiveProjection (const float width, const float height);
    void setScriptedView (const glm::vec3& eye, const glm::vec3& center);

    [[nodiscard]] const glm::vec3& getCenter () const;
    [[nodiscard]] const glm::vec3& getEye () const;
    [[nodiscard]] const glm::vec3& getUp () const;
    [[nodiscard]] const glm::mat4& getProjection () const;
    [[nodiscard]] const glm::mat4& getScreenProjection () const;
    [[nodiscard]] const glm::mat4& getLookAt () const;
    [[nodiscard]] Wallpapers::CScene& getScene () const;
    [[nodiscard]] bool isOrthogonal () const;
    [[nodiscard]] float getWidth () const;
    [[nodiscard]] float getHeight () const;
    [[nodiscard]] float getFov () const;
    /** general.perspectiveoverridefov for perspective:true objects (0 = unset) */
    [[nodiscard]] float getOverrideFov () const;
    [[nodiscard]] float getNearZ () const;
    [[nodiscard]] float getFarZ () const;

private:
    float m_width;
    float m_height;
    bool m_isOrthogonal = false;
    glm::mat4 m_projection = {};
    bool m_hasScriptedView = false;
    glm::vec3 m_scriptedEye = {};
    glm::vec3 m_scriptedCenter = {};
    glm::mat4 m_screenProjection = glm::mat4 (1.0f);
    glm::mat4 m_lookat = {};
    const SceneData::Camera& m_camera;
    Wallpapers::CScene& m_scene;
};
} // namespace WallpaperEngine::Render
