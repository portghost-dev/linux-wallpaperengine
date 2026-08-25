#!/usr/bin/env python3
"""Facet-dial generator - the first 'authored instrument' (PROBE-DESIGN class).

Emits a self-contained WE scene directory: a .mdl of N flat billboard plates
(geometry faces the camera for legibility; each plate carries an AUTHORED
per-vertex normal = its probe direction), a color-key palette .tex
(plates self-identify by hue; 3 channels/plate), a matte generic4
material (no specular contamination), and a scene.json template.

Readout: mean RGB of each plate's fixed screen rect -> known-normal linear
solve for light direction; away-plates read the ambient/skylight floor and
test the Lambert assumption instead of assuming it.

Container layout follows the shipped MDLV0023 format (validated parse to EOF):
  magic cstr | u32 15 | u32 1 | u32 1 | material cstr | u32 0 | f32 bounds[6]
  | u32 15 | u32 vertexBytes | 48B vertices (pos3 normal3 tangent4 uv2)
  | u32 indexBytes | u16 indices | 7 zero bytes
Usage: gen_dial.py <outdir>   (creates <outdir>/ as a runnable wallpaper dir)
"""
import json
import math
import pathlib
import struct
import sys

S3 = 1.0 / math.sqrt(3.0)
S2 = 1.0 / math.sqrt(2.0)


def _ring(prefix, theta_deg, azimuths):
    th = math.radians(theta_deg)
    out = []
    for az in azimuths:
        a = math.radians(az)
        out.append((f'{prefix}{az:03d}',
                    (round(math.sin(th)*math.cos(a), 6),
                     round(math.sin(th)*math.sin(a), 6),
                     round(math.cos(th), 6))))
    return out


# v2 (2026-08-01): rings at polar 0/40/70/90/120/150 deg from +Z -
# dense lit coverage at 40/70, a full EDGE-ON ring at 90 (N.V=0, view-term
# probes), away plates at 120/150 (floor + Lambert wrap). Plus a 4-step
# gray ALBEDO LADDER on identical +Z normals: reads the output transfer
# curve (gamma/tonemap) deterministically in every capture.
PLATES = (
    [('TOP', (0.0, 0.0, 1.0))]
    + _ring('A', 40, (0, 90, 180, 270))
    + _ring('B', 70, (0, 45, 90, 135, 180, 225, 270, 315))
    + _ring('E', 90, (0, 90, 180, 270))
    + _ring('W', 120, (0, 120, 240))
    + [('AWAY', (0.0, 0.0, -1.0))]
    + [('L25', (0.0, 0.0, 1.0)), ('L50', (0.0, 0.0, 1.0)),
       ('L75', (0.0, 0.0, 1.0)), ('L100', (0.0, 0.0, 1.0))]
)
LADDER = {'L25': 64, 'L50': 128, 'L75': 191, 'L100': 255}

# Color keys: distinct triples (nearest-match identity); ladder plates = grays
def _make_colors(n):
    base = [
        (255, 32, 32), (32, 255, 32), (64, 64, 255), (255, 255, 32),
        (255, 32, 255), (32, 255, 255), (255, 144, 32), (144, 32, 255),
        (32, 144, 255), (255, 255, 255), (144, 255, 32), (255, 32, 144),
        (32, 255, 144), (255, 144, 144), (144, 144, 255), (192, 255, 192),
        (255, 208, 96), (96, 208, 255), (208, 96, 255), (160, 160, 32),
        (32, 160, 160), (160, 32, 160), (96, 255, 208), (255, 96, 64),
    ]
    return base[:n]


COLORS = _make_colors(len([p for p in PLATES if p[0] not in LADDER]))
COLORS = COLORS + [(LADDER[nm],)*3 for nm, _ in PLATES if nm in LADDER]

COLS, ROWS = 5, 5
PLATE, GAP = 400.0, 100.0  # scene units


def build_mdl(matpath: str) -> bytes:
    verts, idx = [], []
    total_w = COLS * PLATE + (COLS - 1) * GAP
    total_h = ROWS * PLATE + (ROWS - 1) * GAP
    for i, (_, n) in enumerate(PLATES):
        cx = -total_w / 2 + (i % COLS) * (PLATE + GAP) + PLATE / 2
        cy = total_h / 2 - (i // COLS) * (PLATE + GAP) - PLATE / 2
        # palette texel center for this plate (16x1 strip)
        u = (i + 0.5) / 32.0
        base = len(verts)
        for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            px, py = cx + dx * PLATE / 2, cy + dy * PLATE / 2
            # pos3, AUTHORED normal3, tangent4 (any unit perp; shader needs it), uv2
            verts.append((px, py, 0.0, *n, 1.0, 0.0, 0.0, -1.0, u, 0.5))
        idx += [base, base + 1, base + 2, base, base + 2, base + 3]

    vb = b''.join(struct.pack('<12f', *v) for v in verts)
    ib = b''.join(struct.pack('<H', i) for i in idx)
    ext = max(total_w, total_h) / 2 + 1
    out = b'MDLV0023\x00'
    out += struct.pack('<III', 15, 1, 1)
    out += matpath.encode() + b'\x00'
    out += struct.pack('<I', 0)
    out += struct.pack('<6f', -ext, -ext, -1.0, ext, ext, 1.0)
    out += struct.pack('<I', 15)
    out += struct.pack('<I', len(vb)) + vb
    out += struct.pack('<I', len(ib)) + ib
    out += b'\x00' * 7
    return out


def build_tex() -> bytes:
    """32x1 rgba8888 palette strip, TEXV0005, uncompressed."""
    w, h = 32, 1
    px = bytearray()
    for i in range(w):
        r, g, b = COLORS[i] if i < len(COLORS) else (0, 0, 0)
        px += bytes((r, g, b, 255))
    out = b'TEXV0005\x00TEXI0001\x00'
    out += struct.pack('<7i', 0, 2, w, h, w, h, 0)  # fmt rgba8888=0, flags=2 = ClampUVs (bit 4 = IsGif -> parser demands TEXS; halo_3 uses 2)
    out += b'TEXB0003\x00'
    out += struct.pack('<i', 1)      # image count
    out += struct.pack('<i', -1)     # freeimage format: raw
    out += struct.pack('<i', 1)      # mip count
    out += struct.pack('<ii', w, h)
    out += struct.pack('<ii', 0, len(px))  # not compressed, uncompressed size
    out += struct.pack('<i', len(px)) + bytes(px)
    return out


def main(outdir: str) -> None:
    root = pathlib.Path(outdir)
    (root / 'models/dial').mkdir(parents=True, exist_ok=True)
    (root / 'materials/models/dial').mkdir(parents=True, exist_ok=True)
    (root / 'materials').mkdir(parents=True, exist_ok=True)

    (root / 'models/dial/dial.mdl').write_bytes(build_mdl('materials/models/dial/DialMaterial.json'))
    (root / 'materials/dialpalette.tex').write_bytes(build_tex())
    (root / 'materials/dialpalette.tex-json').write_text(
        json.dumps({'format': 'rgba8888', 'clampuvs': True, 'nomip': True}))
    (root / 'materials/models/dial/DialMaterial.json').write_text(json.dumps({
        'passes': [{
            'blending': 'normal', 'cullmode': 'nocull',
            'depthtest': 'disabled', 'depthwrite': 'disabled',
            'combos': {}, 'shader': 'generic4',
            'constantshadervalues': {'color': '1 1 1', 'roughness': 1, 'metallic': 0},
            'textures': ['dialpalette'],
        }]}, indent=1))
    (root / 'project.json').write_text(json.dumps(
        {'title': 'facet-dial', 'type': 'scene', 'file': 'scene.json'}))
    (root / 'scene.json').write_text(json.dumps({
        'version': 5,
        'camera': {'center': '0.0 0.0 -1.0', 'eye': '0.0 0.0 0.0', 'up': '0.0 1.0 0.0'},
        'general': {
            'ambientcolor': '0.00000 0.00000 0.00000',
            'skylightcolor': '0.00000 0.00000 0.00000',
            'bloom': False, 'camerafade': False, 'cameraparallax': False,
            'camerapreview': False, 'camerashake': False,
            'clearcolor': '0.00000 0.00000 0.00000', 'clearenabled': True,
            'farz': 10000.0, 'fov': 50.0, 'hdr': True, 'nearz': 0.01,
            'orthogonalprojection': {'height': 4320, 'width': 7680}, 'zoom': 1.0,
            'lightconfig': {'directional': 1},
        },
        'objects': [
            {'id': 1, 'name': 'Dial', 'model': 'models/dial/dial.mdl',
             'origin': '3840.0 2160.0 -3000.0', 'angles': '0.0 0.0 0.0',
             'scale': '1.0 1.0 1.0', 'solid': True, 'perspective': True,
             'castshadow': False, 'disablepropagation': False},
            {'id': 2, 'name': 'CalLight', 'light': 'ldirectional',
             'origin': '3840.0 2160.0 0.0', 'angles': '0.0 0.0 0.0',
             'color': '1.0 1.0 1.0', 'intensity': 2.0, 'radius': 500.0,
             'exponent': 2.0, 'castshadow': False, 'disablepropagation': False},
        ]}, indent=1))
    # plate manifest: name, normal, palette color, grid slot - the deterministic key
    (root / 'DIAL-MANIFEST.json').write_text(json.dumps({
        'plates': [{'name': nm, 'normal': n, 'color': COLORS[i], 'col': i % COLS, 'row': i // COLS,
                    'albedo': (LADDER.get(nm, 255)) / 255.0}
                   for i, (nm, n) in enumerate(PLATES)],
        'grid': {'cols': COLS, 'rows': ROWS, 'plate': PLATE, 'gap': GAP},
    }, indent=1))
    print(f'dial written to {root}: {len(PLATES)} plates, mdl '
          f'{(root / "models/dial/dial.mdl").stat().st_size} bytes')


if __name__ == '__main__':
    main(sys.argv[1])
