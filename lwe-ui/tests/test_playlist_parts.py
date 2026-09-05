"""split_playlist_parts cuts a resolved set into playlist-set transfers under the engine caps.

Run: PYTHONPATH=src python3 tests/test_playlist_parts.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lwe_ui import constants as C  # noqa: E402
from lwe_ui.models import split_playlist_parts  # noqa: E402


def _entry(i: int, pad: int = 0) -> dict:
    return {"id": str(i), "ui_id": str(i), "pad": "x" * pad}


class SplitPlaylistParts(unittest.TestCase):
    def test_small_set_is_one_part(self) -> None:
        entries = [_entry(i) for i in range(10)]
        self.assertEqual(split_playlist_parts(entries), [entries])
        self.assertEqual(split_playlist_parts([]), [[]])

    def test_entry_cap_starts_a_new_part(self) -> None:
        entries = [_entry(i) for i in range(C.ENGINE_ROTATE_MAX_ENTRIES + 1)]
        parts = split_playlist_parts(entries)
        self.assertEqual([len(p) for p in parts], [C.ENGINE_ROTATE_MAX_ENTRIES, 1])
        self.assertEqual([e for p in parts for e in p], entries)

    def test_byte_cap_starts_a_new_part(self) -> None:
        entries = [_entry(i, pad=8000) for i in range(20)]
        parts = split_playlist_parts(entries)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(json.dumps(part)), C.ENGINE_ROTATE_MAX_BYTES)
        self.assertEqual([e for p in parts for e in p], entries)

    def test_oversized_entry_is_dropped_not_sent(self) -> None:
        entries = [_entry(1), _entry(2, pad=C.ENGINE_ROTATE_MAX_BYTES), _entry(3)]
        self.assertEqual(split_playlist_parts(entries), [[entries[0], entries[2]]])

    def test_part_count_is_capped(self) -> None:
        entries = [_entry(i, pad=8000) for i in range(64 * 8)]
        parts = split_playlist_parts(entries)
        self.assertEqual(len(parts), 64)
        kept = [e for p in parts for e in p]
        self.assertEqual(kept, entries[:len(kept)], "the retained prefix stays intact and in order")
        self.assertLess(len(kept), len(entries))


if __name__ == "__main__":
    unittest.main(verbosity=1)
