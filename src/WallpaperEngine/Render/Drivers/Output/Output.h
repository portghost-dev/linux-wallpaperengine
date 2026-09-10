#pragma once

#include <glm/vec2.hpp>
#include <glm/vec4.hpp>
#include <map>
#include <string>

#include "WallpaperEngine/Application/ApplicationContext.h"
#include "WallpaperEngine/Render/Drivers/Detectors/FullScreenDetector.h"

using namespace WallpaperEngine::Application;

namespace WallpaperEngine::Application {
class ApplicationContext;
}

namespace WallpaperEngine::Render::Drivers {
class VideoDriver;

namespace Detectors {
    class FullScreenDetector;
}

namespace Output {
    class OutputViewport;

    class Output {
    public:
	Output (ApplicationContext& context, VideoDriver& driver);
	virtual ~Output () = default;

	virtual void reset () = 0;

	int getFullWidth () const;
	int getFullHeight () const;

	virtual bool renderVFlip () const = 0;
	virtual bool renderMultiple () const = 0;
	virtual bool haveImageBuffer () const = 0;
	const std::map<std::string, OutputViewport*>& getViewports () const;
	// the largest screen the wallpaper plays on: the largest viewport here, the largest monitor
	// for a window, so a bench window clamps the way the desktop does
	[[nodiscard]] virtual glm::ivec2 largestScreenSize () const;
	virtual void* getImageBuffer () const = 0;
	virtual uint32_t getImageBufferSize () const = 0;
	virtual void updateRender () const = 0;

    protected:
	mutable int m_fullWidth = 0;
	mutable int m_fullHeight = 0;
	mutable std::map<std::string, OutputViewport*> m_viewports = {};
	ApplicationContext& m_context;
	VideoDriver& m_driver;
    };
} // namespace Output
} // namespace WallpaperEngine::Render::Drivers