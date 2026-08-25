"""Typed load/save for settings.conf (Tier A, shell-sourceable).

Values are python-typed on load (bool/int/float/str per C.SETTINGS_SCHEMA) and serialized
back as shell-safe KEY=value via tier_a. Validation clamps + warns; it never crashes.
"""
from __future__ import annotations

import warnings
from typing import Any

from .. import constants as C
from . import atomic, paths, tier_a


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
    raw = tier_a.parse(text)
    # MIGRATION (ledger S-12.5): vendor-specific decoder tokens collapse to auto. The
    # schema choices are (no, auto) now; without this a stored nvdec would coerce to the
    # DEFAULT (no) and silently flip a hardware-decode user to software.
    if str(raw.get("ENGINE_HWDEC", "")).strip() in ("nvdec", "vaapi", "vulkan"):
        raw["ENGINE_HWDEC"] = "auto"
    # MIGRATION: DETECT_INTERVAL_MIN (minutes) became DETECT_INTERVAL_SEC (seconds).
    # Without this a stored minutes value would be dropped and the user's period reset.
    if "DETECT_INTERVAL_MIN" in raw and "DETECT_INTERVAL_SEC" not in raw:
        try:
            raw["DETECT_INTERVAL_SEC"] = str(int(str(raw["DETECT_INTERVAL_MIN"]).strip()) * 60)
        except (ValueError, TypeError):
            pass
    out: dict[str, Any] = {}
    for key, spec in C.SETTINGS_SCHEMA.items():
        if key in raw:
            out[key] = _coerce(key, raw[key], spec)
        else:
            out[key] = defaults.get(key, spec["default"])
    return out


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
