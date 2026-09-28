#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cstring>
#include <elf.h>
#include <filesystem>
#include <fstream>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace {
bool safeRunPath (const std::string_view runPath) {
    for (std::string_view::size_type start = 0;;) {
	const auto colon = runPath.find (':', start);
	const auto entry
	    = runPath.substr (start, colon == std::string_view::npos ? std::string_view::npos : colon - start);

	if (!entry.starts_with ('/') && !entry.starts_with ("$ORIGIN")) {
	    return false;
	}

	if (colon == std::string_view::npos) {
	    return true;
	}

	start = colon + 1;
    }
}

std::optional<std::string> runPathOf (const std::filesystem::path& file) {
    std::ifstream in (file, std::ios::binary);
    REQUIRE (in.is_open ());

    Elf64_Ehdr header {};
    in.read (reinterpret_cast<char*> (&header), sizeof (header));
    REQUIRE (in.good ());
    REQUIRE (std::memcmp (header.e_ident, ELFMAG, SELFMAG) == 0);
    REQUIRE (header.e_ident[EI_CLASS] == ELFCLASS64);
    REQUIRE (header.e_phentsize == sizeof (Elf64_Phdr));

    std::vector<Elf64_Phdr> segments (header.e_phnum);
    in.seekg (static_cast<std::streamoff> (header.e_phoff));
    in.read (
	reinterpret_cast<char*> (segments.data ()),
	static_cast<std::streamsize> (segments.size () * sizeof (Elf64_Phdr))
    );
    REQUIRE (in.good ());

    const auto dynamic
	= std::ranges::find_if (segments, [] (const Elf64_Phdr& segment) { return segment.p_type == PT_DYNAMIC; });
    REQUIRE (dynamic != segments.end ());

    std::vector<Elf64_Dyn> entries (dynamic->p_filesz / sizeof (Elf64_Dyn));
    in.seekg (static_cast<std::streamoff> (dynamic->p_offset));
    in.read (
	reinterpret_cast<char*> (entries.data ()), static_cast<std::streamsize> (entries.size () * sizeof (Elf64_Dyn))
    );
    REQUIRE (in.good ());

    Elf64_Addr strtab = 0;
    std::optional<Elf64_Xword> runPath;
    std::optional<Elf64_Xword> rPath;

    for (const auto& entry : entries) {
	if (entry.d_tag == DT_NULL) {
	    break;
	}

	if (entry.d_tag == DT_STRTAB) {
	    strtab = entry.d_un.d_ptr;
	} else if (entry.d_tag == DT_RUNPATH) {
	    runPath = entry.d_un.d_val;
	} else if (entry.d_tag == DT_RPATH) {
	    rPath = entry.d_un.d_val;
	}
    }

    const auto offset = runPath.has_value () ? runPath : rPath;

    if (!offset.has_value ()) {
	return std::nullopt;
    }

    const auto load = std::ranges::find_if (segments, [strtab] (const Elf64_Phdr& segment) {
	return segment.p_type == PT_LOAD && strtab >= segment.p_vaddr && strtab < segment.p_vaddr + segment.p_filesz;
    });
    REQUIRE (load != segments.end ());

    in.seekg (static_cast<std::streamoff> (strtab - load->p_vaddr + load->p_offset + *offset));
    std::string value;
    std::getline (in, value, '\0');
    REQUIRE (in.good ());
    return value;
}
} // namespace

TEST_CASE ("a run path passes only with absolute folders and $ORIGIN", "[runpath]") {
    CHECK (safeRunPath ("$ORIGIN:$ORIGIN/lib:$ORIGIN/lib64"));
    CHECK_FALSE (safeRunPath ("/build/output:"));
    CHECK_FALSE (safeRunPath ("."));
    CHECK_FALSE (safeRunPath ("lib"));
    CHECK_FALSE (safeRunPath (":$ORIGIN"));
}

TEST_CASE ("the engine and its library look for libraries only in absolute folders and $ORIGIN", "[runpath]") {
    const auto folder = std::filesystem::read_symlink ("/proc/self/exe").parent_path ();

    for (const char* name : { "linux-wallpaperengine", "liblinux-wallpaperengine-lib.so" }) {
	const auto file = folder / name;

	if (!std::filesystem::exists (file)) {
	    FAIL ("missing: " << file.string ());
	}

	const auto runPath = runPathOf (file);
	INFO (file.string () << " run path: " << runPath.value_or ("(none)"));
	REQUIRE (runPath.has_value ());
	CHECK (safeRunPath (*runPath));
    }
}
