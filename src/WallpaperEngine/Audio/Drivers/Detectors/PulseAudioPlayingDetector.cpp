#include "PulseAudioPlayingDetector.h"
#include "WallpaperEngine/Logging/Log.h"

#include <algorithm>
#include <cstdlib>
#include <unistd.h>

namespace WallpaperEngine::Audio::Drivers::Detectors {
constexpr auto CONNECT_TIMEOUT = std::chrono::milliseconds (2000);
constexpr auto RETRY_DELAY_MAX = std::chrono::milliseconds (30000);

void PulseAudioPlayingDetector::sinkInputInfoCallback (
    pa_context* context, const pa_sink_input_info* info, const int eol, void* userdata
) {
    auto* detector = static_cast<PulseAudioPlayingDetector*> (userdata);

    // the list ends with an empty entry, or with an error. both complete the query
    if (eol != 0 || info == nullptr) {
	detector->setIsPlaying (detector->m_queryPlaying);
	detector->m_queryInFlight = false;
	return;
    }

    if (info->proplist == nullptr) {
	return;
    }

    const char* value = pa_proplist_gets (info->proplist, PA_PROP_APPLICATION_PROCESS_ID);

    if (value && strtol (value, nullptr, 10) != getpid () && pa_cvolume_avg (&info->volume) != PA_VOLUME_MUTED) {
	detector->m_queryPlaying = true;
    }
}

void PulseAudioPlayingDetector::contextStateCallback (pa_context* context, void* userdata) {
    auto* detector = static_cast<PulseAudioPlayingDetector*> (userdata);

    switch (pa_context_get_state (context)) {
	case PA_CONTEXT_READY:
	    detector->m_contextLost = false;
	    break;
	case PA_CONTEXT_TERMINATED:
	case PA_CONTEXT_FAILED:
	    sLog.error ("PulseAudio context lost. Audio detection will reconnect");
	    detector->m_contextLost = true;
	    detector->m_queryInFlight = false;
	    break;
	default:
	    break;
    }
}

PulseAudioPlayingDetector::PulseAudioPlayingDetector (
    Application::ApplicationContext& appContext,
    const Render::Drivers::Detectors::FullScreenDetector& fullscreenDetector
) : AudioPlayingDetector (appContext, fullscreenDetector) {
    this->m_mainloop = pa_mainloop_new ();
    this->m_mainloopApi = pa_mainloop_get_api (this->m_mainloop);
    this->connectContext ();

    // wait for the server, but never forever. update () keeps trying after this
    const auto deadline = std::chrono::steady_clock::now () + CONNECT_TIMEOUT;

    while (!this->m_contextLost && pa_context_get_state (this->m_context) != PA_CONTEXT_READY) {
	const auto remaining = deadline - std::chrono::steady_clock::now ();

	if (remaining <= std::chrono::steady_clock::duration::zero ()) {
	    sLog.error ("PulseAudio did not answer. Audio detection will keep trying");
	    break;
	}

	const auto timeout = std::chrono::duration_cast<std::chrono::microseconds> (remaining).count ();

	if (pa_mainloop_prepare (this->m_mainloop, timeout) < 0 || pa_mainloop_poll (this->m_mainloop) < 0
	    || pa_mainloop_dispatch (this->m_mainloop) < 0) {
	    break;
	}
    }
}

PulseAudioPlayingDetector::~PulseAudioPlayingDetector () {
    this->releaseContext ();

    if (this->m_mainloop) {
	pa_mainloop_free (this->m_mainloop);
    }
}

void PulseAudioPlayingDetector::connectContext () {
    this->m_connectStarted = std::chrono::steady_clock::now ();
    this->m_context = pa_context_new (this->m_mainloopApi, "wallpaperengine");

    if (this->m_context == nullptr) {
	this->m_contextLost = true;
	return;
    }

    pa_context_set_state_callback (this->m_context, &contextStateCallback, this);

    if (pa_context_connect (this->m_context, nullptr, PA_CONTEXT_NOFLAGS, nullptr) < 0) {
	sLog.error ("PulseAudio connection failed. Audio detection will retry");
	this->m_contextLost = true;
	return;
    }

    this->m_contextLost = false;
}

void PulseAudioPlayingDetector::releaseContext () {
    this->m_queryInFlight = false;

    if (this->m_context == nullptr) {
	return;
    }

    pa_context_set_state_callback (this->m_context, nullptr, nullptr);
    pa_context_disconnect (this->m_context);
    pa_context_unref (this->m_context);
    this->m_context = nullptr;
}

void PulseAudioPlayingDetector::maintainConnection (const std::chrono::steady_clock::time_point now) {
    // a connection that never reaches ready is treated the same as a lost one
    if (!this->m_contextLost && this->m_context != nullptr && pa_context_get_state (this->m_context) != PA_CONTEXT_READY
	&& now - this->m_connectStarted > CONNECT_TIMEOUT) {
	sLog.error ("PulseAudio did not answer. Audio detection will reconnect");
	this->m_contextLost = true;
    }

    if (!this->m_contextLost) {
	this->m_retryDelay = std::chrono::milliseconds (250);
	return;
    }

    if (now < this->m_nextRetry) {
	return;
    }

    this->m_nextRetry = now + this->m_retryDelay;
    this->m_retryDelay = std::min (this->m_retryDelay * 2, RETRY_DELAY_MAX);

    this->releaseContext ();
    this->connectContext ();
}

void PulseAudioPlayingDetector::update () {
    // service the connection first so nothing queues up while detection is idle
    while (pa_mainloop_iterate (this->m_mainloop, 0, nullptr) > 0) { }

    this->maintainConnection (std::chrono::steady_clock::now ());

    if (!this->getApplicationContext ().settings.audio.automute) {
	return this->setIsPlaying (false);
    }
    if (this->getFullscreenDetector ().anythingFullscreen ()) {
	return this->setIsPlaying (true);
    }

    // without a server there is nothing else that can be playing
    if (this->m_contextLost || this->m_context == nullptr
	|| pa_context_get_state (this->m_context) != PA_CONTEXT_READY) {
	return this->setIsPlaying (false);
    }

    // the answer arrives through the callback on a later update, nothing waits here
    if (this->m_queryInFlight) {
	return;
    }

    pa_operation* op = pa_context_get_sink_input_info_list (this->m_context, &sinkInputInfoCallback, this);

    if (op == nullptr) {
	return;
    }

    this->m_queryInFlight = true;
    this->m_queryPlaying = false;
    pa_operation_unref (op);
}
} // namespace WallpaperEngine::Audio::Drivers::Detectors
