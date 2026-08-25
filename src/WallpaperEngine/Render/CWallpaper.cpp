#include "CWallpaper.h"
#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/Render/OverlayLabel.h"
#include "WallpaperEngine/Render/Wallpapers/CScene.h"
#include "WallpaperEngine/Render/Wallpapers/CVideo.h"
#include "WallpaperEngine/Render/Wallpapers/CWeb.h"
#include <cstdio>
#include <cstdlib>

#include "WallpaperEngine/Data/Model/Project.h"
#include "WallpaperEngine/Data/Model/Wallpaper.h"

using namespace WallpaperEngine::Render;

CWallpaper::CWallpaper (
    const Wallpaper& wallpaperData, RenderContext& context, AudioContext& audioContext,
    const WallpaperState::TextureUVsScaling& scalingMode, const uint32_t& clampMode
) :
    ContextAware (context), FBOProvider (nullptr), m_wallpaperData (wallpaperData), m_audioContext (audioContext),
    m_state (scalingMode, clampMode) {
    // generate the VAO to stop opengl from complaining
    glGenVertexArrays (1, &this->m_vaoBuffer);
    glBindVertexArray (this->m_vaoBuffer);

    this->setupShaders ();

    constexpr GLfloat texCoords[] = { 0.0f, 0.0f, 1.0f, 0.0f, 0.0f, 1.0f, 0.0f, 1.0f, 1.0f, 0.0f, 1.0f, 1.0f };

    // inverted positions so the final texture is rendered properly
    constexpr GLfloat position[] = { -1.0f, 1.0f,  0.0f, 1.0,  1.0f, 0.0f, -1.0f, -1.0f, 0.0f,
				     -1.0f, -1.0f, 0.0f, 1.0f, 1.0f, 0.0f, 1.0f,  -1.0f, 0.0f };

    glGenBuffers (1, &this->m_texCoordBuffer);
    glBindBuffer (GL_ARRAY_BUFFER, this->m_texCoordBuffer);
    glBufferData (GL_ARRAY_BUFFER, sizeof (texCoords), texCoords, GL_STATIC_DRAW);

    glGenBuffers (1, &this->m_positionBuffer);
    glBindBuffer (GL_ARRAY_BUFFER, this->m_positionBuffer);
    glBufferData (GL_ARRAY_BUFFER, sizeof (position), position, GL_STATIC_DRAW);

    if (getenv ("LWE_TEXCACHEDUMP") != nullptr) {
	sLog.out ("LWE-WPLIFE ctor ", static_cast<const void*> (this));
    }
}

CWallpaper::~CWallpaper () {
    if (getenv ("LWE_TEXCACHEDUMP") != nullptr) {
	sLog.out ("LWE-WPLIFE dtor ", static_cast<const void*> (this));
    }
    // destroy shader programs
    GLuint attachedShaders[2];
    GLsizei attachedCount = 0;

    // destroy shaders (we only attach 2 to each program)
    glGetAttachedShaders (this->m_shader, 2, &attachedCount, attachedShaders);

    for (auto i = 0; i < attachedCount; i++) {
	glDeleteShader (attachedShaders[i]);
    }

    glDeleteProgram (this->m_shader);

    // destroy used buffers
    glDeleteBuffers (1, &this->m_texCoordBuffer);
    glDeleteBuffers (1, &this->m_positionBuffer);
    glDeleteVertexArrays (1, &this->m_vaoBuffer);
}

const AssetLocator& CWallpaper::getAssetLocator () const { return *this->m_wallpaperData.project.assetLocator; }

const Wallpaper& CWallpaper::getWallpaperData () const { return this->m_wallpaperData; }

GLuint CWallpaper::getWallpaperFramebuffer () const { return this->m_sceneFBO->getFramebuffer (); }

GLuint CWallpaper::getWallpaperTexture () const { return this->m_sceneFBO->getTextureID (0); }

void CWallpaper::setupShaders () {
    // reserve shaders in OpenGL
    const GLuint vertexShaderID = glCreateShader (GL_VERTEX_SHADER);

    // give shader's source code to OpenGL to be compiled
    const char* sourcePointer = "#version 330\n"
				"precision highp float;\n"
				"in vec3 a_Position;\n"
				"in vec2 a_TexCoord;\n"
				"out vec2 v_TexCoord;\n"
				"void main () {\n"
				"gl_Position = vec4 (a_Position, 1.0);\n"
				"v_TexCoord = a_TexCoord;\n"
				"}";

    glShaderSource (vertexShaderID, 1, &sourcePointer, nullptr);
    glCompileShader (vertexShaderID);

    GLint result = GL_FALSE;
    int infoLogLength = 0;

    // ensure the vertex shader was correctly compiled
    glGetShaderiv (vertexShaderID, GL_COMPILE_STATUS, &result);
    glGetShaderiv (vertexShaderID, GL_INFO_LOG_LENGTH, &infoLogLength);

    if (infoLogLength > 0) {
	const auto logBuffer = new char[infoLogLength + 1];
	// ensure logBuffer ends with a \0
	memset (logBuffer, 0, infoLogLength + 1);
	// get information about the error
	glGetShaderInfoLog (vertexShaderID, infoLogLength, nullptr, logBuffer);
	// throw an exception about the issue
	const std::string message = logBuffer;
	// free the buffer
	delete[] logBuffer;
	// throw an exception
	sLog.exception (message);
    }

    // reserve shaders in OpenGL
    const GLuint fragmentShaderID = glCreateShader (GL_FRAGMENT_SHADER);

    // give shader's source code to OpenGL to be compiled
    sourcePointer = "#version 330\n"
		    "precision highp float;\n"
		    "uniform sampler2D g_Texture0;\n"
		    "uniform vec4 g_CC;\n" // x=brightness y=contrast z=saturation w=hue(radians)
		    "uniform float g_SrgbOut;\n"
		    "in vec2 v_TexCoord;\n"
		    "out vec4 out_FragColor;\n"
		    "void main () {\n"
		    "vec4 src = texture (g_Texture0, v_TexCoord);\n"
		    "vec3 c = src.rgb;\n"
		    "c *= g_CC.x;\n" // brightness
		    "c = (c - 0.5) * g_CC.y + 0.5;\n" // contrast around mid-grey
		    "if (g_CC.w != 0.0) {\n" // hue rotation
		    "  float s = sin(g_CC.w), co = cos(g_CC.w);\n"
		    "  mat3 hue = mat3(0.299,0.587,0.114, 0.299,0.587,0.114, 0.299,0.587,0.114)\n"
		    "           + co * mat3(0.701,-0.587,-0.114, -0.299,0.413,-0.114, -0.299,-0.587,0.886)\n"
		    "           + s  * mat3(0.168,0.330,-0.497, -0.328,0.035,0.292, 1.250,-1.050,-0.203);\n"
		    "  c = c * hue;\n"
		    "}\n"
		    "float l = dot(c, vec3(0.299,0.587,0.114));\n" // saturation
		    "c = mix(vec3(l), c, g_CC.z);\n"
		    "if (g_SrgbOut > 0.5) { c = pow(max(c, vec3(0.0)), vec3(1.0/2.2)); }\n"
		    "out_FragColor = vec4(clamp(c, 0.0, 1.0), src.a);\n"
		    "}";

    glShaderSource (fragmentShaderID, 1, &sourcePointer, nullptr);
    glCompileShader (fragmentShaderID);

    result = GL_FALSE;
    infoLogLength = 0;

    // ensure the vertex shader was correctly compiled
    glGetShaderiv (fragmentShaderID, GL_COMPILE_STATUS, &result);
    glGetShaderiv (fragmentShaderID, GL_INFO_LOG_LENGTH, &infoLogLength);

    if (infoLogLength > 0) {
	const auto logBuffer = new char[infoLogLength + 1];
	// ensure logBuffer ends with a \0
	memset (logBuffer, 0, infoLogLength + 1);
	// get information about the error
	glGetShaderInfoLog (fragmentShaderID, infoLogLength, nullptr, logBuffer);
	// throw an exception about the issue
	const std::string message = logBuffer;
	// free the buffer
	delete[] logBuffer;
	// throw an exception
	sLog.exception (message);
    }

    // create the final program
    this->m_shader = glCreateProgram ();
    // link the shaders together
    glAttachShader (this->m_shader, vertexShaderID);
    glAttachShader (this->m_shader, fragmentShaderID);
    glLinkProgram (this->m_shader);
    // check that the shader was properly linked
    result = GL_FALSE;
    infoLogLength = 0;

    glGetProgramiv (this->m_shader, GL_LINK_STATUS, &result);
    glGetProgramiv (this->m_shader, GL_INFO_LOG_LENGTH, &infoLogLength);

    if (infoLogLength > 0) {
	const auto logBuffer = new char[infoLogLength + 1];
	// ensure logBuffer ends with a \0
	memset (logBuffer, 0, infoLogLength + 1);
	// get information about the error
	glGetProgramInfoLog (this->m_shader, infoLogLength, nullptr, logBuffer);
	// throw an exception about the issue
	const std::string message = logBuffer;
	// free the buffer
	delete[] logBuffer;
	// throw an exception
	sLog.exception (message);
    }

    // after being liked shaders can be dettached and deleted
    glDetachShader (this->m_shader, vertexShaderID);
    glDetachShader (this->m_shader, fragmentShaderID);

    glDeleteShader (vertexShaderID);
    glDeleteShader (fragmentShaderID);

    // get textures
    this->g_Texture0 = glGetUniformLocation (this->m_shader, "g_Texture0");
    this->a_Position = glGetAttribLocation (this->m_shader, "a_Position");
    this->a_TexCoord = glGetAttribLocation (this->m_shader, "a_TexCoord");
    this->g_CC = glGetUniformLocation (this->m_shader, "g_CC");
    this->g_SrgbOut = glGetUniformLocation (this->m_shader, "g_SrgbOut");
}

void CWallpaper::setDestinationFramebuffer (GLuint framebuffer) { this->m_destFramebuffer = framebuffer; }

void CWallpaper::setSpanInfo (const SpanInfo& spanInfo) { this->m_spanInfo = spanInfo; }

const CWallpaper::SpanInfo* CWallpaper::getSpanInfo () const {
    return this->m_spanInfo.has_value () ? &this->m_spanInfo.value () : nullptr;
}

void CWallpaper::setMirrorOwner (const std::string& screen) { this->m_mirrorOwner = screen; }

const std::string& CWallpaper::getMirrorOwner () const { return this->m_mirrorOwner; }

void CWallpaper::updateUVs (const glm::ivec4& viewport, const bool vflip) {
    // update UVs if something has changed, otherwise use old values
    if (this->m_state.hasChanged (viewport, vflip, this->getWidth (), this->getHeight ())) {
	// Update wallpaper state
	this->m_state.updateState (viewport, vflip, this->getWidth (), this->getHeight ());
    }
}

void CWallpaper::render (
    const glm::ivec4& viewport, const bool vflip, const glm::ivec2& globalPosition, const glm::ivec2& logicalSize,
    const std::string& screenName
) {
    // Get current frame counter from the driver to avoid redundant scene renders
    const uint32_t currentFrame = this->getContext ().getDriver ().getFrameCounter ();
    const bool needsSceneRender = this->m_mirrorOwner.empty () ? (currentFrame != this->m_lastRenderedFrame)
							       : (screenName == this->m_mirrorOwner);
    const glm::ivec4 sceneViewport = this->m_spanInfo.has_value ()
	? glm::ivec4 { 0, 0, this->m_spanInfo->totalBounds.z, this->m_spanInfo->totalBounds.w }
	: viewport;

#if !NDEBUG
    glPushDebugGroup (GL_DEBUG_SOURCE_APPLICATION, 0, -1, "Rendering scene");
#endif /* !NDEBUG */
    if (needsSceneRender) {
	this->renderFrame (sceneViewport);
	this->m_lastRenderedFrame = currentFrame;
    }
#if !NDEBUG
    glPopDebugGroup ();
    glPushDebugGroup (GL_DEBUG_SOURCE_APPLICATION, 0, -1, "Rendering scene to output");
#endif /* !NDEBUG */

    float ustart, uend, vstart, vend;

    if (this->m_spanInfo.has_value ()) {
	// Span mode: treat bounding box as virtual viewport, scale wallpaper using
	// the normal scaling rules (fill/fit/stretch/default), then slice per monitor.
	const auto& span = this->m_spanInfo.value ();
	const float spanW = static_cast<float> (span.totalBounds.z);
	const float spanH = static_cast<float> (span.totalBounds.w);
	const float spanX = static_cast<float> (span.totalBounds.x);
	const float spanY = static_cast<float> (span.totalBounds.y);

	// Compute base UVs for the wallpaper scaled to the bounding box
	this->updateUVs (span.totalBounds, vflip);
	auto [baseUstart, baseUend, baseVstart, baseVend] = this->m_state.getTextureUVs ();

	// This viewport's relative position within the bounding box [0..1]
	// Use logicalSize (same coordinate space as globalPosition and totalBounds)
	const float relLeft = (static_cast<float> (globalPosition.x) - spanX) / spanW;
	const float relRight = (static_cast<float> (globalPosition.x + logicalSize.x) - spanX) / spanW;
	const float relTop = (static_cast<float> (globalPosition.y) - spanY) / spanH;
	const float relBottom = (static_cast<float> (globalPosition.y + logicalSize.y) - spanY) / spanH;

	// Interpolate within the base UVs to get this viewport's slice
	const float baseURange = baseUend - baseUstart;
	const float baseVRange = baseVend - baseVstart;

	ustart = baseUstart + relLeft * baseURange;
	uend = baseUstart + relRight * baseURange;
	vstart = baseVstart + relTop * baseVRange;
	vend = baseVstart + relBottom * baseVRange;

	// Log span debug info only on first few frames
	if (this->m_lastRenderedFrame < 5) {
	    sLog.debug (
		"SPAN DEBUG: viewport=", viewport.z, "x", viewport.w, " globalPos=(", globalPosition.x, ",",
		globalPosition.y, ")", " span=(", span.totalBounds.x, ",", span.totalBounds.y, ",", span.totalBounds.z,
		",", span.totalBounds.w, ")", " rel=[", relLeft, ",", relRight, "]x[", relTop, ",", relBottom, "]",
		" baseUV=[", baseUstart, ",", baseUend, "]x[", baseVstart, ",", baseVend, "]", " finalUV=[", ustart,
		",", uend, "]x[", vstart, ",", vend, "]"
	    );
	}
    } else {
	// Normal mode: compute UVs based on viewport dimensions and wallpaper resolution
	updateUVs (viewport, vflip);
	auto uvs = this->m_state.getTextureUVs ();
	ustart = uvs.ustart;
	uend = uvs.uend;
	vstart = uvs.vstart;
	vend = uvs.vend;
	static const bool s_presentTrace = getenv ("LWE_PRESENTTRACE") != nullptr;
	if (s_presentTrace && this->m_lastRenderedFrame <= 2) {
	    sLog.out (
		"LWE-PRESENT viewport=", viewport.z, "x", viewport.w, " wpRes=", this->getWidth (), "x",
		this->getHeight (), " mode=", static_cast<int> (this->m_state.getTextureUVsScaling ()), " uv=[", ustart,
		",", uend, "]x[", vstart, ",", vend, "]"
	    );
	}
    }

    const GLfloat texCoords[] = {
	ustart, vstart, uend, vstart, ustart, vend, ustart, vend, uend, vstart, uend, vend,
    };

    glViewport (viewport.x, viewport.y, viewport.z, viewport.w);

    glBindFramebuffer (GL_FRAMEBUFFER, this->m_destFramebuffer);

    glBindVertexArray (this->m_vaoBuffer);

    glDisable (GL_BLEND);
    glDisable (GL_DEPTH_TEST);
    glDisable (GL_CULL_FACE);
    glColorMask (true, true, true, true);
    // do not use any shader
    glUseProgram (this->m_shader);
    // activate scene texture
    glActiveTexture (GL_TEXTURE0);
    glBindTexture (GL_TEXTURE_2D, this->getWallpaperTexture ());
    // set uniforms and attribs
    glEnableVertexAttribArray (this->a_TexCoord);
    glBindBuffer (GL_ARRAY_BUFFER, this->m_texCoordBuffer);
    glBufferData (GL_ARRAY_BUFFER, sizeof (texCoords), texCoords, GL_STATIC_DRAW);
    glVertexAttribPointer (this->a_TexCoord, 2, GL_FLOAT, GL_FALSE, 0, nullptr);

    glEnableVertexAttribArray (this->a_Position);
    glBindBuffer (GL_ARRAY_BUFFER, this->m_positionBuffer);
    glVertexAttribPointer (this->a_Position, 3, GL_FLOAT, GL_FALSE, 0, nullptr);

    glUniform1i (this->g_Texture0, 0);
    {
	const auto cc = this->getContext ().getApp ().getColorCorrection ();
	glUniform4f (this->g_CC, cc.x, cc.y, cc.z, cc.w);
	static const float srgbOut
	    = (getenv ("LWE_SRGBALBEDO") != nullptr || getenv ("LWE_SRGBALL") != nullptr) ? 1.0f : 0.0f;
	glUniform1f (this->g_SrgbOut, srgbOut);
    }
    // write the framebuffer as is to the screen
    glBindBuffer (GL_ARRAY_BUFFER, this->m_texCoordBuffer);
    glDrawArrays (GL_TRIANGLES, 0, 6);

    static const bool s_presentProfile = getenv ("LWE_FBPROFILE") != nullptr;
    if (s_presentProfile) {
	static int s_presents = 0;
	if (++s_presents % 450 == 0) {
	    std::vector<unsigned char> px (static_cast<size_t> (viewport.z) * viewport.w * 4);
	    glReadPixels (viewport.x, viewport.y, viewport.z, viewport.w, GL_RGBA, GL_UNSIGNED_BYTE, px.data ());
	    double cols[32] = {};
	    for (int y = 0; y < viewport.w; y += 4) {
		for (int x = 0; x < viewport.z; x += 4) {
		    const size_t o = (static_cast<size_t> (y) * viewport.z + x) * 4;
		    cols[x * 32 / viewport.z] += 0.299 * px[o] + 0.587 * px[o + 1] + 0.114 * px[o + 2];
		}
	    }
	    std::ostringstream c;
	    for (int i = 0; i < 32; i++) {
		c << static_cast<int> (cols[i] / (viewport.w / 4.0 * (viewport.z / 32.0 / 4.0))) << (i < 31 ? "," : "");
	    }
	    sLog.out ("LWE-PRESENTPROFILE viewport=", viewport.z, "x", viewport.w, " cols=", c.str ());
	}
    }

    OverlayLabel::draw (viewport.z, viewport.w);

#if !NDEBUG
    glPopDebugGroup ();
#endif /* !NDEBUG */
}

void CWallpaper::setPause (bool newState) { }

void CWallpaper::setPlaybackSpeed (float speed) { }

void CWallpaper::setAudioVolume (int volume) { }

void CWallpaper::setupFramebuffers () {
    const glm::vec2 capped
	= this->clampToCap ({ static_cast<float> (this->getWidth ()), static_cast<float> (this->getHeight ()) });
    const auto width = static_cast<uint32_t> (capped.x);
    const auto height = static_cast<uint32_t> (capped.y);
    const uint32_t clamp = this->m_state.getClampingMode ();

    // create framebuffer for the scene
    this->m_sceneFBO
	= this->create ("_rt_FullFrameBuffer", this->m_sceneFormat, clamp, 1.0, { width, height }, { width, height });
    if (this->m_wallpaperData.is<Scene> ()) {
	this->m_sceneFBO->ensureDepthAttachment ();
    }

    this->alias ("_rt_MipMappedFrameBuffer", "_rt_FullFrameBuffer");
}

AudioContext& CWallpaper::getAudioContext () const { return this->m_audioContext; }

const WallpaperState& CWallpaper::getState () const { return this->m_state; }

std::shared_ptr<const CFBO> CWallpaper::findFBO (const std::string& name) const {
    const auto fbo = this->find (name);

    if (fbo == nullptr) {
	sLog.exception ("Cannot find FBO ", name);
    }

    return fbo;
}

std::shared_ptr<const CFBO> CWallpaper::getFBO () const { return this->m_sceneFBO; }

std::unique_ptr<CWallpaper> CWallpaper::fromWallpaper (
    const Wallpaper& wallpaper, RenderContext& context, AudioContext& audioContext, WebHelper::HelperClient* webHelper,
    const WallpaperState::TextureUVsScaling& scalingMode, const uint32_t& clampMode
) {
    if (wallpaper.is<Scene> ()) {
	return std::make_unique<WallpaperEngine::Render::Wallpapers::CScene> (
	    wallpaper, context, audioContext, scalingMode, clampMode
	);
    }

    if (wallpaper.is<Video> ()) {
	return std::make_unique<WallpaperEngine::Render::Wallpapers::CVideo> (
	    wallpaper, context, audioContext, scalingMode, clampMode
	);
    }

    if (wallpaper.is<Web> ()) {
	return std::make_unique<WallpaperEngine::Render::Wallpapers::CWeb> (
	    wallpaper, context, audioContext, *webHelper, scalingMode, clampMode
	);
    }

    sLog.exception ("Unsupported wallpaper type");
}
