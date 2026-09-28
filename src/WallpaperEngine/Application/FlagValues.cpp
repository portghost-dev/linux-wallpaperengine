#include "FlagValues.h"

#include <cctype>
#include <cmath>
#include <cstdlib>
#include <numbers>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <vector>

namespace {
std::optional<double> decimal (const std::string& text) {
    if (text.empty () || std::isspace (static_cast<unsigned char> (text.front ()))
	|| std::isspace (static_cast<unsigned char> (text.back ()))) {
	return std::nullopt;
    }

    char* end = nullptr;
    const double value = std::strtod (text.c_str (), &end);

    if (end != text.c_str () + text.size () || !std::isfinite (value)) {
	return std::nullopt;
    }

    return value;
}
} // namespace

namespace WallpaperEngine::Application::FlagValues {
float clampFactor (const std::string& flag, const std::string& text) {
    const auto value = decimal (text);

    if (!value.has_value () || *value > 4.0) {
	throw std::runtime_error (flag + " takes a number up to 4, where 0 or below is no cap; got " + text);
    }

    return *value <= 0.0 ? 0.0f : static_cast<float> (*value);
}

bool onOff (const std::string& flag, const std::string& text) {
    if (text == "on") {
	return true;
    }

    if (text == "off") {
	return false;
    }

    throw std::runtime_error (flag + " takes on or off; got " + text);
}

bool autoFull (const std::string& flag, const std::string& text) {
    if (text == "auto") {
	return true;
    }

    if (text == "full") {
	return false;
    }

    throw std::runtime_error (flag + " takes auto or full; got " + text);
}

std::string videoDecode (const std::string& flag, const std::string& text) {
    if (text == "software") {
	return "no";
    }

    if (text == "auto") {
	return "auto";
    }

    throw std::runtime_error (flag + " takes software or auto; got " + text);
}

glm::vec4 color (const std::string& flag, const std::string& text) {
    const std::runtime_error refusal (
	flag + " takes \"brightness contrast saturation hue\", each 0 to 4 and hue -180 to 180 degrees; got " + text
    );

    if (text.empty () || text.front () == ' ' || text.back () == ' ') {
	throw refusal;
    }

    std::vector<double> values;
    std::string::size_type start = 0;

    while (start != std::string::npos) {
	const auto space = text.find (' ', start);
	const auto value = decimal (text.substr (start, space == std::string::npos ? space : space - start));

	if (!value.has_value ()) {
	    throw refusal;
	}

	values.push_back (*value);
	start = space == std::string::npos ? space : text.find_first_not_of (' ', space);
    }

    if (values.size () != 4) {
	throw refusal;
    }

    for (int i = 0; i < 3; i++) {
	if (values[i] < 0.0 || values[i] > 4.0) {
	    throw refusal;
	}
    }

    if (values[3] < -180.0 || values[3] > 180.0) {
	throw refusal;
    }

    return { static_cast<float> (values[0]), static_cast<float> (values[1]), static_cast<float> (values[2]),
	     static_cast<float> (values[3] * std::numbers::pi / 180.0) };
}

float decimalInRange (const std::string& flag, const std::string& text, const double lo, const double hi) {
    const auto value = decimal (text);

    if (!value.has_value () || *value < lo || *value > hi) {
	std::ostringstream message;

	message << flag << " takes a number from " << lo << " to " << hi << "; got " << text;
	throw std::runtime_error (message.str ());
    }

    return static_cast<float> (*value);
}

int watchdogSeconds (const std::string& flag, const std::string& text) {
    std::string digits = text;
    int unit = 1;

    if (digits.ends_with ('s')) {
	digits.pop_back ();
    } else if (digits.ends_with ('m')) {
	unit = 60;
	digits.pop_back ();
    } else if (digits.ends_with ('h')) {
	unit = 3600;
	digits.pop_back ();
    }

    bool whole = !digits.empty ();
    int count = 0;

    for (const char c : digits) {
	if (c < '0' || c > '9' || count > 86400) {
	    whole = false;
	    break;
	}

	count = count * 10 + (c - '0');
    }

    if (!whole || count > 86400 / unit) {
	throw std::runtime_error (
	    flag + " takes seconds from 0 to 86400, or a number with s, m or h up to 24h; got " + text
	);
    }

    return count * unit;
}

float milliseconds (const std::string& flag, const std::string& text) {
    std::string number = text;

    if (number.ends_with ("ms")) {
	number.erase (number.size () - 2);
    }

    const auto value = decimal (number);

    if (!value.has_value () || *value < 0.0 || *value > 500.0) {
	throw std::runtime_error (flag + " takes milliseconds from 0 to 500; got " + text);
    }

    return static_cast<float> (*value);
}

std::filesystem::path path (const std::string& flag, const std::string& text) {
    if (text.empty ()) {
	throw std::runtime_error (flag + " takes a path; got an empty value");
    }

    return text;
}
} // namespace WallpaperEngine::Application::FlagValues
