#include <catch2/catch_test_macros.hpp>

#include <nlohmann/json.hpp>
#include <string>

#include "WallpaperEngine/Api/CommandDispatcher.h"

using namespace WallpaperEngine::Api;
using json = nlohmann::json;

TEST_CASE ("valid requests parse into commands", "[dispatcher]") {
    SECTION ("bare status") {
	const auto outcome = CommandDispatcher::parse (R"({"id":7,"cmd":"status"})");

	REQUIRE (outcome.command.has_value ());
	CHECK (outcome.command->id == 7);
	CHECK (outcome.command->cmd == "status");
	CHECK (outcome.command->args.is_object ());
	CHECK (outcome.command->args.empty ());
    }

    SECTION ("show with a workshop id") {
	const auto outcome = CommandDispatcher::parse (R"({"id":1,"cmd":"show","args":{"id":"3134543499"}})");

	REQUIRE (outcome.command.has_value ());
	CHECK (outcome.command->args["id"] == "3134543499");
    }

    SECTION ("quit") {
	const auto outcome = CommandDispatcher::parse (R"({"id":2,"cmd":"quit"})");

	REQUIRE (outcome.command.has_value ());
    }
}

TEST_CASE ("malformed requests are rejected with a parseable error", "[dispatcher]") {
    const std::string bad[] = {
	"not json at all",
	"[1,2,3]",
	R"("just a string")",
	R"({"cmd":"status"})",
	R"({"id":"seven","cmd":"status"})",
	R"({"id":3})",
	R"({"id":3,"cmd":42})",
	R"({"id":3,"cmd":"reboot"})",
	R"({"id":3,"cmd":"status","args":[1]})",
    };

    for (const auto& line : bad) {
	const auto outcome = CommandDispatcher::parse (line);

	INFO ("input: " << line);
	REQUIRE_FALSE (outcome.command.has_value ());

	// the error response itself must be valid JSON with ok=false
	const auto response = json::parse (outcome.errorResponse, nullptr, false);
	REQUIRE_FALSE (response.is_discarded ());
	CHECK (response["ok"] == false);
	CHECK (response.contains ("error"));
    }
}

TEST_CASE ("show quality args are validated like scaling and clamp", "[dispatcher]") {
    const auto ok = CommandDispatcher::parse (
	R"({"id":1,"cmd":"show","args":{"id":"1","ssfactor":1.5,"clampcomposites":0,"texcomp":false,"texdetail":"full"}})"
    );
    REQUIRE (ok.command.has_value ());
    CHECK (ok.command->args["ssfactor"] == 1.5);
    CHECK (ok.command->args["clampcomposites"] == 0);
    CHECK (ok.command->args["texcomp"] == false);
    CHECK (ok.command->args["texdetail"] == "full");

    for (const std::string key : { "ssfactor", "clampcomposites" }) {
	for (const json value : { json (0), json (1.5), json (4), json (-1) }) {
	    const json request = { { "id", 1 }, { "cmd", "show" }, { "args", { { "id", "1" }, { key, value } } } };
	    INFO (request.dump ());
	    CHECK (CommandDispatcher::parse (request.dump ()).command.has_value ());
	}

	for (const json value : { json (4.5), json ("1") }) {
	    const json request = { { "id", 1 }, { "cmd", "show" }, { "args", { { "id", "1" }, { key, value } } } };
	    const auto outcome = CommandDispatcher::parse (request.dump ());
	    INFO (request.dump ());
	    REQUIRE_FALSE (outcome.command.has_value ());
	    const auto error = json::parse (outcome.errorResponse)["error"].get<std::string> ();
	    CHECK (error == "args." + key + " must be a number no greater than 4");
	}

	// an overflowing literal is the only non-finite number JSON text can carry; the parser refuses it
	const std::string overflow = R"({"id":1,"cmd":"show","args":{"id":"1",")" + key + R"(":-1e999}})";
	CHECK_FALSE (CommandDispatcher::parse (overflow).command.has_value ());
    }

    const std::string resRefused = "args.res is no longer accepted; send ssfactor and clampcomposites";
    const auto showWithRes = CommandDispatcher::parse (R"({"id":1,"cmd":"show","args":{"id":"1","res":"sharpfx"}})");
    CHECK_FALSE (showWithRes.command.has_value ());
    CHECK (showWithRes.errorResponse == CommandDispatcher::failure (1, resRefused));
    const auto entryWithRes = CommandDispatcher::parse (
	R"({"id":1,"cmd":"playlist-set","args":{"slug":"default","entries":[{"id":"1","res":"sharpfx"}]}})"
    );
    CHECK_FALSE (entryWithRes.command.has_value ());
    CHECK (entryWithRes.errorResponse == CommandDispatcher::failure (1, "entry 1: " + resRefused));

    const std::string bad[] = {
	R"({"id":1,"cmd":"show","args":{"id":"1","texcomp":"yes"}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","texdetail":"medium"}})",
    };
    for (const auto& request : bad) {
	const auto outcome = CommandDispatcher::parse (request);
	INFO (request);
	CHECK_FALSE (outcome.command.has_value ());
	const auto response = json::parse (outcome.errorResponse, nullptr, false);
	REQUIRE (response.is_object ());
	CHECK (response["ok"] == false);
	CHECK (response["error"].get<std::string> ().find ("args.") != std::string::npos);
    }
}

TEST_CASE ("error responses echo a usable id and null otherwise", "[dispatcher]") {
    const auto withId = json::parse (CommandDispatcher::parse (R"({"id":9,"cmd":"reboot"})").errorResponse);
    CHECK (withId["id"] == 9);

    const auto withoutId = json::parse (CommandDispatcher::parse ("garbage").errorResponse);
    CHECK (withoutId["id"].is_null ());
}

TEST_CASE ("path-shaped show ids never survive validation", "[dispatcher]") {
    const std::string hostile[] = {
	"../../etc/passwd",    "/etc/passwd", "..", ".", "a/b", "a\\b", "id with spaces", "id\nnewline", "",
	std::string (65, 'a'), // one over the length cap
	"$(rm -rf ~)",
	"3134543499.conf", // dots rejected wholesale, so no extension tricks
    };

    for (const auto& id : hostile) {
	INFO ("id: " << id);
	CHECK_FALSE (CommandDispatcher::validBackgroundId (id));

	const json request = { { "id", 1 }, { "cmd", "show" }, { "args", { { "id", id } } } };
	const auto outcome = CommandDispatcher::parse (request.dump ());
	CHECK_FALSE (outcome.command.has_value ());
    }

    CHECK (CommandDispatcher::validBackgroundId ("3134543499"));
    CHECK (CommandDispatcher::validBackgroundId ("my_custom-scene"));
    CHECK (CommandDispatcher::validBackgroundId (std::string (64, 'a')));
}

TEST_CASE ("show render settings are validated", "[dispatcher]") {
    const auto ok = CommandDispatcher::parse (
	R"({"id":1,"cmd":"show","args":{"id":"3602874264","cc":[1.02,1.52,2.0,-0.125664],"speed":1.0}})"
    );
    REQUIRE (ok.command.has_value ());
    CHECK (ok.command->args["cc"][1] == 1.52);

    // and without them show still parses (engine keeps current settings)
    CHECK (CommandDispatcher::parse (R"({"id":1,"cmd":"show","args":{"id":"3602874264"}})").command.has_value ());

    const std::string bad[] = {
	R"({"id":1,"cmd":"show","args":{"id":"1","cc":[1,1,1]}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","cc":[1,1,1,0,0]}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","cc":"1 1 1 0"}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","cc":[1,"x",1,0]}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","cc":[9,1,1,0]}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","cc":[1,1,1,99]}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","speed":-1}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","speed":1000}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","speed":"fast"}})",
    };

    for (const auto& line : bad) {
	INFO ("input: " << line);
	CHECK_FALSE (CommandDispatcher::parse (line).command.has_value ());
    }
}

TEST_CASE ("show properties are validated", "[dispatcher]") {
    const auto ok = CommandDispatcher::parse (
	R"({"id":1,"cmd":"show","args":{"id":"2185197772","properties":{"schemecolor":"0 0 0","side":"centerblack","stars":false,"rate":1.5}}})"
    );
    REQUIRE (ok.command.has_value ());
    CHECK (ok.command->args["properties"]["schemecolor"] == "0 0 0");
    CHECK (ok.command->args["properties"]["stars"] == false);

    // empty object is legal and means "defaults" (the engine clears its override map)
    CHECK (
	CommandDispatcher::parse (R"({"id":1,"cmd":"show","args":{"id":"1","properties":{}}})").command.has_value ()
    );

    const std::string longKey (65, 'a');
    const std::string longValue (257, 'v');
    const std::string bad[] = {
	R"({"id":1,"cmd":"show","args":{"id":"1","properties":["stars"]}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","properties":"stars=false"}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","properties":{"bad key":"x"}}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","properties":{"sneaky/../key":"x"}}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","properties":{"":"x"}}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","properties":{"nested":{"a":1}}}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","properties":{"arr":[1]}}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","properties":{")" + longKey + R"(":"x"}}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","properties":{"k":")" + longValue + R"("}}})",
    };

    for (const auto& line : bad) {
	INFO ("input: " << line);
	CHECK_FALSE (CommandDispatcher::parse (line).command.has_value ());
    }

    // entry-count cap: 65 keys must be rejected, 64 accepted
    json many = json::object ();
    for (int i = 0; i < 64; i++) {
	many["k" + std::to_string (i)] = "v";
    }

    json request = { { "id", 1 }, { "cmd", "show" }, { "args", { { "id", "1" }, { "properties", many } } } };
    CHECK (CommandDispatcher::parse (request.dump ()).command.has_value ());

    many["k64"] = "v";
    request["args"]["properties"] = many;
    CHECK_FALSE (CommandDispatcher::parse (request.dump ()).command.has_value ());
}

TEST_CASE ("show requires args.id", "[dispatcher]") {
    CHECK_FALSE (CommandDispatcher::parse (R"({"id":1,"cmd":"show"})").command.has_value ());
    CHECK_FALSE (CommandDispatcher::parse (R"({"id":1,"cmd":"show","args":{}})").command.has_value ());
    CHECK_FALSE (CommandDispatcher::parse (R"({"id":1,"cmd":"show","args":{"id":42}})").command.has_value ());
}

TEST_CASE ("diagnostic verbs validate their shapes", "[dispatcher]") {
    // set-skip: wholesale replace, [] clears
    CHECK (CommandDispatcher::parse (R"({"id":1,"cmd":"set-skip","args":{"ids":[27,539]}})").command.has_value ());
    CHECK (CommandDispatcher::parse (R"({"id":1,"cmd":"set-skip","args":{"ids":[]}})").command.has_value ());
    CHECK (CommandDispatcher::parse (R"({"id":1,"cmd":"list-objects"})").command.has_value ());

    // skip_effects rides show (effects are build-time)
    CHECK (
	CommandDispatcher::parse (R"({"id":1,"cmd":"show","args":{"id":"2977091760","skip_effects":[544]}})")
	    .command.has_value ()
    );
    CHECK (
	CommandDispatcher::parse (R"({"id":1,"cmd":"show","args":{"id":"2977091760","skip_effects":[]}})")
	    .command.has_value ()
    );

    const std::string bad[] = {
	R"({"id":1,"cmd":"set-skip"})",
	R"({"id":1,"cmd":"set-skip","args":{"ids":27}})",
	R"({"id":1,"cmd":"set-skip","args":{"ids":["27"]}})",
	R"({"id":1,"cmd":"set-skip","args":{"ids":[-1]}})",
	R"({"id":1,"cmd":"set-skip","args":{"ids":[2000000]}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","skip_effects":"544"}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","skip_effects":[-4]}})",
    };

    for (const auto& line : bad) {
	INFO ("input: " << line);
	CHECK_FALSE (CommandDispatcher::parse (line).command.has_value ());
    }
}

TEST_CASE ("show vocabulary args are validated", "[dispatcher]") {
    const std::string good[] = {
	R"({"id":1,"cmd":"show","args":{"id":"1","scaling":"fill","clamp":"border"}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","scaling":"default","clamp":"repeat"}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","volume":0}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","volume":128}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","audio_processing":true,"mouse":false}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","automute":true,"fullscreen_pause":false}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","skip_objects":[27,539]}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","skip_objects":[]}})",
    };

    for (const auto& line : good) {
	INFO ("input: " << line);
	CHECK (CommandDispatcher::parse (line).command.has_value ());
    }

    const std::string bad[] = {
	R"({"id":1,"cmd":"show","args":{"id":"1","scaling":"cover"}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","scaling":1}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","clamp":"mirror"}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","volume":129}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","volume":-1}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","volume":15.5}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","mouse":"yes"}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","audio_processing":1}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","skip_objects":"27"}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","skip_objects":[-1]}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","skip_objects":[2000000]}})",
    };

    for (const auto& line : bad) {
	INFO ("input: " << line);
	CHECK_FALSE (CommandDispatcher::parse (line).command.has_value ());
    }
}

TEST_CASE ("the show verb takes automatic only as a boolean", "[dispatcher]") {
    const std::string good[] = {
	R"({"id":1,"cmd":"show","args":{"id":"1","automatic":true}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","automatic":false}})",
    };

    for (const auto& line : good) {
	INFO ("input: " << line);
	const auto outcome = CommandDispatcher::parse (line);
	REQUIRE (outcome.command.has_value ());
	CHECK (outcome.command->args["automatic"].is_boolean ());
    }

    const std::string bad[] = {
	R"({"id":4,"cmd":"show","args":{"id":"1","automatic":"yes"}})",
	R"({"id":4,"cmd":"show","args":{"id":"1","automatic":1}})",
	R"({"id":4,"cmd":"show","args":{"id":"1","automatic":null}})",
    };

    for (const auto& line : bad) {
	INFO ("input: " << line);
	const auto outcome = CommandDispatcher::parse (line);
	CHECK_FALSE (outcome.command.has_value ());
	const auto response = json::parse (outcome.errorResponse, nullptr, false);
	CHECK (response.is_object ());
	CHECK (response.dump () == R"({"error":"args.automatic must be a boolean","id":4,"ok":false})");
    }
}

TEST_CASE ("next and prev take automatic only as a boolean", "[dispatcher]") {
    const std::string good[] = {
	R"({"id":1,"cmd":"next","args":{"automatic":true}})",
	R"({"id":1,"cmd":"next","args":{"automatic":false}})",
	R"({"id":1,"cmd":"prev","args":{"automatic":true}})",
	R"({"id":1,"cmd":"prev","args":{"automatic":false}})",
    };

    for (const auto& line : good) {
	INFO ("input: " << line);
	const auto outcome = CommandDispatcher::parse (line);
	REQUIRE (outcome.command.has_value ());
	CHECK (outcome.command->args["automatic"].is_boolean ());
    }

    const std::string bad[] = {
	R"({"id":4,"cmd":"next","args":{"automatic":"yes"}})",
	R"({"id":4,"cmd":"prev","args":{"automatic":"yes"}})",
    };

    for (const auto& line : bad) {
	INFO ("input: " << line);
	const auto outcome = CommandDispatcher::parse (line);
	CHECK_FALSE (outcome.command.has_value ());
	const auto response = json::parse (outcome.errorResponse, nullptr, false);
	CHECK (response.is_object ());
	CHECK (response.dump () == R"({"error":"args.automatic must be a boolean","id":4,"ok":false})");
    }
}

TEST_CASE ("fit window args are validated on show and set-fit", "[dispatcher]") {
    const std::string good[] = {
	R"({"id":1,"cmd":"show","args":{"id":"1","fit":{"zoom":1.0,"pan_x":0,"pan_y":0}}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","fit":{"zoom":2,"pan_x":-1,"pan_y":1}}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","fit":{}}})",
	R"({"id":1,"cmd":"set-fit","args":{"zoom":1.5}})",
	R"({"id":1,"cmd":"set-fit","args":{"lane":"all","pan_x":0.25,"pan_y":-0.25}})",
	R"({"id":1,"cmd":"set-fit","args":{"layer":"wallpaper","zoom":1.5}})",
	R"({"id":1,"cmd":"set-fit","args":{"layer":"lane","pan_y":1}})",
	R"({"id":1,"cmd":"set-fit","args":{"layer":"wallpaper","id":"1958937446","zoom":1.5}})",
	R"({"id":1,"cmd":"schedule-set","args":{"enabled":true,"entries":[{"at":"08:00","playlist":"day"},{"at":"20:00","playlist":"night"}]}})",
	R"({"id":1,"cmd":"schedule-set","args":{"enabled":false,"entries":[]}})",
	R"({"id":1,"cmd":"lanes-set","args":{"lanes":[{"id":"all","playlist":"party","manual":true}]}})",
    };

    for (const auto& line : good) {
	INFO ("input: " << line);
	CHECK (CommandDispatcher::parse (line).command.has_value ());
    }

    const std::string bad[] = {
	R"({"id":1,"cmd":"show","args":{"id":"1","fit":1}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","fit":{"zoom":0.5}}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","fit":{"zoom":2.01}}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","fit":{"zoom":"2"}}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","fit":{"pan_x":1.5}}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","fit":{"pan_y":-1.5}}})",
	R"({"id":1,"cmd":"set-fit","args":{}})",
	R"({"id":1,"cmd":"set-fit","args":{"lane":"all"}})",
	R"({"id":1,"cmd":"set-fit","args":{"lane":7,"zoom":1.5}})",
	R"({"id":1,"cmd":"set-fit","args":{"zoom":3}})",
	R"({"id":1,"cmd":"set-fit","args":{"pan_x":"left"}})",
	R"({"id":1,"cmd":"set-fit","args":{"layer":"scene","zoom":1.5}})",
	R"({"id":1,"cmd":"set-fit","args":{"layer":2,"zoom":1.5}})",
	R"({"id":1,"cmd":"set-fit","args":{"layer":"wallpaper","id":"../x","zoom":1.5}})",
	R"({"id":1,"cmd":"schedule-set","args":{"entries":[]}})",
	R"({"id":1,"cmd":"schedule-set","args":{"enabled":true,"entries":[{"at":"08:00","playlist":"day"}]}})",
	R"({"id":1,"cmd":"schedule-set","args":{"enabled":true,"entries":[{"at":"8:00","playlist":"day"},{"at":"20:00","playlist":"night"}]}})",
	R"({"id":1,"cmd":"schedule-set","args":{"enabled":true,"entries":[{"at":"24:00","playlist":"day"},{"at":"20:00","playlist":"night"}]}})",
	R"({"id":1,"cmd":"schedule-set","args":{"enabled":true,"entries":[{"at":"08:00","playlist":"../day"},{"at":"20:00","playlist":"night"}]}})",
	R"({"id":1,"cmd":"lanes-set","args":{"lanes":[{"id":"all","manual":"yes"}]}})",
	R"({"id":1,"cmd":"schedule-set","args":{"enabled":true,"entries":[{"at":"08:00","playlist":"day"},{"at":"08:00","playlist":"night"}]}})",
    };

    for (const auto& line : bad) {
	INFO ("input: " << line);
	CHECK_FALSE (CommandDispatcher::parse (line).command.has_value ());
    }
}

TEST_CASE ("rotation and transport verbs are validated", "[dispatcher]") {
    const std::string good[] = {
	R"({"id":1,"cmd":"next"})",
	R"({"id":1,"cmd":"prev"})",
	R"({"id":1,"cmd":"ping"})",
	R"({"id":1,"cmd":"pause"})",
	R"({"id":1,"cmd":"resume"})",
	R"({"id":1,"cmd":"release-outputs"})",
	R"({"id":1,"cmd":"acquire-outputs"})",
	R"({"id":1,"cmd":"rotate-set","args":{"entries":[]}})", // clears the set
	R"({"id":1,"cmd":"rotate-set","args":{"entries":[{"id":"123","ui_id":"p9","cc":[1,1,1,0]}],)"
	R"("interval_s":900,"order":"shuffle","avoid_repeat":true,"enabled":true,"label":"chill"}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","ui_id":"preset-oled-black"}})",
	R"({"id":1,"cmd":"set-fullscreen","args":{"behavior":"off"}})",
	R"({"id":1,"cmd":"set-fullscreen","args":{"behavior":"pause"}})",
	R"({"id":1,"cmd":"set-fullscreen","args":{"behavior":"stop"}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","fullscreen_behavior":"stop"}})",
	// the leg-A boolean alias and the three-state value may both ride one show
	R"({"id":1,"cmd":"show","args":{"id":"1","fullscreen_pause":true,"fullscreen_behavior":"off"}})",
	R"({"id":1,"cmd":"rotate-set","args":{"entries":[{"id":"123","fullscreen_behavior":"pause"}]}})",
	R"({"id":1,"cmd":"set-fps","args":{"fps":1}})",
	R"({"id":1,"cmd":"set-fps","args":{"fps":480}})",
	R"({"id":1,"cmd":"set-parallax","args":{"enabled":false}})",
	R"({"id":1,"cmd":"set-particles","args":{"enabled":true}})",
	R"({"id":1,"cmd":"set-overlay","args":{"text":"A\nFPS 60.0  CPU 3.1% of 32 cores","corner":"bottom-right","visible":true}})",
	R"({"id":1,"cmd":"set-overlay","args":{"corner":"top-left"}})",
	R"({"id":1,"cmd":"set-overlay","args":{"visible":false}})",
	R"({"id":1,"cmd":"set-overlay","args":{"text":""}})",
	R"({"id":1,"cmd":"set-fullscreen-ignore","args":{"app_ids":[]}})",
	R"({"id":1,"cmd":"set-fullscreen-ignore","args":{"app_ids":["steam","org.mozilla.firefox"]}})",
    };

    for (const auto& line : good) {
	INFO ("input: " << line);
	CHECK (CommandDispatcher::parse (line).command.has_value ());
    }

    const std::string bad[] = {
	R"({"id":1,"cmd":"rotate-set"})",
	R"({"id":1,"cmd":"rotate-set","args":{"entries":"nope"}})",
	R"({"id":1,"cmd":"rotate-set","args":{"entries":[{"ui_id":"x"}]}})",
	R"({"id":1,"cmd":"rotate-set","args":{"entries":[{"id":"../etc"}]}})",
	R"({"id":1,"cmd":"rotate-set","args":{"entries":[{"id":"1","volume":900}]}})",
	R"({"id":1,"cmd":"rotate-set","args":{"entries":[],"interval_s":5}})",
	R"({"id":1,"cmd":"rotate-set","args":{"entries":[],"order":"alphabetical"}})",
	R"({"id":1,"cmd":"rotate-set","args":{"entries":[],"enabled":"yes"}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","ui_id":42}})",
	R"({"id":1,"cmd":"set-fullscreen"})",
	R"({"id":1,"cmd":"set-fullscreen","args":{}})",
	R"({"id":1,"cmd":"set-fullscreen","args":{"behavior":"halt"}})",
	R"({"id":1,"cmd":"set-fullscreen","args":{"behavior":true}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","fullscreen_behavior":"halt"}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","fullscreen_behavior":false}})",
	R"({"id":1,"cmd":"rotate-set","args":{"entries":[{"id":"1","fullscreen_behavior":"nope"}]}})",
	// LIVE globals: each handler indexes its arg directly, so absence must be caught here
	R"({"id":1,"cmd":"set-fps"})",
	R"({"id":1,"cmd":"set-fps","args":{"fps":0}})",
	R"({"id":1,"cmd":"set-fps","args":{"fps":481}})",
	R"({"id":1,"cmd":"set-fps","args":{"fps":59.94}})",
	R"({"id":1,"cmd":"set-parallax"})",
	R"({"id":1,"cmd":"set-parallax","args":{"enabled":"yes"}})",
	R"({"id":1,"cmd":"set-particles","args":{}})",
	R"({"id":1,"cmd":"set-overlay"})",
	R"({"id":1,"cmd":"set-overlay","args":{}})",
	R"({"id":1,"cmd":"set-overlay","args":{"corner":"middle"}})",
	R"({"id":1,"cmd":"set-overlay","args":{"corner":3}})",
	R"({"id":1,"cmd":"set-overlay","args":{"visible":"yes"}})",
	R"({"id":1,"cmd":"set-overlay","args":{"text":42}})",
	R"({"id":1,"cmd":"set-overlay","args":{"text":"café"}})",
	R"({"id":1,"cmd":"set-overlay","args":{"text":"tab\there"}})",
	R"({"id":1,"cmd":"set-fullscreen-ignore"})",
	R"({"id":1,"cmd":"set-fullscreen-ignore","args":{"app_ids":"steam"}})",
	R"({"id":1,"cmd":"set-fullscreen-ignore","args":{"app_ids":[""]}})",
	R"({"id":1,"cmd":"set-fullscreen-ignore","args":{"app_ids":[42]}})",
    };

    for (const auto& line : bad) {
	INFO ("input: " << line);
	CHECK_FALSE (CommandDispatcher::parse (line).command.has_value ());
    }
}

TEST_CASE ("playlist and lane verbs are validated", "[dispatcher]") {
    const std::string good[] = {
	R"({"id":1,"cmd":"playlist-set","args":{"slug":"default","entries":[]}})",
	R"({"id":1,"cmd":"playlist-set","args":{"slug":"chill","entries":[{"id":"123","ui_id":"p9"}],)"
	R"("interval_s":900,"order":"static","avoid_repeat":false,"label":"Chill"}})",
	R"({"id":1,"cmd":"playlist-set","args":{"slug":"big","entries":[{"id":"1"}],"part":2,"of":3}})",
	R"({"id":1,"cmd":"playlist-set","args":{"slug":"big","entries":[{"id":"1"}],"of":2}})",
	R"({"id":1,"cmd":"lanes-set","args":{"lanes":[{"id":"all"}]}})",
	R"({"id":1,"cmd":"lanes-set","args":{"lanes":[{"id":"all","playlist":"chill","enabled":true,)"
	R"("group":[{"make":"LG","model":"27GP950","serial":"1","name":"DP-2"}],"fit":{"zoom":1.5,"pan_x":0.2}}]}})",
	R"({"id":1,"cmd":"next","args":{"lane":"all"}})",
	R"({"id":1,"cmd":"show","args":{"id":"1","lane":"all"}})",
    };

    for (const auto& line : good) {
	INFO ("input: " << line);
	CHECK (CommandDispatcher::parse (line).command.has_value ());
    }

    const std::string bad[] = {
	R"({"id":1,"cmd":"playlist-set"})",
	R"({"id":1,"cmd":"playlist-set","args":{"entries":[]}})",
	R"({"id":1,"cmd":"playlist-set","args":{"slug":"../x","entries":[]}})",
	R"({"id":1,"cmd":"playlist-set","args":{"slug":"a","entries":"nope"}})",
	R"({"id":1,"cmd":"playlist-set","args":{"slug":"a","entries":[{"ui_id":"x"}]}})",
	R"({"id":1,"cmd":"playlist-set","args":{"slug":"a","entries":[],"avoid_repeat":"yes"}})",
	R"({"id":1,"cmd":"playlist-set","args":{"slug":"a","entries":[],"part":2}})",
	R"({"id":1,"cmd":"playlist-set","args":{"slug":"a","entries":[],"part":3,"of":2}})",
	R"({"id":1,"cmd":"playlist-set","args":{"slug":"a","entries":[],"part":0,"of":2}})",
	R"({"id":1,"cmd":"playlist-set","args":{"slug":"a","entries":[],"order":"alphabetical"}})",
	R"({"id":1,"cmd":"playlist-set","args":{"slug":"a","entries":[],"interval_s":5}})",
	R"({"id":1,"cmd":"lanes-set"})",
	R"({"id":1,"cmd":"lanes-set","args":{"lanes":[]}})",
	R"({"id":1,"cmd":"lanes-set","args":{"lanes":[{"playlist":"x"}]}})",
	R"({"id":1,"cmd":"lanes-set","args":{"lanes":[{"id":"all","playlist":"../x"}]}})",
	R"({"id":1,"cmd":"lanes-set","args":{"lanes":[{"id":"all","enabled":"yes"}]}})",
	R"({"id":1,"cmd":"lanes-set","args":{"lanes":[{"id":"all","group":"DP-2"}]}})",
	R"({"id":1,"cmd":"lanes-set","args":{"lanes":[{"id":"all","fit":{"zoom":"big"}}]}})",
	R"({"id":1,"cmd":"lanes-set","args":{"lanes":[{"id":"all","fit":{"zoom":3}}]}})",
	R"({"id":1,"cmd":"lanes-set","args":{"lanes":[{"id":"all","fit":{"pan_x":-2}}]}})",
	R"({"id":1,"cmd":"lanes-set","args":{"lanes":[{"id":"all","fit":7}]}})",
	R"({"id":1,"cmd":"next","args":{"lane":"../x"}})",
    };

    for (const auto& line : bad) {
	INFO ("input: " << line);
	CHECK_FALSE (CommandDispatcher::parse (line).command.has_value ());
    }
}

TEST_CASE ("response builders produce the documented shapes", "[dispatcher]") {
    const auto ack = json::parse (CommandDispatcher::accepted (5));
    CHECK (ack["id"] == 5);
    CHECK (ack["ok"] == true);
    CHECK (ack["status"] == "accepted");

    const auto done = json::parse (CommandDispatcher::done (5, { { "screens", 3 } }));
    CHECK (done["status"] == "done");
    CHECK (done["result"]["screens"] == 3);

    const auto fail = json::parse (CommandDispatcher::failure (5, "nope"));
    CHECK (fail["ok"] == false);
    CHECK (fail["error"] == "nope");
}
