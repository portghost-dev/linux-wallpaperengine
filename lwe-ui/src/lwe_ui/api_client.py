"""Client for the engine's daemon API socket (stdlib-only, no PySide6).

Speaks the engine's wire schema: one JSON object per line over a unix socket in
$XDG_RUNTIME_DIR/lwe, replies correlated by id. Short verbs answer once with
status="done"; `show` answers status="accepted" as soon as the engine has taken the
command, then "done" (or an error) when the scene load actually finishes. The split
exists because a load can take seconds on the engine's render thread - the ack is the
part a click handler is allowed to wait for.

Courier discipline: every call is one bounded connect-send-read,
nothing here ever raises into the caller, and an absent or dead engine socket is an
ordinary answer (None), not an exception. The GUI must keep working against an engine
that predates the API or is not running at all.
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

# Ack budget is ~100ms by engine contract; 3s tolerates a busy loop iteration without
# ever stalling the GUI for long.
_TIMEOUT = 3.0

# A `done` for show arrives after the scene load; cached scenes land well under a
# second, heavy ones can take several. Only measurement tools should wait this long.
_DONE_TIMEOUT = 30.0

_MAX_REPLY = 64 * 1024  # mirrors the engine's own per-line cap


def socket_path() -> Path:
    """The engine's command socket: $LWE_SOCKET override, else the runtime-dir default."""
    override = os.environ.get("LWE_SOCKET", "").strip()
    if override:
        return Path(override)
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip() or f"/run/user/{os.getuid()}"
    return Path(runtime) / "lwe" / "engine.sock"


def available() -> bool:
    """True when something is listening on the command socket right now."""
    path = socket_path()
    if not path.is_socket():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(_TIMEOUT)
            s.connect(str(path))
        return True
    except OSError:
        return False


def _read_reply(sock: socket.socket, buf: bytearray) -> dict[str, Any] | None:
    """Read one newline-terminated JSON reply. None on close/timeout/garbage/overrun."""
    while b"\n" not in buf:
        if len(buf) > _MAX_REPLY:
            return None
        try:
            chunk = sock.recv(4096)
        except OSError:
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    line, _, rest = bytes(buf).partition(b"\n")
    del buf[: len(line) + 1]
    try:
        reply = json.loads(line)
    except ValueError:
        return None
    return reply if isinstance(reply, dict) else None


def request(cmd: str, args: dict | None = None, wait_done: bool = True) -> dict[str, Any] | None:
    """Send one command, return its final reply dict, or None if the engine never answered.

    wait_done=False returns the FIRST reply instead - for `show` that is the accepted
    ack, which is the click-handler contract. An engine-side rejection comes back as a
    normal dict with ok=False; the caller distinguishes "engine said no" (dict) from
    "engine unreachable" (None).
    """
    req: dict[str, Any] = {"id": 1, "cmd": cmd}
    if args:
        req["args"] = args
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(_TIMEOUT)
            s.connect(str(socket_path()))
            s.sendall((json.dumps(req) + "\n").encode())
            buf = bytearray()
            reply = _read_reply(s, buf)
            if reply is None or not wait_done:
                return reply
            while reply.get("status") == "accepted":
                s.settimeout(_DONE_TIMEOUT)
                reply = _read_reply(s, buf)
                if reply is None:
                    return None
            return reply
    except OSError:
        return None


def show(
    wid: str,
    wait_done: bool = False,
    cc: list[float] | None = None,
    speed: float | None = None,
    properties: dict[str, str] | None = None,
    scaling: str | None = None,
    clamp: str | None = None,
    volume: int | None = None,
    audio_processing: bool | None = None,
    mouse: bool | None = None,
    automute: bool | None = None,
    fullscreen_pause: bool | None = None,
    fullscreen_behavior: str | None = None,
    skip_objects: list[int] | None = None,
    ui_id: str | None = None,
) -> dict[str, Any] | None:
    """Hot-swap every output to this wallpaper id. Default waits only for the ack.

    Every kwarg carries a RESOLVED per-wallpaper setting (what a cold launch would
    deliver as argv/env). All of them are wallpaper-scoped on the engine side:
    an omitted arg means "the engine's launch default", never "keep the previous
    wallpaper's value".

    cc = [brightness, contrast, saturation, hue_radians]. properties carries the
    PROP_ overrides as raw strings; presets like OLED Black are ALL properties.
    scaling in stretch/fit/fill/default; clamp in clamp/border/repeat; volume 0..128.
    skip_objects is the wallpaper's conf SKIP list (object ids hidden for this
    wallpaper only).
    """
    args: dict[str, Any] = {"id": wid}
    if cc is not None:
        args["cc"] = [float(x) for x in cc]
    if speed is not None:
        args["speed"] = float(speed)
    if properties:
        args["properties"] = {str(k): str(v) for k, v in properties.items()}
    if scaling is not None:
        args["scaling"] = str(scaling)
    if clamp is not None:
        args["clamp"] = str(clamp)
    if volume is not None:
        args["volume"] = int(volume)
    if audio_processing is not None:
        args["audio_processing"] = bool(audio_processing)
    if mouse is not None:
        args["mouse"] = bool(mouse)
    if automute is not None:
        args["automute"] = bool(automute)
    if fullscreen_pause is not None:
        args["fullscreen_pause"] = bool(fullscreen_pause)
    if fullscreen_behavior is not None:
        # three-state policy; wins over the fullscreen_pause alias on the engine side
        args["fullscreen_behavior"] = str(fullscreen_behavior)
    if skip_objects:
        args["skip_objects"] = [int(x) for x in skip_objects]
    if ui_id:
        # opaque identity echo: the engine stores + reports it so Now Playing can name
        # the preset TILE the user picked, not the base wallpaper the engine renders
        args["ui_id"] = str(ui_id)
    return request("show", args, wait_done=wait_done)


def status() -> dict[str, Any] | None:
    """The engine's status snapshot, or None when unreachable."""
    reply = request("status")
    if reply is None or not reply.get("ok"):
        return None
    result = reply.get("result")
    return result if isinstance(result, dict) else None


def rotate_set(
    entries: list[dict[str, Any]],
    interval_s: int,
    order: str,
    enabled: bool,
    avoid_repeat: bool = True,
    label: str = "",
) -> dict[str, Any] | None:
    """Replace the engine's standing rotation order wholesale.

    Each entry is a COMPLETE resolved show-args object (id + the resolved
    per-wallpaper vocabulary + ui_id) - the engine only executes, it never resolves
    confs. The engine floors interval at 15s; clamp here so a 1s playlist can't
    bounce the push.
    """
    args: dict[str, Any] = {
        "entries": entries,
        "interval_s": max(15, min(int(interval_s), 604800)),
        "order": str(order),
        "avoid_repeat": bool(avoid_repeat),
        "enabled": bool(enabled),
        "label": str(label)[:128],
    }
    return request("rotate-set", args)


def next_wallpaper() -> dict[str, Any] | None:
    """Advance the engine's rotation NOW (deck transport). Ack-only, like show."""
    return request("next", wait_done=False)


def prev_wallpaper() -> dict[str, Any] | None:
    """Step back through the engine's show history. Ack-only, like show."""
    return request("prev", wait_done=False)


def ping() -> dict[str, Any] | None:
    """UI heartbeat: feeds the engine's dead-man reflex (and restores a dead-man
    release). Sent alongside the status poll."""
    return request("ping")


def list_objects() -> dict[str, Any] | None:
    """Objects of the scene the engine is ACTUALLY showing: {objects: [...], skipped: [...]}.

    Each object is {id, name} plus, for image objects with a chain, effects[{id, name}].
    Two limits are the engine's, not ours: it walks only the FIRST scene screen (mirror
    groups share one scene), and a video or web wallpaper has no scene graph, so `objects`
    comes back empty rather than erroring.
    """
    reply = request("list-objects")
    if reply is None or not reply.get("ok"):
        return None
    result = reply.get("result")
    return result if isinstance(result, dict) else None


def set_skip(ids: list[int]) -> dict[str, Any] | None:
    """Replace the render skip-list wholesale on the running scene. [] clears it.

    Live: the engine consults the list per frame while traversing, so this hides and
    reveals objects with no rebuild and no relaunch.
    """
    return request("set-skip", {"ids": [int(i) for i in ids]})


def set_fps(fps: int) -> dict[str, Any] | None:
    """Frame cap, 1..480, applied to the running engine.

    Live for the GL path. A web wallpaper's CEF frame rate is fixed when its browser
    is created, so web scenes adopt a new cap on their next show rather than mid-scene.
    """
    return request("set-fps", {"fps": int(fps)})


def set_speed(speed: float) -> dict[str, Any] | None:
    """Animation timescale, 0..20, applied live to the RUNNING scene.

    m_timescale is read every frame inside the g_Time accumulator, so the change lands
    next frame with no rebuild and no time discontinuity. 0 is the animation freeze
    (byte-identical frames; the VRAM tripwire's standing mechanism) - NEVER use the
    `pause` verb for a freeze, its resume jumps g_Time by the whole paused duration.
    Scene-time only: video (mpv) and web (CEF) wallpapers ignore g_Time and are
    unaffected - callers own that caveat in the UI.
    """
    return request("set-speed", {"speed": float(speed)})


def set_volume(volume: int) -> dict[str, Any] | None:
    """Engine volume, 0..128, applied live to the running scene.

    The SDL mix callback reads the volume per audio buffer, so scenes adopt it within
    one buffer. A video wallpaper's mpv volume binds at player creation - videos adopt
    it on their next show, same as the fps cap.
    """
    return request("set-volume", {"volume": int(volume)})


def set_mouse(enabled: bool) -> dict[str, Any] | None:
    """Mouse interaction toggle, applied live - consulted per pointer event."""
    return request("set-mouse", {"enabled": bool(enabled)})


def set_audio(enabled: bool) -> dict[str, Any] | None:
    """Audio-response toggle, applied live to the running scene.

    Honest since the always-built-recorder change: the recorder exists for
    every supporting project and the flag gates CONSUMPTION per frame - off decays the
    published bands to silence, on resumes the FFT. Before that change this verb was
    refused because the flag gated recorder CONSTRUCTION at scene load, and a setter
    over a construction-time gate is a toggle that lies.
    """
    return request("set-audio", {"enabled": bool(enabled)})


def set_tuning(**kwargs: Any) -> dict[str, Any] | None:
    """Set one or more engine tuning constants live (audio_gain, the lighting dials, ...).

    PARTIAL UPDATE by engine contract: the handler applies only the keys present in the
    request, so sending `audio_gain` alone cannot disturb the classic-lighting calibration
    constants sharing the verb. Only the supplied keys are sent - never a full block.

    Validation is engine-side (finite numbers, at least one key required); a refusal comes
    back as ok:false and is a failure-grammar event for the caller, not a transport problem.
    """
    args = {str(k): v for k, v in kwargs.items() if v is not None}
    if not args:
        return None
    return request("set-tuning", args)


def set_parallax(enabled: bool) -> dict[str, Any] | None:
    """Global parallax toggle. Genuinely live - the readers consult it per frame."""
    return request("set-parallax", {"enabled": bool(enabled)})


def set_particles(enabled: bool) -> dict[str, Any] | None:
    """Global particles toggle.

    The engine reads this while BUILDING a scene, so it rebuilds the current wallpaper
    to apply. The reply carries `rebuilt` telling you whether that happened (it does
    not when the outputs are released or nothing is showing).
    """
    return request("set-particles", {"enabled": bool(enabled)})


def set_fullscreen_ignore(app_ids: list[str]) -> dict[str, Any] | None:
    """Replace the app_ids exempt from the fullscreen policy. [] clears the list.

    Substring-matched against the fullscreen window's app_id, so "firefox" covers
    "org.mozilla.firefox". Live, no rebuild.
    """
    return request("set-fullscreen-ignore", {"app_ids": [str(a) for a in app_ids]})


def set_instrument(name: str, enabled: bool) -> dict[str, Any] | None:
    """Toggle a log instrument on the LIVE engine (engine b5fe9044 and later).

    Only pure log gates are settable. The engine REFUSES a name that is not in its runtime
    registry rather than accepting it into a no-op, so a launch-time switch (LWE_TEXCOMP and
    friends, which decide what gets built) comes back as an error naming the reason. Treat a
    failure here as information for the operator, not as a transport problem.
    """
    return request("set-instrument", {"name": name, "enabled": bool(enabled)})


def set_app_conditions(names: list[str], behavior: str) -> dict[str, Any] | None:
    """Replace the engine's running-apps condition wholesale (names are /proc comm)."""
    return request("set-app-conditions",
                   {"names": [str(n)[:64] for n in names][:128], "behavior": str(behavior)})


def set_fullscreen(behavior: str) -> dict[str, Any] | None:
    """Change the fullscreen policy on the RUNNING scene: off | pause | stop.

    The same value rides every show as a resolved arg, but a show only happens on a
    swap - without this verb a mode change sat inert until the next transport click or
    rotation tick, leaving the screens black mid-game. Setting off
    un-latches a pause immediately; leaving stop hands the outputs back immediately.
    """
    return request("set-fullscreen", {"behavior": str(behavior)})
