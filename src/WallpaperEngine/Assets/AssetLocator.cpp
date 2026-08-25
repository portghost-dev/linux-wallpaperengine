#include "AssetLocator.h"

#include "AssetLoadException.h"

#include "WallpaperEngine/FileSystem/Adapters/MediaCover.h"
#include "WallpaperEngine/Media/MediaSource.h"

using namespace WallpaperEngine::Assets;

AssetLocator::AssetLocator (ContainerUniquePtr filesystem) : m_filesystem (std::move (filesystem)) { }

std::string AssetLocator::shader (const std::filesystem::path& filename) const {
    try {
	std::filesystem::path shader = filename;

	// detect workshop shaders and check if there's a
	if (auto it = shader.begin (); *it++ == "workshop") {
	    const std::filesystem::path workshopId = *it++;

	    if (++it != shader.end ()) {
		const std::filesystem::path& shaderfile = *it;

		try {
		    shader = std::filesystem::path ("zcompat") / "scene" / "shaders" / workshopId / shaderfile;
		    // replace the old path with the new one
		    std::string contents = this->m_filesystem->readString (shader);

		    sLog.out ("Replaced ", filename, " with compat ", shader);

		    return contents;
		} catch (std::filesystem::filesystem_error&) {
		    // these exceptions can be ignored because the replacement file might not exist
		}
	    }
	}

	return this->m_filesystem->readString ("shaders" / filename);
    } catch (std::filesystem::filesystem_error& base) {
	throw AssetLoadException (base);
    }
}

std::string AssetLocator::fragmentShader (const std::filesystem::path& filename) const {
    auto final = filename;

    final.replace_extension ("frag");

    return this->shader (final);
}

std::string AssetLocator::vertexShader (const std::filesystem::path& filename) const {
    auto final = filename;

    final.replace_extension ("vert");

    return this->shader (final);
}

std::string AssetLocator::includeShader (const std::filesystem::path& filename) const {
    auto final = filename;

    final.replace_extension ("h");

    return this->shader (final);
}

VirtualAdapter& AssetLocator::getVFS () const { return this->m_filesystem->getVFS (); }

std::string AssetLocator::readString (const std::filesystem::path& filename) const {
    try {
	return this->m_filesystem->readString (filename);
    } catch (std::filesystem::filesystem_error& base) {
	throw AssetLoadException (base);
    }
}

ReadStreamSharedPtr AssetLocator::texture (const std::filesystem::path& filename) const {
    const auto final = std::filesystem::path ("materials") / filename.string ().append (".tex");

    try {
	return this->m_filesystem->read (final);
    } catch (std::filesystem::filesystem_error& base) {
	throw AssetLoadException (base);
    }
}

ReadStreamSharedPtr AssetLocator::read (const std::filesystem::path& path) const {
    try {
	return this->m_filesystem->read (path);
    } catch (std::filesystem::filesystem_error& base) {
	throw AssetLoadException (base);
    }
}

std::filesystem::path AssetLocator::physicalPath (const std::filesystem::path& path) const {
    try {
	return this->m_filesystem->physicalPath (path);
    } catch (std::filesystem::filesystem_error& base) {
	throw AssetLoadException (base);
    }
}

AssetLocatorUniquePtr WallpaperEngine::Assets::setupAssetLocator (
    const std::string& bg, const std::filesystem::path& assetsPath, Media::MediaSource& mediaSource
) {
    auto container = std::make_unique<Container> ();

    const std::filesystem::path path = bg;

    container->registerAdapterFactory (std::make_unique<MediaCoverFactory> (mediaSource));
    container->mount ("$mediaThumbnail", "$mediaThumbnail");
    container->mount (path, "/");

    std::error_code packageError;
    for (const auto& entry : std::filesystem::directory_iterator (path, packageError)) {
	if (entry.path ().extension () != ".pkg") {
	    continue;
	}

	try {
	    container->mount (entry.path (), "/");
	} catch (std::runtime_error&) { }
    }

    try {
	container->mount (assetsPath, "/");
    } catch (std::runtime_error&) {
	sLog.exception ("Cannot find a valid assets folder, resolved to ", assetsPath);
    }

    std::error_code effectsError;
    for (const auto& effect : std::filesystem::directory_iterator (assetsPath / "effects", effectsError)) {
	if (effect.is_directory (effectsError) == false) {
	    continue;
	}

	try {
	    container->mount (effect.path (), "/");
	} catch (std::runtime_error&) { }
    }

    // mount the current directory as root
    try {
	container->mount (std::filesystem::current_path (), "/");
    } catch (std::runtime_error&) { }

    auto& vfs = container->getVFS ();

    //
    // Had to get a little creative with the effects to achieve the same bloom effect without any custom code
    // these virtual files are loaded by an image in the scene that takes current _rt_FullFrameBuffer and
    // applies the bloom effect to render it out to the screen
    //

    // add the effect file for screen bloom

    // add some model for the image element even if it's going to waste rendering cycles
    vfs.add (
	"effects/wpenginelinux/bloomeffect.json",
	{ { "name", "camerabloom_wpengine_linux" },
	  { "group", "wpengine_linux_camera" },
	  { "dependencies", JSON::array () },
	  {
	      "passes",
	      JSON::array (
		  { { { "material", "materials/util/downsample_quarter_bloom.json" },
		      { "target", "_rt_4FrameBuffer" },
		      { "bind", JSON::array ({ { { "name", "_rt_FullFrameBuffer" }, { "index", 0 } } }) } },
		    { { "material", "materials/util/downsample_eighth_blur_v.json" },
		      { "target", "_rt_8FrameBuffer" },
		      { "bind", JSON::array ({ { { "name", "_rt_4FrameBuffer" }, { "index", 0 } } }) } },
		    { { "material", "materials/util/blur_h_bloom.json" },
		      { "target", "_rt_Bloom" },
		      { "bind", JSON::array ({ { { "name", "_rt_8FrameBuffer" }, { "index", 0 } } }) } },
		    { { "material", "materials/util/combine.json" },
		      { "target", "_rt_FullFrameBuffer" },
		      { "bind",
			JSON::array (
			    { { { "name", "_rt_imageLayerComposite_-1_a" }, { "index", 0 } },
			      { { "name", "_rt_Bloom" }, { "index", 1 } } }
			) } } }
	      ),
	  } }
    );

    try {
	std::string hdrFrag = container->readString ("shaders/hdr_downsample.frag");
	const std::string rawUniform = "uniform vec4 g_RenderVar0;";
	const std::string annotated
	    = "uniform vec4 g_RenderVar0; // {\"material\":\"rendervar0\",\"default\":\"0 0 0 0\"}";
	const auto pos = hdrFrag.find (rawUniform);
	if (pos == std::string::npos) {
	    sLog.error ("hdr_downsample.frag: g_RenderVar0 declaration not found - HDR bloom disabled");
	} else {
	    hdrFrag.replace (pos, rawUniform.size (), annotated);
	    vfs.add ("shaders/wpelinux_hdr_downsample.frag", hdrFrag);
	    vfs.add ("shaders/wpelinux_hdr_downsample.vert", container->readString ("shaders/hdr_downsample.vert"));

	    const JSON passCommon
		= { { "cullmode", "nocull" }, { "depthtest", "disabled" }, { "depthwrite", "disabled" } };
	    auto pass = [&] (const JSON& extra) {
		JSON p = passCommon;
		p.update (extra);
		return JSON { { "passes", JSON::array ({ p }) } };
	    };
	    vfs.add (
		"materials/wpelinux/hdr_prefilter.json",
		pass (
		    { { "shader", "wpelinux_hdr_downsample" },
		      { "blending", "normal" },
		      { "combos", { { "BLOOM", 1 } } } }
		)
	    );
	    vfs.add (
		"materials/wpelinux/hdr_down.json",
		pass ({ { "shader", "wpelinux_hdr_downsample" }, { "blending", "normal" } })
	    );
	    vfs.add (
		"materials/wpelinux/hdr_up.json",
		pass (
		    { { "shader", "wpelinux_hdr_downsample" },
		      { "blending", "additive" },
		      { "combos", { { "UPSAMPLE", 1 }, { "BICUBIC", 1 } } } }
		)
	    );
	    std::string combineFrag = container->readString ("shaders/combine_hdr.frag");
	    const std::string rawTexel = "uniform vec2 g_TexelSize;";
	    const std::string annotatedTexel
		= "uniform vec2 g_TexelSize; // {\"material\":\"texelsize\",\"default\":\"0 0\"}";
	    const auto texelPos = combineFrag.find (rawTexel);
	    if (texelPos != std::string::npos) {
		combineFrag.replace (texelPos, rawTexel.size (), annotatedTexel);
	    }
	    vfs.add ("shaders/wpelinux_combine_hdr.frag", combineFrag);
	    vfs.add ("shaders/wpelinux_combine_hdr.vert", container->readString ("shaders/combine_hdr.vert"));
	    vfs.add (
		"materials/wpelinux/hdr_combine.json",
		pass (
		    { { "shader", "wpelinux_combine_hdr" },
		      { "blending", "normal" },
		      { "combos", { { "LINEAR", 1 } } } }
		)
	    );
	}
    } catch (const std::exception& e) {
	sLog.error ("Failed to register HDR bloom shaders: ", e.what ());
    }

    vfs.add ("models/wpenginelinux.json", { { "material", "materials/wpenginelinux.json" } });

    vfs.add (
	"materials/wpenginelinux.json",
	{ { "passes",
	    JSON::array (
		{ { { "blending", "normal" },
		    { "cullmode", "nocull" },
		    { "depthtest", "disabled" },
		    { "depthwrite", "disabled" },
		    { "shader", "genericimage2" },
		    { "textures", JSON::array ({ "_rt_FullFrameBuffer" }) } } }
	    ) } }
    );

    vfs.add (
	"shaders/commands/copy.frag",
	"uniform sampler2D g_Texture0;\n"
	"in vec2 v_TexCoord;\n"
	"void main () {\n"
	"out_FragColor = texture (g_Texture0, v_TexCoord);\n"
	"}"
    );
    vfs.add (
	"shaders/commands/copy.vert",
	"in vec3 a_Position;\n"
	"in vec2 a_TexCoord;\n"
	"out vec2 v_TexCoord;\n"
	"void main () {\n"
	"gl_Position = vec4 (a_Position, 1.0);\n"
	"v_TexCoord = a_TexCoord;\n"
	"}"
    );

    vfs.add (
	"models/wpenginelinux_shape.json",
	{ { "material", "materials/wpenginelinux_shape.json" }, { "solidlayer", true } }
    );
    vfs.add (
	"materials/wpenginelinux_shape.json",
	{ { "passes",
	    JSON::array (
		{ { { "blending", "translucent" },
		    { "cullmode", "nocull" },
		    { "depthtest", "disabled" },
		    { "depthwrite", "disabled" },
		    { "shader", "commands/transparent" } } }
	    ) } }
    );
    vfs.add (
	"shaders/commands/transparent.frag",
	"void main () {\n"
	"out_FragColor = vec4 (0.0);\n"
	"}"
    );
    vfs.add (
	"shaders/commands/transparent.vert",
	"uniform mat4 g_ModelViewProjectionMatrix;\n"
	"in vec3 a_Position;\n"
	"void main () {\n"
	"gl_Position = g_ModelViewProjectionMatrix * vec4 (a_Position, 1.0);\n"
	"}"
    );

    return std::make_unique<AssetLocator> (std::move (container));
}
