#pragma once

#include <filesystem>
#include <optional>
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

    struct Flags {
	std::optional<Knob<std::filesystem::path>> socket;
	std::optional<Knob<float>> ssfactor;
	std::optional<Knob<float>> clampComposites;
	std::optional<Knob<bool>> texcomp;
	std::optional<Knob<bool>> texdetailAuto;
	std::optional<Knob<std::string>> hwdec;
	std::optional<Knob<glm::vec4>> cc;
	std::optional<Knob<float>> timescale;
	std::optional<Knob<int>> deadman;
	std::optional<Knob<float>> classicK;
	std::optional<Knob<float>> classicExp;
	std::optional<Knob<float>> audioGain;
	std::optional<Knob<float>> audioSmooth;
    };
    static void setFlags (const Flags& flags);
    static void clearFlags ();
};

void applyConfigTuning ();
} // namespace WallpaperEngine::Application
