#pragma once

#include <GL/glew.h>
#include <GLFW/glfw3.h>
#include <glm/vec4.hpp>

#include "TextureProvider.h"

namespace WallpaperEngine::Render {
using namespace WallpaperEngine::Data::Assets;
/**
 * Represents current wallpaper state
 */
class WallpaperState {
public:
    // Scaling modes. Defines how UVs coordinates are calculated.
    enum class TextureUVsScaling : uint8_t {
	DefaultUVs,
	ZoomFitUVs,
	ZoomFillUVs,
	StretchUVs,
    };

    /** Zoom/pan window nested in the scaling mode: zoom 1..2 inward, pans -1..1 (see FORK-MAP). */
    struct Fit {
	float zoom = 1.0f;
	float panX = 0.0f;
	float panY = 0.0f;

	bool operator== (const Fit&) const = default;
    };

    /** the two fit layers composed: zooms multiply, pans add, the result clamped */
    [[nodiscard]] static Fit composeFit (const Fit& wallpaper, const Fit& lane);

    WallpaperState (const TextureUVsScaling& textureUVsMode, const uint32_t& clampMode);

    /**
     * Checks if any of the given values has changed
     * @param viewport
     * @param vflip
     * @param projectionWidth
     * @param projectionHeight
     * @return
     */
    [[nodiscard]] bool hasChanged (
	const glm::ivec4& viewport, const bool& vflip, const int& projectionWidth, const int& projectionHeight
    ) const;

    /**
     * Resets UVs to 0/1 values.
     */
    void resetUVs ();

    /**
     * Updates UVs coordinates for current viewport and projection
     *
     * @param projectionWidth
     * @param projectionHeight
     */
    void updateUs (const int& projectionWidth, const int& projectionHeight);

    /**
     * Updates Vs coordinates for current viewport and projection
     *
     * @param projectionWidth
     * @param projectionHeight
     */
    void updateVs (const int& projectionWidth, const int& projectionHeight);

    /**
     * @return Texture UV coordinates for current viewport and projection
     */
    [[nodiscard]] auto getTextureUVs () const { return m_UVs; };

    /**
     * Updates UVs coordinates for current viewport and projection
     */
    template <WallpaperState::TextureUVsScaling> void updateTextureUVs ();

    // Updates state with provided values
    void updateState (
	const glm::ivec4& viewport, const bool& vflip, const int& projectionWidth, const int& projectionHeight
    );

    /** Sets the fit window; the next updateState recomputes the UVs (hasChanged reports it). */
    void setFit (const Fit& fit);

    /**
     * @return The requested fit window
     */
    [[nodiscard]] const Fit& getFit () const;

    /**
     * @return The texture scaling mode
     */
    [[nodiscard]] TextureUVsScaling getTextureUVsScaling () const;

    /**
     * @return The texture clamping mode.
     */
    [[nodiscard]] uint32_t getClampingMode () const;

    /**
     * Sets the texture scaling mode
     *
     * @param strategy
     */
    void setTextureUVsStrategy (TextureUVsScaling strategy);

    /**
     * @return The width of viewport
     */
    [[nodiscard]] int getViewportWidth () const;

    /**
     * @return The height of viewport
     */
    [[nodiscard]] int getViewportHeight () const;

    /**
     * @return The width of the projection
     */
    [[nodiscard]] int getProjectionWidth () const;

    /**
     * @return The height of the projection
     */
    [[nodiscard]] int getProjectionHeight () const;

private:
    // Cached UVs value for texture coordinates. No need to recalculate if viewport and projection haven't changed.
    struct {
	float ustart;
	float uend;
	float vstart;
	float vend;
    } m_UVs {};

    // Viewport for which UVs were calculated
    struct {
	int width;
	int height;
    } m_viewport {};

    // Wallpaper dimensions
    struct {
	int width;
	int height;
    } m_projection {};

    // Are Vs coordinates fliped
    bool m_vflip = false;

    // The fit window asked for, and the one the cached UVs were computed with
    Fit m_fit {};
    Fit m_appliedFit {};

    /** Narrows m_UVs to the fit window; runs after the scaling mode set the full range */
    void applyFit ();

    // Texture scaling mode
    TextureUVsScaling m_textureUVsMode = TextureUVsScaling::DefaultUVs;
    uint32_t m_clampingMode = TextureFlags_NoFlags;
};
} // namespace WallpaperEngine::Render