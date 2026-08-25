#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace WallpaperEngine::WebHelper {
struct SchemeEntry {
    std::string workshopId;
    std::filesystem::path path;
};

struct SpawnConfig {
    std::filesystem::path assetsDir;
    /**
     * The complete scheme universe, from WallpaperApplication::enumerateWebBackgrounds.
     * Immutable after CefInitialize, so a wallpaper missing here can never be shown by
     * this helper - it would fail with ERR_UNKNOWN_URL_SCHEME.
     */
    std::vector<SchemeEntry> schemes;
    /** settings.render.maximumFPS; the helper clamps it to CEF's documented 60 minimum */
    int maximumFPS = 0;
    std::filesystem::path socketPath;
    /** PROTOCOL_VERSION of the process that built this; mismatched pairs refuse to run */
    uint32_t protocolVersion = 0;

    /** argv the engine passes to the helper binary (argv[0] excluded) */
    [[nodiscard]] std::vector<std::string> toArguments () const;

    /**
     * Rebuild from the helper's own argv. Unknown arguments are ignored so CEF's switches
     * can share the command line.
     *
     * @return false if a required field is missing or the protocol version disagrees;
     *         `error` then says which.
     */
    static bool fromArguments (int argc, char** argv, SpawnConfig& out, std::string& error);

    /** default socket location: $LWE_WEB_SOCKET, else $XDG_RUNTIME_DIR/lwe/web-<pid>.sock */
    static std::filesystem::path defaultSocketPath (int enginePid);
};
}
