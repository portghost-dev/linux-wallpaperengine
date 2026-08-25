#include "CTexture.h"
#include "WallpaperEngine/Data/Utils/Sha256.h"
#include "WallpaperEngine/Logging/Log.h"
#include <set>

#include <lz4.h>

#define STB_IMAGE_IMPLEMENTATION
#include "RenderContext.h"

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <nlohmann/json.hpp>
#include <stb_image.h>
#include <vector>

using namespace WallpaperEngine::Render;

#ifndef GL_COMPRESSED_RGBA_BPTC_UNORM
#define GL_COMPRESSED_RGBA_BPTC_UNORM 0x8E8C
#endif
#ifndef GL_COMPRESSED_RED_RGTC1
#define GL_COMPRESSED_RED_RGTC1 0x8DBB
#endif
#ifndef GL_COMPRESSED_RG_RGTC2
#define GL_COMPRESSED_RG_RGTC2 0x8DBD
#endif

namespace {
std::atomic<uint64_t> g_texUploadTotal { 0 };

void logTexUpload (
    const char* path, const char* fmt, const uint32_t w, const uint32_t h, const int levels, const uint64_t bytes
) {
    const uint64_t total = g_texUploadTotal.fetch_add (bytes) + bytes;
    sLog.out (
	"LWE-TEXUPLOAD path=", path, " fmt=", fmt, " dims=", w, "x", h, " levels=", levels, " bytes=", bytes, " (",
	bytes / (1024 * 1024), " MiB) totalMB=", total / (1024 * 1024)
    );
}

// LZ4 source bytes are the retained backing store once uploaded - consumers must
// materialize pixels through materializeMip, never read uncompressedData directly
struct MipPixels {
    const char* data = nullptr;
    int size = 0;
    std::unique_ptr<char[]> owned {};
};

MipPixels materializeMip (const WallpaperEngine::Data::Assets::Mipmap& mip) {
    if (mip.uncompressedData != nullptr) {
	return { mip.uncompressedData.get (), mip.uncompressedSize, nullptr };
    }
    if (mip.compressedData == nullptr || mip.uncompressedSize <= 0) {
	return {};
    }
    auto buffer = std::unique_ptr<char[]> (new char[mip.uncompressedSize]);
    if (LZ4_decompress_safe (mip.compressedData.get (), buffer.get (), mip.compressedSize, mip.uncompressedSize)
	!= mip.uncompressedSize) {
	sLog.error ("materializeMip: LZ4 decode failed (", mip.compressedSize, " -> ", mip.uncompressedSize, ")");
	return {};
    }
    MipPixels out;
    out.size = mip.uncompressedSize;
    out.owned = std::move (buffer);
    out.data = out.owned.get ();
    return out;
}

std::atomic<uint64_t> g_ramSlimTotal { 0 };

// mips without a compressed copy (raw payloads, stbi file bytes, videos) keep theirs
void slimRetainedPixels (const WallpaperEngine::Data::Assets::Texture& header) {
    // video payloads are streamed by the player from the retained buffer - never slim
    if (header.isVideoMp4 || (header.flags & WallpaperEngine::Data::Assets::TextureFlags_Video) != 0) {
	return;
    }
    uint64_t freed = 0;

    for (const auto& [index, mipmaps] : header.images) {
	for (const auto& mipmap : mipmaps) {
	    if (mipmap->compressedData != nullptr && mipmap->uncompressedData != nullptr) {
		freed += static_cast<uint64_t> (mipmap->uncompressedSize);
		mipmap->uncompressedData.reset ();
	    }
	}
    }

    if (freed > 0) {
	const uint64_t total = g_ramSlimTotal.fetch_add (freed) + freed;
	sLog.out (
	    "LWE-RAMSLIM freed=", freed / (1024 * 1024),
	    " MiB (LZ4 backing retained) totalFreedMB=", total / (1024 * 1024)
	);
    }
}

void baseLevelProbe () {
    constexpr int kDim = 64;

    const auto fill = [] (std::vector<uint8_t>& v, const uint8_t seed) {
	for (size_t i = 0; i < v.size (); i++) {
	    v[i] = static_cast<uint8_t> (seed + i * 31);
	}
    };

    {
	GLuint tex = 0;
	glGenTextures (1, &tex);
	glBindTexture (GL_TEXTURE_2D, tex);
	glPixelStorei (GL_UNPACK_ALIGNMENT, 1);
	std::vector<std::vector<uint8_t>> levels;
	for (int level = 1, dim = kDim / 2; dim >= 1; level++, dim /= 2) {
	    levels.emplace_back (static_cast<size_t> (dim) * dim * 4);
	    fill (levels.back (), static_cast<uint8_t> (level * 17));
	    glTexImage2D (
		GL_TEXTURE_2D, level, GL_RGBA8, dim, dim, 0, GL_RGBA, GL_UNSIGNED_BYTE, levels.back ().data ()
	    );
	}
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_BASE_LEVEL, 1);
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, 6);

	std::vector<uint8_t> level0 (static_cast<size_t> (kDim) * kDim * 4);
	fill (level0, 200);
	const auto t0 = std::chrono::steady_clock::now ();
	glTexImage2D (GL_TEXTURE_2D, 0, GL_RGBA8, kDim, kDim, 0, GL_RGBA, GL_UNSIGNED_BYTE, level0.data ());
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_BASE_LEVEL, 0);
	glFinish ();
	const auto us
	    = std::chrono::duration_cast<std::chrono::microseconds> (std::chrono::steady_clock::now () - t0).count ();

	bool ghosted = false;
	std::vector<uint8_t> rb;
	for (int level = 1, dim = kDim / 2; dim >= 1; level++, dim /= 2) {
	    rb.assign (static_cast<size_t> (dim) * dim * 4, 0);
	    glGetTexImage (GL_TEXTURE_2D, level, GL_RGBA, GL_UNSIGNED_BYTE, rb.data ());
	    if (rb != levels[level - 1]) {
		ghosted = true;
	    }
	}
	rb.assign (level0.size (), 0);
	glGetTexImage (GL_TEXTURE_2D, 0, GL_RGBA, GL_UNSIGNED_BYTE, rb.data ());
	const bool level0ok = rb == level0;
	sLog.out (
	    "LWE-BASELEVELPROBE raw: residentIntact=", ghosted ? 0 : 1, " level0=", level0ok ? "ok" : "BAD",
	    " respecify=", us, "us -> ", (!ghosted && level0ok) ? "PASS" : "FAIL"
	);
	glDeleteTextures (1, &tex);
    }

    {
	GLuint tex = 0;
	glGenTextures (1, &tex);
	glBindTexture (GL_TEXTURE_2D, tex);
	const auto dxt1Size = [] (const int dim) { return std::max (1, dim / 4) * std::max (1, dim / 4) * 8; };
	std::vector<std::vector<uint8_t>> levels;
	for (int level = 1, dim = kDim / 2; dim >= 4; level++, dim /= 2) {
	    levels.emplace_back (static_cast<size_t> (dxt1Size (dim)));
	    fill (levels.back (), static_cast<uint8_t> (level * 29));
	    glCompressedTexImage2D (
		GL_TEXTURE_2D, level, GL_COMPRESSED_RGBA_S3TC_DXT1_EXT, dim, dim, 0,
		static_cast<GLsizei> (levels.back ().size ()), levels.back ().data ()
	    );
	}
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_BASE_LEVEL, 1);
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, static_cast<GLint> (levels.size ()));

	std::vector<uint8_t> level0 (static_cast<size_t> (dxt1Size (kDim)));
	fill (level0, 111);
	const auto t0 = std::chrono::steady_clock::now ();
	glCompressedTexImage2D (
	    GL_TEXTURE_2D, 0, GL_COMPRESSED_RGBA_S3TC_DXT1_EXT, kDim, kDim, 0, static_cast<GLsizei> (level0.size ()),
	    level0.data ()
	);
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_BASE_LEVEL, 0);
	glFinish ();
	const auto us
	    = std::chrono::duration_cast<std::chrono::microseconds> (std::chrono::steady_clock::now () - t0).count ();

	bool ghosted = false;
	std::vector<uint8_t> rb;
	for (size_t i = 0; i < levels.size (); i++) {
	    rb.assign (levels[i].size (), 0);
	    glGetCompressedTexImage (GL_TEXTURE_2D, static_cast<GLint> (i + 1), rb.data ());
	    if (rb != levels[i]) {
		ghosted = true;
	    }
	}
	rb.assign (level0.size (), 0);
	glGetCompressedTexImage (GL_TEXTURE_2D, 0, rb.data ());
	const bool level0ok = rb == level0;
	sLog.out (
	    "LWE-BASELEVELPROBE dxt: residentIntact=", ghosted ? 0 : 1, " level0=", level0ok ? "ok" : "BAD",
	    " respecify=", us, "us -> ", (!ghosted && level0ok) ? "PASS" : "FAIL"
	);
	glDeleteTextures (1, &tex);
    }
}

bool uploadFromTexcache (const WallpaperEngine::Data::Assets::Texture& h, const int capDimension, bool& held) {
    using namespace WallpaperEngine::Data::Assets;
    const char* tc = getenv ("LWE_TEXCOMP");
    if (tc != nullptr && std::string (tc) == "0") {
	return false;
    }
    if (getenv ("LWE_SRGBALL") != nullptr) {
	return false;
    }
    if (h.imageCount != 1 || h.animatedVersion != AnimatedVersion_UNKNOWN) {
	return false;
    }
    GLenum glfmt;
    const char* want;
    switch (h.format) {
	case TextureFormat_ARGB8888:
	    glfmt = GL_COMPRESSED_RGBA_BPTC_UNORM;
	    want = "BC7";
	    break;
	case TextureFormat_R8:
	    glfmt = GL_COMPRESSED_RED_RGTC1;
	    want = "BC4";
	    break;
	case TextureFormat_RG88:
	    glfmt = GL_COMPRESSED_RG_RGTC2;
	    want = "BC5";
	    break;
	default:
	    return false;
    }
    const auto it = h.images.find (0);
    if (it == h.images.end () || it->second.empty ()) {
	return false;
    }
    const auto& mip0 = it->second.front ();
    const auto pixels = materializeMip (*mip0);
    if (pixels.data == nullptr || pixels.size <= 0) {
	return false;
    }
    const std::string key = WallpaperEngine::Data::Utils::sha256_hex (pixels.data, (size_t)pixels.size);
    const char* home = getenv ("HOME");
    if (home == nullptr) {
	return false;
    }
    std::filesystem::path base = std::filesystem::path (home) / ".local/state/lwe/texcache" / key;
    std::filesystem::path bc = base;
    bc += ".bc";
    std::filesystem::path mp = base;
    mp += ".meta";
    std::error_code ec;
    if (!std::filesystem::exists (bc, ec) || !std::filesystem::exists (mp, ec)) {
	return false;
    }
    nlohmann::json meta;
    try {
	std::ifstream mf (mp);
	mf >> meta;
    } catch (...) {
	return false;
    }
    if (meta.value ("gl", std::string ()) != want) {
	return false;
    }
    std::ifstream bf (bc, std::ios::binary);
    std::vector<char> blob ((std::istreambuf_iterator<char> (bf)), std::istreambuf_iterator<char> ());

    int startLevel = 0;
    if (capDimension > 0 && meta["mips"].size () > 1) {
	int i = 0;
	for (const auto& m : meta["mips"]) {
	    if (std::max (m[0].get<int> (), m[1].get<int> ()) >= capDimension) {
		startLevel = i;
	    }
	    i++;
	}
    }

    size_t off = 0;
    int level = 0;
    uint64_t uploaded = 0;
    for (const auto& m : meta["mips"]) {
	const int mw = m[0].get<int> ();
	const int mh = m[1].get<int> ();
	const size_t bytes = m[2].get<size_t> ();
	if (off + bytes > blob.size ()) {
	    return false;
	}
	if (level >= startLevel) {
	    // rebased (see the raw path's note): the capped chain uploads as a COMPLETE
	    // pyramid at levels 0.. so the driver never infers - and allocates - the
	    // held levels from pyramid-consistency math
	    glCompressedTexImage2D (
		GL_TEXTURE_2D, level - startLevel, glfmt, mw, mh, 0, (GLsizei)bytes, blob.data () + off
	    );
	    uploaded += bytes;
	}
	off += bytes;
	level++;
    }
    const int resident = level - startLevel;
    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, resident > 0 ? resident - 1 : 0);
    if (startLevel > 0) {
	held = true;
	sLog.out ("LWE-MIPRESIDENCY capped: dropped ", startLevel, " level(s), ", resident, " resident (cache)");
    }
    logTexUpload ("cache", want, h.width, h.height, resident, uploaded);
    return true;
}
} // namespace

namespace {
/** every live CTexture, so expandCappedTexture can find the CTexture behind a provider pointer */
std::set<CTexture*>& textureRegistry () {
    static std::set<CTexture*> registry;
    return registry;
}
} // namespace

CTexture::CTexture (RenderContext& context, TextureUniquePtr header, const int capDimension) :
    Helpers::ContextAware (context), m_header (std::move (header)), m_capDimension (capDimension) {
    // ensure the header is parsed
    this->setupResolution ();
    textureRegistry ().insert (this);
    this->createGL ();
}

void CTexture::createGL () {
    if (getenv ("LWE_BASELEVEL_PROBE") != nullptr) {
	static const bool probed = [] () {
	    baseLevelProbe ();
	    return true;
	}();
	(void)probed;
    }

    const GLint internalFormat = this->setupInternalFormat ();

    // videos are a bit special, they only have one framebuffer, one mipmap
    if (this->m_header->isVideoMp4 || this->m_header->flags & TextureFlags_Video) {
	if (this->m_header->images.empty () || this->m_header->images.begin ()->second.empty ()) {
	    sLog.exception ("Cannot load video texture, no mipmaps found");
	}

	// generate the texture and set it up to be used by the player
	this->m_textureID = new GLuint[1];
	glGenTextures (1, this->m_textureID);
	this->setupOpenGLParameters (0);

	const auto mipmap = *this->m_header->images.begin ()->second.begin ();

	this->m_player = std::make_unique<GLPlayer> (
	    this->getContext (), this->m_textureID[0],
	    std::make_unique<MemoryStreamProtocol> (mipmap->uncompressedData.get (), mipmap->uncompressedSize),
	    this->m_header->textureWidth, this->m_header->textureHeight
	);
	// setup texture video player
	this->m_player->setMuted ();
	this->m_player->setVolume (0.0f);
	this->m_player->setUntimed ();
	// texture is ready, nothing else to do
	return;
    }

    // allocate texture ids list
    this->m_textureID = new GLuint[this->m_header->imageCount];
    // ask opengl for the correct amount of textures and framebuffers
    glGenTextures (this->m_header->imageCount, this->m_textureID);

    for (const auto& [index, mipmaps] : this->m_header->images) {
	this->setupOpenGLParameters (index);

	if (index == 0 && uploadFromTexcache (*this->m_header, this->m_capDimension, this->m_held)) {
	    continue;
	}

	int startLevel = 0;
	if (this->m_capDimension > 0 && mipmaps.size () > 1) {
	    int i = 0;
	    for (const auto& mipmap : mipmaps) {
		if (std::max (mipmap->width, mipmap->height) >= static_cast<uint32_t> (this->m_capDimension)) {
		    startLevel = i;
		}
		i++;
	    }
	}

	int level = 0;
	uint64_t imageBytes = 0;

	int uploadedLevels = 0;
	for (const auto& mipmap : mipmaps) {
	    if (level < startLevel) {
		level++;
		continue;
	    }
	    const int glLevel = level - startLevel;
	    stbi_uc* handle = nullptr;
	    const auto pixels = materializeMip (*mipmap);
	    if (pixels.data == nullptr) {
		// no pixel source for this level: stop the chain here rather than
		// upload undefined texels; resident levels stay valid
		sLog.error ("texture level ", level, " has no pixel source, upload stops here");
		glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, glLevel > 0 ? glLevel - 1 : 0);
		break;
	    }
	    const void* dataptr = pixels.data;
	    int width = mipmap->width;
	    int height = mipmap->height;
	    const uint32_t bufferSize = pixels.size;
	    GLenum textureFormat = GL_RGBA;

	    if (this->m_header->freeImageFormat != FIF_UNKNOWN) {
		int fileChannels;

		dataptr = handle = stbi_load_from_memory (
		    reinterpret_cast<const unsigned char*> (pixels.data), pixels.size, &width, &height, &fileChannels, 4
		);
	    } else {
		glPixelStorei (GL_UNPACK_ALIGNMENT, 1);

		if (this->m_header->format == TextureFormat_R8) {
		    textureFormat = GL_RED;
		} else if (this->m_header->format == TextureFormat_RG88) {
		    textureFormat = GL_RG;
		}
	    }

	    if (glLevel == 0 && getenv ("LWE_MASKAUDIT") != nullptr
		&& (this->m_header->format == TextureFormat_R8 || this->m_header->format == TextureFormat_RG88)
		&& this->m_header->freeImageFormat == FIF_UNKNOWN && dataptr != nullptr) {
		const auto* px = static_cast<const uint8_t*> (dataptr);
		const int ch = this->m_header->format == TextureFormat_RG88 ? 2 : 1;
		uint64_t sum[2] = { 0, 0 };
		uint8_t mn[2] = { 255, 255 }, mx[2] = { 0, 0 };
		const size_t n = static_cast<size_t> (width) * height;
		for (size_t i = 0; i < n; i++) {
		    for (int c = 0; c < ch; c++) {
			const uint8_t v = px[i * ch + c];
			sum[c] += v;
			mn[c] = std::min (mn[c], v);
			mx[c] = std::max (mx[c], v);
		    }
		}
		sLog.out (
		    "LWE-MASKAUDIT ", width, "x", height, " fmt=", this->m_header->format,
		    " flags=", this->m_header->flags, " R(mean/min/max)=", sum[0] / n, "/", (int)mn[0], "/", (int)mx[0],
		    ch == 2 ? " G=" : " ", ch == 2 ? std::to_string (sum[1] / n) : std::string (),
		    ch == 2 ? "/" + std::to_string (mn[1]) + "/" + std::to_string (mx[1]) : std::string ()
		);
	    }

	    switch (internalFormat) {
		case GL_RGBA8:
		case GL_SRGB8_ALPHA8:
		case GL_RG8:
		case GL_R8:
		    glTexImage2D (
			GL_TEXTURE_2D, glLevel, internalFormat, width, height, 0, textureFormat, GL_UNSIGNED_BYTE,
			dataptr
		    );
		    imageBytes += static_cast<uint64_t> (width) * height
			* (internalFormat == GL_R8        ? 1
			       : internalFormat == GL_RG8 ? 2
							  : 4);
		    break;
		case GL_COMPRESSED_RGBA_S3TC_DXT1_EXT:
		case GL_COMPRESSED_RGBA_S3TC_DXT3_EXT:
		case GL_COMPRESSED_RGBA_S3TC_DXT5_EXT:
		case GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT5_EXT:
		case GL_COMPRESSED_SRGB_S3TC_DXT1_EXT:
		case GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT3_EXT:
		    glCompressedTexImage2D (
			GL_TEXTURE_2D, glLevel, internalFormat, width, height, 0, bufferSize, dataptr
		    );
		    imageBytes += bufferSize;
		    if (glLevel == 0 && getenv ("LWE_AUDIT") != nullptr && width >= 1024) {
			std::vector<uint8_t> rb (static_cast<size_t> (width) * height * 4);
			glGetTexImage (GL_TEXTURE_2D, 0, GL_RGBA, GL_UNSIGNED_BYTE, rb.data ());
			uint64_t rs = 0, gs = 0, bs = 0, asum = 0, sat = 0, n = 0;
			const size_t px = static_cast<size_t> (width) * height;
			for (size_t i = 0; i < px; i += 97) {
			    const uint8_t r = rb[i * 4], g = rb[i * 4 + 1], b = rb[i * 4 + 2], a = rb[i * 4 + 3];
			    rs += r;
			    gs += g;
			    bs += b;
			    asum += a;
			    const uint8_t mx = std::max (r, std::max (g, b));
			    const uint8_t mn = std::min (r, std::min (g, b));
			    sat += (mx - mn);
			    n++;
			}
			sLog.out (
			    "LWE-AUDIT gpu-readback ", width, "x", height, " meanRGBA=", rs / n, ",", gs / n, ",",
			    bs / n, ",", asum / n, " meanSat=", sat / n
			);
		    }
		    break;
		default:
		    sLog.exception ("Cannot load texture, unknown format", this->m_header->format);
	    }

	    // stbi_image buffer won't be used anymore, so free memory
	    if (this->m_header->freeImageFormat != FIF_UNKNOWN) {
		stbi_image_free (handle);
	    }

	    level++;
	    uploadedLevels++;
	}

	if (startLevel > 0) {
	    // rebased chain: GL levels 0..uploadedLevels-1 ARE the whole pyramid;
	    // setupOpenGLParameters set MAX_LEVEL for the full stored chain, so tighten
	    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, uploadedLevels > 0 ? uploadedLevels - 1 : 0);
	    this->m_held = true;
	    sLog.out ("LWE-MIPRESIDENCY capped: dropped ", startLevel, " level(s), ", uploadedLevels, " resident");
	}

	const bool isFif = this->m_header->freeImageFormat != FIF_UNKNOWN;
	const bool isDxt = this->m_header->format == TextureFormat_DXT1 || this->m_header->format == TextureFormat_DXT3
	    || this->m_header->format == TextureFormat_DXT5;
	const char* fmtName = isDxt                      ? (this->m_header->format == TextureFormat_DXT1       ? "DXT1"
								: this->m_header->format == TextureFormat_DXT3 ? "DXT3"
													       : "DXT5")
	    : this->m_header->format == TextureFormat_R8 ? "R8"
	    : this->m_header->format == TextureFormat_RG88 ? "RG88"
							   : "RGBA8";
	logTexUpload (
	    isFif       ? "fif"
		: isDxt ? "dxt"
			: "raw",
	    fmtName, this->m_header->width, this->m_header->height, level, imageBytes
	);
    }

    slimRetainedPixels (*this->m_header);
}

void CTexture::expandResidency () {
    if (this->m_capDimension <= 0 || !this->m_held || this->m_textureID == nullptr) {
	return;
    }
    if (this->m_header->isVideoMp4 || this->m_header->flags & TextureFlags_Video) {
	return;
    }
    const auto t0 = std::chrono::steady_clock::now ();
    glDeleteTextures (this->m_header->imageCount, this->m_textureID);
    delete[] this->m_textureID;
    this->m_textureID = nullptr;
    this->m_capDimension = 0;
    this->m_held = false;
    this->createGL ();
    const auto ms
	= std::chrono::duration_cast<std::chrono::milliseconds> (std::chrono::steady_clock::now () - t0).count ();
    sLog.out (
	"LWE-MIPRESIDENCY stream: expanded ", this->m_header->width, "x", this->m_header->height, " to full chain in ",
	ms, "ms"
    );
}

namespace WallpaperEngine::Render {
bool expandCappedTexture (const TextureProvider* texture) {
    for (auto* candidate : textureRegistry ()) {
	if (static_cast<const TextureProvider*> (candidate) == texture) {
	    candidate->expandResidency ();
	    return true;
	}
    }
    return false;
}
} // namespace WallpaperEngine::Render

CTexture::~CTexture () {
    textureRegistry ().erase (this);

    // first release the player to prevent using null references
    this->m_player.reset ();

    if (this->m_textureID == nullptr) {
	return;
    }

    if (this->m_header->isVideoMp4 || this->m_header->flags & TextureFlags_Video) {
	glDeleteTextures (1, this->m_textureID);
    } else {
	glDeleteTextures (this->m_header->imageCount, this->m_textureID);
    }

    delete[] this->m_textureID;
}

void CTexture::setupResolution () {
    if (this->isAnimated ()) {
	this->m_resolution = { this->m_header->textureWidth, this->m_header->textureHeight, this->m_header->gifWidth,
			       this->m_header->gifHeight };
    } else {
	if (this->m_header->freeImageFormat != FIF_UNKNOWN) {
	    // wpengine-texture format always has one mipmap
	    // get first image size
	    const auto element = this->m_header->images.find (0)->second.begin ();

	    // set the texture resolution
	    this->m_resolution
		= { (*element)->width, (*element)->height, this->m_header->width, this->m_header->height };
	} else {
	    // set the texture resolution
	    this->m_resolution = { this->m_header->textureWidth, this->m_header->textureHeight, this->m_header->width,
				   this->m_header->height };
	}
    }
}

GLint CTexture::setupInternalFormat () const {
    static const bool s_srgbProbe = getenv ("LWE_SRGBALBEDO") != nullptr;
    static const bool s_srgbAll = getenv ("LWE_SRGBALL") != nullptr;

    if (this->m_header->freeImageFormat != FIF_UNKNOWN) {
	return s_srgbAll ? GL_SRGB8_ALPHA8 : GL_RGBA8;
    }

    // detect the image format and hand it to openGL to be used
    switch (this->m_header->format) {
	case TextureFormat_DXT5:
	    return (s_srgbProbe || s_srgbAll) ? GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT5_EXT
					      : GL_COMPRESSED_RGBA_S3TC_DXT5_EXT;
	case TextureFormat_DXT3:
	    return s_srgbAll ? GL_COMPRESSED_SRGB_ALPHA_S3TC_DXT3_EXT : GL_COMPRESSED_RGBA_S3TC_DXT3_EXT;
	case TextureFormat_DXT1:
	    return s_srgbAll ? GL_COMPRESSED_SRGB_S3TC_DXT1_EXT : GL_COMPRESSED_RGBA_S3TC_DXT1_EXT;
	case TextureFormat_ARGB8888:
	    return s_srgbAll ? GL_SRGB8_ALPHA8 : GL_RGBA8;
	case TextureFormat_R8:
	    return GL_R8;
	case TextureFormat_RG88:
	    return GL_RG8;
	default:
	    sLog.exception ("Cannot determine texture format");
    }
}

void CTexture::setupOpenGLParameters (const uint32_t textureID) const {
    // TODO: LABEL ELEMENTS TOO
    // bind the texture to assign information to it
    glBindTexture (GL_TEXTURE_2D, this->m_textureID[textureID]);

    // set mipmap levels
    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_BASE_LEVEL, 0);
    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, this->m_header->images[textureID].size () - 1);

    // setup texture wrapping and filtering
    if (this->m_header->flags & TextureFlags_ClampUVs) {
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    } else {
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT);
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT);
    }

    if (this->m_header->flags & TextureFlags_NoInterpolation) {
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST_MIPMAP_NEAREST);
    } else {
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR);
    }

    glTexParameterf (GL_TEXTURE_2D, GL_TEXTURE_MAX_ANISOTROPY, 8.0f);
}

GLuint CTexture::getTextureID (const uint32_t imageIndex) const {
    // ensure we do not go out of bounds
    if (imageIndex >= this->m_header->imageCount) {
	return this->m_textureID[0];
    }

    return this->m_textureID[imageIndex];
}

uint32_t CTexture::getTextureWidth (const uint32_t imageIndex) const {
    if (imageIndex >= this->m_header->imageCount) {
	return this->getHeader ().textureWidth;
    }

    return (*this->m_header->images[imageIndex].begin ())->width;
}

uint32_t CTexture::getTextureHeight (const uint32_t imageIndex) const {
    if (imageIndex >= this->m_header->imageCount) {
	return this->getHeader ().textureHeight;
    }

    return (*this->m_header->images[imageIndex].begin ())->height;
}

uint32_t CTexture::getRealWidth () const {
    return this->isAnimated () ? this->getHeader ().gifWidth : this->getHeader ().width;
}

uint32_t CTexture::getRealHeight () const {
    return this->isAnimated () ? this->getHeader ().gifHeight : this->getHeader ().height;
}

TextureFormat CTexture::getFormat () const { return this->getHeader ().format; }

uint32_t CTexture::getFlags () const { return this->getHeader ().flags; }

const Texture& CTexture::getHeader () const { return *this->m_header; }

const glm::vec4* CTexture::getResolution () const { return &this->m_resolution; }

const std::vector<FrameSharedPtr>& CTexture::getFrames () const { return this->getHeader ().frames; }

bool CTexture::isAnimated () const { return this->getHeader ().isAnimated (); }

uint32_t CTexture::getSpritesheetCols () const { return this->getHeader ().spritesheetCols; }

uint32_t CTexture::getSpritesheetRows () const { return this->getHeader ().spritesheetRows; }

uint32_t CTexture::getSpritesheetFrames () const { return this->getHeader ().spritesheetFrames; }

float CTexture::getSpritesheetDuration () const { return this->getHeader ().spritesheetDuration; }

void CTexture::incrementUsageCount () const {
    if (this->m_player) {
	this->m_player->incrementUsageCount ();
    }
}

void CTexture::decrementUsageCount () const {
    if (this->m_player) {
	this->m_player->decrementUsageCount ();
    }
}

void CTexture::update () const {
    if (this->m_player) {
	this->m_player->render ();
    }
}

// CTextures are always ready to be rendered at all times
bool CTexture::isReady () const { return true; }