#pragma once

#include <string>

namespace WallpaperEngine::Data::Model {
class Scene;
}

namespace WallpaperEngine::Render {
class RenderContext;
}

namespace WallpaperEngine::Render::MipResidency {

bool enabled ();

int capDimension (int liveOutputMax);

/** Largest single-output dimension from live viewports; 0 when unavailable.
 *  Same source as CScene::clampToCap's largestOutputSize - max across outputs,
 *  never per-output (mixed-resolution fleets size to the biggest panel). */
int largestOutputDimension (const RenderContext& context);

void buildReferenceMap (const Data::Model::Scene& scene);

bool cappable (const std::string& textureName);

/** Demand trigger, called per image per frame with the object's on-canvas quad size.
 *  Under LWE_SSFACTOR the canvas is output-sized, so a quad larger than the cap is
 *  literal magnification past the resident texels - the only trigger condition, since
 *  every capped texture is resident at >= the cap. Fast path is two float compares. */
void maybeExpand (const void* textureProvider, float quadWidth, float quadHeight, int liveOutputMax);
}
