#pragma once

#include <set>
#include <unordered_set>

#include "WallpaperEngine/Render/Camera.h"

#include "WallpaperEngine/Render/CWallpaper.h"
#include "WallpaperEngine/Scripting/ScriptEngine.h"

namespace WallpaperEngine::Render {
class Camera;
class CObject;
}

namespace WallpaperEngine::Render::Wallpapers {
using namespace WallpaperEngine::Data::Model;

class CScene final : public CWallpaper {
public:
    CScene (
	const Wallpaper& wallpaper, RenderContext& context, AudioContext& audioContext,
	const WallpaperState::TextureUVsScaling& scalingMode, const uint32_t& clampMode
    );

    ~CScene () override;

    [[nodiscard]] Scripting::ScriptEngine& getScriptEngine () const;
    [[nodiscard]] Camera& getCamera () const;

    [[nodiscard]] const Scene& getScene () const;

    [[nodiscard]] std::shared_ptr<const CFBO> getActiveRenderTarget () const;
    [[nodiscard]] std::shared_ptr<const CFBO> resolveRenderTarget (const std::shared_ptr<const CFBO>& requested) const;
    [[nodiscard]] bool isRenderingToComposition () const;
    [[nodiscard]] bool hasAuthoredChildren (int parentId) const;

    void queueAnimation (Data::Model::DynamicValue& value, CObject& object);
    void forgetObjectAnimations (const CObject& object);

    [[nodiscard]] int getWidth () const override;
    [[nodiscard]] int getHeight () const override;

    // LWE_SSFACTOR resolution clamp (S1): returns `size` scaled down (aspect-preserving) so it
    // never exceeds the output-derived cap = output dims * LWE_SSFACTOR (default 1.0). SSFACTOR=0
    // disables the clamp (exact legacy behavior). Used for scene RTs and oversized layer/effect FBOs.
    [[nodiscard]] glm::vec2 clampToCap (glm::vec2 size) const override;

    /**
     * Dimensions of the LARGEST SINGLE OUTPUT. Output::getFullWidth/getFullHeight give
     * the multi-output SPAN instead, so anything sized per-screen must come through here.
     */
    [[nodiscard]] glm::ivec2 largestOutputSize () const;
    [[nodiscard]] bool isCompositeShared (int id) const { return m_sharedCompositeIds.contains (id); }

    [[nodiscard]] std::pair<std::shared_ptr<CFBO>, std::shared_ptr<CFBO>>
    leaseCompositePair (int id, glm::vec2 size, uint32_t flags, TextureFormat format);
    /** true when this scene runs the HDR bloom ladder (bloom && hdr): layer
     *  composites must be FLOAT so pre-clamp >1 brightness reaches the prefilter */
    [[nodiscard]] bool isHdrBloom () const { return m_hdrBloom; }

    // Time accessors used by dynamic text layers (CText + ScriptEngine).
    // Read from the application-wide g_Time/g_TimeLast globals that other
    // renderers already consume via extern (e.g. CParticle).
    [[nodiscard]] float getTime () const;
    [[nodiscard]] float getDeltaTime () const;
    [[nodiscard]] float getFps () const;

    const glm::vec2* getMousePosition () const;
    const glm::vec2* getMousePositionLast () const;
    const glm::vec2* getMousePositionNormalized () const;
    const glm::vec2* getParallaxDisplacement () const;
    const glm::vec2* getParallaxPosition () const;

    [[nodiscard]] const std::vector<CObject*>& getObjectsByRenderOrder () const;
    [[nodiscard]] const CObject* getObject (int id) const;

    static constexpr int MAX_LIGHTS = 4; // per-type capacity of the generated lighting module
    struct SceneLight {
	enum class Type { Point, Spot, Directional, Tube };
	Type type = Type::Point;
	glm::vec3 position {}; // centered GL world space (same flip as objects); unused for directionals
	glm::vec3 direction {}; // unit vector from the calibrated rotation pipeline (see updateLights)
	glm::vec3 color {}; // authored color premultiplied by intensity
	float radius = 0.0f; // authored falloff radius (scene units)
	float exponent = 0.0f; // authored falloff exponent
	float innerConeDeg = 0.0f; // spots only: authored cone angles, degrees
	float outerConeDeg = 0.0f;
	int lightId = -1;
	glm::vec3 tubeEnd {};
	bool castShadow = false;
	glm::vec3 cascadeDistances {}; // directionals: nested cascade far distances
	int spotShadowFeature = -1; // atlas feature index, -1 = unshadowed
	glm::ivec3 dirShadowFeatures { -1 }; // three cascade feature indices
	int pointShadowSlot = -1; // 2x3 atlas block slot
    };

    [[nodiscard]] const std::vector<SceneLight>& getLights () const { return m_lightState; }

    static constexpr int MAX_SHADOW_FEATURES = 16;
    static constexpr int SHADOW_ATLAS_SIZE = 2048;
    static constexpr int SHADOW_ATLAS_GUARD = 2;
    struct ShadowStage {
	int featureCount = 0; // features assigned this scene (fixed at load)
	int viewCount = 0; // total atlas cells incl. six per point block
	std::array<glm::mat4, MAX_SHADOW_FEATURES> matrices {};
	std::array<glm::vec4, MAX_SHADOW_FEATURES> transforms {}; // atlas cell xywh in [0,1]
	std::array<float, MAX_SHADOW_FEATURES> enabled {};
	std::array<glm::ivec4, MAX_SHADOW_FEATURES> viewports {}; // pixel cells (render side)
	std::array<glm::vec4, MAX_LIGHTS> pointProjections {};
	std::array<glm::vec4, MAX_LIGHTS> pointTransforms {}; // 2x3 block xywh in [0,1]
	std::array<float, MAX_LIGHTS> pointEnabled {};
	std::array<std::array<glm::mat4, 6>, MAX_LIGHTS> pointMatrices {};
	std::array<std::array<glm::ivec4, 6>, MAX_LIGHTS> pointViewports {};
    };

    [[nodiscard]] const ShadowStage& getShadowStage () const { return m_shadowStage; }
    [[nodiscard]] const std::shared_ptr<const CFBO>& getShadowAtlas () const { return _rt_shadowAtlas; }

    struct SceneFog {
	bool distanceEnabled = false;
	bool heightEnabled = false;
	glm::vec4 distanceParams = {};
	glm::vec4 heightParams = {};
    };

    [[nodiscard]] const SceneFog& getFog () const { return m_fog; }

protected:
    void renderFrame (const glm::ivec4& viewport) override;
    void updateMouse (const glm::ivec4& viewport);

    friend class CWallpaper;

private:
    Render::CObject* createObject (const Object& object);
    Render::CObject* dispatchObjectType (const Object& object);
    /** Recomputes m_lightState from the scene's light objects (colors/intensity can be dynamic) */
    void updateLights ();
    void addObjectToRenderOrder (const Object& object);
    void collectSharedComposites (const Scene& scene);
    void reportPoolHighWater () const;

    std::unique_ptr<Scripting::ScriptEngine> m_scriptEngine;
    std::unique_ptr<Camera> m_camera;
    int m_canvasWidth = 0;
    int m_canvasHeight = 0;
    ObjectUniquePtr m_bloomObjectData;
    CObject* m_bloomObject = nullptr;
    bool m_hdrBloom = false;
    std::map<int, CObject*> m_objects = {};
    std::unordered_set<int> m_objectsBeingResolved = {};
    std::shared_ptr<const CFBO> m_compositionRenderTarget = nullptr;
    void tickAnimations ();
    struct AnimatedPropertyEntry {
	Data::Model::DynamicValue* value = nullptr;
	CObject* object = nullptr;
	double elapsedTime = 0.0;
	bool completed = false;
    };
    std::vector<AnimatedPropertyEntry> m_animatedProperties = {};
    std::vector<CObject*> m_objectsByRenderOrder = {};
    std::vector<DynamicValue*> m_scriptedValues = {};
    glm::vec2 m_mousePosition = {};
    glm::vec2 m_mousePositionLast = {};
    glm::vec2 m_mousePositionNormalized = {};
    glm::vec2 m_parallaxDisplacement = {};
    // g_ParallaxPosition semantics: 0.5,0.5 = at-rest center (shader does pos*2-1);
    // an unbound uniform reads 0,0 = pointer pinned to a corner at full amplitude
    glm::vec2 m_parallaxPosition = { 0.5f, 0.5f };
    std::shared_ptr<const CFBO> _rt_4FrameBuffer = nullptr;
    std::shared_ptr<const CFBO> _rt_8FrameBuffer = nullptr;
    std::shared_ptr<const CFBO> _rt_Bloom = nullptr;
    std::shared_ptr<const CFBO> _rt_shadowAtlas = nullptr;
    std::set<int> m_sharedCompositeIds = {};
    std::map<std::string, std::pair<std::shared_ptr<CFBO>, std::shared_ptr<CFBO>>> m_compositePool = {};
    std::map<std::string, int> m_poolLeaseCount = {};
    int m_poolDedicatedCount = 0;
    int m_clearProbeCount = 0;
    std::vector<const Light*> m_lights = {};
    void setupShadowStage ();
    void updateFogState ();
    static glm::vec4 calculateFogParams (float start, float end, float startDensity, float endDensity);
    void stageShadowMatrices ();
    void renderShadowAtlas ();
    bool m_shadowStageBuilt = false;
    ShadowStage m_shadowStage {};
    SceneFog m_fog {};
    std::map<int, int> m_spotFeatureByLight = {};
    std::map<int, glm::ivec3> m_dirFeaturesByLight = {};
    std::map<int, int> m_pointSlotByLight = {};
    std::vector<SceneLight> m_lightState = {};
};
} // namespace WallpaperEngine::Render::Wallpaper
