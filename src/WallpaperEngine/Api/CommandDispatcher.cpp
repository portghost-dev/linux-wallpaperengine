#include "CommandDispatcher.h"

#include <algorithm>
#include <cmath>
#include <set>

using namespace WallpaperEngine::Api;
using json = nlohmann::json;

namespace {
const std::set<std::string> KNOWN_VERBS = { "status",
					    "show",
					    "quit",
					    "set-skip",
					    "list-objects",
					    "rotate-set",
					    "next",
					    "prev",
					    "ping",
					    "pause",
					    "resume",
					    "release-outputs",
					    "acquire-outputs",
					    "set-fullscreen",
					    "set-fullscreen-ignore",
					    "set-fps",
					    "set-speed",
					    "set-volume",
					    "set-mouse",
					    "set-audio",
					    "set-parallax",
					    "set-particles",
					    "set-instrument",
					    "set-tuning",
					    "set-app-conditions" };

std::string validateShowArgs (const json& args) {
    if (args.contains ("cc")) {
	const auto& cc = args["cc"];
	bool ok = cc.is_array () && cc.size () == 4;

	for (size_t i = 0; ok && i < 4; i++) {
	    ok = cc[i].is_number () && std::isfinite (cc[i].get<double> ());
	}

	// brightness/contrast/saturation 0..4, hue radians within +-2pi
	ok = ok && cc[0].get<double> () >= 0.0 && cc[0].get<double> () <= 4.0 && cc[1].get<double> () >= 0.0
	    && cc[1].get<double> () <= 4.0 && cc[2].get<double> () >= 0.0 && cc[2].get<double> () <= 4.0
	    && cc[3].get<double> () >= -6.4 && cc[3].get<double> () <= 6.4;

	if (!ok) {
	    return "args.cc must be [brightness, contrast, saturation, hue] with b/c/s in 0..4 and hue in -6.4..6.4";
	}
    }

    if (args.contains ("speed")) {
	const auto& speed = args["speed"];

	if (!speed.is_number () || !std::isfinite (speed.get<double> ()) || speed.get<double> () < 0.0
	    || speed.get<double> () > 20.0) {
	    return "args.speed must be a number in 0..20";
	}
    }

    if (args.contains ("properties")) {
	const auto& props = args["properties"];

	if (!props.is_object () || props.size () > 64) {
	    return "args.properties must be an object of at most 64 entries";
	}

	for (const auto& [key, value] : props.items ()) {
	    const bool keyOk = !key.empty () && key.size () <= 64 && std::ranges::all_of (key, [] (const char c) {
		return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_';
	    });

	    if (!keyOk) {
		return "property keys must match [A-Za-z0-9_]{1,64}";
	    }

	    if (value.is_string ()) {
		if (value.get<std::string> ().size () > 256) {
		    return "property string values are capped at 256 chars";
		}
	    } else if (!value.is_number () && !value.is_boolean ()) {
		return "property values must be string, number or bool";
	    }
	}
    }

    if (args.contains ("scaling")) {
	static const std::set<std::string> SCALINGS = { "stretch", "fit", "fill", "default" };

	if (!args["scaling"].is_string () || SCALINGS.find (args["scaling"].get<std::string> ()) == SCALINGS.end ()) {
	    return "args.scaling must be one of stretch/fit/fill/default";
	}
    }

    if (args.contains ("clamp")) {
	static const std::set<std::string> CLAMPS = { "clamp", "border", "repeat" };

	if (!args["clamp"].is_string () || CLAMPS.find (args["clamp"].get<std::string> ()) == CLAMPS.end ()) {
	    return "args.clamp must be one of clamp/border/repeat";
	}
    }

    if (args.contains ("volume")) {
	const auto& volume = args["volume"];

	if (!volume.is_number_integer () || volume.get<int64_t> () < 0 || volume.get<int64_t> () > 128) {
	    return "args.volume must be an integer in 0..128";
	}
    }

    for (const auto* flag : { "audio_processing", "mouse", "automute", "fullscreen_pause" }) {
	if (args.contains (flag) && !args[flag].is_boolean ()) {
	    return std::string ("args.") + flag + " must be a boolean";
	}
    }

    // three-state fullscreen policy; fullscreen_pause above stays valid as the
    // boolean alias (true => pause) so stored rotation entries keep working
    if (args.contains ("fullscreen_behavior")) {
	static const std::set<std::string> BEHAVIORS = { "off", "pause", "stop" };

	if (!args["fullscreen_behavior"].is_string ()
	    || BEHAVIORS.find (args["fullscreen_behavior"].get<std::string> ()) == BEHAVIORS.end ()) {
	    return "args.fullscreen_behavior must be one of off/pause/stop";
	}
    }

    // the per-wallpaper conf SKIP list (object ids hidden for this wallpaper); same
    // bounds as set-skip, but riding the show keeps it wallpaper-scoped by construction
    if (args.contains ("skip_objects")) {
	const auto& skips = args["skip_objects"];

	if (!skips.is_array () || skips.size () > 256) {
	    return "args.skip_objects must be an array of at most 256 ints";
	}

	for (const auto& entry : skips) {
	    if (!entry.is_number_integer () || entry.get<int64_t> () < 0 || entry.get<int64_t> () > 1000000) {
		return "skip_objects ids must be integers in 0..1000000";
	    }
	}
    }

    // diagnostic: build the scene without these effect ids (effects are consumed at
    // scene build time, unlike object skips, so they ride the show that rebuilds)
    if (args.contains ("skip_effects")) {
	const auto& fx = args["skip_effects"];

	if (!fx.is_array () || fx.size () > 64) {
	    return "args.skip_effects must be an array of at most 64 ints";
	}

	for (const auto& entry : fx) {
	    if (!entry.is_number_integer () || entry.get<int64_t> () < 0 || entry.get<int64_t> () > 10000000) {
		return "skip_effects ids must be integers in 0..10000000";
	    }
	}
    }

    // the client's opaque identity echo (presets render a BASE wallpaper; this names the
    // tile the user actually picked). Engine stores + echoes, never interprets.
    if (args.contains ("ui_id")) {
	if (!args["ui_id"].is_string () || args["ui_id"].get<std::string> ().size () > 128) {
	    return "args.ui_id must be a string of at most 128 chars";
	}
    }

    return "";
}
} // namespace

bool CommandDispatcher::validBackgroundId (const std::string& id) {
    if (id.empty () || id.size () > 64) {
	return false;
    }

    return std::ranges::all_of (id, [] (const char c) {
	return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-';
    });
}

CommandDispatcher::ParseOutcome CommandDispatcher::parse (const std::string& line) {
    // nlohmann recurses on nested containers; 64KiB of brackets is a stack exhaustion,
    // so refuse absurd nesting before the parser sees it
    {
	int depth = 0;
	int maxDepth = 0;
	bool inString = false;
	bool escaped = false;
	for (const char c : line) {
	    if (escaped) {
		escaped = false;
	    } else if (c == '\\' && inString) {
		escaped = true;
	    } else if (c == '"') {
		inString = !inString;
	    } else if (!inString && (c == '[' || c == '{')) {
		maxDepth = std::max (maxDepth, ++depth);
	    } else if (!inString && (c == ']' || c == '}')) {
		depth--;
	    }
	}
	if (maxDepth > 64) {
	    return { .command = std::nullopt, .errorResponse = failure (nullptr, "request nesting too deep") };
	}
    }

    // parse without exceptions; a hostile line must never throw through the render loop
    const json request = json::parse (line, nullptr, false);

    if (request.is_discarded () || !request.is_object ()) {
	return { .command = std::nullopt, .errorResponse = failure (nullptr, "request is not a JSON object") };
    }

    if (!request.contains ("id") || !request["id"].is_number_integer ()) {
	return { .command = std::nullopt, .errorResponse = failure (nullptr, "missing integer 'id'") };
    }

    const auto id = request["id"].get<int64_t> ();

    if (!request.contains ("cmd") || !request["cmd"].is_string ()) {
	return { .command = std::nullopt, .errorResponse = failure (id, "missing string 'cmd'") };
    }

    const auto cmd = request["cmd"].get<std::string> ();

    if (KNOWN_VERBS.find (cmd) == KNOWN_VERBS.end ()) {
	return { .command = std::nullopt, .errorResponse = failure (id, "unknown command: " + cmd) };
    }

    json args = json::object ();

    if (request.contains ("args")) {
	if (!request["args"].is_object ()) {
	    return { .command = std::nullopt, .errorResponse = failure (id, "'args' must be an object") };
	}

	args = request["args"];
    }

    if (cmd == "show") {
	if (!args.contains ("id") || !args["id"].is_string ()) {
	    return { .command = std::nullopt, .errorResponse = failure (id, "show requires a string args.id") };
	}

	if (!validBackgroundId (args["id"].get<std::string> ())) {
	    // deliberately does not echo the offending value back into a JSON string
	    return { .command = std::nullopt, .errorResponse = failure (id, "args.id must match [A-Za-z0-9_-]{1,64}") };
	}

	const auto error = validateShowArgs (args);

	if (!error.empty ()) {
	    return { .command = std::nullopt, .errorResponse = failure (id, error) };
	}
    }

    if (cmd == "rotate-set") {
	if (!args.contains ("entries") || !args["entries"].is_array () || args["entries"].size () > 512) {
	    return { .command = std::nullopt,
		     .errorResponse
		     = failure (id, "rotate-set requires args.entries, an array of at most 512 entries") };
	}

	for (const auto& entry : args["entries"]) {
	    if (!entry.is_object () || !entry.contains ("id") || !entry["id"].is_string ()
		|| !validBackgroundId (entry["id"].get<std::string> ())) {
		return { .command = std::nullopt,
			 .errorResponse
			 = failure (id, "every rotation entry needs an id matching [A-Za-z0-9_-]{1,64}") };
	    }

	    const auto error = validateShowArgs (entry);

	    if (!error.empty ()) {
		return { .command = std::nullopt,
			 .errorResponse = failure (id, "entry " + entry["id"].get<std::string> () + ": " + error) };
	    }
	}

	if (args.contains ("interval_s")) {
	    const auto& interval = args["interval_s"];

	    if (!interval.is_number_integer () || interval.get<int64_t> () < 15 || interval.get<int64_t> () > 604800) {
		return { .command = std::nullopt,
			 .errorResponse = failure (id, "args.interval_s must be an integer in 15..604800") };
	    }
	}

	if (args.contains ("order")) {
	    static const std::set<std::string> ORDERS = { "sequential", "shuffle", "random" };

	    if (!args["order"].is_string () || ORDERS.find (args["order"].get<std::string> ()) == ORDERS.end ()) {
		return { .command = std::nullopt,
			 .errorResponse = failure (id, "args.order must be one of sequential/shuffle/random") };
	    }
	}

	for (const auto* flag : { "avoid_repeat", "enabled" }) {
	    if (args.contains (flag) && !args[flag].is_boolean ()) {
		return { .command = std::nullopt,
			 .errorResponse = failure (id, std::string ("args.") + flag + " must be a boolean") };
	    }
	}

	if (args.contains ("label")
	    && (!args["label"].is_string () || args["label"].get<std::string> ().size () > 128)) {
	    return { .command = std::nullopt,
		     .errorResponse = failure (id, "args.label must be a string of at most 128 chars") };
	}
    }

    if (cmd == "set-app-conditions") {
	static const std::set<std::string> BEHAVIORS = { "off", "pause", "stop" };

	if (!args.contains ("behavior") || !args["behavior"].is_string ()
	    || BEHAVIORS.find (args["behavior"].get<std::string> ()) == BEHAVIORS.end ()) {
	    return { .command = std::nullopt,
		     .errorResponse = failure (id, "set-app-conditions requires args.behavior: off/pause/stop") };
	}

	if (args.contains ("names")) {
	    if (!args["names"].is_array () || args["names"].size () > 128) {
		return { .command = std::nullopt,
			 .errorResponse = failure (id, "args.names must be an array of at most 128 strings") };
	    }

	    for (const auto& entry : args["names"]) {
		// process comm names; matched literally against /proc/PID/comm, so the
		// only bound needed is a sane length
		if (!entry.is_string () || entry.get<std::string> ().empty ()
		    || entry.get<std::string> ().size () > 64) {
		    return { .command = std::nullopt,
			     .errorResponse = failure (id, "every name must be a string of 1..64 chars") };
		}
	    }
	}
    }

    if (cmd == "set-skip") {
	// diagnostic verb: replaces the render-skip id set wholesale; [] clears. Scene
	// object ids are small ints; the caps only bound a confused client.
	if (!args.contains ("ids") || !args["ids"].is_array () || args["ids"].size () > 256) {
	    return { .command = std::nullopt,
		     .errorResponse = failure (id, "set-skip requires args.ids, an array of at most 256 ints") };
	}

	for (const auto& entry : args["ids"]) {
	    if (!entry.is_number_integer () || entry.get<int64_t> () < 0 || entry.get<int64_t> () > 1000000) {
		return { .command = std::nullopt,
			 .errorResponse = failure (id, "set-skip ids must be integers in 0..1000000") };
	    }
	}
    }

    if (cmd == "set-fps") {
	// 1..480: below 1 the limiter would divide toward infinity, above 480 the cap
	// stops meaning anything on real hardware
	if (!args.contains ("fps") || !args["fps"].is_number_integer () || args["fps"].get<int64_t> () < 1
	    || args["fps"].get<int64_t> () > 480) {
	    return { .command = std::nullopt,
		     .errorResponse = failure (id, "set-fps requires args.fps, an integer in 1..480") };
	}
    }

    if (cmd == "set-speed") {
	// same bounds as the `speed` show-arg above: the setter clamps 0..20 anyway, but a
	// value outside the contract is a confused client and gets told so. 0 is the freeze.
	if (!args.contains ("speed") || !args["speed"].is_number () || !std::isfinite (args["speed"].get<double> ())
	    || args["speed"].get<double> () < 0.0 || args["speed"].get<double> () > 20.0) {
	    return { .command = std::nullopt,
		     .errorResponse = failure (id, "set-speed requires args.speed, a number in 0..20") };
	}
    }

    if (cmd == "set-tuning") {
	// live calibration dials; every supplied key must be a finite number and at
	// least one must be present (the handler clamps to each dial's range)
	bool any = false;
	for (const auto* key : { "classic_k", "classic_exp", "audio_gain", "audio_smooth" }) {
	    if (!args.contains (key)) {
		continue;
	    }
	    if (!args[key].is_number () || !std::isfinite (args[key].get<double> ())) {
		return { .command = std::nullopt,
			 .errorResponse = failure (id, "set-tuning args must be finite numbers") };
	    }
	    any = true;
	}
	if (!any) {
	    return { .command = std::nullopt,
		     .errorResponse = failure (id, "set-tuning requires classic_k, classic_exp or audio_gain") };
	}
    }

    if (cmd == "set-volume") {
	// same bounds as the `volume` show-arg above (0..128, the SDL mixer's range)
	if (!args.contains ("volume") || !args["volume"].is_number_integer () || args["volume"].get<int64_t> () < 0
	    || args["volume"].get<int64_t> () > 128) {
	    return { .command = std::nullopt,
		     .errorResponse = failure (id, "set-volume requires args.volume, an integer in 0..128") };
	}
    }

    if (cmd == "set-mouse") {
	if (!args.contains ("enabled") || !args["enabled"].is_boolean ()) {
	    return { .command = std::nullopt,
		     .errorResponse = failure (id, "set-mouse requires args.enabled, a boolean") };
	}
    }

    if (cmd == "set-audio") {
	if (!args.contains ("enabled") || !args["enabled"].is_boolean ()) {
	    return { .command = std::nullopt,
		     .errorResponse = failure (id, "set-audio requires args.enabled, a boolean") };
	}
    }

    if (cmd == "set-instrument") {
	if (!args.contains ("name") || !args["name"].is_string ()) {
	    return { .command = std::nullopt,
		     .errorResponse = failure (id, "set-instrument requires args.name, a string") };
	}
	if (!args.contains ("enabled") || !args["enabled"].is_boolean ()) {
	    return { .command = std::nullopt,
		     .errorResponse = failure (id, "set-instrument requires args.enabled, a boolean") };
	}
    }

    if (cmd == "set-parallax" || cmd == "set-particles") {
	if (!args.contains ("enabled") || !args["enabled"].is_boolean ()) {
	    return { .command = std::nullopt,
		     .errorResponse = failure (id, cmd + " requires args.enabled, a boolean") };
	}
    }

    if (cmd == "set-fullscreen-ignore") {
	// replaced wholesale; [] clears the list
	if (!args.contains ("app_ids") || !args["app_ids"].is_array () || args["app_ids"].size () > 128) {
	    return { .command = std::nullopt,
		     .errorResponse
		     = failure (id, "set-fullscreen-ignore requires args.app_ids, an array of at most 128 strings") };
	}

	for (const auto& entry : args["app_ids"]) {
	    if (!entry.is_string () || entry.get<std::string> ().empty () || entry.get<std::string> ().size () > 128) {
		return { .command = std::nullopt,
			 .errorResponse
			 = failure (id, "app_ids entries must be non-empty strings of at most 128 chars") };
	    }
	}
    }

    if (cmd == "set-fullscreen") {
	// LIVE fullscreen policy. The handler indexes args.behavior directly, so the
	// string has to be proven here - a missing key would throw through the loop.
	static const std::set<std::string> BEHAVIORS = { "off", "pause", "stop" };

	if (!args.contains ("behavior") || !args["behavior"].is_string ()
	    || BEHAVIORS.find (args["behavior"].get<std::string> ()) == BEHAVIORS.end ()) {
	    return { .command = std::nullopt,
		     .errorResponse = failure (id, "set-fullscreen requires args.behavior of off/pause/stop") };
	}
    }

    return { .command = Command { .id = id, .cmd = cmd, .args = args }, .errorResponse = "" };
}

std::string CommandDispatcher::accepted (int64_t id) {
    return json { { "id", id }, { "ok", true }, { "status", "accepted" } }.dump ();
}

std::string CommandDispatcher::done (int64_t id, const json& result) {
    return json { { "id", id }, { "ok", true }, { "status", "done" }, { "result", result } }.dump ();
}

std::string CommandDispatcher::failure (const json& id, const std::string& message) {
    return json { { "id", id }, { "ok", false }, { "error", message } }.dump ();
}
