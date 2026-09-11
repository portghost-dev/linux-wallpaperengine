"""Which store owns which file under the config dir.

Every file the panel writes under paths.config_dir() is either carried by a Store here or
named in NOT_BACKED_UP with the reason it stays behind; test_store_ownership runs the
writers in a sandbox and fails on a file neither table claims, so a store added later
cannot be forgotten by the backup. Order is the order the backup walks: settings first,
since every other store's plan is decided against the settings the import would leave, and
foreign last, because it writes what the stores before it chose to preserve.
"""
from __future__ import annotations

import fnmatch

from . import discover_cfg, foreign, meta, playlists, rules, settings, tags, themes, wp
from .store import Store

STORES: tuple[Store, ...] = (
    settings.BACKUP,
    themes.BACKUP,
    discover_cfg.BACKUP,
    playlists.BACKUP,
    wp.BACKUP,
    tags.BACKUP,
    meta.BACKUP,
    rules.BACKUP,
    foreign.BACKUP,
)

#: files under config_dir() that deliberately do not travel, each with its reason
NOT_BACKED_UP: dict[str, str] = {
    "engine-env": "generated from settings at every panel start, for this machine's outputs",
    "legacy/playlists/*": "deleted playlists, recoverable on the machine they were deleted on",
}


def matches(rel: str, pattern: str) -> bool:
    """Glob match segment by segment: a * never crosses a /, so wp/*.conf claims only the
    files the override store's own glob would export."""
    a, b = rel.split("/"), pattern.split("/")
    return len(a) == len(b) and all(fnmatch.fnmatchcase(x, y) for x, y in zip(a, b))


def claims(rel: str) -> list[str]:
    """Who claims this config-relative path: store names, plus NOT_BACKED_UP patterns as
    'not backed up: <pattern>'. Exactly one claim is the law the ownership test asserts."""
    out = [s.name for s in STORES if any(matches(rel, p) for p in s.owns)]
    out += [f"not backed up: {p}" for p in NOT_BACKED_UP if matches(rel, p)]
    return out
