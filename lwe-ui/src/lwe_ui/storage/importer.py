"""Workshop import pipeline (the settings Library page's detect/import surface, made real).

Steam drops subscribed items into WORKSHOP_DIR; this module finds the ones the library
does not know and runs the mechanical pass the settings page promises: type-classify,
color-correction derive, thumbnail resolve, dedup. The mechanical pass never skips; what
REVIEW_REQUIRED controls is only the item's landing state:

  review ON  (shipping default): the item is tagged `review` - it renders in the
             library's Review scope, never rotates (the rotation pool is state==good
             only), and waits for the card's approve / trash verdict.
  review OFF: the item is tagged `good` and enters rotation on the next rotation push.

STORAGE_POLICY decides the render source:
  copy:      the workshop dir is copied into WALLPAPERS_DIR/<id> (staged + renamed, so a
             half-copied import can never masquerade as a wallpaper); BG = the library dir.
             Survives unsubscribes.
  reference: nothing copies; wp/<id>.conf's BG points at the workshop dir. Saves disk,
             dies with an unsubscribe. Visibility comes from the tags row (the library
             grid includes `review` ids and resolves presence through BG, same as the
             bg!=id preset machinery).

Everything here is synchronous, stdlib-only, and safe to run off the GUI thread (the
bridge wraps it in a worker - a multi-GB copytree must never freeze the app).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .. import constants as C
from ..discovery import project
from ..discovery.project import derive_cc as _derive_cc
from . import meta, paths, settings, tags, wp


def _snapshot() -> dict:
    """One settings read per pass: per-key re-reads were O(N) file loads per scan and a
    mid-pass settings write could shear policy across items of one batch."""
    try:
        return dict(settings.load())
    except Exception:
        return {}


def _setting_from(cfg: dict, key: str, fallback):
    v = cfg.get(key)
    return v if v not in (None, "") else fallback


def _setting(key: str, fallback):
    return _setting_from(_snapshot(), key, fallback)


def _clean_title(title: str) -> str:
    """Strip control characters from third-party titles: the tags parser is
    line-based, so an embedded newline in a title could inject a fake `good` record and
    self-approve past the review gate (same threat model as the wid safety gate)."""
    return "".join(ch for ch in str(title) if ch.isprintable())[:200]


def _read_deps(proj: dict) -> list[str]:
    """Workshop dependency ids from project.json's `dependency` field (a single id
    string in every observed package; a list is tolerated). These are Wallpaper
    Engine PRESET publications: a `preset` property map over another item's scene,
    no payload of their own - they cannot render without the base item."""
    raw = proj.get("raw") or {}
    dep = raw.get("dependency")
    if not dep:
        return []
    deps = dep if isinstance(dep, list) else [dep]
    return [str(d) for d in deps if paths.is_safe_wid(str(d))]


def _has_own_payload(src: Path, proj: dict) -> bool:
    declared = str(proj.get("file") or "")
    wtype = proj.get("type") or ""
    if wtype == "scene":
        return (src / "scene.pkg").exists() or (src / "scene.json").exists()
    if declared:
        return (src / declared).exists()
    return False


def _looks_complete(src: Path, proj: dict) -> bool:
    """Refuse half-downloaded workshop trees: Steam writes items incrementally and a
    watch/interval pass can land mid-download. Complete = project.json parsed (a type or
    title exists) AND the declared payload is present (scenes: the .pkg or scene.json on
    disk - project.json's `file` says scene.json even when the payload is scene.pkg).
    A payload-less item DECLARING a dependency is a preset publication - complete by
    construction (its payload is the base item's), handled by the dependency paths."""
    raw = proj.get("raw") or {}
    if not raw:
        return False
    if _has_own_payload(src, proj):
        return True
    return bool(_read_deps(proj))


def workshop_dir() -> str:
    return str(_setting("WORKSHOP_DIR", "") or paths.detect_workshop_dir())


def _wallpapers_dir() -> str:
    return str(_setting("WALLPAPERS_DIR", "") or paths.default_wallpapers_dir())


def scan_new(cfg: dict | None = None) -> list[str]:
    """Workshop subdirs the library does not know: not in tags (any state), no library
    dir. Filtered by the wid safety gate (workshop content is third-party; a hostile dir
    name must never reach a shell-sourced file) and the completeness
    gate (half-downloaded trees are left for the next pass, never tagged)."""
    cfg = _snapshot() if cfg is None else cfg
    wdir = str(_setting_from(cfg, "WORKSHOP_DIR", "") or paths.detect_workshop_dir())
    lib = str(_setting_from(cfg, "WALLPAPERS_DIR", "") or paths.default_wallpapers_dir())
    try:
        known = tags.known_ids()
    except Exception:
        known = set()
    out: list[str] = []
    # BOTH pending roots: Steam's workshop tree and LWE's own manual root (the Advanced import).
    # A hand-added folder is a pending item in every way that matters - it surfaces as a Workshop
    # tile, benches, and commits - so it is discovered by the same pass rather than a parallel one.
    entries = []
    for root in (wdir, str(paths.manual_dir())):
        try:
            entries.extend(sorted(os.scandir(root), key=lambda e: e.name))
        except OSError:
            continue
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        wid = entry.name
        # hidden dirs are never workshop items (and .import-* is our own staging);
        # is_safe_wid alone does NOT exclude a leading dot (it allows dots for
        # names like "wall_2.0")
        if wid.startswith(".") or not paths.is_safe_wid(wid):
            continue
        if wid in known or os.path.isdir(os.path.join(lib, wid)):
            continue
        proj = project.read(entry.path)
        if not _looks_complete(Path(entry.path), proj):
            continue
        out.append(wid)
    return out


def import_one(wid: str, cfg: dict | None = None) -> dict:
    """Run the mechanical pass on one workshop item. Returns
    {"wid", "title", "type", "action"} where action is one of
    imported-review | imported-good | skipped-<reason>. Never raises."""
    cfg = _snapshot() if cfg is None else cfg
    wid = str(wid)
    if not paths.is_safe_wid(wid):
        return {"wid": wid, "title": "", "type": "", "action": "skipped-unsafe-id"}
    # per-item pending root: a hand-added folder lives in LWE's manual root, a Steam item in the
    # workshop tree. Falls back to the Steam root, so ids predating the manual root are unchanged.
    src = paths.pending_root_for(
        wid, _setting_from(cfg, "WORKSHOP_DIR", "") or paths.detect_workshop_dir()) / wid
    lib = Path(str(_setting_from(cfg, "WALLPAPERS_DIR", "") or paths.default_wallpapers_dir()))
    try:
        known = tags.known_ids()
    except Exception:
        known = set()
    # dedup: anything the library already knows (any state, incl. bad tombstones) or
    # that already has a dir is not imported again
    if wid in known or (lib / wid).is_dir():
        return {"wid": wid, "title": "", "type": "", "action": "skipped-duplicate"}
    if not src.is_dir():
        return {"wid": wid, "title": "", "type": "", "action": "skipped-missing-source"}

    proj = project.read(src)
    wtype = proj.get("type") or ""
    title = _clean_title(proj.get("title") or wid) or wid
    if not _looks_complete(src, proj):
        # NOT tagged: the next pass retries once the download finishes
        return {"wid": wid, "title": title, "type": wtype, "action": "skipped-incomplete"}
    # dependency paths (16e): a payload-less preset item renders THROUGH its base item.
    deps = _read_deps(proj)
    raw_dep = (proj.get("raw") or {}).get("dependency")
    if raw_dep and not deps and not _has_own_payload(src, proj):
        # every declared dep failed the wid safety gate: visible, never tagged (L2)
        return {"wid": wid, "title": title, "type": wtype, "action": "skipped-bad-dep"}
    if deps and not _has_own_payload(src, proj):
        if any(not _dep_present(d, cfg) for d in deps):
            # held import: the tile exists (review state, amber chip) but cannot run;
            # the resolve pass finishes the wiring hands-free when the base arrives.
            # ALL deps are stored (M4) - the modal surfaces the first missing one.
            return _import_held(wid, src, title, deps, cfg)
        for d in deps:
            _ensure_dep_imported(d, cfg)
        # the base must have actually landed (a failed copy on the ensure hop would
        # leave the preset wiring at a dir with no conf) - otherwise hold (H2 belt)
        try:
            landed = all(d in tags.known_ids() for d in deps)
        except Exception:
            landed = False
        if not landed:
            return _import_held(wid, src, title, deps, cfg)
        return _import_preset(wid, src, proj, title, deps, cfg)

    policy = str(_setting_from(cfg, "STORAGE_POLICY", "copy"))
    if policy == "copy":
        # staged copy + rename: a crash mid-copy leaves only a staging dir the next
        # scan ignores (leading dot = not a wid), never a half-wallpaper
        stage = lib / f".import-{wid}"
        try:
            lib.mkdir(parents=True, exist_ok=True)
            if stage.exists():
                shutil.rmtree(stage)
            shutil.copytree(src, stage)
            os.replace(stage, lib / wid)
        except OSError:
            try:
                if stage.exists():
                    shutil.rmtree(stage)
            except OSError:
                pass
            return {"wid": wid, "title": title, "type": wtype, "action": "skipped-copy-failed"}
        # bare wid, not the absolute path: the app resolves a bare
        # BG against WALLPAPERS_DIR, so relocating the library later cannot orphan
        # imported confs (an absolute path would pin the old location forever)
        bg = wid
    else:
        bg = str(src)

    d = {k: spec["default"] for k, spec in C.WP_SCHEMA.items()}
    d["props"] = {}
    if wtype in C.WALLPAPER_TYPES:
        d["TYPE"] = wtype
    d["BG"] = bg
    raw = proj.get("raw") or {}
    preset = raw.get("preset")
    d["CC"] = _derive_cc(preset if isinstance(preset, dict) else raw)
    gen = raw.get("general") if isinstance(raw.get("general"), dict) else {}
    d["AUDIO_REACTIVE"] = bool(gen.get("supportsaudioprocessing"))
    try:
        wp.save(wid, d)
    except Exception:
        return {"wid": wid, "title": title, "type": wtype, "action": "skipped-conf-failed"}

    review = bool(_setting_from(cfg, "REVIEW_REQUIRED", True))
    try:
        tags.set_state(wid, title, "review" if review else "good")
    except Exception:
        return {"wid": wid, "title": title, "type": wtype, "action": "skipped-tag-failed"}
    return {"wid": wid, "title": title, "type": wtype,
            "action": "imported-review" if review else "imported-good"}


def _dep_present(dep: str, cfg: dict) -> bool:
    """The base is USABLE, not merely a directory (a bare dir check let
    presets wire through half-downloaded or payload-less bases and reach rotation
    unrenderable): either already imported with a conf whose BG resolves, or an
    on-disk tree whose OWN payload is complete (so importing it now will succeed).
    A payload-less preset dir never counts until it is itself imported - which also
    makes dependency cycles structurally inert (each cycle member holds on the
    other; nothing recurses)."""
    lib = str(_setting_from(cfg, "WALLPAPERS_DIR", "") or paths.default_wallpapers_dir())
    try:
        known = dep in tags.known_ids()
    except Exception:
        known = False
    if known:
        # a HELD preset is known and its placeholder conf resolves (it points at its
        # own payload-less dir) - it is not a usable base until it itself resolves
        try:
            if meta.get(dep).get("depMissing"):
                return False
        except Exception:
            pass
        try:
            bg = str(wp.load(dep).get("BG") or "")
        except Exception:
            bg = ""
        if bg:
            cand = bg if os.path.isabs(bg) else os.path.join(lib, bg)
            if os.path.isdir(cand):
                return True
        return False   # known but unresolvable (tombstoned reference, broken conf)
    ws = str(_setting_from(cfg, "WORKSHOP_DIR", "") or paths.detect_workshop_dir())
    for root in (os.path.join(lib, dep), os.path.join(ws, dep)):
        if os.path.isdir(root):
            proj = project.read(root)
            if (proj.get("raw") or {}) and _has_own_payload(Path(root), proj):
                return True
    return False


_DEP_IMPORT_STACK: set = set()   # H1 belt: presence semantics already prevent
                                 # recursion; this makes a hostile graph inert anyway


def _ensure_dep_imported(dep: str, cfg: dict) -> None:
    """A preset's base item imports FIRST (same pass, same snapshot) so the preset's
    conf can point at the base's BG. The dedup guard makes this idempotent; the stack
    guard makes it cycle-proof."""
    if dep in _DEP_IMPORT_STACK:
        return
    try:
        known = tags.known_ids()
    except Exception:
        known = set()
    if dep not in known:
        _DEP_IMPORT_STACK.add(dep)
        try:
            import_one(dep, cfg)
        finally:
            _DEP_IMPORT_STACK.discard(dep)


def _copy_or_reference(wid: str, src: Path, cfg: dict) -> str | None:
    """The storage-policy move shared by every import path. Returns the BG value
    (bare wid for copy, absolute source for reference), None on a failed copy."""
    lib = Path(str(_setting_from(cfg, "WALLPAPERS_DIR", "") or paths.default_wallpapers_dir()))
    if str(_setting_from(cfg, "STORAGE_POLICY", "copy")) != "copy":
        return str(src)
    stage = lib / f".import-{wid}"
    try:
        lib.mkdir(parents=True, exist_ok=True)
        if stage.exists():
            shutil.rmtree(stage)
        shutil.copytree(src, stage)
        os.replace(stage, lib / wid)
    except OSError:
        try:
            if stage.exists():
                shutil.rmtree(stage)
        except OSError:
            pass
        return None
    return wid


def _preset_props(raw_preset: dict) -> dict:
    """The preset block minus the wec_* grade keys (those become CC) and minus
    non-scalar noise (_d0 nulls): the base item's property overlay."""
    out = {}
    for k, v in (raw_preset or {}).items():
        if k.startswith("wec_") or v is None or isinstance(v, (dict, list)):
            continue
        # engine-facing spellings: bools lowercase (str(False) would hand the engine
        # "False"); everything else the conf layer stringifies as-is
        out[str(k)] = ("true" if v else "false") if isinstance(v, bool) else str(v)
    return out


def _wire_preset_conf(wid: str, proj: dict, dep: str, cfg: dict) -> bool:
    """Point the preset's conf THROUGH its base: BG/TYPE from the dep, CC + props from
    the preset block. The dep is expected imported (its conf carries the policy-correct
    BG); an on-disk-only dep falls back to its absolute path."""
    raw = proj.get("raw") or {}
    preset = raw.get("preset") if isinstance(raw.get("preset"), dict) else {}
    d = {k: spec["default"] for k, spec in C.WP_SCHEMA.items()}
    try:
        dep_conf = wp.load(dep)
    except Exception:
        dep_conf = {}
    bg = str(dep_conf.get("BG") or "")
    if not bg:
        ws = str(_setting_from(cfg, "WORKSHOP_DIR", "") or paths.detect_workshop_dir())
        lib = str(_setting_from(cfg, "WALLPAPERS_DIR", "") or paths.default_wallpapers_dir())
        bg = os.path.join(lib, dep) if os.path.isdir(os.path.join(lib, dep)) \
            else os.path.join(ws, dep)
    d["BG"] = bg
    # a preset renders its BASE, so audio support is the base's declaration - inherit the
    # dep conf's (correct post-migration) value rather than re-deriving from disk
    d["AUDIO_REACTIVE"] = bool(dep_conf.get("AUDIO_REACTIVE"))
    dtype = str(dep_conf.get("TYPE") or "")
    if not dtype:
        from ..discovery import project as _project
        for root in (os.path.join(str(_setting_from(cfg, "WALLPAPERS_DIR", "")
                                      or paths.default_wallpapers_dir()), dep),
                     os.path.join(str(_setting_from(cfg, "WORKSHOP_DIR", "")
                                      or paths.detect_workshop_dir()), dep)):
            if os.path.isdir(root):
                dtype = _project.read(root).get("type") or ""
                if dtype:
                    break
    if dtype in C.WALLPAPER_TYPES:
        d["TYPE"] = dtype
    d["CC"] = _derive_cc(preset)
    d["props"] = _preset_props(preset)
    try:
        wp.save(wid, d)
        return True
    except Exception:
        return False


def _dep_display_name(dep: str, cfg: dict) -> str:
    """Best-effort LOCAL name for the modal well; empty when the dep is nowhere on
    disk (no network calls - the modal falls back to the bare id)."""
    from ..discovery import project as _project
    for base in (str(_setting_from(cfg, "WALLPAPERS_DIR", "") or paths.default_wallpapers_dir()),
                 str(_setting_from(cfg, "WORKSHOP_DIR", "") or paths.detect_workshop_dir())):
        root = os.path.join(base, dep)
        if os.path.isdir(root):
            t = _clean_title(_project.read(root).get("title") or "")
            if t:
                return t[:80]
    try:
        for r in tags.load():
            if r.get("id") == dep and r.get("title"):
                return _clean_title(str(r["title"]))[:80]
    except Exception:
        pass
    return ""


def _import_held(wid: str, src: Path, title: str, missing: list[str], cfg: dict) -> dict:
    """The missing-dependency hold: the item lands in review with the missing-dep marker so the tile
    and modal exist; state is review REGARDLESS of REVIEW_REQUIRED (an item that
    cannot render must never auto-graduate into rotation). `missing` carries ALL
    declared deps (M4): the resolve gate re-checks presence per dep anyway, and a
    consistent stored set keeps depInfo/modal coherent."""
    if _copy_or_reference(wid, src, cfg) is None:
        return {"wid": wid, "title": title, "type": "", "action": "skipped-copy-failed"}
    d = {k: spec["default"] for k, spec in C.WP_SCHEMA.items()}
    d["BG"] = str(src)   # placeholder; the resolve pass rewires it through the base
    try:
        wp.save(wid, d)
    except Exception:
        return {"wid": wid, "title": title, "type": "", "action": "skipped-conf-failed"}
    try:
        tags.set_state(wid, title, "review")
    except Exception:
        return {"wid": wid, "title": title, "type": "", "action": "skipped-tag-failed"}
    meta.update(wid, {"depMissing": True, "depWid": " ".join(missing),
                      "depName": _dep_display_name(missing[0], cfg)})
    return {"wid": wid, "title": title, "type": "", "action": "imported-missing-dep"}


def _import_preset(wid: str, src: Path, proj: dict, title: str,
                   deps: list[str], cfg: dict) -> dict:
    """A preset whose base is available: normal landing rules, conf wired through the
    base item."""
    if _copy_or_reference(wid, src, cfg) is None:
        return {"wid": wid, "title": title, "type": "", "action": "skipped-copy-failed"}
    if not _wire_preset_conf(wid, proj, deps[0], cfg):
        return {"wid": wid, "title": title, "type": "", "action": "skipped-conf-failed"}
    review = bool(_setting_from(cfg, "REVIEW_REQUIRED", True))
    try:
        tags.set_state(wid, title, "review" if review else "good")
    except Exception:
        return {"wid": wid, "title": title, "type": "", "action": "skipped-tag-failed"}
    meta.update(wid, {"depMissing": False, "depWid": " ".join(deps),
                      "depName": _dep_display_name(deps[0], cfg)})
    return {"wid": wid, "title": title, "type": "",
            "action": "imported-review" if review else "imported-good"}


def resolve_missing_deps(cfg: dict | None = None) -> int:
    """The hands-free completion: every held item whose base has since arrived
    gets its base imported and its conf rewired; the marker clears, the chip and modal
    follow. Returns the number resolved."""
    cfg = _snapshot() if cfg is None else cfg
    resolved = 0
    try:
        held = [(k, v) for k, v in meta.load().items()
                if isinstance(v, dict) and v.get("depMissing")]
    except Exception:
        return 0
    ws = Path(str(_setting_from(cfg, "WORKSHOP_DIR", "") or paths.detect_workshop_dir()))
    lib = Path(str(_setting_from(cfg, "WALLPAPERS_DIR", "") or paths.default_wallpapers_dir()))
    try:
        review_now = tags.review_ids()
    except Exception:
        review_now = set()
    for wid, m in held:
        if wid not in review_now:
            # trashed/removed while held (M1): clear the stale marker, never rewire
            # a conf for a dead wid, never inflate the resolved count
            meta.update(wid, {"depMissing": False})
            continue
        deps = [d for d in str(m.get("depWid", "")).split() if paths.is_safe_wid(d)]
        if not deps or not all(_dep_present(d, cfg) for d in deps):
            continue
        for d in deps:
            _ensure_dep_imported(d, cfg)
        src = lib / wid if (lib / wid).is_dir() else ws / wid
        from ..discovery import project as _project
        proj = _project.read(str(src)) if src.is_dir() else {"raw": {}}
        if not _wire_preset_conf(wid, proj, deps[0], cfg):
            continue
        meta.update(wid, {"depMissing": False,
                          "depName": _dep_display_name(deps[0], cfg)})
        resolved += 1
    return resolved


def _dir_has_payload(d: str) -> bool:
    """Any renderable payload in a directory (the crash test: --bg on a dir with none
    dies). Authoritative via the dir's OWN project.json (its `file`/type says the real
    payload name - a video ships as e.g. ocean.webm, not literal video.mp4, which the
    old fixed-name list missed and so clobbered a hand-fixed preset-of-a-video). Falls
    back to a payload glob when project.json is unreadable."""
    if not d or not os.path.isdir(d):
        return False
    try:
        if _has_own_payload(Path(d), project.read(d)):
            return True
    except Exception:
        pass
    # unreadable/absent project.json: accept any concrete payload by extension
    for name in os.listdir(d):
        low = name.lower()
        if low in ("scene.pkg", "scene.json") or low.endswith(
                (".mp4", ".webm", ".m4v", ".mkv", ".html", ".htm")):
            return True
    return False


def repair_preset_confs(cfg: dict | None = None) -> list[str]:
    """One-time self-heal for preset imports that predate this conf-wiring behavior.
    A preset whose BG points at a payload-less dir CRASHES the engine (--bg on nothing
    to render); rewire it through its base the way newer imports do. Confs that ALREADY
    render (BG on a real payload - e.g. a hand-fixed preset) are left untouched, so a
    user's manual CC/props tweaks are never clobbered; the render/identity split in the
    model repairs their stolen-identity symptom without touching the conf. Returns the
    repaired wids."""
    cfg = _snapshot() if cfg is None else cfg
    ws = str(_setting_from(cfg, "WORKSHOP_DIR", "") or paths.detect_workshop_dir())
    lib = str(_setting_from(cfg, "WALLPAPERS_DIR", "") or paths.default_wallpapers_dir())
    repaired: list[str] = []
    try:
        rows = tags.load()
    except Exception:
        return []
    for r in rows:
        wid = r.get("id") or ""
        if not paths.is_safe_wid(wid):
            continue
        own = os.path.join(ws, wid)
        if not os.path.isdir(own):
            continue
        proj = project.read(own)
        deps = _read_deps(proj)
        if not deps or _has_own_payload(Path(own), proj):
            continue
        try:
            bg = str(wp.load(wid).get("BG", "") or "")
        except Exception:
            continue
        bg_dir = bg if os.path.isabs(bg) else os.path.join(lib, bg)
        if _dir_has_payload(bg_dir):
            continue   # already renders - never clobber a working (possibly hand-tuned) conf
        if not all(_dep_present(d, cfg) for d in deps):
            continue   # base unavailable - the resolve pass will hold/repair it later
        for d in deps:
            _ensure_dep_imported(d, cfg)
        if _wire_preset_conf(wid, proj, deps[0], cfg):
            meta.update(wid, {"depMissing": False, "depWid": " ".join(deps),
                              "depName": _dep_display_name(deps[0], cfg)})
            repaired.append(wid)
    return repaired


def run_scan_and_import() -> dict:
    """One full detection pass: scan + import everything new under ONE settings
    snapshot, then the dependency resolve pass (held presets whose base just arrived
    complete hands-free). Returns {"found", "imported", "resolved", "results"}.
    Synchronous - call off-thread."""
    cfg = _snapshot()
    wids = scan_new(cfg)
    # bases before their presets: _ensure_dep_imported would import them anyway, but
    # then their own loop slot would read skipped-duplicate and the count would lie
    dep_first: list[str] = []
    ws = str(_setting_from(cfg, "WORKSHOP_DIR", "") or paths.detect_workshop_dir())
    for w in wids:
        from ..discovery import project as _project
        for d in _read_deps(_project.read(os.path.join(ws, w))):
            if d in wids and d not in dep_first:
                dep_first.append(d)
    wids = dep_first + [w for w in wids if w not in dep_first]
    results = []
    for w in wids:
        try:
            results.append(import_one(w, cfg))
        except Exception:
            # never let one poisoned item abort the pass for every legit sibling
            results.append({"wid": w, "title": "", "type": "", "action": "skipped-error"})
    imported = sum(1 for r in results if r["action"].startswith("imported"))
    # fixpoint: resolving Q can unblock P->Q->B chains within the same pass
    resolved = 0
    while True:
        n = resolve_missing_deps(cfg)
        resolved += n
        if n == 0:
            break
    return {"found": len(wids), "imported": imported, "resolved": resolved,
            "results": results}
