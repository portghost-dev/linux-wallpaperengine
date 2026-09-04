#pragma once

#include "ApplicationContext.h"

#include <atomic>

namespace WallpaperEngine::Application {
/**
 * Represents current application state
 */
class ApplicationState {
public:
    struct {
	std::atomic<bool> keepRunning;
    } general {};

    struct {
	bool enabled;
	int volume;
    } audio {};

    struct {
	bool enabled;
    } mouse {};
};
} // namespace WallpaperEngine::Application