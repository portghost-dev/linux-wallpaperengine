#include "PointerMoveGate.h"

#include <algorithm>

using namespace WallpaperEngine::Input;

PointerMoveGate::Decision
PointerMoveGate::update (const std::optional<glm::dvec2>& normalized, const int width, const int height) {
    // No pointer, or nothing to scale it against. Send NOTHING and, crucially, leave
    // m_lastSent alone: the unknown sample must not become the value the next known one is
    // compared with, or the first real move after an unknown stretch could be suppressed.
    if (!normalized.has_value () || width <= 0 || height <= 0) {
	this->m_movesUnknown++;

	return {};
    }

    const int x = std::clamp (static_cast<int> (normalized->x * width), 0, width);
    const int y = std::clamp (static_cast<int> (normalized->y * height), 0, height);
    const glm::ivec2 resolved (x, y);

    // The first known sample always goes out: an optional rather than a default-constructed
    // vector, so a pointer genuinely resting at (0,0) is not mistaken for "already sent".
    if (this->m_lastSent.has_value () && *this->m_lastSent == resolved) {
	this->m_movesSuppressed++;

	return { .send = false, .x = x, .y = y };
    }

    this->m_lastSent = resolved;
    this->m_movesSent++;

    return { .send = true, .x = x, .y = y };
}

void PointerMoveGate::resetCounters () {
    this->m_movesSent = 0;
    this->m_movesSuppressed = 0;
    this->m_movesUnknown = 0;
}
