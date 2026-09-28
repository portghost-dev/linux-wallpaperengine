#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_exception.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>

#include <set>
#include <stdexcept>
#include <string>

#include "WallpaperEngine/Application/DebugSwitches.h"

using namespace WallpaperEngine::Application;
using Catch::Matchers::ContainsSubstring;
using Catch::Matchers::EndsWith;
using Catch::Matchers::Message;
using Catch::Matchers::MessageMatches;
using Catch::Matchers::StartsWith;

namespace {
void checkSets (const std::string& token, const std::string& variable, const std::string& setTo) {
    CAPTURE (token);
    const auto action = DebugSwitches::resolve (token);
    CHECK (action.variable == variable);
    CHECK (action.setTo.has_value ());
    CHECK (action.setTo.value_or ("") == setTo);
}

void checkUnsets (const std::string& token, const std::string& variable) {
    CAPTURE (token);
    const auto action = DebugSwitches::resolve (token);
    CHECK (action.variable == variable);
    CHECK_FALSE (action.setTo.has_value ());
}

void checkRefused (const std::string& name, const std::string& value) {
    CAPTURE (name, value);
    CHECK_THROWS_MATCHES (
	DebugSwitches::resolve (name + "=" + value), std::runtime_error,
	MessageMatches (StartsWith (name + " takes ") && EndsWith ("; got " + value))
    );
}
} // namespace

TEST_CASE ("the debugging switch table has 81 rows with unique plain names and unique variables", "[debug]") {
    const auto& rows = DebugSwitches::table ();
    std::set<std::string> names;
    std::set<std::string> variables;

    for (const auto& row : rows) {
	names.insert (row.name);
	variables.insert (row.variable);
    }

    CHECK (rows.size () == 81);
    CHECK (names.size () == rows.size ());
    CHECK (variables.size () == rows.size ());
}

TEST_CASE ("resolve maps on and off for presence switches and for inverted switches", "[debug]") {
    checkSets ("audit=on", "LWE_AUDIT", "1");
    checkUnsets ("audit=off", "LWE_AUDIT");
    checkRefused ("audit", "yes");

    checkSets ("bloom=off", "LWE_NOBLOOM", "1");
    checkUnsets ("bloom=on", "LWE_NOBLOOM");
    checkRefused ("bloom", "1");
}

TEST_CASE ("resolve writes the exact token each exact-token variable is compared with", "[debug]") {
    checkSets ("buffersharing=off", "LWE_FBOPOOL", "0");
    checkSets ("shapes=off", "LWE_SHAPES", "0");
    checkSets ("skippedobjects=hide", "LWE_SKIPGATE", "0");
    checkSets ("frontface=counterclockwise", "LWE_FRONTFACE", "ccw");
    checkSets ("trailtiming=exact", "LWE_TRAILMODE", "exact");
    checkSets ("buffersharingstats=on", "LWE_POOL_HWM", "1");
    checkRefused ("frontface", "ccw");
}

TEST_CASE ("resolve maps the words of word switches to their values", "[debug]") {
    checkSets ("animationtiming=legacy", "LWE_ANIMFRACTION", "0");
    checkSets ("cropoffset=reverse", "LWE_CROPOFF", "2");
    checkSets ("modeltint=blue", "LWE_TINTFIX", "2");
    checkUnsets ("weblog=warning", "LWE_CEFLOG");
    checkRefused ("weblog", "debug");
}

TEST_CASE ("resolve checks whole numbers against each switch's bounds", "[debug]") {
    checkSets ("webdebug=1", "LWE_CEFDEBUG", "1");
    checkSets ("webdebug=65535", "LWE_CEFDEBUG", "65535");
    checkRefused ("webdebug", "0");
    checkRefused ("webdebug", "65536");
    checkUnsets ("webdebug=off", "LWE_CEFDEBUG");

    checkSets ("overlaysize=8", "LWE_OVERLAY_SIZE", "8");
    checkSets ("overlaysize=200", "LWE_OVERLAY_SIZE", "200");
    checkRefused ("overlaysize", "7");
    checkRefused ("overlaysize", "201");

    checkSets ("sceneimageframe=4", "LWE_FBDUMP_FRAME", "4");
    checkRefused ("sceneimageframe", "3");
    checkSets ("sceneimageframe=2147483647", "LWE_FBDUMP_FRAME", "2147483647");
    checkRefused ("sceneimageframe", "2147483648");

    checkSets ("testtexturelimit=256", "LWE_TEXCAP", "256");
    checkRefused ("testtexturelimit", "255");
    checkUnsets ("testtexturelimit=default", "LWE_TEXCAP");
    checkSets ("testtexturelimit=2147483647", "LWE_TEXCAP", "2147483647");
    checkRefused ("testtexturelimit", "2147483648");

    checkSets ("hidelight=0", "LWE_KILLLIGHT", "0");
    checkUnsets ("hidelight=off", "LWE_KILLLIGHT");
    checkSets ("hidelight=2147483647", "LWE_KILLLIGHT", "2147483647");
    checkRefused ("hidelight", "2147483648");

    checkSets ("videobuffer=1", "LWE_MPV_DEMUX_MB", "1");
    checkRefused ("videobuffer", "0");
    checkSets ("videobuffer=2147483647", "LWE_MPV_DEMUX_MB", "2147483647");
    checkRefused ("videobuffer", "2147483648");

    checkSets ("videoextraframes=256", "LWE_MPV_EXTRA_FRAMES", "256");
    checkRefused ("videoextraframes", "257");

    checkSets ("videothreads=2147483647", "LWE_MPV_THREADS", "2147483647");
    checkRefused ("videothreads", "2147483648");

    checkSets ("passprobe=2147483647", "LWE_PASSPROBE", "2147483647");
    checkRefused ("passprobe", "2147483648");
}

TEST_CASE ("resolve sets text and structured values as given once they fit their rule", "[debug]") {
    checkSets ("overlaytext=hello world", "LWE_OVERLAY_TEXT", "hello world");

    checkSets ("webcrashlimit=3,60000,300000", "LWE_WEB_CRASHGUARD", "3,60000,300000");
    checkRefused ("webcrashlimit", "3,60000");
    checkSets ("webcrashlimit=1,0,0", "LWE_WEB_CRASHGUARD", "1,0,0");
    checkSets (
	"webcrashlimit=2147483647,2147483647,2147483647", "LWE_WEB_CRASHGUARD", "2147483647,2147483647,2147483647"
    );
    checkRefused ("webcrashlimit", "0,60000,300000");
    checkRefused ("webcrashlimit", "2147483648,0,0");
    checkRefused ("webcrashlimit", "1,9223372036854775808,0");
    checkRefused ("webcrashlimit", "1,2147483648,0");
    checkRefused ("webcrashlimit", "1,0,2147483648");

    checkSets ("mouseposition=0.5,0.25", "LWE_MOUSE_POS", "0.5,0.25");
    checkRefused ("mouseposition", "1.5,0");
    checkUnsets ("mouseposition=off", "LWE_MOUSE_POS");
    checkSets ("mouseposition=0,0", "LWE_MOUSE_POS", "0,0");
    checkRefused ("mouseposition", "-0.1,0");
    checkRefused ("mouseposition", "0x0.1,0");

    checkSets ("objectpixels=0 0 10 10", "LWE_OBJPROBE", "0 0 10 10");
    checkSets ("objectpixels=0 0 10 10 2", "LWE_OBJPROBE", "0 0 10 10 2");
    checkRefused ("objectpixels", "0 0 10");
    checkSets ("objectpixels=0 0 2147483647 1 2147483447", "LWE_OBJPROBE", "0 0 2147483647 1 2147483447");
    checkRefused ("objectpixels", "0 0 10 10 2147483448");
    checkRefused ("objectpixels", "10 0 5 10");
    checkRefused ("objectpixels", "0 10 10 5");

    checkSets ("testresolution=3840x2160", "LWE_CLAMPOUTPUT", "3840x2160");
    checkRefused ("testresolution", "0x10");
    checkUnsets ("testresolution=default", "LWE_CLAMPOUTPUT");
    checkSets ("testresolution=2147483647x1", "LWE_CLAMPOUTPUT", "2147483647x1");
    checkRefused ("testresolution", "2147483648x1");

    checkSets ("shaderoption=FOO_BAR=1", "LWE_FORCECOMBO", "FOO_BAR=1");
    checkRefused ("shaderoption", "=1");
    checkRefused ("shaderoption", "FOO=x");
    checkSets ("shaderoption=_A1=2147483647", "LWE_FORCECOMBO", "_A1=2147483647");
    checkRefused ("shaderoption", "A=2147483648");
    checkRefused ("shaderoption", "1FOO=1");
    checkRefused ("shaderoption", "GL_FOO=1");
    checkRefused ("shaderoption", "gl_foo=1");
}

TEST_CASE ("resolve gives webidletime as a whole number of milliseconds", "[debug]") {
    checkSets ("webidletime=1500", "LWE_WEB_IDLE_EXIT_MS", "1500");
    checkSets ("webidletime=2s", "LWE_WEB_IDLE_EXIT_MS", "2000");
    checkSets ("webidletime=1500ms", "LWE_WEB_IDLE_EXIT_MS", "1500");
    checkRefused ("webidletime", "1.5s");
    checkSets ("webidletime=9223372036854775807", "LWE_WEB_IDLE_EXIT_MS", "9223372036854775807");
    checkSets ("webidletime=9223372036854775s", "LWE_WEB_IDLE_EXIT_MS", "9223372036854775000");
    checkRefused ("webidletime", "9223372036854775808");
    checkRefused ("webidletime", "9223372036854776s");
}

TEST_CASE ("a switch's words win over its rule", "[debug]") {
    checkUnsets ("sceneimage=off", "LWE_FBDUMP");
    checkUnsets ("overlayfont=default", "LWE_OVERLAY_FONT");
    checkUnsets ("shadersource=off", "LWE_SHADERDUMP_MATCH");
    checkUnsets ("webhelpersocket=default", "LWE_WEB_SOCKET");
}

TEST_CASE ("resolve refuses a malformed token, an unknown switch and a value outside the rule", "[debug]") {
    CHECK_THROWS_MATCHES (
	DebugSwitches::resolve ("audit"), std::runtime_error, Message ("--debug takes <switch>=<value>; got audit")
    );
    CHECK_THROWS_MATCHES (
	DebugSwitches::resolve ("audit="), std::runtime_error, Message ("--debug takes <switch>=<value>; got audit=")
    );
    CHECK_THROWS_MATCHES (
	DebugSwitches::resolve ("=on"), std::runtime_error, Message ("--debug takes <switch>=<value>; got =on")
    );
    CHECK_THROWS_MATCHES (
	DebugSwitches::resolve ("nosuch=on"), std::runtime_error,
	Message ("unknown debugging switch nosuch; --help-debug lists them")
    );
    CHECK_THROWS_MATCHES (
	DebugSwitches::resolve ("webdebug=0"), std::runtime_error,
	Message ("webdebug takes a port from 1 to 65535 | off; got 0")
    );
}

TEST_CASE ("helpText starts with the unsupported notice and has an entry for every switch", "[debug]") {
    const std::string text = DebugSwitches::helpText ();

    CHECK_THAT (text, StartsWith ("Unsupported debugging switches."));

    for (const auto& row : DebugSwitches::table ()) {
	CAPTURE (row.name);
	CHECK_THAT (text, ContainsSubstring ("\n  " + row.name + " <"));
    }
}
