#pragma once

#include "WallpaperEngine/Media/MediaSource.h"

namespace WallpaperEngine::WebHelper::Service {
class NullMediaSource final : public WallpaperEngine::Media::MediaSource {
public:
    NullMediaSource ();

    /** never polls; the base class timer would have nothing to ask */
    void update () override { }

protected:
    void performUpdate () override { }
};
}
