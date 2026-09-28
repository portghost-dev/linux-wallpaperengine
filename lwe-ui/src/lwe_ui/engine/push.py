"""The engine pushes, each resolved from the store and sent over the engine socket: a change's
own push through run_change (the sync marker, the store write, then the requests of the change's
rows), the bundle that rebuilds the engine from the store (sync_all), engine-only actions under
the sync lock, and a single show. A delivery sends the whole bundle first to an engine the served
record does not name (its pid, boot and start), the first such bundle to an engine owes its re-show
once (_unserved, _owe), and a bundle that ended all ok, its re-show included, records the engine that
took it (_record_served). Every re-show is marked automatic, so an engine held after a refused restore
holds it and answers {"held": true}, which counts as a suppressed re-show (_reshow); the panel keeps
no brake state. Plain Python over the store and the resolver; no Qt import.
"""
from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
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


_log = logging.getLogger(__name__)

BRAKED = ("The engine took your settings but no wallpaper: it refused to restore after two quick crashes. "
          "Show a wallpaper or pick a playlist to start again.")
_now = time.time
_monotonic = time.monotonic
_brake_notes: list[str] = []

_WINDOW_BUDGET_S = 8.0
_READY_POLL_S = 0.25
_restart_holding = threading.Event()

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
    budget stop recorded in the marker; warning says the marker could not be cleared; clock is the
    lane clock (next_in_ms, interval_s) of the last ok lanes-set reply; refused_verb names the verb
    of the first refused request; generation is the marker generation the run or sync_all worked
    from."""
    kind: str
    reason: str | None = None
    message: str | None = None
    env: str | None = None
    recorded: int = 0
    warning: str | None = None
    clock: tuple[int, int] | None = field(default=None, compare=False)
    refused_verb: str | None = field(default=None, compare=False)
    generation: int | None = field(default=None, compare=False)


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
            if bound and paths.is_safe_wid(bound) and paths.playlist_file(bound).exists():
                return bound, "engine"
    return playlists.active_slug(validate=True) or None, "saved"


def engine_only() -> contextlib.AbstractContextManager[None]:
    """The sync hold an engine-only action sends under, with the 2.0 s wait, none while this
    process's restart_hold is held; its StoreBusy refuses the action as busy."""
    return lock.held("sync")


@contextlib.contextmanager
def restart_hold() -> Iterator[None]:
    """The window restart's sync hold, which lasts through the wait for the new engine and its sync.
    It is a long hold (lock.held), so another thread of this process that asks for sync meanwhile,
    or is already waiting for it, gets StoreBusy at once: a delivery ends pending(busy), and an
    engine-only action or a second restart is refused as busy. The holder's own runs take the lock
    again at once, as the lock is re-entrant per thread."""
    with lock.held("sync", long_hold=True):
        _restart_holding.set()
        try:
            yield
        finally:
            _restart_holding.clear()


def restart_holding() -> bool:
    """Whether this process's restart_hold is held."""
    return _restart_holding.is_set()


def show_final(wid: str, tuned: Callable[[dict[str, Any] | None], Any] | None = None,
               automatic: bool = False) -> dict[str, Any] | None:
    """Show `wid` with its resolved arguments and wait for the load to finish; set-tuning follows
    only a done ok the engine did not hold, and its reply goes to `tuned` when given. automatic marks
    a re-show the panel sends on its own. Returns the final reply, None when the engine never
    answered."""
    engine_wid, show_args = resolve_show_args(wid)
    extra = {"automatic": True} if automatic else {}
    reply = api_client.show(engine_wid, wait_done=True, ui_id=wid, **show_args, **extra)
    if reply is not None and _reply_class(reply) == "ok" and not _held(reply):
        tuning = api_client.set_tuning(**resolved_tuning(wid))
        if tuned is not None:
            tuned(tuning)
    return reply


def wait_ready(old_pid: int | None = None, timeout_s: float = 20.0) -> dict[str, Any] | None:
    """Read status every 250 ms for up to `timeout_s`; the first ok status whose pid is the service's
    MainPID from systemd, once that MainPID is not 0 and not `old_pid` (the MainPID before the
    launch, None when there was none), or None. No other engine counts: not the one a restart
    replaces, and not one started by hand on the socket."""
    deadline = time.monotonic() + timeout_s
    while True:
        cls, status = read_status()
        if cls == "ok" and status.get("pid") != old_pid:
            main = daemon_unit._service_main_pid()
            if main is not None and main != old_pid and status.get("pid") == main:
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


def _verb_of(call: Callable[..., Any]) -> str:
    """The engine verb an api_client call sends: its name in api_client, dashed."""
    name = next((n for n in dir(api_client) if getattr(api_client, n, None) is call),
                getattr(call, "__name__", ""))
    return name.replace("_", "-")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _held(reply: dict[str, Any] | None) -> bool:
    """Whether a show's final reply says the engine held it: a done ok whose result is {"held": true}."""
    result = reply.get("result") if isinstance(reply, dict) else None
    return isinstance(result, dict) and result.get("held") is True


def _on_screen(status: dict[str, Any] | None) -> str:
    current = (status or {}).get("current")
    return str(current.get("ui_id") or "") if isinstance(current, dict) else ""


def _scheduled() -> list[str]:
    """The playlists the stored SCHEDULE names, in stored order, each once."""
    return list(dict.fromkeys(entry["playlist"] for entry in _schedule_entries()))


class _Run:
    """One delivery under the sync hold. It sends requests in order and keeps how they ended: an
    uncertain or away reply ends the run, a refusal is kept (the first message) and the run goes on,
    and a window run stops at its budget, which is checked between steps and between playlists.
    bundled, deferred, reshown, held and released say that its bundle ran, that it left its re-show to a
    due delivery, that every request of a re-show ended ok, that the engine held a re-show, and
    that a manual lanes-set, the user's own switch, ended ok. engine is the answering engine's identity,
    taken once from the status as the run starts (_engine) and kept for the whole run."""

    def __init__(self, run: str, generation: int, status: dict[str, Any]) -> None:
        self.window = run == "window"
        self.generation = generation
        self.status = status
        self.engine = _engine(status)
        self.started = time.monotonic()
        self.ended: str | None = None
        self.refused: str | None = None
        self.all_ok = True
        self.stopped = False
        self.recorded = 0
        self.reply: Any = None
        self.clock: tuple[int, int] | None = None
        self.refused_verb: str | None = None
        self.bundled = False
        self.deferred = False
        self.reshown = False
        self.held = False
        self.released = False
        self._payloads: dict[str, tuple] = {}

    def send(self, call: Callable[..., Any], *args: Any, **kwargs: Any) -> bool:
        """Send one request unless the run has ended; True when it ended ok. The reply is kept."""
        if self.ended is not None:
            return False
        self.reply = call(*args, **kwargs)
        return self.note(self.reply, _verb_of(call) if _reply_class(self.reply) == "refused" else None)

    def note(self, reply: dict[str, Any] | None, verb: str | None = None) -> bool:
        """Keep how one reply ended; True when it is ok. The first refusal keeps its message and
        the verb that was refused."""
        cls = _reply_class(reply)
        if cls == "ok":
            return True
        self.all_ok = False
        if cls == "refused":
            if self.refused is None:
                self.refused = str(reply.get("error") or "")
                self.refused_verb = verb
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


def _send_schedule(run: _Run) -> None:
    """schedule-set with the stored schedule."""
    run.send(api_client.schedule_set, _schedule_enabled(), _schedule_entries())


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


def _lanes_set(run: _Run, lane: dict[str, Any]) -> None:
    """lanes-set for the one lane; an ok reply's lane clock (next_in_ms, interval_s) is kept for
    the deck's countdown, and an ok manual one marks the run released."""
    if not run.send(api_client.lanes_set, [lane]):
        return
    if lane.get("manual"):
        run.released = True
    result = run.reply.get("result")
    lanes = result.get("lanes") if isinstance(result, dict) else None
    first = lanes[0] if isinstance(lanes, list) and lanes and isinstance(lanes[0], dict) else {}
    ms, iv = first.get("next_in_ms"), first.get("interval_s")
    if _is_number(ms) and ms >= 0:
        run.clock = (int(ms), int(iv) if _is_number(iv) else -1)


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
    """One re-show of `wid`, marked automatic, that keeps the speed read inside sync: show_final, whose
    set-tuning reply counts as one of the run's requests, then set-skip when the wallpaper lists skips,
    since a show clears the skip list, then set-speed with the read speed, whatever the show's outcome.
    A re-show the engine held marks the run held and sends none of that tail. The run is marked
    reshown only when every one of these requests ended ok."""
    speed = run.status.get("speed")
    ended: list[bool] = []
    shown = show_final(wid, tuned=lambda reply: ended.append(run.note(reply, "set-tuning")), automatic=True)
    ended.append(run.note(shown, "show"))
    if _held(shown):
        run.held = True
        return
    skips = resolve_show_args(wid)[1].get("skip_objects")
    if skips:
        ended.append(run.note(api_client.set_skip(skips), "set-skip"))
    if _is_number(speed):
        ended.append(run.note(api_client.set_speed(speed), "set-speed"))
    if all(ended):
        run.reshown = True


def _holds_current() -> bool:
    """Whether the marker holds CURRENT; a marker that cannot be read counts as holding it."""
    try:
        return "CURRENT" in marker.read()["classes"]
    except OSError:
        return True


def _bundle(run: _Run, derived: str | None, reshow: bool, reload: bool = False, defer: bool = False) -> None:
    """sync_all's requests on the run's generation and status. It re-shows when the
    marker holds CURRENT, and for a reload's run whatever the marker holds, since that re-show is
    the reload's own action and goes out after a budget stop as a change's own rows do; a re-show
    left to a due delivery (`defer`) marks the run deferred."""
    _bundle_steps(run, derived)
    run.bundled = True
    wid = _on_screen(run.status)
    if not wid or not (reshow or defer):
        return
    if (run.ended is None if reload else not run.halted() and _holds_current()):
        if reshow:
            _reshow(run, wid)
        else:
            run.deferred = True


def _bundle_steps(run: _Run, derived: str | None) -> None:
    """The bundle's requests before the re-show. A window run records each playlist it transferred
    in the marker and skips the ones an earlier run of this generation recorded for the same engine,
    by its pid, boot and start (run.engine); a run whose status gives no pid records and skips
    nothing."""
    scheduled = _scheduled()
    tracked = run.window and run.engine is not None
    skip: set[str] = set()
    if tracked:
        try:
            marker.start_sent(run.generation, *run.engine)
            skip = set(marker.sent_for(run.generation, *run.engine))
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
        elif tracked:
            try:
                if marker.record_sent(run.generation, *run.engine, slug):
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
        _lanes_set(run, lane)
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


_MEMBER_KEYS = (None, "MEMBERS", "NAME")


def _member_slug(key: str | None, ticket: Ticket) -> str | None:
    """The playlist a members row names: its key, or the change's slug when the key is one of
    _MEMBER_KEYS, which name no playlist."""
    return ticket.slug if key in _MEMBER_KEYS else key


def _carried(row: str, key: str | None, ticket: Ticket, derived: str | None, held: list[str],
             active: str | None) -> list[str]:
    """The playlists one row's entry refresh sends."""
    if row == "active":
        return [active] if active else []
    if row == "policy":
        target = ticket.slug or derived
        return [target] if target else []
    if row == "members":
        slug = _member_slug(key, ticket)
        return [slug] if slug in held else []
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
            _lanes_set(run, {"id": "all", "playlist": active, "enabled": run.enabled(active), "manual": True})
    elif row == "pause":
        _lanes_set(run, {"id": "all", "enabled": run.enabled(derived)})
    elif row in ("policy", "members"):
        target = (ticket.slug if row == "policy" else _member_slug(key, ticket)) or derived
        if sent.get(target) and (row == "policy" or target == derived):
            _lanes_set(run, {"id": "all", "enabled": run.enabled(derived)})
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
    status, or the pending outcome that stops the run with nothing sent. While a restart_hold is
    held, sync is tried once, whatever `wait_s` says."""
    try:
        stack.enter_context(lock.held("sync", wait_s=0 if _restart_holding.is_set() else wait_s))
    except lock.StoreBusy:
        return Outcome("pending", reason="busy", env=env)
    cls, status = read_status()
    if cls != "ok" or status is None:
        return Outcome("pending", reason=cls, env=env)
    refusal = _version_refusal(status)
    if refusal is not None:
        return Outcome("pending", reason="version", message=refusal, env=env)
    return status


def _engine(status: dict[str, Any]) -> tuple[int, float | None] | None:
    """The engine that answered `status` as (pid, start), start being the engine's clock now minus its
    uptime_s; the caller takes it once, as the status arrives. time.monotonic reads CLOCK_MONOTONIC, the
    clock the engine's steady_clock uptime runs on, so a suspend or a wall-clock step leaves the start
    where it was. start is None when the status gives no uptime_s, and the identity is None for a status
    with no pid."""
    pid = status.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool):
        return None
    uptime = status.get("uptime_s")
    return pid, (_monotonic() - uptime if _is_number(uptime) else None)


def _same_engine(a: tuple[int, float | None] | None, b: tuple[int, float | None] | None) -> bool:
    """Whether two identities from _engine are one engine for the poll drain's retry count: the same pid,
    and starts within 5 s of each other or both unknown. The boot cannot change within one panel
    process."""
    if a is None or b is None or a[0] != b[0]:
        return False
    if a[1] is None or b[1] is None:
        return a[1] is None and b[1] is None
    return abs(a[1] - b[1]) <= marker._START_SLACK_S


def _unserved(engine: tuple[int, float | None] | None) -> bool:
    """Whether `engine`, an identity from _engine, is owed the whole bundle: the served record does not
    name it (marker.names: its pid, this boot and its start, which a start that is unknown never
    matches), none is recorded, or the marker cannot be read. No identity (a status with no pid) owes
    nothing, since no served engine could be recorded for it."""
    if engine is None:
        return False
    try:
        return not marker.names(marker.served_record(), *engine)
    except OSError:
        return True


def _owe(run: _Run) -> None:
    """The first bundle to an engine the served record does not name owes BUNDLE and CURRENT, once for
    that engine (marker.owe), and the run takes the marker's generation, since its whole bundle and
    re-show deliver everything the marker holds. An engine the owed record already names is owed the
    bundle only, and a marker that cannot be read or written leaves the run's own generation."""
    try:
        if run.engine is not None and not marker.names(marker.owed_record(), *run.engine):
            run.generation = marker.owe(*run.engine)
    except OSError:
        pass


def brake_notes() -> list[str]:
    """The brake notes of this process's command runs that ended applied with a re-show the engine held,
    kept since the last call, which are then forgotten."""
    notes = list(_brake_notes)
    _brake_notes.clear()
    return notes


def _record_served(run: _Run) -> None:
    """What the run leaves in the served and owed records for its engine (run.engine). After a bundle
    whose every request ended ok, with nothing stopped and no re-show left to a due delivery, the engine
    is served, a re-show the engine held counting as ended ok. A run that ended refused or uncertain
    takes back the CURRENT its engine's owed bundle added (marker.drop_owed) only when every request of
    its re-show ended ok or the engine held the re-show; otherwise CURRENT stays owed for the next
    bundle."""
    if run.engine is None:
        return
    with contextlib.suppress(OSError):
        if run.bundled and run.all_ok and run.ended is None and not run.stopped and not run.deferred:
            marker.record_served(*run.engine, at=_now())
        elif not run.all_ok and not run.stopped and run.ended != "away" and (run.reshown or run.held):
            marker.drop_owed(*run.engine)


def _finish(run: _Run, env: str | None = None, keep_current: bool = False) -> Outcome:
    """Record the run's engine (_record_served), clear the marker when every request ended ok,
    CURRENT kept when `keep_current`, and name the outcome; a command run that ended applied with a
    re-show the engine held keeps the brake note for brake_notes(), unless its own manual switch
    released the engine."""
    _record_served(run)
    warning = None
    if run.all_ok and not run.stopped:
        try:
            if keep_current:
                marker.clear(run.generation, ("CURRENT",))
            else:
                marker.clear(run.generation)
        except OSError as exc:
            warning = f"the sync marker could not be cleared: {exc}"
    seen: dict[str, Any] = {"env": env, "clock": run.clock, "refused_verb": run.refused_verb,
                            "generation": run.generation}
    if run.ended == "uncertain":
        return Outcome("uncertain", **seen)
    if run.ended is not None:
        return Outcome("pending", reason=run.ended, **seen)
    if run.stopped:
        _log.warning("engine sync stopped at the window's %.0f s budget with %d playlists recorded",
                     _WINDOW_BUDGET_S, run.recorded)
        return Outcome("pending", reason="budget", recorded=run.recorded, **seen)
    if run.refused is not None:
        return Outcome("refused", message=run.refused, **seen)
    if run.held and not run.released and not run.window:
        _brake_notes.append(BRAKED)
    return Outcome("applied", warning=warning, **seen)


def _deliver(ticket: Ticket, defer_current: bool = False) -> Outcome:
    if ticket.generation is None:
        return Outcome("applied", reason="no engine side", env=ticket.env)
    with contextlib.ExitStack() as stack:
        status = _synced(stack, lock.LOCK_WAIT_S, ticket.env)
        if isinstance(status, Outcome):
            return status
        run = _Run(ticket.run, ticket.generation, status)
        derived = derived_active(status)[0]
        rows = [row for row, _key in ticket.rows]
        reload = "reload" in rows
        reshow = bool(ticket.wid) and ticket.wid == _on_screen(status) and "wp_build" in rows
        unserved = _unserved(run.engine)
        bundle = unserved or ticket.existed or reload
        whole = bundle and not defer_current
        if reshow or whole:
            _adopt_current(run, whole)
        if unserved:
            _owe(run)
        if bundle:
            _bundle(run, derived, reshow=not reshow and not defer_current, reload=reload,
                    defer=defer_current and not reshow)
        if run.ended is None:
            _rows(run, ticket, derived, reshow)
        return _finish(run, ticket.env, keep_current=defer_current and not reshow)


def _adopt_current(run: _Run, whole: bool) -> None:
    """A delivery that re-shows the wallpaper on screen serves what the marker holds, so it takes and
    clears that marker's generation: with its whole bundle and its re-show (`whole`), everything the marker
    holds, as sync_all and _owe do, and with only its own build re-show, a marker left holding only
    CURRENT by a deferred re-show. A marker that cannot be read leaves the run's own generation."""
    with contextlib.suppress(OSError):
        state = marker.read()
        if state["classes"] == ["CURRENT"] or (whole and state["classes"]):
            run.generation = state["generation"]


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


def burst_existed(tickets: list[Ticket]) -> bool:
    """Whether a burst of saved changes delivered as one owes the bundle first: its first change
    found a marker when it set its own, or another writer raised the generation between two of its
    changes, so the marker may hold a change the burst's own rows do not carry."""
    return tickets[0].existed or any(b.generation != a.generation + 1 for a, b in zip(tickets, tickets[1:]))


def deliver(ticket: Ticket, defer_current: bool = False) -> Outcome:
    """The second half of run_change for a saved change: sync, the status and version check inside it,
    the bundle first when a marker existed or the engine is not the served one, the change's push,
    and the clear when every request ended ok. defer_current is run_change's."""
    return _deliver(ticket, defer_current)


def run_change(locks: Iterable[str], write: Callable[[], Any], rows: Iterable[tuple[str, str | None]],
               *, wid: str | None = None, slug: str | None = None, manual: bool = False,
               status: tuple[str, dict[str, Any] | None] | None = None, run: str = "window",
               defer_current: bool = False) -> Outcome:
    """One change from any caller. rows are (row, key) pairs: the row from SETTING_ROWS,
    WP_ROWS or active, policy, members, reload and none; the key the verb, live, tuning and
    wallpaper rows name (None where a row names none). A members row's key names its playlist, so
    one change can carry several; a key of MEMBERS, NAME or None stands for slug. locks names
    every store write touches;
    status is a read_status() result already taken. Status is read before any lock, and it raises
    SwitchRefused for a manual switch it cannot make; the store locks are taken in rank order; the
    marker is set and held across write(); engine-env is written for a tuning or restart row; the
    store locks are released, highest rank first; then sync, the status and version check inside
    it, the bundle first when a marker existed or the answering engine is not the served one (with
    BUNDLE and CURRENT ensured for it, once for that engine), this change's push with each row's own
    verb last, also after a bundle the window's budget stopped, and the clear when every request ended ok. An
    exception from write() reaches the caller with nothing sent, and a marker already set stays.
    defer_current, set while a window delivery is due, leaves the bundle's re-show to that
    delivery and keeps CURRENT in the marker for it to clear."""
    return _deliver(save_change(locks, write, rows, wid=wid, slug=slug, manual=manual, status=status, run=run),
                    defer_current)


def sync_all(run: str, classes: Iterable[str] = ("BUNDLE",), wait_s: float = 2.0,
             defer_current: bool = False) -> Outcome:
    """Rebuild the engine from the store under one sync hold. The marker is ensured first,
    keeping an existing generation unless `classes` adds a class it did not hold; a status that is
    not ok, or another build's engine, returns pending with nothing sent. Then every engine-held
    playlist, the schedule, the lane, the global verbs, the live values of the wallpaper on screen
    with speed left out while the engine reports 0, and a re-show when the marker holds CURRENT or
    `classes` names CURRENT (a reload's run); the marker clears when every request ended ok. A
    "window" run keeps an 8 s budget and records its progress in the marker; a "command" run has
    neither. defer_current leaves the re-show and CURRENT to a window delivery that is due, as
    run_change does. An engine the served record does not name gets CURRENT ensured as well, once
    for that engine, and a bundle whose every request ended ok, its re-show included, records it as
    served; the re-show is marked automatic, and one the engine held counts as suppressed. The
    outcome carries the generation the run worked from: the one ensure gave, or the one its owed bundle
    raised it to."""
    classes = tuple(classes)
    generation = marker.ensure(classes)
    with contextlib.ExitStack() as stack:
        status = _synced(stack, wait_s, None)
        if isinstance(status, Outcome):
            return replace(status, generation=generation)
        r = _Run(run, generation, status)
        reload = "CURRENT" in classes
        if _unserved(r.engine):
            _owe(r)
        _bundle(r, derived_active(status)[0], reshow=not defer_current, reload=reload, defer=defer_current)
        return _finish(r, keep_current=defer_current)
