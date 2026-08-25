#pragma once

#include "WallpaperEngine/Application/ApplicationContext.h"

namespace WallpaperEngine::Render::Drivers::Detectors {
class FullScreenDetector {
public:
    explicit FullScreenDetector (Application::ApplicationContext& appContext);
    virtual ~FullScreenDetector () = default;

    /**
     * @return If anything is fullscreen
     */
    [[nodiscard]] virtual bool anythingFullscreen () const;
    /**
     * Restarts the fullscreen detector, specially useful if there's any resources tied to the output driver
     */
    virtual void reset ();
    /**
     * Re-evaluates which known toplevels count as fullscreen RIGHT NOW.
     *
     * Relevance is normally decided when a toplevel event arrives, so the verdict is
     * cached against whatever the settings said at that moment. Anything that changes
     * the relevance rules at runtime - the fullscreen ignore-list, live-set through
     * `set-fullscreen-ignore` - must call this, or the stale verdict stands until the
     * window itself happens to send another state event.
     */
    virtual void recomputeRelevance ();
    /**
     * @return The application context using this detector
     */
    [[nodiscard]] Application::ApplicationContext& getApplicationContext () const;

private:
    Application::ApplicationContext& m_applicationContext;
};
} // namespace WallpaperEngine::Render::Drivers::Detectors