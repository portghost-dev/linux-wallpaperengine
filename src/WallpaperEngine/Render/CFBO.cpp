#include "CFBO.h"
#include "WallpaperEngine/Logging/Log.h"

using namespace WallpaperEngine::Render;

CFBO::CFBO (
    std::string name, const TextureFormat format, const uint32_t flags, const float scale, uint32_t realWidth,
    uint32_t realHeight, uint32_t textureWidth, uint32_t textureHeight
) : m_scale (scale), m_name (std::move (name)), m_format (format), m_flags (flags) {
    this->createGL (textureWidth, textureHeight);

    this->m_resolution = { textureWidth, textureHeight, realWidth, realHeight };

    // create the textureframe entries
    const auto frame = std::make_shared<Frame> ();

    frame->frameNumber = 0;
    frame->frametime = 0;
    frame->height1 = textureHeight;
    frame->height2 = realHeight;
    frame->width1 = textureWidth;
    frame->width2 = realWidth;
    frame->x = 0;
    frame->y = 0;

    this->m_frames.push_back (frame);
}

void CFBO::createGL (const uint32_t textureWidth, const uint32_t textureHeight) {
    const TextureFormat format = this->m_format;
    const uint32_t flags = this->m_flags;
    // create an empty texture that'll be free'd so the FBO is transparent
    constexpr GLenum drawBuffers[1] = { GL_COLOR_ATTACHMENT0 };
    // create the main framebuffer
    glGenFramebuffers (1, &this->m_framebuffer);
    glBindFramebuffer (GL_FRAMEBUFFER, this->m_framebuffer);
    // create the main texture
    glGenTextures (1, &this->m_texture);
    // bind the new texture to set settings on it
    glBindTexture (GL_TEXTURE_2D, this->m_texture);
    if (format == TextureFormat_RGBA16161616f) {
	glTexImage2D (GL_TEXTURE_2D, 0, GL_RGBA16F, textureWidth, textureHeight, 0, GL_RGBA, GL_HALF_FLOAT, nullptr);
    } else {
	glTexImage2D (GL_TEXTURE_2D, 0, GL_RGBA8, textureWidth, textureHeight, 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
    }
    // label stuff for debugging
#if !NDEBUG
    glObjectLabel (GL_TEXTURE, this->m_texture, -1, this->m_name.c_str ());
#endif /* DEBUG */
    // set filtering parameters, otherwise the texture is not rendered
    if (flags & TextureFlags_ClampUVs) {
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    } else if (flags & TextureFlags_ClampUVsBorder) {
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_BORDER);
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_BORDER);
    } else {
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT);
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT);
    }

    if (flags & TextureFlags_NoInterpolation) {
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    } else {
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    }

    glTexParameterf (GL_TEXTURE_2D, GL_TEXTURE_MAX_ANISOTROPY, 8.0f);

    // set the texture as the colour attachmend #0
    glFramebufferTexture2D (GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, this->m_texture, 0);
    // finally set the list of draw buffers
    glDrawBuffers (1, drawBuffers);

    // ensure first framebuffer is okay
    if (glCheckFramebufferStatus (GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
	sLog.exception ("Framebuffers are not properly set");
    }

    GLfloat previousClearColor[4] = {};
    glGetFloatv (GL_COLOR_CLEAR_VALUE, previousClearColor);
    glColorMask (true, true, true, true);
    glClearColor (0.0f, 0.0f, 0.0f, 0.0f);
    glClear (GL_COLOR_BUFFER_BIT);
    glClearColor (previousClearColor[0], previousClearColor[1], previousClearColor[2], previousClearColor[3]);
}

CFBO::~CFBO () {
    if (this->m_texture != GL_NONE) {
	glDeleteTextures (1, &this->m_texture);
    }
    if (this->m_depthTexture != GL_NONE) {
	glDeleteTextures (1, &this->m_depthTexture);
    }
    if (this->m_depthbuffer != GL_NONE) {
	glDeleteRenderbuffers (1, &this->m_depthbuffer);
    }
    if (this->m_framebuffer != GL_NONE) {
	glDeleteFramebuffers (1, &this->m_framebuffer);
    }
}

void CFBO::ensureDepthTextureAttachment () const {
    if (this->m_depthTexture != GL_NONE) {
	return;
    }

    GLint prevFramebuffer = 0;
    GLint prevTexture = 0;
    glGetIntegerv (GL_FRAMEBUFFER_BINDING, &prevFramebuffer);
    glGetIntegerv (GL_TEXTURE_BINDING_2D, &prevTexture);

    glGenTextures (1, &this->m_depthTexture);
    glBindTexture (GL_TEXTURE_2D, this->m_depthTexture);
    glTexImage2D (
	GL_TEXTURE_2D, 0, GL_DEPTH_COMPONENT24, static_cast<GLsizei> (this->getRealWidth ()),
	static_cast<GLsizei> (this->getRealHeight ()), 0, GL_DEPTH_COMPONENT, GL_UNSIGNED_INT, nullptr
    );
    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_BORDER);
    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_BORDER);
    constexpr float litBorder[] = { 1.0f, 1.0f, 1.0f, 1.0f };
    glTexParameterfv (GL_TEXTURE_2D, GL_TEXTURE_BORDER_COLOR, litBorder);
    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_COMPARE_MODE, GL_COMPARE_REF_TO_TEXTURE);
    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_COMPARE_FUNC, GL_LEQUAL);

    glBindFramebuffer (GL_FRAMEBUFFER, this->m_framebuffer);
    glFramebufferTexture2D (GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_TEXTURE_2D, this->m_depthTexture, 0);

    if (glCheckFramebufferStatus (GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
	sLog.exception ("Framebuffer incomplete after depth-texture attachment on ", this->m_name);
    }

    glBindFramebuffer (GL_FRAMEBUFFER, static_cast<GLuint> (prevFramebuffer));
    glBindTexture (GL_TEXTURE_2D, static_cast<GLuint> (prevTexture));
}

void CFBO::ensureDepthAttachment () const {
    if (this->m_depthbuffer != GL_NONE) {
	return;
    }

    GLint prevFramebuffer = 0;
    GLint prevRenderbuffer = 0;
    glGetIntegerv (GL_FRAMEBUFFER_BINDING, &prevFramebuffer);
    glGetIntegerv (GL_RENDERBUFFER_BINDING, &prevRenderbuffer);

    glGenRenderbuffers (1, &this->m_depthbuffer);
    glBindRenderbuffer (GL_RENDERBUFFER, this->m_depthbuffer);
    glRenderbufferStorage (
	GL_RENDERBUFFER, GL_DEPTH_COMPONENT24, static_cast<GLsizei> (this->getRealWidth ()),
	static_cast<GLsizei> (this->getRealHeight ())
    );
    glBindFramebuffer (GL_FRAMEBUFFER, this->m_framebuffer);
    glFramebufferRenderbuffer (GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_RENDERBUFFER, this->m_depthbuffer);

    if (glCheckFramebufferStatus (GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
	sLog.exception ("Framebuffer incomplete after depth attachment on ", this->m_name);
    }

    glBindFramebuffer (GL_FRAMEBUFFER, prevFramebuffer);
    glBindRenderbuffer (GL_RENDERBUFFER, prevRenderbuffer);
}

const std::string& CFBO::getName () const { return this->m_name; }

const float& CFBO::getScale () const { return this->m_scale; }

TextureFormat CFBO::getFormat () const { return this->m_format; }

uint32_t CFBO::getFlags () const { return this->m_flags; }

GLuint CFBO::getFramebuffer () const { return this->m_framebuffer; }

GLuint CFBO::getDepthbuffer () const { return this->m_depthbuffer; }

GLuint CFBO::getTextureID (uint32_t imageIndex) const { return this->m_texture; }

uint32_t CFBO::getTextureWidth (uint32_t imageIndex) const { return this->m_resolution.x; }

uint32_t CFBO::getTextureHeight (uint32_t imageIndex) const { return this->m_resolution.y; }

uint32_t CFBO::getRealWidth () const { return this->m_resolution.z; }

uint32_t CFBO::getRealHeight () const { return this->m_resolution.w; }

const std::vector<FrameSharedPtr>& CFBO::getFrames () const { return this->m_frames; }

const glm::vec4* CFBO::getResolution () const { return &this->m_resolution; }

bool CFBO::isAnimated () const { return false; }

uint32_t CFBO::getSpritesheetCols () const {
    return 0; // FBOs don't have spritesheets
}

uint32_t CFBO::getSpritesheetRows () const {
    return 0; // FBOs don't have spritesheets
}

uint32_t CFBO::getSpritesheetFrames () const {
    return 0; // FBOs don't have spritesheets
}

float CFBO::getSpritesheetDuration () const {
    return 0.0f; // FBOs don't have spritesheets
}

void CFBO::incrementUsageCount () const { }
void CFBO::decrementUsageCount () const { }
void CFBO::update () const { }
// FBOs are always ready
bool CFBO::isReady () const { return true; }