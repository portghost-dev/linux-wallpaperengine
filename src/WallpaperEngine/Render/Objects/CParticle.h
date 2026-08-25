#pragma once

#include "CRenderable.h"
#include "WallpaperEngine/Data/Model/Object.h"
#include "WallpaperEngine/Render/Objects/Effects/CPass.h"
#include "WallpaperEngine/Render/Wallpapers/CScene.h"
#include "WallpaperEngine/Scripting/ScriptableObject.h"

#include <cstdint>
#include <functional>
#include <glm/mat4x4.hpp>
#include <glm/vec3.hpp>
#include <glm/vec4.hpp>
#include <memory>
#include <random>
#include <vector>

using namespace WallpaperEngine;
using namespace WallpaperEngine::Render;
using namespace WallpaperEngine::Data::Model;

namespace WallpaperEngine::Render::Objects {

constexpr uint32_t DEFAULT_MAX_PARTICLES = 1000;

/**
 * Runtime particle instance state
 */
struct ParticleInstance {
    // Position and movement
    glm::vec3 position { 0.0f };
    glm::vec3 velocity { 0.0f };
    glm::vec3 acceleration { 0.0f };

    // Rotation
    glm::vec3 rotation { 0.0f };
    glm::vec3 angularVelocity { 0.0f };
    glm::vec3 angularAcceleration { 0.0f };

    // Visual properties
    glm::vec3 color { 1.0f };
    float alpha { 1.0f };
    float size { 20.0f };
    float followAlpha { 1.0f };
    float frame { 0.0f }; // Current animation frame

    // Lifetime
    float lifetime { 1.0f }; // Total lifetime in seconds
    float age { 0.0f }; // Current age in seconds

    // Oscillator state (per-particle random values)
    // base is updated by alphafade/sizechange operators so oscillation combines properly
    struct {
	float frequency { 0.0f };
	float scale { 1.0f };
	float phase { 0.0f };
	float base { 1.0f };
	bool initialized { false };
    } oscillateAlpha, oscillateSize;

    struct {
	glm::vec3 frequency { 0.0f };
	glm::vec3 scale { 1.0f };
	glm::vec3 phase { 0.0f };
	glm::vec3 lastOffset { 0.0f };
	bool initialized { false };
    } oscillatePosition;

    // Initial values for resets/multipliers
    struct {
	glm::vec3 color { 1.0f };
	float alpha { 1.0f };
	float size { 20.0f };
	float lifetime { 1.0f };
    } initial;

    uint32_t uid { 0 };
    uint16_t ownerTag { 0 };

    bool alive { false };

    struct TrailNode {
	glm::vec3 position;
	float size;
	glm::vec3 color;
	float alpha;
	double time; // system-time of the sample
    };
    std::vector<TrailNode> trail;

    // Get normalized lifetime position (0.0 to 1.0)
    float getLifetimePos () const { return lifetime > 0.0f ? (age / lifetime) : 1.0f; }

    bool isAlive () const { return alive && age < lifetime; }
};

/**
 * Control point runtime data
 */
struct ControlPointData {
    glm::vec3 position { 0.0f };
    glm::vec3 offset { 0.0f };
    bool linkMouse { false };
    bool worldSpace { false };
};

/**
 * Context for one emitter invocation. Root systems use the defaults; child systems emit
 * at event anchors (parent-local space) with per-instance tags and pool budgets.
 */
struct EmitContext {
    /** Added to the emitter's spawn origin (event position / follow anchor) */
    glm::vec3 anchor { 0.0f };
    /** ownerTag stamped on emitted particles (0 = root emission) */
    uint16_t tag { 0 };
    /** Max particles this call may add (per-instance pool cap) */
    int32_t budget { INT32_MAX };
    bool burst { false };
};

/**
 * Particle emitter function
 */
using EmitterFunc = std::function<void (std::vector<ParticleInstance>&, uint32_t&, float, const EmitContext&)>;

/**
 * Particle initializer function
 */
using InitializerFunc = std::function<void (ParticleInstance&)>;

/**
 * Particle operator function
 */
using OperatorFunc = std::function<
    void (std::vector<ParticleInstance>&, uint32_t, const std::vector<ControlPointData>&, float, float)>;

class CParticle final : public CRenderable, public Scripting::ScriptableObject {
    friend CObject;

public:
    CParticle (Wallpapers::CScene& scene, const Particle& particle);
    /** Child-system constructor: `link` describes the parent link (type/maxcount/
     *  probability/offsets), `parentSystem` drives this system's clock and events */
    CParticle (Wallpapers::CScene& scene, const Particle& particle, CParticle* parentSystem, const ParticleChild* link);
    ~CParticle ();

    void setup () override;
    void render () override;
    void update (float dt);

    [[nodiscard]] bool isChildSystem () const { return m_parentSystem != nullptr; }

    void resetPopulation ();

    [[nodiscard]] const Particle& getParticle () const;

    [[nodiscard]] const float& getBrightness () const override;
    [[nodiscard]] const float& getUserAlpha () const override;
    [[nodiscard]] const float& getAlpha () const override;
    [[nodiscard]] const glm::vec3& getColor () const override;
    [[nodiscard]] glm::vec4 getColor4 () const override;
    [[nodiscard]] const glm::vec3& getCompositeColor () const override;

protected:
    void setupEmitters ();
    void setupInitializers ();
    void setupOperators ();

    // Emitter creators
    EmitterFunc createBoxEmitter (const ParticleEmitter& emitter);
    EmitterFunc createSphereEmitter (const ParticleEmitter& emitter);

    // Initializer creators
    InitializerFunc createColorRandomInitializer (const ColorRandomInitializer& init, bool multiplyInto);
    InitializerFunc
    createPkgDualColorRandomInitializer (const ColorRandomInitializer& first, const ColorRandomInitializer& second);
    InitializerFunc createSizeRandomInitializer (const SizeRandomInitializer& init);
    InitializerFunc createAlphaRandomInitializer (const AlphaRandomInitializer& init);
    InitializerFunc createLifetimeRandomInitializer (const LifetimeRandomInitializer& init);
    InitializerFunc createVelocityRandomInitializer (const VelocityRandomInitializer& init);
    InitializerFunc createRotationRandomInitializer (const RotationRandomInitializer& init);
    InitializerFunc createAngularVelocityRandomInitializer (const AngularVelocityRandomInitializer& init);
    InitializerFunc createTurbulentVelocityRandomInitializer (const TurbulentVelocityRandomInitializer& init);
    InitializerFunc
    createMapSequenceAroundControlPointInitializer (const MapSequenceAroundControlPointInitializer& init);

    // Operator creators
    OperatorFunc createMovementOperator (const MovementOperator& op);
    OperatorFunc createAngularMovementOperator (const AngularMovementOperator& op);
    OperatorFunc createAlphaFadeOperator (const AlphaFadeOperator& op);
    OperatorFunc createSizeChangeOperator (const SizeChangeOperator& op);
    OperatorFunc createAlphaChangeOperator (const AlphaChangeOperator& op);
    OperatorFunc createColorChangeOperator (const ColorChangeOperator& op);
    OperatorFunc createTurbulenceOperator (const TurbulenceOperator& op);
    OperatorFunc createVortexOperator (const VortexOperator& op);
    OperatorFunc createControlPointAttractOperator (const ControlPointAttractOperator& op);
    OperatorFunc createOscillateAlphaOperator (const OscillateAlphaOperator& op);
    OperatorFunc createOscillateSizeOperator (const OscillateSizeOperator& op);
    OperatorFunc createOscillatePositionOperator (const OscillatePositionOperator& op);

    // Rendering
    void renderSprites ();
    void renderRope ();
    void setupPass ();
    void setupGeometryCallbacks ();
    void setupParticleUniforms ();
    void updateMatrices ();
    void applyParallaxToModelMatrix (glm::mat4& matrix) const;
    void updateParticleViewProjection ();
    void updateParticleRenderVars ();

    /** One live child-system instance: a one-shot event burst or a follow attachment.
     *  Particles are tagged with `tag`; the instance is reaped when its particles die. */
    struct ChildEventInstance {
	uint16_t tag { 0 };
	/** eventfollow: uid of the followed parent particle (0 for bursts) */
	uint32_t parentUid { 0 };
	/** eventfollow: parent particle still alive (bursts are born done) */
	bool emitting { false };
	uint32_t liveCount { 0 };
	/** eventfollow: per-instance emitter copies (closure timers become instance state) */
	std::vector<EmitterFunc> emitters;
	glm::vec3 lastAnchor { 0.0f };
	bool hasAnchor { false };
    };

    /** A parent-particle lifecycle event visible to child systems */
    struct ParticleEvent {
	glm::vec3 position;
	uint32_t uid;
    };

    void setupChildren ();
    /** Shared transform/control-point upkeep extracted from update() (adds parent-CP
     *  mirroring for child systems) */
    void updateTransformAndControlPoints ();
    /** Emission stage for child systems: static run, event bursts, follow attachments */
    void emitAsChild (float dt);
    /** Age/operators/animation/compaction/stats shared by root and child systems */
    void simCommon (float dt);
    void updateChildren (float dt);
    /** Draw path for child systems, called by the parent after its own draw setup */
    void renderAsChild ();
    void spawnBurstInstance (const glm::vec3& anchor, float dt);
    void recordSpawnRange (uint32_t from);
    void applyLinkScale (uint32_t from);
    [[nodiscard]] const ParticleInstance* findParticleByUid (uint32_t uid) const;
    [[nodiscard]] glm::vec3 linkAnchorOffset () const;
    [[nodiscard]] bool rollProbability ();
    [[nodiscard]] const CParticle* rootSystem () const;
    [[nodiscard]] float worldSizeDivisor () const;

private:
    const Particle& m_particle;

    std::vector<ParticleInstance> m_particles;
    uint32_t m_particleCount { 0 };
    uint32_t m_maxParticles { DEFAULT_MAX_PARTICLES };

    std::vector<EmitterFunc> m_emitters;
    std::vector<InitializerFunc> m_initializers;
    std::vector<OperatorFunc> m_operators;

    std::vector<ControlPointData> m_controlPoints;

    std::vector<float> m_vertices;
    std::vector<uint32_t> m_indices;

    double m_time { 0.0 };
    double m_sysTime { 0.0 };
    double m_startWall { 0.0 };

    double m_statWindowStart { 0.0 };
    uint64_t m_statEmitted { 0 };
    uint64_t m_statDied { 0 };
    uint64_t m_statFrames { 0 };
    uint32_t m_statPeakLive { 0 };
    // Instrument epoch these accumulators were gathered under. LWE_PARTSTATS is runtime
    // settable, and the counters above advance only while it is on - so without this, the
    // first line after a re-enable divides counts gathered during THIS window by a window
    // start left over from the previous one, and under-reports the rate. See
    // Logging/InstrumentRegistry.h.
    std::uint32_t m_statEpoch { 0 };

    // CPass-based rendering
    Effects::CPass* m_pass { nullptr };
    std::unique_ptr<ImageEffectPassOverride> m_passOverride;
    std::shared_ptr<FBOProvider> m_passFBOProvider;
    TextureMap m_passBinds;
    GLsizei m_activeIndexCount { 0 };

    // REFRACT support: copy of scene FBO to avoid read-write conflict
    bool m_hasRefract { false };
    std::shared_ptr<CFBO> m_refractFBO;

    // OpenGL buffers
    GLuint m_vao { 0 };
    GLuint m_vbo { 0 };
    GLuint m_ebo { 0 };
    GLint m_prevVAO { 0 };
    GLsizeiptr m_vboCapacity { 0 };
    GLsizeiptr m_eboCapacity { 0 };

    // Particle-specific uniform data (stored here, pointed to by CPass)
    glm::mat4 m_modelMatrix { 1.0f };
    glm::mat4 m_modelMatrixInverse { 1.0f };
    glm::mat4 m_mvpMatrix { 1.0f };
    glm::mat4 m_mvpMatrixInverse { 1.0f };
    glm::mat4 m_viewProjectionMatrix { 1.0f };
    glm::vec3 m_orientationUp { 0.0f, 1.0f, 0.0f };
    glm::vec3 m_orientationRight { 1.0f, 0.0f, 0.0f };
    glm::vec3 m_orientationForward { 0.0f, 0.0f, 1.0f };
    glm::vec3 m_viewUp { 0.0f, 1.0f, 0.0f };
    glm::vec3 m_viewRight { 1.0f, 0.0f, 0.0f };
    glm::vec3 m_eyePosition { 0.0f, 0.0f, 1000.0f };
    glm::vec4 m_renderVar0 { 0.0f };
    glm::vec4 m_renderVar1 { 0.0f };
    float m_dtReal { 0.0f };
    bool m_prewarmDone { false };
    glm::vec3 m_axisComp { 1.0f, 1.0f, 1.0f };

    // Spritesheet animation data
    int m_spritesheetCols { 0 };
    int m_spritesheetRows { 0 };
    int m_spritesheetFrames { 0 };
    float m_spritesheetDuration { 1.0f };

    // Material shader constants
    float m_overbright { 1.0f };
    float m_refractAmount { 0.05f }; // Default from shader annotation

    // Renderer configuration
    bool m_useTrailRenderer { false };
    float m_trailLength { 0.05f };
    float m_trailMaxLength { 10.0f };
    float m_trailMinLength { 0.0f };
    // Rope renderer (rope + ropetrail both use genericropeparticle shader)
    bool m_useRopeRenderer { false };
    int m_ropeSubdivision { 4 }; // Catmull-Rom subdivisions between points (smoothing)
    int m_ropeSegments { 4 }; // ropetrail: historical position snapshots per particle
    float m_ropeUVScale { 1.0f };
    bool m_ropeUVScrolling { false };
    bool m_ropeUVSmoothing { true }; // rope only
    bool m_uniformLifetimes { false }; // true when lifetime min==max (enables UV smoothing)

    // Per-vertex float counts for different renderer types
    static constexpr int SPRITE_FLOATS_PER_VERTEX = 17;
    static constexpr int ROPE_FLOATS_PER_VERTEX = 26;

    // Transformed origin (screen space to centered space conversion)
    glm::vec3 m_transformedOrigin { 0.0f };

    // Last known resolution for detecting changes
    float m_lastScreenWidth { 0.0f };
    float m_lastScreenHeight { 0.0f };

    // Random number generator
    std::mt19937 m_rng;

    bool m_initialized { false };

    /** Non-null when this system is a child driven by a parent CParticle */
    CParticle* m_parentSystem { nullptr };

    float m_inhSize { 1.0f };
    float m_inhAlpha { 1.0f };
    float m_inhLifetime { 1.0f };
    float m_inhSpeed { 1.0f };
    glm::vec3 m_inhColorN { 1.0f };
    bool m_inhColorNAuthored { false };
    /** The parent link that instantiated this child (lifetime: owned by parent's Particle data) */
    const ParticleChild* m_link { nullptr };
    std::vector<std::unique_ptr<CParticle>> m_children;
    std::vector<ChildEventInstance> m_eventInstances;
    /** Monotonic; uint16 wrap is safe because instances live seconds, not sessions */
    uint16_t m_nextTag { 1 };
    uint32_t m_nextUid { 1 };
    /** Static children gate their own starttime from the first driven frame */
    double m_childStartWall { -1.0 };
    std::vector<ParticleEvent> m_frameSpawns;
    std::vector<ParticleEvent> m_frameDeaths;
};
} // namespace WallpaperEngine::Render::Objects
