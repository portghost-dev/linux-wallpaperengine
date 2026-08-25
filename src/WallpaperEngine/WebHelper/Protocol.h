#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace WallpaperEngine::WebHelper {
/** identifies one CWeb instance; 0 is never handed out and means "no instance" */
using InstanceId = uint32_t;

/** protocol revision, carried in the spawn config so a stale binary pair fails loudly */
inline constexpr uint32_t PROTOCOL_VERSION = 1;

enum class MessageType : uint16_t {
    Create = 0x0001,
    Resize = 0x0002,
    MouseMove = 0x0003,
    MouseClick = 0x0004,
    InjectProperties = 0x0005,
    SetProperty = 0x0006,
    AudioSpectrum = 0x0007,
    Destroy = 0x0008,

    /** announces a new shm generation, NOT a new frame; see encodeFrameReady below */
    FrameReady = 0x8001,
    PageLoaded = 0x8002,
    PageFailed = 0x8003,
};

[[nodiscard]] inline bool isCommand (const MessageType type) { return static_cast<uint16_t> (type) < 0x8000; }

/** how many audio bands cross the wire; the helper mirrors these into the page's 128 */
inline constexpr size_t AUDIO_BANDS = 64;

/** mouse buttons the engine forwards; CEF has more, the wallpaper path only uses these */
enum class MouseButton : uint8_t {
    Left = 0,
    Right = 1,
};

/**
 * A user property as the page must SEE it.
 *
 * The type travels with the value because Wallpaper Engine on Windows hands
 * applyUserProperties native JS types, and string-wrapping a boolean is a known
 * regression (the fluid sim assigns config.PAUSED raw, so "false" froze the simulation).
 * The engine classifies once, from the parsed Property, and the helper renders the JS.
 */
struct PropertyValue {
    enum class Kind : uint8_t {
	Boolean = 0,
	Number = 1,
	/** combos, text, and colors ("r g b" space-separated floats, the WE convention) */
	String = 2,
    };

    std::string key;
    Kind kind = Kind::String;
    bool booleanValue = false;
    double numberValue = 0.0;
    std::string stringValue;
};

/** fixed 8-byte prefix on every message; `length` counts the payload only */
struct MessageHeader {
    uint32_t length;
    uint16_t type;
    /** reserved, must be 0 - a place to put per-message bits without a new type */
    uint16_t flags;
};

static_assert (sizeof (MessageHeader) == 8, "the wire header must be exactly 8 bytes");

struct Message {
    MessageType type = MessageType::Create;
    std::vector<uint8_t> payload;
};

/**
 * Payload writer. Appends little-endian fields to a byte vector. Deliberately dumb - no
 * allocation strategy, no schema - so both peers share exactly one encoding.
 */
class PayloadWriter {
public:
    void u8 (uint8_t value);
    void u32 (uint32_t value);
    void i32 (int32_t value);
    void f32 (float value);
    void f64 (double value);
    /** u32 byte-length then the bytes; no terminator, no encoding assumption */
    void str (const std::string& value);
    /** raw block, length written by the caller (used for the fixed-size spectrum) */
    void bytes (const void* data, size_t length);

    [[nodiscard]] const std::vector<uint8_t>& data () const { return this->m_data; }

private:
    std::vector<uint8_t> m_data;
};

/**
 * Payload reader. Every accessor is bounds-checked; the first overrun sets a sticky
 * failure flag and all later reads return zero, so a caller can decode a whole message
 * and check ok() once instead of testing every field. A malformed message must never be
 * able to walk off the end of a buffer - the peer is same-uid, not trusted.
 */
class PayloadReader {
public:
    explicit PayloadReader (const std::vector<uint8_t>& payload) : m_data (payload) { }

    uint8_t u8 ();
    uint32_t u32 ();
    int32_t i32 ();
    float f32 ();
    double f64 ();
    std::string str ();
    /** copies exactly `length` bytes; fails (and writes nothing) if they are not there */
    void bytes (void* out, size_t length);

    [[nodiscard]] bool ok () const { return !this->m_failed; }
    [[nodiscard]] size_t remaining () const { return this->m_data.size () - this->m_offset; }

private:
    [[nodiscard]] bool take (size_t length);

    const std::vector<uint8_t>& m_data;
    size_t m_offset = 0;
    bool m_failed = false;
};

/** guard against a confused or hostile peer sizing us out of memory */
inline constexpr uint32_t MAX_PAYLOAD_BYTES = 1024 * 1024;

std::vector<uint8_t> encodeCreate (
    InstanceId id, const std::string& workshopId, const std::string& file, uint32_t width, uint32_t height,
    uint32_t framerate
);
std::vector<uint8_t> encodeResize (InstanceId id, uint32_t width, uint32_t height);
std::vector<uint8_t> encodeMouseMove (InstanceId id, int32_t x, int32_t y);
std::vector<uint8_t> encodeMouseClick (InstanceId id, int32_t x, int32_t y, MouseButton button, bool released);
std::vector<uint8_t> encodeInjectProperties (InstanceId id, const std::vector<PropertyValue>& properties);
std::vector<uint8_t> encodeSetProperty (InstanceId id, const PropertyValue& property);
std::vector<uint8_t> encodeAudioSpectrum (InstanceId id, const float* bands);
std::vector<uint8_t> encodeDestroy (InstanceId id);

std::vector<uint8_t>
encodeFrameReady (InstanceId id, uint32_t generation, uint32_t sequence, uint32_t width, uint32_t height);
std::vector<uint8_t> encodePageLoaded (InstanceId id);
std::vector<uint8_t>
encodePageFailed (InstanceId id, int32_t errorCode, const std::string& errorText, const std::string& failedUrl);

void writeProperty (PayloadWriter& writer, const PropertyValue& property);
/** returns false on an unknown kind; the reader's own sticky failure flag covers the rest */
[[nodiscard]] bool readProperty (PayloadReader& reader, PropertyValue& property);

std::vector<uint8_t> frame (MessageType type, const std::vector<uint8_t>& payload);
}
