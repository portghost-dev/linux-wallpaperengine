#include "HelperClient.h"

#include "SpawnGate.h"
#include "WallpaperEngine/Logging/Log.h"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <thread>

#include <csignal>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

using namespace WallpaperEngine::WebHelper;

namespace {
int64_t steadyNowMs () {
    return std::chrono::duration_cast<std::chrono::milliseconds> (std::chrono::steady_clock::now ().time_since_epoch ())
	.count ();
}

std::string describeExit (const int status) {
    if (WIFEXITED (status)) {
	return "exited with status " + std::to_string (WEXITSTATUS (status));
    }

    if (WIFSIGNALED (status)) {
	const int signalNumber = WTERMSIG (status);
	const char* name = strsignal (signalNumber);

	return "killed by signal " + std::to_string (signalNumber) + " (" + (name != nullptr ? name : "?") + ")";
    }

    return "ended in an unrecognized way (raw status " + std::to_string (status) + ")";
}
} // namespace

const char* WallpaperEngine::WebHelper::lifecycleStateName (const LifecycleState state) {
    switch (state) {
	case LifecycleState::Idle:
	    return "Idle";
	case LifecycleState::Starting:
	    return "Starting";
	case LifecycleState::Connected:
	    return "Connected";
	case LifecycleState::Draining:
	    return "Draining";
	case LifecycleState::Backoff:
	    return "Backoff";
	case LifecycleState::Cooldown:
	    return "Cooldown";
    }

    return "?";
}

CrashGuardSettings CrashGuardSettings::fromEnvironment () {
    CrashGuardSettings settings;
    const char* raw = std::getenv ("LWE_WEB_CRASHGUARD");

    if (raw == nullptr || *raw == '\0') {
	return settings;
    }

    const std::string value (raw);
    const auto firstComma = value.find (',');
    const auto secondComma = firstComma == std::string::npos ? std::string::npos : value.find (',', firstComma + 1);

    if (firstComma == std::string::npos || secondComma == std::string::npos) {
	sLog.error (
	    "web helper: LWE_WEB_CRASHGUARD must be <deaths>,<windowMs>,<cooldownMs>; ignoring \"", value, "\""
	);
	return settings;
    }

    try {
	const int deaths = std::stoi (value.substr (0, firstComma));
	const int64_t windowMs = std::stoll (value.substr (firstComma + 1, secondComma - firstComma - 1));
	const int64_t cooldownMs = std::stoll (value.substr (secondComma + 1));

	if (deaths < 1 || windowMs < 0 || cooldownMs < 0) {
	    sLog.error ("web helper: LWE_WEB_CRASHGUARD values out of range; ignoring \"", value, "\"");
	    return settings;
	}

	settings.deaths = deaths;
	settings.windowMs = windowMs;
	settings.cooldownMs = cooldownMs;
    } catch (const std::exception&) {
	sLog.error ("web helper: LWE_WEB_CRASHGUARD is not three numbers; ignoring \"", value, "\"");
    }

    return settings;
}

HelperClient::HelperClient (SpawnConfig config) :
    m_config (std::move (config)), m_crashGuard (CrashGuardSettings::fromEnvironment ()) {
    sLog.out (
	"web helper: client ready for ", this->m_config.schemes.size (), " scheme(s), socket ",
	this->m_config.socketPath.string ()
    );
    sLog.out (
	"web helper: crash-loop guard ", this->m_crashGuard.deaths, " death(s) in ", this->m_crashGuard.windowMs,
	" ms -> ", this->m_crashGuard.cooldownMs, " ms cooldown"
    );
}

HelperClient::~HelperClient () { this->stopHelper (); }

int64_t HelperClient::millisUntilNextAttempt () const {
    if (this->m_state != LifecycleState::Backoff && this->m_state != LifecycleState::Cooldown) {
	return 0;
    }

    return std::max<int64_t> (0, this->m_nextAttemptMs - steadyNowMs ());
}

bool HelperClient::spawnService () {
    std::string error;
    const pid_t pid = SpawnGate::spawn (this->m_config.toArguments (), error);

    if (pid <= 0) {
	sLog.error ("web helper: cannot start lwe-web-service: ", error);
	// a spawn that cannot even happen is a death for guard purposes; without this a
	// missing binary would be retried every single frame
	this->scheduleRespawn (steadyNowMs ());

	return false;
    }

    this->m_helperPid = pid;
    this->m_spawnCount++;
    this->m_state = LifecycleState::Starting;
    this->m_nextAttemptMs = 0;
    sLog.out ("web helper: started lwe-web-service pid ", pid, " (spawn #", this->m_spawnCount, ")");

    return true;
}

bool HelperClient::tryConnect () {
    std::string error;
    auto channel = connectToHelper (this->m_config.socketPath, error);

    if (!channel.has_value ()) {
	return false;
    }

    this->m_channel = std::move (channel);
    this->m_state = LifecycleState::Connected;
    this->m_connectedSinceMs = steadyNowMs ();
    this->m_warnedDisconnected = false;
    this->replayState ();

    return true;
}

void HelperClient::replayState () {
    if (this->m_replay.empty () || !this->m_channel.has_value ()) {
	return;
    }

    sLog.out ("web helper: replaying ", this->m_replay.size (), " instance(s) into pid ", this->m_helperPid);

    for (auto& [id, record] : this->m_replay) {
	this->m_instances[id] = {};
	this->m_channel->send (
	    encodeCreate (id, record.workshopId, record.file, record.width, record.height, record.framerate)
	);

	// PROPERTIES ARE NOT SENT HERE. The helper renders them as JS into the page, and a
	// page that has not finished loading swallows the call - which is exactly why CWeb
	// gates its first injection on page-loaded. They go out from handle(PageLoaded).
	record.propertiesPending = !record.properties.empty ();
    }
}

bool HelperClient::reapChild (const bool force) {
    if (this->m_helperPid <= 0) {
	return true;
    }

    const int pid = this->m_helperPid;

    if (force) {
	kill (pid, SIGKILL);
    }

    int status = 0;
    const pid_t reaped = force ? waitpid (pid, &status, 0) : waitpid (pid, &status, WNOHANG);

    if (reaped != pid) {
	if (reaped < 0 && errno == ECHILD) {
	    // somebody else reaped it, or it was never ours; either way it is not running
	    this->m_helperPid = -1;
	    this->m_lastExitDescription = "vanished (no such child)";

	    return true;
	}

	return false;
    }

    this->m_helperPid = -1;
    this->m_lastExitDescription = describeExit (status);

    return true;
}

void HelperClient::reapShmObjects (const int pid) const {
    if (pid <= 0) {
	return;
    }

    const std::string prefix = "lwe-web-" + std::to_string (pid) + "-";
    std::error_code ec;
    size_t removed = 0;

    for (const auto& entry : std::filesystem::directory_iterator ("/dev/shm", ec)) {
	const std::string name = entry.path ().filename ().string ();

	if (name.rfind (prefix, 0) != 0) {
	    continue;
	}

	if (shm_unlink (("/" + name).c_str ()) == 0) {
	    removed++;
	}
    }

    if (removed > 0) {
	sLog.error ("web helper: unlinked ", removed, " frame buffer(s) left behind by pid ", pid);
    }
}

void HelperClient::invalidateInstanceState () {
    // every instance's state belonged to that process, including its shm objects - a dead
    // helper's generations are gone and the engine must not go on reading them
    for (auto& [id, state] : this->m_instances) {
	state.pageLoaded = false;
	state.loadFailed = false;
	state.loadErrorCode = 0;
	state.loadErrorText.clear ();
	state.frameGeneration = 0;
    }
}

bool HelperClient::ensureHelper () {
    if (this->isConnected ()) {
	if (this->m_state == LifecycleState::Draining) {
	    sLog.out (
		"web helper: teardown canceled, a web wallpaper arrived before pid ", this->m_helperPid, " left"
	    );
	    this->m_state = LifecycleState::Connected;
	}

	return true;
    }

    const int64_t now = steadyNowMs ();

    if (now < this->m_nextAttemptMs) {
	return false;
    }

    if (this->m_helperPid <= 0 && !this->spawnService ()) {
	return false;
    }

    const auto started = std::chrono::steady_clock::now ();

    for (int attempt = 0; attempt < CONNECT_ATTEMPTS; attempt++) {
	if (this->tryConnect ()) {
	    const auto elapsed
		= std::chrono::duration_cast<std::chrono::milliseconds> (std::chrono::steady_clock::now () - started);
	    sLog.out ("web helper: connected after ", elapsed.count (), " ms (", attempt + 1, " attempt(s))");

	    return true;
	}

	const int pid = this->m_helperPid;

	if (this->reapChild (false)) {
	    sLog.error ("web helper: pid ", pid, " ", this->m_lastExitDescription, " before accepting a connection");
	    this->reapShmObjects (pid);
	    this->scheduleRespawn (steadyNowMs ());

	    return false;
	}

	std::this_thread::sleep_for (std::chrono::milliseconds (CONNECT_BACKOFF_MS));
    }

    sLog.error (
	"web helper: gave up connecting to ", this->m_config.socketPath.string (), " after ",
	CONNECT_ATTEMPTS * CONNECT_BACKOFF_MS, " ms"
    );

    return false;
}

void HelperClient::onDisconnected () {
    const std::string reason = this->m_channel->error ();
    this->m_channel.reset ();
    this->m_connectedSinceMs = 0;
    this->invalidateInstanceState ();

    const int pid = this->m_helperPid;

    if (this->m_state == LifecycleState::Draining || this->m_replay.empty ()) {
	this->m_state = LifecycleState::Draining;
	this->m_drainDeadlineMs = steadyNowMs () + DRAIN_TIMEOUT_MS;

	return;
    }

    sLog.error (
	"web helper: connection to pid ", pid, " lost unexpectedly (", reason, ") with ", this->m_replay.size (),
	" web wallpaper(s) still on screen"
    );

    // It has either died already or is wedged with a live socket; either way it is not
    // coming back, and leaving it would orphan ~475 MB of CEF.
    this->reapChild (true);
    sLog.error ("web helper: pid ", pid, " ", this->m_lastExitDescription);
    this->reapShmObjects (pid);
    this->scheduleRespawn (steadyNowMs ());
}

void HelperClient::tickDrain (const int64_t now) {
    const bool overdue = now >= this->m_drainDeadlineMs;

    if (this->m_channel.has_value () && !overdue) {
	return;
    }

    if (this->m_channel.has_value ()) {
	sLog.error (
	    "web helper: pid ", this->m_helperPid, " held the socket open past the ", DRAIN_TIMEOUT_MS,
	    " ms drain deadline; forcing it down"
	);
	this->m_channel.reset ();
    }

    if (this->m_helperPid <= 0) {
	this->m_state = LifecycleState::Idle;

	return;
    }

    const int pid = this->m_helperPid;

    if (!this->reapChild (overdue)) {
	return;
    }

    this->reapShmObjects (pid);
    this->m_state = LifecycleState::Idle;
    sLog.out ("web helper: torn down to zero, pid ", pid, " ", this->m_lastExitDescription);
}

void HelperClient::scheduleRespawn (const int64_t now) {
    this->m_unexpectedDeaths++;
    this->m_deaths.push_back (now);

    while (!this->m_deaths.empty () && now - this->m_deaths.front () > this->m_crashGuard.windowMs) {
	this->m_deaths.pop_front ();
    }

    if (static_cast<int> (this->m_deaths.size ()) >= this->m_crashGuard.deaths) {
	// TRIP. The ring is CLEARED rather than left to slide, so a cooldown is always
	// followed by at least one real attempt. Left sliding, the deaths that caused the
	// trip would still be inside the window when the cooldown expired and would trip
	// it again immediately, which is a guard that never lets the helper come back.
	this->m_deaths.clear ();
	this->m_nextAttemptMs = now + this->m_crashGuard.cooldownMs;
	this->m_state = LifecycleState::Cooldown;
	this->m_backoffMs = BACKOFF_BASE_MS;
	sLog.error (
	    "web helper: CRASH-LOOP GUARD tripped - ", this->m_crashGuard.deaths, " deaths within ",
	    this->m_crashGuard.windowMs, " ms; no browser process for ", this->m_crashGuard.cooldownMs,
	    " ms. Web wallpapers stay dark until then; scenes are unaffected."
	);

	return;
    }

    this->m_nextAttemptMs = now + this->m_backoffMs;
    this->m_state = LifecycleState::Backoff;
    sLog.error (
	"web helper: respawning in ", this->m_backoffMs, " ms (death ", this->m_deaths.size (), " of ",
	this->m_crashGuard.deaths, " in the guard window)"
    );
    this->m_backoffMs = std::min (this->m_backoffMs * 2, BACKOFF_MAX_MS);
}

void HelperClient::stopHelper () {
    if (this->m_channel.has_value ()) {
	this->m_channel->close ();
	this->m_channel.reset ();
    }

    this->m_state = LifecycleState::Idle;
    this->m_nextAttemptMs = 0;

    if (this->m_helperPid <= 0) {
	return;
    }

    const int pid = this->m_helperPid;

    for (int attempt = 0; attempt < 100 && this->m_helperPid > 0; attempt++) {
	if (this->reapChild (false)) {
	    break;
	}

	std::this_thread::sleep_for (std::chrono::milliseconds (10));
    }

    if (this->m_helperPid > 0) {
	sLog.error ("web helper: pid ", pid, " ignored the closed socket, killing");
	this->reapChild (true);
    }

    this->reapShmObjects (pid);
}

InstanceId HelperClient::allocateInstance () {
    const InstanceId id = this->m_nextInstanceId++;
    this->m_instances[id] = {};

    return id;
}

void HelperClient::pumpEvents () {
    const int64_t now = steadyNowMs ();

    if (this->m_channel.has_value ()) {
	this->m_channel->flush ();

	for (const auto& message : this->m_channel->receive ()) {
	    this->handle (message);
	}

	if (!this->m_channel->isOpen ()) {
	    this->onDisconnected ();
	}
    }

    if (this->m_state == LifecycleState::Connected && this->m_connectedSinceMs != 0
	&& now - this->m_connectedSinceMs >= HEALTHY_CONNECTION_MS && this->m_backoffMs != BACKOFF_BASE_MS) {
	this->m_backoffMs = BACKOFF_BASE_MS;
    }

    if (this->m_state == LifecycleState::Starting) {
	const int pid = this->m_helperPid;

	if (!this->tryConnect () && pid > 0 && this->reapChild (false)) {
	    sLog.error ("web helper: pid ", pid, " ", this->m_lastExitDescription, " before accepting a connection");
	    this->reapShmObjects (pid);
	    this->scheduleRespawn (now);
	}
    }

    if (this->m_state == LifecycleState::Connected && this->m_replay.empty ()) {
	this->m_state = LifecycleState::Draining;
	this->m_drainDeadlineMs = now + DRAIN_TIMEOUT_MS;
	sLog.out ("web helper: nothing on screen wants a browser; pid ", this->m_helperPid, " will exit");
    }

    if (this->m_state == LifecycleState::Draining) {
	this->tickDrain (now);
    }

    if ((this->m_state == LifecycleState::Backoff || this->m_state == LifecycleState::Cooldown)
	&& now >= this->m_nextAttemptMs) {
	if (this->m_replay.empty ()) {
	    this->m_state = LifecycleState::Idle;
	    this->m_nextAttemptMs = 0;
	} else {
	    this->spawnService ();
	}
    }
}

void HelperClient::handle (const Message& message) {
    if (isCommand (message.type)) {
	sLog.error (
	    "web helper: ignoring a command received on the engine side, type ", static_cast<int> (message.type)
	);
	return;
    }

    PayloadReader reader (message.payload);
    const InstanceId id = reader.u32 ();

    switch (message.type) {
	case MessageType::PageLoaded:
	    {
		if (!reader.ok ()) {
		    sLog.error ("web helper: malformed page-loaded event");
		    return;
		}

		if (const auto it = this->m_instances.find (id); it != this->m_instances.end ()) {
		    it->second.pageLoaded = true;
		    it->second.loadFailed = false;
		}

		if (const auto record = this->m_replay.find (id);
		    record != this->m_replay.end () && record->second.propertiesPending) {
		    std::vector<PropertyValue> properties;
		    properties.reserve (record->second.properties.size ());

		    for (const auto& [key, value] : record->second.properties) {
			properties.push_back (value);
		    }

		    sLog.out (
			"web helper: replaying ", properties.size (), " property(ies) into instance ", id,
			" after respawn"
		    );
		    this->sendStateful (encodeInjectProperties (id, properties));
		    record->second.propertiesPending = false;
		}

		break;
	    }
	case MessageType::PageFailed:
	    {
		const int32_t errorCode = reader.i32 ();
		const std::string errorText = reader.str ();
		const std::string failedUrl = reader.str ();

		if (!reader.ok ()) {
		    sLog.error ("web helper: malformed page-failed event");
		    return;
		}

		sLog.error (
		    "web helper: instance ", id, " could not load the wallpaper: ", errorText, " (code ", errorCode,
		    ", url ", failedUrl, ")"
		);

		if (const auto it = this->m_instances.find (id); it != this->m_instances.end ()) {
		    it->second.pageLoaded = false;
		    it->second.loadFailed = true;
		    it->second.loadErrorCode = errorCode;
		    it->second.loadErrorText = errorText;
		}

		break;
	    }
	case MessageType::FrameReady:
	    {
		const uint32_t generation = reader.u32 ();
		const uint32_t sequence = reader.u32 ();
		const uint32_t width = reader.u32 ();
		const uint32_t height = reader.u32 ();

		if (!reader.ok ()) {
		    sLog.error ("web helper: malformed frame-ready event");
		    return;
		}

		if (const auto it = this->m_instances.find (id); it != this->m_instances.end ()) {
		    it->second.frameGeneration = generation;
		    it->second.frameWidth = width;
		    it->second.frameHeight = height;
		}

		sLog.out (
		    "web helper: instance ", id, " published generation ", generation, " (", width, "x", height,
		    ", first sequence ", sequence, ")"
		);
		break;
	    }
	default:
	    sLog.error ("web helper: unknown event type ", static_cast<int> (message.type));
	    break;
    }
}

void HelperClient::sendTransient (const std::vector<uint8_t>& framed) {
    if (!this->isConnected ()) {
	return;
    }

    this->m_channel->send (framed);
}

void HelperClient::sendStateful (const std::vector<uint8_t>& framed) {
    if (this->isConnected ()) {
	this->m_channel->send (framed);
	return;
    }

    if (!this->m_warnedDisconnected) {
	sLog.out (
	    "web helper: no helper connected (", this->stateName (), "); stateful commands held in the replay record"
	);
	this->m_warnedDisconnected = true;
    }
}

void HelperClient::create (
    const InstanceId id, const std::string& workshopId, const std::string& file, const uint32_t width,
    const uint32_t height, const uint32_t framerate
) {
    this->ensureHelper ();

    ReplayRecord& record = this->m_replay[id];
    record.workshopId = workshopId;
    record.file = file;
    record.width = width;
    record.height = height;
    record.framerate = framerate;
    record.properties.clear ();
    record.propertiesPending = false;

    this->sendStateful (encodeCreate (id, workshopId, file, width, height, framerate));
}

void HelperClient::resize (const InstanceId id, const uint32_t width, const uint32_t height) {
    if (const auto it = this->m_replay.find (id); it != this->m_replay.end ()) {
	// the record carries the CURRENT size, so a replayed create is born at the size the
	// viewport is now rather than the one it had when the wallpaper first appeared
	it->second.width = width;
	it->second.height = height;
    }

    this->sendStateful (encodeResize (id, width, height));
}

void HelperClient::mouseMove (const InstanceId id, const int32_t x, const int32_t y) {
    this->sendTransient (encodeMouseMove (id, x, y));
}

void HelperClient::mouseClick (
    const InstanceId id, const int32_t x, const int32_t y, const MouseButton button, const bool released
) {
    this->sendTransient (encodeMouseClick (id, x, y, button, released));
}

void HelperClient::injectProperties (const InstanceId id, const std::vector<PropertyValue>& properties) {
    if (const auto it = this->m_replay.find (id); it != this->m_replay.end ()) {
	it->second.properties.clear ();

	for (const auto& property : properties) {
	    it->second.properties[property.key] = property;
	}
    }

    this->sendStateful (encodeInjectProperties (id, properties));
}

void HelperClient::setProperty (const InstanceId id, const PropertyValue& property) {
    if (const auto it = this->m_replay.find (id); it != this->m_replay.end ()) {
	// MERGED over the bulk injection, so a live change from a control client is part
	// of what a replacement helper is given rather than being undone by a respawn
	it->second.properties[property.key] = property;
    }

    this->sendStateful (encodeSetProperty (id, property));
}

void HelperClient::audioSpectrum (const InstanceId id, const float* bands) {
    this->sendTransient (encodeAudioSpectrum (id, bands));
}

void HelperClient::destroy (const InstanceId id) {
    this->sendStateful (encodeDestroy (id));
    this->m_instances.erase (id);
    this->m_replay.erase (id);

    if (!this->m_replay.empty ()) {
	return;
    }

    if (this->m_state == LifecycleState::Backoff || this->m_state == LifecycleState::Cooldown) {
	this->m_state = LifecycleState::Idle;
	this->m_nextAttemptMs = 0;
    }
}

bool HelperClient::isPageLoaded (const InstanceId id) const {
    const auto it = this->m_instances.find (id);

    return it != this->m_instances.end () && it->second.pageLoaded;
}

bool HelperClient::didLoadFail (const InstanceId id) const {
    const auto it = this->m_instances.find (id);

    return it != this->m_instances.end () && it->second.loadFailed;
}

const InstanceState* HelperClient::instance (const InstanceId id) const {
    const auto it = this->m_instances.find (id);

    return it != this->m_instances.end () ? &it->second : nullptr;
}
