#pragma once

// Matrices manipulation for OpenGL
#include <glm/ext.hpp>
#include <glm/glm.hpp>

#include <algorithm>
#include <chrono>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "WallpaperEngine/Audio/AudioStream.h"
#include "WallpaperEngine/Input/PointerMoveGate.h"
#include "WallpaperEngine/Render/CWallpaper.h"
#include "WallpaperEngine/WebHelper/FrameContract.h"
#include "WallpaperEngine/WebHelper/HelperClient.h"

#include "WallpaperEngine/Data/Model/Wallpaper.h"

namespace WallpaperEngine::Render::Wallpapers {
class CWeb : public CWallpaper {
public:
    CWeb (
	const Wallpaper& wallpaper, RenderContext& context, AudioContext& audioContext,
	WallpaperEngine::WebHelper::HelperClient& helper, const WallpaperState::TextureUVsScaling& scalingMode,
	const uint32_t& clampMode
    );
    ~CWeb () override;
    [[nodiscard]] int getWidth () const override { return this->m_width; }

    [[nodiscard]] int getHeight () const override { return this->m_height; }

    void setSize (int width, int height);
    void notifyPropertyChanged (const std::string& key);

protected:
    void renderFrame (const glm::ivec4& viewport) override;
    bool syncFrameReader ();
    /** allocate the wallpaper texture for the current size; the ONLY glTexImage2D here */
    void allocateTexture ();
    void updateMouse (const glm::ivec4& viewport);
    /** LWE_MOUSEDBG=1, read once; the mouse-trail instrument in updateMouse */
    static bool mouseDebugEnabled ();
    void injectProperties ();
    void injectAudio ();
    const Web& getWeb () const { return *this->getWallpaperData ().as<Web> (); }

    friend class CWallpaper;

private:
    WallpaperEngine::WebHelper::HelperClient& m_helper;
    /** identifies this CWeb to the helper; allocated once, released by destroy */
    WallpaperEngine::WebHelper::InstanceId m_instanceId = 0;

    int m_width = 16;
    int m_height = 16;

    WallpaperEngine::Input::MouseClickStatus m_leftClick = Input::Released;
    WallpaperEngine::Input::MouseClickStatus m_rightClick = Input::Released;

    WallpaperEngine::Input::PointerMoveGate m_moveGate;

    std::chrono::steady_clock::time_point m_lastMouseDebug = {};
    bool m_propertiesInjected = false;
    int m_audioFrameCount = 0;

    /** read side of the shm ring; re-opened whenever the helper publishes a new generation */
    WallpaperEngine::WebHelper::FrameReader m_frames;
    /** which generation m_frames is mapped to; 0 = nothing mapped */
    uint32_t m_frameGeneration = 0;
    /** size the GL texture was last allocated for, so uploads can be sub-image only */
    int m_textureWidth = 0;
    int m_textureHeight = 0;
};
}
