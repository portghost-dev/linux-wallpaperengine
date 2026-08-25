#include <iostream>

#include <GL/glew.h>

#include "CWallpaper.h"
#include "RenderContext.h"

#include "WallpaperEngine/Data/Model/Project.h"
#include "WallpaperEngine/Logging/Log.h"

#include <set>

namespace WallpaperEngine::Render {
RenderContext::RenderContext (
    Drivers::VideoDriver& driver, WallpaperApplication& app, Media::MediaSource& mediaSource
) :
    m_driver (driver), m_app (app), m_mediaSource (mediaSource),
    m_textureCache (std::make_unique<TextureCache> (*this)) { }

void RenderContext::render (Drivers::Output::OutputViewport* viewport) {
    viewport->makeCurrent ();

#if !NDEBUG
    const std::string str = "Rendering to output " + viewport->name;

    glPushDebugGroup (GL_DEBUG_SOURCE_APPLICATION, 0, -1, str.c_str ());
#endif /* DEBUG */

    // search the background in the viewport selection

    // render the background
    if (const auto ref = this->m_wallpapers.find (viewport->name); ref != this->m_wallpapers.end ()) {
	ref->second->render (
	    viewport->viewport, this->getOutput ().renderVFlip (), viewport->globalPosition, viewport->logicalSize,
	    viewport->name
	);
    } else {
	glBindFramebuffer (GL_FRAMEBUFFER, 0);
	glColorMask (GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
	GLfloat previousClearColor[4];
	glGetFloatv (GL_COLOR_CLEAR_VALUE, previousClearColor);
	glClearColor (0.0f, 0.0f, 0.0f, 1.0f);
	glClear (GL_COLOR_BUFFER_BIT);
	glClearColor (previousClearColor[0], previousClearColor[1], previousClearColor[2], previousClearColor[3]);

	static std::set<std::string> loggedMissing;
	if (loggedMissing.insert (viewport->name).second && !this->m_app.getContext ().settings.general.daemonMode) {
	    std::string known;
	    for (const auto& [name, wallpaper] : this->m_wallpapers) {
		known += (known.empty () ? "" : ", ") + name;
	    }
	    sLog.error ("No wallpaper mapped for viewport '", viewport->name, "' (mapped: ", known, ")");
	}
    }

#if !NDEBUG
    glPopDebugGroup ();
#endif /* DEBUG */

    viewport->swapOutput ();
}

void RenderContext::setWallpaper (const std::string& display, std::shared_ptr<CWallpaper> wallpaper) {
    wallpaper->setDestinationFramebuffer (this->m_app.getDestinationFramebuffer ());
    this->m_wallpapers.insert_or_assign (display, wallpaper);
}

void RenderContext::clearWallpapers () { this->m_wallpapers.clear (); }

size_t RenderContext::evictUnusedTextures () { return this->m_textureCache->evictUnused (); }

void RenderContext::setPause (const bool newState) const {
    for (const auto& wallpaper : this->m_wallpapers | std::views::values) {
	wallpaper->setPause (newState);
    }
}

void RenderContext::setPlaybackSpeed (const float speed) const {
    for (const auto& wallpaper : this->m_wallpapers | std::views::values) {
	wallpaper->setPlaybackSpeed (speed);
    }
}

void RenderContext::setAudioVolume (const int volume) const {
    for (const auto& wallpaper : this->m_wallpapers | std::views::values) {
	wallpaper->setAudioVolume (volume);
    }
}

Input::InputContext& RenderContext::getInputContext () const { return this->m_driver.getInputContext (); }

const WallpaperApplication& RenderContext::getApp () const { return this->m_app; }

const Drivers::VideoDriver& RenderContext::getDriver () const { return this->m_driver; }

const Drivers::Output::Output& RenderContext::getOutput () const { return this->m_driver.getOutput (); }

std::shared_ptr<const TextureProvider> RenderContext::resolveTexture (const std::string& name) const {
    return this->m_textureCache->resolve (name);
}

const std::map<std::string, std::shared_ptr<CWallpaper>>& RenderContext::getWallpapers () const {
    return this->m_wallpapers;
}

Media::MediaSource& RenderContext::getMediaSource () const { return this->m_mediaSource; }

} // namespace WallpaperEngine::Render
