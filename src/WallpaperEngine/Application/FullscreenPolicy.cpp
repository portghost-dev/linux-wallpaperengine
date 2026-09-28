#include "FullscreenPolicy.h"

namespace WallpaperEngine::Application::FullscreenPolicy {
FullscreenBehavior inEffect (const FullscreenBehavior behavior, const bool canReleaseOutputs) {
    if (behavior == FullscreenBehavior::Stop && !canReleaseOutputs) {
	return FullscreenBehavior::Pause;
    }

    return behavior;
}

bool wantsRealDetector (const decltype (ApplicationContext::settings)& settings) {
    return settings.render.pauseOnFullscreen || settings.general.apiSocket;
}
} // namespace WallpaperEngine::Application::FullscreenPolicy
