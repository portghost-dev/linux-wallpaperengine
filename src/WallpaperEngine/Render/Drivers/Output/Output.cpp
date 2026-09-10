#include "Output.h"
#include "OutputViewport.h"

#include <algorithm>

using namespace WallpaperEngine::Render::Drivers::Output;

Output::Output (ApplicationContext& context, VideoDriver& driver) : m_context (context), m_driver (driver) { }

const std::map<std::string, OutputViewport*>& Output::getViewports () const { return this->m_viewports; }

int Output::getFullWidth () const { return this->m_fullWidth; }

int Output::getFullHeight () const { return this->m_fullHeight; }

glm::ivec2 Output::largestScreenSize () const {
    glm::ivec2 best { 0, 0 };
    for (const auto& [name, viewport] : this->m_viewports) {
	best.x = std::max (best.x, viewport->viewport.z);
	best.y = std::max (best.y, viewport->viewport.w);
    }
    return best;
}
