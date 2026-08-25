#include "NullAudioDriver.h"

namespace WallpaperEngine::Audio::Drivers {
NullAudioDriver::NullAudioDriver (
    Application::ApplicationContext& applicationContext, Detectors::AudioPlayingDetector& detector,
    Recorders::PlaybackRecorder& recorder
) : AudioDriver (applicationContext, detector, recorder) { }

int NullAudioDriver::addStream (AudioStream*) { return -1; }

void NullAudioDriver::removeStream (int) { }

AVSampleFormat NullAudioDriver::getFormat () const { return AV_SAMPLE_FMT_FLT; }

int NullAudioDriver::getSampleRate () const { return 48000; }

int NullAudioDriver::getChannels () const { return 2; }
} // namespace WallpaperEngine::Audio::Drivers
