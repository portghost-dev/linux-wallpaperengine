#pragma once

#include "WallpaperEngine/WebBrowser/CEF/BrowserClient.h"
#include "WallpaperEngine/WebBrowser/CEF/RenderHandler.h"
#include "WallpaperEngine/WebHelper/FrameContract.h"
#include "WallpaperEngine/WebHelper/Protocol.h"

#include "include/cef_browser.h"

#include <string>
#include <vector>

namespace WallpaperEngine::WebHelper::Service {
class WebInstance {
public:
    WebInstance (InstanceId id, std::string workshopId, std::string file, int width, int height, int framerate);
    ~WebInstance ();

    WebInstance (const WebInstance&) = delete;
    WebInstance& operator= (const WebInstance&) = delete;

    [[nodiscard]] InstanceId id () const { return this->m_id; }
    [[nodiscard]] int getWidth () const { return this->m_width; }
    [[nodiscard]] int getHeight () const { return this->m_height; }

    /** create the CEF browser; separate from the constructor so a failure is reportable */
    bool open ();

    void setSize (int width, int height);
    void mouseMove (int x, int y);
    void mouseClick (int x, int y, MouseButton button, bool released);
    void injectProperties (const std::vector<PropertyValue>& properties);
    /** live single-key update; TYPED, for the reason in Protocol.h's encodeSetProperty */
    void setProperty (const PropertyValue& property);
    /** `bands` points at AUDIO_BANDS floats; mirrored into the page's 128-element array */
    void injectAudio (const float* bands);

    /** CEF's paint callback lands here and becomes a seqlock publish into the shm ring */
    void onPaint (const void* buffer, int width, int height);

    /** which shm allocation is live; the engine maps the object named after it */
    [[nodiscard]] uint32_t frameGeneration () const { return this->m_frames.generation (); }
    /** frames published into the live generation; 0 = the engine has nothing to map yet */
    [[nodiscard]] uint32_t frameSequence () const { return this->m_frames.sequence (); }
    [[nodiscard]] uint64_t droppedFrames () const { return this->m_frames.droppedFrames (); }

    /**
     * Has a generation been published that the engine has not been told about? The server
     * polls this and emits frame-ready once per generation, which is the whole reason that
     * event exists (Protocol.h).
     */
    [[nodiscard]] bool hasUnannouncedGeneration () const {
	return this->m_frames.sequence () > 0 && this->m_frames.generation () != this->m_announcedGeneration;
    }
    void markGenerationAnnounced () { this->m_announcedGeneration = this->m_frames.generation (); }

    /**
     * Has the main frame finished loading? Polled by the server so the page-loaded event
     * is emitted exactly once, rather than having BrowserClient reach back into the
     * socket from CEF's UI thread.
     */
    [[nodiscard]] bool isPageLoaded () const;
    [[nodiscard]] bool pageLoadedEventSent () const { return this->m_pageLoadedSent; }
    void markPageLoadedEventSent () { this->m_pageLoadedSent = true; }

    [[nodiscard]] bool didLoadFail () const;
    [[nodiscard]] int loadErrorCode () const;
    [[nodiscard]] std::string loadErrorText () const;
    [[nodiscard]] std::string failedUrl () const;
    [[nodiscard]] bool pageFailedEventSent () const { return this->m_pageFailedSent; }
    void markPageFailedEventSent () { this->m_pageFailedSent = true; }

    void close ();

private:
    /** run JS in the main frame, or do nothing if there is no frame yet */
    void execute (const std::string& script);

    /** one typed property as a JS literal; the ONE place a kind becomes syntax */
    static std::string renderPropertyValue (const PropertyValue& property);
    static std::string renderApplyUserProperties (const std::vector<PropertyValue>& properties);

    InstanceId m_id;
    std::string m_workshopId;
    std::string m_file;
    int m_width;
    int m_height;
    int m_framerate;

    CefRefPtr<CefBrowser> m_browser = nullptr;
    CefRefPtr<WallpaperEngine::WebBrowser::CEF::BrowserClient> m_client = nullptr;
    WallpaperEngine::WebBrowser::CEF::RenderHandler* m_renderHandler = nullptr;
    bool m_pageLoadedSent = false;
    bool m_pageFailedSent = false;

    /** this instance's shared-memory ring; the engine maps the matching FrameReader */
    FrameWriter m_frames;
    uint32_t m_announcedGeneration = 0;
};
}
