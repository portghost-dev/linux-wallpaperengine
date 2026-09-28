#pragma once

#include <cstddef>
#include <filesystem>
#include <nlohmann/json.hpp>
#include <string>

namespace WallpaperEngine::Api {
class CommandClient {
public:
    enum class Outcome { Reply, NotRunning, NoAnswer, Closed, BadReply };

    struct Response {
	Outcome outcome;
	nlohmann::json reply;
	int error = 0;
    };

    static constexpr int DEADLINE_MS = 3000;
    static constexpr size_t MAX_REPLY_BYTES = 64 * 1024;

    static Response request (const std::filesystem::path& socketPath, const std::string& cmd);
};
}
