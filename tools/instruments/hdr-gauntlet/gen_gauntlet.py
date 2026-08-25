#!/usr/bin/env python3
"""Generates the HDR-bloom calibration gauntlet: a hand-authored scene with
five fixture plates, plus knob-matrix variants.

Plates on a 1920x1080 black canvas:
  1. IMPULSE STRIP: 16 4x4-px emitters, x-spaced 96px @ y=270, brightness
     ramp 0.4 -> 1.9 (via layer color value below 1.0 and brightness above).
  2. PHASE PAIR: two 4x4 emitters @ y=135: one texel-grid-aligned, one
     offset by half a texel (odd origin) - halo-centroid drift test.
  3. HARD EDGE: 0<->1 white half-rect (x 0..960 white, rest black) band
     y 540..675 - sRGB-vs-linear blur midpoint test.
  4. RED PLATE: pure (1,0,0) 200x135 @ (1440,607) brightness 3 - clip/hue
     shift test.
  5. GRAY PLATES: flat 0.5 gray 200x135 @ (240,878) (rounding/banding) +
     below-threshold ramp strip y=1012 (5 patches 0.05..0.30 - zero-leak).

Each emitter uses models/util/solidlayer.json with authored color x
brightness; brightness > 1 is the WE per-layer "HDR brightness".

Usage:
  gen_gauntlet.py OUTDIR                    # center-point scene only
  gen_gauntlet.py OUTDIR --matrix           # + OFAT knob sweep variants
Output: OUTDIR/<variant>/project.json + scene.json (loose files; both our
engine and Windows WE load unpacked projects).
"""
import json, os, sys, itertools

W, H = 1920, 1080

CENTER = dict(iterations=8, scatter=1.0, feather=0.5, strength=2.0, threshold=0.7)
SWEEPS = {
    'iterations': [1, 2, 3, 4, 8],
    'scatter':    [0.2, 0.5, 1.0, 1.62, 2.0],
    'feather':    [0.0, 0.25, 0.5, 0.75, 1.0],
    'strength':   [0.5, 1.0, 2.0, 5.0, 10.0],
    'threshold':  [0.4, 0.55, 0.7, 0.85, 1.0],
}
# designed interaction blocks: knee shape + energy distribution
INTERACTIONS = [
    ('thrXfea', 'threshold', [0.55, 1.0], 'feather', [0.1, 0.9]),
    ('itXsc',   'iterations', [2, 8],     'scatter', [0.5, 2.0]),
]


def solid(idx, name, origin, size, color, brightness=1.0, visible=True):
    # design coords are top-down; authored scene origins are y-UP from the
    # bottom-left (validated: red plate probed 127px high before this flip)
    origin = (origin[0], H - origin[1])
    return {
        "alignment": "center", "alpha": 1.0,
        "angles": "0.00000 0.00000 0.00000",
        "brightness": brightness,
        "color": f"{color[0]:.5f} {color[1]:.5f} {color[2]:.5f}",
        "colorBlendMode": 0, "copybackground": False, "id": idx,
        "image": "models/util/solidlayer.json", "ledsource": False,
        "locktransforms": True, "name": name,
        "origin": f"{origin[0]:.5f} {origin[1]:.5f} 0.00000",
        "parallaxDepth": "0.00000 0.00000", "perspective": False,
        "scale": "1.00000 1.00000 1.00000",
        "size": f"{size[0]:.5f} {size[1]:.5f}",
        "solid": True, "visible": visible,
    }


def build_objects():
    objs, idx = [], 10
    # background: full-canvas black (keeps composite defined everywhere)
    objs.append(solid(idx, "bg_black", (W / 2, H / 2), (W, H), (0, 0, 0))); idx += 1
    # 1. impulse strip: 16 emitters, intensity 0.4..1.9 in 0.1 steps
    for i in range(16):
        inten = 0.4 + 0.1 * i
        col = min(inten, 1.0)
        bri = inten / col  # brightness carries the >1 part (WE HDR brightness)
        # even 96px grid -> texel-aligned at every ladder step's even divisor
        objs.append(solid(idx, f"impulse_{inten:.1f}", (96 + 96 * i, 270), (4, 4),
                          (col, col, col), round(bri, 5))); idx += 1
    # 2. phase pair: aligned (x=512, even) vs half-offset (x=1024+0.5)
    objs.append(solid(idx, "phase_aligned", (512, 135), (4, 4), (1, 1, 1), 1.5)); idx += 1
    objs.append(solid(idx, "phase_offset", (1024.5, 135.5), (4, 4), (1, 1, 1), 1.5)); idx += 1
    # 3. hard edge: white half-rect band
    objs.append(solid(idx, "edge_white", (480, 607.5), (960, 135), (1, 1, 1))); idx += 1
    # 4. red plate
    objs.append(solid(idx, "red_plate", (1440, 607.5), (200, 135), (1, 0, 0), 3.0)); idx += 1
    # 5. gray flat + below-threshold ramp
    objs.append(solid(idx, "gray_flat", (240, 878), (200, 135), (0.5, 0.5, 0.5))); idx += 1
    for i, v in enumerate((0.05, 0.10, 0.15, 0.20, 0.30)):
        objs.append(solid(idx, f"ramp_{v:.2f}", (720 + 180 * i, 1012), (120, 60),
                          (v, v, v))); idx += 1
    return objs


def scene_json(k):
    return {
        "camera": {"center": f"{W/2:.5f} {H/2:.5f} -1.00000",
                   "eye": f"{W/2:.5f} {H/2:.5f} 0.00000",
                   "up": "0.00000 1.00000 0.00000"},
        "general": {
            "ambientcolor": "1.00000 1.00000 1.00000",
            "bloom": True,
            "bloomstrength": 0.0,          # classic bloom parked at zero
            "bloomthreshold": 1.0,
            "bloomhdriterations": k['iterations'],
            "bloomhdrscatter": k['scatter'],
            "bloomhdrfeather": k['feather'],
            "bloomhdrstrength": k['strength'],
            "bloomhdrthreshold": k['threshold'],
            "hdr": True,
            "camerafade": False, "cameraparallax": False, "camerashake": False,
            "clearcolor": "0.00000 0.00000 0.00000", "clearenabled": True,
            "farz": 10000, "fov": 50, "nearz": 0.01,
            "orthogonalprojection": {"width": W, "height": H},
            "skylightcolor": "0.00000 0.00000 0.00000",
            "zoom": 1.0,
        },
        "objects": build_objects(),
        "version": 1,
    }


def project_json(title):
    return {"title": title, "type": "scene", "file": "scene.json",
            "general": {"properties": {}}, "contentrating": "Everyone"}


def emit(outdir, name, knobs):
    d = os.path.join(outdir, name)
    os.makedirs(d, exist_ok=True)
    json.dump(project_json(f"gauntlet {name}"), open(os.path.join(d, 'project.json'), 'w'), indent=1)
    json.dump(scene_json(knobs), open(os.path.join(d, 'scene.json'), 'w'), indent=1)
    return name


def main():
    outdir = sys.argv[1]
    names = [emit(outdir, 'center', CENTER)]
    if '--matrix' in sys.argv:
        for knob, values in SWEEPS.items():
            for v in values:
                if v == CENTER[knob]:
                    continue
                k = dict(CENTER); k[knob] = v
                names.append(emit(outdir, f"{knob}_{v}", k))
        for tag, ka, va, kb, vb in INTERACTIONS:
            for a, b in itertools.product(va, vb):
                k = dict(CENTER); k[ka] = a; k[kb] = b
                names.append(emit(outdir, f"{tag}_{a}_{b}", k))
    json.dump(names, open(os.path.join(outdir, 'MANIFEST.json'), 'w'), indent=1)
    print(f"{len(names)} variant(s) -> {outdir}")


if __name__ == '__main__':
    main()
