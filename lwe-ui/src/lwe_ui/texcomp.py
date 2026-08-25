"""Texture compression for the Workshop wizard.

This is the ingest pipeline from the parity campaign's `lwe-texcache` tool, ported into
the panel so the wizard's Compression phase can run it natively - no external tool, no
engine involvement. The CACHE CONTRACT IS THE ENGINE'S and must not drift:

  * key   = sha256 hex of the STORED (post-LZ4, pre-image-decode) mip-0 bytes - the
    exact bytes `CTexture.uploadFromTexcache` hashes on the engine side;
  * blob  = `<key>.bc` - the per-mip encoded blocks, concatenated in mip order;
  * meta  = `<key>.meta` - json {src, wallpaper, fif, gl, fmt_src, tw, th, mips} with
    mips = [[w, h, blockBytes], ...], same field set the tool wrote;
  * writes are atomic (tmp + rename) and append-only - an existing pair is never
    rewritten, which is also what makes re-benching a compressed scene instant.

ELIGIBILITY is the tool's, verbatim: ARGB8888/R8/RG88 only (DXT ships compressed
already; BC7 cannot beat it), >=128px, data-texture names exempt (lut/ramp/noise/mask/
displace/distort/flow/gradientmap - shaders read those as math and block quantization
corrupts them), plus the wallpaper's own exempt.txt.

ALL-CORE comes from band splitting: BC7/BC4/BC5 encode independent 4x4 blocks in
row-major order, so a level cut into 4-row-aligned bands and concatenated re-forms the
byte-identical whole-surface encoding. The shim (`lwe_bc7enc`, ISPC alpha_slow - the
A24-gated profile) stays the single encoder; parallelism is N shim processes.

FIF textures (embedded PNG/JPEG) are real - one shipped scene carries two 8K backgrounds - and are
decoded with PIL exactly as the tool did; the parity guard (decoded dims must equal the
header's) skips a lying mip rather than caching it.
"""
from __future__ import annotations

import ctypes
import glob
import hashlib
import json
import os
import re
import struct
import subprocess
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import Any, Callable

CACHE = os.path.expanduser("~/.local/state/lwe/texcache")
SHIM = os.path.expanduser("~/.local/bin/lwe_bc7enc")

#: source format -> (shim fmt code, source channels, GL name)
FMTMAP = {0: (7, 4, "BC7"), 9: (4, 1, "BC4"), 8: (5, 2, "BC5")}
FMTNAME = {0: "ARGB8888", 9: "R8", 8: "RG88", 1: "RGB888", 2: "RGB565",
           4: "DXT5", 6: "DXT3", 7: "DXT1", 12: "BC7"}
#: compressed bytes/texel by shim fmt: BC7 1.0, BC4 0.5, BC5 1.0
_BC_BPT = {7: 1.0, 4: 0.5, 5: 1.0}
EXEMPT_RE = re.compile(r"lut|ramp|gradientmap|noise|displace|distort|flow|mask", re.I)
UNKNOWN = 0xFFFFFFFF

_lz4 = None


def _lz4_dec(src: bytes, dsz: int) -> bytes:
    global _lz4
    if _lz4 is None:
        lib = ctypes.CDLL("liblz4.so.1")
        lib.LZ4_decompress_safe.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                            ctypes.c_int, ctypes.c_int]
        lib.LZ4_decompress_safe.restype = ctypes.c_int
        _lz4 = lib
    dst = ctypes.create_string_buffer(dsz)
    n = _lz4.LZ4_decompress_safe(src, dst, len(src), dsz)
    if n < 0:
        raise ValueError("lz4 decompress failed")
    return dst.raw[:n]


def shim_path() -> str:
    return str(os.environ.get("LWE_BC7ENC") or SHIM)


def shim_available() -> bool:
    p = shim_path()
    return os.path.isfile(p) and os.access(p, os.X_OK)


def read_pkg(path: str) -> dict[str, bytes]:
    b = open(path, "rb").read()
    o = [0]

    def u32() -> int:
        v = struct.unpack_from("<I", b, o[0])[0]
        o[0] += 4
        return v

    def s() -> bytes:
        n = u32()
        v = b[o[0]:o[0] + n]
        o[0] += n
        return v

    s()
    count = u32()
    entries = {}
    for _ in range(count):
        name = s().decode("utf8", "replace")
        off = u32()
        ln = u32()
        entries[name] = (off, ln)
    base = o[0]
    return {name: b[base + off:base + off + ln] for name, (off, ln) in entries.items()}


def parse_tex(data: bytes):
    """-> (fmt, fif, tw, th, images) with images = [[(mw, mh, storedBytes), ...], ...].

    storedBytes are post-LZ4 - for raw formats the pixel payload, for FIF the embedded
    PNG/JPEG file bytes. The engine's cache key hashes exactly these.
    """
    o = [0]

    def u32() -> int:
        v = struct.unpack_from("<I", data, o[0])[0]
        o[0] += 4
        return v

    def i32() -> int:
        v = struct.unpack_from("<i", data, o[0])[0]
        o[0] += 4
        return v

    def nstr() -> bytes:
        st = o[0]
        while data[o[0]] != 0:
            o[0] += 1
        o[0] += 1
        return data[st:o[0] - 1]

    if data[:8] != b"TEXV0005":
        raise ValueError("bad TEXV")
    o[0] = 18
    fmt = u32()
    u32()
    tw = u32()
    th = u32()
    u32(); u32(); u32()
    cm = data[o[0]:o[0] + 8]
    o[0] += 9
    ic = u32()
    cv = int(cm[4:8])
    fif = UNKNOWN
    if cm.startswith(b"TEXB0004"):
        fif = u32()
        isvid = u32()
        # engine downgrade rule (TextureParser): only a genuine mp4 keeps the 0004
        # per-mip extra header; anything else parses with the 0003 layout
        if not (fif == UNKNOWN and isvid == 1):
            cv = 3
    elif cm.startswith(b"TEXB0003"):
        fif = u32()
    images = []
    for _ in range(ic):
        mc = u32()
        mips = []
        for _ in range(mc):
            if cv == 4:
                u32(); u32(); nstr(); u32()
            mw = u32()
            mh = u32()
            comp = 0
            unc = 0
            if cv in (2, 3, 4):
                comp = u32()
                unc = i32()
            csz = i32()
            if comp == 0:
                unc = csz
            blob = data[o[0]:o[0] + csz]
            o[0] += csz
            raw = _lz4_dec(blob, unc) if comp == 1 else blob
            mips.append((mw, mh, raw))
        images.append(mips)
    return fmt, fif, tw, th, images


def _to_rgba(raw: bytes, w: int, h: int, ch: int) -> bytes:
    n = w * h
    if ch == 4:
        return raw[:n * 4]
    import numpy as np
    a = np.frombuffer(raw[:n * ch], dtype=np.uint8).reshape(h, w, ch)
    out = np.zeros((h, w, 4), np.uint8)
    out[..., 3] = 255
    out[..., :ch] = a
    return out.tobytes()


def _gen_mips(rgba: bytes, w: int, h: int):
    """sRGB-correct box chain for level-0-only color textures (tool-verbatim math)."""
    import numpy as np

    def s2l(c):
        return np.where(c <= 10.31475, (c / 255.0) / 12.92,
                        (((c / 255.0) + 0.055) / 1.055) ** 2.4)

    def l2s(l):
        return np.where(l <= 0.0031308, l * 12.92 * 255.0,
                        (1.055 * (l ** (1 / 2.4)) - 0.055) * 255.0)

    cur = np.frombuffer(rgba, dtype=np.uint8).reshape(h, w, 4).astype(np.float64)
    cur = np.concatenate([s2l(cur[..., :3]), cur[..., 3:4] / 255.0], axis=2)
    mw, mh = w, h
    yield (mw, mh, rgba)
    while mw > 1 or mh > 1:
        nw, nh = max(1, mw // 2), max(1, mh // 2)
        c = cur
        pw, ph = nw * 2, nh * 2
        if c.shape[1] < pw or c.shape[0] < ph:
            c = np.pad(c, ((0, ph - c.shape[0]), (0, pw - c.shape[1]), (0, 0)),
                       mode="edge")
        cur = c.reshape(nh, 2, nw, 2, 4).mean(axis=(1, 3))
        mw, mh = nw, nh
        rgb = np.clip(l2s(cur[..., :3]), 0, 255)
        a = np.clip(cur[..., 3:4] * 255.0, 0, 255)
        yield (mw, mh, np.concatenate([rgb, a], axis=2).astype(np.uint8).tobytes())


def _decode_levels(fmt: int, fif: int, images) -> list[tuple[int, int, bytes]] | None:
    """The tool's per-texture pipeline up to encode input; None = parity/size skip."""
    from PIL import Image
    shimfmt, ch, _gl = FMTMAP[fmt]
    levels: list[tuple[int, int, bytes]] = []
    if fif not in (None, UNKNOWN):
        for (mw, mh, blob) in images[0]:
            im = Image.open(BytesIO(blob)).convert("RGBA")
            if im.width != mw or im.height != mh:
                return None            # parity guard: never cache a lying mip
            levels.append((mw, mh, im.tobytes()))
    else:
        for (mw, mh, raw) in images[0]:
            if len(raw) < mw * mh * ch:
                return None
            levels.append((mw, mh, _to_rgba(raw, mw, mh, ch)))
    if len(levels) == 1 and shimfmt == 7:
        levels = list(_gen_mips(levels[0][2], levels[0][0], levels[0][1]))
    return levels


def _iter_eligible(d: str):
    """Yield (pkgKey, baseName, fmt, fif, tw, th, images) for each eligible texture."""
    for k, base, fmt, fif, tw, th, images in _iter_scene_all(d):
        if fmt is not None:
            yield k, base, fmt, fif, tw, th, images


def _key_of(images) -> str:
    return hashlib.sha256(images[0][0][2]).hexdigest()


def _cached(key: str) -> bool:
    return (os.path.exists(os.path.join(CACHE, key + ".bc"))
            and os.path.exists(os.path.join(CACHE, key + ".meta")))


def purge_wallpaper(wid: str) -> int:
    """Delete every cache row whose meta belongs to this wallpaper (trash cleanup).

    Ownership comes from the meta's `wallpaper` field, so shared keys are never an
    issue: a key is written by exactly one wallpaper's encode pass. Unreadable meta
    rows are skipped, not guessed at. Returns the number of rows removed; POSIX
    unlink is safe even if the engine still maps a .bc."""
    wid = str(wid)
    removed = 0
    try:
        names = os.listdir(CACHE)
    except OSError:
        return 0
    for name in names:
        if not name.endswith(".meta"):
            continue
        try:
            with open(os.path.join(CACHE, name)) as f:
                owner = json.load(f).get("wallpaper")
        except Exception:
            continue
        if str(owner) != wid:
            continue
        base = name[: -len(".meta")]
        for suffix in (".bc", ".meta"):
            try:
                os.remove(os.path.join(CACHE, base + suffix))
            except OSError:
                pass
        removed += 1
    return removed


def scan(d: str) -> dict[str, Any]:
    """Read-only eligibility inspection of one wallpaper directory.

    -> {total, eligible, cached, todo, raw_mb, bc_mb, shim} where raw/bc cover only
    the TODO set (what Start Compression would actually process), full mip chains
    included, bc estimated at the target format's bytes/texel.
    """
    total = eligible = cached = 0
    raw = bc = 0.0
    for _k, _base, fmt, _fif, _tw, _th, images in _iter_scene_all(d):
        total += 1
        if fmt is None:
            continue
        eligible += 1
        if _cached(_key_of(images)):
            cached += 1
            continue
        shimfmt, _ch, _gl = FMTMAP[fmt]
        for (mw, mh, _raw) in images[0]:
            raw += mw * mh * 4
            bc += mw * mh * _BC_BPT[shimfmt]
    return {"total": total, "eligible": eligible, "cached": cached,
            "todo": eligible - cached,
            "raw_mb": int(raw / 1048576), "bc_mb": int(bc / 1048576),
            "shim": shim_available()}


def _iter_scene_all(d: str):
    """Like _iter_eligible but ALSO yields ineligible textures (fmt=None) so the scan
    can report an honest total for the card."""
    pk = glob.glob(os.path.join(d, "*.pkg"))
    if not pk:
        return
    files = read_pkg(pk[0])
    exempt_extra: set[str] = set()
    ex_path = os.path.join(d, "exempt.txt")
    if os.path.exists(ex_path):
        exempt_extra = {ln.strip() for ln in open(ex_path) if ln.strip()}
    for k in sorted(files):
        if not k.endswith(".tex"):
            continue
        base = os.path.basename(k)
        try:
            fmt, fif, tw, th, images = parse_tex(files[k])
        except Exception:
            continue
        if not images or not images[0]:
            continue
        ok = (fmt in FMTMAP and tw >= 128 and th >= 128
              and not EXEMPT_RE.search(base) and base not in exempt_extra)
        yield k, base, (fmt if ok else None), fif, tw, th, images


def _encode_surface(w: int, h: int, rgba: bytes, shimfmt: int,
                    pool: ThreadPoolExecutor) -> bytes:
    """One level through the shim, band-split across the pool for big surfaces.

    Bands are 4-row aligned; blocks are independent and row-major, so the concat is
    byte-identical to a whole-surface encode.
    """
    def run(bw: int, bh: int, data: bytes) -> bytes:
        p = subprocess.run([shim_path()],
                           input=struct.pack("<III", bw, bh, shimfmt) + data,
                           capture_output=True)
        if p.returncode != 0:
            raise RuntimeError("encoder: " + p.stderr.decode("utf8", "replace"))
        return p.stdout

    if h < 128:
        return run(w, h, rgba)
    bh = max(4, ((h + 31) // 32 + 3) // 4 * 4)
    futures = []
    y = 0
    while y < h:
        bandh = min(bh, h - y)
        futures.append(pool.submit(run, w, bandh, rgba[y * w * 4:(y + bandh) * w * 4]))
        y += bandh
    return b"".join(f.result() for f in futures)


def encode_scene(d: str, wid: str,
                 progress: Callable[[int, int], None] | None = None,
                 cancelled: Callable[[], bool] | None = None,
                 workers: int | None = None) -> dict[str, int]:
    """Encode every eligible-and-uncached texture of one wallpaper into the cache.

    progress(done, total) fires per finished texture; cancelled() is polled between
    textures (a started texture always completes - a torn cache entry must never
    exist, and atomic rename guarantees it doesn't).
    """
    jobs = []
    for k, _base, fmt, fif, tw, th, images in _iter_eligible(d):
        if not _cached(_key_of(images)):
            jobs.append((k, fmt, fif, tw, th, images))
    total = len(jobs)
    done = encoded = failed = 0
    if progress:
        progress(0, total)
    os.makedirs(CACHE, exist_ok=True)
    nworkers = workers or os.cpu_count() or 8
    with ThreadPoolExecutor(max_workers=nworkers) as pool:
        for (k, fmt, fif, tw, th, images) in jobs:
            if cancelled and cancelled():
                break
            shimfmt, _ch, glname = FMTMAP[fmt]
            try:
                levels = _decode_levels(fmt, fif, images)
                if levels is None:
                    failed += 1
                    continue
                blocks = []
                meta_mips = []
                for (mw, mh, rgba) in levels:
                    blk = _encode_surface(mw, mh, rgba, shimfmt, pool)
                    blocks.append(blk)
                    meta_mips.append([mw, mh, len(blk)])
                key = _key_of(images)
                bcp = os.path.join(CACHE, key + ".bc")
                mp = os.path.join(CACHE, key + ".meta")
                tmp = bcp + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(b"".join(blocks))
                os.replace(tmp, bcp)
                # meta carries the HEADER dims, exactly as the tool wrote (mip0 can be
                # smaller than the padded header - e.g. an 8192x4096 header over an
                # 8192x3240 mip0)
                json.dump({"src": k, "wallpaper": wid, "fif": int(fif),
                           "gl": glname, "fmt_src": FMTNAME.get(fmt, fmt),
                           "tw": tw, "th": th, "mips": meta_mips}, open(mp, "w"))
                encoded += 1
            except Exception:
                failed += 1
            finally:
                done += 1
                if progress:
                    progress(done, total)
    return {"encoded": encoded, "failed": failed, "total": total}
