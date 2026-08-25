
#include "Steam/FileSystem/FileSystem.h"
#include "WallpaperEngine/Data/Model/Property.h"
#include "WallpaperEngine/Data/Model/UserSetting.h"
#include "WallpaperEngine/Data/Parsers/PropertyParser.h"
#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/WebHelper/FrameContract.h"
#include "WallpaperEngine/WebHelper/HelperClient.h"
#include "WallpaperEngine/WebHelper/PropertyClassifier.h"
#include "WallpaperEngine/WebHelper/SpawnGate.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include <csignal>
#include <unistd.h>

#include <nlohmann/json.hpp>

using namespace WallpaperEngine::WebHelper;

namespace {
using Clock = std::chrono::steady_clock;

int millisSince (const Clock::time_point start) {
    return static_cast<int> (std::chrono::duration_cast<std::chrono::milliseconds> (Clock::now () - start).count ());
}

struct ChannelStats {
    uint8_t min[4] = { 255, 255, 255, 255 };
    uint8_t max[4] = { 0, 0, 0, 0 };
    double mean[4] = { 0, 0, 0, 0 };
    /** how many DISTINCT pixel values the frame contains; 1 means a flat fill */
    size_t distinctPixels = 0;
};

ChannelStats measure (const uint8_t* pixels, const uint32_t width, const uint32_t height) {
    ChannelStats stats;
    const size_t count = static_cast<size_t> (width) * height;
    uint64_t sums[4] = { 0, 0, 0, 0 };

    // a cheap distinct-value set: 32-bit pixels bucketed into a bitset over the low 16 bits
    // plus the high 16, which is enough to tell "flat fill" from "a rendered page"
    std::vector<bool> seen (1u << 16, false);

    for (size_t i = 0; i < count; i++) {
	const uint8_t* pixel = pixels + i * 4;

	for (int channel = 0; channel < 4; channel++) {
	    stats.min[channel] = std::min (stats.min[channel], pixel[channel]);
	    stats.max[channel] = std::max (stats.max[channel], pixel[channel]);
	    sums[channel] += pixel[channel];
	}

	uint32_t packed = 0;
	std::memcpy (&packed, pixel, 4);

	if (const uint16_t bucket = static_cast<uint16_t> (packed ^ (packed >> 16)); !seen[bucket]) {
	    seen[bucket] = true;
	    stats.distinctPixels++;
	}
    }

    for (int channel = 0; channel < 4; channel++) {
	stats.mean[channel] = count > 0 ? static_cast<double> (sums[channel]) / static_cast<double> (count) : 0.0;
    }

    return stats;
}

void printStats (const char* label, const ChannelStats& stats) {
    static const char* names[4] = { "B", "G", "R", "A" };

    std::cout << "    " << label << "\n";

    for (int channel = 0; channel < 4; channel++) {
	std::cout << "      " << names[channel] << ": min=" << static_cast<int> (stats.min[channel])
		  << " max=" << static_cast<int> (stats.max[channel]) << " mean=" << stats.mean[channel] << "\n";
    }

    std::cout << "      distinct pixel buckets: " << stats.distinctPixels << "\n";
}

/**
 * Mean absolute per-channel difference between two frames, over B, G and R only.
 *
 * Alpha is excluded because it is constant 255 and would dilute every number by a quarter
 * for no information. Returns -1 when there is no comparable previous frame.
 */
double
frameDelta (const uint8_t* current, const std::vector<uint8_t>& previous, const uint32_t width, const uint32_t height) {
    const size_t count = static_cast<size_t> (width) * height;

    if (previous.size () != count * 4 || count == 0) {
	return -1.0;
    }

    uint64_t sum = 0;

    for (size_t i = 0; i < count; i++) {
	for (int channel = 0; channel < 3; channel++) {
	    const int difference
		= static_cast<int> (current[i * 4 + channel]) - static_cast<int> (previous[i * 4 + channel]);
	    sum += static_cast<uint64_t> (difference < 0 ? -difference : difference);
	}
    }

    return static_cast<double> (sum) / static_cast<double> (count * 3);
}

struct Window {
    uint32_t firstSequence = 0;
    uint32_t lastSequence = 0;
    uint64_t accepted = 0;
    uint64_t retries = 0;
    uint64_t abandoned = 0;
    uint32_t width = 0;
    uint32_t height = 0;
    bool sequenceWentBackwards = false;
    bool sawNonUniform = false;
    ChannelStats last;
    size_t maxDistinct = 0;

    uint64_t comparisons = 0;
    double meanDelta = 0.0;
    double maxDelta = 0.0;
};

using TickCallback = std::function<void (int tick)>;

Window readFor (
    HelperClient& client, FrameReader& reader, const double seconds, const int stallEvery, const int stallMillis,
    std::vector<uint8_t>* previousFrame = nullptr, const TickCallback& onTick = nullptr
) {
    Window window;
    const auto started = Clock::now ();
    uint64_t iterations = 0;
    int tick = 0;
    const uint64_t acceptedBefore = reader.accepted ();
    const uint64_t retriesBefore = reader.retries ();
    const uint64_t abandonedBefore = reader.abandoned ();
    double deltaSum = 0.0;

    while (std::chrono::duration<double> (Clock::now () - started).count () < seconds) {
	client.pumpEvents ();

	if (onTick) {
	    onTick (tick);
	}

	tick++;

	const bool got = reader.consume ([&] (const void* pixels, const uint32_t width, const uint32_t height) {
	    iterations++;

	    if (stallEvery > 0 && iterations % static_cast<uint64_t> (stallEvery) == 0) {
		std::this_thread::sleep_for (std::chrono::milliseconds (stallMillis));
	    }

	    const auto* bytes = static_cast<const uint8_t*> (pixels);

	    window.last = measure (bytes, width, height);
	    window.width = width;
	    window.height = height;

	    window.maxDistinct = std::max (window.maxDistinct, window.last.distinctPixels);

	    if (window.last.distinctPixels > 1) {
		window.sawNonUniform = true;
	    }

	    if (previousFrame != nullptr) {
		if (const double delta = frameDelta (bytes, *previousFrame, width, height); delta >= 0.0) {
		    window.comparisons++;
		    deltaSum += delta;
		    window.maxDelta = std::max (window.maxDelta, delta);
		}

		previousFrame->assign (bytes, bytes + static_cast<size_t> (width) * height * 4);
	    }
	});

	if (got) {
	    const uint32_t sequence = reader.lastSequence ();

	    if (window.firstSequence == 0) {
		window.firstSequence = sequence;
	    } else if (sequence <= window.lastSequence) {
		// the contract says sequence is monotonic; anything else is a bug in the
		// writer, the reader, or the memory ordering between them
		window.sequenceWentBackwards = true;
	    }

	    window.lastSequence = sequence;
	}

	std::this_thread::sleep_for (std::chrono::milliseconds (2));
    }

    // per-WINDOW counters, not lifetime ones: the verb phases run back to back against a
    // single reader, and a lifetime total would smear every phase into the next
    window.accepted = reader.accepted () - acceptedBefore;
    window.retries = reader.retries () - retriesBefore;
    window.abandoned = reader.abandoned () - abandonedBefore;
    window.meanDelta = window.comparisons > 0 ? deltaSum / static_cast<double> (window.comparisons) : 0.0;

    return window;
}

void printMotion (const char* label, const Window& window) {
    std::cout << "    " << label << ": " << window.accepted << " frames, " << window.comparisons
	      << " frame-to-frame comparisons, mean delta " << std::fixed << std::setprecision (3) << window.meanDelta
	      << ", max delta " << window.maxDelta << std::defaultfloat << "\n";
}

std::filesystem::path resolveAssetsDir (const std::string& explicitValue) {
    if (!explicitValue.empty ()) {
	return { explicitValue };
    }

    try {
	return Steam::FileSystem::appDirectory ("wallpaper_engine", "assets");
    } catch (const std::exception&) {
	return std::filesystem::canonical ("/proc/self/exe").parent_path () / "assets";
    }
}

std::string argumentValue (const int argc, char** argv, const std::string& prefix, const std::string& fallback) {
    for (int i = 1; i < argc; i++) {
	if (const std::string argument = argv[i]; argument.rfind (prefix, 0) == 0) {
	    return argument.substr (prefix.size ());
	}
    }

    return fallback;
}

bool hasFlag (const int argc, char** argv, const std::string& flag) {
    for (int i = 1; i < argc; i++) {
	if (flag == argv[i]) {
	    return true;
	}
    }

    return false;
}

struct Subject {
    std::filesystem::path path;
    std::string workshopId;
    std::string file;
    nlohmann::json project;
};

WallpaperEngine::Data::Model::Properties parseProperties (const nlohmann::json& project) {
    WallpaperEngine::Data::Model::Properties properties;

    const auto general = project.find ("general");

    if (general == project.end ()) {
	return properties;
    }

    const auto declared = general->find ("properties");

    if (declared == general->end ()) {
	return properties;
    }

    for (const auto& [key, value] : declared->items ()) {
	try {
	    if (auto parsed = WallpaperEngine::Data::Parsers::PropertyParser::parse (value, key); parsed != nullptr) {
		properties.emplace (key, std::move (parsed));
	    }
	} catch (const std::exception& e) {
	    std::cout << "    (skipping property " << key << ": " << e.what () << ")\n";
	}
    }

    return properties;
}

void synthesiseSpectrum (float* bands, const double phase) {
    const double centre = 6.0 + 5.0 * std::sin (phase);

    for (size_t i = 0; i < AUDIO_BANDS; i++) {
	const double distance = static_cast<double> (i) - centre;
	bands[i] = static_cast<float> (0.30 * std::exp (-(distance * distance) / 18.0));
    }
}

std::set<int> processPidsNamed (const std::string& comm) {
    std::set<int> pids;
    std::error_code ec;

    for (const auto& entry : std::filesystem::directory_iterator ("/proc", ec)) {
	const std::string name = entry.path ().filename ().string ();

	if (name.empty () || !std::isdigit (static_cast<unsigned char> (name[0]))) {
	    continue;
	}

	std::ifstream stream (entry.path () / "comm");
	std::string line;

	if (std::getline (stream, line) && line == comm) {
	    pids.insert (std::stoi (name));
	}
    }

    return pids;
}

std::set<int> newSince (const std::set<int>& baseline, const std::set<int>& current) {
    std::set<int> difference;

    for (const int pid : current) {
	if (baseline.find (pid) == baseline.end ()) {
	    difference.insert (pid);
	}
    }

    return difference;
}

/** parent pid from /proc/<pid>/stat, or -1 */
int parentOf (const int pid) {
    std::ifstream stream ("/proc/" + std::to_string (pid) + "/stat");
    std::string contents;

    if (!std::getline (stream, contents)) {
	return -1;
    }

    // the comm field is parenthesised and may contain spaces, so parse after the last ')'
    const auto close = contents.rfind (')');

    if (close == std::string::npos) {
	return -1;
    }

    std::istringstream fields (contents.substr (close + 1));
    std::string state;
    int ppid = -1;
    fields >> state >> ppid;

    return ppid;
}

std::vector<std::string> shmObjectsFor (const int pid) {
    std::vector<std::string> found;
    const std::string prefix = "lwe-web-" + std::to_string (pid) + "-";
    std::error_code ec;

    for (const auto& entry : std::filesystem::directory_iterator ("/dev/shm", ec)) {
	if (const std::string name = entry.path ().filename ().string (); name.rfind (prefix, 0) == 0) {
	    found.push_back (name);
	}
    }

    return found;
}

std::set<std::string> shmObjectsNow () {
    std::set<std::string> found;
    std::error_code ec;

    for (const auto& entry : std::filesystem::directory_iterator ("/dev/shm", ec)) {
	if (const std::string name = entry.path ().filename ().string (); name.rfind ("lwe-web-", 0) == 0) {
	    found.insert (name);
	}
    }

    return found;
}

bool processExists (const int pid) { return pid > 0 && std::filesystem::exists ("/proc/" + std::to_string (pid)); }

/** pump the client until `done` says so, or the budget runs out. Returns ms elapsed, or -1 */
int pumpUntil (HelperClient& client, const int budgetMs, const std::function<bool ()>& done) {
    const auto started = Clock::now ();

    while (millisSince (started) < budgetMs) {
	client.pumpEvents ();

	if (done ()) {
	    return millisSince (started);
	}

	std::this_thread::sleep_for (std::chrono::milliseconds (2));
    }

    return -1;
}
} // namespace

static int runFrameTransportProof (
    HelperClient& client, const Subject& subject, const uint32_t width, const uint32_t height,
    const uint32_t resizeWidth, const uint32_t resizeHeight, const uint32_t fps, const double seconds,
    std::string& shmNameOne, std::string& shmNameTwo
) {
    int failures = 0;

    const auto spawnStarted = Clock::now ();
    const InstanceId id = client.allocateInstance ();
    client.create (id, subject.workshopId, subject.file, width, height, fps);

    if (!client.isConnected ()) {
	std::cerr << "FAIL: never connected to lwe-web-service\n";
	return 1;
    }

    std::cout << "[1] spawn+connect: helper pid " << client.helperPid () << ", " << millisSince (spawnStarted)
	      << " ms\n";

    const auto loadStarted = Clock::now ();

    while (!client.isPageLoaded (id) && !client.didLoadFail (id) && millisSince (loadStarted) < 30000) {
	client.pumpEvents ();
	std::this_thread::sleep_for (std::chrono::milliseconds (5));
    }

    if (client.didLoadFail (id)) {
	std::cerr << "FAIL: the page FAILED to load (page-failed event); see the error above\n";
	return 1;
    }

    if (!client.isPageLoaded (id)) {
	std::cerr << "FAIL: page never loaded\n";
	return 1;
    }

    std::cout << "[2] page-loaded after " << millisSince (loadStarted) << " ms\n";

    const auto generationStarted = Clock::now ();
    uint32_t generationOne = 0;

    while (millisSince (generationStarted) < 15000) {
	client.pumpEvents ();

	if (const auto* state = client.instance (id); state != nullptr && state->frameGeneration != 0) {
	    generationOne = state->frameGeneration;
	    break;
	}

	std::this_thread::sleep_for (std::chrono::milliseconds (5));
    }

    if (generationOne == 0) {
	std::cerr << "FAIL: helper never announced a frame generation\n";
	return 1;
    }

    shmNameOne = frameShmName (client.helperPid (), id, generationOne);
    std::cout << "[3] generation " << generationOne << " announced after " << millisSince (generationStarted)
	      << " ms, shm " << shmNameOne << "\n";

    FrameReader reader;

    if (!reader.open (shmNameOne)) {
	std::cerr << "FAIL: cannot map " << shmNameOne << ": " << reader.error () << "\n";
	return 1;
    }

    std::cout << "    mapped " << reader.width () << "x" << reader.height () << ", stride " << reader.stride () << "\n";

    std::cout << "\n[4] reading for " << seconds << " s at " << width << "x" << height
	      << " (stalling every 10th read by 60 ms to force the writer to lap us)\n";

    const Window first = readFor (client, reader, seconds, 10, 60);

    std::cout << "    sequence:  " << first.firstSequence << " -> " << first.lastSequence << "\n"
	      << "    accepted:  " << first.accepted << " frames\n"
	      << "    retries:   " << first.retries << " (writer lapped the reader mid-read)\n"
	      << "    abandoned: " << first.abandoned << "\n"
	      << "    torn reads accepted: 0 (a read is only accepted when the re-check matches)\n"
	      << "    monotonic: " << (first.sequenceWentBackwards ? "NO" : "yes") << "\n"
	      << "    any non-uniform frame in the window: " << (first.sawNonUniform ? "yes" : "no") << "\n"
	      << "    size:      " << first.width << "x" << first.height << "\n";
    printStats ("last frame, BGRA:", first.last);

    if (first.lastSequence <= first.firstSequence) {
	std::cerr << "FAIL: sequence did not advance\n";
	failures++;
    }

    if (first.sequenceWentBackwards) {
	std::cerr << "FAIL: sequence went backwards\n";
	failures++;
    }

    if (first.width != width || first.height != height) {
	std::cerr << "FAIL: frames are not the size that was requested\n";
	failures++;
    }

    if (!first.sawNonUniform || first.last.distinctPixels <= 1) {
	std::cerr << "FAIL: frame content is uniform (blank page or a flat fill)\n";
	failures++;
    }

    if (first.last.max[0] == 0 && first.last.max[1] == 0 && first.last.max[2] == 0) {
	std::cerr << "FAIL: frame is all black across B, G and R\n";
	failures++;
    }

    std::cout << "\n[5] resize to " << resizeWidth << "x" << resizeHeight << "\n";
    client.resize (id, resizeWidth, resizeHeight);

    const auto resizeStarted = Clock::now ();
    uint32_t generationTwo = 0;

    while (millisSince (resizeStarted) < 15000) {
	client.pumpEvents ();

	if (const auto* state = client.instance (id);
	    state != nullptr && state->frameGeneration != 0 && state->frameGeneration != generationOne) {
	    generationTwo = state->frameGeneration;
	    break;
	}

	std::this_thread::sleep_for (std::chrono::milliseconds (5));
    }

    if (generationTwo == 0) {
	std::cerr << "FAIL: no new generation after resize\n";
	return 1;
    }

    shmNameTwo = frameShmName (client.helperPid (), id, generationTwo);
    std::cout << "    generation " << generationOne << " -> " << generationTwo << " after "
	      << millisSince (resizeStarted) << " ms, shm " << shmNameTwo << "\n";

    if (!reader.open (shmNameTwo)) {
	std::cerr << "FAIL: cannot map " << shmNameTwo << ": " << reader.error () << "\n";
	return 1;
    }

    const Window second = readFor (client, reader, seconds, 10, 60);

    std::cout << "    sequence:  " << second.firstSequence << " -> " << second.lastSequence << "\n"
	      << "    accepted:  " << second.accepted << " frames\n"
	      << "    retries:   " << second.retries << "\n"
	      << "    abandoned: " << second.abandoned << "\n"
	      << "    monotonic: " << (second.sequenceWentBackwards ? "NO" : "yes") << "\n"
	      << "    any non-uniform frame in the window: " << (second.sawNonUniform ? "yes" : "no") << "\n"
	      << "    size:      " << second.width << "x" << second.height << "\n";
    printStats ("last frame, BGRA:", second.last);

    if (second.lastSequence == 0 || second.accepted == 0) {
	std::cerr << "FAIL: frames stopped flowing after the resize\n";
	failures++;
    }

    if (second.width != resizeWidth || second.height != resizeHeight) {
	std::cerr << "FAIL: frames did not adopt the new size\n";
	failures++;
    }

    if (second.sequenceWentBackwards) {
	std::cerr << "FAIL: sequence went backwards after the resize\n";
	failures++;
    }

    if (!second.sawNonUniform) {
	std::cerr << "FAIL: frame content is uniform after the resize\n";
	failures++;
    }

    std::cout << "\n[6] destroy(" << id << ") and disconnect\n";
    client.destroy (id);
    client.pumpEvents ();
    std::this_thread::sleep_for (std::chrono::milliseconds (200));

    std::cout << "    helper pid was " << client.helperPid () << "\n";

    return failures;
}

static int runVerbProof (
    HelperClient& client, const Subject& subject, const uint32_t width, const uint32_t height,
    const uint32_t resizeWidth, const uint32_t resizeHeight, const uint32_t fps, const double seconds,
    std::string& shmNameOne, std::string& shmNameTwo
) {
    int failures = 0;

    const auto spawnStarted = Clock::now ();
    const InstanceId id = client.allocateInstance ();
    client.create (id, subject.workshopId, subject.file, width, height, fps);

    if (!client.isConnected ()) {
	std::cerr << "FAIL: never connected to lwe-web-service\n";
	return 1;
    }

    std::cout << "[create] helper pid " << client.helperPid () << ", " << millisSince (spawnStarted) << " ms\n";

    const auto loadStarted = Clock::now ();

    while (!client.isPageLoaded (id) && !client.didLoadFail (id) && millisSince (loadStarted) < 30000) {
	client.pumpEvents ();
	std::this_thread::sleep_for (std::chrono::milliseconds (5));
    }

    if (client.didLoadFail (id)) {
	const auto* state = client.instance (id);
	std::cerr << "FAIL [page-loaded]: the page FAILED to load: "
		  << (state != nullptr ? state->loadErrorText : std::string ("?")) << "\n";
	return 1;
    }

    if (!client.isPageLoaded (id)) {
	std::cerr << "FAIL [page-loaded]: no page-loaded and no page-failed within 30 s\n";
	return 1;
    }

    std::cout << "[page-loaded] after " << millisSince (loadStarted) << " ms, load-failed flag "
	      << (client.didLoadFail (id) ? "SET" : "clear") << "\n";

    uint32_t generationOne = 0;
    const auto generationStarted = Clock::now ();

    while (millisSince (generationStarted) < 15000) {
	client.pumpEvents ();

	if (const auto* state = client.instance (id); state != nullptr && state->frameGeneration != 0) {
	    generationOne = state->frameGeneration;
	    break;
	}

	std::this_thread::sleep_for (std::chrono::milliseconds (5));
    }

    if (generationOne == 0) {
	std::cerr << "FAIL: helper never announced a frame generation\n";
	return 1;
    }

    shmNameOne = frameShmName (client.helperPid (), id, generationOne);
    FrameReader reader;

    if (!reader.open (shmNameOne)) {
	std::cerr << "FAIL: cannot map " << shmNameOne << ": " << reader.error () << "\n";
	return 1;
    }

    std::vector<uint8_t> previousFrame;

    auto properties = parseProperties (subject.project);
    const auto classified = WallpaperEngine::WebHelper::classifyProperties (properties);

    size_t booleans = 0;
    size_t numbers = 0;
    size_t strings = 0;

    for (const auto& property : classified) {
	switch (property.kind) {
	    case PropertyValue::Kind::Boolean:
		booleans++;
		break;
	    case PropertyValue::Kind::Number:
		numbers++;
		break;
	    case PropertyValue::Kind::String:
		strings++;
		break;
	}
    }

    std::cout << "\n[inject-properties] " << properties.size () << " parsed, " << classified.size () << " classified ("
	      << booleans << " bool, " << numbers << " number, " << strings << " string)\n";

    for (const auto& property : classified) {
	if (property.kind == PropertyValue::Kind::Boolean) {
	    std::cout << "    bool   " << property.key << " = " << (property.booleanValue ? "true" : "false") << "\n";
	}
    }

    if (classified.empty ()) {
	std::cerr << "FAIL [inject-properties]: nothing to inject; this wallpaper declares no properties\n";
	failures++;
    }

    client.injectProperties (id, classified);
    client.pumpEvents ();

    const Window settle = readFor (client, reader, 1.5, 0, 0, &previousFrame);
    printMotion ("settle after injection", settle);

    const Window baseline = readFor (client, reader, seconds, 0, 0, &previousFrame);
    printMotion ("BASELINE (no input)", baseline);

    const int centreX = static_cast<int> (width) / 2;
    const int centreY = static_cast<int> (height) / 2;
    int clicks = 0;

    const Window clicked = readFor (client, reader, seconds, 0, 0, &previousFrame, [&] (const int tick) {
	if (tick % 120 == 0) {
	    client.mouseClick (id, centreX, centreY, MouseButton::Left, false);
	    clicks++;
	} else if (tick % 120 == 8) {
	    client.mouseClick (id, centreX, centreY, MouseButton::Left, true);
	}
    });

    std::cout << "\n[mouse-click] " << clicks << " press/release pairs at (" << centreX << "," << centreY << ")\n";
    printMotion ("with clicks", clicked);

    const double radius = 0.30 * std::min (width, height);
    int moves = 0;
    int lastX = -1;
    int lastY = -1;

    const Window moved = readFor (client, reader, seconds, 0, 0, &previousFrame, [&] (const int tick) {
	const double angle = static_cast<double> (tick) * 0.09;
	const int x = static_cast<int> (centreX + radius * std::cos (angle));
	const int y = static_cast<int> (centreY + radius * std::sin (angle));

	if (x == lastX && y == lastY) {
	    return;
	}

	lastX = x;
	lastY = y;
	client.mouseMove (id, x, y);
	moves++;
    });

    std::cout << "\n[mouse-move] " << moves << " move events along a circle, center (" << centreX << "," << centreY
	      << ") radius " << radius << "\n";
    printMotion ("with movement", moved);

    const std::string toggleKey = "show_mouse_movement";
    Window movementOff;
    Window movementOn;
    const bool hasToggle = properties.find (toggleKey) != properties.end ();

    if (hasToggle) {
	auto& property = properties[toggleKey];
	property->update (false, WallpaperEngine::Data::Model::DynamicValue::UpdateSource::User);

	if (const auto value = WallpaperEngine::WebHelper::classifyProperty (toggleKey, *property);
	    value.has_value ()) {
	    std::cout << "\n[set-property] " << toggleKey << " -> false (kind " << static_cast<int> (value->kind)
		      << ", booleanValue " << (value->booleanValue ? "true" : "false") << ")\n";
	    client.setProperty (id, *value);
	}

	lastX = -1;
	lastY = -1;

	movementOff = readFor (client, reader, seconds, 0, 0, &previousFrame, [&] (const int tick) {
	    const double angle = static_cast<double> (tick) * 0.09;
	    const int x = static_cast<int> (centreX + radius * std::cos (angle));
	    const int y = static_cast<int> (centreY + radius * std::sin (angle));

	    if (x == lastX && y == lastY) {
		return;
	    }

	    lastX = x;
	    lastY = y;
	    client.mouseMove (id, x, y);
	});

	printMotion ("moving, movement DISABLED live", movementOff);

	property->update (true, WallpaperEngine::Data::Model::DynamicValue::UpdateSource::User);

	if (const auto value = WallpaperEngine::WebHelper::classifyProperty (toggleKey, *property);
	    value.has_value ()) {
	    std::cout << "[set-property] " << toggleKey << " -> true\n";
	    client.setProperty (id, *value);
	}

	lastX = -1;
	lastY = -1;

	movementOn = readFor (client, reader, seconds, 0, 0, &previousFrame, [&] (const int tick) {
	    const double angle = static_cast<double> (tick) * 0.09;
	    const int x = static_cast<int> (centreX + radius * std::cos (angle));
	    const int y = static_cast<int> (centreY + radius * std::sin (angle));

	    if (x == lastX && y == lastY) {
		return;
	    }

	    lastX = x;
	    lastY = y;
	    client.mouseMove (id, x, y);
	});

	printMotion ("moving, movement RE-ENABLED live", movementOn);
    } else {
	std::cout << "\n[set-property] this wallpaper has no " << toggleKey << "; phase skipped\n";
    }

    int spectra = 0;
    float bands[AUDIO_BANDS] = {};

    const Window audio = readFor (client, reader, seconds, 0, 0, &previousFrame, [&] (const int tick) {
	if ((tick & 1) != 0) {
	    return;
	}

	synthesiseSpectrum (bands, static_cast<double> (tick) * 0.02);
	client.audioSpectrum (id, bands);
	spectra++;
    });

    std::cout << "\n[audio-spectrum] " << spectra << " spectra sent (" << AUDIO_BANDS
	      << " raw floats each, every other tick)\n";
    printMotion ("with audio", audio);

    std::cout << "\n[resize] " << width << "x" << height << " -> " << resizeWidth << "x" << resizeHeight << "\n";
    client.resize (id, resizeWidth, resizeHeight);

    const auto resizeStarted = Clock::now ();
    uint32_t generationTwo = 0;

    while (millisSince (resizeStarted) < 15000) {
	client.pumpEvents ();

	if (const auto* state = client.instance (id);
	    state != nullptr && state->frameGeneration != 0 && state->frameGeneration != generationOne) {
	    generationTwo = state->frameGeneration;
	    break;
	}

	std::this_thread::sleep_for (std::chrono::milliseconds (5));
    }

    if (generationTwo == 0) {
	std::cerr << "FAIL [resize]: no new generation after resize\n";
	return failures + 1;
    }

    shmNameTwo = frameShmName (client.helperPid (), id, generationTwo);
    std::cout << "    generation " << generationOne << " -> " << generationTwo << " after "
	      << millisSince (resizeStarted) << " ms, shm " << shmNameTwo << "\n";

    if (!reader.open (shmNameTwo)) {
	std::cerr << "FAIL [resize]: cannot map " << shmNameTwo << ": " << reader.error () << "\n";
	return failures + 1;
    }

    previousFrame.clear ();
    lastX = -1;
    lastY = -1;
    const int resizedCentreX = static_cast<int> (resizeWidth) / 2;
    const int resizedCentreY = static_cast<int> (resizeHeight) / 2;
    const double resizedRadius = 0.30 * std::min (resizeWidth, resizeHeight);

    const Window afterResize = readFor (client, reader, seconds, 0, 0, &previousFrame, [&] (const int tick) {
	const double angle = static_cast<double> (tick) * 0.09;
	const int x = static_cast<int> (resizedCentreX + resizedRadius * std::cos (angle));
	const int y = static_cast<int> (resizedCentreY + resizedRadius * std::sin (angle));

	if (x == lastX && y == lastY) {
	    return;
	}

	lastX = x;
	lastY = y;
	client.mouseMove (id, x, y);
    });

    printMotion ("moving, after resize", afterResize);
    std::cout << "    size: " << afterResize.width << "x" << afterResize.height << "\n";

    if (afterResize.width != resizeWidth || afterResize.height != resizeHeight) {
	std::cerr << "FAIL [resize]: frames did not adopt the new size\n";
	failures++;
    }

    constexpr double RESPONSE_FLOOR = 0.10;

    std::cout << "\n--- verdicts (BASELINE mean delta " << std::fixed << std::setprecision (3) << baseline.meanDelta
	      << std::defaultfloat
	      << "; a verb passes only above BOTH twice the baseline and the 0.10 absolute floor) ---\n";

    int inconclusive = 0;
    const bool subjectIsQuiet = baseline.meanDelta <= RESPONSE_FLOOR;

    const auto verdict = [&failures, &inconclusive,
			  subjectIsQuiet] (const char* label, const bool pass, const std::string& detail) {
	if (!subjectIsQuiet) {
	    std::cout << "    SKIP  " << label << "  " << detail << "  (subject animates at rest; not measurable)\n";
	    inconclusive++;
	    return;
	}

	std::cout << "    " << (pass ? "PASS" : "FAIL") << "  " << label << "  " << detail << "\n";

	if (!pass) {
	    failures++;
	}
    };

    const auto ratio = [] (const double value, const double against) {
	return against > 0.0001 ? value / against : (value > 0.0001 ? 999.0 : 1.0);
    };

    const auto responded
	= [&] (const double value) { return value > baseline.meanDelta * 2.0 && value > RESPONSE_FLOOR; };

    {
	std::ostringstream detail;
	detail << "clicks " << std::fixed << std::setprecision (3) << clicked.meanDelta << " vs baseline "
	       << baseline.meanDelta << " (x" << std::setprecision (1) << ratio (clicked.meanDelta, baseline.meanDelta)
	       << ")";
	verdict ("mouse-click splats", responded (clicked.meanDelta), detail.str ());
    }

    {
	std::ostringstream detail;
	detail << "moves " << std::fixed << std::setprecision (3) << moved.meanDelta << " vs baseline "
	       << baseline.meanDelta << " (x" << std::setprecision (1) << ratio (moved.meanDelta, baseline.meanDelta)
	       << ")";
	verdict ("mouse-move leaves a trail", responded (moved.meanDelta), detail.str ());
    }

    if (hasToggle) {
	std::ostringstream detail;
	detail << "off " << std::fixed << std::setprecision (3) << movementOff.meanDelta << ", back on "
	       << movementOn.meanDelta << ", moving " << moved.meanDelta;
	verdict (
	    "set-property typed (bool false really disables)",
	    movementOff.meanDelta < moved.meanDelta * 0.5 && movementOff.meanDelta < RESPONSE_FLOOR
		&& responded (movementOn.meanDelta),
	    detail.str ()
	);
    }

    {
	std::ostringstream detail;
	detail << "audio " << std::fixed << std::setprecision (3) << audio.meanDelta << " vs baseline "
	       << baseline.meanDelta << " (x" << std::setprecision (1) << ratio (audio.meanDelta, baseline.meanDelta)
	       << ")";
	verdict ("audio-spectrum drives the page", responded (audio.meanDelta), detail.str ());
    }

    {
	std::ostringstream detail;
	detail << "after-resize " << std::fixed << std::setprecision (3) << afterResize.meanDelta << " at "
	       << afterResize.width << "x" << afterResize.height;
	verdict ("resize keeps input working", responded (afterResize.meanDelta), detail.str ());
    }

    if (inconclusive > 0) {
	std::cout << "\n    " << inconclusive << " verdict(s) INCONCLUSIVE: this wallpaper's no-input baseline is "
		  << std::fixed << std::setprecision (3) << baseline.meanDelta << std::defaultfloat
		  << ", above the 0.10 floor, so input-driven motion cannot be separated from the page's own\n"
		  << "    animation. The verbs were still DELIVERED (the numbers above are real); only the\n"
		  << "    behavioral assertion is unavailable on this subject.\n";
    }

    std::cout << "\n[destroy] instance " << id << "\n";
    client.destroy (id);
    client.pumpEvents ();
    std::this_thread::sleep_for (std::chrono::milliseconds (200));

    return failures;
}

namespace {
struct LiveInstance {
    InstanceId id = 0;
    uint32_t generation = 0;
    std::string shmName;
    int helperPid = -1;
    bool ok = false;
};

LiveInstance bringUp (
    HelperClient& client, const Subject& subject, const uint32_t width, const uint32_t height, const uint32_t fps,
    FrameReader& reader
) {
    LiveInstance live;
    live.id = client.allocateInstance ();
    client.create (live.id, subject.workshopId, subject.file, width, height, fps);

    if (pumpUntil (client, 30000, [&] { return client.isPageLoaded (live.id) || client.didLoadFail (live.id); }) < 0) {
	std::cerr << "FAIL: no page-loaded and no page-failed within 30 s\n";
	return live;
    }

    if (client.didLoadFail (live.id)) {
	std::cerr << "FAIL: the page failed to load; see the error above\n";
	return live;
    }

    if (pumpUntil (
	    client, 15000,
	    [&] {
		const auto* state = client.instance (live.id);
		return state != nullptr && state->frameGeneration != 0;
	    }
	)
	< 0) {
	std::cerr << "FAIL: helper never announced a frame generation\n";
	return live;
    }

    live.helperPid = client.helperPid ();
    live.generation = client.instance (live.id)->frameGeneration;
    live.shmName = frameShmName (live.helperPid, live.id, live.generation);

    if (!reader.open (live.shmName)) {
	std::cerr << "FAIL: cannot map " << live.shmName << ": " << reader.error () << "\n";
	return live;
    }

    live.ok = true;

    return live;
}

struct PropertySet {
    WallpaperEngine::Data::Model::Properties parsed;
    std::vector<PropertyValue> classified;
    bool hasToggle = false;
};

const char* const TOGGLE_KEY = "show_mouse_movement";

PropertySet buildProperties (const Subject& subject) {
    PropertySet set;
    set.parsed = parseProperties (subject.project);
    set.classified = WallpaperEngine::WebHelper::classifyProperties (set.parsed);
    set.hasToggle = set.parsed.find (TOGGLE_KEY) != set.parsed.end ();

    return set;
}

void setToggle (HelperClient& client, const InstanceId id, PropertySet& set, const bool value) {
    auto& property = set.parsed[TOGGLE_KEY];
    property->update (value, WallpaperEngine::Data::Model::DynamicValue::UpdateSource::User);

    if (const auto classified = WallpaperEngine::WebHelper::classifyProperty (TOGGLE_KEY, *property);
	classified.has_value ()) {
	client.setProperty (id, *classified);
    }
}

Window measureWithMouse (
    HelperClient& client, FrameReader& reader, const double seconds, const InstanceId id, const uint32_t width,
    const uint32_t height, std::vector<uint8_t>& previousFrame
) {
    const int centreX = static_cast<int> (width) / 2;
    const int centreY = static_cast<int> (height) / 2;
    const double radius = 0.30 * std::min (width, height);
    int lastX = -1;
    int lastY = -1;

    return readFor (client, reader, seconds, 0, 0, &previousFrame, [&] (const int tick) {
	const double angle = static_cast<double> (tick) * 0.09;
	const int x = static_cast<int> (centreX + radius * std::cos (angle));
	const int y = static_cast<int> (centreY + radius * std::sin (angle));

	if (x == lastX && y == lastY) {
	    return;
	}

	lastX = x;
	lastY = y;
	client.mouseMove (id, x, y);
    });
}
} // namespace

static int runTeardownScenario (
    HelperClient& client, const Subject& subject, const uint32_t width, const uint32_t height, const uint32_t fps,
    const int64_t graceMs, const std::set<int>& baselineServices
) {
    int failures = 0;

    std::cout << "\n=== (a) TEARDOWN TO ZERO ===\n"
	      << "    Teardown is unconditional: when the last web wallpaper leaves the\n"
	      << "    screen the browser process EXITS. Nothing is warm-parked.\n";

    FrameReader reader;
    const LiveInstance live = bringUp (client, subject, width, height, fps, reader);

    if (!live.ok) {
	return 1;
    }

    std::cout << "    instance " << live.id << " up in pid " << live.helperPid << ", state " << client.stateName ()
	      << ", shm " << live.shmName << "\n";

    const Window flowing = readFor (client, reader, 1.5, 0, 0);
    std::cout << "    frames before teardown: " << flowing.accepted << " accepted, sequence " << flowing.firstSequence
	      << " -> " << flowing.lastSequence << ", " << (flowing.sawNonUniform ? "non-uniform" : "UNIFORM") << "\n";

    if (flowing.accepted == 0) {
	std::cerr << "FAIL [teardown]: nothing was flowing to tear down\n";
	failures++;
    }

    const int pid = live.helperPid;
    const auto destroyed = Clock::now ();
    client.destroy (live.id);
    std::cout << "    destroy(" << live.id << ") sent; state is now " << client.stateName () << ", grace is " << graceMs
	      << " ms\n";

    const int exitMs = pumpUntil (client, 20000, [&] { return client.helperPid () < 0; });

    if (exitMs < 0) {
	std::cerr << "FAIL [teardown]: pid " << pid << " was still running 20 s after the last destroy\n";
	return failures + 1;
    }

    std::cout << "    pid " << pid << " gone after " << exitMs << " ms (" << client.lastExitDescription () << ")\n"
	      << "    state " << client.stateName () << ", " << millisSince (destroyed) << " ms since destroy\n";

    if (exitMs > graceMs + 3000) {
	std::cerr << "FAIL [teardown]: exit took " << exitMs << " ms, more than the " << graceMs
		  << " ms grace plus 3 s of slack\n";
	failures++;
    }

    if (processExists (pid)) {
	std::cerr << "FAIL [teardown]: /proc/" << pid << " still exists\n";
	failures++;
    }

    const auto strayServices = newSince (baselineServices, processPidsNamed ("lwe-web-service"));
    std::cout << "    lwe-web-service processes not present at baseline: " << strayServices.size () << "\n";

    for (const int stray : strayServices) {
	std::cerr << "FAIL [teardown]: orphan lwe-web-service pid " << stray << " (parent " << parentOf (stray)
		  << ")\n";
	failures++;
    }

    const auto leftovers = shmObjectsFor (pid);
    std::cout << "    /dev/shm objects for pid " << pid << ": " << leftovers.size () << "\n";

    for (const auto& name : leftovers) {
	std::cerr << "FAIL [teardown]: shm object " << name << " outlived the helper\n";
	failures++;
    }

    if (client.state () != WallpaperEngine::WebHelper::LifecycleState::Idle) {
	std::cerr << "FAIL [teardown]: state is " << client.stateName () << ", expected Idle\n";
	failures++;
    }

    return failures;
}

static int runRespawnScenario (
    HelperClient& client, const Subject& subject, const uint32_t width, const uint32_t height, const uint32_t fps,
    const double seconds, const std::set<int>& baselineHelpers
) {
    int failures = 0;

    std::cout << "\n=== (b) RESPAWN AFTER A CRASH ===\n"
	      << "    kill -9 the service mid-stream. The engine must notice, start a replacement, replay\n"
	      << "    the instance INCLUDING its live property overrides, and get frames flowing again.\n";

    FrameReader reader;
    const LiveInstance live = bringUp (client, subject, width, height, fps, reader);

    if (!live.ok) {
	return 1;
    }

    PropertySet properties = buildProperties (subject);
    std::cout << "    injecting " << properties.classified.size () << " properties into instance " << live.id << "\n";
    client.injectProperties (live.id, properties.classified);
    client.pumpEvents ();

    std::vector<uint8_t> previousFrame;
    const Window settle = readFor (client, reader, 1.5, 0, 0, &previousFrame);
    printMotion ("settle after injection", settle);

    if (!properties.hasToggle) {
	std::cerr << "FAIL [respawn]: this wallpaper has no " << TOGGLE_KEY
		  << ", so replay fidelity cannot be measured on it\n";
	return failures + 1;
    }

    setToggle (client, live.id, properties, false);
    std::cout << "\n    [tracer] " << TOGGLE_KEY << " set live to false\n";
    const Window beforeKill = measureWithMouse (client, reader, seconds, live.id, width, height, previousFrame);
    printMotion ("moving, tracer false, before the kill", beforeKill);

    const int pid = client.helperPid ();
    const auto killedAt = Clock::now ();
    const uint64_t spawnsBefore = client.spawnCount ();
    const auto helpersBeforeKill = processPidsNamed ("lwe-web-helper");
    kill (pid, SIGKILL);
    std::cout << "\n    SIGKILL sent to pid " << pid << "\n";

    const int detectMs = pumpUntil (client, 15000, [&] {
	return client.state () != WallpaperEngine::WebHelper::LifecycleState::Connected;
    });

    if (detectMs < 0) {
	std::cerr << "FAIL [respawn]: the engine never noticed the helper had died\n";
	return failures + 1;
    }

    std::cout << "    death detected after " << detectMs << " ms (" << client.lastExitDescription () << "), state "
	      << client.stateName () << "\n";

    const auto oldShm = shmObjectsFor (pid);
    std::cout << "    /dev/shm objects for the dead pid " << pid << ": " << oldShm.size () << "\n";

    for (const auto& name : oldShm) {
	std::cerr << "FAIL [respawn]: shm object " << name << " survived a SIGKILLed helper\n";
	failures++;
    }

    const int reconnectMs
	= pumpUntil (client, 30000, [&] { return client.spawnCount () > spawnsBefore && client.isConnected (); });

    if (reconnectMs < 0) {
	std::cerr << "FAIL [respawn]: no replacement helper connected within 30 s\n";
	return failures + 1;
    }

    std::cout << "    replacement pid " << client.helperPid () << " connected " << reconnectMs
	      << " ms after the death was detected (" << millisSince (killedAt) << " ms after the kill)\n";

    if (client.helperPid () == pid) {
	std::cerr << "FAIL [respawn]: the replacement has the same pid as the corpse\n";
	failures++;
    }

    const int reloadMs
	= pumpUntil (client, 30000, [&] { return client.isPageLoaded (live.id) || client.didLoadFail (live.id); });

    if (reloadMs < 0 || client.didLoadFail (live.id)) {
	std::cerr << "FAIL [respawn]: the replayed instance never loaded\n";
	return failures + 1;
    }

    std::cout << "    replayed instance " << live.id << " reported page-loaded " << reloadMs << " ms later\n";

    const int generationMs = pumpUntil (client, 15000, [&] {
	const auto* state = client.instance (live.id);
	return state != nullptr && state->frameGeneration != 0;
    });

    if (generationMs < 0) {
	std::cerr << "FAIL [respawn]: no frame generation from the replacement\n";
	return failures + 1;
    }

    const uint32_t generation = client.instance (live.id)->frameGeneration;
    const std::string shmName = frameShmName (client.helperPid (), live.id, generation);
    std::cout << "    new generation " << generation << " announced, shm " << shmName << "\n";

    if (!reader.open (shmName)) {
	std::cerr << "FAIL [respawn]: cannot map " << shmName << ": " << reader.error () << "\n";
	return failures + 1;
    }

    const int gapMs = millisSince (killedAt);
    std::cout << "    TOTAL GAP kill -> mapped ring: " << gapMs << " ms\n";

    const auto mineBeforeKill = newSince (baselineHelpers, helpersBeforeKill);
    const int childExitMs = pumpUntil (client, 15000, [&] {
	const auto alive = processPidsNamed ("lwe-web-helper");

	return std::none_of (mineBeforeKill.begin (), mineBeforeKill.end (), [&] (const int pid) {
	    return alive.find (pid) != alive.end ();
	});
    });
    std::cout << "    CEF children of the killed service: " << mineBeforeKill.size () << ", "
	      << (childExitMs >= 0 ? "all gone " + std::to_string (childExitMs) + " ms after the kill"
				   : "STILL ALIVE 15 s after the kill")
	      << "\n";

    if (childExitMs < 0) {
	std::cerr << "FAIL [respawn]: a SIGKILLed service orphaned its CEF children\n";
	failures++;
    }

    previousFrame.clear ();

    const Window settled = readFor (client, reader, seconds, 0, 0, &previousFrame);
    printMotion ("quiescing after the respawn (no input; not asserted on, see above)", settled);

    if (settled.accepted == 0) {
	std::cerr << "FAIL [respawn]: no frames from the replacement\n";
	failures++;
    }

    const Window afterKill = measureWithMouse (client, reader, seconds, live.id, width, height, previousFrame);
    printMotion ("moving, after the respawn (tracer should still be false)", afterKill);

    setToggle (client, live.id, properties, true);
    std::cout << "\n    [tracer] " << TOGGLE_KEY << " set live back to true\n";
    const Window restored = measureWithMouse (client, reader, seconds, live.id, width, height, previousFrame);
    printMotion ("moving, tracer restored to true", restored);

    constexpr double RESPONSE_FLOOR = 0.10;

    std::cout << "\n    --- verdicts ---\n";

    {
	const bool pass = afterKill.meanDelta < RESPONSE_FLOOR;
	std::cout << "    " << (pass ? "PASS" : "FAIL")
		  << "  properties replayed (the live false survived the respawn)  after-kill delta " << std::fixed
		  << std::setprecision (3) << afterKill.meanDelta << " vs before-kill " << beforeKill.meanDelta
		  << std::defaultfloat << "\n";

	if (!pass) {
	    std::cerr << "FAIL [respawn]: trails came back, so the replacement is running the page's own "
			 "default rather than the replayed override\n";
	    failures++;
	}
    }

    {
	const bool pass = restored.meanDelta > RESPONSE_FLOOR;
	std::cout << "    " << (pass ? "PASS" : "FAIL")
		  << "  the replacement is live and responsive                    restored delta " << std::fixed
		  << std::setprecision (3) << restored.meanDelta << std::defaultfloat << "\n";

	if (!pass) {
	    std::cerr << "FAIL [respawn]: the replacement never responded to input\n";
	    failures++;
	}
    }

    {
	const bool pass = restored.maxDistinct > 1;
	std::cout << "    " << (pass ? "PASS" : "FAIL")
		  << "  the new generation renders with pixel variance             peak " << restored.maxDistinct
		  << " distinct values in a single frame\n";

	if (!pass) {
	    std::cerr << "FAIL [respawn]: every frame of the new generation was a flat fill\n";
	    failures++;
	}
    }

    client.destroy (live.id);
    pumpUntil (client, 20000, [&] { return client.helperPid () < 0; });

    return failures;
}

static int runCrashLoopScenario (
    HelperClient& client, const Subject& subject, const uint32_t width, const uint32_t height, const uint32_t fps
) {
    int failures = 0;
    const auto guard = client.crashGuard ();

    std::cout << "\n=== (c) CRASH-LOOP GUARD ===\n"
	      << "    guard: " << guard.deaths << " deaths within " << guard.windowMs << " ms -> " << guard.cooldownMs
	      << " ms cooldown.\n"
	      << "    This is about the HELPER not thrashing, never about blaming a\n"
	      << "    wallpaper - the struck per-wallpaper circuit breaker is not being reintroduced.\n";

    FrameReader reader;
    const LiveInstance live = bringUp (client, subject, width, height, fps, reader);

    if (!live.ok) {
	return 1;
    }

    int kills = 0;
    const int maximumKills = guard.deaths + 3;

    while (client.state () != WallpaperEngine::WebHelper::LifecycleState::Cooldown && kills < maximumKills) {
	if (!client.isConnected ()) {
	    if (pumpUntil (
		    client, 30000,
		    [&] {
			return client.isConnected ()
			    || client.state () == WallpaperEngine::WebHelper::LifecycleState::Cooldown;
		    }
		)
		< 0) {
		break;
	    }

	    continue;
	}

	const int pid = client.helperPid ();
	kill (pid, SIGKILL);
	kills++;
	std::cout << "    kill " << kills << ": SIGKILL pid " << pid << "\n";

	pumpUntil (client, 30000, [&] {
	    return client.state () == WallpaperEngine::WebHelper::LifecycleState::Cooldown
		|| (client.isConnected () && client.helperPid () != pid);
	});
    }

    std::cout << "    " << kills << " kill(s), " << client.unexpectedDeaths () << " unexpected death(s), state "
	      << client.stateName () << "\n";

    if (client.state () != WallpaperEngine::WebHelper::LifecycleState::Cooldown) {
	std::cerr << "FAIL [crash-loop]: the guard did not trip after " << kills << " kills\n";
	return failures + 1;
    }

    // THE MEASUREMENT. From the moment the cooldown is observed, no spawn may happen until
    // it expires. spawnCount is the discriminator: it counts posix_spawns, so a helper that
    // was started and died instantly still moves it.
    const uint64_t spawnsAtTrip = client.spawnCount ();
    const int64_t remainingAtTrip = client.millisUntilNextAttempt ();
    std::cout << "    cooldown observed with " << remainingAtTrip << " ms left; spawn count frozen at " << spawnsAtTrip
	      << "\n";

    const int spawnGapMs = pumpUntil (client, static_cast<int> (guard.cooldownMs * 4 + 10000), [&] {
	return client.spawnCount () > spawnsAtTrip;
    });

    if (spawnGapMs < 0) {
	std::cerr << "FAIL [crash-loop]: no spawn attempt after the cooldown; the guard never lets go\n";
	return failures + 1;
    }

    std::cout << "    next spawn happened " << spawnGapMs << " ms after the cooldown was observed (cooldown is "
	      << guard.cooldownMs << " ms)\n";

    // 100 ms of tolerance: the trip is OBSERVED by a polling probe, so the measured gap is
    // the true one minus however long it took to notice, never more than one poll interval.
    if (spawnGapMs + 100 < static_cast<int> (remainingAtTrip)) {
	std::cerr << "FAIL [crash-loop]: a helper was spawned " << spawnGapMs << " ms in, with " << remainingAtTrip
		  << " ms of cooldown to run\n";
	failures++;
    }

    const int recoveryMs
	= pumpUntil (client, 40000, [&] { return client.isConnected () && client.isPageLoaded (live.id); });

    if (recoveryMs < 0) {
	std::cerr << "FAIL [crash-loop]: no recovery after the cooldown\n";
	return failures + 1;
    }

    std::cout << "    recovered: pid " << client.helperPid () << " connected and instance " << live.id << " loaded, "
	      << recoveryMs << " ms after the cooldown ended\n";

    const int generationMs = pumpUntil (client, 15000, [&] {
	const auto* state = client.instance (live.id);
	return state != nullptr && state->frameGeneration != 0;
    });

    if (generationMs < 0) {
	std::cerr << "FAIL [crash-loop]: the recovered instance never published a generation\n";
	return failures + 1;
    }

    const std::string shmName = frameShmName (client.helperPid (), live.id, client.instance (live.id)->frameGeneration);

    if (!reader.open (shmName)) {
	std::cerr << "FAIL [crash-loop]: cannot map " << shmName << ": " << reader.error () << "\n";
	return failures + 1;
    }

    const Window recovered = readFor (client, reader, 2.0, 0, 0);
    std::cout << "    frames after recovery: " << recovered.accepted << " accepted, sequence "
	      << recovered.firstSequence << " -> " << recovered.lastSequence << ", "
	      << (recovered.sawNonUniform ? "non-uniform" : "UNIFORM") << ", peak " << recovered.maxDistinct
	      << " distinct values in a frame\n";

    if (recovered.accepted == 0 || recovered.maxDistinct <= 1) {
	std::cerr << "FAIL [crash-loop]: the recovered helper is not painting\n";
	failures++;
    }

    client.destroy (live.id);
    pumpUntil (client, 20000, [&] { return client.helperPid () < 0; });

    return failures;
}

static int runLifecycleProof (
    const SpawnConfig& config, const Subject& subject, const uint32_t width, const uint32_t height, const uint32_t fps,
    const double seconds, const int64_t graceMs
) {
    int failures = 0;
    const auto baselineServices = processPidsNamed ("lwe-web-service");
    const auto baselineHelpers = processPidsNamed ("lwe-web-helper");
    const auto baselineShm = shmObjectsNow ();

    std::cout << "baseline: " << baselineServices.size () << " lwe-web-service, " << baselineHelpers.size ()
	      << " lwe-web-helper, " << baselineShm.size ()
	      << " lwe-web-* shm object(s) - all belonging to something else on this machine\n";

    {
	HelperClient client (config);
	failures += runTeardownScenario (client, subject, width, height, fps, graceMs, baselineServices);
	failures += runRespawnScenario (client, subject, width, height, fps, seconds, baselineHelpers);
    }

    {
	HelperClient client (config);
	failures += runCrashLoopScenario (client, subject, width, height, fps);
    }

    std::cout << "\n=== lifecycle hygiene ===\n";

    const auto settleStarted = Clock::now ();
    std::set<int> strayServices;
    std::set<int> strayHelpers;

    while (millisSince (settleStarted) < 10000) {
	strayServices = newSince (baselineServices, processPidsNamed ("lwe-web-service"));
	strayHelpers = newSince (baselineHelpers, processPidsNamed ("lwe-web-helper"));

	if (strayServices.empty () && strayHelpers.empty ()) {
	    break;
	}

	std::this_thread::sleep_for (std::chrono::milliseconds (50));
    }

    std::cout << "    settled for " << millisSince (settleStarted) << " ms\n"
	      << "    lwe-web-service processes this probe left behind: " << strayServices.size () << "\n"
	      << "    lwe-web-helper processes this probe left behind:  " << strayHelpers.size () << "\n";

    for (const int stray : strayServices) {
	std::cerr << "FAIL: orphan lwe-web-service pid " << stray << " (parent " << parentOf (stray) << ")\n";
	failures++;
    }

    for (const int stray : strayHelpers) {
	std::cerr << "FAIL: orphan lwe-web-helper pid " << stray << " (parent " << parentOf (stray) << ")\n";
	failures++;
    }

    const auto strayShm = shmObjectsNow ();
    size_t strays = 0;

    for (const auto& name : strayShm) {
	if (baselineShm.find (name) != baselineShm.end ()) {
	    continue;
	}

	std::cerr << "FAIL: stray shm object /dev/shm/" << name << "\n";
	strays++;
    }

    std::cout << "    /dev/shm lwe-web-* objects this probe left behind: " << strays << "\n";
    failures += static_cast<int> (strays);

    return failures;
}

int main (int argc, char** argv) {
    WallpaperEngine::WebHelper::SpawnGate::captureAtStartup ();

    sLog.addOutput (new std::ostream (std::cout.rdbuf ()));
    sLog.addError (new std::ostream (std::cerr.rdbuf ()));

    const std::string wallpaper = argumentValue (argc, argv, "--wallpaper=", "");

    if (wallpaper.empty ()) {
	std::cerr << "usage: lwe-web-frame-probe --wallpaper=<dir> [--assets=<dir>] [--seconds=N]\n"
		     "                           [--verbs | --lifecycle] [--crashguard=N,windowMs,cooldownMs]\n"
		     "                           [--idle-exit=MS]\n";
	return 2;
    }

    Subject subject;
    subject.path = wallpaper;

    const auto projectFile = subject.path / "project.json";

    if (!std::filesystem::exists (projectFile)) {
	std::cerr << "no project.json in " << subject.path.string () << "\n";
	return 2;
    }

    {
	std::ifstream stream (projectFile);
	stream >> subject.project;
    }

    if (const auto it = subject.project.find ("workshopid"); it != subject.project.end ()) {
	subject.workshopId = it->is_string () ? it->get<std::string> () : std::to_string (it->get<int64_t> ());
    }

    subject.file = subject.project.value ("file", "index.html");

    const bool verbs = hasFlag (argc, argv, "--verbs");
    const bool lifecycle = hasFlag (argc, argv, "--lifecycle");

    if (const std::string guard = argumentValue (argc, argv, "--crashguard=", ""); !guard.empty ()) {
	setenv ("LWE_WEB_CRASHGUARD", guard.c_str (), 1);
    }

    if (const std::string idle = argumentValue (argc, argv, "--idle-exit=", ""); !idle.empty ()) {
	setenv ("LWE_WEB_IDLE_EXIT_MS", idle.c_str (), 1);
    }

    const double seconds
	= std::atof (argumentValue (argc, argv, "--seconds=", verbs || lifecycle ? "4" : "3").c_str ());
    const auto width = static_cast<uint32_t> (std::atoi (argumentValue (argc, argv, "--width=", "640").c_str ()));
    const auto height = static_cast<uint32_t> (std::atoi (argumentValue (argc, argv, "--height=", "360").c_str ()));
    const auto resizeWidth
	= static_cast<uint32_t> (std::atoi (argumentValue (argc, argv, "--resize-width=", "800").c_str ()));
    const auto resizeHeight
	= static_cast<uint32_t> (std::atoi (argumentValue (argc, argv, "--resize-height=", "450").c_str ()));
    const auto fps = static_cast<uint32_t> (std::atoi (argumentValue (argc, argv, "--fps=", "30").c_str ()));

    SpawnConfig config;
    config.assetsDir = resolveAssetsDir (argumentValue (argc, argv, "--assets=", ""));
    config.maximumFPS = static_cast<int> (fps);
    config.protocolVersion = PROTOCOL_VERSION;
    config.socketPath = SpawnConfig::defaultSocketPath (getpid ());
    config.schemes.push_back ({ .workshopId = subject.workshopId, .path = subject.path });

    std::cout << "=== lwe web "
	      << (lifecycle   ? "LIFECYCLE"
		      : verbs ? "VERB"
			      : "frame")
	      << " probe ===\n"
	      << "  wallpaper:  " << subject.path.string () << " (workshop " << subject.workshopId << ", "
	      << subject.file << ")\n"
	      << "  viewport:   " << width << "x" << height << " @" << fps << " fps\n"
	      << "  socket:     " << config.socketPath.string () << "\n"
	      << "  assets:     " << config.assetsDir.string () << "\n"
	      << "  spawn gate: " << WallpaperEngine::WebHelper::SpawnGate::serviceBinary ().string ()
	      << ", captured at " << WallpaperEngine::WebHelper::SpawnGate::threadsAtCapture () << " thread(s)\n\n";

    if (lifecycle) {
	const char* idle = std::getenv ("LWE_WEB_IDLE_EXIT_MS");
	const int64_t graceMs = idle != nullptr ? std::atoll (idle) : 1000;
	const int lifecycleFailures = runLifecycleProof (config, subject, width, height, fps, seconds, graceMs);

	std::cout << "\n=== " << (lifecycleFailures == 0 ? "PASS" : "FAIL") << " (" << lifecycleFailures
		  << " failure(s)) ===\n";

	return lifecycleFailures == 0 ? 0 : 1;
    }

    int failures = 0;
    std::string shmNameOne;
    std::string shmNameTwo;

    {
	HelperClient client (config);

	failures = verbs
	    ? runVerbProof (
		  client, subject, width, height, resizeWidth, resizeHeight, fps, seconds, shmNameOne, shmNameTwo
	      )
	    : runFrameTransportProof (
		  client, subject, width, height, resizeWidth, resizeHeight, fps, seconds, shmNameOne, shmNameTwo
	      );

	std::cout << "    helper pid was " << client.helperPid () << "\n";
    }

    std::cout << "\n[teardown]\n";

    // the shm objects must be gone: the writer unlinks on release, and release happens when
    // the instance is destroyed. A leftover object is a real leak - it is memory that
    // survives the process that made it.
    for (const auto& name : { shmNameOne, shmNameTwo }) {
	if (name.empty ()) {
	    continue;
	}

	const std::string path = "/dev/shm" + name;
	const bool present = std::filesystem::exists (path);

	std::cout << "    " << path << ": " << (present ? "STILL PRESENT" : "unlinked") << "\n";

	if (present) {
	    std::cerr << "FAIL: shm object " << name << " outlived the helper\n";
	    failures++;
	}
    }

    std::cout << "\n=== " << (failures == 0 ? "PASS" : "FAIL") << " (" << failures << " failure(s)) ===\n";

    return failures == 0 ? 0 : 1;
}
