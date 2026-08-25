#pragma once

#include "AudioDriver.h"

namespace WallpaperEngine::Audio::Drivers {
class NullAudioDriver final : public AudioDriver {
public:
    NullAudioDriver (
	Application::ApplicationContext& applicationContext, Detectors::AudioPlayingDetector& detector,
	Recorders::PlaybackRecorder& recorder
    );

    /** @inheritdoc
     *
     * Returns a fixed invalid id. Unreachable in practice: CSound::load() only runs when
     * settings.audio.enabled is set, and that plus the presence of a sound object is exactly
     * the condition under which a real driver is constructed (or lazily upgraded to, via
     * WallpaperApplication::ensureAudioForProject) before any scene is built.
     */
    int addStream (AudioStream* stream) override;
    /** @inheritdoc */
    void removeStream (int streamId) override;

    /** @inheritdoc */
    [[nodiscard]] AVSampleFormat getFormat () const override;
    /** @inheritdoc */
    [[nodiscard]] int getSampleRate () const override;
    /** @inheritdoc */
    [[nodiscard]] int getChannels () const override;
};
} // namespace WallpaperEngine::Audio::Drivers
