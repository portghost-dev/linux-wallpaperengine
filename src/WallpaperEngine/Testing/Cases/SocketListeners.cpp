#include <catch2/catch_test_macros.hpp>

#include <cstring>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include "WallpaperEngine/Api/CommandServer.h"
#include "WallpaperEngine/WebHelper/MessageChannel.h"

using WallpaperEngine::Api::CommandServer;
using WallpaperEngine::WebHelper::MessageListener;

namespace {

struct ScratchFolder {
    std::filesystem::path path;

    explicit ScratchFolder (const std::string& name) :
	path (
	    std::filesystem::temp_directory_path () / ("lwe-listener-test-" + std::to_string (getpid ()) + "-" + name)
	) {
	std::filesystem::remove_all (this->path);
	std::filesystem::create_directories (this->path);
    }

    ~ScratchFolder () {
	std::error_code ignored;
	std::filesystem::remove_all (this->path, ignored);
    }
};

void writeFile (const std::filesystem::path& path, const std::string& text) {
    std::ofstream out (path);
    out << text;
}

std::string readFile (const std::filesystem::path& path) {
    std::ifstream in (path);
    std::stringstream text;
    text << in.rdbuf ();
    return text.str ();
}

bool isRegularFile (const std::filesystem::path& path) {
    struct stat entry {};
    return lstat (path.c_str (), &entry) == 0 && S_ISREG (entry.st_mode);
}

bool isSocket (const std::filesystem::path& path) {
    struct stat entry {};
    return lstat (path.c_str (), &entry) == 0 && S_ISSOCK (entry.st_mode);
}

sockaddr_un addressOf (const std::filesystem::path& path) {
    sockaddr_un addr {};
    addr.sun_family = AF_UNIX;
    std::strncpy (addr.sun_path, path.c_str (), sizeof (addr.sun_path) - 1);
    return addr;
}

void leaveStaleSocket (const std::filesystem::path& path) {
    const int fd = socket (AF_UNIX, SOCK_STREAM, 0);
    REQUIRE (fd >= 0);

    const sockaddr_un addr = addressOf (path);
    const bool bound = bind (fd, reinterpret_cast<const sockaddr*> (&addr), sizeof (addr)) == 0;
    close (fd);
    REQUIRE (bound);
}

bool answers (const std::filesystem::path& path) {
    const int fd = socket (AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK, 0);
    REQUIRE (fd >= 0);

    const sockaddr_un addr = addressOf (path);
    const bool connected = connect (fd, reinterpret_cast<const sockaddr*> (&addr), sizeof (addr)) == 0;
    close (fd);
    return connected;
}

mode_t folderMode (const std::filesystem::path& folder) {
    struct stat info {};
    REQUIRE (stat (folder.c_str (), &info) == 0);
    return info.st_mode & 0777;
}
} // namespace

TEST_CASE ("CommandServer refuses a path that holds a file that is not a socket", "[socket]") {
    const ScratchFolder folder ("engine-file");
    const auto path = folder.path / "notes.txt";
    writeFile (path, "keep me\n");
    REQUIRE (isRegularFile (path));

    {
	CommandServer server (path);
	CHECK_FALSE (server.listen ());
	CHECK (server.error () == path.string () + " exists and is not a socket; refusing to replace it");
	CHECK_FALSE (server.isListening ());
    }

    CHECK (isRegularFile (path));
    CHECK (readFile (path) == "keep me\n");
}

TEST_CASE ("MessageListener refuses a path that holds a file that is not a socket", "[socket]") {
    const ScratchFolder folder ("helper-file");
    const auto path = folder.path / "notes.txt";
    writeFile (path, "keep me\n");
    REQUIRE (isRegularFile (path));

    {
	MessageListener listener (path);
	CHECK_FALSE (listener.listen ());
	CHECK (listener.error () == path.string () + " exists and is not a socket; refusing to replace it");
	CHECK_FALSE (listener.isListening ());
    }

    CHECK (isRegularFile (path));
    CHECK (readFile (path) == "keep me\n");
}

TEST_CASE ("MessageListener replaces a stale socket", "[socket]") {
    const ScratchFolder folder ("helper-stale");
    const auto path = folder.path / "web.sock";
    leaveStaleSocket (path);
    REQUIRE (isSocket (path));
    REQUIRE_FALSE (answers (path));

    MessageListener listener (path);
    CHECK (listener.listen ());
    CHECK (listener.error ().empty ());
    CHECK (answers (path));
}

TEST_CASE ("MessageListener refuses a socket that another listener answers", "[socket]") {
    const ScratchFolder folder ("helper-live");
    const auto path = folder.path / "web.sock";

    MessageListener first (path);
    REQUIRE (first.listen ());

    MessageListener second (path);
    CHECK_FALSE (second.listen ());
    CHECK (second.error () == "another web helper is already listening on " + path.string ());
    CHECK (answers (path));
}

TEST_CASE ("MessageListener keeps the mode of a folder it did not create", "[socket]") {
    const ScratchFolder folder ("helper-folder");
    std::filesystem::permissions (folder.path, std::filesystem::perms (0755), std::filesystem::perm_options::replace);
    REQUIRE (folderMode (folder.path) == 0755);

    MessageListener listener (folder.path / "web.sock");
    REQUIRE (listener.listen ());
    CHECK (folderMode (folder.path) == 0755);
}
