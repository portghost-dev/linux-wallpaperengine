#include "OverlayLabel.h"
#include "WallpaperEngine/Logging/Log.h"

#include <GL/glew.h>

#include <ft2build.h>
#include FT_FREETYPE_H

#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <vector>

using namespace WallpaperEngine::Render::OverlayLabel;

namespace {
const char* kFonts[] = {
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
};

// heading size for a 2160 px tall viewport, smaller viewports scale it down; lines after the
// first draw at kBodyScale of it. LWE_OVERLAY_SIZE overrides the size, LWE_OVERLAY_FONT the face.
constexpr unsigned int kDefaultPixelSize = 40;
constexpr unsigned int kMinPixelSize = 8;
constexpr unsigned int kMaxPixelSize = 200;
constexpr float kBodyScale = 2.0f / 3.0f;
constexpr float kReferenceHeight = 2160.0f;
constexpr float kMinScale = 0.5f;
constexpr int kMarginX = 14;
constexpr int kMarginY = 12;

const char* kVert = R"glsl(
#version 330 core
layout(location = 0) in vec2 aPos;
layout(location = 1) in vec2 aUV;
uniform vec2 uViewport;
uniform vec2 uOffset;
uniform float uScale;
out vec2 vUV;
void main() {
    // pixel space, origin top-left -> NDC
    vec2 p = (aPos * uScale + uOffset) / uViewport * 2.0 - 1.0;
    gl_Position = vec4(p.x, -p.y, 0.0, 1.0);
    vUV = aUV;
}
)glsl";

const char* kFrag = R"glsl(
#version 330 core
in vec2 vUV;
uniform sampler2D uTex;
uniform vec4 uColor;
out vec4 color;
void main() {
    color = vec4(uColor.rgb, uColor.a * texture(uTex, vUV).r);
}
)glsl";

struct State {
    Settings settings;
    bool envRead = false;
    bool dirty = false;
    bool glAttempted = false;
    bool glReady = false;
    bool textReady = false;
    FT_Library library = nullptr;
    FT_Face face = nullptr;
    bool fontAttempted = false;
    unsigned int pixelSize = kDefaultPixelSize;
    GLuint program = 0;
    GLuint vao = 0;
    GLuint vbo = 0;
    GLuint texture = 0;
    int width = 0;
    int height = 0;
    GLint uViewport = -1;
    GLint uOffset = -1;
    GLint uScale = -1;
    GLint uColor = -1;
};

State g_state;

GLuint compile (const GLenum type, const char* src) {
    const GLuint shader = glCreateShader (type);
    glShaderSource (shader, 1, &src, nullptr);
    glCompileShader (shader);
    GLint ok = GL_FALSE;
    glGetShaderiv (shader, GL_COMPILE_STATUS, &ok);
    if (ok != GL_TRUE) {
	glDeleteShader (shader);
	return 0;
    }
    return shader;
}

/** the process keeps the face open; a missing font is remembered and never retried */
bool loadFont () {
    if (g_state.fontAttempted) {
	return g_state.face != nullptr;
    }
    g_state.fontAttempted = true;
    if (FT_Init_FreeType (&g_state.library) != 0) {
	return false;
    }
    if (const char* size = getenv ("LWE_OVERLAY_SIZE"); size != nullptr && size[0] != '\0') {
	g_state.pixelSize
	    = std::clamp (static_cast<unsigned int> (std::strtoul (size, nullptr, 10)), kMinPixelSize, kMaxPixelSize);
    }
    std::vector<const char*> candidates;
    if (const char* font = getenv ("LWE_OVERLAY_FONT"); font != nullptr && font[0] != '\0') {
	candidates.push_back (font);
    }
    candidates.insert (candidates.end (), std::begin (kFonts), std::end (kFonts));
    for (const auto* candidate : candidates) {
	if (std::filesystem::exists (candidate) && FT_New_Face (g_state.library, candidate, 0, &g_state.face) == 0) {
	    return true;
	}
	g_state.face = nullptr;
    }
    return false;
}

std::vector<std::string> splitLines (const std::string& text) {
    std::vector<std::string> lines;
    size_t start = 0;
    while (start <= text.size ()) {
	const size_t end = text.find ('\n', start);
	lines.push_back (text.substr (start, end == std::string::npos ? std::string::npos : end - start));
	if (end == std::string::npos) {
	    break;
	}
	start = end + 1;
    }
    return lines;
}

int lineWidth (const std::string& line) {
    const FT_GlyphSlot slot = g_state.face->glyph;
    int penX = 0;
    for (const char c : line) {
	if (FT_Load_Char (g_state.face, static_cast<FT_ULong> (static_cast<unsigned char> (c)), FT_LOAD_RENDER) != 0) {
	    continue;
	}
	penX += static_cast<int> (slot->advance.x >> 6);
    }
    return penX;
}

bool rasterize (const std::string& text, std::vector<uint8_t>& out, int& w, int& h) {
    if (!loadFont ()) {
	return false;
    }
    const FT_Face face = g_state.face;
    const FT_GlyphSlot slot = face->glyph;
    const std::vector<std::string> lines = splitLines (text);
    const unsigned int bodySize = std::max (kMinPixelSize, static_cast<unsigned int> (g_state.pixelSize * kBodyScale));
    auto sizeOf = [&] (const size_t index) { return index == 0 ? g_state.pixelSize : bodySize; };

    int maxWidth = 0;
    int total = 2;
    std::vector<int> heights;
    std::vector<int> ascents;
    for (size_t i = 0; i < lines.size (); i++) {
	FT_Set_Pixel_Sizes (face, 0, sizeOf (i));
	maxWidth = std::max (maxWidth, lineWidth (lines[i]));
	heights.push_back (static_cast<int> (face->size->metrics.height >> 6));
	ascents.push_back (static_cast<int> (face->size->metrics.ascender >> 6));
	total += heights.back ();
    }
    w = maxWidth + 2;
    h = total;
    if (w <= 2 || h <= 2) {
	return false;
    }
    out.assign (static_cast<size_t> (w) * h, 0);

    int top = 1;
    for (size_t i = 0; i < lines.size (); i++) {
	FT_Set_Pixel_Sizes (face, 0, sizeOf (i));
	const int baseline = top + ascents[i];
	int penX = 1;
	for (const char c : lines[i]) {
	    if (FT_Load_Char (face, static_cast<FT_ULong> (static_cast<unsigned char> (c)), FT_LOAD_RENDER) != 0) {
		continue;
	    }
	    const FT_Bitmap& bm = slot->bitmap;
	    const int x0 = penX + slot->bitmap_left;
	    const int y0 = baseline - slot->bitmap_top;
	    for (unsigned int row = 0; row < bm.rows; row++) {
		for (unsigned int col = 0; col < bm.width; col++) {
		    const int x = x0 + static_cast<int> (col);
		    const int y = y0 + static_cast<int> (row);
		    if (x >= 0 && x < w && y >= 0 && y < h) {
			const uint8_t v = bm.buffer[row * bm.pitch + col];
			auto& dst = out[static_cast<size_t> (y) * w + x];
			dst = std::max (dst, v);
		    }
		}
	    }
	    penX += static_cast<int> (slot->advance.x >> 6);
	}
	top += heights[i];
    }
    return true;
}

/** one-shot GL setup; failure is remembered so a broken driver never retries per frame */
void init () {
    g_state.glAttempted = true;

    const GLuint vs = compile (GL_VERTEX_SHADER, kVert);
    const GLuint fs = compile (GL_FRAGMENT_SHADER, kFrag);
    if (vs == 0 || fs == 0) {
	sLog.error ("OverlayLabel: shader compile failed; overlay disabled");
	return;
    }
    g_state.program = glCreateProgram ();
    glAttachShader (g_state.program, vs);
    glAttachShader (g_state.program, fs);
    glLinkProgram (g_state.program);
    glDeleteShader (vs);
    glDeleteShader (fs);
    GLint ok = GL_FALSE;
    glGetProgramiv (g_state.program, GL_LINK_STATUS, &ok);
    if (ok != GL_TRUE) {
	sLog.error ("OverlayLabel: shader link failed; overlay disabled");
	return;
    }
    g_state.uViewport = glGetUniformLocation (g_state.program, "uViewport");
    g_state.uOffset = glGetUniformLocation (g_state.program, "uOffset");
    g_state.uScale = glGetUniformLocation (g_state.program, "uScale");
    g_state.uColor = glGetUniformLocation (g_state.program, "uColor");

    glGenTextures (1, &g_state.texture);
    glBindTexture (GL_TEXTURE_2D, g_state.texture);
    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);

    glGenVertexArrays (1, &g_state.vao);
    glGenBuffers (1, &g_state.vbo);
    glBindVertexArray (g_state.vao);
    glBindBuffer (GL_ARRAY_BUFFER, g_state.vbo);
    glBufferData (GL_ARRAY_BUFFER, 6 * 4 * sizeof (float), nullptr, GL_DYNAMIC_DRAW);
    glEnableVertexAttribArray (0);
    glVertexAttribPointer (0, 2, GL_FLOAT, GL_FALSE, 4 * sizeof (float), nullptr);
    glEnableVertexAttribArray (1);
    glVertexAttribPointer (1, 2, GL_FLOAT, GL_FALSE, 4 * sizeof (float), reinterpret_cast<void*> (2 * sizeof (float)));
    glBindVertexArray (0);

    g_state.glReady = true;
}

/** rasterizes the current text into the texture and sizes the quad to it */
void refresh () {
    g_state.dirty = false;
    g_state.textReady = false;

    std::vector<uint8_t> bitmap;
    if (!rasterize (g_state.settings.text, bitmap, g_state.width, g_state.height)) {
	sLog.error ("OverlayLabel: rasterization failed; overlay hidden until the text changes");
	return;
    }

    glBindTexture (GL_TEXTURE_2D, g_state.texture);
    glPixelStorei (GL_UNPACK_ALIGNMENT, 1);
    glTexImage2D (GL_TEXTURE_2D, 0, GL_R8, g_state.width, g_state.height, 0, GL_RED, GL_UNSIGNED_BYTE, bitmap.data ());

    const float wf = static_cast<float> (g_state.width);
    const float hf = static_cast<float> (g_state.height);
    const float quad[] = {
	0.0f, 0.0f, 0.0f, 0.0f, wf, 0.0f, 1.0f, 0.0f, 0.0f, hf, 0.0f, 1.0f,
	0.0f, hf,   0.0f, 1.0f, wf, 0.0f, 1.0f, 0.0f, wf,   hf, 1.0f, 1.0f,
    };
    glBindBuffer (GL_ARRAY_BUFFER, g_state.vbo);
    glBufferSubData (GL_ARRAY_BUFFER, 0, sizeof (quad), quad);

    sLog.out ("OverlayLabel: active (", g_state.width, "x", g_state.height, " label)");
    g_state.textReady = true;
}

/** LWE_OVERLAY_TEXT seeds the text once, unless a command set it first */
void readEnvironment () {
    if (g_state.envRead) {
	return;
    }
    g_state.envRead = true;
    const char* text = getenv ("LWE_OVERLAY_TEXT");
    if (text != nullptr && text[0] != '\0' && g_state.settings.text.empty ()) {
	g_state.settings.text = text;
	g_state.dirty = true;
    }
}
} // namespace

namespace WallpaperEngine::Render::OverlayLabel {
const char* cornerName (const Corner corner) {
    switch (corner) {
	case Corner::TopRight:
	    return "top-right";
	case Corner::BottomLeft:
	    return "bottom-left";
	case Corner::BottomRight:
	    return "bottom-right";
	default:
	    return "top-left";
    }
}

std::optional<Corner> cornerFromName (const std::string& name) {
    if (name == "top-left") {
	return Corner::TopLeft;
    }
    if (name == "top-right") {
	return Corner::TopRight;
    }
    if (name == "bottom-left") {
	return Corner::BottomLeft;
    }
    if (name == "bottom-right") {
	return Corner::BottomRight;
    }
    return std::nullopt;
}

void set (
    const std::optional<std::string>& text, const std::optional<Corner> corner, const std::optional<bool> visible
) {
    readEnvironment ();
    if (text.has_value () && *text != g_state.settings.text) {
	g_state.settings.text = *text;
	g_state.dirty = true;
    }
    if (corner.has_value ()) {
	g_state.settings.corner = *corner;
    }
    if (visible.has_value ()) {
	g_state.settings.visible = *visible;
    }
}

Settings current () {
    readEnvironment ();
    return g_state.settings;
}

void draw (const int viewportWidth, const int viewportHeight) {
    readEnvironment ();
    if (!g_state.settings.visible || g_state.settings.text.empty ()) {
	return;
    }
    if (!g_state.glAttempted) {
	init ();
    }
    if (!g_state.glReady) {
	return;
    }
    if (g_state.dirty) {
	refresh ();
    }
    if (!g_state.textReady) {
	return;
    }

    GLint prevProgram = 0;
    GLint prevVao = 0;
    GLint prevTex = 0;
    glGetIntegerv (GL_CURRENT_PROGRAM, &prevProgram);
    glGetIntegerv (GL_VERTEX_ARRAY_BINDING, &prevVao);
    glGetIntegerv (GL_TEXTURE_BINDING_2D, &prevTex);
    const GLboolean hadBlend = glIsEnabled (GL_BLEND);

    const float scale = std::clamp (static_cast<float> (viewportHeight) / kReferenceHeight, kMinScale, 1.0f);
    const float drawnWidth = static_cast<float> (g_state.width) * scale;
    const float drawnHeight = static_cast<float> (g_state.height) * scale;
    const float marginX = static_cast<float> (kMarginX) * scale;
    const float marginY = static_cast<float> (kMarginY) * scale;
    const Corner corner = g_state.settings.corner;
    const bool right = corner == Corner::TopRight || corner == Corner::BottomRight;
    const bool bottom = corner == Corner::BottomLeft || corner == Corner::BottomRight;
    const float x = right ? static_cast<float> (viewportWidth) - marginX - drawnWidth : marginX;
    const float y = bottom ? static_cast<float> (viewportHeight) - marginY - drawnHeight : marginY;

    glUseProgram (g_state.program);
    glActiveTexture (GL_TEXTURE0);
    glBindTexture (GL_TEXTURE_2D, g_state.texture);
    glBindVertexArray (g_state.vao);
    glEnable (GL_BLEND);
    glBlendFunc (GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    glUniform2f (g_state.uViewport, static_cast<float> (viewportWidth), static_cast<float> (viewportHeight));
    glUniform1f (g_state.uScale, scale);

    // a one pixel black outline keeps the text legible on a light scene
    glUniform4f (g_state.uColor, 0.0f, 0.0f, 0.0f, 0.9f);
    for (int dy = -1; dy <= 1; dy++) {
	for (int dx = -1; dx <= 1; dx++) {
	    if (dx == 0 && dy == 0) {
		continue;
	    }
	    glUniform2f (g_state.uOffset, x + static_cast<float> (dx), y + static_cast<float> (dy));
	    glDrawArrays (GL_TRIANGLES, 0, 6);
	}
    }
    glUniform2f (g_state.uOffset, x, y);
    glUniform4f (g_state.uColor, 1.0f, 1.0f, 1.0f, 0.92f);
    glDrawArrays (GL_TRIANGLES, 0, 6);

    if (hadBlend == GL_FALSE) {
	glDisable (GL_BLEND);
    }
    glBindVertexArray (static_cast<GLuint> (prevVao));
    glBindTexture (GL_TEXTURE_2D, static_cast<GLuint> (prevTex));
    glUseProgram (static_cast<GLuint> (prevProgram));
}
}
