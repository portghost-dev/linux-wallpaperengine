#pragma once

#include <set>

#include "Helpers/ContextAware.h"
#include "TextureProvider.h"
#include "WallpaperEngine/Data/Assets/Texture.h"
#include "WallpaperEngine/VideoPlayback/MPV/GLPlayer.h"

#include <GL/glew.h>
#include <glm/vec4.hpp>
#include <memory>
#include <mpv/client.h>
#include <mpv/render.h>
#include <mpv/render_gl.h>
#include <vector>

namespace WallpaperEngine::Render {
class RenderContext;
using namespace WallpaperEngine::Data::Assets;
using namespace WallpaperEngine::VideoPlayback::MPV;
/**
 * A normal texture file in WallpaperEngine's format
 */
class CTexture final : public TextureProvider, public Helpers::ContextAware {
public:
    /** capDimension > 0 = mip residency: upload starting at the first stored level
     *  whose dimensions fit the cap, rebased to level 0; 0 = full chain. */
    explicit CTexture (RenderContext& context, TextureUniquePtr header, int capDimension = 0);
    ~CTexture () override;

    /** Demand stream (swap-object rebuild): drop the capped GL objects and re-upload
     *  the FULL chain from the retained RAM copy. GL context must be current; holders
     *  pick the new IDs up on their next per-bind query. No-op when not capped. */
    void expandResidency ();

    [[nodiscard]] GLuint getTextureID (uint32_t imageIndex) const override;
    [[nodiscard]] uint32_t getTextureWidth (uint32_t imageIndex) const override;
    [[nodiscard]] uint32_t getTextureHeight (uint32_t imageIndex) const override;
    [[nodiscard]] uint32_t getRealWidth () const override;
    [[nodiscard]] uint32_t getRealHeight () const override;
    [[nodiscard]] TextureFormat getFormat () const override;
    [[nodiscard]] uint32_t getFlags () const override;
    [[nodiscard]] const glm::vec4* getResolution () const override;
    [[nodiscard]] const std::vector<FrameSharedPtr>& getFrames () const override;
    [[nodiscard]] bool isAnimated () const override;
    [[nodiscard]] uint32_t getSpritesheetCols () const override;
    [[nodiscard]] uint32_t getSpritesheetRows () const override;
    [[nodiscard]] uint32_t getSpritesheetFrames () const override;
    [[nodiscard]] float getSpritesheetDuration () const override;

    /**
     * Increments the usage count of the texture
     *
     * Directly controls playback for video CTextures, only started when at least one thing is using it
     * Initializes mpv if needed and starts playback
     */
    void incrementUsageCount () const override;
    /**
     * Decrements the usage count of the texture
     *
     * Directly controls playback for video CTextures, only stopped when nothing is using it
     * De-initializes mpv if needed
     */
    void decrementUsageCount () const override;
    /**
     * Some textures need to be updated
     */
    void update () const override;
    bool isReady () const override;

private:
    /**
     * @return The texture header
     */
    [[nodiscard]] const Texture& getHeader () const;

    /**
     * Calculate's texture's resolution vec4
     */
    void setupResolution ();
    /**
     * Determines the texture's internal storage format
     */
    GLint setupInternalFormat () const;
    /**
     * Prepares openGL parameters for loading texture data
     */
    void setupOpenGLParameters (uint32_t textureID) const;

    void createGL ();

    /** The texture header */
    TextureUniquePtr m_header;

    /** mip residency cap (largest resident dimension); 0 = uncapped */
    int m_capDimension = 0;
    bool m_held = false;
    /** OpenGL's texture ID */
    GLuint* m_textureID = nullptr;
    /** Resolution vector of the texture */
    glm::vec4 m_resolution {};
    /** The video player in use */
    GLPlayerUniquePtr m_player;
};

/** Find a capped CTexture behind a provider pointer (via the texture registry) and
 *  expand it to its full chain. False when the provider is not a live CTexture. */
bool expandCappedTexture (const TextureProvider* texture);
} // namespace WallpaperEngine::Render