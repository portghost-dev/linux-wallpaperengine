#include "BootGuard.h"

namespace WallpaperEngine::Application {
bool BootGuard::evaluate (const nlohmann::json& history) {
    const size_t n = history.size ();
    this->m_restoreRefused = n >= 2 && history[n - 1].is_object () && history[n - 2].is_object ()
	&& !history[n - 1].value ("survived", true) && !history[n - 2].value ("survived", true);
    return this->m_restoreRefused;
}

void BootGuard::wallpaperShown () { this->m_restoreRefused = false; }

bool BootGuard::restoreRefused () const { return this->m_restoreRefused; }
} // namespace WallpaperEngine::Application
