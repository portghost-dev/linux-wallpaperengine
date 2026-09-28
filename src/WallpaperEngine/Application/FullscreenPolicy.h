#pragma once

#include "WallpaperEngine/Application/ApplicationContext.h"

namespace WallpaperEngine::Application::FullscreenPolicy {
FullscreenBehavior inEffect (FullscreenBehavior behavior, bool canReleaseOutputs);
bool wantsRealDetector (const decltype (ApplicationContext::settings)& settings);
} // namespace WallpaperEngine::Application::FullscreenPolicy
