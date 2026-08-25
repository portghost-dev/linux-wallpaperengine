#pragma once

#include <csignal>
#include <filesystem>
#include <string>
#include <vector>

namespace WallpaperEngine::WebHelper {
class SpawnGate {
public:
    /**
     * Call once from main(), before any thread, GL context or audio driver exists.
     *
     * Cheap and side-effect free: two reads of /proc and one sigprocmask query. Safe to
     * call from a binary that never spawns a helper, which is why the probe calls it too.
     */
    static void captureAtStartup ();

    [[nodiscard]] static bool captured ();
    /** where lwe-web-service was resolved to; empty if capture failed */
    [[nodiscard]] static const std::filesystem::path& serviceBinary ();
    /** how many threads the process had at capture time; 1 means the gate was placed right */
    [[nodiscard]] static int threadsAtCapture ();

    /**
     * Start lwe-web-service. Returns its pid, or -1 with `error` set.
     *
     * posix_spawn rather than fork+exec: it runs no user code between the clone and the
     * exec, so no allocator or locale lock held by another engine thread can be observed
     * half-held in the child. The signal mask captured at startup is restored explicitly,
     * because the measurement above shows the current one would otherwise be inherited.
     */
    [[nodiscard]] static pid_t spawn (const std::vector<std::string>& arguments, std::string& error);
};
}
