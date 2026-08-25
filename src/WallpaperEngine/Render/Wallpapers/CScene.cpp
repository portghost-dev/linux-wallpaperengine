#include "WallpaperEngine/Render/FBOProvider.h"
#include "WallpaperEngine/Render/Objects/CImage.h"
#include "WallpaperEngine/Render/Objects/CLight.h"
#include "WallpaperEngine/Render/Objects/CModel.h"
#include "WallpaperEngine/Render/Objects/CParticle.h"
#include "WallpaperEngine/Render/Objects/CSound.h"
#include "WallpaperEngine/Render/Objects/CText.h"

#include "WallpaperEngine/Render/WallpaperState.h"

#include "CScene.h"
#include "WallpaperEngine/Logging/Log.h"

#include "WallpaperEngine/Data/Model/Wallpaper.h"
#include "WallpaperEngine/Data/Parsers/ObjectParser.h"
#include "WallpaperEngine/Render/Drivers/Output/OutputViewport.h"
#include "WallpaperEngine/Render/MipResidency.h"

#include <algorithm>
#include <cstdlib>
#include <ranges>

extern float g_Time;
extern float g_TimeLast;

namespace {
float lwe_ssfactor () {
    static const float f = [] () {
	const char* e = getenv ("LWE_SSFACTOR");
	return e && *e ? static_cast<float> (atof (e)) : 1.0f;
    }();
    return f;
}
} // namespace

using namespace WallpaperEngine;
using namespace WallpaperEngine::Render;
using namespace WallpaperEngine::Data::Model;
using namespace WallpaperEngine::Data::Parsers;
using namespace WallpaperEngine::Render::Wallpapers;

CScene::CScene (
    const Wallpaper& wallpaper, RenderContext& context, AudioContext& audioContext,
    const WallpaperState::TextureUVsScaling& scalingMode, const uint32_t& clampMode
) : CWallpaper (wallpaper, context, audioContext, scalingMode, clampMode) {
    // caller should check this, if not a std::bad_cast is good to throw
    auto scene = wallpaper.as<Scene> ();

    // mip residency: decide per-texture cappability from the data model BEFORE any
    // texture resolves (the cache is shared, so eligibility cannot be first-caller-
    // decides). Inert when LWE_TEXDETAIL=full.
    MipResidency::buildReferenceMap (*scene);

    // setup scripting engine
    this->m_scriptEngine = std::make_unique<Scripting::ScriptEngine> (*this, context.getMediaSource ());
    // setup the scene camera
    this->m_camera = std::make_unique<Camera> (*this, scene->camera);
    this->updateFogState ();

    float width = scene->camera.projection.width;
    float height = scene->camera.projection.height;

    if (scene->camera.projection.isAuto || scene->camera.projection.isPerspective) {
	glm::vec2 maxExtent = { 0.0f, 0.0f };

	for (const auto& object : scene->objects) {
	    if (!object->is<Image> ()) {
		continue;
	    }

	    const auto* image = object->as<Image> ();
	    if (!image->origin || !image->origin->value) {
		continue;
	    }

	    const glm::vec3 origin = image->origin->value->getVec3 ();
	    const glm::vec2 halfSize = image->size / 2.0f;

	    maxExtent.x = glm::max (maxExtent.x, glm::abs (origin.x) + halfSize.x);
	    maxExtent.y = glm::max (maxExtent.y, glm::abs (origin.y) + halfSize.y);
	}

	if (maxExtent.x > 0.0f && maxExtent.y > 0.0f) {
	    width = maxExtent.x * 2.0f;
	    height = maxExtent.y * 2.0f;
	} else {
	    width = this->getContext ().getOutput ().getFullWidth ();
	    height = this->getContext ().getOutput ().getFullHeight ();
	    sLog.debug ("Auto projection: falling back to screen resolution ", width, "x", height);
	}
    }

    this->m_parallaxDisplacement = { 0, 0 };

    // CANVAS dims (authored layout space) vs camera VIEW dims (canvas / general.zoom)
    // are distinct spaces: layout, FBO sizing and the present pass live in canvas space;
    // only projection construction consumes the zoomed view extent. Coupling them made
    // zoom resize the scene FBO (zoom 0.5 = 4x VRAM; deep zoom-out = GL size bomb).
    this->m_canvasWidth = static_cast<int> (width);
    this->m_canvasHeight = static_cast<int> (height);

    // TODO: CONVERSION
    if (scene->camera.projection.isPerspective) {
	this->m_camera->setPerspectiveProjection (width, height);
    } else {
	this->m_camera->setOrthogonalProjection (width, height);
    }

    sLog.error (
	"LWE-DEBUG isAuto=", scene->camera.projection.isAuto, " rawProj=", scene->camera.projection.width, "x",
	scene->camera.projection.height, " usedProj=", width, "x", height,
	" output=", this->getContext ().getOutput ().getFullWidth (), "x",
	this->getContext ().getOutput ().getFullHeight ()
    );

    const bool hdrLadder = scene->camera.bloom.enabled->value->getBool () && scene->camera.bloom.hdr;
    if (hdrLadder) {
	this->m_sceneFormat = TextureFormat_RGBA16161616f;
	this->m_hdrBloom = true;
    }

    // setup framebuffers here as they're required for the scene setup
    this->setupFramebuffers ();

    const auto sceneWidth = static_cast<uint32_t> (this->m_canvasWidth);
    const auto sceneHeight = static_cast<uint32_t> (this->m_canvasHeight);

    this->_rt_shadowAtlas
	= this->create ("_rt_shadowAtlas", TextureFormat_ARGB8888, TextureFlags_ClampUVs, 1.0, { 1, 1 }, { 1, 1 });
    this->alias ("_alias_lightCookie", "_rt_shadowAtlas");

    // set clear color
    const glm::vec3 clearColor = scene->colors.clear->value->getVec3 ();

    glClearColor (clearColor.r, clearColor.g, clearColor.b, 1.0f);

    if (getenv ("LWE_CLEARPROBE") != nullptr) {
	GLfloat cc[4] = {};
	glGetFloatv (GL_COLOR_CLEAR_VALUE, cc);
	sLog.out (
	    "LWE-CLEARPROBE ctor scene=", static_cast<const void*> (this), " clearColor=", cc[0], ",", cc[1], ",",
	    cc[2], ",", cc[3]
	);
    }

    this->collectSharedComposites (*scene);

    static const bool skipGate = [] () {
	const char* e = getenv ("LWE_SKIPGATE");
	return e == nullptr || std::string (e) != "0";
    }();
    const auto& skipObjs = this->getContext ().getApp ().getContext ().settings.render.debug.skipObjects;
    for (const auto& object : scene->objects) {
	if (skipGate && !skipObjs.empty () && std::ranges::find (skipObjs, object->id) != skipObjs.end ()) {
	    continue;
	}
	this->createObject (*object);
    }

    // copy over objects by render order
    for (const auto& object : scene->objects) {
	this->addObjectToRenderOrder (*object);
    }

    const float bloomBaseW = static_cast<float> (this->m_sceneFBO->getRealWidth ());
    const float bloomBaseH = static_cast<float> (this->m_sceneFBO->getRealHeight ());
    this->_rt_4FrameBuffer = this->create (
	"_rt_4FrameBuffer", TextureFormat_ARGB8888, TextureFlags_ClampUVs, 1.0, { bloomBaseW / 4, bloomBaseH / 4 },
	{ bloomBaseW / 4, bloomBaseH / 4 }
    );
    this->_rt_8FrameBuffer = this->create (
	"_rt_8FrameBuffer", TextureFormat_ARGB8888, TextureFlags_ClampUVs, 1.0, { bloomBaseW / 8, bloomBaseH / 8 },
	{ bloomBaseW / 8, bloomBaseH / 8 }
    );
    this->_rt_Bloom = this->create (
	"_rt_Bloom", TextureFormat_ARGB8888, TextureFlags_ClampUVs, 1.0, { bloomBaseW / 8, bloomBaseH / 8 },
	{ bloomBaseW / 8, bloomBaseH / 8 }
    );

    //
    // Had to get a little creative with the effects to achieve the same bloom effect without any custom code
    // this custom image loads some effect files from the virtual container to achieve the same bloom effect
    // this approach requires of two extra draw calls due to the way the effect works in official WPE
    // (it renders directly to the screen, whereas here we never do that from a scene)
    //

    const auto bloomOrigin = glm::vec3 { sceneWidth / 2, sceneHeight / 2, 0.0f };
    const auto bloomSize = glm::vec2 { sceneWidth, sceneHeight };

    const auto bloomTintVec = this->getScene ().camera.bloom.tint->value->getVec3 ();
    const std::string bloomTint = std::to_string (bloomTintVec.x) + " " + std::to_string (bloomTintVec.y) + " "
	+ std::to_string (bloomTintVec.z);

    const JSON bloom
	= { { "image", "models/wpenginelinux.json" },
	    { "name", "bloomimagewpenginelinux" },
	    { "visible", true },
	    { "scale", "1.0 1.0 1.0" },
	    { "angles", "0.0 0.0 0.0" },
	    { "origin",
	      std::to_string (bloomOrigin.x) + " " + std::to_string (bloomOrigin.y) + " "
		  + std::to_string (bloomOrigin.z) },
	    { "size", std::to_string (bloomSize.x) + " " + std::to_string (bloomSize.y) },
	    { "id", -1 },
	    { "effects",
	      JSON::array (
		  { { { "file", "effects/wpenginelinux/bloomeffect.json" },
		      { "id", 15242000 },
		      { "name", "" },
		      { "passes",
			JSON::array (
			    { { { "constantshadervalues",
				  { { "bloomstrength", this->getScene ().camera.bloom.strength->value->getFloat () },
				    { "bloomtint", bloomTint },
				    { "bloomthreshold",
				      this->getScene ().camera.bloom.threshold->value->getFloat () } } } },
			      { { "constantshadervalues",
				  { { "bloomstrength", this->getScene ().camera.bloom.strength->value->getFloat () },
				    { "bloomtint", bloomTint },
				    { "bloomthreshold",
				      this->getScene ().camera.bloom.threshold->value->getFloat () } } } },
			      { { "constantshadervalues",
				  { { "bloomstrength", this->getScene ().camera.bloom.strength->value->getFloat () },
				    { "bloomtint", bloomTint },
				    { "bloomthreshold",
				      this->getScene ().camera.bloom.threshold->value->getFloat () } } } } }
			) } } }
	      ) } };

    JSON hdrObject;
    if (hdrLadder) {
	const int iterations = std::clamp (scene->camera.bloom.hdrIterations, 1, 12);
	const float scatter = scene->camera.bloom.hdrScatter;
	const float feather = scene->camera.bloom.hdrFeather;
	const float threshold = scene->camera.bloom.hdrThreshold;
	const float authored = scene->camera.bloom.hdrStrength->value->getFloat ();
	const float strength = authored / (1.0f + std::pow (scatter, static_cast<float> (iterations - 2)));
	const float knee = threshold * feather;
	char buf[128];
	snprintf (
	    buf, sizeof (buf), "%.9g %.9g %.9g %.9g", threshold, threshold - knee, 2.0f * knee,
	    0.25f / std::max (knee, 1e-5f)
	);
	const std::string blendParams = buf;
	const auto offsets = [] (const float srcW, const float srcH) -> std::string {
	    char local[128];
	    snprintf (
		local, sizeof (local), "%.9g %.9g %.9g %.9g", 1.0f / srcW, 1.0f / srcH, -1.0f / srcW, -1.0f / srcH
	    );
	    return local;
	};
	const auto levelName = [] (const int level) { return "_rt_hdrBloom_" + std::to_string (level); };

	JSON effectPasses = JSON::array ();
	JSON instancePasses = JSON::array ();
	const glm::ivec2 screen = this->largestOutputSize ();
	const auto outputW = static_cast<uint32_t> (screen.x);
	const auto outputH = static_cast<uint32_t> (screen.y);
	const auto canvasW = static_cast<float> (outputW);
	const auto canvasH = static_cast<float> (outputH);

	for (int k = 1; k <= iterations; k++) {
	    const uint32_t w = std::max (1u, outputW >> k);
	    const uint32_t h = std::max (1u, outputH >> k);
	    this->create (levelName (k), TextureFormat_RGBA16161616f, TextureFlags_ClampUVs, 1.0, { w, h }, { w, h });

	    effectPasses.push_back (
		{ { "material", k == 1 ? "materials/wpelinux/hdr_prefilter.json" : "materials/wpelinux/hdr_down.json" },
		  { "target", levelName (k) },
		  { "bind",
		    JSON::array (
			{ { { "name", k == 1 ? std::string ("_rt_FullFrameBuffer") : levelName (k - 1) },
			    { "index", 0 } } }
		    ) } }
	    );
	    JSON constants
		= { { "rendervar0", offsets (canvasW / std::pow (2.0f, k - 1), canvasH / std::pow (2.0f, k - 1)) } };
	    if (k == 1) {
		constants["blend"] = blendParams;
		constants["bloomstrength"] = strength;
		constants["bloomtint"] = bloomTint;
	    }
	    instancePasses.push_back ({ { "constantshadervalues", constants } });
	}
	for (int j = iterations - 1; j >= 1; j--) {
	    effectPasses.push_back (
		{ { "material", "materials/wpelinux/hdr_up.json" },
		  { "target", levelName (j) },
		  { "bind", JSON::array ({ { { "name", levelName (j + 1) }, { "index", 0 } } }) } }
	    );
	    instancePasses.push_back (
		{ { "constantshadervalues",
		    { { "scatter", scatter },
		      { "rendervar0", offsets (canvasW / std::pow (2.0f, j), canvasH / std::pow (2.0f, j)) } } } }
	    );
	}
	effectPasses.push_back (
	    { { "material", "materials/wpelinux/hdr_combine.json" },
	      { "target", "_rt_FullFrameBuffer" },
	      { "bind",
		JSON::array (
		    { { { "name", "_rt_imageLayerComposite_-1_a" }, { "index", 0 } },
		      { { "name", levelName (1) }, { "index", 1 } } }
		) } }
	);
	{
	    char texel[64];
	    snprintf (texel, sizeof (texel), "%.9g %.9g", 1.0f / canvasW, 1.0f / canvasH);
	    instancePasses.push_back ({ { "constantshadervalues", { { "texelsize", std::string (texel) } } } });
	}

	scene->project.assetLocator->getVFS ().add (
	    "effects/wpenginelinux/hdrbloomeffect.json",
	    { { "name", "camerahdrbloom_wpengine_linux" },
	      { "group", "wpengine_linux_camera" },
	      { "dependencies", JSON::array () },
	      { "passes", effectPasses } }
	);

	hdrObject = { { "image", "models/wpenginelinux.json" },
		      { "name", "hdrbloomimagewpenginelinux" },
		      { "visible", true },
		      { "scale", "1.0 1.0 1.0" },
		      { "angles", "0.0 0.0 0.0" },
		      { "origin",
			std::to_string (bloomOrigin.x) + " " + std::to_string (bloomOrigin.y) + " "
			    + std::to_string (bloomOrigin.z) },
		      { "size", std::to_string (bloomSize.x) + " " + std::to_string (bloomSize.y) },
		      { "id", -1 },
		      { "effects",
			JSON::array (
			    { { { "file", "effects/wpenginelinux/hdrbloomeffect.json" },
				{ "id", 15242001 },
				{ "name", "" },
				{ "passes", instancePasses } } }
			) } };
    }

    static const bool s_noBloom = getenv ("LWE_NOBLOOM") != nullptr;
    if (!s_noBloom && scene->camera.bloom.enabled->value->getBool ()) {
	this->m_sharedCompositeIds.insert (-1);
	this->m_bloomObjectData = ObjectParser::parse (hdrLadder ? hdrObject : bloom, scene->project);
	this->m_bloomObject = this->createObject (*this->m_bloomObjectData);

	if (this->m_bloomObject != nullptr) {
	    this->m_objectsByRenderOrder.push_back (this->m_bloomObject);
	} else {
	    sLog.error ("Bloom object failed to set up - rendering scene WITHOUT bloom");
	}
    }

    this->reportPoolHighWater ();
}

CScene::~CScene () {
    // bloom object is in the objects list, so no need to explicitly delete it
    this->m_bloomObject = nullptr;

    for (const auto& val : this->m_objects | std::views::values) {
	delete val;
    }

    this->m_objectsByRenderOrder.clear ();
    this->m_objects.clear ();
}

void CScene::queueAnimation (DynamicValue& value, CObject& object) {
    const auto& animation = value.getAnimation ();

    if (!animation.has_value () || animation->mode == AnimationMode::Unknown) {
	return;
    }

    this->m_animatedProperties.push_back (AnimatedPropertyEntry { .value = &value, .object = &object });
    sLog.out (
	"LWE-TIMELINE registered obj=", object.getId (), " mode=", static_cast<int> (animation->mode),
	" channels=", animation->channels.size (), " len=", animation->length, "f fps=", animation->fps,
	" relative=", animation->relative ? 1 : 0
    );
}

void CScene::tickAnimations () {
    if (this->m_animatedProperties.empty ()) {
	return;
    }

    const auto delta = static_cast<double> (g_Time - g_TimeLast);

    for (auto& entry : this->m_animatedProperties) {
	if (entry.completed) {
	    continue;
	}

	entry.elapsedTime += delta;

	const auto& animation = entry.value->getAnimation ();

	if (!animation.has_value ()) {
	    entry.completed = true;
	    continue;
	}

	double sampleTime = entry.elapsedTime;
	const double duration = animation->durationSeconds ();
	if (duration > 0.0) {
	    if (animation->mode == AnimationMode::Loop) {
		sampleTime = std::fmod (sampleTime, duration);
	    } else if (animation->mode == AnimationMode::Mirror) {
		const double folded = std::fmod (sampleTime, duration * 2.0);
		sampleTime = folded <= duration ? folded : duration * 2.0 - folded;
	    }
	}

	const glm::vec4 result = animation->evaluate (sampleTime);

	static const bool s_tickTrace = getenv ("LWE_LIGHTDUMP") != nullptr;
	if (s_tickTrace && &entry == &this->m_animatedProperties.front ()) {
	    static int s_tickCount = 0;
	    if (++s_tickCount % 60 == 0) {
		sLog.out (
		    "LWE-TIMELINE tick obj=", entry.object->getId (), " elapsed=", entry.elapsedTime,
		    " sample=", sampleTime, " result=(", result.x, ",", result.y, ",", result.z, ")"
		);
	    }
	}

	switch (animation->channels.size ()) {
	    case 1:
		entry.value->update (result.x, DynamicValue::UpdateSource::Animation);
		break;
	    case 2:
		entry.value->update (glm::vec2 (result), DynamicValue::UpdateSource::Animation);
		break;
	    case 3:
		entry.value->update (glm::vec3 (result), DynamicValue::UpdateSource::Animation);
		break;
	    default:
		entry.value->update (result, DynamicValue::UpdateSource::Animation);
		break;
	}

	if (animation->mode == AnimationMode::Single && entry.elapsedTime >= animation->durationSeconds ()) {
	    entry.completed = true;
	}
    }
}

void CScene::forgetObjectAnimations (const CObject& object) {
    std::erase_if (this->m_animatedProperties, [&object] (const AnimatedPropertyEntry& entry) {
	return entry.object == &object;
    });
}

std::shared_ptr<const CFBO> CScene::getActiveRenderTarget () const {
    return this->m_compositionRenderTarget != nullptr ? this->m_compositionRenderTarget : this->getFBO ();
}

std::shared_ptr<const CFBO> CScene::resolveRenderTarget (const std::shared_ptr<const CFBO>& requested) const {
    if (requested == this->getFBO () && this->m_compositionRenderTarget != nullptr) {
	return this->m_compositionRenderTarget;
    }
    return requested;
}

bool CScene::isRenderingToComposition () const { return this->m_compositionRenderTarget != nullptr; }

bool CScene::hasAuthoredChildren (const int parentId) const {
    return std::ranges::any_of (this->getScene ().objects, [parentId] (const auto& object) {
	return object != nullptr && object->parent.has_value () && *object->parent == parentId;
    });
}

Render::CObject* CScene::createObject (const Object& object) {
    Render::CObject* renderObject = nullptr;

    // ensure the item is not loaded already
    if (const auto current = this->m_objects.find (object.id); current != this->m_objects.end ()) {
	return current->second;
    }

    if (!this->m_objectsBeingResolved.insert (object.id).second) {
	sLog.error (
	    "Scene graph cycle detected: object ", object.id,
	    " is already being resolved (dependency/parent cycle) - skipping this edge to break the cycle"
	);
	return nullptr;
    }

    struct ResolvingGuard {
	std::unordered_set<int>& set;
	int id;
	~ResolvingGuard () { set.erase (id); }
    } resolvingGuard { this->m_objectsBeingResolved, object.id };

    // check dependencies too!
    for (const auto& cur : object.dependencies) {
	// self-dependency is a possibility...
	if (cur == object.id) {
	    continue;
	}

	const auto dep
	    = std::ranges::find_if (this->getScene ().objects, [&cur] (const auto& o) { return o->id == cur; });

	if (dep != this->getScene ().objects.end ()) {
	    this->createObject (**dep);
	}
    }

    // check if the item has any parent and also create it first
    if (object.parent.has_value ()) {
	int parentId = object.parent.value ();

	const auto dep = std::ranges::find_if (this->getScene ().objects, [&parentId] (const auto& o) {
	    return o->id == parentId;
	});

	if (dep == this->getScene ().objects.end ()) {
	    sLog.exception ("Cannot find parent ", parentId, " for object ", object.id);
	}

	this->createObject (**dep);
    }

    renderObject = this->dispatchObjectType (object);

    if (renderObject != nullptr) {
	this->m_objects.emplace (renderObject->getId (), renderObject);

	const auto queueIfAnimated = [this, renderObject] (const UserSettingUniquePtr& setting) {
	    if (setting != nullptr && setting->value != nullptr && setting->value->getAnimation ().has_value ()) {
		this->queueAnimation (*setting->value, *renderObject);
	    }
	};
	queueIfAnimated (object.origin);
	queueIfAnimated (object.groupScale);
	queueIfAnimated (object.groupAngles);
	if (object.is<Image> ()) {
	    const auto* image = object.as<Image> ();
	    queueIfAnimated (image->scale);
	    queueIfAnimated (image->angles);
	    queueIfAnimated (image->alpha);
	}
    }

    return renderObject;
}

void CScene::setupShadowStage () {
    this->m_shadowStageBuilt = true;

    if (this->m_camera->isOrthogonal ()) {
	return;
    }

    int featureCount = 0;
    int pointShadowCount = 0;
    for (const auto* light : m_lights) {
	if (!light->castShadow) {
	    continue;
	}
	if (light->lightType == "lspot" && featureCount + 1 <= MAX_SHADOW_FEATURES) {
	    m_spotFeatureByLight[light->id] = featureCount++;
	} else if (light->lightType == "ldirectional" && featureCount + 3 <= MAX_SHADOW_FEATURES) {
	    m_dirFeaturesByLight[light->id] = glm::ivec3 (featureCount, featureCount + 1, featureCount + 2);
	    featureCount += 3;
	} else if (light->lightType == "lpoint" && pointShadowCount < MAX_LIGHTS) {
	    m_pointSlotByLight[light->id] = pointShadowCount++;
	}
    }

    this->m_shadowStage.featureCount = featureCount;
    this->m_shadowStage.viewCount = featureCount + pointShadowCount * 6;

    if (this->m_shadowStage.viewCount == 0) {
	return;
    }

    // the real atlas replaces the load-time placeholder (same provider name); depth
    // sampling rides our hardware-compare depth-texture attachment
    this->_rt_shadowAtlas = this->create (
	"_rt_shadowAtlas", TextureFormat_ARGB8888, TextureFlags_ClampUVs, 1.0, { SHADOW_ATLAS_SIZE, SHADOW_ATLAS_SIZE },
	{ SHADOW_ATLAS_SIZE, SHADOW_ATLAS_SIZE }
    );
    this->_rt_shadowAtlas->ensureDepthTextureAttachment ();

    // grow-grid packer, verbatim: 2x3 point blocks first, then 1x1 feature cells
    int grid = static_cast<int> (std::ceil (std::sqrt (static_cast<float> (this->m_shadowStage.viewCount))));
    std::vector<glm::ivec2> pointBlocks (pointShadowCount, glm::ivec2 (-1));
    std::vector<glm::ivec2> featureCells (featureCount, glm::ivec2 (-1));

    while (true) {
	std::vector<bool> occupied (static_cast<size_t> (grid) * grid, false);
	const auto reserveBlock = [&occupied, grid] (const int width, const int height) -> std::optional<glm::ivec2> {
	    for (int y = 0; y <= grid - height; y++) {
		for (int x = 0; x <= grid - width; x++) {
		    bool available = true;
		    for (int row = 0; row < height && available; row++) {
			for (int column = 0; column < width; column++) {
			    available = !occupied[(y + row) * grid + x + column];
			    if (!available) {
				break;
			    }
			}
		    }
		    if (!available) {
			continue;
		    }
		    for (int row = 0; row < height; row++) {
			for (int column = 0; column < width; column++) {
			    occupied[(y + row) * grid + x + column] = true;
			}
		    }
		    return glm::ivec2 (x, y);
		}
	    }
	    return std::nullopt;
	};

	bool packed = true;
	for (int pointIndex = 0; pointIndex < pointShadowCount; pointIndex++) {
	    const auto block = reserveBlock (2, 3);
	    if (!block.has_value ()) {
		packed = false;
		break;
	    }
	    pointBlocks[pointIndex] = *block;
	}
	for (int feature = 0; packed && feature < featureCount; feature++) {
	    const auto cell = reserveBlock (1, 1);
	    if (!cell.has_value ()) {
		packed = false;
		break;
	    }
	    featureCells[feature] = *cell;
	}
	if (packed) {
	    break;
	}
	grid++;
    }

    const int tileSize = SHADOW_ATLAS_SIZE / glm::max (grid, 1);
    for (int pointIndex = 0; pointIndex < pointShadowCount; pointIndex++) {
	const glm::ivec2 block = pointBlocks[pointIndex];
	this->m_shadowStage.pointTransforms[pointIndex] = glm::vec4 (
	    static_cast<float> (block.x * tileSize) / SHADOW_ATLAS_SIZE,
	    static_cast<float> (block.y * tileSize) / SHADOW_ATLAS_SIZE,
	    static_cast<float> (tileSize * 2) / SHADOW_ATLAS_SIZE, static_cast<float> (tileSize * 3) / SHADOW_ATLAS_SIZE
	);
	for (int face = 0; face < 6; face++) {
	    this->m_shadowStage.pointViewports[pointIndex][face]
		= glm::ivec4 ((block.x + face % 2) * tileSize, (block.y + face / 2) * tileSize, tileSize, tileSize);
	}
    }
    for (int feature = 0; feature < featureCount; feature++) {
	const glm::ivec2 cell = featureCells[feature];
	const glm::ivec4 viewport (
	    cell.x * tileSize + SHADOW_ATLAS_GUARD, cell.y * tileSize + SHADOW_ATLAS_GUARD,
	    tileSize - SHADOW_ATLAS_GUARD * 2, tileSize - SHADOW_ATLAS_GUARD * 2
	);
	this->m_shadowStage.viewports[feature] = viewport;
	this->m_shadowStage.transforms[feature] = glm::vec4 (
	    static_cast<float> (viewport.x) / SHADOW_ATLAS_SIZE, static_cast<float> (viewport.y) / SHADOW_ATLAS_SIZE,
	    static_cast<float> (viewport.z) / SHADOW_ATLAS_SIZE, static_cast<float> (viewport.w) / SHADOW_ATLAS_SIZE
	);
    }

    sLog.out (
	"shadow stage: ", featureCount, " features + ", pointShadowCount, " point blocks on a ", grid, "x", grid,
	" atlas grid"
    );
}

void CScene::stageShadowMatrices () {
    if (this->m_shadowStage.viewCount == 0) {
	return;
    }

    this->m_shadowStage.enabled.fill (0.0f);
    this->m_shadowStage.pointEnabled.fill (0.0f);

    const glm::vec3 cameraEye = this->m_camera->getEye ();
    const glm::vec3 cameraCenter = this->m_camera->getCenter ();
    const glm::vec3 cameraUp = this->m_camera->getUp ();
    const float fov = this->m_camera->getFov ();
    const float aspect = static_cast<float> (this->m_camera->getWidth ())
	/ std::max (1.0f, static_cast<float> (this->m_camera->getHeight ()));

    for (const auto& sl : m_lightState) {
	if (!sl.castShadow) {
	    continue;
	}

	if (sl.type == SceneLight::Type::Spot && sl.spotShadowFeature >= 0) {
	    const int feature = sl.spotShadowFeature;
	    this->m_shadowStage.matrices[feature] = Objects::CLight::calculateSpotShadowViewProjection (
		sl.position, sl.direction, sl.outerConeDeg, sl.radius
	    );
	    this->m_shadowStage.enabled[feature] = 1.0f;
	} else if (sl.type == SceneLight::Type::Directional && sl.dirShadowFeatures.x >= 0) {
	    // three nested cascades: (0..d0), (d0..d1), (d1..d2)
	    const glm::vec3 travel = -sl.direction; // sl.direction is TO-LIGHT for directionals
	    float nearDistance = 0.01f;
	    for (int cascade = 0; cascade < 3; cascade++) {
		const int feature = sl.dirShadowFeatures[cascade];
		const float farDistance = sl.cascadeDistances[cascade];
		this->m_shadowStage.matrices[feature] = Objects::CLight::calculateDirectionalShadowViewProjection (
		    cameraEye, cameraCenter, cameraUp, fov, aspect, 1.0f, nearDistance, farDistance, travel,
		    this->m_shadowStage.viewports[feature].z
		);
		this->m_shadowStage.enabled[feature] = 1.0f;
		nearDistance = farDistance;
	    }
	} else if (sl.type == SceneLight::Type::Point && sl.pointShadowSlot >= 0) {
	    const int slot = sl.pointShadowSlot;
	    this->m_shadowStage.pointMatrices[slot]
		= Objects::CLight::calculatePointShadowViewProjections (sl.position, sl.radius);
	    this->m_shadowStage.pointProjections[slot]
		= Objects::CLight::calculatePointShadowProjectionInfo (sl.radius);
	    this->m_shadowStage.pointEnabled[slot] = 1.0f;
	}
    }
}

void CScene::renderShadowAtlas () {
    if (this->m_shadowStage.viewCount == 0 || this->_rt_shadowAtlas == nullptr) {
	return;
    }

#if !NDEBUG
    glPushDebugGroup (GL_DEBUG_SOURCE_APPLICATION, 0, -1, "Scene shadow atlas");
#endif

    glBindFramebuffer (GL_FRAMEBUFFER, this->_rt_shadowAtlas->getFramebuffer ());
    glViewport (0, 0, SHADOW_ATLAS_SIZE, SHADOW_ATLAS_SIZE);
    glColorMask (false, false, false, false);
    glDisable (GL_BLEND);
    glEnable (GL_DEPTH_TEST);
    glDepthFunc (GL_LEQUAL);
    glDepthMask (true);
    glClearDepth (1.0);
    glClear (GL_DEPTH_BUFFER_BIT);
    glEnable (GL_POLYGON_OFFSET_FILL);
    glPolygonOffset (2.0f, 4.0f);

    for (int feature = 0; feature < this->m_shadowStage.featureCount; feature++) {
	if (this->m_shadowStage.enabled[feature] < 0.5f) {
	    continue;
	}

	const glm::ivec4& viewport = this->m_shadowStage.viewports[feature];
	glViewport (viewport.x, viewport.y, viewport.z, viewport.w);

	for (auto* object : this->m_objectsByRenderOrder) {
	    if (auto* model = dynamic_cast<Objects::CModel*> (object); model != nullptr) {
		model->renderShadow (this->m_shadowStage.matrices[feature]);
	    }
	}
    }

    for (int slot = 0; slot < MAX_LIGHTS; slot++) {
	if (this->m_shadowStage.pointEnabled[slot] < 0.5f) {
	    continue;
	}

	for (int face = 0; face < 6; face++) {
	    const glm::ivec4& viewport = this->m_shadowStage.pointViewports[slot][face];
	    glViewport (viewport.x, viewport.y, viewport.z, viewport.w);
	    for (auto* object : this->m_objectsByRenderOrder) {
		if (auto* model = dynamic_cast<Objects::CModel*> (object); model != nullptr) {
		    model->renderShadow (this->m_shadowStage.pointMatrices[slot][face]);
		}
	    }
	}
    }

    glDisable (GL_POLYGON_OFFSET_FILL);
    glDisable (GL_CULL_FACE);
    glDisable (GL_DEPTH_TEST);
    glFrontFace (GL_CCW);
    glColorMask (true, true, true, true);
    glDepthMask (true);
    glUseProgram (GL_NONE);

#if !NDEBUG
    glPopDebugGroup ();
#endif
}

glm::vec4
CScene::calculateFogParams (const float start, const float end, const float startDensity, const float endDensity) {
    return glm::vec4 (start, end - start, startDensity, endDensity - startDensity);
}

void CScene::updateFogState () {
    const auto& fog = this->getScene ().fog;
    const bool perspective = this->getScene ().camera.projection.isPerspective;
    this->m_fog.distanceEnabled = perspective && fog.distance.enabled->value->getBool ();
    this->m_fog.heightEnabled = perspective && fog.height.enabled->value->getBool ();
    this->m_fog.distanceParams = calculateFogParams (
	fog.distance.start->value->getFloat (), fog.distance.end->value->getFloat (),
	fog.distance.startDensity->value->getFloat (), fog.distance.endDensity->value->getFloat ()
    );
    this->m_fog.heightParams = calculateFogParams (
	fog.height.start->value->getFloat (), fog.height.end->value->getFloat (),
	fog.height.startDensity->value->getFloat (), fog.height.endDensity->value->getFloat ()
    );
}

void CScene::updateLights () {
    if (!this->m_shadowStageBuilt) {
	this->setupShadowStage ();
    }

    m_lightState.clear ();

    const float sceneW = static_cast<float> (this->getWidth ());
    const float sceneH = static_cast<float> (this->getHeight ());

    static const int s_killLight = [] () -> int {
	const char* v = getenv ("LWE_KILLLIGHT");
	return v != nullptr ? atoi (v) : -1;
    }();

    for (const auto* light : m_lights) {
	if (light->id == s_killLight) {
	    continue;
	}
	if (!light->groupVisible->value->getBool ()) {
	    continue;
	}
	glm::vec3 origin = light->origin->value->getVec3 ();
	glm::vec3 angles = light->groupAngles->value->getVec3 ();

	std::optional<int> parentId = light->parent;
	for (int depth = 0; parentId.has_value () && depth < 8; depth++) {
	    const auto parent
		= std::ranges::find_if (this->getScene ().objects, [&] (const auto& o) { return o->id == *parentId; });
	    if (parent == this->getScene ().objects.end ()) {
		break;
	    }
	    const glm::vec3 pOrigin = (*parent)->origin->value->getVec3 ();
	    const float pAngle = (*parent)->groupAngles->value->getVec3 ().z;
	    const float c = std::cos (pAngle), s = std::sin (pAngle);
	    origin = { pOrigin.x + origin.x * c - origin.y * s, pOrigin.y + origin.x * s + origin.y * c,
		       pOrigin.z + origin.z };
	    angles.z += pAngle;
	    parentId = (*parent)->parent;
	}

	const glm::vec3 position = { origin.x - sceneW / 2.0f, sceneH / 2.0f - origin.y, origin.z };

	glm::mat4 rot (1.0f);
	rot = glm::rotate (rot, angles.z, glm::vec3 (0, 0, 1));
	rot = glm::rotate (rot, angles.y, glm::vec3 (0, 1, 0));
	rot = glm::rotate (rot, angles.x, glm::vec3 (1, 0, 0));
	glm::vec3 travel = glm::normalize (glm::vec3 (rot * glm::vec4 (1.0f, 0.0f, 0.0f, 0.0f)));
	travel.y = -travel.y;

	const glm::vec3 rgb = light->color->value->getVec3 () * light->intensity->value->getFloat ();

	SceneLight sl {};
	sl.position = position;
	// directionals: TO-LIGHT = -travel; spots/points: travel = beam axis
	sl.direction = travel;
	sl.color = rgb;
	sl.radius = light->radius;
	sl.exponent = light->exponent;
	sl.lightId = light->id;
	sl.castShadow = light->castShadow;
	sl.cascadeDistances = light->cascadeDistances;

	if (light->lightType == "ldirectional") {
	    sl.type = SceneLight::Type::Directional;
	    sl.direction = -travel;
	    if (const auto it = m_dirFeaturesByLight.find (light->id); it != m_dirFeaturesByLight.end ()) {
		sl.dirShadowFeatures = it->second;
	    }
	    m_lightState.push_back (sl);
	} else if (light->lightType == "lspot") {
	    sl.type = SceneLight::Type::Spot;
	    sl.innerConeDeg = light->innercone;
	    sl.outerConeDeg = light->outercone;
	    if (const auto it = m_spotFeatureByLight.find (light->id); it != m_spotFeatureByLight.end ()) {
		sl.spotShadowFeature = it->second;
	    }
	    m_lightState.push_back (sl);
	} else if (light->lightType == "ltube") {
	    sl.type = SceneLight::Type::Tube;
	    const glm::vec3 endLocal = glm::vec3 (rot * glm::vec4 (light->controlPoint, 0.0f));
	    sl.tubeEnd = position + glm::vec3 (endLocal.x, -endLocal.y, endLocal.z);
	    m_lightState.push_back (sl);
	} else if (light->lightType == "lpoint") {
	    sl.type = SceneLight::Type::Point;
	    if (const auto it = m_pointSlotByLight.find (light->id); it != m_pointSlotByLight.end ()) {
		sl.pointShadowSlot = it->second;
	    }
	    m_lightState.push_back (sl);
	}

	static const bool s_lightDump = getenv ("LWE_LIGHTDUMP") != nullptr;
	static int s_lightDumpCount = 0;
	if (s_lightDump && s_lightDumpCount < 16) {
	    s_lightDumpCount++;
	    sLog.out (
		"LWE-LIGHTDUMP id=", light->id, " type=", light->lightType, " pos=(", position.x, ",", position.y, ",",
		position.z, ") travel=(", travel.x, ",", travel.y, ",", travel.z, ") rgb=(", rgb.r, ",", rgb.g, ",",
		rgb.b, ") radius=", light->radius, " exp=", light->exponent
	    );
	}
    }
}

Render::CObject* CScene::dispatchObjectType (const Object& object) {
    Render::CObject* renderObject = nullptr;

    if (object.is<Image> ()) {
	renderObject = new Objects::CImage (*this, *object.as<Image> ());
    } else if (object.is<Sound> ()) {
	renderObject = new Objects::CSound (*this, *object.as<Sound> ());
    } else if (object.is<Text> ()) {
	renderObject = new Objects::CText (*this, *object.as<Text> ());
    } else if (object.is<ModelObject> ()) {
	renderObject = new Objects::CModel (*this, *object.as<ModelObject> ());
    } else if (object.is<Light> ()) {
	// lights draw nothing (updateLights feeds the PerformLighting_V1 uniforms from
	// their shared DynamicValues) but need a ScriptableObject so scripted light
	// properties (color/intensity/transforms) tick
	this->m_lights.push_back (object.as<Light> ());
	renderObject = new Objects::CLight (*this, *object.as<Light> ());
    } else if (object.is<Particle> ()) {
	static const bool s_noParticles = getenv ("LWE_NOPARTICLES") != nullptr;
	if (s_noParticles) {
	    return nullptr;
	}
	const auto& particleData = *object.as<Particle> ();

	if (this->getContext ().getApp ().getContext ().settings.general.disableParticles == true) {
	    sLog.debug ("Ignoring particle system (disabled in settings): ", particleData.name);
	    return nullptr;
	}

	if (particleData.material == nullptr || particleData.material->material == nullptr) {
	    sLog.error ("Particle system has no valid material, skipping: ", particleData.name);
	    return nullptr;
	}

	renderObject = new Objects::CParticle (*this, particleData);
    } else {
	sLog.error ("Unknown object type, creating placeholder, empty object: ", object.id);
	renderObject = new CObject (*this, object);
    }

    try {
	renderObject->setup ();
    } catch (const std::exception& e) {
	sLog.error ("Failed to setup object ", object.id, ": ", e.what ());
	delete renderObject;
	renderObject = nullptr;
    }

    return renderObject;
}

void CScene::addObjectToRenderOrder (const Object& object) {
    const auto obj = this->m_objects.find (object.id);

    // ignores not created objects like particle systems
    if (obj == this->m_objects.end ()) {
	return;
    }

    // take into account any dependency first
    for (const auto& dep : object.dependencies) {
	// self-dependency is possible
	if (dep == object.id) {
	    continue;
	}

	// add the dependency to the list if it's created
	auto depIt = std::ranges::find_if (this->getScene ().objects, [&dep] (const auto& o) { return o->id == dep; });

	if (depIt != this->getScene ().objects.end ()) {
	    this->addObjectToRenderOrder (**depIt);
	} else {
	    sLog.error ("Cannot find dependency ", dep, " for object ", object.id);
	}
    }

    // ensure we're added only once to the render list
    const auto renderIt = std::ranges::find_if (this->m_objectsByRenderOrder, [&object] (const auto& o) {
	return o->getId () == object.id;
    });

    if (renderIt == this->m_objectsByRenderOrder.end ()) {
	this->m_objectsByRenderOrder.emplace_back (obj->second);
    }
}

ScriptEngine& CScene::getScriptEngine () const { return *this->m_scriptEngine; }
Camera& CScene::getCamera () const { return *this->m_camera; }

void CScene::renderFrame (const glm::ivec4& viewport) {
    // ensure the virtual mouse position is up to date
    this->updateMouse (viewport);

    // update the parallax position if required
    if (this->getScene ().camera.parallax.enabled->value->getBool ()
	&& !this->getContext ().getApp ().getContext ().settings.mouse.disableparallax) {
	const float influence = this->getScene ().camera.parallax.mouseInfluence->value->getFloat ();
	const float delay = this->getScene ().camera.parallax.delay->value->getFloat ();
	const float dt = g_Time - g_TimeLast;
	const float lag = delay <= 0.0f ? 1.0f : glm::clamp (dt / delay, 0.0f, 1.0f);

	const glm::vec2 centeredMouse = this->m_mousePosition - glm::vec2 (0.5f, 0.5f);
	this->m_parallaxDisplacement = glm::mix (this->m_parallaxDisplacement, centeredMouse * influence, lag);
    }

    // g_ParallaxPosition rides the same smoothed, influence-scaled displacement;
    // stays at the 0.5,0.5 neutral center whenever camera parallax is disabled
    this->m_parallaxPosition = glm::vec2 (0.5f, 0.5f) + this->m_parallaxDisplacement;

    // run a tick in the javascript logic
    this->getScriptEngine ().tick ();

    this->tickAnimations ();

    // update main textures for images
    for (const auto& cur : this->m_objectsByRenderOrder) {
	if (!cur->is<Objects::CImage> ()) {
	    continue;
	}

	const Objects::CImage* image = cur->as<Objects::CImage> ();

#if !NDEBUG
	const std::string message = "Updating texture " + image->getImage ().model->filename;

	glPushDebugGroup (GL_DEBUG_SOURCE_APPLICATION, 0, -1, message.c_str ());
#endif

	image->getTexture ()->update ();

#if !NDEBUG
	glPopDebugGroup ();
#endif
    }

    // bind the vertex array
    glBindVertexArray (this->m_vaoBuffer);
    // use the scene's framebuffer by default
    glBindFramebuffer (GL_FRAMEBUFFER, this->getWallpaperFramebuffer ());
    {
	static int n = 0;
	if (n++ < 3) {
	    GLint internalFormat = 0;
	    glBindTexture (GL_TEXTURE_2D, this->m_sceneFBO->getTextureID (0));
	    glGetTexLevelParameteriv (GL_TEXTURE_2D, 0, GL_TEXTURE_INTERNAL_FORMAT, &internalFormat);
	    sLog.error (
		"LWE-SCENEFB camW=", this->m_camera->getWidth (), " camH=", this->m_camera->getHeight (),
		" fboReal=", this->m_sceneFBO->getRealWidth (), "x", this->m_sceneFBO->getRealHeight (),
		" fboTex=", this->m_sceneFBO->getTextureWidth (0), "x", this->m_sceneFBO->getTextureHeight (0),
		" internalFmt=0x", std::hex, internalFormat, std::dec
	    );
	}
    }
    // ensure we render over the whole framebuffer
    glViewport (0, 0, this->m_sceneFBO->getRealWidth (), this->m_sceneFBO->getRealHeight ());

    glDepthMask (true);
    glColorMask (true, true, true, true);

    const glm::vec3 frameClearColor = this->getScene ().colors.clear->value->getVec3 ();
    glClearColor (frameClearColor.r, frameClearColor.g, frameClearColor.b, 1.0f);

    static const bool s_clearProbe = getenv ("LWE_CLEARPROBE") != nullptr;
    if (s_clearProbe && this->m_clearProbeCount < 3) {
	this->m_clearProbeCount++;
	GLfloat cc[4] = {};
	GLboolean mask[4] = {};
	glGetFloatv (GL_COLOR_CLEAR_VALUE, cc);
	glGetBooleanv (GL_COLOR_WRITEMASK, mask);
	sLog.out (
	    "LWE-CLEARPROBE frame scene=", static_cast<const void*> (this), " clearColor=", cc[0], ",", cc[1], ",",
	    cc[2], ",", cc[3], " mask=", static_cast<int> (mask[0]), static_cast<int> (mask[1]),
	    static_cast<int> (mask[2]), static_cast<int> (mask[3])
	);
    }

    glClear (GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);

    this->updateLights ();

    this->updateFogState ();

    if (this->m_shadowStage.viewCount > 0) {
	this->stageShadowMatrices ();
	this->renderShadowAtlas ();
	glBindFramebuffer (GL_FRAMEBUFFER, this->getWallpaperFramebuffer ());
	glViewport (0, 0, this->m_sceneFBO->getRealWidth (), this->m_sceneFBO->getRealHeight ());
	glEnable (GL_BLEND);
    }

    static const bool s_ledger = getenv ("LWE_LEDGER") != nullptr;
    static int s_ledgerFrame = 0;
    const bool ledgerActive = s_ledger && ++s_ledgerFrame <= 2;
    const auto ledgerDirty = [this, ledgerActive] () -> long {
	if (!ledgerActive) {
	    return 0;
	}
	const int fw = static_cast<int> (this->m_sceneFBO->getRealWidth ());
	const int fh = static_cast<int> (this->m_sceneFBO->getRealHeight ());
	std::vector<unsigned char> px (static_cast<size_t> (fw) * fh * 4);
	GLint prevRead = 0;
	glGetIntegerv (GL_READ_FRAMEBUFFER_BINDING, &prevRead);
	glBindFramebuffer (GL_READ_FRAMEBUFFER, this->getWallpaperFramebuffer ());
	glReadPixels (0, 0, fw, fh, GL_RGBA, GL_UNSIGNED_BYTE, px.data ());
	glBindFramebuffer (GL_READ_FRAMEBUFFER, prevRead);
	long sum = 0;
	for (size_t i = 0; i < px.size (); i += 16) {
	    sum += px[i] + px[i + 1] + px[i + 2];
	}
	return sum;
    };
    long ledgerPrev = ledgerDirty ();

    const auto& debug = this->getContext ().getApp ().getContext ().settings.render.debug;
    const auto enabledByDebug = [&debug] (const CObject* object) {
	if (debug.objectFilter.has_value () && object->getId () != debug.objectFilter.value ()) {
	    return false;
	}
	return std::ranges::find (debug.skipObjects, object->getId ()) == debug.skipObjects.end ();
    };
    const auto findAuthored = [this] (const int id) -> const Object* {
	const auto it = std::ranges::find_if (this->getScene ().objects, [id] (const auto& object) {
	    return object != nullptr && object->id == id;
	});
	return it == this->getScene ().objects.end () ? nullptr : it->get ();
    };
    const auto compositionAncestor = [this, &findAuthored] (const CObject* object) -> Objects::CImage* {
	const Object* current = findAuthored (object->getId ());
	for (int depth = 0; current != nullptr && current->parent.has_value () && depth < 32; depth++) {
	    const auto parentIt = this->m_objects.find (*current->parent);
	    if (parentIt == this->m_objects.end ()) {
		break;
	    }
	    if (auto* image = dynamic_cast<Objects::CImage*> (parentIt->second);
		image != nullptr && image->isCompositionLayer () && image->getCompositionFBO () != nullptr) {
		return image;
	    }
	    current = findAuthored (parentIt->second->getId ());
	}
	return nullptr;
    };
    std::set<int> compositionSubmitted;
    const auto renderCompositionImpl = [&] (Objects::CImage* composition, const auto& self) -> void {
	if (composition == nullptr || !compositionSubmitted.insert (composition->getId ()).second) {
	    return;
	}

	const auto target = composition->getCompositionFBO ();
	if (target == nullptr) {
	    if (enabledByDebug (composition)) {
		composition->render ();
	    }
	    return;
	}

	const auto previousTarget = this->m_compositionRenderTarget;
	const auto source = this->getActiveRenderTarget ();
	GLfloat previousClearColor[4];
	glGetFloatv (GL_COLOR_CLEAR_VALUE, previousClearColor);
	glBindFramebuffer (GL_FRAMEBUFFER, target->getFramebuffer ());
	glViewport (
	    0, 0, static_cast<GLsizei> (target->getRealWidth ()), static_cast<GLsizei> (target->getRealHeight ())
	);
	glColorMask (true, true, true, true);
	glDepthMask (true);
	glClearColor (0.0f, 0.0f, 0.0f, 0.0f);
	glClear (GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
	glClearColor (previousClearColor[0], previousClearColor[1], previousClearColor[2], previousClearColor[3]);

	if (composition->copiesCompositionBackground () && source != nullptr && source != target) {
	    glBindFramebuffer (GL_READ_FRAMEBUFFER, source->getFramebuffer ());
	    glBindFramebuffer (GL_DRAW_FRAMEBUFFER, target->getFramebuffer ());
	    glBlitFramebuffer (
		0, 0, static_cast<GLint> (source->getRealWidth ()), static_cast<GLint> (source->getRealHeight ()), 0, 0,
		static_cast<GLint> (target->getRealWidth ()), static_cast<GLint> (target->getRealHeight ()),
		GL_COLOR_BUFFER_BIT, GL_LINEAR
	    );
	}

	this->m_compositionRenderTarget = target;
	for (CObject* child : this->m_objectsByRenderOrder) {
	    if (compositionSubmitted.contains (child->getId ()) || compositionAncestor (child) != composition) {
		continue;
	    }
	    if (auto* nested = dynamic_cast<Objects::CImage*> (child);
		nested != nullptr && nested->isCompositionLayer ()) {
		self (nested, self);
	    } else {
		compositionSubmitted.insert (child->getId ());
		if (enabledByDebug (child)) {
		    child->render ();
		}
	    }
	}
	this->m_compositionRenderTarget = previousTarget;

	if (enabledByDebug (composition)) {
	    composition->render ();
	}
    };
    const auto renderComposition
	= [&] (Objects::CImage* composition) { renderCompositionImpl (composition, renderCompositionImpl); };

    for (const auto& cur : this->m_objectsByRenderOrder) {
	if (compositionSubmitted.contains (cur->getId ())) {
	    continue;
	}
	if (auto* ancestor = compositionAncestor (cur); ancestor != nullptr) {
	    renderComposition (ancestor);
	    continue;
	}
	if (auto* image = dynamic_cast<Objects::CImage*> (cur);
	    image != nullptr && image->isCompositionLayer () && image->getCompositionFBO () != nullptr) {
	    renderComposition (image);
	    continue;
	}
	if (debug.objectFilter.has_value () && cur->getId () != debug.objectFilter.value ()) {
	    if (ledgerActive) {
		sLog.out ("LWE-LEDGER f", s_ledgerFrame, " id=", cur->getId (), " SKIP(filter)");
	    }
	    continue;
	}
	if (std::ranges::find (debug.skipObjects, cur->getId ()) != debug.skipObjects.end ()) {
	    if (ledgerActive) {
		sLog.out ("LWE-LEDGER f", s_ledgerFrame, " id=", cur->getId (), " SKIP(skiplist)");
	    }
	    continue;
	}

	cur->render ();

	if (ledgerActive) {
	    const long now = ledgerDirty ();
	    sLog.out ("LWE-LEDGER f", s_ledgerFrame, " id=", cur->getId (), " DRAW dirty=", now - ledgerPrev);
	    ledgerPrev = now;
	}

	static const char* s_objProbe = getenv ("LWE_OBJPROBE");
	static int s_objProbeRuns = 0;
	if (s_objProbe != nullptr) {
	    int x0 = 0, y0 = 0, x1 = 0, y1 = 0, skip = 0;
	    const int nf = sscanf (s_objProbe, "%d %d %d %d %d", &x0, &y0, &x1, &y1, &skip);
	    if (nf >= 4 && x1 > x0 && y1 > y0 && s_objProbeRuns++ >= skip && s_objProbeRuns < skip + 200) {
		const int rw = x1 - x0, rh = y1 - y0;
		// LWE_OBJPROBE_FLOAT=1: raw float readback (FP16 targets hold pre-clamp
		// >1 values that a UNSIGNED_BYTE read would clamp away)
		static const bool s_floatRead = getenv ("LWE_OBJPROBE_FLOAT") != nullptr;
		std::vector<float> fpx;
		std::vector<unsigned char> px (static_cast<size_t> (rw) * rh * 4);
		GLint prevRead = 0;
		glGetIntegerv (GL_READ_FRAMEBUFFER_BINDING, &prevRead);
		glBindFramebuffer (GL_READ_FRAMEBUFFER, this->getWallpaperFramebuffer ());
		if (s_floatRead) {
		    fpx.resize (static_cast<size_t> (rw) * rh * 4);
		    glReadPixels (x0, y0, rw, rh, GL_RGBA, GL_FLOAT, fpx.data ());
		    float maxV = 0.0f;
		    size_t over1 = 0;
		    for (size_t i = 0; i < fpx.size (); i += 4) {
			const float m = std::max (fpx[i], std::max (fpx[i + 1], fpx[i + 2]));
			maxV = std::max (maxV, m);
			over1 += m > 1.001f ? 1 : 0;
		    }
		    sLog.out ("LWE-OBJPROBE-FLOAT max=", maxV, " over1px=", over1, "/", fpx.size () / 4);
		    for (size_t i = 0; i < px.size (); i++) {
			px[i] = static_cast<unsigned char> (std::min (255.0f, fpx[i] * 100.0f));
		    }
		}
		if (!s_floatRead) {
		    glReadPixels (x0, y0, rw, rh, GL_RGBA, GL_UNSIGNED_BYTE, px.data ());
		}
		glBindFramebuffer (GL_READ_FRAMEBUFFER, prevRead);
		double sum[4] = {};
		for (size_t i = 0; i < px.size (); i += 4) {
		    for (int c = 0; c < 4; c++) {
			sum[c] += px[i + c];
		    }
		}
		const double n = static_cast<double> (rw) * rh;
		sLog.out (
		    "LWE-OBJPROBE obj=", cur->getId (), " rect mean=", sum[0] / n, ",", sum[1] / n, ",", sum[2] / n,
		    ",", sum[3] / n
		);
	    }
	}
    }

    static const char* s_fbDump = getenv ("LWE_FBDUMP");
    if (s_fbDump != nullptr) {
	static int s_dumpFrame = 0;
	static const char* s_dumpFrameEnv = getenv ("LWE_FBDUMP_FRAME");
	static const int s_refreshFrame = s_dumpFrameEnv != nullptr ? std::max (4, atoi (s_dumpFrameEnv)) : 150;
	++s_dumpFrame;
	if (s_dumpFrame == 3 || s_dumpFrame == s_refreshFrame) {
	    const int fw = static_cast<int> (this->m_sceneFBO->getRealWidth ());
	    const int fh = static_cast<int> (this->m_sceneFBO->getRealHeight ());
	    std::vector<unsigned char> px (static_cast<size_t> (fw) * fh * 4);
	    GLint prevRead = 0;
	    glGetIntegerv (GL_READ_FRAMEBUFFER_BINDING, &prevRead);
	    glBindFramebuffer (GL_READ_FRAMEBUFFER, this->getWallpaperFramebuffer ());
	    glReadPixels (0, 0, fw, fh, GL_RGBA, GL_UNSIGNED_BYTE, px.data ());
	    glBindFramebuffer (GL_READ_FRAMEBUFFER, prevRead);
	    const std::string path = std::string (s_fbDump) + ".ppm";
	    FILE* f = fopen (path.c_str (), "wb");
	    if (f != nullptr) {
		fprintf (f, "P6\n%d %d\n255\n", fw, fh);
		// FBO row 0 = top of presented content (scene space is y-down)
		for (int y = 0; y < fh; y++) {
		    for (int x = 0; x < fw; x++) {
			fwrite (&px[(static_cast<size_t> (y) * fw + x) * 4], 1, 3, f);
		    }
		}
		fclose (f);
		sLog.out ("LWE-FBDUMP wrote ", path, " (", fw, "x", fh, ")");
	    } else {
		sLog.error ("LWE-FBDUMP cannot open ", path);
	    }
	}
    }

    static const bool s_fbProfile = getenv ("LWE_FBPROFILE") != nullptr;
    if (s_fbProfile) {
	static int s_frame = 0;
	if (++s_frame == 150) {
	    const int fw = static_cast<int> (this->m_sceneFBO->getRealWidth ());
	    const int fh = static_cast<int> (this->m_sceneFBO->getRealHeight ());
	    std::vector<unsigned char> px (static_cast<size_t> (fw) * fh * 4);
	    glBindFramebuffer (GL_READ_FRAMEBUFFER, this->getWallpaperFramebuffer ());
	    glReadPixels (0, 0, fw, fh, GL_RGBA, GL_UNSIGNED_BYTE, px.data ());
	    double cols[32] = {}, rows[32] = {};
	    for (int y = 0; y < fh; y += 4) {
		for (int x = 0; x < fw; x += 4) {
		    const size_t o = (static_cast<size_t> (y) * fw + x) * 4;
		    const double lum = 0.299 * px[o] + 0.587 * px[o + 1] + 0.114 * px[o + 2];
		    cols[x * 32 / fw] += lum;
		    rows[y * 32 / fh] += lum;
		}
	    }
	    std::ostringstream c, r;
	    for (int i = 0; i < 32; i++) {
		c << static_cast<int> (cols[i] / (fh / 4.0 * (fw / 32.0 / 4.0))) << (i < 31 ? "," : "");
		r << static_cast<int> (rows[i] / (fw / 4.0 * (fh / 32.0 / 4.0))) << (i < 31 ? "," : "");
	    }
	    sLog.out ("LWE-FBPROFILE cols=", c.str ());
	    sLog.out ("LWE-FBPROFILE rows=", r.str ());
	}
    }
}

void CScene::updateMouse (const glm::ivec4& viewport) {
    static const char* s_mousePin = getenv ("LWE_MOUSE_POS");
    if (s_mousePin != nullptr) {
	float fx = 0.5f, fy = 0.5f;
	if (sscanf (s_mousePin, "%f,%f", &fx, &fy) == 2) {
	    const glm::dvec2 synthetic = { viewport.x + static_cast<double> (fx) * viewport.z,
					   viewport.y + static_cast<double> (fy) * viewport.w };
	    this->m_mousePositionLast = this->m_mousePosition;
	    const double mouseX = glm::clamp ((synthetic.x - viewport.x) / viewport.z, 0.0, 1.0);
	    const double normalizedMouseY = glm::clamp ((synthetic.y - viewport.y) / viewport.w, 0.0, 1.0);
	    const auto uvs = this->getState ().getTextureUVs ();
	    this->m_mousePositionNormalized.x = uvs.ustart + mouseX * (uvs.uend - uvs.ustart);
	    this->m_mousePositionNormalized.y = uvs.vstart + normalizedMouseY * (uvs.vend - uvs.vstart);
	    const double mouseY = 1.0 - normalizedMouseY;
	    this->m_mousePosition.x = this->m_mousePositionNormalized.x;
	    this->m_mousePosition.y = uvs.vstart + mouseY * (uvs.vend - uvs.vstart);
	    return;
	}
    }

    const glm::dvec2 n = this->getContext ().getInputContext ().getMouseInput ().normalized ();

    // rollover the position to the last
    this->m_mousePositionLast = this->m_mousePosition;

    const double mouseX = n.x;
    // Normalize Y coordinate (OpenGL convention: 0=bottom, 1=top)
    // Particle code expects this convention: 0=bottom results in negative Y (down), 1=top results in positive Y (up)
    const double normalizedMouseY = 1.0 - n.y;

    // Account for UV cropping when using fill/fit scaling modes
    // The scene may be rendered larger than viewport and cropped via UVs
    const auto uvs = this->getState ().getTextureUVs ();

    // Map mouse position from viewport space to scene UV space
    // UVs define what portion of the scene texture is visible
    this->m_mousePositionNormalized.x = uvs.ustart + mouseX * (uvs.uend - uvs.ustart);
    this->m_mousePositionNormalized.y = uvs.vstart + normalizedMouseY * (uvs.vend - uvs.vstart);

    // Invert previous normalization of Y to match what the shader expects
    double mouseY = 1.0 - normalizedMouseY;

    this->m_mousePosition.x = this->m_mousePositionNormalized.x;
    this->m_mousePosition.y = uvs.vstart + mouseY * (uvs.vend - uvs.vstart);
}

const Scene& CScene::getScene () const { return *this->getWallpaperData ().as<Scene> (); }

glm::ivec2 CScene::largestOutputSize () const {
    int outW = 0, outH = 0;

    for (const auto& vp : this->getContext ().getOutput ().getViewports () | std::views::values) {
	outW = std::max (outW, vp->viewport.z);
	outH = std::max (outH, vp->viewport.w);
    }

    if (outW <= 0 || outH <= 0) {
	outW = this->getContext ().getOutput ().getFullWidth ();
	outH = this->getContext ().getOutput ().getFullHeight ();
    }

    return { outW, outH };
}

glm::vec2 CScene::clampToCap (glm::vec2 size) const {
    const float ssf = lwe_ssfactor ();
    if (ssf <= 0.0f || size.x <= 0.0f || size.y <= 0.0f) {
	return size; // disabled (escape hatch) or degenerate -> legacy behavior
    }
    const glm::ivec2 out = this->largestOutputSize ();
    const float capW = static_cast<float> (out.x) * ssf;
    const float capH = static_cast<float> (out.y) * ssf;
    if (capW <= 0.0f || capH <= 0.0f) {
	return size;
    }
    const float rw = capW / size.x;
    const float rh = capH / size.y;
    // Mode-aware scale: fit/stretch shrink to fit INSIDE the cap (s_min); fill/default must COVER the
    // cap so the visible (cropped) region still samples >=1:1 (s_max). Matches the present-pass UV mode.
    const WallpaperState::TextureUVsScaling mode = this->getState ().getTextureUVsScaling ();
    const bool cover
	= (mode == WallpaperState::TextureUVsScaling::ZoomFillUVs
	   || mode == WallpaperState::TextureUVsScaling::DefaultUVs);
    const float s = cover ? std::min (1.0f, std::max (rw, rh)) : std::min ({ 1.0f, rw, rh });
    return s < 1.0f ? size * s : size;
}

void CScene::collectSharedComposites (const Scene& scene) {
    static const std::string prefix = "_rt_imageLayerComposite_";
    const auto scan = [this] (int ownerId, const std::string& name) {
	if (name.rfind (prefix, 0) != 0) {
	    return;
	}
	std::string rest = name.substr (prefix.size ()); // "<id>_a" / "<id>_b"
	const auto us = rest.find_last_of ('_');
	if (us != std::string::npos) {
	    rest = rest.substr (0, us);
	}
	try {
	    const int refId = std::stoi (rest);
	    if (refId != ownerId) {
		this->m_sharedCompositeIds.insert (refId);
	    }
	} catch (...) { }
    };

    for (const auto& obj : scene.objects) {
	if (!obj->is<Image> ()) {
	    continue;
	}
	const Image* img = obj->as<Image> ();
	const int oid = img->id;
	for (const auto& ie : img->effects) {
	    if (ie->effect != nullptr) {
		for (const auto& p : ie->effect->passes) {
		    if (p->target.has_value ()) {
			scan (oid, p->target.value ());
		    }
		    if (p->source.has_value ()) {
			scan (oid, p->source.value ());
		    }
		    for (const auto& name : p->binds | std::views::values) {
			scan (oid, name);
		    }
		}
	    }
	    for (const auto& po : ie->passOverrides) {
		for (const auto& name : po->textures | std::views::values) {
		    scan (oid, name);
		}
		for (const auto& name : po->usertextures | std::views::values) {
		    scan (oid, name);
		}
	    }
	}
    }
}

std::pair<std::shared_ptr<CFBO>, std::shared_ptr<CFBO>>
CScene::leaseCompositePair (int id, glm::vec2 size, uint32_t flags, TextureFormat format) {
    // Escape hatch (default ON): LWE_FBOPOOL=0 dedicates every layer = exact legacy allocation.
    static const bool poolDisabled = [] () {
	const char* e = getenv ("LWE_FBOPOOL");
	return e != nullptr && std::string (e) == "0";
    }();
    if (poolDisabled) {
	return { nullptr, nullptr };
    }
    // SHARED layer composites are read by name from other objects -> must stay dedicated.
    if (this->m_sharedCompositeIds.contains (id)) {
	this->m_poolDedicatedCount++;
	return { nullptr, nullptr };
    }
    const auto w = static_cast<uint32_t> (size.x);
    const auto h = static_cast<uint32_t> (size.y);
    if (w == 0 || h == 0) {
	this->m_poolDedicatedCount++;
	return { nullptr, nullptr };
    }
    std::ostringstream key;
    key << w << "x" << h << "_" << flags << "_" << static_cast<int> (format);
    if (const auto it = this->m_compositePool.find (key.str ()); it != this->m_compositePool.end ()) {
	this->m_poolLeaseCount[key.str ()]++;
	return it->second;
    }
    std::ostringstream na, nb;
    na << "_rt_pool_composite_" << key.str () << "_a";
    nb << "_rt_pool_composite_" << key.str () << "_b";
    auto a = this->create (na.str (), format, flags, 1, { size.x, size.y }, { size.x, size.y });
    auto b = this->create (nb.str (), format, flags, 1, { size.x, size.y }, { size.x, size.y });
    auto pair = std::make_pair (a, b);
    this->m_compositePool.emplace (key.str (), pair);
    this->m_poolLeaseCount[key.str ()]++;
    return pair;
}

void CScene::reportPoolHighWater () const {
    static const bool on = [] () {
	const char* e = getenv ("LWE_POOL_HWM");
	return e != nullptr && std::string (e) == "1";
    }();
    if (!on) {
	return;
    }
    int totalLeases = 0;
    int maxReuse = 0;
    for (const auto& [key, count] : this->m_poolLeaseCount) {
	totalLeases += count;
	maxReuse = std::max (maxReuse, count);
    }
    sLog.out (
	"LWE-POOLHWM pooledPairs=", this->m_compositePool.size (), " pooledLeases=", totalLeases,
	" maxReusePerPair=", maxReuse, " dedicated=", this->m_poolDedicatedCount
    );
    for (const auto& [key, count] : this->m_poolLeaseCount) {
	sLog.out ("LWE-POOLHWM   class ", key, " leases=", count);
    }
}

int CScene::getWidth () const { return this->m_canvasWidth; }

int CScene::getHeight () const { return this->m_canvasHeight; }

float CScene::getTime () const { return g_Time; }

float CScene::getDeltaTime () const { return g_Time - g_TimeLast; }

float CScene::getFps () const {
    const float dt = g_Time - g_TimeLast;
    // Guard against the first frame (where g_TimeLast is 0 so dt == g_Time)
    // and division by zero on the very first call.
    if (dt <= 1e-6f) {
	return 60.0f;
    }
    return 1.0f / dt;
}

const glm::vec2* CScene::getMousePosition () const { return &this->m_mousePosition; }

const glm::vec2* CScene::getMousePositionLast () const { return &this->m_mousePositionLast; }

const glm::vec2* CScene::getMousePositionNormalized () const { return &this->m_mousePositionNormalized; }

const glm::vec2* CScene::getParallaxDisplacement () const { return &this->m_parallaxDisplacement; }

const glm::vec2* CScene::getParallaxPosition () const { return &this->m_parallaxPosition; }

const std::vector<CObject*>& CScene::getObjectsByRenderOrder () const { return this->m_objectsByRenderOrder; }

const CObject* CScene::getObject (int id) const {
    const auto object = this->m_objects.find (id);
    return object == this->m_objects.end () ? nullptr : object->second;
}
