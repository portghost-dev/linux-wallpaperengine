#include "CImage.h"

#include "WallpaperEngine/Render/MipResidency.h"

#include "CRenderable.h"

#include <algorithm>
#include <cstring>
#include <iterator>
#include <optional>
#include <set>
#include <sstream>

#include <glm/glm.hpp>
#include <glm/gtc/matrix_transform.hpp>
#define GLM_ENABLE_EXPERIMENTAL
#include <glm/gtx/rotate_vector.hpp>
#undef GLM_ENABLE_EXPERIMENTAL

#include "WallpaperEngine/Data/Model/DynamicValue.h"
#include "WallpaperEngine/Data/Model/Material.h"
#include "WallpaperEngine/Data/Model/Object.h"
#include "WallpaperEngine/Data/Model/UserSetting.h"
#include "WallpaperEngine/Data/Parsers/MaterialParser.h"
#include "WallpaperEngine/Data/Utils/BinaryReader.h"
#include "WallpaperEngine/Data/Utils/MemoryStream.h"
#include "WallpaperEngine/Logging/Log.h"

using namespace WallpaperEngine;
using namespace WallpaperEngine::Render::Objects;
using namespace WallpaperEngine::Render::Objects::Effects;
using namespace WallpaperEngine::Data::Parsers;
using namespace WallpaperEngine::Data::Builders;
using namespace WallpaperEngine::Data::Utils;

extern float g_Time;

namespace {
glm::vec2 rotateVec2 (const glm::vec2& value, float angle) {
    const float cosAngle = std::cos (angle);
    const float sinAngle = std::sin (angle);
    return { value.x * cosAngle - value.y * sinAngle, value.x * sinAngle + value.y * cosAngle };
}

bool isMagentaNeonTint (const glm::vec3& color) { return color.r > 0.55f && color.g < 0.25f && color.b > 0.45f; }

std::optional<glm::vec3> findMagentaCompositeTint (const Image& image, const std::vector<int>& skippedEffectIds) {
    for (const auto& effect : image.effects) {
	if (std::find (skippedEffectIds.begin (), skippedEffectIds.end (), static_cast<int> (effect->id))
	    != skippedEffectIds.end ()) {
	    continue;
	}
	if (!effect->visible->value->getBool ()) {
	    continue;
	}

	for (const auto& passOverride : effect->passOverrides) {
	    const auto compositeCombo = passOverride->combos.find ("COMPOSITE");
	    if (compositeCombo == passOverride->combos.end () || compositeCombo->second != 2) {
		continue;
	    }

	    const auto compositeColor = passOverride->constants.find ("compositecolor");
	    if (compositeColor == passOverride->constants.end () || compositeColor->second == nullptr
		|| compositeColor->second->value == nullptr) {
		continue;
	    }

	    const auto tint = compositeColor->second->value->getVec3 ();
	    if (isMagentaNeonTint (tint)) {
		return tint;
	    }
	}
    }

    return std::nullopt;
}

}

CImage::ResolvedTransform CImage::localTransform (const Object& object) {
    glm::vec3 origin = object.origin->value->getVec3 ();
    glm::vec3 scale = glm::vec3 (1.0f);
    float angle = 0.0f;

    if (object.is<Image> ()) {
	const auto* image = object.as<Image> ();
	scale = image->scale->value->getVec3 ();
	angle = image->angles->value->getVec3 ().z;
    } else if (object.is<Text> ()) {
	const auto* text = object.as<Text> ();
	scale = text->scale->value->getVec3 ();
    } else {
	scale = object.groupScale->value->getVec3 ();
	angle = object.groupAngles->value->getVec3 ().z;
    }

    return { origin, scale, angle };
}

CImage::ResolvedTransform CImage::resolveTransform (const Object& object) const {
    constexpr int kMaxParentDepth = 32;

    // Walk up the parent chain leaf-first, bounded by kMaxParentDepth to guard
    // against cycles. chain[0] is the requested object; the last entry is the root.
    const Object* chain[kMaxParentDepth + 1];
    int count = 0;
    const Object* current = &object;
    chain[count++] = current;

    while (current->parent.has_value ()) {
	if (count > kMaxParentDepth) {
	    sLog.error ("Parent transform chain is too deep; possible cycle at object id=", current->id);
	    break;
	}
	const auto* parentObject = this->getScene ().getObject (current->parent.value ());
	if (parentObject == nullptr) {
	    break;
	}
	current = &parentObject->getObject ();
	chain[count++] = current;
    }

    // Accumulate top-down: the root's local transform is already its resolved
    // transform, then fold each child onto its already-resolved parent.
    ResolvedTransform resolved = localTransform (*chain[count - 1]);
    for (int i = count - 2; i >= 0; --i) {
	ResolvedTransform local = localTransform (*chain[i]);
	const glm::vec2 offset
	    = rotateVec2 ({ local.origin.x * resolved.scale.x, local.origin.y * resolved.scale.y }, resolved.angle);
	local.origin.x = resolved.origin.x + offset.x;
	local.origin.y = resolved.origin.y + offset.y;
	local.origin.z = resolved.origin.z + local.origin.z * resolved.scale.z;
	resolved = { local.origin, local.scale * resolved.scale, local.angle + resolved.angle };
    }

    return resolved;
}

CImage::CImage (Wallpapers::CScene& scene, const Image& image) :
    CObject (scene, image), CRenderable (scene, image, *image.model->material), ScriptableObject (scene, image),
    m_sceneSpacePosition (GL_NONE), m_copySpacePosition (GL_NONE), m_passSpacePosition (GL_NONE),
    m_texcoordCopy (GL_NONE), m_texcoordPass (GL_NONE), m_modelViewProjectionScreen (),
    m_modelViewProjectionPass (glm::mat4 (1.0)), m_modelViewProjectionCopy (), m_modelViewProjectionScreenInverse (),
    m_modelViewProjectionPassInverse (glm::inverse (m_modelViewProjectionPass)), m_modelViewProjectionCopyInverse (),
    m_modelMatrix (), m_viewProjectionMatrix (), m_image (image), m_pos (), m_initialized (false) {
    // register any properties in use on this object
    this->registerProperty ("origin", *image.origin->value);
    this->registerProperty ("scale", *image.scale->value);
    this->registerProperty ("angles", *image.angles->value);
    this->registerProperty ("visible", *image.visible->value);
    this->registerProperty ("alpha", *image.alpha->value);
    this->registerProperty ("color", *image.color->value);
    this->registerProperty ("parallaxDepth", *image.parallaxDepth->value);

    this->m_isShape
	= image.model->material != nullptr && image.model->material->filename == "materials/wpenginelinux_shape.json";

    // get scene width and height to calculate positions
    auto scene_width = static_cast<float> (scene.getWidth ());
    auto scene_height = static_cast<float> (scene.getHeight ());

    const auto transform = this->resolveTransform (this->getImage ());
    glm::vec3 origin = transform.origin;
    glm::vec2 size = this->getSize ();
    glm::vec3 scale = transform.scale;

    if (this->isCompositionLayer () && scene.hasAuthoredChildren (image.id)) {
	// must track the scene target's RESOLUTION: this buffer shadows _rt_FullFrameBuffer for the
	// subtree, so a mismatch would hand the subtree's passes a different-sized "full frame"
	const glm::vec2 compositionSize
	    = scene.clampToCap ({ static_cast<float> (scene.getWidth ()), static_cast<float> (scene.getHeight ()) });
	this->m_compositionFBO = scene.create (
	    "_rt_compositionLayer_" + std::to_string (image.id), TextureFormat_ARGB8888, TextureFlags_ClampUVs, 1.0f,
	    compositionSize, compositionSize
	);
	this->alias ("_rt_FullFrameBuffer", std::const_pointer_cast<CFBO> (this->m_compositionFBO));
    }

    this->detectTexture ();

    // detect texture (if any)
    if (this->m_texture == nullptr) {
	if (this->m_image.model->solidlayer && size.x == 0.0f && size.y == 0.0f) {
	    size.x = scene_width;
	    size.y = scene_height;
	}
	// if (this->m_image->isSolid ()) // layer receives cursor events:
	// https://docs.wallpaperengine.io/en/scene/scenescript/reference/event/cursor.html same applies to effects
	// TODO: create a dummy texture of correct size, fbo constructors should be enough, but this should be properly
	// handled
	this->m_texture = std::make_shared<CFBO> (
	    "", TextureFormat_ARGB8888, TextureFlags_NoFlags, 1, size.x, size.y, size.x, size.y
	);
    }

    // If the wallpaper doesn't specify a size, fall back to the texture or model dimensions
    if ((size.x == 0.0f || size.y == 0.0f) && this->m_texture != nullptr) {
	size.x = static_cast<float> (this->m_texture->getRealWidth ());
	size.y = static_cast<float> (this->m_texture->getRealHeight ());
    } else if (
	(size.x == 0.0f || size.y == 0.0f) && this->getImage ().model->width.has_value ()
	&& this->getImage ().model->height.has_value ()
    ) {
	size.x = static_cast<float> (this->getImage ().model->width.value ());
	size.y = static_cast<float> (this->getImage ().model->height.value ());
    }

    // fullscreen layers should use the whole projection's size
    // TODO: WHAT SHOULD AUTOSIZE DO?
    if (this->getImage ().model->fullscreen) {
	size = { scene_width, scene_height };
	origin = { scene_width / 2, scene_height / 2, 0 };

	// TODO: CHANGE ALIGNMENT TOO?
    }
    this->m_size = size;

    glm::vec2 scaledSize = size * glm::vec2 (scale);

    static const char* s_cropMode = getenv ("LWE_CROPOFF");
    if (s_cropMode != nullptr && !this->getImage ().model->puppet.has_value ()) {
	const float sign = (s_cropMode[0] == '2') ? -1.0f : 1.0f;
	const glm::vec2& c = this->getImage ().model->cropOffset;
	origin.x += sign * c.x * scale.x;
	origin.y += sign * c.y * scale.y;
    }

    // calculate the center and shift from there
    this->m_pos.x = origin.x - (scaledSize.x / 2);
    this->m_pos.w = origin.y + (scaledSize.y / 2);
    this->m_pos.z = origin.x + (scaledSize.x / 2);
    this->m_pos.y = origin.y - (scaledSize.y / 2);

    if (this->getImage ().alignment.find ("top") != std::string::npos) {
	this->m_pos.y -= scaledSize.y / 2;
	this->m_pos.w -= scaledSize.y / 2;
    } else if (this->getImage ().alignment.find ("bottom") != std::string::npos) {
	this->m_pos.y += scaledSize.y / 2;
	this->m_pos.w += scaledSize.y / 2;
    }

    if (this->getImage ().alignment.find ("left") != std::string::npos) {
	this->m_pos.x += scaledSize.x / 2;
	this->m_pos.z += scaledSize.x / 2;
    } else if (this->getImage ().alignment.find ("right") != std::string::npos) {
	this->m_pos.x -= scaledSize.x / 2;
	this->m_pos.z -= scaledSize.x / 2;
    }

    // wallpaper engine
    this->m_pos.x -= scene_width / 2;
    this->m_pos.y = scene_height / 2 - this->m_pos.y;
    this->m_pos.z -= scene_width / 2;
    this->m_pos.w = scene_height / 2 - this->m_pos.w;

    static const bool s_imgDump = getenv ("LWE_IMGDUMP") != nullptr;
    if (s_imgDump) {
	sLog.out (
	    "LWE-IMGDUMP obj=", this->getId (), " origin=(", origin.x, ",", origin.y, ",", origin.z, ") size=(", size.x,
	    ",", size.y, ") scale=(", scale.x, ",", scale.y, ") rect=[", this->m_pos.x, "..", this->m_pos.z, "]x[",
	    this->m_pos.w, "..", this->m_pos.y, "] scene=", scene_width, "x", scene_height
	);
    }

    // register both FBOs into the scene
    std::ostringstream nameA, nameB;

    // TODO: determine when _rt_imageLayerComposite and _rt_imageLayerAlbedo is used
    nameA << "_rt_imageLayerComposite_" << this->getImage ().id << "_a";
    nameB << "_rt_imageLayerComposite_" << this->getImage ().id << "_b";

    static const bool s_fboCoverage = getenv ("LWE_NOFBOCOVERAGE") == nullptr;
    static const bool s_fboCoverageLog = getenv ("LWE_FBOCOVERAGE") != nullptr;
    glm::vec2 fboBase = { size.x, size.y };
    if (s_fboCoverage) {
	const glm::vec2 coverage = glm::abs (glm::vec2 (size) * glm::vec2 (scale));
	fboBase = glm::max (fboBase, glm::min (coverage, glm::vec2 (scene_width, scene_height)));
	if (s_fboCoverageLog && fboBase != glm::vec2 (size)) {
	    sLog.out (
		"LWE-FBOCOVERAGE obj=", this->getId (), " authored=", size.x, "x", size.y, " -> fbo=", fboBase.x, "x",
		fboBase.y
	    );
	}
    }
    const glm::vec2 fboSize = scene.clampToCap (fboBase);
    const uint32_t fboFlags = this->m_texture->getFlags ();
    const TextureFormat compositeFormat = scene.isHdrBloom () ? TextureFormat_RGBA16161616f : TextureFormat_ARGB8888;
    auto [poolA, poolB] = scene.leaseCompositePair (this->getImage ().id, fboSize, fboFlags, compositeFormat);
    if (poolA != nullptr && poolB != nullptr) {
	this->m_currentMainFBO = this->m_mainFBO = poolA;
	this->m_currentSubFBO = this->m_subFBO = poolB;
    } else {
	this->m_currentMainFBO = this->m_mainFBO = scene.create (
	    nameA.str (), compositeFormat, fboFlags, 1, { fboSize.x, fboSize.y }, { fboSize.x, fboSize.y }
	);
	this->m_currentSubFBO = this->m_subFBO = scene.create (
	    nameB.str (), compositeFormat, fboFlags, 1, { fboSize.x, fboSize.y }, { fboSize.x, fboSize.y }
	);
    }

    // build a list of vertices, these might need some change later (or maybe invert the camera)
    GLfloat sceneSpacePosition[] = { this->m_pos.x, this->m_pos.y, 0.0f, this->m_pos.x, this->m_pos.w, 0.0f,
				     this->m_pos.z, this->m_pos.y, 0.0f, this->m_pos.z, this->m_pos.y, 0.0f,
				     this->m_pos.x, this->m_pos.w, 0.0f, this->m_pos.z, this->m_pos.w, 0.0f };

    float width = 1.0f;
    float height = 1.0f;

    if (this->getTexture ()->isAnimated ()) {
	// animated images use different coordinates as they're essentially a texture atlas
	width = static_cast<float> (this->getTexture ()->getRealWidth ())
	    / static_cast<float> (this->getTexture ()->getTextureWidth (0));
	height = static_cast<float> (this->getTexture ()->getRealHeight ())
	    / static_cast<float> (this->getTexture ()->getTextureHeight (0));
    }
    // calculate the correct texCoord limits for the texture based on the texture screen size and real size
    else if (
	this->getTexture () != nullptr
	&& (this->getTexture ()->getTextureWidth (0) != this->getTexture ()->getRealWidth ()
	    || this->getTexture ()->getTextureHeight (0) != this->getTexture ()->getRealHeight ())
    ) {
	// Account for padding in non-power-of-two textures: clamp UVs to the real content
	width = static_cast<float> (this->getTexture ()->getRealWidth ())
	    / static_cast<float> (this->getTexture ()->getTextureWidth (0));
	height = static_cast<float> (this->getTexture ()->getRealHeight ())
	    / static_cast<float> (this->getTexture ()->getTextureHeight (0));
    }

    // TODO: RECALCULATE THESE POSITIONS FOR PASSTHROUGH SO THEY TAKE THE RIGHT PART OF THE TEXTURE
    float x = 0.0f;
    float y = 0.0f;

    if (this->getTexture ()->isAnimated ()) {
	// animations should be copied completely
	x = 0.0f;
	y = 0.0f;
	width = 1.0f;
	height = 1.0f;
    }

    GLfloat realWidth = size.x;
    GLfloat realHeight = size.y;
    GLfloat realX = 0.0;
    GLfloat realY = 0.0;

    if (this->getImage ().model->passthrough) {
	// Passthrough shaders fill the destination FBO from texcoords and sample the scene using positions.
	// Keep the destination quad full-screen in local FBO space, but pass scene-space positions through.
	x = 0.0f;
	y = 0.0f;
	width = 1.0f;
	height = 1.0f;
	realX = this->m_pos.x;
	realY = this->m_pos.w;
	realWidth = this->m_pos.z;
	realHeight = this->m_pos.y;

	if (this->getImage ().model->fullscreen) {
	    realX = -1.0;
	    realY = -1.0;
	    realWidth = 1.0;
	    realHeight = 1.0;
	}
    }

    if (getenv ("LWE_IMGDUMP") != nullptr && this->getTexture () != nullptr) {
	sLog.out (
	    "LWE-UVDUMP obj=", this->getId (), " texReal=", this->getTexture ()->getRealWidth (), "x",
	    this->getTexture ()->getRealHeight (), " texAlloc=", this->getTexture ()->getTextureWidth (0), "x",
	    this->getTexture ()->getTextureHeight (0), " copyUV=[", x, "..", width, "]x[", y, "..", height, "]"
	);
    }

    GLfloat texcoordCopy[] = { x, height, x, y, width, height, width, height, x, y, width, y };

    GLfloat copySpacePosition[] = { realX,     realHeight, 0.0f, realX, realY, 0.0f, realWidth, realHeight, 0.0f,
				    realWidth, realHeight, 0.0f, realX, realY, 0.0f, realWidth, realY,      0.0f };

    GLfloat texcoordPass[] = { 0.0f, 1.0f, 0.0f, 0.0f, 1.0f, 1.0f, 1.0f, 1.0f, 0.0f, 0.0f, 1.0f, 0.0f };

    GLfloat passSpacePosition[]
	= { -1.0, 1.0, 0.0f, -1.0, -1.0, 0.0f, 1.0, 1.0, 0.0f, 1.0, 1.0, 0.0f, -1.0, -1.0, 0.0f, 1.0, -1.0, 0.0f };

    // bind vertex list to the openGL buffers
    glGenBuffers (1, &this->m_sceneSpacePosition);
    glBindBuffer (GL_ARRAY_BUFFER, this->m_sceneSpacePosition);
    glBufferData (GL_ARRAY_BUFFER, sizeof (sceneSpacePosition), sceneSpacePosition, GL_STATIC_DRAW);

    glGenBuffers (1, &this->m_copySpacePosition);
    glBindBuffer (GL_ARRAY_BUFFER, this->m_copySpacePosition);
    glBufferData (GL_ARRAY_BUFFER, sizeof (copySpacePosition), copySpacePosition, GL_STATIC_DRAW);

    // bind pass' vertex list to the openGL buffers
    glGenBuffers (1, &this->m_passSpacePosition);
    glBindBuffer (GL_ARRAY_BUFFER, this->m_passSpacePosition);
    glBufferData (GL_ARRAY_BUFFER, sizeof (passSpacePosition), passSpacePosition, GL_STATIC_DRAW);

    glGenBuffers (1, &this->m_texcoordCopy);
    glBindBuffer (GL_ARRAY_BUFFER, this->m_texcoordCopy);
    glBufferData (GL_ARRAY_BUFFER, sizeof (texcoordCopy), texcoordCopy, GL_STATIC_DRAW);

    glGenBuffers (1, &this->m_texcoordPass);
    glBindBuffer (GL_ARRAY_BUFFER, this->m_texcoordPass);
    glBufferData (GL_ARRAY_BUFFER, sizeof (texcoordPass), texcoordPass, GL_STATIC_DRAW);

    this->m_hasPuppetMesh = this->loadPuppetMesh (size);

    // compute the center of the image in scene space for rotation
    this->m_sceneCenter
	= glm::vec3 ((this->m_pos.x + this->m_pos.z) / 2.0f, (this->m_pos.y + this->m_pos.w) / 2.0f, 0.0f);

    this->m_modelViewProjectionScreen = this->buildScreenViewProjection ();

    if (this->getImage ().model->passthrough) {
	this->m_modelViewProjectionCopy = this->m_modelViewProjectionScreen;
    } else {
	this->m_modelViewProjectionCopy = glm::ortho<float> (0.0, size.x, 0.0, size.y);
    }
    this->m_modelViewProjectionCopyInverse = glm::inverse (this->m_modelViewProjectionCopy);
    this->m_modelMatrix = glm::mat4 (1.0);
    this->m_viewProjectionMatrix = glm::mat4 (1.0);

    // ensure the input texture is marked as used
    // this makes video playback start if it's not already
    this->m_texture->incrementUsageCount ();
}

CImage::~CImage () {
    this->m_texture->decrementUsageCount ();

    // delete passes first as they depend on the image's data
    for (auto* pass : this->m_passes) {
	delete pass;
    }

    this->m_passes.clear ();

    // free any gl resources
    glDeleteBuffers (1, &this->m_sceneSpacePosition);
    glDeleteBuffers (1, &this->m_copySpacePosition);
    glDeleteBuffers (1, &this->m_passSpacePosition);
    glDeleteBuffers (1, &this->m_texcoordCopy);
    glDeleteBuffers (1, &this->m_texcoordPass);
    if (this->m_puppetSpacePosition != GL_NONE) {
	glDeleteBuffers (1, &this->m_puppetSpacePosition);
    }
    if (this->m_puppetTexCoord != GL_NONE) {
	glDeleteBuffers (1, &this->m_puppetTexCoord);
    }
    if (this->m_puppetIndices != GL_NONE) {
	glDeleteBuffers (1, &this->m_puppetIndices);
    }
}

bool CImage::loadPuppetMesh (const glm::vec2& size) {
    if (!this->getImage ().model->puppet.has_value ()) {
	return false;
    }

    try {
	const auto stream = this->getScene ().getScene ().project.assetLocator->read (*this->getImage ().model->puppet);
	std::vector<char> data { std::istreambuf_iterator<char> (*stream), std::istreambuf_iterator<char> () };

	std::string error;
	auto parsed = PuppetModel::parse (data, error);
	if (!parsed.has_value ()) {
	    sLog.error ("Could not parse puppet ", *this->getImage ().model->puppet, ": ", error);
	    return false;
	}
	this->m_puppetModel = std::move (parsed);
	const auto& model = *this->m_puppetModel;

	const size_t vertexCount = model.positions.size ();
	this->m_puppetRawPositions.clear ();
	this->m_puppetRawPositions.reserve (vertexCount * 3);
	std::vector<GLfloat> texcoords;
	texcoords.reserve (vertexCount * 2);
	for (size_t i = 0; i < vertexCount; i++) {
	    this->m_puppetRawPositions.push_back (model.positions[i].x);
	    this->m_puppetRawPositions.push_back (model.positions[i].y);
	    this->m_puppetRawPositions.push_back (model.positions[i].z);
	    texcoords.push_back (model.uvs[i].x);
	    texcoords.push_back (model.uvs[i].y);
	}

	this->updatePuppetPositionBuffer (size);

	glGenBuffers (1, &this->m_puppetTexCoord);
	glBindBuffer (GL_ARRAY_BUFFER, this->m_puppetTexCoord);
	glBufferData (GL_ARRAY_BUFFER, texcoords.size () * sizeof (GLfloat), texcoords.data (), GL_STATIC_DRAW);

	glGenBuffers (1, &this->m_puppetIndices);
	glBindBuffer (GL_ARRAY_BUFFER, this->m_puppetIndices);
	glBufferData (
	    GL_ARRAY_BUFFER, model.indices.size () * sizeof (GLushort), model.indices.data (), GL_STATIC_DRAW
	);

	this->m_puppetIndexCount = static_cast<GLsizei> (model.indices.size ());

	for (const auto& layer : this->getImage ().animationLayers) {
	    const auto clipId = static_cast<uint32_t> (layer->animation->value->getInt ());
	    const auto* clip = model.findClip (clipId);
	    if (clip == nullptr) {
		sLog.out ("Puppet ", *this->getImage ().model->puppet, ": no clip with id ", clipId, ", layer ignored");
		continue;
	    }
	    this->m_puppetLayers.push_back (PuppetLayerBinding { .clip = clip, .layer = layer.get () });
	}

	sLog.out (
	    "Loaded puppet ", *this->getImage ().model->puppet, " vertices=", vertexCount,
	    " indices=", this->m_puppetIndexCount, " bones=", model.bones.size (), " clips=", model.clips.size (),
	    " layers=", this->m_puppetLayers.size ()
	);

	return true;
    } catch (const std::exception& ex) {
	sLog.error ("Could not load puppet mesh ", *this->getImage ().model->puppet, ": ", ex.what ());
	return false;
    }
}

void CImage::updatePuppetPositionBuffer (const glm::vec2& size) {
    this->uploadPuppetPositions (this->m_puppetRawPositions, size);
}

void CImage::uploadPuppetPositions (const std::vector<GLfloat>& raw, const glm::vec2& size) {
    if (raw.empty ()) {
	return;
    }

    std::vector<GLfloat> positions;
    positions.reserve (raw.size ());
    for (size_t index = 0; index + 2 < raw.size (); index += 3) {
	const float localX = size.x / 2.0f + raw[index];
	const float localY = size.y / 2.0f - raw[index + 1];
	if (this->m_puppetScreenSpace) {
	    const float u = localX / size.x;
	    const float v = localY / size.y;
	    positions.push_back (this->m_pos.x + u * (this->m_pos.z - this->m_pos.x));
	    positions.push_back (this->m_pos.w + v * (this->m_pos.y - this->m_pos.w));
	} else {
	    positions.push_back (localX);
	    positions.push_back (localY);
	}
	positions.push_back (raw[index + 2]);
    }

    if (this->m_puppetSpacePosition == GL_NONE) {
	glGenBuffers (1, &this->m_puppetSpacePosition);
    }
    glBindBuffer (GL_ARRAY_BUFFER, this->m_puppetSpacePosition);
    glBufferData (GL_ARRAY_BUFFER, positions.size () * sizeof (GLfloat), positions.data (), GL_DYNAMIC_DRAW);
}

void CImage::updatePuppetAnimation () {
    if (!this->m_hasPuppetMesh || !this->m_puppetModel.has_value () || this->m_puppetLayers.empty ()
	|| !this->m_puppetModel->hasAnimation ()) {
	return;
    }

    this->m_puppetActiveScratch.clear ();
    for (const auto& binding : this->m_puppetLayers) {
	if (!binding.layer->visible->value->getBool ()) {
	    continue;
	}
	this->m_puppetActiveScratch.push_back (
	    PuppetModel::ActiveLayer {
		.clip = binding.clip,
		.rate = binding.layer->rate->value->getFloat (),
		.blend = binding.layer->blend->value->getFloat (),
	    }
	);
    }

    this->m_puppetModel->evaluateSkinning (
	this->m_puppetActiveScratch, static_cast<double> (g_Time), this->m_puppetSkinMatrices
    );
    this->m_puppetModel->skinPositions (this->m_puppetSkinMatrices, this->m_puppetSkinnedPositions);

    this->m_puppetSkinnedFlat.resize (this->m_puppetSkinnedPositions.size () * 3);
    for (size_t i = 0; i < this->m_puppetSkinnedPositions.size (); i++) {
	this->m_puppetSkinnedFlat[i * 3 + 0] = this->m_puppetSkinnedPositions[i].x;
	this->m_puppetSkinnedFlat[i * 3 + 1] = this->m_puppetSkinnedPositions[i].y;
	this->m_puppetSkinnedFlat[i * 3 + 2] = this->m_puppetSkinnedPositions[i].z;
    }

    this->uploadPuppetPositions (this->m_puppetSkinnedFlat, this->m_size);
}

void CImage::setupPuppetGeometryCallback (Effects::CPass* pass) const {
    pass->setGeometryCallback (
	[this, pass] () {
	    const GLint position = glGetAttribLocation (pass->getProgramID (), "a_Position");
	    const GLint texCoord = glGetAttribLocation (pass->getProgramID (), "a_TexCoord");

	    if (position >= 0) {
		glEnableVertexAttribArray (position);
		glBindBuffer (GL_ARRAY_BUFFER, this->m_puppetSpacePosition);
		glVertexAttribPointer (position, 3, GL_FLOAT, GL_FALSE, 0, nullptr);
	    }

	    if (texCoord >= 0) {
		glEnableVertexAttribArray (texCoord);
		glBindBuffer (GL_ARRAY_BUFFER, this->m_puppetTexCoord);
		glVertexAttribPointer (texCoord, 2, GL_FLOAT, GL_FALSE, 0, nullptr);
	    }
	},
	[this] () {
	    GLint currentFramebuffer = 0;
	    glGetIntegerv (GL_DRAW_FRAMEBUFFER_BINDING, &currentFramebuffer);
	    if (currentFramebuffer != static_cast<GLint> (this->getScene ().getFBO ()->getFramebuffer ())) {
		GLfloat previousClearColor[4] = {};
		glGetFloatv (GL_COLOR_CLEAR_VALUE, previousClearColor);
		glClearColor (0.0f, 0.0f, 0.0f, 0.0f);
		glClear (GL_COLOR_BUFFER_BIT);
		glClearColor (
		    previousClearColor[0], previousClearColor[1], previousClearColor[2], previousClearColor[3]
		);
	    }
	    glBindBuffer (GL_ELEMENT_ARRAY_BUFFER, this->m_puppetIndices);
	    glDrawElements (GL_TRIANGLES, this->m_puppetIndexCount, GL_UNSIGNED_SHORT, nullptr);
	},
	[pass] () {
	    const GLint position = glGetAttribLocation (pass->getProgramID (), "a_Position");
	    const GLint texCoord = glGetAttribLocation (pass->getProgramID (), "a_TexCoord");

	    if (position >= 0) {
		glDisableVertexAttribArray (position);
	    }

	    if (texCoord >= 0) {
		glDisableVertexAttribArray (texCoord);
	    }
	}
    );
}

void CImage::setup () {
    // do not double-init stuff, that's bad!
    if (this->m_initialized) {
	return;
    }

    // TODO: CHECK ORDER OF THINGS, 2419444134'S ID 27 DEPENDS ON 104'S COMPOSITE_A WHEN OUR LAST RENDER IS ON
    // COMPOSITE_B
    // TODO: SUPPORT PASSTHROUGH (IT'S A SHADER)
    if (this->m_image.model->passthrough) {
	// passthrough images without effects are bad, do not draw them
	if (this->m_image.effects.empty ()) {
	    return;
	}

	// Some have attempted to declare effects with visible set to false.
	bool allEffectsInvisible = true;
	for (const auto& cur : this->m_image.effects) {
	    if (cur->visible->value->getBool ()) {
		allEffectsInvisible = false;
		break;
	    }
	}

	if (allEffectsInvisible) {
	    return;
	}
    }

    const auto& debug = this->getScene ().getContext ().getApp ().getContext ().settings.render.debug;

    // copy pass to the composite layer
    for (const auto& cur : this->getImage ().model->material->passes) {
	this->m_passes.push_back (
	    new CPass (*this, std::make_shared<FBOProvider> (this), *cur, std::nullopt, std::nullopt, std::nullopt)
	);
    }

    // prepare the passes list
    if (!debug.baseOnly && !this->getImage ().effects.empty ()) {
	// generate the effects used by this material
	for (const auto& cur : this->m_image.effects) {
	    if (std::find (debug.skipEffects.begin (), debug.skipEffects.end (), static_cast<int> (cur->id))
		!= debug.skipEffects.end ()) {
		continue;
	    }

	    // do not add non-visible effects, this might need some adjustements tho as some effects might not be
	    // visible but affect the output of the image...
	    if (!cur->visible->value->getBool ()) {
		continue;
	    }

	    const size_t passesBefore = this->m_passes.size ();
	    const size_t virtualsBefore = this->m_virtualPassess.size ();

	    try {

		const auto fboProvider = std::make_shared<FBOProvider> (this);

		// create all the fbos for this effect
		for (const auto& fbo : cur->effect->fbos) {
		    fboProvider->create (
			*fbo, this->m_texture->getFlags (), this->getScene ().clampToCap (this->getSize ())
		    );
		}

		// TODO: MAKE USE OF ZIP OPERATOR IN BOOST? WAY OVERKILL JUST FOR THIS...

		auto curEffect = cur->effect->passes.begin ();
		auto endEffect = cur->effect->passes.end ();
		auto curOverride = cur->passOverrides.begin ();
		auto endOverride = cur->passOverrides.end ();

		for (; curEffect != endEffect; ++curEffect) {
		    if (!(*curEffect)->material.has_value ()) {
			if (!(*curEffect)->command.has_value ()) {
			    sLog.error ("Pass without material and command not supported");
			    continue;
			}

			if (!(*curEffect)->source.has_value ()) {
			    sLog.error ("Pass without material and source not supported");
			    continue;
			}

			if (!(*curEffect)->target.has_value ()) {
			    sLog.error ("Pass without material and target not supported");
			    continue;
			}

			if ((*curEffect)->command != Command_Copy) {
			    sLog.error ("Only copy command is supported for pass without material");
			    continue;
			}

			auto virtualPass = std::make_unique<MaterialPass> (MaterialPass {
			    .blending = BlendingMode_Normal,
			    .cullmode = CullingMode_Disable,
			    .depthtest = DepthtestMode_Disabled,
			    .depthwrite = DepthwriteMode_Disabled,
			    .shader = "commands/copy",
			    .textures = { { 0, *(*curEffect)->source } },
			    .combos = {},
			    .constants = {} });

			const auto& config = *this->m_virtualPassess.emplace_back (std::move (virtualPass));

			// build a pass for a copy shader
			this->m_passes.push_back (new CPass (
			    *this, fboProvider, config, std::nullopt, std::nullopt, (*curEffect)->target.value ()
			));
		    } else {
			for (auto& pass : (*curEffect)->material.value ()->passes) {
			    const auto override = curOverride != endOverride
				? **curOverride
				: std::optional<std::reference_wrapper<const ImageEffectPassOverride>> (std::nullopt);
			    const auto target = (*curEffect)->target.has_value ()
				? *(*curEffect)->target
				: std::optional<std::reference_wrapper<std::string>> (std::nullopt);

			    this->m_passes.push_back (
				new CPass (*this, fboProvider, *pass, override, (*curEffect)->binds, target)
			    );
			}

			if (curOverride != endOverride) {
			    ++curOverride;
			}
		    }
		}

	    } catch (const std::exception& e) {
		for (size_t i = passesBefore; i < this->m_passes.size (); i++) {
		    delete this->m_passes[i];
		}
		this->m_passes.resize (passesBefore);
		this->m_virtualPassess.resize (virtualsBefore);
		sLog.error ("Effect '", cur->name, "' failed to build - skipping it: ", e.what ());
	    }
	}
    }

    if (!debug.baseOnly) {
	const auto magentaCompositeTint = findMagentaCompositeTint (this->m_image, debug.skipEffects);
	if (magentaCompositeTint.has_value ()) {
	    auto tintOverride = std::make_unique<ImageEffectPassOverride> (ImageEffectPassOverride {
		.id = -1,
		.combos = {
		    { "BLENDMODE", 30 },
		},
		.constants = {},
		.textures = {},
	    });
	    tintOverride->constants.emplace ("color", UserSettingBuilder::fromValue (magentaCompositeTint.value ()));
	    tintOverride->constants.emplace ("alpha", UserSettingBuilder::fromValue (1.0f));

	    this->m_materials.compatibilityMaterials.emplace_back (
		MaterialParser::load (this->getScene ().getScene ().project, "materials/effects/tint.json")
	    );
	    this->m_materials.compatibilityOverrides.emplace_back (std::move (tintOverride));

	    this->m_passes.push_back (new CPass (
		*this, std::make_shared<FBOProvider> (this),
		**this->m_materials.compatibilityMaterials.back ()->passes.begin (),
		*this->m_materials.compatibilityOverrides.back (), std::nullopt, std::nullopt
	    ));
	}
    }

    // extra render pass if there's any blending to be done
    if (!debug.baseOnly && this->m_image.colorBlendMode->value->getInt () > 0) {
	this->m_materials.colorBlending.material
	    = MaterialParser::load (this->getScene ().getScene ().project, "materials/util/effectpassthrough.json");
	this->m_materials.colorBlending.override = std::make_unique<ImageEffectPassOverride> (ImageEffectPassOverride {
            .id = -1,
            .combos = {
                {"BLENDMODE", this->m_image.colorBlendMode->value->getInt()},
            },
            .constants = {},
            .textures = {},
        });

	this->m_passes.push_back (new CPass (
	    *this, std::make_shared<FBOProvider> (this), **this->m_materials.colorBlending.material->passes.begin (),
	    *this->m_materials.colorBlending.override, std::nullopt, std::nullopt
	));
	static const glm::vec4 s_identityColor4 (1.0f);
	this->m_passes.back ()->addUniform ("g_Color4", &s_identityColor4);
    }

    // if there's more than one pass the blendmode has to be moved from the beginning to the end
    if (this->m_passes.size () > 1) {
	const auto first = this->m_passes.begin ();
	const auto last = this->m_passes.rbegin ();

	(*last)->setBlendingMode ((*first)->getBlendingMode ());
	(*first)->setBlendingMode (BlendingMode_Normal);
    }

    if (this->m_isShape && !this->m_passes.empty ()) {
	this->m_passes.back ()->setBlendingMode (BlendingMode_Additive);
    }

    CRenderable::setup ();

    this->setupPasses ();
    this->m_initialized = true;
}

void CImage::setupPasses () {
    // do a pass on everything and setup proper inputs and values
    std::shared_ptr<const CFBO> drawTo = this->m_currentMainFBO;
    std::shared_ptr<const TextureProvider> asInput = this->getTexture ();
    GLuint texcoord = this->getTexCoordCopy ();

    auto cur = this->m_passes.begin ();
    auto end = this->m_passes.end ();
    bool first = true;
    bool inTargetEffectSequence = false;
    std::shared_ptr<const TextureProvider> effectInput = nullptr;

    for (; cur != end; ++cur) {
	// TODO: PROPERLY CHECK EFFECT'S VISIBILITY AND TAKE IT INTO ACCOUNT
	// TODO: THIS REQUIRES ON-THE-FLY EVALUATION OF EFFECTS VISIBILITY TO FIGURE OUT
	// TODO: WHICH ONE IS THE LAST + A FEW OTHER THINGS
	Effects::CPass* pass = *cur;
	std::shared_ptr<const CFBO> prevDrawTo = drawTo;
	bool writesToTarget = false;
	const bool isFirstPass = first;
	GLuint spacePosition = (isFirstPass)
	    ? (this->m_hasPuppetMesh ? this->m_puppetSpacePosition : this->getCopySpacePosition ())
	    : this->getPassSpacePosition ();
	const glm::mat4* projection
	    = (isFirstPass) ? &this->m_modelViewProjectionCopy : &this->m_modelViewProjectionPass;
	const glm::mat4* inverseProjection
	    = (isFirstPass) ? &this->m_modelViewProjectionCopyInverse : &this->m_modelViewProjectionPassInverse;
	// classic-light frame follows the vertex space chosen above: first passes render
	// image-LOCAL 0..size verts (copy projection); overridden below if this pass gets
	// redirected to the screen
	pass->setClassicLocalFrame (isFirstPass);
	pass->setScreenViewProjectionMatrix (
	    isFirstPass ? &this->m_lweScreenVPComposite : &this->m_modelViewProjectionPass
	);
	first = false;

	if (isFirstPass && this->m_hasPuppetMesh) {
	    pass->setBlendingMode (BlendingMode_Translucent);
	    this->setupPuppetGeometryCallback (pass);
	}

	pass->setModelMatrix (&this->m_modelMatrix);

	writesToTarget = this->configurePassTarget (pass, drawTo, asInput, effectInput, inTargetEffectSequence);
	// determine if it's the last element in the list as this is a screen-copy-like process
	// TODO: PROPERLY CHECK IF THIS IS ALL THAT'S NEEDED
	if (!writesToTarget && this->shouldRenderFinalPass (std::next (cur) == end)) {
	    // TODO: PROPERLY CHECK EFFECT'S VISIBILITY AND TAKE IT INTO ACCOUNT
	    drawTo = this->getScene ().getFBO ();

	    if (this->getImage ().model->passthrough && this->getImage ().model->fullscreen) {
		spacePosition = this->getPassSpacePosition ();
		projection = &this->m_modelViewProjectionPass;
		inverseProjection = &this->m_modelViewProjectionPassInverse;
	    } else {
		spacePosition = this->getSceneSpacePosition ();
		projection = &this->m_modelViewProjectionScreen;
		inverseProjection = &this->m_modelViewProjectionScreenInverse;
	    }

	    pass->setClassicLocalFrame (false);
	    pass->setScreenViewProjectionMatrix (projection);

	    if (isFirstPass && this->m_hasPuppetMesh && !this->m_puppetScreenSpace) {
		this->m_puppetScreenSpace = true;
		this->updatePuppetPositionBuffer (this->m_size);
	    }
	}

	pass->setDestination (drawTo);
	pass->setInput (asInput);
	pass->setPreviousInput (inTargetEffectSequence ? effectInput : nullptr);
	pass->setPosition (spacePosition);
	pass->setTexCoord (texcoord);
	pass->setModelViewProjectionMatrix (projection);
	pass->setModelViewProjectionMatrixInverse (inverseProjection);
	pass->setViewProjectionMatrix (projection);

	static const bool s_passDump = getenv ("LWE_IMGDUMP") != nullptr;
	if (s_passDump) {
	    const char* projName = (projection == &this->m_modelViewProjectionScreen) ? "SCREEN"
		: (projection == &this->m_modelViewProjectionCopy)                    ? "COPY"
										      : "PASS";
	    sLog.out (
		"LWE-PASSDUMP obj=", this->getId (), " pass=", static_cast<const void*> (pass), " proj=", projName,
		" projScale=(", (*projection)[0][0], ",", (*projection)[1][1], ")",
		" pos=", (spacePosition == this->getSceneSpacePosition ()) ? "SCENE" : "LOCAL",
		" drawToScene=", (drawTo == this->getScene ().getFBO ()) ? 1 : 0, " writesToTarget=", writesToTarget
	    );
	}

	texcoord = this->getTexCoordPass ();

	if (writesToTarget) {
	    asInput = drawTo;
	    drawTo = prevDrawTo;
	} else {
	    drawTo = prevDrawTo;
	    this->pinpongFramebuffer (&drawTo, &asInput);
	    inTargetEffectSequence = false;
	    effectInput = nullptr;
	}
    }
}

bool CImage::shouldRenderFinalPass (bool isLastPass) const {
    if (!isLastPass || !this->getImage ().visible->value->getBool ()) {
	return false;
    }

    const auto& debug = this->getScene ().getContext ().getApp ().getContext ().settings.render.debug;
    return !(debug.noSolidFinal && this->getImage ().model->solidlayer);
}

bool CImage::configurePassTarget (
    Effects::CPass* pass, std::shared_ptr<const CFBO>& drawTo, const std::shared_ptr<const TextureProvider>& asInput,
    std::shared_ptr<const TextureProvider>& effectInput, bool& inTargetEffectSequence
) {
    if (!pass->getTarget ().has_value ()) {
	return false;
    }

    const std::string target = pass->getTarget ().value ();
    std::shared_ptr<const CFBO> resolved = pass->getFBOProvider ()->find (target);
    if (resolved == nullptr) {
	resolved = this->getScene ().findFBO (target);
    }
    if (resolved == nullptr) {
	sLog.error (
	    "Pass target FBO '", target, "' could not be resolved for object ", pass->getRenderable ().getId (),
	    " shader=", pass->getPass ().shader
	);
	return false;
    }

    if (!inTargetEffectSequence) {
	effectInput = asInput;
	inTargetEffectSequence = true;
    }
    drawTo = resolved;
    return true;
}

void CImage::pinpongFramebuffer (std::shared_ptr<const CFBO>* drawTo, std::shared_ptr<const TextureProvider>* asInput) {
    // temporarily store FBOs used
    std::shared_ptr<const CFBO> currentMainFBO = this->m_currentMainFBO;
    std::shared_ptr<const CFBO> currentSubFBO = this->m_currentSubFBO;

    if (drawTo != nullptr) {
	*drawTo = currentSubFBO;
    }
    if (asInput != nullptr) {
	*asInput = currentMainFBO;
    }

    // swap the FBOs
    this->m_currentMainFBO = currentSubFBO;
    this->m_currentSubFBO = currentMainFBO;
}

void CImage::render () {
    // do not try to render something that did not initialize successfully
    if (!this->m_initialized) {
	return;
    }

    const bool imgVisible = this->getImage ().visible->value->getBool ();
    if (!imgVisible && !this->getScene ().isCompositeShared (this->getImage ().id)) {
	return;
    }

    glColorMask (true, true, true, true);

    // Always update screen transform (handles rotation + parallax dynamically)
    this->updateScreenSpacePosition ();

    if (this->m_texture != nullptr) {
	const auto animScale = this->getImage ().scale->value->getVec3 ();
	const auto fbo = this->getScene ().getFBO ();
	const float projW = static_cast<float> (this->getScene ().getWidth ());
	const float pxPerUnit
	    = (fbo != nullptr && projW > 0.0f) ? static_cast<float> (fbo->getRealWidth ()) / projW : 1.0f;
	MipResidency::maybeExpand (
	    this->m_texture.get (), std::abs (this->getSize ().x * animScale.x) * pxPerUnit,
	    std::abs (this->getSize ().y * animScale.y) * pxPerUnit,
	    MipResidency::largestOutputDimension (this->getScene ().getContext ())
	);
    }

    this->updatePuppetAnimation ();

#if !NDEBUG
    std::string str = "Image ";

    if (this->getScene ().getScene ().camera.bloom.enabled->value->getBool () && this->getId () == -1) {
	str += "bloom";
    } else {
	str += this->getImage ().name + " (" + std::to_string (this->getId ()) + ", "
	    + this->getImage ().model->material->filename + ")";
    }

    glPushDebugGroup (GL_DEBUG_SOURCE_APPLICATION, 0, -1, str.c_str ());
#endif /* DEBUG */

    auto cur = this->m_passes.begin ();

    for (const auto end = this->m_passes.end (); cur != end; ++cur) {
	if (std::next (cur) == end && imgVisible) {
	    glColorMask (true, true, true, this->getScene ().isRenderingToComposition () ? GL_TRUE : GL_FALSE);
	}

	(*cur)->render ();
    }

#if !NDEBUG
    glPopDebugGroup ();
#endif /* DEBUG */
}

const float& CImage::getBrightness () const { return this->m_image.brightness->value->getFloat (); }

const float& CImage::getUserAlpha () const { return this->m_image.alpha->value->getFloat (); }

const float& CImage::getAlpha () const { return this->m_image.alpha->value->getFloat (); }

const glm::vec3& CImage::getColor () const { return this->m_image.color->value->getVec3 (); }

glm::vec4 CImage::getColor4 () const {
    return { this->m_image.color->value->getVec3 () * this->m_image.brightness->value->getFloat (),
	     this->m_image.alpha->value->getFloat () };
}

const glm::vec3& CImage::getCompositeColor () const { return this->m_image.color->value->getVec3 (); }

glm::vec2 CImage::resolveGeometrySize (float sceneWidth, float sceneHeight, glm::vec3& origin) const {
    glm::vec2 size = this->getSize ();

    if ((size.x == 0.0f || size.y == 0.0f) && this->m_texture != nullptr) {
	size.x = static_cast<float> (this->m_texture->getRealWidth ());
	size.y = static_cast<float> (this->m_texture->getRealHeight ());
    } else if (
	(size.x == 0.0f || size.y == 0.0f) && this->getImage ().model->width.has_value ()
	&& this->getImage ().model->height.has_value ()
    ) {
	size.x = static_cast<float> (this->getImage ().model->width.value ());
	size.y = static_cast<float> (this->getImage ().model->height.value ());
    }

    if (this->getImage ().model->fullscreen) {
	size = { sceneWidth, sceneHeight };
	origin = { sceneWidth / 2.0f, sceneHeight / 2.0f, 0.0f };
    }

    return size;
}

void CImage::updateScenePosition (
    const glm::vec3& origin, const glm::vec2& size, const glm::vec3& scale, float sceneWidth, float sceneHeight
) {
    const glm::vec2 scaledSize = size * glm::vec2 (scale);
    this->m_pos.x = origin.x - (scaledSize.x / 2.0f);
    this->m_pos.w = origin.y + (scaledSize.y / 2.0f);
    this->m_pos.z = origin.x + (scaledSize.x / 2.0f);
    this->m_pos.y = origin.y - (scaledSize.y / 2.0f);

    if (this->getImage ().alignment.find ("top") != std::string::npos) {
	this->m_pos.y -= scaledSize.y / 2.0f;
	this->m_pos.w -= scaledSize.y / 2.0f;
    } else if (this->getImage ().alignment.find ("bottom") != std::string::npos) {
	this->m_pos.y += scaledSize.y / 2.0f;
	this->m_pos.w += scaledSize.y / 2.0f;
    }

    if (this->getImage ().alignment.find ("left") != std::string::npos) {
	this->m_pos.x += scaledSize.x / 2.0f;
	this->m_pos.z += scaledSize.x / 2.0f;
    } else if (this->getImage ().alignment.find ("right") != std::string::npos) {
	this->m_pos.x -= scaledSize.x / 2.0f;
	this->m_pos.z -= scaledSize.x / 2.0f;
    }

    this->m_pos.x -= sceneWidth / 2.0f;
    this->m_pos.y = sceneHeight / 2.0f - this->m_pos.y;
    this->m_pos.z -= sceneWidth / 2.0f;
    this->m_pos.w = sceneHeight / 2.0f - this->m_pos.w;
}

void CImage::uploadGeometryBuffers (const glm::vec2& size) {
    // perspective:true objects sit at their authored z (e.g. 3D Earth at z=-3000);
    // everything else renders on the z=0 plane
    const GLfloat z = this->getImage ().perspective ? this->getImage ().origin->value->getVec3 ().z : 0.0f;
    GLfloat sceneSpacePosition[]
	= { this->m_pos.x, this->m_pos.y, z, this->m_pos.x, this->m_pos.w, z, this->m_pos.z, this->m_pos.y, z,
	    this->m_pos.z, this->m_pos.y, z, this->m_pos.x, this->m_pos.w, z, this->m_pos.z, this->m_pos.w, z };

    float width = 1.0f;
    float height = 1.0f;
    if (this->getTexture () != nullptr && !this->getTexture ()->isAnimated ()
	&& (this->getTexture ()->getTextureWidth (0) != this->getTexture ()->getRealWidth ()
	    || this->getTexture ()->getTextureHeight (0) != this->getTexture ()->getRealHeight ())) {
	width = static_cast<float> (this->getTexture ()->getRealWidth ())
	    / static_cast<float> (this->getTexture ()->getTextureWidth (0));
	height = static_cast<float> (this->getTexture ()->getRealHeight ())
	    / static_cast<float> (this->getTexture ()->getTextureHeight (0));
    }

    float x = 0.0f;
    float y = 0.0f;
    GLfloat realWidth = size.x;
    GLfloat realHeight = size.y;
    GLfloat realX = 0.0f;
    GLfloat realY = 0.0f;

    if (this->getImage ().model->passthrough) {
	width = 1.0f;
	height = 1.0f;
	realX = this->m_pos.x;
	realY = this->m_pos.w;
	realWidth = this->m_pos.z;
	realHeight = this->m_pos.y;

	if (this->getImage ().model->fullscreen) {
	    realX = -1.0f;
	    realY = -1.0f;
	    realWidth = 1.0f;
	    realHeight = 1.0f;
	}
    }

    GLfloat texcoordCopy[] = { x, height, x, y, width, height, width, height, x, y, width, y };
    GLfloat copySpacePosition[] = { realX,     realHeight, 0.0f, realX, realY, 0.0f, realWidth, realHeight, 0.0f,
				    realWidth, realHeight, 0.0f, realX, realY, 0.0f, realWidth, realY,      0.0f };

    glBindBuffer (GL_ARRAY_BUFFER, this->m_sceneSpacePosition);
    glBufferData (GL_ARRAY_BUFFER, sizeof (sceneSpacePosition), sceneSpacePosition, GL_DYNAMIC_DRAW);
    glBindBuffer (GL_ARRAY_BUFFER, this->m_copySpacePosition);
    glBufferData (GL_ARRAY_BUFFER, sizeof (copySpacePosition), copySpacePosition, GL_DYNAMIC_DRAW);
    glBindBuffer (GL_ARRAY_BUFFER, this->m_texcoordCopy);
    glBufferData (GL_ARRAY_BUFFER, sizeof (texcoordCopy), texcoordCopy, GL_DYNAMIC_DRAW);

    this->m_sceneCenter
	= glm::vec3 ((this->m_pos.x + this->m_pos.z) / 2.0f, (this->m_pos.y + this->m_pos.w) / 2.0f, 0.0f);
    this->m_modelViewProjectionCopy = this->getImage ().model->passthrough
	? this->m_modelViewProjectionScreen
	: glm::ortho<float> (0.0, size.x, 0.0, size.y);
    this->m_modelViewProjectionCopyInverse = glm::inverse (this->m_modelViewProjectionCopy);
    this->m_modelMatrix = glm::mat4 (1.0);
}

CImage::ResolvedTransform CImage::updateGeometryBuffers () {
    auto sceneWidth = static_cast<float> (this->getScene ().getWidth ());
    auto sceneHeight = static_cast<float> (this->getScene ().getHeight ());
    const auto transform = this->resolveTransform (this->getImage ());
    glm::vec3 origin = transform.origin;
    const glm::vec3 scale = transform.scale;
    const glm::vec2 size = this->resolveGeometrySize (sceneWidth, sceneHeight, origin);
    const glm::vec2 previousSize = this->m_size;
    this->m_size = size;
    if (this->m_hasPuppetMesh && size != previousSize) {
	this->updatePuppetPositionBuffer (size);
    }

    this->updateScenePosition (origin, size, scale, sceneWidth, sceneHeight);
    this->uploadGeometryBuffers (size);
    if (this->m_isShape) {
	this->applyShapeGeometry (transform);
    }

    static const bool s_imgProbe = getenv ("LWE_IMGPROBE") != nullptr;
    static int s_imgProbeCount = 0;
    if (s_imgProbe && s_imgProbeCount < 600 && ++s_imgProbeCount > 0) {
	const auto& cam = this->getScene ().getCamera ();
	sLog.out (
	    "LWE-IMGPROBE id=", this->getId (), " scene=", sceneWidth, "x", sceneHeight, " cam=", cam.getWidth (), "x",
	    cam.getHeight (), " size=", size.x, "x", size.y, " pos=[", this->m_pos.x, ",", this->m_pos.y, "..",
	    this->m_pos.z, ",", this->m_pos.w, "]"
	);
    }
    return transform;
}

glm::vec3 CImage::toClassicLightSpace (const glm::vec3& litSpacePos) const {
    const glm::vec4 p = this->m_lweMPosFromWorld * glm::vec4 (litSpacePos, 1.0f);
    return { p.x, p.y, p.z };
}

glm::vec3 CImage::toClassicLightSpaceLocal (const glm::vec3& litSpacePos) const {
    const glm::vec3 world = this->toClassicLightSpace (litSpacePos);
    const float spanX = this->m_pos.z - this->m_pos.x;
    const float spanY = this->m_pos.y - this->m_pos.w;
    if (spanX == 0.0f || spanY == 0.0f) {
	return world;
    }

    const float u = (world.x - this->m_pos.x) / spanX;
    const float v = (world.y - this->m_pos.w) / spanY;
    const float unitScale = std::sqrt (std::abs ((this->m_size.x / spanX) * (this->m_size.y / spanY)));
    return { u * this->m_size.x, v * this->m_size.y, world.z * unitScale };
}

float CImage::classicLocalRadianceScale () const {
    // local units = world units / layer scale; 1/d^2 in local units runs scale^2 hot.
    // Compensate with the per-axis unit ratio (abs: mirrored layers have negative spans).
    const float spanX = this->m_pos.z - this->m_pos.x;
    const float spanY = this->m_pos.y - this->m_pos.w;
    if (spanX == 0.0f || spanY == 0.0f || this->m_size.x == 0.0f || this->m_size.y == 0.0f) {
	return 1.0f;
    }
    return std::abs ((this->m_size.x / spanX) * (this->m_size.y / spanY)) * 1.5f;
}

void CImage::applyShapeGeometry (const ResolvedTransform& transform) {
    const auto& effects = this->m_image.effects;
    if (effects.empty () || effects.front ()->passOverrides.empty ()) {
	return;
    }
    const auto& constants = effects.front ()->passOverrides.front ()->constants;
    glm::vec2 pts[4];
    for (int i = 0; i < 4; i++) {
	const auto it = constants.find ("point" + std::to_string (i));
	if (it == constants.end () || !it->second || !it->second->value) {
	    return; // non-quad or differently-authored shape: keep default geometry
	}
	pts[i] = it->second->value->getVec2 ();
    }

    const auto sceneW = static_cast<float> (this->getScene ().getWidth ());
    const auto sceneH = static_cast<float> (this->getScene ().getHeight ());
    const glm::vec3 origin = transform.origin;
    const float ca = std::cos (transform.angle);
    const float sa = std::sin (transform.angle);

    static const bool s_shapeProbe = getenv ("LWE_IMGPROBE") != nullptr;
    static int s_shapeProbeCount = 0;
    if (s_shapeProbe && s_shapeProbeCount < 4 && ++s_shapeProbeCount > 0) {
	sLog.out (
	    "LWE-SHAPEPROBE id=", this->getId (), " origin=(", origin.x, ",", origin.y, ") angle=", transform.angle,
	    " canvas=", sceneW, "x", sceneH
	);
    }

    glm::vec3 corners[4];
    for (int i = 0; i < 4; i++) {
	const glm::vec2 local = sceneH * glm::vec2 (pts[i].x - 0.5f, 0.5f - pts[i].y);
	const glm::vec2 world
	    = glm::vec2 (origin) + glm::vec2 (ca * local.x - sa * local.y, sa * local.x + ca * local.y);
	// authored y-up world -> our centered, y-flipped scene space (same
	// conversion updateScenePosition applies to rectangular layers)
	corners[i] = { world.x - sceneW / 2.0f, sceneH / 2.0f - world.y, 0.0f };
    }

    // reference triangulation (0,2,1)(0,3,2); UVs follow the same order
    const int order[6] = { 0, 2, 1, 0, 3, 2 };
    GLfloat positions[18];
    GLfloat uvs[12];
    for (int v = 0; v < 6; v++) {
	positions[v * 3 + 0] = corners[order[v]].x;
	positions[v * 3 + 1] = corners[order[v]].y;
	positions[v * 3 + 2] = 0.0f;
	uvs[v * 2 + 0] = pts[order[v]].x;
	uvs[v * 2 + 1] = pts[order[v]].y;
    }

    glBindBuffer (GL_ARRAY_BUFFER, this->m_sceneSpacePosition);
    glBufferData (GL_ARRAY_BUFFER, sizeof (positions), positions, GL_STATIC_DRAW);
    glBindBuffer (GL_ARRAY_BUFFER, this->m_texcoordPass);
    glBufferData (GL_ARRAY_BUFFER, sizeof (uvs), uvs, GL_STATIC_DRAW);
    glBindBuffer (GL_ARRAY_BUFFER, GL_NONE);

    static const bool s_bufProbe = getenv ("LWE_IMGPROBE") != nullptr;
    static int s_bufProbeCount = 0;
    if (s_bufProbe && s_bufProbeCount < 2 && ++s_bufProbeCount > 0) {
	GLfloat back[18] = {};
	glBindBuffer (GL_ARRAY_BUFFER, this->m_sceneSpacePosition);
	glGetBufferSubData (GL_ARRAY_BUFFER, 0, sizeof (back), back);
	glBindBuffer (GL_ARRAY_BUFFER, GL_NONE);
	sLog.out (
	    "LWE-SHAPEBUF pos v0=(", back[0], ",", back[1], ") v1=(", back[3], ",", back[4], ") v2=(", back[6], ",",
	    back[7], ") v3=(", back[9], ",", back[10], ") v4=(", back[12], ",", back[13], ") v5=(", back[15], ",",
	    back[16], ")"
	);
	const auto& m = this->m_modelViewProjectionScreen;
	sLog.out (
	    "LWE-SHAPEMVP col0=(", m[0][0], ",", m[0][1], ") col1=(", m[1][0], ",", m[1][1], ") col3=(", m[3][0], ",",
	    m[3][1], ")"
	);
    }
}

glm::mat4 CImage::buildScreenViewProjection () const {
    const auto& cam = this->getScene ().getCamera ();
    if (!this->getImage ().perspective) {
	if (this->getImage ().model->fullscreen || this->getImage ().model->passthrough) {
	    return cam.getScreenProjection ();
	}
	// ortho scenes: lookAt is identity here (editor-viewport state, runtime-inert);
	// perspective scenes: the 3D view belongs to models, never to 2D layers
	return cam.isOrthogonal () ? cam.getScreenProjection () * cam.getLookAt () : cam.getScreenProjection ();
    }
    const float sceneW = cam.getWidth ();
    const float sceneH = cam.getHeight ();
    const float sceneFov = glm::radians (cam.getFov ());
    const float overrideFov = cam.getOverrideFov ();
    const float projFov = overrideFov > 0.0f ? glm::radians (overrideFov) : sceneFov;
    const float eyeZ = (sceneH * 0.5f) / std::tan (projFov * 0.5f);
    const float nearz = std::max (cam.getNearZ (), 1.0f);
    const float farz = std::max (cam.getFarZ (), eyeZ + 10.0f * sceneH);

    const glm::mat4 proj = glm::perspective (projFov, sceneW / sceneH, nearz, farz);
    const glm::mat4 view
	= glm::lookAt (glm::vec3 (0.0f, 0.0f, eyeZ), glm::vec3 (0.0f, 0.0f, 0.0f), glm::vec3 (0.0f, 1.0f, 0.0f));
    return proj * view;
}

void CImage::updateScreenSpacePosition () {
    const ResolvedTransform transform = this->updateGeometryBuffers ();

    const float angle = this->m_isShape ? 0.0f : transform.angle;
    glm::mat4 rotModel = glm::mat4 (1.0f);
    if (angle != 0.0f) {
	rotModel = glm::translate (rotModel, this->m_sceneCenter);
	rotModel = glm::rotate (rotModel, -angle, glm::vec3 (0.0f, 0.0f, 1.0f));
	rotModel = glm::translate (rotModel, -this->m_sceneCenter);
    }

    glm::mat4 mvp = this->buildScreenViewProjection ();

    float lweParX = 0.0f;
    float lweParY = 0.0f;
    if (this->getScene ().getScene ().camera.parallax.enabled
	&& !this->getScene ().getContext ().getApp ().getContext ().settings.mouse.disableparallax) {
	const float parallaxAmount = this->getScene ().getScene ().camera.parallax.amount->value->getFloat ();
	const glm::vec2 depth = this->getImage ().parallaxDepth->value->getVec2 ();
	const glm::vec2* displacement = this->getScene ().getParallaxDisplacement ();
	const float x
	    = -depth.x * parallaxAmount * displacement->x * static_cast<float> (this->getScene ().getWidth ());
	const float y
	    = depth.y * parallaxAmount * displacement->y * static_cast<float> (this->getScene ().getHeight ());
	mvp = glm::translate (mvp, { x, y, 0.0f });
	lweParX = x;
	lweParY = y;
    }

    mvp = mvp * rotModel;

    this->m_modelViewProjectionScreen = mvp;
    this->m_modelViewProjectionScreenInverse = glm::inverse (mvp);

    this->m_lweMPosFromWorld = glm::inverse (glm::translate (glm::mat4 (1.0f), { lweParX, lweParY, 0.0f }) * rotModel);

    {
	const float sx = (this->m_pos.z - this->m_pos.x) / std::max (this->m_size.x, 1.0f);
	const float sy = (this->m_pos.y - this->m_pos.w) / std::max (this->m_size.y, 1.0f);
	glm::mat4 localToScene = glm::translate (glm::mat4 (1.0f), { this->m_pos.x, this->m_pos.w, 0.0f });
	localToScene = glm::scale (localToScene, { sx, sy, 1.0f });
	this->m_lweScreenVPComposite = mvp * localToScene;
    }
    if (this->getImage ().model->passthrough) {
	this->m_modelViewProjectionCopy = this->m_modelViewProjectionScreen;
	this->m_modelViewProjectionCopyInverse = this->m_modelViewProjectionScreenInverse;
    }

    static const bool s_mvpDump = getenv ("LWE_IMGDUMP") != nullptr;
    if (s_mvpDump) {
	static std::set<int> s_dumped;
	if (s_dumped.insert (this->getId ()).second) {
	    const glm::vec4 c0 = mvp * glm::vec4 (this->m_pos.x, this->m_pos.w, 0.0f, 1.0f);
	    const glm::vec4 c1 = mvp * glm::vec4 (this->m_pos.z, this->m_pos.y, 0.0f, 1.0f);
	    sLog.out (
		"LWE-MVPDUMP obj=", this->getId (), " mvp00=", mvp[0][0], " mvp11=", mvp[1][1], " mvp30=", mvp[3][0],
		" mvp31=", mvp[3][1], " ndc=[", c0.x, "..", c1.x, "]x[", c0.y, "..", c1.y,
		"] parallaxOn=", this->getScene ().getScene ().camera.parallax.enabled ? 1 : 0
	    );
	}
    }
}

const Image& CImage::getImage () const { return this->m_image; }

bool CImage::isCompositionLayer () const {
    return this->m_image.model != nullptr && this->m_image.model->filename == "models/util/composelayer.json";
}

bool CImage::copiesCompositionBackground () const { return this->m_image.copyBackground; }

std::shared_ptr<const CFBO> CImage::getCompositionFBO () const { return this->m_compositionFBO; }

glm::vec2 CImage::getSize () const {
    const glm::vec2 authored = this->getImage ().size;

    if (authored.x > 0.0f && authored.y > 0.0f) {
	return authored;
    }

    if (this->m_texture != nullptr) {
	return { this->m_texture->getRealWidth (), this->m_texture->getRealHeight () };
    }

    return authored;
}

GLuint CImage::getSceneSpacePosition () const { return this->m_sceneSpacePosition; }

GLuint CImage::getCopySpacePosition () const { return this->m_copySpacePosition; }

GLuint CImage::getPassSpacePosition () const { return this->m_passSpacePosition; }

GLuint CImage::getTexCoordCopy () const { return this->m_texcoordCopy; }

GLuint CImage::getTexCoordPass () const { return this->m_texcoordPass; }

std::optional<glm::vec3> CImage::cursorLocalPosition (const glm::vec3& worldPosition) const {
    if (!this->m_image.visible->value->getBool ()) {
	return std::nullopt;
    }

    // m_pos = the screen pass's centered-world quad edges (x/z = X extremes, y/w = Y
    // extremes; see updateScenePosition). Rotation is applied by the pass matrix about
    // the quad center, so un-rotate the probe point into the quad's frame first.
    const glm::vec2 center = { (this->m_pos.x + this->m_pos.z) * 0.5f, (this->m_pos.y + this->m_pos.w) * 0.5f };
    const glm::vec2 halfExtent
	= { std::abs (this->m_pos.z - this->m_pos.x) * 0.5f, std::abs (this->m_pos.w - this->m_pos.y) * 0.5f };

    static const bool s_cursorDbg = getenv ("LWE_CURSORDBG") != nullptr;
    static int s_cursorDbgTick = 0;
    if (s_cursorDbg && (s_cursorDbgTick++ % 60) == 0) {
	const auto live = this->resolveTransform (this->getImage ());
	sLog.out (
	    "LWE-CURSORDBG quad id=", this->getId (), " center=", center.x, ",", center.y, " half=", halfExtent.x, ",",
	    halfExtent.y, " liveOrigin=", live.origin.x, ",", live.origin.y, " liveScale=", live.scale.x
	);
    }

    glm::vec2 local = glm::vec2 (worldPosition) - center;
    const float angle = this->m_image.angles->value->getVec3 ().z;
    if (angle != 0.0f) {
	const float c = std::cos (-angle);
	const float s = std::sin (-angle);
	local = { local.x * c - local.y * s, local.x * s + local.y * c };
    }

    if (std::abs (local.x) > halfExtent.x || std::abs (local.y) > halfExtent.y) {
	return std::nullopt;
    }

    return glm::vec3 (local, 0.0f);
}
