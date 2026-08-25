#include "GLFWMouseInput.h"
#include <glm/common.hpp>

#include "WallpaperEngine/Render/Drivers/GLFWOpenGLDriver.h"

using namespace WallpaperEngine::Input::Drivers;

GLFWMouseInput::GLFWMouseInput (const Render::Drivers::GLFWOpenGLDriver& driver) : m_driver (driver) { }

void GLFWMouseInput::update () {
    if (!this->m_driver.getApp ().getContext ().settings.mouse.enabled) {
	this->m_reportedPosition = { 0, 0 };
	return;
    }

    const int leftClickState = glfwGetMouseButton (this->m_driver.getWindow (), GLFW_MOUSE_BUTTON_LEFT);
    const int rightClickState = glfwGetMouseButton (this->m_driver.getWindow (), GLFW_MOUSE_BUTTON_RIGHT);

    this->m_leftClick = leftClickState == GLFW_RELEASE ? MouseClickStatus::Released : MouseClickStatus::Clicked;
    this->m_rightClick = rightClickState == GLFW_RELEASE ? MouseClickStatus::Released : MouseClickStatus::Clicked;

    // update current mouse position
    glfwGetCursorPos (this->m_driver.getWindow (), &this->m_mousePosition.x, &this->m_mousePosition.y);

    // Convert from GLFW coordinate system (Y=0 at top) to OpenGL coordinate system (Y=0 at bottom)
    const glm::ivec2 framebufferSize = this->m_driver.getFramebufferSize ();
    this->m_mousePosition.y = static_cast<double> (framebufferSize.y) - this->m_mousePosition.y;

    // interpolate to the new position
    this->m_reportedPosition = glm::mix (this->m_reportedPosition, this->m_mousePosition, 1.0);
}

glm::dvec2 GLFWMouseInput::position () const { return this->m_reportedPosition; }

glm::dvec2 GLFWMouseInput::normalized () const { return this->resolveNormalized ().value_or (glm::dvec2 { 0.5, 0.5 }); }

bool GLFWMouseInput::hasPointer () const { return this->resolveNormalized ().has_value (); }

std::optional<glm::dvec2> GLFWMouseInput::resolveNormalized () const {
    // [0,1]^2 within the window, y-down; nullopt when disabled/degenerate.
    // m_reportedPosition is GL y-up (update() flips), so undo it here.
    if (!this->m_driver.getApp ().getContext ().settings.mouse.enabled) {
	return std::nullopt;
    }
    const glm::ivec2 fb = this->m_driver.getFramebufferSize ();
    if (fb.x <= 0 || fb.y <= 0) {
	return std::nullopt;
    }
    return glm::dvec2 { glm::clamp (this->m_reportedPosition.x / fb.x, 0.0, 1.0),
			glm::clamp (1.0 - this->m_reportedPosition.y / fb.y, 0.0, 1.0) };
}

WallpaperEngine::Input::MouseClickStatus GLFWMouseInput::leftClick () const { return m_leftClick; }

WallpaperEngine::Input::MouseClickStatus GLFWMouseInput::rightClick () const { return m_rightClick; }