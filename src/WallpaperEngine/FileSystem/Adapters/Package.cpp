#include <fstream>
#include <memory>

#include "Package.h"

#include "WallpaperEngine/Assets/AssetLoadException.h"
#include "WallpaperEngine/Data/Parsers/PackageParser.h"
#include "WallpaperEngine/Data/Utils/BinaryReader.h"
#include "WallpaperEngine/Data/Utils/MemoryStream.h"

#include <algorithm>
#include <cctype>

using namespace WallpaperEngine::FileSystem;
using namespace WallpaperEngine::FileSystem::Adapters;

namespace {
std::string lowercase (std::string value) {
    std::ranges::transform (value, value.begin (), [] (const unsigned char character) {
	return static_cast<char> (std::tolower (character));
    });

    return value;
}
} // namespace

PackageAdapter::PackageAdapter (PackageUniquePtr package) : package (std::move (package)) {
    // wallpapers are authored on Windows, so a scene referencing "sounds/song.mp3" still resolves
    // when the package stores "Sounds/song.mp3". lookups have to ignore case to match that
    for (const auto& file : this->package->files) {
	this->m_index.emplace (lowercase (file->filename), file.get ());
    }
}

const FileEntry* PackageAdapter::find (const std::filesystem::path& path) const {
    const auto it = this->m_index.find (lowercase (path.string ()));

    return it == this->m_index.end () ? nullptr : it->second;
}

ReadStreamSharedPtr PackageAdapter::open (const std::filesystem::path& path) const {
    const auto* entry = this->find (path);

    if (entry == nullptr) {
	throw std::filesystem::filesystem_error ("Cannot find file", path, std::error_code ());
    }

    // read file into memory
    auto buffer = std::make_unique<char[]> (entry->length);

    // go to the file's position and read into the buffer
    this->package->file->base ().seekg (entry->offset + this->package->baseOffset, std::ios::beg);
    this->package->file->next (buffer.get (), entry->length);

    // create a memory stream and return that
    return std::make_shared<MemoryStream> (std::move (buffer), entry->length);
}

bool PackageAdapter::exists (const std::filesystem::path& path) const { return this->find (path) != nullptr; }

std::filesystem::path PackageAdapter::physicalPath (const std::filesystem::path& path) const {
    throw std::filesystem::filesystem_error ("Package adapter does not support realpath", path, std::error_code ());
}

bool PackageFactory::handlesMountpoint (const std::filesystem::path& path) const {
    try {
	const auto finalpath = std::filesystem::canonical (path);
	const auto status = std::filesystem::status (finalpath);

	return std::filesystem::exists (finalpath) && std::filesystem::is_regular_file (status)
	    && finalpath.extension () == ".pkg";
    } catch (std::filesystem::filesystem_error&) {
	return false;
    }
}

AdapterSharedPtr PackageFactory::create (const std::filesystem::path& path) const {
    const auto stream = std::make_shared<std::ifstream> (path, std::ios::binary);
    auto package = Data::Parsers::PackageParser::parse (stream);

    return std::make_unique<PackageAdapter> (std::move (package));
}
