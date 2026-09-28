#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>

#include <cstring>
#include <memory>
#include <string>

#include "WallpaperEngine/Data/Assets/Package.h"
#include "WallpaperEngine/Data/Parsers/PackageParser.h"
#include "WallpaperEngine/Data/Parsers/TextureParser.h"
#include "WallpaperEngine/Data/Utils/BinaryReader.h"
#include "WallpaperEngine/Data/Utils/MemoryStream.h"

using namespace WallpaperEngine::Data::Utils;
using namespace WallpaperEngine::Data::Parsers;
using Catch::Matchers::ContainsSubstring;

namespace {
void u32 (std::string& b, uint32_t v) {
    b.push_back (char (v & 0xFF));
    b.push_back (char ((v >> 8) & 0xFF));
    b.push_back (char ((v >> 16) & 0xFF));
    b.push_back (char ((v >> 24) & 0xFF));
}

// texture magics are read as 9 bytes and compared with strncmp(..., 9): 8 chars + NUL
void magic (std::string& b, const char* m) {
    b.append (m, 8);
    b.push_back ('\0');
}

std::shared_ptr<std::istream> streamOf (const std::string& bytes) {
    auto buf = std::make_unique<char[]> (bytes.size ());
    std::memcpy (buf.get (), bytes.data (), bytes.size ());
    return std::make_shared<MemoryStream> (std::move (buf), bytes.size ());
}

std::shared_ptr<BinaryReader> reader (const std::string& bytes) {
    return std::make_shared<BinaryReader> (streamOf (bytes));
}

std::string validTexture (uint32_t flags, int compressedSize, const std::string& payload) {
    std::string b;
    magic (b, "TEXV0005");
    magic (b, "TEXI0001");
    u32 (b, 0);
    u32 (b, flags);
    u32 (b, 4);
    u32 (b, 4);
    u32 (b, 4);
    u32 (b, 4);
    u32 (b, 0 /* ignored */);
    magic (b, "TEXB0001");
    u32 (b, 1);
    u32 (b, 1);
    u32 (b, 1);
    u32 (b, 1);
    u32 (b, uint32_t (compressedSize)); // compression stays 0 for TEXB0001
    b += payload;
    return b;
}

std::string
rawTexture (uint32_t format, uint32_t width, uint32_t height, const std::string& payload, uint32_t flags = 0) {
    std::string b;
    magic (b, "TEXV0005");
    magic (b, "TEXI0001");
    u32 (b, format);
    u32 (b, flags);
    u32 (b, width);
    u32 (b, height);
    u32 (b, width);
    u32 (b, height);
    u32 (b, 0);
    magic (b, "TEXB0002");
    u32 (b, 1);
    u32 (b, 1);
    u32 (b, width);
    u32 (b, height);
    u32 (b, 0);
    u32 (b, uint32_t (payload.size ()));
    u32 (b, uint32_t (payload.size ()));
    b += payload;
    return b;
}

std::string rawGLTexture (uint32_t format, uint32_t width, uint32_t height, const std::string& payload) {
    std::string b;
    magic (b, "TEXV0005");
    magic (b, "TEXI0001");
    u32 (b, format);
    u32 (b, 0);
    u32 (b, width);
    u32 (b, height);
    u32 (b, width);
    u32 (b, height);
    u32 (b, 0);
    magic (b, "TEXB0004");
    u32 (b, 1);
    u32 (b, uint32_t (FIF_UNKNOWN));
    u32 (b, 0);
    u32 (b, 1);
    u32 (b, 0);
    u32 (b, 0);
    u32 (b, 1);
    u32 (b, uint32_t (payload.size ()));
    u32 (b, uint32_t (payload.size ()));
    b += payload;
    return b;
}

std::string fileImageTexture (uint32_t width, uint32_t height, const std::string& payload) {
    std::string b;
    magic (b, "TEXV0005");
    magic (b, "TEXI0001");
    u32 (b, TextureFormat_ARGB8888);
    u32 (b, 0);
    u32 (b, width);
    u32 (b, height);
    u32 (b, width);
    u32 (b, height);
    u32 (b, 0);
    magic (b, "TEXB0003");
    u32 (b, 1);
    u32 (b, FIF_PNG);
    u32 (b, 1);
    u32 (b, width);
    u32 (b, height);
    u32 (b, 0);
    u32 (b, uint32_t (payload.size ()));
    u32 (b, uint32_t (payload.size ()));
    b += payload;
    return b;
}

std::string pngTextureWithCounts (uint32_t imageCount, uint32_t mipmapCount) {
    std::string b;
    magic (b, "TEXV0005");
    magic (b, "TEXI0001");
    u32 (b, TextureFormat_ARGB8888);
    u32 (b, 0);
    u32 (b, 4);
    u32 (b, 4);
    u32 (b, 4);
    u32 (b, 4);
    u32 (b, 0);
    magic (b, "TEXB0003");
    u32 (b, imageCount);
    u32 (b, FIF_PNG);
    for (uint32_t image = 0; image < imageCount; image++) {
	u32 (b, mipmapCount);
    }
    return b;
}
} // namespace

TEST_CASE ("BinaryReader rejects truncated reads instead of returning garbage") {
    std::string b;
    b.push_back (0x11);
    b.push_back (0x22);

    REQUIRE_THROWS (reader (b)->nextUInt32 ());
}

TEST_CASE ("BinaryReader reads a complete uint32") {
    std::string b;
    u32 (b, 0xDEADBEEF);

    REQUIRE (reader (b)->nextUInt32 () == 0xDEADBEEF);
}

TEST_CASE ("BinaryReader null-terminated string stops at end of stream") {
    // no terminator present: must return what it read rather than spin on an indeterminate byte
    std::string b = "abc";

    REQUIRE (reader (b)->nextNullTerminatedString () == "abc");
}

TEST_CASE ("BinaryReader sized string round-trips and rejects oversized lengths") {
    SECTION ("valid length") {
	std::string b;
	u32 (b, 3);
	b += "abc";

	REQUIRE (reader (b)->nextSizedString () == "abc");
    }

    SECTION ("length beyond the stream is rejected before allocating") {
	std::string b;
	u32 (b, 0xFFFFFFFF);

	REQUIRE_THROWS (reader (b)->nextSizedString ());
    }
}

TEST_CASE ("MemoryStream refuses seeks outside the buffer") {
    SECTION ("in-range seek succeeds") {
	auto r = reader (std::string (4, 'x'));
	r->base ().seekg (2, std::ios::beg);

	REQUIRE (r->base ().good ());
	REQUIRE (r->base ().tellg () == 2);
    }

    SECTION ("out-of-range seek fails cleanly") {
	auto r = reader (std::string (4, 'x'));
	r->base ().seekg (1000, std::ios::beg);

	REQUIRE (r->base ().fail ());
    }
}

TEST_CASE ("PackageParser bounds the declared file count") {
    SECTION ("empty but well-formed package parses") {
	std::string b;
	u32 (b, 8);
	b += "PKGV0001";
	u32 (b, 0);

	REQUIRE_NOTHROW (PackageParser::parse (streamOf (b)));
    }

    SECTION ("absurd file count is rejected instead of reserving gigabytes") {
	std::string b;
	u32 (b, 8);
	b += "PKGV0001";
	u32 (b, 0xFFFFFFFF);

	REQUIRE_THROWS (PackageParser::parse (streamOf (b)));
    }
}

TEST_CASE ("TextureParser validates untrusted sizes and counts") {
    SECTION ("a valid static texture parses") {
	REQUIRE_NOTHROW (TextureParser::parse (*reader (validTexture (0, 4, std::string (4, '\0')))));
    }

    SECTION ("a negative mipmap size is rejected before new[]") {
	REQUIRE_THROWS (TextureParser::parse (*reader (validTexture (0, -1, ""))));
    }

    SECTION ("an animated texture with zero frames does not dereference an empty vector") {
	std::string b = validTexture (4, 4, std::string (4, '\0'));
	magic (b, "TEXS0001");
	u32 (b, 0);

	REQUIRE_THROWS (TextureParser::parse (*reader (b)));
    }
}

TEST_CASE ("TextureParser refuses raw payloads smaller than their pixels and empty image or mip lists") {
    SECTION ("a 4096x4096 R8 mip with a 1-byte payload is refused") {
	REQUIRE_THROWS_WITH (
	    TextureParser::parse (*reader (rawTexture (TextureFormat_R8, 4096, 4096, std::string (1, '\0')))),
	    ContainsSubstring ("smaller than 4096x4096 pixels at 1 bytes each")
	);
    }

    SECTION ("an 8192x8192 ARGB8888 mip with a 16-byte payload is refused") {
	REQUIRE_THROWS_WITH (
	    TextureParser::parse (*reader (rawTexture (TextureFormat_ARGB8888, 8192, 8192, std::string (16, '\0')))),
	    ContainsSubstring ("smaller than 8192x8192 pixels at 4 bytes each")
	);
    }

    SECTION ("a 2x2 ARGB8888 mip with an 8-byte payload is refused") {
	REQUIRE_THROWS_WITH (
	    TextureParser::parse (*reader (rawTexture (TextureFormat_ARGB8888, 2, 2, std::string (8, '\0')))),
	    ContainsSubstring ("smaller than 2x2 pixels at 4 bytes each")
	);
    }

    SECTION ("a 2x2 RG88 raw-GL mip with a 4-byte payload is refused") {
	REQUIRE_THROWS_WITH (
	    TextureParser::parse (*reader (rawGLTexture (TextureFormat_RG88, 2, 2, std::string (4, '\0')))),
	    ContainsSubstring ("smaller than 2x2 pixels at 2 bytes each")
	);
    }

    SECTION ("a texture that declares zero images is refused") {
	REQUIRE_THROWS_WITH (
	    TextureParser::parse (*reader (pngTextureWithCounts (0, 1))), ContainsSubstring ("declares no images")
	);
    }

    SECTION ("an image that declares zero mips is refused") {
	REQUIRE_THROWS_WITH (
	    TextureParser::parse (*reader (pngTextureWithCounts (1, 0))), ContainsSubstring ("declares 0 mipmaps")
	);
    }

    SECTION ("a valid small texture still loads") {
	REQUIRE_NOTHROW (TextureParser::parse (*reader (rawTexture (TextureFormat_R8, 2, 2, std::string (4, '\0')))));
    }
}

TEST_CASE ("TextureParser refuses a zero pixel dimension and keeps its exemptions and its wide size math") {
    SECTION ("a raw R8 mip 0 pixels wide is refused") {
	REQUIRE_THROWS_WITH (
	    TextureParser::parse (*reader (rawTexture (TextureFormat_R8, 0, 4, ""))),
	    ContainsSubstring ("has a zero dimension")
	);
    }

    SECTION ("a raw R8 mip 0 pixels high is refused") {
	REQUIRE_THROWS_WITH (
	    TextureParser::parse (*reader (rawTexture (TextureFormat_R8, 4, 0, ""))),
	    ContainsSubstring ("has a zero dimension")
	);
    }

    SECTION ("a raw-GL RG88 mip 0 pixels wide is refused") {
	REQUIRE_THROWS_WITH (
	    TextureParser::parse (*reader (rawGLTexture (TextureFormat_RG88, 0, 4, ""))),
	    ContainsSubstring ("has a zero dimension")
	);
    }

    SECTION ("a 65536x65536 R8 mip with a 16-byte payload is refused") {
	REQUIRE_THROWS_WITH (
	    TextureParser::parse (*reader (rawTexture (TextureFormat_R8, 65536, 65536, std::string (16, '\0')))),
	    ContainsSubstring ("smaller than 65536x65536 pixels at 1 bytes each")
	);
    }

    SECTION ("a video-flagged raw texture with a short stream parses") {
	REQUIRE_NOTHROW (
	    TextureParser::parse (
		*reader (rawTexture (TextureFormat_ARGB8888, 64, 64, std::string (16, '\0'), TextureFlags_Video))
	    )
	);
    }

    SECTION ("a file-image texture with short file data parses") {
	REQUIRE_NOTHROW (TextureParser::parse (*reader (fileImageTexture (64, 64, std::string (16, '\0')))));
    }
}
