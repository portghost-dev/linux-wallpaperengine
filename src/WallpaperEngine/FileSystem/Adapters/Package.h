#pragma once

#include <filesystem>
#include <unordered_map>

#include "Types.h"
#include "WallpaperEngine/Data/Assets/Package.h"
#include "WallpaperEngine/Data/Assets/Types.h"

namespace WallpaperEngine::FileSystem::Adapters {
using namespace WallpaperEngine::Data::Assets;

struct PackageFactory final : Factory {
    PackageFactory () = default;

    [[nodiscard]] bool handlesMountpoint (const std::filesystem::path& path) const override;
    [[nodiscard]] AdapterSharedPtr create (const std::filesystem::path& path) const override;
};

struct PackageAdapter final : Adapter {
    explicit PackageAdapter (PackageUniquePtr package);

    [[nodiscard]] ReadStreamSharedPtr open (const std::filesystem::path& path) const override;
    [[nodiscard]] bool exists (const std::filesystem::path& path) const override;
    [[nodiscard]] std::filesystem::path physicalPath (const std::filesystem::path& path) const override;

    PackageUniquePtr package;

private:
    [[nodiscard]] const FileEntry* find (const std::filesystem::path& path) const;

    /** The package's entries, keyed by lowercased filename */
    std::unordered_map<std::string, const FileEntry*> m_index;
};
}