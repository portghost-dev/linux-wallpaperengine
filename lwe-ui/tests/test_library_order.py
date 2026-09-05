"""The library grid's order model and the drag drop table (design spec 1.1, 2.3; R1, R37, R44).

Sandboxes HOME/XDG before importing lwe_ui; drives the real Backend offscreen with seven seeded
wallpapers. Checks the member-first order, the filler padding at three and five columns, the
hairline index, the empty playlist, the search interplay, a checkbox landing as one row move
(no reset), and every row of the drop table with one storage write and one push per drop.

Run: PYTHONPATH=src python3 tests/test_library_order.py
"""
import _sandbox  # noqa: F401  (pins the engine socket before any lwe_ui import)
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lwe-order-test-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_STATE_HOME"] = str(Path(_TMP) / ".local/state")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local/share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="lwe-rt-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from lwe_ui.library_order import FILLER, MEMBER, POOL  # noqa: E402
from lwe_ui.storage import playlists, tags  # noqa: E402

IDS = ["100", "200", "300", "400", "500", "600", "700"]


def seed_library() -> None:
    wdir = Path(_TMP) / ".local/share/lwe/wallpapers"
    for wid in IDS:
        d = wdir / wid
        d.mkdir(parents=True, exist_ok=True)
        (d / "project.json").write_text(
            '{"title": "WP %s", "type": "scene", "file": "x", "preview": ""}' % wid, encoding="utf-8")
        tags.set_state(wid, "WP " + wid, "good")


_APP = QGuiApplication.instance() or QGuiApplication([])
seed_library()

from lwe_ui.models import Backend  # noqa: E402


class LibraryOrder(unittest.TestCase):
    def setUp(self) -> None:
        self.b = Backend()
        self.slug = playlists.active_slug()
        self.set_members(["300", "100", "500"])
        self.pushes = 0
        self.b._sync_engine = lambda: setattr(self, "pushes", self.pushes + 1)
        self.resets = 0
        self.b.orderModel.modelReset.connect(lambda: setattr(self, "resets", self.resets + 1))

    def set_members(self, ids: list[str]) -> None:
        d = playlists.load(self.slug)
        d["MEMBERS"] = " ".join(ids)
        playlists.save(self.slug, d)
        self.b.refresh()
        self.b.playlistsChanged.emit()

    def rows(self) -> list[tuple[str, str]]:
        return self.b.orderModel.order()

    def test_members_first_in_stored_order_then_pool_by_id(self) -> None:
        self.b.orderModel.setColumns(3)
        self.assertEqual(self.rows(), [(MEMBER, "300"), (MEMBER, "100"), (MEMBER, "500"),
                                       (POOL, "200"), (POOL, "400"), (POOL, "600"), (POOL, "700")])
        self.assertEqual(self.b.orderModel.hairlineIndex, 3)
        self.assertEqual(self.b.orderModel.memberCount, 3)

    def test_fillers_pad_the_member_block_to_a_column_boundary(self) -> None:
        self.b.orderModel.setColumns(5)
        self.assertEqual([k for k, _ in self.rows()][:6], [MEMBER, MEMBER, MEMBER, FILLER, FILLER, POOL])
        self.assertEqual(self.b.orderModel.fillerCount, 2)
        self.assertEqual(self.b.orderModel.hairlineIndex, 5)
        self.b.orderModel.setColumns(3)
        self.assertEqual(self.b.orderModel.fillerCount, 0)
        self.b.orderModel.setColumns(2)
        self.assertEqual(self.b.orderModel.fillerCount, 1)

    def test_empty_playlist_has_no_fillers_and_no_hairline(self) -> None:
        self.b.orderModel.setColumns(5)
        self.set_members([])
        self.assertEqual([k for k, _ in self.rows()], [POOL] * 7)
        self.assertEqual(self.b.orderModel.hairlineIndex, -1)
        self.assertEqual(self.b.orderModel.fillerCount, 0)

    def test_search_hides_a_member_and_the_block_repads(self) -> None:
        self.b.orderModel.setColumns(5)
        self.b.filterModel.setSearchText("WP 100")
        self.assertEqual(self.rows(), [(MEMBER, "100")], "no pool: no padding, no hairline")
        self.assertEqual(self.b.orderModel.hairlineIndex, -1)
        self.b.filterModel.setSearchText("")
        self.b.filterModel.setSearchText("00")
        self.assertEqual(self.b.orderModel.fillerCount, 2)
        self.b.filterModel.setSearchText("")

    def test_checkbox_lands_as_one_row_move_not_a_reset(self) -> None:
        self.b.orderModel.setColumns(3)
        self.resets = 0
        self.b.setPlaylist("400", True)
        self.assertEqual([w for k, w in self.rows() if k == MEMBER], ["300", "100", "500", "400"])
        self.assertEqual(self.b.orderModel.fillerCount, 2)
        self.b.setPlaylist("100", False)
        self.assertEqual([w for k, w in self.rows() if k == MEMBER], ["300", "500", "400"])
        self.assertEqual([w for k, w in self.rows() if k == POOL], ["100", "200", "600", "700"])
        self.assertEqual(self.resets, 0, "a checkbox is one row move, never a reset")

    # --- drop table (spec 2.3 as amended by R37) ---
    def test_member_over_a_member_slot_reorders(self) -> None:
        self.b.orderModel.setColumns(3)
        self.assertTrue(self.b.beginDrag("500"))
        self.assertTrue(self.b.orderModel.dragging)
        self.b.dragOver(0)
        self.assertEqual([w for k, w in self.rows() if k == MEMBER], ["500", "300", "100"])
        self.assertEqual(self.b.endDrag(True), "reorder")
        self.assertEqual(playlists.members(self.slug), ["500", "300", "100"])
        self.assertEqual(self.pushes, 1)
        self.assertFalse(self.b.orderModel.dragging)

    def test_member_over_a_filler_cell_moves_to_the_end(self) -> None:
        self.b.orderModel.setColumns(5)  # three members, two fillers, then the pool
        self.b.beginDrag("300")
        self.b.dragOver(4)  # the second filler cell
        self.assertEqual([w for k, w in self.rows() if k == MEMBER], ["100", "500", "300"])
        self.assertEqual(self.b.endDrag(True), "reorder")
        self.assertEqual(playlists.members(self.slug), ["100", "500", "300"])
        self.assertEqual(self.pushes, 1)

    def test_member_over_the_pool_drops_home(self) -> None:
        self.b.orderModel.setColumns(5)
        self.b.beginDrag("300")
        self.b.dragOver(1)
        self.b.dragOver(6)  # over the pool: R37, never a removal, and not a move either
        self.assertEqual([w for k, w in self.rows() if k == MEMBER], ["300", "100", "500"])
        self.assertEqual(self.b.endDrag(True), "none")
        self.assertEqual(playlists.members(self.slug), ["300", "100", "500"])
        self.assertEqual(self.pushes, 0)

    def test_member_dropped_home_or_outside_changes_nothing(self) -> None:
        self.b.orderModel.setColumns(3)
        self.b.beginDrag("100")
        self.b.dragOver(2)
        self.b.dragOver(1)  # back to its own slot
        self.assertEqual(self.b.endDrag(True), "none")
        self.b.beginDrag("100")
        self.b.dragOver(0)
        self.b.dragOver(-1)  # released outside the grid
        self.assertEqual(self.b.endDrag(True), "none")
        self.assertEqual(playlists.members(self.slug), ["300", "100", "500"])
        self.assertEqual(self.pushes, 0)

    def test_pool_card_over_a_member_slot_is_added_there(self) -> None:
        self.b.orderModel.setColumns(3)
        self.b.beginDrag("400")
        self.b.dragOver(1)
        self.assertEqual([w for k, w in self.rows() if k == MEMBER], ["300", "400", "100", "500"])
        self.assertEqual(self.b.endDrag(True), "insert")
        self.assertEqual(playlists.members(self.slug), ["300", "400", "100", "500"])
        self.assertEqual(self.pushes, 1)
        row = self.b.orderModel.rowOf("400")
        self.assertTrue(self.b.orderModel.data(self.b.orderModel.index(row, 0), 0x0101), "checkbox turns on")

    def test_pool_card_over_a_padding_cell_is_added_at_the_end(self) -> None:
        self.b.orderModel.setColumns(5)  # three members, two padding cells
        self.b.beginDrag("600")
        self.b.dragOver(4)
        self.assertEqual([w for k, w in self.rows() if k == MEMBER], ["300", "100", "500", "600"])
        self.assertEqual(self.b.orderModel.fillerCount, 1)
        self.assertEqual(self.b.endDrag(True), "insert")
        self.assertEqual(playlists.members(self.slug), ["300", "100", "500", "600"])
        self.assertEqual(self.pushes, 1)

    def test_the_hairline_band_means_end_for_a_member_and_home_for_a_pool_card(self) -> None:
        self.b.orderModel.setColumns(3)
        self.b.beginDrag("300")
        self.b.dragOver(-2)
        self.assertEqual([w for k, w in self.rows() if k == MEMBER], ["100", "500", "300"])
        self.assertEqual(self.b.endDrag(True), "reorder")
        self.b.beginDrag("600")
        self.b.dragOver(0)
        self.b.dragOver(-2)
        self.assertEqual([w for k, w in self.rows() if k == MEMBER], ["100", "500", "300"])
        self.assertEqual(self.b.endDrag(True), "none")

    def test_pool_card_over_the_pool_or_outside_drops_home(self) -> None:
        self.b.orderModel.setColumns(3)
        self.b.beginDrag("600")
        self.b.dragOver(0)
        self.assertEqual([w for k, w in self.rows() if k == MEMBER], ["600", "300", "100", "500"])
        self.b.dragOver(6)  # back over the pool: provisional add withdrawn
        self.assertEqual([w for k, w in self.rows() if k == MEMBER], ["300", "100", "500"])
        self.assertEqual(self.b.endDrag(True), "none")
        self.assertEqual(playlists.members(self.slug), ["300", "100", "500"])
        self.assertEqual(self.pushes, 0)

    def test_drag_under_a_filter_keeps_hidden_members_in_place(self) -> None:
        # only "00" ids show: members 300 100 500 all show; hide 100 by favorites scope instead
        self.b.orderModel.setColumns(2)
        self.b.toggleFavorite("300"); self.b.toggleFavorite("500"); self.b.toggleFavorite("400")
        self.b.filterModel.setScope("favorites")
        self.assertEqual(self.rows(), [(MEMBER, "300"), (MEMBER, "500"), (POOL, "400")])
        # a pool card released on its own pool cell: home, never a member (R37)
        self.b.beginDrag("400"); self.b.dragOver(2)
        self.assertEqual(self.b.endDrag(True), "none")
        # a member released on its own visible slot: nothing is rewritten
        self.b.beginDrag("500"); self.b.dragOver(1)
        self.assertEqual(self.b.endDrag(True), "none")
        self.assertEqual(playlists.members(self.slug), ["300", "100", "500"])
        # a visible reorder keeps the hidden member in its place
        self.b.beginDrag("500"); self.b.dragOver(0)
        self.assertEqual(self.b.endDrag(True), "reorder")
        self.assertEqual(playlists.members(self.slug), ["500", "100", "300"])
        # a pool card added at visible slot 1 lands before the visible member that follows it
        self.b.beginDrag("400"); self.b.dragOver(1)
        self.assertEqual(self.b.endDrag(True), "insert")
        self.assertEqual(playlists.members(self.slug), ["500", "100", "400", "300"])
        self.b.filterModel.setScope("all")

    def test_full_member_block_with_no_pool_still_reaches_the_end(self) -> None:
        self.set_members(["100", "200", "300", "400", "500", "600", "700"])
        self.b.orderModel.setColumns(7)
        self.assertEqual(self.b.orderModel.hairlineIndex, -1)
        self.b.beginDrag("100"); self.b.dragOver(7)  # below the only row
        self.assertEqual(self.b.endDrag(True), "reorder")
        self.assertEqual(playlists.members(self.slug)[-1], "100")

    def test_a_source_reset_mid_drag_releases_the_drag_without_storing(self) -> None:
        self.b.orderModel.setColumns(3)
        self.b.beginDrag("500"); self.b.dragOver(0)
        self.b.refresh()  # a library rescan resets the source model
        self.assertFalse(self.b.orderModel.dragging)
        self.assertEqual(self.b.endDrag(True), "none")
        self.assertEqual(playlists.members(self.slug), ["300", "100", "500"])
        self.assertTrue(self.b.beginDrag("500"), "dragging works again afterwards")
        self.assertEqual(self.b.endDrag(False), "none")

    def test_cancelled_drag_restores_the_stored_order(self) -> None:
        self.b.orderModel.setColumns(3)
        self.b.beginDrag("500")
        self.b.dragOver(0)
        self.assertEqual(self.b.endDrag(False), "none")
        self.assertEqual([w for k, w in self.rows() if k == MEMBER], ["300", "100", "500"])
        self.assertEqual(self.pushes, 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
