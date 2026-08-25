#include "PuppetModel.h"

#include "WallpaperEngine/Logging/Log.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <glm/gtc/matrix_transform.hpp>

using namespace WallpaperEngine::Render::Objects;

namespace {

/** Bounded little-endian cursor; all reads are validated against the buffer */
struct Cursor {
    const char* data;
    size_t size;
    size_t off { 0 };
    bool ok { true };

    template <typename T> T read () {
	T value {};
	if (!ok || off + sizeof (T) > size) {
	    ok = false;
	    return value;
	}
	std::memcpy (&value, data + off, sizeof (T));
	off += sizeof (T);
	return value;
    }

    std::string readCString () {
	if (!ok) {
	    return {};
	}
	const auto* end = static_cast<const char*> (std::memchr (data + off, 0, size - off));
	if (end == nullptr) {
	    ok = false;
	    return {};
	}
	std::string value (data + off, end);
	off = static_cast<size_t> (end - data) + 1;
	return value;
    }

    void skip (size_t bytes) {
	if (off + bytes > size) {
	    ok = false;
	    return;
	}
	off += bytes;
    }
};

size_t findMarker (const std::vector<char>& data, const char* marker, size_t from = 0) {
    const size_t len = std::strlen (marker);
    for (size_t offset = from; offset + len <= data.size (); offset++) {
	if (std::memcmp (data.data () + offset, marker, len) == 0) {
	    return offset;
	}
    }
    return data.size ();
}

/** File matrices are D3D row-major with translation in row 3; a plain memcpy into
 *  column-major glm is exactly the transpose, which converts row-vector convention
 *  to column-vector convention (translation lands in column 3) */
glm::mat4 readFileMatrix (Cursor& cur) {
    glm::mat4 out (1.0f);
    float values[16];
    for (float& value : values) {
	value = cur.read<float> ();
    }
    if (cur.ok) {
	std::memcpy (&out, values, sizeof (values));
    }
    return out;
}

PuppetModel::PlaybackMode parseMode (const std::string& mode) {
    if (mode == "mirror") {
	return PuppetModel::PlaybackMode::Mirror;
    }
    if (mode == "single") {
	return PuppetModel::PlaybackMode::Single;
    }
    // "loop" and anything unknown (silent-default)
    return PuppetModel::PlaybackMode::Loop;
}

bool parseSkeleton (const std::vector<char>& data, PuppetModel& model) {
    const size_t mdls = findMarker (data, "MDLS");
    if (mdls >= data.size ()) {
	return false;
    }

    Cursor cur { data.data (), data.size (), mdls + 8 };
    cur.skip (1); // pad
    cur.read<uint32_t> (); // block end
    const auto boneCount = cur.read<uint32_t> ();
    cur.skip (1); // pad
    if (!cur.ok || boneCount == 0 || boneCount > 512) {
	return false;
    }

    model.bones.reserve (boneCount);
    std::vector<glm::mat4> bindWorld (boneCount);

    for (uint32_t i = 0; i < boneCount; i++) {
	cur.read<uint32_t> (); // per-bone flags (0/1 seen, semantics unknown)
	const auto parent = cur.read<int32_t> ();
	const auto matrixBytes = cur.read<uint32_t> ();
	if (!cur.ok || matrixBytes != 64 || parent >= static_cast<int32_t> (i)) {
	    if (cur.ok && parent >= static_cast<int32_t> (i)) {
		sLog.error ("Puppet bone ", i, " references forward parent ", parent);
	    }
	    model.bones.clear ();
	    return false;
	}
	const glm::mat4 bindLocal = readFileMatrix (cur);
	cur.skip (2); // pad
	if (!cur.ok) {
	    model.bones.clear ();
	    return false;
	}

	bindWorld[i] = parent >= 0 ? bindWorld[parent] * bindLocal : bindLocal;
	model.bones.push_back (
	    PuppetModel::Bone {
		.parent = parent, .bindLocal = bindLocal, .bindWorldInverse = glm::inverse (bindWorld[i]) }
	);
    }

    return true;
}

bool parseAnimations (const std::vector<char>& data, PuppetModel& model) {
    const size_t mdla = findMarker (data, "MDLA");
    if (mdla >= data.size () || model.bones.empty ()) {
	return false;
    }

    Cursor cur { data.data (), data.size (), mdla + 8 };
    cur.skip (1); // pad
    cur.read<uint32_t> (); // block end
    const auto animCount = cur.read<uint32_t> ();
    if (!cur.ok || animCount == 0 || animCount > 64) {
	return false;
    }

    for (uint32_t a = 0; a < animCount && cur.ok; a++) {
	if (a > 0) {
	    const size_t padStart = cur.off;
	    while (cur.off < cur.size && data[cur.off] == 0 && cur.off - padStart < 64) {
		cur.off++;
	    }
	    size_t chosen = cur.off;
	    for (size_t back = 0; back <= 3 && cur.off >= padStart + back; back++) {
		Cursor probe { cur.data, cur.size, cur.off - back };
		probe.read<uint32_t> (); // candidate id
		const auto zero = probe.read<uint32_t> ();
		probe.readCString (); // name (any UTF-8)
		const std::string mode = probe.readCString ();
		if (probe.ok && zero == 0 && (mode == "loop" || mode == "mirror" || mode == "single")) {
		    chosen = cur.off - back;
		    break;
		}
	    }
	    cur.off = chosen;
	}
	if (!cur.ok || cur.off + 8 >= cur.size) {
	    break;
	}

	PuppetModel::Clip clip;
	clip.id = cur.read<uint32_t> ();
	cur.read<uint32_t> (); // zero
	clip.name = cur.readCString ();
	clip.mode = parseMode (cur.readCString ());
	clip.fps = cur.read<float> ();
	clip.frameCount = cur.read<uint32_t> ();
	cur.read<uint32_t> (); // zero
	const auto channelCount = cur.read<uint32_t> ();
	if (!cur.ok || clip.frameCount == 0 || clip.fps <= 0.0f || channelCount == 0
	    || channelCount > model.bones.size ()) {
	    sLog.error ("Puppet animation ", a, " has unexpected framing, keeping ", model.clips.size (), " clips");
	    return !model.clips.empty ();
	}

	clip.channels.resize (channelCount);
	for (uint32_t c = 0; c < channelCount && cur.ok; c++) {
	    cur.read<uint32_t> (); // pad
	    const auto byteSize = cur.read<uint32_t> ();
	    // bound BEFORE reserving: a hostile byteSize must not drive a multi-GB reserve
	    if (!cur.ok || byteSize % 36 != 0 || byteSize == 0 || byteSize > cur.size - cur.off) {
		sLog.error ("Puppet animation ", a, " channel ", c, " framing broke");
		return !model.clips.empty ();
	    }
	    const size_t records = byteSize / 36;
	    auto& channel = clip.channels[c];
	    channel.reserve (records);
	    for (size_t r = 0; r < records && cur.ok; r++) {
		PuppetModel::Key key;
		key.position = { cur.read<float> (), cur.read<float> (), cur.read<float> () };
		key.rotation = { cur.read<float> (), cur.read<float> (), cur.read<float> () };
		key.scale = { cur.read<float> (), cur.read<float> (), cur.read<float> () };
		channel.push_back (key);
	    }
	}

	if (cur.ok) {
	    model.clips.push_back (std::move (clip));
	}
    }

    return !model.clips.empty ();
}

/** Wrap into [0, period) handling negative phases (rate can be user-driven negative) */
double wrapPhase (double phase, double period) {
    double cycle = std::fmod (phase, period);
    if (cycle < 0.0) {
	cycle += period;
    }
    return cycle;
}

float clipFrame (const PuppetModel::Clip& clip, double phase) {
    const auto frames = static_cast<double> (clip.frameCount);
    switch (clip.mode) {
	case PuppetModel::PlaybackMode::Single:
	    return static_cast<float> (std::clamp (phase, 0.0, frames));
	case PuppetModel::PlaybackMode::Mirror:
	    {
		const double cycle = wrapPhase (phase, frames * 2.0);
		return static_cast<float> (cycle <= frames ? cycle : frames * 2.0 - cycle);
	    }
	case PuppetModel::PlaybackMode::Loop:
	default:
	    return static_cast<float> (wrapPhase (phase, frames));
    }
}

PuppetModel::Key sampleChannel (const std::vector<PuppetModel::Key>& channel, float frame) {
    if (channel.empty ()) {
	return {};
    }
    const auto i0 = static_cast<size_t> (frame);
    const size_t i1 = std::min (i0 + 1, channel.size () - 1);
    const float t = frame - static_cast<float> (i0);
    const auto& a = channel[std::min (i0, channel.size () - 1)];
    const auto& b = channel[i1];
    return PuppetModel::Key { .position = glm::mix (a.position, b.position, t),
			      .rotation = glm::mix (a.rotation, b.rotation, t),
			      .scale = glm::mix (a.scale, b.scale, t) };
}

} // namespace

const PuppetModel::Clip* PuppetModel::findClip (uint32_t id) const {
    for (const auto& clip : clips) {
	if (clip.id == id) {
	    return &clip;
	}
    }
    return nullptr;
}

void PuppetModel::evaluateSkinning (
    const std::vector<ActiveLayer>& layers, double time, std::vector<glm::mat4>& out
) const {
    const size_t boneCount = bones.size ();
    out.resize (boneCount);

    std::vector<Key> accumulated (boneCount);
    std::vector<bool> hasRest (boneCount, false);

    for (const auto& layer : layers) {
	if (layer.clip == nullptr || layer.blend == 0.0f) {
	    continue;
	}
	const double phase = time * static_cast<double> (layer.clip->fps) * static_cast<double> (layer.rate);
	const float frame = clipFrame (*layer.clip, phase);

	for (size_t b = 0; b < boneCount && b < layer.clip->channels.size (); b++) {
	    const auto& channel = layer.clip->channels[b];
	    if (channel.empty ()) {
		continue;
	    }
	    const Key rest = channel.front ();
	    if (!hasRest[b]) {
		accumulated[b] = rest;
		hasRest[b] = true;
	    }
	    const Key key = sampleChannel (channel, frame);
	    accumulated[b].position += (key.position - rest.position) * layer.blend;
	    accumulated[b].rotation += (key.rotation - rest.rotation) * layer.blend;
	    accumulated[b].scale += (key.scale - rest.scale) * layer.blend;
	}
    }

    std::vector<glm::mat4> world (boneCount);
    for (size_t b = 0; b < boneCount; b++) {
	glm::mat4 local;
	if (hasRest[b]) {
	    const auto& key = accumulated[b];
	    local = glm::translate (glm::mat4 (1.0f), key.position);
	    local = glm::rotate (local, key.rotation.z, glm::vec3 (0, 0, 1));
	    local = glm::rotate (local, key.rotation.y, glm::vec3 (0, 1, 0));
	    local = glm::rotate (local, key.rotation.x, glm::vec3 (1, 0, 0));
	    local = glm::scale (local, key.scale);
	} else {
	    local = bones[b].bindLocal;
	}
	world[b] = bones[b].parent >= 0 ? world[bones[b].parent] * local : local;
	out[b] = world[b] * bones[b].bindWorldInverse;
    }
}

void PuppetModel::skinPositions (const std::vector<glm::mat4>& skin, std::vector<glm::vec3>& out) const {
    const size_t count = positions.size ();
    out.resize (count);
    for (size_t i = 0; i < count; i++) {
	const glm::vec4 pos (positions[i], 1.0f);
	const auto& idx = blendIndices[i];
	const auto& weight = blendWeights[i];
	glm::vec4 result (0.0f);
	for (int j = 0; j < 4; j++) {
	    const float w = weight[j];
	    if (w == 0.0f) {
		continue;
	    }
	    const uint32_t bone = idx[j];
	    if (bone < skin.size ()) {
		result += skin[bone] * pos * w;
	    }
	}
	out[i] = glm::vec3 (result);
    }
}

std::optional<PuppetModel> PuppetModel::parse (const std::vector<char>& data, std::string& error) {
    struct SkinnedLayout {
	size_t stride = 80;
	size_t idxOff = 40;
	size_t weightOff = 56;
	size_t uvOff = 72;
    };
    SkinnedLayout layout {};
    size_t vertexStride = 80;

    if (data.size () < 32 || std::memcmp (data.data (), "MDLV", 4) != 0) {
	error = "not an MDLV container";
	return std::nullopt;
    }

    uint32_t mdlvVersion = 0;
    for (size_t digit = 4; digit < 8 && digit < data.size (); digit++) {
	const char c = data[digit];
	if (c < '0' || c > '9') {
	    mdlvVersion = 0;
	    break;
	}
	mdlvVersion = mdlvVersion * 10 + static_cast<uint32_t> (c - '0');
    }

    // Mesh block: scan from the end of the material path for
    // [u32 vertexBytes][vertices][u32 indexBytes][u16 indices] with stride-80 vertices
    const size_t skeletonStart = findMarker (data, "MDLS");
    const size_t materialsEnd = [&data] () -> size_t {
	const size_t m = findMarker (data, "materials/");
	if (m >= data.size ()) {
	    return 9;
	}
	const auto* end = static_cast<const char*> (std::memchr (data.data () + m, 0, data.size () - m));
	return end != nullptr ? static_cast<size_t> (end - data.data ()) : 9;
    }();

    size_t verticesOffset = 0;
    uint32_t vertexBytes = 0;
    uint32_t indexBytes = 0;

    const auto structuredWalk = [&] () -> bool {
	constexpr uint32_t KNOWN_VERTEX_BITS = 0x0181002F;
	struct AttributeSize {
	    uint32_t bit;
	    size_t bytes;
	};
	constexpr AttributeSize ATTRIBUTES[] = {
	    { 0x00000002, 12 }, { 0x00000004, 16 }, { 0x00010000, 4 }, { 0x00800000, 16 },
	    { 0x01000000, 16 }, { 0x00000020, 16 }, { 0x00000008, 8 },
	};

	const size_t magicEnd = std::string_view (data.data (), data.size ()).find ('\0');
	if (magicEnd == std::string_view::npos) {
	    return false;
	}
	size_t offset = magicEnd + 1;

	const auto readU32 = [&] (uint32_t& out) -> bool {
	    if (offset + sizeof (uint32_t) > data.size ()) {
		return false;
	    }
	    std::memcpy (&out, data.data () + offset, sizeof (out));
	    offset += sizeof (uint32_t);
	    return true;
	};

	uint32_t headerTag = 0, materialCount = 0, submeshCount = 0;
	if (!readU32 (headerTag) || !readU32 (materialCount) || !readU32 (submeshCount)) {
	    return false;
	}
	if (submeshCount == 0 || submeshCount > 16 || materialCount == 0 || materialCount > 16) {
	    return false;
	}

	for (uint32_t record = 0; record < submeshCount; record++) {
	    for (uint32_t material = 0; material < materialCount; material++) {
		while (offset < data.size () && static_cast<unsigned char> (data[offset]) <= 0x20) {
		    offset++;
		}
		const auto* nameEnd
		    = static_cast<const char*> (std::memchr (data.data () + offset, 0, data.size () - offset));
		if (nameEnd == nullptr) {
		    return false;
		}
		offset = (nameEnd - data.data ()) + 1;
	    }

	    uint32_t flags = 0;
	    if (!readU32 (flags)) {
		return false;
	    }
	    if ((flags & 0x2u) != 0) {
		uint32_t extra = 0;
		if (!readU32 (extra)) {
		    return false;
		}
	    }
	    if (mdlvVersion >= 17) {
		offset += 6 * sizeof (float);
	    }

	    uint32_t tag = headerTag;
	    if (mdlvVersion >= 16 && !readU32 (tag)) {
		return false;
	    }
	    if ((tag & ~KNOWN_VERTEX_BITS) != 0) {
		return false;
	    }

	    SkinnedLayout recordLayout {};
	    size_t recordStride = 12; // implicit position
	    bool hasIndices = false, hasWeights = false, hasUv = false;
	    for (const auto& attribute : ATTRIBUTES) {
		if ((tag & attribute.bit) == 0) {
		    continue;
		}
		if (attribute.bit == 0x00800000u) {
		    recordLayout.idxOff = recordStride;
		    hasIndices = true;
		} else if (attribute.bit == 0x01000000u) {
		    recordLayout.weightOff = recordStride;
		    hasWeights = true;
		} else if (attribute.bit == 0x00000008u) {
		    recordLayout.uvOff = recordStride;
		    hasUv = true;
		}
		recordStride += attribute.bytes;
	    }
	    recordLayout.stride = recordStride;

	    uint32_t recordVertexBytes = 0;
	    if (!readU32 (recordVertexBytes) || recordVertexBytes == 0 || recordVertexBytes % recordStride != 0
		|| offset + recordVertexBytes > data.size ()) {
		return false;
	    }
	    const size_t recordVerticesOffset = offset;
	    offset += recordVertexBytes;

	    const size_t indexWidth = (flags & 0x1u) != 0 ? 4 : 2;
	    uint32_t recordIndexBytes = 0;
	    if (!readU32 (recordIndexBytes) || recordIndexBytes == 0 || recordIndexBytes % (indexWidth * 3) != 0
		|| offset + recordIndexBytes > data.size ()) {
		return false;
	    }
	    offset += recordIndexBytes;

	    if (hasIndices && hasWeights && hasUv) {
		if (indexWidth != 2) {
		    sLog.error ("Puppet mesh uses 32-bit indices - unsupported, falling back");
		    return false;
		}
		verticesOffset = recordVerticesOffset;
		vertexBytes = recordVertexBytes;
		indexBytes = recordIndexBytes;
		vertexStride = recordStride;
		layout = recordLayout;
		return true;
	    }
	}
	return false;
    };

    const bool structuredFound = structuredWalk ();

    const auto scanRange = [&] (size_t from, size_t to) {
	for (size_t offset = from; offset + sizeof (uint32_t) < skeletonStart && offset < to; offset++) {
	    uint32_t candidate = 0;
	    std::memcpy (&candidate, data.data () + offset, sizeof (candidate));
	    if (candidate == 0 || candidate % vertexStride != 0) {
		continue;
	    }
	    const size_t indexLengthOffset = offset + sizeof (uint32_t) + candidate;
	    if (indexLengthOffset + sizeof (uint32_t) > skeletonStart) {
		continue;
	    }
	    uint32_t candidateIndexBytes = 0;
	    std::memcpy (&candidateIndexBytes, data.data () + indexLengthOffset, sizeof (candidateIndexBytes));
	    if (candidateIndexBytes == 0 || candidateIndexBytes % (sizeof (uint16_t) * 3) != 0
		|| indexLengthOffset + sizeof (uint32_t) + candidateIndexBytes > data.size ()) {
		continue;
	    }
	    verticesOffset = offset + sizeof (uint32_t);
	    vertexBytes = candidate;
	    indexBytes = candidateIndexBytes;
	    return true;
	}
	return false;
    };
    if (!structuredFound && !scanRange (materialsEnd, materialsEnd + 128)) {
	scanRange (9, skeletonStart);
    }

    if (vertexBytes == 0) {
	error = "no skinned mesh block found (structured walk + stride-80 scan)";
	return std::nullopt;
    }

    PuppetModel model;
    const size_t vertexCount = vertexBytes / vertexStride;
    model.positions.reserve (vertexCount);
    model.uvs.reserve (vertexCount);
    model.blendIndices.reserve (vertexCount);
    model.blendWeights.reserve (vertexCount);

    for (size_t i = 0; i < vertexCount; i++) {
	const char* v = data.data () + verticesOffset + i * vertexStride;
	float pos[3];
	std::memcpy (pos, v, sizeof (pos));
	uint32_t idx[4];
	std::memcpy (idx, v + layout.idxOff, sizeof (idx));
	float weights[4];
	std::memcpy (weights, v + layout.weightOff, sizeof (weights));
	float uv[2];
	std::memcpy (uv, v + layout.uvOff, sizeof (uv));
	model.positions.emplace_back (pos[0], pos[1], pos[2]);
	model.blendIndices.emplace_back (idx[0], idx[1], idx[2], idx[3]);
	model.blendWeights.emplace_back (weights[0], weights[1], weights[2], weights[3]);
	model.uvs.emplace_back (uv[0], uv[1]);
    }

    const size_t indicesOffset = verticesOffset + vertexBytes + sizeof (uint32_t);
    const size_t indexCount = indexBytes / sizeof (uint16_t);
    model.indices.resize (indexCount);
    std::memcpy (model.indices.data (), data.data () + indicesOffset, indexBytes);
    for (const auto index : model.indices) {
	if (index >= vertexCount) {
	    error = "mesh index out of range";
	    return std::nullopt;
	}
    }

    // Skeleton + animations degrade gracefully: a puppet without them renders bind pose
    if (parseSkeleton (data, model)) {
	if (!parseAnimations (data, model)) {
	    model.clips.clear ();
	}
    } else {
	model.bones.clear ();
    }

    return model;
}
