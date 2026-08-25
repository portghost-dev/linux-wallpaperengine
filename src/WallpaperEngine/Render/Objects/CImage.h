#pragma once

#include "CRenderable.h"
#include "PuppetModel.h"
#include "WallpaperEngine/Render/CObject.h"
#include "WallpaperEngine/Render/Objects/Effects/CPass.h"
#include "WallpaperEngine/Render/Wallpapers/CScene.h"

#include "WallpaperEngine/Render/Shaders/Shader.h"

#include "../TextureProvider.h"
#include "WallpaperEngine/Scripting/ScriptableObject.h"

#include <glm/vec3.hpp>
#include <optional>
#include <vector>

using namespace WallpaperEngine;
using namespace WallpaperEngine::Render;
using namespace WallpaperEngine::Scripting;
namespace WallpaperEngine::Render::Objects::Effects {
class CMaterial;
class CPass;
} // namespace WallpaperEngine::Render::Objects::Effects

namespace WallpaperEngine::Render::Objects {
class CImage final : public CRenderable, public ScriptableObject {
    friend CObject;

public:
    CImage (Wallpapers::CScene& scene, const Image& image);
    ~CImage () override;

    void setup () override;
    void render () override;

    [[nodiscard]] const Image& getImage () const;

    [[nodiscard]] std::optional<glm::vec3> cursorLocalPosition (const glm::vec3& worldPosition) const;
    [[nodiscard]] glm::vec2 getSize () const;

    [[nodiscard]] GLuint getSceneSpacePosition () const;
    [[nodiscard]] GLuint getCopySpacePosition () const;
    [[nodiscard]] GLuint getPassSpacePosition () const;
    [[nodiscard]] GLuint getTexCoordCopy () const;
    [[nodiscard]] GLuint getTexCoordPass () const;

    [[nodiscard]] const float& getBrightness () const override;
    [[nodiscard]] const float& getUserAlpha () const override;
    [[nodiscard]] const float& getAlpha () const override;
    [[nodiscard]] const glm::vec3& getColor () const override;
    [[nodiscard]] glm::vec4 getColor4 () const override;
    [[nodiscard]] const glm::vec3& getCompositeColor () const override;

    /**
     * Performs a ping-pong on the available framebuffers to be able to continue rendering things to them
     *
     * @param drawTo The framebuffer to use
     * @param asInput The last texture used as output (if needed)
     */
    void pinpongFramebuffer (std::shared_ptr<const CFBO>* drawTo, std::shared_ptr<const TextureProvider>* asInput);

    [[nodiscard]] bool isCompositionLayer () const;
    [[nodiscard]] bool copiesCompositionBackground () const;
    [[nodiscard]] std::shared_ptr<const CFBO> getCompositionFBO () const;

protected:
    void setupPasses ();

    void updateScreenSpacePosition ();
    [[nodiscard]] glm::mat4 buildScreenViewProjection () const;

    struct ResolvedTransform {
	glm::vec3 origin;
	glm::vec3 scale;
	float angle;
    };

    [[nodiscard]] ResolvedTransform resolveTransform (const WallpaperEngine::Data::Model::Object& object) const;

    [[nodiscard]] glm::vec3 toClassicLightSpace (const glm::vec3& litSpacePos) const override;
    [[nodiscard]] glm::vec3 toClassicLightSpaceLocal (const glm::vec3& litSpacePos) const override;
    [[nodiscard]] float classicLocalRadianceScale () const override;

    /**
     * Computes the object's own transform (origin/scale/angle) without walking the
     * parent chain. Used as the per-node step of resolveTransform.
     */
    [[nodiscard]] static ResolvedTransform localTransform (const WallpaperEngine::Data::Model::Object& object);

private:
    bool loadPuppetMesh (const glm::vec2& size);
    void updatePuppetPositionBuffer (const glm::vec2& size);
    void setupPuppetGeometryCallback (Effects::CPass* pass) const;
    void uploadPuppetPositions (const std::vector<GLfloat>& raw, const glm::vec2& size);
    void updatePuppetAnimation ();
    ResolvedTransform updateGeometryBuffers ();
    void applyShapeGeometry (const ResolvedTransform& transform);
    [[nodiscard]] glm::vec2 resolveGeometrySize (float sceneWidth, float sceneHeight, glm::vec3& origin) const;
    void updateScenePosition (
	const glm::vec3& origin, const glm::vec2& size, const glm::vec3& scale, float sceneWidth, float sceneHeight
    );
    void uploadGeometryBuffers (const glm::vec2& size);
    [[nodiscard]] bool shouldRenderFinalPass (bool isLastPass) const;
    bool configurePassTarget (
	Effects::CPass* pass, std::shared_ptr<const CFBO>& drawTo,
	const std::shared_ptr<const TextureProvider>& asInput, std::shared_ptr<const TextureProvider>& effectInput,
	bool& inTargetEffectSequence
    );

    GLuint m_sceneSpacePosition;
    GLuint m_copySpacePosition;
    GLuint m_passSpacePosition;
    GLuint m_texcoordCopy;
    GLuint m_texcoordPass;
    GLuint m_puppetSpacePosition = GL_NONE;
    GLuint m_puppetTexCoord = GL_NONE;
    GLuint m_puppetIndices = GL_NONE;
    GLsizei m_puppetIndexCount = 0;
    bool m_hasPuppetMesh = false;
    /** Effectless puppet: the first pass IS the screen pass, so puppet verts must be
     *  uploaded in centered-world quad space (m_pos) instead of texture-local space */
    bool m_puppetScreenSpace = false;
    bool m_isShape = false;
    std::vector<GLfloat> m_puppetRawPositions = {};

    std::optional<PuppetModel> m_puppetModel = std::nullopt;
    struct PuppetLayerBinding {
	const PuppetModel::Clip* clip;
	const ImageAnimationLayer* layer;
    };
    std::vector<PuppetLayerBinding> m_puppetLayers = {};
    std::vector<PuppetModel::ActiveLayer> m_puppetActiveScratch = {};
    std::vector<glm::mat4> m_puppetSkinMatrices = {};
    std::vector<glm::vec3> m_puppetSkinnedPositions = {};
    std::vector<GLfloat> m_puppetSkinnedFlat = {};

    glm::mat4 m_modelViewProjectionScreen = {};
    glm::mat4 m_modelViewProjectionPass = {};
    glm::mat4 m_modelViewProjectionCopy = {};
    glm::mat4 m_lweScreenVPComposite = {};
    glm::mat4 m_lweMPosFromWorld = glm::mat4 (1.0f);
    glm::mat4 m_modelViewProjectionScreenInverse = {};
    glm::mat4 m_modelViewProjectionPassInverse = {};
    glm::mat4 m_modelViewProjectionCopyInverse = {};

    glm::mat4 m_modelMatrix = {};
    glm::mat4 m_viewProjectionMatrix = {};

    std::shared_ptr<const CFBO> m_mainFBO = nullptr;
    std::shared_ptr<const CFBO> m_subFBO = nullptr;
    std::shared_ptr<const CFBO> m_currentMainFBO = nullptr;
    std::shared_ptr<const CFBO> m_currentSubFBO = nullptr;

    const Image& m_image;

    std::vector<Effects::CPass*> m_passes = {};
    std::shared_ptr<const CFBO> m_compositionFBO = nullptr;
    std::vector<MaterialPassUniquePtr> m_virtualPassess = {};

    glm::vec4 m_pos = {};
    glm::vec3 m_sceneCenter = {};
    glm::vec2 m_size = {};

    bool m_initialized = false;

    struct {
	struct {
	    MaterialUniquePtr material;
	    ImageEffectPassOverrideUniquePtr override;
	} colorBlending;
	std::vector<MaterialUniquePtr> compatibilityMaterials = {};
	std::vector<ImageEffectPassOverrideUniquePtr> compatibilityOverrides = {};
    } m_materials;
};
} // namespace WallpaperEngine::Render::Objects
