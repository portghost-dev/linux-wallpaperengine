#pragma once

#include <limits>
#include <optional>
#include <string>
#include <vector>

namespace WallpaperEngine::Application::DebugSwitches {
struct Action {
    std::string variable;
    std::optional<std::string> setTo;
};

struct Choice {
    std::string word;
    std::optional<std::string> setTo;
};

enum class Rule { Words, Whole, Text, Size, Position, Rectangle, Combo, CrashLimit, Milliseconds };

struct Row {
    std::string name;
    std::string variable;
    std::string accepts;
    std::vector<Choice> words;
    Rule rule = Rule::Words;
    unsigned long long lo = 0;
    unsigned long long hi = std::numeric_limits<unsigned long long>::max ();
};

const std::vector<Row>& table ();
Action resolve (const std::string& token);
const char* helpText ();
} // namespace WallpaperEngine::Application::DebugSwitches
