"""lwe list, workshop and scan print the library catalog and only read; lwe compress builds the
engine's texture cache; lwe add brings wallpapers into the pool and lwe untrash lets trashed ones be
imported again; lwe bench runs one wallpaper in a test window of its own. None of them sends anything
to the running engine."""
from __future__ import annotations

import json

from .. import DONE, REFUSED, USAGE, Context
from ..registry import Verb

_TYPES = ("scene", "video", "web")


def _refuse(ctx: Context, message: str) -> int:
    ctx.error(message, "lwe: " + message)
    return USAGE


def _rows() -> tuple[list, int]:
    from ...library import catalog

    return catalog.wallpaper_rows()


def _left_out(ctx: Context, unsafe: int) -> None:
    if unsafe:
        print(f"lwe: {unsafe} folders were left out because their names use characters other than "
              "letters, digits, dot, underscore and hyphen", file=ctx.err)


def _print(ctx: Context, rows: list, mark: str, marked_state: str) -> None:
    """One line per row, or under -j one JSON array; `mark` ends the lines of rows in marked_state."""
    if ctx.json:
        print(json.dumps([{"n": r.n, "id": r.id, "title": r.title, "alias": r.alias, "type": r.type,
                           "state": r.state} for r in rows], ensure_ascii=False, separators=(",", ":")),
              file=ctx.out)
        return
    width = len(str(max((r.n for r in rows), default=0)))
    for r in rows:
        line = f"{r.n:>{width}}  {r.title} ({r.id})"
        if r.alias:
            line += f"  @{r.alias}"
        if r.state == marked_state:
            line += f"  {mark}"
        print(line, file=ctx.out)


def _list(ctx: Context, args: list[str]) -> int:
    if len(args) > 1 or (args and args[0] not in _TYPES):
        return _refuse(ctx, "list takes scene, video or web")
    rows, unsafe = _rows()
    pool = [r for r in rows if r.state in ("pool", "missing") and (not args or r.type == args[0])]
    _print(ctx, pool, "files missing", "missing")
    _left_out(ctx, unsafe)
    return DONE


def _workshop(ctx: Context, args: list[str]) -> int:
    if args:
        return _refuse(ctx, "workshop takes no words")
    rows, unsafe = _rows()
    _print(ctx, [r for r in rows if r.state in ("waiting", "download")], "waiting for review", "waiting")
    _left_out(ctx, unsafe)
    return DONE


def _scan(ctx: Context, args: list[str]) -> int:
    if args:
        return _refuse(ctx, "scan takes no words")
    rows, unsafe = _rows()
    new = sum(1 for r in rows if r.state == "download")
    waiting = sum(1 for r in rows if r.state == "waiting")
    if ctx.json:
        print(json.dumps({"new": new, "waiting": waiting}, separators=(",", ":")), file=ctx.out)
    else:
        print(f"{new} new Workshop downloads, {waiting} waiting for review; lwe workshop lists them.",
              file=ctx.out)
    _left_out(ctx, unsafe)
    return DONE


def _compress(ctx: Context, args: list[str]) -> int:
    options = [a for a in args if a.startswith("-")]
    words = [a for a in args if not a.startswith("-")]
    if options not in ([], ["--all"]) or bool(words) == bool(options):
        return _refuse(ctx, "compress takes wallpapers or --all")
    from ... import texcomp
    from ...library import catalog, compress
    from .. import select

    rows = catalog.wallpaper_rows()[0]
    if options:
        named = [r for r in rows if r.state in ("pool", "missing")]
    else:
        try:
            picks = select.wallpapers(words)
        except select.PickError as exc:
            return select.report(ctx, exc)
        for p in picks:
            if p.source == "trashed":
                message = f"{p.title} ({p.ui_id}) is in the trash; untrash it first"
                ctx.error(message, "lwe: " + message)
                return REFUSED
        by_id = {r.id: r for r in rows}
        named = [by_id.get(p.ui_id) or catalog.Row(0, p.ui_id, p.title, p.alias, "", p.source, False)
                 for p in picks]
    if not texcomp.shim_available():
        message = f"the texture encoder {texcomp.shim_path()} is missing; nothing was compressed"
        ctx.error(message, "lwe: " + message)
        return REFUSED
    results = []
    for row in named:
        result = compress.compress_one(row)
        results.append(result)
        if not ctx.json:
            print(compress.result_line(row, result), file=ctx.out, flush=True)
    if ctx.json:
        print(json.dumps({
            "wallpapers": [{"id": row.id, "title": row.title, "result": r.kind, "bytes_before": r.before,
                            "bytes_after": r.after, "failed": r.failed, "disk_bytes": r.disk}
                           for row, r in zip(named, results)],
            "total": {"compressed": sum(1 for r in results if compress.written(r)), "named": len(results),
                      "bytes_before": sum(r.before for r in results),
                      "bytes_after": sum(r.after for r in results),
                      "disk_bytes": sum(r.disk for r in results)},
        }, ensure_ascii=False, separators=(",", ":")), file=ctx.out)
    elif len(results) > 1:
        print(compress.total_line(results), file=ctx.out)
    return DONE


_REASONS = {"skipped-copy-failed": "the copy failed",
            "skipped-conf-failed": "its settings file could not be written",
            "skipped-tag-failed": "the library list could not be written",
            "skipped-incomplete": "the download is incomplete"}


def _download_deps(wid: str) -> tuple[list[str], bool]:
    """A download's declared bases and whether it has a payload of its own."""
    from ...discovery import project
    from ...storage import importer, paths

    folder = paths.pending_root_for(wid, importer.workshop_dir()) / wid
    proj = project.read(folder)
    return importer._read_deps(proj), importer._has_own_payload(folder, proj)


def _held_bases(wid: str) -> list[str] | None:
    """The bases a held import waits for, or None when it is not held."""
    from ...storage import meta, paths

    entry = meta.get(wid)
    if not entry.get("depMissing"):
        return None
    return [d for d in str(entry.get("depWid", "")).split() if paths.is_safe_wid(d)]


def _base_not_here(pick, cfg: dict) -> str:
    """The first base a preset pick needs that is not here, or ""."""
    from ...storage import importer

    bases: list[str] = []
    if pick.source == "waiting":
        bases = _held_bases(pick.ui_id) or []
    elif pick.source == "download":
        deps, own = _download_deps(pick.ui_id)
        bases = [] if own else deps
    return next((b for b in bases if not importer._dep_present(b, cfg)), "")


def _failed(facts: dict, reason: str) -> tuple[str, dict]:
    return f"not added: {reason}", {**facts, "result": "failed", "reason": reason}


def _add_one(row, cfg: dict) -> tuple[str, dict]:
    """Bring one wallpaper into the pool; returns its line's text and facts."""
    from ...library import actions, catalog, compress
    from ...storage import importer, meta, tags, wizard

    facts = {"id": row.id, "title": row.title}
    if row.state in ("pool", "missing"):
        return "already in the pool", {**facts, "result": "already"}
    before = tags.known_ids()
    title = row.title
    if row.state == "download":
        deps, own = _download_deps(row.id)
        folder = catalog.render_dir(deps[0]) if deps and not own else None
        result = compress.compress_one(row, folder=folder)
        done = importer.import_one(row.id)
        if done["action"] not in ("imported-review", "imported-good"):
            return _failed(facts, _REASONS.get(done["action"], done["action"]))
        title = done["title"] or row.title
        head, kind = "imported into the pool", "imported"
    else:
        held = _held_bases(row.id)
        deps = held or []
        if held is not None:
            if all(importer._dep_present(d, cfg) for d in deps):
                importer.resolve_missing_deps()
            if meta.get(row.id).get("depMissing"):
                return _failed(facts, _REASONS["skipped-conf-failed"])
        result = compress.compress_one(row)
        head, kind = "in the pool", "approved"
    actions.approve(row.id, title, wizard.approved_untested(where="workshop"))
    text = f"{head}; {compress.result_text(row, result)}"
    tagged = {r.get("id"): r for r in tags.load()}
    bases = []
    for d in deps:
        if d in before or d not in tagged:
            continue
        waiting = tagged[d].get("state") == "review"
        base_title = tagged[d].get("title") or d
        text += (f"; its base {base_title} ({d}) was imported and "
                 + ("waits for review" if waiting else "is in the pool"))
        bases.append({"id": d, "title": base_title, "state": "waiting" if waiting else "pool"})
    return text, {**facts, "result": kind,
                  "compress": {"result": result.kind, "bytes_before": result.before,
                               "bytes_after": result.after, "failed": result.failed,
                               "disk_bytes": result.disk},
                  "bases": bases}


def _add(ctx: Context, args: list[str]) -> int:
    if not args or any(a.startswith("-") for a in args):
        return _refuse(ctx, "add takes wallpapers")
    from ...library import catalog, compress
    from ...storage import importer
    from .. import select

    try:
        picks = select.wallpapers(args)
    except select.PickError as exc:
        return select.report(ctx, exc)
    cfg = importer._snapshot()
    for p in picks:
        label = f"{p.title} ({p.ui_id})"
        if p.source == "trashed":
            message = f"{label} is in the trash; untrash it first"
        elif p.source == "screen":
            message = f"{label} is not in the pool or the Workshop list"
        else:
            base = _base_not_here(p, cfg)
            if not base:
                continue
            message = f"{label} needs its base {base}, which is not here"
        ctx.error(message, "lwe: " + message)
        return REFUSED
    results = []
    for p in picks:
        rows = {r.id: r for r in catalog.wallpaper_rows()[0]}
        row = rows.get(p.ui_id) or catalog.Row(0, p.ui_id, p.title, p.alias, "", p.source, False)
        text, facts = _add_one(row, cfg)
        results.append(facts)
        if not ctx.json:
            print(compress.row_line(row, text), file=ctx.out, flush=True)
    if ctx.json:
        print(json.dumps({"results": results, "receipt": None}, ensure_ascii=False,
                         separators=(",", ":")), file=ctx.out)
    return REFUSED if any(f["result"] == "failed" for f in results) else DONE


def _untrash(ctx: Context, args: list[str]) -> int:
    options = [a for a in args if a.startswith("-")]
    words = [a for a in args if not a.startswith("-")]
    if options not in ([], ["--all"]) or (words and options):
        return _refuse(ctx, "untrash takes wallpapers or --all")
    from ...library import actions, catalog, compress
    from .. import select

    if not args:
        rows = catalog.trash_rows()
        if ctx.json:
            print(json.dumps([{"n": r.n, "id": r.id, "title": r.title, "files": r.files} for r in rows],
                             ensure_ascii=False, separators=(",", ":")), file=ctx.out)
            return DONE
        width = len(str(max((r.n for r in rows), default=0)))
        for r in rows:
            gone = "" if r.files else "  (files gone)"
            print(f"{r.n:>{width}}  {r.title} ({r.id}){gone}", file=ctx.out)
        return DONE
    if options:
        named = catalog.trash_rows()
    else:
        try:
            picks = select.wallpapers(words, domain="trash")
        except select.PickError as exc:
            return select.report(ctx, exc)
        by_id = {r.id: r for r in catalog.trash_rows()}
        named = [by_id.get(p.ui_id) or catalog.Row(0, p.ui_id, p.title, p.alias, "", "trashed", True)
                 for p in picks]
    results = []
    for row in named:
        actions.untrash(row.id)
        results.append({"id": row.id, "title": row.title, "files": row.files})
        if not ctx.json:
            text = "can be imported again"
            if not row.files:
                text += " (its files are gone; a new download comes back in)"
            print(compress.row_line(row, text), file=ctx.out, flush=True)
    if ctx.json:
        print(json.dumps({"results": results, "receipt": None}, ensure_ascii=False,
                         separators=(",", ":")), file=ctx.out)
    return DONE


_ENDINGS = {"closed": "the window was closed (exit code 0)", "stopped": "stopped by lwe"}


def _bench(ctx: Context, args: list[str]) -> int:
    if len(args) != 1 or args[0].startswith("-"):
        return _refuse(ctx, "bench takes one wallpaper")
    import os
    import subprocess

    from ... import placement
    from ...engine import daemon_unit
    from ...library import benchrun, catalog
    from ...storage import paths, settings
    from .. import select

    try:
        pick = select.wallpaper(args[0])
    except select.PickError as exc:
        return select.report(ctx, exc)
    label = f"{pick.title} ({pick.ui_id})"
    engine = folder = ""
    if pick.source == "trashed":
        message = f"{label} is in the trash; untrash it first"
    elif not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        message = "bench opens a window; no graphical session was found"
    elif not (engine := daemon_unit.resolve_engine_bin()):
        message = "the engine program was not found"
    elif not (folder := catalog.render_dir(pick.ui_id)):
        message = f"{label}: its files are missing"
    else:
        message = ""
    if message:
        ctx.error(message, "lwe: " + message)
        return REFUSED
    assets = str(settings.load().get("ASSETS_DIR") or paths.default_assets_dir())
    geometry = placement.window_geometry("A", placement.layout()) or "0x0x1280x720"
    argv = benchrun.bench_argv(engine, assets, geometry, folder, socket=False)
    log_path = paths.command_bench_log_file()

    def launch(*popen_args, **popen_kwargs) -> subprocess.Popen:
        proc = subprocess.Popen(*popen_args, **popen_kwargs)
        if not ctx.json:
            print(f"Opened a test window for {label}; close it to see the summary.", file=ctx.out, flush=True)
        return proc

    summary = benchrun.run(argv, dict(os.environ), log_path, pick.title, pick.ui_id, launcher=launch)
    if ctx.json:
        print(json.dumps({
            "id": pick.ui_id, "title": pick.title,
            "first_frame_s": None if summary.first_frame_s is None else round(summary.first_frame_s, 3),
            "ran_s": round(summary.ran_s, 3), "ended": summary.ended, "exit_code": summary.exit_code,
            "signal": summary.signal, "fatal_line": summary.fatal_line, "log": str(log_path),
        }, ensure_ascii=False, separators=(",", ":")), file=ctx.out)
    else:
        first = "none" if summary.first_frame_s is None else f"after {summary.first_frame_s:.1f} s"
        ending = _ENDINGS.get(summary.ended) or (f"signal {summary.signal}" if summary.ended == "signal"
                                                 else f"exit code {summary.exit_code}")
        print(f"First frame: {first}", file=ctx.out)
        print(f"Ran {summary.ran_s:.0f} s; {ending}", file=ctx.out)
        if summary.fatal_line is not None:
            print(f"Fatal line: {summary.fatal_line}", file=ctx.out)
        print(f"Log: {log_path}", file=ctx.out)
    return DONE if summary.first_frame_s is not None and summary.fatal_line is None else REFUSED


VERBS = (
    Verb("list", _list, "Your library, numbered by title, with each wallpaper's id and alias.", "library"),
    Verb("workshop", _workshop, "Workshop downloads that are not in your pool yet, numbered, including ones "
         "waiting for review.", "library"),
    Verb("scan", _scan, "Looks for new Workshop downloads now.", "library"),
    Verb("compress", _compress, "Builds the compressed textures that make wallpapers load faster and use "
         "less video memory.", "library"),
    Verb("add", _add, "Brings wallpapers into the pool: checks them, compresses them and registers them.",
         "library"),
    Verb("untrash", _untrash, "Lifts that block so a wallpaper can be imported again; it does not bring "
         "deleted files back.", "library"),
    Verb("bench", _bench, "Opens a test window beside your wallpaper and prints its log summary when you "
         "close it.", "library"),
)
