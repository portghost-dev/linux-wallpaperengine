#include "Protocol.h"

#include <cstring>

using namespace WallpaperEngine::WebHelper;

namespace {
/**
 * Every scalar goes through memcpy into a byte buffer rather than a reinterpret_cast over
 * a struct. Same instruction count after optimization, no alignment or strict-aliasing
 * question to answer, and float/double keep their exact bit pattern.
 */
template <typename T> void append (std::vector<uint8_t>& out, const T value) {
    const auto offset = out.size ();
    out.resize (offset + sizeof (T));
    std::memcpy (out.data () + offset, &value, sizeof (T));
}
} // namespace

void PayloadWriter::u8 (const uint8_t value) { append (this->m_data, value); }

void PayloadWriter::u32 (const uint32_t value) { append (this->m_data, value); }

void PayloadWriter::i32 (const int32_t value) { append (this->m_data, value); }

void PayloadWriter::f32 (const float value) { append (this->m_data, value); }

void PayloadWriter::f64 (const double value) { append (this->m_data, value); }

void PayloadWriter::str (const std::string& value) {
    this->u32 (static_cast<uint32_t> (value.size ()));
    this->bytes (value.data (), value.size ());
}

void PayloadWriter::bytes (const void* data, const size_t length) {
    if (length == 0) {
	return;
    }

    const auto offset = this->m_data.size ();
    this->m_data.resize (offset + length);
    std::memcpy (this->m_data.data () + offset, data, length);
}

bool PayloadReader::take (const size_t length) {
    if (this->m_failed || this->remaining () < length) {
	this->m_failed = true;
	return false;
    }

    return true;
}

uint8_t PayloadReader::u8 () {
    if (!this->take (sizeof (uint8_t))) {
	return 0;
    }

    const uint8_t value = this->m_data[this->m_offset];
    this->m_offset += sizeof (uint8_t);

    return value;
}

uint32_t PayloadReader::u32 () {
    if (!this->take (sizeof (uint32_t))) {
	return 0;
    }

    uint32_t value = 0;
    std::memcpy (&value, this->m_data.data () + this->m_offset, sizeof (value));
    this->m_offset += sizeof (value);

    return value;
}

int32_t PayloadReader::i32 () {
    if (!this->take (sizeof (int32_t))) {
	return 0;
    }

    int32_t value = 0;
    std::memcpy (&value, this->m_data.data () + this->m_offset, sizeof (value));
    this->m_offset += sizeof (value);

    return value;
}

float PayloadReader::f32 () {
    if (!this->take (sizeof (float))) {
	return 0.0f;
    }

    float value = 0.0f;
    std::memcpy (&value, this->m_data.data () + this->m_offset, sizeof (value));
    this->m_offset += sizeof (value);

    return value;
}

double PayloadReader::f64 () {
    if (!this->take (sizeof (double))) {
	return 0.0;
    }

    double value = 0.0;
    std::memcpy (&value, this->m_data.data () + this->m_offset, sizeof (value));
    this->m_offset += sizeof (value);

    return value;
}

std::string PayloadReader::str () {
    const uint32_t length = this->u32 ();

    if (!this->take (length)) {
	return {};
    }

    std::string value (reinterpret_cast<const char*> (this->m_data.data () + this->m_offset), length);
    this->m_offset += length;

    return value;
}

void PayloadReader::bytes (void* out, const size_t length) {
    if (!this->take (length)) {
	return;
    }

    std::memcpy (out, this->m_data.data () + this->m_offset, length);
    this->m_offset += length;
}

std::vector<uint8_t> WallpaperEngine::WebHelper::frame (const MessageType type, const std::vector<uint8_t>& payload) {
    std::vector<uint8_t> out;
    out.reserve (sizeof (MessageHeader) + payload.size ());

    append (out, static_cast<uint32_t> (payload.size ()));
    append (out, static_cast<uint16_t> (type));
    append (out, static_cast<uint16_t> (0));

    out.insert (out.end (), payload.begin (), payload.end ());

    return out;
}

std::vector<uint8_t> WallpaperEngine::WebHelper::encodeCreate (
    const InstanceId id, const std::string& workshopId, const std::string& file, const uint32_t width,
    const uint32_t height, const uint32_t framerate
) {
    PayloadWriter writer;
    writer.u32 (id);
    writer.u32 (width);
    writer.u32 (height);
    writer.u32 (framerate);
    writer.str (workshopId);
    writer.str (file);

    return frame (MessageType::Create, writer.data ());
}

std::vector<uint8_t>
WallpaperEngine::WebHelper::encodeResize (const InstanceId id, const uint32_t width, const uint32_t height) {
    PayloadWriter writer;
    writer.u32 (id);
    writer.u32 (width);
    writer.u32 (height);

    return frame (MessageType::Resize, writer.data ());
}

std::vector<uint8_t>
WallpaperEngine::WebHelper::encodeMouseMove (const InstanceId id, const int32_t x, const int32_t y) {
    PayloadWriter writer;
    writer.u32 (id);
    writer.i32 (x);
    writer.i32 (y);

    return frame (MessageType::MouseMove, writer.data ());
}

std::vector<uint8_t> WallpaperEngine::WebHelper::encodeMouseClick (
    const InstanceId id, const int32_t x, const int32_t y, const MouseButton button, const bool released
) {
    PayloadWriter writer;
    writer.u32 (id);
    writer.i32 (x);
    writer.i32 (y);
    writer.u8 (static_cast<uint8_t> (button));
    writer.u8 (released ? 1 : 0);

    return frame (MessageType::MouseClick, writer.data ());
}

void WallpaperEngine::WebHelper::writeProperty (PayloadWriter& writer, const PropertyValue& property) {
    writer.str (property.key);
    writer.u8 (static_cast<uint8_t> (property.kind));

    switch (property.kind) {
	case PropertyValue::Kind::Boolean:
	    writer.u8 (property.booleanValue ? 1 : 0);
	    break;
	case PropertyValue::Kind::Number:
	    writer.f64 (property.numberValue);
	    break;
	case PropertyValue::Kind::String:
	    writer.str (property.stringValue);
	    break;
    }
}

bool WallpaperEngine::WebHelper::readProperty (PayloadReader& reader, PropertyValue& property) {
    property.key = reader.str ();
    property.kind = static_cast<PropertyValue::Kind> (reader.u8 ());

    switch (property.kind) {
	case PropertyValue::Kind::Boolean:
	    property.booleanValue = reader.u8 () != 0;
	    return true;
	case PropertyValue::Kind::Number:
	    property.numberValue = reader.f64 ();
	    return true;
	case PropertyValue::Kind::String:
	    property.stringValue = reader.str ();
	    return true;
	default:
	    // an unknown kind means the payload can no longer be walked: the value's width is
	    // decided by the kind byte, so guessing would desynchronise every later field
	    return false;
    }
}

std::vector<uint8_t>
WallpaperEngine::WebHelper::encodeInjectProperties (const InstanceId id, const std::vector<PropertyValue>& properties) {
    PayloadWriter writer;
    writer.u32 (id);
    writer.u32 (static_cast<uint32_t> (properties.size ()));

    for (const auto& property : properties) {
	writeProperty (writer, property);
    }

    return frame (MessageType::InjectProperties, writer.data ());
}

std::vector<uint8_t>
WallpaperEngine::WebHelper::encodeSetProperty (const InstanceId id, const PropertyValue& property) {
    PayloadWriter writer;
    writer.u32 (id);
    writeProperty (writer, property);

    return frame (MessageType::SetProperty, writer.data ());
}

std::vector<uint8_t> WallpaperEngine::WebHelper::encodeAudioSpectrum (const InstanceId id, const float* bands) {
    PayloadWriter writer;
    writer.u32 (id);
    // fixed-size block, no per-message count: AUDIO_BANDS is part of the contract, and
    // the whole point of this verb is that nothing here builds a string at frame rate
    writer.bytes (bands, AUDIO_BANDS * sizeof (float));

    return frame (MessageType::AudioSpectrum, writer.data ());
}

std::vector<uint8_t> WallpaperEngine::WebHelper::encodeDestroy (const InstanceId id) {
    PayloadWriter writer;
    writer.u32 (id);

    return frame (MessageType::Destroy, writer.data ());
}

std::vector<uint8_t> WallpaperEngine::WebHelper::encodeFrameReady (
    const InstanceId id, const uint32_t generation, const uint32_t sequence, const uint32_t width, const uint32_t height
) {
    PayloadWriter writer;
    writer.u32 (id);
    writer.u32 (generation);
    writer.u32 (sequence);
    writer.u32 (width);
    writer.u32 (height);

    return frame (MessageType::FrameReady, writer.data ());
}

std::vector<uint8_t> WallpaperEngine::WebHelper::encodePageLoaded (const InstanceId id) {
    PayloadWriter writer;
    writer.u32 (id);

    return frame (MessageType::PageLoaded, writer.data ());
}

std::vector<uint8_t> WallpaperEngine::WebHelper::encodePageFailed (
    const InstanceId id, const int32_t errorCode, const std::string& errorText, const std::string& failedUrl
) {
    PayloadWriter writer;
    writer.u32 (id);
    writer.i32 (errorCode);
    writer.str (errorText);
    writer.str (failedUrl);

    return frame (MessageType::PageFailed, writer.data ());
}
