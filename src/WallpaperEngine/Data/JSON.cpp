#include "JSON.h"

#include "WallpaperEngine/Data/Parsers/UserSettingParser.h"

using namespace WallpaperEngine::Data::JSON;
using namespace WallpaperEngine::Data::Model;
using namespace WallpaperEngine::Data::Parsers;

UserSettingUniquePtr JsonExtensions::user (const std::string& key, const Properties& properties) const {
    const auto value = this->require (key, "User setting without default value must be present");

    return UserSettingParser::parse (value, properties);
}

UserSettingUniquePtr JsonExtensions::color (const std::string& key, const Properties& properties) const {
    const auto value = this->require (key, "User setting without default value must be present");

    return UserSettingParser::parse (value, properties, true);
}

namespace {
/** drops commas whose next non-whitespace char closes an array/object; string-aware */
std::string stripComments (const std::string& text) {
    std::string out = text;
    bool inString = false;
    bool escaped = false;

    for (size_t i = 0; i < out.size (); i++) {
	const char c = out[i];

	if (inString) {
	    if (escaped) {
		escaped = false;
	    } else if (c == '\\') {
		escaped = true;
	    } else if (c == '"') {
		inString = false;
	    }
	    continue;
	}

	if (c == '"') {
	    inString = true;
	    continue;
	}

	if (c == '/' && i + 1 < out.size () && out[i + 1] == '/') {
	    while (i < out.size () && out[i] != '\n') {
		out[i++] = ' ';
	    }
	    continue;
	}

	if (c == '/' && i + 1 < out.size () && out[i + 1] == '*') {
	    while (i + 1 < out.size () && !(out[i] == '*' && out[i + 1] == '/')) {
		if (out[i] != '\n') {
		    out[i] = ' ';
		}
		i++;
	    }
	    if (i + 1 < out.size ()) {
		out[i] = ' ';
		out[i + 1] = ' ';
		i++;
	    }
	    continue;
	}
    }

    return out;
}

std::string stripTrailingCommas (const std::string& text) {
    std::string out;
    out.reserve (text.size ());
    bool inString = false;
    bool escaped = false;

    for (size_t i = 0; i < text.size (); i++) {
	const char c = text[i];

	if (inString) {
	    out.push_back (c);
	    if (escaped) {
		escaped = false;
	    } else if (c == '\\') {
		escaped = true;
	    } else if (c == '"') {
		inString = false;
	    }
	    continue;
	}

	if (c == '"') {
	    inString = true;
	    out.push_back (c);
	    continue;
	}

	if (c == ',') {
	    size_t next = i + 1;
	    while (next < text.size () && std::isspace (static_cast<unsigned char> (text[next]))) {
		next++;
	    }
	    if (next < text.size () && (text[next] == ']' || text[next] == '}')) {
		continue;
	    }
	}

	out.push_back (c);
    }

    return out;
}
} // namespace

JSON WallpaperEngine::Data::JSON::parseLenient (const std::string& text) {
    try {
	// allow_exceptions = true, ignore_comments = true (WE tolerates both)
	return JSON::parse (text, nullptr, true, true);
    } catch (const nlohmann::detail::parse_error&) {
	sLog.error ("JSON strict parse failed - retrying with trailing commas stripped (WE-lenient)");
	return JSON::parse (stripTrailingCommas (stripComments (text)), nullptr, true, true);
    }
}
