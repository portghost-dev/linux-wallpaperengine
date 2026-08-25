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

Write order is VERB FIRST, PERSIST ON CONFIRMATION for every LIVE-class key: the store
records what the engine accepted, and a rejected verb
persists nothing.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from PySide6.QtCore import QObject, QUrl, Signal, Slot

from . import api_client
from . import constants as C
from .engine import daemon_unit
from .storage import paths, settings, tags

_CLASS_NEXT_SHOW = ("ENGINE_SCALING", "ENGINE_CLAMP", "AUTOMUTE_DEFAULT")
_CLASS_SERVICE_RESTART = ("ENGINE_LAYER", "ENGINE_HWDEC", "ENGINE_TEXCOMP", "TEXTURE_DETAIL", "ASSETS_DIR")
_CLASS_PANEL = ("CLOSE_TO_TRAY", "STEAM_DIR")
_CLASS_BOUNDARY = ("SCHEDULE_ENABLED", "SCHEDULE")
_CLASS_NEXT_SCAN = ("WORKSHOP_DIR", "WALLPAPERS_DIR")
_CLASS_RE_ARM = ("DETECT_MODE", "DETECT_INTERVAL_SEC")
_CLASS_NEXT_IMPORT = ("REVIEW_REQUIRED", "STORAGE_POLICY")

_REGENERATE_KEYS = _CLASS_SERVICE_RESTART

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class SettingsBridge(QObject):
    """`settingsBridge` - the Settings surface's store access, reach receipt and verbs."""

    changed = Signal()
    commitFailed = Signal("QVariantList", str)
    truthRefreshed = Signal()

    def __init__(self, backend: Any, import_bridge: Any = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._import = import_bridge
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

    @Slot(result=bool)
    def scheduleUi(self) -> bool:
        """The Schedule section's build-time gate. False = it does not render.

        Not dim, not disabled-and-visible: absent. A visible-but-inert section is the same
        lie in a quieter voice, and nothing executes the schedule in the daemon era.
        """
        return bool(C.SCHEDULE_UI)

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
        for entry in packed.split(";"):
            if not entry.strip():
                continue
            head, _, slug = entry.partition("=")
            if not _TIME_RE.match(head.strip()) or not slug.strip():
                return False, None, "Use a 24-hour time, like 07:30."
        return True, packed, ""

    @Slot(str, result=str)
    def reach(self, key: str) -> str:
        key = str(key)
        if key in _CLASS_SERVICE_RESTART:
            return "SERVICE-RESTART"
        if key in _CLASS_PANEL:
            return "PANEL"
        if key in _CLASS_BOUNDARY:
            return "BOUNDARY"
        if key in _CLASS_NEXT_SCAN:
            return "NEXT-SCAN"
        if key in _CLASS_RE_ARM:
            return "RE-ARM"
        if key in _CLASS_NEXT_IMPORT:
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
        if key in _CLASS_NEXT_SHOW:
            return "NEXT-SHOW"
        return "NEXT-SHOW"

    def _fail(self, key: str, reason: str) -> bool:
        self.commitFailed.emit([key], reason)
        return False

    @Slot(str, "QVariant", result=bool)
    def commit(self, key: str, value: Any) -> bool:
        """Validate, apply, persist. Verb first and persist on confirmation for LIVE keys."""
        key = str(key)
        ok, coerced, reason = self._validate(key, value)
        if not ok:
            return self._fail(key, reason)

        if self.reach(key) == "LIVE" and not self._push_verb(key, coerced):
            # G3: nothing is persisted, so the control re-reads the value still in force.
            return self._fail(key, "The engine did not answer.")

        try:
            self._backend.setSetting(key, coerced)
        except Exception:
            return self._fail(key, "Settings could not be saved.")

        if key in _REGENERATE_KEYS and not self._regenerate():
            # G7: the settings write already landed, so this says the change did not REACH
            # the engine - not that it failed.
            return self._fail(key, "Saved, but the engine file could not be written.")

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

    def _push_verb(self, key: str, value: Any) -> bool:
        """The live half of the write order. True when there is nothing to push, too.

        A key with no verb of its own is pushed by Backend's own fan-out after the write;
        this only leads with the verbs that answer, so a dead socket is caught BEFORE the
        store records something the engine never accepted.
        """
        try:
            if not api_client.available():
                # No live engine to lie about: the write is the whole of the change.
                return True
        except Exception:
            return True
        try:
            if key == "ENGINE_TIMESCALE":
                return self._ok(api_client.set_speed(float(value)))
            if key == "ENGINE_VOLUME":
                return self._ok(api_client.set_volume(int(value)))
            if key == "AUDIO_REACTIVE_DEFAULT":
                return self._ok(api_client.set_audio(bool(value)))
            if key == "MOUSE_DEFAULT":
                return self._ok(api_client.set_mouse(bool(value)))
            if key == "PARALLAX_DEFAULT":
                return self._ok(api_client.set_parallax(bool(value)))
            if key == "PARTICLES_DEFAULT":
                return self._ok(api_client.set_particles(bool(value)))
            if key == "ENGINE_FPS":
                text = str(value).strip()
                return True if text == "" else self._ok(api_client.set_fps(int(text)))
            if key == "APP_CONDITION_BEHAVIOR":
                return self._ok(api_client.set_app_conditions(
                    self._backend._app_condition_names(), str(value)))
            if key in C.AUDIO_DIAL_ENV:
                field = next(f for f, k in C.AUDIO_DIAL_KEYS.items() if k == key)
                return self._ok(api_client.set_tuning(**{field: float(value)}))
        except Exception:
            return False
        return True

    @staticmethod
    def _ok(reply: Any) -> bool:
        return bool(isinstance(reply, dict) and reply.get("ok"))

    def _regenerate(self) -> bool:
        """Rewrite the engine env file so a SERVICE-RESTART key means
        something at all. The restart itself is NOT taken - deliberately left open, so the
        change lands in the file and the user restarts. Safe only because U4 landed first.
        """
        try:
            daemon_unit.write_files()
            return True
        except Exception:
            return False

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
        """Push one dial engine-native, then persist on confirmation. Same store as the
        editor's identical row: one fact, two doors, one store."""
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
            target = paths.state_dir()
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


    @Slot(str, result=bool)
    def exportConfig(self, url: str) -> bool:
        if not bool(self._backend.exportConfig(url)):
            return self._fail("Configuration", "The backup could not be written.")
        return True

    @Slot(str, result=bool)
    def importConfig(self, url: str) -> bool:
        if not bool(self._backend.importConfig(url)):
            return self._fail("Configuration", "That folder is not an LWE backup.")
        self.changed.emit()
        self.truthRefreshed.emit()
        return True

    @Slot(result=bool)
    def resetConfig(self) -> bool:
        if not bool(self._backend.resetConfig()):
            return self._fail("Configuration", "Settings could not be reset.")
        self.changed.emit()
        self.truthRefreshed.emit()
        return True

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

    def _write_exceptions(self, entries: list[str]) -> bool:
        path = paths.config_dir() / "pause-blacklist.txt"
        header = "# fullscreen app_ids exempt from pause, one per line; e.g. steam\n"
        try:
            path.write_text(header + "".join(e + "\n" for e in entries), encoding="utf-8")
        except OSError:
            return False
        try:
            if api_client.available():
                api_client.set_fullscreen_ignore(entries)
        except Exception:
            pass
        self.truthRefreshed.emit()
        return True

    @Slot(str, result=bool)
    def addException(self, app_id: str) -> bool:
        entry = str(app_id).strip()[:128]
        if not entry or entry.startswith("#"):
            return self._fail("Exceptions", "That is not an app id.")
        current = self.exceptions()
        if entry in current:
            return True
        if not self._write_exceptions(current + [entry]):
            return self._fail("Exceptions", "The exceptions file could not be written.")
        return True

    @Slot(str, result=bool)
    def removeException(self, app_id: str) -> bool:
        entry = str(app_id).strip()
        remaining = [e for e in self.exceptions() if e != entry]
        if not self._write_exceptions(remaining):
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

    def _write_app_list(self, entries: list[str]) -> bool:
        header = "# processes that trigger the running-apps rule, one comm name per line\n"
        try:
            (paths.config_dir() / "app-condition.txt").write_text(
                header + "".join(e + "\n" for e in entries), encoding="utf-8")
        except OSError:
            return False
        # the engine owns the poll now: a list edit must reach it live, not wait for
        # the next reconnect push
        try:
            api_client.set_app_conditions(
                entries, str(self._load().get("APP_CONDITION_BEHAVIOR") or "off"))
        except Exception:
            pass
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
        current = self.appEntries()
        if entry in current:
            return True
        if not self._write_app_list(current + [entry]):
            return self._fail("Apps", "The app list file could not be written.")
        return True

    @Slot(str, result=bool)
    def removeAppEntry(self, name: str) -> bool:
        entry = str(name).strip()
        remaining = [e for e in self.appEntries() if e != entry]
        if not self._write_app_list(remaining):
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
            "socketLive": bool(api_client.available()),
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

    @Slot(result="QVariantList")
    def playlistSlugs(self) -> list:
        """Slugs + names for the Schedule playlist dropdowns (flag-gated section)."""
        try:
            from .storage import playlists
            return [{"slug": p.get("slug", ""), "name": p.get("NAME") or p.get("slug", "")}
                    for p in playlists.list_playlists()]
        except Exception:
            return []

    @Slot(result=bool)
    def restartEngine(self) -> bool:
        """This is never called automatically - it
        exists so the honest row can offer the restart the user chooses to take."""
        try:
            proc = subprocess.run(["systemctl", "--user", "restart", C.ENGINE_SERVICE],
                                  capture_output=True, timeout=20, check=False)
        except (OSError, subprocess.SubprocessError):
            return self._fail("Advanced", "The engine service could not be restarted.")
        if proc.returncode != 0:
            return self._fail("Advanced", "The engine service could not be restarted.")
        return True
