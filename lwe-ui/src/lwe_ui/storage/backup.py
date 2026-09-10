"""Configuration backup: one `.lwebackup` file, a zip with a manifest.

What travels is what the user authored and the app cannot regenerate: settings, theme,
playlists, per-wallpaper overrides, tags, favourites and the app rules. Nothing generated
travels (the engine env file, caches, state, logs).

Durability across builds comes from the schemas, not from the files: import never copies a
file, it loads each one through this build's schema and re-saves it, so unknown keys drop,
missing keys take defaults and renamed keys go through the same migrations a normal load
does. The manifest carries a format version so a future breaking change is refused with a
plain reason instead of mangled.
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import os
import socket
import zipfile
from pathlib import Path
from typing import Any

from .. import constants as C
from . import meta, paths, playlists, settings, tags, theme_cfg, tier_a, wp

FORMAT = 1
EXTENSION = ".lwebackup"
MANIFEST = "manifest.json"
RULE_FILES = ("app-condition.txt", "pause-blacklist.txt", "pause-whitelist.txt")
#: settings keys that name this machine's folders and binary: kept only when they resolve here
MACHINE_KEYS = ("ENGINE_BIN", "ASSETS_DIR", "WALLPAPERS_DIR", "WORKSHOP_DIR", "STEAM_DIR")


def default_name() -> str:
    return f"lwe-backup-{datetime.datetime.now():%Y-%m-%d-%H%M}{EXTENSION}"


def _receipt(kind: str) -> dict[str, Any]:
    return {"kind": kind, "path": "", "counts": {}, "dropped": [], "held": [],
            "reresolved": [], "followups": [], "errors": []}


# --- export ---------------------------------------------------------------------------

def _portable_bg(wid: str, bg: str) -> str:
    """A folder that is this library's own copy travels as the bare id, which the app
    resolves against the library folder; a referenced folder travels as it is."""
    if bg and os.path.isabs(bg) and os.path.basename(bg.rstrip("/")) == wid:
        return wid
    return bg


def export_to(path: str | Path) -> dict[str, Any]:
    r = _receipt("export")
    r["path"] = str(path)
    buf = io.BytesIO()
    counts: dict[str, int] = {"playlists": 0, "overrides": 0, "tags": 0, "favourites": 0, "rules": 0}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        s = settings.load()
        z.writestr("settings.conf", tier_a.serialize(settings._to_text(s), header="lwe settings backup"))
        try:
            z.writestr("theme.json", json.dumps(theme_cfg.load(), indent=1))
        except Exception as exc:  # a theme that will not load is not the backup's fault
            r["errors"].append({"file": "theme.json", "reason": str(exc)})
        for pl in playlists.list_playlists():
            slug = str(pl.get("slug") or "")
            if not slug:
                continue
            d = playlists.load(slug)
            z.writestr(f"playlists/{slug}.conf",
                       tier_a.serialize({k: str(d[k]) for k in C.PLAYLIST_SCHEMA}, header="lwe playlist"))
            counts["playlists"] += 1
        for conf in sorted(paths.wp_dir().glob("*.conf")):
            wid = conf.stem
            if not paths.is_safe_wid(wid):
                continue
            try:
                d = wp.load_path(conf)
            except Exception as exc:
                r["errors"].append({"file": conf.name, "reason": str(exc)})
                continue
            d["BG"] = _portable_bg(wid, str(d.get("BG") or ""))
            z.writestr(f"wp/{wid}.conf", wp.serialize(d, wid))
            counts["overrides"] += 1
        rows = tags.load()
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(tags.HEADER)
        for row in rows:
            w.writerow([row.get("id", ""), row.get("title", ""), row.get("state", "")])
        z.writestr("tags.csv", out.getvalue())
        counts["tags"] = len(rows)
        m = meta.load()
        z.writestr("meta.json", json.dumps(m, indent=1))
        counts["favourites"] = sum(1 for v in m.values() if isinstance(v, dict) and v.get("favorite"))
        for name in RULE_FILES:
            p = paths.config_dir() / name
            if p.is_file():
                try:
                    z.writestr(f"rules/{name}", p.read_text(encoding="utf-8"))
                    counts["rules"] += 1
                except OSError as exc:
                    r["errors"].append({"file": name, "reason": str(exc)})
        manifest = {
            "format": FORMAT,
            "app": "lwe-ui",
            "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "host": socket.gethostname(),
            "counts": counts,
            "machine_keys": list(MACHINE_KEYS),
        }
        z.writestr(MANIFEST, json.dumps(manifest, indent=1))
    Path(path).write_bytes(buf.getvalue())
    r["counts"] = counts
    return r


# --- import ---------------------------------------------------------------------------

def _read_manifest(z: zipfile.ZipFile) -> dict[str, Any]:
    try:
        m = json.loads(z.read(MANIFEST).decode("utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError):
        raise ValueError("That file is not an LWE backup.")
    if not isinstance(m, dict) or m.get("app") != "lwe-ui":
        raise ValueError("That file is not an LWE backup.")
    try:
        fmt = int(m.get("format", 0))
    except (TypeError, ValueError):
        fmt = 0
    if fmt < 1:
        raise ValueError("That file is not an LWE backup.")
    if fmt > FORMAT:
        raise ValueError("That backup was made by a newer version of LWE.")
    return m


def _wallpaper_present(wid: str, cfg: dict[str, Any]) -> bool:
    root = str(cfg.get("WALLPAPERS_DIR") or "")
    return bool(root) and os.path.isdir(os.path.join(root, wid))


def preflight(path: str | Path) -> dict[str, Any]:
    """Everything the import will do, decided before anything is written."""
    r = _receipt("import")
    r["path"] = str(path)
    plan: dict[str, Any] = {"settings": {}, "theme": None, "playlists": {}, "overrides": {},
                            "tags": [], "meta": {}, "rules": {}}
    r["plan"] = plan
    try:
        z = zipfile.ZipFile(str(path))
    except (OSError, zipfile.BadZipFile):
        r["errors"].append({"file": os.path.basename(str(path)), "reason": "That file is not an LWE backup."})
        return r
    with z:
        try:
            r["manifest"] = _read_manifest(z)
        except ValueError as exc:
            r["errors"].append({"file": MANIFEST, "reason": str(exc)})
            return r
        names = set(z.namelist())
        current = settings.load()

        # settings: through the schema; unknown keys are dropped and named
        if "settings.conf" in names:
            raw = tier_a.parse(z.read("settings.conf").decode("utf-8", "replace"))
            known = {k: v for k, v in raw.items() if k in C.SETTINGS_SCHEMA}
            for k in raw:
                if k not in C.SETTINGS_SCHEMA:
                    r["dropped"].append({"kind": "setting", "id": k, "reason": "unknown to this version"})
            coerced = settings._validate({k: settings._coerce(k, v, C.SETTINGS_SCHEMA[k]) for k, v in known.items()})
            for key in MACHINE_KEYS:
                if key not in coerced:
                    continue
                want = str(coerced[key])
                have = str(current.get(key, ""))
                if want and want != have and not os.path.exists(want):
                    r["reresolved"].append({"key": key, "from": want, "to": have})
                    coerced[key] = current.get(key, "")
            plan["settings"] = coerced
        else:
            r["errors"].append({"file": "settings.conf", "reason": "The backup carries no settings."})
            return r

        if "theme.json" in names:
            try:
                t = json.loads(z.read("theme.json").decode("utf-8"))
                plan["theme"] = t if isinstance(t, dict) else None
            except ValueError:
                r["errors"].append({"file": "theme.json", "reason": "not readable, skipped"})

        cfg_after = {**current, **plan["settings"]}
        for n in sorted(names):
            if n.startswith("playlists/") and n.endswith(".conf"):
                slug = n[len("playlists/"):-5]
                if not slug or playlists.slugify(slug) != slug:
                    r["dropped"].append({"kind": "playlist", "id": slug, "reason": "bad name"})
                    continue
                raw = tier_a.parse(z.read(n).decode("utf-8", "replace"))
                d = playlists._validate({k: playlists._coerce(k, raw[k], s) if k in raw else s["default"]
                                         for k, s in C.PLAYLIST_SCHEMA.items()})
                missing = [m for m in str(d["MEMBERS"]).split() if not _wallpaper_present(m, cfg_after)]
                if missing:
                    r["held"].append({"kind": "playlist-members", "id": slug, "count": len(missing)})
                plan["playlists"][slug] = d
            elif n.startswith("wp/") and n.endswith(".conf"):
                wid = n[3:-5]
                if not paths.is_safe_wid(wid):
                    r["dropped"].append({"kind": "override", "id": wid, "reason": "bad id"})
                    continue
                raw = tier_a.parse(z.read(n).decode("utf-8", "replace"))
                for k in raw:
                    if k not in C.WP_SCHEMA and not k.startswith(C.WP_PROP_PREFIX):
                        r["dropped"].append({"kind": "override-key", "id": f"{wid}:{k}", "reason": "unknown to this version"})
                d = wp.load_text("\n".join(f"{k}={tier_a.quote(v)}" for k, v in raw.items()))
                bg = str(d.get("BG") or "")
                if bg and os.path.isabs(bg) and not os.path.isdir(bg):
                    r["reresolved"].append({"key": f"override {wid} folder", "from": bg, "to": wid})
                    d["BG"] = wid
                if not d.get("BG"):
                    d["BG"] = wid
                if not _wallpaper_present(wid, cfg_after):
                    r["held"].append({"kind": "override", "id": wid})
                plan["overrides"][wid] = d
            elif n.startswith("rules/"):
                name = n[len("rules/"):]
                if name in RULE_FILES:
                    plan["rules"][name] = z.read(n).decode("utf-8", "replace")

        if "tags.csv" in names:
            reader = csv.DictReader(io.StringIO(z.read("tags.csv").decode("utf-8", "replace")))
            for row in reader:
                wid = str(row.get("id") or "").strip()
                state = str(row.get("state") or "").strip()
                if not paths.is_safe_wid(wid) or state not in tags._VALID_STATES:
                    r["dropped"].append({"kind": "tag", "id": wid or "?", "reason": "bad row"})
                    continue
                plan["tags"].append({"id": wid, "title": str(row.get("title") or ""), "state": state})
        if "meta.json" in names:
            try:
                m = json.loads(z.read("meta.json").decode("utf-8"))
                plan["meta"] = {k: v for k, v in m.items() if isinstance(v, dict) and paths.is_safe_wid(str(k))}
            except ValueError:
                r["errors"].append({"file": "meta.json", "reason": "not readable, skipped"})

        held_over = sum(1 for h in r["held"] if h["kind"] == "override")
        r["counts"] = {
            "playlists": len(plan["playlists"]),
            "overrides": len(plan["overrides"]),
            "overrides_held": held_over,
            "tags": len(plan["tags"]),
            "favourites": sum(1 for v in plan["meta"].values() if v.get("favorite")),
            "rules": len(plan["rules"]),
        }
        # follow-ups: what a changed setting implies, decided from the diff
        s_after = plan["settings"]
        if "INTERFACE_SCALE" in s_after and int(s_after["INTERFACE_SCALE"]) != int(current.get("INTERFACE_SCALE", 100)):
            r["followups"].append({"kind": "relaunch", "state": "pending"})
        from ..settings_bridge import _CLASS_SERVICE_RESTART
        if any(k in s_after and s_after[k] != current.get(k) for k in _CLASS_SERVICE_RESTART):
            r["followups"].append({"kind": "engine-restart", "state": "pending"})
        if any(k in s_after and s_after[k] != current.get(k) for k in ("WALLPAPERS_DIR", "WORKSHOP_DIR")):
            r["followups"].append({"kind": "rescan", "state": "pending"})
    return r


def apply(plan_receipt: dict[str, Any]) -> dict[str, Any]:
    """Write a preflight's plan through this build's stores. Returns the receipt with the
    plan removed and any write failure added to errors."""
    r = dict(plan_receipt)
    plan = r.pop("plan", None) or {}
    if r["errors"] and not plan.get("settings"):
        return r
    paths.ensure_dirs()
    try:
        settings.save({**settings.load(), **plan["settings"]})
    except Exception as exc:
        r["errors"].append({"file": "settings.conf", "reason": str(exc)})
        return r
    if plan.get("theme"):
        try:
            theme_cfg.save(plan["theme"])
        except Exception as exc:
            r["errors"].append({"file": "theme.json", "reason": str(exc)})
    for slug, d in plan["playlists"].items():
        try:
            playlists.save(slug, d)
        except Exception as exc:
            r["errors"].append({"file": f"playlists/{slug}.conf", "reason": str(exc)})
    for wid, d in plan["overrides"].items():
        try:
            wp.save(wid, d)
        except Exception as exc:
            r["errors"].append({"file": f"wp/{wid}.conf", "reason": str(exc)})
    if plan["tags"]:
        try:
            current = {row["id"]: row for row in tags.load() if row.get("id")}
            for row in plan["tags"]:
                current[row["id"]] = row
            tags.save(list(current.values()))
        except Exception as exc:
            r["errors"].append({"file": "tags.csv", "reason": str(exc)})
    if plan["meta"]:
        try:
            m = meta.load()
            for wid, patch in plan["meta"].items():
                m[wid] = {**m.get(wid, {}), **patch}
            meta.save(m)
        except Exception as exc:
            r["errors"].append({"file": "meta.json", "reason": str(exc)})
    for name, text in plan["rules"].items():
        try:
            (paths.config_dir() / name).write_text(text, encoding="utf-8")
        except OSError as exc:
            r["errors"].append({"file": name, "reason": str(exc)})
    return r


def import_from(path: str | Path) -> dict[str, Any]:
    return apply(preflight(path))


def receipt_line(r: dict[str, Any]) -> str:
    """The one-line receipt the Configuration row shows after an import."""
    if r.get("errors") and not r.get("counts"):
        return ""
    c = r.get("counts", {})
    parts = []
    if c.get("playlists"):
        parts.append(f"{c['playlists']} playlist{'s' if c['playlists'] != 1 else ''}")
    if c.get("overrides"):
        parts.append(f"{c['overrides']} override{'s' if c['overrides'] != 1 else ''}")
    if c.get("tags"):
        parts.append(f"{c['tags']} tag{'s' if c['tags'] != 1 else ''}")
    line = "Restored " + (", ".join(parts) if parts else "settings")
    held = c.get("overrides_held", 0)
    if held:
        line += f" · {held} waiting for wallpapers"
    if r.get("dropped"):
        line += f" · {len(r['dropped'])} dropped"
    return line
