#include "CModel.h"

#include "WallpaperEngine/Data/Builders/UserSettingBuilder.h"
#include "WallpaperEngine/Data/Model/DynamicValue.h"
#include "WallpaperEngine/Data/Model/Material.h"
#include "WallpaperEngine/Data/Model/Project.h"
#include "WallpaperEngine/Data/Model/UserSetting.h"
#include "WallpaperEngine/Logging/Log.h"

#include <algorithm>
#include <cstring>
#include <glm/gtc/matrix_inverse.hpp>
#include <glm/gtc/matrix_transform.hpp>

using namespace WallpaperEngine;
using namespace WallpaperEngine::Render::Objects;

CModel::CModel (Wallpapers::CScene& scene, const ModelObject& model) :
    CObject (scene, model), CRenderable (scene, model, *model.material), ScriptableObject (scene, model),
    m_model (model) {
    this->registerProperty ("origin", *model.origin->value);
    this->registerProperty ("scale", *model.scale->value);
    this->registerProperty ("angles", *model.angles->value);
    this->registerProperty ("visible", *model.visible->value);
    this->registerProperty ("alpha", *model.alpha->value);
    this->registerProperty ("color", *model.color->value);

    this->detectTexture ();
}

CModel::~CModel () {
    for (auto& submesh : m_submeshes) {
	delete submesh.pass;

	if (submesh.vbo != GL_NONE) {
	    glDeleteBuffers (1, &submesh.vbo);
	}
	if (submesh.ebo != GL_NONE) {
	    glDeleteBuffers (1, &submesh.ebo);
	}
	if (submesh.vao != GL_NONE) {
	    glDeleteVertexArrays (1, &submesh.vao);
	}
    }
}

void CModel::setup () {
    if (m_initialized) {
	return;
    }

    CRenderable::setup ();

    if (!this->loadMesh ()) {
	sLog.error ("Model object ", this->getId (), " has no usable mesh - skipping");
	return;
    }

    for (auto& submesh : m_submeshes) {
	this->setupPass (submesh);
    }
    this->updateMatrices ();

    m_initialized = true;
}

bool CModel::loadMesh () {

    const auto stream = this->getScene ().getScene ().project.assetLocator->read (m_model.modelFile);
    const std::vector<char> data { std::istreambuf_iterator<char> (*stream), std::istreambuf_iterator<char> () };

    if (data.size () < 32 || std::memcmp (data.data (), "MDLV", 4) != 0) {
	sLog.error ("Not an MDLV model: ", m_model.modelFile);
	return false;
    }

    const size_t magicEnd = std::string_view (data.data (), data.size ()).find ('\0');
    if (magicEnd == std::string_view::npos) {
	sLog.error ("Unterminated MDLV magic in ", m_model.modelFile);
	return false;
    }

    uint32_t mdlvVersion = 0;
    if (magicEnd >= 8) {
	for (size_t digit = 4; digit < 8; digit++) {
	    const char c = data[digit];
	    if (c < '0' || c > '9') {
		mdlvVersion = 0;
		break;
	    }
	    mdlvVersion = mdlvVersion * 10 + static_cast<uint32_t> (c - '0');
	}
    }

    size_t offset = magicEnd + 1 + 2 * sizeof (uint32_t);
    // a magic string ending near EOF puts this read past the buffer; the minimum-size
    // check above bounds data.size(), not magicEnd
    if (offset + sizeof (uint32_t) > data.size ()) {
	sLog.error ("Truncated MDLV header in ", m_model.modelFile);
	return false;
    }
    uint32_t submeshCount = 0;
    std::memcpy (&submeshCount, data.data () + offset, sizeof (submeshCount));
    offset += sizeof (uint32_t);

    if (submeshCount == 0 || submeshCount > 16) {
	sLog.error ("Unexpected submesh count ", submeshCount, " in ", m_model.modelFile);
	return false;
    }

    const auto readU32 = [&data, &offset] (uint32_t& out) -> bool {
	if (offset + sizeof (uint32_t) > data.size ()) {
	    return false;
	}
	std::memcpy (&out, data.data () + offset, sizeof (out));
	offset += sizeof (uint32_t);
	return true;
    };

    GLint prevVAO = 0;
    glGetIntegerv (GL_VERTEX_ARRAY_BINDING, &prevVAO);

    for (uint32_t index = 0; index < submeshCount; index++) {
	// the previous record's fixed-size skip is unchecked, so offset can sit past EOF
	// here; data.size() - offset would then underflow to a huge scan length
	if (offset >= data.size ()) {
	    sLog.error ("Truncated submesh record ", index, " in ", m_model.modelFile);
	    return false;
	}
	const auto* nameEnd = static_cast<const char*> (std::memchr (data.data () + offset, 0, data.size () - offset));
	if (nameEnd == nullptr) {
	    sLog.error ("Unterminated submesh material name in ", m_model.modelFile);
	    return false;
	}
	offset = (nameEnd - data.data ()) + 1;
	offset += sizeof (uint32_t) + 6 * sizeof (float); // [u32 0][bounds]

	uint32_t vertexTag = 0;
	size_t vertexStride = 48;
	GLuint uvOffset = 40;
	if (!readU32 (vertexTag)) {
	    sLog.error ("Bad vertex tag in submesh ", index, " of ", m_model.modelFile);
	    return false;
	}
	if (vertexTag == 0x0180000fu) {
	    vertexStride = 80;
	    uvOffset = 72;
	} else if (vertexTag == 0u && mdlvVersion != 0 && mdlvVersion < 16) {
	    vertexStride = 52;
	    uvOffset = 44;
	} else if (vertexTag != 15u) {
	    sLog.error ("Unsupported MDLV vertex layout tag ", vertexTag, " in ", m_model.modelFile);
	    return false;
	}

	uint32_t vertexBytes = 0;
	if (!readU32 (vertexBytes) || vertexBytes == 0 || vertexBytes % vertexStride != 0
	    || offset + vertexBytes > data.size ()) {
	    sLog.error ("Bad vertex block in submesh ", index, " of ", m_model.modelFile);
	    return false;
	}
	const size_t verticesOffset = offset;
	offset += vertexBytes;

	uint32_t indexBytes = 0;
	if (!readU32 (indexBytes) || indexBytes == 0 || indexBytes % (sizeof (uint16_t) * 3) != 0
	    || offset + indexBytes > data.size ()) {
	    sLog.error ("Bad index block in submesh ", index, " of ", m_model.modelFile);
	    return false;
	}
	const size_t indicesOffset = offset;
	offset += indexBytes;

	const size_t vertexCount = vertexBytes / vertexStride;
	const auto indexCount = static_cast<GLsizei> (indexBytes / sizeof (uint16_t));

	const auto* indices = reinterpret_cast<const uint16_t*> (data.data () + indicesOffset);
	for (GLsizei i = 0; i < indexCount; i++) {
	    if (indices[i] >= vertexCount) {
		sLog.error ("Invalid mesh index ", indices[i], " in submesh ", index, " of ", m_model.modelFile);
		return false;
	    }
	}

	Submesh submesh {};
	submesh.indexCount = indexCount;
	submesh.stride = static_cast<GLsizei> (vertexStride);
	submesh.uvOffset = uvOffset;
	// submesh 0 uses the object's material (also the renderable's); the rest carry their own
	submesh.material = index == 0 ? m_model.material.get () : m_model.extraMaterials[index - 1].get ();

	if (submesh.material == nullptr || submesh.material->passes.empty ()) {
	    sLog.error ("Submesh ", index, " of ", m_model.modelFile, " has no material passes - skipping submesh");
	    continue;
	}

	glGenVertexArrays (1, &submesh.vao);
	glGenBuffers (1, &submesh.vbo);
	glGenBuffers (1, &submesh.ebo);

	glBindVertexArray (submesh.vao);
	glBindBuffer (GL_ARRAY_BUFFER, submesh.vbo);
	glBufferData (GL_ARRAY_BUFFER, vertexBytes, data.data () + verticesOffset, GL_STATIC_DRAW);
	glBindBuffer (GL_ELEMENT_ARRAY_BUFFER, submesh.ebo);
	glBufferData (GL_ELEMENT_ARRAY_BUFFER, indexBytes, data.data () + indicesOffset, GL_STATIC_DRAW);

	sLog.debug (
	    "Model ", m_model.modelFile, " submesh ", index, ": ", vertexCount, " vertices, ", indexCount / 3,
	    " triangles"
	);

	m_submeshes.push_back (std::move (submesh));
    }

    glBindVertexArray (prevVAO);

    if (!this->getScene ().getScene ().transparentSorting) {
	glBindVertexArray (prevVAO);
	return !m_submeshes.empty ();
    }

    std::stable_sort (m_submeshes.begin (), m_submeshes.end (), [] (const Submesh& a, const Submesh& b) {
	const auto rank = [] (const Submesh& s) {
	    if (s.material == nullptr || s.material->passes.empty ()) {
		return 0;
	    }
	    return (**s.material->passes.begin ()).blending == BlendingMode_Translucent ? 1 : 0;
	};
	return rank (a) < rank (b);
    });

    return !m_submeshes.empty ();
}

void CModel::setupPass (Submesh& submesh) {
    const auto& firstPass = **submesh.material->passes.begin ();

    submesh.fboProvider = std::make_shared<FBOProvider> (this);

    submesh.passOverride = std::make_unique<ImageEffectPassOverride> ();
    if (!firstPass.combos.contains ("LIGHTING")) {
	submesh.passOverride->combos["LIGHTING"] = 1;
    }
    if (const char* tintFix = getenv ("LWE_TINTFIX"); tintFix != nullptr) {
	if (tintFix[0] == '1') {
	    submesh.passOverride->combos["TINTMASKALPHA"] = 0;
	} else if (tintFix[0] == '2') {
	    submesh.passOverride->constants["color"]
		= Data::Builders::UserSettingBuilder::fromValue (glm::vec3 (0.0f, 0.5254902f, 1.0f));
	}
    }
    if (getenv ("LWE_SPECFIX") != nullptr) {
	submesh.passOverride->constants["roughness"] = Data::Builders::UserSettingBuilder::fromValue (1.0f);
    }

    submesh.pass
	= new Effects::CPass (*this, submesh.fboProvider, firstPass, *submesh.passOverride, std::nullopt, std::nullopt);

    if (getenv ("LWE_LIGHTDUMP") != nullptr) {
	const auto& fragSrc = submesh.pass->getShader ()->fragment ();
	sLog.out (
	    "LWE-MODELPASS obj=", this->getId (), " shader=", firstPass.shader,
	    " LIGHTING-define=", fragSrc.find ("#define LIGHTING 1") != std::string::npos,
	    " performCall=", fragSrc.find ("PerformLighting_V1") != std::string::npos
	);
    }

    submesh.pass->setDestination (this->getScene ().getFBO ());
    // fallback only: a pass whose own material names textures resolves those first
    submesh.pass->setInput (this->getTexture ());

    submesh.pass->setModelViewProjectionMatrix (&m_mvpMatrix);
    submesh.pass->setModelViewProjectionMatrixInverse (&m_mvpMatrixInverse);
    submesh.pass->setModelMatrix (&m_modelMatrix);
    submesh.pass->setViewProjectionMatrix (&m_viewProjectionMatrix);

    submesh.pass->addUniform ("g_NormalModelMatrix", &m_normalMatrix);
    submesh.pass->addUniform ("g_EyePosition", &m_eyePosition);

    // mesh vertex layout per submesh tag: a_Position @0, a_Normal @12,
    // a_Tangent4 @24, a_TexCoord @ submesh.uvOffset (40 static / 72 skinned)
    const GLuint program = submesh.pass->getProgramID ();
    const GLsizei stride = submesh.stride;

    GLint prevVAO = 0;
    glGetIntegerv (GL_VERTEX_ARRAY_BINDING, &prevVAO);
    glBindVertexArray (submesh.vao);
    glBindBuffer (GL_ARRAY_BUFFER, submesh.vbo);
    glBindBuffer (GL_ELEMENT_ARRAY_BUFFER, submesh.ebo);

    const GLint locPosition = glGetAttribLocation (program, "a_Position");
    const GLint locNormal = glGetAttribLocation (program, "a_Normal");
    const GLint locTangent = glGetAttribLocation (program, "a_Tangent4");
    const GLint locTexCoord = glGetAttribLocation (program, "a_TexCoord");

    if (locPosition >= 0) {
	glEnableVertexAttribArray (locPosition);
	glVertexAttribPointer (locPosition, 3, GL_FLOAT, GL_FALSE, stride, nullptr);
    }
    if (locNormal >= 0) {
	glEnableVertexAttribArray (locNormal);
	glVertexAttribPointer (locNormal, 3, GL_FLOAT, GL_FALSE, stride, (void*)12);
    }
    if (locTangent >= 0) {
	glEnableVertexAttribArray (locTangent);
	glVertexAttribPointer (locTangent, 4, GL_FLOAT, GL_FALSE, stride, (void*)24);
    }
    if (locTexCoord >= 0) {
	glEnableVertexAttribArray (locTexCoord);
	glVertexAttribPointer (
	    locTexCoord, 2, GL_FLOAT, GL_FALSE, stride,
	    reinterpret_cast<void*> (static_cast<uintptr_t> (submesh.uvOffset))
	);
    }

    glBindVertexArray (prevVAO);

    Submesh* self = &submesh;
    submesh.pass->setGeometryCallback (
	[this, self] () {
	    glGetIntegerv (GL_VERTEX_ARRAY_BINDING, &self->prevVAO);
	    glBindVertexArray (self->vao);
	    static const bool s_frontFaceCCW = [] () -> bool {
		const char* v = getenv ("LWE_FRONTFACE");
		return v != nullptr && std::strcmp (v, "ccw") == 0;
	    }();
	    glFrontFace (s_frontFaceCCW ? GL_CCW : GL_CW);
	    static const bool s_stateAudit = getenv ("LWE_AUDIT") != nullptr;
	    if (s_stateAudit) {
		static bool s_logged = false;
		if (!s_logged) {
		    s_logged = true;
		    GLint cullMode = 0;
		    glGetIntegerv (GL_CULL_FACE_MODE, &cullMode);
		    sLog.out (
			"LWE-AUDIT model-draw state: cull=", static_cast<int> (glIsEnabled (GL_CULL_FACE)),
			" cullMode=", cullMode == GL_BACK ? "BACK" : "FRONT/other",
			" frontFace=", s_frontFaceCCW ? "CCW" : "CW",
			" depthTest=", static_cast<int> (glIsEnabled (GL_DEPTH_TEST))
		    );
		}
	    }
	},
	[self] () { glDrawElements (GL_TRIANGLES, self->indexCount, GL_UNSIGNED_SHORT, nullptr); },
	[self] () {
	    glFrontFace (GL_CCW);
	    glBindVertexArray (self->prevVAO);
	}
    );
}

namespace {
float bezierEase (const float t, const float x1, const float x2) {
    const auto bx = [x1, x2] (const float s) {
	const float inv = 1.0f - s;
	return 3.0f * inv * inv * s * x1 + 3.0f * inv * s * s * x2 + s * s * s;
    };
    float s = t;
    for (int i = 0; i < 6; i++) {
	const float inv = 1.0f - s;
	const float dx = 3.0f * inv * inv * x1 + 6.0f * inv * s * (x2 - x1) + 3.0f * s * s * (1.0f - x2);
	if (std::abs (dx) < 1e-6f) {
	    break;
	}
	s -= (bx (s) - t) / dx;
	s = std::clamp (s, 0.0f, 1.0f);
    }
    const float inv = 1.0f - s;
    return 3.0f * inv * s * s + s * s * s;
}

float evalChannel (const AnimationChannel& channel, const float frame, const float fallback) {
    if (channel.keys.empty ()) {
	return fallback;
    }
    if (channel.keys.size () == 1 || frame <= channel.keys.front ().frame) {
	return channel.keys.front ().value;
    }
    if (frame >= channel.keys.back ().frame) {
	return channel.keys.back ().value;
    }
    for (size_t i = 0; i + 1 < channel.keys.size (); i++) {
	const auto& k0 = channel.keys[i];
	const auto& k1 = channel.keys[i + 1];
	if (frame < k0.frame || frame > k1.frame) {
	    continue;
	}
	const float span = std::max (k1.frame - k0.frame, 1e-6f);
	const float t = (frame - k0.frame) / span;
	const float x1 = std::clamp (k0.frontX * 0.5f, 0.0f, 1.0f);
	const float x2 = std::clamp (1.0f + k1.backX * 0.5f, 0.0f, 1.0f);
	return k0.value + (k1.value - k0.value) * bezierEase (t, x1, x2);
    }
    return fallback;
}
} // namespace

extern float g_Time;

glm::vec3 CModel::effectiveAngles () const {
    const glm::vec3 authored = m_model.angles->value->getVec3 ();
    const auto* anim = m_model.anglesAnimation.get ();
    if (anim == nullptr || anim->maxFrame <= 0.0f) {
	return authored;
    }
    const float frame = std::fmod (g_Time * PropertyAnimation::FPS, anim->maxFrame);
    return {
	evalChannel (anim->channels[0], frame, authored.x),
	evalChannel (anim->channels[1], frame, authored.y),
	evalChannel (anim->channels[2], frame, authored.z),
    };
}

void CModel::updateMatrices () {
    const auto& cam = this->getScene ().getCamera ();
    const float sceneW = cam.getWidth ();
    const float sceneH = cam.getHeight ();

    if (!cam.isOrthogonal ()) {
	const glm::vec3 rawOrigin = m_model.origin->value->getVec3 ();
	const glm::vec3 rawAngles = this->effectiveAngles ();
	const glm::vec3 rawScale = m_model.scale->value->getVec3 ();

	m_modelMatrix = glm::translate (glm::mat4 (1.0f), rawOrigin);
	m_modelMatrix = glm::rotate (m_modelMatrix, rawAngles.z, glm::vec3 (0, 0, 1));
	m_modelMatrix = glm::rotate (m_modelMatrix, rawAngles.y, glm::vec3 (0, 1, 0));
	m_modelMatrix = glm::rotate (m_modelMatrix, rawAngles.x, glm::vec3 (1, 0, 0));
	m_modelMatrix = glm::scale (m_modelMatrix, rawScale);

	m_viewProjectionMatrix = cam.getProjection () * cam.getLookAt ();
	m_eyePosition = cam.getEye ();

	static const bool s_vpProbe = getenv ("LWE_CAMPROBE") != nullptr;
	static int s_vpProbeCount = 0;
	if (s_vpProbe && s_vpProbeCount < 3 && ++s_vpProbeCount > 0) {
	    const auto& m = m_viewProjectionMatrix;
	    for (int r = 0; r < 4; r++) {
		sLog.out ("LWE-VPPROBE row", r, ": ", m[0][r], " ", m[1][r], " ", m[2][r], " ", m[3][r]);
	    }
	}

	m_mvpMatrix = m_viewProjectionMatrix * m_modelMatrix;
	m_mvpMatrixInverse = glm::inverse (m_mvpMatrix);
	m_normalMatrix = glm::inverseTranspose (glm::mat3 (m_modelMatrix));
	return;
    }

    glm::vec3 origin = m_model.origin->value->getVec3 ();
    origin.x -= sceneW / 2.0f;
    origin.y = sceneH / 2.0f - origin.y;

    const glm::vec3 angles = this->effectiveAngles ();
    const glm::vec3 scale = m_model.scale->value->getVec3 ();

    m_modelMatrix = glm::translate (glm::mat4 (1.0f), origin);
    // Negate X and Z rotations to account for the Y-flipped coordinate system (CParticle convention)
    m_modelMatrix = glm::rotate (m_modelMatrix, -angles.z, glm::vec3 (0, 0, 1));
    m_modelMatrix = glm::rotate (m_modelMatrix, angles.y, glm::vec3 (0, 1, 0));
    m_modelMatrix = glm::rotate (m_modelMatrix, -angles.x, glm::vec3 (1, 0, 0));
    m_modelMatrix = glm::scale (m_modelMatrix, scale);
    m_modelMatrix = glm::scale (m_modelMatrix, glm::vec3 (1.0f, -1.0f, 1.0f));

    if (m_model.perspective) {
	// Same construction as CImage::buildScreenViewProjection: eye distance from the
	// SCENE fov (z=0 framing matches ortho), projection fov from perspectiveoverridefov
	const float sceneFov = glm::radians (cam.getFov ());
	const float overrideFov = cam.getOverrideFov ();
	const float projFov = overrideFov > 0.0f ? glm::radians (overrideFov) : sceneFov;
	const float eyeZ = (sceneH * 0.5f) / std::tan (projFov * 0.5f);
	const float nearz = std::max (cam.getNearZ (), 1.0f);
	const float farz = std::max (cam.getFarZ (), eyeZ + 10.0f * sceneH);

	const glm::mat4 proj = glm::perspective (projFov, sceneW / sceneH, nearz, farz);
	const glm::mat4 view
	    = glm::lookAt (glm::vec3 (0.0f, 0.0f, eyeZ), glm::vec3 (0.0f, 0.0f, 0.0f), glm::vec3 (0.0f, 1.0f, 0.0f));

	m_viewProjectionMatrix = proj * view;
	m_eyePosition = glm::vec3 (0.0f, 0.0f, eyeZ);
    } else {
	m_viewProjectionMatrix = cam.getProjection () * cam.getLookAt ();
	m_eyePosition = glm::vec3 (0.0f, 0.0f, 1000.0f);
    }

    m_mvpMatrix = m_viewProjectionMatrix * m_modelMatrix;
    m_mvpMatrixInverse = glm::inverse (m_mvpMatrix);
    m_normalMatrix = glm::inverseTranspose (glm::mat3 (m_modelMatrix));
}

void CModel::renderShadow (const glm::mat4& lightViewProjection) {
    if (!m_initialized || !m_model.visible->value->getBool ()) {
	return;
    }

    if (this->m_shadowProgram == GL_NONE) {
	static const char* vertexSource
	    = "#version 330 core\n"
	      "layout(location = 0) in vec3 aPos;\n"
	      "uniform mat4 uLightViewProjection;\n"
	      "uniform mat4 uModel;\n"
	      "void main() { gl_Position = uLightViewProjection * uModel * vec4(aPos, 1.0); }\n";
	static const char* fragmentSource = "#version 330 core\n"
					    "void main() { }\n";

	const auto compile = [] (GLenum type, const char* source) -> GLuint {
	    GLuint shader = glCreateShader (type);
	    glShaderSource (shader, 1, &source, nullptr);
	    glCompileShader (shader);
	    GLint status = GL_FALSE;
	    glGetShaderiv (shader, GL_COMPILE_STATUS, &status);
	    if (status != GL_TRUE) {
		glDeleteShader (shader);
		return GL_NONE;
	    }
	    return shader;
	};

	const GLuint vertex = compile (GL_VERTEX_SHADER, vertexSource);
	const GLuint fragment = compile (GL_FRAGMENT_SHADER, fragmentSource);
	if (vertex == GL_NONE || fragment == GL_NONE) {
	    sLog.error ("CModel shadow program failed to compile for ", m_model.modelFile);
	    return;
	}
	this->m_shadowProgram = glCreateProgram ();
	glAttachShader (this->m_shadowProgram, vertex);
	glAttachShader (this->m_shadowProgram, fragment);
	glLinkProgram (this->m_shadowProgram);
	glDeleteShader (vertex);
	glDeleteShader (fragment);
	this->m_shadowLightViewProjection = glGetUniformLocation (this->m_shadowProgram, "uLightViewProjection");
	this->m_shadowModel = glGetUniformLocation (this->m_shadowProgram, "uModel");
	glGenVertexArrays (1, &this->m_shadowVao);
    }

    this->updateMatrices ();

    GLint prevVao = 0;
    glGetIntegerv (GL_VERTEX_ARRAY_BINDING, &prevVao);
    glBindVertexArray (this->m_shadowVao);
    glUseProgram (this->m_shadowProgram);
    glUniformMatrix4fv (this->m_shadowLightViewProjection, 1, GL_FALSE, glm::value_ptr (lightViewProjection));
    glUniformMatrix4fv (this->m_shadowModel, 1, GL_FALSE, glm::value_ptr (this->m_modelMatrix));

    glFrontFace (GL_CCW);

    for (const auto& submesh : m_submeshes) {
	glBindBuffer (GL_ARRAY_BUFFER, submesh.vbo);
	glEnableVertexAttribArray (0);
	// position @0 in every layout; stride is per-submesh (48 static / 80 skinned, e1b04f4)
	glVertexAttribPointer (0, 3, GL_FLOAT, GL_FALSE, submesh.stride, reinterpret_cast<void*> (0));
	glBindBuffer (GL_ELEMENT_ARRAY_BUFFER, submesh.ebo);
	glDrawElements (GL_TRIANGLES, submesh.indexCount, GL_UNSIGNED_SHORT, nullptr);
    }

    glDisableVertexAttribArray (0);
    glBindVertexArray (static_cast<GLuint> (prevVao));
}

void CModel::render () {
    if (!m_initialized || !m_model.visible->value->getBool ()) {
	return;
    }

    this->updateMatrices ();

    for (const auto& submesh : m_submeshes) {
	submesh.pass->render ();
    }
}

const float& CModel::getBrightness () const { return m_brightness; }

const float& CModel::getUserAlpha () const { return m_model.alpha->value->getFloat (); }

const float& CModel::getAlpha () const { return m_model.alpha->value->getFloat (); }

const glm::vec3& CModel::getColor () const { return m_model.color->value->getVec3 (); }

glm::vec4 CModel::getColor4 () const { return { this->getColor (), this->getAlpha () }; }

const glm::vec3& CModel::getCompositeColor () const { return this->getColor (); }
