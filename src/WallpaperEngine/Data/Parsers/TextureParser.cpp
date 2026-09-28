#include <cmath>
#include <cstring>

#include <lz4.h>
#include <nlohmann/json.hpp>

#include "TextureParser.h"
#include "WallpaperEngine/Data/Assets/Texture.h"
#include "WallpaperEngine/Logging/Log.h"

using namespace WallpaperEngine::Data::Assets;
using namespace WallpaperEngine::Data::Parsers;

// no texture this engine can upload needs more than this much decoded across all of its
// images and mips; a file that asks for more is claiming space, not describing pixels
constexpr uint64_t MAX_TEXTURE_BYTES = 4ull << 30;
// a mip chain of a texture with dimensions that fit in 32 bits is never longer than this
constexpr uint32_t MAX_MIPMAP_COUNT = 32;

TextureUniquePtr TextureParser::parse (const BinaryReader& file) {
    auto result = std::make_unique<Texture> ();
    uint64_t budget = MAX_TEXTURE_BYTES;

    parseTextureHeader (*result, file);
    parseContainer (*result, file);

    if (result->imageCount == 0) {
	sLog.exception ("Texture declares no images");
    }

    for (uint32_t image = 0; image < result->imageCount; image++) {
	const uint32_t placeholderMipmapCount = file.nextUInt32 ();
	const uint32_t mipmapCount = result->rawGLMipLevels > 0 ? result->rawGLMipLevels : placeholderMipmapCount;
	MipmapList mipmaps;

	if (mipmapCount == 0 || mipmapCount > MAX_MIPMAP_COUNT) {
	    sLog.exception ("Texture image ", image, " declares ", mipmapCount, " mipmaps");
	}

	for (uint32_t mipmap = 0; mipmap < mipmapCount; mipmap++) {
	    mipmaps.emplace_back (parseMipmap (file, *result, image, mipmap, budget));
	}

	result->images.emplace (image, mipmaps);
    }

    if (!result->isAnimated ()) {
	return result;
    }

    parseAnimations (*result, file);

    return result;
}

// sizes come straight from an untrusted .tex file (PR #631 hardening): reject negatives
// (which would turn into a huge size_t in new[]), absurd allocations, and payloads larger
// than the bytes actually left in the stream. Shared by every mip variant below.
void TextureParser::validateMipmapPayloadBounds (const Mipmap& mipmap, const BinaryReader& file) {
    if (mipmap.compressedSize < 0 || mipmap.uncompressedSize < 0) {
	sLog.exception ("Texture mipmap has a negative size");
    }

    // upper bound on a single decompressed mipmap; a genuine texture never approaches this, but it
    // stops a tiny compressed payload from forcing a multi-gigabyte allocation before decompression
    constexpr int MAX_MIPMAP_BYTES = 1 << 30; // 1 GiB
    if (mipmap.uncompressedSize > MAX_MIPMAP_BYTES) {
	sLog.exception ("Texture mipmap uncompressed size ", mipmap.uncompressedSize, " exceeds sane limit");
    }

    // the bytes we are about to read from the file (compressed payload, or the raw payload otherwise)
    // must actually exist in what remains of the stream
    const std::streamsize toRead = (mipmap.compression == 1) ? mipmap.compressedSize : mipmap.uncompressedSize;
    if (toRead > file.remaining ()) {
	sLog.exception ("Texture mipmap claims ", toRead, " bytes but only ", file.remaining (), " remain");
    }
}

// the per-mip cap above bounds one allocation; this bounds the sum, since a small file can
// carry many mips that each expand well past their compressed size
static void chargeTextureBudget (const Mipmap& mipmap, uint64_t& budget) {
    const auto bytes = static_cast<uint64_t> (mipmap.uncompressedSize);

    if (bytes > budget) {
	sLog.exception ("Texture decoded size exceeds the ", MAX_TEXTURE_BYTES >> 20, " MiB limit");
    }

    budget -= bytes;
}

static void validateRawPixelBytes (const Mipmap& mipmap, const Texture& header) {
    if (header.freeImageFormat != FIF_UNKNOWN || header.isVideoMp4 || (header.flags & TextureFlags_Video)) {
	return;
    }

    uint64_t bytesPerPixel = 0;

    switch (header.format) {
	case TextureFormat_ARGB8888:
	    bytesPerPixel = 4;
	    break;
	case TextureFormat_RG88:
	    bytesPerPixel = 2;
	    break;
	case TextureFormat_R8:
	    bytesPerPixel = 1;
	    break;
	default:
	    return;
    }

    const uint64_t pixels = static_cast<uint64_t> (mipmap.width) * mipmap.height;

    if (pixels > static_cast<uint64_t> (mipmap.uncompressedSize) / bytesPerPixel) {
	sLog.exception (
	    "Texture mipmap payload of ", mipmap.uncompressedSize, " bytes is smaller than ", mipmap.width, "x",
	    mipmap.height, " pixels at ", bytesPerPixel, " bytes each"
	);
    }
}

MipmapSharedPtr TextureParser::parseMipmap (
    const BinaryReader& file, const Texture& header, uint32_t imageIndex, uint32_t mipIndex, uint64_t& budget
) {
    auto result = std::make_shared<Mipmap> ();

    if (header.containerVersion == ContainerVersion_TEXB0004 && header.freeImageFormat == FIF_UNKNOWN) {
	uint32_t mipWidth = header.textureWidth;
	uint32_t mipHeight = header.textureHeight;

	if (imageIndex > 0 || mipIndex > 0) {
	    mipWidth = file.nextUInt32 ();
	    mipHeight = file.nextUInt32 ();
	    std::ignore = file.nextUInt32 (); // 0/1-valued flag, purpose unknown
	}

	result->width = mipWidth;
	result->height = mipHeight;
	result->uncompressedSize = file.nextInt ();
	result->compressedSize = file.nextInt ();
	result->compression = (result->uncompressedSize != result->compressedSize) ? 1 : 0;

	validateMipmapPayloadBounds (*result, file);
	validateRawPixelBytes (*result, header);
	chargeTextureBudget (*result, budget);

	result->uncompressedData = std::unique_ptr<char[]> (new char[result->uncompressedSize]);

	if (result->compression == 1) {
	    result->compressedData = std::unique_ptr<char[]> (new char[result->compressedSize]);
	    file.next (result->compressedData.get (), result->compressedSize);

	    if (LZ4_decompress_safe (
		    result->compressedData.get (), result->uncompressedData.get (), result->compressedSize,
		    result->uncompressedSize
		)
		!= static_cast<int> (result->uncompressedSize)) {
		sLog.exception ("Cannot decompress raw-GL texture mipmap data");
	    }
	} else {
	    file.next (result->uncompressedData.get (), result->uncompressedSize);
	}

	return result;
    }

    // TEXB0004 has some extra data in the header that has to be handled
    if (header.containerVersion == ContainerVersion_TEXB0004) {
	// some integers that we can ignore as they only seem to affect
	// the editor
	std::ignore = file.nextUInt32 ();
	std::ignore = file.nextUInt32 ();
	// this format includes some json in the header that we might need
	// to parse at some point...
	result->json = file.nextNullTerminatedString ();
	// last ignorable integer
	std::ignore = file.nextUInt32 ();
    }

    result->width = file.nextUInt32 ();
    result->height = file.nextUInt32 ();

    if (header.containerVersion == ContainerVersion_TEXB0004 || header.containerVersion == ContainerVersion_TEXB0003
	|| header.containerVersion == ContainerVersion_TEXB0002) {
	result->compression = file.nextUInt32 ();
	result->uncompressedSize = file.nextInt ();
    }

    result->compressedSize = file.nextInt ();

    if (result->compression == 0) {
	// this might be better named as mipmap_bytes_size instead of compressedSize
	// as in uncompressed files this variable actually holds the file length
	result->uncompressedSize = result->compressedSize;
    }

    validateMipmapPayloadBounds (*result, file);
    validateRawPixelBytes (*result, header);
    chargeTextureBudget (*result, budget);

    result->uncompressedData = std::unique_ptr<char[]> (new char[result->uncompressedSize]);

    if (result->compression == 1) {
	result->compressedData = std::unique_ptr<char[]> (new char[result->compressedSize]);
	// read the compressed data into the buffer
	file.next (result->compressedData.get (), result->compressedSize);
	// finally decompress it
	int bytes = LZ4_decompress_safe (
	    result->compressedData.get (), result->uncompressedData.get (), result->compressedSize,
	    result->uncompressedSize
	);

	// a short decode would leave the buffer tail as uninitialized heap, uploaded as texels
	if (bytes != static_cast<int> (result->uncompressedSize)) {
	    sLog.exception ("Cannot decompress texture data, LZ4_decompress_safe returned an error");
	}
    } else {
	file.next (result->uncompressedData.get (), result->uncompressedSize);
    }

    return result;
}

FrameSharedPtr TextureParser::parseFrameV1 (const BinaryReader& file) {
    auto result = std::make_shared<Frame> ();

    result->frameNumber = file.nextUInt32 ();
    result->frametime = file.nextFloat ();
    result->x = static_cast<float> (file.nextUInt32 ());
    result->y = static_cast<float> (file.nextUInt32 ());
    result->width1 = static_cast<float> (file.nextUInt32 ());
    std::ignore = file.nextUInt32 (); // unknown
    std::ignore = file.nextUInt32 (); // unknown
    result->height1 = static_cast<float> (file.nextUInt32 ());

    return result;
}

FrameSharedPtr TextureParser::parseFrame (const BinaryReader& file) {
    auto result = std::make_shared<Frame> ();

    result->frameNumber = file.nextUInt32 ();
    result->frametime = file.nextFloat ();
    result->x = file.nextFloat ();
    result->y = file.nextFloat ();
    result->width1 = file.nextFloat ();
    result->width2 = file.nextFloat ();
    result->height2 = file.nextFloat ();
    result->height1 = file.nextFloat ();

    return result;
}

TextureMap TextureParser::parseTextureMap (const JSON& it) {
    if (!it.is_array ()) {
	return {};
    }

    TextureMap result = {};
    int textureIndex = -1;

    for (const auto& cur : it) {
	textureIndex++;

	if (cur.is_null ()) {
	    continue;
	}

	if (cur.is_object ()) {
	    const auto nameIt = cur.find ("name");
	    if (nameIt != cur.end () && nameIt->is_string ()) {
		result.emplace (textureIndex, nameIt->get<std::string> ());
	    }
	} else if (cur.is_string ()) {
	    std::string texName = cur;
	    if (!texName.empty ()) {
		result.emplace (textureIndex, texName);
	    }
	}
    }

    return result;
}

TextureFormat TextureParser::parseTextureFormat (uint32_t value) {
    switch (value) {
	case TextureFormat_UNKNOWN:
	case TextureFormat_ARGB8888:
	case TextureFormat_RGB888:
	case TextureFormat_RGB565:
	case TextureFormat_DXT5:
	case TextureFormat_DXT3:
	case TextureFormat_DXT1:
	case TextureFormat_RG88:
	case TextureFormat_R8:
	case TextureFormat_RG1616f:
	case TextureFormat_R16f:
	case TextureFormat_BC7:
	case TextureFormat_RGBa1010102:
	case TextureFormat_RGBA16161616f:
	case TextureFormat_RGB161616f:
	    return static_cast<TextureFormat> (value);

	default:
	    sLog.exception ("unknown texture format: ", value);
    }
}

void TextureParser::parseTextureHeader (Texture& header, const BinaryReader& file) {
    char magic[9] = { 0 };

    file.next (magic, 9);

    if (strncmp (magic, "TEXV0005", 9) != 0) {
	sLog.exception ("unexpected texture container type: ", std::string_view (magic, 9));
    }

    file.next (magic, 9);

    if (strncmp (magic, "TEXI0001", 9) != 0) {
	sLog.exception ("unexpected texture sub-container type: ", std::string_view (magic, 9));
    }

    header.format = parseTextureFormat (file.nextUInt32 ());
    header.flags = parseTextureFlags (file.nextUInt32 ());
    header.textureWidth = file.nextUInt32 ();
    header.textureHeight = file.nextUInt32 ();
    header.width = file.nextUInt32 ();
    header.height = file.nextUInt32 ();

    // ignore some more bytes
    std::ignore = file.nextUInt32 ();
}

void TextureParser::parseContainer (Texture& header, const BinaryReader& file) {
    char magic[9] = { 0 };

    file.next (magic, 9);

    header.imageCount = file.nextUInt32 ();

    if (strncmp (magic, "TEXB0004", 9) == 0) {
	header.containerVersion = ContainerVersion_TEXB0004;
	header.freeImageFormat = parseFIF (file.nextUInt32 ());
	header.isVideoMp4 = file.nextUInt32 () == 1;

	if (header.freeImageFormat == FIF_UNKNOWN && header.isVideoMp4) {
	    header.freeImageFormat = FIF_MP4;
	}

	if (header.freeImageFormat == FIF_UNKNOWN) {
	    header.rawGLMipLevels = file.nextUInt32 ();
	    std::ignore = file.nextUInt32 ();
	    std::ignore = file.nextUInt32 ();

	    if (header.rawGLMipLevels == 0 || header.rawGLMipLevels > 16) {
		sLog.exception ("raw-GL texture declares ", header.rawGLMipLevels, " mip levels");
	    }
	} else if (header.freeImageFormat != FIF_MP4) {
	    header.containerVersion = ContainerVersion_TEXB0003;
	}
    } else if (strncmp (magic, "TEXB0003", 9) == 0) {
	header.containerVersion = ContainerVersion_TEXB0003;
	header.freeImageFormat = parseFIF (file.nextUInt32 ());
    } else if (strncmp (magic, "TEXB0002", 9) == 0) {
	header.containerVersion = ContainerVersion_TEXB0002;
    } else if (strncmp (magic, "TEXB0001", 9) == 0) {
	header.containerVersion = ContainerVersion_TEXB0001;
    } else {
	sLog.exception ("unknown texture format type: ", std::string_view (magic, 9));
    }
}

void TextureParser::parseAnimations (Texture& header, const BinaryReader& file) {
    char magic[9] = { 0 };

    // image is animated, keep parsing the rest of the image info
    file.next (magic, 9);

    if (strncmp (magic, "TEXS0001", 9) == 0) {
	header.animatedVersion = AnimatedVersion_TEXS0001;
    } else if (strncmp (magic, "TEXS0002", 9) == 0) {
	header.animatedVersion = AnimatedVersion_TEXS0002;
    } else if (strncmp (magic, "TEXS0003", 9) == 0) {
	header.animatedVersion = AnimatedVersion_TEXS0003;
    } else {
	sLog.exception ("found animation information of unknown type: ", std::string_view (magic, 9));
    }

    uint32_t frameCount = file.nextUInt32 ();

    if (header.animatedVersion == AnimatedVersion_TEXS0003) {
	header.gifWidth = file.nextUInt32 ();
	header.gifHeight = file.nextUInt32 ();
    }

    while (frameCount-- > 0) {
	if (header.animatedVersion == AnimatedVersion_TEXS0001) {
	    header.frames.push_back (parseFrameV1 (file));
	} else {
	    header.frames.push_back (parseFrame (file));
	}
    }

    // ensure gif width and height is right for TEXS0001, TEXS0002
    if (header.animatedVersion == AnimatedVersion_TEXS0001 || header.animatedVersion == AnimatedVersion_TEXS0002) {
	// a frame count of zero would otherwise dereference begin() on an empty vector
	if (header.frames.empty ()) {
	    sLog.exception ("Animated texture declares no frames");
	}

	const float w = header.frames.front ()->width1;
	const float h = header.frames.front ()->height1;

	// frame sizes are floats from the file; a negative, non-finite or absurd one has no
	// unsigned equivalent to convert to
	if (!std::isfinite (w) || !std::isfinite (h) || w < 0.0f || h < 0.0f || w > 65536.0f || h > 65536.0f) {
	    sLog.exception ("Animated texture frame size ", w, "x", h, " is not usable");
	}

	header.gifWidth = static_cast<uint32_t> (w);
	header.gifHeight = static_cast<uint32_t> (h);
    }

    // Calculate spritesheet grid dimensions from animation frames
    // Spritesheets are grid-based textures where each frame is at a specific position
    if (!header.frames.empty () && header.width > 0 && header.height > 0) {
	auto& firstFrame = *header.frames.front ();
	float frameWidth = firstFrame.width1;
	float frameHeight = firstFrame.height1;

	if (std::isfinite (frameWidth) && std::isfinite (frameHeight) && frameWidth > 0.0f && frameHeight > 0.0f) {
	    const double colsD = std::round (static_cast<double> (header.width) / frameWidth);
	    const double rowsD = std::round (static_cast<double> (header.height) / frameHeight);

	    // a tiny frame size divides into a grid no unsigned can hold; treat it as not a spritesheet
	    if (colsD < 0.0 || rowsD < 0.0 || colsD > 65536.0 || rowsD > 65536.0) {
		return;
	    }

	    const uint32_t cols = static_cast<uint32_t> (colsD);
	    const uint32_t rows = static_cast<uint32_t> (rowsD);
	    const uint32_t frameCount = static_cast<uint32_t> (header.frames.size ());

	    // Only populate spritesheet metadata if the inferred grid can actually hold all frames
	    // This prevents GIFs (where frameWidth == textureWidth) from being treated as 1x1 spritesheets
	    if (cols > 0 && rows > 0 && cols * rows >= frameCount) {
		header.spritesheetCols = cols;
		header.spritesheetRows = rows;
		header.spritesheetFrames = frameCount;

		float totalDuration = 0.0f;
		for (const auto& frame : header.frames) {
		    totalDuration += frame->frametime;
		}
		header.spritesheetDuration = totalDuration;
	    }
	}
    }
}

uint32_t TextureParser::parseTextureFlags (uint32_t value) {
    // Windows-parity: NEVER reject content over unknown flag bits (silent-default is
    // engine policy, behaviorally confirmed). Keep the bits we model,
    // log the rest once per value. Rejecting here dropped real textures (3D Earth
    // masks flags 0x800000, Bus Stop class) and killed whole objects with them.
    const uint32_t known = value & TextureFlags_All;
    if (known != value) {
	sLog.out ("ignoring unknown texture flag bits: ", value & ~TextureFlags_All, " (keeping ", known, ")");
    }
    return known;
}

FIF TextureParser::parseFIF (uint32_t value) {
    switch (value) {
	case FIF_UNKNOWN:
	case FIF_BMP:
	case FIF_ICO:
	case FIF_JPEG:
	case FIF_JNG:
	case FIF_KOALA:
	case FIF_LBM:
	case FIF_MNG:
	case FIF_PBM:
	case FIF_PBMRAW:
	case FIF_PCD:
	case FIF_PCX:
	case FIF_PGM:
	case FIF_PGMRAW:
	case FIF_PNG:
	case FIF_PPM:
	case FIF_PPMRAW:
	case FIF_RAS:
	case FIF_TARGA:
	case FIF_TIFF:
	case FIF_WBMP:
	case FIF_PSD:
	case FIF_CUT:
	case FIF_XBM:
	case FIF_XPM:
	case FIF_DDS:
	case FIF_GIF:
	case FIF_HDR:
	case FIF_FAXG3:
	case FIF_SGI:
	case FIF_EXR:
	case FIF_J2K:
	case FIF_JP2:
	case FIF_PFM:
	case FIF_PICT:
	case FIF_RAW:
	case FIF_WEBP:
	case FIF_JXR:
	    return static_cast<FIF> (value);

	default:
	    sLog.exception ("unknown free image format: ", value);
    }
}

TextureUniquePtr TextureParser::parse (
    const BinaryReader& file, const std::string& filename,
    std::function<std::string (const std::string&)> metadataLoader
) {
    // Parse the binary .tex file first
    auto result = parse (file);

    // Try to load optional .tex-json metadata for spritesheet data
    if (metadataLoader) {
	parseSpritesheetMetadata (*result, filename, metadataLoader);
    }

    return result;
}

void TextureParser::parseSpritesheetMetadata (
    Texture& header, const std::string& filename, std::function<std::string (const std::string&)> metadataLoader
) {
    try {
	std::string texJsonContent = metadataLoader (filename + ".tex-json");
	nlohmann::json texJson = nlohmann::json::parse (texJsonContent);

	// Check for spritesheet sequences
	if (texJson.contains ("spritesheetsequences") && texJson["spritesheetsequences"].is_array ()) {
	    auto& sequences = texJson["spritesheetsequences"];
	    if (!sequences.empty ()) {
		auto& firstSeq = sequences[0];
		int frames = firstSeq.value ("frames", 0);
		float frameWidth = firstSeq.value ("width", 0.0f);
		float frameHeight = firstSeq.value ("height", 0.0f);
		float duration = firstSeq.value ("duration", 1.0f);

		if (frames > 0 && std::isfinite (frameWidth) && std::isfinite (frameHeight) && frameWidth > 0.0f
		    && frameHeight > 0.0f && header.width > 0 && header.height > 0) {
		    // Calculate grid dimensions from texture size and frame size
		    const double cols = std::round (header.width / frameWidth);
		    const double rows = std::round (header.height / frameHeight);

		    if (cols >= 0.0 && rows >= 0.0 && cols <= 65536.0 && rows <= 65536.0) {
			header.spritesheetCols = static_cast<uint32_t> (cols);
			header.spritesheetRows = static_cast<uint32_t> (rows);
			header.spritesheetFrames = static_cast<uint32_t> (frames);
			header.spritesheetDuration = duration;
		    }
		}
	    }
	}
    } catch (const std::exception&) {
	// .tex-json file is optional, only used for spritesheet data
    }
}