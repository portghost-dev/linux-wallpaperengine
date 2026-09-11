"""Typed load/save for wp/<id>.conf (Tier A, shell-sourceable).

Each conf is a per-wallpaper override set. Dynamic PROP_<name> keys are collected into a
`props: dict[str,str]` on load and expanded back on save. Optional/empty keys are omitted
on save so the file stays minimal and consumers can distinguish "unset" from "set empty".
"""
from __future__ import annotations

import os
import warnings
import zipfile
from pathlib import Path
from typing import Any

from .. import constants as C
from . import atomic, foreign, migrate, paths, tier_a
from .store import Store

# Keys whose empty value means "unset / inherit" and must NOT be written.
_OMIT_IF_EMPTY = ("FPS", "CLAMPING", "FULLSCREEN_PAUSE", "SKIP", "CC_MODE")


def _read_raw(path) -> str:
    """The conf text, or "" when there is no file. A file that exists but cannot be read
    raises, so no caller mistakes an unreadable conf for an empty one."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _coerce(spec: dict, raw: str) -> Any:
    t = spec["type"]
    if t == "bool":
        return str(raw).strip().lower() in ("true", "1", "yes", "on")
    if t == "bool_or_empty":
        s = str(raw).strip()
        if s == "":
            return ""
        return s.lower() in ("true", "1", "yes", "on")
    if t == "int":
        try:
            return int(str(raw).strip())
        except (ValueError, TypeError):
            return spec["default"]
    if t == "int_or_empty":
        s = str(raw).strip()
        if s == "":
            return ""
        try:
            return int(s)
        except (ValueError, TypeError):
            return ""
    if t == "float":
        try:
            return float(str(raw).strip())
        except (ValueError, TypeError):
            return spec["default"]
    # enum / enum_or_empty / str all carry through as strings.
    return str(raw)


def load_path(path) -> dict[str, Any]:
    """Read a Tier A wp-schema conf at `path` into a typed dict + props.

    Missing file or unreadable -> schema defaults. This is the path-based core; load(wid) is a
    thin wrapper over it, so the serialization is defined in exactly one place and any other
    conf path reuses it verbatim. An unreadable file reads as defaults here; the editing
    readers below raise instead.
    """
    try:
        text = _read_raw(path)
    except OSError:
        text = ""
    return load_text(text)


def load_text(text: str) -> dict[str, Any]:
    """The conf TEXT into a typed dict + props: the one deserialization every reader uses."""
    raw, _ = migrate.apply_tables("overrides", tier_a.parse(text))
    out: dict[str, Any] = {}
    for key, spec in C.WP_SCHEMA.items():
        out[key] = _coerce(spec, raw[key]) if key in raw else spec["default"]
    props: dict[str, str] = {}
    for key, val in raw.items():
        if key.startswith(C.WP_PROP_PREFIX):
            name = key[len(C.WP_PROP_PREFIX):]
            if name:
                props[name] = val
    out["props"] = props
    return out


def load(wid: str) -> dict[str, Any]:
    """Read wp/<wid>.conf into a typed dict + props. Missing keys take schema defaults."""
    return load_path(paths.wp_file(wid))


def load_set_path(path) -> dict[str, Any]:
    """Presence-aware read: ONLY the schema keys the file actually carries, typed, + props.

    load() materialises every schema key, which makes "explicitly set to the default" and
    "inheriting" the same dict - so VOLUME=0, AUDIO_REACTIVE=false, MOUSE=false and
    SCALING=default cannot be expressed as overrides. This reader answers the other
    question: which keys are PRESENT. Key presence IS set-ness.

    load() is untouched and remains the reader for every resolve/launch path; this is a
    second view over the same file for surfaces that must tell set from inherited.
    Raises when the file exists but cannot be read.
    """
    raw, _ = migrate.apply_tables("overrides", tier_a.parse(_read_raw(path)))
    out: dict[str, Any] = {}
    for key, spec in C.WP_SCHEMA.items():
        if key in raw:
            out[key] = _coerce(spec, raw[key])
    props: dict[str, str] = {}
    for key, val in raw.items():
        if key.startswith(C.WP_PROP_PREFIX):
            name = key[len(C.WP_PROP_PREFIX):]
            if name:
                props[name] = val
    out["props"] = props
    return out


def load_set(wid: str) -> dict[str, Any]:
    """Presence-aware read of wp/<wid>.conf. Only keys the file carries; props included."""
    return load_set_path(paths.wp_file(wid))


def update_set_path(path, changes: dict[str, Any]) -> None:
    """Presence-preserving key edit at `path`: a value writes/overwrites, None DELETES the key.

    The counterpart to load_set. save() rewrites the whole file from a materialised dict,
    so it cannot express "this key is absent" for a non-optional schema key; this reads
    the raw file, applies exactly the named changes, and writes the rest back byte-for-byte
    (same Tier A serialization). Keys not named are never touched - BG and TYPE in
    particular survive every edit, since they are identity, not override.

    Path-based so the same presence-preserving edit can be aimed at any conf on the same
    Tier A schema, not only the one wp/<id>.conf that load()/save() resolve by id.

    A PROP_ key that is not a shell identifier is refused rather than raising, matching
    save_path's discipline: one bad property name must not lose the whole file.

    Raises when the file exists but cannot be read: a rewrite from nothing plus the
    changes would drop every other key, so the edit must fail instead.
    """
    flat = tier_a.parse(_read_raw(path))
    for key, val in changes.items():
        if not tier_a.is_valid_key(key):
            warnings.warn(f"wp: {key!r} is not a shell identifier; skipping it")
            continue
        if val is None:
            flat.pop(key, None)
            continue
        sval = _bool_str(val) if isinstance(val, bool) else str(val)
        if "\n" in sval or "\r" in sval:
            warnings.warn(f"wp: value for {key!r} has a newline; skipping it")
            continue
        flat[key] = sval
    stem = Path(path).stem
    atomic.atomic_write_text(
        path, tier_a.serialize(flat, header=f"lwe wallpaper override {stem} (Tier A)"))


#: keys that are the wallpaper's identity, never inherited, always written
IDENTITY_KEYS = ("BG", "TYPE")


def facts_to_keys(d: dict[str, Any]) -> dict[str, str]:
    """The flat keys an importer writes for a wallpaper: its identity, the facts it declares
    (a colour grade that is not identity, audio support when declared, preset properties),
    and nothing else. A default is never written; an absent key inherits the global."""
    flat: dict[str, str] = {}
    for key in IDENTITY_KEYS:
        if d.get(key) not in (None, ""):
            flat[key] = str(d[key])
    for key, spec in C.WP_SCHEMA.items():
        if key in IDENTITY_KEYS or key not in d:
            continue
        val = d[key]
        if _coerce(spec, str(val)) == spec["default"] or (spec["type"] == "bool" and not val):
            continue
        flat[key] = _bool_str(val) if spec["type"] == "bool" else str(val)
    for name, pval in (d.get("props") or {}).items():
        key = f"{C.WP_PROP_PREFIX}{name}"
        if pval is None or str(pval) == "" or not tier_a.is_valid_key(key):
            continue
        flat[key] = str(pval)
    return flat


def write_keys(wid: str, flat: dict[str, str]) -> None:
    """Write wp/<wid>.conf as exactly these keys: the sparse whole-file writer."""
    paths.ensure_dirs()
    atomic.atomic_write_text(paths.wp_file(wid),
                             tier_a.serialize(flat, header=f"lwe wallpaper override {wid} (Tier A)"))


def sparsify_overrides() -> dict[str, list[str]]:
    """One-time clean-up: drop every schema key whose value equals the current default
    (identity keys and PROP_ keys kept), so a materialised default stops reading as a pin.
    Returns {wid: [removed keys]} for the files that changed."""
    report: dict[str, list[str]] = {}
    for conf in sorted(paths.wp_dir().glob("*.conf")):
        wid = conf.stem
        if not paths.is_safe_wid(wid):
            continue
        try:
            raw = tier_a.parse(conf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        removed = [k for k, v in raw.items()
                   if k in C.WP_SCHEMA and k not in IDENTITY_KEYS
                   and _coerce(C.WP_SCHEMA[k], v) == C.WP_SCHEMA[k]["default"]]
        if not removed:
            continue
        kept = {k: v for k, v in raw.items() if k not in removed}
        try:
            atomic.atomic_write_text(conf, tier_a.serialize(kept, header=f"lwe wallpaper override {wid} (Tier A)"))
        except OSError:
            continue
        report[wid] = removed
    return report


def update_set(wid: str, changes: dict[str, Any]) -> None:
    """Presence-preserving key edit of wp/<wid>.conf. See update_set_path."""
    update_set_path(paths.wp_file(wid), changes)


def _bool_str(val: Any) -> str:
    return "true" if val else "false"


def save_path(path, d: dict[str, Any]) -> None:
    """Inverse of load_path: serialize and write atomically. Path-based core behind save(wid),
    so every writer of a wp-schema conf shares one serialization."""
    from pathlib import Path

    atomic.atomic_write_text(path, serialize(d, Path(path).stem))


def serialize(d: dict[str, Any], wid: str = "") -> str:
    """The conf TEXT for a typed dict + props: expand props to PROP_<name>, omit empty
    optionals. The one serialization every writer uses."""
    flat: dict[str, str] = {}
    for key, spec in C.WP_SCHEMA.items():
        if key not in d:
            if key in _OMIT_IF_EMPTY:
                continue
            val = spec["default"]
        else:
            val = d[key]
        t = spec["type"]
        if t == "bool":
            flat[key] = _bool_str(val)
            continue
        if t == "bool_or_empty":
            if val == "" or val is None:
                continue  # inherit -> omit
            flat[key] = _bool_str(val)
            continue
        sval = "" if val is None else str(val)
        if key in _OMIT_IF_EMPTY and sval == "":
            continue
        flat[key] = sval

    props = d.get("props") or {}
    for name, pval in props.items():
        if pval is None:
            continue
        sval = str(pval)
        if sval == "":
            continue
        key = f"{C.WP_PROP_PREFIX}{name}"
        if not tier_a.is_valid_key(key):
            # a prop name that is not a shell identifier (dot, dash, non-ASCII) can't be a
            # PROP_<name> key. Skip just this one and warn - do not let serialize() raise and
            # take the whole override file down with it.
            warnings.warn(f"wp: property name {name!r} is not a shell identifier; skipping it")
            continue
        if "\n" in sval or "\r" in sval:
            # Tier A is line-based; a newline in a value would make serialize() raise and lose the
            # whole write. Not reachable through the single-line GUI, but skip it defensively.
            warnings.warn(f"wp: property {name!r} value has a newline; skipping it")
            continue
        flat[key] = sval

    return tier_a.serialize(flat, header=f"lwe wallpaper override {wid} (Tier A)")


def save(wid: str, d: dict[str, Any]) -> None:
    """Inverse of load: expand props to PROP_<name>, omit empty optionals, write atomically."""
    save_path(paths.wp_file(wid), d)


def exists(wid: str) -> bool:
    """Does wp/<wid>.conf exist on disk? A FILE probe, and only ever a file probe.

    NEVER a library-membership test. Membership is models.library_ids() -
    the WALLPAPERS_DIR scan unioned with the good/review tag states - and conf existence
    is not a term in it. The editor writes wp/<id>.conf for a pending item at approval
    time, so treating this as membership would put un-approved items in the grid.
    """
    return paths.wp_file(wid).exists()


# --- backup ---------------------------------------------------------------------------
PREFIX = "wp/"


def _portable_bg(wid: str, bg: str, cfg: dict[str, Any]) -> str:
    """A folder inside the library this machine's settings name is this library's own copy
    and travels as the bare id, which the app resolves against the library folder; a
    referenced folder anywhere else travels as it is."""
    root = str(cfg.get("WALLPAPERS_DIR") or "")
    if bg and root and os.path.isabs(bg) and os.path.realpath(bg) == os.path.realpath(os.path.join(root, wid)):
        return wid
    return bg


def _backup_text(wid: str, text: str, cfg: dict[str, Any], r: dict[str, Any] | None = None,
                 kept_aside: dict | None = None) -> str:
    """An override as it travels: the keys the file carries and nothing more, so set-ness
    (a present key overrides, an absent key inherits) survives the round trip, plus the keys
    an earlier import preserved for it."""
    raw = tier_a.parse(text)
    kept = {k: v for k, v in raw.items()
            if k in C.WP_SCHEMA or (k.startswith(C.WP_PROP_PREFIX) and len(k) > len(C.WP_PROP_PREFIX))}
    if "BG" in kept:
        kept["BG"] = _portable_bg(wid, kept["BG"], cfg)
    for key, val in foreign.extras("overrides", wid, kept_aside).items():
        if key in kept:
            continue
        if tier_a.is_valid_key(key):
            kept[key] = str(val)
            if r is not None:
                foreign.emitted(r, 1)
        elif r is not None:
            r["dropped"].append({"kind": "preserved-key", "id": f"{wid}:{key}", "reason": "not a key this format can hold"})
    return tier_a.serialize(kept, header=f"lwe wallpaper override {wid} (Tier A)")


def _backup_export(z: zipfile.ZipFile, r: dict[str, Any]) -> None:
    from . import settings
    cfg = settings.load()
    kept_aside = foreign.load()
    n = 0
    for conf in sorted(paths.wp_dir().glob("*.conf")):
        wid = conf.stem
        if not paths.is_safe_wid(wid):
            continue
        try:
            z.writestr(f"{PREFIX}{wid}.conf", _backup_text(wid, conf.read_text(encoding="utf-8"), cfg, r, kept_aside))
        except (OSError, ValueError) as exc:
            r["errors"].append({"file": conf.name, "reason": str(exc)})
            continue
        n += 1
    r["counts"]["overrides"] = n


def _backup_preflight(z: zipfile.ZipFile, r: dict[str, Any], plan: dict[str, Any],
                      cfg_after: dict[str, Any]) -> bool:
    out: dict[str, Any] = {}
    held = 0
    for n in sorted(z.namelist()):
        if not (n.startswith(PREFIX) and n.endswith(".conf")):
            continue
        wid = n[len(PREFIX):-5]
        if not paths.is_safe_wid(wid):
            r["dropped"].append({"kind": "override", "id": wid, "reason": "bad id"})
            continue
        raw, actions = migrate.apply_tables("overrides", tier_a.parse(z.read(n).decode("utf-8", "replace")))
        migrate.report("overrides", actions, r, wid, "override-key", f"{wid}:")
        kept: dict[str, str] = {}
        for k, v in raw.items():
            if k in C.WP_SCHEMA:
                verdict, val, reason = migrate.coerce(C.WP_SCHEMA[k], v, dense=False)
                if verdict == "clamp":
                    kept[k] = str(val)
                    r["adjusted"].append({"kind": "clamp", "store": "overrides", "id": wid,
                                          "key": k, "from": v, "to": val})
                elif verdict == "preserve":
                    foreign.record(plan, "overrides", wid, k, v, r)
                elif verdict == "drop":
                    r["dropped"].append({"kind": "override-key", "id": f"{wid}:{k}", "reason": reason})
                else:
                    kept[k] = str(val)
            elif k.startswith(C.WP_PROP_PREFIX) and tier_a.is_valid_key(k):
                kept[k] = str(v)
            else:
                foreign.record(plan, "overrides", wid, k, v, r)
        bg = kept.get("BG", "")
        if bg and os.path.isabs(bg) and not os.path.isdir(bg):
            r["reresolved"].append({"key": f"override {wid} folder", "from": bg, "to": wid})
            kept["BG"] = wid
        if not kept.get("BG"):
            kept["BG"] = wid
        if not paths.wallpaper_present(wid, cfg_after):
            r["held"].append({"kind": "override", "id": wid})
            held += 1
        out[wid] = kept
    plan["overrides"] = out
    r["counts"]["overrides"] = len(out)
    r["counts"]["overrides_held"] = held
    return True


def _backup_apply(plan: dict[str, Any], r: dict[str, Any]) -> bool:
    for wid, kept in (plan.get("overrides") or {}).items():
        try:
            write_keys(wid, kept)
        except Exception as exc:
            r["errors"].append({"file": f"{PREFIX}{wid}.conf", "reason": str(exc)})
    return True


BACKUP = Store("overrides", (f"{PREFIX}*.conf",), _backup_export, _backup_preflight, _backup_apply)
