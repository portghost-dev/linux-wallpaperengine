"""The sync marker, <state_dir>/panel/sync-pending: the durable record that the engine may lack
what the store holds.

The file holds {"version": 1, "generation": N, "classes": [...], "sent": {"pid": N or null,
"playlists": [...]}}. BUNDLE means the engine needs the full sync from the store, and CURRENT
that the wallpaper on screen also needs a re-show. sent lists the playlists a window run has
already transferred in this generation to that engine pid. A writer raises the generation of
the marker; ensure makes a fresh one when no marker exists and raises it when it adds a class
the marker did not hold; a clear keeps it. So a generation value never repeats, and a clear
succeeds only for the generation its caller read.
Every read and write holds the marker lock, and every write is an atomic replace followed by
an fsync of the directory. Plain Python, no Qt import.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from ..storage import atomic, lock, paths

_CLASSES = ("BUNDLE", "CURRENT")


def _file() -> Path:
    return paths.panel_state_dir() / "sync-pending"


def _state(generation: int | None, classes: Iterable[str], pid: int | None = None,
           playlists: Iterable[str] = ()) -> dict[str, Any]:
    return {"generation": generation, "classes": list(classes),
            "sent": {"pid": pid, "playlists": list(playlists)}}


def _write(state: dict[str, Any]) -> None:
    path = _file()
    atomic.atomic_write_text(path, json.dumps({"version": 1, **state}) + "\n")
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _load() -> dict[str, Any]:
    """The marker's state; the caller holds the marker lock. A malformed file, or one with
    another version, is rewritten as BUNDLE and CURRENT with an empty sent and a fresh
    generation."""
    try:
        doc = json.loads(_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _state(None, ())
    except ValueError:
        doc = None
    sent = doc.get("sent") if isinstance(doc, dict) else None
    if (isinstance(sent, dict) and type(doc.get("version")) is int and doc["version"] == 1
            and type(doc.get("generation")) is int
            and isinstance(doc.get("classes"), list) and all(c in _CLASSES for c in doc["classes"])
            and "pid" in sent and (sent["pid"] is None or type(sent["pid"]) is int)
            and isinstance(sent.get("playlists"), list)
            and all(isinstance(slug, str) for slug in sent["playlists"])):
        return _state(doc["generation"], (c for c in _CLASSES if c in doc["classes"]),
                      sent["pid"], sent["playlists"])
    state = _state(time.time_ns(), _CLASSES)
    _write(state)
    return state


def _fresh(state: dict[str, Any]) -> int:
    return time.time_ns() if state["generation"] is None else state["generation"] + 1


def _merged(have: Iterable[str], add: Iterable[str]) -> list[str]:
    add = list(add)
    for name in add:
        if name not in _CLASSES:
            raise ValueError(f"not a marker class: {name}")
    have = list(have)
    return [c for c in _CLASSES if c in have or c in add]


def _matches(state: dict[str, Any], generation: int) -> bool:
    return state["generation"] is not None and state["generation"] == generation


def read() -> dict[str, Any]:
    """The marker as {"generation", "classes", "sent": {"pid", "playlists"}}: with no file,
    generation None, no classes and an empty sent. A malformed file, or one with another
    version, reads as BUNDLE and CURRENT with an empty sent and is rewritten in place."""
    with lock.held("marker"):
        return _load()


@contextmanager
def writing(classes: Iterable[str]) -> Iterator[tuple[int, bool]]:
    """A writer's marker step, taken before its store write: raise the generation (the kept
    value plus 1; with no file, time.time_ns()), add `classes`, empty sent and write, then
    yield (generation, existed), existed being true when the marker had classes before. The
    marker lock stays held until the body, the store write, ends. A failed marker write
    raises OSError before the body runs."""
    with lock.held("marker"):
        state = _load()
        generation = _fresh(state)
        _write(_state(generation, _merged(state["classes"], classes)))
        yield generation, bool(state["classes"])


def ensure(classes: Iterable[str]) -> int:
    """With no marker (no file, or no classes), make a fresh generation as writing() does,
    with `classes`. With one, a class it does not hold raises the generation and empties sent,
    as writing() does, so a run that read the older generation cannot clear it; classes it
    already holds keep the generation. Returns the generation."""
    with lock.held("marker"):
        state = _load()
        merged = _merged(state["classes"], classes)
        if state["classes"] and merged == state["classes"]:
            return state["generation"]
        generation = _fresh(state)
        _write(_state(generation, merged))
        return generation


def generation() -> int | None:
    """The generation, read under the marker lock: a clearer reads it before its first store
    read. None with no file."""
    with lock.held("marker"):
        return _load()["generation"]


def clear(generation: int, keep: Iterable[str] = ()) -> bool:
    """Only while the marker's generation is `generation`: no classes but those of `keep` it
    holds, an empty sent, the generation kept. Returns whether the generation matched. A failed
    write puts the previous record back, best effort, and raises OSError, so a failed clear
    never leaves a visibly cleared marker."""
    with lock.held("marker"):
        state = _load()
        if not _matches(state, generation):
            return False
        cleared = _state(generation, (c for c in state["classes"] if c in keep))
        if cleared != state:
            try:
                _write(cleared)
            except OSError:
                with suppress(OSError):
                    _write(state)
                raise
        return True


def sent_for(generation: int, pid: int) -> list[str]:
    """The playlists listed in sent while both the generation and the engine pid match;
    otherwise none."""
    with lock.held("marker"):
        state = _load()
        if not _matches(state, generation) or state["sent"]["pid"] != pid:
            return []
        return state["sent"]["playlists"]


def start_sent(generation: int, pid: int) -> None:
    """At a window run's start: while the generation matches and sent names another pid,
    empty sent."""
    with lock.held("marker"):
        state = _load()
        listed = state["sent"]["pid"]
        if _matches(state, generation) and listed is not None and listed != pid:
            _write(_state(state["generation"], state["classes"]))


def record_sent(generation: int, pid: int, slug: str) -> bool:
    """Append `slug` to sent and set its pid, only while the generation matches and the listed
    pid is empty or `pid`; otherwise record nothing. Returns whether it recorded."""
    with lock.held("marker"):
        state = _load()
        listed, playlists = state["sent"]["pid"], state["sent"]["playlists"]
        if not _matches(state, generation) or listed not in (None, pid):
            return False
        if listed != pid or slug not in playlists:
            _write(_state(state["generation"], state["classes"], pid,
                          playlists + ([] if slug in playlists else [slug])))
        return True
