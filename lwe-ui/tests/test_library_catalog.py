"""The library catalog: the pool, the Workshop rows and the trash as commands list them, without Qt.

One scratch store holds a good item, a good item without files, a review item, a bad item whose
library folder is still on disk, a legacy tags state, an unknown library folder, an unsafe folder
name, a complete download, a half download, a manual-root item, a preset and a reference import.
The catalog agrees with the grid (library_ids and the All scope), the Workshop tiles and the
importer's scan; the pool is numbered by title then id from 1 and the Workshop rows continue it;
the unsafe name is counted and left out; trash rows, render folders and the alias index read as
the panel reads them; and the catalog calls write nothing.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 tests/test_library_catalog.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.TemporaryDirectory(prefix="lwe-catalog-")
_ROOT = Path(_TMP.name)
for _key, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"), ("XDG_DATA_HOME", "d")):
    os.environ[_key] = str(_ROOT / _sub) if _sub else str(_ROOT)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QCoreApplication  # noqa: E402

_APP = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])

from lwe_ui import models  # noqa: E402
from lwe_ui.library import catalog  # noqa: E402
from lwe_ui.library.catalog import Row  # noqa: E402
from lwe_ui.storage import importer, paths, records, settings, tags  # noqa: E402
from lwe_ui.workshop import WorkshopBridge  # noqa: E402

LIB = _ROOT / "lib"
WORKSHOP = _ROOT / "workshop"
REFERENCE = _ROOT / "refsrc" / "501"


def _item(folder: Path, title: str, kind: str = "scene", payload: bool = True) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    file = {"video": "video.mp4", "web": "index.html"}.get(kind, "scene.json")
    (folder / "project.json").write_text(json.dumps({"title": title, "type": kind, "file": file}),
                                         encoding="utf-8")
    if payload:
        (folder / ("scene.pkg" if kind == "scene" else file)).write_bytes(b"x" * 16)


def _conf(wid: str, text: str) -> None:
    paths.wp_file(wid).write_text(text, encoding="utf-8")


def _tree(root: Path) -> dict:
    """Every path under root with its kind, size and modification time."""
    out = {}
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            p = Path(folder) / name
            st = p.lstat()
            out[str(p.relative_to(root))] = (p.is_dir(), st.st_size, st.st_mtime_ns)
    return out


def setUpModule() -> None:
    paths.ensure_dirs()
    settings.ensure_exists()
    settings.save({**settings.load(), "WALLPAPERS_DIR": str(LIB), "WORKSHOP_DIR": str(WORKSHOP)})
    _item(LIB / "101", "Delta")
    tags.set_state("101", "Delta", "good")
    tags.set_state("102", "Bravo", "good")
    _item(LIB / "103", "Echo")
    tags.set_state("103", "Echo", "review")
    _item(LIB / "104", "Trashed One")
    _item(WORKSHOP / "104", "Trashed One")
    tags.set_state("104", "Trashed One", "bad")
    _item(LIB / "105", "alpha")
    tags.set_state("105", "alpha", "old")
    _item(LIB / "701", "Golf")
    _item(LIB / "bad name", "Unsafe")
    _item(WORKSHOP / "301", "Golf", "video")
    _item(WORKSHOP / "302", "Half", payload=False)
    _item(paths.manual_dir() / "hand_made", "Hotel", "web")
    _item(LIB / "401", "Alpha")
    tags.set_state("401", "Alpha", "good")
    _conf("401", "BG=101\n")
    _item(REFERENCE, "India")
    tags.set_state("501", "India", "good")
    _conf("501", f"BG={REFERENCE}\n")
    records.append("601", records.make_event("deleted", where="library"))
    _conf("101", "ALIAS=first\nALIAS=Deep\n")
    _conf("105", "ALIAS=dEEP\n")


class CatalogTest(unittest.TestCase):
    def test_library_ids_equal_the_grid_copy(self) -> None:
        self.assertEqual(catalog.library_ids(), models.library_ids())

    def test_pool_is_the_grid_all_scope(self) -> None:
        model = models.LibraryModel()
        model.reload()
        proxy = models.LibraryFilterModel(model)
        kept = {proxy.data(proxy.index(i, 0), models._ROLE_ID) for i in range(proxy.rowCount())}
        rows, _unsafe = catalog.wallpaper_rows()
        self.assertEqual({r.id for r in rows if r.state in ("pool", "missing")},
                         {wid for wid in kept if paths.is_safe_wid(wid)})

    def test_waiting_rows_are_the_workshop_tiles(self) -> None:
        tiles = WorkshopBridge(models.Backend(), None).itemList()
        rows, _unsafe = catalog.wallpaper_rows()
        self.assertEqual({r.id for r in rows if r.state == "waiting"},
                         {t["wid"] for t in tiles if paths.is_safe_wid(t["wid"])})

    def test_download_rows_are_the_importer_scan(self) -> None:
        rows, _unsafe = catalog.wallpaper_rows()
        self.assertEqual(sorted(r.id for r in rows if r.state == "download"), sorted(importer.scan_new()))

    def test_rows_are_numbered_by_title_then_id_pool_first(self) -> None:
        rows, unsafe = catalog.wallpaper_rows()
        self.assertEqual(rows, [
            Row(1, "105", "alpha", "dEEP", "scene", "pool", False),
            Row(2, "401", "Alpha", "", "scene", "pool", False),
            Row(3, "102", "Bravo", "", "", "missing", False),
            Row(4, "101", "Delta", "Deep", "scene", "pool", False),
            Row(5, "501", "India", "", "scene", "pool", False),
            Row(6, "103", "Echo", "", "scene", "waiting", False),
            Row(7, "301", "Golf", "", "video", "download", False),
            Row(8, "701", "Golf", "", "scene", "waiting", False),
            Row(9, "hand_made", "Hotel", "", "web", "download", False),
        ])
        self.assertEqual(unsafe, 1)

    def test_trash_rows_with_and_without_a_download_folder(self) -> None:
        self.assertEqual(catalog.trash_rows(), [
            Row(1, "601", "601", "", "", "trashed", False),
            Row(2, "104", "Trashed One", "", "scene", "trashed", True),
        ])

    def test_render_dir(self) -> None:
        self.assertEqual(catalog.render_dir("101"), str(LIB / "101"))
        self.assertEqual(catalog.render_dir("501"), str(REFERENCE))
        self.assertEqual(catalog.render_dir("401"), str(LIB / "101"))
        self.assertEqual(catalog.render_dir("301"), str(WORKSHOP / "301"))
        self.assertEqual(catalog.render_dir("hand_made"), str(paths.manual_dir() / "hand_made"))
        self.assertEqual(catalog.render_dir("102"), "")

    def test_alias_index_takes_the_last_assignment_and_ignores_case(self) -> None:
        self.assertEqual(catalog.alias_index(), {"deep": ["101", "105"]})

    def test_the_catalog_writes_nothing(self) -> None:
        before = _tree(_ROOT)
        catalog.library_ids()
        catalog.wallpaper_rows()
        catalog.trash_rows()
        catalog.alias_index()
        for wid in ("101", "102", "301", "401", "501", "hand_made"):
            catalog.render_dir(wid)
        self.assertEqual(_tree(_ROOT), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
