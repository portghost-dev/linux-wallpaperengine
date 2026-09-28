#include <catch2/catch_test_macros.hpp>

#include <memory>
#include <numeric>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/Render/Wallpapers/CScene.h"

using namespace WallpaperEngine::Render::Wallpapers;

namespace {
ObjectList sceneOf (const std::vector<std::pair<int, std::vector<int>>>& graph) {
    ObjectList objects;

    for (const auto& [id, dependencies] : graph) {
	objects.push_back (std::make_unique<Object> (ObjectData { .id = id, .dependencies = dependencies }));
    }

    return objects;
}

bool everything (int) { return true; }

size_t linesWith (const std::string& lines, const std::string& text) {
    size_t found = 0;
    std::istringstream in (lines);

    for (std::string line; std::getline (in, line);) {
	if (line.find (text) != std::string::npos) {
	    found++;
	}
    }

    return found;
}
} // namespace

TEST_CASE ("a dependency cycle is broken, each object placed once and the cycle logged once", "[renderorder]") {
    auto* errors = new std::ostringstream ();
    sLog.addError (errors);

    const auto pair = sceneOf ({ { 10, { 11 } }, { 11, { 10 } } });
    const auto triple = sceneOf ({ { 1, { 2 } }, { 2, { 3 } }, { 3, { 1 } } });
    RenderOrder pairOrder;
    RenderOrder tripleOrder;

    REQUIRE_NOTHROW (pairOrder = renderOrder (pair, everything));
    REQUIRE_NOTHROW (tripleOrder = renderOrder (triple, everything));

    const std::string lines = errors->str ();
    errors->setstate (std::ios::badbit);

    CHECK (pairOrder.ids == std::vector<int> { 11, 10 });
    CHECK (tripleOrder.ids == std::vector<int> { 3, 2, 1 });
    CHECK (pairOrder.steps <= 2 + 2);
    CHECK (tripleOrder.steps <= 3 + 3);
    CHECK (linesWith (lines, "Dependency cycle among scene objects") == 2);
    CHECK (linesWith (lines, "Dependency cycle among scene objects 10, 11, 10:") == 1);
    CHECK (linesWith (lines, "Dependency cycle among scene objects 1, 2, 3, 1:") == 1);
}

TEST_CASE ("a 30-level diamond orders each object once in linear steps", "[renderorder]") {
    std::vector<std::pair<int, std::vector<int>>> graph;
    size_t edges = 0;

    for (int level = 30; level >= 1; level--) {
	std::vector<int> dependencies;

	if (level >= 2) {
	    dependencies.push_back (level - 1);
	}

	if (level >= 3) {
	    dependencies.push_back (level - 2);
	}

	edges += dependencies.size ();
	graph.emplace_back (level, dependencies);
    }

    const auto objects = sceneOf (graph);
    const auto order = renderOrder (objects, everything);

    std::vector<int> expected (30);
    std::iota (expected.begin (), expected.end (), 1);

    CHECK (order.ids == expected);
    CHECK (order.steps <= objects.size () + edges);
}

TEST_CASE ("an acyclic scene keeps its render order", "[renderorder]") {
    const auto objects = sceneOf (
	{
	    { 5, { 3 } },
	    { 3, { 3, 9 } },
	    { 7, { 8 } },
	    { 8, { 6 } },
	    { 6, {} },
	    { 5, { 4 } },
	    { 2, {} },
	    { 4, {} },
	}
    );
    const auto order = renderOrder (objects, [] (const int id) { return id != 8; });

    CHECK (order.ids == std::vector<int> { 3, 5, 7, 6, 4, 2 });
}
