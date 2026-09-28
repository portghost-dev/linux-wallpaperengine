"""Picking wallpapers and playlists from command words.

A wallpaper word: "current" is the wallpaper on screen; "@name" is an alias; one to five ASCII
digits are a place in the list; six to eight digits are refused with both readings; nine or more
digits are an id; any other word is an exact id or a title ignoring case. In the wallpaper list, a
word that names no listed row may still name a trashed wallpaper or, when member_of names
playlists, one of their members. A playlist word: one to five ASCII digits are a place in
playlist_rows(); any other word is a name. Every word is resolved before anything returns, and a
refusal raises PickError.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import NamedTuple

from .. import api_client, version
from ..engine import resolve
from ..library import catalog
from ..storage import alias, playlists

_DIGITS = re.compile(r"[0-9]+")


class Pick(NamedTuple):
    number: int | None
    ui_id: str
    engine_id: str
    title: str
    alias: str
    source: str


class PlaylistPick(NamedTuple):
    number: int
    slug: str
    name: str


class PickError(Exception):
    """A refusal: code is the exit code, message one line without the program prefix, lines the
    extra text lines and choices the picks the refusal lists."""

    def __init__(self, code: int, message: str, lines: list[str] | None = None,
                 choices: list | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.lines = lines or []
        self.choices = choices or []


def report(ctx, exc: PickError) -> int:
    """Print a refusal the way cli.main prints its own, the extra lines in text mode only."""
    ctx.error(exc.message, "lwe: " + exc.message)
    if not ctx.json:
        for line in exc.lines:
            print(line, file=ctx.err)
    return exc.code


def pick_line(p: Pick) -> str:
    return f"{p.title} ({p.ui_id})" if p.number is None else f"{p.number} = {p.title} ({p.ui_id})"


def _ambiguous(word: str, picks: list[Pick]) -> PickError:
    return PickError(1, f"{word} matches more than one wallpaper; nothing was changed",
                     ["  " + pick_line(p) for p in picks], picks)


class _Resolver:
    """The rows one call resolves its words against, read once."""

    def __init__(self, domain: str, member_of: Iterable[str]) -> None:
        if domain == "wallpaper":
            self.rows = catalog.wallpaper_rows()[0]
            self.trash = catalog.trash_rows()
        elif domain == "trash":
            self.rows = catalog.trash_rows()
            self.trash = []
        else:
            raise ValueError(f"unknown domain {domain}")
        self.domain = domain
        self.member_of = tuple(member_of)
        self._members: list[str] | None = None

    def _pick(self, row: catalog.Row, numbered: bool = True) -> Pick:
        return Pick(row.n if numbered else None, row.id, resolve.resolve_show_args(row.id)[0],
                    row.title, row.alias, row.state if numbered else "trashed")

    def _bare(self, wid: str, title: str, source: str) -> Pick:
        return Pick(None, wid, resolve.resolve_show_args(wid)[0], title, "", source)

    def _member_ids(self) -> list[str]:
        """Ids the member_of playlists hold that no listed or trashed row has."""
        if self._members is None:
            listed = {r.id for r in self.rows} | {r.id for r in self.trash}
            self._members = []
            for slug in self.member_of:
                for wid in playlists.members(slug):
                    if wid not in listed and wid not in self._members:
                        self._members.append(wid)
        return self._members

    def _no_match(self, word: str) -> PickError:
        if self.domain == "trash":
            return PickError(1, f"{word} is not in the trash")
        return PickError(1, f"nothing matches {word}")

    def pick(self, word: str) -> Pick:
        if word == "":
            raise PickError(3, "an empty word names nothing")
        if word == "current":
            return self._current()
        if word.startswith("@"):
            return self._alias(word)
        if _DIGITS.fullmatch(word):
            if len(word) <= 5:
                return self._place(int(word))
            if len(word) <= 8:
                raise self._both_readings(word)
            return self._identified(word, lambda wid, _title: wid == word)
        folded = word.casefold()
        return self._identified(word, lambda wid, title: wid == word or title.casefold() == folded)

    def _identified(self, word: str, match: Callable[[str, str], bool]) -> Pick:
        """One listed row, else (wallpaper list) one trashed row, else one member id; two at the
        first step that finds any refuse and are listed."""
        hits = [self._pick(r) for r in self.rows if match(r.id, r.title)]
        if not hits and self.domain == "wallpaper":
            hits = [self._pick(r, numbered=False) for r in self.trash if match(r.id, r.title)]
        if not hits and self.member_of:
            hits = [self._bare(wid, wid, "member") for wid in self._member_ids() if match(wid, wid)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise _ambiguous(word, hits)
        raise self._no_match(word)

    def _place(self, n: int) -> Pick:
        if 1 <= n <= len(self.rows):
            return self._pick(self.rows[n - 1])
        raise PickError(1, f"there is no number {n} in the list (1 to {len(self.rows)})")

    def _both_readings(self, word: str) -> PickError:
        n = int(word)
        at = self._pick(self.rows[n - 1]) if 1 <= n <= len(self.rows) else None
        same = next((self._pick(r) for r in self.rows if r.id == word), None)
        return PickError(
            3, f"{word} has 6 to 8 digits; a list number has at most 5 and a Workshop id 9 or 10",
            [f"  as a list number: {pick_line(at) if at else 'nothing'}",
             f"  as an id: {pick_line(same) if same else 'nothing'}"],
            [p for p in (at, same) if p is not None])

    def _alias(self, word: str) -> Pick:
        name = word[1:]
        if not name:
            raise PickError(3, "@ needs an alias after it")
        wid = alias.index().get(name.casefold())
        if wid is None:
            ids = alias.claims().get(name.casefold(), [])
            if len(ids) > 1:
                raise _ambiguous(word, [self._listed(w) for w in ids])
            raise self._no_match(word)
        return self._identified(word, lambda w, _title: w == wid)

    def _listed(self, wid: str) -> Pick:
        """The pick a refusal lists for an id: its row, its trash row or member id, else the id."""
        for r in self.rows:
            if r.id == wid:
                return self._pick(r)
        for r in self.trash:
            if r.id == wid:
                return self._pick(r, numbered=False)
        if self.member_of and wid in self._member_ids():
            return self._bare(wid, wid, "member")
        return self._bare(wid, wid, "missing")

    def _current(self) -> Pick:
        status = api_client.status()
        if status is None:
            if not api_client.available():
                raise PickError(2, "the service is not running, so current names nothing")
            raise PickError(1, "the service is not answering")
        try:
            refusal = version.running_refusal(status, version.panel_stamp())
        except version.StampError as exc:
            raise PickError(1, str(exc)) from None
        if refusal is not None:
            raise PickError(1, refusal)
        cur = status.get("current")
        cur = cur if isinstance(cur, dict) else {}
        wid = str(cur.get("ui_id") or "") or str(cur.get("id") or "")
        if not wid:
            raise PickError(1, "nothing is on screen")
        for r in self.rows:
            if r.id == wid:
                return self._pick(r)
        if self.domain == "trash":
            raise self._no_match("current")
        for r in self.trash:
            if r.id == wid:
                return self._pick(r, numbered=False)
        return self._bare(wid, str(cur.get("title") or "") or wid, "screen")


def wallpapers(words: Iterable[str], domain: str = "wallpaper",
               member_of: Iterable[str] = ()) -> list[Pick]:
    """One pick per wallpaper the words name, in the order first named."""
    resolver = _Resolver(domain, member_of)
    picks: list[Pick] = []
    for word in words:
        p = resolver.pick(word)
        if all(q.ui_id != p.ui_id for q in picks):
            picks.append(p)
    return picks


def wallpaper(word: str, domain: str = "wallpaper") -> Pick:
    return _Resolver(domain, ()).pick(word)


def playlist_rows() -> list[PlaylistPick]:
    """Every playlist, numbered from 1 in the store's order (by name)."""
    return [PlaylistPick(n, row["slug"], str(row.get("NAME") or ""))
            for n, row in enumerate(playlists.list_playlists(), 1)]


def _name_key(text: str) -> str:
    return " ".join(text.split()).casefold()


def playlist(word: str) -> PlaylistPick:
    if word == "":
        raise PickError(3, "an empty word names nothing")
    rows = playlist_rows()
    key = _name_key(word)
    named = [r for r in rows if r.name and _name_key(r.name) == key]
    if _DIGITS.fullmatch(word) and len(word) <= 5:
        n = int(word)
        if not 1 <= n <= len(rows):
            raise PickError(1, f"there is no number {n} in the list (1 to {len(rows)})")
        at = rows[n - 1]
        if all(r == at for r in named):
            return at
        named = [at] + [r for r in named if r != at]
    if len(named) == 1:
        return named[0]
    if named:
        raise PickError(1, f"{word} matches more than one playlist",
                        [f"  {r.number} = {r.name} ({r.slug}.conf)" for r in named], named)
    raise PickError(1, f"no playlist matches {word}")
