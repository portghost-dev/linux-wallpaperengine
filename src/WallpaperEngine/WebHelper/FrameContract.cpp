
#include "FrameContract.h"

#include "WallpaperEngine/Logging/Log.h"

#include <cerrno>
#include <cstring>

#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

using namespace WallpaperEngine::WebHelper;

namespace {
/** shm_open + ftruncate + mmap, shared by both halves */
void* mapShared (const std::string& name, const size_t bytes, const bool create, std::string& error) {
    const int flags = create ? (O_RDWR | O_CREAT | O_EXCL) : O_RDONLY;
    // 0600: the helper and the engine are the same uid and nobody else has any business
    // reading a wallpaper's pixels
    const int fd = shm_open (name.c_str (), flags, S_IRUSR | S_IWUSR);

    if (fd < 0) {
	error = "shm_open(" + name + ") failed: " + std::strerror (errno);
	return nullptr;
    }

    if (create && ftruncate (fd, static_cast<off_t> (bytes)) != 0) {
	error = "ftruncate(" + name + ") failed: " + std::strerror (errno);
	close (fd);
	shm_unlink (name.c_str ());
	return nullptr;
    }

    void* mapping = mmap (nullptr, bytes, create ? (PROT_READ | PROT_WRITE) : PROT_READ, MAP_SHARED, fd, 0);

    // the mapping keeps the object alive; the descriptor has done its job either way
    close (fd);

    if (mapping == MAP_FAILED) {
	error = "mmap(" + name + ") failed: " + std::strerror (errno);

	if (create) {
	    shm_unlink (name.c_str ());
	}

	return nullptr;
    }

    return mapping;
}
} // namespace

FrameWriter::~FrameWriter () { this->release (); }

uint8_t* FrameWriter::slot (const uint32_t index) const {
    return static_cast<uint8_t*> (this->m_mapping) + FRAME_PIXEL_OFFSET
	+ static_cast<size_t> (index) * this->m_slotBytes;
}

bool FrameWriter::allocate (
    const int helperPid, const uint32_t instanceId, const uint32_t width, const uint32_t height
) {
    if (width == 0 || height == 0) {
	this->m_error = "refusing to allocate a zero-sized frame buffer";
	return false;
    }

    // The generation advances even if the allocation below fails, so a failed resize can
    // never leave a later success reusing a name a reader might still hold.
    const uint32_t generation = this->m_generation + 1;
    const std::string name = frameShmName (helperPid, instanceId, generation);
    const size_t bytes = frameMappingBytes (width, height);

    std::string error;
    void* mapping = mapShared (name, bytes, true, error);

    if (mapping == nullptr) {
	this->m_error = error;
	this->m_generation = generation;
	return false;
    }

    // Drop the previous allocation only once the new one is known good. unlink() removes
    // the NAME, not the object: an engine that is mid-upload against the old generation
    // keeps a perfectly valid mapping and only releases it when it moves to this one.
    this->release ();

    this->m_mapping = mapping;
    this->m_mappingBytes = bytes;
    this->m_name = name;
    this->m_generation = generation;
    this->m_width = width;
    this->m_height = height;
    this->m_slotBytes = width * height * FRAME_BYTES_PER_PIXEL;
    // a fresh object starts empty, so the sequence restarts with it
    this->m_sequence = 0;
    this->m_error.clear ();

    FrameHeader* header = this->header ();
    header->magic = FRAME_MAGIC;
    header->version = FRAME_VERSION;
    header->width = width;
    header->height = height;
    header->stride = width * FRAME_BYTES_PER_PIXEL;
    header->slotBytes = this->m_slotBytes;
    header->pixelOffset = FRAME_PIXEL_OFFSET;
    header->generation = generation;
    // published LAST of the header fields, and with release ordering, so a reader that
    // sees a non-zero sequence is guaranteed to see the geometry that describes it
    header->sequence.store (0, std::memory_order_release);

    return true;
}

void FrameWriter::release () {
    if (this->m_mapping == nullptr) {
	return;
    }

    munmap (this->m_mapping, this->m_mappingBytes);
    shm_unlink (this->m_name.c_str ());

    this->m_mapping = nullptr;
    this->m_mappingBytes = 0;
    this->m_name.clear ();
    this->m_slotBytes = 0;
}

bool FrameWriter::publish (const void* buffer, const uint32_t width, const uint32_t height) {
    if (this->m_mapping == nullptr || buffer == nullptr) {
	return false;
    }

    if (width != this->m_width || height != this->m_height) {
	// CEF can still deliver one paint at the previous size just after WasResized().
	// Dropping it is correct: the slot is sized for the new geometry, and a mis-shaped
	// image is worse than one missing frame.
	this->m_dropped++;
	return false;
    }

    // THE SEQLOCK WRITE. Fill the slot the reader is NOT looking at, then publish.
    //
    //   the reader takes slot (sequence % 2), so the back slot is ((sequence + 1) % 2)
    //
    // The store must be RELEASE so that every byte of the memcpy is visible to any reader
    // that observes the new sequence with acquire. Getting this ordering wrong is invisible
    // on x86 and corrupts frames on anything weaker.
    const uint32_t next = this->m_sequence + 1;

    std::memcpy (this->slot (next % FRAME_SLOT_COUNT), buffer, this->m_slotBytes);

    this->header ()->sequence.store (next, std::memory_order_release);
    this->m_sequence = next;

    return true;
}

FrameReader::~FrameReader () { this->close (); }

const uint8_t* FrameReader::slot (const uint32_t index) const {
    return static_cast<const uint8_t*> (this->m_mapping) + this->m_pixelOffset
	+ static_cast<size_t> (index) * this->m_slotBytes;
}

bool FrameReader::open (const std::string& name) {
    this->close ();

    // The geometry lives in the header, but the header lives in the mapping, so map the
    // header first at a minimal size, then map the whole thing once the size is known.
    std::string error;
    void* probe = mapShared (name, FRAME_PIXEL_OFFSET, false, error);

    if (probe == nullptr) {
	this->m_error = error;
	return false;
    }

    const FrameHeader* probeHeader = static_cast<const FrameHeader*> (probe);
    const uint32_t magic = probeHeader->magic;
    const uint32_t version = probeHeader->version;
    const uint32_t width = probeHeader->width;
    const uint32_t height = probeHeader->height;
    const uint32_t stride = probeHeader->stride;
    const uint32_t slotBytes = probeHeader->slotBytes;
    const uint32_t pixelOffset = probeHeader->pixelOffset;
    const uint32_t generation = probeHeader->generation;

    munmap (probe, FRAME_PIXEL_OFFSET);

    // Everything below is written by ANOTHER PROCESS, so it is validated rather than
    // trusted. A mismatch means a stale object, a half-upgraded pair of binaries, or
    // something that is not ours at all - never a reason to compute an offset from it.
    if (magic != FRAME_MAGIC) {
	this->m_error = "not an lwe frame buffer: " + name;
	return false;
    }

    if (version != FRAME_VERSION) {
	this->m_error = "frame contract version mismatch on " + name + ": object is " + std::to_string (version)
	    + ", this build speaks " + std::to_string (FRAME_VERSION);
	return false;
    }

    if (width == 0 || height == 0 || pixelOffset != FRAME_PIXEL_OFFSET || stride != width * FRAME_BYTES_PER_PIXEL
	|| slotBytes != stride * height) {
	this->m_error = "frame buffer geometry is inconsistent on " + name;
	return false;
    }

    const size_t bytes = frameMappingBytes (width, height);
    void* mapping = mapShared (name, bytes, false, error);

    if (mapping == nullptr) {
	this->m_error = error;
	return false;
    }

    this->m_mapping = mapping;
    this->m_mappingBytes = bytes;
    this->m_name = name;
    this->m_width = width;
    this->m_height = height;
    this->m_stride = stride;
    this->m_slotBytes = slotBytes;
    this->m_pixelOffset = pixelOffset;
    this->m_generation = generation;
    this->m_lastSequence = 0;
    this->m_error.clear ();

    return true;
}

void FrameReader::close () {
    if (this->m_mapping != nullptr) {
	munmap (this->m_mapping, this->m_mappingBytes);
    }

    this->m_mapping = nullptr;
    this->m_mappingBytes = 0;
    this->m_name.clear ();
    this->m_width = 0;
    this->m_height = 0;
    this->m_stride = 0;
    this->m_slotBytes = 0;
    this->m_pixelOffset = 0;
    this->m_generation = 0;
    this->m_lastSequence = 0;
}

bool FrameReader::consume (const std::function<void (const void*, uint32_t, uint32_t)>& sink) {
    if (this->m_mapping == nullptr) {
	return false;
    }

    const FrameHeader* header = this->header ();

    for (int attempt = 0; attempt < MAX_READ_ATTEMPTS; attempt++) {
	// 1. LATCH. Acquire pairs with the writer's release store, so observing this value
	//    also makes every pixel byte the writer copied before it visible to us.
	const uint32_t sequence = header->sequence.load (std::memory_order_acquire);

	// 2. Nothing has ever been published into this generation.
	if (sequence == 0) {
	    return false;
	}

	if (sequence == this->m_lastSequence) {
	    return false;
	}

	// 4. READ the slot the writer published. The writer is by now filling the OTHER
	//    slot, which is the whole point of the double buffer: in the common case this
	//    copy races with nothing at all.
	sink (this->slot (sequence % FRAME_SLOT_COUNT), this->m_width, this->m_height);

	// 5. RE-CHECK. If the sequence still reads what we latched, the writer has not
	//    published anything since, so it cannot yet have come back around to the slot
	//    we just read, and the read was clean.
	//
	//    If it MOVED, the writer published at least one more frame while we were
	//    reading. One more frame means it is now filling the slot we were reading out
	//    of, so our bytes may be a mix of two frames. Discard and retry - the acquire
	//    load at the top of the next iteration re-latches whatever is current now.
	if (header->sequence.load (std::memory_order_acquire) == sequence) {
	    this->m_lastSequence = sequence;
	    this->m_accepted++;

	    return true;
	}

	this->m_retries++;
    }

    // Lapped every time. The caller keeps the texture it already has; a frame that is one
    // vsync stale is invisible, and blocking the render thread to chase the writer is the
    // thing this design exists to avoid.
    this->m_abandoned++;

    return false;
}
