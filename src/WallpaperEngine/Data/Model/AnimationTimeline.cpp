#include "AnimationTimeline.h"

#include <algorithm>
#include <cmath>

using namespace WallpaperEngine::Data::Model;

namespace {
float bezierEase (const float t, const float x1, const float x2) {
    const auto bx = [x1, x2] (const float s) {
	const float inv = 1.0f - s;
	return 3.0f * inv * inv * s * x1 + 3.0f * inv * s * s * x2 + s * s * s;
    };
    float s = t;
    for (int i = 0; i < 6; i++) {
	const float inv = 1.0f - s;
	const float dx = 3.0f * inv * inv * x1 + 6.0f * inv * s * (x2 - x1) + 3.0f * s * s * (1.0f - x2);
	if (std::abs (dx) < 1e-6f) {
	    break;
	}
	s -= (bx (s) - t) / dx;
	s = std::clamp (s, 0.0f, 1.0f);
    }
    const float inv = 1.0f - s;
    return 3.0f * inv * s * s + s * s * s; // bezierY with y1 = 0, y2 = 1
}

float evaluateChannel (const std::vector<AnimationKeyframe>& keyframes, const float currentFrame) {
    if (keyframes.empty ()) {
	return 0.0f;
    }
    if (keyframes.size () == 1 || currentFrame <= keyframes.front ().frame) {
	return keyframes.front ().value;
    }
    if (currentFrame >= keyframes.back ().frame) {
	return keyframes.back ().value;
    }

    for (size_t i = 0; i + 1 < keyframes.size (); i++) {
	const auto& k0 = keyframes[i];
	const auto& k1 = keyframes[i + 1];
	if (currentFrame < k0.frame || currentFrame > k1.frame) {
	    continue;
	}
	const float span = std::max (k1.frame - k0.frame, 1e-6f);
	const float t = (currentFrame - k0.frame) / span;
	const float frontX = k0.front.enabled ? k0.front.x : 1.0f;
	const float backX = k1.back.enabled ? k1.back.x : -1.0f;
	const float x1 = std::clamp (frontX * 0.5f, 0.0f, 1.0f);
	const float x2 = std::clamp (1.0f + backX * 0.5f, 0.0f, 1.0f);
	return k0.value + (k1.value - k0.value) * bezierEase (t, x1, x2);
    }

    return keyframes.back ().value;
}
} // namespace

glm::vec4 AnimationTimeline::evaluate (const double elapsedSeconds) const {
    const float currentFrame = static_cast<float> (elapsedSeconds) * this->fps;
    glm::vec4 result (0.0f);

    for (size_t i = 0; i < this->channels.size () && i < 4; ++i) {
	result[static_cast<glm::vec4::length_type> (i)] = evaluateChannel (this->channels[i], currentFrame);
    }

    if (this->relative) {
	result += this->baseValue;
    }

    return result;
}
