"""Approving a wallpaper into the pool, trashing it and lifting a trash block, and its playlist
membership, shared by the window and the commands. Plain Python; no Qt.

approve and untrash write one store after the other, each under its own lock. trash and the two
membership changes are each one change through the change runner: the status read, then, under the
playlists store lock, the playlists the change will alter decided and the sync marker set for them
and held across the store write, then the push of every engine-held playlist whose members
changed. They return the per-item results and the change's push.Outcome.
"""
from __future__ import annotations

import dataclasses
import os
import shutil
from collections.abc import Callable, Iterable
from typing import Any

from ..engine import push
from ..engine.resolve import _wallpapers_dir
from ..storage import importer, lock, meta, paths, playlists, records, tags


def approve(wid: str, title: str, event: dict | None = None) -> None:
    """Append event to the wallpaper's record when one is given, then set its tags state to good."""
    if event is not None:
        records.append(wid, event)
    tags.set_state(wid, title, "good")


def untrash(wid: str) -> None:
    """Append a bypassed event to the wallpaper's record, then drop its tags row, so the next scan
    imports it again."""
    records.append(wid, records.make_event("bypassed", where="workshop", initiator="human"))
    tags.remove(wid)


def _name(slug: str) -> str:
    return str(playlists.load(slug).get("NAME") or slug)


def _members_change(locks: Iterable[str], write: Callable[[list[str]], Any], affected: Callable[[], list[str]],
                    run: str, status: tuple[str, dict[str, Any] | None] | None) -> push.Outcome:
    """One change whose write reports the playlists it changed. Status is read before any lock;
    then the playlists store lock is held from affected(), which reads the playlists the write
    will change, through the write, so the change's rows name every one of them and the sync
    marker is set before any playlist changes. The push names the ones that did."""
    first = push.read_status() if status is None else status
    changed: list[str] = []
    with lock.held("playlists"):
        rows = [("members", slug) for slug in affected()] or [("none", None)]
        ticket = push.save_change(locks, lambda: write(changed), rows, run=run, status=first)
    done = tuple(("members", slug) for slug in dict.fromkeys(changed))
    return push.deliver(dataclasses.replace(ticket, rows=done or (("none", None),)))


def _holding(wids: Iterable[str]) -> list[str]:
    """The playlists whose members hold any of wids."""
    wanted = set(wids)
    return [row["slug"] for row in playlists.list_playlists() if wanted & set(row["MEMBERS"].split())]


def copy_deletable(wid: str) -> bool:
    """Whether LWE's own copy can go: a safe id whose library folder is a real subfolder of the
    library folder, never a symlink out of it."""
    if not paths.is_safe_wid(wid):
        return False
    lib = _wallpapers_dir()
    copy_dir = os.path.join(lib, wid)
    return (os.path.isdir(copy_dir) and not os.path.islink(copy_dir)
            and os.path.dirname(os.path.abspath(copy_dir)) == os.path.abspath(lib))


def dependents(wid: str) -> int:
    """How many other Workshop downloads declare wid as their base."""
    wid = str(wid)
    wsdir = importer.workshop_dir()
    n = 0
    try:
        from ..discovery import project
        for name in os.listdir(wsdir):
            if name == wid or not os.path.isdir(os.path.join(wsdir, name)):
                continue
            dep = (project.read(os.path.join(wsdir, name)).get("raw") or {}).get("dependency")
            deps = dep if isinstance(dep, list) else ([dep] if dep else [])
            if wid in [str(d) for d in deps]:
                n += 1
    except Exception:
        pass
    return n


def trash(items: Iterable[tuple[str, str]], comment: str | None = None, record: bool = True,
          run: str = "window", status: tuple[str, dict[str, Any] | None] | None = None
          ) -> tuple[list[dict[str, Any]], push.Outcome]:
    """Trash each (wid, title) in one change: a deleted event when record, tags bad, the id out of
    every playlist that holds it, and the missing-base mark cleared. An id already bad is left
    alone. Each result: id, title, already, left (the NAMEs of the playlists it left),
    copy_deletable and dependents."""
    items = list({str(wid): (str(wid), str(title)) for wid, title in items}.values())
    results = {wid: {"id": wid, "title": title, "already": False, "left": []} for wid, title in items}

    def write(changed: list[str]) -> None:
        bad = {row["id"] for row in tags.load() if row.get("state") == "bad"}
        slugs = [row["slug"] for row in playlists.list_playlists()]
        for wid, title in items:
            if wid in bad:
                results[wid]["already"] = True
                continue
            if record:
                records.append(wid, records.make_event("deleted", where="library", initiator="human",
                                                       comment=comment))
            tags.set_state(wid, title, "bad")
            for slug in slugs:
                if playlists.modify(slug, lambda d, w=wid: _without(d, [w])):
                    changed.append(slug)
                    results[wid]["left"].append(_name(slug))
            meta.modify(wid, lambda entry: {"depMissing": False} if entry.get("depMissing") else None)

    outcome = _members_change(("playlists", "tags", "meta", "records"), write,
                              lambda: _holding(wid for wid, _title in items), run, status)
    for r in results.values():
        r["copy_deletable"] = copy_deletable(r["id"])
        r["dependents"] = dependents(r["id"])
    return list(results.values()), outcome


def trash_tail(wid: str) -> bool:
    """After a trash: the texture cache rows the id owns, then LWE's own copy when copy_deletable.
    Returns whether the copy was removed."""
    from .. import texcomp
    texcomp.purge_wallpaper(wid)
    if not copy_deletable(wid):
        return False
    copy_dir = os.path.join(_wallpapers_dir(), wid)
    shutil.rmtree(copy_dir, ignore_errors=True)
    return not os.path.lexists(copy_dir)


def _without(d: dict[str, Any], wids: list[str]) -> dict[str, Any]:
    ids = d["MEMBERS"].split()
    kept = [i for i in ids if i not in wids]
    return {"MEMBERS": " ".join(kept)} if len(kept) != len(ids) else {}


def add_to_playlists(slugs: Iterable[str], wids: Iterable[str], run: str = "window",
                     status: tuple[str, dict[str, Any] | None] | None = None
                     ) -> tuple[list[dict[str, Any]], push.Outcome]:
    """Append the ids each playlist lacks at its end, in argument order, in one change. Each
    result: slug, name, id, and result "appended" or "already"."""
    slugs, wids = list(dict.fromkeys(slugs)), list(dict.fromkeys(wids))
    results: list[dict[str, Any]] = []

    def write(changed: list[str]) -> None:
        for slug in slugs:
            added: list[str] = []

            def append(d: dict[str, Any]) -> dict[str, Any]:
                ids = d["MEMBERS"].split()
                added.extend(w for w in wids if w not in ids)
                return {"MEMBERS": " ".join(ids + added)} if added else {}
            playlists.modify(slug, append)
            if added:
                changed.append(slug)
            name = _name(slug)
            results.extend({"slug": slug, "name": name, "id": w, "result": "appended" if w in added else "already"}
                           for w in wids)

    return results, _members_change(("playlists",), write,
                                    lambda: [s for s in slugs if not set(wids) <= set(playlists.members(s))],
                                    run, status)


def remove_from_playlists(slugs: Iterable[str], wids: Iterable[str], run: str = "window",
                          status: tuple[str, dict[str, Any] | None] | None = None
                          ) -> tuple[list[dict[str, Any]], push.Outcome]:
    """Take the ids out of each playlist that holds them, in one change. Each result: slug, name,
    id, and result "removed" or "absent"."""
    slugs, wids = list(dict.fromkeys(slugs)), list(dict.fromkeys(wids))
    results: list[dict[str, Any]] = []

    def write(changed: list[str]) -> None:
        for slug in slugs:
            held = playlists.members(slug)
            if playlists.modify(slug, lambda d: _without(d, wids)):
                changed.append(slug)
            name = _name(slug)
            results.extend({"slug": slug, "name": name, "id": w, "result": "removed" if w in held else "absent"}
                           for w in wids)

    return results, _members_change(("playlists",), write,
                                    lambda: [s for s in slugs if set(wids) & set(playlists.members(s))],
                                    run, status)
