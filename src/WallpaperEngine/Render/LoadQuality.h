#pragma once

#include <string>

/** the show's quality switches, resolved at scene load; an empty value is the launch environment */
namespace WallpaperEngine::Render::LoadQuality {
float ssfactor (const std::string& res);
bool clampComposites (const std::string& res);
bool texcomp (const std::string& value);
bool texdetailAuto (const std::string& value);
} // namespace WallpaperEngine::Render::LoadQuality
