"""The single commit gate + reject.

Commit is the **only** operation that moves an item forward in the lifecycle. It is one function
with two branches differing in whether the item is *entering* the library (first-commit) or is
*already in it* (re-commit). The two branches are deliberately authored as one function so they
cannot drift.

NO PENDING STORE. There is no draft buffer to promote any more: the bench builds
and tunes `wp/<id>.conf` directly, so an approved item ships with its conf exactly as it sits at
approval. What is left for commit to do is the publish and the tag.

  first-commit  (source == "pending"):  validate source -> copy {project.json, payload, preview.*}
      (NEVER shaders/) into the library via an atomic dir publish -> rewrite the conf's BG from
      the workshop path to the library reference -> tag good -> build obj/prop indexes
      (tolerated) -> resume the engine. ANY copy/validate failure leaves the item
      pending (no partial publish, no tag).
  re-commit     (source == "good"):      a NO-OP promote. The conf being edited IS the live conf,
      so there is nothing to promote and nothing to delete; it only resumes the engine (the
      caller re-pushes the rotation set).

reject: tag bad, resume the engine. Copies nothing; identical from tray/bench.

LIVE-MACHINE SAFETY: the content copy is parameterized by `workshop_dir` / `wallpapers_dir`, so
callers (and self-tests) target tempfile trees. NOTE: the obj/prop index WRITE target follows
`XDG_STATE_HOME` (paths.objindex_file/propindex_file under state_dir), not `wallpapers_dir` - a
self-test that wants full isolation must also point XDG_STATE_HOME at its temp tree.
"""
from __future__ import annotations

import glob
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from . import bench_courier
from .storage import atomic, paths, tags, wp

# Files copied into the library on first-commit. shaders/ is deliberately NOT among them
# (6.4.3 v2 / 2.8 - shaders are regenerated, never published).
_PROJECT_JSON = "project.json"

_VALID_SOURCES = ("pending", "good")


def _safe_rel(name: str) -> bool:
    """A payload/preview name that stays inside its dir: not absolute and no `..` escape.

    project.json is third-party Steam content, so a crafted `file` like `../x` or `/etc/x` must
    not be allowed to write outside the per-id staged/published dir (path-traversal guard).
    """
    if not name or os.path.isabs(name):
        return False
    norm = os.path.normpath(name)
    return not (norm == ".." or norm.startswith(".." + os.sep) or os.path.isabs(norm))


def _find_previews(src_dir: Path) -> list[Path]:
    """Every `preview.*` file directly in `src_dir` (sorted, regular files only)."""
    out: list[Path] = []
    for match in sorted(glob.glob(os.path.join(str(src_dir), "preview.*"))):
        p = Path(match)
        if p.is_file():
            out.append(p)
    return out


def _resolve_payloads(src_dir: Path) -> list[str]:
    """On-disk payload file(s) to copy into the library. Returns relative names present on disk.

    For VIDEO/WEB, project.json.file names the actual on-disk file (e.g. `foo.mp4`). For a PACKED
    SCENE, project.json.file is `scene.json` - but that scene.json lives *inside* `scene.pkg`, so
    the real on-disk payload is `scene.pkg` (and/or a loose `scene.json`/`gifscene.json` if the
    wallpaper was shipped unpacked). So: take the declared file if it is actually on disk, then add
    the packed-scene payloads that exist. (A naive file==on-disk-name assumption is false for
    packed scenes - every scene ingest would otherwise fail validation.)
    """
    raw = atomic.read_json(src_dir / _PROJECT_JSON, default=None)
    declared = raw.get("file") if isinstance(raw, dict) else None
    found: list[str] = []
    if isinstance(declared, str) and declared and _safe_rel(declared) and (src_dir / declared).is_file():
        found.append(declared)
    for cand in ("scene.pkg", "scene.json", "gifscene.json"):
        if cand not in found and (src_dir / cand).is_file():
            found.append(cand)
    return found


def _first_commit(
    wid: str,
    *,
    title: str,
    workshop_dir: Path,
    wallpapers_dir: Path,
) -> dict[str, Any]:
    """First-commit branch: validate + publish from the workshop tree, then promote/tag/index."""
    src_dir = workshop_dir / wid
    report: dict[str, Any] = {"ok": False, "wid": wid, "source": "pending"}

    if not src_dir.is_dir():
        report["reason"] = f"workshop dir missing: {src_dir}"
        return report
    if not (src_dir / _PROJECT_JSON).is_file():
        report["reason"] = "source missing project.json"
        return report
    payloads = _resolve_payloads(src_dir)
    if not payloads:
        report["reason"] = "no renderable payload on disk (project.json.file / scene.pkg)"
        return report
    previews = _find_previews(src_dir)
    if not previews:
        report["reason"] = "source missing preview.*"
        return report

    # 2. copy ONLY {project.json, payload, preview.*} into a temp dir, then atomically publish it
    #    into the library. On ANY failure: drop the temp dir, return ok:False, write no tags.
    dest_dir = wallpapers_dir / wid
    wallpapers_dir.mkdir(parents=True, exist_ok=True)

    # de-dupe the copy set by relative name (a file that is both the payload AND a preview.* must
    # not be copied/listed twice). _safe_rel already constrains every name to stay inside dest.
    to_copy: list[tuple[Path, str]] = []
    seen: set[str] = set()
    def _add(src: Path, rel: str) -> None:
        if rel not in seen:
            seen.add(rel)
            to_copy.append((src, rel))
    _add(src_dir / _PROJECT_JSON, _PROJECT_JSON)
    for payload in payloads:          # for a packed scene this is scene.pkg; may sit in a subdir
        _add(src_dir / payload, payload)
    for prev in previews:
        _add(prev, prev.name)

    staged = tempfile.mkdtemp(prefix=f".{wid}.publish.", dir=str(wallpapers_dir))
    try:
        staged_p = Path(staged)
        for src, rel in to_copy:
            dst = staged_p / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        atomic.atomic_publish_dir(staged_p, dest_dir)
    except (OSError, shutil.Error) as exc:
        shutil.rmtree(staged, ignore_errors=True)
        report["reason"] = f"publish failed: {exc}"
        return report  # stays pending; no tag written

    # 3-4. rewrite BG (workshop path -> library reference) + tag good, durably. This is the one
    #      job the pending branch still has over the conf (L-19): the bench built it pointing at
    #      Steam's tree so the test could render, and the library reference is what ships. If
    #      either step fails, roll back the publish so the item stays pending and retryable
    #      (no untagged on-disk orphan).
    bg = str(dest_dir)
    try:
        prior_bg = str(wp.load(wid).get("BG", "") or "")
    except Exception:  # noqa: BLE001 - a conf we cannot read has no BG to put back
        prior_bg = ""
    try:
        wp.update_set(wid, {"BG": bg})
        tags.set_state(wid, title, "good")
    except Exception as exc:  # noqa: BLE001 - any promote/tag failure must not partially commit
        shutil.rmtree(dest_dir, ignore_errors=True)
        # put BG back where it pointed, and NOTHING else. Deleting the conf here would have
        # been safe while a draft held the real work; under L-19 this conf IS the user's
        # tuning session, so a failed tag must cost them the publish, never their edits.
        if prior_bg:
            try:
                wp.update_set(wid, {"BG": prior_bg})
            except Exception:  # noqa: BLE001 - best-effort restore
                pass
        report["reason"] = f"promote/tag failed: {exc}"
        return report  # untagged -> stays pending

    # 5. build obj + prop indexes; tolerate failure (the item is committed regardless).
    warnings: list[str] = []
    from .discovery import objects, properties

    for name, builder in (("objindex", objects.build_index), ("propindex", properties.build_index)):
        try:
            builder(wid, str(wallpapers_dir))
        except Exception as exc:  # noqa: BLE001 - index build is best-effort
            warnings.append(f"{name} build failed: {exc}")

    resumed = bench_courier.resume()

    report.update(
        ok=True,
        published=str(dest_dir),
        wp_conf=str(paths.wp_file(wid)),
        bg=bg,
        tagged="good",
        copied=[rel for _, rel in to_copy],
        resumed=resumed,
        warnings=warnings,
    )
    return report


def _re_commit(wid: str) -> dict[str, Any]:
    """Re-commit branch: a NO-OP promote.

    The bench tunes `wp/<id>.conf` in place, so by the time this runs the live conf already IS
    the edited conf - there is nothing to promote over it and no buffer to consume. The branch
    survives because it still owns the resume; the caller's onItemCommitted re-pushes the
    changed entry into the rotation set.
    """
    report: dict[str, Any] = {"ok": False, "wid": wid, "source": "good"}
    try:
        bg = str(wp.load(wid).get("BG", "") or "")
    except Exception:  # noqa: BLE001 - the signals below matter more than the report field
        bg = ""

    resumed = bench_courier.resume()

    report.update(
        ok=True,
        wp_conf=str(paths.wp_file(wid)),
        bg=bg,
        resumed=resumed,
        warnings=[],
    )
    return report


def commit(
    wid: str,
    *,
    source: str,
    title: str,
    workshop_dir: str | os.PathLike,
    wallpapers_dir: str | os.PathLike,
) -> dict[str, Any]:
    """The single commit gate. One function, two branches selected by `source`.

    Args:
        wid: the wallpaper / workshop id.
        source: "pending" (first-commit: copy in + tag good) or "good" (re-commit: overwrite conf).
        title: the wallpaper title written into tags.csv (first-commit only).
        workshop_dir: Steam workshop content root (the per-id source dir is <workshop_dir>/<wid>).
        wallpapers_dir: the app's library root (publish target is <wallpapers_dir>/<wid>).

    Returns a report dict with at least {"ok": bool}. On ok:False the item's state is unchanged:
    first-commit leaves it pending with no partial publish + no tag; re-commit leaves the live conf
    untouched. The two branches never both run.
    """
    if source not in _VALID_SOURCES:
        return {"ok": False, "wid": wid, "reason": f"bad source {source!r} (want pending|good)"}

    if source == "pending":
        return _first_commit(
            wid,
            title=title,
            workshop_dir=Path(workshop_dir),
            wallpapers_dir=Path(wallpapers_dir),
        )
    return _re_commit(wid)


def reject(wid: str, *, title: str) -> dict[str, Any]:
    """Reject - tombstone the item. Tag bad, resume the engine. No copy.

    Identical from the pending tray or the bench. Stopping a live test (reap engine) is the
    caller's concern; here we only flip state and clear any bench pause.
    """
    tags.set_state(wid, title, "bad")
    resumed = bench_courier.resume()
    return {"ok": True, "wid": wid, "tagged": "bad", "resumed": resumed}
