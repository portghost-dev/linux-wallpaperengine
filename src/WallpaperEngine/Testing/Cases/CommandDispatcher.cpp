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
