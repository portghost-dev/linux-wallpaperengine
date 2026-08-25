#include "MipResidency.h"
#include "CTexture.h"
#include "RenderContext.h"
#include "WallpaperEngine/Application/WallpaperApplication.h"
#include "WallpaperEngine/Data/Model/Object.h"
#include "WallpaperEngine/Data/Model/Wallpaper.h"
#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/Render/Drivers/Output/Output.h"

#include <algorithm>
#include <cstdlib>
#include <map>
#include <mutex>
#include <ranges>
#include <set>

using namespace WallpaperEngine::Data::Model;

namespace {
const std::set<std::string> kAllowlist = {
    "genericimage", "genericimage2", "genericimage3", "genericimage4", "generic4", "chroma4",
};

// name -> may cap. Additive-restrictive across scenes: the TextureCache outlives any
// one scene, so `false` is permanent for the process; `true` may later be demoted.
std::map<std::string, bool> g_verdicts;
std::mutex g_lock;

bool allowlisted (const std::string& shader) { return kAllowlist.contains (shader); }

void recordPass (const std::string& shader, const TextureMap& textures, const bool objectEligible) {
    static const bool debug = getenv ("LWE_MIPRESIDENCY_DEBUG") != nullptr;
    const bool ok = objectEligible && allowlisted (shader);
    for (const auto& [slot, name] : textures) {
	if (name.empty ()) {
	    continue;
	}
	auto [it, inserted] = g_verdicts.emplace (name, ok);
	if (!inserted && !ok) {
	    it->second = false;
	}
	if (debug) {
	    sLog.out (
		"LWE-MIPRESIDENCY verdict: '", name, "' shader='", shader, "' objectEligible=", objectEligible, " -> ",
		it->second ? "cappable" : "demoted"
	    );
	}
    }
}
} // namespace

namespace WallpaperEngine::Render::MipResidency {
bool enabled () {
    // unset means auto: capping is the default, "full" asks for the authored chain
    static const char* mode = getenv ("LWE_TEXDETAIL");
    return mode == nullptr || std::string (mode) == "auto";
}

int capDimension (const int liveOutputMax) {
    // the env override is a TEST knob only; the product path is the live output query
    static const int override_ = [] () {
	if (const char* env = getenv ("LWE_TEXCAP"); env != nullptr && *env != '\0') {
	    const int v = atoi (env);
	    if (v >= 256) {
		return v;
	    }
	}
	return 0;
    }();
    if (override_ > 0) {
	return override_;
    }
    return liveOutputMax >= 256 ? liveOutputMax : 4096;
}

int largestOutputDimension (const RenderContext& context) {
    const auto mode = context.getApp ().getContext ().settings.render.mode;
    if (mode != Application::ApplicationContext::DESKTOP_BACKGROUND) {
	return 0;
    }
    int best = 0;
    for (const auto& vp : context.getOutput ().getViewports () | std::views::values) {
	best = std::max ({ best, vp->viewport.z, vp->viewport.w });
    }
    return best;
}

void buildReferenceMap (const Scene& scene) {
    if (!enabled ()) {
	return;
    }
    std::lock_guard lock (g_lock);
    size_t images = 0;
    for (const auto& object : scene.objects) {
	const auto* image = dynamic_cast<const Image*> (object.get ());
	if (image == nullptr) {
	    continue;
	}
	images++;
	const bool objectEligible = image->animationLayers.empty () && !image->perspective;

	if (image->model != nullptr && image->model->material != nullptr) {
	    for (const auto& pass : image->model->material->passes) {
		recordPass (pass->shader, pass->textures, objectEligible);
		recordPass (pass->shader, pass->usertextures, objectEligible);
	    }
	}

	// effect passes: the effect's own material shader (override-aware) + its binds.
	// A shader override that is not itself allowlisted demotes conservatively.
	for (const auto& effect : image->effects) {
	    if (effect->effect == nullptr) {
		continue;
	    }
	    size_t passIndex = 0;
	    for (const auto& pass : effect->effect->passes) {
		std::string shader;
		if (pass->material.has_value () && *pass->material != nullptr && !(*pass->material)->passes.empty ()) {
		    shader = (*pass->material)->passes.front ()->shader;
		}
		for (const auto& override_ : effect->passOverrides) {
		    if (static_cast<size_t> (override_->id) == passIndex && override_->shaderOverride.has_value ()) {
			shader = *override_->shaderOverride;
		    }
		}
		recordPass (shader, pass->binds, objectEligible);
		if (pass->material.has_value () && *pass->material != nullptr) {
		    for (const auto& matPass : (*pass->material)->passes) {
			recordPass (shader.empty () ? matPass->shader : shader, matPass->textures, objectEligible);
			recordPass (shader.empty () ? matPass->shader : shader, matPass->usertextures, objectEligible);
		    }
		}
		passIndex++;
	    }
	    // pass-override texture swaps consume with the (possibly overridden) shader
	    for (const auto& override_ : effect->passOverrides) {
		const std::string shader
		    = override_->shaderOverride.has_value () ? *override_->shaderOverride : std::string ();
		recordPass (shader, override_->textures, objectEligible);
		recordPass (shader, override_->usertextures, objectEligible);
	    }
	}
    }
    size_t cappableCount = 0;
    for (const auto& [name, ok] : g_verdicts) {
	if (ok) {
	    cappableCount++;
	}
    }
    sLog.out (
	"LWE-MIPRESIDENCY map: ", images, " image objects walked, ", g_verdicts.size (), " textures seen, ",
	cappableCount, " cappable (cap=live-per-load)"
    );
}

bool cappable (const std::string& textureName) {
    if (!enabled ()) {
	return false;
    }
    std::lock_guard lock (g_lock);
    const auto it = g_verdicts.find (textureName);
    return it != g_verdicts.end () && it->second;
}

void maybeExpand (const void* textureProvider, const float quadWidth, const float quadHeight, const int liveCap) {
    if (!enabled () || textureProvider == nullptr) {
	return;
    }
    // 2% margin over the cap: sub-pixel scale jitter must not churn rebuilds
    const float threshold = static_cast<float> (capDimension (liveCap)) * 1.02f;
    if (quadWidth <= threshold && quadHeight <= threshold) {
	return;
    }
    expandCappedTexture (static_cast<const TextureProvider*> (textureProvider));
}
}
