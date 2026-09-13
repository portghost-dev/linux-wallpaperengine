#include <catch2/catch_test_macros.hpp>
#include <memory>
#include <string>

#include "WallpaperEngine/Data/Assets/Texture.h"
#include "WallpaperEngine/Data/Parsers/TextureParser.h"
#include "WallpaperEngine/Data/Utils/BinaryReader.h"
#include "WallpaperEngine/Data/Utils/MemoryStream.h"

using namespace WallpaperEngine::Data::Assets;
using namespace WallpaperEngine::Data::Utils;
using namespace WallpaperEngine::Data::Parsers;

namespace {
void u32 (std::string& b, uint32_t v) {
    b.push_back (char (v & 0xFF));
    b.push_back (char ((v >> 8) & 0xFF));
    b.push_back (char ((v >> 16) & 0xFF));
    b.push_back (char ((v >> 24) & 0xFF));
}

void magic (std::string& b, const char* m) {
    b.append (m, 8);
    b.push_back ('\0');
}

std::shared_ptr<BinaryReader> reader (const std::string& bytes) {
    auto buf = std::make_unique<char[]> (bytes.size ());
    memcpy (buf.get (), bytes.data (), bytes.size ());
    return std::make_shared<BinaryReader> (std::make_shared<MemoryStream> (std::move (buf), bytes.size ()));
}

// the smallest complete TEXV0005 / TEXB0001 texture: one 4x4 mip of raw bytes
std::string textureWithFlags (uint32_t flags) {
    std::string b;
    magic (b, "TEXV0005");
    magic (b, "TEXI0001");
    u32 (b, 0); // ARGB8888
    u32 (b, flags);
    u32 (b, 4);
    u32 (b, 4);
    u32 (b, 4);
    u32 (b, 4);
    u32 (b, 0);
    magic (b, "TEXB0001");
    u32 (b, 1);
    u32 (b, 1);
    u32 (b, 1);
    u32 (b, 1);
    u32 (b, 4);
    b.append (4, '\0');
    return b;
}
} // namespace

// The editor marks painted PBR mask channels with header bits 20..23 (metallic, roughness, reflection, emissive);
// the runtime enables the matching <COMPONENT>_MAP combo only from these bits, so the parser must keep them.
TEST_CASE ("Texture parser keeps the painted-component flag bits") {
    // Tron bike mask: ClampUVs + metallic painted
    CHECK (TextureParser::parse (*reader (textureWithFlags (0x100002)))->flags == (TextureFlags_ClampUVs | TextureFlags_ComponentMetallic));
    // 3D Earth masks: emissive painted
    CHECK (TextureParser::parse (*reader (textureWithFlags (0x800000)))->flags == TextureFlags_ComponentEmissive);
    CHECK (TextureParser::parse (*reader (textureWithFlags (0xF00000)))->flags == TextureFlags_ComponentMask);
    // unrelated unknown bits are still dropped
    CHECK (TextureParser::parse (*reader (textureWithFlags (0x1000002)))->flags == TextureFlags_ClampUVs);
}

TEST_CASE ("Component flag bits map to the shader component order") {
    // genericimage4 `components`: metallic, roughness, reflection, emissive -> bits 20, 21, 22, 23
    CHECK (TextureFlags_ComponentMetallic == (1u << 20));
    CHECK (TextureFlags_ComponentRoughness == (1u << 21));
    CHECK (TextureFlags_ComponentReflection == (1u << 22));
    CHECK (TextureFlags_ComponentEmissive == (1u << 23));
}
