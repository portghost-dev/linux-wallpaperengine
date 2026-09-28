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
import zipfile
from typing import Any, Callable

from .. import constants as C
from . import atomic, foreign, lock, migrate, paths, settings, tier_a
from .store import Store

_SLUG_RE = re.compile(r"[^a-z0-9]+")


SLUG_MAX = 60  # the engine's id limit is 64; _unique_slug may append "-NN"


def slugify(name: str) -> str:
    """Display name -> filesystem-safe slug ('' never returned), at most SLUG_MAX chars."""
    s = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")[:SLUG_MAX].rstrip("-")
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
            val = C.VALUE_ALIASES.get("playlists", {}).get(key, {}).get(str(val), val)
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
    raw, _ = migrate.apply_tables("playlists", tier_a.parse(text))
    return _validate({k: _coerce(k, raw[k], s) if k in raw else s["default"]
                      for k, s in C.PLAYLIST_SCHEMA.items()})


def save(slug: str, d: dict[str, Any]) -> None:
    valid = _validate(d)
    flat = {k: str(valid[k]) for k in C.PLAYLIST_SCHEMA}
    text = tier_a.serialize(flat, header="lwe playlist (Tier A) - managed by LWE Control Panel")
    paths.ensure_dirs()
    with lock.held("playlists"):
        atomic.atomic_write_text(paths.playlist_file(slug), text)


def modify(slug: str, fn: Callable[[dict[str, Any]], dict[str, Any] | None]) -> dict[str, Any]:
    """Under the playlists lock: load the playlist fresh, pass it to fn, and apply the keys fn
    returns to the file's own lines: a value sets its key, None deletes it and every old name
    of it, every other line stays. A playlist with no file is written whole, without the None
    changes. Returns those keys; the file is written only when its text changes."""
    with lock.held("playlists"):
        p = paths.playlist_file(slug)
        current = load(slug)
        changes = fn(dict(current)) or {}
        if changes and not p.exists():
            save(slug, {**current, **{k: v for k, v in changes.items() if v is not None}})
        elif changes:
            text = p.read_bytes().decode("utf-8")
            sets = {k: v for k, v in changes.items() if v is not None}
            valid = _validate({**current, **sets})
            flat: dict[str, str | None] = {k: str(valid[k]) for k in sets if k in C.PLAYLIST_SCHEMA}
            flat.update({k: None for k, v in changes.items() if v is None and k in C.PLAYLIST_SCHEMA})
            new = tier_a.edit(text, migrate.with_old_names("playlists", flat), path=p)
            if new != text:
                atomic.atomic_write_text(p, new)
        return changes


def update(slug: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Set these keys of one playlist under the playlists lock, over a fresh load."""
    return modify(slug, lambda _current: dict(changes))


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
    with lock.held("playlists"):
        slug = _unique_slug(name)
        save(slug, {"NAME": name.strip() or slug, "MODE": mode, "INTERVAL": interval,
                    "UNIT": unit, "MEMBERS": " ".join(members or [])})
    return slug


def rename(slug: str, new_name: str) -> None:
    """Display-name change only - the slug (and every pointer to it) stays stable."""
    update(slug, {"NAME": new_name.strip() or slug})


def delete(slug: str) -> None:
    """Tombstone to legacy/playlists/ (recoverable). Reassigns the active pointer."""
    with lock.held("playlists"):
        src = paths.playlist_file(slug)
        if src.exists():
            dst_dir = paths.legacy_playlists_dir()
            dst_dir.mkdir(parents=True, exist_ok=True)
            src.replace(dst_dir / f"{slug}.conf.{time.strftime('%Y%m%d-%H%M%S')}")

    def repoint(cfg: dict[str, Any]) -> dict[str, Any]:
        if str(cfg.get("ACTIVE_PLAYLIST") or "") != slug:
            return {}
        remaining = list_playlists()
        return {"ACTIVE_PLAYLIST": remaining[0]["slug"] if remaining else ""}
    settings.modify(repoint)


def members(slug: str) -> list[str]:
    return load(slug)["MEMBERS"].split()


def toggle_member(slug: str, wid: str) -> bool:
    """Add/remove `wid` from the playlist. Returns True if it is a member AFTER the call."""
    def toggle(d: dict[str, Any]) -> dict[str, Any]:
        ids = d["MEMBERS"].split()
        if wid in ids:
            ids.remove(wid)  # removal always allowed, even for a legacy-unsafe id already stored
        elif paths.is_safe_wid(wid):
            ids.append(wid)
        else:
            return {}  # never ADD an id that would split or glob in the shell MEMBERS list
        return {"MEMBERS": " ".join(ids)}
    return wid in modify(slug, toggle).get("MEMBERS", "").split()


def reorder(slug: str, ids: list[str]) -> list[str]:
    """Store `ids` as the playlist's order. Members left out keep their old relative order
    at the end; ids that are not members are ignored. Ordering never adds or removes."""
    def order(d: dict[str, Any]) -> dict[str, Any]:
        current = d["MEMBERS"].split()
        wanted = [w for w in dict.fromkeys(ids) if w in current]
        rest = [w for w in current if w not in wanted]
        return {"MEMBERS": " ".join(wanted + rest)}
    return modify(slug, order)["MEMBERS"].split()


def insert_member(slug: str, wid: str, index: int) -> int:
    """Put `wid` at `index` (clamped), adding it when absent and moving it when present, so
    a playlist never holds the same id twice. Returns the index it landed at, -1 for an id
    that cannot be stored."""
    if not paths.is_safe_wid(wid):
        return -1

    def insert(d: dict[str, Any]) -> dict[str, Any]:
        ids = d["MEMBERS"].split()
        if wid in ids:
            ids.remove(wid)
        ids.insert(max(0, min(int(index), len(ids))), wid)
        return {"MEMBERS": " ".join(ids)}
    return modify(slug, insert)["MEMBERS"].split().index(wid)


def active_slug(validate: bool = True) -> str:
    slug = str(settings.load().get("ACTIVE_PLAYLIST") or "")
    if validate and slug and not paths.playlist_file(slug).exists():
        return ""
    return slug


def set_active(slug: str) -> None:
    settings.update({"ACTIVE_PLAYLIST": slug})


def ensure_default() -> str:
    """First-run/migration: no playlists yet -> build one from the legacy single-playlist
    state (good pool + ORDER/INTERVAL; stored 'weighted' arrives snapped to 'shuffle' by
    the settings enum). Idempotent; returns the active slug either way."""
    from . import tags  # local import: keep module import cost flat
    with lock.held("playlists"):
        existing = list_playlists()
        if existing:
            slug = existing[0]["slug"]
        else:
            s = settings.load()
            mode = s.get("ORDER") if s.get("ORDER") in C.PLAYLIST_MODES else "shuffle"
            if not s.get("ROTATION_ENABLED", True):
                mode = "static"
            slug = create(C.DEFAULT_PLAYLIST_NAME, members=sorted(tags.good_ids()),
                          mode=mode, interval=int(s.get("INTERVAL") or 900), unit="min")

    def adopt(cfg: dict[str, Any]) -> dict[str, Any]:
        current = str(cfg.get("ACTIVE_PLAYLIST") or "")
        return {} if current and paths.playlist_file(current).exists() else {"ACTIVE_PLAYLIST": slug}
    settings.modify(adopt)
    return active_slug() or slug


# --- backup ---------------------------------------------------------------------------
PREFIX = "playlists/"


def _backup_export(z: zipfile.ZipFile, r: dict[str, Any]) -> None:
    n = 0
    kept_aside = foreign.load()
    for pl in list_playlists():
        slug = str(pl.get("slug") or "")
        if not slug:
            continue
        d = load(slug)
        flat = {k: str(d[k]) for k in C.PLAYLIST_SCHEMA}
        for key, val in foreign.extras("playlists", slug, kept_aside).items():
            if key in flat:
                continue
            if tier_a.is_valid_key(key):
                flat[key] = str(val)
                foreign.emitted(r, 1)
            else:
                r["dropped"].append({"kind": "preserved-key", "id": f"{slug}:{key}", "reason": "not a key this format can hold"})
        z.writestr(f"{PREFIX}{slug}.conf", tier_a.serialize(flat, header="lwe playlist"))
        n += 1
    r["counts"]["playlists"] = n


def _backup_preflight(z: zipfile.ZipFile, r: dict[str, Any], plan: dict[str, Any],
                      cfg_after: dict[str, Any]) -> bool:
    out: dict[str, Any] = {}
    for n in sorted(z.namelist()):
        if not (n.startswith(PREFIX) and n.endswith(".conf")):
            continue
        slug = n[len(PREFIX):-5]
        if not slug or slugify(slug) != slug:
            r["dropped"].append({"kind": "playlist", "id": slug, "reason": "bad name"})
            continue
        raw, actions = migrate.apply_tables("playlists", tier_a.parse(z.read(n).decode("utf-8", "replace")))
        migrate.report("playlists", actions, r, slug, "playlist-key", f"{slug}:")
        kept: dict[str, Any] = {}
        for key, spec in C.PLAYLIST_SCHEMA.items():
            if key not in raw:
                continue
            verdict, val, reason = migrate.coerce(spec, raw[key], dense=True)
            if verdict in ("clamp", "snap"):
                kept[key] = val
                r["adjusted"].append({"kind": verdict, "store": "playlists", "id": slug,
                                      "key": key, "from": raw[key], "to": val})
            elif verdict == "drop":
                r["dropped"].append({"kind": "playlist-key", "id": f"{slug}:{key}", "reason": reason})
            else:
                kept[key] = _coerce(key, raw[key], spec)
        for key, val in raw.items():
            if key in C.PLAYLIST_SCHEMA:
                continue
            foreign.record(plan, "playlists", slug, key, val, r)
        d = _validate(kept)
        missing = [m for m in str(d["MEMBERS"]).split() if not paths.wallpaper_present(m, cfg_after)]
        if missing:
            r["held"].append({"kind": "playlist-members", "id": slug, "count": len(missing)})
        out[slug] = d
    plan["playlists"] = out
    r["counts"]["playlists"] = len(out)
    return True


def _backup_apply(plan: dict[str, Any], r: dict[str, Any]) -> bool:
    for slug, d in (plan.get("playlists") or {}).items():
        try:
            save(slug, d)
        except Exception as exc:
            r["errors"].append({"file": f"{PREFIX}{slug}.conf", "reason": str(exc)})
    return True


BACKUP = Store("playlists", (f"{PREFIX}*.conf",), _backup_export, _backup_preflight, _backup_apply)
