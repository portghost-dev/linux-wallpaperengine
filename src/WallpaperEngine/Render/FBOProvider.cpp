#include "FBOProvider.h"

#include "WallpaperEngine/Logging/Log.h"
#include <cstdlib>

using namespace WallpaperEngine::Render;
using namespace WallpaperEngine::Data::Model;

namespace {
uint32_t bytesPerTexel (const TextureFormat format) {
    switch (format) {
	case TextureFormat_RGBA16161616f:
	    return 8;
	case TextureFormat_RGB161616f:
	    return 6;
	case TextureFormat_RG1616f:
	    return 4;
	case TextureFormat_RGBa1010102:
	    return 4;
	case TextureFormat_RGB888:
	    return 3;
	case TextureFormat_RG88:
	case TextureFormat_RGB565:
	case TextureFormat_R16f:
	    return 2;
	case TextureFormat_R8:
	    return 1;
	default:
	    return 4;
    }
}

void logAllocation (const std::string& name, const TextureFormat format, const float w, const float h) {
    static const bool enabled = getenv ("LWE_FBOALLOC") != nullptr;

    if (!enabled) {
	return;
    }

    static uint64_t runningTotal = 0;
    const auto bytes = static_cast<uint64_t> (w) * static_cast<uint64_t> (h) * bytesPerTexel (format);
    runningTotal += bytes;

    sLog.out (
	"LWE-FBOALLOC ", name, " ", static_cast<int> (w), "x", static_cast<int> (h), " fmt=", static_cast<int> (format),
	" bpt=", bytesPerTexel (format), " bytes=", bytes, " (", bytes / (1024 * 1024),
	" MiB) runningTotal=", runningTotal / (1024 * 1024), " MiB"
    );
}
} // namespace

FBOProvider::FBOProvider (const FBOProvider* parent) : m_parent (parent) { }

std::shared_ptr<CFBO> FBOProvider::create (const FBO& base, uint32_t flags, const glm::vec2 size) {
    logAllocation (base.name, TextureFormat_ARGB8888, size.x / base.scale, size.y / base.scale);

    return this->m_fbos[base.name] = std::make_shared<CFBO> (
	       base.name,
	       // TODO: PROPERLY DETERMINE FBO FORMAT BASED ON THE STRING
	       TextureFormat_ARGB8888, flags, base.scale, size.x / base.scale, size.y / base.scale, size.x / base.scale,
	       size.y / base.scale
	   );
}

std::shared_ptr<CFBO> FBOProvider::create (
    const std::string& name, TextureFormat format, uint32_t flags, float scale, glm::vec2 realSize,
    glm::vec2 textureSize
) {
    logAllocation (name, format, realSize.x, realSize.y);

    return this->m_fbos[name]
	= std::make_shared<CFBO> (name, format, flags, scale, realSize.x, realSize.y, textureSize.x, textureSize.y);
}

namespace {
bool fboTraceEnabled () {
    static const bool enabled = getenv ("LWE_FBOTRACE") != nullptr;
    return enabled;
}
} // namespace

std::shared_ptr<CFBO> FBOProvider::alias (const std::string& newName, const std::string& original) {
    if (fboTraceEnabled ()) {
	const auto& target = this->m_fbos[original];
	sLog.out ("LWE-FBOTRACE alias ", newName, " -> ", original, " tex=", target ? target->getTextureID (0) : 0);
    }
    return this->m_fbos[newName] = this->m_fbos[original];
}

std::shared_ptr<CFBO> FBOProvider::alias (const std::string& newName, const std::shared_ptr<CFBO>& original) {
    if (fboTraceEnabled ()) {
	sLog.out (
	    "LWE-FBOTRACE alias ", newName, " -> ", original ? original->getName () : "<null>",
	    " tex=", original ? original->getTextureID (0) : 0
	);
    }
    return this->m_fbos[newName] = original;
}

std::shared_ptr<CFBO> FBOProvider::find (const std::string& name) const {
    if (const auto it = this->m_fbos.find (name); it != this->m_fbos.end ()) {
	return it->second;
    }

    if (this->m_parent == nullptr) {
	return nullptr;
    }

    return this->m_parent->find (name);
}