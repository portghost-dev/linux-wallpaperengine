"""Builders for synthetic wallpaper packages, copied from test_texcomp.py: one TEXV0005/TEXB0003
texture, a scene.pkg holding textures, and the source of a stub texture encoder that writes 16
deterministic bytes per 4x4 block, so sizes and order are checkable without the real encoder."""
from __future__ import annotations

import struct


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


STUB_ENCODER = (
    "#!/usr/bin/env python3\n"
    "import sys, struct\n"
    "d = sys.stdin.buffer.read()\n"
    "w, h, fmt = struct.unpack('<III', d[:12])\n"
    "blocks = ((w + 3) // 4) * ((h + 3) // 4)\n"
    "if len(d) - 12 < w * h * 4:\n"
    "    sys.stderr.write('stub: short pixel read'); sys.exit(2)\n"
    "sys.stdout.buffer.write(bytes([fmt]) * (blocks * 16))\n")
