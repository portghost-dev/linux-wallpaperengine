
#include <catch2/catch_test_macros.hpp>
#include <filesystem>
#include <unistd.h>

#include "WallpaperEngine/WebHelper/HelperClient.h"
#include "WallpaperEngine/WebHelper/SpawnConfig.h"

using WallpaperEngine::WebHelper::HelperClient;
using WallpaperEngine::WebHelper::LifecycleState;
using WallpaperEngine::WebHelper::SpawnConfig;

namespace {
SpawnConfig testConfig () {
    SpawnConfig config;
    config.assetsDir = "/nonexistent-assets-for-this-test";
    config.maximumFPS = 60;
    config.protocolVersion = WallpaperEngine::WebHelper::PROTOCOL_VERSION;
    config.socketPath = SpawnConfig::defaultSocketPath (getpid ());
    config.schemes.push_back ({ .workshopId = "1748506393", .path = "/nonexistent-wallpaper-for-this-test" });

    return config;
}
} // namespace

TEST_CASE ("A HelperClient that is never asked to create starts no browser process") {
    const auto config = testConfig ();

    std::filesystem::remove (config.socketPath);

    HelperClient client (config);

    CHECK (client.state () == LifecycleState::Idle);
    CHECK (client.spawnCount () == 0);
    CHECK (client.helperPid () == -1);
    CHECK_FALSE (client.isConnected ());
    CHECK (client.unexpectedDeaths () == 0);
    CHECK (client.lastExitDescription ().empty ());
    CHECK_FALSE (std::filesystem::exists (config.socketPath));

    // ...and it stays that way while the engine loop runs. pumpEvents is called once per
    // frame forever on a scene-only desktop; nothing in it may decide to start a browser.
    for (int frame = 0; frame < 240; ++frame) {
	client.pumpEvents ();
    }

    CHECK (client.state () == LifecycleState::Idle);
    CHECK (client.spawnCount () == 0);
    CHECK (client.helperPid () == -1);
    CHECK_FALSE (client.isConnected ());
    CHECK_FALSE (std::filesystem::exists (config.socketPath));

    CHECK (client.millisUntilNextAttempt () == 0);
}

TEST_CASE ("Allocating an instance id does not start a browser process either") {
    // CWeb allocates its id in its constructor and only then calls create(). The id itself
    // must stay free: a mirror group builds one CWeb, but other code paths ask for ids too.
    HelperClient client (testConfig ());

    const auto first = client.allocateInstance ();
    const auto second = client.allocateInstance ();

    CHECK (first != second);
    CHECK (client.state () == LifecycleState::Idle);
    CHECK (client.spawnCount () == 0);
    CHECK (client.helperPid () == -1);
}
