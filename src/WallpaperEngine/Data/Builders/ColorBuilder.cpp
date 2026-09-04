#include "ColorBuilder.h"
#include "VectorBuilder.h"

#include <algorithm>
#include <glm/vec3.hpp>

const WallpaperEngine::Data::Model::Color WallpaperEngine::Data::Builders::ColorBuilder::White
    = WallpaperEngine::Data::Model::Color (1.0f, 1.0f, 1.0f, 1.0f);
const WallpaperEngine::Data::Model::Color WallpaperEngine::Data::Builders::ColorBuilder::Black
    = WallpaperEngine::Data::Model::Color (0.0f, 0.0f, 0.0f, 1.0f);

WallpaperEngine::Data::Model::Color
WallpaperEngine::Data::Builders::ColorBuilder::parse (const std::string& value, float alpha) {
    auto copy = value;

    // replace the actual separators with spaces to normalize them
    if (copy.find (',') != std::string::npos) {
	// replace comma separator with spaces so it's
	std::ranges::replace (copy, ',', ' ');
    }

    // hex colors should be converted to int colors
    if (copy.find ('#') == 0) {
	auto number = copy.substr (1);

	// expand short css notation into the right one
	if (number.size () == 3 || number.size () == 4) {
	    std::string expanded;
	    for (const char digit : number) {
		expanded += digit;
		expanded += digit;
	    }
	    number = expanded;
	} else if (number.size () != 6 && number.size () != 8) {
	    sLog.exception ("Invalid CSS color notation for ", value);
	}

	if (number.find_first_not_of ("0123456789abcdefABCDEF") != std::string::npos) {
	    sLog.exception ("Invalid CSS color notation for ", value);
	}

	// parse hex color
	const auto color = std::stoul (number, nullptr, 16);
	const bool hasAlpha = number.size () == 8;
	const auto rgb = hasAlpha ? color >> 8 : color;

	return WallpaperEngine::Data::Model::Color (
	    (rgb >> 16 & 0xFF) / 255.0f, (rgb >> 8 & 0xFF) / 255.0f, (rgb & 0xFF) / 255.0f,
	    hasAlpha ? (color & 0xFF) / 255.0f : alpha
	);
    }

    int vectorSize = VectorBuilder::preparseSize (copy);

    if (vectorSize != 3 && vectorSize != 4) {
	throw std::invalid_argument ("Invalid color value");
    }

    if (copy.find ('.') == std::string::npos) {
	const auto final = vectorSize == 3 ? glm::ivec4 (VectorBuilder::parse<glm::ivec3> (copy), alpha * 255)
					   : VectorBuilder::parse<glm::ivec4> (copy);

	if (final.r <= 1 && final.g <= 1 && final.b <= 1 && (vectorSize == 3 || final.a <= 1)) {
	    return WallpaperEngine::Data::Model::Color (
		static_cast<float> (final.r), static_cast<float> (final.g), static_cast<float> (final.b),
		vectorSize == 3 ? alpha : static_cast<float> (final.a)
	    );
	}

	return { final.r / 255.0f, final.g / 255.0f, final.b / 255.0f, final.a / 255.0f };
    }

    return Model::Color (
	vectorSize == 3 ? glm::vec4 (VectorBuilder::parse<glm::vec3> (copy), alpha)
			: VectorBuilder::parse<glm::vec4> (copy)
    );
}