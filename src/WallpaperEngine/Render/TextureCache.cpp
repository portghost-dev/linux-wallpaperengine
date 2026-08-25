#include "TextureCache.h"

#include "AlbumTexture.h"
#include "WallpaperEngine/FileSystem/Container.h"

#include "CTexture.h"
#include "WallpaperEngine/Assets/AssetLoadException.h"
#include "WallpaperEngine/Render/Helpers/ContextAware.h"
#include "WallpaperEngine/Render/MipResidency.h"

#include "WallpaperEngine/Data/Model/Project.h"
#include "WallpaperEngine/Data/Parsers/TextureParser.h"

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <fstream>

using namespace WallpaperEngine::Render;
using namespace WallpaperEngine::FileSystem;
using namespace WallpaperEngine::Data::Parsers;
using namespace WallpaperEngine::Data::Assets;

TextureCache::TextureCache (RenderContext& context) : Helpers::ContextAware (context) {
    // these textures are special cases, so make sure they're created only upon request
    this->m_currentThumbnail = std::make_shared<AlbumTexture> (this->getContext ());

#if !NDEBUG
    glObjectLabel (GL_TEXTURE, this->m_currentThumbnail->getTextureID (0), -1, "$mediaThumbnail");
#endif

    this->m_previousThumbnail = std::make_shared<AlbumTexture> (this->getContext ());

#if !NDEBUG
    glObjectLabel (GL_TEXTURE, this->m_previousThumbnail->getTextureID (0), -1, "$mediaPreviousThumbnail");
#endif

    // load the latest texture (if available)
    this->m_currentThumbnail->load ();

    // add these to the cache and return the right one
    this->store ("$mediaThumbnail", this->m_currentThumbnail);
    this->store ("$mediaPreviousThumbnail", this->m_previousThumbnail);

    this->m_mediaCallback = this->getContext ().getMediaSource ().addAlbumArtListener (
	[this] (const Media::MediaSource::MediaInfo& data) {
	    if (this->m_currentThumbnail->isReady ()) {
		// copy over pixel data and setup the new texture with the new data
		this->m_previousThumbnail->copyContents (*this->m_currentThumbnail);
	    }

	    // load the next image
	    this->m_currentThumbnail->load ();
	}
    );
}

TextureCache::~TextureCache () { this->m_mediaCallback (); }

std::shared_ptr<const TextureProvider> TextureCache::resolve (const std::string& filename) {
    if (const auto found = this->m_textureCache.find (filename); found != this->m_textureCache.end ()) {
	return found->second;
    }

    // search for the texture in all the different containers just in case
    for (const auto& project : this->getContext ().getApp ().getBackgrounds () | std::views::values) {
	try {
	    const auto contents = project->assetLocator->texture (filename);
	    auto stream = BinaryReader (contents);

	    // Create metadata loader lambda that captures the assetLocator
	    // so we need to construct the full path here
	    auto metadataLoader = [&project] (const std::string& metaFilename) -> std::string {
		std::filesystem::path fullPath = std::filesystem::path ("materials") / metaFilename;
		return project->assetLocator->readString (fullPath);
	    };

	    auto parsedTexture = TextureParser::parse (stream, filename, metadataLoader);
	    // mip residency: the scene-load reference map decided this texture's
	    // cappability before anything resolved; the cap itself resolves LIVE from
	    // the current outputs at this moment (hotplug-fresh, never launch-cached);
	    // 0 = full chain (default path)
	    const int capDim = MipResidency::cappable (filename)
		? MipResidency::capDimension (MipResidency::largestOutputDimension (this->getContext ()))
		: 0;
	    auto texture = std::make_shared<CTexture> (this->getContext (), std::move (parsedTexture), capDim);

#if !NDEBUG
	    glObjectLabel (GL_TEXTURE, texture->getTextureID (0), -1, filename.c_str ());
#endif

	    if (getenv ("LWE_AUDIT") != nullptr) {
		sLog.out ("LWE-AUDIT texture resolve '", filename, "' -> texid=", texture->getTextureID (0));
	    }
	    this->store (filename, texture);

	    return texture;
	} catch (AssetLoadException&) {
	    // ignored, this happens if we're looking at the wrong background
	}
    }

    // TODO: FILL IN WITH A CHECKERED PATTERN TEXTURE INSTEAD?
    throw AssetLoadException ("Cannot find file", filename, std::error_code ());
}

void TextureCache::store (const std::string& name, std::shared_ptr<const TextureProvider> texture) {
    this->m_textureCache.insert_or_assign (name, texture);
}

size_t TextureCache::evictUnused () {
    static const bool dump = getenv ("LWE_TEXCACHEDUMP") != nullptr;
    size_t evicted = 0;

    for (auto it = this->m_textureCache.begin (); it != this->m_textureCache.end ();) {
	if (it->second.use_count () == 1) {
	    it = this->m_textureCache.erase (it);
	    evicted++;
	} else {
	    if (dump) {
		sLog.out ("LWE-TEXCACHE survivor ", it->first, " refs=", it->second.use_count ());
	    }
	    ++it;
	}
    }

    return evicted;
}
