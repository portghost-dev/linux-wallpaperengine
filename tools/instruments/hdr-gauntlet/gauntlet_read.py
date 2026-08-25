#!/usr/bin/env python3
"""Reads a gauntlet dump (PPM/PNG) and emits the fixture numbers:
impulse responses + radial profiles, phase centroids, edge midpoint,
red-plate hue, gray/ramp checks. Engine-agnostic: run on this engine's
LWE_FBDUMP output or on a Windows Wallpaper Engine capture of the same scene (any resolution;
coordinates scale from the 1920x1080 authoring space).

Usage: gauntlet_read.py IMAGE [--flipped] [--encode] [--json OUT]
"""
import json, sys, struct, zlib

W0, H0 = 1920.0, 1080.0


def load_image(path):
    """PPM (engine dump) or PNG (Windows capture). Returns (w, h, rgb-bytes).
    Windows captures are y-flipped vs design coords: screen y = (1080 -
    design_y) * scale (established by measured monotonicity, not assumption)."""
    if path.endswith('.png'):
        from PIL import Image
        im = Image.open(path).convert('RGB')
        buf = im.tobytes()
        if '--encode' in sys.argv:
            # Windows captures hold LINEAR floats (FP16 scRGB swapchain, clamped
            # to 8-bit); the engine FBO holds ENCODED values. sRGB-encode to compare.
            lut = bytes(
                min(255, round(255 * (12.92 * (v / 255) if v / 255 <= 0.0031308
                                      else 1.055 * (v / 255) ** (1 / 2.4) - 0.055)))
                for v in range(256))
            buf = buf.translate(lut)
        return im.width, im.height, buf
    return load_ppm(path)


def load_ppm(path):
    d = open(path, 'rb').read()
    if d[:2] == b'P6':
        parts = d.split(b'\n', 3)
        # tolerate comment lines
        toks, rest = [], d[2:]
        i, vals = 0, []
        body = None
        # simple tokenizer: read 3 ints after magic, skipping comments
        pos = 2
        while len(vals) < 3:
            while d[pos:pos + 1].isspace():
                pos += 1
            if d[pos:pos + 1] == b'#':
                while d[pos:pos + 1] not in (b'\n', b''):
                    pos += 1
                continue
            start = pos
            while not d[pos:pos + 1].isspace():
                pos += 1
            vals.append(int(d[start:pos]))
        pos += 1
        w, h, _ = vals
        return w, h, d[pos:pos + w * h * 3]
    raise SystemExit(f"unsupported format (P6 PPM expected): {path}")


def px(buf, w, x, y):
    o = (y * w + x) * 3
    return buf[o], buf[o + 1], buf[o + 2]


def region_mean(buf, w, h, cx, cy, rw, rh, sx, sy, flip=False):
    if flip:
        cy = H0 - cy
    x0, x1 = int((cx - rw / 2) * sx), int((cx + rw / 2) * sx)
    y0, y1 = int((cy - rh / 2) * sy), int((cy + rh / 2) * sy)
    n, s = 0, [0, 0, 0]
    for y in range(max(0, y0), min(h, y1)):
        for x in range(max(0, x0), min(w, x1)):
            p = px(buf, w, x, y)
            for c in range(3):
                s[c] += p[c]
            n += 1
    return [round(v / max(1, n), 3) for v in s]


def radial_profile(buf, w, h, cx, cy, sx, sy, rmax=120, flip=False):
    """luminance vs DESIGN-space radius (2 design-px bins) - binning in native
    px makes profiles taken at different resolutions incomparable"""
    if flip:
        cy = H0 - cy
    icx, icy = cx * sx, cy * sy
    bins = [0.0] * (rmax // 2)
    cnts = [0] * (rmax // 2)
    span = rmax * sx
    r0 = int(max(0, icx - span)); r1 = int(min(w, icx + span))
    c0 = int(max(0, icy - span)); c1 = int(min(h, icy + span))
    for y in range(c0, c1):
        for x in range(r0, r1):
            dx, dy = (x - icx) / sx, (y - icy) / sy
            r = (dx * dx + dy * dy) ** 0.5
            b = int(r // 2)
            if b < len(bins):
                p = px(buf, w, x, y)
                bins[b] += 0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]
                cnts[b] += 1
    return [round(bins[i] / max(1, cnts[i]), 2) for i in range(len(bins))]


def centroid(buf, w, h, cx, cy, sx, sy, box=100, thresh=4, flip=False):
    if flip:
        cy = H0 - cy
    icx, icy = cx * sx, cy * sy
    sw = sxsum = sysum = 0.0
    for y in range(int(icy - box), int(icy + box)):
        for x in range(int(icx - box), int(icx + box)):
            if 0 <= x < w and 0 <= y < h:
                p = px(buf, w, x, y)
                l = 0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]
                if l > thresh:
                    sw += l; sxsum += l * x; sysum += l * y
    if sw == 0:
        return None
    return [round(sxsum / sw - icx, 3), round(sysum / sw - icy, 3)]


def main():
    path = sys.argv[1]
    w, h, buf = load_image(path)
    flip = '--flipped' in sys.argv  # Windows captures are y-flipped vs design coords
    sx, sy = w / W0, h / H0
    out = {'image': path, 'dims': [w, h]}

    # 1. impulse strip: peak + halo energy + profile per emitter
    impulses = []
    for i in range(16):
        inten = round(0.4 + 0.1 * i, 1)
        cx, cy = 96 + 96 * i, 270
        prof = radial_profile(buf, w, h, cx, cy, sx, sy, rmax=80, flip=flip)
        peak = region_mean(buf, w, h, cx, cy, 6, 6, sx, sy, flip)
        halo = sum(prof[4:])  # energy beyond the emitter core (r>8px)
        impulses.append({'intensity': inten, 'peak': peak,
                         'halo_sum': round(halo, 2), 'profile': prof[:20]})
    out['impulses'] = impulses

    # 2. phase pair centroid drift
    out['phase_aligned'] = centroid(buf, w, h, 512, 135, sx, sy, flip=flip)
    out['phase_offset'] = centroid(buf, w, h, 1024.5, 135.5, sx, sy, flip=flip)

    # 3. hard edge: sample midpoint texel column at the edge (x=960)
    edge = []
    yy = int(((H0 - 607.5) if flip else 607.5) * sy)
    for dx in range(-6, 7):
        x = int(960 * sx) + dx
        edge.append(px(buf, w, x, yy)[0])
    out['edge_scan_r'] = edge  # blur midpoint ~128 = encoded-space, ~188 = linear

    # 4. red plate mean + immediate surround (hue shift check)
    out['red_plate'] = region_mean(buf, w, h, 1440, 607.5, 180, 115, sx, sy, flip)
    out['red_halo'] = region_mean(buf, w, h, 1440, 480, 180, 40, sx, sy, flip)

    # 5. gray flat + ramp (zero-leakage: ramp halo must be 0)
    out['gray_flat'] = region_mean(buf, w, h, 240, 878, 180, 115, sx, sy, flip)
    ramp = []
    for i, v in enumerate((0.05, 0.10, 0.15, 0.20, 0.30)):
        m = region_mean(buf, w, h, 720 + 180 * i, 1012, 100, 40, sx, sy, flip)
        above = region_mean(buf, w, h, 720 + 180 * i, 950, 100, 20, sx, sy, flip)
        ramp.append({'value': v, 'mean': m, 'leak_above': above})
    out['ramp'] = ramp

    txt = json.dumps(out, indent=1)
    if '--json' in sys.argv:
        open(sys.argv[sys.argv.index('--json') + 1], 'w').write(txt)
    # terse console summary
    print(f"dims {w}x{h}")
    print("impulse peaks (R):", [i['peak'][0] for i in impulses])
    print("impulse halos   :", [i['halo_sum'] for i in impulses])
    print("phase drift aligned/offset:", out['phase_aligned'], out['phase_offset'])
    print("edge scan:", out['edge_scan_r'])
    print("red plate/halo:", out['red_plate'], out['red_halo'])
    print("gray flat:", out['gray_flat'])
    print("ramp leaks:", [r['leak_above'] for r in ramp])


if __name__ == '__main__':
    main()
