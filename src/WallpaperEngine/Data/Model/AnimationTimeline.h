#pragma once

#include <glm/glm.hpp>
#include <vector>

namespace WallpaperEngine::Data::Model {

struct AnimationTangent {
    bool enabled = false;
    float x = 0.0f;
    float y = 0.0f;
};

struct AnimationKeyframe {
    float frame = 0.0f;
    float value = 0.0f;
    AnimationTangent back;
    AnimationTangent front;
};

enum class AnimationMode {
    Unknown,
    Single,
    Loop,
    Mirror,
};

struct AnimationTimeline {
    std::vector<std::vector<AnimationKeyframe>> channels;
    AnimationMode mode = AnimationMode::Unknown;
    float fps = 30.0f;
    float length = 0.0f;
    bool relative = false;
    glm::vec4 baseValue = {};

    [[nodiscard]] glm::vec4 evaluate (double elapsedSeconds) const;

    /** Total timeline duration in seconds (length is in frames). */
    [[nodiscard]] double durationSeconds () const {
	return this->fps > 0.0f ? static_cast<double> (this->length) / static_cast<double> (this->fps) : 0.0;
    }
};

} // namespace WallpaperEngine::Data::Model
