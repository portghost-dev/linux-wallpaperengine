#include "CWeb.h"

#include "WallpaperEngine/Audio/Drivers/Recorders/PlaybackRecorder.h"
#include "WallpaperEngine/Data/Model/Project.h"
#include "WallpaperEngine/Data/Model/Property.h"
#include "WallpaperEngine/Data/Model/Wallpaper.h"
#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/WebHelper/PropertyClassifier.h"

#include <chrono>
#include <cstdlib>
#include <sstream>

using namespace WallpaperEngine::Render;
using namespace WallpaperEngine::Render::Wallpapers;
using namespace WallpaperEngine::WebHelper;

bool CWeb::mouseDebugEnabled () {
    static const bool enabled = getenv ("LWE_MOUSEDBG") != nullptr;
    return enabled;
}

CWeb::CWeb (
    const Wallpaper& wallpaper, RenderContext& context, AudioContext& audioContext, HelperClient& helper,
    const WallpaperState::TextureUVsScaling& scalingMode, const uint32_t& clampMode
) : CWallpaper (wallpaper, context, audioContext, scalingMode, clampMode), m_helper (helper) {
    const int outputWidth = context.getOutput ().getFullWidth ();
    const int outputHeight = context.getOutput ().getFullHeight ();
    if (outputWidth > 0) {
	this->m_width = outputWidth;
    }
    if (outputHeight > 0) {
	this->m_height = outputHeight;
    }

    // setup framebuffers
    this->setupFramebuffers ();

    this->m_instanceId = this->m_helper.allocateInstance ();

    // documentation says that 60 fps is the maximum value
    const uint32_t framerate
	= static_cast<uint32_t> (std::max (60, context.getApp ().getContext ().settings.render.maximumFPS));

    // the helper builds the <scheme>://root/<filename> URL from these two fields; the
    // scheme name is derived the same way on both sides (WPSchemeHandlerFactory)
    this->m_helper.create (
	this->m_instanceId, this->getWeb ().project.workshopId, this->getWeb ().filename,
	static_cast<uint32_t> (this->m_width), static_cast<uint32_t> (this->m_height), framerate
    );
}

void CWeb::allocateTexture () {
    if (this->m_width <= 0 || this->m_height <= 0) {
	return;
    }

    if (this->m_textureWidth == this->m_width && this->m_textureHeight == this->m_height) {
	return;
    }

    glBindTexture (GL_TEXTURE_2D, this->getWallpaperTexture ());
    glTexImage2D (GL_TEXTURE_2D, 0, GL_RGBA8, this->m_width, this->m_height, 0, GL_BGRA, GL_UNSIGNED_BYTE, nullptr);

    this->m_textureWidth = this->m_width;
    this->m_textureHeight = this->m_height;
}

void CWeb::setSize (const int width, const int height) {
    this->m_width = width > 0 ? width : this->m_width;
    this->m_height = height > 0 ? height : this->m_height;

    // do not refresh the texture if any of the sizes are invalid
    if (this->m_width <= 0 || this->m_height <= 0) {
	return;
    }

    // the legitimate reallocation point, and the only one
    this->allocateTexture ();

    this->m_helper.resize (
	this->m_instanceId, static_cast<uint32_t> (this->m_width), static_cast<uint32_t> (this->m_height)
    );
}

bool CWeb::syncFrameReader () {
    const auto* state = this->m_helper.instance (this->m_instanceId);

    if (state == nullptr || state->frameGeneration == 0) {
	if (this->m_frameGeneration != 0) {
	    this->m_frames.close ();
	    this->m_frameGeneration = 0;
	}

	return false;
    }

    if (state->frameGeneration == this->m_frameGeneration) {
	return this->m_frames.isOpen ();
    }

    // A new generation means a new shm object, which means a resize happened. Opening the
    // new one implicitly drops the old mapping; the helper already unlinked its name, so
    // this is the moment the old buffer's memory is actually returned.
    const std::string name = WallpaperEngine::WebHelper::frameShmName (
	this->m_helper.helperPid (), this->m_instanceId, state->frameGeneration
    );

    if (!this->m_frames.open (name)) {
	sLog.error ("web: cannot map frame buffer ", name, ": ", this->m_frames.error ());
	this->m_frameGeneration = 0;

	return false;
    }

    this->m_frameGeneration = state->frameGeneration;

    return true;
}

void CWeb::injectProperties () {
    this->m_helper.injectProperties (
	this->m_instanceId, WallpaperEngine::WebHelper::classifyProperties (this->getWeb ().project.properties)
    );

    this->m_propertiesInjected = true;
}

void CWeb::notifyPropertyChanged (const std::string& key) {
    using namespace WallpaperEngine::Data::Model;
    const auto& properties = this->getWeb ().project.properties;
    const auto it = properties.find (key);

    if (it == properties.end () || it->second == nullptr) {
	return;
    }

    if (auto classified = WallpaperEngine::WebHelper::classifyProperty (key, *it->second); classified.has_value ()) {
	this->m_helper.setProperty (this->m_instanceId, *classified);
    }
}

void CWeb::injectAudio () {
    if ((this->m_audioFrameCount++ & 1) != 0) {
	return;
    }

    const auto& recorder = this->getAudioContext ().getRecorder ();

    this->m_helper.audioSpectrum (this->m_instanceId, recorder.audio64);
}

void CWeb::renderFrame (const glm::ivec4& viewport) {
    // ensure the viewport matches the window size, and resize if needed
    if (viewport.z != this->getWidth () || viewport.w != this->getHeight ()) {
	this->setSize (viewport.z, viewport.w);
    }

    if (!this->m_propertiesInjected && this->m_helper.isPageLoaded (this->m_instanceId)) {
	this->injectProperties ();
    }

    // ensure the virtual mouse position is up to date
    this->updateMouse (viewport);
    // use the scene's framebuffer by default
    glBindFramebuffer (GL_FRAMEBUFFER, this->getWallpaperFramebuffer ());
    // ensure we render over the whole framebuffer
    glViewport (0, 0, this->getWidth (), this->getHeight ());

    if (this->syncFrameReader ()) {
	// A generation always matches the size we asked for, but the resize round-trip is
	// asynchronous, so the mapping can still describe the PREVIOUS size for a frame or
	// two. Upload at the buffer's size, not the viewport's, and keep the texture sized
	// to match, or glTexSubImage2D would read past the end of the slot.
	if (static_cast<int> (this->m_frames.width ()) != this->m_textureWidth
	    || static_cast<int> (this->m_frames.height ()) != this->m_textureHeight) {
	    this->m_textureWidth = 0;
	    this->m_textureHeight = 0;
	    this->m_width = static_cast<int> (this->m_frames.width ());
	    this->m_height = static_cast<int> (this->m_frames.height ());
	    this->allocateTexture ();
	}

	this->m_frames.consume ([this] (const void* pixels, const uint32_t width, const uint32_t height) {
	    glBindTexture (GL_TEXTURE_2D, this->getWallpaperTexture ());
	    glTexSubImage2D (
		GL_TEXTURE_2D, 0, 0, 0, static_cast<GLsizei> (width), static_cast<GLsizei> (height), GL_BGRA,
		GL_UNSIGNED_BYTE, pixels
	    );
	});
    }

    if (this->getWeb ().project.supportsAudioProcessing) {
	this->injectAudio ();
    }
}

void CWeb::updateMouse (const glm::ivec4&) {
    auto& input = this->getContext ().getInputContext ().getMouseInput ();

    const bool known = input.hasPointer ();
    const glm::dvec2 n = input.normalized ();
    const auto leftClick = input.leftClick ();
    const auto rightClick = input.rightClick ();

    const auto move = this->m_moveGate.update (
	known ? std::optional<glm::dvec2> (n) : std::nullopt, this->getWidth (), this->getHeight ()
    );

    if (move.send) {
	this->m_helper.mouseMove (this->m_instanceId, move.x, move.y);
    }

    glm::ivec2 clickAt (this->getWidth () / 2, this->getHeight () / 2);

    if (move.send) {
	clickAt = { move.x, move.y };
    } else if (this->m_moveGate.lastSent ().has_value ()) {
	clickAt = *this->m_moveGate.lastSent ();
    }

    if (CWeb::mouseDebugEnabled ()) {
	const auto now = std::chrono::steady_clock::now ();

	if (now - this->m_lastMouseDebug >= std::chrono::seconds (1)) {
	    this->m_lastMouseDebug = now;
	    sLog.out (
		"LWE-MOUSEDBG web instance ", this->m_instanceId, " normalized=",
		known ? "(" + std::to_string (n.x) + "," + std::to_string (n.y) + ")" : std::string ("unknown"),
		" pixel=(", move.x, ",", move.y, ") size=", this->getWidth (), "x", this->getHeight (),
		" movesSent=", this->m_moveGate.movesSent (), " movesSuppressed=", this->m_moveGate.movesSuppressed (),
		" movesUnknown=", this->m_moveGate.movesUnknown ()
	    );
	    this->m_moveGate.resetCounters ();
	}
    }

    // TODO: ANY OTHER MOUSE EVENTS TO SEND?
    if (leftClick != this->m_leftClick) {
	this->m_helper.mouseClick (
	    this->m_instanceId, clickAt.x, clickAt.y, MouseButton::Left,
	    leftClick == WallpaperEngine::Input::MouseClickStatus::Released
	);
    }

    if (rightClick != this->m_rightClick) {
	this->m_helper.mouseClick (
	    this->m_instanceId, clickAt.x, clickAt.y, MouseButton::Right,
	    rightClick == WallpaperEngine::Input::MouseClickStatus::Released
	);
    }

    this->m_leftClick = leftClick;
    this->m_rightClick = rightClick;
}

CWeb::~CWeb () { this->m_helper.destroy (this->m_instanceId); }
