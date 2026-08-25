#pragma once

#include <cstdint>
#include <glm/vec2.hpp>
#include <optional>

namespace WallpaperEngine::Input {
/**
 * Per-consumer pointer state: what was last sent, and the counters LWE_MOUSEDBG prints.
 * One of these per CWeb, never per screen.
 */
class PointerMoveGate {
public:
    struct Decision {
	bool send = false;
	int x = 0;
	int y = 0;
    };

    Decision update (const std::optional<glm::dvec2>& normalized, int width, int height);

    /**
     * The last position actually sent, if any. Clicks are edge-triggered on BUTTON state,
     * not position, so a click can arrive while the pointer is unknown; it should be
     * reported where the pointer was last known to be rather than at a made-up center.
     */
    [[nodiscard]] const std::optional<glm::ivec2>& lastSent () const { return this->m_lastSent; }

    [[nodiscard]] uint64_t movesSent () const { return this->m_movesSent; }
    [[nodiscard]] uint64_t movesSuppressed () const { return this->m_movesSuppressed; }
    [[nodiscard]] uint64_t movesUnknown () const { return this->m_movesUnknown; }

    void resetCounters ();

private:
    std::optional<glm::ivec2> m_lastSent;
    uint64_t m_movesSent = 0;
    uint64_t m_movesSuppressed = 0;
    uint64_t m_movesUnknown = 0;
};
} // namespace WallpaperEngine::Input
