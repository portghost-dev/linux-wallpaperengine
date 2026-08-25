"""Regression tests for the Library filter (live-app bugs #1/#2/#3) and the F10 review scope.

#1 search match must be case-insensitive on BOTH sides ("Imp"/"imp"/"IMP" all match "Imperial...",
   and a mid-word fragment "mpe" also matches).
#2 the filter must produce a correct, fully-recomputed row set on every change (the proxy emits
   reset/insert/remove signals so the GridView relayouts - verified here at the model level via
   rowCount; the GridView contentHeight relayout is verified in test_card_render.py).
#3 toggling a favorite updates the filtered view live (dynamicSortFilter), and switching
   all->favorites->all restores the full set (the old DelegateModel left it stale).
F10 scope="review" must render the rows the source model flags pendingReview (on disk, never
   classified good/bad in tags.csv) - not unconditionally return empty. A non-pending row must
   never appear in review scope, and switching back to "all" must restore everything.

Pure model-level (no QML rendering). Uses an offscreen QGuiApplication.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication

from lwe_ui.models import LibraryModel, LibraryFilterModel, _Row, _ROLE_ID


def _model_with(rows: list[tuple[str, str, bool]]) -> LibraryModel:
    m = LibraryModel()
    objs = []
    for wid, title, fav in rows:
        r = _Row(wid)
        r.title = title
        r.favorite = fav
        objs.append(r)
    m.beginResetModel()
    m._rows = objs
    m.endResetModel()
    return m


def _model_with_review(rows: list[tuple[str, str, bool]]) -> LibraryModel:
    """Like _model_with, but the 3rd tuple element is pending_review, not favorite."""
    m = LibraryModel()
    objs = []
    for wid, title, pending in rows:
        r = _Row(wid)
        r.title = title
        r.pending_review = pending
        objs.append(r)
    m.beginResetModel()
    m._rows = objs
    m.endResetModel()
    return m


def _ids(proxy: LibraryFilterModel) -> set[str]:
    return {proxy.data(proxy.index(i, 0), _ROLE_ID) for i in range(proxy.rowCount())}


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(["t"])  # noqa: F841 (keep ref)

    src = _model_with([
        ("2105138680", "Imperial vs Chaos Space Battle", False),
        ("1505438974", "Deep Space", True),
        ("1275921440", "4K & 2K Aurora Lake", False),
    ])
    proxy = LibraryFilterModel(src)
    assert proxy.rowCount() == 3, "no filter -> all rows"

    for q in ("Imp", "imp", "IMP", "imperial", "mpe", "VS"):
        proxy.setSearchText(q)
        assert _ids(proxy) == {"2105138680"}, f"query {q!r} should match only Imperial, got {_ids(proxy)}"
    proxy.setSearchText("zzzz")
    assert proxy.rowCount() == 0, "non-matching query -> empty"

    proxy.setSearchText("")
    assert proxy.rowCount() == 3 and _ids(proxy) == {"2105138680", "1505438974", "1275921440"}, \
        "clearing search must restore every row"
    # match-by-id too (the haystack includes the id)
    proxy.setSearchText("1275921440")
    assert _ids(proxy) == {"1275921440"}
    proxy.setSearchText("")

    proxy.setFilterMode("favorites")
    assert _ids(proxy) == {"1505438974"}, "favorites mode shows only favorited rows"
    # favoriting another row updates the filtered view LIVE (dynamicSortFilter on dataChanged)
    src.set_favorite("1275921440", True)
    assert _ids(proxy) == {"1505438974", "1275921440"}, "toggling a favorite must update the view live"
    src.set_favorite("1505438974", False)
    assert _ids(proxy) == {"1275921440"}
    proxy.setFilterMode("all")
    assert proxy.rowCount() == 3, "all mode must restore the full set after a favorites round-trip"

    print("OK test_filter_model - search case-insensitive (#1), filter recompute (#2), live favorites (#3)")


def test_review_scope() -> None:
    """F10: review scope renders the pendingReview-filtered model, not an unconditional empty set."""
    src = _model_with_review([
        ("1111111111", "Reviewed Good", False),
        ("2222222222", "Never Classified A", True),
        ("3333333333", "Never Classified B", True),
    ])
    proxy = LibraryFilterModel(src)
    assert _ids(proxy) == {"1111111111"}, "default scope must hide pending rows (funnel A2)"

    proxy.setScope("review")
    assert _ids(proxy) == {"2222222222", "3333333333"}, (
        f"review scope must show only pendingReview rows, got {_ids(proxy)}"
    )
    assert "1111111111" not in _ids(proxy), "an already-classified row must never appear in review"

    # switching back to all restores the library set (review must not leave the proxy stuck
    # empty) - still excluding the pending rows, which belong to Workshop alone
    proxy.setScope("all")
    assert _ids(proxy) == {"1111111111"}, "leaving review scope must restore the library set"

    # empty review population -> genuinely empty (the label-only-at-zero contract in Library.qml)
    empty_src = _model_with_review([("4444444444", "All Classified", False)])
    empty_proxy = LibraryFilterModel(empty_src)
    empty_proxy.setScope("review")
    assert empty_proxy.rowCount() == 0, "review scope with nothing pending must be empty, not all rows"

    print("OK test_review_scope - review renders pendingReview rows only (F10)")


def test_scope_wiring_contract() -> None:
    """The rail's scope call must route through the FILTER MODEL.

    setScope lives on LibraryFilterModel, not Backend; Library.qml calling backend.setScope
    threw 'setScope is not a function' on every Favorites/Review click (found during manual
    UI testing - app-load never clicks the rail, so only a source contract pins it)."""
    from pathlib import Path

    assert hasattr(LibraryFilterModel, "setScope"), "filter model must expose setScope"
    qml = (Path(__file__).resolve().parent.parent
           / "src" / "lwe_ui" / "qml" / "Library.qml").read_text(encoding="utf-8")
    assert "backend.setScope(" not in qml, (
        "Library.qml must call backend.filterModel.setScope, not backend.setScope "
        "(Backend has no such slot; the call throws at click time)")
    assert "backend.filterModel.setScope(" in qml, "scope call missing entirely"
    print("OK test_scope_wiring_contract - rail scope routes through the filter model")


if __name__ == "__main__":
    main()
    test_review_scope()
    test_scope_wiring_contract()
