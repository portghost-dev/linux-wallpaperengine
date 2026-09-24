#include <catch2/catch_test_macros.hpp>

#include <fstream>
#include <string>

TEST_CASE ("the build's version stamp is the first line of VERSION", "[version]") {
    std::ifstream file (LWE_SOURCE_DIR "/VERSION");
    REQUIRE (file.is_open ());

    std::string first;
    std::getline (file, first);

    if (first.starts_with ("\xEF\xBB\xBF")) {
	first.erase (0, 3);
    }

    const std::string whitespace = " \t\n\v\f\r";
    first.erase (0, first.find_first_not_of (whitespace));
    first.erase (first.find_last_not_of (whitespace) + 1);

    CHECK (std::string (LWE_VERSION) == first);
}
