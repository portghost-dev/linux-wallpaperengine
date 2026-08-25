#include "CPass.h"
#include <algorithm>
#include <cmath>
#include <fstream>
#include <sstream>
#include <unordered_map>
#include <utility>

#include "WallpaperEngine/Render/Wallpapers/CScene.h"

#include "WallpaperEngine/Render/Helpers/ContextAware.h"

#include "WallpaperEngine/Data/Model/Effect.h"
#include "WallpaperEngine/Data/Model/Material.h"

#include "WallpaperEngine/Render/CFBO.h"
#include "WallpaperEngine/Render/Objects/CImage.h"

#include "WallpaperEngine/Render/Shaders/Variables/ShaderVariable.h"
#include "WallpaperEngine/Render/Shaders/Variables/ShaderVariableFloat.h"
#include "WallpaperEngine/Render/Shaders/Variables/ShaderVariableInteger.h"
#include "WallpaperEngine/Render/Shaders/Variables/ShaderVariableVector2.h"
#include "WallpaperEngine/Render/Shaders/Variables/ShaderVariableVector3.h"
#include "WallpaperEngine/Render/Shaders/Variables/ShaderVariableVector4.h"

#include "WallpaperEngine/Logging/Log.h"

using namespace WallpaperEngine;
using namespace WallpaperEngine::Render;
using namespace WallpaperEngine::Render::Objects;

using namespace WallpaperEngine::Render::Shaders::Variables;
using namespace WallpaperEngine::Render::Objects::Effects;

extern float g_Time;
extern float g_Daytime;
extern float g_LweClassicDivisor;
extern float g_LweFalloffExp;

namespace {
class SolidColorTexture final : public WallpaperEngine::Render::TextureProvider {
public:
    explicit SolidColorTexture (const glm::vec4 color) {
	glGenTextures (1, &m_textureID);
	glBindTexture (GL_TEXTURE_2D, m_textureID);
	const uint8_t pixels[4] = {
	    static_cast<uint8_t> (std::clamp (color.r, 0.0f, 1.0f) * 255.0f),
	    static_cast<uint8_t> (std::clamp (color.g, 0.0f, 1.0f) * 255.0f),
	    static_cast<uint8_t> (std::clamp (color.b, 0.0f, 1.0f) * 255.0f),
	    static_cast<uint8_t> (std::clamp (color.a, 0.0f, 1.0f) * 255.0f),
	};
	glTexImage2D (GL_TEXTURE_2D, 0, GL_RGBA8, 1, 1, 0, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
	glBindTexture (GL_TEXTURE_2D, 0);
    }
    ~SolidColorTexture () override { glDeleteTextures (1, &m_textureID); }
    SolidColorTexture (const SolidColorTexture&) = delete;
    SolidColorTexture& operator= (const SolidColorTexture&) = delete;

    [[nodiscard]] GLuint getTextureID (uint32_t) const override { return m_textureID; }
    [[nodiscard]] uint32_t getTextureWidth (uint32_t) const override { return 1; }
    [[nodiscard]] uint32_t getTextureHeight (uint32_t) const override { return 1; }
    [[nodiscard]] uint32_t getRealWidth () const override { return 1; }
    [[nodiscard]] uint32_t getRealHeight () const override { return 1; }
    [[nodiscard]] TextureFormat getFormat () const override { return TextureFormat_ARGB8888; }
    [[nodiscard]] uint32_t getFlags () const override { return 0; }
    [[nodiscard]] const std::vector<FrameSharedPtr>& getFrames () const override { return m_frames; }
    [[nodiscard]] const glm::vec4* getResolution () const override { return &m_resolution; }
    [[nodiscard]] bool isAnimated () const override { return false; }
    [[nodiscard]] uint32_t getSpritesheetCols () const override { return 0; }
    [[nodiscard]] uint32_t getSpritesheetRows () const override { return 0; }
    [[nodiscard]] uint32_t getSpritesheetFrames () const override { return 0; }
    [[nodiscard]] float getSpritesheetDuration () const override { return 0.0f; }
    [[nodiscard]] bool isReady () const override { return true; }
    void incrementUsageCount () const override { }
    void decrementUsageCount () const override { }
    void update () const override { }

private:
    GLuint m_textureID = 0;
    glm::vec4 m_resolution { 1.0f, 1.0f, 1.0f, 1.0f };
    std::vector<FrameSharedPtr> m_frames {};
};
} // namespace

const TextureMap DEFAULT_BINDS = {};
const ImageEffectPassOverride DEFAULT_OVERRIDE = {};

namespace {
std::string textureSizeLabel (const std::shared_ptr<const TextureProvider>& texture) {
    if (texture == nullptr) {
	return "<null>";
    }

    return std::to_string (texture->getRealWidth ()) + "x" + std::to_string (texture->getRealHeight ());
}
}

CPass::CPass (
    CRenderable& renderable, std::shared_ptr<const FBOProvider> fboProvider, const MaterialPass& pass,
    std::optional<std::reference_wrapper<const ImageEffectPassOverride>> override,
    std::optional<std::reference_wrapper<const TextureMap>> binds,
    std::optional<std::reference_wrapper<std::string>> target
) :
    Helpers::ContextAware (renderable), m_renderable (renderable), m_fboProvider (std::move (fboProvider)),
    m_pass (pass), m_binds (binds.has_value () ? binds.value ().get () : DEFAULT_BINDS),
    m_override (override.has_value () ? override.value ().get () : DEFAULT_OVERRIDE), m_target (target),
    m_blendingmode (pass.blending), m_vao (GL_NONE) {
    this->setupShaders ();
    glGenVertexArrays (1, &m_vao);
}

CPass::~CPass () {
    glDeleteVertexArrays (1, &m_vao);
    this->m_vao = GL_NONE;

    // freed before the early return below, which the invalid-program path takes
    delete this->m_shader;
    this->m_shader = nullptr;

    // destroy shader programs
    if (!glIsProgram (this->m_programID)) {
	return; // program already invalid or deleted
    }

    GLint shaderCount = 0;
    glGetProgramiv (this->m_programID, GL_ATTACHED_SHADERS, &shaderCount);

    if (shaderCount > 0) {
	std::vector<GLuint> attachedShaders (shaderCount);
	glGetAttachedShaders (this->m_programID, shaderCount, nullptr, attachedShaders.data ());

	for (GLuint s : attachedShaders) {
	    if (glIsShader (s)) {
		glDeleteShader (s);
	    }
	}
    }

    glDeleteProgram (this->m_programID);
    this->m_programID = 0;
}

std::shared_ptr<const TextureProvider> CPass::resolveTexture (
    std::shared_ptr<const TextureProvider> expected, int index, std::shared_ptr<const TextureProvider> previous
) {
    if (expected == nullptr) {
	if (const auto it = this->m_fbos.find (index); it != this->m_fbos.end ()) {
	    expected = it->second;
	}
    }

    // first check in the binds and replace it if necessary
    const auto it = this->m_binds.find (index);

    if (it == this->m_binds.end ()) {
	return expected;
    }

    // a bind named "previous" is just another way of telling it to use whatever texture there was already
    if (it->second == "previous") {
	return this->m_previousInput ?: (previous ?: expected);
    }

    // the bind actually has a name, search the FBO in the effect and return it
    return this->resolveFBO (it->second);
}

std::shared_ptr<const CFBO> CPass::resolveFBO (const std::string& name) const {
    auto fbo = this->m_fboProvider->find (name);

    if (fbo == nullptr) {
	sLog.exception ("Tried to resolve and FBO without any luck: ", name);
    }

    return fbo;
}

void CPass::setupRenderFramebuffer () const {
    glBindFramebuffer (GL_FRAMEBUFFER, this->m_resolvedDrawTo->getFramebuffer ());

    // set proper viewport based on what we're drawing to
    glViewport (0, 0, this->m_resolvedDrawTo->getRealWidth (), this->m_resolvedDrawTo->getRealHeight ());

    // set texture blending
    switch (this->getBlendingMode ()) {
	case BlendingMode_Translucent:
	    glEnable (GL_BLEND);
	    glBlendFuncSeparate (GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
	    break;
	case BlendingMode_Additive:
	    glEnable (GL_BLEND);
	    glBlendFuncSeparate (GL_SRC_ALPHA, GL_ONE, GL_SRC_ALPHA, GL_ONE);
	    break;
	case BlendingMode_Normal:
	    glEnable (GL_BLEND);
	    glBlendFuncSeparate (GL_ONE, GL_ZERO, GL_ONE, GL_ZERO);
	    break;
	default:
	    glDisable (GL_BLEND);
	    break;
    }

    switch (this->m_pass.depthtest) {
	case DepthtestMode_Enabled:
	    glEnable (GL_DEPTH_TEST);
	    glDepthFunc (GL_LEQUAL);
	    break;
	case DepthtestMode_Disabled:
	default:
	    glDisable (GL_DEPTH_TEST);
	    break;
    }

    switch (this->m_pass.cullmode) {
	case CullingMode_Normal:
	    glEnable (GL_CULL_FACE);
	    break;

	case CullingMode_Disable:
	default:
	    glDisable (GL_CULL_FACE);
	    break;
    }

    if (this->m_pass.blending == BlendingMode_Translucent) {
	glDepthMask (false);
	return;
    }

    switch (this->m_pass.depthwrite) {
	case DepthwriteMode_Enabled:
	    glDepthMask (true);
	    break;

	case DepthwriteMode_Disabled:
	default:
	    glDepthMask (false);
	    break;
    }
}

void CPass::setupRenderTexture () {
    // use the shader we have registered
    glUseProgram (this->m_programID);

    auto texture0 = this->resolveTexture0 ();
    const auto animation = this->resolveTextureAnimationState (texture0);

    this->bindTextureUnit (0, texture0, animation.currentTexture);
    this->bindTextureOverrides (animation.currentTexture, texture0);

    if (texture0 != nullptr) {
	this->m_texture0Resolution = *texture0->getResolution ();
    }

    // used in animations when one of the frames is vertical instead of horizontal
    // rotation with translation = origin and end of the image to display
    if (this->g_Texture0Rotation != -1) {
	glUniform4f (
	    this->g_Texture0Rotation, animation.rotation.x, animation.rotation.y, animation.rotation.z,
	    animation.rotation.w
	);
    }
    // this actually picks the origin point of the image from the atlast
    if (this->g_Texture0Translation != -1) {
	glUniform2f (this->g_Texture0Translation, animation.translation.x, animation.translation.y);
    }

    if (this->m_usesSceneLights) {
	const auto& shadowAtlas = this->m_renderable.getScene ().getShadowAtlas ();
	if (shadowAtlas != nullptr && shadowAtlas->getDepthTexture () != GL_NONE) {
	    glActiveTexture (GL_TEXTURE16);
	    glBindTexture (GL_TEXTURE_2D, shadowAtlas->getDepthTexture ());
	    glActiveTexture (GL_TEXTURE0);
	}
    }
}

std::shared_ptr<const TextureProvider> CPass::resolveTexture0 () {
    auto texture0 = this->resolveTexture (this->m_input, 0, this->m_input);
    const auto it = this->m_textures.find (0);

    if (it == this->m_textures.end ()) {
	return texture0;
    }

    auto& chain = it->second;

    do {
	texture0 = chain->texture;

	if (texture0 == nullptr) {
	    if (this->m_previousInput != nullptr && this->m_previousInput->isReady ()) {
		return this->m_previousInput;
	    }

	    if (this->m_input != nullptr && this->m_input->isReady ()) {
		return this->m_input;
	    }
	} else if (texture0->isReady ()) {
	    return texture0;
	}

	chain = chain->next;
    } while (chain != nullptr);

    // got to the end of the chain, use previous input or current input if available
    if (this->m_previousInput != nullptr && this->m_previousInput->isReady ()) {
	return this->m_previousInput;
    }

    // last resort, doesn't matter if the input is ready or not
    return this->m_input;
}

CPass::TextureAnimationState
CPass::resolveTextureAnimationState (const std::shared_ptr<const TextureProvider>& texture) const {
    TextureAnimationState state;

    if (texture == nullptr || !texture->isAnimated ()) {
	return state;
    }

    double animationTime = 0.0;
    for (const auto& frameCur : texture->getFrames ()) {
	animationTime += frameCur->frametime;
    }
    if (animationTime <= 0.0) {
	return state;
    }

    const auto& playback = this->m_renderable.getTextureAnimationPlayback ();
    if (playback.controlled && playback.frameOverride >= 0) {
	const auto& frames = texture->getFrames ();
	const auto& frameCur = frames[std::min (static_cast<size_t> (playback.frameOverride), frames.size () - 1)];
	state.currentTexture = frameCur->frameNumber;
	state.translation.x = frameCur->x / texture->getTextureWidth (state.currentTexture);
	state.translation.y = frameCur->y / texture->getTextureHeight (state.currentTexture);
	state.rotation.x = frameCur->width1 / static_cast<float> (texture->getTextureWidth (state.currentTexture));
	state.rotation.y = frameCur->width2 / static_cast<float> (texture->getTextureWidth (state.currentTexture));
	state.rotation.z = frameCur->height2 / static_cast<float> (texture->getTextureHeight (state.currentTexture));
	state.rotation.w = frameCur->height1 / static_cast<float> (texture->getTextureHeight (state.currentTexture));
	return state;
    }
    const double animClock
	= playback.controlled && !playback.playing ? playback.baseTime : static_cast<double> (g_Time);

    double currentRenderTime = fmod (animClock, animationTime);
    const double animPos = currentRenderTime;

    for (const auto& frameCur : texture->getFrames ()) {
	currentRenderTime -= frameCur->frametime;

	if (currentRenderTime > 0.0f) {
	    continue;
	}

	state.currentTexture = frameCur->frameNumber;
	state.translation.x = frameCur->x / texture->getTextureWidth (state.currentTexture);
	state.translation.y = frameCur->y / texture->getTextureHeight (state.currentTexture);

	state.rotation.x = frameCur->width1 / static_cast<float> (texture->getTextureWidth (state.currentTexture));
	state.rotation.y = frameCur->width2 / static_cast<float> (texture->getTextureWidth (state.currentTexture));
	state.rotation.z = frameCur->height2 / static_cast<float> (texture->getTextureHeight (state.currentTexture));
	state.rotation.w = frameCur->height1 / static_cast<float> (texture->getTextureHeight (state.currentTexture));
	break;
    }

    static const bool s_animStats = getenv ("LWE_ANIMSTATS") != nullptr;
    if (s_animStats) {
	struct AnimStat {
	    double windowStart = 0.0;
	    uint64_t calls = 0;
	    uint64_t advances = 0;
	    uint64_t wraps = 0;
	    uint32_t lastFrame = UINT32_MAX;
	    double lastPos = -1.0;
	    bool announced = false;
	};
	static std::unordered_map<const void*, AnimStat> s_stats;
	auto& st = s_stats[static_cast<const void*> (this)];
	const double wall = static_cast<double> (this->getContext ().getDriver ().getRenderTime ());
	if (!st.announced) {
	    st.announced = true;
	    st.windowStart = wall;
	    double texFrametimeSum = 0.0;
	    for (const auto& fr : texture->getFrames ()) {
		texFrametimeSum += fr->frametime;
	    }
	    sLog.out (
		"LWE-ANIMSTATS pass=", static_cast<const void*> (this), " nframes=", texture->getFrames ().size (),
		" animationTime=", this->m_renderable.getAnimationTime (), " texFrametimeSum=", texFrametimeSum,
		" spritesheetDur=", texture->getSpritesheetDuration ()
	    );
	}
	st.calls++;
	if (state.currentTexture != st.lastFrame) {
	    st.advances++;
	    st.lastFrame = state.currentTexture;
	}
	if (animPos < st.lastPos) {
	    st.wraps++;
	}
	st.lastPos = animPos;
	const double window = wall - st.windowStart;
	if (window >= 5.0) {
	    sLog.out (
		"LWE-ANIMSTATS pass=", static_cast<const void*> (this), " win=", static_cast<float> (window),
		" calls_s=", static_cast<float> (static_cast<double> (st.calls) / window),
		" advances_s=", static_cast<float> (static_cast<double> (st.advances) / window),
		" cycles_s=", static_cast<float> (static_cast<double> (st.wraps) / window)
	    );
	    st.windowStart = wall;
	    st.calls = 0;
	    st.advances = 0;
	    st.wraps = 0;
	}
    }

    return state;
}

void CPass::bindTextureUnit (int index, const std::shared_ptr<const TextureProvider>& texture, uint32_t frame) const {
    if (texture == nullptr) {
	return;
    }

    glActiveTexture (GL_TEXTURE0 + index);
    glBindTexture (GL_TEXTURE_2D, texture->getTextureID (frame));
}

void CPass::bindTextureOverrides (uint32_t currentTexture, std::shared_ptr<const TextureProvider>& texture0) const {
    static const char* s_bindProbe = getenv ("LWE_PASSPROBE");
    static int s_bindProbeRuns = 0;
    const bool probing = s_bindProbe != nullptr && this->m_renderable.getId () == atoi (s_bindProbe)
	&& s_bindProbeRuns < 400 && ++s_bindProbeRuns > 0;

    if (probing) {
	sLog.out (
	    "LWE-BINDPROBE pass=", this, " shader=", this->m_pass.shader, " entries=", this->m_textures.size (),
	    " binds=", this->m_binds.size (), " input=", textureSizeLabel (this->m_input),
	    " inputTexid=", this->m_input != nullptr ? this->m_input->getTextureID (0) : 0,
	    " prev=", textureSizeLabel (this->m_previousInput),
	    " DRAWTO=", this->m_drawTo != nullptr ? this->m_drawTo->getName () : std::string ("<null>"),
	    " target=", this->m_target.has_value () ? this->m_target->get () : std::string ("<none>")
	);
    }

    for (auto [index, chain] : this->m_textures) {
	// find the expected texture
	auto expectedTexture = chain->texture;

	do {
	    if (expectedTexture == nullptr) {
		if (this->m_previousInput != nullptr && this->m_previousInput->isReady ()) {
		    expectedTexture = this->m_previousInput;
		    break;
		}

		if (this->m_input != nullptr && this->m_input->isReady ()) {
		    expectedTexture = this->m_input;
		    break;
		}
	    } else if (expectedTexture->isReady ()) {
		break;
	    }

	    chain = chain->next;
	    expectedTexture = chain == nullptr ? nullptr : chain->texture;
	} while (chain != nullptr);

	if (expectedTexture == nullptr && this->m_previousInput != nullptr && this->m_previousInput->isReady ()) {
	    expectedTexture = this->m_previousInput;
	}

	if (expectedTexture == nullptr) {
	    expectedTexture = this->m_input;
	}

	if (probing) {
	    const auto* asFBO = dynamic_cast<const CFBO*> (expectedTexture.get ());
	    GLint wrapS = 0;
	    GLint wrapT = 0;
	    if (expectedTexture != nullptr) {
		const GLuint id = expectedTexture->getTextureID (index == 0 ? currentTexture : 0);
		glGetTextureParameteriv (id, GL_TEXTURE_WRAP_S, &wrapS);
		glGetTextureParameteriv (id, GL_TEXTURE_WRAP_T, &wrapT);
	    }
	    sLog.out (
		"LWE-BINDPROBE pass=", this, " idx=", index, " wrapS=0x", std::hex, wrapS, " wrapT=0x", wrapT, std::dec,
		" tex=", textureSizeLabel (expectedTexture), " texid=",
		expectedTexture != nullptr ? expectedTexture->getTextureID (index == 0 ? currentTexture : 0) : 0,
		" name=", asFBO != nullptr ? asFBO->getName () : "<tex>",
		" drawToTexid=", this->m_drawTo != nullptr ? this->m_drawTo->getTextureID (0) : 0
	    );
	}

	this->bindTextureUnit (index, expectedTexture, index == 0 ? currentTexture : 0);

	if (index == 0) {
	    texture0 = expectedTexture;
	}
    }
}

static bool unifValsEnabled () {
    static const bool enabled = getenv ("LWE_UNIFVALS") != nullptr;
    return enabled;
}

static void unifValsDump (
    const void* pass, const std::string& shader, const char* src, const std::string& name, int type, int count,
    const void* ptr
) {
    std::ostringstream ss;
    ss.precision (9);
    ss << "LWE-UNIFVALS pass=" << pass << " shader=" << shader << " t=" << g_Time << " src=" << src << " name=" << name
       << " type=" << type << " n=" << count << " v=[";
    const auto floats = [&ss] (const float* v, int n) {
	for (int i = 0; i < n; i++) {
	    ss << (i ? "," : "") << v[i];
	}
    };
    switch (type) {
	case 0:
	    floats (static_cast<const float*> (ptr), count);
	    break;
	case 1:
	    floats (static_cast<const float*> (ptr), 9);
	    break;
	case 2:
	    floats (static_cast<const float*> (ptr), 16);
	    break;
	case 3:
	    {
		const auto* v = static_cast<const int*> (ptr);
		for (int i = 0; i < count; i++) {
		    ss << (i ? "," : "") << v[i];
		}
		break;
	    }
	case 4:
	    floats (static_cast<const float*> (ptr), 2);
	    break;
	case 5:
	    floats (static_cast<const float*> (ptr), 3);
	    break;
	case 6:
	    floats (static_cast<const float*> (ptr), 4 * count);
	    break;
	case 7:
	    {
		const auto* v = static_cast<const double*> (ptr);
		for (int i = 0; i < count; i++) {
		    ss << (i ? "," : "") << v[i];
		}
		break;
	    }
    }
    ss << "]";
    sLog.out (ss.str ());
}

void CPass::setupRenderReferenceUniforms () {
    // add reference uniforms
    for (const auto& value : this->m_referenceUniforms | std::views::values) {
	if (unifValsEnabled ()) {
	    unifValsDump (this, this->m_pass.shader, "ref", value->name, value->type, 1, *value->value);
	}
	switch (value->type) {
	    case Double:
		glUniform1d (value->id, *static_cast<const double*> (*value->value));
		break;
	    case Float:
		glUniform1f (value->id, *static_cast<const float*> (*value->value));
		break;
	    case Integer:
		glUniform1i (value->id, *static_cast<const int*> (*value->value));
		break;
	    case Vector4:
		glUniform4fv (value->id, 1, glm::value_ptr (*static_cast<const glm::vec4*> (*value->value)));
		break;
	    case Vector3:
		glUniform3fv (value->id, 1, glm::value_ptr (*static_cast<const glm::vec3*> (*value->value)));
		break;
	    case Vector2:
		glUniform2fv (value->id, 1, glm::value_ptr (*static_cast<const glm::vec2*> (*value->value)));
		break;
	    case Matrix4:
		glUniformMatrix4fv (
		    value->id, 1, GL_FALSE, glm::value_ptr (*static_cast<const glm::mat4*> (*value->value))
		);
		break;
	    case Matrix3:
		glUniformMatrix3fv (
		    value->id, 1, GL_FALSE, glm::value_ptr (*static_cast<const glm::mat3*> (*value->value))
		);
		break;
	}
    }
}

void CPass::setupRenderUniforms () {
    // add uniforms
    for (const auto& value : this->m_uniforms | std::views::values) {
	if (unifValsEnabled ()) {
	    unifValsDump (this, this->m_pass.shader, "uni", value->name, value->type, value->count, value->value);
	}
	switch (value->type) {
	    case Double:
		glUniform1dv (value->id, value->count, static_cast<const double*> (value->value));
		break;
	    case Float:
		glUniform1fv (value->id, value->count, static_cast<const float*> (value->value));
		break;
	    case Integer:
		glUniform1iv (value->id, value->count, static_cast<const int*> (value->value));
		break;
	    case Vector4:
		glUniform4fv (value->id, value->count, glm::value_ptr (*static_cast<const glm::vec4*> (value->value)));
		break;
	    case Vector3:
		// count-aware since the classic v2 light interface ships vec3 arrays
		// (g_LightsPosition[4]); single registrations keep count = 1
		glUniform3fv (value->id, value->count, glm::value_ptr (*static_cast<const glm::vec3*> (value->value)));
		break;
	    case Vector2:
		glUniform2fv (value->id, 1, glm::value_ptr (*static_cast<const glm::vec2*> (value->value)));
		break;
	    case Matrix4:
		// count-aware since the shadow stage ships mat4 arrays; single registrations keep count = 1
		glUniformMatrix4fv (
		    value->id, value->count, GL_FALSE, glm::value_ptr (*static_cast<const glm::mat4*> (value->value))
		);
		break;
	    case Matrix3:
		glUniformMatrix3fv (
		    value->id, 1, GL_FALSE, glm::value_ptr (*static_cast<const glm::mat3*> (value->value))
		);
		break;
	}
    }
}

void CPass::setupRenderableUniforms () const {
    if (this->g_AlphaLocation != -1) {
	glUniform1f (this->g_AlphaLocation, this->m_renderable.getAlpha ());
    }

    if (this->g_ColorLocation != -1) {
	const glm::vec3 color
	    = this->m_renderable.getColor () * (this->m_foldBrightness ? this->m_renderable.getBrightness () : 1.0f);
	glUniform3fv (this->g_ColorLocation, 1, glm::value_ptr (color));
    }

    if (this->g_Color4Location != -1) {
	const glm::vec4 color4 = this->m_renderable.getColor4 ();
	glUniform4fv (this->g_Color4Location, 1, glm::value_ptr (color4));
    }

    if (this->g_BrightnessLocation != -1) {
	glUniform1f (this->g_BrightnessLocation, this->m_renderable.getBrightness ());
    }

    if (this->g_UserAlphaLocation != -1) {
	glUniform1f (this->g_UserAlphaLocation, this->m_renderable.getUserAlpha ());
    }
}

void CPass::setupRenderAttributes () const {
    if (this->m_setupAttribsCallback) {
	this->m_setupAttribsCallback ();
	return;
    }

    for (const auto& cur : this->m_attribs) {
	glEnableVertexAttribArray (cur->id);
	glBindBuffer (GL_ARRAY_BUFFER, *cur->value);
	glVertexAttribPointer (cur->id, cur->elements, cur->type, GL_FALSE, 0, nullptr);

#if !NDEBUG
	glObjectLabel (
	    GL_BUFFER, *cur->value, -1,
	    ("Image " + std::to_string (this->m_renderable.getId ()) + " Pass " + this->m_pass.shader + " " + cur->name)
		.c_str ()
	);
#endif /* DEBUG */
    }
}

void CPass::renderGeometry () const {
    if (this->m_drawGeometryCallback) {
	this->m_drawGeometryCallback ();
	return;
    }

    // start actual rendering now
    glBindBuffer (GL_ARRAY_BUFFER, this->a_Position);
    glDrawArrays (GL_TRIANGLES, 0, 6);
}

void CPass::cleanupRenderSetup () {
    if (this->m_cleanupAttribsCallback) {
	this->m_cleanupAttribsCallback ();
    } else {
	// disable vertex attribs array
	for (const auto& cur : this->m_attribs) {
	    glDisableVertexAttribArray (cur->id);
	}
    }

    // unbind all the used textures
    glActiveTexture (GL_TEXTURE0);
    glBindTexture (GL_TEXTURE_2D, 0);

    // continue on the map from the second texture
    for (const auto& index : this->m_textures | std::views::keys) {
	glActiveTexture (GL_TEXTURE0 + index);
	glBindTexture (GL_TEXTURE_2D, 0);
    }
}

void CPass::render () {
    // set the VAO for now
    glBindVertexArray (this->m_vao);

    const auto& debug = this->getContext ().getApp ().getContext ().settings.render.debug;
    if (debug.passLog) {
	sLog.out (
	    "Render pass object=", this->m_renderable.getId (), " shader=", this->m_pass.shader,
	    " target=", this->m_target.has_value () ? this->m_target.value ().get () : std::string ("<screen/local>"),
	    " drawTo=", this->m_drawTo ? this->m_drawTo->getName () : std::string ("<null>"),
	    " drawSize=", textureSizeLabel (this->m_drawTo), " inputSize=", textureSizeLabel (this->m_input)
	);
	for (const auto* uniformName : { "g_TintColor", "g_CompositeColor", "g_BlendAlpha", "g_CompositeAlpha" }) {
	    const auto uniform = this->m_uniforms.find (uniformName);
	    if (uniform == this->m_uniforms.end ()) {
		continue;
	    }

	    switch (uniform->second->type) {
		case Vector3:
		    {
			const auto* v = static_cast<const glm::vec3*> (uniform->second->value);
			sLog.out ("  uniform ", uniformName, "=", v->x, " ", v->y, " ", v->z);
			break;
		    }
		case Float:
		    {
			const auto* v = static_cast<const float*> (uniform->second->value);
			sLog.out ("  uniform ", uniformName, "=", *v);
			break;
		    }
		default:
		    break;
	    }
	}
    }

    this->m_resolvedDrawTo = this->m_renderable.getScene ().resolveRenderTarget (this->m_drawTo);

    if (this->m_resolvedDrawTo == nullptr) {
	sLog.error ("Skipping render pass for object ", this->m_renderable.getId (), ": no destination FBO set");
	return;
    }

    this->m_frameTime = this->m_renderable.getScene ().getDeltaTime ();

    if (this->m_input == nullptr && !this->hasGeometryCallback ()) {
	sLog.error ("Skipping render pass for object ", this->m_renderable.getId (), ": no input texture set");
	return;
    }

    this->setupRenderFramebuffer ();
    this->setupRenderTexture ();

    // LightingV1: repack the scene's current light snapshot into the staging block the
    // registered uniform pointers read from (only when this program consumes it)
    if (this->m_usesSceneLights) {
	this->refreshLightStage ();
    }

    this->setupRenderUniforms ();
    this->setupRenderReferenceUniforms ();
    this->setupRenderableUniforms ();
    this->setupRenderAttributes ();

    static const char* s_passProbe = getenv ("LWE_PASSPROBE");
    static int s_probeRuns = 0;
    const bool probeByDest = s_passProbe != nullptr && strcmp (s_passProbe, "final") == 0 && this->m_drawTo != nullptr
	&& (this->m_drawTo->getName () == "_rt_FullFrameBuffer"
	    || this->m_drawTo->getName ().rfind ("_rt_imageLayerComposite_-1", 0) == 0);
    const bool probeNow = s_passProbe != nullptr && (probeByDest || this->m_renderable.getId () == atoi (s_passProbe))
	&& s_probeRuns < 20000 && ++s_probeRuns > 0;
    const auto profileTarget = [this] (const char* tag) {
	const int fw = static_cast<int> (this->m_resolvedDrawTo->getRealWidth ());
	const int fh = static_cast<int> (this->m_resolvedDrawTo->getRealHeight ());
	std::vector<unsigned char> px (static_cast<size_t> (fw) * fh * 4);
	glReadPixels (0, 0, fw, fh, GL_RGBA, GL_UNSIGNED_BYTE, px.data ());
	double sum[4] = {};
	double third[3] = {};
	size_t thirdN[3] = {};
	size_t lit = 0, n = 0;
	for (int y = 0; y < fh; y += 8) {
	    for (int x = 0; x < fw; x += 8) {
		const size_t o = (static_cast<size_t> (y) * fw + x) * 4;
		for (int c = 0; c < 4; c++) {
		    sum[c] += px[o + c];
		}
		const int t = std::min (2, x * 3 / fw);
		third[t] += px[o] + px[o + 1] + px[o + 2];
		thirdN[t]++;
		lit += (px[o] | px[o + 1] | px[o + 2]) > 8 ? 1 : 0;
		n++;
	    }
	}
	sLog.out (
	    "LWE-PASSPROBE obj=", this->m_renderable.getId (), " pass=", this, " shader=", this->m_pass.shader, " ",
	    tag, " drawTo=", this->m_drawTo->getName (), " drawTex=", this->m_resolvedDrawTo->getTextureID (0),
	    " inputTex=", this->m_input != nullptr ? this->m_input->getTextureID (0) : 0, " ", fw, "x", fh,
	    " meanRGBA=", sum[0] / n, ",", sum[1] / n, ",", sum[2] / n, ",", sum[3] / n,
	    " litFrac=", static_cast<double> (lit) / n, " thirdsLCR=", third[0] / std::max<size_t> (1, thirdN[0]), ",",
	    third[1] / std::max<size_t> (1, thirdN[1]), ",", third[2] / std::max<size_t> (1, thirdN[2])
	);
	{
	    double colSum[64] = {};
	    double colA[64] = {};
	    size_t colN[64] = {};
	    for (int y = 0; y < fh; y += 8) {
		for (int x = 0; x < fw; x += 4) {
		    const size_t o = (static_cast<size_t> (y) * fw + x) * 4;
		    const int b = std::min (63, x * 64 / fw);
		    colSum[b] += px[o] + px[o + 1] + px[o + 2];
		    colA[b] += px[o + 3];
		    colN[b]++;
		}
	    }
	    std::ostringstream cols;
	    cols << "LWE-COLPROFILE obj=" << this->m_renderable.getId () << " " << tag << " ";
	    for (int b = 0; b < 64; b++) {
		cols << (b ? "," : "") << static_cast<int> (colSum[b] / std::max<size_t> (1, colN[b]));
	    }
	    sLog.out (cols.str ());
	    std::ostringstream colsA;
	    colsA << "LWE-COLPROFILE-A obj=" << this->m_renderable.getId () << " " << tag << " ";
	    for (int b = 0; b < 64; b++) {
		colsA << (b ? "," : "") << static_cast<int> (colA[b] / std::max<size_t> (1, colN[b]));
	    }
	    sLog.out (colsA.str ());
	    {
		double rowSum[64] = {};
		size_t rowN[64] = {};
		for (int y = 0; y < fh; y += 4) {
		    for (int x = 0; x < fw; x += 8) {
			const size_t o = (static_cast<size_t> (y) * fw + x) * 4;
			const int b = std::min (63, y * 64 / fh);
			rowSum[b] += px[o] + px[o + 1] + px[o + 2];
			rowN[b]++;
		    }
		}
		std::ostringstream rows;
		rows << "LWE-ROWPROFILE obj=" << this->m_renderable.getId () << " " << tag << " ";
		for (int b = 0; b < 64; b++) {
		    rows << (b ? "," : "") << static_cast<int> (rowSum[b] / std::max<size_t> (1, rowN[b]));
		}
		sLog.out (rows.str ());
	    }
	    static const bool s_ppmDump = getenv ("LWE_PASSPROBE_DUMP") != nullptr;
	    static bool s_ppmDone = false;
	    if (s_ppmDump && !s_ppmDone && std::string (tag) == "post") {
		s_ppmDone = true;
		const char* home = getenv ("HOME");
		if (home != nullptr) {
		    std::ofstream out (std::string (home) + "/.local/state/lwe/passprobe-post.ppm", std::ios::binary);
		    out << "P6\n" << fw << " " << fh << "\n255\n";
		    for (int y = 0; y < fh; y++) {
			for (int x = 0; x < fw; x++) {
			    const size_t o = (static_cast<size_t> (y) * fw + x) * 4;
			    out.put (static_cast<char> (px[o]));
			    out.put (static_cast<char> (px[o + 1]));
			    out.put (static_cast<char> (px[o + 2]));
			}
		    }
		    sLog.out ("LWE-PASSPROBE wrote passprobe-post.ppm");
		}
	    }
	}
    };

    if (probeNow) {
	profileTarget ("pre");
    }

    this->renderGeometry ();

    if (probeNow) {
	profileTarget ("post");
	sLog.out ("LWE-PASSPROBE glerr=", glGetError ());
    }

    this->cleanupRenderSetup ();
}

std::shared_ptr<const FBOProvider> CPass::getFBOProvider () const { return this->m_fboProvider; }

const CRenderable& CPass::getRenderable () const { return this->m_renderable; }

void CPass::setDestination (std::shared_ptr<const CFBO> drawTo) { this->m_drawTo = std::move (drawTo); }

void CPass::setInput (std::shared_ptr<const TextureProvider> input) { this->m_input = std::move (input); }

void CPass::setPreviousInput (std::shared_ptr<const TextureProvider> input) {
    this->m_previousInput = std::move (input);
}

void CPass::setModelViewProjectionMatrix (const glm::mat4* projection) {
    this->m_modelViewProjectionMatrix = projection;
}

void CPass::setModelViewProjectionMatrixInverse (const glm::mat4* projection) {
    this->m_modelViewProjectionMatrixInverse = projection;
}

void CPass::setModelMatrix (const glm::mat4* model) { this->m_modelMatrix = model; }

void CPass::setViewProjectionMatrix (const glm::mat4* viewProjection) { this->m_viewProjectionMatrix = viewProjection; }

void CPass::setBlendingMode (BlendingMode blendingmode) { this->m_blendingmode = blendingmode; }

BlendingMode CPass::getBlendingMode () const { return this->m_blendingmode; }

void CPass::setTexCoord (GLuint texcoord) { this->a_TexCoord = texcoord; }

void CPass::setPosition (GLuint position) { this->a_Position = position; }

const MaterialPass& CPass::getPass () const { return this->m_pass; }

std::optional<std::reference_wrapper<std::string>> CPass::getTarget () const { return this->m_target; }

Render::Shaders::Shader* CPass::getShader () const { return this->m_shader; }

GLuint CPass::getProgramID () const { return this->m_programID; }

void CPass::setGeometryCallback (
    GeometryCallback setupAttribs, GeometryCallback drawGeometry, GeometryCallback cleanupAttribs
) {
    this->m_setupAttribsCallback = std::move (setupAttribs);
    this->m_drawGeometryCallback = std::move (drawGeometry);
    this->m_cleanupAttribsCallback = std::move (cleanupAttribs);
}

GLuint CPass::compileShader (const char* shader, GLuint type) {
    // reserve shaders in OpenGL
    const GLuint shaderID = glCreateShader (type);

    glShaderSource (shaderID, 1, &shader, nullptr);
    glCompileShader (shaderID);

    GLint result = GL_FALSE;
    int infoLogLength = 0;

    // ensure the vertex shader was correctly compiled
    glGetShaderiv (shaderID, GL_COMPILE_STATUS, &result);
    glGetShaderiv (shaderID, GL_INFO_LOG_LENGTH, &infoLogLength);

    if (infoLogLength > 0) {
	const auto logBuffer = new char[infoLogLength + 1];
	// ensure logBuffer ends with a \0
	memset (logBuffer, 0, infoLogLength + 1);
	// get information about the error
	glGetShaderInfoLog (shaderID, infoLogLength, nullptr, logBuffer);
	// throw an exception about the issue
	std::stringstream buffer;
	buffer << logBuffer << std::endl << "Compiled source code:" << std::endl << shader;
	// free the buffer
	delete[] logBuffer;

	if (result == GL_FALSE) {
	    // shader compilation failed completely, throw an exception
	    sLog.exception (buffer.str ());
	} else {
	    // some warning was emitted, log the error and keep chuging along
	    sLog.error (buffer.str ());
	}
    }

    return shaderID;
}

void CPass::setupShaders () {
    // ensure the constants are defined
    const auto texture0 = this->m_renderable.getTexture ();

    // copy the combos from the pass
    this->m_combos.insert (this->m_pass.combos.begin (), this->m_pass.combos.end ());

    // TODO: THE VALUES ARE THE SAME AS THE ENUMERATION, SO MAYBE IT HAS TO BE SPECIFIED FOR THE TEXTURE 0 OF ALL
    // ELEMENTS?
    if (texture0 != nullptr) {
	if (texture0->getFormat () == TextureFormat_RG88) {
	    this->m_combos.insert_or_assign ("TEX0FORMAT", 8);
	} else if (texture0->getFormat () == TextureFormat_R8) {
	    this->m_combos.insert_or_assign ("TEX0FORMAT", 9);
	}
    }

    const auto& fog = this->m_renderable.getScene ().getFog ();
    const auto fogOverride = this->m_override.combos.find ("FOG");
    const auto fogMaterial = this->m_pass.combos.find ("FOG");
    const bool materialFogEnabled = fogOverride != this->m_override.combos.end ()
	? fogOverride->second != 0
	: fogMaterial != this->m_pass.combos.end () && fogMaterial->second != 0;
    this->m_combos.insert_or_assign ("FOG_DIST", fog.distanceEnabled ? 1 : 0);
    this->m_combos.insert_or_assign ("FOG_HEIGHT", fog.heightEnabled ? 1 : 0);
    this->m_combos.insert_or_assign ("FOG_COMPUTED", materialFogEnabled ? 1 : 0);

    // TODO: REVIEW THE SHADER TEXTURES HERE, THE ONES PASSED ON TO THE SHADER SHOULD NOT BE IN THE LIST
    // TODO: USED TO BUILD THE TEXTURES LATER
    // use the combos copied from the pass so it includes the texture format
    const std::string& shaderName
	= this->m_override.shaderOverride.has_value () ? this->m_override.shaderOverride.value () : this->m_pass.shader;

    TextureMap passTextures = this->m_pass.textures;
    for (const auto& [index, texture] : this->m_pass.usertextures) {
	passTextures.insert_or_assign (index, texture);
    }

    this->m_shader = new Render::Shaders::Shader (
	this->m_renderable.getAssetLocator (), shaderName, this->m_combos, this->m_override.combos, passTextures,
	this->m_override.textures, this->m_override.constants, this->m_pass.constants
    );

    const auto [vertex, fragment]
	= Shaders::GLSLContext::get ().toGlsl (this->m_shader->vertex (), this->m_shader->fragment ());

    // compile the shaders
    const GLuint vertexShaderID = compileShader (vertex.c_str (), GL_VERTEX_SHADER);
    const GLuint fragmentShaderID = compileShader (fragment.c_str (), GL_FRAGMENT_SHADER);
    // create the final program
    this->m_programID = glCreateProgram ();
    // link the shaders together
    glAttachShader (this->m_programID, vertexShaderID);
    glAttachShader (this->m_programID, fragmentShaderID);
    glLinkProgram (this->m_programID);
    // check that the shader was properly linked
    GLint result = GL_FALSE;
    int infoLogLength = 0;

    glGetProgramiv (this->m_programID, GL_LINK_STATUS, &result);
    glGetProgramiv (this->m_programID, GL_INFO_LOG_LENGTH, &infoLogLength);

    if (infoLogLength > 0) {
	const auto logBuffer = new char[infoLogLength + 1];
	// ensure logBuffer ends with a \0
	memset (logBuffer, 0, infoLogLength + 1);
	// get information about the error
	glGetProgramInfoLog (this->m_programID, infoLogLength, nullptr, logBuffer);
	// throw an exception about the issue
	const std::string message = logBuffer;
	// free the buffer
	delete[] logBuffer;
	if (result == GL_FALSE) {
	    // shader compilation failed completely, throw an exception
	    sLog.exception (message);
	} else {
	    // some warning was emitted, log the error and keep chuging along
	    sLog.error (message);
	}
    }

#if !NDEBUG
    glObjectLabel (GL_PROGRAM, this->m_programID, -1, shaderName.c_str ());
    glObjectLabel (GL_SHADER, vertexShaderID, -1, (shaderName + ".vert").c_str ());
    glObjectLabel (GL_SHADER, fragmentShaderID, -1, (shaderName + ".frag").c_str ());
#endif /* DEBUG */

    // after being liked shaders can be dettached and deleted
    glDetachShader (this->m_programID, vertexShaderID);
    glDetachShader (this->m_programID, fragmentShaderID);

    glDeleteShader (vertexShaderID);
    glDeleteShader (fragmentShaderID);

    // first setup the default values, these will be overwritten by future values
    this->setupShaderVariables ();
    // setup uniforms
    this->setupUniforms ();
    // setup attributes too
    this->setupAttributes ();
    // get information from the program, like uniforms, etc
    // support three textures for now
    this->g_Texture0Rotation = glGetUniformLocation (this->m_programID, "g_Texture0Rotation");
    this->g_Texture0Translation = glGetUniformLocation (this->m_programID, "g_Texture0Translation");
    this->g_AlphaLocation = glGetUniformLocation (this->m_programID, "g_Alpha");
    this->g_ColorLocation = glGetUniformLocation (this->m_programID, "g_Color");
    this->g_Color4Location = glGetUniformLocation (this->m_programID, "g_Color4");
    this->g_BrightnessLocation = glGetUniformLocation (this->m_programID, "g_Brightness");
    this->g_UserAlphaLocation = glGetUniformLocation (this->m_programID, "g_UserAlpha");
}

void CPass::setupAttributes () {
    this->addAttribute ("a_TexCoord", GL_FLOAT, 2, &this->a_TexCoord);
    this->addAttribute ("a_Position", GL_FLOAT, 3, &this->a_Position);
}

void CPass::setupTextureUniforms () {
    // first set default textures extracted from the shader
    // vertex shader doesn't seem to have texture info
    // but for now just set first vertex's textures
    // and then try with fragment's and override any existing
    for (const auto& [index, textureName] : this->m_shader->getVertex ().getTextures ()) {
	try {
	    auto texture = textureName.find ("_rt_") == 0 || textureName.find ("_alias_") == 0
		? this->resolveFBO (textureName)
		: this->getContext ().resolveTexture (textureName);

	    // create chain entry
	    this->m_textures[index] = std::make_shared<TextureChainEntry> (TextureChainEntry {
		.texture = texture,
		.next = nullptr,
	    });
	} catch (std::runtime_error& ex) {
	    sLog.error ("Cannot resolve texture ", textureName, " for fragment shader ", ex.what ());
	}
    }

    for (const auto& [index, textureName] : this->m_shader->getFragment ().getTextures ()) {
	try {
	    auto texture = textureName.find ("_rt_") == 0 || textureName.find ("_alias_") == 0
		? this->resolveFBO (textureName)
		: this->getContext ().resolveTexture (textureName);

	    const auto it = this->m_textures.find (index);
	    const auto chain = std::make_shared<TextureChainEntry> (TextureChainEntry {
		.texture = texture,
		.next = it != this->m_textures.end () ? it->second : nullptr,
	    });

	    this->m_textures[index] = chain;
	} catch (std::runtime_error& ex) {
	    sLog.error ("Cannot resolve texture ", textureName, " for fragment shader ", ex.what ());
	}
    }

    for (const auto& [index, textureName] : this->m_pass.textures) {
	try {
	    auto texture = textureName.find ("_rt_") == 0 || textureName.find ("_alias_") == 0
		? this->resolveFBO (textureName)
		: this->getContext ().resolveTexture (textureName);

	    const auto it = this->m_textures.find (index);
	    const auto chain = std::make_shared<TextureChainEntry> (TextureChainEntry {
		.texture = texture,
		.next = it != this->m_textures.end () ? it->second : nullptr,
	    });

	    this->m_textures[index] = chain;
	} catch (std::runtime_error& ex) {
	    sLog.error ("Cannot resolve texture ", textureName, " for pass ", ex.what ());
	}
    }

    for (const auto& [index, textureName] : this->m_pass.usertextures) {
	try {
	    auto texture = textureName.find ("_rt_") == 0 || textureName.find ("_alias_") == 0
		? this->resolveFBO (textureName)
		: this->getContext ().resolveTexture (textureName);

	    const auto it = this->m_textures.find (index);
	    const auto chain = std::make_shared<TextureChainEntry> (TextureChainEntry {
		.texture = texture,
		.next = it != this->m_textures.end () ? it->second : nullptr,
	    });

	    this->m_textures[index] = chain;
	} catch (std::runtime_error& ex) {
	    sLog.error ("Cannot resolve user texture ", textureName, " for pass ", ex.what ());
	}
    }

    // override any texture
    for (const auto& [index, textureName] : this->m_override.textures) {
	try {
	    auto texture = textureName.find ("_rt_") == 0 || textureName.find ("_alias_") == 0
		? this->resolveFBO (textureName)
		: this->getContext ().resolveTexture (textureName);

	    const auto it = this->m_textures.find (index);
	    const auto chain = std::make_shared<TextureChainEntry> (TextureChainEntry {
		.texture = texture,
		.next = it != this->m_textures.end () ? it->second : nullptr,
	    });

	    this->m_textures[index] = chain;
	} catch (std::runtime_error& ex) {
	    sLog.error ("Cannot resolve texture ", textureName, " for override ", ex.what ());
	}
    }

    for (const auto& [index, textureName] : this->m_override.usertextures) {
	try {
	    auto texture = textureName.find ("_rt_") == 0 || textureName.find ("_alias_") == 0
		? this->resolveFBO (textureName)
		: this->getContext ().resolveTexture (textureName);

	    const auto it = this->m_textures.find (index);
	    const auto chain = std::make_shared<TextureChainEntry> (TextureChainEntry {
		.texture = texture,
		.next = it != this->m_textures.end () ? it->second : nullptr,
	    });

	    this->m_textures[index] = chain;
	} catch (std::runtime_error& ex) {
	    sLog.error ("Cannot resolve user texture ", textureName, " for override ", ex.what ());
	}
    }

    for (const auto* unit : { &this->m_shader->getVertex (), &this->m_shader->getFragment () }) {
	for (const auto& [index, color] : unit->getPaintDefaultColors ()) {
	    if (!this->m_textures.contains (index)) {
		this->m_textures[index] = std::make_shared<TextureChainEntry> (TextureChainEntry {
		    .texture = std::make_shared<SolidColorTexture> (color),
		    .next = nullptr,
		});
	    }
	}
    }

    // binds are set last as they're the most important to be set
    for (const auto& [index, bind] : this->m_binds) {
	const auto texture = bind == "previous" ? nullptr : this->resolveFBO (bind);
	const auto it = this->m_textures.find (index);
	const auto chain = std::make_shared<TextureChainEntry> (TextureChainEntry {
	    .texture = texture,
	    .next = it != this->m_textures.end () ? it->second : nullptr,
	});

	this->m_textures[index] = chain;
    }

    // resolve the main texture
    std::shared_ptr<const TextureProvider> texture = this->resolveTexture (this->m_renderable.getTexture (), 0);
    for (int unit = 0; unit < 16; unit++) {
	this->addUniform ("g_Texture" + std::to_string (unit), unit);
    }
    this->addUniform ("g_TextureReductionScale", 1.0f);
    this->m_texture0Resolution = texture != nullptr ? *texture->getResolution () : glm::vec4 (1.0f);
    this->addUniform ("g_Texture0Resolution", &this->m_texture0Resolution);

    for (const auto& [textureIndex, expectedTexture] : this->m_textures) {
	std::ostringstream namestream;

	namestream << "g_Texture" << textureIndex << "Resolution";

	texture = this->resolveTexture (expectedTexture->texture, textureIndex, texture);
	if (texture == nullptr) {
	    continue;
	}
	this->addUniform (namestream.str (), texture->getResolution ());
    }

    this->addUniform ("g_Texture0Resolution", &this->m_texture0Resolution);
}

void CPass::setupUniforms () {
    this->setupTextureUniforms ();

    const auto& renderable = this->m_renderable;
    const auto& scene = this->m_renderable.getScene ();
    const auto& sceneData = this->m_renderable.getScene ().getScene ();
    const auto& recorder = this->m_renderable.getScene ().getAudioContext ().getRecorder ();

    static const bool s_noScreen = getenv ("LWE_NOSCREEN") != nullptr;
    if (!s_noScreen) {
	const auto w = static_cast<float> (scene.getWidth ());
	const auto h = static_cast<float> (scene.getHeight ());
	this->addUniform ("g_Screen", glm::vec3 (w, h, h > 0.0f ? w / h : 1.0f));
    }

    // lighting variables
    this->addUniform ("g_LightAmbientColor", sceneData.colors.ambient->value->getVec3 ());
    this->addUniform ("g_LightSkylightColor", sceneData.colors.skylight->value->getVec3 ());
    const auto& fog = scene.getFog ();
    this->addUniform ("g_FogDistanceColor", &sceneData.fog.distance.color->value->getVec3 ());
    this->addUniform ("g_FogDistanceParams", &fog.distanceParams);
    this->addUniform ("g_FogHeightColor", &sceneData.fog.height.color->value->getVec3 ());
    this->addUniform ("g_FogHeightParams", &fog.heightParams);
    this->refreshLightStage ();
    this->addUniform ("lwe_LitPointCount", &this->m_lightStage.pointCount);
    this->addUniform ("lwe_LitPointPosRad", this->m_lightStage.pointPosRad.data (), LIGHT_SLOTS);
    this->addUniform ("lwe_LitPointColorExp", this->m_lightStage.pointColorExp.data (), LIGHT_SLOTS);
    this->addUniform ("lwe_LitSpotCount", &this->m_lightStage.spotCount);
    this->addUniform ("lwe_LitSpotPosRad", this->m_lightStage.spotPosRad.data (), LIGHT_SLOTS);
    this->addUniform ("lwe_LitSpotColorExp", this->m_lightStage.spotColorExp.data (), LIGHT_SLOTS);
    this->addUniform ("lwe_LitSpotAxisCosIn", this->m_lightStage.spotAxisCosIn.data (), LIGHT_SLOTS);
    this->addUniform ("lwe_LitSpotCosOut", this->m_lightStage.spotCosOut.data (), LIGHT_SLOTS);
    // classic v2 interface: same staging block, refreshed by the same repack pass. Shaders
    // that do not declare these (the whole v4 family) simply skip registration.
    this->addUniform ("g_LightsPosition", this->m_lightStage.classicPosition.data (), LIGHT_SLOTS);
    this->addUniform ("g_LightsColorPremultiplied", this->m_lightStage.classicColorPremultiplied.data (), 3);
    // LWE_LIGHTDUMP: one-shot per pass build - do the classic names RESOLVE on this program?
    // addUniform drops a -1 location silently, and GL drivers differ on whether an array
    // uniform answers to its bare name or only to "name[0]" - log both forms.
    static const bool s_lightRegDump = getenv ("LWE_LIGHTDUMP") != nullptr;
    if (s_lightRegDump) {
	sLog.out (
	    "LWE-LIGHTREG shader=", this->m_pass.shader,
	    " pos=", glGetUniformLocation (this->m_programID, "g_LightsPosition"), "/",
	    glGetUniformLocation (this->m_programID, "g_LightsPosition[0]"),
	    " col=", glGetUniformLocation (this->m_programID, "g_LightsColorPremultiplied"), "/",
	    glGetUniformLocation (this->m_programID, "g_LightsColorPremultiplied[0]")
	);
    }
    this->addUniform ("lwe_LitDirCount", &this->m_lightStage.dirCount);
    this->addUniform ("lwe_LitDirToLight", this->m_lightStage.dirToLight.data (), LIGHT_SLOTS);
    this->addUniform ("lwe_LitDirColor", this->m_lightStage.dirColor.data (), LIGHT_SLOTS);
    this->addUniform ("lwe_ShadowAtlas", 16);
    this->addUniform ("lwe_ShadowFeatureCount", &this->m_lightStage.shadowFeatureCount);
    this->addUniform ("lwe_ShadowMatrix", this->m_lightStage.shadowMatrix.data (), SHADOW_FEATURES);
    this->addUniform ("lwe_ShadowTransform", this->m_lightStage.shadowTransform.data (), SHADOW_FEATURES);
    this->addUniform ("lwe_ShadowEnabled", this->m_lightStage.shadowEnabled.data (), SHADOW_FEATURES);
    this->addUniform ("lwe_LitSpotShadowFeature", this->m_lightStage.spotShadowFeature.data (), LIGHT_SLOTS);
    this->addUniform ("lwe_LitDirShadowFeatures", this->m_lightStage.dirShadowFeatures.data (), LIGHT_SLOTS);
    this->addUniform ("lwe_LitPointShadowMat", this->m_lightStage.pointShadowMat.data (), LIGHT_SLOTS * 6);
    this->addUniform ("lwe_LitPointShadowXform", this->m_lightStage.pointShadowXform.data (), LIGHT_SLOTS);
    this->addUniform ("lwe_LitPointShadowEnabled", this->m_lightStage.pointShadowEnabled.data (), LIGHT_SLOTS);
    this->addUniform ("lwe_LitTubeCount", &this->m_lightStage.tubeCount);
    this->addUniform ("lwe_LitTubePosRadA", this->m_lightStage.tubePosRadA.data (), LIGHT_SLOTS);
    this->addUniform ("lwe_LitTubeEndExpB", this->m_lightStage.tubeEndExpB.data (), LIGHT_SLOTS);
    this->addUniform ("lwe_LitTubeColor", this->m_lightStage.tubeColor.data (), LIGHT_SLOTS);
    this->m_usesSceneLights = this->m_uniforms.contains ("lwe_LitPointCount")
	|| this->m_uniforms.contains ("lwe_LitSpotCount") || this->m_uniforms.contains ("lwe_LitDirCount")
	|| this->m_uniforms.contains ("lwe_LitTubeCount") || this->m_uniforms.contains ("g_LightsPosition")
	|| this->m_uniforms.contains ("g_LightsColorPremultiplied");
    this->m_foldBrightness = dynamic_cast<const CImage*> (&renderable) != nullptr;
    static const bool s_colorTrace = getenv ("LWE_UNIFVALS") != nullptr;
    static int s_colorTraceCount = 0;
    if (s_colorTrace && renderable.getBrightness () != 1.0f && s_colorTraceCount < 8 && ++s_colorTraceCount > 0) {
	sLog.out (
	    "LWE-COLORTRACE color=", renderable.getColor ().r, " brightness=", renderable.getBrightness (),
	    " -> g_Color.r=", renderable.getColor ().r * renderable.getBrightness ()
	);
    }
    if (!this->m_uniforms.contains ("g_CompositeColor")) {
	this->addUniform ("g_CompositeColor", renderable.getCompositeColor ());
    }
    // add some external variables
    this->addUniform ("g_Time", &g_Time);
    this->addUniform ("g_Frametime", &this->m_frameTime);
    this->addUniform ("g_Daytime", &g_Daytime);
    // add model-view-projection matrix
    this->addUniform ("g_ModelViewProjectionMatrixInverse", &this->m_modelViewProjectionMatrixInverse);
    this->addUniform ("g_ModelViewProjectionMatrix", &this->m_modelViewProjectionMatrix);
    this->addUniform ("g_EffectModelViewProjectionMatrix", &this->m_modelViewProjectionMatrix);
    this->addUniform ("g_ModelMatrix", &this->m_modelMatrix);
    this->addUniform ("g_EffectModelMatrix", &this->m_modelMatrix);
    this->addUniform ("g_NormalModelMatrix", glm::identity<glm::mat3> ());
    this->addUniform ("g_ViewProjectionMatrix", &this->m_viewProjectionMatrix);
    this->addUniform (
	"g_LWEScreenVP", this->m_lweScreenVP != nullptr ? &this->m_lweScreenVP : &this->m_viewProjectionMatrix
    );
    this->addUniform ("g_LWEFalloffExp", &g_LweFalloffExp);
    this->addUniform ("g_PointerPosition", scene.getMousePosition ());
    this->addUniform ("g_PointerPositionLast", scene.getMousePositionLast ());
    this->addUniform ("g_ParallaxPosition", scene.getParallaxPosition ());
    this->addUniform ("g_EffectTextureProjectionMatrix", glm::mat4 (1.0));
    this->addUniform ("g_EffectTextureProjectionMatrixInverse", glm::mat4 (1.0));
    this->addUniform ("g_TexelSize", glm::vec2 (1.0 / scene.getWidth (), 1.0 / scene.getHeight ()));
    this->addUniform ("g_TexelSizeHalf", glm::vec2 (0.5 / scene.getWidth (), 0.5 / scene.getHeight ()));
    this->addUniform ("g_AudioSpectrum16Left", recorder.audio16, 16);
    this->addUniform ("g_AudioSpectrum16Right", recorder.audio16, 16);
    this->addUniform ("g_AudioSpectrum32Left", recorder.audio32, 32);
    this->addUniform ("g_AudioSpectrum32Right", recorder.audio32, 32);
    this->addUniform ("g_AudioSpectrum64Left", recorder.audio64, 64);
    this->addUniform ("g_AudioSpectrum64Right", recorder.audio64, 64);
}

void CPass::refreshLightStage () {
    using SceneLight = Wallpapers::CScene::SceneLight;
    static_assert (
	LIGHT_SLOTS == Wallpapers::CScene::MAX_LIGHTS,
	"LightingV1 staging capacity must match the scene light snapshot capacity"
    );
    static_assert (
	SHADOW_FEATURES == Wallpapers::CScene::MAX_SHADOW_FEATURES,
	"LightingV1 shadow staging capacity must match the scene shadow stage capacity"
    );

    auto& stage = this->m_lightStage;
    stage.pointCount = 0;
    stage.spotCount = 0;
    stage.dirCount = 0;
    stage.tubeCount = 0;

    const auto& shadowStage = this->m_renderable.getScene ().getShadowStage ();
    stage.shadowFeatureCount = shadowStage.featureCount;
    stage.shadowMatrix = shadowStage.matrices;
    stage.shadowTransform = shadowStage.transforms;
    stage.shadowEnabled = shadowStage.enabled;
    stage.spotShadowFeature.fill (-1.0f);
    stage.dirShadowFeatures.fill (glm::vec4 (-1.0f));
    stage.pointShadowEnabled.fill (0.0f);

    for (const auto& light : this->m_renderable.getScene ().getLights ()) {
	switch (light.type) {
	    case SceneLight::Type::Tube:
		if (stage.tubeCount >= LIGHT_SLOTS) {
		    break;
		}

		stage.tubePosRadA[stage.tubeCount] = glm::vec4 (light.position, light.radius);
		stage.tubeEndExpB[stage.tubeCount] = glm::vec4 (light.tubeEnd, light.exponent);
		stage.tubeColor[stage.tubeCount] = glm::vec4 (light.color, 0.0f);
		stage.tubeCount++;
		break;
	    case SceneLight::Type::Point:
		if (stage.pointCount >= LIGHT_SLOTS) {
		    break;
		}

		// shadowed points carry their 2x3 atlas block (six face view-projections)
		if (light.pointShadowSlot >= 0) {
		    for (int face = 0; face < 6; face++) {
			stage.pointShadowMat[stage.pointCount * 6 + face]
			    = shadowStage.pointMatrices[light.pointShadowSlot][face];
		    }
		    stage.pointShadowXform[stage.pointCount] = shadowStage.pointTransforms[light.pointShadowSlot];
		    stage.pointShadowEnabled[stage.pointCount] = shadowStage.pointEnabled[light.pointShadowSlot];
		}
		stage.pointPosRad[stage.pointCount] = glm::vec4 (light.position, light.radius);
		stage.pointColorExp[stage.pointCount] = glm::vec4 (light.color, light.exponent);
		stage.pointCount++;
		break;
	    case SceneLight::Type::Spot:
		{
		    if (stage.spotCount >= LIGHT_SLOTS) {
			break;
		    }

		    const glm::vec3 beamAxis = light.direction;
		    const float cosOuter = std::cos (glm::radians (light.outerConeDeg));
		    // keep the cone transition edges strictly ordered even for degenerate cones
		    const float cosInner = std::max (std::cos (glm::radians (light.innerConeDeg)), cosOuter + 1e-4f);

		    stage.spotPosRad[stage.spotCount] = glm::vec4 (light.position, light.radius);
		    stage.spotColorExp[stage.spotCount] = glm::vec4 (light.color, light.exponent);
		    stage.spotAxisCosIn[stage.spotCount] = glm::vec4 (beamAxis, cosInner);
		    stage.spotCosOut[stage.spotCount] = cosOuter;
		    stage.spotShadowFeature[stage.spotCount] = (float)light.spotShadowFeature;
		    stage.spotCount++;
		    break;
		}
	    case SceneLight::Type::Directional:
		if (stage.dirCount >= LIGHT_SLOTS) {
		    break;
		}

		if (dynamic_cast<const CImage*> (&this->m_renderable) != nullptr) {
		    break;
		}

		stage.dirToLight[stage.dirCount] = glm::vec4 (light.direction, 0.0f);
		stage.dirColor[stage.dirCount] = glm::vec4 (light.color, 0.0f);
		stage.dirShadowFeatures[stage.dirCount] = glm::vec4 (light.dirShadowFeatures, 0.0f);
		stage.dirCount++;
		break;
	}
    }

    stage.classicPosition.fill (glm::vec3 (0.0f));
    stage.classicColorPremultiplied.fill (glm::vec4 (0.0f));
    int classicSlot = 0;
    for (const auto& light : this->m_renderable.getScene ().getLights ()) {
	if (classicSlot >= LIGHT_SLOTS) {
	    break;
	}

	const float premultiplier = light.radius * light.radius / g_LweClassicDivisor;
	const float localScale = this->m_classicLocalFrame ? this->m_renderable.classicLocalRadianceScale () : 1.0f;
	// exponent renorm: keep brightness at d=100 constant while the falloff shape dial moves
	const float expRenorm = std::pow (100.0f, g_LweFalloffExp - 2.0f);
	const glm::vec3 premultiplied
	    = light.color * (premultiplier * localScale * expRenorm); // color already carries intensity

	stage.classicPosition[classicSlot] = this->m_classicLocalFrame
	    ? this->m_renderable.toClassicLightSpaceLocal (light.position)
	    : this->m_renderable.toClassicLightSpace (light.position);
	if (classicSlot < 3) {
	    stage.classicColorPremultiplied[classicSlot].r = premultiplied.r;
	    stage.classicColorPremultiplied[classicSlot].g = premultiplied.g;
	    stage.classicColorPremultiplied[classicSlot].b = premultiplied.b;
	} else {
	    stage.classicColorPremultiplied[0].w = premultiplied.r;
	    stage.classicColorPremultiplied[1].w = premultiplied.g;
	    stage.classicColorPremultiplied[2].w = premultiplied.b;
	}
	classicSlot++;
    }
}

void CPass::addAttribute (const std::string& name, GLint type, GLint elements, const GLuint* value) {
    const GLint id = glGetAttribLocation (this->m_programID, name.c_str ());

    if (id == -1) {
	return;
    }

    this->m_attribs.emplace_back (new AttribEntry (id, name, type, elements, value));
}

template <typename T> void CPass::addUniform (const std::string& name, UniformType type, T value) {
    GLint id = glGetUniformLocation (this->m_programID, name.c_str ());

    // parameter not found, can be ignored
    if (id == -1) {
	return;
    }

    // free the uniform that's already registered if it's there already
    const auto it = this->m_uniforms.find (name);

    if (it != this->m_uniforms.end ()) {
	delete it->second;
    }

    // build a copy of the value and allocate it somewhere
    T* newValue = new T (value);

    // uniform found, add it to the list
    this->m_uniforms.insert_or_assign (name, new UniformEntry (id, name, type, newValue, 1));
}

template <typename T> void CPass::addUniform (const std::string& name, UniformType type, T* value, int count) {
    // this version is used to reference to system variables so things like g_Time works fine
    GLint id = glGetUniformLocation (this->m_programID, name.c_str ());

    // parameter not found, can be ignored
    if (id == -1) {
	return;
    }

    // free the uniform that's already registered if it's there already

    if (const auto it = this->m_uniforms.find (name); it != this->m_uniforms.end ()) {
	delete it->second;
    }

    // uniform found, add it to the list
    this->m_uniforms.insert_or_assign (name, new UniformEntry (id, name, type, value, count));
}

template <typename T> void CPass::addUniform (const std::string& name, UniformType type, T** value) {
    // this version is used to reference to system variables so things like g_Time works fine
    const GLint id = glGetUniformLocation (this->m_programID, name.c_str ());

    // parameter not found, can be ignored
    if (id == -1) {
	return;
    }

    // free the uniform that's already registered if it's there already

    if (const auto it = this->m_uniforms.find (name); it != this->m_uniforms.end ()) {
	delete it->second;
    }

    // uniform found, add it to the list
    this->m_referenceUniforms.insert_or_assign (
	name, new ReferenceUniformEntry (id, name, type, reinterpret_cast<const void**> (value))
    );
}

void CPass::setupShaderVariables () {
    static const bool s_unifDump = getenv ("LWE_UNIFDUMP") != nullptr;
    const auto dumpValue = [] (const DynamicValue* v) -> std::string {
	try {
	    const auto vec = v->getVec3 ();
	    return std::to_string (vec.x) + " " + std::to_string (vec.y) + " " + std::to_string (vec.z);
	} catch (...) { }
	try {
	    return std::to_string (v->getFloat ());
	} catch (...) {
	    return "<non-float>";
	}
    };

    for (const auto& cur : this->m_shader->getVertex ().getParameters ()) {
	if (!this->m_uniforms.contains (cur->getName ())) {
	    this->addUniform (cur);
	    if (s_unifDump) {
		sLog.out (
		    "LWE-UNIFDUMP pass=", static_cast<const void*> (this), " shader=", this->m_pass.shader,
		    " src=shader-default(vert) name=", cur->getName ()
		);
	    }
	}
    }

    for (const auto& cur : this->m_shader->getFragment ().getParameters ()) {
	if (!this->m_uniforms.contains (cur->getName ())) {
	    this->addUniform (cur);
	    if (s_unifDump) {
		sLog.out (
		    "LWE-UNIFDUMP pass=", static_cast<const void*> (this), " shader=", this->m_pass.shader,
		    " src=shader-default(frag) name=", cur->getName ()
		);
	    }
	}
    }

    // apply material pass constants (e.g. constantshadervalues from the material JSON)
    for (const auto& [name, value] : this->m_pass.constants) {
	const auto [vertex, fragment] = this->m_shader->findParameter (name);

	if (vertex == nullptr && fragment == nullptr) {
	    if (s_unifDump) {
		sLog.out (
		    "LWE-UNIFDUMP pass=", static_cast<const void*> (this), " shader=", this->m_pass.shader,
		    " src=material-constant name=", name, " value=", dumpValue (value->value.get ()),
		    " DROPPED-no-shader-param"
		);
	    }
	    continue;
	}

	ShaderVariable* var = vertex == nullptr ? fragment : vertex;
	this->addUniform (var, value->value.get ());
	if (s_unifDump) {
	    sLog.out (
		"LWE-UNIFDUMP pass=", static_cast<const void*> (this), " shader=", this->m_pass.shader,
		" src=material-constant name=", name, " value=", dumpValue (value->value.get ()), " BOUND"
	    );
	}
    }

    // apply override constants (highest priority, overrides both defaults and pass constants)
    for (const auto& [name, value] : this->m_override.constants) {
	const auto [vertex, fragment] = this->m_shader->findParameter (name);

	if (vertex == nullptr && fragment == nullptr) {
	    if (s_unifDump) {
		sLog.out (
		    "LWE-UNIFDUMP pass=", static_cast<const void*> (this), " shader=", this->m_pass.shader,
		    " src=override-constant name=", name, " value=", dumpValue (value->value.get ()),
		    " DROPPED-no-shader-param"
		);
	    }
	    continue;
	}

	ShaderVariable* var = vertex == nullptr ? fragment : vertex;
	this->addUniform (var, value->value.get ());
	if (s_unifDump) {
	    sLog.out (
		"LWE-UNIFDUMP pass=", static_cast<const void*> (this), " shader=", this->m_pass.shader,
		" src=override-constant name=", name, " value=", dumpValue (value->value.get ()), " BOUND"
	    );
	}
    }
}

// define some basic methods for the template
void CPass::addUniform (ShaderVariable* value) {
    // no need to re-implement this, call the version that takes a CDynamicValue as second parameter
    // and that handles casting and everything
    this->addUniform (value, value);
}

void CPass::addUniform (const ShaderVariable* value, const DynamicValue* setting) {
    if (value->is<ShaderVariableFloat> ()) {
	this->addUniform (value->getName (), &setting->getFloat ());
    } else if (value->is<ShaderVariableInteger> ()) {
	this->addUniform (value->getName (), &setting->getInt ());
    } else if (value->is<ShaderVariableVector2> ()) {
	this->addUniform (value->getName (), &setting->getVec2 ());
    } else if (value->is<ShaderVariableVector3> ()) {
	this->addUniform (value->getName (), &setting->getVec3 ());
    } else if (value->is<ShaderVariableVector4> ()) {
	this->addUniform (value->getName (), &setting->getVec4 ());
    } else {
	sLog.error ("Cannot convert setting dynamic value  to ", value->getName (), ". Using default value");
    }
}

void CPass::addUniform (const std::string& name, int value) { this->addUniform (name, UniformType::Integer, value); }

void CPass::addUniform (const std::string& name, const int* value, int count) {
    this->addUniform (name, UniformType::Integer, value, count);
}

void CPass::addUniform (const std::string& name, const int** value) {
    this->addUniform (name, UniformType::Integer, value);
}

void CPass::addUniform (const std::string& name, double value) { this->addUniform (name, UniformType::Double, value); }

void CPass::addUniform (const std::string& name, const double* value, int count) {
    this->addUniform (name, UniformType::Double, value, count);
}

void CPass::addUniform (const std::string& name, const double** value) {
    this->addUniform (name, UniformType::Double, value);
}

void CPass::addUniform (const std::string& name, float value) { this->addUniform (name, UniformType::Float, value); }

void CPass::addUniform (const std::string& name, const float* value, int count) {
    this->addUniform (name, UniformType::Float, value, count);
}

void CPass::addUniform (const std::string& name, const glm::vec3* value, int count) {
    this->addUniform (name, UniformType::Vector3, value, count);
}

void CPass::addUniform (const std::string& name, const float** value) {
    this->addUniform (name, UniformType::Float, value);
}

void CPass::addUniform (const std::string& name, glm::vec2 value) {
    this->addUniform (name, UniformType::Vector2, value);
}

void CPass::addUniform (const std::string& name, const glm::vec2* value) {
    this->addUniform (name, UniformType::Vector2, value, 1);
}

void CPass::addUniform (const std::string& name, const glm::vec2** value) {
    this->addUniform (name, UniformType::Vector2, value, 1);
}

void CPass::addUniform (const std::string& name, glm::vec3 value) {
    this->addUniform (name, UniformType::Vector3, value);
}

void CPass::addUniform (const std::string& name, const glm::vec3* value) {
    this->addUniform (name, UniformType::Vector3, value, 1);
}

void CPass::addUniform (const std::string& name, const glm::vec3** value) {
    this->addUniform (name, UniformType::Vector3, value);
}

void CPass::addUniform (const std::string& name, const glm::vec4 value) {
    this->addUniform (name, UniformType::Vector4, value);
}

void CPass::addUniform (const std::string& name, const glm::vec4* value) {
    this->addUniform (name, UniformType::Vector4, value, 1);
}

void CPass::addUniform (const std::string& name, const glm::vec4* value, const int count) {
    this->addUniform (name, UniformType::Vector4, value, count);
}

void CPass::addUniform (const std::string& name, const glm::vec4** value) {
    this->addUniform (name, UniformType::Vector4, value);
}

void CPass::addUniform (const std::string& name, const glm::mat3& value) {
    this->addUniform (name, UniformType::Matrix3, value);
}

void CPass::addUniform (const std::string& name, const glm::mat3* value) {
    this->addUniform (name, UniformType::Matrix3, value, 1);
}

void CPass::addUniform (const std::string& name, const glm::mat3** value) {
    this->addUniform (name, UniformType::Matrix3, value);
}

void CPass::addUniform (const std::string& name, const glm::mat4 value) {
    this->addUniform (name, UniformType::Matrix4, value);
}

void CPass::addUniform (const std::string& name, const glm::mat4* value) {
    this->addUniform (name, UniformType::Matrix4, value, 1);
}

void CPass::addUniform (const std::string& name, const glm::mat4* value, int count) {
    this->addUniform (name, UniformType::Matrix4, value, count);
}

void CPass::addUniform (const std::string& name, const glm::mat4** value) {
    this->addUniform (name, UniformType::Matrix4, value);
}
