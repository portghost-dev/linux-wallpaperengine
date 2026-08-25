#include "DynamicValueParser.h"

#include "UserSettingParser.h"
#include "WallpaperEngine/Data/Model/DynamicValue.h"

using namespace WallpaperEngine::Data::Parsers;

DynamicValueUniquePtr DynamicValueParser::parse (const json& data, const Properties& properties, bool expectColor) {
    auto value = std::make_unique<DynamicValue> ();
    auto valueIt = data;
    std::optional<std::string> scriptSource = std::nullopt;
    std::optional<json> scriptPropsJson = std::nullopt;

    if (data.is_object ()) {
	const auto user = data.optional ("user");
	const auto script = data.optional ("script");
	valueIt = data.require ("value", "User setting must have a value");

	if (script.has_value () && !script->is_null ()) {
	    scriptSource = script->get<std::string> ();
	    scriptPropsJson = data.optional ("scriptproperties");
	}
    }

    // actual value parsing
    if (valueIt.is_string ()) {
	if (expectColor) {
	    value->update (Builders::ColorBuilder::parse (valueIt), DynamicValue::UpdateSource::Initialization);
	} else {
	    std::string str = valueIt;
	    int size = Builders::VectorBuilder::preparseSize (str);

	    if (size == 1) {
		// scalar? text value?
		std::size_t parsed = 0;
		try {
		    float f = std::stof (str, &parsed);

		    if (parsed == str.size ()) {
			value->update (f, DynamicValue::UpdateSource::Initialization);
		    } else {
			value->update (str, DynamicValue::UpdateSource::Initialization);
		    }
		} catch (const std::exception&) {
		    value->update (str, DynamicValue::UpdateSource::Initialization);
		}
	    } else if (size == 2) {
		value->update (static_cast<glm::vec2> (valueIt), DynamicValue::UpdateSource::Initialization);
	    } else if (size == 3) {
		value->update (static_cast<glm::vec3> (valueIt), DynamicValue::UpdateSource::Initialization);
	    } else {
		value->update (static_cast<glm::vec4> (valueIt), DynamicValue::UpdateSource::Initialization);
	    }
	}
    } else if (valueIt.is_number_integer ()) {
	value->update (valueIt.get<int> (), DynamicValue::UpdateSource::Initialization);
    } else if (valueIt.is_number_float ()) {
	value->update (valueIt.get<float> (), DynamicValue::UpdateSource::Initialization);
    } else if (valueIt.is_boolean ()) {
	value->update (valueIt.get<bool> (), DynamicValue::UpdateSource::Initialization);
    } else if (valueIt.is_null ()) {
	// null value with no connection to property
	value->update (DynamicValue::UpdateSource::Initialization);
    }

    if (scriptSource.has_value ()) {
	std::map<std::string, UserSettingUniquePtr> scriptProps;

	if (scriptPropsJson.has_value () && scriptPropsJson->is_object ()) {
	    for (const auto& [key, propData] : scriptPropsJson->items ()) {
		scriptProps[key] = UserSettingParser::parse (propData, properties);
	    }
	}

	value->setProperties (std::move (scriptProps));
	value->setScriptSource (scriptSource.value ());
    }

    if (data.is_object ()) {
	const auto animationIt = data.optional ("animation");

	if (animationIt.has_value () && animationIt->is_object ()) {
	    value->setAnimation (DynamicValueParser::parseAnimation (*animationIt, value->getVec4 ()));
	}
    }

    return value;
}

namespace {
AnimationTangent parseTangent (const WallpaperEngine::Data::Parsers::json& data) {
    AnimationTangent tangent;
    const auto enabledIt = data.optional ("enabled");
    const auto xIt = data.optional ("x");
    const auto yIt = data.optional ("y");

    tangent.enabled = enabledIt.has_value () && enabledIt->is_boolean () && enabledIt->get<bool> ();
    tangent.x = xIt.has_value () && xIt->is_number () ? xIt->get<float> () : 0.0f;
    tangent.y = yIt.has_value () && yIt->is_number () ? yIt->get<float> () : 0.0f;

    return tangent;
}

std::vector<AnimationKeyframe> parseChannel (const WallpaperEngine::Data::Parsers::json& data) {
    std::vector<AnimationKeyframe> keyframes;

    if (!data.is_array ()) {
	return keyframes;
    }

    keyframes.reserve (data.size ());

    for (const auto& cur : data) {
	AnimationKeyframe keyframe;
	const auto frameIt = cur.optional ("frame");
	const auto valueIt = cur.optional ("value");
	const auto backIt = cur.optional ("back");
	const auto frontIt = cur.optional ("front");

	keyframe.frame = frameIt.has_value () && frameIt->is_number () ? frameIt->get<float> () : 0.0f;
	keyframe.value = valueIt.has_value () && valueIt->is_number () ? valueIt->get<float> () : 0.0f;
	keyframe.back = backIt.has_value () && backIt->is_object () ? parseTangent (*backIt) : AnimationTangent {};
	keyframe.front = frontIt.has_value () && frontIt->is_object () ? parseTangent (*frontIt) : AnimationTangent {};

	keyframes.push_back (keyframe);
    }

    std::ranges::sort (keyframes, {}, &AnimationKeyframe::frame);

    return keyframes;
}
} // namespace

AnimationTimeline DynamicValueParser::parseAnimation (const json& data, const glm::vec4& baseValue) {
    AnimationTimeline timeline;
    timeline.baseValue = baseValue;

    const auto relativeIt = data.optional ("relative");
    timeline.relative = relativeIt.has_value () && relativeIt->is_boolean () && relativeIt->get<bool> ();

    const auto optionsIt = data.optional ("options");

    if (optionsIt.has_value () && optionsIt->is_object ()) {
	const auto fpsIt = optionsIt->optional ("fps");
	const auto lengthIt = optionsIt->optional ("length");
	const auto modeIt = optionsIt->optional ("mode");

	timeline.fps = fpsIt.has_value () && fpsIt->is_number () ? fpsIt->get<float> () : 30.0f;
	timeline.length = lengthIt.has_value () && lengthIt->is_number () ? lengthIt->get<float> () : 0.0f;

	if (modeIt.has_value () && modeIt->is_string ()) {
	    const std::string mode = modeIt->get<std::string> ();

	    if (mode == "single") {
		timeline.mode = AnimationMode::Single;
	    } else if (mode == "loop") {
		timeline.mode = AnimationMode::Loop;
	    } else if (mode == "mirror") {
		timeline.mode = AnimationMode::Mirror;
	    }
	}
    }

    for (int index = 0; index < 4; ++index) {
	const auto channelIt = data.optional ("c" + std::to_string (index));

	if (!channelIt.has_value () || !channelIt->is_array ()) {
	    break;
	}

	timeline.channels.push_back (parseChannel (*channelIt));
    }

    return timeline;
}