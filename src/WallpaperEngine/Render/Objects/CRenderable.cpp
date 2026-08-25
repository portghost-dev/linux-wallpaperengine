#include "CRenderable.h"

#include "WallpaperEngine/Data/Model/Material.h"
#include "WallpaperEngine/Data/Model/Object.h"
#include "WallpaperEngine/Data/Parsers/MaterialParser.h"

using namespace WallpaperEngine;
using namespace WallpaperEngine::Render::Objects;
using namespace WallpaperEngine::Render::Objects::Effects;
using namespace WallpaperEngine::Data::Parsers;
using namespace WallpaperEngine::Data::Builders;

CRenderable::CRenderable (Wallpapers::CScene& scene, const Object& object, const Material& material) :
    CObject (scene, object), Render::FBOProvider (&scene), m_material (material) { }

void CRenderable::detectTexture () {
    if (TextureMap* textures = &(*this->m_material.passes.begin ())->textures; !textures->empty ()) {
	std::string textureName = textures->begin ()->second;

	if (textureName.find ("_rt_") == 0 || textureName.find ("_alias_") == 0) {
	    this->m_texture = this->find (textureName);
	} else {
	    this->m_texture = this->getContext ().resolveTexture (textureName);
	}
    }
}

void CRenderable::setup () {
    CObject::setup ();

    // calculate full animation time (if any)
    this->m_animationTime = 0.0f;

    const auto texture = this->getTexture ();
    if (texture == nullptr) {
	return;
    }

    for (const auto& cur : texture->getFrames ()) {
	this->m_animationTime += cur->frametime;
    }
}

std::shared_ptr<const TextureProvider> CRenderable::getTexture () const { return this->m_texture; }

double CRenderable::getAnimationTime () const { return this->m_animationTime; }
extern float g_Time;

bool CRenderable::hasTextureAnimation () const { return this->m_texture != nullptr && this->m_texture->isAnimated (); }

size_t CRenderable::getTextureAnimationFrameCount () const {
    return this->m_texture != nullptr ? this->m_texture->getFrames ().size () : 0;
}

void CRenderable::pauseTextureAnimation () {
    if (!this->m_textureAnimationPlayback.controlled || this->m_textureAnimationPlayback.playing) {
	// freeze the clock where it is (frame stays wherever the free-run left it)
	this->m_textureAnimationPlayback.baseTime = static_cast<double> (g_Time);
    }
    this->m_textureAnimationPlayback.controlled = true;
    this->m_textureAnimationPlayback.playing = false;
}

void CRenderable::playTextureAnimation () { this->m_textureAnimationPlayback = {}; }

bool CRenderable::isTextureAnimationPlaying () const {
    return !this->m_textureAnimationPlayback.controlled || this->m_textureAnimationPlayback.playing;
}

void CRenderable::setTextureAnimationFrame (const size_t frame) {
    this->m_textureAnimationPlayback.controlled = true;
    this->m_textureAnimationPlayback.playing = false;
    this->m_textureAnimationPlayback.frameOverride = static_cast<int> (frame);
}

size_t CRenderable::getTextureAnimationFrame () const {
    return this->m_textureAnimationPlayback.frameOverride >= 0
	? static_cast<size_t> (this->m_textureAnimationPlayback.frameOverride)
	: 0;
}
