"""A wallpaper's alias: the ALIAS key of its wp/<id>.conf, the name typed after @, and the
rules it keeps. Plain Python over the store; no Qt import."""
from __future__ import annotations

import os
import re
from typing import Any

from . import paths, settings, tier_a

_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")


def claims() -> dict[str, list[str]]:
    """{casefolded alias: the sorted ids whose wp/<id>.conf carries it}, read from the raw lines."""
    out: dict[str, list[str]] = {}
    for conf in paths.wp_dir().glob("*.conf"):
        wid = conf.stem
        if not paths.is_safe_wid(wid):
            continue
        try:
            name = tier_a.parse(conf.read_text(encoding="utf-8")).get("ALIAS", "").strip()
        except (OSError, ValueError):
            continue
        if name:
            out.setdefault(name.casefold(), []).append(wid)
    return {name: sorted(wids) for name, wids in out.items()}


def existing_ids(cfg: dict[str, Any]) -> set[str]:
    """Every existing id: the wp/*.conf stems and the folder names under the library, the
    Workshop root and the manual root that `cfg` names."""
    ids = {conf.stem for conf in paths.wp_dir().glob("*.conf")}
    for root in (cfg.get("WALLPAPERS_DIR") or paths.default_wallpapers_dir(),
                 cfg.get("WORKSHOP_DIR") or paths.detect_workshop_dir(), paths.manual_dir()):
        try:
            with os.scandir(root) as entries:
                ids.update(entry.name for entry in entries if entry.is_dir())
        except OSError:
            continue
    return ids


def check(name: str, wid: str, *, ids: set[str] | None = None,
          taken: dict[str, list[str]] | None = None) -> str | None:
    """None when `name` may be `wid`'s alias, else the reason. `ids` and `taken` default to
    existing_ids() and claims() of the store as it stands; a backup import passes the ones
    the import would leave."""
    if not _NAME.fullmatch(name or ""):
        return "an alias is 1 to 64 letters, digits, dots, dashes or underscores, with no spaces"
    if name.isdigit():
        return "an alias cannot be all digits"
    if name in (existing_ids(settings.load()) if ids is None else ids):
        return f"{name} is already a wallpaper id"
    others = sorted(w for w in (claims() if taken is None else taken).get(name.casefold(), ())
                    if w != wid)
    if others:
        return f"@{name} is taken by {others[0]}"
    return None


def index() -> dict[str, str]:
    """{casefolded alias: id} for every alias that exactly one wp/<id>.conf carries."""
    return {name: next(iter(wids)) for name, wids in claims().items() if len(wids) == 1}
