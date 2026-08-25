#pragma once

#include "CRenderable.h"
#include "WallpaperEngine/Data/Model/Object.h"
#include "WallpaperEngine/Render/Objects/Effects/CPass.h"
#include "WallpaperEngine/Render/Wallpapers/CScene.h"
#include "WallpaperEngine/Scripting/ScriptableObject.h"

#include <glm/mat3x3.hpp>
#include <glm/mat4x4.hpp>

using namespace WallpaperEngine;
using namespace WallpaperEngine::Render;
using namespace WallpaperEngine::Data::Model;

namespace WallpaperEngine::Render::Objects {
class CModel final : public CRenderable, public Scripting::ScriptableObject {
    friend CObject;

public:
    CModel (Wallpapers::CScene& scene, const ModelObject& model);
    ~CModel () override;

    void setup () override;
    void render () override;
    void renderShadow (const glm::mat4& lightViewProjection);

    [[nodiscard]] const float& getBrightness () const override;
    [[nodiscard]] const float& getUserAlpha () const override;
    [[nodiscard]] const float& getAlpha () const override;
    [[nodiscard]] const glm::vec3& getColor () const override;
    [[nodiscard]] glm::vec4 getColor4 () const override;
    [[nodiscard]] const glm::vec3& getCompositeColor () const override;

private:
    struct Submesh {
	GLuint vao = GL_NONE;
	GLuint vbo = GL_NONE;
	GLuint ebo = GL_NONE;
	GLint prevVAO = 0;
	GLsizei indexCount = 0;
	GLsizei stride = 48;
	GLuint uvOffset = 40;
	const Material* material = nullptr;
	Effects::CPass* pass = nullptr;
	std::unique_ptr<ImageEffectPassOverride> passOverride;
	std::shared_ptr<FBOProvider> fboProvider;
    };

    /** Walks every MDLV submesh record (48-byte vertices: pos3+normal3+tangent4+uv2, u16 indices) */
    bool loadMesh ();
    void setupPass (Submesh& submesh);
    void updateMatrices ();
    [[nodiscard]] glm::vec3 effectiveAngles () const;

    const ModelObject& m_model;

    std::vector<Submesh> m_submeshes;

    glm::mat4 m_modelMatrix = glm::mat4 (1.0f);
    GLuint m_shadowProgram = GL_NONE;
    GLuint m_shadowVao = GL_NONE;
    GLint m_shadowLightViewProjection = -1;
    GLint m_shadowModel = -1;
    glm::mat4 m_viewProjectionMatrix = glm::mat4 (1.0f);
    glm::mat4 m_mvpMatrix = glm::mat4 (1.0f);
    glm::mat4 m_mvpMatrixInverse = glm::mat4 (1.0f);
    glm::mat3 m_normalMatrix = glm::mat3 (1.0f);
    glm::vec3 m_eyePosition = glm::vec3 (0.0f);

    float m_brightness = 1.0f;
    bool m_initialized = false;
};
} // namespace WallpaperEngine::Render::Objects
