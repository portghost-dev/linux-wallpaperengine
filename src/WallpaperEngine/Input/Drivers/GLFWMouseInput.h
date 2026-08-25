#pragma once

#include "WallpaperEngine/Input/MouseInput.h"

#include <glm/vec2.hpp>
#include <optional>

namespace WallpaperEngine::Render::Drivers {
class GLFWOpenGLDriver;
}

namespace WallpaperEngine::Input::Drivers {
/**
 * Handles mouse input for the background
 */
class GLFWMouseInput final : public MouseInput {
public:
    explicit GLFWMouseInput (const Render::Drivers::GLFWOpenGLDriver& driver);

    /**
     * Takes current mouse position and updates it
     */
    void update () override;

    /**
     * The virtual pointer's position
     */
    [[nodiscard]] glm::dvec2 position () const override;

    [[nodiscard]] glm::dvec2 normalized () const override;

    [[nodiscard]] bool hasPointer () const override;

    /**
     * @return The status of the mouse's left click
     */
    [[nodiscard]] MouseClickStatus leftClick () const override;

    /**
     * @return The status of the mouse's right click
     */
    [[nodiscard]] MouseClickStatus rightClick () const override;

private:
    [[nodiscard]] std::optional<glm::dvec2> resolveNormalized () const;

    const Render::Drivers::GLFWOpenGLDriver& m_driver;

    /**
     * The current mouse position
     */
    glm::dvec2 m_mousePosition = {};
    glm::dvec2 m_reportedPosition = {};
    MouseClickStatus m_leftClick = Released;
    MouseClickStatus m_rightClick = Released;
};
} // namespace WallpaperEngine::Input::Drivers
