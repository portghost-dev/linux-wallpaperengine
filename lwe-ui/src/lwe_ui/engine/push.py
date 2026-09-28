"""The engine pushes, each resolved from the store and sent over the engine socket: a change's
own push through run_change (the sync marker, the store write, then the requests of the change's
rows), the bundle that rebuilds the engine from the store (sync_all), engine-only actions under
the sync lock, and the rotation set, schedule, fullscreen policy, live globals and single show
the window sends. Plain Python over the store and the resolver; no Qt import.
"""
from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from .. import api_client, version
from .. import constants as C
from ..storage import lock, meta, paths, playlists, settings, tags
from . import daemon_unit, marker
from .resolve import (_conf_true, effective_speed, resolve_fullscreen_behavior, resolve_show_args,
                      resolved_tuning, split_playlist_parts)

_SESSION_KEYS = {"mute": "OVERRIDE_MUTE", "audio": "OVERRIDE_AUDIO_OFF",
                 "parallax": "OVERRIDE_PARALLAX_OFF", "mouse": "OVERRIDE_MOUSE_OFF"}

#: settings whose value feeds resolve_fullscreen_behavior; changing any of them has
#: to reach the RUNNING scene, not wait for the next swap
_FULLSCREEN_KEYS = ("FULLSCREEN_BEHAVIOR", "PAUSE_RECOVERY_ACTION", "PAUSE_RECOVERY_CONDITION")

_LIVE_GLOBAL_KEYS = ("ENGINE_FPS", "PARALLAX_DEFAULT", "PARTICLES_DEFAULT",
                     "OVERRIDE_PARALLAX_OFF", "OVERRIDE_MUTE", "OVERRIDE_MOUSE_OFF",
                     "OVERRIDE_AUDIO_OFF", "ENGINE_TIMESCALE", "ENGINE_VOLUME",
                     "AUDIO_REACTIVE_DEFAULT", "MOUSE_DEFAULT",
                     "APP_CONDITION_BEHAVIOR")


def _active_slug() -> str:
    try:
        return playlists.active_slug()
    except Exception:
        return ""


def _schedule_entries() -> list[dict[str, str]]:
    """The stored SCHEDULE as the engine's entries, in stored order (row 1 is where day
    begins, row 2 where it ends); an entry naming a missing playlist is dropped."""
    packed = str(_setting("SCHEDULE", "") or "")
    out: list[dict[str, str]] = []
    for item in packed.split(";"):
        head, _, slug = item.strip().partition("=")
        head, slug = head.strip(), slug.strip()
        if not head or not slug or not paths.is_safe_wid(slug):
            continue
        if not paths.playlist_file(slug).exists():
            continue
        out.append({"at": head, "playlist": slug})
    return out


def _schedule_enabled() -> bool:
    return bool(_setting("SCHEDULE_ENABLED", False)) and len(_schedule_entries()) >= 2


def _push_schedule() -> bool:
    """Send every scheduled playlist the engine does not already hold from the active push,
    then the schedule itself. Returns False when anything was refused."""
    try:
        entries = _schedule_entries()
        active = _active_slug() or "default"
        for slug in sorted({e["playlist"] for e in entries} - {active}):
            e, interval, order, enabled, label = _playlist_payload(slug)
            parts = split_playlist_parts(e)
            for number, part in enumerate(parts, start=1):
                reply = api_client.playlist_set(slug, part, order, interval, part=number,
                                                of=len(parts), label=label)
                if reply is None or not reply.get("ok"):
                    return False
        reply = api_client.schedule_set(_schedule_enabled(), entries)
        return bool(reply is not None and reply.get("ok"))
    except Exception:
        return False


def _playlist_payload(slug: str) -> tuple[list, int, str, bool, str]:
    """Resolve one stored playlist into the engine's terms: (entries in stored order,
    interval_s, order, enabled, label). Each entry is a complete resolved show-args
    object - the engine executes, never resolves. Member order is the stored order."""
    d = playlists.load(slug)
    entries = []
    for wid in str(d.get("MEMBERS") or "").split():
        if not paths.is_safe_wid(wid):
            continue
        try:
            engine_wid, args = resolve_show_args(wid)
        except Exception:
            continue  # one broken conf must not sink the whole set
        entries.append({"id": engine_wid, "ui_id": wid, **args})
    mode = str(d.get("MODE") or "shuffle")
    order = mode if mode in C.PLAYLIST_MODES else "shuffle"
    enabled = (bool(_setting("ROTATION_ENABLED", True)) and mode != "static"
               and bool(entries))
    try:
        interval = int(d.get("INTERVAL") or 900)
    except (TypeError, ValueError):
        interval = 900
    label = str(d.get("NAME") or slug)
    return entries, interval, order, enabled, label


def _sync_engine(manual: bool = False) -> dict[str, Any]:
    """Push the active playlist to the engine by slug, in parts, then the scheduled
    playlists and the schedule, then bind the lane (policy push). The engine owns the
    walk and the schedule; the panel owns resolution. `manual` marks the user's own
    playlist switch, which the engine holds until the next boundary (R67). Best-effort by
    design: a dead socket is retried by the status poll's pid-change tracker, never
    surfaced to the caller.

    Returns what the caller keeps, each key only when its step was reached: "policy_dirty"
    (True from the socket check until the lane binding is answered), "schedule_refused"
    (from the schedule push) and "rotation_clock" ((next_in_ms, interval_s) from the lanes
    reply)."""
    outcome: dict[str, Any] = {}
    try:
        if not api_client.available():
            outcome["policy_dirty"] = True
            return outcome
        outcome["policy_dirty"] = True   # cleared below, once the lane binding was answered
        slug = _active_slug() or "default"
        entries, interval, order, enabled, label = _playlist_payload(slug)
        parts = split_playlist_parts(entries)
        for number, part in enumerate(parts, start=1):
            reply = api_client.playlist_set(slug, part, order, interval, part=number,
                                            of=len(parts), label=label)
            if reply is None or not reply.get("ok"):
                return outcome  # a refused part must never bind a half-sent playlist
        # a refused or unknown schedule must not leave the lane unbound, but the cell
        # must not claim a schedule the engine did not take
        outcome["schedule_refused"] = not _push_schedule()
        lane: dict[str, Any] = {"id": "all", "playlist": slug, "enabled": enabled}
        if manual:
            lane["manual"] = True
        reply = api_client.lanes_set([lane])
        try:
            # the reply is the envelope: {ok, result: {lanes: [...]}}; an unanswered push
            # (socket gone mid-way) leaves the policy marked for the next poll
            if isinstance(reply, dict) and reply.get("ok"):
                outcome["policy_dirty"] = False
            result = reply.get("result") if isinstance(reply, dict) and reply.get("ok") else None
            lanes = result.get("lanes") if isinstance(result, dict) else None
            if isinstance(lanes, list) and lanes:
                ms, iv = lanes[0].get("next_in_ms"), lanes[0].get("interval_s")
                if isinstance(ms, (int, float)) and int(ms) >= 0:
                    outcome["rotation_clock"] = (int(ms), int(iv) if isinstance(iv, (int, float)) else -1)
        except Exception:
            pass
    except Exception:
        pass
    return outcome


def effectiveSpeed(factor: float) -> float:
    """The rate the engine should run for the wallpaper on screen under `factor`."""
    return effective_speed(_current_ui_wid(), factor)


def _current_ui_wid() -> str:
    """The ui_id of whatever the daemon is showing right now, "" when idle/down."""
    try:
        api = api_client.status()
        cur = (api or {}).get("current") or {}
        return str(cur.get("ui_id") or "")
    except Exception:
        return ""


def show(wid: str) -> bool:
    """Show `wid` with its resolved arguments, then send its resolved audio dials with
    set-tuning. True when the engine accepted the show."""
    try:
        engine_wid, show_args = resolve_show_args(wid)
        reply = api_client.show(engine_wid, ui_id=wid, **show_args)
        if reply is not None and reply.get("ok"):
            try:
                api_client.set_tuning(**resolved_tuning(wid))
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False


def _setting(key: str, default: Any) -> Any:
    try:
        return settings.load().get(key, default)
    except Exception:
        return default


def _fullscreen_ignore_ids() -> list[str]:
    """app_ids exempt from the fullscreen policy, from the pause-blacklist file."""
    try:
        text = (paths.config_dir() / "pause-blacklist.txt").read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            out.append(entry[:128])
    return out[:128]


def _app_condition_names() -> list[str]:
    """Process names (comm) for the engine's running-apps condition, from the
    hand-edited list file. NOT the fullscreen exceptions list - comm names and
    window app_ids are different identifier spaces and must never merge."""
    try:
        text = (paths.config_dir() / "app-condition.txt").read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            out.append(entry[:64])
    return out[:128]


def _push_live_globals() -> None:
    """Push the engine-global toggles to the running engine.

    These are NOT per-wallpaper, so they never ride a show and a restarted engine
    knows nothing about them - hence this is also called from the pid-change
    reconnect. Idempotent and best-effort: a dead socket is picked up by the next
    status poll, never surfaced.

    ENGINE_FPS empty means "whatever the engine launched with", so there is nothing
    to push; it takes effect on the next service restart.
    """
    try:
        s = settings.load()
        if not api_client.available():
            return

        fps = str(s.get("ENGINE_FPS") or "").strip()
        if fps:
            try:
                api_client.set_fps(max(1, min(480, int(fps))))
            except ValueError:
                pass

        # the engine holds one speed number, the resolved rate of the wallpaper on screen, so the global
        # speed is pushed through the same resolve as a show. independently tolerant, like every other
        # push in this method: one verb that cannot answer must never cost the rest of the fan-out
        try:
            api_client.set_speed(effectiveSpeed(float(s.get("ENGINE_TIMESCALE") or 1.0)))
        except Exception:
            pass

        parallax = _conf_true(s.get("PARALLAX_DEFAULT"), True) and not _conf_true(
            s.get("OVERRIDE_PARALLAX_OFF"), False)
        api_client.set_parallax(parallax)
        api_client.set_particles(_conf_true(s.get("PARTICLES_DEFAULT"), True))
        api_client.set_fullscreen_ignore(_fullscreen_ignore_ids())
        # a restarted engine restores conditions from its own state file; this push
        # covers the fresh-install boot and any hand-edit of the list file
        api_client.set_app_conditions(
            _app_condition_names(),
            str(s.get("APP_CONDITION_BEHAVIOR") or "off"))

        # mute + mouse (v1.10 sec 4: set-volume/set-mouse ship). These are NOT globals - the honest live value
        # is the CURRENT wallpaper's resolved one, so it is computed by the same resolve_show_args every show
        # uses. No wallpaper showing (idle daemon) means nothing to retune; the next show carries the override.
        wid = _current_ui_wid()
        if wid:
            _, args = resolve_show_args(wid)
            if "volume" in args:
                api_client.set_volume(int(args["volume"]))
            if "mouse" in args:
                api_client.set_mouse(bool(args["mouse"]))
            if "audio_processing" in args:
                api_client.set_audio(bool(args["audio_processing"]))
    except Exception:
        pass


def _push_fullscreen_behavior() -> None:
    """Apply the fullscreen policy to the live engine.

    The verb changes the RUNNING scene: turning the mode off un-latches a pause or
    hands the outputs back at once, instead of waiting for the next swap.

    The rotation set also has to be refreshed whenever this changes, because every
    stored entry carries its own resolved copy and the next timed advance would
    otherwise restore the old policy. That is NOT done here: every caller already
    follows a settings write with _sync_engine(), and doing it in both places
    pushed 54 entries twice per change.
    """
    try:
        s = settings.load()
        if not api_client.available():
            return
        api_client.set_fullscreen(resolve_fullscreen_behavior(s))
    except Exception:
        pass


_log = logging.getLogger(__name__)

_WINDOW_BUDGET_S = 8.0
_READY_POLL_S = 0.25

_LIVE_VERBS = {"ENGINE_TIMESCALE": "speed", "ENGINE_VOLUME": "volume", "OVERRIDE_MUTE": "volume",
               "OVERRIDE_AUDIO_OFF": "audio", "AUDIO_REACTIVE_DEFAULT": "audio",
               "OVERRIDE_MOUSE_OFF": "mouse", "MOUSE_DEFAULT": "mouse",
               "FULLSCREEN_BEHAVIOR": "fullscreen", "PAUSE_RECOVERY_ACTION": "fullscreen",
               "PAUSE_RECOVERY_CONDITION": "fullscreen"}
_WP_VERBS = {"SPEED": "speed", "VOLUME": "volume", "AUDIO_REACTIVE": "audio", "MOUSE": "mouse",
             "FULLSCREEN_PAUSE": "fullscreen", **dict.fromkeys(C.FIT_FIELDS.values(), "fit"),
             "SKIP": "skip", "AUDIO_GAIN": "tuning", "CLASSIC_K": "tuning", "CLASSIC_EXP": "tuning"}
_SETTERS = {"speed": ("set_speed", "speed"), "volume": ("set_volume", "volume"),
            "audio": ("set_audio", "audio_processing"), "mouse": ("set_mouse", "mouse"),
            "fullscreen": ("set_fullscreen", "fullscreen_behavior")}

SETTING_ROWS: dict[str, str] = {
    "ACTIVE_PLAYLIST": "active",
    "ROTATION_ENABLED": "pause",
    "SCHEDULE": "schedule",
    "SCHEDULE_ENABLED": "schedule",
    **dict.fromkeys(("ENGINE_FPS", "PARALLAX_DEFAULT", "OVERRIDE_PARALLAX_OFF", "PARTICLES_DEFAULT",
                     "APP_CONDITION_BEHAVIOR", "app-condition.txt", "pause-blacklist.txt"), "verb"),
    **dict.fromkeys(_LIVE_VERBS, "live"),
    **dict.fromkeys(("ENGINE_AUDIO_GAIN", "ENGINE_CLASSIC_K", "ENGINE_CLASSIC_EXP"), "tuning"),
    **dict.fromkeys(C.REACH_NEXT_SHOW, "next_show"),
    **dict.fromkeys(C.REACH_SERVICE_RESTART, "restart"),
}
#: every PROP_<name> key is wp_build as well; FPS, CC_MODE and the keys not listed have row none
WP_ROWS: dict[str, str] = {
    **dict.fromkeys(_WP_VERBS, "wp_live"),
    **dict.fromkeys(("CC", "SCALING", "CLAMPING", "AUTOMUTE", "RENDER_RESOLUTION", "TEXCOMP",
                     "TEXTURE_DETAIL", "SSFACTOR", "CLAMPCOMPOSITES"), "wp_build"),
}


class SwitchRefused(Exception):
    """A manual playlist switch while the stored schedule is on and the engine gave no status:
    refused before any lock, store write or marker change."""


@dataclass(frozen=True)
class Outcome:
    """How a change or a sync ended: kind "applied" (every request ok; reason "no engine side" when
    the change had nothing to send), "pending" (reason busy, away, unresponsive, version or budget),
    "refused" (message quotes the engine's first refusal) or "uncertain" (a request got no final
    reply, or one that was neither a done ok nor ok false). message also carries a version refusal;
    env is the engine-env write of a tuning or restart change; recorded counts the playlists a
    budget stop recorded in the marker; warning says the marker could not be cleared."""
    kind: str
    reason: str | None = None
    message: str | None = None
    env: str | None = None
    recorded: int = 0
    warning: str | None = None


@dataclass(frozen=True)
class Ticket:
    """A saved change waiting for deliver(): the marker generation its write set (None when the
    change has nothing to send), whether a marker existed before, its rows, wallpaper and playlist,
    the run kind and the engine-env write."""
    generation: int | None
    existed: bool
    rows: tuple[tuple[str, str | None], ...]
    wid: str | None = None
    slug: str | None = None
    run: str = "window"
    env: str | None = None


def read_status() -> tuple[str, dict[str, Any] | None]:
    """One status read: ("ok", status), ("away", None) when nothing ran, or ("unresponsive", None)
    for any other failed read."""
    status = api_client.status()
    if isinstance(status, dict):
        return "ok", status
    return ("away" if api_client.last_class() == "away" else "unresponsive"), None


def derived_active(status: dict[str, Any] | None) -> tuple[str | None, str]:
    """The playlist the engine plays for the panel, never written: the lane's binding while status
    answered with the schedule on and that playlist's file exists ("engine"), else the saved active
    playlist when its file exists, or None ("saved")."""
    if isinstance(status, dict):
        schedule, lanes = status.get("schedule"), status.get("lanes")
        if isinstance(schedule, dict) and schedule.get("enabled") and isinstance(lanes, list) and lanes \
                and isinstance(lanes[0], dict):
            bound = str(lanes[0].get("playlist") or "")
            if bound and paths.playlist_file(bound).exists():
                return bound, "engine"
    return playlists.active_slug(validate=True) or None, "saved"


def engine_only() -> contextlib.AbstractContextManager[None]:
    """The sync hold an engine-only action sends under, with the 2.0 s wait; its StoreBusy refuses
    the action as busy."""
    return lock.held("sync")


def show_final(wid: str, tuned: Callable[[dict[str, Any] | None], Any] | None = None) -> dict[str, Any] | None:
    """Show `wid` with its resolved arguments and wait for the load to finish; set-tuning follows
    only a done ok, and its reply goes to `tuned` when given. Returns the final reply, None when
    the engine never answered."""
    engine_wid, show_args = resolve_show_args(wid)
    reply = api_client.show(engine_wid, wait_done=True, ui_id=wid, **show_args)
    if reply is not None and _reply_class(reply) == "ok":
        tuning = api_client.set_tuning(**resolved_tuning(wid))
        if tuned is not None:
            tuned(tuning)
    return reply


def wait_ready(old_pid: int | None = None, timeout_s: float = 20.0) -> dict[str, Any] | None:
    """Read status every 250 ms for up to `timeout_s`; the first ok status whose pid differs from
    `old_pid`, or None."""
    deadline = time.monotonic() + timeout_s
    while True:
        cls, status = read_status()
        if cls == "ok" and status.get("pid") != old_pid:
            return status
        if time.monotonic() >= deadline:
            return None
        time.sleep(_READY_POLL_S)


def _reply_class(reply: dict[str, Any] | None) -> str:
    """The class of one request, as api_client.reply_class and last_class give it: "ok" only
    for a done reply with ok true, "refused" for ok false, "uncertain" for any other reply or for
    none after the connect, "away" when nothing ran."""
    if isinstance(reply, dict):
        if reply.get("ok") is True and reply.get("status") == "done":
            return "ok"
        return "refused" if reply.get("ok") is False else "uncertain"
    return "away" if api_client.last_class() == "away" else "uncertain"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _on_screen(status: dict[str, Any] | None) -> str:
    current = (status or {}).get("current")
    return str(current.get("ui_id") or "") if isinstance(current, dict) else ""


def _scheduled() -> list[str]:
    """The playlists the stored SCHEDULE names, in stored order, each once."""
    return list(dict.fromkeys(entry["playlist"] for entry in _schedule_entries()))


class _Run:
    """One delivery under the sync hold. It sends requests in order and keeps how they ended: an
    uncertain or away reply ends the run, a refusal is kept (the first message) and the run goes on,
    and a window run stops at its budget, which is checked between steps and between playlists."""

    def __init__(self, run: str, generation: int, status: dict[str, Any]) -> None:
        self.window = run == "window"
        self.generation = generation
        self.status = status
        self.started = time.monotonic()
        self.ended: str | None = None
        self.refused: str | None = None
        self.all_ok = True
        self.stopped = False
        self.recorded = 0
        self.schedule: bool | None = None
        self._payloads: dict[str, tuple] = {}

    def send(self, call: Callable[..., Any], *args: Any, **kwargs: Any) -> bool:
        """Send one request unless the run has ended; True when it ended ok."""
        if self.ended is not None:
            return False
        return self.note(call(*args, **kwargs))

    def note(self, reply: dict[str, Any] | None) -> bool:
        """Keep how one reply ended; True when it is ok."""
        cls = _reply_class(reply)
        if cls == "ok":
            return True
        self.all_ok = False
        if cls == "refused":
            if self.refused is None:
                self.refused = str(reply.get("error") or "")
        else:
            self.ended = cls
        return False

    def halted(self) -> bool:
        """True once the run has ended, or a window run has spent its budget."""
        if self.ended is None and self.window and time.monotonic() - self.started >= _WINDOW_BUDGET_S:
            self.stopped = True
        return self.ended is not None or self.stopped

    def payload(self, slug: str) -> tuple:
        if slug not in self._payloads:
            self._payloads[slug] = _playlist_payload(slug)
        return self._payloads[slug]

    def enabled(self, slug: str | None) -> bool:
        return bool(slug) and bool(self.payload(slug)[3])

    def schedule_on(self) -> bool:
        """Whether the engine's schedule is on: as this run's last schedule-set that ended ok
        left it, else as the status read inside sync reported it."""
        if self.schedule is not None:
            return self.schedule
        schedule = self.status.get("schedule")
        return isinstance(schedule, dict) and bool(schedule.get("enabled"))


def _send_schedule(run: _Run) -> None:
    """schedule-set with the stored schedule; one that ends ok is the schedule state the run
    leaves in the engine."""
    enabled = _schedule_enabled()
    if run.send(api_client.schedule_set, enabled, _schedule_entries()):
        run.schedule = enabled


def _transfer(run: _Run, slug: str) -> bool:
    """Send one playlist by its slug in numbered parts; True when every part ended ok. A part that
    did not end ok ends the transfer."""
    entries, interval, order, _enabled, label = run.payload(slug)
    parts = split_playlist_parts(entries)
    for number, part in enumerate(parts, start=1):
        if not run.send(api_client.playlist_set, slug, part, order, interval, part=number,
                        of=len(parts), label=label):
            return False
    return True


def _verb(run: _Run, key: str, s: dict[str, Any]) -> None:
    """The verb row's request for `key`: set-fps (nothing for an empty ENGINE_FPS), set-parallax,
    set-particles, set-app-conditions or set-fullscreen-ignore."""
    if key == "ENGINE_FPS":
        fps = str(s.get("ENGINE_FPS") or "").strip()
        if fps:
            try:
                value = max(1, min(480, int(fps)))
            except ValueError:
                return
            run.send(api_client.set_fps, value)
    elif key in ("PARALLAX_DEFAULT", "OVERRIDE_PARALLAX_OFF"):
        run.send(api_client.set_parallax, _conf_true(s.get("PARALLAX_DEFAULT"), True)
                 and not _conf_true(s.get("OVERRIDE_PARALLAX_OFF"), False))
    elif key == "PARTICLES_DEFAULT":
        run.send(api_client.set_particles, _conf_true(s.get("PARTICLES_DEFAULT"), True))
    elif key == "pause-blacklist.txt":
        run.send(api_client.set_fullscreen_ignore, _fullscreen_ignore_ids())
    elif key in ("APP_CONDITION_BEHAVIOR", "app-condition.txt"):
        run.send(api_client.set_app_conditions, _app_condition_names(),
                 str(s.get("APP_CONDITION_BEHAVIOR") or "off"))


def _live(run: _Run, verb: str, wid: str, args: dict[str, Any]) -> None:
    """One live verb with its value resolved for `wid`, the wallpaper on screen, whose resolved show
    arguments are `args`."""
    if verb == "tuning":
        run.send(api_client.set_tuning, **resolved_tuning(wid))
    elif verb == "fit":
        run.send(api_client.set_fit, layer="wallpaper", id=wid, **args["fit"])
    elif verb == "skip":
        run.send(api_client.set_skip, args.get("skip_objects", []))
    else:
        name, field = _SETTERS[verb]
        run.send(getattr(api_client, name), args[field])


def _reshow(run: _Run, wid: str) -> None:
    """One re-show of `wid` that keeps the speed read inside sync: show_final, whose set-tuning
    reply counts as one of the run's requests, then set-skip when the wallpaper lists skips, since
    a show clears the skip list, then set-speed with the read speed, whatever the show's outcome."""
    speed = run.status.get("speed")
    run.note(show_final(wid, tuned=run.note))
    skips = resolve_show_args(wid)[1].get("skip_objects")
    if skips:
        run.note(api_client.set_skip(skips))
    if _is_number(speed):
        run.note(api_client.set_speed(speed))


def _holds_current() -> bool:
    """Whether the marker holds CURRENT; a marker that cannot be read counts as holding it."""
    try:
        return "CURRENT" in marker.read()["classes"]
    except OSError:
        return True


def _bundle(run: _Run, derived: str | None, reshow: bool, reload: bool = False) -> None:
    """sync_all's steps 2 to 7 on the run's generation and status. A window run records each
    playlist it transferred in the marker and skips the ones an earlier run of this generation
    recorded for the same engine pid. Step 7 re-shows when the marker holds CURRENT, and for a
    reload's run whatever the marker holds, since that re-show is the reload's own action."""
    scheduled = _scheduled()
    pid = run.status.get("pid")
    skip: set[str] = set()
    if run.window:
        try:
            marker.start_sent(run.generation, pid)
            skip = set(marker.sent_for(run.generation, pid))
        except OSError:
            skip = set()
    failed: set[str] = set()
    for slug in dict.fromkeys(([derived] if derived else []) + scheduled):
        if run.halted():
            return
        if slug in skip:
            continue
        if not _transfer(run, slug):
            failed.add(slug)
        elif run.window:
            try:
                if marker.record_sent(run.generation, pid, slug):
                    run.recorded += 1
            except OSError:
                pass
    if run.halted():
        return
    if not failed.intersection(scheduled):
        _send_schedule(run)
    if run.halted():
        return
    if derived not in failed:
        lane: dict[str, Any] = {"id": "all"}
        if derived:
            lane["playlist"] = derived
        lane["enabled"] = run.enabled(derived)
        run.send(api_client.lanes_set, [lane])
    if run.halted():
        return
    s = settings.load()
    for key in ("ENGINE_FPS", "PARALLAX_DEFAULT", "PARTICLES_DEFAULT", "pause-blacklist.txt",
                "APP_CONDITION_BEHAVIOR"):
        _verb(run, key, s)
    if run.halted():
        return
    wid = _on_screen(run.status)
    if wid:
        _, args = resolve_show_args(wid)
        speed = run.status.get("speed")
        frozen = _is_number(speed) and speed == 0
        for verb in ("fullscreen", "speed", "volume", "mouse", "audio", "tuning", "fit", "skip"):
            if verb != "speed" or not frozen:
                _live(run, verb, wid, args)
    else:
        run.send(api_client.set_fullscreen, resolve_fullscreen_behavior(s))
        run.send(api_client.set_tuning, **resolved_tuning(""))
    if reshow and wid and not run.halted() and (reload or _holds_current()):
        _reshow(run, wid)


def _carried(row: str, key: str | None, ticket: Ticket, derived: str | None, held: list[str],
             active: str | None) -> list[str]:
    """The playlists one row's entry refresh sends."""
    if row == "active":
        return [active] if active else []
    if row == "policy":
        target = ticket.slug or derived
        return [target] if target else []
    if row == "members":
        return [ticket.slug] if ticket.slug in held else []
    if row == "schedule":
        return _scheduled()
    if row in ("live", "next_show"):
        return held
    if row == "wp_build" or (row == "wp_live" and _WP_VERBS.get(key) != "tuning"):
        return [slug for slug in held if ticket.wid in playlists.members(slug)]
    return []


def _own(run: _Run, row: str, key: str | None, ticket: Ticket, derived: str | None, screen: str,
         active: str | None, sent: dict[str, bool], s: dict[str, Any]) -> None:
    """One row's own verb, resolved from the store and, for a live value, for the wallpaper on
    screen."""
    if row == "active":
        if active and sent.get(active):
            lane: dict[str, Any] = {"id": "all", "playlist": active, "enabled": run.enabled(active)}
            if run.schedule_on():
                lane["manual"] = True
            run.send(api_client.lanes_set, [lane])
    elif row == "pause":
        run.send(api_client.lanes_set, [{"id": "all", "enabled": run.enabled(derived)}])
    elif row in ("policy", "members"):
        target = ticket.slug or derived
        if sent.get(target) and (row == "policy" or target == derived):
            run.send(api_client.lanes_set, [{"id": "all", "enabled": run.enabled(derived)}])
    elif row == "schedule":
        if all(sent.get(slug) for slug in _scheduled()):
            _send_schedule(run)
    elif row == "verb":
        _verb(run, str(key), s)
    elif row == "live":
        verb = _LIVE_VERBS[str(key)]
        if screen:
            _live(run, verb, screen, resolve_show_args(screen)[1])
        elif verb == "fullscreen":
            run.send(api_client.set_fullscreen, resolve_fullscreen_behavior(s))
        elif verb == "speed":
            run.send(api_client.set_speed, effective_speed(""))
    elif row == "tuning":
        run.send(api_client.set_tuning, **resolved_tuning(screen))
    elif row == "wp_live" and ticket.wid and ticket.wid == screen:
        _live(run, _WP_VERBS[str(key)], screen, resolve_show_args(screen)[1])


def _deliberate_speed(row: str, key: str | None) -> bool:
    """Whether the row's own verb is a deliberate set-speed: ENGINE_TIMESCALE or a wallpaper's
    SPEED."""
    verbs = _LIVE_VERBS if row == "live" else _WP_VERBS if row == "wp_live" else {}
    return verbs.get(str(key)) == "speed"


def _rows(run: _Run, ticket: Ticket, derived: str | None, reshow: bool) -> None:
    """The change's own push: the entry refresh of every playlist its rows carry, each sent once,
    then each row's own verb in order, and last the one re-show of a wallpaper build change,
    followed by the change's deliberate set-speed so the re-show's restored speed never
    replaces it."""
    held = list(dict.fromkeys(([derived] if derived else []) + _scheduled()))
    active = ticket.slug or playlists.active_slug(validate=True) or None
    refresh: dict[str, None] = {}
    for row, key in ticket.rows:
        for slug in _carried(row, key, ticket, derived, held, active):
            refresh.setdefault(slug)
    sent: dict[str, bool] = {}
    for slug in refresh:
        if run.ended is not None:
            return
        sent[slug] = _transfer(run, slug)
    s = settings.load()
    screen = _on_screen(run.status)
    after = [(row, key) for row, key in ticket.rows if reshow and _deliberate_speed(row, key)]
    for row, key in [pair for pair in ticket.rows if pair not in after]:
        if run.ended is not None:
            return
        _own(run, row, key, ticket, derived, screen, active, sent, s)
    if reshow and run.ended is None:
        _reshow(run, str(ticket.wid))
    for row, key in after:
        if run.ended is not None:
            return
        _own(run, row, key, ticket, derived, screen, active, sent, s)


def _version_refusal(status: dict[str, Any]) -> str | None:
    try:
        return version.running_refusal(status, version.panel_stamp())
    except version.StampError as exc:
        return str(exc)


def _synced(stack: contextlib.ExitStack, wait_s: float, env: str | None) -> dict[str, Any] | Outcome:
    """Take sync on `stack`, read status inside it and check the running engine's version: the
    status, or the pending outcome that stops the run with nothing sent."""
    try:
        stack.enter_context(lock.held("sync", wait_s=wait_s))
    except lock.StoreBusy:
        return Outcome("pending", reason="busy", env=env)
    cls, status = read_status()
    if cls != "ok" or status is None:
        return Outcome("pending", reason=cls, env=env)
    refusal = _version_refusal(status)
    if refusal is not None:
        return Outcome("pending", reason="version", message=refusal, env=env)
    return status


def _finish(run: _Run, env: str | None = None) -> Outcome:
    """Clear the marker when every request ended ok, and name the outcome."""
    warning = None
    if run.all_ok and not run.stopped:
        try:
            marker.clear(run.generation)
        except OSError as exc:
            warning = f"the sync marker could not be cleared: {exc}"
    if run.ended == "uncertain":
        return Outcome("uncertain", env=env)
    if run.ended is not None:
        return Outcome("pending", reason=run.ended, env=env)
    if run.stopped:
        _log.warning("engine sync stopped at the window's %.0f s budget with %d playlists recorded",
                     _WINDOW_BUDGET_S, run.recorded)
        return Outcome("pending", reason="budget", env=env, recorded=run.recorded)
    if run.refused is not None:
        return Outcome("refused", message=run.refused, env=env)
    return Outcome("applied", env=env, warning=warning)


def _deliver(ticket: Ticket) -> Outcome:
    if ticket.generation is None:
        return Outcome("applied", reason="no engine side", env=ticket.env)
    with contextlib.ExitStack() as stack:
        status = _synced(stack, lock.LOCK_WAIT_S, ticket.env)
        if isinstance(status, Outcome):
            return status
        run = _Run(ticket.run, ticket.generation, status)
        derived = derived_active(status)[0]
        rows = [row for row, _key in ticket.rows]
        reshow = bool(ticket.wid) and ticket.wid == _on_screen(status) and "wp_build" in rows
        if ticket.existed or "reload" in rows:
            _bundle(run, derived, reshow=not reshow, reload="reload" in rows)
        if run.ended is None:
            _rows(run, ticket, derived, reshow)
        return _finish(run, ticket.env)


@contextlib.contextmanager
def _store_locks(names: Iterable[str]) -> Iterator[None]:
    """The store locks in rank order, tags and meta through their held()."""
    with contextlib.ExitStack() as stack:
        for name in sorted(set(names), key=lock._ORDER.index):
            stack.enter_context(tags.held() if name == "tags" else meta.held() if name == "meta"
                                else lock.held(name))
        yield


def _read_first(rows: tuple, manual: bool,
                status: tuple[str, dict[str, Any] | None] | None) -> tuple[str, dict[str, Any] | None] | None:
    """The status read, before any lock: one read unless given (none for a change with no engine
    side). A manual switch while the stored schedule is on and that read is not ok is refused."""
    if all(row == "none" for row, _key in rows):
        return None
    first = read_status() if status is None else status
    if manual and first[0] != "ok" and _schedule_enabled():
        raise SwitchRefused("the schedule is on and the engine did not answer, so the playlist switch "
                            "cannot be made now")
    return first


def _write_env() -> str:
    try:
        return daemon_unit.write_env()
    except Exception as exc:
        return str(exc) or type(exc).__name__


def _write_step(rows: tuple, write: Callable[[], Any], wid: str | None, slug: str | None, run: str,
                first: tuple[str, dict[str, Any] | None] | None) -> Ticket:
    """Inside the store locks: the marker, held across the store write; engine-env for
    a tuning or restart row after it. A change that is only restart or none sets no marker."""
    kinds = {row for row, _key in rows}
    if kinds <= {"restart", "none"}:
        write()
        return Ticket(None, False, rows, wid, slug, run, _write_env() if "restart" in kinds else None)
    classes = ["BUNDLE"]
    if "reload" in kinds or ("wp_build" in kinds and (first is None or first[0] != "ok"
                                                      or _on_screen(first[1]) == wid)):
        classes.append("CURRENT")
    with marker.writing(classes) as (generation, existed):
        write()
    env = _write_env() if kinds & {"tuning", "restart"} else None
    return Ticket(generation, existed, rows, wid, slug, run, env)


def save_change(locks: Iterable[str], write: Callable[[], Any], rows: Iterable[tuple[str, str | None]],
                *, wid: str | None = None, slug: str | None = None, manual: bool = False,
                status: tuple[str, dict[str, Any] | None] | None = None, run: str = "window") -> Ticket:
    """The first half of run_change: the status read, the store locks, the marker and the store write;
    the locks are released when it returns. deliver(ticket) sends the push."""
    rows = tuple(rows)
    first = _read_first(rows, manual, status)
    with _store_locks(locks):
        return _write_step(rows, write, wid, slug, run, first)


def deliver(ticket: Ticket) -> Outcome:
    """The second half of run_change for a saved change: sync, the status and version check inside it,
    the bundle first when a marker existed, the change's push, and the clear when every request
    ended ok."""
    return _deliver(ticket)


def run_change(locks: Iterable[str], write: Callable[[], Any], rows: Iterable[tuple[str, str | None]],
               *, wid: str | None = None, slug: str | None = None, manual: bool = False,
               status: tuple[str, dict[str, Any] | None] | None = None, run: str = "window") -> Outcome:
    """One change from any caller. rows are (row, key) pairs: the row from SETTING_ROWS,
    WP_ROWS or active, policy, members, reload and none; the key the verb, live, tuning and
    wallpaper rows name (None where a row names none). locks names every store write touches;
    status is a read_status() result already taken. Status is read before any lock, and it raises
    SwitchRefused for a manual switch it cannot make; the store locks are taken in rank order; the
    marker is set and held across write(); engine-env is written for a tuning or restart row; the
    store locks are released, highest rank first; then sync, the status and version check inside
    it, the bundle first when a marker existed, this change's push with each row's own verb last,
    also after a bundle the window's budget stopped, and the clear when every request ended ok. An
    exception from write() reaches the caller with nothing sent, and a marker already set stays."""
    return _deliver(save_change(locks, write, rows, wid=wid, slug=slug, manual=manual, status=status, run=run))


def sync_all(run: str, classes: Iterable[str] = ("BUNDLE",), wait_s: float = 2.0) -> Outcome:
    """Rebuild the engine from the store under one sync hold. The marker is ensured first,
    keeping an existing generation unless `classes` adds a class it did not hold; a status that is
    not ok, or another build's engine, returns pending with nothing sent. Then every engine-held
    playlist, the schedule, the lane, the global verbs, the live values of the wallpaper on screen
    with speed left out while the engine reports 0, and a re-show when the marker holds CURRENT or
    `classes` names CURRENT (a reload's run); the marker clears when every request ended ok. A
    "window" run keeps an 8 s budget and records its progress in the marker; a "command" run has
    neither."""
    classes = tuple(classes)
    generation = marker.ensure(classes)
    with contextlib.ExitStack() as stack:
        status = _synced(stack, wait_s, None)
        if isinstance(status, Outcome):
            return status
        r = _Run(run, generation, status)
        _bundle(r, derived_active(status)[0], reshow=True, reload="CURRENT" in classes)
        return _finish(r)
