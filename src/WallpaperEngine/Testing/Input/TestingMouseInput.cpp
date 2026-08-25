#include "TestingMouseInput.h"

using namespace WallpaperEngine::Testing::Input;

void TestingMouseInput::update () { }

glm::dvec2 TestingMouseInput::position () const { return {}; }

// (0.5,0.5) is the rest-centre the real drivers return when the mouse is disabled or its
// output is unknown, so a test double reports the same neutral state rather than a corner.
glm::dvec2 TestingMouseInput::normalized () const { return this->m_normalized.value_or (glm::dvec2 { 0.5, 0.5 }); }

bool TestingMouseInput::hasPointer () const { return this->m_normalized.has_value (); }

void TestingMouseInput::setNormalized (std::optional<glm::dvec2> normalized) { this->m_normalized = normalized; }

MouseClickStatus TestingMouseInput::leftClick () const { return MouseClickStatus::Released; }

MouseClickStatus TestingMouseInput::rightClick () const { return MouseClickStatus::Released; }
