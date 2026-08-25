#include "NullMediaSource.h"

using namespace WallpaperEngine::WebHelper::Service;

// The interval is inert - update() is overridden to do nothing - but the base class has no
// default constructor, so something has to be passed. A large value makes the intent
// obvious to anyone who later removes the override.
NullMediaSource::NullMediaSource () : MediaSource (std::chrono::hours (24)) { }
