#pragma once
#include "WallpaperEngine/Render/CObject.h"
#include "WallpaperEngine/Render/FBOProvider.h"
#include "WallpaperEngine/Render/Objects/Effects/CPass.h"
#include "WallpaperEngine/Render/Wallpapers/CScene.h"

#include "WallpaperEngine/Render/Shaders/Shader.h"

using namespace WallpaperEngine;
using namespace WallpaperEngine::Render;

namespace WallpaperEngine::Render::Objects {
class CRenderable : virtual public CObject, public FBOProvider {
    friend CObject;

public:
    CRenderable (Wallpapers::CScene& scene, const Object& object, const Material& material);

    [[nodiscard]] std::shared_ptr<const TextureProvider> getTexture () const;

    [[nodiscard]] double getAnimationTime () const;

    struct TextureAnimationPlayback {
	bool controlled = false;
	bool playing = true;
	double rate = 1.0;
	// frame override (setFrame while paused); -1 = none
	int frameOverride = -1;
	// clock freeze point (pause) / rebase origin (play), in g_Time seconds
	double baseTime = 0.0;
    };

    [[nodiscard]] bool hasTextureAnimation () const;
    [[nodiscard]] size_t getTextureAnimationFrameCount () const;
    void pauseTextureAnimation ();
    void playTextureAnimation ();
    [[nodiscard]] bool isTextureAnimationPlaying () const;
    void setTextureAnimationFrame (size_t frame);
    [[nodiscard]] size_t getTextureAnimationFrame () const;
    [[nodiscard]] const TextureAnimationPlayback& getTextureAnimationPlayback () const {
	return m_textureAnimationPlayback;
    }

    void setup () override;

    [[nodiscard]] virtual const float& getBrightness () const = 0;
    [[nodiscard]] virtual const float& getUserAlpha () const = 0;
    [[nodiscard]] virtual const float& getAlpha () const = 0;
    [[nodiscard]] virtual const glm::vec3& getColor () const = 0;
    [[nodiscard]] virtual glm::vec4 getColor4 () const = 0;
    [[nodiscard]] virtual const glm::vec3& getCompositeColor () const = 0;

    [[nodiscard]] virtual glm::vec3 toClassicLightSpace (const glm::vec3& litSpacePos) const { return litSpacePos; }

    /** Same mapping, but into the image-LOCAL 0..size frame an effect-chain FIRST pass
     * renders its vertices in (ortho(0,size) copy projection). Default: the world-frame
     * mapping (renderables whose passes never use a local frame). */
    [[nodiscard]] virtual glm::vec3 toClassicLightSpaceLocal (const glm::vec3& litSpacePos) const {
	return toClassicLightSpace (litSpacePos);
    }

    [[nodiscard]] virtual float classicLocalRadianceScale () const { return 1.0f; }

protected:
    void detectTexture ();

    double m_animationTime = 0.0;

    TextureAnimationPlayback m_textureAnimationPlayback = {};

    std::shared_ptr<const TextureProvider> m_texture = nullptr;
    const Material& m_material;
};
}
