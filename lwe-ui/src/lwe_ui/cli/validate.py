"""Reload's checks and its report: every file lwe reads, checked from its raw text and never through the
loaders that fill defaults, and what changed since reload's own last snapshot.

check_all(status) returns (errors, warnings, cleanups). Each error and warning reads "file:line: text",
or "file: text" for a whole file; each cleanup is an edit cli/verbs/playlist.py::apply_cleanups makes.
changes() compares the files with the snapshot, and write_snapshot() replaces the snapshot with copies of
them. Nothing else here writes. Top level imports stdlib only; the store and the other checkers are
imported when a function runs.
"""
from __future__ import annotations

import math
import os
import re
import shutil
from pathlib import Path
from typing import Any

APP_LISTS = (("app-condition.txt", 15), ("pause-blacklist.txt", 128))
LIST_LINES = 128
HUE_LIMIT = 6.4
_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_TRUE_FALSE = ("true", "false", "1", "0", "yes", "no", "on", "off")
_SCHEDULE_TEXT = {
    "rows": "SCHEDULE must be two time=playlist entries",
    "time": "SCHEDULE's {part} time is not a time of day (HH:MM)",
    "same time": "SCHEDULE's day and night start at the same time",
    "unset": "SCHEDULE's {part} playlist is not set while SCHEDULE_ENABLED is true",
    "missing": "SCHEDULE's {part} playlist has no file in playlists while SCHEDULE_ENABLED is true",
}


def _bash_value(value: str) -> tuple[str | None, str]:
    """The value bash gives `KEY=<value>`, and why it cannot be read the same way when that is so."""
    out: list[str] = []
    quote, i = "", 0
    while i < len(value):
        ch = value[i]
        if quote == "'":
            if ch == "'":
                quote = ""
            else:
                out.append(ch)
        elif quote == '"':
            if ch == '"':
                quote = ""
            elif ch == "\\" and i + 1 < len(value) and value[i + 1] in '\\"$`':
                out.append(value[i + 1])
                i += 1
            elif ch in "$`":
                return None, "a $ or ` the shell would expand"
            else:
                out.append(ch)
        elif ch in " \t":
            rest = value[i:].strip()
            if rest and not rest.startswith("#"):
                return None, "a space outside quotes; the shell would run the rest as a command"
            return "".join(out), ""
        elif ch in "'\"":
            quote = ch
        elif ch == "\\" and i + 1 < len(value):
            out.append(value[i + 1])
            i += 1
        elif ch in "$`":
            return None, "a $ or ` the shell would expand"
        else:
            out.append(ch)
        i += 1
    if quote:
        return None, "a quote the line does not close"
    return "".join(out), ""


def line_problem(line: str) -> str | None:
    """Why a non-blank, non-comment line of a Tier A file is not an assignment lwe and bash read alike,
    or None when it is."""
    from ..storage import tier_a
    s = line.strip()
    if s.startswith("export ") or s.startswith("export\t"):
        return "an export line; lwe reads only KEY=value lines"
    match = _ASSIGNMENT.match(s)
    if not match:
        return "not a KEY=value line"
    bash, why = _bash_value(match.group(2))
    if bash is None:
        return why
    ours = tier_a.parse(s).get(match.group(1), "")
    if bash != ours:
        return f"lwe reads this value as {ours!r} and the shell as {bash!r}; write it in double quotes"
    return None


def _lines(where: str, text: str, store: str, errors: list[str],
           warnings: list[str]) -> list[tuple[int, str, str, str]]:
    """Each assignment of a Tier A file as (line number, the key as written, the key under this build's
    name, the value); bad lines become errors, a key set twice a warning."""
    from ..storage import migrate, tier_a
    out: list[tuple[int, str, str, str]] = []
    seen: dict[str, int] = {}
    for number, line in enumerate(text.split("\n"), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        problem = line_problem(line)
        if problem is not None:
            errors.append(f"{where}:{number}: {problem}")
            continue
        parsed = tier_a.parse(s)
        written = next(iter(parsed))
        mapped, _actions = migrate.apply_tables(store, parsed)
        for key, value in mapped.items():
            if key in seen:
                warnings.append(f"{where}:{number}: {written} is set again; line {seen[key]} is not read")
            seen[key] = number
            out.append((number, written, key, str(value)))
    return out


def _check_settings(errors: list[str], warnings: list[str]) -> None:
    from .. import constants as C
    from ..storage import paths, schedule, settings
    try:
        text = paths.settings_file().read_bytes().decode("utf-8", "replace")
    except OSError:
        return
    values: dict[str, tuple[int, str]] = {}
    for number, written, key, value in _lines("settings.conf", text, "settings", errors, warnings):
        values[key] = (number, value)
        if key in C.SETTINGS_SCHEMA:
            reason = settings.check_raw(key, value)
            if reason is not None:
                errors.append(f"settings.conf:{number}: {written}={value} is {reason}")
        elif written != settings.OWNED_KEY:
            warnings.append(f"settings.conf:{number}: {written} is not a setting this version knows; it is kept "
                            "and not applied")
    enabled_raw = values.get("SCHEDULE_ENABLED", (0, "false"))[1].strip().lower()
    if enabled_raw not in _TRUE_FALSE:
        return
    number, text_value = values.get("SCHEDULE", (0, ""))
    for reason, part, _line in schedule.check_reload(text_value, enabled_raw in ("true", "1", "yes", "on")):
        where = f"settings.conf:{number}" if number else "settings.conf"
        errors.append(f"{where}: {_SCHEDULE_TEXT[reason].format(part=part)}")


def _wp_problem(spec: dict, value: str) -> str | None:
    """Why wp/<id>.conf may not hold `value` for a key of this spec, or None."""
    from ..storage import migrate
    kind, s = spec["type"], value.strip()
    if kind in ("bool", "bool_or_empty"):
        if kind == "bool_or_empty" and s == "":
            return None
        return None if s.lower() in _TRUE_FALSE else "not true or false"
    verdict, _value, reason = migrate.coerce(spec, value, False)
    if verdict == "clamp":
        return f"outside {spec.get('min', '')} to {spec.get('max', '')}"
    if verdict != "ok":
        return reason or "not a value this key takes"
    if kind in ("float", "float_or_empty") and s != "" and not math.isfinite(float(s)):
        return "not a finite number"
    return None


def _cc_problem(value: str) -> str | None:
    """Why a CC value is not four finite numbers within the engine's clamps, or None."""
    if value.strip() == "":
        return None
    try:
        parts = [float(p) for p in value.split()]
    except ValueError:
        return "CC must be four numbers: brightness contrast saturation hue"
    if len(parts) != 4 or not all(math.isfinite(p) for p in parts):
        return "CC must be four numbers: brightness contrast saturation hue"
    if not all(0.0 <= p <= 4.0 for p in parts[:3]):
        return "CC brightness, contrast and saturation must each be 0 to 4"
    if not -HUE_LIMIT <= parts[3] <= HUE_LIMIT:
        return f"CC hue must be within {HUE_LIMIT} radians either side of 0"
    return None


def _declared(wid: str, cfg: dict[str, Any]) -> set[str] | None:
    """The property names the wallpaper's project.json declares; None when it cannot be read."""
    from ..discovery import project
    from ..storage import paths
    root = str(cfg.get("WALLPAPERS_DIR") or paths.default_wallpapers_dir())
    try:
        return set(project.read(Path(root) / wid).get("properties") or {})
    except Exception:
        return None


def _check_wallpapers(errors: list[str], warnings: list[str]) -> None:
    from .. import constants as C
    from ..storage import alias, paths, settings
    folder = paths.wp_dir()
    if not folder.is_dir():
        return
    cfg = settings.load()
    taken = alias.claims()
    ids = alias.existing_ids(cfg)
    for path in sorted(folder.glob("*.conf")):
        wid, where = path.stem, f"wp/{path.name}"
        if not paths.is_safe_wid(wid):
            errors.append(f"{where}: the file name is not a wallpaper id")
            continue
        text = path.read_bytes().decode("utf-8", "replace")
        declared: set[str] | None = None
        for number, written, key, value in _lines(where, text, "overrides", errors, warnings):
            if key == "CC":
                problem = _cc_problem(value)
            elif key == "ALIAS":
                problem = alias.check(value.strip(), wid, ids=ids, taken=taken) if value.strip() else None
            elif key in C.WP_SCHEMA:
                problem = _wp_problem(C.WP_SCHEMA[key], value)
                problem = None if problem is None else f"{written}={value} is {problem}"
            elif key.startswith(C.WP_PROP_PREFIX):
                if declared is None:
                    declared = _declared(wid, cfg) or set()
                name = key[len(C.WP_PROP_PREFIX):]
                if declared and name not in declared:
                    warnings.append(f"{where}:{number}: the wallpaper declares no property {name}; the line is "
                                    "kept and sent")
                problem = None
            else:
                warnings.append(f"{where}:{number}: {written} is not a key this version knows; it is kept and not "
                                "applied")
                problem = None
            if problem is not None:
                errors.append(f"{where}:{number}: {problem}")


def _check_lists(errors: list[str], warnings: list[str]) -> None:
    from ..storage import paths
    for name, limit in APP_LISTS:
        try:
            text = (paths.config_dir() / name).read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        entries = 0
        for number, line in enumerate(text.split("\n"), 1):
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue
            entries += 1
            if not entry.isprintable():
                errors.append(f"{name}:{number}: the entry holds a character that is not printable")
            elif len(entry) > limit:
                errors.append(f"{name}:{number}: the entry is longer than {limit} characters")
            if entries == LIST_LINES + 1:
                warnings.append(f"{name}:{number}: entries past the first {LIST_LINES} are not read")


def check_all(status: dict | None = None) -> tuple[list[str], list[str], list[dict]]:
    """(errors, warnings, cleanups) over settings.conf, every playlists/*.conf and wp/*.conf, and the two
    app lists; `status` is the engine's status or None."""
    from .verbs.playlist import check_files
    errors: list[str] = []
    warnings: list[str] = []
    _check_settings(errors, warnings)
    playlist_errors, playlist_warnings, cleanups, _changes = check_files(status)
    errors += playlist_errors
    warnings += playlist_warnings
    _check_wallpapers(errors, warnings)
    _check_lists(errors, warnings)
    return errors, warnings, cleanups


def census() -> dict[str, Path]:
    """{the name reload prints: the path} for every file reload reads that exists."""
    from ..storage import paths
    out: dict[str, Path] = {}
    if paths.settings_file().is_file():
        out["settings.conf"] = paths.settings_file()
    for sub, folder in (("playlists", paths.playlists_dir()), ("wp", paths.wp_dir())):
        if folder.is_dir():
            for path in sorted(folder.glob("*.conf")):
                out[f"{sub}/{path.name}"] = path
    for name, _limit in APP_LISTS:
        path = paths.config_dir() / name
        if path.is_file():
            out[name] = path
    return out


def snapshot_dir() -> Path:
    from ..storage import paths
    return paths.panel_state_dir() / "reload-snapshot"


def _read(path: Path, name: str) -> dict[str, str] | list[str]:
    """A file as reload compares it: a Tier A file's keys, or an app list's entries."""
    from ..storage import tier_a
    text = path.read_bytes().decode("utf-8", "replace")
    if name.endswith(".txt"):
        return [e.strip() for e in text.split("\n") if e.strip() and not e.strip().startswith("#")]
    return tier_a.parse(text)


def _names(name: str) -> dict[str, str]:
    """{file key: the name lwe's commands give it} for the keys of this file that have one."""
    from . import settings_table
    if name != "settings.conf" and not name.startswith("wp/"):
        return {}
    form = settings_table.GLOBAL if name == "settings.conf" else settings_table.WALLPAPER
    named: dict[str, list[str]] = {}
    for row in settings_table.ROWS:
        if row.form == form and row.field is None and not row.key.endswith("_"):
            named.setdefault(row.key, []).append(row.name)
    return {key: names[0] for key, names in named.items() if len(names) == 1}


def changes() -> list[str] | None:
    """What changed since the last snapshot, one line each; None when there is no snapshot yet."""
    old_root = snapshot_dir()
    if not old_root.is_dir():
        return None
    now = census()
    before = {str(p.relative_to(old_root)): p for p in sorted(old_root.rglob("*")) if p.is_file()}
    lines: list[str] = []
    for name in sorted(set(now) | set(before)):
        if name not in before:
            lines.append(f"{name}: added")
            continue
        if name not in now:
            lines.append(f"{name}: removed")
            continue
        old, new = _read(before[name], name), _read(now[name], name)
        if isinstance(new, list) and isinstance(old, list):
            lines += [f"{name}: {e} added" for e in new if e not in old]
            lines += [f"{name}: {e} removed" for e in old if e not in new]
            continue
        names = _names(name)
        for key in list(dict.fromkeys([*old, *new])):
            label = names.get(key, key)
            if key not in old:
                lines.append(f"{name}: {label} set to {new[key]}")
            elif key not in new:
                lines.append(f"{name}: {label} removed (was {old[key]})")
            elif old[key] != new[key]:
                lines.append(f"{name}: {label} {old[key]} -> {new[key]}")
    return lines


def write_snapshot() -> None:
    """Replace the snapshot with copies of the files reload read."""
    root = snapshot_dir()
    fresh = root.with_name(root.name + ".new")
    shutil.rmtree(fresh, ignore_errors=True)
    fresh.mkdir(parents=True)
    for name, path in census().items():
        target = fresh / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    shutil.rmtree(root, ignore_errors=True)
    os.replace(fresh, root)
