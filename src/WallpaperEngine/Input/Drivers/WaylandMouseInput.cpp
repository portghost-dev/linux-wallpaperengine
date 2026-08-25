#include "WaylandMouseInput.h"
#include "WallpaperEngine/Render/Drivers/WaylandOpenGLDriver.h"
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <glm/common.hpp>
#include <regex>
#include <string>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>

using namespace WallpaperEngine::Input::Drivers;

WaylandMouseInput::WaylandMouseInput (const WallpaperEngine::Render::Drivers::WaylandOpenGLDriver& driver) :
    m_waylandDriver (driver) { }

void WaylandMouseInput::update () {
    if (!this->m_waylandDriver.getApp ().getContext ().settings.mouse.enabled) {
	this->m_pos = { 0, 0 };
	return;
    }

    if (m_waylandDriver.viewportInFocus && m_waylandDriver.viewportInFocus->rendering) {
	// GLOBAL layout coords: CScene::updateMouse subtracts the rendered
	// output's layout origin, so surface-local positions broke every
	// non-origin monitor (the multi-monitor "mouse never worked" bug)
	const auto* vp = m_waylandDriver.viewportInFocus;
	this->m_pos = glm::dvec2 (
	    vp->mousePos.x + vp->globalPosition.x * vp->scale, vp->mousePos.y + vp->globalPosition.y * vp->scale
	);
	return;
    }

    const auto now = std::chrono::steady_clock::now ();
    if (now - this->m_lastHyprlandQuery < std::chrono::milliseconds (16)) {
	return;
    }
    this->m_lastHyprlandQuery = now;

    const auto globalCursor = this->queryHyprlandCursorPosition ();
    if (!globalCursor.has_value ()) {
	this->m_pos = { 0, 0 };
	return;
    }

    for (const auto* viewport : this->m_waylandDriver.m_screens) {
	if (!viewport || viewport->size.x <= 0 || viewport->size.y <= 0) {
	    continue;
	}

	const double localX = globalCursor->x - viewport->globalPosition.x;
	const double localY = globalCursor->y - viewport->globalPosition.y;
	if (localX < 0.0 || localY < 0.0 || localX > viewport->size.x || localY > viewport->size.y) {
	    continue;
	}

	this->m_pos = { (localX + viewport->globalPosition.x) * viewport->scale,
			(localY + viewport->globalPosition.y) * viewport->scale };
	return;
    }

    this->m_pos = { 0, 0 };
}

glm::dvec2 WaylandMouseInput::position () const {
    const auto* viewport = this->getActiveOutputViewport ();

    if (!viewport) {
	return { 0, 0 };
    }

    const glm::dvec2 origin
	= { viewport->globalPosition.x * viewport->scale, viewport->globalPosition.y * viewport->scale };
    const glm::dvec2 center = {
	origin.x + static_cast<double> (viewport->size.x * viewport->scale) / 2.0,
	origin.y + static_cast<double> (viewport->size.y * viewport->scale) / 2.0,
    };

    if (!this->m_waylandDriver.getApp ().getContext ().settings.mouse.enabled) {
	return center;
    }

    if (viewport == m_waylandDriver.viewportInFocus) {
	return { viewport->mousePos.x + origin.x, viewport->mousePos.y + origin.y };
    }

    if (this->m_pos.x != 0 || this->m_pos.y != 0) {
	return this->m_pos;
    }

    // No IPC data (non-Hyprland): freeze at the last hover position, Windows-like
    if (viewport->mousePos.x != 0 || viewport->mousePos.y != 0) {
	return { viewport->mousePos.x + origin.x, viewport->mousePos.y + origin.y };
    }

    return center;
}

glm::dvec2 WaylandMouseInput::normalized () const {
    return this->resolveNormalized ().value_or (glm::dvec2 { 0.5, 0.5 });
}

bool WaylandMouseInput::hasPointer () const { return this->resolveNormalized ().has_value (); }

std::optional<glm::dvec2> WaylandMouseInput::resolveNormalized () const {
    // the mouse being off is an UNKNOWN pointer, not a centered one: nothing should be
    // pushed to a page on behalf of a pointer the user has switched off
    if (!this->m_waylandDriver.getApp ().getContext ().settings.mouse.enabled) {
	return std::nullopt;
    }

    if (m_waylandDriver.viewportInFocus && m_waylandDriver.viewportInFocus->rendering) {
	const auto* vp = m_waylandDriver.viewportInFocus;
	const double w = static_cast<double> (vp->size.x) * vp->scale;
	const double h = static_cast<double> (vp->size.y) * vp->scale;
	if (w > 0 && h > 0) {
	    return glm::dvec2 { glm::clamp (vp->mousePos.x / w, 0.0, 1.0), glm::clamp (vp->mousePos.y / h, 0.0, 1.0) };
	}
    }

    // IPC-tracked global position (update() keeps m_pos in scaled layout coords)
    if (this->m_pos.x != 0 || this->m_pos.y != 0) {
	for (const auto* vp : this->m_waylandDriver.m_screens) {
	    if (!vp || vp->size.x <= 0 || vp->size.y <= 0) {
		continue;
	    }
	    const double ox = vp->globalPosition.x * vp->scale;
	    const double oy = vp->globalPosition.y * vp->scale;
	    const double w = static_cast<double> (vp->size.x) * vp->scale;
	    const double h = static_cast<double> (vp->size.y) * vp->scale;
	    const double lx = this->m_pos.x - ox;
	    const double ly = this->m_pos.y - oy;
	    if (lx >= 0 && ly >= 0 && lx <= w && ly <= h) {
		return glm::dvec2 { glm::clamp (lx / w, 0.0, 1.0), glm::clamp (ly / h, 0.0, 1.0) };
	    }
	}
    }

    return std::nullopt;
}

WallpaperEngine::Input::MouseClickStatus WaylandMouseInput::leftClick () const {
    if (this->m_waylandDriver.viewportInFocus) {
	return this->m_waylandDriver.viewportInFocus->leftClick;
    }

    return MouseClickStatus::Released;
}

const WallpaperEngine::Render::Drivers::Output::WaylandOutputViewport*
WaylandMouseInput::getActiveOutputViewport () const {
    if (this->m_waylandDriver.viewportInFocus && this->m_waylandDriver.viewportInFocus->rendering) {
	return this->m_waylandDriver.viewportInFocus;
    }

    for (const auto* viewport : this->m_waylandDriver.m_screens) {
	if (viewport && viewport->rendering) {
	    return viewport;
	}
    }

    return nullptr;
}

std::optional<glm::dvec2> WaylandMouseInput::queryHyprlandCursorPosition () const {
    const char* signature = std::getenv ("HYPRLAND_INSTANCE_SIGNATURE");
    const char* runtime = std::getenv ("XDG_RUNTIME_DIR");
    if (!signature || !runtime) {
	return std::nullopt;
    }

    const std::string socketPath = std::string (runtime) + "/hypr/" + signature + "/.socket.sock";

    int fd = socket (AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) {
	return std::nullopt;
    }

    timeval timeout {};
    timeout.tv_usec = 50000;
    if (setsockopt (fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof (timeout)) != 0
	|| setsockopt (fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof (timeout)) != 0) {
	close (fd);
	return std::nullopt;
    }

    sockaddr_un addr {};
    addr.sun_family = AF_UNIX;
    if (socketPath.size () >= sizeof (addr.sun_path)) {
	close (fd);
	return std::nullopt;
    }
    std::strncpy (addr.sun_path, socketPath.c_str (), sizeof (addr.sun_path) - 1);

    if (connect (fd, reinterpret_cast<sockaddr*> (&addr), sizeof (addr)) != 0) {
	close (fd);
	return std::nullopt;
    }

    constexpr const char* request = "j/cursorpos";
    if (send (fd, request, std::strlen (request), MSG_NOSIGNAL) < 0) {
	close (fd);
	return std::nullopt;
    }
    shutdown (fd, SHUT_WR);

    std::string response;
    char buffer[256];
    ssize_t readBytes = 0;
    while ((readBytes = recv (fd, buffer, sizeof (buffer), 0)) > 0) {
	response.append (buffer, static_cast<std::size_t> (readBytes));
    }
    close (fd);

    static const std::regex xRegex (R"("x"\s*:\s*(-?\d+(?:\.\d+)?))");
    static const std::regex yRegex (R"("y"\s*:\s*(-?\d+(?:\.\d+)?))");
    std::smatch xMatch;
    std::smatch yMatch;
    if (!std::regex_search (response, xMatch, xRegex) || !std::regex_search (response, yMatch, yRegex)) {
	return std::nullopt;
    }

    try {
	return glm::dvec2 { std::stod (xMatch[1].str ()), std::stod (yMatch[1].str ()) };
    } catch (const std::exception&) {
	return std::nullopt;
    }
}

WallpaperEngine::Input::MouseClickStatus WaylandMouseInput::rightClick () const {
    if (this->m_waylandDriver.viewportInFocus) {
	return this->m_waylandDriver.viewportInFocus->rightClick;
    }

    return MouseClickStatus::Released;
}
