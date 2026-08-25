#pragma once

#include <filesystem>
#include <string>
#include <vector>

namespace Steam::FileSystem {
std::filesystem::path workshopDirectory (int appID, const std::string& contentID);
std::filesystem::path appDirectory (const std::string& appDirectory, const std::string& path);
/** every EXISTING workshop content root for the app (e.g. .../workshop/content/431960) */
std::vector<std::filesystem::path> workshopContentRoots (int appID);
} // namespace Steam::FileSystem