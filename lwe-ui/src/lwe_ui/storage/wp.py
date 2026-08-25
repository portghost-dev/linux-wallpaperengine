"""Typed load/save for wp/<id>.conf (Tier A, shell-sourceable).

Each conf is a per-wallpaper override set. Dynamic PROP_<name> keys are collected into a
`props: dict[str,str]` on load and expanded back on save. Optional/empty keys are omitted
on save so the file stays minimal and consumers can distinguish "unset" from "set empty".
"""
from __future__ import annotations

import warnings
from typing import Any

from .. import constants as C
from . import atomic, paths, tier_a

# Keys whose empty value means "unset / inherit" and must NOT be written.
_OMIT_IF_EMPTY = ("FPS", "CLAMPING", "FULLSCREEN_PAUSE", "SKIP", "CC_MODE")


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
    conf path reuses it verbatim.
    """
    from pathlib import Path

    text = ""
    p = Path(path)
    if p.exists():
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            text = ""
    raw = tier_a.parse(text)
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
    """
    from pathlib import Path

    text = ""
    p = Path(path)
    if p.exists():
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            text = ""
    raw = tier_a.parse(text)
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
    """
    from pathlib import Path

    text = ""
    if Path(path).exists():
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            text = ""
    flat = tier_a.parse(text)
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


def update_set(wid: str, changes: dict[str, Any]) -> None:
    """Presence-preserving key edit of wp/<wid>.conf. See update_set_path."""
    update_set_path(paths.wp_file(wid), changes)


def _bool_str(val: Any) -> str:
    return "true" if val else "false"


def save_path(path, d: dict[str, Any]) -> None:
    """Inverse of load_path: expand props to PROP_<name>, omit empty optionals, write atomically.

    Path-based core behind save(wid), so every writer of a wp-schema conf shares one
    serialization.
    """
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

    from pathlib import Path

    stem = Path(path).stem
    text = tier_a.serialize(flat, header=f"lwe wallpaper override {stem} (Tier A)")
    atomic.atomic_write_text(path, text)


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
