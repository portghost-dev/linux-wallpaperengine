#pragma once

#include <optional>
#include <string>

namespace WallpaperEngine::Render::OverlayLabel {

enum class Corner { TopLeft, TopRight, BottomLeft, BottomRight };

struct Settings {
    std::string text;
    Corner corner = Corner::TopLeft;
    bool visible = true;
};

/** Wire names: top-left, top-right, bottom-left, bottom-right */
const char* cornerName (Corner corner);
std::optional<Corner> cornerFromName (const std::string& name);

/** Update any subset of the overlay settings; the text is re-rasterized on the next draw. Main thread. */
void set (const std::optional<std::string>& text, std::optional<Corner> corner, std::optional<bool> visible);
Settings current ();

/** Draw the label (if visible and non-empty) into the current viewport. GL context current. */
void draw (int viewportWidth, int viewportHeight);
}
