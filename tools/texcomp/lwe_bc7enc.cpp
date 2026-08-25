// lwe_bc7enc - minimal BC7/BC4/BC5 encoder shim over ISPCTextureCompressor.
// stdin : [u32 width][u32 height][u32 fmt (7=BC7,4=BC4,5=BC5)][RGBA8 pixels = width*height*4 bytes]
// stdout: raw compressed blocks (raster order). Encodes at MAX BC7 quality (GetProfile_alpha_slow).
// Pads to a multiple of 4 by edge replication so the block count = ceil(w/4)*ceil(h/4); the engine
// uploads with the ORIGINAL width/height (from .meta) so the padding is transparent to GL.
#include "ispc_texcomp.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

static uint32_t rd32 () {
    uint32_t v = 0;
    if (fread (&v, 4, 1, stdin) != 1) {
	fprintf (stderr, "lwe_bc7enc: short read\n");
	exit (2);
    }
    return v;
}

int main () {
    const uint32_t w = rd32 (), h = rd32 (), fmt = rd32 ();
    if (w == 0 || h == 0 || w > 16384 || h > 16384) {
	fprintf (stderr, "lwe_bc7enc: bad dims %ux%u\n", w, h);
	return 2;
    }
    std::vector<uint8_t> rgba ((size_t)w * h * 4);
    if (fread (rgba.data (), 1, rgba.size (), stdin) != rgba.size ()) {
	fprintf (stderr, "lwe_bc7enc: short pixel read\n");
	return 2;
    }
    const uint32_t pw = (w + 3u) & ~3u;
    const uint32_t ph = (h + 3u) & ~3u;
    std::vector<uint8_t> padded ((size_t)pw * ph * 4);
    for (uint32_t y = 0; y < ph; y++) {
	const uint32_t sy = (y < h) ? y : h - 1;
	for (uint32_t x = 0; x < pw; x++) {
	    const uint32_t sx = (x < w) ? x : w - 1;
	    memcpy (&padded[((size_t)y * pw + x) * 4], &rgba[((size_t)sy * w + sx) * 4], 4);
	}
    }
    rgba_surface surf;
    surf.ptr = padded.data ();
    surf.width = (int32_t)pw;
    surf.height = (int32_t)ph;
    surf.stride = (int32_t)(pw * 4);

    const uint32_t blocks = (pw / 4) * (ph / 4);
    const uint32_t bpb = (fmt == 4) ? 8u : 16u; // BC4=8, BC5/BC7=16 bytes/block
    std::vector<uint8_t> dst ((size_t)blocks * bpb);

    if (fmt == 7) {
	bc7_enc_settings s;
	GetProfile_alpha_slow (&s); // ingest-time-once -> max quality
	CompressBlocksBC7 (&surf, dst.data (), &s);
    } else if (fmt == 4) {
	CompressBlocksBC4 (&surf, dst.data ());
    } else if (fmt == 5) {
	CompressBlocksBC5 (&surf, dst.data ());
    } else {
	fprintf (stderr, "lwe_bc7enc: bad fmt %u\n", fmt);
	return 2;
    }
    fwrite (dst.data (), 1, dst.size (), stdout);
    return 0;
}
