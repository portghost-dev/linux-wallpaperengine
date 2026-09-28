"""The library as commands see it, without Qt: the pool and the Workshop in one numbered list, the
trash list, the folder a wallpaper renders from, and the alias index.

Every list leaves out ids that are not safe names and sorts by title (casefolded), then id. Nothing
here writes: no cache, no folder, no settings file.
"""
from __future__ import annotations

import os
from typing import NamedTuple

from ..discovery import project
from ..engine.resolve import _identity_dir, _wallpapers_dir
from ..storage import alias, importer, paths, records_view, tags, tier_a, wp


class Row(NamedTuple):
    n: int
    id: str
    title: str
    alias: str
    type: str
    state: str
    files: bool


def _scan_dir_ids(wallpapers_dir: str) -> list[str]:
    """Immediate subdirectory names of WALLPAPERS_DIR (each is a wallpaper id). Tolerant."""
    out: list[str] = []
    try:
        with os.scandir(wallpapers_dir) as it:
            for entry in it:
                try:
                    # dot-dirs are never wallpapers (.import-<wid> is the importer's
                    # staging area, and a half-copied tree must never be approvable)
                    if entry.is_dir() and not entry.name.startswith("."):
                        out.append(entry.name)
                except OSError:
                    continue
    except (OSError, ValueError):
        return []
    return out


def _is_present(wid: str, wallpapers_dir: str, dir_ids: set[str]) -> bool:
    """True if the wallpaper's render source exists on disk.

    Most wallpapers render from WALLPAPERS_DIR/<id>/. A bg!=id PRESET (dependency+preset) has no
    dir of its own - its render source is the base's dir, recorded as BG in its committed
    wp/<id>.conf. So presence = a same-named dir OR a committed conf whose BG dir resolves.
    (Pre-migration, before any wp/<id>.conf exists, a preset reads as not-present until migrated.)
    """
    if wid in dir_ids:
        return True
    try:
        cfg = wp.load(wid)
    except Exception:
        return False
    bg = str(cfg.get("BG", "") or "")
    if not bg:
        return False
    cand = bg if os.path.isabs(bg) else os.path.join(wallpapers_dir, bg)
    return os.path.isdir(cand)


def library_ids() -> list[str]:
    """Grid membership: disk presence defines membership. On-disk wallpapers get a card;
    `good`-but-absent ids are surfaced as broken; `bad` ids are excluded even while their
    dir is still on disk (a copy-mode trash deletes the tree asynchronously, and the card
    must leave the grid at trash time, not when the rm finishes). Sorted, deduped."""
    dir_ids = set(_scan_dir_ids(_wallpapers_dir()))
    try:
        good = tags.good_ids()
    except Exception:
        good = set()
    try:
        review = tags.review_ids()
    except Exception:
        review = set()
    try:
        bad = {r["id"] for r in tags.load() if r.get("id") and r.get("state") == "bad"}
    except Exception:
        bad = set()
    # review ids join the grid even without a library dir (a reference-policy import
    # renders from the workshop tree via its wp-conf BG, same as bg!=id presets)
    ids = (dir_ids - bad) | good | review
    ids.discard("")
    return sorted(ids)


def _aliases() -> dict[str, str]:
    """Each safe-named wp/*.conf's ALIAS value as written, stripped, read from the raw lines; a
    file without one, or one that cannot be read, is left out."""
    out: dict[str, str] = {}
    for conf in sorted(paths.wp_dir().glob("*.conf")):
        if not paths.is_safe_wid(conf.stem):
            continue
        try:
            text = conf.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        name = tier_a.parse(text).get("ALIAS", "").strip()
        if name:
            out[conf.stem] = name
    return out


def alias_index() -> dict[str, list[str]]:
    """{alias casefolded: the ids whose conf carries it, sorted}."""
    return alias.claims()


def _conf_type(wid: str) -> str:
    """The TYPE the wallpaper's conf carries, or "" when it carries none."""
    try:
        return str(wp.load_set(wid).get("TYPE") or "")
    except Exception:
        return ""


def _described(wid: str, folder: str | os.PathLike, titles: dict[str, str]) -> tuple[str, str]:
    """(title, type): the project file in `folder`, then the tags title or the conf TYPE, then the
    id or ""."""
    try:
        proj = project.read(folder)
    except Exception:
        proj = {}
    title = str(proj.get("title") or "") or titles.get(wid, "") or wid
    return title, str(proj.get("type") or "") or _conf_type(wid)


def _tag_rows() -> list[dict]:
    try:
        return [r for r in tags.load() if r.get("id")]
    except Exception:
        return []


def _numbered(groups: list[list[tuple[str, str, str, str, bool]]], aliases: dict[str, str]) -> list[Row]:
    """Rows from (title, id, type, state, files) groups, each group sorted, numbered on from 1."""
    rows: list[Row] = []
    for group in groups:
        for title, wid, wtype, state, files in sorted(group, key=lambda g: (g[0].casefold(), g[1])):
            rows.append(Row(len(rows) + 1, wid, title, aliases.get(wid, ""), wtype, state, files))
    return rows


def wallpaper_rows() -> tuple[list[Row], int]:
    """The pool, numbered from 1, then the Workshop rows numbered on from it, and the count of ids
    left out because their names are not safe, the folders of both pending roots included.

    Pool: the ids of library_ids() the tags know and do not hold for review (the grid's All
    scope); "missing" when nothing renders them, else "pool". Workshop: tags review rows and
    library folders the tags do not know ("waiting"), then complete downloads never seen
    ("download")."""
    lib = _wallpapers_dir()
    dir_ids = set(_scan_dir_ids(lib))
    tag_rows = _tag_rows()
    known = {r["id"] for r in tag_rows}
    review = {r["id"] for r in tag_rows if r.get("state") == "review"}
    titles = {r["id"]: r.get("title") or "" for r in tag_rows}
    unsafe: set[str] = set()
    pool: list[tuple[str, str, str, str, bool]] = []
    shop: list[tuple[str, str, str, str, bool]] = []
    for wid in library_ids():
        if wid not in known or wid in review:
            continue
        if not paths.is_safe_wid(wid):
            unsafe.add(wid)
            continue
        title, wtype = _described(wid, _identity_dir(wid, lib), titles)
        state = "pool" if _is_present(wid, lib, dir_ids) else "missing"
        pool.append((title, wid, wtype, state, False))
    for wid in sorted(review | (dir_ids - known)):
        if not paths.is_safe_wid(wid):
            unsafe.add(wid)
            continue
        title, wtype = _described(wid, _identity_dir(wid, lib), titles)
        shop.append((title, wid, wtype, "waiting", False))
    workshop = importer.workshop_dir()
    for root in (workshop, str(paths.manual_dir())):
        unsafe.update(name for name in _scan_dir_ids(root) if not paths.is_safe_wid(name))
    seen = {entry[1] for entry in shop}
    for wid in importer.scan_new():
        if wid in seen:
            continue
        seen.add(wid)
        title, wtype = _described(wid, paths.pending_root_for(wid, workshop) / wid, titles)
        shop.append((title, wid, wtype, "download", False))
    return _numbered([pool, shop], _aliases()), len(unsafe)


def trash_rows() -> list[Row]:
    """Trashed ids (tags state bad, or a record whose last event is a deletion), numbered from 1;
    files tells whether a download folder still holds them."""
    tag_rows = _tag_rows()
    titles = {r["id"]: r.get("title") or "" for r in tag_rows}
    ids = {r["id"] for r in tag_rows if r.get("state") == "bad"}
    try:
        ids |= set(records_view.tombstoned_wids())
    except Exception:
        pass
    workshop = importer.workshop_dir()
    manual = paths.manual_dir()
    found: list[tuple[str, str, str, str, bool]] = []
    for wid in ids:
        if not paths.is_safe_wid(wid):
            continue
        files = os.path.isdir(os.path.join(workshop, wid)) or (manual / wid).is_dir()
        folder = paths.pending_root_for(wid, workshop) / wid
        try:
            proj = project.read(folder) if folder.is_dir() else {}
        except Exception:
            proj = {}
        title = titles.get(wid, "") or str(proj.get("title") or "") or wid
        found.append((title, wid, str(proj.get("type") or "") or _conf_type(wid), "trashed", files))
    return _numbered([found], _aliases())


def render_dir(wid: str) -> str:
    """The folder the wallpaper renders from: an absolute conf BG, else the library folder of BG
    (or of the id), else the pending root that holds it; "" when none is a folder."""
    try:
        bg = str(wp.load(wid).get("BG") or "")
    except Exception:
        bg = ""
    if bg and os.path.isabs(bg) and os.path.isdir(bg):
        return bg
    cand = os.path.join(_wallpapers_dir(), bg or wid)
    if os.path.isdir(cand):
        return cand
    pending = paths.pending_root_for(wid, importer.workshop_dir()) / wid
    return str(pending) if pending.is_dir() else ""
