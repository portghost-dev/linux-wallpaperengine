#pragma once

#include <optional>
#include <string>

/** the show's quality switches, resolved at scene load; an empty value is the launch flag, else environment */
namespace WallpaperEngine::Render::LoadQuality {
float ssfactor (std::optional<float> perShow);
float clampComposites (std::optional<float> perShow);
bool texcomp (const std::string& value);
bool texdetailAuto (const std::string& value);
} // namespace WallpaperEngine::Render::LoadQuality
