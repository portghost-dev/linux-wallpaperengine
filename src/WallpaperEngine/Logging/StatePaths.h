#pragma once

#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <streambuf>
#include <string>
#include <system_error>

// The engine's half of the state tree under $XDG_STATE_HOME/lwe: engine/ for its own
// files, logs/<subsystem>/ for real-time logs, probes/ for instrument dumps. Header-only so
// every binary (engine, web service) resolves the same paths without a link dependency.
namespace WallpaperEngine::State {

inline std::filesystem::path root () {
    const char* xdgState = std::getenv ("XDG_STATE_HOME");

    if (xdgState != nullptr && xdgState[0] != '\0') {
	return std::filesystem::path (xdgState) / "lwe";
    }

    const char* home = std::getenv ("HOME");

    return std::filesystem::path (home != nullptr ? home : "/tmp") / ".local" / "state" / "lwe";
}

inline std::filesystem::path engineDir () { return root () / "engine"; }

inline std::filesystem::path logDir (const std::string& subsystem) { return root () / "logs" / subsystem; }

inline std::filesystem::path probesDir () { return root () / "probes"; }

// path -> path.1 -> ... -> path.<keep>; the oldest generation falls off the end
inline void rotate (const std::filesystem::path& path, int keep) {
    std::error_code ec;
    std::filesystem::create_directories (path.parent_path (), ec);

    for (int generation = keep; generation > 0; --generation) {
	std::filesystem::path older = path;
	older += "." + std::to_string (generation);
	std::filesystem::path newer = path;

	if (generation > 1) {
	    newer += "." + std::to_string (generation - 1);
	}

	if (std::filesystem::exists (newer, ec)) {
	    std::filesystem::rename (newer, older, ec);
	}
    }
}

namespace Detail {
    // a file moves when nothing sits at dest; a directory merges entry by entry into an
    // existing dest, so a cache the panel started before the engine's first boot keeps
    // both halves; the emptied source dir is removed
    inline int moveInto (const std::filesystem::path& src, const std::filesystem::path& dest) {
	std::error_code ec;

	if (!std::filesystem::exists (src, ec)) {
	    return 0;
	}

	if (!std::filesystem::exists (dest, ec)) {
	    std::filesystem::create_directories (dest.parent_path (), ec);
	    std::filesystem::rename (src, dest, ec);

	    return ec ? 0 : 1;
	}

	if (!std::filesystem::is_directory (src, ec) || !std::filesystem::is_directory (dest, ec)) {
	    return 0;
	}

	int moved = 0;

	for (const auto& entry : std::filesystem::directory_iterator (src, ec)) {
	    moved += moveInto (entry.path (), dest / entry.path ().filename ());
	}

	std::filesystem::remove (src, ec); // only succeeds once empty

	return moved;
    }
} // namespace Detail

// One-time move of the engine's files from the flat state dir into the tree. Nothing is
// overwritten and nothing is deleted; a file already in place is left alone. Returns the
// number of entries moved.
inline int migrateEngineFiles () {
    const auto top = root ();
    int moved = 0;
    std::error_code ec;

    for (const char* name : { "engine-state.json", "boot-history.json", "texcache" }) {
	moved += Detail::moveInto (top / name, engineDir () / name);
    }

    for (const char* name : { "cef.log", "cef.log.1", "cef.log.2", "cef.log.3" }) {
	moved += Detail::moveInto (top / name, logDir ("cef") / name);
    }

    moved += Detail::moveInto (top / "passprobe-post.ppm", probesDir () / "passprobe-post.ppm");

    if (std::filesystem::is_directory (top, ec)) {
	for (const auto& entry : std::filesystem::directory_iterator (top, ec)) {
	    const std::string file = entry.path ().filename ().string ();

	    if (file.rfind ("shaderdump-", 0) == 0) {
		moved += Detail::moveInto (entry.path (), probesDir () / file);
	    }
	}
    }

    return moved;
}

// A file sink for the logger: every line starts with a local timestamp and is flushed as it
// ends, so the file is complete at the moment of a crash.
class TimestampedFileBuf : public std::streambuf {
public:
    bool open (const std::filesystem::path& path) {
	std::error_code ec;
	std::filesystem::create_directories (path.parent_path (), ec);

	return this->m_file.open (path, std::ios::out | std::ios::app) != nullptr;
    }

protected:
    int overflow (int c) override {
	if (c == traits_type::eof ()) {
	    return this->m_file.pubsync () == 0 ? 0 : traits_type::eof ();
	}

	if (this->m_lineStart) {
	    this->stamp ();
	    this->m_lineStart = false;
	}

	if (this->m_file.sputc (static_cast<char> (c)) == traits_type::eof ()) {
	    return traits_type::eof ();
	}

	if (c == '\n') {
	    this->m_lineStart = true;
	    this->m_file.pubsync ();
	}

	return c;
    }

    int sync () override { return this->m_file.pubsync (); }

private:
    void stamp () {
	const std::time_t now = std::time (nullptr);
	std::tm local {};
	localtime_r (&now, &local);
	char text[32];
	const size_t len = std::strftime (text, sizeof (text), "%Y-%m-%d %H:%M:%S ", &local);
	this->m_file.sputn (text, static_cast<std::streamsize> (len));
    }

    std::filebuf m_file;
    bool m_lineStart = true;
};

} // namespace WallpaperEngine::State
