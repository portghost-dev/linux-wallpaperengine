#pragma once

#include "WallpaperEngine/Input/InputContext.h"

#include <optional>

namespace WallpaperEngine::Testing::Input {
using namespace WallpaperEngine::Input;

class TestingMouseInput final : public MouseInput {
public:
    void update () override;
    [[nodiscard]] glm::dvec2 position () const override;
    [[nodiscard]] glm::dvec2 normalized () const override;
    [[nodiscard]] bool hasPointer () const override;
    [[nodiscard]] MouseClickStatus leftClick () const override;
    [[nodiscard]] MouseClickStatus rightClick () const override;

    void setNormalized (std::optional<glm::dvec2> normalized);

private:
    std::optional<glm::dvec2> m_normalized = glm::dvec2 { 0.5, 0.5 };
};
} // namespace WallpaperEngine::Testing::Input
