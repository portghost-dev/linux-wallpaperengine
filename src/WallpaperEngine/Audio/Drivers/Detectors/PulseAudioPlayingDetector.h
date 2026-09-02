#pragma once

#include "AudioPlayingDetector.h"
#include <chrono>
#include <pulse/pulseaudio.h>

namespace WallpaperEngine::Audio::Drivers::Detectors {
class PulseAudioPlayingDetector final : public AudioPlayingDetector {
public:
    explicit PulseAudioPlayingDetector (
	Application::ApplicationContext& appContext, const Render::Drivers::Detectors::FullScreenDetector&
    );
    ~PulseAudioPlayingDetector () override;

    void update () override;

private:
    static void contextStateCallback (pa_context* context, void* userdata);
    static void sinkInputInfoCallback (pa_context* context, const pa_sink_input_info* info, int eol, void* userdata);

    void connectContext ();
    void releaseContext ();
    void maintainConnection (std::chrono::steady_clock::time_point now);

    pa_mainloop* m_mainloop = nullptr;
    pa_mainloop_api* m_mainloopApi = nullptr;
    pa_context* m_context = nullptr;
    bool m_contextLost = false;
    bool m_queryInFlight = false;
    bool m_queryPlaying = false;
    std::chrono::steady_clock::time_point m_connectStarted = {};
    std::chrono::steady_clock::time_point m_nextRetry = {};
    std::chrono::milliseconds m_retryDelay { 250 };
};
} // namespace WallpaperEngine::Audio::Drivers::Detectors
