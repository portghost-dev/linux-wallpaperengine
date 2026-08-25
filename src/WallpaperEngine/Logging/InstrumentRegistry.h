#pragma once

#include <cstdint>
#include <string>

namespace WallpaperEngine::Logging {

bool instrumentOn (const char* name);

/** Transitions from off to on since process start. Changes => reset your latched state. */
std::uint32_t instrumentEpoch (const char* name);

bool instrumentSet (const std::string& name, bool enabled);

void instrumentSeedFromEnv ();

std::string instrumentsEnabled ();

bool instrumentKnown (const std::string& name);
} // namespace WallpaperEngine::Logging
