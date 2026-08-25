#include "CText.h"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <optional>
#include <ranges>
#include <vector>

#include <ft2build.h>
#include FT_FREETYPE_H

#include <glm/gtc/matrix_transform.hpp>
#include <glm/gtc/type_ptr.hpp>

#include "WallpaperEngine/Data/Model/DynamicValue.h"
#include "WallpaperEngine/Data/Model/Object.h"
#include "WallpaperEngine/Data/Model/UserSetting.h"
#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/Render/Camera.h"
#include "WallpaperEngine/Render/Wallpapers/CScene.h"
#include "WallpaperEngine/Scripting/ScriptEngine.h"

using namespace WallpaperEngine::Render::Objects;

namespace {
// TODO: Phase 2 - load font from wallpaper's materials/fonts/ using AssetLocator
// Phase 1 uses a system font instead of the font shipped by the wallpaper.
// Wallpaper Engine bundles .ttf files in `materials/fonts/`; wiring those in
// is deferred to Phase 2 along with dynamic/scripted text.
const std::vector<std::string> kFontCandidates = {
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
};

const char* kVertexShader = R"glsl(
#version 330 core
layout(location = 0) in vec2 aPos;
layout(location = 1) in vec2 aUV;
uniform mat4 uMVP;
out vec2 vUV;
void main() {
    vUV = aUV;
    gl_Position = uMVP * vec4(aPos, 0.0, 1.0);
}
)glsl";

const char* kFragmentShader = R"glsl(
#version 330 core
in vec2 vUV;
uniform sampler2D uTexture;
uniform vec4 uColor;
out vec4 FragColor;
void main() {
    float coverage = texture(uTexture, vUV).r;
    FragColor = vec4(uColor.rgb, uColor.a * coverage);
}
)glsl";

GLuint compileShader (GLenum type, const char* source) {
    GLuint shader = glCreateShader (type);
    glShaderSource (shader, 1, &source, nullptr);
    glCompileShader (shader);

    GLint status = GL_FALSE;
    glGetShaderiv (shader, GL_COMPILE_STATUS, &status);
    if (status != GL_TRUE) {
	char log[1024];
	glGetShaderInfoLog (shader, sizeof (log), nullptr, log);
	sLog.error ("CText shader compile failed: ", log);
	glDeleteShader (shader);
	return 0;
    }
    return shader;
}
} // namespace

CText::CText (Wallpapers::CScene& scene, const Text& text) :
    CObject (scene, text), ScriptableObject (scene, text), m_text (text) {
    this->registerProperty ("color", *text.color->value);
    this->registerProperty ("alpha", *text.alpha->value);
    this->registerProperty ("origin", *text.origin->value);
    this->registerProperty ("scale", *text.scale->value);
    this->registerProperty ("visible", *text.visible->value);
    this->registerProperty ("pointSize", *text.pointSize->value);
    this->registerProperty ("text", *text.text->value);
    this->registerProperty ("pointSize", *text.pointSize->value);
}

CText::~CText () {
    if (m_layerHandle != Scripting::kInvalidLayerHandle) {
	this->getScene ().getScriptEngine ().destroyLayer (m_layerHandle);
	m_layerHandle = Scripting::kInvalidLayerHandle;
    }
    if (m_vbo != 0) {
	glDeleteBuffers (1, &m_vbo);
    }
    if (m_vao != 0) {
	glDeleteVertexArrays (1, &m_vao);
    }
    if (m_program != 0) {
	glDeleteProgram (m_program);
    }
    if (m_texture != 0) {
	glDeleteTextures (1, &m_texture);
    }
    if (m_ftFace != nullptr) {
	FT_Done_Face (m_ftFace);
    }
    if (m_ftLibrary != nullptr) {
	FT_Done_FreeType (m_ftLibrary);
    }
}

void CText::setup () {
    const bool scripted = m_text.text->value->getScriptSource ().has_value ();
    const auto& text = m_text.text->value->getString ();

    // Nothing to render and no script to produce text later -> bail.
    if (text.empty () && !scripted) {
	return;
    }

    if (!initFreeType ()) {
	return;
    }

    if (!loadEmbeddedFont () && !loadSystemFont ()) {
	return;
    }

    m_lastPixelSize = computeEffectivePixelSize ();
    FT_Set_Pixel_Sizes (m_ftFace, 0, static_cast<FT_UInt> (m_lastPixelSize));

    buildShader ();
    // Scripted text may have an empty placeholder; use a single space so the
    // glyph texture has non-zero dimensions until the script produces a value.
    rebuildTextureFrom (text.empty () ? std::string (" ") : text);

    if (scripted) {
	initScriptLayer ();
    }

    m_valid = m_texture != 0 && m_program != 0 && m_vao != 0;
}

bool CText::initFreeType () {
    if (FT_Init_FreeType (&m_ftLibrary) == 0) {
	return true;
    }
    sLog.error ("CText: FT_Init_FreeType failed for object ", m_text.name);
    return false;
}

bool CText::loadEmbeddedFont () {
    // Wallpapers packed in .pkg don't expose physical paths, so we read the font
    // into memory and use FT_New_Memory_Face. m_fontData must outlive the face.
    // `systemfont_*` references signal "use a system font"; let the fallback handle them.
    if (m_text.font.empty () || m_text.font.rfind ("systemfont_", 0) == 0) {
	return false;
    }

    try {
	auto stream = getAssetLocator ().read (m_text.font);
	stream->seekg (0, std::ios::end);
	const auto size = stream->tellg ();
	stream->seekg (0, std::ios::beg);
	m_fontData.resize (static_cast<size_t> (size));
	stream->read (reinterpret_cast<char*> (m_fontData.data ()), size);

	if (FT_New_Memory_Face (
		m_ftLibrary, m_fontData.data (), static_cast<FT_Long> (m_fontData.size ()), 0, &m_ftFace
	    )
	    == 0) {
	    return true;
	}

	sLog.error ("CText: FT_New_Memory_Face failed for '", m_text.font, "', falling back to system font");
    } catch (const std::exception& e) {
	sLog.error ("CText: cannot read font '", m_text.font, "': ", e.what (), ", falling back to system font");
    }

    m_fontData.clear ();
    return false;
}

bool CText::loadSystemFont () {
    std::string fontPath;
    for (const auto& candidate : kFontCandidates) {
	if (std::filesystem::exists (candidate)) {
	    fontPath = candidate;
	    break;
	}
    }
    if (fontPath.empty ()) {
	sLog.error ("CText: no usable system font found");
	return false;
    }
    if (FT_New_Face (m_ftLibrary, fontPath.c_str (), 0, &m_ftFace) != 0) {
	sLog.error ("CText: FT_New_Face failed for ", fontPath);
	return false;
    }
    return true;
}

unsigned int CText::computeEffectivePixelSize () const {
    const float sceneH = static_cast<float> (this->getScene ().getHeight ());
    const float px = m_text.pointSize->value->getFloat () * (4.0f / 3.0f) * (sceneH / 1080.0f);
    return std::max<unsigned int> (1u, static_cast<unsigned int> (std::min (px, 512.0f)));
}

void CText::initScriptLayer () {
    const auto& script = m_text.text->value->getScriptSource ();

    if (!script.has_value ()) {
	return;
    }

    m_layerHandle = this->getScene ().getScriptEngine ().createLayerScript (
	*script, m_text.text->value->getProperties (), m_text.text->value->getString ()
    );

    if (m_layerHandle == Scripting::kInvalidLayerHandle) {
	sLog.error ("CText: createLayerScript failed for '", m_text.name, "'");
    }
}

namespace {
uint32_t nextUtf8Codepoint (const std::string& text, size_t& offset) {
    const auto lead = static_cast<uint8_t> (text[offset]);
    int continuations;
    uint32_t code;

    if (lead < 0x80) {
	continuations = 0;
	code = lead;
    } else if ((lead & 0xE0) == 0xC0) {
	continuations = 1;
	code = lead & 0x1Fu;
    } else if ((lead & 0xF0) == 0xE0) {
	continuations = 2;
	code = lead & 0x0Fu;
    } else if ((lead & 0xF8) == 0xF0) {
	continuations = 3;
	code = lead & 0x07u;
    } else {
	offset++;
	return 0xFFFD;
    }

    if (offset + continuations >= text.size ()) {
	offset++;
	return 0xFFFD;
    }

    for (int i = 1; i <= continuations; i++) {
	const auto cont = static_cast<uint8_t> (text[offset + i]);
	if ((cont & 0xC0) != 0x80) {
	    offset++;
	    return 0xFFFD;
	}
	code = (code << 6) | (cont & 0x3Fu);
    }

    offset += continuations + 1;
    return code;
}

glm::vec2 computeTextAlignmentOffset (
    const std::string& horizontalAlign, const std::string& verticalAlign, const glm::vec4& glyphBounds,
    const float ascender, const float descender, const float lineSpacing, const size_t lineCount
) {
    const float width = glyphBounds.z - glyphBounds.x;
    const float boundsCenterY = (glyphBounds.y + glyphBounds.w) * 0.5f;
    const float followingRows = static_cast<float> (lineCount > 0 ? lineCount - 1 : 0);

    float x = 0.0f;
    if (horizontalAlign == "left") {
	x = width * 0.5f;
    } else if (horizontalAlign == "right") {
	x = width * -0.5f;
    }

    float verticalReference;
    if (verticalAlign == "top") {
	verticalReference = ascender;
    } else if (verticalAlign == "bottom") {
	verticalReference = descender - followingRows * lineSpacing;
    } else {
	verticalReference = (ascender - followingRows * lineSpacing) * 0.5f;
    }

    return { x, boundsCenterY - verticalReference };
}
} // namespace

void CText::rebuildTextureFrom (const std::string& text) {
    // Two-pass rasterization: first measure the bounding box, then rasterize
    // every glyph into a single grayscale bitmap. Phase 1 renders one line -
    // multi-line wrapping, alignment, and padding come with Phase 2.
    //
    // Safe to call repeatedly: GL handles (texture, VAO, VBO) are reused when
    // already allocated, so dynamic/scripted text can regenerate the glyph
    // bitmap every time the rendered string changes without leaking.
    FT_GlyphSlot slot = m_ftFace->glyph;

    std::string effectiveText = text;
    if (m_text.limitwidth && m_text.maxwidth > 0.0f) {
	const auto advanceOf = [&] (const std::string& s) {
	    int w = 0;
	    for (size_t offset = 0; offset < s.size ();) {
		const auto code = static_cast<FT_ULong> (nextUtf8Codepoint (s, offset));
		if (FT_Load_Char (m_ftFace, code, FT_LOAD_DEFAULT) == 0) {
		    w += static_cast<int> (slot->advance.x >> 6);
		}
	    }
	    return w;
	};
	const auto maxW = static_cast<int> (m_text.maxwidth);
	if (advanceOf (text) > maxW) {
	    const std::string ellipsis = m_text.limituseellipsis ? "..." : "";
	    const int budget = maxW - advanceOf (ellipsis);
	    std::string cut;
	    int cutW = 0;
	    for (size_t offset = 0; offset < text.size ();) {
		const size_t sequenceStart = offset;
		const auto code = static_cast<FT_ULong> (nextUtf8Codepoint (text, offset));
		if (FT_Load_Char (m_ftFace, code, FT_LOAD_DEFAULT) != 0) {
		    continue;
		}
		const int adv = static_cast<int> (slot->advance.x >> 6);
		if (cutW + adv > budget) {
		    break;
		}
		cutW += adv;
		cut.append (text, sequenceStart, offset - sequenceStart);
	    }
	    effectiveText = cut + ellipsis;
	}
    }

    int penX = 0;
    int maxAscent = 0;
    int maxDescent = 0;

    for (size_t offset = 0; offset < effectiveText.size ();) {
	const auto code = static_cast<FT_ULong> (nextUtf8Codepoint (effectiveText, offset));
	if (FT_Load_Char (m_ftFace, code, FT_LOAD_RENDER) != 0) {
	    continue;
	}
	penX += slot->advance.x >> 6;
	maxAscent = std::max (maxAscent, slot->bitmap_top);
	maxDescent = std::max (maxDescent, static_cast<int> (slot->bitmap.rows) - slot->bitmap_top);
    }

    const int width = std::max (1, penX);
    const int height = std::max (1, maxAscent + maxDescent);
    std::vector<uint8_t> pixels (static_cast<size_t> (width) * height, 0);

    penX = 0;
    for (size_t offset = 0; offset < effectiveText.size ();) {
	const auto code = static_cast<FT_ULong> (nextUtf8Codepoint (effectiveText, offset));
	if (FT_Load_Char (m_ftFace, code, FT_LOAD_RENDER) != 0) {
	    continue;
	}

	const auto& bmp = slot->bitmap;
	const int originX = penX + slot->bitmap_left;
	const int originY = maxAscent - slot->bitmap_top;

	for (unsigned int row = 0; row < bmp.rows; ++row) {
	    for (unsigned int col = 0; col < bmp.width; ++col) {
		const int dstX = originX + static_cast<int> (col);
		const int dstY = originY + static_cast<int> (row);
		if (dstX < 0 || dstX >= width || dstY < 0 || dstY >= height) {
		    continue;
		}
		pixels[static_cast<size_t> (dstY) * width + dstX] = bmp.buffer[row * bmp.pitch + col];
	    }
	}

	penX += slot->advance.x >> 6;
    }

    const bool firstUpload = (m_texture == 0);
    if (firstUpload) {
	glGenTextures (1, &m_texture);
    }
    glBindTexture (GL_TEXTURE_2D, m_texture);
    glPixelStorei (GL_UNPACK_ALIGNMENT, 1);
    glTexImage2D (GL_TEXTURE_2D, 0, GL_RED, width, height, 0, GL_RED, GL_UNSIGNED_BYTE, pixels.data ());
    if (firstUpload) {
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
	glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    }

    m_textureSize = { width, height };
    m_quadSize = { static_cast<float> (width), static_cast<float> (height) };
    m_lastRenderedText = text;

    {
	const float ascender = static_cast<float> (m_ftFace->size->metrics.ascender >> 6);
	const float descender = static_cast<float> (m_ftFace->size->metrics.descender >> 6);
	const float lineSpacing = static_cast<float> (m_ftFace->size->metrics.height >> 6);
	const glm::vec4 glyphBounds
	    = { 0.0f, static_cast<float> (-maxDescent), static_cast<float> (width), static_cast<float> (maxAscent) };
	const glm::vec2 aligned = computeTextAlignmentOffset (
	    m_text.alignment, m_text.verticalalign, glyphBounds, ascender, descender, lineSpacing, 1
	);
	m_alignOffset = { aligned.x, -aligned.y };
    }

    uploadQuadVertices ();
}

void CText::buildShader () {
    GLuint vs = compileShader (GL_VERTEX_SHADER, kVertexShader);
    GLuint fs = compileShader (GL_FRAGMENT_SHADER, kFragmentShader);
    if (vs == 0 || fs == 0) {
	if (vs) {
	    glDeleteShader (vs);
	}
	if (fs) {
	    glDeleteShader (fs);
	}
	return;
    }

    m_program = glCreateProgram ();
    glAttachShader (m_program, vs);
    glAttachShader (m_program, fs);
    glLinkProgram (m_program);
    glDeleteShader (vs);
    glDeleteShader (fs);

    GLint status = GL_FALSE;
    glGetProgramiv (m_program, GL_LINK_STATUS, &status);
    if (status != GL_TRUE) {
	char log[1024];
	glGetProgramInfoLog (m_program, sizeof (log), nullptr, log);
	sLog.error ("CText program link failed: ", log);
	glDeleteProgram (m_program);
	m_program = 0;
	return;
    }

    m_uMVP = glGetUniformLocation (m_program, "uMVP");
    m_uColor = glGetUniformLocation (m_program, "uColor");
    m_uTexture = glGetUniformLocation (m_program, "uTexture");
}

void CText::uploadQuadVertices () {
    // Quad centered at the origin, sized in pixels. Scene-space placement is
    // done via the model matrix using the object's origin/scale. VBO contents
    // are re-uploaded whenever the glyph bitmap is rebuilt so the quad always
    // matches the current texture dimensions.
    const float hx = m_quadSize.x * 0.5f;
    const float hy = m_quadSize.y * 0.5f;
    const float ox = m_alignOffset.x;
    const float oy = m_alignOffset.y;
    // With vflip=true (Wayland/GLFW), GL y- = screen top. So the quad bottom (y=-hy,
    // lower GL y) appears at screen top. UV.v=0 here = FT glyph top -> shows at screen top
    const float verts[] = {
	// pos        // uv
	-hx + ox, -hy + oy, 0.0f, 0.0f, hx + ox, -hy + oy, 1.0f, 0.0f, hx + ox,  hy + oy, 1.0f, 1.0f,
	-hx + ox, -hy + oy, 0.0f, 0.0f, hx + ox, hy + oy,  1.0f, 1.0f, -hx + ox, hy + oy, 0.0f, 1.0f,
    };

    const bool firstUpload = (m_vao == 0);
    if (firstUpload) {
	glGenVertexArrays (1, &m_vao);
	glGenBuffers (1, &m_vbo);
    }
    glBindVertexArray (m_vao);
    glBindBuffer (GL_ARRAY_BUFFER, m_vbo);
    glBufferData (GL_ARRAY_BUFFER, sizeof (verts), verts, GL_DYNAMIC_DRAW);
    if (firstUpload) {
	glEnableVertexAttribArray (0);
	glVertexAttribPointer (0, 2, GL_FLOAT, GL_FALSE, 4 * sizeof (float), reinterpret_cast<void*> (0));
	glEnableVertexAttribArray (1);
	glVertexAttribPointer (
	    1, 2, GL_FLOAT, GL_FALSE, 4 * sizeof (float), reinterpret_cast<void*> (2 * sizeof (float))
	);
    }
    glBindVertexArray (0);
}

void CText::render () {
    if (!m_valid) {
	return;
    }
    if (!m_text.visible->value->getBool ()) {
	return;
    }

#if !NDEBUG
    std::string str = "Text " + this->getObject ().name + " (" + std::to_string (this->getObject ().id) + ")";
    glPushDebugGroup (GL_DEBUG_SOURCE_APPLICATION, 0, -1, str.c_str ());
#endif /* DEBUG */
    std::string renderedText = m_lastRenderedText;
    if (m_layerHandle != Scripting::kInvalidLayerHandle) {
	auto& se = this->getScene ().getScriptEngine ();
	se.tickLayer (
	    m_layerHandle, static_cast<double> (getScene ().getTime ()),
	    static_cast<double> (getScene ().getDeltaTime ()), static_cast<double> (getScene ().getFps ())
	);
	const std::string current = se.layerText (m_layerHandle);
	renderedText = current.empty () ? std::string (" ") : current;
    }

    const unsigned int pixelSize = computeEffectivePixelSize ();
    if (pixelSize != m_lastPixelSize) {
	m_lastPixelSize = pixelSize;
	FT_Set_Pixel_Sizes (m_ftFace, 0, static_cast<FT_UInt> (m_lastPixelSize));
	rebuildTextureFrom (renderedText);
    } else if (renderedText != m_lastRenderedText) {
	rebuildTextureFrom (renderedText);
    }

    const glm::vec4 color = m_text.color->value->getVec4 ();
    const float alpha = m_text.alpha->value->getFloat ();
    const glm::vec3 scale = m_text.scale->value->getVec3 ();

    glm::vec3 origin = m_text.origin->value->getVec3 ();
    {
	std::optional<int> parentId = m_text.parent;
	for (int depth = 0; parentId.has_value () && depth < 8; depth++) {
	    const auto parent = std::ranges::find_if (this->getScene ().getScene ().objects, [&] (const auto& o) {
		return o->id == *parentId;
	    });
	    if (parent == this->getScene ().getScene ().objects.end ()) {
		break;
	    }
	    const glm::vec3 pOrigin = (*parent)->origin->value->getVec3 ();
	    const glm::vec3 pScale = (*parent)->groupScale->value->getVec3 ();
	    const float pAngle = (*parent)->groupAngles->value->getVec3 ().z;
	    const float c = std::cos (pAngle), sn = std::sin (pAngle);
	    const glm::vec2 scaled = { origin.x * pScale.x, origin.y * pScale.y };
	    origin = { pOrigin.x + scaled.x * c - scaled.y * sn, pOrigin.y + scaled.x * sn + scaled.y * c,
		       pOrigin.z + origin.z };
	    parentId = (*parent)->parent;
	}
    }

    static const bool s_textDump = getenv ("LWE_LIGHTDUMP") != nullptr;
    static int s_textDumpCount = 0;
    if (s_textDump && s_textDumpCount < 8) {
	s_textDumpCount++;
	sLog.out (
	    "LWE-TEXTDUMP id=", this->getId (), " resolvedOrigin=(", origin.x, ",", origin.y, ",", origin.z,
	    ") parent=", m_text.parent.has_value () ? *m_text.parent : -1, " quadW=", m_quadSize.x,
	    " maxwidth=", m_text.maxwidth, " limitwidth=", m_text.limitwidth, " text='", m_lastRenderedText, "'"
	);
    }

    const float scene_w = getScene ().getCamera ().getWidth ();
    const float scene_h = getScene ().getCamera ().getHeight ();
    const glm::vec3 gl_origin = {
	origin.x - scene_w * 0.5f,
	scene_h * 0.5f - origin.y,
	origin.z,
    };

    glm::mat4 model = glm::translate (glm::mat4 (1.0f), gl_origin);
    model = glm::scale (model, scale);

    const auto& cam = getScene ().getCamera ();
    const glm::mat4 view = cam.isOrthogonal () ? cam.getLookAt () : glm::mat4 (1.0f);
    const glm::mat4 mvp = cam.getScreenProjection () * view * model;

    glDisable (GL_DEPTH_TEST);
    glDisable (GL_CULL_FACE);
    const auto& sceneFBO = *this->getScene ().getFBO ();
    glBindFramebuffer (GL_FRAMEBUFFER, sceneFBO.getFramebuffer ());
    glViewport (
	0, 0, static_cast<GLsizei> (sceneFBO.getRealWidth ()), static_cast<GLsizei> (sceneFBO.getRealHeight ())
    );
    glColorMask (true, true, true, false);

    glEnable (GL_BLEND);
    glBlendFunc (GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);

    glUseProgram (m_program);
    glUniformMatrix4fv (m_uMVP, 1, GL_FALSE, glm::value_ptr (mvp));
    glUniform4f (m_uColor, color.r, color.g, color.b, color.a * alpha);

    glActiveTexture (GL_TEXTURE0);
    glBindTexture (GL_TEXTURE_2D, m_texture);
    glUniform1i (m_uTexture, 0);

    glBindVertexArray (m_vao);
    glDrawArrays (GL_TRIANGLES, 0, 6);
    glBindVertexArray (0);
#if !NDEBUG
    glPopDebugGroup ();
#endif /* DEBUG */
}
