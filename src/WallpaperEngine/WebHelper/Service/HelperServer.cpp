#include "HelperServer.h"

#include "WallpaperEngine/Logging/Log.h"

#include <chrono>
#include <cstdlib>

using namespace WallpaperEngine::WebHelper::Service;

namespace {
int64_t steadyNowMs () {
    return std::chrono::duration_cast<std::chrono::milliseconds> (std::chrono::steady_clock::now ().time_since_epoch ())
	.count ();
}
} // namespace

HelperServer::HelperServer (const SpawnConfig& config) : m_config (config), m_listener (config.socketPath) { }

int64_t HelperServer::idleExitGraceMs () {
    // read once: the value has to be stable for the whole life of the process or the log
    // line that reports it stops describing what actually happened
    static const int64_t grace = [] () -> int64_t {
	const char* raw = std::getenv ("LWE_WEB_IDLE_EXIT_MS");

	if (raw == nullptr || *raw == '\0') {
	    return HelperServer::DEFAULT_IDLE_EXIT_MS;
	}

	try {
	    const int64_t parsed = std::stoll (raw);

	    return parsed >= 0 ? parsed : HelperServer::DEFAULT_IDLE_EXIT_MS;
	} catch (const std::exception&) {
	    return HelperServer::DEFAULT_IDLE_EXIT_MS;
	}
    }();

    return grace;
}

void HelperServer::updateIdleTimer () {
    if (!this->m_instances.empty ()) {
	if (this->m_idleSinceMs != 0) {
	    sLog.out ("web-service: idle exit canceled, an instance arrived within the grace window");
	    this->m_idleSinceMs = 0;
	}

	return;
    }

    if (this->m_engineWasConnected && this->m_idleSinceMs == 0) {
	this->m_idleSinceMs = steadyNowMs ();
	sLog.out (
	    "web-service: last instance destroyed; exiting in ", HelperServer::idleExitGraceMs (),
	    " ms unless another arrives"
	);
    }
}

bool HelperServer::shouldExitIdle () const {
    if (this->m_idleSinceMs == 0) {
	return false;
    }

    return steadyNowMs () - this->m_idleSinceMs >= HelperServer::idleExitGraceMs ();
}

int64_t HelperServer::millisUntilIdleExit () const {
    if (this->m_idleSinceMs == 0) {
	return -1;
    }

    const int64_t remaining = HelperServer::idleExitGraceMs () - (steadyNowMs () - this->m_idleSinceMs);

    return remaining > 0 ? remaining : 0;
}

bool HelperServer::start () {
    if (!this->m_listener.listen ()) {
	this->m_error = this->m_listener.error ();
	return false;
    }

    sLog.out ("web-service: listening on ", this->m_config.socketPath.string ());

    return true;
}

void HelperServer::tick () {
    if (!this->m_channel.has_value ()) {
	if (auto accepted = this->m_listener.accept (); accepted.has_value ()) {
	    this->m_channel = std::move (accepted);
	    this->m_engineWasConnected = true;
	    sLog.out ("web-service: engine connected");
	}
    }

    if (!this->m_channel.has_value ()) {
	return;
    }

    this->m_channel->flush ();

    for (const auto& message : this->m_channel->receive ()) {
	this->handle (message);
    }

    if (!this->m_channel->isOpen ()) {
	sLog.out ("web-service: engine disconnected (", this->m_channel->error (), ")");
	this->m_channel.reset ();
	this->m_instances.clear ();
	return;
    }

    this->emitPageLoadedEvents ();
    this->emitFrameReadyEvents ();

    this->updateIdleTimer ();
}

WallpaperEngine::WebHelper::Service::WebInstance* HelperServer::find (const InstanceId id) {
    const auto it = this->m_instances.find (id);

    if (it == this->m_instances.end ()) {
	sLog.error ("web-service: command for unknown instance ", id);
	return nullptr;
    }

    return it->second.get ();
}

void HelperServer::handle (const Message& message) {
    if (!isCommand (message.type)) {
	sLog.error (
	    "web-service: ignoring an event received on the helper side, type ", static_cast<int> (message.type)
	);
	return;
    }

    PayloadReader reader (message.payload);
    const InstanceId id = reader.u32 ();

    switch (message.type) {
	case MessageType::Create:
	    {
		const uint32_t width = reader.u32 ();
		const uint32_t height = reader.u32 ();
		const uint32_t framerate = reader.u32 ();
		const std::string workshopId = reader.str ();
		const std::string file = reader.str ();

		if (!reader.ok ()) {
		    sLog.error ("web-service: malformed create command");
		    return;
		}

		// the shm ring is width*height*4 per frame; unchecked dimensions are a
		// tmpfs exhaustion and go negative through the int casts below
		if (width == 0 || height == 0 || width > 16384 || height > 16384) {
		    sLog.error ("web-service: rejecting create with dimensions ", width, "x", height);
		    return;
		}

		auto instance = std::make_unique<WebInstance> (
		    id, workshopId, file, static_cast<int> (width), static_cast<int> (height),
		    static_cast<int> (framerate)
		);

		if (!instance->open ()) {
		    sLog.error ("web-service: could not open instance ", id);
		    return;
		}

		this->m_instances[id] = std::move (instance);
		break;
	    }
	case MessageType::Resize:
	    {
		const uint32_t width = reader.u32 ();
		const uint32_t height = reader.u32 ();

		if (!reader.ok ()) {
		    sLog.error ("web-service: malformed resize command");
		    return;
		}

		if (width == 0 || height == 0 || width > 16384 || height > 16384) {
		    sLog.error ("web-service: rejecting resize to ", width, "x", height);
		    return;
		}

		if (WebInstance* instance = this->find (id); instance != nullptr) {
		    instance->setSize (static_cast<int> (width), static_cast<int> (height));
		}

		break;
	    }
	case MessageType::MouseMove:
	    {
		const int32_t x = reader.i32 ();
		const int32_t y = reader.i32 ();

		if (!reader.ok ()) {
		    sLog.error ("web-service: malformed mouse-move command");
		    return;
		}

		if (WebInstance* instance = this->find (id); instance != nullptr) {
		    instance->mouseMove (x, y);
		}

		break;
	    }
	case MessageType::MouseClick:
	    {
		const int32_t x = reader.i32 ();
		const int32_t y = reader.i32 ();
		const auto button = static_cast<MouseButton> (reader.u8 ());
		const bool released = reader.u8 () != 0;

		if (!reader.ok ()) {
		    sLog.error ("web-service: malformed mouse-click command");
		    return;
		}

		if (WebInstance* instance = this->find (id); instance != nullptr) {
		    instance->mouseClick (x, y, button, released);
		}

		break;
	    }
	case MessageType::InjectProperties:
	    {
		const uint32_t count = reader.u32 ();
		std::vector<PropertyValue> properties;

		for (uint32_t i = 0; i < count && reader.ok (); i++) {
		    PropertyValue property;

		    if (!readProperty (reader, property)) {
			sLog.error ("web-service: unknown property kind in inject-properties");
			return;
		    }

		    properties.push_back (std::move (property));
		}

		if (!reader.ok ()) {
		    sLog.error ("web-service: malformed inject-properties command");
		    return;
		}

		if (WebInstance* instance = this->find (id); instance != nullptr) {
		    instance->injectProperties (properties);
		}

		break;
	    }
	case MessageType::SetProperty:
	    {
		PropertyValue property;
		const bool decoded = readProperty (reader, property);

		if (!decoded || !reader.ok ()) {
		    sLog.error ("web-service: malformed set-property command");
		    return;
		}

		if (WebInstance* instance = this->find (id); instance != nullptr) {
		    instance->setProperty (property);
		}

		break;
	    }
	case MessageType::AudioSpectrum:
	    {
		float bands[AUDIO_BANDS] = {};
		reader.bytes (bands, sizeof (bands));

		if (!reader.ok ()) {
		    sLog.error ("web-service: malformed audio-spectrum command");
		    return;
		}

		if (WebInstance* instance = this->find (id); instance != nullptr) {
		    instance->injectAudio (bands);
		}

		break;
	    }
	case MessageType::Destroy:
	    {
		if (!reader.ok ()) {
		    sLog.error ("web-service: malformed destroy command");
		    return;
		}

		this->m_instances.erase (id);
		break;
	    }
	default:
	    sLog.error ("web-service: unknown command type ", static_cast<int> (message.type));
	    break;
    }
}

void HelperServer::emitPageLoadedEvents () {
    if (!this->m_channel.has_value ()) {
	return;
    }

    for (const auto& [id, instance] : this->m_instances) {
	if (instance->didLoadFail ()) {
	    if (instance->pageFailedEventSent ()) {
		continue;
	    }

	    sLog.error (
		"web-service: instance ", id, " FAILED to load: ", instance->loadErrorText (), " (code ",
		instance->loadErrorCode (), ", url ", instance->failedUrl (), ")"
	    );
	    this->m_channel->send (
		encodePageFailed (id, instance->loadErrorCode (), instance->loadErrorText (), instance->failedUrl ())
	    );
	    instance->markPageFailedEventSent ();
	    continue;
	}

	if (instance->pageLoadedEventSent () || !instance->isPageLoaded ()) {
	    continue;
	}

	// polled rather than pushed from BrowserClient::OnLoadEnd: that callback runs on
	// CEF's UI thread, and the socket belongs to this loop
	this->m_channel->send (encodePageLoaded (id));
	instance->markPageLoadedEventSent ();
    }
}

void HelperServer::emitFrameReadyEvents () {
    if (!this->m_channel.has_value ()) {
	return;
    }

    for (const auto& [id, instance] : this->m_instances) {
	if (!instance->hasUnannouncedGeneration ()) {
	    continue;
	}

	this->m_channel->send (encodeFrameReady (
	    id, instance->frameGeneration (), instance->frameSequence (), static_cast<uint32_t> (instance->getWidth ()),
	    static_cast<uint32_t> (instance->getHeight ())
	));
	instance->markGenerationAnnounced ();
    }
}
