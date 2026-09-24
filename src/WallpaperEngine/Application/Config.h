#pragma once

#include <filesystem>
#include <string>

#include <glm/vec4.hpp>

namespace WallpaperEngine::Application {
template <typename T> struct Knob {
    T value;
    std::string source;
    std::string raw;
};

struct Config {
    Knob<std::filesystem::path> socket;
    Knob<float> ssfactor;
    Knob<float> clampComposites;
    Knob<bool> texcomp;
    Knob<bool> texdetailAuto;
    Knob<std::string> hwdec;
    Knob<glm::vec4> cc;
    Knob<float> timescale;
    Knob<int> deadman;
    Knob<float> classicK;
    Knob<float> classicExp;
    Knob<float> audioGain;
    Knob<float> audioSmooth;

    static const Config& get ();
    static void reload ();
};
} // namespace WallpaperEngine::Application
