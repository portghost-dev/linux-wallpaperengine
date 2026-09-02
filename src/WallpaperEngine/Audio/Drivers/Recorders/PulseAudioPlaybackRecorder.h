#pragma once

#include "PlaybackRecorder.h"
#include "kiss_fftr.h"
#include <chrono>
#include <pulse/pulseaudio.h>
#include <string>

#define WAVE_BUFFER_SIZE 1024

namespace WallpaperEngine::Audio::Drivers::Recorders {
class PlaybackRecorder;

class PulseAudioPlaybackRecorder final : public PlaybackRecorder {
public:
    /**
     * Struct that contains all the required data for the PulseAudio callbacks
     */
    struct PulseAudioData {
	kiss_fftr_cfg kisscfg;
	uint8_t* audioBuffer;
	uint8_t* audioBufferTmp;
	size_t currentWritePointer;
	bool fullFrameReady;
	pa_stream* captureStream;
	std::string currentSink;
	bool streamFailed = false;
	bool contextLost = false;
    };

    PulseAudioPlaybackRecorder ();
    ~PulseAudioPlaybackRecorder () override;

    void update () override;

private:
    void releaseContext ();
    void maintainConnection (std::chrono::steady_clock::time_point now);

    pa_mainloop* m_mainloop;
    pa_mainloop_api* m_mainloopApi;
    pa_context* m_context;
    PulseAudioData m_captureData;

    std::chrono::steady_clock::time_point m_lastFrame = {};
    std::chrono::steady_clock::time_point m_nextRetry = {};
    std::chrono::milliseconds m_retryDelay { 250 };
    bool m_starved = false;

    float m_audioFFTbuffer[WAVE_BUFFER_SIZE] = { 0.0f };
    kiss_fft_cpx m_FFTinfo[WAVE_BUFFER_SIZE / 2 + 1] = { { .r = 0.0f, .i = 0.0f } };
    float m_FFTdestination64[64] = { 0 };
    float m_FFTdestination32[32] = { 0 };
    float m_FFTdestination16[16] = { 0 };
};
} // namespace WallpaperEngine::Audio::Drivers::Recorders
