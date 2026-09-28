"""Typed load/save for settings.conf (Tier A, shell-sourceable).

Values are python-typed on load (bool/int/float/str per C.SETTINGS_SCHEMA) and serialized
back as shell-safe KEY=value via tier_a. Validation clamps + warns; it never crashes.
"""
from __future__ import annotations

import os
import warnings
import zipfile
from typing import Any, Callable

from .. import constants as C
from . import atomic, foreign, lock, migrate, paths, tier_a
from .store import Store


_TRUE = ("true", "1", "yes", "on")
_FALSE = ("false", "0", "no", "off", "")


def _coerce(key: str, raw: str, spec: dict, report: list | None = None) -> Any:
    """Coerce a raw string from the file to the schema python type. Tolerant of bad input."""
    t = spec["type"]
    s = str(raw).strip()
    if t in ("bool", "bool_or_empty"):
        if s == "" and t == "bool_or_empty":
            return ""
        if s.lower() in _TRUE:
            return True
        if s.lower() in _FALSE:
            return False
        _say(report, "snap", key, raw, spec["default"], f"settings: {key}={raw!r} not a bool; using default")
        return spec["default"]
    if t == "int":
        try:
            return int(s)
        except (ValueError, TypeError):
            _say(report, "snap", key, raw, spec["default"], f"settings: {key}={raw!r} not an int; using default")
            return spec["default"]
    if t == "int_or_empty":
        if s == "":
            return ""
        try:
            return int(s)
        except (ValueError, TypeError):
            _say(report, "snap", key, raw, "", f"settings: {key}={raw!r} not an int; leaving empty")
            return ""
    if t == "float":
        try:
            return float(s)
        except (ValueError, TypeError):
            _say(report, "snap", key, raw, spec["default"], f"settings: {key}={raw!r} not a number; using default")
            return spec["default"]
    # enum / enum_or_empty / path / str / packed all carry through as plain strings.
    return str(raw)


def _say(report: list | None, kind: str, key: str, frm: Any, to: Any, text: str) -> None:
    """One channel for a clamp or a snap: the caller's list when it asked for one, the
    warning a normal load has always emitted otherwise."""
    if report is None:
        warnings.warn(text)
    else:
        report.append({"kind": kind, "key": key, "from": frm, "to": to})


def _clamp_int(key: str, val: int, spec: dict, report: list | None = None) -> int:
    """Clamp an int to the schema [min,max], naming each clamp through _say."""
    lo, hi = spec.get("min"), spec.get("max")
    if lo is not None and val < lo:
        _say(report, "clamp", key, val, lo, f"settings: {key}={val} < min {lo}; clamping")
        val = lo
    if hi is not None and val > hi:
        _say(report, "clamp", key, val, hi, f"settings: {key}={val} > max {hi}; clamping")
        val = hi
    return val


def migrate_raw(raw: dict[str, str], actions: list | None = None) -> dict[str, str]:
    """The renames, retirements and value aliases a stored settings text goes through before
    coercion. One call, so a normal load and a backup import apply exactly the same tables;
    `actions` collects what they did for a caller that reports it."""
    out, did = migrate.apply_tables("settings", raw)
    if actions is not None:
        actions.extend(did)
    return out


def load() -> dict[str, Any]:
    """Read settings.conf, coerce per schema. Missing keys filled from default_settings()."""
    defaults = paths.default_settings()
    text = ""
    p = paths.settings_file()
    if p.exists():
        try:
            text = p.read_bytes().decode("utf-8")
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


def _validate(d: dict[str, Any], report: list | None = None) -> dict[str, Any]:
    """Clamp ints to [min,max], snap unknown enum values to default. Never raises. Each
    adjustment reaches `report` when the caller passes one and a warning when it does not."""
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
                _say(report, "snap", key, val, spec["default"],
                     f"settings: {key}={val!r} not an int; using default")
                val = spec["default"]
            val = _clamp_int(key, val, spec, report)
        elif t == "int_or_empty":
            # "" means "let the engine decide"; a present value must be a clamped int.
            if val is None or str(val).strip() == "":
                val = ""
            else:
                try:
                    val = _clamp_int(key, int(val), spec, report)
                except (ValueError, TypeError):
                    _say(report, "snap", key, val, "",
                         f"settings: {key}={val!r} not an int; leaving empty")
                    val = ""
        elif t == "enum":
            choices = spec.get("choices", ())
            val = C.VALUE_ALIASES.get("settings", {}).get(key, {}).get(str(val), val)
            if val not in choices:
                _say(report, "snap", key, val, spec["default"],
                     f"settings: {key}={val!r} not in {choices}; using default")
                val = spec["default"]
        elif t == "enum_or_empty":
            choices = spec.get("choices", ())
            if val != "" and val not in choices:
                _say(report, "snap", key, val, "",
                     f"settings: {key}={val!r} not in {choices}; leaving empty")
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
    with lock.held("settings"):
        atomic.atomic_write_text(paths.settings_file(), text)


def ensure_exists() -> None:
    """Write defaults if the file is absent (does not overwrite an existing file)."""
    if not paths.settings_file().exists():
        with lock.held("settings"):
            if not paths.settings_file().exists():
                save(paths.default_settings())


def modify(fn: Callable[[dict[str, Any]], dict[str, Any] | None]) -> dict[str, Any]:
    """Under the settings lock: load settings.conf fresh, pass it to fn, and apply the keys fn
    returns to the file's own lines: a value sets its key, None deletes it and every old name
    of it, every other line stays. A missing file is first written with the full defaults when
    fn returns a change. Returns those keys; the file is written only when its text changes."""
    with lock.held("settings"):
        p = paths.settings_file()
        changes = fn(load()) or {}
        if changes:
            if not p.exists():
                save(paths.default_settings())
            text = p.read_bytes().decode("utf-8")
            flat: dict[str, str | None] = dict(_to_text(_validate(
                {k: v for k, v in changes.items() if v is not None})))
            flat.update({k: None for k, v in changes.items() if v is None and k in C.SETTINGS_SCHEMA})
            new = tier_a.edit(text, migrate.with_old_names("settings", flat), path=p)
            if new != text:
                atomic.atomic_write_text(p, new)
        return changes


def update(changes: dict[str, Any]) -> dict[str, Any]:
    """Set these keys under the settings lock, over a fresh load."""
    return modify(lambda _current: dict(changes))


def replace(fn: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    """Whole-store write under the settings lock: fn receives a fresh load and returns the
    complete settings to write."""
    with lock.held("settings"):
        save(fn(load()))


# --- backup ---------------------------------------------------------------------------
MEMBER = "settings.conf"
#: every path-typed setting names this machine: kept on import only when it resolves here
MACHINE_KEYS = tuple(k for k, s in C.SETTINGS_SCHEMA.items() if s["type"] == "path")


def _backup_export(z: zipfile.ZipFile, r: dict[str, Any]) -> None:
    flat = dict(_to_text(load()))
    for key, val in foreign.extras("settings", MEMBER).items():
        if key in flat:
            continue
        if tier_a.is_valid_key(key):
            flat[key] = str(val)
            foreign.emitted(r, 1)
        else:
            r["dropped"].append({"kind": "preserved-key", "id": key, "reason": "not a key this format can hold"})
    for key in [k for k, v in flat.items() if "\r" in v]:
        del flat[key]
        r["dropped"].append({"kind": "setting", "file": MEMBER, "key": key,
                             "reason": "the value holds a line break the archive cannot carry"})
    z.writestr(MEMBER, tier_a.serialize(flat, header="lwe settings backup"))


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
    """The same tables, coercion and validation a normal load applies; a key this build does
    not know is preserved, every clamp, snap and alias is named. A backup with no settings
    member leaves the current settings to decide the rest of the plan."""
    current = load()
    if MEMBER not in z.namelist():
        r["notes"].append({"kind": "no-settings", "member": MEMBER})
        plan["settings"] = {}
        return True
    actions: list = []
    raw = migrate_raw(tier_a.parse(z.read(MEMBER).decode("utf-8", "replace")), actions)
    migrate.report("settings", actions, r, MEMBER, "setting")
    known: dict[str, str] = {}
    for k, v in raw.items():
        if k in C.SETTINGS_SCHEMA:
            known[k] = v
        else:
            foreign.record(plan, "settings", MEMBER, k, v, r)
    report: list = []
    coerced = _validate({k: _coerce(k, v, C.SETTINGS_SCHEMA[k], report) for k, v in known.items()}, report)
    for a in report:
        r["adjusted"].append({"kind": a["kind"], "store": "settings", "id": MEMBER,
                              "key": a["key"], "from": a["from"], "to": a["to"]})
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
        replace(lambda current: {**current, **(plan.get("settings") or {})})
    except Exception as exc:
        r["errors"].append({"file": MEMBER, "reason": str(exc)})
        return False
    return True


BACKUP = Store("settings", (MEMBER,), _backup_export, _backup_preflight, _backup_apply)
