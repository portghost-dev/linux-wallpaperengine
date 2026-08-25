#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>

namespace WallpaperEngine::WebHelper {
/** 'L' 'W' 'E' 'F' - a mapping that does not start with this is not ours */
inline constexpr uint32_t FRAME_MAGIC = 0x4645574cu;

/** bumped whenever the header layout below changes */
inline constexpr uint32_t FRAME_VERSION = 1;

inline constexpr uint32_t FRAME_SLOT_COUNT = 2;

/** BGRA8 */
inline constexpr uint32_t FRAME_BYTES_PER_PIXEL = 4;

/**
 * Header at offset 0 of the shared mapping. The pixel slots follow it, slot i starting at
 * `pixelOffset + i * slotBytes`. Every field except `sequence` is written once, before the
 * reader can possibly have opened the object; `sequence` is the only concurrently mutated
 * word.
 */
struct FrameHeader {
    uint32_t magic;
    uint32_t version;
    /** viewport size the slots were sized for */
    uint32_t width;
    uint32_t height;
    /** bytes per row, width * FRAME_BYTES_PER_PIXEL (no padding today, kept explicit) */
    uint32_t stride;
    uint32_t slotBytes;
    uint32_t pixelOffset;
    /**
     * Which allocation this is, monotonic from 1 per instance. It is part of the shm
     * object's NAME as well, so a reader can never mistake a recycled name for the object
     * it was reading - a resize from A to B and back to A produces three distinct names.
     */
    uint32_t generation;
    /**
     * Frames published so far. 0 = nothing yet. The live slot is `sequence % 2`.
     * Free-standing atomic so the type is identical in both processes and no lock or
     * futex is implied - a plain 32-bit load/store with the right fences.
     */
    std::atomic<uint32_t> sequence;
};

static_assert (
    std::atomic<uint32_t>::is_always_lock_free, "the seqlock word must be lock-free to be shared across processes"
);

/** slot 0 begins here; keeps the pixel data on a comfortable alignment boundary */
inline constexpr uint32_t FRAME_PIXEL_OFFSET = 64;

static_assert (sizeof (FrameHeader) <= FRAME_PIXEL_OFFSET, "the header must fit before the first slot");

[[nodiscard]] inline size_t frameMappingBytes (const uint32_t width, const uint32_t height) {
    return FRAME_PIXEL_OFFSET + static_cast<size_t> (width) * height * FRAME_BYTES_PER_PIXEL * FRAME_SLOT_COUNT;
}

[[nodiscard]] inline std::string
frameShmName (const int helperPid, const uint32_t instanceId, const uint32_t generation) {
    return "/lwe-web-" + std::to_string (helperPid) + "-" + std::to_string (instanceId) + "-"
	+ std::to_string (generation);
}

/**
 * Writer half, owned by the helper's WebInstance.
 *
 * THREADING. Both allocate() and publish() run on CEF's UI thread, which - because
 * lwe-web-service leaves multi_threaded_message_loop false and drives CEF with
 * CefDoMessageLoopWork() from main - is the helper's main thread. OnPaint is delivered
 * inside that pump, so a resize can never interleave with a publish and no lock is needed
 * on this side either. If that ever changes, this is the invariant that breaks first.
 */
class FrameWriter {
public:
    FrameWriter () = default;
    ~FrameWriter ();

    FrameWriter (const FrameWriter&) = delete;
    FrameWriter& operator= (const FrameWriter&) = delete;

    bool allocate (int helperPid, uint32_t instanceId, uint32_t width, uint32_t height);

    /** unmap and unlink; safe to call when nothing is allocated */
    void release ();

    /**
     * Copy one CEF paint into the back slot and publish it.
     *
     * A paint whose size disagrees with the current allocation is DROPPED: CEF can deliver
     * one more frame at the old size after WasResized(), and writing it would either
     * overrun the slot or hand the engine a mis-shaped image.
     *
     * @return true if the frame was published.
     */
    bool publish (const void* buffer, uint32_t width, uint32_t height);

    [[nodiscard]] bool isOpen () const { return this->m_mapping != nullptr; }
    [[nodiscard]] uint32_t generation () const { return this->m_generation; }
    [[nodiscard]] uint32_t sequence () const { return this->m_sequence; }
    [[nodiscard]] uint32_t width () const { return this->m_width; }
    [[nodiscard]] uint32_t height () const { return this->m_height; }
    [[nodiscard]] const std::string& name () const { return this->m_name; }
    [[nodiscard]] const std::string& error () const { return this->m_error; }
    [[nodiscard]] uint64_t droppedFrames () const { return this->m_dropped; }

private:
    [[nodiscard]] FrameHeader* header () const { return static_cast<FrameHeader*> (this->m_mapping); }
    [[nodiscard]] uint8_t* slot (uint32_t index) const;

    void* m_mapping = nullptr;
    size_t m_mappingBytes = 0;
    std::string m_name;
    std::string m_error;
    uint32_t m_generation = 0;
    uint32_t m_sequence = 0;
    uint32_t m_width = 0;
    uint32_t m_height = 0;
    uint32_t m_slotBytes = 0;
    uint64_t m_dropped = 0;
};

/**
 * Reader half, owned by the engine's CWeb (and by the standalone frame probe).
 *
 * Maps the object READ-ONLY: the engine has no business writing to the helper's buffer,
 * and a read-only mapping makes that structural rather than a convention.
 */
class FrameReader {
public:
    static constexpr int MAX_READ_ATTEMPTS = 4;

    FrameReader () = default;
    ~FrameReader ();

    FrameReader (const FrameReader&) = delete;
    FrameReader& operator= (const FrameReader&) = delete;

    bool open (const std::string& name);

    void close ();

    [[nodiscard]] bool isOpen () const { return this->m_mapping != nullptr; }
    [[nodiscard]] const std::string& name () const { return this->m_name; }
    [[nodiscard]] const std::string& error () const { return this->m_error; }
    [[nodiscard]] uint32_t width () const { return this->m_width; }
    [[nodiscard]] uint32_t height () const { return this->m_height; }
    [[nodiscard]] uint32_t stride () const { return this->m_stride; }
    [[nodiscard]] uint32_t generation () const { return this->m_generation; }

    [[nodiscard]] uint32_t lastSequence () const { return this->m_lastSequence; }
    [[nodiscard]] uint64_t retries () const { return this->m_retries; }
    [[nodiscard]] uint64_t abandoned () const { return this->m_abandoned; }
    [[nodiscard]] uint64_t accepted () const { return this->m_accepted; }

    bool consume (const std::function<void (const void* pixels, uint32_t width, uint32_t height)>& sink);

private:
    [[nodiscard]] const FrameHeader* header () const { return static_cast<const FrameHeader*> (this->m_mapping); }
    [[nodiscard]] const uint8_t* slot (uint32_t index) const;

    void* m_mapping = nullptr;
    size_t m_mappingBytes = 0;
    std::string m_name;
    std::string m_error;
    uint32_t m_width = 0;
    uint32_t m_height = 0;
    uint32_t m_stride = 0;
    uint32_t m_slotBytes = 0;
    uint32_t m_pixelOffset = 0;
    uint32_t m_generation = 0;
    uint32_t m_lastSequence = 0;
    uint64_t m_retries = 0;
    uint64_t m_abandoned = 0;
    uint64_t m_accepted = 0;
};
}
