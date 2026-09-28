"""The sync marker, <state_dir>/panel/sync-pending: the durable record that the engine may lack
what the store holds.

The file holds {"version": 1, "generation": N, "classes": [...], "sent": {"pid": N or null,
"playlists": [...]}, "served": {"pid": P, "at": T, "boot": B, "start": S, "braked": F}, "owed": {"pid": P,
"boot": B, "start": S, "current": G}}. BUNDLE means the engine needs the full sync from the store, and
CURRENT that the wallpaper on screen also needs a re-show. sent lists the playlists a window run has
already transferred in this generation to that engine pid. served names the engine that last took a
whole bundle: its pid, the boot_id of the boot it ran in, its start time S on the engine's clock (None when
its status gave no uptime, which names no engine) and whether it took the bundle under the brake F, written
at time T. owed names the engine the owed bundle was last owed to, with the generation G at which owe
added CURRENT for it (None once taken back). Both are written without raising the generation, every other
write keeps them, and a file without them reads as none, so an engine the served record does not name is
owed the bundle.
A record that holds only served has no generation, and reads as no marker for everything else.
A writer raises the generation of the marker; ensure makes a fresh one when no marker exists and
raises it when it adds a class the marker did not hold; a clear keeps it. So a generation value
never repeats, and a clear succeeds only for the generation its caller read. A service start or
restart holds the sync lock from its record through its own sync, so no other run can clear the
record in between. Every read and write holds the marker lock, and every write is an atomic
replace followed by an fsync of the directory. Plain Python, no Qt import.
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
_SIDE = ("served", "owed")
_START_SLACK_S = 5.0
_BOOT_ID = Path("/proc/sys/kernel/random/boot_id")


def _file() -> Path:
    return paths.panel_state_dir() / "sync-pending"


def _state(generation: int | None, classes: Iterable[str], pid: int | None = None,
           playlists: Iterable[str] = (), served: dict[str, Any] | None = None,
           owed: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"generation": generation, "classes": list(classes),
            "sent": {"pid": pid, "playlists": list(playlists)}, "served": served, "owed": owed}


def _public(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key not in _SIDE}


def _write(state: dict[str, Any]) -> None:
    path = _file()
    doc = {"version": 1, **_public(state)}
    for key in _SIDE:
        if state[key] is not None:
            doc[key] = state[key]
    atomic.atomic_write_text(path, json.dumps(doc) + "\n")
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _load() -> dict[str, Any]:
    """The marker's state, served and owed included; the caller holds the marker lock. A malformed
    file, or one with another version, is rewritten as BUNDLE and CURRENT with an empty sent, a fresh
    generation and no served or owed record; a served or owed entry without an int pid reads as none,
    and one with an int pid is kept as written, for names() to read."""
    try:
        doc = json.loads(_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _state(None, ())
    except ValueError:
        doc = None
    sent = doc.get("sent") if isinstance(doc, dict) else None
    if (isinstance(sent, dict) and type(doc.get("version")) is int and doc["version"] == 1
            and (type(doc.get("generation")) is int
                 or ("generation" in doc and doc["generation"] is None and doc.get("classes") == []))
            and isinstance(doc.get("classes"), list) and all(c in _CLASSES for c in doc["classes"])
            and "pid" in sent and (sent["pid"] is None or type(sent["pid"]) is int)
            and isinstance(sent.get("playlists"), list)
            and all(isinstance(slug, str) for slug in sent["playlists"])):
        side = {key: doc[key] if isinstance(doc.get(key), dict) and type(doc[key].get("pid")) is int else None
                for key in _SIDE}
        return _state(doc["generation"], (c for c in _CLASSES if c in doc["classes"]),
                      sent["pid"], sent["playlists"], **side)
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
        return _public(_load())


def boot_id() -> str:
    """The text of /proc/sys/kernel/random/boot_id, which names this boot; "" when it cannot be read."""
    try:
        return _BOOT_ID.read_text(encoding="ascii").strip()
    except OSError:
        return ""


def names(record: dict[str, Any] | None, pid: int, start: float | None) -> bool:
    """Whether a served or owed record names the engine whose status pid is `pid` and whose start is
    `start`: the same pid, the boot_id of this boot, and starts within 5 s of each other. A start that is
    unknown (None, from a status without uptime_s) cannot be verified, so it matches no record, and a
    record without "boot" or "start" names no engine."""
    if not isinstance(record, dict) or record.get("pid") != pid or "boot" not in record or "start" not in record:
        return False
    if record["boot"] != boot_id():
        return False
    have = record["start"]
    if start is None or type(have) not in (int, float):
        return False
    return abs(have - start) <= _START_SLACK_S


def served() -> int | None:
    """The pid the served record names, read under the marker lock; None when none is recorded."""
    record = served_record()
    return None if record is None else record["pid"]


def served_record() -> dict[str, Any] | None:
    """The served record as written, read under the marker lock; None when none is recorded."""
    with lock.held("marker"):
        record = _load()["served"]
    return None if record is None else dict(record)


def owed_record() -> dict[str, Any] | None:
    """The owed record as written, read under the marker lock; None when none is recorded."""
    with lock.held("marker"):
        record = _load()["owed"]
    return None if record is None else dict(record)


def record_served(pid: int, start: float | None = None, braked: bool = False, at: float | None = None) -> None:
    """The engine whose status pid is `pid` and whose start is `start` has taken a whole bundle, under
    the brake when `braked`: write served as {"pid", "at", "boot", "start", "braked"} under the marker
    lock, with the time `at` (now when not given) and this boot's boot_id, keeping the generation, the
    classes, sent and owed. A record that already names that engine with the same braked is left as it
    is. With no marker, the record holds served alone."""
    with lock.held("marker"):
        state = _load()
        if names(state["served"], pid, start) and state["served"].get("braked") is braked:
            return
        _write({**state, "served": {"pid": pid, "at": time.time() if at is None else at, "boot": boot_id(),
                                    "start": start, "braked": braked}})


def clear_braked(pid: int, start: float | None) -> None:
    """While the served record names that engine as braked, write it with braked false."""
    with lock.held("marker"):
        state = _load()
        if names(state["served"], pid, start) and state["served"].get("braked") is True:
            _write({**state, "served": {**state["served"], "braked": False}})


def owe(pid: int, start: float | None) -> int:
    """The owed bundle for the engine whose status pid is `pid` and whose start is `start`: BUNDLE and
    CURRENT ensured as ensure() does, and owed written as {"pid", "boot", "start", "current"} for that
    engine, current being the generation when this call added CURRENT, or when the CURRENT an earlier
    owe added is still held at the generation it recorded, else None. Returns the generation."""
    with lock.held("marker"):
        state = _load()
        merged = _merged(state["classes"], _CLASSES)
        earlier = (state["owed"] or {}).get("current")
        ours = "CURRENT" not in state["classes"] or (earlier is not None and earlier == state["generation"])
        if state["classes"] and merged == state["classes"]:
            generation, kept = state["generation"], state
        else:
            generation = _fresh(state)
            kept = _state(generation, merged, served=state["served"], owed=state["owed"])
        _write({**kept, "owed": {"pid": pid, "boot": boot_id(), "start": start,
                                 "current": generation if ours else None}})
        return generation


def drop_owed(pid: int, start: float | None) -> None:
    """After a run to the engine the owed record names that ended refused or uncertain: while the marker
    holds CURRENT at the generation owe recorded for it, CURRENT goes and owed keeps no generation; the
    generation, the other classes and sent are kept."""
    with lock.held("marker"):
        state = _load()
        owed = state["owed"]
        if not names(owed, pid, start) or owed.get("current") is None or owed["current"] != state["generation"] \
                or "CURRENT" not in state["classes"]:
            return
        _write({**state, "classes": [c for c in state["classes"] if c != "CURRENT"],
                "owed": {**owed, "current": None}})


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
        _write(_state(generation, _merged(state["classes"], classes), served=state["served"], owed=state["owed"]))
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
        _write(_state(generation, merged, served=state["served"], owed=state["owed"]))
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
        cleared = _state(generation, (c for c in state["classes"] if c in keep), served=state["served"],
                         owed=state["owed"])
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
            _write(_state(state["generation"], state["classes"], served=state["served"], owed=state["owed"]))


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
                          playlists + ([] if slug in playlists else [slug]), state["served"], state["owed"]))
        return True
