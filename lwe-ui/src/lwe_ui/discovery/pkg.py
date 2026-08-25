"""Pure-stdlib reader for Wallpaper Engine `.pkg` archives (see docs/findings.md).

The engine's `-z/--dump-structure` path segfaults on GL teardown and emits no type token, so
object/property discovery never spawns the engine. Instead we slice the `.pkg` ourselves: it is
an uncompressed little-endian container.

Byte layout (verified against PKGV0002/0007/0018/0019):

    u32   hdrLen
    char  header[hdrLen]            # e.g. "PKGV0002" (version digits vary)
    u32   fileCount
    fileCount x {
        u32   nameLen
        char  name[nameLen]
        u32   offset               # relative to payloadBase
        u32   length
    }
    <payloadBase = stream position after the table>
    ...payload bytes...

A file's bytes are buf[payloadBase + offset : payloadBase + offset + length].
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

_U32 = struct.Struct("<I")


class PkgError(Exception):
    """Raised when a `.pkg` file is malformed or cannot be parsed."""


class PkgReader:
    """Parse a `.pkg` archive's file table; slice payloads lazily on demand.

    Attributes:
        path: the source archive path.
        header: the decoded header string (e.g. "PKGV0002").
        files: name -> (offset, length), offsets relative to the payload base.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._buf = self.path.read_bytes()
        self.header: str = ""
        self.files: dict[str, tuple[int, int]] = {}
        self._payload_base: int = 0
        self._parse()

    def _u32(self, pos: int) -> tuple[int, int]:
        if pos + 4 > len(self._buf):
            raise PkgError(f"{self.path}: truncated reading u32 at {pos}")
        return _U32.unpack_from(self._buf, pos)[0], pos + 4

    def _parse(self) -> None:
        buf = self._buf
        pos = 0
        hdr_len, pos = self._u32(pos)
        if pos + hdr_len > len(buf):
            raise PkgError(f"{self.path}: truncated header (len={hdr_len})")
        self.header = buf[pos:pos + hdr_len].decode("utf-8", "replace")
        pos += hdr_len
        count, pos = self._u32(pos)
        files: dict[str, tuple[int, int]] = {}
        for i in range(count):
            name_len, pos = self._u32(pos)
            if pos + name_len > len(buf):
                raise PkgError(f"{self.path}: truncated name #{i} (len={name_len})")
            name = buf[pos:pos + name_len].decode("utf-8", "replace")
            pos += name_len
            offset, pos = self._u32(pos)
            length, pos = self._u32(pos)
            files[name] = (offset, length)
        self._payload_base = pos
        self.files = files

    def names(self) -> list[str]:
        """File names in table order."""
        return list(self.files.keys())

    def read(self, name: str) -> bytes:
        """Return the raw bytes of one archived file.

        Raises KeyError if absent, PkgError if the slice exceeds the buffer.
        """
        offset, length = self.files[name]
        start = self._payload_base + offset
        end = start + length
        if end > len(self._buf):
            raise PkgError(f"{self.path}: payload slice for {name!r} exceeds buffer")
        return self._buf[start:end]

    def read_json(self, name: str) -> dict:
        """Decode an archived file as JSON (UTF-8)."""
        return json.loads(self.read(name).decode("utf-8"))


def read_scene_json(path: str | Path) -> dict:
    """Convenience: open a `.pkg`, locate `scene.json`, return the parsed dict.

    Raises KeyError if the archive has no `scene.json`.
    """
    reader = PkgReader(path)
    return reader.read_json("scene.json")
