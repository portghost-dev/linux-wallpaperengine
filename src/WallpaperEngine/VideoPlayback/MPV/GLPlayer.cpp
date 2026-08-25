#include "GLPlayer.h"

#include "WallpaperEngine/Logging/Log.h"

#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <mpv/render_gl.h>
#include <mpv/stream_cb.h>
#include <thread>

using namespace WallpaperEngine::VideoPlayback::MPV;

// LWE_MPV_REALSYNC restores mpv's real ARB_sync entry points and direct rendering
static bool useRealMpvSync () {
    const char* mode = getenv ("LWE_MPV_REALSYNC");
    return mode != nullptr && *mode != '\0';
}

// nothing in mpv's render-API backend ever reaps its per-frame fences, so real sync
// objects accumulate for the life of this GL context; the stubs allocate nothing
static GLsync stubFenceSync (GLenum, GLbitfield) { return nullptr; }
static GLenum stubClientWaitSync (GLsync, GLbitfield, GLuint64) { return GL_ALREADY_SIGNALED; }
static void stubDeleteSync (GLsync) { }

static void* get_proc_address (void* ctx, const char* name) {
    if (!useRealMpvSync ()) {
	if (strcmp (name, "glFenceSync") == 0) {
	    return reinterpret_cast<void*> (&stubFenceSync);
	}
	if (strcmp (name, "glClientWaitSync") == 0) {
	    return reinterpret_cast<void*> (&stubClientWaitSync);
	}
	if (strcmp (name, "glDeleteSync") == 0) {
	    return reinterpret_cast<void*> (&stubDeleteSync);
	}
    }
    return static_cast<GLPlayer*> (ctx)->getContext ().getDriver ().getProcAddress (name);
}

GLPlayer::GLPlayer (
    RenderContext& context, GLuint outputTexture, const std::filesystem::path& file, const int64_t baseWidth,
    const int64_t baseHeight, const GLuint fbo
) : ContextAware (context), m_outputTexture (outputTexture), m_width (baseWidth), m_height (baseHeight) {
    this->m_fbo = fbo;
    this->m_doWeOwnFramebuffer = this->m_fbo == GL_NONE;

    this->prepareGL ();
    this->setSource (file);
}

GLPlayer::GLPlayer (
    RenderContext& context, const GLuint outputTexture, MemoryStreamProtocolUniquePtr stream, const int64_t baseWidth,
    const int64_t baseHeight, const GLuint fbo
) : ContextAware (context), m_outputTexture (outputTexture), m_width (baseWidth), m_height (baseHeight) {
    this->m_fbo = fbo;
    this->m_doWeOwnFramebuffer = this->m_fbo == GL_NONE;

    this->prepareGL ();
    this->setSource (std::move (stream));
}

GLPlayer::~GLPlayer () {
    // clean up any kept resources
    this->stop ();

    // only clean up framebuffer if we own it
    if (this->m_doWeOwnFramebuffer) {
	// free gl resources too
	glDeleteFramebuffers (1, &this->m_fbo);
    }
}

void GLPlayer::incrementUsageCount () {
    this->m_usageCount++;

    if (this->m_usageCount == 1) {
	this->play ();
    }
}

void GLPlayer::decrementUsageCount () {
    if (this->m_usageCount == 0) {
	sLog.exception ("GLPlayer usage count would underflow");
    }

    this->m_usageCount--;

    if (this->m_usageCount == 0) {
	this->stop ();
    }
}

void GLPlayer::setUntimed () {
    if (this->m_handle) {
	sLog.exception ("Cannot set untimed mode after playback has started");
    }

    this->m_untimed = true;
}

void GLPlayer::clearUntimed () {
    if (this->m_handle) {
	sLog.exception ("Cannot set untimed mode after playback has started");
    }

    this->m_untimed = false;
}

void GLPlayer::setMuted () {
    this->m_muted = true;

    if (this->m_handle) {
	mpv_set_property_string (this->m_handle, "mute", "yes");
    }
}

void GLPlayer::clearMuted () {
    this->m_muted = false;

    if (this->m_handle) {
	mpv_set_property_string (this->m_handle, "mute", "no");
    }
}

void GLPlayer::setVolume (double volume) {
    this->m_volume = volume;

    if (this->m_handle) {
	mpv_set_property (this->m_handle, "volume", MPV_FORMAT_DOUBLE, &this->m_volume);
    }
}

void GLPlayer::setSpeed (const double speed) {
    // mpv's own documented range; 0 is not a valid rate - pausing is a separate fact
    this->m_speed = std::clamp (speed, 0.01, 100.0);
    if (this->m_handle != nullptr) {
	mpv_set_property (this->m_handle, "speed", MPV_FORMAT_DOUBLE, &this->m_speed);
    }
}

void GLPlayer::setPaused () {
    this->m_paused = true;

    if (this->m_handle) {
	mpv_set_property_string (this->m_handle, "pause", "yes");
    }
}

void GLPlayer::clearPaused () {
    this->m_paused = false;

    if (this->m_handle) {
	mpv_set_property_string (this->m_handle, "pause", "no");
    }
}

void GLPlayer::render () const {
    // rendering should only happen if the texture is in use
    if (this->m_handle == nullptr) {
	return;
    }

    // read all the events available
    while (true) {
	const mpv_event* event = mpv_wait_event (this->m_handle, 0);

	if (event == nullptr || event->event_id == MPV_EVENT_NONE) {
	    break;
	}

	if (event->event_id != MPV_EVENT_VIDEO_RECONFIG) {
	    continue;
	}

	int64_t width, height;

	if (mpv_get_property (this->m_handle, "dwidth", MPV_FORMAT_INT64, &width) < 0) {
	    continue;
	}

	if (mpv_get_property (this->m_handle, "dheight", MPV_FORMAT_INT64, &height) < 0) {
	    continue;
	}

	if (width < 0 || height < 0) {
	    continue;
	}

	this->m_width = width;
	this->m_height = height;
	// reconfigure the texture
	glBindTexture (GL_TEXTURE_2D, this->m_outputTexture);
	glTexImage2D (GL_TEXTURE_2D, 0, GL_RGBA8, this->m_width, this->m_height, 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
    }

    // render the next
    glViewport (0, 0, this->m_width, this->m_height);

    mpv_opengl_fbo fbo { static_cast<int> (this->m_fbo), static_cast<int> (this->m_width),
			 static_cast<int> (this->m_height), GL_RGBA8 };

    // no need to flip as it'll be handled by the wallpaper rendering code
    int flip_y = 0;

    mpv_render_param params[] = { { MPV_RENDER_PARAM_OPENGL_FBO, &fbo },
				  { MPV_RENDER_PARAM_FLIP_Y, &flip_y },
				  { MPV_RENDER_PARAM_INVALID, nullptr } };

    glColorMask (true, true, true, true);
    mpv_render_context_render (this->m_renderContext, params);
}

int GLPlayer::getWidth () const { return this->m_width; }
int GLPlayer::getHeight () const { return this->m_height; }

void GLPlayer::prepareGL () {
    if (!this->m_doWeOwnFramebuffer || this->m_fbo != GL_NONE) {
	return;
    }

    glGenFramebuffers (1, &this->m_fbo);
    glBindFramebuffer (GL_FRAMEBUFFER, this->m_fbo);
    glBindTexture (GL_TEXTURE_2D, this->m_outputTexture);
    // reset texture's contents
    glTexImage2D (GL_TEXTURE_2D, 0, GL_RGBA8, this->m_width, this->m_height, 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
    constexpr GLenum drawBuffers[1] = { GL_COLOR_ATTACHMENT0 };
    glFramebufferTexture2D (GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, this->m_outputTexture, 0);
    glDrawBuffers (1, drawBuffers);

    // ensure first framebuffer is okay
    if (glCheckFramebufferStatus (GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
	sLog.exception ("Framebuffers are not properly set");
    }

    glColorMask (true, true, true, true);
    GLfloat previousClearColor[4] = {};
    glGetFloatv (GL_COLOR_CLEAR_VALUE, previousClearColor);
    glClearColor (0.0f, 0.0f, 0.0f, 0.0f);
    glClear (GL_COLOR_BUFFER_BIT);
    glClearColor (previousClearColor[0], previousClearColor[1], previousClearColor[2], previousClearColor[3]);
}

void GLPlayer::init () {
    this->m_handle = mpv_create ();

    if (this->m_handle == nullptr) {
	sLog.exception ("Cannot create mpv context for video texture");
    }

    // setup mpv options for playback
    mpv_set_option_string (this->m_handle, "terminal", "yes");
#if NDEBUG
    mpv_set_option_string (this->m_handle, "msg-level", "all=status,statusline=no");
#else
    mpv_set_option_string (this->m_handle, "msg-level", "all=v");
#endif
    mpv_set_option_string (this->m_handle, "input-cursor", "no");
    mpv_set_option_string (this->m_handle, "cursor-autohide", "no");
    mpv_set_option_string (this->m_handle, "config", "no");
    mpv_set_option_string (this->m_handle, "fbo-format", "rgba8");
    mpv_set_option_string (this->m_handle, "vo", "libmpv");
    mpv_set_option_string (this->m_handle, "profile", "fast");
    mpv_set_option_string (this->m_handle, "untimed", this->m_untimed ? "yes" : "no");
    // looping local files need no deep read-ahead; the default ~200MB demuxer cache
    // is pure RAM cost and heap churn here. Override with LWE_MPV_DEMUX_MB.
    const char* demuxMode = getenv ("LWE_MPV_DEMUX_MB");
    int demuxMB = 48;
    if (demuxMode != nullptr && *demuxMode != '\0') {
	const int parsed = atoi (demuxMode);
	if (parsed > 0) {
	    demuxMB = parsed;
	} else {
	    sLog.error ("LWE_MPV_DEMUX_MB is not a positive number, using ", demuxMB);
	}
    }
    const std::string demuxMax = std::to_string (demuxMB) + "MiB";
    mpv_set_option_string (this->m_handle, "demuxer-max-bytes", demuxMax.c_str ());
    mpv_set_option_string (this->m_handle, "demuxer-max-back-bytes", "16MiB");
    // lavc thread pool defaults to core count; each thread holds in-flight frames.
    // Override with LWE_MPV_THREADS.
    const char* thrMode = getenv ("LWE_MPV_THREADS");
    const unsigned cores = std::thread::hardware_concurrency ();
    const unsigned defThreads = std::max (1u, std::min (4u, cores > 0 ? cores : 4u));
    const std::string threads = (thrMode && *thrMode) ? thrMode : std::to_string (defThreads);
    mpv_set_option_string (this->m_handle, "vd-lavc-threads", threads.c_str ());
    // stubbed fences cannot pace host-mapped PBO reuse, so direct rendering must stay
    // off whenever the stubs are active; both flip together on LWE_MPV_REALSYNC
    mpv_set_option_string (this->m_handle, "vd-lavc-dr", useRealMpvSync () ? "auto" : "no");

    if (mpv_initialize (this->m_handle) < 0) {
	sLog.exception ("Could not initialize mpv context");
    }

    const char* hwdecMode = getenv ("LWE_HWDEC");
    mpv_set_property_string (this->m_handle, "hwdec", (hwdecMode && *hwdecMode) ? hwdecMode : "no");
    // LWE: cap the NVDEC decode-ahead pool (libmpv default 6 -> 2) to trim ~160MB of hw-decode VRAM;
    // ignored under software decode. Override with LWE_MPV_EXTRA_FRAMES.
    const char* efMode = getenv ("LWE_MPV_EXTRA_FRAMES");
    mpv_set_property_string (this->m_handle, "hwdec-extra-frames", (efMode && *efMode) ? efMode : "2");
    mpv_set_property_string (this->m_handle, "loop", "inf");
    mpv_set_property (this->m_handle, "volume", MPV_FORMAT_DOUBLE, &this->m_volume);
    // playback rate set before init (a set-speed may have landed pre-creation)
    mpv_set_property (this->m_handle, "speed", MPV_FORMAT_DOUBLE, &this->m_speed);

    // initialize gl context for mpv
    mpv_opengl_init_params gl_init_params { get_proc_address, this };
    mpv_render_param params[] { { MPV_RENDER_PARAM_API_TYPE, const_cast<char*> (MPV_RENDER_API_TYPE_OPENGL) },
				{ MPV_RENDER_PARAM_OPENGL_INIT_PARAMS, &gl_init_params },
				{ MPV_RENDER_PARAM_INVALID, nullptr } };

    if (mpv_render_context_create (&this->m_renderContext, this->m_handle, params) < 0) {
	sLog.exception ("Failed to initialize MPV's GL context");
    }

    // mute the video if required
    mpv_set_property_string (this->m_handle, "mute", this->m_muted ? "yes" : "no");
    // ensure play/pause status is respected too
    mpv_set_property_string (this->m_handle, "pause", this->m_paused ? "yes" : "no");
}

void GLPlayer::setSource (const std::filesystem::path& file) { this->m_file = file; }

void GLPlayer::setSource (MemoryStreamProtocolUniquePtr source) { this->m_stream = std::move (source); }

void GLPlayer::play () {
    if (this->m_handle != nullptr) {
	sLog.exception ("Cannot play the same GLPlayer twice");
    }

    if (!this->m_file.has_value () && !this->m_stream.has_value ()) {
	sLog.exception ("Cannot play a GLPlayer without a source");
    }

    this->init ();

    if (this->m_file.has_value ()) {
	// build the path to the video file
	const char* command[] = { "loadfile", this->m_file.value ().c_str (), nullptr };

	if (mpv_command (this->m_handle, command) < 0) {
	    sLog.exception ("Cannot load video to play");
	}
    } else if (this->m_stream) {
	this->m_stream.value ()->registerReadCallback (this->m_handle);

	// start playing the video
	const char* command[] = { "loadfile", "buffer://", nullptr };

	if (mpv_command (this->m_handle, command) < 0) {
	    sLog.exception ("Cannot load video texture to play");
	}
    }
}

void GLPlayer::stop () {
    // clean up mpv and get it ready to start again at some point
    if (this->m_renderContext) {
	mpv_render_context_free (this->m_renderContext);
	this->m_renderContext = nullptr;
    }

    if (this->m_handle) {
	mpv_terminate_destroy (this->m_handle);
	this->m_handle = nullptr;
    }
}