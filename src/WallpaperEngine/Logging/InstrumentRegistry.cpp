#include "InstrumentRegistry.h"

#include <atomic>
#include <cstdlib>
#include <cstring>

namespace WallpaperEngine::Logging {
namespace {
    /**
     * The instruments the API may toggle. A fixed table rather than a map so the read path is
     * a pointer compare over a handful of entries with no allocation and no lock - these calls
     * sit inside per-frame code.
     *
     * Only PURE LOG GATES belong here. A switch that changes what gets built (texture format,
     * FBO pooling, shader selection) must keep reading the environment, because flipping it
     * mid-process would either do nothing or disagree with objects already constructed.
     */
    struct Entry {
	const char* name;
	std::atomic<bool> on;
	std::atomic<std::uint32_t> epoch;
    };

    // Kept in sync with the companion control surface's instrument list. Adding a name here does NOT convert an
    // instrument on its own - the read site must also consult the registry instead of getenv,
    // and must reset its latched state on an epoch change.
    Entry g_entries[] = {
	{ "LWE_PARTSTATS", { false }, { 0 } },
	{ "LWE_TWINKLEPROBE", { false }, { 0 } },
	{ "LWE_ROPETRAILPROBE", { false }, { 0 } },
    };

    Entry* find (const char* name) {
	if (name == nullptr) {
	    return nullptr;
	}
	for (auto& e : g_entries) {
	    if (std::strcmp (e.name, name) == 0) {
		return &e;
	    }
	}
	return nullptr;
    }
} // namespace

bool instrumentOn (const char* name) {
    const Entry* e = find (name);
    return e != nullptr && e->on.load (std::memory_order_relaxed);
}

std::uint32_t instrumentEpoch (const char* name) {
    const Entry* e = find (name);
    return e == nullptr ? 0 : e->epoch.load (std::memory_order_relaxed);
}

bool instrumentSet (const std::string& name, const bool enabled) {
    Entry* e = find (name.c_str ());
    if (e == nullptr) {
	return false;
    }
    // Bump the epoch only on a genuine off->on edge. Re-enabling something already on must
    // NOT wipe a run in progress - an operator pressing the same switch twice would otherwise
    // silently reset the window they were reading.
    const bool was = e->on.exchange (enabled, std::memory_order_relaxed);
    if (enabled && !was) {
	e->epoch.fetch_add (1, std::memory_order_relaxed);
    }
    return true;
}

void instrumentSeedFromEnv () {
    for (auto& e : g_entries) {
	const bool on = getenv (e.name) != nullptr;
	e.on.store (on, std::memory_order_relaxed);
	// Seeding counts as the first enable, so a consumer latching at epoch 0 and comparing
	// against 1 clears once at startup rather than treating the launch state as stale.
	if (on) {
	    e.epoch.store (1, std::memory_order_relaxed);
	}
    }
}

std::string instrumentsEnabled () {
    std::string out;
    for (const auto& e : g_entries) {
	if (e.on.load (std::memory_order_relaxed)) {
	    if (!out.empty ()) {
		out += ",";
	    }
	    out += e.name;
	}
    }
    return out;
}

bool instrumentKnown (const std::string& name) { return find (name.c_str ()) != nullptr; }
} // namespace WallpaperEngine::Logging
