"""The engine pushes: the rotation set, the schedule and the lane binding, the fullscreen
policy, the live globals and a single show, each resolved from the store and sent over the
engine socket. Plain Python over the store and the resolver; no Qt import.
"""
from __future__ import annotations

from typing import Any

from .. import api_client
from .. import constants as C
from ..storage import paths, playlists, settings
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
