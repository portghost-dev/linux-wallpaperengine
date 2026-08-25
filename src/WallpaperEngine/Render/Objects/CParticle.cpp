#include "CParticle.h"

#include "WallpaperEngine/Logging/InstrumentRegistry.h"

#include "WallpaperEngine/Data/Model/Property.h"
#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/Maths.h"
#include "WallpaperEngine/Render/Utils/NoiseUtils.h"

#include <GL/glew.h>
#include <algorithm>
#include <cmath>
#include <glm/gtc/constants.hpp>
#include <glm/gtc/matrix_transform.hpp>

extern float g_Time;

using namespace WallpaperEngine::Render::Objects;
using namespace WallpaperEngine::Render::Utils;
using namespace WallpaperEngine::Data::Model;

namespace {
struct PartAllocFrameStats {
    double lastFrame { -1.0 };
    uint64_t vboBytes { 0 };
    uint64_t eboBytes { 0 };
    uint32_t calls { 0 };
    uint64_t hiWaterBytes { 0 };
};

bool partAllocEnabled () {
    static const bool enabled = getenv ("LWE_PARTALLOC") != nullptr;
    return enabled;
}

void logParticlePool (
    const std::string& name, uint32_t id, bool rope, uint32_t maxParticles, size_t vertexBytes, size_t indexBytes
) {
    if (!partAllocEnabled ()) {
	return;
    }

    sLog.out (
	"LWE-PARTALLOC-POOL obj=", id, " ", name, " rope=", rope, " maxcount=", maxParticles,
	" poolVBO=", vertexBytes / (1024 * 1024), " MiB poolEBO=", indexBytes / (1024 * 1024), " MiB"
    );
}

void logParticleBuffer (
    const std::string& name, uint32_t id, bool rope, bool vbo, uint64_t bytes, uint32_t live, uint32_t maxParticles
) {
    if (!partAllocEnabled ()) {
	return;
    }

    static PartAllocFrameStats stats;

    if (static_cast<double> (g_Time) != stats.lastFrame) {
	if (stats.lastFrame >= 0.0) {
	    const uint64_t frameBytes = stats.vboBytes + stats.eboBytes;
	    stats.hiWaterBytes = std::max (stats.hiWaterBytes, frameBytes);
	    sLog.out (
		"LWE-PARTALLOC-FRAME t=", stats.lastFrame, " calls=", stats.calls, " vbo=", stats.vboBytes / 1024,
		" KiB ebo=", stats.eboBytes / 1024, " KiB frame=", frameBytes / (1024 * 1024),
		" MiB hiWater=", stats.hiWaterBytes / (1024 * 1024), " MiB"
	    );
	}
	stats.lastFrame = g_Time;
	stats.vboBytes = 0;
	stats.eboBytes = 0;
	stats.calls = 0;
    }

    if (vbo) {
	stats.vboBytes += bytes;
    } else {
	stats.eboBytes += bytes;
    }
    stats.calls++;

    sLog.out (
	"LWE-PARTALLOC obj=", id, " ", name, " rope=", rope, " ", (vbo ? "VBO" : "EBO"), " bytes=", bytes,
	" live=", live, " maxcount=", maxParticles
    );
}

void uploadParticleBuffer (GLenum target, GLsizeiptr& capacity, GLsizeiptr bytes, const void* data) {
    if (bytes > capacity) {
	glBufferData (target, bytes, data, GL_DYNAMIC_DRAW);
	capacity = bytes;
	return;
    }
    if (bytes > 0) {
	glBufferSubData (target, 0, bytes, data);
    }
}
} // namespace

CParticle::CParticle (Wallpapers::CScene& scene, const Particle& particle) :
    CParticle (scene, particle, nullptr, nullptr) { }

CParticle::CParticle (
    Wallpapers::CScene& scene, const Particle& particle, CParticle* parentSystem, const ParticleChild* link
) :
    CObject (scene, particle), CRenderable (scene, particle, *particle.material->material),
    ScriptableObject (scene, particle), m_particle (particle), m_parentSystem (parentSystem), m_link (link) {
    if (m_parentSystem != nullptr) {
	const auto& po = m_parentSystem->m_particle.instanceOverride;
	m_inhSize = m_parentSystem->m_inhSize * po.size->value->getFloat ();
	m_inhAlpha = m_parentSystem->m_inhAlpha * po.alpha->value->getFloat ();
	m_inhLifetime = m_parentSystem->m_inhLifetime * po.lifetime->value->getFloat ();
	m_inhSpeed = m_parentSystem->m_inhSpeed * po.speed->value->getFloat ();
	m_inhColorN = m_parentSystem->m_inhColorN * po.colorn->value->getVec3 ();
	m_inhColorNAuthored = m_parentSystem->m_inhColorNAuthored || po.colornAuthored;
    }

    this->registerProperty ("origin", *particle.origin->value);
    this->registerProperty ("scale", *particle.scale->value);
    this->registerProperty ("angles", *particle.angles->value);
    this->registerProperty ("visible", *particle.visible->value);
    this->registerProperty ("parallaxDepth", *particle.parallaxDepth->value);
    this->registerProperty ("alpha", *particle.instanceOverride.alpha->value);
    this->registerProperty ("size", *particle.instanceOverride.size->value);
    this->registerProperty ("lifetime", *particle.instanceOverride.lifetime->value);
    this->registerProperty ("rate", *particle.instanceOverride.rate->value);
    this->registerProperty ("speed", *particle.instanceOverride.speed->value);
    this->registerProperty ("count", *particle.instanceOverride.count->value);
    this->registerProperty ("color", *particle.instanceOverride.color->value);
    this->registerProperty ("colorn", *particle.instanceOverride.colorn->value);

    this->detectTexture ();
    // Initialize random number generator with time-based seed
    std::random_device rd;
    m_rng.seed (rd ());

    // Read renderer configuration early to determine rendering mode
    if (!m_particle.renderers.empty ()) {
	const auto& renderer = m_particle.renderers[0];
	static const bool s_billboard = getenv ("LWE_BILLBOARD") != nullptr;
	const bool billboardCollapse = s_billboard && renderer.name == "ropetrail" && renderer.length < 0.35f;
	if (billboardCollapse) {
	    m_useTrailRenderer = true;
	    m_trailLength = renderer.length;
	    sLog.out ("particle ropetrail length ", renderer.length, " < 0.35 - billboard collapse (spritetrail path)");
	} else if (renderer.name == "rope" || renderer.name == "ropetrail") {
	    // Both rope and ropetrail use genericropeparticle shader
	    m_useRopeRenderer = true;
	    m_ropeSubdivision = std::max (0, static_cast<int> (renderer.subdivision));
	    m_ropeUVScale = renderer.uvScale;
	    m_ropeUVScrolling = renderer.uvScrolling;
	    m_ropeUVSmoothing = renderer.uvSmoothing;

	    if (renderer.name == "ropetrail") {
		m_useTrailRenderer = true;
		m_trailLength = renderer.length;
		m_ropeSegments = std::max (2, static_cast<int> (renderer.segments));
	    }
	} else if (renderer.name == "spritetrail") {
	    // spritetrail uses genericparticle with TRAILRENDERER combo
	    m_useTrailRenderer = true;
	    m_trailLength = renderer.length;
	    m_trailMaxLength = renderer.maxLength;
	    m_trailMinLength = renderer.minLength;
	}
    }

    m_maxParticles = particle.maxCount;

    if (m_link != nullptr && m_link->type.starts_with ("event")) {
	const uint64_t pool = static_cast<uint64_t> (std::max (1u, particle.maxCount))
	    * static_cast<uint64_t> (std::max (1, m_link->maxCount));
	m_maxParticles = static_cast<uint32_t> (std::min<uint64_t> (pool, 4096));
    }

    m_particles.resize (m_maxParticles);

    // Calculate buffer sizes based on renderer type
    if (m_useRopeRenderer) {
	const int subdivision = std::max (1, m_ropeSubdivision);
	int maxSubSegments;
	if (m_useTrailRenderer) {
	    maxSubSegments = static_cast<int> (m_maxParticles) * std::max (1, m_ropeSegments) * subdivision;
	} else {
	    // Rope: connects N particles with (N-1) segments, each subdivided into sub-segments
	    maxSubSegments = std::max (1, static_cast<int> (m_maxParticles - 1)) * subdivision;
	}
	m_vertices.resize (static_cast<size_t> (maxSubSegments) * 4 * ROPE_FLOATS_PER_VERTEX);
	m_indices.resize (static_cast<size_t> (maxSubSegments) * 6);
    } else {
	// Trail particles: (N+1) * 2 vertices for ribbon strip, N * 6 indices for N quads
	// Normal particles: 4 vertices, 6 indices
	const int verticesPerParticle = 4;
	const int indicesPerParticle = 6;

	m_vertices.resize (m_maxParticles * verticesPerParticle * SPRITE_FLOATS_PER_VERTEX);
	m_indices.resize (m_maxParticles * indicesPerParticle);
    }

    logParticlePool (
	particle.name, getId (), m_useRopeRenderer, m_maxParticles, m_vertices.size () * sizeof (float),
	m_indices.size () * sizeof (uint32_t)
    );
}

CParticle::~CParticle () {
    delete m_pass;

    if (m_vao != 0) {
	glDeleteVertexArrays (1, &m_vao);
    }
    if (m_vbo != 0) {
	glDeleteBuffers (1, &m_vbo);
    }
    if (m_ebo != 0) {
	glDeleteBuffers (1, &m_ebo);
    }

    m_vertices.clear ();
    m_indices.clear ();
}

void CParticle::setup () {
    if (m_initialized) {
	return;
    }

    // Convert origin from screen space to centered space
    // Projection uses ortho(-width/2, width/2, -height/2, height/2)
    // but particle origins are in screen space where (0,0) is top-left
    m_lastScreenWidth = getScene ().getCamera ().getWidth ();
    m_lastScreenHeight = getScene ().getCamera ().getHeight ();

    if (isChildSystem ()) {
	// Child systems live in the parent's local space: the parent's transform is
	// copied wholesale in updateMatrices, anchors arrive via EmitContext
	m_transformedOrigin = glm::vec3 (0.0f);
    } else {
	glm::vec3 origin = m_particle.origin->value->getVec3 ();
	origin.x -= m_lastScreenWidth / 2.0f;
	origin.y = m_lastScreenHeight / 2.0f - origin.y;
	m_transformedOrigin = origin;
    }

    // Load particle material constants
    if (m_particle.material && m_particle.material->material && !m_particle.material->material->passes.empty ()) {
	auto& firstPass = *m_particle.material->material->passes.begin ();

	// Read overbright constant (brightness multiplier for additive particles)
	auto overbrightIt = firstPass->constants.find ("ui_editor_properties_overbright");
	if (overbrightIt != firstPass->constants.end ()) {
	    m_overbright = overbrightIt->second->value->getFloat ();
	}
    }

    // Texture is resolved by CRenderable base class; read spritesheet data.
    // TextureParser computes spritesheet grid from TEXS frame data (animated textures)
    // or .tex-json metadata (static textures). For GIF-style animated textures (separate
    // GL texture per frame), the parser returns 0 cols/rows since a 1x1 grid can't hold
    // all frames - so no SPRITESHEET mode is needed (frame switching happens via texture ID).
    if (const auto texture = getTexture ()) {
	m_spritesheetCols = static_cast<int> (texture->getSpritesheetCols ());
	m_spritesheetRows = static_cast<int> (texture->getSpritesheetRows ());
	m_spritesheetFrames = static_cast<int> (texture->getSpritesheetFrames ());
	m_spritesheetDuration = texture->getSpritesheetDuration ();
    }

    setupEmitters ();
    setupInitializers ();
    setupOperators ();
    setupPass ();

    // Setup control points (max 8)
    m_controlPoints.resize (8);
    for (const auto& cp : m_particle.controlPoints) {
	if (cp.id >= 0 && cp.id < 8) {
	    m_controlPoints[cp.id].offset = cp.offset;
	    // Link to mouse if either flags bit 0 is set
	    m_controlPoints[cp.id].linkMouse = (cp.flags & 1) != 0;
	    m_controlPoints[cp.id].worldSpace = (cp.flags & 2) != 0;

	    // Initialize position to offset for non-mouse-linked control points
	    // Mouse-linked CPs will have their position updated in update()
	    if (!m_controlPoints[cp.id].linkMouse) {
		if (m_controlPoints[cp.id].worldSpace) {
		    m_controlPoints[cp.id].position = cp.offset - rootSystem ()->m_transformedOrigin;
		} else {
		    // Local space: offset is already relative to particle system center
		    m_controlPoints[cp.id].position = cp.offset;
		}
	    }
	}
    }

    setupChildren ();

    m_initialized = true;
}

void CParticle::setupChildren () {
    for (const auto& child : m_particle.children) {
	if (!child.definition || !child.definition->material || !child.definition->material->material) {
	    if (!child.name.empty ()) {
		sLog.out ("Particle child skipped (unresolved definition/material): ", child.name);
	    }
	    continue;
	}

	auto system = std::make_unique<CParticle> (getScene (), *child.definition, this, &child);
	system->setup ();

	if (system->m_useRopeRenderer && child.type.starts_with ("event")) {
	    sLog.out ("Particle child uses a rope renderer with event emission (may rope across bursts): ", child.name);
	}

	m_children.push_back (std::move (system));
    }
}

void CParticle::render () {
    if (!m_initialized || !m_particle.visible->value->getBool ()) {
	return;
    }

    // Initialize time on first render to avoid huge dt spike
    if (m_time == 0.0) {
	m_time = g_Time;
	m_startWall = g_Time;
	// Skip update on first frame to avoid weird initial burst
	// This ensures all particles start from a clean state
	if (m_useRopeRenderer) {
	    renderRope ();
	} else {
	    renderSprites ();
	}
	return;
    }

    // Update particles
    float dt = g_Time - static_cast<float> (m_time);
    m_time = g_Time;

    const float playbackRate = m_particle.instanceOverride.rate->value->getFloat ();

    const bool started = (g_Time - m_startWall) >= static_cast<float> (m_particle.startTime);

    if (dt > 0.0f && playbackRate > 0.0f) {
	m_dtReal = std::min (dt, 0.1f);
	dt = m_dtReal * std::min (playbackRate, 1.0f);

	static const bool s_noPrewarm = getenv ("LWE_NOPREWARM") != nullptr;
	if (started && !m_prewarmDone && !s_noPrewarm && m_particle.startTime > 0 && !isChildSystem ()) {
	    resetPopulation ();
	    const float step = 1.0f / 30.0f;
	    const float depth = 60.0f;
	    for (float t = 0.0f; t < depth; t += step) {
		m_sysTime += step;
		update (step);
	    }
	    m_prewarmDone = true;
	}
	if (started || !s_noPrewarm) {
	    m_sysTime += dt;
	    update (dt);
	}
    }

    if (started) {
	for (const auto& child : m_children) {
	    child->renderAsChild ();
	}
    }

    // Render particles
    if (m_particleCount > 0 && m_particle.material) {
	if (m_useRopeRenderer) {
	    renderRope ();
	} else {
	    renderSprites ();
	}
    }
}

void CParticle::renderAsChild () {
    if (!m_initialized || !m_particle.visible->value->getBool ()) {
	return;
    }

    for (const auto& child : m_children) {
	child->renderAsChild ();
    }

    if (m_particleCount > 0 && m_particle.material) {
	if (m_useRopeRenderer) {
	    renderRope ();
	} else {
	    renderSprites ();
	}
    }
}

void CParticle::update (float dt) {
    m_time = g_Time;

    updateTransformAndControlPoints ();

    // Lifecycle events are per-frame: children consume them right after this update
    m_frameSpawns.clear ();
    m_frameDeaths.clear ();

    const bool s_partStats = Logging::instrumentOn ("LWE_PARTSTATS");
    if (s_partStats) {
	if (const std::uint32_t e = Logging::instrumentEpoch ("LWE_PARTSTATS"); e != m_statEpoch) {
	    m_statEpoch = e;
	    m_statWindowStart = m_time;
	    m_statEmitted = 0;
	    m_statDied = 0;
	    m_statFrames = 0;
	    m_statPeakLive = 0;
	}
    }
    const uint32_t statCountBefore = m_particleCount;

    // Emit particles
    if (isChildSystem ()) {
	emitAsChild (dt);
    } else {
	const uint32_t emitStart = m_particleCount;
	static const EmitContext rootCtx {};
	for (auto& emitter : m_emitters) {
	    emitter (m_particles, m_particleCount, dt, rootCtx);
	}
	recordSpawnRange (emitStart);
    }

    if (s_partStats) {
	m_statEmitted += m_particleCount - statCountBefore;
	if (m_particleCount > m_statPeakLive) {
	    m_statPeakLive = m_particleCount;
	}
    }

    simCommon (dt);
    updateChildren (dt);
}

void CParticle::updateTransformAndControlPoints () {
    // Detect resolution changes and recalculate transformed origin
    float screenWidth = static_cast<float> (getScene ().getWidth ());
    float screenHeight = static_cast<float> (getScene ().getHeight ());

    if (screenWidth != m_lastScreenWidth || screenHeight != m_lastScreenHeight) {
	// Resolution changed - recalculate transformed origin (children stay at the
	// parent-local origin; their positions inherit the parent transform)
	if (!isChildSystem ()) {
	    glm::vec3 origin = m_particle.origin->value->getVec3 ();
	    origin.x -= screenWidth / 2.0f;
	    origin.y = screenHeight / 2.0f - origin.y;
	    m_transformedOrigin = origin;
	}

	// Update world-space control points that aren't mouse-linked
	for (size_t i = 0; i < m_controlPoints.size (); i++) {
	    auto& cp = m_controlPoints[i];
	    if (!cp.linkMouse && cp.worldSpace) {
		cp.position = cp.offset - rootSystem ()->m_transformedOrigin;
	    }
	}

	m_lastScreenWidth = screenWidth;
	m_lastScreenHeight = screenHeight;
    }

    // Update control points with mouse position
    const glm::vec2* mousePos = getScene ().getMousePositionNormalized ();
    if (mousePos) {
	// Child particles live in the ROOT system's local space (the copied transform),
	// so mouse coordinates convert through the root origin
	const glm::vec3& mouseOrigin = rootSystem ()->m_transformedOrigin;

	for (auto& cp : m_controlPoints) {
	    if (cp.linkMouse) {
		// Convert mouse position from normalized [0,1] to centered screen space
		glm::vec3 position;
		position.x = (mousePos->x * screenWidth) - (screenWidth / 2.0f);
		position.y = (screenHeight / 2.0f) - (mousePos->y * screenHeight);
		position.z = 0.0f;

		// Apply control point offset
		position += cp.offset;

		// Convert to particle local space to prevent double transformation by model matrix
		// Both world-space and local-space CPs are handled the same way now
		cp.position = position - mouseOrigin;
	    }
	}
    }

    if (isChildSystem ()) {
	for (const auto& cpData : m_particle.controlPoints) {
	    if (cpData.parentControlPoint >= 0 && cpData.parentControlPoint < 8 && cpData.id >= 0 && cpData.id < 8) {
		m_controlPoints[cpData.id].position
		    = m_parentSystem->m_controlPoints[cpData.parentControlPoint].position + cpData.offset;
	    }
	}
    }
}

void CParticle::simCommon (float dt) {
    const bool s_partStats = Logging::instrumentOn ("LWE_PARTSTATS");

    // Update particle age
    for (uint32_t i = 0; i < m_particleCount; i++) {
	m_particles[i].age += dt;
    }

    // Apply operators to living particles (including alphafade).
    // Operators receive the SYSTEM clock (playback-rate dilated), not wall time.
    for (auto& op : m_operators) {
	op (m_particles, m_particleCount, m_controlPoints, static_cast<float> (m_sysTime), dt);
    }

    // Update animation frames
    for (uint32_t i = 0; i < m_particleCount; i++) {
	auto& p = m_particles[i];

	if (m_spritesheetFrames > 0) {
	    float lifetimePos = p.getLifetimePos ();
	    float animSpeed = m_particle.sequenceMultiplier > 0.0f ? m_particle.sequenceMultiplier : 1.0f;

	    if (m_particle.animationMode == "randomframe") {
		if (p.frame < 0.0f) {
		    std::mt19937 particleRng (
			static_cast<std::mt19937::result_type> (reinterpret_cast<uintptr_t> (&p))
		    );
		    std::uniform_int_distribution<int> dist (0, m_spritesheetFrames - 1);
		    p.frame = static_cast<float> (dist (particleRng));
		}
	    } else if (m_particle.animationMode == "once") {
		p.frame = std::min (
		    lifetimePos * m_spritesheetFrames * animSpeed, static_cast<float> (m_spritesheetFrames - 1)
		);
	    } else {
		static const bool s_legacyAnimClock = [] () -> bool {
		    const char* v = getenv ("LWE_ANIMFRACTION");
		    return v != nullptr && v[0] == '0';
		}();
		if (!s_legacyAnimClock) {
		    float pos = lifetimePos * animSpeed;
		    pos -= std::floor (pos);
		    p.frame = pos * static_cast<float> (m_spritesheetFrames);
		} else if (m_spritesheetDuration > 0.0f) {
		    float timeInCycle = std::fmod (p.age * animSpeed, m_spritesheetDuration);
		    float cyclePos = timeInCycle / m_spritesheetDuration;
		    p.frame = std::fmod (cyclePos * m_spritesheetFrames, static_cast<float> (m_spritesheetFrames));
		} else {
		    p.frame = std::fmod (
			lifetimePos * m_spritesheetFrames * animSpeed, static_cast<float> (m_spritesheetFrames)
		    );
		}
	    }
	}
    }

    // Remove dead particles with order-preserving compaction.
    // Particles only die from natural lifetime expiry (age >= lifetime).
    // We never kill based on size - particles may fade in/out with oscillating size.
    // Compaction preserves spawn order so array index 0 is always the oldest particle.
    const bool trackDeaths = !m_children.empty ();
    uint32_t writeIdx = 0;
    for (uint32_t readIdx = 0; readIdx < m_particleCount; readIdx++) {
	if (m_particles[readIdx].isAlive ()) {
	    if (writeIdx != readIdx) {
		m_particles[writeIdx] = m_particles[readIdx];
	    }
	    writeIdx++;
	} else {
	    const auto& dead = m_particles[readIdx];
	    // eventdeath children fire at the particle's last position
	    if (trackDeaths) {
		m_frameDeaths.push_back ({ dead.position, dead.uid });
	    }
	    if (dead.ownerTag != 0) {
		for (auto& instance : m_eventInstances) {
		    if (instance.tag == dead.ownerTag) {
			if (instance.liveCount > 0) {
			    instance.liveCount--;
			}
			break;
		    }
		}
	    }
	}
    }
    if (s_partStats) {
	m_statDied += m_particleCount - writeIdx;
    }
    m_particleCount = writeIdx;

    if (m_useRopeRenderer && m_useTrailRenderer && m_trailLength > 0.0f) {
	constexpr size_t TRAIL_NODE_CAP = 4096; // defensive: unbounded fps guard
	for (uint32_t i = 0; i < m_particleCount; i++) {
	    auto& p = m_particles[i];
	    p.trail.push_back ({ p.position, p.size, p.color, p.alpha, m_sysTime });
	    size_t drop = 0;
	    while (drop < p.trail.size () - 1
		   && (m_sysTime - p.trail[drop + 1].time) > static_cast<double> (m_trailLength)) {
		drop++;
	    }
	    if (p.trail.size () - drop > TRAIL_NODE_CAP) {
		drop = p.trail.size () - TRAIL_NODE_CAP;
	    }
	    if (drop > 0) {
		p.trail.erase (p.trail.begin (), p.trail.begin () + static_cast<ptrdiff_t> (drop));
	    }
	}
    }

    if (s_partStats) {
	m_statFrames++;
	if (m_statWindowStart == 0.0) {
	    m_statWindowStart = m_time;
	}
	const double window = m_time - m_statWindowStart;
	if (window >= 5.0) {
	    float speedSum = 0.0f;
	    for (uint32_t i = 0; i < m_particleCount; i++) {
		speedSum += glm::length (m_particles[i].velocity);
	    }
	    const float meanSpeed = m_particleCount > 0 ? speedSum / static_cast<float> (m_particleCount) : 0.0f;
	    sLog.out (
		"LWE-PARTSTATS obj=", getId (), " t=", static_cast<float> (m_time),
		" win=", static_cast<float> (window),
		" fps=", static_cast<float> (static_cast<double> (m_statFrames) / window),
		" emit_s=", static_cast<float> (static_cast<double> (m_statEmitted) / window),
		" died_s=", static_cast<float> (static_cast<double> (m_statDied) / window), " live=", m_particleCount,
		" peak=", m_statPeakLive, " meanspd=", meanSpeed
	    );
	    m_statWindowStart = m_time;
	    m_statEmitted = 0;
	    m_statDied = 0;
	    m_statFrames = 0;
	    m_statPeakLive = m_particleCount;
	}
    }
}

void CParticle::updateChildren (float dt) {
    for (const auto& child : m_children) {
	// children share the parent's real-dt for the kinematics clock split
	child->m_dtReal = m_dtReal;
	child->update (dt);
    }
}

void CParticle::emitAsChild (float dt) {
    const auto& type = m_link->type;

    if (type.empty () || type == "static") {
	if (m_childStartWall < 0.0) {
	    m_childStartWall = g_Time;
	}
	if ((g_Time - m_childStartWall) < static_cast<float> (m_particle.startTime)) {
	    return;
	}
	EmitContext ctx {};
	ctx.anchor = linkAnchorOffset ();
	const uint32_t emitStart = m_particleCount;
	for (auto& emitter : m_emitters) {
	    emitter (m_particles, m_particleCount, dt, ctx);
	}
	applyLinkScale (emitStart);
	recordSpawnRange (emitStart);
	return;
    }

    std::erase_if (m_eventInstances, [] (const ChildEventInstance& instance) {
	return !instance.emitting && instance.liveCount == 0;
    });

    const auto instanceCap = static_cast<size_t> (std::max (1, m_link->maxCount));

    if (type == "eventspawn" || type == "eventdeath") {
	const auto& events = (type == "eventspawn") ? m_parentSystem->m_frameSpawns : m_parentSystem->m_frameDeaths;
	for (const auto& event : events) {
	    if (m_eventInstances.size () >= instanceCap) {
		break; // at concurrent-instance cap: drop the remaining events this frame
	    }
	    if (!rollProbability ()) {
		continue;
	    }
	    spawnBurstInstance (event.position, dt);
	}
    } else if (type == "eventfollow") {
	for (const auto& event : m_parentSystem->m_frameSpawns) {
	    if (m_eventInstances.size () >= instanceCap) {
		break;
	    }
	    if (!rollProbability ()) {
		continue;
	    }
	    ChildEventInstance instance {};
	    instance.tag = m_nextTag++;
	    if (m_nextTag == 0) {
		m_nextTag = 1;
	    }
	    instance.parentUid = event.uid;
	    instance.emitting = true;
	    // pristine closure copies: the emitters' mutable timers become instance state
	    instance.emitters = m_emitters;
	    m_eventInstances.push_back (std::move (instance));
	}

	for (auto& instance : m_eventInstances) {
	    if (!instance.emitting) {
		continue;
	    }
	    const auto* followed = m_parentSystem->findParticleByUid (instance.parentUid);
	    if (followed == nullptr) {
		instance.emitting = false; // parent died: existing particles age out
		continue;
	    }
	    EmitContext ctx {};
	    ctx.anchor = followed->position + linkAnchorOffset ();
	    ctx.tag = instance.tag;
	    if (instance.hasAnchor) {
		const glm::vec3 delta = ctx.anchor - instance.lastAnchor;
		if (delta.x != 0.0f || delta.y != 0.0f || delta.z != 0.0f) {
		    for (uint32_t i = 0; i < m_particleCount; i++) {
			auto& p = m_particles[i];
			if (p.ownerTag == instance.tag) {
			    p.position += delta;
			    for (auto& n : p.trail) {
				n.position += delta;
			    }
			}
		    }
		}
	    }
	    instance.lastAnchor = ctx.anchor;
	    instance.hasAnchor = true;
	    // Alpha-follow: children of this instance ride the parent particle's live
	    // alpha (see ParticleInstance::followAlpha). LWE_NOFOLLOWALPHA disables.
	    static const bool s_noFollowAlpha = getenv ("LWE_NOFOLLOWALPHA") != nullptr;
	    if (!s_noFollowAlpha) {
		for (uint32_t i = 0; i < m_particleCount; i++) {
		    if (m_particles[i].ownerTag == instance.tag) {
			m_particles[i].followAlpha = followed->alpha;
		    }
		}
	    }
	    ctx.budget = static_cast<int32_t> (m_particle.maxCount) - static_cast<int32_t> (instance.liveCount);
	    if (ctx.budget <= 0) {
		continue;
	    }
	    const uint32_t emitStart = m_particleCount;
	    for (auto& emitter : instance.emitters) {
		emitter (m_particles, m_particleCount, dt, ctx);
	    }
	    if (m_particleCount > emitStart) {
		instance.liveCount += m_particleCount - emitStart;
		applyLinkScale (emitStart);
		recordSpawnRange (emitStart);
	    }
	}
    }
}

void CParticle::spawnBurstInstance (const glm::vec3& anchor, float dt) {
    ChildEventInstance instance {};
    instance.tag = m_nextTag++;
    if (m_nextTag == 0) {
	m_nextTag = 1;
    }

    EmitContext ctx {};
    ctx.anchor = anchor + linkAnchorOffset ();
    ctx.tag = instance.tag;
    ctx.budget = static_cast<int32_t> (std::max (1u, m_particle.maxCount));
    ctx.burst = true;

    const uint32_t emitStart = m_particleCount;
    for (auto& emitter : m_emitters) {
	emitter (m_particles, m_particleCount, dt, ctx);
    }

    if (m_particleCount > emitStart) {
	instance.liveCount = m_particleCount - emitStart;
	applyLinkScale (emitStart);
	recordSpawnRange (emitStart);
	m_eventInstances.push_back (instance);
    }
}

void CParticle::recordSpawnRange (uint32_t from) {
    if (m_children.empty ()) {
	return;
    }
    for (uint32_t i = from; i < m_particleCount; i++) {
	m_frameSpawns.push_back ({ m_particles[i].position, m_particles[i].uid });
    }
}

void CParticle::applyLinkScale (uint32_t from) {
    const float scale = m_link != nullptr ? m_link->scale.x : 1.0f;
    if (scale == 1.0f) {
	return;
    }
    for (uint32_t i = from; i < m_particleCount; i++) {
	m_particles[i].size *= scale;
	m_particles[i].initial.size *= scale;
    }
}

const ParticleInstance* CParticle::findParticleByUid (uint32_t uid) const {
    if (uid == 0) {
	return nullptr;
    }
    for (uint32_t i = 0; i < m_particleCount; i++) {
	if (m_particles[i].uid == uid) {
	    return &m_particles[i];
	}
    }
    return nullptr;
}

glm::vec3 CParticle::linkAnchorOffset () const {
    // Link origin follows the emitter-origin convention (authored y-down -> internal y-up)
    glm::vec3 offset = m_link != nullptr ? m_link->origin : glm::vec3 (0.0f);
    offset.y = -offset.y;
    return offset;
}

bool CParticle::rollProbability () {
    const float probability = m_link != nullptr ? m_link->probability : 1.0f;
    if (probability >= 1.0f) {
	return true;
    }
    return WallpaperEngine::Maths::randomFloat (m_rng, 0.0f, 1.0f) <= probability;
}

float CParticle::worldSizeDivisor () const {
    const glm::vec3 s = rootSystem ()->m_particle.scale->value->getVec3 ();
    const float m = std::sqrt (std::abs (s.x) * std::abs (s.y));
    if (isChildSystem () && m > 1e-6f) {
	return m;
    }
    return m > 1.0f ? m : 1.0f;
}

void CParticle::resetPopulation () {
    // clear the live pool + histories; recurses into children (their pools are
    // event-driven and regenerate during the prewarm fast-forward)
    for (uint32_t i = 0; i < m_particleCount; i++) {
	m_particles[i].alive = false;
	m_particles[i].trail.clear ();
    }
    m_particleCount = 0;
    m_frameSpawns.clear ();
    m_frameDeaths.clear ();
    for (auto& instance : m_eventInstances) {
	instance.liveCount = 0;
    }
    for (const auto& child : m_children) {
	child->resetPopulation ();
    }
}

const CParticle* CParticle::rootSystem () const {
    const CParticle* system = this;
    while (system->m_parentSystem != nullptr) {
	system = system->m_parentSystem;
    }
    return system;
}

const Particle& CParticle::getParticle () const { return m_particle; }

const float& CParticle::getBrightness () const { return m_overbright; }

const float& CParticle::getUserAlpha () const { return m_particle.instanceOverride.alpha->value->getFloat (); }

const float& CParticle::getAlpha () const { return m_particle.instanceOverride.alpha->value->getFloat (); }

const glm::vec3& CParticle::getColor () const {
    static const glm::vec3 defaultColor (1.0f);
    if (m_particle.instanceOverride.color && m_particle.instanceOverride.color->value) {
	return m_particle.instanceOverride.color->value->getVec3 ();
    }
    return defaultColor;
}

glm::vec4 CParticle::getColor4 () const { return { this->getColor (), this->getAlpha () }; }

const glm::vec3& CParticle::getCompositeColor () const { return getColor (); }

// ========== EMITTERS ==========

void CParticle::setupEmitters () {
    for (const auto& emitter : m_particle.emitters) {
	EmitterFunc func;

	if (emitter.name == "boxrandom") {
	    func = createBoxEmitter (emitter);
	} else if (emitter.name == "sphererandom") {
	    func = createSphereEmitter (emitter);
	} else if (emitter.name == "instant") {
	    func = createSphereEmitter (emitter);
	} else {
	    sLog.out ("Unknown emitter type: ", emitter.name);
	    continue;
	}

	if (func) {
	    m_emitters.push_back (std::move (func));
	}
    }
}

EmitterFunc CParticle::createBoxEmitter (const ParticleEmitter& emitter) {
    const float rate = emitter.rate;

    glm::vec3 transformedEmitterOrigin = emitter.origin;
    transformedEmitterOrigin.y = -transformedEmitterOrigin.y;
    if ((m_particle.flags & 1) != 0) {
	const glm::vec3 sOrig = rootSystem ()->m_particle.scale->value->getVec3 ();
	if (std::abs (sOrig.x) > 1e-6f) {
	    transformedEmitterOrigin.x /= sOrig.x;
	}
	if (std::abs (sOrig.y) > 1e-6f) {
	    transformedEmitterOrigin.y /= sOrig.y;
	}
	if (std::abs (sOrig.z) > 1e-6f) {
	    transformedEmitterOrigin.z /= sOrig.z;
	}
    }

    int controlPointIndex = emitter.controlPoint;
    if (controlPointIndex == -1 && !m_particle.controlPoints.empty ()) {
	const auto& cp0 = m_particle.controlPoints[0];
	if ((cp0.flags & 1) != 0) {
	    controlPointIndex = 0;
	}
    }

    glm::vec3 flippedDirections = emitter.directions;
    flippedDirections.y = -flippedDirections.y;

    bool limitOnePerFrame = (emitter.flags & 2) != 0;
    bool randomPeriodicEmission = (emitter.flags & 4) != 0;

    return [this, emitter, transformedEmitterOrigin, controlPointIndex, rate, flippedDirections, limitOnePerFrame,
	    randomPeriodicEmission, emissionTimer = 0.0f, delayTimer = emitter.delay, durationTimer = 0.0f,
	    periodicTimer = 0.0f, periodicDuration = 0.0f, periodicDelay = 0.0f, emitting = false,
	    instantaneousEmitted = false] (
	       std::vector<ParticleInstance>& particles, uint32_t& count, float dt, const EmitContext& ctx
	   ) mutable {
	if (count >= particles.size ()) {
	    return;
	}

	uint32_t toEmit = 0;

	if (ctx.burst) {
	    toEmit = emitter.instantaneous > 0 ? emitter.instantaneous : 1;
	} else {
	    // Handle delay
	    if (delayTimer > 0.0f) {
		delayTimer -= dt;
		return;
	    }

	    // Handle duration
	    if (emitter.duration > 0.0f) {
		durationTimer += dt;
		if (durationTimer >= emitter.duration) {
		    return;
		}
	    }

	    // Handle random periodic emission
	    if (randomPeriodicEmission) {
		periodicTimer += dt;

		if (!emitting) {
		    if (periodicTimer >= periodicDelay) {
			emitting = true;
			periodicTimer = 0.0f;
			periodicDuration = WallpaperEngine::Maths::randomFloat (
			    m_rng, emitter.minPeriodicDuration, emitter.maxPeriodicDuration
			);
		    } else {
			return;
		    }
		} else {
		    if (periodicTimer >= periodicDuration) {
			emitting = false;
			periodicTimer = 0.0f;
			periodicDelay = WallpaperEngine::Maths::randomFloat (
			    m_rng, emitter.minPeriodicDelay, emitter.maxPeriodicDelay
			);
			return;
		    }
		}
	    }

	    // TODO: Audio processing (audioProcessingMode, audioProcessingBounds, etc.)

	    // Handle instantaneous emission
	    if (emitter.instantaneous > 0 && !instantaneousEmitted) {
		toEmit = emitter.instantaneous;
		instantaneousEmitted = true;
	    }

	    if (emitter.rate > 0.0f) {
		emissionTimer += dt * rate * m_particle.instanceOverride.count->value->getFloat ();
		uint32_t rateEmit = static_cast<uint32_t> (emissionTimer);
		emissionTimer -= static_cast<float> (rateEmit);
		// limitOnePerFrame (flags bit 1): cap at 1 to prevent rope artifacts
		if (limitOnePerFrame && rateEmit > 1) {
		    rateEmit = 1;
		}
		toEmit += rateEmit;
	    }
	}

	if (ctx.budget < static_cast<int32_t> (toEmit)) {
	    toEmit = static_cast<uint32_t> (std::max (0, ctx.budget));
	}

	// Emit particles
	for (uint32_t i = 0; i < toEmit && count < particles.size (); i++) {
	    auto& p = particles[count];

	    glm::vec3 spawnOrigin = transformedEmitterOrigin + ctx.anchor;
	    if (controlPointIndex >= 0 && controlPointIndex < static_cast<int> (m_controlPoints.size ())) {
		spawnOrigin += m_controlPoints[controlPointIndex].position;
	    }

	    // Generate random position within box volume centered on origin
	    // This creates a centered box (or hollow box if distanceMin > 0)
	    glm::vec3 randomPos;
	    for (int axis = 0; axis < 3; axis++) {
		float minDist = emitter.distanceMin[axis];
		float maxDist = emitter.distanceMax[axis];
		// Generate value in [minDist, maxDist]
		float dist = WallpaperEngine::Maths::randomFloat (m_rng, minDist, maxDist);
		// Randomly flip sign to center the distribution
		if (WallpaperEngine::Maths::randomFloat (m_rng, 0.0f, 1.0f) < 0.5f) {
		    dist = -dist;
		}
		randomPos[axis] = dist;
	    }
	    randomPos *= flippedDirections;

	    p.position = spawnOrigin + randomPos;

	    // Emitter does not set velocity - initializers handle that
	    p.velocity = glm::vec3 (0.0f);
	    p.acceleration = glm::vec3 (0.0f);
	    p.rotation = glm::vec3 (0.0f);
	    p.angularVelocity = glm::vec3 (0.0f);
	    p.angularAcceleration = glm::vec3 (0.0f);

	    p.color = glm::vec3 (1.0f) * m_particle.instanceOverride.colorn->value->getVec3 () * m_inhColorN;
	    p.alpha = 1.0f * m_particle.instanceOverride.alpha->value->getFloat () * m_inhAlpha;
	    p.size = 20.0f * m_particle.instanceOverride.size->value->getFloat () * m_inhSize / worldSizeDivisor ();
	    p.lifetime = 1.0f * m_particle.instanceOverride.lifetime->value->getFloat () * m_inhLifetime;
	    p.age = 0.0f;
	    p.alive = true;
	    p.frame = -1.0f;
	    p.trail.clear ();
	    // Stable id + owning child instance (0 = root emission)
	    p.uid = m_nextUid++;
	    if (m_nextUid == 0) {
		m_nextUid = 1;
	    }
	    p.ownerTag = ctx.tag;

	    p.initial.color = p.color;
	    p.initial.alpha = p.alpha;
	    p.initial.size = p.size;
	    p.initial.lifetime = p.lifetime;

	    // Reset oscillator state for reused particles
	    p.oscillateAlpha = {};
	    p.oscillateSize = {};
	    p.oscillatePosition = {};

	    // Apply initializers
	    for (auto& init : m_initializers) {
		init (p);
	    }

	    count++;
	}
    };
}

EmitterFunc CParticle::createSphereEmitter (const ParticleEmitter& emitter) {
    const float rate = emitter.rate;
    float lifetime = 1.0f * m_particle.instanceOverride.lifetime->value->getFloat () * m_inhLifetime;

    // Convert emitter origin from screen space (Y down) to centered space (Y up)
    glm::vec3 transformedEmitterOrigin = emitter.origin;
    transformedEmitterOrigin.y = -transformedEmitterOrigin.y;
    if ((m_particle.flags & 1) != 0) {
	const glm::vec3 sOrig = rootSystem ()->m_particle.scale->value->getVec3 ();
	if (std::abs (sOrig.x) > 1e-6f) {
	    transformedEmitterOrigin.x /= sOrig.x;
	}
	if (std::abs (sOrig.y) > 1e-6f) {
	    transformedEmitterOrigin.y /= sOrig.y;
	}
	if (std::abs (sOrig.z) > 1e-6f) {
	    transformedEmitterOrigin.z /= sOrig.z;
	}
    }

    int controlPointIndex = emitter.controlPoint;

    // Auto-detect control point 0 usage if controlPoint field not specified and CP0 has linkMouse
    if (controlPointIndex == -1 && !m_particle.controlPoints.empty ()) {
	const auto& cp0 = m_particle.controlPoints[0];
	if ((cp0.flags & 1) != 0) { // Bit 0: linkMouse flag
	    controlPointIndex = 0;
	}
    }

    bool limitOnePerFrame = (emitter.flags & 2) != 0;

    return [this, emitter, transformedEmitterOrigin, controlPointIndex, rate, lifetime, limitOnePerFrame,
	    emissionTimer = 0.0f, remaining = emitter.instantaneous] (
	       std::vector<ParticleInstance>& particles, uint32_t& count, float dt, const EmitContext& ctx
	   ) mutable {
	if (count >= particles.size ()) {
	    return;
	}

	uint32_t toEmit = 0;

	if (ctx.burst) {
	    toEmit = emitter.instantaneous > 0 ? emitter.instantaneous : 1;
	} else {
	    emissionTimer += dt * rate * m_particle.instanceOverride.count->value->getFloat ();
	    toEmit = static_cast<uint32_t> (emissionTimer);
	    emissionTimer -= static_cast<float> (toEmit);
	    // limitOnePerFrame (flags bit 1): cap at 1 to prevent rope artifacts
	    if (limitOnePerFrame && toEmit > 1) {
		toEmit = 1;
	    }

	    if (remaining > 0) {
		toEmit = remaining;
		remaining = 0;
	    }
	}

	if (ctx.budget < static_cast<int32_t> (toEmit)) {
	    toEmit = static_cast<uint32_t> (std::max (0, ctx.budget));
	}

	for (uint32_t i = 0; i < toEmit && count < particles.size (); i++) {
	    auto& p = particles[count];

	    glm::vec3 spawnOrigin = transformedEmitterOrigin + ctx.anchor;
	    if (controlPointIndex >= 0 && controlPointIndex < static_cast<int> (m_controlPoints.size ())) {
		spawnOrigin += m_controlPoints[controlPointIndex].position;
	    }

	    // Spawn at random position on ellipsoid surface
	    glm::vec3 randomPos;

	    // Orthographic particles (flags & 4 == 0): use 2D disk distribution in X/Y plane
	    // Perspective particles (flags & 4 != 0): use 3D spherical shell distribution
	    if ((m_particle.flags & 4) == 0) {
		// 2D disk distribution with random Z offset
		float angle = WallpaperEngine::Maths::randomFloat (m_rng, 0.0f, glm::two_pi<float> ());
		float minRadius = emitter.distanceMin.x;
		float maxRadius = emitter.distanceMax.x;

		// Use sqrt for uniform area distribution in annulus
		float minRadiusSq = minRadius * minRadius;
		float maxRadiusSq = maxRadius * maxRadius;
		float radiusXY = std::sqrt (WallpaperEngine::Maths::randomFloat (m_rng, minRadiusSq, maxRadiusSq));

		randomPos = glm::vec3 (
		    radiusXY * std::cos (angle), radiusXY * std::sin (angle),
		    WallpaperEngine::Maths::randomFloat (m_rng, -maxRadius, maxRadius)
		);

		randomPos *= emitter.directions;
	    } else {
		// 3D spherical shell distribution
		float theta = WallpaperEngine::Maths::randomFloat (m_rng, 0.0f, glm::two_pi<float> ());
		float cosTheta = WallpaperEngine::Maths::randomFloat (m_rng, -1.0f, 1.0f);
		float sinTheta = std::sqrt (1.0f - cosTheta * cosTheta);

		randomPos = glm::vec3 (sinTheta * std::cos (theta), sinTheta * std::sin (theta), cosTheta);

		// Use cubic root for uniform volume distribution
		float minRadius = emitter.distanceMin.x;
		float maxRadius = emitter.distanceMax.x;
		float minRadiusCubed = minRadius * minRadius * minRadius;
		float maxRadiusCubed = maxRadius * maxRadius * maxRadius;
		float radius = std::cbrt (WallpaperEngine::Maths::randomFloat (m_rng, minRadiusCubed, maxRadiusCubed));

		randomPos *= radius;
		randomPos *= emitter.directions;
	    }

	    // Apply sign property to force positive/negative values per axis
	    // 0 = both, 1 = positive only, -1 = negative only
	    for (int i = 0; i < 3; i++) {
		if (emitter.sign[i] == 1) {
		    randomPos[i] = std::abs (randomPos[i]); // Force positive
		} else if (emitter.sign[i] == -1) {
		    randomPos[i] = -std::abs (randomPos[i]); // Force negative
		}
		// If sign[i] == 0, leave as-is (both positive and negative possible)
	    }
	    p.position = spawnOrigin + randomPos;

	    // Set velocity only if emitter specifies speed (otherwise use initializers)
	    if (emitter.speedMax > 0.0f || emitter.speedMin != 0.0f) {
		// Velocity pointing outward from ellipsoid (randomPos already includes directions scaling)
		glm::vec3 direction
		    = glm::length (randomPos) > 0.0f ? glm::normalize (randomPos) : glm::vec3 (0.0f, 1.0f, 0.0f);
		float speed = WallpaperEngine::Maths::randomFloat (m_rng, emitter.speedMin, emitter.speedMax);
		p.velocity = direction * speed;
	    } else {
		// No emitter speed specified, velocity will be set by initializers
		p.velocity = glm::vec3 (0.0f);
	    }

	    p.acceleration = glm::vec3 (0.0f);
	    p.rotation = glm::vec3 (0.0f);
	    p.angularVelocity = glm::vec3 (0.0f);
	    p.angularAcceleration = glm::vec3 (0.0f);

	    p.color = glm::vec3 (1.0f) * m_particle.instanceOverride.colorn->value->getVec3 () * m_inhColorN;
	    p.alpha = 1.0f * m_particle.instanceOverride.alpha->value->getFloat () * m_inhAlpha;
	    p.size = 20.0f * m_particle.instanceOverride.size->value->getFloat () * m_inhSize / worldSizeDivisor ();
	    p.lifetime = lifetime;
	    p.age = 0.0f;
	    p.alive = true;
	    p.frame = -1.0f;
	    p.trail.clear ();
	    // Stable id + owning child instance (0 = root emission)
	    p.uid = m_nextUid++;
	    if (m_nextUid == 0) {
		m_nextUid = 1;
	    }
	    p.ownerTag = ctx.tag;

	    p.initial.color = p.color;
	    p.initial.alpha = p.alpha;
	    p.initial.size = p.size;
	    p.initial.lifetime = p.lifetime;

	    // Reset oscillator state for reused particles
	    p.oscillateAlpha = {};
	    p.oscillateSize = {};
	    p.oscillatePosition = {};

	    for (auto& init : m_initializers) {
		init (p);
	    }

	    count++;
	}
    };
}

// ========== INITIALIZERS ==========

void CParticle::setupInitializers () {
    const bool fromPackage = this->getScene ().getScene ().project.fromPackage;
    // pkg + two colorrandoms: gather the pair and emit ONE composed initializer
    const ColorRandomInitializer* pkgPair[2] = { nullptr, nullptr };
    if (fromPackage) {
	int nCR = 0;
	for (const auto& ini : m_particle.initializers) {
	    if (ini && ini->is<ColorRandomInitializer> () && nCR < 2) {
		pkgPair[nCR++] = ini->as<ColorRandomInitializer> ();
	    }
	}
	if (nCR < 2) {
	    pkgPair[0] = pkgPair[1] = nullptr;
	}
    }
    bool seenColorRandom = false;
    for (const auto& initializer : m_particle.initializers) {
	if (!initializer) {
	    continue;
	}

	InitializerFunc func;

	if (initializer->is<ColorRandomInitializer> ()) {
	    if (seenColorRandom && fromPackage) {
		continue;
	    }
	    if (pkgPair[1] != nullptr) {
		func = createPkgDualColorRandomInitializer (*pkgPair[0], *pkgPair[1]);
	    } else {
		func = createColorRandomInitializer (*initializer->as<ColorRandomInitializer> (), seenColorRandom);
	    }
	    seenColorRandom = true;
	} else if (initializer->is<SizeRandomInitializer> ()) {
	    func = createSizeRandomInitializer (*initializer->as<SizeRandomInitializer> ());
	} else if (initializer->is<AlphaRandomInitializer> ()) {
	    func = createAlphaRandomInitializer (*initializer->as<AlphaRandomInitializer> ());
	} else if (initializer->is<LifetimeRandomInitializer> ()) {
	    const auto& lifeInit = *initializer->as<LifetimeRandomInitializer> ();
	    m_uniformLifetimes = (lifeInit.min->value->getFloat () == lifeInit.max->value->getFloat ());
	    func = createLifetimeRandomInitializer (lifeInit);
	} else if (initializer->is<VelocityRandomInitializer> ()) {
	    func = createVelocityRandomInitializer (*initializer->as<VelocityRandomInitializer> ());
	} else if (initializer->is<RotationRandomInitializer> ()) {
	    func = createRotationRandomInitializer (*initializer->as<RotationRandomInitializer> ());
	} else if (initializer->is<AngularVelocityRandomInitializer> ()) {
	    func = createAngularVelocityRandomInitializer (*initializer->as<AngularVelocityRandomInitializer> ());
	} else if (initializer->is<TurbulentVelocityRandomInitializer> ()) {
	    func = createTurbulentVelocityRandomInitializer (*initializer->as<TurbulentVelocityRandomInitializer> ());
	} else if (initializer->is<MapSequenceAroundControlPointInitializer> ()) {
	    func = createMapSequenceAroundControlPointInitializer (
		*initializer->as<MapSequenceAroundControlPointInitializer> ()
	    );
	} else {
	    sLog.out ("Unknown initializer type");
	}

	if (func) {
	    m_initializers.push_back (std::move (func));
	}
    }
}

InitializerFunc CParticle::createPkgDualColorRandomInitializer (
    const ColorRandomInitializer& first, const ColorRandomInitializer& second
) {
    DynamicValue* min1 = first.min->value.get ();
    DynamicValue* max1 = first.max->value.get ();
    DynamicValue* min2 = second.min->value.get ();
    DynamicValue* max2 = second.max->value.get ();
    DynamicValue* colorOverride = m_particle.instanceOverride.colorn->value.get ();

    return [this, min1, max1, min2, max2, colorOverride] (ParticleInstance& p) {
	const glm::vec3 cn = colorOverride->getVec3 () * m_inhColorN;
	if ((m_particle.instanceOverride.colornAuthored || m_inhColorNAuthored) && cn != glm::vec3 (1.0f)) {
	    p.color = cn;
	    p.initial.color = p.color;
	    return;
	}
	const float t1 = WallpaperEngine::Maths::randomFloat (m_rng, 0.0f, 1.0f);
	const float t2 = WallpaperEngine::Maths::randomFloat (m_rng, 0.0f, 1.0f);
	const glm::vec3 c1 = glm::mix (min1->getVec3 (), max1->getVec3 (), t1);
	const glm::vec3 a = min2->getVec3 (), b = max2->getVec3 ();
	const glm::vec3 c2 = glm::mix (a, b, t2);
	const float lo2R = std::min (a.r, b.r), hi2R = std::max (a.r, b.r);
	const float lo2G = std::min (a.g, b.g), hi2G = std::max (a.g, b.g);
	const float span2G = hi2G - lo2G;
	const auto avgW = [] (float x) { return (1.0f + x) * 0.5f; };

	const float R = c1.r * (hi2R - lo2R) * 0.5f + c1.b * t1;
	float G, B;
	if (span2G < 1e-6f) {
	    G = avgW (c1.g) * avgW (t2);
	    B = avgW (lo2R + (hi2R - lo2R) * t1);
	} else if (span2G > 1.0f - 1e-6f) {
	    G = avgW (c2.g);
	    B = avgW (c1.g) * avgW (c2.g);
	} else {
	    G = c2.g;
	    B = c2.g * ((lo2G + hi2G) * 0.5f - c1.b * 0.5f + avgW (c1.b) * c1.g);
	}
	p.color = glm::clamp (glm::vec3 (R, G, B), 0.0f, 1.0f);
	p.initial.color = p.color;
    };
}

InitializerFunc CParticle::createColorRandomInitializer (const ColorRandomInitializer& init, const bool multiplyInto) {
    DynamicValue* minValue = init.min->value.get ();
    DynamicValue* maxValue = init.max->value.get ();
    DynamicValue* colorOverride = m_particle.instanceOverride.colorn->value.get ();
    const bool fromPackage = this->getScene ().getScene ().project.fromPackage;

    return [this, minValue, maxValue, colorOverride, multiplyInto, fromPackage] (ParticleInstance& p) {
	const glm::vec3 cn = colorOverride->getVec3 () * m_inhColorN;
	const bool authored = m_particle.instanceOverride.colornAuthored || m_inhColorNAuthored;
	if (authored && (!fromPackage || cn != glm::vec3 (1.0f))) {
	    p.color = cn;
	} else {
	    const float t = WallpaperEngine::Maths::randomFloat (m_rng, 0.0f, 1.0f);
	    const glm::vec3 drawn = glm::mix (minValue->getVec3 (), maxValue->getVec3 (), t);
	    p.color = multiplyInto ? p.color * drawn : drawn;
	}
	p.initial.color = p.color;
    };
}

InitializerFunc CParticle::createSizeRandomInitializer (const SizeRandomInitializer& init) {
    DynamicValue* minValue = init.min->value.get ();
    DynamicValue* maxValue = init.max->value.get ();
    DynamicValue* exponentValue = init.exponent->value.get ();
    DynamicValue* sizeOverride = m_particle.instanceOverride.size->value.get ();

    return [this, minValue, maxValue, exponentValue, sizeOverride] (ParticleInstance& p) {
	float t = WallpaperEngine::Maths::randomFloat (m_rng, 0.0f, 1.0f);
	float exponent = exponentValue->getFloat ();
	float min = minValue->getFloat ();
	float max = maxValue->getFloat ();

	// Apply exponent for non-linear distribution
	float adjustedT = std::pow (t, exponent);
	p.size = (min + adjustedT * (max - min)) * sizeOverride->getFloat () * m_inhSize / 2.0f / worldSizeDivisor ();
	p.initial.size = p.size;
    };
}

InitializerFunc CParticle::createAlphaRandomInitializer (const AlphaRandomInitializer& init) {
    DynamicValue* minValue = init.min->value.get ();
    DynamicValue* maxValue = init.max->value.get ();
    DynamicValue* alphaOverride = m_particle.instanceOverride.alpha->value.get ();

    return [this, minValue, maxValue, alphaOverride] (ParticleInstance& p) {
	p.alpha = WallpaperEngine::Maths::randomFloat (m_rng, minValue->getFloat (), maxValue->getFloat ())
	    * alphaOverride->getFloat () * m_inhAlpha;
	p.initial.alpha = p.alpha;
    };
}

InitializerFunc CParticle::createLifetimeRandomInitializer (const LifetimeRandomInitializer& init) {
    DynamicValue* minValue = init.min->value.get ();
    DynamicValue* maxValue = init.max->value.get ();
    DynamicValue* lifetimeOverride = m_particle.instanceOverride.lifetime->value.get ();

    return [this, minValue, maxValue, lifetimeOverride] (ParticleInstance& p) {
	p.lifetime = WallpaperEngine::Maths::randomFloat (m_rng, minValue->getFloat (), maxValue->getFloat ())
	    * lifetimeOverride->getFloat () * m_inhLifetime;
	p.initial.lifetime = p.lifetime;
    };
}

InitializerFunc CParticle::createVelocityRandomInitializer (const VelocityRandomInitializer& init) {
    DynamicValue* minValue = init.min->value.get ();
    DynamicValue* maxValue = init.max->value.get ();
    DynamicValue* speedOverride = m_particle.instanceOverride.speed->value.get ();

    return [this, minValue, maxValue, speedOverride] (ParticleInstance& p) {
	glm::vec3 vel = WallpaperEngine::Maths::randomVec3 (m_rng, minValue->getVec3 (), maxValue->getVec3 ())
	    * speedOverride->getFloat () * m_inhSpeed;
	vel.y = -vel.y;
	static const bool s_velProbe = getenv ("LWE_VELPROBE") != nullptr;
	if (s_velProbe) {
	    sLog.out ("LWE-VELPROBE id=", this->getId (), " vel=(", vel.x, ",", vel.y, ")");
	}
	p.velocity += vel;
    };
}

InitializerFunc CParticle::createRotationRandomInitializer (const RotationRandomInitializer& init) {
    DynamicValue* minValue = init.min->value.get ();
    DynamicValue* maxValue = init.max->value.get ();
    DynamicValue* speedOverride = m_particle.instanceOverride.speed->value.get ();

    return [this, minValue, maxValue, speedOverride] (ParticleInstance& p) {
	p.rotation = WallpaperEngine::Maths::randomVec3 (m_rng, minValue->getVec3 (), maxValue->getVec3 ())
	    * speedOverride->getFloat () * m_inhSpeed;
    };
}

InitializerFunc CParticle::createAngularVelocityRandomInitializer (const AngularVelocityRandomInitializer& init) {
    DynamicValue* minValue = init.min->value.get ();
    DynamicValue* maxValue = init.max->value.get ();
    DynamicValue* exponentValue = init.exponent->value.get ();
    DynamicValue* speedOverride = m_particle.instanceOverride.speed->value.get ();

    return [this, minValue, maxValue, exponentValue, speedOverride] (ParticleInstance& p) {
	glm::vec3 minVec = minValue->getVec3 ();
	glm::vec3 maxVec = maxValue->getVec3 ();
	float exponent = exponentValue->getFloat ();

	// Apply exponent bias to random distribution
	// exponent = 1: uniform distribution
	// exponent -> 0: bias towards max
	// exponent >= 2: bias towards min
	glm::vec3 result;
	for (int i = 0; i < 3; i++) {
	    float t = WallpaperEngine::Maths::randomFloat (m_rng, 0.0f, 1.0f);
	    t = std::pow (t, exponent);
	    result[i] = minVec[i] + t * (maxVec[i] - minVec[i]);
	}

	p.angularVelocity = result * speedOverride->getFloat () * m_inhSpeed;
    };
}

InitializerFunc CParticle::createTurbulentVelocityRandomInitializer (const TurbulentVelocityRandomInitializer& init) {
    DynamicValue* speedMin = init.speedMin->value.get ();
    DynamicValue* speedMax = init.speedMax->value.get ();
    DynamicValue* offsetVal = init.offset->value.get ();
    DynamicValue* scaleVal = init.scale->value.get ();
    DynamicValue* forwardVal = init.forward->value.get ();
    DynamicValue* timeScaleVal = init.timeScale->value.get ();
    DynamicValue* phaseMinVal = init.phaseMin->value.get ();
    DynamicValue* phaseMaxVal = init.phaseMax->value.get ();
    DynamicValue* rightVal = init.right->value.get ();
    DynamicValue* speedOverride = m_particle.instanceOverride.speed->value.get ();

    return [this, speedMin, speedMax, offsetVal, scaleVal, forwardVal, timeScaleVal, phaseMinVal, phaseMaxVal, rightVal,
	    speedOverride] (ParticleInstance& p) {
	// Get direction parameters
	glm::vec3 forward = forwardVal->getVec3 ();
	glm::vec3 right = rightVal->getVec3 ();
	// Y-flip for coordinate system conversion
	forward.y = -forward.y;
	right.y = -right.y;

	if (glm::length (forward) > 0.0001f) {
	    forward = glm::normalize (forward);
	} else {
	    // Default forward direction when not specified (up in centered space)
	    forward = glm::vec3 (0.0f, 1.0f, 0.0f);
	}
	if (glm::length (right) > 0.0001f) {
	    right = glm::normalize (right);
	} else {
	    right = glm::vec3 (1.0f, 0.0f, 0.0f);
	}

	float speed = WallpaperEngine::Maths::randomFloat (m_rng, speedMin->getFloat (), speedMax->getFloat ());
	float scale = scaleVal->getFloat ();
	float offset = offsetVal->getFloat ();
	float timeScale = timeScaleVal->getFloat ();
	float phaseMin = phaseMinVal->getFloat ();
	float phaseMax = phaseMaxVal->getFloat ();

	// Sample noise at particle position + time-based offset.
	// timescale shifts the noise field over time so particles spawned at different
	// times get gradually changing directions (creates smooth evolving vapor stream).
	// Position component provides spatial coherence for nearby particles.
	glm::vec3 noisePos = p.position * 0.1f;
	noisePos += glm::vec3 (static_cast<float> (m_sysTime) * timeScale);

	// Phase adds per-particle randomization to noise position
	float phase = WallpaperEngine::Maths::randomFloat (m_rng, phaseMin, phaseMax);
	glm::vec3 samplePos = noisePos + glm::vec3 (phase, phase * 0.7f, phase * 1.3f);

	// Sample curl noise for direction and normalize
	glm::vec3 result = curlNoise (samplePos);
	float len = glm::length (result);
	if (len < 0.0001f) {
	    result = forward;
	} else {
	    result = result / len;
	}

	// Scale limits how far direction can deviate from forward
	if (scale < 2.0f) {
	    float cosAngle = glm::dot (result, forward);
	    float angle = std::acos (glm::clamp (cosAngle, -1.0f, 1.0f)) / glm::pi<float> ();
	    float maxAngle = scale / 2.0f;

	    if (angle > maxAngle && maxAngle > 0.0001f) {
		glm::vec3 axis = glm::cross (result, forward);
		float axisLen = glm::length (axis);
		if (axisLen > 0.0001f) {
		    axis = axis / axisLen;
		    float rotAngle = (angle - maxAngle) * glm::pi<float> ();
		    glm::mat3 rot = glm::mat3 (glm::rotate (glm::mat4 (1.0f), rotAngle, axis));
		    result = rot * result;
		}
	    }
	}

	// Offset rotates result around right axis (tilts up/down)
	if (std::abs (offset) > 0.0001f) {
	    glm::mat3 rot = glm::mat3 (glm::rotate (glm::mat4 (1.0f), -offset, right));
	    result = rot * result;
	}

	// For 2D/orthographic particles (flags & 4 == 0), project direction onto XY plane.
	// curlNoise is 3D but z-drift is meaningless for 2D particles and causes
	// rope segments to diverge in depth, breaking visual connectivity.
	if ((m_particle.flags & 4) == 0) {
	    result.z = 0.0f;
	    float len2d = glm::length (result);
	    if (len2d > 0.0001f) {
		result /= len2d;
	    }
	}

	// Apply speed and instance override
	glm::vec3 finalVel = result * speed * speedOverride->getFloat () * m_inhSpeed;

	p.velocity += finalVel;
    };
}

InitializerFunc
CParticle::createMapSequenceAroundControlPointInitializer (const MapSequenceAroundControlPointInitializer& init) {
    DynamicValue* controlPointValue = init.controlPoint->value.get ();
    DynamicValue* countValue = init.count->value.get ();
    DynamicValue* speedMinValue = init.speedMin->value.get ();
    DynamicValue* speedMaxValue = init.speedMax->value.get ();
    DynamicValue* speedOverride = m_particle.instanceOverride.speed->value.get ();

    // Sequence counter shared across all particles spawned with this initializer
    // This creates the circular distribution pattern
    int sequenceIndex = 0;

    return [this, controlPointValue, countValue, speedMinValue, speedMaxValue, sequenceIndex,
	    speedOverride] (ParticleInstance& p) mutable {
	int controlPoint = static_cast<int> (controlPointValue->getFloat ());
	int count = std::max (1, static_cast<int> (countValue->getFloat ()));

	// Calculate angle for this particle in the sequence (evenly distributed around circle)
	float angle = (static_cast<float> (sequenceIndex) / static_cast<float> (count)) * glm::two_pi<float> ();
	sequenceIndex = (sequenceIndex + 1) % count; // Wrap around after reaching count

	// Get control point position to spawn around
	glm::vec3 centerPos = glm::vec3 (0.0f);
	if (controlPoint >= 0 && controlPoint < static_cast<int> (m_controlPoints.size ())) {
	    centerPos = m_controlPoints[controlPoint].position;
	}

	// Set particle position in circular pattern around control point
	// This creates the natural clustering seen in the original
	p.position = centerPos;

	// Set velocity based on angle and speed range
	glm::vec3 speedMin = speedMinValue->getVec3 ();
	glm::vec3 speedMax = speedMaxValue->getVec3 ();
	glm::vec3 speed = WallpaperEngine::Maths::randomVec3 (m_rng, speedMin, speedMax);

	// Flip Y before rotation to convert to centered space
	speed.y = -speed.y;

	// Rotate velocity based on sequence angle (creates outward radial pattern)
	glm::mat3 rotationMatrix = glm::mat3 (
	    std::cos (angle), -std::sin (angle), 0.0f, std::sin (angle), std::cos (angle), 0.0f, 0.0f, 0.0f, 1.0f
	);
	glm::vec3 rotatedSpeed = rotationMatrix * speed * speedOverride->getFloat () * m_inhSpeed;

	// Set velocity (speed override applied in movement operator)
	p.velocity = rotatedSpeed;
    };
}

// ========== OPERATORS ==========

void CParticle::setupOperators () {
    for (const auto& op : m_particle.operators) {
	if (!op) {
	    continue;
	}

	OperatorFunc func;

	if (op->is<MovementOperator> ()) {
	    func = createMovementOperator (*op->as<MovementOperator> ());
	} else if (op->is<AngularMovementOperator> ()) {
	    func = createAngularMovementOperator (*op->as<AngularMovementOperator> ());
	} else if (op->is<AlphaFadeOperator> ()) {
	    func = createAlphaFadeOperator (*op->as<AlphaFadeOperator> ());
	} else if (op->is<SizeChangeOperator> ()) {
	    func = createSizeChangeOperator (*op->as<SizeChangeOperator> ());
	} else if (op->is<AlphaChangeOperator> ()) {
	    func = createAlphaChangeOperator (*op->as<AlphaChangeOperator> ());
	} else if (op->is<ColorChangeOperator> ()) {
	    func = createColorChangeOperator (*op->as<ColorChangeOperator> ());
	} else if (op->is<TurbulenceOperator> ()) {
	    func = createTurbulenceOperator (*op->as<TurbulenceOperator> ());
	} else if (op->is<VortexOperator> ()) {
	    func = createVortexOperator (*op->as<VortexOperator> ());
	} else if (op->is<ControlPointAttractOperator> ()) {
	    func = createControlPointAttractOperator (*op->as<ControlPointAttractOperator> ());
	} else if (op->is<OscillateAlphaOperator> ()) {
	    func = createOscillateAlphaOperator (*op->as<OscillateAlphaOperator> ());
	} else if (op->is<OscillateSizeOperator> ()) {
	    func = createOscillateSizeOperator (*op->as<OscillateSizeOperator> ());
	} else if (op->is<OscillatePositionOperator> ()) {
	    func = createOscillatePositionOperator (*op->as<OscillatePositionOperator> ());
	} else {
	    sLog.out ("Unknown operator type");
	}

	if (func) {
	    m_operators.push_back (std::move (func));
	}
    }
}

OperatorFunc CParticle::createMovementOperator (const MovementOperator& op) {
    DynamicValue* dragValue = op.drag->value.get ();
    DynamicValue* gravityValue = op.gravity->value.get ();
    DynamicValue* speedOverride = m_particle.instanceOverride.speed->value.get ();

    return [this, dragValue, gravityValue, speedOverride, inhSpeed = m_inhSpeed] (
	       std::vector<ParticleInstance>& particles, uint32_t count, const std::vector<ControlPointData>&, float,
	       float dt
	   ) {
	float speed = speedOverride->getFloat () * inhSpeed;
	float drag = dragValue->getFloat () * 0.5f;
	glm::vec3 gravity = gravityValue->getVec3 ();
	// Flip gravity Y for centered space
	gravity.y = -gravity.y;

	const glm::vec3 objScale = rootSystem ()->m_particle.scale->value->getVec3 ();
	if (objScale.x != 0.0f) {
	    gravity.x /= objScale.x;
	}
	if (objScale.y != 0.0f) {
	    gravity.y /= objScale.y;
	}
	if (objScale.z != 0.0f) {
	    gravity.z /= objScale.z;
	}

	for (uint32_t i = 0; i < count; i++) {
	    auto& p = particles[i];
	    if (!p.alive) {
		continue;
	    }

	    // Update position FIRST using current velocity
	    // Velocity is already scaled by speed override
	    p.position += p.velocity * dt;

	    // Then apply forces to modify velocity for NEXT frame
	    // Apply gravity
	    p.velocity += gravity * dt * speed;

	    // Apply drag (velocity decay)
	    // Clamp to prevent velocity reversal if drag*dt > 1.0
	    float dragFactor = 1.0f - (drag * dt);
	    if (dragFactor < 0.0f) {
		dragFactor = 0.0f;
	    }
	    p.velocity *= dragFactor;
	}
    };
}

OperatorFunc CParticle::createAngularMovementOperator (const AngularMovementOperator& op) {
    DynamicValue* dragValue = op.drag->value.get ();
    DynamicValue* forceValue = op.force->value.get ();
    DynamicValue* speedOverride = m_particle.instanceOverride.speed->value.get ();

    return [dragValue, forceValue, speedOverride, inhSpeed = m_inhSpeed] (
	       std::vector<ParticleInstance>& particles, uint32_t count, const std::vector<ControlPointData>&, float,
	       float dt
	   ) {
	float drag = dragValue->getFloat ();
	float speed = speedOverride->getFloat () * inhSpeed;
	glm::vec3 force = forceValue->getVec3 ();

	for (uint32_t i = 0; i < count; i++) {
	    auto& p = particles[i];
	    if (!p.alive) {
		continue;
	    }

	    // Update rotation using current angular velocity
	    p.rotation += p.angularVelocity * dt * speed;

	    // Apply force (angular acceleration)
	    p.angularVelocity += force * dt * speed;

	    // Apply drag (angular velocity decay)
	    // Positive drag slows down, negative drag speeds up
	    // Clamp to prevent velocity reversal if drag*dt > 1.0
	    float dragFactor = 1.0f - (drag * dt);
	    if (dragFactor < 0.0f) {
		dragFactor = 0.0f;
	    }
	    p.angularVelocity *= dragFactor;

	    // Wrap rotation to prevent floating-point precision issues
	    const float pi = glm::pi<float> ();
	    const float two_pi = glm::two_pi<float> ();
	    for (int j = 0; j < 3; j++) {
		while (p.rotation[j] > pi) {
		    p.rotation[j] -= two_pi;
		}
		while (p.rotation[j] < -pi) {
		    p.rotation[j] += two_pi;
		}
	    }
	}
    };
}

OperatorFunc CParticle::createAlphaFadeOperator (const AlphaFadeOperator& op) {
    DynamicValue* fadeInTimeValue = op.fadeInTime->value.get ();
    DynamicValue* fadeOutTimeValue = op.fadeOutTime->value.get ();

    return
	[fadeInTimeValue, fadeOutTimeValue] (
	    std::vector<ParticleInstance>& particles, uint32_t count, const std::vector<ControlPointData>&, float, float
	) {
	    float fadeInTime = fadeInTimeValue->getFloat ();
	    float fadeOutTime = fadeOutTimeValue->getFloat ();

	    for (uint32_t i = 0; i < count; i++) {
		auto& p = particles[i];
		if (!p.alive) {
		    continue;
		}

		float life = p.getLifetimePos ();

		if (life <= fadeInTime) {
		    float fade = WallpaperEngine::Maths::fadeValue (life, 0.0f, fadeInTime, 0.0f, 1.0f);
		    p.alpha = p.initial.alpha * fade;
		} else if (life > fadeOutTime) {
		    float fade = 1.0f - WallpaperEngine::Maths::fadeValue (life, fadeOutTime, 1.0f, 0.0f, 1.0f);
		    p.alpha = p.initial.alpha * fade;
		} else {
		    p.alpha = p.initial.alpha;
		}

		// Update oscillator base so oscillateAlpha combines properly
		p.oscillateAlpha.base = p.alpha;
	    }
	};
}

OperatorFunc CParticle::createSizeChangeOperator (const SizeChangeOperator& op) {
    DynamicValue* startTimeValue = op.startTime->value.get ();
    DynamicValue* endTimeValue = op.endTime->value.get ();
    DynamicValue* startValueValue = op.startValue->value.get ();
    DynamicValue* endValueValue = op.endValue->value.get ();

    return
	[startTimeValue, endTimeValue, startValueValue, endValueValue] (
	    std::vector<ParticleInstance>& particles, uint32_t count, const std::vector<ControlPointData>&, float, float
	) {
	    float startTime = startTimeValue->getFloat ();
	    float endTime = endTimeValue->getFloat ();
	    float startValue = startValueValue->getFloat ();
	    float endValue = endValueValue->getFloat ();

	    for (uint32_t i = 0; i < count; i++) {
		auto& p = particles[i];
		if (!p.alive) {
		    continue;
		}

		float life = p.getLifetimePos ();
		float multiplier = WallpaperEngine::Maths::fadeValue (life, startTime, endTime, startValue, endValue);
		p.size = p.initial.size * multiplier;

		// Update oscillator base so oscillateSize combines properly
		p.oscillateSize.base = p.size;
	    }
	};
}

OperatorFunc CParticle::createAlphaChangeOperator (const AlphaChangeOperator& op) {
    DynamicValue* startTimeValue = op.startTime->value.get ();
    DynamicValue* endTimeValue = op.endTime->value.get ();
    DynamicValue* startValueValue = op.startValue->value.get ();
    DynamicValue* endValueValue = op.endValue->value.get ();

    return
	[startTimeValue, endTimeValue, startValueValue, endValueValue] (
	    std::vector<ParticleInstance>& particles, uint32_t count, const std::vector<ControlPointData>&, float, float
	) {
	    float startTime = startTimeValue->getFloat ();
	    float endTime = endTimeValue->getFloat ();
	    float startValue = startValueValue->getFloat ();
	    float endValue = endValueValue->getFloat ();

	    for (uint32_t i = 0; i < count; i++) {
		auto& p = particles[i];
		if (!p.alive) {
		    continue;
		}

		float life = p.getLifetimePos ();
		float multiplier = WallpaperEngine::Maths::fadeValue (life, startTime, endTime, startValue, endValue);
		p.alpha = p.initial.alpha * multiplier;

		// Update oscillator base so oscillateAlpha combines properly
		p.oscillateAlpha.base = p.alpha;
	    }
	};
}

OperatorFunc CParticle::createColorChangeOperator (const ColorChangeOperator& op) {
    DynamicValue* startTimeValue = op.startTime->value.get ();
    DynamicValue* endTimeValue = op.endTime->value.get ();
    DynamicValue* startValueValue = op.startValue->value.get ();
    DynamicValue* endValueValue = op.endValue->value.get ();

    return
	[startTimeValue, endTimeValue, startValueValue, endValueValue] (
	    std::vector<ParticleInstance>& particles, uint32_t count, const std::vector<ControlPointData>&, float, float
	) {
	    float startTime = startTimeValue->getFloat ();
	    float endTime = endTimeValue->getFloat ();
	    glm::vec3 startValue = startValueValue->getVec3 ();
	    glm::vec3 endValue = endValueValue->getVec3 ();

	    for (uint32_t i = 0; i < count; i++) {
		auto& p = particles[i];
		if (!p.alive) {
		    continue;
		}

		float life = p.getLifetimePos ();

		glm::vec3 color;
		color.r = WallpaperEngine::Maths::fadeValue (life, startTime, endTime, startValue.r, endValue.r);
		color.g = WallpaperEngine::Maths::fadeValue (life, startTime, endTime, startValue.g, endValue.g);
		color.b = WallpaperEngine::Maths::fadeValue (life, startTime, endTime, startValue.b, endValue.b);

		p.color = p.initial.color * color;
	    }
	};
}

OperatorFunc CParticle::createTurbulenceOperator (const TurbulenceOperator& op) {
    DynamicValue* scaleValue = op.scale->value.get ();
    DynamicValue* speedMinValue = op.speedMin->value.get ();
    DynamicValue* speedMaxValue = op.speedMax->value.get ();
    DynamicValue* timeScaleValue = op.timeScale->value.get ();
    DynamicValue* maskValue = op.mask->value.get ();
    DynamicValue* phaseMinValue = op.phaseMin->value.get ();
    DynamicValue* phaseMaxValue = op.phaseMax->value.get ();
    DynamicValue* speedOverride = m_particle.instanceOverride.speed->value.get ();

    // TODO: Audio processing support
    // DynamicValue* audioModeValue = op.audioProcessingMode->value.get ();
    // DynamicValue* audioBoundsValue = op.audioProcessingBounds->value.get ();
    // DynamicValue* audioExponentValue = op.audioProcessingExponent->value.get ();
    // DynamicValue* audioFreqStartValue = op.audioProcessingFrequencyStart->value.get ();
    // DynamicValue* audioFreqEndValue = op.audioProcessingFrequencyEnd->value.get ();

    const float phase
	= WallpaperEngine::Maths::randomFloat (m_rng, phaseMinValue->getFloat (), phaseMaxValue->getFloat ());

    return [scaleValue, timeScaleValue, speedMinValue, speedMaxValue, maskValue, speedOverride, phase,
	    inhSpeed = m_inhSpeed] (
	       std::vector<ParticleInstance>& particles, uint32_t count, const std::vector<ControlPointData>&,
	       float currentTime, float dt
	   ) {
	const float noiseScale = scaleValue->getFloat () * 2.0f;
	const float timeScale = timeScaleValue->getFloat ();
	const glm::vec3 mask = maskValue->getVec3 ();
	const float speed = speedOverride->getFloat () * inhSpeed;
	const float speedMin = speedMinValue->getFloat ();
	const float speedMax = speedMaxValue->getFloat ();

	if (std::max (speedMin, speedMax) <= 0.0001f) {
	    return;
	}

	for (size_t i = 0; i < count; ++i) {
	    ParticleInstance& p = particles[i];
	    if (!p.alive) {
		continue;
	    }

	    const float slotRandom = glm::fract (std::sin (static_cast<float> (i) * 12.9898f) * 43758.5453f);
	    const float turbSpeed = speedMin + (speedMax - speedMin) * slotRandom;

	    glm::vec3 noisePos = p.position;
	    noisePos.x += phase;
	    noisePos *= noiseScale;
	    noisePos.z += timeScale * currentTime * noiseScale;

	    glm::vec3 curlDir = curlNoise (noisePos);
	    const float len = glm::length (curlDir);
	    if (len > 0.0001f) {
		curlDir = (curlDir / len) * turbSpeed;
	    }

	    curlDir *= mask;
	    p.velocity += curlDir * dt * speed;
	}
    };
}

OperatorFunc CParticle::createVortexOperator (const VortexOperator& op) {
    int controlPoint = op.controlPoint;
    int flags = op.flags;
    DynamicValue* axisValue = op.axis->value.get ();
    DynamicValue* offsetValue = op.offset->value.get ();
    DynamicValue* distanceInnerValue = op.distanceInner->value.get ();
    DynamicValue* distanceOuterValue = op.distanceOuter->value.get ();
    DynamicValue* speedInnerValue = op.speedInner->value.get ();
    DynamicValue* speedOuterValue = op.speedOuter->value.get ();
    DynamicValue* centerForceValue = op.centerForce->value.get ();
    DynamicValue* ringRadiusValue = op.ringRadius->value.get ();
    DynamicValue* ringWidthValue = op.ringWidth->value.get ();
    DynamicValue* ringPullDistanceValue = op.ringPullDistance->value.get ();
    DynamicValue* ringPullForceValue = op.ringPullForce->value.get ();
    DynamicValue* audioModeValue = op.audioProcessingMode->value.get ();
    DynamicValue* speedOverride = m_particle.instanceOverride.speed->value.get ();

    // Check if audio processing is enabled
    int audioMode = static_cast<int> (audioModeValue->getFloat ());

    // Extract flag bits
    bool infiniteAxis = (flags & 1) != 0;
    bool maintainDistance = (flags & 2) != 0;
    bool ringShape = (flags & 4) != 0;

    return
	[controlPoint, axisValue, offsetValue, distanceInnerValue, distanceOuterValue, speedInnerValue, speedOuterValue,
	 centerForceValue, ringRadiusValue, ringWidthValue, ringPullDistanceValue, ringPullForceValue, audioMode,
	 infiniteAxis, maintainDistance, ringShape, speedOverride, inhSpeed = m_inhSpeed] (
	    std::vector<ParticleInstance>& particles, uint32_t count,
	    const std::vector<ControlPointData>& controlPoints, float, float dt
	) {
	    // Audio modulation (when implemented, this will sample from audio context)
	    float audioAmplitude = 0.0f; // TODO: Sample from AudioContext when audio processing is implemented

	    // If audio mode is enabled but no audio, skip vortex entirely
	    if (audioMode > 0 && audioAmplitude == 0.0f) {
		return;
	    }

	    glm::vec3 axis = axisValue->getVec3 ();
	    glm::vec3 offset = offsetValue->getVec3 ();
	    float distanceInner = distanceInnerValue->getFloat ();
	    float distanceOuter = distanceOuterValue->getFloat ();
	    float speedInner = speedInnerValue->getFloat ();
	    float speedOuter = speedOuterValue->getFloat ();
	    float centerForce = centerForceValue->getFloat ();
	    float ringRadius = ringRadiusValue->getFloat ();
	    float ringWidth = ringWidthValue->getFloat ();
	    float ringPullDistance = ringPullDistanceValue->getFloat ();
	    float ringPullForce = ringPullForceValue->getFloat ();

	    // Apply audio modulation to speeds
	    if (audioMode > 0) {
		speedInner *= (1.0f + audioAmplitude);
		speedOuter *= (1.0f + audioAmplitude);
	    }

	    // Get vortex center from control point
	    glm::vec3 center = glm::vec3 (0.0f);
	    if (controlPoint >= 0 && controlPoint < static_cast<int> (controlPoints.size ())) {
		center = controlPoints[controlPoint].position + offset;
	    } else {
		center = offset;
	    }

	    // Normalize axis
	    if (glm::length (axis) > 0.0f) {
		axis = glm::normalize (axis);
	    } else {
		axis = glm::vec3 (0.0f, 0.0f, 1.0f); // Default to Z-axis
	    }

	    for (uint32_t i = 0; i < count; i++) {
		auto& p = particles[i];
		if (!p.alive) {
		    continue;
		}

		// Calculate vector from center to particle
		glm::vec3 toParticle = p.position - center;

		// For infinite axis mode, project onto plane perpendicular to axis (cylinder shape)
		// Otherwise use full 3D distance (sphere shape)
		float axialDistance = 0.0f;
		glm::vec3 radialVector = toParticle;
		if (infiniteAxis) {
		    // Project out the axis component
		    axialDistance = glm::dot (toParticle, axis);
		    radialVector = toParticle - axis * axialDistance;
		}

		float distance = glm::length (radialVector);

		// Compute tangent direction (perpendicular to both axis and radial vector)
		glm::vec3 tangent = glm::cross (axis, radialVector);
		if (glm::length (tangent) > 0.001f) {
		    tangent = glm::normalize (tangent);
		} else {
		    continue; // Particle is on the axis
		}

		// Calculate spin speed and apply forces based on mode
		float speed = 0.0f;
		glm::vec3 radialForce = glm::vec3 (0.0f);

		if (ringShape) {
		    // Ring mode: hollow center with ring-shaped influence zone
		    float ringInner = ringRadius - ringWidth * 0.5f;
		    float ringOuter = ringRadius + ringWidth * 0.5f;

		    if (distance < ringInner) {
			// Inside the ring's hollow center - no spin, but may be pulled outward
			speed = 0.0f;
		    } else if (distance <= ringOuter) {
			// Inside the ring - full effect
			float t = (distance - ringInner) / ringWidth;
			speed = glm::mix (speedInner, speedOuter, t);
		    } else if (distance <= ringOuter + ringPullDistance) {
			// Outside ring but within pull distance - attract toward ring
			float pullT = (distance - ringOuter) / ringPullDistance;
			speed = speedOuter * (1.0f - pullT);
			// Pull toward ring
			if (distance > 0.001f) {
			    glm::vec3 towardRing = -glm::normalize (radialVector);
			    radialForce = towardRing * ringPullForce * pullT;
			}
		    } else {
			// Too far from ring - no effect
			speed = 0.0f;
		    }
		} else {
		    // Standard vortex mode
		    float disMid = distanceOuter - distanceInner + 0.1f;

		    if (disMid < 0 || distance < distanceInner) {
			speed = speedInner;
		    } else if (distance > distanceOuter) {
			speed = speedOuter;
		    } else {
			float t = (distance - distanceInner) / disMid;
			speed = glm::mix (speedInner, speedOuter, t);
		    }
		}

		// Apply tangential velocity (spinning)
		p.velocity += tangent * speed * dt * speedOverride->getFloat () * inhSpeed;

		// Apply radial force (ring pull)
		p.velocity += radialForce * dt * speedOverride->getFloat () * inhSpeed;

		// Apply center force when maintain distance is enabled
		if (maintainDistance && distance > 0.001f) {
		    glm::vec3 towardCenter = -glm::normalize (radialVector);
		    p.velocity += towardCenter * centerForce * dt * speedOverride->getFloat () * inhSpeed;
		}
	    }
	};
}

OperatorFunc CParticle::createControlPointAttractOperator (const ControlPointAttractOperator& op) {
    int controlPoint = op.controlPoint;
    DynamicValue* originValue = op.origin->value.get ();
    DynamicValue* scaleValue = op.scale->value.get ();
    DynamicValue* thresholdValue = op.threshold->value.get ();
    DynamicValue* speedOverride = m_particle.instanceOverride.speed->value.get ();

    return [controlPoint, originValue, scaleValue, thresholdValue, speedOverride, inhSpeed = m_inhSpeed] (
	       std::vector<ParticleInstance>& particles, uint32_t count,
	       const std::vector<ControlPointData>& controlPoints, float currentTime, float dt
	   ) {
	// Get dynamic values
	glm::vec3 origin = originValue->getVec3 ();
	float scale = scaleValue->getFloat ();
	float threshold = thresholdValue->getFloat ();

	// Get control point position
	if (controlPoint < 0 || controlPoint >= static_cast<int> (controlPoints.size ())) {
	    return;
	}

	glm::vec3 center = controlPoints[controlPoint].position + origin;

	// Apply attraction force to all particles within threshold
	for (uint32_t i = 0; i < count; i++) {
	    auto& p = particles[i];
	    if (!p.alive) {
		continue;
	    }

	    // Calculate distance and direction to control point
	    glm::vec3 toCenter = center - p.position;
	    float distance = glm::length (toCenter);

	    // Only apply force if within threshold
	    if (distance > 0.001f && distance < threshold) {
		// Normalize direction
		glm::vec3 direction = toCenter / distance;

		// Apply constant force in direction of control point
		glm::vec3 forceVec = direction * scale * dt;
		p.velocity += forceVec * speedOverride->getFloat () * inhSpeed;
	    }
	}
    };
}

OperatorFunc CParticle::createOscillateAlphaOperator (const OscillateAlphaOperator& op) {
    DynamicValue* freqMinValue = op.frequencyMin->value.get ();
    DynamicValue* freqMaxValue = op.frequencyMax->value.get ();
    DynamicValue* scaleMinValue = op.scaleMin->value.get ();
    DynamicValue* scaleMaxValue = op.scaleMax->value.get ();
    DynamicValue* phaseMinValue = op.phaseMin->value.get ();
    DynamicValue* phaseMaxValue = op.phaseMax->value.get ();

    return
	[this, freqMinValue, freqMaxValue, scaleMinValue, scaleMaxValue, phaseMinValue, phaseMaxValue] (
	    std::vector<ParticleInstance>& particles, uint32_t count, const std::vector<ControlPointData>&, float, float
	) {
	    float freqMin = freqMinValue->getFloat ();
	    float freqMax = freqMaxValue->getFloat ();
	    float scaleMin = scaleMinValue->getFloat ();
	    float scaleMax = scaleMaxValue->getFloat ();
	    float phaseMin = phaseMinValue->getFloat ();
	    float phaseMax = phaseMaxValue->getFloat ();

	    for (uint32_t i = 0; i < count; i++) {
		auto& p = particles[i];

		// Initialize per-particle oscillator values on first use
		if (!p.oscillateAlpha.initialized) {
		    p.oscillateAlpha.frequency = WallpaperEngine::Maths::randomFloat (m_rng, freqMin, freqMax);
		    p.oscillateAlpha.scale = WallpaperEngine::Maths::randomFloat (m_rng, scaleMin, scaleMax);
		    p.oscillateAlpha.phase
			= WallpaperEngine::Maths::randomFloat (m_rng, phaseMin, phaseMax + 2.0f * glm::pi<float> ());
		    p.oscillateAlpha.base = p.alpha; // Capture initial base
		    p.oscillateAlpha.initialized = true;
		}

		float t = p.age;
		float multiplier;
		if (freqMin >= 1.0f) {
		    float w = p.oscillateAlpha.frequency * (glm::two_pi<float> () / 60.0f);
		    float cosVal = (std::cos (w * t + p.oscillateAlpha.phase) + 1.0f) * 0.5f;
		    multiplier = std::max (scaleMin, scaleMax * cosVal);
		} else {
		    float w = p.oscillateAlpha.frequency;
		    float cosVal = (std::cos (w * t + p.oscillateAlpha.phase) + 1.0f) * 0.5f;
		    multiplier = glm::mix (scaleMin, scaleMax, cosVal);
		}

		// Apply to base value (alphafade updates base each frame if present)
		p.alpha = p.oscillateAlpha.base * multiplier;
	    }
	};
}

OperatorFunc CParticle::createOscillateSizeOperator (const OscillateSizeOperator& op) {
    DynamicValue* freqMinValue = op.frequencyMin->value.get ();
    DynamicValue* freqMaxValue = op.frequencyMax->value.get ();
    DynamicValue* scaleMinValue = op.scaleMin->value.get ();
    DynamicValue* scaleMaxValue = op.scaleMax->value.get ();
    DynamicValue* phaseMinValue = op.phaseMin->value.get ();
    DynamicValue* phaseMaxValue = op.phaseMax->value.get ();

    return
	[this, freqMinValue, freqMaxValue, scaleMinValue, scaleMaxValue, phaseMinValue, phaseMaxValue] (
	    std::vector<ParticleInstance>& particles, uint32_t count, const std::vector<ControlPointData>&, float, float
	) {
	    float freqMin = freqMinValue->getFloat ();
	    float freqMax = freqMaxValue->getFloat ();
	    float scaleMin = scaleMinValue->getFloat ();
	    float scaleMax = scaleMaxValue->getFloat ();
	    float phaseMin = phaseMinValue->getFloat ();
	    float phaseMax = phaseMaxValue->getFloat ();

	    for (uint32_t i = 0; i < count; i++) {
		auto& p = particles[i];

		// Initialize per-particle oscillator values on first use
		if (!p.oscillateSize.initialized) {
		    p.oscillateSize.frequency = WallpaperEngine::Maths::randomFloat (m_rng, freqMin, freqMax);
		    p.oscillateSize.scale = WallpaperEngine::Maths::randomFloat (m_rng, scaleMin, scaleMax);
		    p.oscillateSize.phase
			= WallpaperEngine::Maths::randomFloat (m_rng, phaseMin, phaseMax + 2.0f * glm::pi<float> ());
		    p.oscillateSize.base = p.size; // Capture initial base
		    p.oscillateSize.initialized = true;
		}

		// Calculate oscillation: interpolate between scaleMin and scaleMax using cosine wave
		float w = p.oscillateSize.frequency;
		float t = p.age;
		float cosVal = (std::cos (w * t + p.oscillateSize.phase) + 1.0f) * 0.5f;
		float multiplier = glm::mix (scaleMin, scaleMax, cosVal);

		// Apply to base value (sizeChange updates base each frame if present)
		p.size = p.oscillateSize.base * multiplier;
	    }
	};
}

OperatorFunc CParticle::createOscillatePositionOperator (const OscillatePositionOperator& op) {
    DynamicValue* freqMinValue = op.frequencyMin->value.get ();
    DynamicValue* freqMaxValue = op.frequencyMax->value.get ();
    DynamicValue* scaleMinValue = op.scaleMin->value.get ();
    DynamicValue* scaleMaxValue = op.scaleMax->value.get ();
    DynamicValue* phaseMinValue = op.phaseMin->value.get ();
    DynamicValue* phaseMaxValue = op.phaseMax->value.get ();
    DynamicValue* maskValue = op.mask->value.get ();

    return [this, freqMinValue, freqMaxValue, scaleMinValue, scaleMaxValue, phaseMinValue, phaseMaxValue, maskValue] (
	       std::vector<ParticleInstance>& particles, uint32_t count, const std::vector<ControlPointData>&, float,
	       float
	   ) {
	float freqMin = freqMinValue->getFloat ();
	float freqMax = freqMaxValue->getFloat ();
	float scaleMin = scaleMinValue->getFloat ();
	float scaleMax = scaleMaxValue->getFloat ();
	float phaseMin = phaseMinValue->getFloat ();
	float phaseMax = phaseMaxValue->getFloat ();
	glm::vec3 mask = maskValue->getVec3 ();

	for (uint32_t i = 0; i < count; i++) {
	    auto& p = particles[i];

	    // Initialize per-particle oscillator values on first use (per axis)
	    if (!p.oscillatePosition.initialized) {
		for (int axis = 0; axis < 3; axis++) {
		    p.oscillatePosition.frequency[axis] = WallpaperEngine::Maths::randomFloat (m_rng, freqMin, freqMax);
		    p.oscillatePosition.scale[axis] = WallpaperEngine::Maths::randomFloat (m_rng, scaleMin, scaleMax);
		    p.oscillatePosition.phase[axis]
			= WallpaperEngine::Maths::randomFloat (m_rng, phaseMin, phaseMax + 2.0f * glm::pi<float> ());
		}
		p.oscillatePosition.lastOffset = glm::vec3 (0.0f);
		p.oscillatePosition.initialized = true;
	    }

	    const float t = p.age;
	    glm::vec3 delta (0.0f);

	    for (int axis = 0; axis < 3; axis++) {
		const float w = p.oscillatePosition.frequency[axis];
		const float offset
		    = p.oscillatePosition.scale[axis] * std::cos (w * t + p.oscillatePosition.phase[axis]);
		// Apply mask as bias multiplier for this axis
		delta[axis] = (offset - p.oscillatePosition.lastOffset[axis]) * mask[axis];
		p.oscillatePosition.lastOffset[axis] = offset;
	    }

	    p.position += delta;
	}
    };
}

// ========== RENDERING ==========

void CParticle::setupPass () {
    if (!m_particle.material || !m_particle.material->material || m_particle.material->material->passes.empty ()) {
	sLog.error ("No valid material for particle ", m_particle.name);
	return;
    }

    const auto& firstPass = **m_particle.material->material->passes.begin ();

    // Build override with particle-specific combos
    m_passOverride = std::make_unique<ImageEffectPassOverride> ();
    m_passOverride->combos["THICKFORMAT"] = 1;
    if (m_useRopeRenderer) {
	m_passOverride->shaderOverride = "genericropeparticle";
    }
    if (m_spritesheetFrames > 0) {
	m_passOverride->combos["SPRITESHEET"] = 1;
	m_passOverride->combos["SPRITESHEETBLEND"] = 1;
    }
    if (m_useTrailRenderer) {
	m_passOverride->combos["TRAILRENDERER"] = 1;
    }

    // Force texture 0 to use the input (particle texture) rather than the shader's
    // default "util/white" annotation, which would override it in setupRenderTexture()
    m_passBinds = { { 0, "previous" } };

    // Check if material uses REFRACT combo
    auto refractIt = firstPass.combos.find ("REFRACT");
    m_hasRefract = refractIt != firstPass.combos.end () && refractIt->second != 0;

    // Create the FBO provider for CPass
    m_passFBOProvider = std::make_shared<FBOProvider> (this);

    // For REFRACT: create a copy FBO that shadows _rt_FullFrameBuffer.
    // The REFRACT shader reads g_Texture3 (= _rt_FullFrameBuffer) while we render TO the scene FBO.
    // Reading from the same FBO being rendered to is undefined behavior in OpenGL, causing
    // black reads on NVIDIA. By placing a copy FBO with the same name in our FBOProvider,
    // CPass resolves g_Texture3 to the copy instead. We blit the scene content before each render.
    if (m_hasRefract) {
	auto sceneFBO = getScene ().getActiveRenderTarget ();
	float w = static_cast<float> (sceneFBO->getRealWidth ());
	float h = static_cast<float> (sceneFBO->getRealHeight ());
	m_refractFBO = m_passFBOProvider->create (
	    "_rt_FullFrameBuffer", TextureFormat_ARGB8888, TextureFlags_ClampUVs, 1.0f, { w, h }, { w, h }
	);
    }

    // Create CPass with the WP particle shader
    m_pass = new Effects::CPass (*this, m_passFBOProvider, firstPass, *m_passOverride, m_passBinds, std::nullopt);

    // Set destination to scene FBO and input to particle texture
    m_pass->setDestination (getScene ().getFBO ());
    m_pass->setInput (getTexture ());

    // Set matrix pointers - CPass will dereference these each frame
    m_pass->setModelViewProjectionMatrix (&m_mvpMatrix);
    m_pass->setModelViewProjectionMatrixInverse (&m_mvpMatrixInverse);
    m_pass->setModelMatrix (&m_modelMatrix);
    m_pass->setViewProjectionMatrix (&m_viewProjectionMatrix);

    // Create OpenGL buffers
    GLint prevVAO = 0;
    glGetIntegerv (GL_VERTEX_ARRAY_BINDING, &prevVAO);

    glGenVertexArrays (1, &m_vao);
    glGenBuffers (1, &m_vbo);
    glGenBuffers (1, &m_ebo);

    glBindVertexArray (m_vao);
    glBindBuffer (GL_ARRAY_BUFFER, m_vbo);
    glBindBuffer (GL_ELEMENT_ARRAY_BUFFER, m_ebo);

    // Size both buffers once to the authored worst case (m_vertices/m_indices are
    // already pooled to maxcount by the constructor). Across a heavy scene this is
    // ~36 MiB total and flat, versus a re-spec per system per frame.
    m_vboCapacity = static_cast<GLsizeiptr> (m_vertices.size () * sizeof (float));
    m_eboCapacity = static_cast<GLsizeiptr> (m_indices.size () * sizeof (uint32_t));
    glBufferData (GL_ARRAY_BUFFER, m_vboCapacity, nullptr, GL_DYNAMIC_DRAW);
    glBufferData (GL_ELEMENT_ARRAY_BUFFER, m_eboCapacity, nullptr, GL_DYNAMIC_DRAW);

    const GLuint program = m_pass->getProgramID ();

    if (m_useRopeRenderer) {
	// Rope vertex layout: 7 attributes, 26 floats/vertex, stride=104 bytes
	// a_PositionVec4(4) + a_TexCoordVec4(4) + a_TexCoordVec4C1(4) + a_TexCoordVec4C2(4)
	// + a_TexCoordVec4C3(4) + a_TexCoordC4(2) + a_Color(4) = 26
	const GLsizei stride = sizeof (float) * ROPE_FLOATS_PER_VERTEX;

	const GLint loc0 = glGetAttribLocation (program, "a_PositionVec4");
	const GLint loc1 = glGetAttribLocation (program, "a_TexCoordVec4");
	const GLint loc2 = glGetAttribLocation (program, "a_TexCoordVec4C1");
	const GLint loc3 = glGetAttribLocation (program, "a_TexCoordVec4C2");
	const GLint loc4 = glGetAttribLocation (program, "a_TexCoordVec4C3");
	const GLint loc5 = glGetAttribLocation (program, "a_TexCoordC4");
	const GLint loc6 = glGetAttribLocation (program, "a_Color");

	if (loc0 >= 0) {
	    glEnableVertexAttribArray (loc0);
	    glVertexAttribPointer (loc0, 4, GL_FLOAT, GL_FALSE, stride, (void*)(sizeof (float) * 0));
	}
	if (loc1 >= 0) {
	    glEnableVertexAttribArray (loc1);
	    glVertexAttribPointer (loc1, 4, GL_FLOAT, GL_FALSE, stride, (void*)(sizeof (float) * 4));
	}
	if (loc2 >= 0) {
	    glEnableVertexAttribArray (loc2);
	    glVertexAttribPointer (loc2, 4, GL_FLOAT, GL_FALSE, stride, (void*)(sizeof (float) * 8));
	}
	if (loc3 >= 0) {
	    glEnableVertexAttribArray (loc3);
	    glVertexAttribPointer (loc3, 4, GL_FLOAT, GL_FALSE, stride, (void*)(sizeof (float) * 12));
	}
	if (loc4 >= 0) {
	    glEnableVertexAttribArray (loc4);
	    glVertexAttribPointer (loc4, 4, GL_FLOAT, GL_FALSE, stride, (void*)(sizeof (float) * 16));
	}
	if (loc5 >= 0) {
	    glEnableVertexAttribArray (loc5);
	    glVertexAttribPointer (loc5, 2, GL_FLOAT, GL_FALSE, stride, (void*)(sizeof (float) * 20));
	}
	if (loc6 >= 0) {
	    glEnableVertexAttribArray (loc6);
	    glVertexAttribPointer (loc6, 4, GL_FLOAT, GL_FALSE, stride, (void*)(sizeof (float) * 22));
	}
    } else {
	// Sprite vertex layout: 5 attributes, 17 floats/vertex, stride=68 bytes
	// a_Position(3) + a_TexCoordVec4(4) + a_Color(4) + a_TexCoordVec4C1(4) + a_TexCoordC2(2) = 17
	const GLsizei stride = sizeof (float) * SPRITE_FLOATS_PER_VERTEX;

	const GLint loc0 = glGetAttribLocation (program, "a_Position");
	const GLint loc1 = glGetAttribLocation (program, "a_TexCoordVec4");
	const GLint loc2 = glGetAttribLocation (program, "a_Color");
	const GLint loc3 = glGetAttribLocation (program, "a_TexCoordVec4C1");
	const GLint loc4 = glGetAttribLocation (program, "a_TexCoordC2");

	if (loc0 >= 0) {
	    glEnableVertexAttribArray (loc0);
	    glVertexAttribPointer (loc0, 3, GL_FLOAT, GL_FALSE, stride, (void*)(sizeof (float) * 0));
	}
	if (loc1 >= 0) {
	    glEnableVertexAttribArray (loc1);
	    glVertexAttribPointer (loc1, 4, GL_FLOAT, GL_FALSE, stride, (void*)(sizeof (float) * 3));
	}
	if (loc2 >= 0) {
	    glEnableVertexAttribArray (loc2);
	    glVertexAttribPointer (loc2, 4, GL_FLOAT, GL_FALSE, stride, (void*)(sizeof (float) * 7));
	}
	if (loc3 >= 0) {
	    glEnableVertexAttribArray (loc3);
	    glVertexAttribPointer (loc3, 4, GL_FLOAT, GL_FALSE, stride, (void*)(sizeof (float) * 11));
	}
	if (loc4 >= 0) {
	    glEnableVertexAttribArray (loc4);
	    glVertexAttribPointer (loc4, 2, GL_FLOAT, GL_FALSE, stride, (void*)(sizeof (float) * 15));
	}
    }

    glBindVertexArray (prevVAO);

    setupGeometryCallbacks ();
    setupParticleUniforms ();
}

void CParticle::setupGeometryCallbacks () {
    m_pass->setGeometryCallback (
	// Setup attribs: save current VAO, bind particle VAO
	[this] () {
	    glGetIntegerv (GL_VERTEX_ARRAY_BINDING, &m_prevVAO);
	    glBindVertexArray (m_vao);
	},
	// Draw geometry: indexed rendering
	[this] () { glDrawElements (GL_TRIANGLES, m_activeIndexCount, GL_UNSIGNED_INT, nullptr); },
	// Cleanup: restore previous VAO
	[this] () { glBindVertexArray (m_prevVAO); }
    );
}

void CParticle::setupParticleUniforms () {
    // Add particle-specific uniforms from common_particles.h that CPass doesn't provide
    // These are pointer-based: CPass reads the current value each frame
    m_pass->addUniform ("g_ModelMatrixInverse", &m_modelMatrixInverse);
    m_pass->addUniform ("g_OrientationUp", &m_orientationUp);
    m_pass->addUniform ("g_OrientationRight", &m_orientationRight);
    m_pass->addUniform ("g_OrientationForward", &m_orientationForward);
    m_pass->addUniform ("g_ViewUp", &m_viewUp);
    m_pass->addUniform ("g_ViewRight", &m_viewRight);
    m_pass->addUniform ("g_EyePosition", &m_eyePosition);
    m_pass->addUniform ("g_RenderVar0", &m_renderVar0);
    m_pass->addUniform ("g_RenderVar1", &m_renderVar1);
    m_pass->addUniform ("g_LWEAxisComp", &m_axisComp);

    // REFRACT: set g_RefractAmount (shader default 0.05, may not be applied by CPass's parameter system)
    if (m_hasRefract) {
	m_pass->addUniform ("g_RefractAmount", &m_refractAmount);
    }
}

void CParticle::updateMatrices () {
    // Child systems live in the parent's local space: the whole transform chain
    // (origin/parallax/rotation/scale) comes from the ROOT system so anchors taken from
    // parent particle positions land exactly where the parent draws them
    const CParticle* src = rootSystem ();

    glm::vec3 scale = src->m_particle.scale->value->getVec3 ();
    glm::vec3 angles = src->m_particle.angles->value->getVec3 ();

    m_modelMatrix = glm::mat4 (1.0f);
    m_modelMatrix = glm::translate (m_modelMatrix, src->m_transformedOrigin);
    src->applyParallaxToModelMatrix (m_modelMatrix);

    // Negate X and Z rotations to account for Y-flipped coordinate system
    m_modelMatrix = glm::rotate (m_modelMatrix, -angles.z, glm::vec3 (0, 0, 1));
    m_modelMatrix = glm::rotate (m_modelMatrix, angles.y, glm::vec3 (0, 1, 0));
    m_modelMatrix = glm::rotate (m_modelMatrix, -angles.x, glm::vec3 (1, 0, 0));
    m_modelMatrix = glm::scale (m_modelMatrix, scale);
    m_modelMatrixInverse = glm::inverse (m_modelMatrix);

    this->updateParticleViewProjection ();
    m_mvpMatrix = m_viewProjectionMatrix * m_modelMatrix;
    m_mvpMatrixInverse = glm::inverse (m_mvpMatrix);

    static const bool s_noVFlip = getenv ("LWE_NOSPRITEVFLIP") != nullptr;
    m_orientationUp = glm::vec3 (0.0f, s_noVFlip ? 1.0f : -1.0f, 0.0f);
    m_orientationRight = glm::vec3 (1.0f, 0.0f, 0.0f);
    m_orientationForward = glm::vec3 (0.0f, 0.0f, 1.0f);
    m_viewUp = glm::vec3 (0.0f, 1.0f, 0.0f);
    m_viewRight = glm::vec3 (1.0f, 0.0f, 0.0f);

    {
	const float ax = std::abs (scale.x), ay = std::abs (scale.y);
	const float m = worldSizeDivisor ();
	const bool child = isChildSystem ();
	static const bool s_noChildRide = getenv ("LWE_NOCHILDRIDE") != nullptr;
	const bool eventFollow = child && m_link != nullptr && m_link->type == "eventfollow";
	const bool anchored = eventFollow || (m_particle.flags & 1) != 0 || (child && s_noChildRide);
	const float nx = anchored ? 1.0f : ax;
	const float ny = anchored ? 1.0f : ay;
	m_axisComp.x = ax > 1e-6f ? nx * m / ax : 1.0f;
	m_axisComp.y = ay > 1e-6f ? ny * m / ay : 1.0f;
	m_axisComp.z = 1.0f;
    }

    this->updateParticleRenderVars ();
}

void CParticle::applyParallaxToModelMatrix (glm::mat4& matrix) const {
    if (!getScene ().getScene ().camera.parallax.enabled
	|| getScene ().getContext ().getApp ().getContext ().settings.mouse.disableparallax) {
	return;
    }

    const float parallaxAmount = getScene ().getScene ().camera.parallax.amount->value->getFloat ();
    const glm::vec2 depth = m_particle.parallaxDepth->value->getVec2 ();
    const glm::vec2* displacement = getScene ().getParallaxDisplacement ();
    const glm::vec3 parallaxOffset {
	-depth.x * parallaxAmount * displacement->x * static_cast<float> (getScene ().getWidth ()),
	depth.y * parallaxAmount * displacement->y * static_cast<float> (getScene ().getHeight ()),
	0.0f,
    };
    matrix = glm::translate (matrix, parallaxOffset);
}

void CParticle::updateParticleViewProjection () {
    if ((m_particle.flags & 4) != 0) {
	float width = getScene ().getCamera ().getWidth ();
	float height = getScene ().getCamera ().getHeight ();
	float aspect = width / height;
	float fov = glm::radians (getScene ().getCamera ().getFov ());
	float nearz = getScene ().getCamera ().getNearZ ();
	float farz = getScene ().getCamera ().getFarZ ();

	const float eyeZ = (height * 0.5f) / std::tan (fov * 0.5f);
	farz = std::max (farz, eyeZ + height);

	glm::mat4 perspectiveProj = glm::perspective (fov, aspect, nearz, farz);
	glm::mat4 perspectiveView
	    = glm::lookAt (glm::vec3 (0.0f, 0.0f, eyeZ), glm::vec3 (0.0f, 0.0f, 0.0f), glm::vec3 (0.0f, 1.0f, 0.0f));

	m_viewProjectionMatrix = perspectiveProj * perspectiveView;
	m_eyePosition = glm::vec3 (0.0f, 0.0f, eyeZ);
    } else {
	// Orthographic projection from scene camera
	m_viewProjectionMatrix = getScene ().getCamera ().getProjection () * getScene ().getCamera ().getLookAt ();
	// For 2D/orthographic scenes the camera eye is at (0,0,0). The shader's
	// ComputeParticleTrailTangents uses cross(eyeDirection, velocity) to
	// compute the trail ribbon width. With eye at z=0 and particles at z=0,
	// eyeDirection is purely in XY - the cross product yields a Z-only vector
	// that is invisible under orthographic projection. Place the eye at z=1000
	// so the cross product produces a visible XY perpendicular direction.
	m_eyePosition = glm::vec3 (0.0f, 0.0f, 1000.0f);
    }
}

void CParticle::updateParticleRenderVars () {
    m_renderVar0 = glm::vec4 (m_trailLength, m_trailMaxLength, m_trailMinLength, 0.0f);

    if (m_spritesheetFrames > 0 && m_spritesheetCols > 0 && m_spritesheetRows > 0) {
	float frameWidth = 1.0f / static_cast<float> (m_spritesheetCols);
	float frameHeight = 1.0f / static_cast<float> (m_spritesheetRows);
	float textureRatio = 1.0f;
	if (const auto texture = getTexture ()) {
	    // Use atlas dimensions (from resolution vec4) for textureRatio, NOT getRealWidth/Height
	    // which returns per-frame dimensions for animated textures. The shader needs the
	    // per-frame pixel aspect ratio: (atlasH * frameHeight) / (atlasW * frameWidth).
	    const glm::vec4* res = texture->getResolution ();
	    float w = res->x; // atlas/GL texture width
	    float h = res->y; // atlas/GL texture height
	    if (w > 0.0f) {
		textureRatio = (h * frameHeight) / (w * frameWidth);
	    }
	}
	m_renderVar1 = glm::vec4 (frameWidth, frameHeight, static_cast<float> (m_spritesheetFrames), textureRatio);
    } else {
	// No spritesheet - texture ratio is height/width
	float textureRatio = 1.0f;
	if (const auto texture = getTexture ()) {
	    float w = static_cast<float> (texture->getRealWidth ());
	    float h = static_cast<float> (texture->getRealHeight ());
	    if (w > 0.0f) {
		textureRatio = h / w;
	    }
	}
	m_renderVar1 = glm::vec4 (0.0f, 0.0f, 0.0f, textureRatio);
    }
}

void CParticle::renderSprites () {
    if (m_particleCount == 0 || m_pass == nullptr) {
	return;
    }

    static const bool s_hideStParent = getenv ("LWE_HIDESTPARENT") != nullptr;
    if (s_hideStParent && m_useTrailRenderer && !m_useRopeRenderer && !m_children.empty ()) {
	return;
    }

    // Count alive particles
    uint32_t aliveCount = 0;
    for (uint32_t i = 0; i < m_particleCount; i++) {
	if (m_particles[i].alive) {
	    aliveCount++;
	}
    }

    if (aliveCount == 0) {
	return;
    }

    const bool s_twinkleProbe = Logging::instrumentOn ("LWE_TWINKLEPROBE");
    if (s_twinkleProbe) {
	static std::unordered_map<const void*, int> s_twTicks;
	static std::uint32_t s_twEpoch = 0;
	if (const std::uint32_t e = Logging::instrumentEpoch ("LWE_TWINKLEPROBE"); e != s_twEpoch) {
	    s_twEpoch = e;
	    s_twTicks.clear ();
	}
	if (s_twTicks[this]++ % 30 == 0) {
	    for (uint32_t i = 0; i < std::min (m_particleCount, 1u); i++) {
		const auto& q = m_particles[i];
		sLog.out (
		    "LWE-TWINKLE id=", this->getId (), " child=", isChildSystem (), " age=", q.age, " alpha=", q.alpha,
		    " followAlpha=", q.followAlpha, " oscBase=", q.oscillateAlpha.base,
		    " oscFreq=", q.oscillateAlpha.frequency, " oscInit=", q.oscillateAlpha.initialized
		);
	    }
	}
    }

    static const bool s_sizeProbe = getenv ("LWE_SIZEPROBE") != nullptr;
    if (s_sizeProbe) {
	static std::unordered_map<const void*, int> s_probeTicks;
	const int tick = s_probeTicks[this]++;
	if (tick == 1 || tick == 150) {
	    const GLint loc = glGetUniformLocation (m_pass->getProgramID (), "g_LWEAxisComp");
	    GLfloat v[3] = { -9.0f, -9.0f, -9.0f };
	    if (loc >= 0) {
		glGetUniformfv (m_pass->getProgramID (), loc, v);
	    }
	    sLog.out (
		"LWE-COMPPROBE id=", this->getId (), " child=", isChildSystem (), " prog=", m_pass->getProgramID (),
		" compLoc=", loc, " stored=(", v[0], ",", v[1], ",", v[2], ") cpu=(", m_axisComp.x, ",", m_axisComp.y,
		") rope=", m_useRopeRenderer
	    );
	    if (tick == 150 && isChildSystem ()) {
		const std::string& vsrc = m_pass->getShader ()->vertex ();
		size_t count = 0, pos = 0;
		while ((pos = vsrc.find ("g_LWEAxisComp", pos)) != std::string::npos) {
		    count++;
		    pos++;
		}
		const size_t mul = vsrc.find ("* g_LWEAxisComp");
		sLog.out (
		    "LWE-COMPSRC id=", this->getId (), " len=", vsrc.size (), " occurrences=", count,
		    " mulAt=", mul == std::string::npos ? -1 : static_cast<long> (mul)
		);
		if (mul != std::string::npos) {
		    sLog.out ("LWE-COMPSRC ctx: ", vsrc.substr (mul > 200 ? mul - 200 : 0, 300));
		}
	    }
	}
	if (tick < 3 || tick % 150 == 0) {
	    float szMin = 1e9f, szMax = 0.0f, szSum = 0.0f, alSum = 0.0f;
	    glm::vec3 posSum { 0.0f }, velSum { 0.0f };
	    for (uint32_t i = 0; i < m_particleCount; i++) {
		const auto& q = m_particles[i];
		if (!q.alive) {
		    continue;
		}
		szMin = std::min (szMin, q.size);
		szMax = std::max (szMax, q.size);
		szSum += q.size;
		alSum += q.alpha;
		posSum += q.position;
		velSum += q.velocity;
	    }
	    const glm::vec3 scl = rootSystem ()->m_particle.scale->value->getVec3 ();
	    const float inv = 1.0f / static_cast<float> (aliveCount);
	    sLog.out (
		"LWE-SIZEPROBE id=", this->getId (), " alive=", aliveCount, " size=[", szMin, "..", szMax,
		"] mean=", szSum / aliveCount, " alphaMean=", alSum / aliveCount, " posMean=(", posSum.x * inv, ",",
		posSum.y * inv, ") velMean=(", velSum.x * inv, ",", velSum.y * inv, ") objScale=(", scl.x, ",", scl.y,
		") divisor=", worldSizeDivisor (), " comp=(", m_axisComp.x, ",", m_axisComp.y, ")"
	    );
	}
    }

    // Build vertex data in WP shader layout:
    // a_Position(3) + a_TexCoordVec4(uv.x, uv.y, rotZ, size)(4) + a_Color(4)
    //   + a_TexCoordVec4C1(vel.x, vel.y, vel.z, lifetime)(4) + a_TexCoordC2(rotX, rotY)(2) = 17 floats
    uint32_t vertexIndex = 0;
    uint32_t indexOffset = 0;

    for (uint32_t i = 0; i < m_particleCount; i++) {
	const auto& p = m_particles[i];
	if (!p.alive) {
	    continue;
	}

	// Skip particles with invalid values
	if (!std::isfinite (p.position.x) || !std::isfinite (p.position.y) || !std::isfinite (p.position.z)
	    || !std::isfinite (p.size) || p.size <= 0.0f || p.size > 10000.0f) {
	    continue;
	}

	// Compute the lifetime value for the WP shader's ComputeSpriteFrame.
	// The shader computes: floor(frac(lifetime) * numFrames) to get current frame,
	// and frac(lifetime * numFrames) for the blend factor between frames.
	// We encode the CPU-computed p.frame (which accounts for sequenceMultiplier
	// and animation mode) into the lifetime value the shader expects.
	float lifetime = p.getLifetimePos ();

	if (m_spritesheetFrames > 0 && p.frame >= 0.0f) {
	    if (m_particle.animationMode == "randomframe") {
		// Center within the frame to avoid floating-point edge cases
		lifetime = (p.frame + 0.5f) / static_cast<float> (m_spritesheetFrames);
	    } else {
		// Encode frame index + fractional blend: shader reconstructs via
		// floor(lifetime * numFrames) = current frame,
		// frac(lifetime * numFrames) = blend toward next frame
		lifetime = p.frame / static_cast<float> (m_spritesheetFrames);
	    }
	}

	auto addVertex = [&] (float u, float v) {
	    const uint32_t base = vertexIndex * SPRITE_FLOATS_PER_VERTEX;
	    // a_Position (vec3)
	    m_vertices[base + 0] = p.position.x;
	    m_vertices[base + 1] = p.position.y;
	    m_vertices[base + 2] = p.position.z;
	    // a_TexCoordVec4 (vec4: uv.x, uv.y, rotZ, size)
	    m_vertices[base + 3] = u;
	    m_vertices[base + 4] = v;
	    m_vertices[base + 5] = p.rotation.z;
	    m_vertices[base + 6] = p.size;
	    m_vertices[base + 7] = p.color.r;
	    m_vertices[base + 8] = p.color.g;
	    m_vertices[base + 9] = p.color.b;
	    m_vertices[base + 10] = p.alpha * p.followAlpha;
	    // a_TexCoordVec4C1 (vec4: vel.x, vel.y, vel.z, lifetime)
	    m_vertices[base + 11] = p.velocity.x;
	    m_vertices[base + 12] = p.velocity.y;
	    m_vertices[base + 13] = p.velocity.z;
	    m_vertices[base + 14] = lifetime;
	    // a_TexCoordC2 (vec2: rotX, rotY)
	    m_vertices[base + 15] = p.rotation.x;
	    m_vertices[base + 16] = p.rotation.y;
	    vertexIndex++;
	};

	// 4 vertices for quad corners
	uint32_t baseVertex = vertexIndex;
	addVertex (0.0f, 1.0f); // 0: Bottom-left
	addVertex (1.0f, 1.0f); // 1: Bottom-right
	addVertex (1.0f, 0.0f); // 2: Top-right
	addVertex (0.0f, 0.0f); // 3: Top-left

	// 6 indices forming 2 triangles
	m_indices[indexOffset++] = baseVertex + 0;
	m_indices[indexOffset++] = baseVertex + 1;
	m_indices[indexOffset++] = baseVertex + 2;
	m_indices[indexOffset++] = baseVertex + 2;
	m_indices[indexOffset++] = baseVertex + 3;
	m_indices[indexOffset++] = baseVertex + 0;
    }

    m_activeIndexCount = static_cast<GLsizei> (indexOffset);
    if (m_activeIndexCount == 0) {
	return;
    }

#if !NDEBUG
    std::string str = "Particles ";
    str += this->getParticle ().name + " (" + std::to_string (this->getId ()) + ", " + this->getParticle ().particleFile
	+ ")";
    glPushDebugGroup (GL_DEBUG_SOURCE_APPLICATION, 0, -1, str.c_str ());
#endif

    // Upload vertex and index data
    glBindBuffer (GL_ARRAY_BUFFER, m_vbo);
    uploadParticleBuffer (
	GL_ARRAY_BUFFER, m_vboCapacity,
	static_cast<GLsizeiptr> (vertexIndex * SPRITE_FLOATS_PER_VERTEX * sizeof (float)), m_vertices.data ()
    );

    glBindBuffer (GL_ELEMENT_ARRAY_BUFFER, m_ebo);
    uploadParticleBuffer (
	GL_ELEMENT_ARRAY_BUFFER, m_eboCapacity, static_cast<GLsizeiptr> (indexOffset * sizeof (uint32_t)),
	m_indices.data ()
    );

    logParticleBuffer (
	this->getParticle ().name, getId (), false, true,
	static_cast<uint64_t> (vertexIndex) * SPRITE_FLOATS_PER_VERTEX * sizeof (float), m_particleCount, m_maxParticles
    );
    logParticleBuffer (
	this->getParticle ().name, getId (), false, false, static_cast<uint64_t> (indexOffset) * sizeof (uint32_t),
	m_particleCount, m_maxParticles
    );

    // Update matrices and uniform data
    updateMatrices ();

    // For REFRACT: blit current scene content into the copy FBO before rendering.
    // This gives the shader a snapshot of what's behind the particles for refraction,
    // without a feedback loop (rendering to scene FBO while reading from copy FBO).
    if (m_hasRefract && m_refractFBO) {
	auto sceneFBO = getScene ().getActiveRenderTarget ();
	GLint w = static_cast<GLint> (sceneFBO->getRealWidth ());
	GLint h = static_cast<GLint> (sceneFBO->getRealHeight ());
	glBindFramebuffer (GL_READ_FRAMEBUFFER, sceneFBO->getFramebuffer ());
	glBindFramebuffer (GL_DRAW_FRAMEBUFFER, m_refractFBO->getFramebuffer ());
	glBlitFramebuffer (0, 0, w, h, 0, 0, w, h, GL_COLOR_BUFFER_BIT, GL_NEAREST);
    }

    // The shader's ComputeParticleTrailTangents produces a right vector with a Z component
    // (from cross(eyeDirection, velocity) where eyeDirection has XY offset from model transform).
    // For 2D/ortho particles at z of ~0, the ortho near plane sits at ndc.z=-1 - any Z offset from
    // the right vector pushes vertices past the near plane, causing half the quad to be clipped.
    // GL_DEPTH_CLAMP prevents near/far clipping by clamping depth instead.
    glEnable (GL_DEPTH_CLAMP);

    // CPass::render() handles: FBO binding, texture setup, uniforms, blending, draw call, cleanup
    m_pass->render ();

    glDisable (GL_DEPTH_CLAMP);

#if !NDEBUG
    glPopDebugGroup ();
#endif
}

void CParticle::renderRope () {
    if (m_pass == nullptr) {
	return;
    }

    struct StripNode {
	glm::vec3 position;
	float size;
	glm::vec4 color;
    };

    std::vector<std::vector<StripNode>> strips;

    if (m_useTrailRenderer) {
	static const char* s_tmEnv = getenv ("LWE_TRAILMODE");
	static const bool s_exactTrail = s_tmEnv != nullptr && std::string (s_tmEnv) == "exact";
	const double now = m_sysTime;
	const int segs = std::max (1, m_ropeSegments);
	strips.reserve (m_particleCount);
	for (uint32_t i = 0; i < m_particleCount; i++) {
	    const auto& p = m_particles[i];
	    if (p.trail.empty ()) {
		continue;
	    }
	    // Trail grows from birth: span is capped by both renderer.length (already
	    // enforced by the update-side trim) and how long this particle has lived
	    const double span = now - p.trail.front ().time;
	    if (span <= 0.0) {
		continue;
	    }
	    // Build the list of sample times for this strip (oldest first, head last)
	    std::vector<double> sampleTimes;
	    if (s_exactTrail) {
		const double tau = static_cast<double> (m_trailLength) / 4.0;
		const double birth = now - static_cast<double> (p.age);
		if (tau > 0.0) {
		    const int kmax = static_cast<int> (p.age / tau);
		    for (int k = std::max (0, kmax - 2); k <= kmax; k++) {
			sampleTimes.push_back (birth + static_cast<double> (k) * tau);
		    }
		}
	    } else {
		for (int k = 0; k < segs; k++) {
		    sampleTimes.push_back (now - span * (1.0 - static_cast<double> (k) / static_cast<double> (segs)));
		}
	    }
	    std::vector<StripNode> nodes;
	    nodes.reserve (sampleTimes.size () + 1);
	    size_t cursor = 0;
	    for (const double tk : sampleTimes) {
		while (cursor + 1 < p.trail.size () && p.trail[cursor + 1].time <= tk) {
		    cursor++;
		}
		const auto& a = p.trail[cursor];
		const bool haveB = (cursor + 1 < p.trail.size ());
		const glm::vec3 bPos = haveB ? p.trail[cursor + 1].position : p.position;
		const float bSize = haveB ? p.trail[cursor + 1].size : p.size;
		const glm::vec4 bColor
		    = haveB ? glm::vec4 (p.trail[cursor + 1].color, p.alpha) : glm::vec4 (p.color, p.alpha);
		const double bTime = haveB ? p.trail[cursor + 1].time : now;
		const double denom = bTime - a.time;
		const float t
		    = denom > 0.0 ? glm::clamp (static_cast<float> ((tk - a.time) / denom), 0.0f, 1.0f) : 1.0f;
		nodes.push_back (
		    { glm::mix (a.position, bPos, t), glm::mix (a.size, bSize, t),
		      glm::mix (glm::vec4 (a.color, p.alpha), bColor, t) }
		);
	    }
	    nodes.push_back ({ p.position, p.size, glm::vec4 (p.color, p.alpha) });
	    if (nodes.size () >= 2) {
		strips.push_back (std::move (nodes));
	    }
	}
    } else if (m_particleCount >= 2) {
	// Array is already in spawn order (oldest at index 0) thanks to order-preserving
	// compaction in update(). All particles in [0, m_particleCount) are alive.
	std::vector<StripNode> nodes;
	nodes.reserve (m_particleCount);
	for (uint32_t i = 0; i < m_particleCount; i++) {
	    const auto& p = m_particles[i];
	    nodes.push_back ({ p.position, p.size, glm::vec4 (p.color, p.alpha) });
	}
	strips.push_back (std::move (nodes));
    }

    if (strips.empty ()) {
	return;
    }

    const bool s_ropeProbe = Logging::instrumentOn ("LWE_ROPETRAILPROBE");
    if (s_ropeProbe) {
	float maxSeg = 0.0f;
	size_t nodeTotal = 0;
	for (const auto& sn : strips) {
	    nodeTotal += sn.size ();
	    for (size_t i = 1; i < sn.size (); i++) {
		maxSeg = std::max (maxSeg, glm::distance (sn[i].position, sn[i - 1].position));
	    }
	}
	const glm::vec3& head = strips[0].back ().position;
	const glm::vec4 clip = m_mvpMatrix * glm::vec4 (head, 1.0f);
	const glm::vec2 ndc = clip.w != 0.0f ? glm::vec2 (clip) / clip.w : glm::vec2 (0.0f);
	sLog.out (
	    "LWE-ROPETRAIL pass=", static_cast<const void*> (this), " strips=", strips.size (), " nodes=", nodeTotal,
	    " maxseg=", maxSeg, " t=", g_Time, " head=", head.x, ",", head.y, " ndc=", ndc.x, ",", ndc.y
	);
    }

    const int subdivision = std::max (1, m_ropeSubdivision);

    {
	size_t neededSubSegments = 0;
	for (const auto& sn : strips) {
	    neededSubSegments += (sn.size () - 1) * static_cast<size_t> (subdivision);
	}
	if (m_vertices.size () < neededSubSegments * 4 * ROPE_FLOATS_PER_VERTEX) {
	    m_vertices.resize (neededSubSegments * 4 * ROPE_FLOATS_PER_VERTEX);
	    m_indices.resize (neededSubSegments * 6);
	}
    }

    // Build vertex data with Catmull-Rom spline subdivision.
    // Each segment between consecutive particles is subdivided into m_ropeSubdivision
    // sub-segments for smooth curves instead of harsh corners at particle positions.
    //
    // Rope vertex layout (26 floats per vertex, THICKFORMAT):
    // [0-3]   a_PositionVec4:   startPos.xyz, sizeStart
    // [4-7]   a_TexCoordVec4:   endPos.xyz, trailLength
    // [8-11]  a_TexCoordVec4C1: CP0.xyz, trailPosition
    // [12-15] a_TexCoordVec4C2: CP1.xyz, sizeEnd
    // [16-19] a_TexCoordVec4C3: colorEnd.rgba
    // [20-21] a_TexCoordC4:     uvs.xy
    // [22-25] a_Color:          colorStart.rgba

    uint32_t vertexIndex = 0;
    uint32_t indexOffset = 0;
    const float uvScale = (m_ropeUVScale > 0.0f) ? m_ropeUVScale : 1.0f;

    // Catmull-Rom spline evaluation
    auto catmullRom = [] (const glm::vec3& p0, const glm::vec3& p1, const glm::vec3& p2, const glm::vec3& p3,
			  float t) -> glm::vec3 {
	float t2 = t * t, t3 = t2 * t;
	return 0.5f
	    * ((2.0f * p1) + (-p0 + p2) * t + (2.0f * p0 - 5.0f * p1 + 4.0f * p2 - p3) * t2
	       + (-p0 + 3.0f * p1 - 3.0f * p2 + p3) * t3);
    };

    for (const auto& stripNodes : strips) {
	const uint32_t nodeCount = static_cast<uint32_t> (stripNodes.size ());
	const uint32_t numSegments = nodeCount - 1;

	// First pass: evaluate spline to get all interpolated points
	const uint32_t totalPoints = numSegments * subdivision + 1;
	// Store position, size, color (rgba) per point = 3 + 1 + 4 = 8 floats
	std::vector<glm::vec3> splinePositions (totalPoints);
	std::vector<float> splineSizes (totalPoints);
	std::vector<glm::vec4> splineColors (totalPoints); // rgba

	for (uint32_t i = 0; i < numSegments; i++) {
	    const auto& p1 = stripNodes[i];
	    const auto& p2 = stripNodes[i + 1];
	    const auto& p0 = (i > 0) ? stripNodes[i - 1] : p1;
	    const auto& p3 = (i + 2 < nodeCount) ? stripNodes[i + 2] : p2;

	    for (int k = 0; k < subdivision; k++) {
		float t = static_cast<float> (k) / static_cast<float> (subdivision);
		uint32_t idx = i * subdivision + k;

		splinePositions[idx] = catmullRom (p0.position, p1.position, p2.position, p3.position, t);
		splineSizes[idx] = glm::mix (p1.size, p2.size, t);
		splineColors[idx] = glm::mix (p1.color, p2.color, t);
	    }
	}
	{
	    const auto& pLast = stripNodes[nodeCount - 1];
	    splinePositions[totalPoints - 1] = pLast.position;
	    splineSizes[totalPoints - 1] = pLast.size;
	    splineColors[totalPoints - 1] = pLast.color;
	}

	// Second pass: build quads from consecutive spline points.
	// The shader computes UV.v from trailPosition / (trailLength - 1), consuming
	// 1/(trailLength-1) of UV space per quad. Express trailLength and trailPosition
	// in sub-segment units so each sub-segment quad gets the correct UV slice.
	// UV scale divides the effective length, making UVs exceed [0,1] -> texture repeats.
	const uint32_t totalSubSegments = totalPoints - 1;
	const float trailLength = static_cast<float> (totalSubSegments) / uvScale + 1.0f;
	const float usableLength = trailLength - 1.0f;

	// UV smoothing: distribute UV proportional to arc length instead of uniform index.
	// Per wiki: only when all particle lifetimes match and scrolling is disabled.
	const bool useSmoothing = m_ropeUVSmoothing && m_uniformLifetimes && !m_ropeUVScrolling;
	std::vector<float> cumulativeArcLength;
	float totalArcLength = 0.0f;

	if (useSmoothing) {
	    cumulativeArcLength.resize (totalPoints, 0.0f);
	    for (uint32_t i = 1; i < totalPoints; i++) {
		totalArcLength += glm::distance (splinePositions[i], splinePositions[i - 1]);
		cumulativeArcLength[i] = totalArcLength;
	    }
	}

	// UV scrolling: shift UV along the rope over time (1 UV cycle per second)
	float scrollOffset = 0.0f;
	if (m_ropeUVScrolling && usableLength > 0.0f) {
	    scrollOffset = std::fmod (static_cast<float> (g_Time), 10000.0f) * usableLength;
	}

	for (uint32_t s = 0; s < totalSubSegments; s++) {
	    const glm::vec3& posStart = splinePositions[s];
	    const glm::vec3& posEnd = splinePositions[s + 1];
	    float sizeStart = splineSizes[s];
	    float sizeEnd = splineSizes[s + 1];
	    const glm::vec4& colorStart = splineColors[s];
	    const glm::vec4& colorEnd = splineColors[s + 1];

	    // Neighboring points for shader tangent computation (CP0/CP1)
	    const glm::vec3& posPrev = (s > 0) ? splinePositions[s - 1] : posStart;
	    const glm::vec3& posAfter = (s + 2 < totalPoints) ? splinePositions[s + 2] : posEnd;

	    // Compute trailPosition for UV mapping
	    float trailPosition;
	    if (useSmoothing && totalArcLength > 0.0f) {
		// Arc-length parameterization: map cumulative distance to sub-segment space
		trailPosition = cumulativeArcLength[s] / totalArcLength * static_cast<float> (totalSubSegments);
	    } else {
		trailPosition = static_cast<float> (s);
	    }
	    trailPosition += scrollOffset;

	    auto addRopeVertex = [&] (float uvX, float uvY) {
		const uint32_t base = vertexIndex * ROPE_FLOATS_PER_VERTEX;

		// a_PositionVec4: startPos.xyz, sizeStart
		m_vertices[base + 0] = posStart.x;
		m_vertices[base + 1] = posStart.y;
		m_vertices[base + 2] = posStart.z;
		m_vertices[base + 3] = sizeStart;

		// a_TexCoordVec4: endPos.xyz, trailLength
		m_vertices[base + 4] = posEnd.x;
		m_vertices[base + 5] = posEnd.y;
		m_vertices[base + 6] = posEnd.z;
		m_vertices[base + 7] = trailLength;

		// a_TexCoordVec4C1: CP0.xyz (neighbor before start), trailPosition
		m_vertices[base + 8] = posPrev.x;
		m_vertices[base + 9] = posPrev.y;
		m_vertices[base + 10] = posPrev.z;
		m_vertices[base + 11] = trailPosition;

		// a_TexCoordVec4C2: CP1.xyz (neighbor after end), sizeEnd
		m_vertices[base + 12] = posAfter.x;
		m_vertices[base + 13] = posAfter.y;
		m_vertices[base + 14] = posAfter.z;
		m_vertices[base + 15] = sizeEnd;

		// a_TexCoordVec4C3: colorEnd.rgba
		m_vertices[base + 16] = colorEnd.r;
		m_vertices[base + 17] = colorEnd.g;
		m_vertices[base + 18] = colorEnd.b;
		m_vertices[base + 19] = colorEnd.a;

		// a_TexCoordC4: uvs.xy
		m_vertices[base + 20] = uvX;
		m_vertices[base + 21] = uvY;

		// a_Color: colorStart.rgba
		m_vertices[base + 22] = colorStart.r;
		m_vertices[base + 23] = colorStart.g;
		m_vertices[base + 24] = colorStart.b;
		m_vertices[base + 25] = colorStart.a;

		vertexIndex++;
	    };

	    // Quad: 4 vertices (left/right at start/end of segment)
	    uint32_t baseVertex = vertexIndex;
	    addRopeVertex (0.0f, 0.0f); // left at start
	    addRopeVertex (1.0f, 0.0f); // right at start
	    addRopeVertex (1.0f, 1.0f); // right at end
	    addRopeVertex (0.0f, 1.0f); // left at end

	    // 2 triangles
	    m_indices[indexOffset++] = baseVertex + 0;
	    m_indices[indexOffset++] = baseVertex + 1;
	    m_indices[indexOffset++] = baseVertex + 2;
	    m_indices[indexOffset++] = baseVertex + 2;
	    m_indices[indexOffset++] = baseVertex + 3;
	    m_indices[indexOffset++] = baseVertex + 0;
	}
    }

    m_activeIndexCount = static_cast<GLsizei> (indexOffset);
    if (m_activeIndexCount == 0) {
	return;
    }

#if !NDEBUG
    std::string str = "Rope particles ";
    str += this->getParticle ().name + " (" + std::to_string (this->getId ()) + ", " + this->getParticle ().particleFile
	+ ")";
    glPushDebugGroup (GL_DEBUG_SOURCE_APPLICATION, 0, -1, str.c_str ());
#endif

    // Upload vertex and index data
    glBindBuffer (GL_ARRAY_BUFFER, m_vbo);
    uploadParticleBuffer (
	GL_ARRAY_BUFFER, m_vboCapacity, static_cast<GLsizeiptr> (vertexIndex * ROPE_FLOATS_PER_VERTEX * sizeof (float)),
	m_vertices.data ()
    );

    glBindBuffer (GL_ELEMENT_ARRAY_BUFFER, m_ebo);
    uploadParticleBuffer (
	GL_ELEMENT_ARRAY_BUFFER, m_eboCapacity, static_cast<GLsizeiptr> (indexOffset * sizeof (uint32_t)),
	m_indices.data ()
    );

    logParticleBuffer (
	this->getParticle ().name, getId (), true, true,
	static_cast<uint64_t> (vertexIndex) * ROPE_FLOATS_PER_VERTEX * sizeof (float), m_particleCount, m_maxParticles
    );
    logParticleBuffer (
	this->getParticle ().name, getId (), true, false, static_cast<uint64_t> (indexOffset) * sizeof (uint32_t),
	m_particleCount, m_maxParticles
    );

    // Update matrices and uniform data
    updateMatrices ();

    // For REFRACT: blit current scene content into the copy FBO before rendering
    if (m_hasRefract && m_refractFBO) {
	auto sceneFBO = getScene ().getActiveRenderTarget ();
	GLint w = static_cast<GLint> (sceneFBO->getRealWidth ());
	GLint h = static_cast<GLint> (sceneFBO->getRealHeight ());
	glBindFramebuffer (GL_READ_FRAMEBUFFER, sceneFBO->getFramebuffer ());
	glBindFramebuffer (GL_DRAW_FRAMEBUFFER, m_refractFBO->getFramebuffer ());
	glBlitFramebuffer (0, 0, w, h, 0, 0, w, h, GL_COLOR_BUFFER_BIT, GL_NEAREST);
    }

    glEnable (GL_DEPTH_CLAMP);
    m_pass->render ();
    glDisable (GL_DEPTH_CLAMP);

#if !NDEBUG
    glPopDebugGroup ();
#endif
}
