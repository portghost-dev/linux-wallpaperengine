"""Workshop scope backend.

The procurement + processing bridge behind the Workshop screen: steam:// handler
detection and the funnel deep links, the tile population (review-state imports),
the ruled trash chain (deletion record + copy-mode delete + unsubscribe deep
link), and the Settings > Library record manager slots.

Assessing whether a wallpaper runs (crash / heavy VRAM) is the import WIZARD's job now
(wizard_bridge.py + storage/bench_verdict.py) - it owns the windowed bench under the
daemon standdown. This bridge only supplies the shared spawn helpers (_resolve_dir,
_spawn_geometry) the wizard reuses; it no longer spawns an engine of its own.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from .dev import _wallpapers_dir
from .discovery import project as project_disc
from .storage import meta, paths, records, records_view, tags, tombstones, wp

WE_APPID = "431960"
WORKSHOP_BROWSE_URL = f"https://steamcommunity.com/workshop/browse/?appid={WE_APPID}"
GET_STEAM_URL = "https://store.steampowered.com/about/"


def _item_page_url(wid: str, steam: bool) -> str:
    """The canonical item deep link, exactly as the 16d link well displays it."""
    if steam:
        return f"steam://url/CommunityFilePage/{wid}"
    return f"https://steamcommunity.com/sharedfiles/filedetails/?id={wid}"


class WorkshopBridge(QObject):
    """Backend for the Workshop scope (16a/16b/16c)."""

    stateChanged = Signal()
    steamChanged = Signal()
    # internal: the trash cleanup thread (texcache purge + copy rmtree) finished.
    # Emitted off-thread; the queued connection lands the refresh on the GUI thread.
    _trashCleanupDone = Signal()

    def __init__(self, backend, dev, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._dev = dev
        self._trashCleanupDone.connect(self._on_trash_cleanup_done)
        self._steam = None           # None = never checked; recheck on scope entry

        # One-time migration off the legacy tombstones.json map into the per-wid record store,
        # then retire the old file. Idempotent + no-op once done, so running it every launch is safe.
        try:
            records_view.migrate_legacy()
        except Exception:
            pass

    @Slot(result=bool)
    def engineBusy(self) -> bool:
        """This bridge no longer spawns an engine of its own (the wizard owns the bench). The other
        bridges still query this in the symmetrical conflict gate, so it stays - it simply never
        reports busy. It no longer tracks peers itself, having nothing of its own to gate."""
        return False

    @Slot(result=bool)
    def steamAvailable(self) -> bool:
        if self._steam is None:
            self.recheckSteam()
        return bool(self._steam)

    @Slot()
    def recheckSteam(self) -> None:
        """Re-run handler detection (scope entry + Rescan per 16c)."""
        found = False
        try:
            r = subprocess.run(["xdg-mime", "query", "default", "x-scheme-handler/steam"],
                               capture_output=True, text=True, timeout=2, check=False)
            found = r.returncode == 0 and bool(r.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            found = False
        if found != self._steam:
            self._steam = found
            self.steamChanged.emit()
        else:
            self._steam = found

    @Slot()
    def openWorkshop(self) -> None:
        """Primary CTA: the Workshop browse page in the Steam client, or the Get Steam
        web page when no handler exists (16c: no dead clicks, ever)."""
        if self.steamAvailable():
            QDesktopServices.openUrl(QUrl(f"steam://openurl/{WORKSHOP_BROWSE_URL}"))
        else:
            QDesktopServices.openUrl(QUrl(GET_STEAM_URL))

    @Slot(str)
    def openItemPage(self, wid: str) -> None:
        """The item's own Workshop page (the trash wizard's one-click unsubscribe)."""
        if not paths.is_safe_wid(str(wid)):
            return
        QDesktopServices.openUrl(QUrl(_item_page_url(str(wid), self.steamAvailable())))

    @Slot(str, result=str)
    def itemLinkText(self, wid: str) -> str:
        """The 16d link well's display text - exactly the URL the primary fires."""
        if not paths.is_safe_wid(str(wid)):
            return ""
        return _item_page_url(str(wid), self.steamAvailable())

    @Slot(str)
    def copyItemLink(self, wid: str) -> None:
        """16d link well click: copy the deep link to the clipboard."""
        text = self.itemLinkText(wid)
        if not text:
            return
        try:
            from PySide6.QtGui import QGuiApplication
            cb = QGuiApplication.clipboard()
            if cb is not None:
                cb.setText(text)
        except Exception:
            pass

    @Slot(result="QVariantList")
    def itemList(self) -> list:
        """Workshop tiles = EXACTLY the badge population (review F2: the invariant in
        pendingReviewCount's docstring must stay true): review-state imports newest
        first, then library dirs never classified in tags (pre-pipeline items - only
        this scope can graduate them, so hiding them made the badge a dead-end count).
        Forecasts come from the type census; crash/heavy assessment is the wizard's job
        now, not a tile chip. The just-kept linger is view-side state."""
        try:
            all_rows = tags.load()
        except Exception:
            all_rows = []
        known = {r["id"] for r in all_rows if r.get("id")}
        rows = [(r["id"], r.get("title") or r["id"])
                for r in reversed(all_rows)
                if r.get("state") == "review" and r.get("id")]
        try:
            from .models import _scan_dir_ids
            unknown = [d for d in _scan_dir_ids(_wallpapers_dir()) if d not in known]
        except Exception:
            unknown = []
        rows += [(wid, "") for wid in sorted(unknown)]
        out = []
        for wid, title in rows:
            try:
                conf = wp.load(wid)
            except Exception:
                conf = {}
            d = self._resolve_dir(wid)
            try:
                wtype = str(project_disc.read(d).get("type") or "") if d else ""
            except Exception:
                wtype = ""
            wtype = wtype or str(conf.get("TYPE", ""))
            if not title:
                title = self._backend.titleOf(wid) or wid
            m = meta.get(wid)
            dep_missing = bool(m.get("depMissing"))
            out.append({
                "wid": wid,
                "title": title,
                "thumb": self._backend.thumbUrl(wid),
                "wpType": wtype,
                "forecast": "",
                "crashed": self._crash_chip(wid),
                "depMissing": dep_missing,
                "depWid": str(m.get("depWid", "")).split()[0] if m.get("depWid") else "",
                "depName": str(m.get("depName", "")),
            })
        return out

    def _crash_chip(self, wid: str) -> bool:
        """The one persistent TILE verdict chip (finalized triage): "Crashed", sourced from the
        records log and HASH-GUARDED so it self-clears when the item is edited on disk. True only
        when the item's most recent bench outcome was a crash for its CURRENT content - i.e. a
        benched-but-kept item (the wizard's "Keep and fix later" path). Cheap: no record file ->
        no chip, no hashing."""
        try:
            if not records.has_record(wid):
                return False
            h = records.head(wid)
            m = h.get("machine") if h else None
            if not m or m.get("verdict") != "crashed":
                return False
            d = self._resolve_dir(wid)
            return bool(d) and m.get("contentHash", "") == records.content_hash(d)
        except Exception:
            return False

    @Slot(str, result="QVariantMap")
    def depInfo(self, wid: str) -> dict:
        """The 16e modal's facts for one held item. Multi-dep (M4): the stored set is
        ALL declared deps; the modal surfaces the FIRST STILL-MISSING one so the deep
        link is always actionable, falling back to the first."""
        m = meta.get(str(wid))
        deps = str(m.get("depWid", "")).split()
        target = deps[0] if deps else ""
        name = str(m.get("depName", ""))
        if len(deps) > 1 and bool(m.get("depMissing")):
            try:
                from .storage import importer as _importer
                cfg = _importer._snapshot()
                for d in deps:
                    if not _importer._dep_present(d, cfg):
                        target = d
                        name = _importer._dep_display_name(d, cfg)
                        break
            except Exception:
                pass
        return {"depWid": target,
                "depName": name,
                "missing": bool(m.get("depMissing"))}

    @Slot(str, result=bool)
    def isGood(self, wid: str) -> bool:
        """Post-approve verification (review F12): the kept chip must never lie about a
        swallowed tags write."""
        try:
            return str(wid) in tags.good_ids()
        except Exception:
            return False

    @Slot(str, result=bool)
    def isSteamSubscribed(self, wid: str) -> bool:
        """True only if Steam actually still has this item (its workshop dir exists) -
        the POSITIVE origin signal the trash wizard's beat 2 gates on (review B12 MED).
        A numeric-named local pack, or an already-unsubscribed item, has no workshop dir
        and must NOT be offered an unsubscribe deep link to an unrelated Steam page."""
        wid = str(wid)
        if not paths.is_safe_wid(wid):
            return False
        try:
            from .storage.importer import workshop_dir as _wsdir
            return os.path.isdir(os.path.join(_wsdir(), wid))
        except Exception:
            return False

    @Slot(str, result="QVariantMap")
    def trashConsequence(self, wid: str) -> dict:
        """The dialog's disk truth, mode-correct: whether OUR copy exists (and gets
        deleted) vs reference (nothing of ours on disk). Steam keeps its copy either
        way while subscribed."""
        wid = str(wid)
        has_copy = paths.is_safe_wid(wid) and self._copy_deletable(wid)
        return {"hasCopy": bool(has_copy)}

    def _resolve_dir(self, wid: str) -> str:
        """The render dir, conf-first (BG carries the copy-vs-reference truth): a bare
        BG resolves against the library dir, an absolute BG (reference imports) is
        taken as-is. Falls back to the library dir for pre-pipeline review items."""
        try:
            bg = str(wp.load(wid).get("BG") or "")
        except Exception:
            bg = ""
        if bg and os.path.isabs(bg) and os.path.isdir(bg):
            return bg
        cand = os.path.join(_wallpapers_dir(), bg or wid)
        return cand if os.path.isdir(cand) else ""

    def _spawn_geometry(self) -> str:
        """Monitor-ratio quadrant of the focused output (the A/B lesson: matching the
        output's aspect projects the scene as it does as a wallpaper)."""
        try:
            import json as _json
            r = subprocess.run(["hyprctl", "-j", "monitors"], capture_output=True,
                               text=True, timeout=2, check=False)
            mons = _json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []
            if mons:
                m = next((x for x in mons if x.get("focused")), mons[0])
                return f"0x0x{int(m['width']) // 2}x{int(m['height']) // 2}"
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            pass
        return "0x0x1280x720"

    @Slot(str)
    @Slot(str)
    @Slot(str, str)
    def trashItem(self, wid: str, comment: str = "") -> None:
        """Tile/library trash entry: write a human deletion RECORD (with the optional note the trash
        wizard collected), then do the file work. The wizard-deny writes its own richer event (with
        the crash lineage) and calls trashFiles() directly, so a deletion is never double-recorded.
        Steam's copy is never touched (the dialog carries the unsubscribe deep link instead)."""
        wid = str(wid)
        if not paths.is_safe_wid(wid):
            return
        try:
            records.append(wid, records.make_event("deleted", where="library", initiator="human",
                                                   comment=(str(comment) or None)))
        except Exception:
            pass
        self._trash_files(wid)

    @Slot(str)
    def trashFiles(self, wid: str) -> None:
        """The file/tags work with NO record write - for callers (the wizard) that already wrote
        their own deletion event."""
        self._trash_files(str(wid))

    def _trash_files(self, wid: str) -> None:
        """tags bad (the re-import gate) + drop from disk if our copy. No record write here."""
        if not paths.is_safe_wid(wid):
            return
        self._backend.trashWallpaper(wid)
        if bool(meta.get(wid).get("depMissing")):
            try:
                meta.update(wid, {"depMissing": False})
            except Exception:
                pass
        copy_dir = os.path.join(_wallpapers_dir(), wid) if self._copy_deletable(wid) else None

        # off the GUI thread (review F9): copy-mode trees can be GB-scale and the
        # texcache scan is IO; a POSIX unlink under the dying engine's open fds is
        # safe. Cleanup ends with a queued refresh so presence-derived state settles.
        def _cleanup() -> None:
            try:
                from . import texcomp
                texcomp.purge_wallpaper(wid)
            except Exception:
                pass
            if copy_dir:
                shutil.rmtree(copy_dir, ignore_errors=True)
            self._trashCleanupDone.emit()

        import threading
        threading.Thread(target=_cleanup, daemon=True).start()
        self.stateChanged.emit()

    def _on_trash_cleanup_done(self) -> None:
        """GUI-thread tail of a trash: the copy dir and texcache rows are gone now, so
        re-derive everything presence-based and let the tiles re-pull."""
        try:
            self._backend.refresh()
        except Exception:
            pass
        self.stateChanged.emit()

    def _copy_deletable(self, wid: str) -> bool:
        """Our copy in the library dir gets deleted on trash, source or no source
        (simplicity first; the earlier only-if-re-obtainable
        guard was reverted). The fence stays: a real subdir of the library dir, never
        a symlink out of it."""
        lib = _wallpapers_dir()
        copy_dir = os.path.join(lib, wid)
        return (os.path.isdir(copy_dir) and not os.path.islink(copy_dir)
                and os.path.dirname(os.path.abspath(copy_dir)) == os.path.abspath(lib))

    @Slot(result="QVariantList")
    def recordList(self) -> list:
        """Manager rows: every tombstoned item (its head event is a deletion), with the composed
        latest-event line. Title resolved best-effort (falls back to the wid for items already
        gone from disk)."""
        # a trashed item lives as a tags 'bad' row that carries its title (records do not store
        # one); fall back to the library title, then the wid, for items already gone from disk.
        try:
            titles = {r["id"]: (r.get("title") or r["id"]) for r in tags.load() if r.get("id")}
        except Exception:
            titles = {}
        # the modal shows EVERY tombstone but may only offer Import on the ones whose files are
        # still on disk - an unsubscribed item has nothing to re-import, and offering it would be
        # the interface lying. Computed once here rather than per row.
        importable = set(self.bypassableWids())
        # a MANUAL item has no Steam subscription, so Unsubscribe is meaningless on its row
        man_root = str(paths.manual_dir())
        try:
            manual = {d for d in os.listdir(man_root)
                      if os.path.isdir(os.path.join(man_root, d))}
        except OSError:
            manual = set()
        out = []
        for wid in records_view.tombstoned_wids():
            h = records.head(wid) or {}
            m = h.get("machine") or {}
            crashed = m.get("verdict") == "crashed"
            outcome = "Crashed" if crashed else str(h.get("action", "")).replace("_", " ").capitalize()
            out.append({"wid": wid,
                        "importable": wid in importable,
                        "manual": wid in manual,
                        "title": titles.get(wid) or self._backend.titleOf(wid) or wid,
                        "line": records_view.summary(wid)["line"],
                        "outcome": outcome,
                        "crashed": crashed,
                        "date": records_view._fmt_date(str(h.get("when", "")))})
        out.sort(key=lambda r: not r["importable"])
        return out

    @Slot(str, result="QVariantList")
    def recordTimeline(self, wid: str) -> list:
        """The item's full history, newest-first, each event as a neutral composed line."""
        return records_view.timeline(str(wid))

    @Slot(str)
    def purgeRecord(self, wid: str) -> None:
        """Escape hatch: wipe the record file AND drop the tags gate, so the item can re-import.
        Erases both the suppression and the audit trail (the confirm says so)."""
        records_view.purge_and_ungate(str(wid))
        try:
            self._backend.refresh()
            self._backend.settingsChanged.emit()
        except Exception:
            pass
        self.stateChanged.emit()

    @Slot()
    def purgeAllRecords(self) -> None:
        """20c 'Purge all': wipe EVERY record file and drop each item's tags gate in one go, so all
        purged items can re-import on the next scan. Erases both the suppression and the audit trail
        for all of them - the footer copy states the consequence; no confirm step."""
        for wid in list(records_view.tombstoned_wids()):
            records_view.purge_and_ungate(wid)
        try:
            self._backend.refresh()
            self._backend.settingsChanged.emit()
        except Exception:
            pass
        self.stateChanged.emit()

    @staticmethod
    def _ws_dir() -> str:
        from .storage import importer as _importer
        return _importer.workshop_dir()

    @Slot(str, result=int)
    def dependentCount(self, wid: str) -> int:
        """How many OTHER subscribed items declare this wid as their dependency (base). Trashing a
        base breaks the presets built on it, so the trash wizard warns when this is > 0."""
        wid = str(wid)
        wsdir = self._ws_dir()
        n = 0
        try:
            from .discovery import project as _project
            for name in os.listdir(wsdir):
                if name == wid or not os.path.isdir(os.path.join(wsdir, name)):
                    continue
                dep = (_project.read(os.path.join(wsdir, name)).get("raw") or {}).get("dependency")
                deps = dep if isinstance(dep, list) else ([dep] if dep else [])
                if wid in [str(d) for d in deps]:
                    n += 1
        except Exception:
            pass
        return n

    @staticmethod
    def _safe_stem(name: str) -> str:
        """Fold a folder name into a legal wid. is_safe_wid rejects whitespace, slashes and glob
        metacharacters because a shell consumer word-splits and glob-expands the MEMBERS list, so
        those become hyphens rather than a refusal - a user should not have to rename a directory
        to import it. Length is capped so one long name cannot stretch every surface that shows an
        id."""
        out = []
        for ch in str(name or "").strip():
            out.append(ch if (ch.isalnum() or ch in "._-") else "-")
        stem = "".join(out).strip(".-") or "scene"
        return stem[:64]

    def _free_wid(self, stem: str) -> str:
        """First unused id for this stem, suffixing -adv-N on collision.

        The id IS the folder name, so two unrelated folders can genuinely want the same one.
        Suffixing is non-destructive by construction - this is copy-only, so nothing existing is
        ever touched - and the tile still shows project.json's title, so the suffix stays an
        internal detail unless the user goes looking for it in the editor's id chip."""
        taken = set()
        try:
            taken |= set(tags.known_ids())
        except Exception:
            pass
        for root in (paths.manual_dir(), self._ws_dir(), paths.default_wallpapers_dir()):
            try:
                taken |= set(os.listdir(str(root)))
            except OSError:
                pass
        if stem not in taken:
            return stem
        n = 1
        while f"{stem}-adv-{n}" in taken:
            n += 1
        return f"{stem}-adv-{n}"

    @Slot(str, result=str)
    def addFromFolder(self, folder: str) -> str:
        """COPY a folder into LWE's manual pending root. Returns the new wid, or "" on failure.

        Copy only, never a reference: an added folder may live anywhere and may be moved or
        deleted by its owner, so the app takes its own copy and stops caring about the original.
        No origin is recorded and no update path exists - re-importing an edited scene is a fresh
        add, and housekeeping is the power user's.

        Staged then renamed, so a crash mid-copy leaves a staging directory the scan ignores
        rather than a half-built wallpaper that could be benched."""
        src = str(folder or "").strip()
        if src.startswith("file://"):
            src = QUrl(src).toLocalFile()
        if not src or not os.path.isdir(src):
            return ""
        if not os.path.isfile(os.path.join(src, "project.json")):
            return ""
        wid = self._free_wid(self._safe_stem(os.path.basename(os.path.normpath(src))))
        if not paths.is_safe_wid(wid):
            return ""
        root = paths.manual_dir()
        try:
            os.makedirs(str(root), exist_ok=True)
            stage = os.path.join(str(root), f".import-{wid}")
            shutil.rmtree(stage, ignore_errors=True)
            shutil.copytree(src, stage)
            os.rename(stage, os.path.join(str(root), wid))
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            return ""
        self.stateChanged.emit()
        return wid

    @Slot(result="QVariantList")
    def bypassableWids(self) -> list:
        """Suppressed items (head event = a deletion) whose files are STILL in the configured Steam
        workshop dir - the only set the tombstone gate actually fights (an unsubscribed item can't
        reimport anyway). The red 'Import Tombstoned?' button acts on exactly these."""
        wsdir = self._ws_dir()
        man = str(paths.manual_dir())
        # BOTH pending roots. A hand-added item's files sit in the manual root, so checking only
        # the Steam tree would call it unimportable while its content is safely on disk.
        return [w for w in records_view.tombstoned_wids()
                if os.path.isdir(os.path.join(wsdir, w)) or os.path.isdir(os.path.join(man, w))]

    def _bypass_one(self, wid: str) -> bool:
        """Write the 'bypassed' record and drop the tags gate for ONE item. Returns whether it
        was actually actioned - an item whose files are gone is skipped, since dropping its gate
        would advertise an import that cannot happen."""
        wsdir = self._ws_dir()
        w = str(wid or "")
        if not w or not os.path.isdir(os.path.join(wsdir, w)):
            return False
        try:
            records.append(w, records.make_event("bypassed", where="workshop", initiator="human"))
            tags.remove(w)
            return True
        except Exception:
            return False

    def _after_bypass(self, n: int) -> None:
        if not n:
            return
        try:
            self._backend.refresh()
        except Exception:
            pass
        self.stateChanged.emit()

    @Slot(str, result=bool)
    def bypassImportOne(self, wid: str) -> bool:
        """Per-row import (the tombstone-import modal): re-admit exactly this item to Workshop.
        It goes back to WORKSHOP for benching, never straight to the library - dropping the gate
        only un-suppresses the next scan, it does not commit anything."""
        ok = self._bypass_one(wid)
        self._after_bypass(1 if ok else 0)
        return ok

    @Slot()
    def bypassImport(self) -> None:
        """One-shot override: for every still-subscribed tombstoned item, write a 'bypassed' record
        and drop its gate so the next scan reimports it. A deliberate 'disregard my tombstones this
        once' - the caller triggers the rescan."""
        n = 0
        for w in records_view.tombstoned_wids():
            if self._bypass_one(w):
                n += 1
        self._after_bypass(n)
