#include "OverlayLabel.h"
#include "WallpaperEngine/Logging/Log.h"

#include <GL/glew.h>

#include <ft2build.h>
#include FT_FREETYPE_H

#include <cstdlib>
#include <filesystem>
#include <vector>

namespace {
const char* kFonts[] = {
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
};

constexpr unsigned int kPixelSize = 20;
constexpr int kMarginX = 14;
constexpr int kMarginY = 12;

const char* kVert = R"glsl(
#version 330 core
layout(location = 0) in vec2 aPos;
layout(location = 1) in vec2 aUV;
uniform vec2 uViewport;
uniform vec2 uOffset;
out vec2 vUV;
void main() {
    // pixel space, origin top-left -> NDC
    vec2 p = (aPos + uOffset) / uViewport * 2.0 - 1.0;
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
    bool attempted = false;
    bool ready = false;
    GLuint program = 0;
    GLuint vao = 0;
    GLuint vbo = 0;
    GLuint texture = 0;
    int width = 0;
    int height = 0;
    GLint uViewport = -1;
    GLint uOffset = -1;
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

bool rasterize (const std::string& text, std::vector<uint8_t>& out, int& w, int& h) {
    FT_Library lib = nullptr;
    if (FT_Init_FreeType (&lib) != 0) {
	return false;
    }
    FT_Face face = nullptr;
    for (const auto* candidate : kFonts) {
	if (std::filesystem::exists (candidate) && FT_New_Face (lib, candidate, 0, &face) == 0) {
	    break;
	}
	face = nullptr;
    }
    if (face == nullptr) {
	FT_Done_FreeType (lib);
	return false;
    }
    FT_Set_Pixel_Sizes (face, 0, kPixelSize);
    const FT_GlyphSlot slot = face->glyph;

    int penX = 0;
    int maxAscent = 0;
    int maxDescent = 0;
    for (const char c : text) {
	if (FT_Load_Char (face, static_cast<FT_ULong> (static_cast<unsigned char> (c)), FT_LOAD_RENDER) != 0) {
	    continue;
	}
	penX += static_cast<int> (slot->advance.x >> 6);
	maxAscent = std::max (maxAscent, slot->bitmap_top);
	maxDescent = std::max (maxDescent, static_cast<int> (slot->bitmap.rows) - slot->bitmap_top);
    }
    w = penX + 2;
    h = maxAscent + maxDescent + 2;
    if (w <= 2 || h <= 2) {
	FT_Done_Face (face);
	FT_Done_FreeType (lib);
	return false;
    }
    out.assign (static_cast<size_t> (w) * h, 0);

    penX = 1;
    for (const char c : text) {
	if (FT_Load_Char (face, static_cast<FT_ULong> (static_cast<unsigned char> (c)), FT_LOAD_RENDER) != 0) {
	    continue;
	}
	const FT_Bitmap& bm = slot->bitmap;
	const int x0 = penX + slot->bitmap_left;
	const int y0 = 1 + maxAscent - slot->bitmap_top;
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
    FT_Done_Face (face);
    FT_Done_FreeType (lib);
    return true;
}

/** one-shot GL setup; failure is remembered so a broken font never retries per frame */
void init (const std::string& text) {
    g_state.attempted = true;

    std::vector<uint8_t> bitmap;
    if (!rasterize (text, bitmap, g_state.width, g_state.height)) {
	sLog.error ("OverlayLabel: rasterization failed; overlay disabled");
	return;
    }

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
    g_state.uColor = glGetUniformLocation (g_state.program, "uColor");

    glGenTextures (1, &g_state.texture);
    glBindTexture (GL_TEXTURE_2D, g_state.texture);
    glPixelStorei (GL_UNPACK_ALIGNMENT, 1);
    glTexImage2D (GL_TEXTURE_2D, 0, GL_R8, g_state.width, g_state.height, 0, GL_RED, GL_UNSIGNED_BYTE, bitmap.data ());
    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri (GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);

    const float wf = static_cast<float> (g_state.width);
    const float hf = static_cast<float> (g_state.height);
    const float quad[] = {
	0.0f, 0.0f, 0.0f, 0.0f, wf, 0.0f, 1.0f, 0.0f, 0.0f, hf, 0.0f, 1.0f,
	0.0f, hf,   0.0f, 1.0f, wf, 0.0f, 1.0f, 0.0f, wf,   hf, 1.0f, 1.0f,
    };
    glGenVertexArrays (1, &g_state.vao);
    glGenBuffers (1, &g_state.vbo);
    glBindVertexArray (g_state.vao);
    glBindBuffer (GL_ARRAY_BUFFER, g_state.vbo);
    glBufferData (GL_ARRAY_BUFFER, sizeof (quad), quad, GL_STATIC_DRAW);
    glEnableVertexAttribArray (0);
    glVertexAttribPointer (0, 2, GL_FLOAT, GL_FALSE, 4 * sizeof (float), nullptr);
    glEnableVertexAttribArray (1);
    glVertexAttribPointer (1, 2, GL_FLOAT, GL_FALSE, 4 * sizeof (float), reinterpret_cast<void*> (2 * sizeof (float)));
    glBindVertexArray (0);

    g_state.ready = true;
    sLog.out ("OverlayLabel: active (", g_state.width, "x", g_state.height, " label)");
}
} // namespace

namespace WallpaperEngine::Render::OverlayLabel {
void draw (const int viewportWidth, const int viewportHeight) {
    static const char* text = getenv ("LWE_OVERLAY_TEXT");
    if (text == nullptr || text[0] == '\0') {
	return;
    }
    if (!g_state.attempted) {
	init (text);
    }
    if (!g_state.ready) {
	return;
    }

    GLint prevProgram = 0;
    GLint prevVao = 0;
    GLint prevTex = 0;
    glGetIntegerv (GL_CURRENT_PROGRAM, &prevProgram);
    glGetIntegerv (GL_VERTEX_ARRAY_BINDING, &prevVao);
    glGetIntegerv (GL_TEXTURE_BINDING_2D, &prevTex);
    const GLboolean hadBlend = glIsEnabled (GL_BLEND);

    glUseProgram (g_state.program);
    glActiveTexture (GL_TEXTURE0);
    glBindTexture (GL_TEXTURE_2D, g_state.texture);
    glBindVertexArray (g_state.vao);
    glEnable (GL_BLEND);
    glBlendFunc (GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    glUniform2f (g_state.uViewport, static_cast<float> (viewportWidth), static_cast<float> (viewportHeight));

    glUniform2f (g_state.uOffset, static_cast<float> (kMarginX + 1), static_cast<float> (kMarginY + 1));
    glUniform4f (g_state.uColor, 0.0f, 0.0f, 0.0f, 0.85f);
    glDrawArrays (GL_TRIANGLES, 0, 6);
    glUniform2f (g_state.uOffset, static_cast<float> (kMarginX), static_cast<float> (kMarginY));
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
