#pragma once

#include <cstdint>
#include <nlohmann/json.hpp>
#include <optional>
#include <string>

namespace WallpaperEngine::Api {
/**
 * Wire schema for the daemon API, one JSON object per line.
 *
 * Request:  {"id": <integer>, "cmd": <verb>, "args": {<object, optional>}}
 * Replies:  {"id": N, "ok": true,  "status": "accepted"}                 - long command taken, work follows
 *           {"id": N, "ok": true,  "status": "done", "result": {...}}    - command finished
 *           {"id": N|null, "ok": false, "error": "..."}                  - rejected or failed
 *
 * Short commands (status, quit) reply once with "done". Long commands (show) reply
 * "accepted" immediately, then "done" or an error with the SAME id when the work
 * finishes. The ack exists because a scene load can take seconds on the render thread
 * and the client must be able to distinguish "never heard you" from "working on it".
 *
 * This layer is pure parsing and validation so it can be unit-tested without a socket
 * or an engine. Everything arriving here is treated as hostile even though the
 * transport only admits same-uid peers: unknown verbs are rejected, `args` must be an
 * object, and a background id must match [A-Za-z0-9_-]{1,64} - no separators, no dots,
 * so no wire input can ever traverse a path. Resolution against the library roots
 * happens in the engine handler; REJECTION of anything path-shaped happens here.
 */
struct Command {
    int64_t id = -1;
    std::string cmd;
    /** always an object; empty when the request carried none */
    nlohmann::json args = nlohmann::json::object ();
};

class CommandDispatcher {
public:
    /** exactly one of `command` / `errorResponse` is meaningful */
    struct ParseOutcome {
	std::optional<Command> command;
	/** complete response line (no trailing newline) describing the rejection */
	std::string errorResponse;
    };

    static ParseOutcome parse (const std::string& line);

    static std::string accepted (int64_t id);
    static std::string done (int64_t id, const nlohmann::json& result = nlohmann::json::object ());
    /** id is echoed when the request carried a usable one, null otherwise */
    static std::string failure (const nlohmann::json& id, const std::string& message);

    /** true only for [A-Za-z0-9_-]{1,64}: safe to append to a library root */
    static bool validBackgroundId (const std::string& id);
};
}
