#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <functional>
#include <memory>
#include <numeric>
#include <optional>
#include <sstream>
#include <string>
#include <unordered_set>
#include <vector>

#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/Render/Wallpapers/CScene.h"

using namespace WallpaperEngine::Render::Wallpapers;

namespace {
struct Spec {
    int id;
    std::vector<int> dependencies;
    std::optional<int> parent;
    std::string name;
};

ObjectList sceneOf (const std::vector<Spec>& specs) {
    ObjectList objects;

    for (const auto& spec : specs) {
	objects.push_back (
	    std::make_unique<Object> (ObjectData {
		.id = spec.id, .name = spec.name, .dependencies = spec.dependencies, .parent = spec.parent })
	);
    }

    return objects;
}

struct Creation {
    std::vector<const Object*> attempts;
    std::vector<const Object*> created;
    size_t steps = 0;
};

Creation createAll (const ObjectList& objects, const std::function<bool (const Object&)>& succeeds) {
    Creation result;
    std::unordered_set<int> createdIds;
    CreationWalk walk (
	objects, [&createdIds] (const int id) { return createdIds.contains (id); },
	[&] (const Object& object) {
	    result.attempts.push_back (&object);

	    if (succeeds (object)) {
		createdIds.insert (object.id);
		result.created.push_back (&object);
	    }
	}
    );

    for (const auto& object : objects) {
	walk.visit (*object);
    }

    result.steps = walk.steps ();
    return result;
}

std::vector<int> idsOf (const std::vector<const Object*>& objects) {
    std::vector<int> ids;

    for (const auto* object : objects) {
	ids.push_back (object->id);
    }

    return ids;
}

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

TEST_CASE ("a 20000-deep chain walks with no stack overflow", "[scenewalk]") {
    constexpr int depth = 20000;
    std::vector<Spec> chain;
    std::vector<Spec> parents;

    for (int id = depth; id >= 1; id--) {
	chain.push_back ({ .id = id, .dependencies = id > 1 ? std::vector<int> { id - 1 } : std::vector<int> {} });
	parents.push_back ({ .id = id, .parent = id > 1 ? std::optional<int> (id - 1) : std::nullopt });
    }

    const auto chainScene = sceneOf (chain);
    const auto parentScene = sceneOf (parents);
    const auto everything = [] (const Object&) { return true; };
    std::vector<int> expected (depth);
    std::iota (expected.begin (), expected.end (), 1);

    const auto byDependency = createAll (chainScene, everything);
    CHECK (idsOf (byDependency.created) == expected);
    CHECK (byDependency.steps <= 2 * depth - 1);

    const auto byParent = createAll (parentScene, everything);
    CHECK (idsOf (byParent.created) == expected);
    CHECK (byParent.steps <= 2 * depth - 1);

    const auto order = renderOrder (chainScene, [] (int) { return true; });
    CHECK (order.ids == expected);
    CHECK (order.steps <= 2 * depth - 1);
}

TEST_CASE (
    "objects that fail to create are not walked again, so a failed diamond walks in linear steps", "[scenewalk]"
) {
    constexpr int size = 30;
    std::vector<Spec> specs;
    size_t edges = 0;

    for (int id = size; id >= 1; id--) {
	std::vector<int> dependencies;

	if (id >= 2) {
	    dependencies.push_back (id - 1);
	}

	if (id >= 3) {
	    dependencies.push_back (id - 2);
	}

	edges += dependencies.size ();
	specs.push_back ({ .id = id, .dependencies = dependencies });
    }

    const auto scene = sceneOf (specs);
    const auto result = createAll (scene, [] (const Object&) { return false; });

    std::vector<int> attempted = idsOf (result.attempts);
    std::ranges::sort (attempted);
    std::vector<int> expected (size);
    std::iota (expected.begin (), expected.end (), 1);

    CHECK (result.attempts.size () == size);
    CHECK (attempted == expected);
    CHECK (result.steps <= scene.size () + edges);
}

TEST_CASE ("a chain with back edges logs each cycle once and names at most 16 ids", "[scenewalk]") {
    constexpr int size = 2000;
    std::vector<Spec> specs;

    for (int id = 1; id <= size; id++) {
	std::vector<int> dependencies;

	if (id < size) {
	    dependencies.push_back (id + 1);
	}

	if (id > 1) {
	    dependencies.push_back (1);
	}

	if (id == size) {
	    dependencies.push_back (1);
	}

	specs.push_back ({ .id = id, .dependencies = dependencies });
    }

    const auto scene = sceneOf (specs);

    auto* renderErrors = new std::ostringstream ();
    sLog.addError (renderErrors);
    const auto order = renderOrder (scene, [] (int) { return true; });
    const std::string renderLines = renderErrors->str ();
    renderErrors->setstate (std::ios::badbit);

    size_t mostIds = 0;
    std::istringstream in (renderLines);

    for (std::string line; std::getline (in, line);) {
	const auto begin = line.find ("scene objects ");
	const auto end = line.find (": render order");

	if (begin == std::string::npos || end == std::string::npos) {
	    continue;
	}

	std::string ids = line.substr (begin + 14, end - begin - 14);

	if (const auto more = ids.find (", and "); more != std::string::npos) {
	    ids.resize (more);
	}

	mostIds = std::max (mostIds, static_cast<size_t> (std::ranges::count (ids, ',')) + 1);
    }

    CHECK (linesWith (renderLines, "Dependency cycle among scene objects") == size - 1);
    CHECK (mostIds <= 16);
    CHECK (
	renderLines.find (
	    "Dependency cycle among scene objects 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, and 1985 "
	    "more: "
	    "render order ignores the dependency of 2000 on 1"
	)
	!= std::string::npos
    );
    CHECK (
	renderLines.find ("Dependency cycle among scene objects 1, 2, 1: render order ignores the dependency of 2 on 1")
	!= std::string::npos
    );
    CHECK (renderLines.size () < 300 * size);

    auto* creationErrors = new std::ostringstream ();
    sLog.addError (creationErrors);
    createAll (scene, [] (const Object&) { return true; });
    const std::string creationLines = creationErrors->str ();
    creationErrors->setstate (std::ios::badbit);

    CHECK (linesWith (creationLines, "Scene graph cycle detected") <= size);
}

TEST_CASE ("an acyclic diamond logs no dependency cycle", "[scenewalk]") {
    const auto scene = sceneOf (
	{ { .id = 1, .dependencies = { 2, 3 } },
	  { .id = 2, .dependencies = { 4 } },
	  { .id = 3, .dependencies = { 4 } },
	  { .id = 4 } }
    );

    auto* errors = new std::ostringstream ();
    sLog.addError (errors);
    const auto order = renderOrder (scene, [] (int) { return true; });
    const std::string lines = errors->str ();
    errors->setstate (std::ios::badbit);

    CHECK (order.ids == std::vector<int> { 4, 2, 3, 1 });
    CHECK (linesWith (lines, "Dependency cycle") == 0);
}

TEST_CASE ("a dependency on a duplicated id resolves to the first object with that id", "[scenewalk]") {
    const auto scene = sceneOf (
	{ { .id = 1, .dependencies = { 5 } },
	  { .id = 5, .dependencies = { 3 }, .name = "first" },
	  { .id = 5, .dependencies = { 4 }, .name = "second" },
	  { .id = 3 },
	  { .id = 4 } }
    );

    CHECK (renderOrder (scene, [] (int) { return true; }).ids == std::vector<int> { 3, 5, 1, 4 });

    const auto creation = createAll (scene, [] (const Object&) { return true; });
    CHECK (idsOf (creation.created) == std::vector<int> { 3, 5, 1, 4 });
    CHECK (creation.created[1]->name == "first");
}

TEST_CASE ("creation keeps its results for an acyclic scene", "[scenewalk]") {
    const auto scene = sceneOf (
	{ { .id = 10, .dependencies = { 12, 11 }, .parent = 13 },
	  { .id = 11, .dependencies = { 11, 99 } },
	  { .id = 12, .dependencies = { 14 } },
	  { .id = 13, .dependencies = { 14 } },
	  { .id = 14 },
	  { .id = 16, .name = "first" },
	  { .id = 16, .dependencies = { 17 }, .name = "second" },
	  { .id = 17 } }
    );

    const auto result = createAll (scene, [] (const Object& object) { return object.id != 14; });

    CHECK (idsOf (result.created) == std::vector<int> { 12, 11, 13, 10, 16, 17 });
    CHECK (result.created[4]->name == "first");
    CHECK (std::ranges::count (idsOf (result.attempts), 14) == 1);

    const auto orphan = sceneOf ({ { .id = 1, .parent = 2 } });
    CreationWalk walk (orphan, [] (int) { return false; }, [] (const Object&) { });
    CHECK_THROWS (walk.visit (*orphan.front ()));
}
