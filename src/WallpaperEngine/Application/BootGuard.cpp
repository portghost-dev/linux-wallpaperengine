#include "BootGuard.h"

#include "WallpaperEngine/Logging/Log.h"

namespace WallpaperEngine::Application {
bool BootGuard::evaluate (const nlohmann::json& history) {
    const size_t n = history.size ();
    this->m_restoreRefused = n >= 2 && history[n - 1].is_object () && history[n - 2].is_object ()
	&& !history[n - 1].value ("survived", true) && !history[n - 2].value ("survived", true);
    return this->m_restoreRefused;
}

void BootGuard::wallpaperShown () { this->m_restoreRefused = false; }

bool BootGuard::restoreRefused () const { return this->m_restoreRefused; }

bool BootGuard::holdsShow (const std::string& cmd, const nlohmann::json& args) {
    if (!this->m_restoreRefused || (cmd != "show" && cmd != "next" && cmd != "prev") || !args.contains ("automatic")
	|| args["automatic"] != true) {
	return false;
    }

    sLog.out ("Held an automatic show: the restore was refused after two quick crashes.");
    return true;
}

bool BootGuard::rotationMayAdvance (
    const bool live, const Api::Lane& lane, const Api::Playlist& playlist, const Api::Clock::time_point now
) {
    return live && Api::dueForAdvance (lane, playlist, now) && !this->holds ();
}

bool BootGuard::playlistTimerMayAdvance (const Api::Clock::time_point nextSwitch, const Api::Clock::time_point now) {
    return now >= nextSwitch && !this->holds ();
}

bool BootGuard::scheduleMayApply (
    const bool live, const Api::Schedule& schedule, const std::string& missing, const Api::Playlist& bound
) {
    return !schedule.pending.empty () && schedule.pending != missing && bound.order == "static" && live
	&& !this->holds ();
}

bool BootGuard::release (
    const std::string& cmd, const nlohmann::json& args, const bool ok, Api::Lane& lane, const Api::Playlist& playlist,
    Api::Schedule& schedule, const Api::Clock::time_point now
) {
    bool releasing = false;

    if (cmd == "show" || cmd == "next" || cmd == "prev") {
	releasing = !args.contains ("automatic") || args["automatic"] != true;
    } else if (cmd == "lanes-set" && args.contains ("lanes") && args["lanes"].is_array ()) {
	for (const auto& item : args["lanes"]) {
	    releasing = releasing || (item.is_object () && item.contains ("manual") && item["manual"] == true);
	}
    }

    if (!this->m_restoreRefused || !ok || !releasing) {
	return false;
    }

    this->wallpaperShown ();
    Api::restartCountdown (lane, playlist, now);

    if (schedule.enabled && !schedule.pending.empty () && playlist.order == "static") {
	schedule.held = true;
	schedule.pending.clear ();
    }

    return true;
}

bool BootGuard::showsAfterRelease (
    const bool released, const std::string& cmd, const bool live, const bool screenEmpty
) {
    return released && cmd == "lanes-set" && live && screenEmpty;
}

bool BootGuard::holds () {
    if (this->m_restoreRefused && !this->m_holdLogged) {
	this->m_holdLogged = true;
	sLog.out (
	    "Holding automatic wallpaper changes: the restore was refused after two quick crashes. Show a wallpaper or "
	    "pick a playlist to resume them."
	);
    }

    return this->m_restoreRefused;
}
} // namespace WallpaperEngine::Application
