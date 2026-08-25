"""texcomp - the wizard's compression engine (workshop-bench R1).

Hermetic: a SYNTHETIC scene.pkg (built to the same TEXV0005/TEXB0003 layout the parser
reads), a STUB encoder shim (deterministic block-sized output, no ISPC), and a temp
cache dir. Nothing touches the real texcache, library, or shim.

What is proven:
  * scan() applies the tool's eligibility verbatim (format / size / name-exempt) and
    reports honest totals + todo;
  * encode_scene() writes the engine's cache contract - key = sha256 of the stored
    mip0 bytes, .bc = concatenated per-mip blocks, .meta field set - atomically;
  * a level-0-only color texture grows the sRGB mip chain (8 levels for 128px);
  * re-scan sees the cache (todo drops to 0) and a second encode is a no-op;
  * cancellation between textures stops the run.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_TMP = tempfile.mkdtemp(prefix="lwe-texcomp-test-")


def _tex(fmt: int, tw: int, th: int, raw: bytes) -> bytes:
    """One TEXV0005/TEXB0003 texture: single image, single stored mip, uncompressed."""
    out = b"TEXV0005" + b"\x00" * 10                    # parser starts reading at 18
    out += struct.pack("<7I", fmt, 0, tw, th, 0, 0, 0)  # fmt flags tw th +3 skipped
    out += b"TEXB0003" + b"\x00"                        # container magic + 1 pad
    out += struct.pack("<I", 1)                         # image count
    out += struct.pack("<I", 0xFFFFFFFF)                # fif = UNKNOWN (raw pixels)
    out += struct.pack("<I", 1)                         # mip count
    out += struct.pack("<II", tw, th)                   # mip dims
    out += struct.pack("<Ii", 0, 0)                     # comp=0, unc (unused)
    out += struct.pack("<i", len(raw)) + raw
    return out


def _pkg(files: dict[str, bytes]) -> bytes:
    def s(b: bytes) -> bytes:
        return struct.pack("<I", len(b)) + b

    out = s(b"PKGV0001") + struct.pack("<I", len(files))
    blobs = b""
    for name, data in files.items():
        out += s(name.encode()) + struct.pack("<II", len(blobs), len(data))
        blobs += data
    return out + blobs


def main() -> None:
    from lwe_ui import texcomp

    # stub shim: 16 deterministic bytes per 4x4 block, so sizes and concat order are
    # checkable without the real encoder
    shim = Path(_TMP) / "stub_bc7enc"
    shim.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, struct\n"
        "d = sys.stdin.buffer.read()\n"
        "w, h, fmt = struct.unpack('<III', d[:12])\n"
        "blocks = ((w + 3) // 4) * ((h + 3) // 4)\n"
        "if len(d) - 12 < w * h * 4:\n"
        "    sys.stderr.write('stub: short pixel read'); sys.exit(2)\n"
        "sys.stdout.buffer.write(bytes([fmt]) * (blocks * 16))\n")
    shim.chmod(0o755)
    os.environ["LWE_BC7ENC"] = str(shim)
    texcomp.CACHE = str(Path(_TMP) / "cache")

    scene = Path(_TMP) / "wp" / "100"
    scene.mkdir(parents=True)
    big = bytes(range(256)) * (128 * 128 * 4 // 256)          # 128x128 RGBA8, eligible
    files = {
        "materials/big.tex": _tex(0, 128, 128, big),
        "materials/small.tex": _tex(0, 64, 64, b"\x00" * (64 * 64 * 4)),   # too small
        "materials/thing_noise.tex": _tex(0, 128, 128, big),               # name-exempt
        "materials/skip.json": b"{}",
    }
    (scene / "scene.pkg").write_bytes(_pkg(files))

    s = texcomp.scan(str(scene))
    assert s["total"] == 3 and s["eligible"] == 1 and s["cached"] == 0, s
    assert s["todo"] == 1 and s["shim"] is True, s
    assert s["raw_mb"] == 0, "128px chain rounds to 0 MB - honest, not inflated"

    prog = []
    r = texcomp.encode_scene(str(scene), "100",
                             progress=lambda d, t: prog.append((d, t)))
    assert r == {"encoded": 1, "failed": 0, "total": 1}, r
    assert prog[0] == (0, 1) and prog[-1] == (1, 1), prog

    key = hashlib.sha256(big).hexdigest()
    bcp = Path(texcomp.CACHE) / (key + ".bc")
    mp = Path(texcomp.CACHE) / (key + ".meta")
    assert bcp.exists() and mp.exists(), "blob + meta pair written"
    meta = json.loads(mp.read_text())
    assert meta["wallpaper"] == "100" and meta["gl"] == "BC7"
    assert meta["fmt_src"] == "ARGB8888" and meta["tw"] == 128 and meta["th"] == 128
    # level-0-only color grows the sRGB chain: 128 -> 1 is 8 levels
    assert len(meta["mips"]) == 8, meta["mips"]
    expect = sum(((w + 3) // 4) * ((h + 3) // 4) * 16 for w, h, _n in meta["mips"])
    assert bcp.stat().st_size == expect, "blob is the concatenated per-mip blocks"
    assert not list(Path(texcomp.CACHE).glob("*.tmp")), "atomic writes leave no tmp"

    s2 = texcomp.scan(str(scene))
    assert s2["cached"] == 1 and s2["todo"] == 0, s2
    r2 = texcomp.encode_scene(str(scene), "100")
    assert r2["total"] == 0, "already-cached texture is never re-encoded"

    scene2 = Path(_TMP) / "wp" / "200"
    scene2.mkdir(parents=True)
    other = bytes(reversed(range(256))) * (128 * 128 * 4 // 256)
    (scene2 / "scene.pkg").write_bytes(_pkg({
        "a.tex": _tex(0, 128, 128, other),
        "b.tex": _tex(0, 128, 128, other[::-1]),
    }))
    r3 = texcomp.encode_scene(str(scene2), "200", cancelled=lambda: True)
    assert r3["encoded"] == 0, "a pre-tripped cancel encodes nothing"

    print("OK test_texcomp - scan eligibility + totals, cache contract (key/blob/meta/"
          "atomic), mip-chain growth, cached no-op, cancel")
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
