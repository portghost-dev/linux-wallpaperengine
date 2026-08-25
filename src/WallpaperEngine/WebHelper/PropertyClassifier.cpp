#include "PropertyClassifier.h"

#include "WallpaperEngine/Data/Model/Property.h"
#include "WallpaperEngine/Data/Model/UserSetting.h"

#include <sstream>

using namespace WallpaperEngine::WebHelper;
using namespace WallpaperEngine::Data::Model;

std::optional<PropertyValue> WallpaperEngine::WebHelper::classifyProperty (
    const std::string& key, const WallpaperEngine::Data::Model::Property& property
) {
    // PropertyText is a display-label with no JS-visible value
    if (dynamic_cast<const PropertyText*> (&property) != nullptr) {
	return std::nullopt;
    }

    PropertyValue value;
    value.key = key;

    if (const auto* color = dynamic_cast<const PropertyColor*> (&property); color != nullptr) {
	// WE convention: "r g b" space-separated floats in the 0-1 range. A string, not three
	// numbers, because that is the shape the pages parse.
	const auto& vector = color->getVec4 ();
	std::ostringstream stream;
	stream << vector.r << " " << vector.g << " " << vector.b;
	value.kind = PropertyValue::Kind::String;
	value.stringValue = stream.str ();
    } else if (const auto* boolean = dynamic_cast<const PropertyBoolean*> (&property); boolean != nullptr) {
	value.kind = PropertyValue::Kind::Boolean;
	value.booleanValue = boolean->getBool ();
    } else if (const auto* slider = dynamic_cast<const PropertySlider*> (&property); slider != nullptr) {
	value.kind = PropertyValue::Kind::Number;
	value.numberValue = slider->getFloat ();
    } else {
	// Combos and text inputs stay strings. Combo option values are authored as strings in
	// every observed wallpaper, and JS arithmetic coerces the numeric-looking ones.
	value.kind = PropertyValue::Kind::String;
	value.stringValue = property.toString ();
    }

    return value;
}

std::vector<PropertyValue>
WallpaperEngine::WebHelper::classifyProperties (const WallpaperEngine::Data::Model::Properties& properties) {
    std::vector<PropertyValue> values;
    values.reserve (properties.size ());

    for (const auto& [key, property] : properties) {
	if (property == nullptr) {
	    continue;
	}

	if (auto value = classifyProperty (key, *property); value.has_value ()) {
	    values.push_back (std::move (*value));
	}
    }

    return values;
}
