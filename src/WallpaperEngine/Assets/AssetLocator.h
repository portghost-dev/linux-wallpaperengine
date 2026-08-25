#pragma once

#include "WallpaperEngine/FileSystem/Container.h"

namespace WallpaperEngine::Media {
class MediaSource;
}

namespace WallpaperEngine::Assets {
using namespace WallpaperEngine::FileSystem;
using namespace WallpaperEngine::Data::Model;
class AssetLocator {
public:
    explicit AssetLocator (ContainerUniquePtr filesystem);

    std::string vertexShader (const std::filesystem::path& filename) const;
    std::string fragmentShader (const std::filesystem::path& filename) const;
    std::string includeShader (const std::filesystem::path& filename) const;
    ReadStreamSharedPtr texture (const std::filesystem::path& filename) const;
    std::string readString (const std::filesystem::path& filename) const;
    ReadStreamSharedPtr read (const std::filesystem::path& path) const;
    std::filesystem::path physicalPath (const std::filesystem::path& path) const;
    /** scene-dependent virtual assets (the HDR bloom ladder's effect file varies
     *  with bloomhdriterations, so CScene registers it at construction time) */
    VirtualAdapter& getVFS () const;

private:
    std::string shader (const std::filesystem::path& filename) const;

    ContainerUniquePtr m_filesystem;
};

using AssetLocatorUniquePtr = std::unique_ptr<AssetLocator>;

AssetLocatorUniquePtr
setupAssetLocator (const std::string& bg, const std::filesystem::path& assetsPath, Media::MediaSource& mediaSource);
}