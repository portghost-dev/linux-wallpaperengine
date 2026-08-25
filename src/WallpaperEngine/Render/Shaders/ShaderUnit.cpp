#include "ShaderUnit.h"
#include <fstream>

#include "WallpaperEngine/Logging/Log.h"
#include <algorithm>
#include <cmath>
#include <exception>
#include <regex>
#include <set>
#include <sstream>
#include <stack>
#include <string>
#include <utility>

#include "GLSLContext.h"
#include "WallpaperEngine/Assets/AssetLoadException.h"
#include "WallpaperEngine/Render/Shaders/Variables/ShaderVariable.h"
#include "WallpaperEngine/Render/Shaders/Variables/ShaderVariableFloat.h"
#include "WallpaperEngine/Render/Shaders/Variables/ShaderVariableInteger.h"
#include "WallpaperEngine/Render/Shaders/Variables/ShaderVariableVector2.h"
#include "WallpaperEngine/Render/Shaders/Variables/ShaderVariableVector3.h"
#include "WallpaperEngine/Render/Shaders/Variables/ShaderVariableVector4.h"

#include "WallpaperEngine/Data/Builders/VectorBuilder.h"
#include "WallpaperEngine/FileSystem/Container.h"
#include "WallpaperEngine/Render/Wallpapers/CScene.h"

#define SHADER_HEADER(filename)                                                                                        \
    "#version 330\n"                                                                                                   \
    "// ======================================================\n"                                                      \
    "// Processed shader "                                                                                             \
	+ filename                                                                                                     \
	+ "\n"                                                                                                         \
	  "// ======================================================\n"                                                \
	  "precision highp float;\n"                                                                                   \
	  "#define mul(x, y) ((y) * (x))\n"                                                                            \
	  "#define max(x, y) max (y, x)\n"                                                                             \
	  "#define lerp mix\n"                                                                                         \
	  "#define frac fract\n"                                                                                       \
	  "#define CAST2(x) (vec2(x))\n"                                                                               \
	  "#define CAST3(x) (vec3(x))\n"                                                                               \
	  "#define CAST4(x) (vec4(x))\n"                                                                               \
	  "#define CAST3X3(x) (mat3(x))\n"                                                                             \
	  "#define float2 vec2\n"                                                                                      \
	  "#define float3 vec3\n"                                                                                      \
	  "#define float4 vec4\n"                                                                                      \
	  "#define int2 ivec2\n"                                                                                       \
	  "#define int3 ivec3\n"                                                                                       \
	  "#define int4 ivec4\n"                                                                                       \
	  "#define saturate(x) (clamp(x, 0.0, 1.0))\n"                                                                 \
	  "#define texSample2D texture\n"                                                                              \
	  "#define texSample2DLod textureLod\n"                                                                        \
	  "#define log10(x) (log2(x) * 0.301029995663981)\n"                                                           \
	  "#define atan2 atan\n"                                                                                       \
	  "#define fmod(x, y) ((x)-(y)*trunc((x)/(y)))\n"                                                              \
	  "#define ddx dFdx\n"                                                                                         \
	  "#define ddy(x) dFdy(-(x))\n"                                                                                \
	  "#define GLSL 1\n\n";
#define FRAGMENT_SHADER_DEFINES                                                                                        \
    "out vec4 out_FragColor;\n"                                                                                        \
    "#define varying in\n"
#define VERTEX_SHADER_DEFINES                                                                                          \
    "#define attribute in\n"                                                                                           \
    "#define varying out\n"
#define DEFINE_COMBO(name, value) "#define " + name + " " + std::to_string (value) + "\n";

using namespace WallpaperEngine::Render;
using namespace WallpaperEngine::Data::Builders;
using namespace WallpaperEngine::Render::Shaders;

ShaderUnit::ShaderUnit (
    const GLSLContext::UnitType type, std::string file, std::string content, const AssetLocator& assetLocator,
    const ShaderConstantMap& constants, const TextureMap& passTextures, const TextureMap& overrideTextures,
    const ComboMap& combos, const ComboMap& overrideCombos, const ShaderConstantMap& materialConstants
) :
    m_type (type), m_file (std::move (file)), m_content (std::move (content)), m_combos (combos),
    m_overrideCombos (overrideCombos), m_constants (constants), m_materialConstants (materialConstants),
    m_passTextures (passTextures), m_overrideTextures (overrideTextures), m_link (nullptr),
    m_assetLocator (assetLocator) {
    // pre-process the shader so the units are clear
    this->preprocess ();
}

void ShaderUnit::preprocess () {
    this->m_preprocessed = this->m_content;
    this->m_includes = "";

    this->preprocessIncludes ();
    this->preprocessRequires ();
    this->preprocessVariables ();
    this->preprocessGlobalConsts ();

    {
	const std::string ambFrom = "mix(g_LightSkylightColor, g_LightAmbientColor, dot(normal, vec3(0, 1, 0))";
	const std::string ambTo = "mix(g_LightSkylightColor, g_LightAmbientColor, dot(normal, vec3(0, -1, 0))";
	size_t pos = 0;
	while ((pos = this->m_preprocessed.find (ambFrom, pos)) != std::string::npos) {
	    this->m_preprocessed.replace (pos, ambFrom.length (), ambTo);
	    pos += ambTo.length ();
	}
    }

    // replace gl_FragColor with the equivalent
    const std::string from = "gl_FragColor";
    const std::string to = "out_FragColor";

    size_t start_pos = 0;
    while ((start_pos = this->m_preprocessed.find (from, start_pos)) != std::string::npos) {
	this->m_preprocessed.replace (start_pos, from.length (), to);
	start_pos += to.length (); // Handles case where 'to' is a substring of 'from'
    }

    if (this->m_type == GLSLContext::UnitType_Vertex && this->m_file.find ("genericropeparticle") != std::string::npos
	&& getenv ("LWE_NOROPEUVFLIP") == nullptr) {
	const std::string uvLine = "v_TexCoord.y = mix(uvMinimum, uvMinimum + uvDelta, uvs.y);";
	const size_t uvAt = this->m_preprocessed.find (uvLine);
	if (uvAt != std::string::npos) {
	    this->m_preprocessed.replace (
		uvAt, uvLine.length (), "v_TexCoord.y = 1.0 - mix(uvMinimum, uvMinimum + uvDelta, uvs.y);"
	    );
	    sLog.out ("LWE-ROPEUVFLIP active");
	}
    }

    if (this->m_type == GLSLContext::UnitType_Vertex && this->m_file.find ("genericparticle") != std::string::npos) {
	const std::string posLine
	    = "vec3 position = ComputeParticlePosition(a_TexCoordVec4.xy, textureRatio, vec4(a_Position.xyz, "
	      "in_ParticleSize), right, up);";
	const size_t at = this->m_preprocessed.find (posLine);
	if (at != std::string::npos) {
	    this->m_preprocessed.replace (
		at, posLine.length (),
		posLine
		    + "\n#if TRAILRENDERER\n"
		      "\tposition -= up * (in_ParticleSize * 0.5 * textureRatio);\n"
		      "#endif\n"
		      "\tposition = a_Position.xyz + (position - a_Position.xyz) * g_LWEAxisComp;"
	    );
	    const std::string mainDecl = "void main()";
	    const size_t mainAt = this->m_preprocessed.find (mainDecl);
	    if (mainAt != std::string::npos) {
		this->m_preprocessed.insert (mainAt, "uniform vec3 g_LWEAxisComp;\n");
	    }
	    sLog.debug ("LWE-AXISCOMP injected into ", this->m_file);
	} else {
	    sLog.error ("LWE-AXISCOMP anchor line NOT FOUND in ", this->m_file, " - compensation inactive");
	}
    }
}

void ShaderUnit::preprocessGlobalConsts () {
    static const std::regex constDecl (
	R"(^\s*(?:static\s+)?const\s+(?:high|medium|low)?p?\w*\s*(float|int|uint|vec[234]|ivec[234]|mat[234])\s+(\w+)\s*=\s*([^;]+);\s*$)"
    );
    static const std::regex identifier (R"([A-Za-z_]\w*)");
    static const std::set<std::string> constantSafe = { "float", "int",   "uint", "vec2", "vec3", "vec4", "ivec2",
							"ivec3", "ivec4", "mat2", "mat3", "mat4", "true", "false" };

    std::istringstream input (this->m_preprocessed);
    std::ostringstream output;
    std::string line;
    int braceDepth = 0;

    while (std::getline (input, line)) {
	std::smatch match;
	if (std::regex_match (line, match, constDecl)) {
	    const std::string& name = match[2];
	    const std::string& expr = match[3];
	    bool nonConstant = false;
	    for (auto it = std::sregex_iterator (expr.begin (), expr.end (), identifier); it != std::sregex_iterator ();
		 ++it) {
		if (!constantSafe.contains (it->str ())) {
		    nonConstant = true;
		    break;
		}
	    }
	    if (nonConstant) {
		// wrap in the declared type's constructor: HLSL allows implicit
		// narrowing (float2 x = <float4 expr>), GLSL needs vec2(<expr>)
		const std::string& type = match[1];
		if (braceDepth == 0) {
		    output << "#define " << name << " (" << type << "(" << expr << "))\n";
		} else {
		    output << type << " " << name << " = " << type << "(" << expr << ");\n";
		}
		continue;
	    }
	}
	braceDepth += static_cast<int> (std::count (line.begin (), line.end (), '{'));
	braceDepth -= static_cast<int> (std::count (line.begin (), line.end (), '}'));
	output << line << "\n";
    }

    this->m_preprocessed = output.str ();
}

void ShaderUnit::preprocessVariables () {
    size_t start = 0, end = 0;
    while ((end = this->m_preprocessed.find ('\n', start)) != std::string::npos) {
	// Extract a line from the string
	std::string line = this->m_preprocessed.substr (start, end - start);
	const size_t combo = line.find ("// [COMBO] ");
	const size_t uniform = line.find ("uniform ");
	const size_t comment = line.find ("// ");
	const size_t semicolon = line.find (';');

	if (combo != std::string::npos) {
	    this->parseComboConfiguration (line.substr (combo + strlen ("// [COMBO] ")), 0);
	} else if (
	    uniform != std::string::npos && comment != std::string::npos && semicolon != std::string::npos &&
	    // this check ensures that the comment is after the semicolon (so it's not a commented-out line)
	    // this needs further refining as it's not taking into account block comments
	    semicolon < comment
	) {
	    // uniforms with comments should never have a value assigned, use this fact to detect the required parts
	    const size_t last_space = line.find_last_of (' ', semicolon);

	    if (last_space != std::string::npos) {
		const size_t previous_space = line.find_last_of (' ', last_space - 1);

		if (previous_space != std::string::npos) {
		    // extract type and name
		    std::string type = line.substr (previous_space + 1, last_space - previous_space - 1);
		    std::string name = line.substr (last_space + 1, semicolon - last_space - 1);
		    std::string json = line.substr (comment + 2);

		    this->parseParameterConfiguration (type, name, json);
		}
	    }
	}

	// Move to the next line
	start = end + 1;
    }
}

void ShaderUnit::preprocessIncludes () {
    size_t start = 0, end = 0;
    // prepare the include content
    while ((start = this->m_preprocessed.find ("#include", end)) != std::string::npos) {
	// TODO: CHECK FOR ERRORS HERE, MALFORMED INCLUDES WILL NOT BE PROPERLY HANDLED
	const size_t quoteStart = this->m_preprocessed.find_first_of ('"', start) + 1;
	const size_t quoteEnd = this->m_preprocessed.find_first_of ('"', quoteStart);
	const std::string filename = this->m_preprocessed.substr (quoteStart, quoteEnd - quoteStart);

	// some includes might not be present
	// and that should not be treated as an error mainly because these could come from
	// commented out content
	std::string content;

	try {
	    content += "// begin of include from file ";
	    content += filename;
	    content += "\n";
	    content += this->m_assetLocator.includeShader (filename);
	    content += "\n// end of included from file ";
	    content += filename;
	    content += "\n";
	} catch (AssetLoadException&) {
	    content += "// tried including file ";
	    content += filename;
	    content += " but was not found\n";
	}

	// replace the first two letters with a comment so the filelength doesn't change
	this->m_preprocessed = this->m_preprocessed.replace (start, 2, "//");

	this->m_includes += content;

	// go to the end of the line
	end = start;
    }

    // ensure the included files do not include other files
    end = 0;

    // then apply includes in-place
    while ((start = this->m_includes.find ("#include", end)) != std::string::npos) {
	const size_t lineEnd = this->m_includes.find_first_of ('\n', start);
	// TODO: CHECK FOR ERRORS HERE, MALFORMED INCLUDES WILL NOT BE PROPERLY HANDLED
	const size_t quoteStart = this->m_includes.find_first_of ('"', start) + 1;
	const size_t quoteEnd = this->m_includes.find_first_of ('"', quoteStart);
	const std::string filename = this->m_includes.substr (quoteStart, quoteEnd - quoteStart);

	// some includes might not be present
	// and that should not be treated as an error mainly because these could come from
	// commented out content
	std::string content;

	try {
	    content = "// begin of include from file ";
	    content += filename;
	    content += "\n";
	    content += this->m_assetLocator.includeShader (filename);
	    content += "\n// end of included from file ";
	    content += filename;
	    content += "\n";
	} catch (AssetLoadException&) {
	    content = "// tried including file ";
	    content += filename;
	    content += " but was not found\n";
	}

	// file contents ready, replace things
	this->m_includes = this->m_includes.replace (start, lineEnd - start, content);

	// go back to the beginning of the line to properly continue detecting things
	end = start;
    }

    // search for the main function and add the includes before that for now
    end = 0;
    bool includesAdded = false;

    // finally, try to place the include contents before the main function
    while ((start = this->m_preprocessed.find (" main", end)) != std::string::npos) {
	char value = this->m_preprocessed.at (start + 5);

	end = start + 5;

	if (value != ' ' && value != '(') {
	    continue;
	}

	// main located, search for uniforms and find the latest one available
	size_t lastAttribute = this->m_preprocessed.rfind ("attribute", start);
	size_t lastVarying = this->m_preprocessed.rfind ("varying", start);
	size_t lastUniform = this->m_preprocessed.rfind ("uniform", start);
	size_t latest = lastAttribute;

	if (latest == std::string::npos) {
	    latest = lastVarying;
	} else if (latest < lastVarying && lastVarying != std::string::npos) {
	    latest = lastVarying;
	}

	if (latest == std::string::npos) {
	    latest = lastUniform;
	} else if (latest < lastUniform && lastUniform != std::string::npos) {
	    latest = lastUniform;
	}

	if (latest < start) {
	    // find the end of the current line
	    latest = this->m_preprocessed.find ('\n', latest);
	} else {
	    // find the end of the previous line
	    latest = this->m_preprocessed.rfind ('\n', start);
	}

	// update the function start to point to the end of the previous line
	// as this will be used to determine the position of the includes
	start = this->m_preprocessed.rfind ('\n', start);

	// keeps track of the start and end of ifdefs to look for the right
	// place to put the includes in
	std::stack<size_t> ifdefStack;

	// start looking for #if and #endif results and add to the stack so we find the start of the current chain of
	// ifdefs and use that as point

	// for this we'll use regex
	const std::regex ifdef (R"((#if|#endif))");
	std::smatch match;
	size_t current = 0;

	while (
	    std::regex_search (this->m_preprocessed.cbegin () + current, this->m_preprocessed.cend (), match, ifdef)) {
	    current += match.position ();

	    // if it's opening an #ifdef keep track of the start of the block
	    // and that's it
	    if (this->m_preprocessed.substr (current, 3) == "#if") {
		// go to the next character so the regex doesn't match with the same thing again
		ifdefStack.push (current++);
		continue;
	    }

	    // go to the next character so the regex doesn't match with the same thing again
	    current++;

	    // most likely a syntax error, but we'll ignore it for now...
	    if (ifdefStack.empty ()) {
		continue;
	    }

	    size_t stackStart = ifdefStack.top ();
	    ifdefStack.pop ();

	    if (latest > stackStart && latest <= current) {
		// The insertion point is inside a conditional block.
		// Move to BEFORE the #if so includes are available to all branches
		// (e.g. genericropeparticle.vert has #if GS_ENABLED wrapping two main() functions).
		size_t beforeIfdef = this->m_preprocessed.rfind ('\n', stackStart);
		latest = (beforeIfdef != std::string::npos) ? beforeIfdef : 0;
	    }
	}

	// no more matches, get the one that happens the earliest
	// TODO: IS THIS GOOD ENOUGH? MAYBE WE SHOULD BE GETTING THE FIRST #IF BLOCK INSTEAD?
	latest = std::min (latest, start);

	// finally insert it there
	this->m_preprocessed.insert (latest + 1, this->m_includes + '\n');
	includesAdded = true;
	break;
    }

    if (!includesAdded) {
	sLog.exception ("Could not find where to place includes for shader unit ", this->m_file);
    }
}

void ShaderUnit::preprocessRequires () {
    size_t start = 0, end = 0;

    while ((start = this->m_preprocessed.find ("#require", end)) != std::string::npos) {
	const size_t lineEnd = this->m_preprocessed.find_first_of ('\n', start);

	const size_t nameStart = start + std::string ("#require ").length ();

	if (nameStart >= lineEnd) {
	    sLog.error ("Malformed #require directive (no module name) in shader ", this->m_file);
	    end = lineEnd;
	    continue;
	}

	std::string moduleName = this->m_preprocessed.substr (nameStart, lineEnd - nameStart);

	while (!moduleName.empty () && (moduleName.back () == ' ' || moduleName.back () == '\r')) {
	    moduleName.pop_back ();
	}

	if (moduleName.empty ()) {
	    sLog.error ("Malformed #require directive (empty module name) in shader ", this->m_file);
	    end = lineEnd;
	    continue;
	}

	sLog.out ("Resolving require module: ", moduleName, " in shader ", this->m_file);

	std::string moduleCode = this->resolveRequireModule (moduleName);

	// comment out the #require directive
	this->m_preprocessed = this->m_preprocessed.replace (start, 2, "//");

	if (!moduleCode.empty ()) {
	    // insert the generated code directly into m_preprocessed at the #require location
	    // (m_includes was already consumed by preprocessIncludes, so appending there would be lost)
	    this->m_preprocessed.insert (start, moduleCode);
	    end = start + moduleCode.length ();
	} else {
	    end = lineEnd;
	}
    }
}

std::string ShaderUnit::resolveRequireModule (const std::string& moduleName) const {
    if (moduleName == "LightingV1") {
	return this->generateLightingV1 ();
    }

    sLog.error ("Unknown #require module: ", moduleName, " in shader ", this->m_file);
    return "";
}

std::string ShaderUnit::generateLightingV1 () const {
    const std::string slots = std::to_string (Wallpapers::CScene::MAX_LIGHTS);
    const std::string features = std::to_string (Wallpapers::CScene::MAX_SHADOW_FEATURES);

    std::ostringstream module;

    module << "// ---- begin generated module: LightingV1 ----\n"
	   << "uniform int lwe_LitPointCount;\n"
	   << "uniform vec4 lwe_LitPointPosRad[" << slots << "];\n"
	   << "uniform vec4 lwe_LitPointColorExp[" << slots << "];\n"
	   << "uniform int lwe_LitSpotCount;\n"
	   << "uniform vec4 lwe_LitSpotPosRad[" << slots << "];\n"
	   << "uniform vec4 lwe_LitSpotColorExp[" << slots << "];\n"
	   << "uniform vec4 lwe_LitSpotAxisCosIn[" << slots << "];\n"
	   << "uniform float lwe_LitSpotCosOut[" << slots << "];\n"
	   << "uniform int lwe_LitDirCount;\n"
	   << "uniform vec4 lwe_LitDirToLight[" << slots << "];\n"
	   << "uniform vec4 lwe_LitDirColor[" << slots << "];\n"
	   << "uniform sampler2DShadow lwe_ShadowAtlas;\n"
	   << "uniform int lwe_ShadowFeatureCount;\n"
	   << "uniform mat4 lwe_ShadowMatrix[" << features << "];\n"
	   << "uniform vec4 lwe_ShadowTransform[" << features << "];\n"
	   << "uniform float lwe_ShadowEnabled[" << features << "];\n"
	   << "uniform float lwe_LitSpotShadowFeature[" << slots << "];\n"
	   << "uniform vec4 lwe_LitDirShadowFeatures[" << slots << "];\n"
	   << "uniform mat4 lwe_LitPointShadowMat[" << slots << " * 6];\n"
	   << "uniform vec4 lwe_LitPointShadowXform[" << slots << "];\n"
	   << "uniform float lwe_LitPointShadowEnabled[" << slots << "];\n"
	   << "uniform int lwe_LitTubeCount;\n"
	   << "uniform vec4 lwe_LitTubePosRadA[" << slots << "];\n"
	   << "uniform vec4 lwe_LitTubeEndExpB[" << slots << "];\n"
	   << "uniform vec4 lwe_LitTubeColor[" << slots << "];\n"
	   << "\n"
	   << "float lweShadowFeatureFactor(vec3 worldPos, int feature) {\n"
	   << "    if (feature < 0 || lwe_ShadowEnabled[feature] < 0.5) return 1.0;\n"
	   << "    vec4 clip = lwe_ShadowMatrix[feature] * vec4(worldPos, 1.0);\n"
	   << "    vec3 ndc = clip.xyz / max(clip.w, 1e-6);\n"
	   << "    if (any(greaterThan(abs(ndc), vec3(1.0)))) return 1.0;\n"
	   << "    vec2 uv = ndc.xy * 0.5 + 0.5;\n"
	   << "    float depthRef = ndc.z * 0.5 + 0.5 - 0.0015;\n"
	   << "    vec4 cell = lwe_ShadowTransform[feature];\n"
	   << "    return texture(lwe_ShadowAtlas, vec3(cell.xy + uv * cell.zw, depthRef));\n"
	   << "}\n"
	   << "float lwePointShadowFactor(vec3 worldPos, int slot) {\n"
	   << "    if (lwe_LitPointShadowEnabled[slot] < 0.5) return 1.0;\n"
	   << "    vec4 block = lwe_LitPointShadowXform[slot];\n"
	   << "    vec2 cellSize = vec2(block.z * 0.5, block.w / 3.0);\n"
	   << "    for (int face = 0; face < 6; face++) {\n"
	   << "        vec4 clip = lwe_LitPointShadowMat[slot * 6 + face] * vec4(worldPos, 1.0);\n"
	   << "        if (clip.w <= 0.0) continue;\n"
	   << "        vec3 ndc = clip.xyz / clip.w;\n"
	   << "        if (any(greaterThan(abs(ndc), vec3(1.0)))) continue;\n"
	   << "        vec2 uv = ndc.xy * 0.5 + 0.5;\n"
	   << "        float depthRef = ndc.z * 0.5 + 0.5 - 0.0015;\n"
	   << "        vec2 cellOrigin = block.xy + vec2(float(face % 2), float(face / 2)) * cellSize;\n"
	   << "        return texture(lwe_ShadowAtlas, vec3(cellOrigin + uv * cellSize, depthRef));\n"
	   << "    }\n"
	   << "    return 1.0;\n"
	   << "}\n"
	   << "\n"
	   << "vec3 PerformLighting_V1(vec3 worldPos, vec3 baseColor, vec3 surfNormal, vec3 towardEye,\n"
	   << "                        vec3 specTint, vec3 reflect0, float roughVal, float metalVal) {\n"
	   << "    vec3 accum = CAST3(0.0);\n"
	   << "    for (int idx = 0; idx < lwe_LitPointCount; idx++) {\n"
	   << "        vec3 towardLight = lwe_LitPointPosRad[idx].xyz - worldPos;\n"
	   << "        float shadowFactor = lwePointShadowFactor(worldPos, idx);\n"
	   << "        accum += ComputePBRLightShadow(surfNormal, towardLight, towardEye, baseColor,\n"
	   << "            lwe_LitPointColorExp[idx].rgb, lwe_LitPointPosRad[idx].w, lwe_LitPointColorExp[idx].w,\n"
	   << "            specTint, reflect0, roughVal, metalVal, shadowFactor);\n"
	   << "    }\n"
	   << "    for (int idx = 0; idx < lwe_LitSpotCount; idx++) {\n"
	   << "        vec3 towardLight = lwe_LitSpotPosRad[idx].xyz - worldPos;\n"
	   << "        float beamCos = dot(normalize(-towardLight), lwe_LitSpotAxisCosIn[idx].xyz);\n"
	   << "        float coneFade = smoothstep(lwe_LitSpotCosOut[idx], lwe_LitSpotAxisCosIn[idx].w, beamCos);\n"
	   << "        float shadowFactor = lweShadowFeatureFactor(worldPos, int(lwe_LitSpotShadowFeature[idx]));\n"
	   << "        accum += ComputePBRLightShadow(surfNormal, towardLight, towardEye, baseColor,\n"
	   << "            lwe_LitSpotColorExp[idx].rgb * coneFade, lwe_LitSpotPosRad[idx].w, "
	      "lwe_LitSpotColorExp[idx].w,\n"
	   << "            specTint, reflect0, roughVal, metalVal, shadowFactor);\n"
	   << "    }\n"
	   << "    for (int idx = 0; idx < lwe_LitDirCount; idx++) {\n"
	   << "        vec4 dirFeatures = lwe_LitDirShadowFeatures[idx];\n"
	   << "        float shadowFactor = 1.0;\n"
	   << "        if (dirFeatures.x >= 0.0) {\n"
	   << "            float f1 = lweShadowFeatureFactor(worldPos, int(dirFeatures.x));\n"
	   << "            float f2 = lweShadowFeatureFactor(worldPos, int(dirFeatures.y));\n"
	   << "            float f3 = lweShadowFeatureFactor(worldPos, int(dirFeatures.z));\n"
	   << "            shadowFactor = min(f1, min(f2, f3));\n"
	   << "        }\n"
	   << "        accum += ComputePBRLightShadowInfinite(surfNormal, lwe_LitDirToLight[idx].xyz, towardEye,\n"
	   << "            baseColor, lwe_LitDirColor[idx].rgb, specTint, reflect0, roughVal, metalVal, "
	      "shadowFactor);\n"
	   << "    }\n"
	   << "    for (int idx = 0; idx < lwe_LitTubeCount; idx++) {\n"
	   << "        vec3 segA = lwe_LitTubePosRadA[idx].xyz;\n"
	   << "        vec3 segAB = lwe_LitTubeEndExpB[idx].xyz - segA;\n"
	   << "        float segT = clamp(dot(worldPos - segA, segAB) / max(dot(segAB, segAB), 1e-6), 0.0, 1.0);\n"
	   << "        vec3 towardLight = (segA + segAB * segT) - worldPos;\n"
	   << "        accum += ComputePBRLightShadow(surfNormal, towardLight, towardEye, baseColor,\n"
	   << "            lwe_LitTubeColor[idx].rgb, lwe_LitTubePosRadA[idx].w, lwe_LitTubeEndExpB[idx].w,\n"
	   << "            specTint, reflect0, roughVal, metalVal, 1.0);\n"
	   << "    }\n"
	   << "    return accum;\n"
	   << "}\n"
	   << "// ---- end generated module: LightingV1 ----\n";

    return module.str ();
}

std::string ShaderUnit::applyLinkedVaryingCompatibility (std::string source) const {
    if (this->m_type != GLSLContext::UnitType_Vertex || this->m_link == nullptr) {
	return source;
    }

    std::regex fragmentVec4Varying (R"(\bvarying\s+vec4\s+([A-Za-z_][A-Za-z0-9_]*)\s*;)");
    std::smatch varyingMatch;
    std::string linked = this->m_link->m_preprocessed;
    size_t linkedOffset = 0;

    while (std::regex_search (linked.cbegin () + linkedOffset, linked.cend (), varyingMatch, fragmentVec4Varying)) {
	const std::string name = varyingMatch[1].str ();
	linkedOffset += varyingMatch.position () + varyingMatch.length ();

	const std::regex vertexVec2Decl ("\\bvarying\\s+vec2\\s+" + name + "\\s*;");
	if (!std::regex_search (source, vertexVec2Decl)) {
	    continue;
	}

	source = std::regex_replace (source, vertexVec2Decl, "varying vec4 " + name + ";");

	const std::regex assignment ("(^|\\n)([ \\t]*)" + name + "\\s*=\\s*([^;\\n]+);");
	std::smatch assignmentMatch;
	size_t offset = 0;
	while (std::regex_search (source.cbegin () + offset, source.cend (), assignmentMatch, assignment)) {
	    const std::string prefix = assignmentMatch[1].str ();
	    const std::string indent = assignmentMatch[2].str ();
	    const std::string expression = assignmentMatch[3].str ();
	    const std::string replacement = prefix + indent + name + " = vec4(" + expression + ", 0.0, 1.0);";
	    const size_t position = offset + assignmentMatch.position ();
	    source.replace (position, assignmentMatch.length (), replacement);
	    offset = position + replacement.length ();
	}
    }

    return source;
}

std::string ShaderUnit::applyTextureResolutionSwizzleCompatibility (std::string source) const {
    static const std::regex vec2ThenRes (
	R"((CAST2\s*\([^()]*\)|vec2\s*\([^()]*\))(\s*[*/+-]\s*)(g_Texture\d+Resolution)\b(?!\.))"
    );
    static const std::regex resThenVec2 (
	R"((g_Texture\d+Resolution)\b(?!\.)(\s*[*/+-]\s*)(CAST2\s*\([^()]*\)|vec2\s*\([^()]*\)))"
    );
    std::string patched = std::regex_replace (source, vec2ThenRes, "$1$2$3.xy");
    patched = std::regex_replace (patched, resThenVec2, "$1.xy$2$3");
    if (patched != source) {
	sLog.out ("Applied g_TextureResolution vec4/vec2 swizzle compatibility in ", this->m_file);
    }
    return patched;
}

std::string ShaderUnit::applyFragmentTexCoordCompatibility (std::string source) const {
    if (this->m_type != GLSLContext::UnitType_Fragment) {
	return source;
    }

    const std::regex texCoordBeforeCast2 (R"(\bv_TexCoord\b(\s*[-+*/]\s*CAST2\s*\())");
    const std::regex cast2BeforeTexCoord (R"((CAST2\s*\([^)]+\)\s*[-+*/]\s*)\bv_TexCoord\b)");

    const std::regex wideTexCoordDecl (R"(\bvarying\s+vec[34]\s+v_TexCoord\s*;)");
    if (!std::regex_search (source, wideTexCoordDecl)
	|| (!std::regex_search (source, texCoordBeforeCast2) && !std::regex_search (source, cast2BeforeTexCoord))) {
	return source;
    }

    const std::string original = source;
    source = std::regex_replace (source, texCoordBeforeCast2, "v_TexCoord.xy$1");
    source = std::regex_replace (source, cast2BeforeTexCoord, "$1v_TexCoord.xy");

    if (source != original) {
	sLog.out ("Applied fragment TexCoord vec2 compatibility in ", this->m_file);
    }

    return source;
}

void ShaderUnit::parseComboConfiguration (const std::string& content, const int defaultValue) {
    // TODO: SUPPORT REQUIRES SO WE PROPERLY FOLLOW THE REQUIRED CHAIN
    JSON data;
    try {
	data = WallpaperEngine::Data::JSON::parseLenient (content);
    } catch (const std::exception& e) {
	sLog.error ("Cannot parse combo metadata in shader ", this->m_file, ": ", e.what ());
	return;
    }
    const auto combo = data.require<std::string> ("combo", "cannot parse combo information");
    // ignore type as it seems to be used only on the editor
    // const auto type = data.find ("type");
    const auto defvalue = data.find ("default");

    // check the combos
    const auto entry = this->m_combos.find (combo);
    const auto entryOverride = this->m_overrideCombos.find (combo);

    // add the combo to the found list
    this->m_usedCombos.emplace (combo, true);

    // if the combo was not found in the predefined values this means that the default value in the JSON data can be
    // used so only define the ones that are not already defined
    if (entry == this->m_combos.end () && entryOverride == this->m_overrideCombos.end ()) {
	// if no combo is defined just load the default settings
	if (defvalue == data.end ()) {
	    // TODO: PROPERLY SUPPORT EMPTY COMBOS
	    this->m_discoveredCombos.emplace (combo, defaultValue);
	} else if (defvalue->is_number_float ()) {
	    this->m_discoveredCombos.emplace (combo, static_cast<int> (std::round (defvalue->get<float> ())));
	} else if (defvalue->is_number_integer ()) {
	    this->m_discoveredCombos.emplace (combo, defvalue->get<int> ());
	} else if (defvalue->is_string ()) {
	    try {
		this->m_discoveredCombos.emplace (combo, std::stoi (defvalue->get<std::string> ()));
	    } catch (const std::exception&) {
		sLog.error (
		    "Cannot parse string combo default for ", combo, " in shader ", this->m_file, " - using ",
		    defaultValue
		);
		this->m_discoveredCombos.emplace (combo, defaultValue);
	    }
	} else {
	    sLog.exception ("cannot parse combo information ", combo, ". unknown type for ", defvalue->dump ());
	}
    }
}

void ShaderUnit::parseParameterConfiguration (
    const std::string& type, const std::string& name, const std::string& content
) {
    JSON data;
    try {
	data = WallpaperEngine::Data::JSON::parseLenient (content);
    } catch (const std::exception& e) {
	sLog.error ("Cannot parse parameter metadata for ", name, " in shader ", this->m_file, ": ", e.what ());
	return;
    }
    const auto material = data.optional ("material");
    const auto defvalue = data.optional ("default");
    // auto range = data.find ("range");
    const auto combo = data.find ("combo");

    // this is not a real parameter
    auto constant = this->m_constants.end ();

    if (material.has_value ()) {
	constant = this->m_constants.find (*material);
    }

    if (constant == this->m_constants.end () && !defvalue.has_value ()) {
	if (type != "sampler2D") {
	    sLog.exception ("Cannot parse parameter data for ", name, " in shader ", this->m_file);
	}
    }

    Variables::ShaderVariable* parameter = nullptr;

    if (type == "vec4") {
	parameter
	    = new Variables::ShaderVariableVector4 (VectorBuilder::parse<glm::vec4> (defvalue->get<std::string> ()));
    } else if (type == "vec3") {
	parameter = new Variables::ShaderVariableVector3 (VectorBuilder::parse<glm::vec3> (*defvalue));
    } else if (type == "vec2") {
	parameter = new Variables::ShaderVariableVector2 (VectorBuilder::parse<glm::vec2> (*defvalue));
    } else if (type == "float") {
	if (defvalue->is_string ()) {
	    parameter = new Variables::ShaderVariableFloat (std::stoi (defvalue->get<std::string> ()));
	} else {
	    parameter = new Variables::ShaderVariableFloat (defvalue->get<float> ());
	}
    } else if (type == "int") {
	if (defvalue->is_string ()) {
	    parameter = new Variables::ShaderVariableInteger (std::stoi (defvalue->get<std::string> ()));
	} else {
	    parameter = new Variables::ShaderVariableInteger (defvalue->get<int> ());
	}
    } else if (type == "sampler2D" || type == "sampler2DComparison") {
	// samplers can have special requirements, check what sampler we're working with and create definitions
	// if needed
	const auto textureName = data.find ("default");
	size_t index = 0;
	try {
	    index = std::stoul (name.substr (std::string ("g_Texture").length ()));
	} catch (const std::exception&) {
	    sLog.error ("Cannot parse texture index from name: ", name, " in shader ", this->m_file);
	    return;
	}

	const auto paintDefaultColor = data.find ("paintdefaultcolor");
	if (paintDefaultColor != data.end () && paintDefaultColor->is_string ()) {
	    try {
		const std::string& colorStr = paintDefaultColor->get<std::string> ();
		const int colorComponents = VectorBuilder::preparseSize (colorStr);
		if (colorComponents == 4) {
		    this->m_paintDefaultColors.emplace (index, VectorBuilder::parse<glm::vec4> (colorStr));
		} else if (colorComponents == 3) {
		    this->m_paintDefaultColors.emplace (
			index, glm::vec4 (VectorBuilder::parse<glm::vec3> (colorStr), 1.0f)
		    );
		}
	    } catch (const std::exception& e) {
		sLog.error ("Cannot parse paintdefaultcolor for ", name, " in shader ", this->m_file, ": ", e.what ());
	    }
	}
	const auto requireany = data.find ("requireany");
	const auto require = data.find ("require");
	// TODO: SUPPORT USER TEXTURES!!

	if (combo != data.end ()) {
	    // TODO: CLEANUP HOW THIS IS DETERMINED FIRST
	    // if the texture exists (and is not null), add to the combo
	    const auto textureSlotUsed
		= this->m_passTextures.contains (index) || this->m_overrideTextures.contains (index);
	    bool isRequired = false;
	    int comboValue = 1;

	    if (textureSlotUsed) {
		// nothing extra to do, the texture exists, the combo must be set
		// these tend to not have default value
		isRequired = true;
	    } else if (require != data.end ()) {
		// this is required based on certain conditions
		if (requireany != data.end () && requireany->get<bool> ()) {
		    // any of the values set are valid, check for them
		    for (const auto& item : require->items ()) {
			const std::string& macro = item.key ();
			const auto it = this->m_combos.find (macro);

			// if any of the values matched, this option is required
			if (it == this->m_combos.end () || this->m_overrideCombos.contains (macro)
			    || it->second != item.value ()) {
			    isRequired = true;
			    break;
			}
		    }
		} else {
		    isRequired = true;

		    // all values must match for it to be required
		    for (const auto& item : require->items ()) {
			const std::string& macro = item.key ();
			const auto it = this->m_combos.find (macro);

			// these can not exist and that'd be fine, we just care about the values
			if ((it != this->m_combos.end () || this->m_overrideCombos.contains (macro))
			    && it->second == item.value ()) {
			    isRequired = false;
			    break;
			}
		    }
		}
	    }

	    if (isRequired && !textureSlotUsed) {
		if (!defvalue.has_value ()) {
		    isRequired = false;
		} else {
		    // is the combo registered already?
		    // if not, add it with the default value
		    // there's already a combo providing this value, so it doesn't need to be added
		    if (this->m_combos.contains (*combo) || this->m_overrideCombos.contains (*combo)) {
			isRequired = false;
			// otherwise a default value must be used
		    } else if (defvalue->is_string ()) {
			comboValue = std::stoi (defvalue->get<std::string> ().c_str ());
		    } else if (defvalue->is_number ()) {
			comboValue = *defvalue;
		    } else {
			sLog.exception (
			    "Cannot determine default value for combo ", combo->get<std::string> (),
			    " because it's not specified by the shader and is not given a default value: ", this->m_file
			);
		    }
		}
	    }

	    if (isRequired) {
		// add the new combo to the list
		this->m_discoveredCombos.emplace (*combo, comboValue);
		// textures linked to combos need to be tracked too
		this->m_usedCombos.emplace (*combo, true);

		const auto components = data.find ("components");
		if (textureSlotUsed && components != data.end () && components->is_array ()) {
		    for (const auto& component : *components) {
			const auto componentCombo = component.find ("combo");
			if (componentCombo == component.end () || !componentCombo->is_string ()) {
			    continue;
			}
			if (this->m_combos.contains (*componentCombo)
			    || this->m_overrideCombos.contains (*componentCombo)) {
			    continue;
			}
			const std::string comboName = *componentCombo;
			bool enable = true;
			if (comboName == "METALLIC_MAP") {
			    enable = this->m_constants.find ("metallic") == this->m_constants.end ()
				&& this->m_materialConstants.find ("metallic") == this->m_materialConstants.end ();
			} else if (comboName == "ROUGHNESS_MAP") {
			    enable = this->m_constants.find ("roughness") == this->m_constants.end ()
				&& this->m_materialConstants.find ("roughness") == this->m_materialConstants.end ();
			} else if (comboName == "REFLECTION_MAP") {
			    const auto reflection = this->m_combos.find ("REFLECTION");
			    enable = reflection != this->m_combos.end () && reflection->second != 0;
			}
			if (getenv ("LWE_AUDIT") != nullptr) {
			    sLog.out (
				"LWE-AUDIT PBRMASKS component ", comboName, " enable=", enable, " shader=", this->m_file
			    );
			}
			if (!enable) {
			    continue;
			}
			this->m_discoveredCombos.emplace (*componentCombo, 1);
			this->m_usedCombos.emplace (*componentCombo, true);
		    }
		}
	    }
	}

	if (textureName != data.end ()) {
	    this->m_defaultTextures.emplace (index, *textureName);
	}

	// samplers are not saved, we can ignore them for now
	return;
    } else {
	sLog.error ("Unknown parameter type: ", type, " for ", name, " in shader ", this->m_file);
	return;
    }

    if (material.has_value () && parameter != nullptr) {
	parameter->setIdentifierName (*material);
	parameter->setName (name);

	this->m_parameters.push_back (parameter);
    }
}

const ComboMap& ShaderUnit::getCombos () const { return this->m_combos; }

const ComboMap& ShaderUnit::getDiscoveredCombos () const { return this->m_discoveredCombos; }

ShaderUnit::~ShaderUnit () {
    for (const auto* parameter : this->m_parameters) {
	delete parameter;
    }
}

void ShaderUnit::linkToUnit (const ShaderUnit* unit) { this->m_link = unit; }

const ShaderUnit* ShaderUnit::getLinkedUnit () const { return this->m_link; }

const std::string& ShaderUnit::compile () {
    if (!this->m_final.empty ()) {
	return this->m_final;
    }

    this->m_final = SHADER_HEADER (this->m_file);

    if (this->m_type == GLSLContext::UnitType_Fragment) {
	this->m_final += FRAGMENT_SHADER_DEFINES;
    } else {
	this->m_final += VERTEX_SHADER_DEFINES;
    }

    std::map<std::string, bool> addedCombos;

    static const char* s_forceCombo = getenv ("LWE_FORCECOMBO");
    if (s_forceCombo != nullptr) {
	const std::string spec = s_forceCombo;
	if (const auto eq = spec.find ('='); eq != std::string::npos) {
	    std::string uppercase;
	    std::ranges::transform (spec.substr (0, eq), std::back_inserter (uppercase), ::toupper);
	    const int value = atoi (spec.c_str () + eq + 1);
	    this->m_final += DEFINE_COMBO (uppercase, value);
	    addedCombos.emplace (uppercase, true);
	    static bool s_logged = false;
	    if (!s_logged) {
		s_logged = true;
		sLog.out ("LWE-FORCECOMBO forcing ", uppercase, "=", value, " on all shader units");
	    }
	}
    }

    for (const auto& [name, value] : this->m_overrideCombos) {
	std::string uppercase;
	std::ranges::transform (name, std::back_inserter (uppercase), ::toupper);

	if (!addedCombos.contains (uppercase)) {
	    this->m_final += DEFINE_COMBO (uppercase, value);
	    addedCombos.emplace (uppercase, true);
	}
    }

    // now add all the combos to the source
    for (const auto& [name, value] : this->m_combos) {
	std::string uppercase;
	std::ranges::transform (name, std::back_inserter (uppercase), ::toupper);

	if (!addedCombos.contains (uppercase)) {
	    this->m_final += DEFINE_COMBO (uppercase, value);
	    addedCombos.emplace (uppercase, true);
	}
    }

    for (const auto& [name, value] : this->m_discoveredCombos) {
	std::string uppercase;
	std::ranges::transform (name, std::back_inserter (uppercase), ::toupper);

	if (!addedCombos.contains (uppercase)) {
	    this->m_final += DEFINE_COMBO (uppercase, value);
	    addedCombos.emplace (uppercase, true);
	}
    }

    if (this->m_link != nullptr) {
	for (const auto& [name, value] : this->m_link->getCombos ()) {
	    std::string uppercase;
	    std::ranges::transform (name, std::back_inserter (uppercase), ::toupper);

	    if (!addedCombos.contains (uppercase)) {
		this->m_final += DEFINE_COMBO (uppercase, value);
		addedCombos.emplace (uppercase, true);
	    }
	}

	for (const auto& [name, value] : this->m_link->getDiscoveredCombos ()) {
	    std::string uppercase;
	    std::ranges::transform (name, std::back_inserter (uppercase), ::toupper);

	    if (!addedCombos.contains (uppercase)) {
		this->m_final += DEFINE_COMBO (uppercase, value);
		addedCombos.emplace (uppercase, true);
	    }
	}
    }

    // this should be the rest of the shader
    this->m_final += this->applyFragmentTexCoordCompatibility (
	this->applyTextureResolutionSwizzleCompatibility (this->applyLinkedVaryingCompatibility (this->m_preprocessed))
    );

    static const char* s_shaderDump = getenv ("LWE_SHADERDUMP_MATCH");
    if (s_shaderDump != nullptr && s_shaderDump[0] != '\0' && this->m_file.find (s_shaderDump) != std::string::npos) {
	const char* home = getenv ("HOME");
	if (home != nullptr) {
	    std::string safeName;
	    for (const char c : this->m_file) {
		safeName += (isalnum (c) != 0 ? c : '_');
	    }
	    const std::string path = std::string (home) + "/.local/state/lwe/shaderdump-" + safeName
		+ (this->m_type == GLSLContext::UnitType_Vertex ? ".vert" : ".frag") + ".glsl";
	    std::ofstream out (path, std::ios::trunc);
	    out << this->m_final;
	    sLog.out ("LWE-SHADERDUMP wrote ", path);
	}
    }

    // the pass itself handles shader compilation, the unit doesn't have enough information for this step
    return this->m_final;
}

const std::vector<Variables::ShaderVariable*>& ShaderUnit::getParameters () const { return this->m_parameters; }
const TextureMap& ShaderUnit::getTextures () const { return this->m_defaultTextures; }

const std::map<int, glm::vec4>& ShaderUnit::getPaintDefaultColors () const { return this->m_paintDefaultColors; }
