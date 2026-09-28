#include "LweCommand.h"

#include "WallpaperEngine/Api/CommandClient.h"
#include "WallpaperEngine/Application/Config.h"
#include "WallpaperEngine/Application/PanelHandoff.h"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <iostream>

using namespace WallpaperEngine;

namespace {
constexpr int DONE = 0;
constexpr int REFUSED = 1;
constexpr int NOT_RUNNING = 2;
constexpr int USAGE = 3;

constexpr const char* OFF_DONE
    = "Wallpaper off on every screen now; not saved: lwe on or a service restart brings it back.";
constexpr const char* ON_DONE = "Wallpaper back on every screen now; on is the normal state, so nothing is saved.";

struct Answer {
    std::optional<nlohmann::json> result;
    int code = DONE;
};

const nlohmann::json* member (const nlohmann::json& object, const char* key) {
    if (!object.is_object ()) {
	return nullptr;
    }

    const auto found = object.find (key);

    return found == object.end () ? nullptr : &*found;
}

const nlohmann::json& section (const nlohmann::json& status, const char* key) {
    static const nlohmann::json empty = nlohmann::json::object ();
    const auto* value = member (status, key);

    return value != nullptr && value->is_object () ? *value : empty;
}

std::string text (const nlohmann::json& object, const char* key) {
    const auto* value = member (object, key);

    return value != nullptr && value->is_string () ? value->get<std::string> () : "";
}

std::string runningVersion (const nlohmann::json& status) {
    const auto* version = member (status, "version");

    return version != nullptr && version->is_string () ? version->get<std::string> () : "older than 1.1.0";
}

std::string number (const nlohmann::json& value) {
    if (value.is_number_integer ()) {
	return std::to_string (value.get<int64_t> ());
    }

    char buffer[32];
    std::snprintf (buffer, sizeof (buffer), "%g", value.get<double> ());

    return buffer;
}

std::string duration (const int64_t seconds) {
    const auto hours = static_cast<long long> (seconds / 3600);
    const auto minutes = static_cast<long long> (seconds % 3600 / 60);
    const auto rest = static_cast<long long> (seconds % 60);
    char buffer[64];

    if (hours > 0) {
	std::snprintf (buffer, sizeof (buffer), "%lldh %02lldm %02llds", hours, minutes, rest);
    } else if (minutes > 0) {
	std::snprintf (buffer, sizeof (buffer), "%lldm %02llds", minutes, rest);
    } else {
	std::snprintf (buffer, sizeof (buffer), "%llds", rest);
    }

    return buffer;
}

std::string joined (const std::vector<std::string>& parts) {
    std::string result;

    for (const auto& part : parts) {
	result += (result.empty () ? "" : ", ") + part;
    }

    return result;
}

std::string printable (std::string value) {
    for (auto& character : value) {
	if (static_cast<unsigned char> (character) < 0x20 || character == 0x7F) {
	    character = '?';
	}
    }

    return value;
}

std::string line (const std::string& label, const std::string& value) {
    if (value.empty ()) {
	return label + "\n";
    }

    std::string padded = label;
    padded.resize (12, ' ');

    return padded + printable (value) + "\n";
}

int fail (const bool json, const std::string& message, const int code, const bool prefixed = true) {
    if (json) {
	std::cerr << nlohmann::json { { "error", message } }.dump (
	    -1, ' ', false, nlohmann::json::error_handler_t::replace
	) << std::endl;
    } else {
	std::cerr << (prefixed ? "lwe: " : "") << message << std::endl;
    }

    return code;
}

Answer ask (const bool json, const std::string& cmd) {
    const auto& socket = Application::Config::get ().socket.value;
    const auto response = Api::CommandClient::request (socket, cmd);

    if (response.outcome == Api::CommandClient::Outcome::NotRunning) {
	return { .result = std::nullopt,
		 .code = fail (
		     json,
		     "the engine is not running (" + socket.string () + ": " + std::strerror (response.error) + ")",
		     NOT_RUNNING
		 ) };
    }

    if (response.outcome == Api::CommandClient::Outcome::NoAnswer) {
	return { .result = std::nullopt,
		 .code = fail (
		     json,
		     "the engine did not answer within " + std::to_string (Api::CommandClient::DEADLINE_MS / 1000)
			 + " seconds",
		     NOT_RUNNING
		 ) };
    }

    if (response.outcome == Api::CommandClient::Outcome::Closed) {
	return { .result = std::nullopt,
		 .code = fail (
		     json, "the engine closed the connection without answering; it may have too many clients.",
		     NOT_RUNNING
		 ) };
    }

    if (response.outcome == Api::CommandClient::Outcome::BadReply) {
	return { .result = std::nullopt, .code = fail (json, "the engine sent a reply lwe cannot read", NOT_RUNNING) };
    }

    if (!response.reply.at ("ok").get<bool> ()) {
	return { .result = std::nullopt,
		 .code
		 = fail (json, "the engine refused: " + response.reply.at ("error").get<std::string> (), REFUSED) };
    }

    return { .result = response.reply.at ("result"), .code = DONE };
}

int printStatus (const bool json) {
    const auto answer = ask (json, "status");

    if (!answer.result.has_value ()) {
	return answer.code;
    }

    if (const auto mismatch = Application::LweCommand::versionMismatch (*answer.result, LWE_VERSION);
	mismatch.has_value ()) {
	std::cerr << *mismatch << std::endl;
    }

    std::cout << (json ? answer.result->dump () + "\n" : Application::LweCommand::statusText (*answer.result));

    return DONE;
}

int switchOutputs (const bool json, const std::string& cmd, const char* done) {
    const auto current = ask (json, "status");

    if (!current.result.has_value ()) {
	return current.code;
    }

    if (const auto mismatch = Application::LweCommand::versionMismatch (*current.result, LWE_VERSION);
	mismatch.has_value ()) {
	return fail (json, *mismatch, REFUSED, false);
    }

    const auto answer = ask (json, cmd);

    if (!answer.result.has_value ()) {
	return answer.code;
    }

    std::cout << (json ? answer.result->dump () : std::string (done)) << std::endl;

    return DONE;
}
} // namespace

namespace WallpaperEngine::Application::LweCommand {
bool isLweName (const int argc, char* argv[]) {
    if (argc < 1 || argv[0] == nullptr) {
	return false;
    }

    const char* slash = std::strrchr (argv[0], '/');

    return std::strcmp (slash != nullptr ? slash + 1 : argv[0], "lwe") == 0;
}

Request parse (const std::vector<std::string>& words) {
    Request request;
    std::vector<std::string> rest;

    for (const auto& word : words) {
	if (word == "-j" || word == "--json") {
	    request.json = true;
	} else {
	    rest.push_back (word);
	}
    }

    if (rest.empty ()) {
	request.action = Action::Status;
	request.verb = "status";
	return request;
    }

    request.verb = rest.front ();

    if (request.verb == "--version" || request.verb == "status" || request.verb == "off" || request.verb == "on") {
	request.action = rest.size () > 1 ? Action::Usage
	    : request.verb == "--version" ? Action::Version
	    : request.verb == "status"    ? Action::Status
	    : request.verb == "off"       ? Action::Off
					  : Action::On;
    } else {
	request.action = Action::Panel;
    }

    return request;
}

std::string statusText (const nlohmann::json& status) {
    const auto& current = section (status, "current");
    const auto title = text (current, "title");
    const auto id = text (current, "id");
    auto onScreen = !title.empty () && !id.empty () ? title + " (" + id + ")" : title + id;

    if (onScreen.empty ()) {
	onScreen = "nothing";
    }

    const auto& rotation = section (status, "rotation");
    const auto order = text (rotation, "order");
    std::vector<std::string> playlist;

    if (const auto label = text (rotation, "label"); !label.empty ()) {
	playlist.push_back (label);
    }

    if (!order.empty ()) {
	playlist.push_back (order);
    }

    if (order != "static") {
	const auto* enabled = member (rotation, "enabled");
	const auto* next = member (rotation, "next_in_s");

	if (enabled != nullptr && enabled->is_boolean () && !enabled->get<bool> ()) {
	    playlist.push_back ("paused");
	} else if (next != nullptr && next->is_number_integer () && next->get<int64_t> () >= 0) {
	    playlist.push_back ("next in " + duration (next->get<int64_t> ()));
	}
    }

    const auto& outputs = section (status, "outputs");
    const bool released = text (outputs, "state") == "released";
    std::string screens = "on";

    if (released) {
	const auto reason = text (outputs, "reason");

	screens = reason == "verb"   ? "off (lwe off)"
	    : reason == "fullscreen" ? "off (fullscreen app)"
	    : reason == "deadman"    ? "off (watchdog)"
	    : reason == "app"        ? "off (running-app rule)"
				     : "off";
    }

    std::vector<std::string> settings;

    for (const char* key : { "volume", "speed", "fps" }) {
	if (const auto* value = member (status, key); value != nullptr && value->is_number ()) {
	    settings.push_back (std::string (key) + " " + number (*value));
	}
    }

    for (const char* key : { "mouse", "parallax", "particles" }) {
	if (const auto* value = member (status, key); value != nullptr && value->is_boolean ()) {
	    settings.push_back (std::string (key) + (value->get<bool> () ? " on" : " off"));
	}
    }

    return line (released ? "last shown" : "on screen", onScreen) + line ("playlist", joined (playlist))
	+ line ("screens", screens) + line ("version", runningVersion (status)) + line ("settings", joined (settings));
}

std::optional<std::string> versionMismatch (const nlohmann::json& status, const std::string& installed) {
    const auto* version = member (status, "version");

    if (version != nullptr && version->is_string () && version->get<std::string> () == installed) {
	return std::nullopt;
    }

    return "The running engine is " + runningVersion (status) + " but " + installed
	+ " is installed; run lwe service restart.";
}

int run (const int argc, char* argv[]) {
    const std::vector<std::string> words (argv + 1, argv + argc);
    const auto request = parse (words);

    switch (request.action) {
	case Action::Version:
	    std::cout << LWE_VERSION << std::endl;
	    return DONE;
	case Action::Usage:
	    return fail (request.json, request.verb + " takes no value", USAGE);
	case Action::Panel:
	    return PanelHandoff::handOff (argv[0], words, request.verb);
	case Action::Status:
	    return printStatus (request.json);
	case Action::Off:
	    return switchOutputs (request.json, "release-outputs", OFF_DONE);
	case Action::On:
	    return switchOutputs (request.json, "acquire-outputs", ON_DONE);
    }

    return USAGE;
}
} // namespace WallpaperEngine::Application::LweCommand
