"""Typed load/save for settings.conf (Tier A, shell-sourceable).

Values are python-typed on load (bool/int/float/str per C.SETTINGS_SCHEMA) and serialized
back as shell-safe KEY=value via tier_a. Validation clamps + warns; it never crashes.
"""
from __future__ import annotations

import os
import warnings
import zipfile
from typing import Any

from .. import constants as C
from . import atomic, paths, tier_a
from .store import Store


def _coerce(key: str, raw: str, spec: dict) -> Any:
    """Coerce a raw string from the file to the schema python type. Tolerant of bad input."""
    t = spec["type"]
    if t == "bool":
        return str(raw).strip().lower() in ("true", "1", "yes", "on")
    if t == "bool_or_empty":
        s = str(raw).strip()
        return "" if s == "" else s.lower() in ("true", "1", "yes", "on")
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
    # enum / enum_or_empty / path / str / packed all carry through as plain strings.
    return str(raw)


def _clamp_int(key: str, val: int, spec: dict) -> int:
    """Clamp an int to the schema [min,max], warning on each clamp."""
    lo, hi = spec.get("min"), spec.get("max")
    if lo is not None and val < lo:
        warnings.warn(f"settings: {key}={val} < min {lo}; clamping")
        val = lo
    if hi is not None and val > hi:
        warnings.warn(f"settings: {key}={val} > max {hi}; clamping")
        val = hi
    return val


def migrate_raw(raw: dict[str, str]) -> dict[str, str]:
    """The renames and retirements a stored settings text goes through before coercion.
    One function, so a normal load and a backup import apply exactly the same migrations."""
    # MIGRATION (ledger S-12.5): vendor-specific decoder tokens collapse to auto. The
    # schema choices are (no, auto) now; without this a stored nvdec would coerce to the
    # DEFAULT (no) and silently flip a hardware-decode user to software.
    if str(raw.get("ENGINE_HWDEC", "")).strip() in ("nvdec", "vaapi", "vulkan"):
        raw["ENGINE_HWDEC"] = "auto"
    # MIGRATION: DETECT_INTERVAL_MIN (minutes) became DETECT_INTERVAL_SEC (seconds).
    # Without this a stored minutes value would be dropped and the user's period reset.
    if "DETECT_INTERVAL_MIN" in raw:
        if "DETECT_INTERVAL_SEC" not in raw:
            try:
                raw["DETECT_INTERVAL_SEC"] = str(int(str(raw["DETECT_INTERVAL_MIN"]).strip()) * 60)
            except (ValueError, TypeError):
                pass
        raw.pop("DETECT_INTERVAL_MIN", None)
    return raw


def load() -> dict[str, Any]:
    """Read settings.conf, coerce per schema. Missing keys filled from default_settings()."""
    defaults = paths.default_settings()
    text = ""
    p = paths.settings_file()
    if p.exists():
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            text = ""
    raw = migrate_raw(tier_a.parse(text))
    out: dict[str, Any] = {}
    for key, spec in C.SETTINGS_SCHEMA.items():
        if key in raw:
            out[key] = _coerce(key, raw[key], spec)
        else:
            out[key] = defaults.get(key, spec["default"])
    # the same clamp and enum snap a save applies, so a value edited by hand or written
    # by an older panel never reaches a consumer out of range
    return _validate(out)


def _validate(d: dict[str, Any]) -> dict[str, Any]:
    """Clamp ints to [min,max], snap unknown enum values to default. Warn, never raise."""
    out: dict[str, Any] = {}
    for key, spec in C.SETTINGS_SCHEMA.items():
        if key not in d:
            continue
        t = spec["type"]
        val = d[key]
        if t == "int":
            try:
                val = int(val)
            except (ValueError, TypeError):
                warnings.warn(f"settings: {key}={val!r} not an int; using default")
                val = spec["default"]
            val = _clamp_int(key, val, spec)
        elif t == "int_or_empty":
            # "" means "let the engine decide"; a present value must be a clamped int.
            if val is None or str(val).strip() == "":
                val = ""
            else:
                try:
                    val = _clamp_int(key, int(val), spec)
                except (ValueError, TypeError):
                    warnings.warn(f"settings: {key}={val!r} not an int; leaving empty")
                    val = ""
        elif t == "enum":
            choices = spec.get("choices", ())
            if key == "ORDER" and val == "random":
                val = "shuffle"  # retired mode, see PLAYLIST_MODES
            if val not in choices:
                warnings.warn(f"settings: {key}={val!r} not in {choices}; using default")
                val = spec["default"]
        elif t == "enum_or_empty":
            choices = spec.get("choices", ())
            if val != "" and val not in choices:
                warnings.warn(f"settings: {key}={val!r} not in {choices}; leaving empty")
                val = ""  # empty = engine default, never a wrong choice
        elif t == "bool":
            val = bool(val)
        elif t == "bool_or_empty":
            if str(val).strip() != "":
                val = bool(val)
        out[key] = val
    return out


def _to_text(d: dict[str, Any]) -> dict[str, str]:
    """Schema-typed dict -> str dict for tier_a (bool as true/false)."""
    flat: dict[str, str] = {}
    for key, spec in C.SETTINGS_SCHEMA.items():
        if key not in d:
            continue
        val = d[key]
        t = spec["type"]
        if t == "bool":
            flat[key] = "true" if val else "false"
        elif t == "bool_or_empty":
            flat[key] = "" if val is None or str(val).strip() == "" else ("true" if val else "false")
        else:
            flat[key] = "" if val is None else str(val)
    return flat


def save(d: dict[str, Any]) -> None:
    """Validate, serialize (bools as true/false), atomically write settings.conf."""
    valid = _validate(d)
    text = tier_a.serialize(_to_text(valid), header="lwe settings (Tier A) - managed by LWE Control Panel")
    atomic.atomic_write_text(paths.settings_file(), text)


def ensure_exists() -> None:
    """Write defaults if the file is absent (does not overwrite an existing file)."""
    if not paths.settings_file().exists():
        save(paths.default_settings())


# --- backup ---------------------------------------------------------------------------
MEMBER = "settings.conf"
#: every path-typed setting names this machine: kept on import only when it resolves here
MACHINE_KEYS = tuple(k for k, s in C.SETTINGS_SCHEMA.items() if s["type"] == "path")


def _backup_export(z: zipfile.ZipFile, r: dict[str, Any]) -> None:
    z.writestr(MEMBER, tier_a.serialize(_to_text(load()), header="lwe settings backup"))


def _followups(r: dict[str, Any], after: dict[str, Any], current: dict[str, Any]) -> None:
    """What a changed setting implies, decided from the diff."""
    if "INTERFACE_SCALE" in after and int(after["INTERFACE_SCALE"]) != int(current.get("INTERFACE_SCALE", 100)):
        r["followups"].append({"kind": "relaunch", "state": "pending"})
    if any(k in after and after[k] != current.get(k) for k in C.REACH_SERVICE_RESTART):
        r["followups"].append({"kind": "engine-restart", "state": "pending"})
    if any(k in after and after[k] != current.get(k) for k in ("WALLPAPERS_DIR", "WORKSHOP_DIR")):
        r["followups"].append({"kind": "rescan", "state": "pending"})


def _backup_preflight(z: zipfile.ZipFile, r: dict[str, Any], plan: dict[str, Any],
                      cfg_after: dict[str, Any]) -> bool:
    """The same migrations and coercion a normal load applies; unknown keys are dropped and
    named. Without settings there is nothing to decide the rest of the plan against, so this
    is the one store whose absence abandons the import."""
    if MEMBER not in z.namelist():
        r["errors"].append({"file": MEMBER, "reason": "The backup carries no settings."})
        return False
    current = load()
    raw = migrate_raw(tier_a.parse(z.read(MEMBER).decode("utf-8", "replace")))
    known = {k: v for k, v in raw.items() if k in C.SETTINGS_SCHEMA}
    for k in raw:
        if k not in C.SETTINGS_SCHEMA:
            r["dropped"].append({"kind": "setting", "id": k, "reason": "unknown to this version"})
    coerced = _validate({k: _coerce(k, v, C.SETTINGS_SCHEMA[k]) for k, v in known.items()})
    for key in MACHINE_KEYS:
        if key not in coerced:
            continue
        want = str(coerced[key])
        have = str(current.get(key, ""))
        if want and want != have and not os.path.exists(want):
            r["reresolved"].append({"key": key, "from": want, "to": have})
            coerced[key] = current.get(key, "")
    plan["settings"] = coerced
    _followups(r, coerced, current)
    return True


def _backup_apply(plan: dict[str, Any], r: dict[str, Any]) -> bool:
    try:
        save({**load(), **(plan.get("settings") or {})})
    except Exception as exc:
        r["errors"].append({"file": MEMBER, "reason": str(exc)})
        return False
    return True


BACKUP = Store("settings", (MEMBER,), _backup_export, _backup_preflight, _backup_apply)
