#!/usr/bin/env python3
"""Facet-dial reader: capture(PPM) + manifest + present-mapping -> plate RGB table + direction solve.

Usage: dial_read.py <cap.ppm> <DIAL-MANIFEST.json> <engine.log> [--solve]
The engine log's final 'LWE-PRESENT viewport=WxH wpRes=WxH mode=0 uv=[u0,u1]x[v0,v1]'
line gives the deterministic scene->pixel mapping (no fitting, no search).
Plate identity is verified by color-key: a plate's rect must be hue-dominated
by its manifest color, else the row is flagged UNIDENTIFIED (never guessed).
"""
import json
import pathlib
import re
import sys


def read_ppm(path):
    data = pathlib.Path(path).read_bytes()
    fields, i = [], 0
    while len(fields) < 4:
        while data[i] in b' \t\r\n':
            i += 1
        if data[i:i+1] == b'#':
            while data[i] not in b'\r\n':
                i += 1
            continue
        s = i
        while data[i] not in b' \t\r\n':
            i += 1
        fields.append(data[s:i])
    i += 1
    assert fields[0] == b'P6'
    w, h = int(fields[1]), int(fields[2])
    return w, h, data[i:i + w*h*3]


def main():
    cap, manifest_path, log_path = sys.argv[1], sys.argv[2], sys.argv[3]
    man = json.loads(pathlib.Path(manifest_path).read_text())
    # last present line = the live mapping
    pres = None
    for line in pathlib.Path(log_path).read_text(errors='replace').splitlines():
        m = re.search(r'LWE-PRESENT viewport=(\d+)x(\d+) wpRes=(\d+)x(\d+) mode=\d+ '
                      r'uv=\[([\d.eE+-]+),([\d.eE+-]+)\]x\[([\d.eE+-]+),([\d.eE+-]+)\]', line)
        if m:
            pres = [float(v) for v in m.groups()]
    assert pres, 'no LWE-PRESENT line in log'
    vw, vh, sw, sh, u0, u1, v0, v1 = pres

    w, h, px = read_ppm(cap)
    # viewport pixels may differ from capture (window decorations): assume the
    # viewport fills the capture bottom-aligned; require sizes match closely.
    assert abs(w - vw) <= 4 and abs(h - vh) <= 90, f'capture {w}x{h} vs viewport {vw}x{vh}'
    yoff = h - int(vh)  # decoration/title band at top

    def scene_to_px(sx, sy):
        # scene coords: origin top-left (y down), sw x sh; dial authored around center
        u = sx / sw
        v = sy / sh
        cx = (u - u0) / (u1 - u0) * vw
        cy = (v - v0) / (v1 - v0) * vh + yoff
        return cx, cy

    g = man['grid']
    total_w = g['cols']*g['plate'] + (g['cols']-1)*g['gap']
    total_h = g['rows']*g['plate'] + (g['rows']-1)*g['gap']
    print(f"{'plate':6s} {'R':>6s} {'G':>6s} {'B':>6s} {'keyed':>6s}")
    rows = []
    for p in man['plates']:
        # plate center in scene coords. The MODEL world is y-down-on-screen
        # (measured: generator row 0, the highest model +y, renders at screen
        # BOTTOM) -> flip rows here.
        row_eff = g['rows'] - 1 - p['row']
        cx_s = sw/2 - total_w/2 + p['col']*(g['plate']+g['gap']) + g['plate']/2
        cy_s = sh/2 - total_h/2 + row_eff*(g['plate']+g['gap']) + g['plate']/2
        pcx, pcy = scene_to_px(cx_s, cy_s)
        # inner half-plate rect
        hw = g['plate'] * 0.25 / sw * (u1-u0)**-1 * vw / 1  # scene->px scale x
        hh = g['plate'] * 0.25 / sh * (v1-v0)**-1 * vh / 1
        x0, x1 = int(pcx-hw), int(pcx+hw)
        y0, y1 = int(pcy-hh), int(pcy+hh)
        n = 0
        acc = [0, 0, 0]
        for y in range(max(0, y0), min(h, y1), 2):
            for x in range(max(0, x0), min(w, x1), 2):
                j = 3*(y*w+x)
                acc[0] += px[j]; acc[1] += px[j+1]; acc[2] += px[j+2]
                n += 1
        rgb = [a/max(1, n) for a in acc]
        # color-key check: chromaticity (rgb ratios survive uniform lighting scale)
        key = p['color']
        def chroma(t):
            s = sum(t)
            return [c/s for c in t] if s > 0 else [1/3.0]*3
        if max(rgb) <= 8:
            keyed = 'DARK'
        elif max(key) - min(key) < 24:
            keyed = 'GRAY'  # ladder/white plates: chromaticity-degenerate, position-trusted
        else:
            d = sum(abs(a-b) for a, b in zip(chroma(rgb), chroma(key)))
            keyed = 'OK' if d < 0.22 else 'MISMATCH'
        print(f"{p['name']:6s} {rgb[0]:6.1f} {rgb[1]:6.1f} {rgb[2]:6.1f} {keyed:>6s}")
        rows.append((p['name'], p['normal'], rgb, keyed))

    if '--solve' in sys.argv:
        # normalized luma per plate (divide by key channel max to undo albedo), lambert solve
        import itertools
        samples = []
        for name, nrm, rgb, keyed in rows:
            if keyed == 'MISMATCH':
                continue
            key = next(p['color'] for p in man['plates'] if p['name'] == name)
            # use the plate's dominant key channel as its photometer
            ch = max(range(3), key=lambda i: key[i])
            lum = rgb[ch] / (key[ch]/255.0)
            samples.append((nrm, lum))
        # least squares: lum_i ~ a*(n_i . d) + floor  over lit plates; try all sign patterns via iteration
        best = None
        import math
        # coarse direction grid then refine
        for it in range(2):
            step = 0.2 if it == 0 else 0.05
            cands = []
            r = [x/100 for x in range(-100, 101, int(step*100))]
            base = best[0] if best else (0, 0, 0)
            for dx in r:
                for dy in r:
                    for dz in r:
                        v = (base[0]+dx*step if it else dx, base[1]+dy*step if it else dy, base[2]+dz*step if it else dz)
                        l = math.sqrt(sum(c*c for c in v))
                        if l < 1e-6:
                            continue
                        d = tuple(c/l for c in v)
                        # fit a, floor by least squares given d
                        xs = [max(0.0, sum(n[i]*d[i] for i in range(3))) for n, _ in samples]
                        ys = [lum for _, lum in samples]
                        m = len(xs)
                        sx, sy_ = sum(xs), sum(ys)
                        sxx, sxy = sum(x*x for x in xs), sum(x*y for x, y in zip(xs, ys))
                        den = m*sxx - sx*sx
                        if abs(den) < 1e-9:
                            continue
                        a = (m*sxy - sx*sy_) / den
                        fl = (sy_ - a*sx) / m
                        err = sum((a*x+fl-y)**2 for x, y in zip(xs, ys))
                        cands.append((err, d, a, fl))
            cands.sort()
            best = (cands[0][1], cands[0][2], cands[0][3], cands[0][0])
        d, a, fl, err = best
        print(f"\nSOLVE: toLight dir = ({d[0]:+.3f}, {d[1]:+.3f}, {d[2]:+.3f})  gain={a:.1f} floor={fl:.1f} rmse={math.sqrt(err/len(samples)):.2f}")


if __name__ == '__main__':
    main()
