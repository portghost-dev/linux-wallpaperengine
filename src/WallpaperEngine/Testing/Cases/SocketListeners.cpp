#include <catch2/catch_test_macros.hpp>

#include <cstring>
#include <filesystem>
#include <fstream>
#include <optional>
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

enum class Entry { Directory, Fifo, LinkToFile, LinkToSocket };

const char* entryName (const Entry kind) {
    switch (kind) {
	case Entry::Directory:
	    return "a directory";
	case Entry::Fifo:
	    return "a FIFO";
	case Entry::LinkToFile:
	    return "a symlink to a file";
	default:
	    return "a symlink to a socket";
    }
}

void makeEntry (const Entry kind, const std::filesystem::path& folder, const std::filesystem::path& path) {
    switch (kind) {
	case Entry::Directory:
	    std::filesystem::create_directory (path);
	    break;
	case Entry::Fifo:
	    REQUIRE (mkfifo (path.c_str (), 0600) == 0);
	    break;
	case Entry::LinkToFile:
	    writeFile (folder / "target.txt", "target text\n");
	    std::filesystem::create_symlink (folder / "target.txt", path);
	    break;
	case Entry::LinkToSocket:
	    leaveStaleSocket (folder / "target.sock");
	    std::filesystem::create_symlink (folder / "target.sock", path);
	    break;
    }
}

bool entryIntact (const Entry kind, const std::filesystem::path& folder, const std::filesystem::path& path) {
    struct stat entry {};

    if (lstat (path.c_str (), &entry) != 0) {
	return false;
    }

    switch (kind) {
	case Entry::Directory:
	    return S_ISDIR (entry.st_mode);
	case Entry::Fifo:
	    return S_ISFIFO (entry.st_mode);
	case Entry::LinkToFile:
	    return S_ISLNK (entry.st_mode) && std::filesystem::read_symlink (path) == folder / "target.txt"
		&& readFile (folder / "target.txt") == "target text\n";
	default:
	    return S_ISLNK (entry.st_mode) && std::filesystem::read_symlink (path) == folder / "target.sock"
		&& isSocket (folder / "target.sock");
    }
}

template <typename Listener> void checkRefusedAndLeft (const std::string& name) {
    for (const auto kind : { Entry::Directory, Entry::Fifo, Entry::LinkToFile, Entry::LinkToSocket }) {
	INFO (entryName (kind));
	const ScratchFolder folder (name + "-" + std::to_string (static_cast<int> (kind)));
	const auto path = folder.path / "s.sock";
	makeEntry (kind, folder.path, path);
	REQUIRE (entryIntact (kind, folder.path, path));

	{
	    Listener listener (path);
	    CHECK_FALSE (listener.listen ());
	    CHECK (listener.error () == path.string () + " exists and is not a socket; refusing to replace it");
	}

	CHECK (entryIntact (kind, folder.path, path));
    }
}

template <typename Listener> void checkSavedOverSocketSurvivesExit (const std::string& name) {
    {
	const ScratchFolder folder (name + "-file");
	const auto path = folder.path / "s.sock";

	{
	    Listener listener (path);
	    REQUIRE (listener.listen ());
	    REQUIRE (isSocket (path));
	    writeFile (folder.path / "saved.tmp", "saved text\n");
	    std::filesystem::rename (folder.path / "saved.tmp", path);
	}

	CHECK (isRegularFile (path));
	CHECK (readFile (path) == "saved text\n");
    }

    {
	const ScratchFolder folder (name + "-link");
	const auto path = folder.path / "s.sock";

	{
	    Listener listener (path);
	    REQUIRE (listener.listen ());
	    leaveStaleSocket (folder.path / "target.sock");
	    std::filesystem::create_symlink (folder.path / "target.sock", folder.path / "link.tmp");
	    std::filesystem::rename (folder.path / "link.tmp", path);
	}

	CHECK (entryIntact (Entry::LinkToSocket, folder.path, path));
    }
}

template <typename Listener> void checkOwnSocketRemovedAtExit (const std::string& name) {
    const ScratchFolder folder (name);
    const auto path = folder.path / "s.sock";

    {
	Listener listener (path);
	REQUIRE (listener.listen ());
	REQUIRE (isSocket (path));
    }

    CHECK_FALSE (std::filesystem::exists (std::filesystem::symlink_status (path)));
}

template <typename Listener> void checkReboundSocketLeftAtExit (const std::string& name) {
    const ScratchFolder folder (name);
    const auto path = folder.path / "s.sock";
    std::optional<Listener> first;
    first.emplace (path);
    REQUIRE (first->listen ());
    REQUIRE (unlink (path.c_str ()) == 0);

    Listener second (path);
    REQUIRE (second.listen ());
    first.reset ();

    CHECK (isSocket (path));
    CHECK (answers (path));
}

template <typename Listener> void checkRenamedSocketLeftAtExit (const std::string& name) {
    const ScratchFolder folder (name);
    const auto path = folder.path / "s.sock";
    std::optional<Listener> first;
    first.emplace (path);
    REQUIRE (first->listen ());

    Listener second (folder.path / "other.sock");
    REQUIRE (second.listen ());
    std::filesystem::rename (folder.path / "other.sock", path);
    first.reset ();

    CHECK (isSocket (path));
    CHECK (answers (path));
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

TEST_CASE ("MessageListener sets 0700 on a folder it creates", "[socket]") {
    const ScratchFolder folder ("helper-created");
    const auto created = folder.path / "created";
    const mode_t previous = umask (022);
    MessageListener listener (created / "web.sock");
    const bool listening = listener.listen ();
    umask (previous);

    REQUIRE (listening);
    CHECK (folderMode (created) == 0700);
}

TEST_CASE ("CommandServer refuses a directory, a FIFO or a symlink at its path and leaves it", "[socket]") {
    checkRefusedAndLeft<CommandServer> ("engine-kind");
}

TEST_CASE ("MessageListener refuses a directory, a FIFO or a symlink at its path and leaves it", "[socket]") {
    checkRefusedAndLeft<MessageListener> ("helper-kind");
}

TEST_CASE ("CommandServer leaves a file or a symlink saved over its socket at exit", "[socket]") {
    checkSavedOverSocketSurvivesExit<CommandServer> ("engine-exit");
}

TEST_CASE ("MessageListener leaves a file or a symlink saved over its socket at exit", "[socket]") {
    checkSavedOverSocketSurvivesExit<MessageListener> ("helper-exit");
}

TEST_CASE ("CommandServer removes its own socket at exit", "[socket]") {
    checkOwnSocketRemovedAtExit<CommandServer> ("engine-own");
}

TEST_CASE ("MessageListener removes its own socket at exit", "[socket]") {
    checkOwnSocketRemovedAtExit<MessageListener> ("helper-own");
}

TEST_CASE ("CommandServer leaves another listener's socket bound at its path after an unlink", "[socket]") {
    checkReboundSocketLeftAtExit<CommandServer> ("engine-rebound");
}

TEST_CASE ("MessageListener leaves another listener's socket bound at its path after an unlink", "[socket]") {
    checkReboundSocketLeftAtExit<MessageListener> ("helper-rebound");
}

TEST_CASE ("CommandServer leaves another listener's socket renamed over its path", "[socket]") {
    checkRenamedSocketLeftAtExit<CommandServer> ("engine-renamed");
}

TEST_CASE ("MessageListener leaves another listener's socket renamed over its path", "[socket]") {
    checkRenamedSocketLeftAtExit<MessageListener> ("helper-renamed");
}
