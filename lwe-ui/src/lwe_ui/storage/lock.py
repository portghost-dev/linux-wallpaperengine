"""Cross-process store locks: one kernel flock per store, on a sidecar file under
<config_dir>/locks. Writers that go through these functions, in the window, the tray or a
command, never interleave a store's read, change and write.

A lock file is opened "a+" and never truncated, replaced or deleted, since a writer that
locked a replaced file would hold a lock no other writer sees. The kernel releases a lock
when its holder exits, however it exits. A thread that holds a store takes it again without
blocking; other threads and processes wait up to LOCK_WAIT_S, then get StoreBusy. Stores are
locked in rank order: taking a store while holding one ranked after it raises RuntimeError.
"""
from __future__ import annotations

import fcntl
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from . import paths

_ORDER = ("foreign", "settings", "theme", "discovery", "playlists", "overrides", "tags", "meta",
          "rules", "records", "env", "sync", "marker")
LOCK_WAIT_S = 2.0
_POLL_S = 0.001

_local = threading.local()


class StoreBusy(OSError):
    """Another writer held the store's lock past LOCK_WAIT_S; nothing was written."""


def _holding() -> set[str]:
    stores = getattr(_local, "stores", None)
    if stores is None:
        stores = _local.stores = set()
    return stores


@contextmanager
def held(store: str, wait_s: float | None = None) -> Iterator[None]:
    """Hold `store`'s lock for the body of the with statement, waiting up to `wait_s`
    seconds (LOCK_WAIT_S when not given; 0 tries once)."""
    rank = _ORDER.index(store)
    holding = _holding()
    if store in holding:
        yield
        return
    above = [s for s in holding if _ORDER.index(s) > rank]
    if above:
        raise RuntimeError(f"lock order: {store} cannot be taken while {above[0]} is held")
    path = paths.locks_dir() / f"{store}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as f:
        deadline = time.monotonic() + (LOCK_WAIT_S if wait_s is None else wait_s)
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise StoreBusy(f"Store busy: another writer holds {path}") from None
                time.sleep(_POLL_S)
        holding.add(store)
        try:
            yield
        finally:
            holding.discard(store)
            fcntl.flock(f, fcntl.LOCK_UN)
