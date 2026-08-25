"""Named playlists (playlists/<slug>.conf, Tier A).

A playlist = {name, membership set, mode, interval(+unit)}. Files are shell-sourceable
KEY=value. The active playlist is the settings.conf ACTIVE_PLAYLIST slug; the rotation
push resolves the playlist directly (MODE / INTERVAL / MEMBERS), and ROTATION_ENABLED
stays the user's own pause switch - nothing here writes it. The legacy single-playlist
keys (ORDER / INTERVAL) are read only by ensure_default's one-time first-run migration.

Membership is ORTHOGONAL to tags.csv good/bad curation: the rotation set is
MEMBERS intersect good-pool, so a tombstoned wallpaper can never rotate even if a stale playlist
still lists it. Slugs are filesystem-safe, stable across renames (NAME is display-only).
"""
from __future__ import annotations

import re
import time
import warnings
from typing import Any

from .. import constants as C
from . import atomic, paths, settings, tier_a

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Display name -> filesystem-safe slug ('' never returned)."""
    s = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return s or "playlist"


def _unique_slug(name: str) -> str:
    base = slugify(name)
    slug, n = base, 2
    while paths.playlist_file(slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


# --- coercion / validation (mirrors settings.py semantics, PLAYLIST_SCHEMA-keyed) ------
def _coerce(key: str, raw: Any, spec: dict) -> Any:
    if spec["type"] == "int":
        try:
            return int(str(raw).strip())
        except (ValueError, TypeError):
            return spec["default"]
    return str(raw)


def _validate(d: dict[str, Any]) -> dict[str, Any]:
    """Clamp/snap to schema. Warn, never raise. Normalizes MEMBERS (dedupe, single spaces)."""
    out: dict[str, Any] = {}
    for key, spec in C.PLAYLIST_SCHEMA.items():
        val = d.get(key, spec["default"])
        if spec["type"] == "int":
            try:
                val = int(val)
            except (ValueError, TypeError):
                warnings.warn(f"playlist: {key}={val!r} not an int; using default")
                val = spec["default"]
            lo, hi = spec.get("min"), spec.get("max")
            if lo is not None and val < lo:
                val = lo
            if hi is not None and val > hi:
                val = hi
        elif spec["type"] == "enum":
            if val not in spec.get("choices", ()):
                warnings.warn(f"playlist: {key}={val!r} not in {spec.get('choices')}; using default")
                val = spec["default"]
        elif key == "NAME":
            val = " ".join(str(val).split())  # collapse whitespace; tier_a rejects newlines
        elif key == "MEMBERS":
            seen: dict[str, None] = {}
            for tok in str(val).split():
                seen.setdefault(tok, None)
            val = " ".join(seen)
        else:
            val = str(val)
        out[key] = val
    return out


def load(slug: str) -> dict[str, Any]:
    """Read a playlist conf, schema-coerced; missing/garbage keys fall to defaults."""
    text = ""
    p = paths.playlist_file(slug)
    if p.exists():
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            text = ""
    raw = tier_a.parse(text)
    return _validate({k: _coerce(k, raw[k], s) if k in raw else s["default"]
                      for k, s in C.PLAYLIST_SCHEMA.items()})


def save(slug: str, d: dict[str, Any]) -> None:
    valid = _validate(d)
    flat = {k: str(valid[k]) for k in C.PLAYLIST_SCHEMA}
    text = tier_a.serialize(flat, header="lwe playlist (Tier A) - managed by LWE Control Panel")
    paths.ensure_dirs()
    atomic.atomic_write_text(paths.playlist_file(slug), text)


def list_playlists() -> list[dict[str, Any]]:
    """All playlists as dicts (with 'slug'), sorted by display name."""
    out = []
    d = paths.playlists_dir()
    if d.is_dir():
        for p in sorted(d.glob("*.conf")):
            row = load(p.stem)
            row["slug"] = p.stem
            out.append(row)
    out.sort(key=lambda r: (r.get("NAME") or r["slug"]).lower())
    return out


def create(name: str, members: list[str] | None = None, mode: str = "shuffle",
           interval: int = 900, unit: str = "min") -> str:
    slug = _unique_slug(name)
    save(slug, {"NAME": name.strip() or slug, "MODE": mode, "INTERVAL": interval,
                "UNIT": unit, "MEMBERS": " ".join(members or [])})
    return slug


def rename(slug: str, new_name: str) -> None:
    """Display-name change only - the slug (and every pointer to it) stays stable."""
    d = load(slug)
    d["NAME"] = new_name.strip() or slug
    save(slug, d)


def delete(slug: str) -> None:
    """Tombstone to legacy/playlists/ (recoverable). Reassigns the active pointer."""
    src = paths.playlist_file(slug)
    if src.exists():
        dst_dir = paths.legacy_playlists_dir()
        dst_dir.mkdir(parents=True, exist_ok=True)
        src.replace(dst_dir / f"{slug}.conf.{time.strftime('%Y%m%d-%H%M%S')}")
    if active_slug(validate=False) == slug:
        remaining = list_playlists()
        set_active(remaining[0]["slug"] if remaining else "")


def members(slug: str) -> list[str]:
    return load(slug)["MEMBERS"].split()


def toggle_member(slug: str, wid: str) -> bool:
    """Add/remove `wid` from the playlist. Returns True if it is a member AFTER the call."""
    d = load(slug)
    ids = d["MEMBERS"].split()
    if wid in ids:
        ids.remove(wid)  # removal always allowed, even for a legacy-unsafe id already stored
        now = False
    elif paths.is_safe_wid(wid):
        ids.append(wid)
        now = True
    else:
        return False  # never ADD an id that would split or glob in the shell MEMBERS list
    d["MEMBERS"] = " ".join(ids)
    save(slug, d)
    return now


def active_slug(validate: bool = True) -> str:
    slug = str(settings.load().get("ACTIVE_PLAYLIST") or "")
    if validate and slug and not paths.playlist_file(slug).exists():
        return ""
    return slug


def set_active(slug: str) -> None:
    s = settings.load()
    s["ACTIVE_PLAYLIST"] = slug
    settings.save(s)


def ensure_default() -> str:
    """First-run/migration: no playlists yet -> build one from the legacy single-playlist
    state (good pool + ORDER/INTERVAL; stored 'weighted' arrives snapped to 'shuffle' by
    the settings enum). Idempotent; returns the active slug either way."""
    existing = list_playlists()
    if existing:
        slug = active_slug()
        if not slug:
            slug = existing[0]["slug"]
            set_active(slug)  # persist the pointer
        return slug
    from . import tags  # local import: keep module import cost flat
    s = settings.load()
    mode = s.get("ORDER") if s.get("ORDER") in C.PLAYLIST_MODES else "shuffle"
    if not s.get("ROTATION_ENABLED", True):
        mode = "static"
    slug = create(C.DEFAULT_PLAYLIST_NAME, members=sorted(tags.good_ids()),
                  mode=mode, interval=int(s.get("INTERVAL") or 900), unit="min")
    set_active(slug)
    return slug
