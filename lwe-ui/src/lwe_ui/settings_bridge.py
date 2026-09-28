"""The Settings surface's own bridge.

Settings was the only major surface without a purpose-built bridge; it rode the
god-object `Backend` directly, which is why every page was a load-time snapshot that
went stale the moment the popup or the editor wrote the same key.

Three things this module exists to guarantee:

  ONE STORE (sec 2.2).  Every commit ends in the same `Backend._set_setting` fan-out, so a
  settings commit and a popup commit of the same key are the SAME write and `settingsChanged`
  still reaches the deck, header, popup and editor. The bridge keeps no second copy.

  HONEST REACH (sec 1.1, S2).  `reach(key)` is DERIVED from the same tuples the push path
  consumes, never hardcoded prose - exactly as `Backend.overrideReach` is. A verb landing
  later flips the answer with no UI change.

  FAILURE IS LOUD (S5).  `Backend.setSetting` returns void and swallows exceptions, and
  `storage.settings._validate` warns-and-clamps; both are silent-failure paths, which S5
  makes illegal on this surface. The bridge validates BEFORE the write and reports a
  rejected value as a failure event, never as a silent clamp.

Write order is SAVE FIRST for every key: a commit saves through `Backend.save_setting`, the
change runner, which then sends the key's push resolved from the store. A change the engine
did not take stays saved and applies at the next opportunity.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from typing import Any, Callable

from time import monotonic

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot

from . import api_client
from . import constants as C
from .engine import daemon_unit, push
from .storage import backup, paths, rules, settings, tags

_REGENERATE_KEYS = C.REACH_SERVICE_RESTART

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class SettingsBridge(QObject):
    """`settingsBridge` - the Settings surface's store access, reach receipt and verbs."""

    changed = Signal()
    restartBusyChanged = Signal()
    commitFailed = Signal("QVariantList", str)
    truthRefreshed = Signal()

    def __init__(self, backend: Any, import_bridge: Any = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._import = import_bridge
        self._receipt: dict = {}
        # one service read answers every row; cleared on an env file rewrite or invalidate
        self._pending: dict[str, bool] | None = None
        self._observed = False
        # settle window of a restart taken from a row; held here because the Settings
        # pages are rebuilt on a tab switch
        self._settling = False
        self._settle_started = 0.0
        self._settle = QTimer(self)
        self._settle.setInterval(1500)
        self._settle.timeout.connect(self._settle_tick)
        try:
            backend.settingsChanged.connect(self.changed)
        except Exception:
            pass

    def _load(self) -> dict[str, Any]:
        try:
            return settings.load()
        except Exception:
            try:
                return dict(paths.default_settings())
            except Exception:
                return {}

    @Slot(str, result="QVariant")
    def value(self, key: str) -> Any:
        spec = C.SETTINGS_SCHEMA.get(str(key))
        if spec is None:
            return None
        return self._load().get(str(key), spec["default"])

    def _validate(self, key: str, value: Any) -> tuple[bool, Any, str]:
        """(ok, coerced, reason). Range/choice truth comes from SETTINGS_SCHEMA only."""
        spec = C.SETTINGS_SCHEMA.get(key)
        if spec is None:
            return False, None, "That setting does not exist."
        t = spec["type"]
        try:
            if t == "bool":
                return True, bool(value), ""
            if t in ("int", "int_or_empty"):
                text = str(value).strip()
                if t == "int_or_empty" and text == "":
                    return True, "", ""
                n = int(float(text))
                lo, hi = spec.get("min"), spec.get("max")
                if lo is not None and n < lo:
                    return False, None, "That value is outside the allowed range."
                if hi is not None and n > hi:
                    return False, None, "That value is outside the allowed range."
                return True, n, ""
            if t == "float":
                f = float(str(value).strip())
                lo, hi = spec.get("min"), spec.get("max")
                if lo is not None and f < lo:
                    return False, None, "That value is outside the allowed range."
                if hi is not None and f > hi:
                    return False, None, "That value is outside the allowed range."
                return True, f, ""
            if t in ("enum", "enum_or_empty"):
                text = str(value)
                if t == "enum_or_empty" and text == "":
                    return True, "", ""
                if text not in spec.get("choices", ()):
                    return False, None, "That is not one of the choices."
                return True, text, ""
            if t == "packed":
                return self._validate_schedule(str(value))
            if t == "path":
                text = str(value)
                if text.startswith("file://"):
                    text = QUrl(text).toLocalFile()
                if text and not os.path.isdir(text):
                    return False, None, "That is not a folder."
                return True, text, ""
            return True, str(value), ""
        except (TypeError, ValueError):
            return False, None, "That is not a number."

    def _validate_schedule(self, packed: str) -> tuple[bool, Any, str]:
        """`HH:MM=slug;HH:MM=slug` (constants.py SCHEDULE) - 24-hour, rejected not clamped."""
        if packed.strip() == "":
            return True, "", ""
        times: list[str] = []
        slugs: list[str] = []
        for entry in packed.split(";"):
            if not entry.strip():
                continue
            head, _, slug = entry.partition("=")
            if not _TIME_RE.match(head.strip()) or not slug.strip():
                return False, None, "Use a 24-hour time, like 07:30."
            times.append(head.strip())
            slugs.append(slug.strip())
        if len(times) != len(set(times)):
            return False, None, "Day and night need two different times."
        if len(slugs) != len(set(slugs)):
            return False, None, "Day and night need two different playlists."
        return True, packed, ""

    @Slot(str, result=str)
    def reach(self, key: str) -> str:
        key = str(key)
        if key in C.REACH_SERVICE_RESTART:
            return "SERVICE-RESTART"
        if key in C.REACH_PANEL:
            return "PANEL"
        if key in C.REACH_BOUNDARY:
            return "BOUNDARY"
        if key in C.REACH_NEXT_SCAN:
            return "NEXT-SCAN"
        if key in C.REACH_RE_ARM:
            return "RE-ARM"
        if key in C.REACH_NEXT_IMPORT:
            return "NEXT-IMPORT"
        if key in C.AUDIO_DIAL_ENV:
            return "LIVE"
        # The derived half: a key the push path consumes is LIVE, and it becomes LIVE the
        # day a verb lands, with no edit here (Backend.overrideReach, models.py).
        try:
            if key in self._backend._LIVE_GLOBAL_KEYS:
                return "LIVE"
        except Exception:
            pass
        if key in C.REACH_NEXT_SHOW:
            return "NEXT-SHOW"
        return "NEXT-SHOW"

    def _refresh_pending(self) -> None:
        try:
            self._observed, self._pending = daemon_unit.restart_state()
        except Exception:
            self._observed, self._pending = False, {}

    @Slot(str, result=bool)
    def restartPending(self, key: str) -> bool:
        """True while the running engine started with other values than the env file now
        carries for the keys `key` reaches it through. Read from the engine's status (the
        layer from its process), so it holds across panel launches and clears by itself once
        the engine comes back up on the new file. Cached: a rewrite of the env file and
        `invalidateRestart` are the only things that read the service again, and one read
        answers every row."""
        if self._pending is None:
            self._refresh_pending()
        return bool((self._pending or {}).get(str(key), False))

    @Slot(result="QVariantList")
    def restartKeys(self) -> list:
        """The setting keys that carry a restart verb, from the engine env table."""
        return list(daemon_unit.RESTART_ENV_KEYS)

    @Slot()
    def invalidateRestart(self) -> None:
        """Forget the cached answers, so the next restartPending reads the service."""
        self._pending = None

    @Property(bool, notify=restartBusyChanged)
    def restartBusy(self) -> bool:
        """True from a restart taken here until the engine is back up on the file or the
        window runs out; every verb is held while it is."""
        return self._settling

    @Slot(result=bool)
    def takeRestart(self) -> bool:
        """One restart for every row that is pending. Queued through the backend, then a
        settle window re-reads the service every 1.5 s until nothing pends or 20 s pass.
        A refused job opens no window."""
        if self._settling:
            return False
        try:
            accepted = bool(self._backend.restartMaster())
        except Exception:
            accepted = False
        if not accepted:
            return False
        self._settling = True
        self._settle_started = monotonic()
        self._pending = None
        self._settle.start()
        self.restartBusyChanged.emit()
        return True

    def _settle_tick(self) -> None:
        self._pending = None
        self._refresh_pending()
        self.changed.emit()
        # the window closes on an observed engine with nothing pending, never on an
        # unreadable one: the replacement process has to be seen on the file
        settled = self._observed and not any((self._pending or {}).values())
        if settled or monotonic() - self._settle_started >= 20:
            self._settle.stop()
            self._settling = False
            self.restartBusyChanged.emit()

    def _fail(self, key: str, reason: str) -> bool:
        self.commitFailed.emit([key], reason)
        return False

    @Slot(str, "QVariant", result=bool)
    def commit(self, key: str, value: Any) -> bool:
        """Validate, then save through the change runner, which sends the key's push resolved
        from the store. A change the engine did not take stays saved."""
        key = str(key)
        ok, coerced, reason = self._validate(key, value)
        if not ok:
            return self._fail(key, reason)

        try:
            outcome = self._backend.save_setting(key, coerced)
        except Exception:
            return self._fail(key, "Settings could not be saved.")

        if key in _REGENERATE_KEYS:
            self._regenerate()
        if outcome.env is not None and outcome.env not in ("written", "unchanged"):
            return self._fail(key, "Saved, but the engine file could not be written.")
        if outcome.kind in ("refused", "uncertain"):
            return self._fail(key, "The engine did not answer.")

        self.changed.emit()
        return True

    @Slot(str, str, result=bool)
    def commitPath(self, key: str, url: str) -> bool:
        """Folder picker commit, taken on the dialog's accept. Fails on an unreadable path."""
        local = str(url)
        if local.startswith("file://"):
            local = QUrl(local).toLocalFile()
        if not local or not os.path.isdir(local):
            return self._fail(str(key), "That folder could not be read.")
        return self.commit(key, local)

    @staticmethod
    def _ok(reply: Any) -> bool:
        return bool(isinstance(reply, dict) and reply.get("ok"))

    def _regenerate(self) -> None:
        """Forget the cached restart answers after a SERVICE-RESTART key's save, whose engine-env
        write the change runner made. The restart itself is not taken: the change lands in the
        file and the user restarts."""
        self._pending = None

    @Slot(result="QVariantList")
    def audioDials(self) -> list:
        """Live `status` -> the persisted key -> `calibrated`.

        U4 created the store, so the ladder finally has a true middle rung; the calibrated
        floor stays last, for a first run against a dead socket. Ranges, log and invert
        flags come from the shipped editor table and are never re-derived here.
        """
        from .editor import AUDIO_DIALS, _dial_to_quality

        snap: dict[str, Any] = {}
        try:
            got = api_client.status()
            if isinstance(got, dict):
                snap = got
        except Exception:
            snap = {}
        stored = self._load()
        out = []
        for _key, spec in AUDIO_DIALS.items():
            skey = C.AUDIO_DIAL_KEYS[spec["field"]]
            raw = snap.get(spec["field"])
            if isinstance(raw, (int, float)):
                value = float(raw)
            else:
                try:
                    value = float(stored.get(skey, spec["calibrated"]))
                except (TypeError, ValueError):
                    value = float(spec["calibrated"])
            out.append({
                "key": skey,
                "label": spec["label"],
                "lo": spec["lo"], "hi": spec["hi"],
                "log": bool(spec["log"]), "invert": bool(spec["invert"]),
                "engineValue": value,
                "quality": _dial_to_quality(spec, value),
            })
        return out

    @Slot(str, float, result=bool)
    def setAudioDial(self, settings_key: str, engine_value: float) -> bool:
        """Save one dial engine-native first; the change runner then sends set-tuning. Same store
        as the editor's identical row: one fact, two doors, one store."""
        return self.commit(str(settings_key), float(engine_value))

    @Slot(result=bool)
    def openLogs(self) -> bool:
        """Open the log DIRECTORY in the user's file manager - by NAME, not by association.

        An earlier attempt replaced xdg-open with QDesktopServices.openUrl; that proved
        insufficient - on Linux both resolve through the same mimeapps association for
        inode/directory, and on this box that association is a terminal emulator. So the
        spec's "a file manager, never a terminal" is only satisfiable by addressing THE
        FILE MANAGER as a service: org.freedesktop.FileManager1, the freedesktop interface
        every mainstream file manager registers, whose ShowFolders can only ever open a
        file manager because that is what the interface IS. QDesktopServices remains as the
        fallback for the rare session with no FileManager1 provider.
        """
        try:
            target = paths.logs_dir()
            target.mkdir(parents=True, exist_ok=True)
        except Exception:
            return self._fail("Logs", "The log folder could not be found.")
        url = QUrl.fromLocalFile(str(target)).toString()
        try:
            from PySide6.QtDBus import QDBusConnection, QDBusInterface

            bus = QDBusConnection.sessionBus()
            fm = QDBusInterface("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                                "org.freedesktop.FileManager1", bus)
            if fm.isValid():
                reply = fm.call("ShowFolders", [url], "")
                if reply.errorName() == "":
                    return True
        except Exception:
            pass
        from PySide6.QtGui import QDesktopServices

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            return self._fail("Logs", "The log folder could not be opened.")
        return True


    # --- backup: one .lwebackup file, a receipt line on the Configuration row after import
    receiptChanged = Signal()

    @Slot(result=str)
    def backupDefaultName(self) -> str:
        return backup.default_name()

    @Slot(result=str)
    def backupFilter(self) -> str:
        return f"LWE backup (*{backup.EXTENSION})"

    def _local(self, url: str) -> str:
        local = str(url)
        if local.startswith("file://"):
            local = QUrl(local).toLocalFile()
        return local

    @Slot(str, result=bool)
    def exportBackup(self, url: str) -> bool:
        """Save dialog accept: write the archive where the user put it. Success is silent."""
        local = self._local(url)
        if not local:
            return self._fail("Configuration", "The backup could not be written.")
        try:
            r = backup.export_to(local)
        except Exception:
            return self._fail("Configuration", "The backup could not be written.")
        log = logging.getLogger("lwe_ui.backup")
        log.info("exported %s: %s", local, json.dumps(r["counts"]))
        for e in r["errors"]:
            log.warning("export skipped %s: %s", e["file"], e["reason"])
        return True

    @Slot(str, result=bool)
    def importBackup(self, url: str) -> bool:
        """Open dialog accept: preflight, write through the schemas, refresh, receipt."""
        local = self._local(url)
        if not local or not os.path.isfile(local):
            return self._fail("Configuration", "That file is not an LWE backup.")
        try:
            r = backup.apply(backup.preflight(local))
        except Exception:
            return self._fail("Configuration", "That backup could not be restored.")
        log = logging.getLogger("lwe_ui.backup")
        if r.get("refused"):
            why = (r.get("errors") or [{}])[0].get("reason") or "That backup could not be restored."
            return self._fail("Configuration", why)
        log.info("restored %s: %s", local, json.dumps(r["counts"]))
        for key in ("dropped", "held", "reresolved", "adjusted", "preserved", "notes",
                    "followups", "errors"):
            for item in r.get(key, []):
                log.info("restore %s: %s", key, json.dumps(item))
        self._receipt = r
        # the same refresh a settings edit gets, then the engine sees the new policy
        try:
            self._backend.refresh()
            self._backend.settingsChanged.emit()
            self._backend.playlistsChanged.emit()
            self._backend.themeRefreshRequested.emit()
            push.sync_all("window", ("BUNDLE", "CURRENT"), defer_current=self._backend.delivery_due())
        except Exception:
            pass
        if any(f["kind"] == "engine-restart" for f in r["followups"]):
            try:
                daemon_unit.write_env()
            except Exception:
                pass
            self._pending = None
        self.changed.emit()
        self.truthRefreshed.emit()
        self.receiptChanged.emit()
        return True

    @Property(str, notify=receiptChanged)
    def receiptLine(self) -> str:
        return backup.receipt_line(self._receipt) if self._receipt else ""

    @Property("QVariantMap", notify=receiptChanged)
    def receipt(self) -> dict:
        return dict(self._receipt or {})

    @Slot(result=bool)
    def resetConfig(self) -> bool:
        if not bool(self._backend.resetConfig()):
            return self._fail("Configuration", "Settings could not be reset.")
        self.changed.emit()
        self.truthRefreshed.emit()
        return True

    #: Interface scale detents: a release within three points of one settles on it.
    _SCALE_DETENTS = (75, 100, 150)

    @Slot(float, result=int)
    def settleScale(self, value: float) -> int:
        """The percent a released Interface scale knob settles on: the nearest detent when
        within three points of one, else the integer the knob sits at."""
        spec = C.SETTINGS_SCHEMA["INTERFACE_SCALE"]
        v = int(round(float(value)))
        v = max(int(spec["min"]), min(int(spec["max"]), v))
        for d in self._SCALE_DETENTS:
            if abs(v - d) <= 3:
                return d
        return v

    @Slot(result=bool)
    def autostart(self) -> bool:
        return bool(self._backend.getAutostart())

    @Slot(bool)
    def setAutostart(self, on: bool) -> None:
        self._backend.setAutostart(bool(on))
        self.changed.emit()

    @Slot()
    def rescanNow(self) -> None:
        if self._import is not None:
            self._import.rescanNow()

    @Slot(result=str)
    def diskUsage(self) -> str:
        """`N.N GB` alone. The old composite `N.N GB - N tombstones` string splits into the
        two rows 30c draws, so neither row has to carry the other's fact."""
        composite = str(self._backend.diskUsage())
        return composite.split("·")[0].strip() or composite

    @Slot(result=int)
    def tombstoneCount(self) -> int:
        try:
            return sum(1 for r in tags.load() if r.get("state") == "bad")
        except Exception:
            return 0

    @Slot(result="QVariantList")
    def exceptions(self) -> list:
        try:
            return list(self._backend._fullscreen_ignore_ids())
        except Exception:
            return []

    @Slot(result=int)
    def exceptionCount(self) -> int:
        return len(self.exceptions())

    def _write_exceptions(self, change: Callable[[str], str]) -> bool:
        header = "# fullscreen app_ids exempt from pause, one per line; e.g. steam\n"
        try:
            outcome = push.run_change(
                ("rules",), lambda: rules.modify("pause-blacklist.txt", lambda text: change(text or header)),
                [("verb", "pause-blacklist.txt")], defer_current=self._backend.delivery_due())
        except OSError:
            return False
        if outcome.kind != "applied":
            logging.getLogger(__name__).warning("exceptions list not applied: %s %s", outcome.kind,
                                                outcome.reason or outcome.message or "")
        self.truthRefreshed.emit()
        return True

    @Slot(str, result=bool)
    def addException(self, app_id: str) -> bool:
        entry = str(app_id).strip()[:128]
        if not entry or entry.startswith("#"):
            return self._fail("Exceptions", "That is not an app id.")
        if entry in self.exceptions():
            return True
        if not self._write_exceptions(lambda text: rules.add_entry(text, entry)):
            return self._fail("Exceptions", "The exceptions file could not be written.")
        return True

    @Slot(str, result=bool)
    def removeException(self, app_id: str) -> bool:
        entry = str(app_id).strip()
        if not self._write_exceptions(lambda text: rules.remove_entry(text, entry, 128)):
            return self._fail("Exceptions", "The exceptions file could not be written.")
        return True

    # ----------------------------------------------------------------------------------
    # Running-apps list (AMENDMENT-A1 sec 3, S-14) - the file behind the ENGINE's
    # app-condition poll (set-app-conditions verb). Entries are /proc comm NAMES, a
    # different identifier space than the exceptions list's window classes; the two
    # lists never merge [S-18].
    # ----------------------------------------------------------------------------------
    @Slot(result="QVariantList")
    def appEntries(self) -> list:
        return [str(e) for e in self._backend._app_condition_names()]

    @Slot(result=int)
    def appEntryCount(self) -> int:
        return len(self.appEntries())

    def _write_app_list(self, change: Callable[[str], str]) -> bool:
        header = "# processes that trigger the running-apps rule, one comm name per line\n"
        try:
            outcome = push.run_change(
                ("rules",), lambda: rules.modify("app-condition.txt", lambda text: change(text or header)),
                [("verb", "app-condition.txt")], defer_current=self._backend.delivery_due())
        except OSError:
            return False
        if outcome.kind != "applied":
            logging.getLogger(__name__).warning("app list not applied: %s %s", outcome.kind,
                                                outcome.reason or outcome.message or "")
        self.truthRefreshed.emit()
        return True

    @Slot(str, result=bool)
    def addAppEntry(self, name: str) -> bool:
        # H-A2 resolution [S-18]: the stored string must equal the matcher's comparand,
        # and the matcher compares /proc comm, which the kernel caps at 15 chars - so the
        # store truncates. Without this, "linux-wallpaperengine" would sit in the list
        # forever and never match its own comm "linux-wallpaper".
        entry = str(name).strip()[:15]
        if not entry or entry.startswith("#"):
            return self._fail("Apps", "That is not a process name.")
        if entry in self.appEntries():
            return True
        if not self._write_app_list(lambda text: rules.add_entry(text, entry)):
            return self._fail("Apps", "The app list file could not be written.")
        return True

    @Slot(str, result=bool)
    def removeAppEntry(self, name: str) -> bool:
        entry = str(name).strip()
        if not self._write_app_list(lambda text: rules.remove_entry(text, entry, 64)):
            return self._fail("Apps", "The app list file could not be written.")
        return True

    # ----------------------------------------------------------------------------------
    # Running-now picker source (AMENDMENT-A1 sec 3, H-A1 resolution [S-18]).
    #
    # PER-LIST SOURCES, because the lists match in different identifier spaces:
    #   exceptions -> compositor clients (window CLASS is the comparand; the title rides
    #                 along as the human name). hyprctl answers over a local socket in
    #                 milliseconds; a non-Hyprland session returns [] and the popup just
    #                 has no Running-now section.
    #   apps       -> /proc comm, filtered to THIS user's processes that have a cmdline
    #                 (no kernel threads, no other users' daemons) - a raw comm sweep
    #                 would show hundreds of rows of system noise.
    # ----------------------------------------------------------------------------------
    @Slot(str, result="QVariantList")
    def runningNow(self, kind: str) -> list:
        if kind == "apps":
            return self._running_procs()
        return self._running_clients()

    @staticmethod
    def _running_procs() -> list:
        try:
            uid = os.getuid()
            pids = [d for d in os.listdir("/proc") if d.isdigit()]
        except OSError:
            return []
        names: set[str] = set()
        for pid in pids:
            base = f"/proc/{pid}"
            try:
                if os.stat(base).st_uid != uid:
                    continue
                with open(f"{base}/cmdline", "rb") as f:
                    if not f.read(1):
                        continue
                with open(f"{base}/comm", "rb") as f:
                    comm = f.read().decode("utf-8", "replace").strip()
            except OSError:
                continue
            if comm:
                names.add(comm)
        return [{"human": n, "match": n} for n in sorted(names, key=str.lower)]

    @staticmethod
    def _running_clients() -> list:
        import json
        try:
            out = subprocess.run(["hyprctl", "-j", "clients"], capture_output=True,
                                 timeout=1.5)
            clients = json.loads(out.stdout.decode("utf-8", "replace"))
        except Exception:
            return []
        seen: dict[str, str] = {}
        for c in clients:
            if not isinstance(c, dict):
                continue
            cls = str(c.get("class") or "").strip()
            if not cls or cls in seen:
                continue
            seen[cls] = str(c.get("title") or "").strip() or cls
        return [{"human": seen[k], "match": k} for k in sorted(seen, key=str.lower)]

    @Slot(result="QVariantMap")
    def systemTruth(self) -> dict:
        return {
            "memoryHigh": self._unit_cap("MemoryHigh"),
            "memoryMax": self._unit_cap("MemoryMax"),
        }

    @staticmethod
    def _unit_file() -> str:
        return os.path.expanduser("~/.config/systemd/user/" + daemon_unit.UNIT_FILE_NAME)

    def _unit_cap(self, field: str) -> str:
        """Read a cap from the LIVE unit file, never from the template.

        A hand-edited unit is what systemd actually enforces, so the file on disk is the
        truth. G9: unparsable means the row says so - it never fabricates a number.
        """
        try:
            text = open(self._unit_file(), encoding="utf-8").read()
        except OSError:
            return ""
        match = re.search(rf"^{field}=(\S+)\s*$", text, re.MULTILINE)
        return match.group(1) if match else ""

