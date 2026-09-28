"""get, config and the setting commands.

get and config read the saved settings, each through cli/settings_table.py::read with where its value
comes from; reads take no lock and write nothing, and a running engine from another build is named on
stderr while the read goes on. A panel setting saves one line of settings.conf and sends nothing. A
restart setting saves its line and rebuilds engine-env through daemon_unit.write_env while still holding
the settings lock; watchdog and color are engine-env lines with no settings key. Apart from the status
read, none of those sends the engine a request. A live or next-wallpaper setting saves its line through
engine/push.py::run_change, which sends the key's targeted push; speed 0 and audiosmoothing are sent to
the engine under sync and never saved. reload checks every file through cli/validate.py::check_all, prints
what changed since its last snapshot, and applies the store through sync_all with the re-show before
rebuilding engine-env.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable

from .. import DONE, ENGINE_DOWN, REFUSED, USAGE, Context, help_text, report, settings_table, vocabulary
from ..registry import Verb
from ..values import Refused, UsageError, format_number

GET_USAGE = "usage: lwe get <setting> [<setting> ...]; lwe config lists the settings"
CONFIG_USAGE = ("usage: lwe config [<setting> [<value>]] or lwe config unset <setting>; lwe config lists the "
                "settings")
_NEEDS_STATUS = ("order", "interval", "playlist", "audiosmoothing")
_PANEL = ("reviewrequired", "storage", "detect", "detectevery", "libraryfolder", "workshopfolder", "steamfolder")
_RESTART = ("layer", "videodecode", "texturecache", "texturedetail", "assetsfolder", "resclamp", "effectclamp")
_LINES = ("watchdog", "color")
_LIVE = ("volume", "mute", "audioreactive", "mouse", "parallax", "automute", "particles", "fps", "speed", "scaling",
         "edge", "fullscreen", "lightdimming", "lightfalloff", "audiogain")
_CONFIG_ONLY = ("audioreactivedefault", "mousedefault", "parallaxdefault")
OWN_VALUE = "the wallpaper on screen keeps its own value (lwe wallpaper current unset {name})"
NOT_SAVED_NOW = "applies now; not saved yet, so a restart goes back to the start value"
_FOLDERS = ("libraryfolder", "workshopfolder", "steamfolder", "assetsfolder")
_LOAD_REFUSED = ("layer", "videodecode", "assetsfolder", "watchdog", "color", *_PANEL)
RESTART_ONLY = "applies only at a restart (lwe service restart)"
PANEL_ONLY = "read by the panel, not the engine"
NEXT_ONLY = "applies to the next wallpaper"
RELOAD_FIRST = "no earlier reload to compare with; every file was checked"
RELOAD_SAME = "nothing changed since the last reload"
RESTART_LINE = "the saved global takes effect at the next service restart (lwe service restart applies it)."
_LOADS = ("volume", "mute", "audioreactive", "mouse", "parallax", "particles", "fps", "fullscreen", "lightdimming",
          "lightfalloff", "audiogain", "speed", "audiosmoothing")
_RESHOW_LOADS = {"resclamp": "LWE_SSFACTOR", "effectclamp": "LWE_CLAMPCOMPOSITES", "texturecache": "LWE_TEXCOMP",
                 "texturedetail": "LWE_TEXDETAIL"}
NO_SCREENS_SAVED = ("engine-env was not rebuilt because no screens were found; it catches up when the panel "
                    "next starts or lwe service start or restart runs from your desktop session")
NO_SCREENS_REFUSED = ("not saved: engine-env is only rewritten where screens are found; run it from your desktop "
                      "session, or open the panel once")


def _print_json(ctx: Context, data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")), file=ctx.out)


def _status(ctx: Context) -> tuple[dict | None, bool]:
    """One status read: (the status or None, whether something listens on the socket)."""
    from ... import api_client, version
    status = api_client.status()
    if status is None:
        return None, api_client.available()
    refusal = version.running_refusal(status, version.panel_stamp())
    if refusal is not None:
        ctx.error(refusal)
    return status, True


def _unknown(ctx: Context, name: str, usage: str) -> int:
    ctx.error(f"{name} is not a setting; {usage}")
    return USAGE


def _get(ctx: Context, args: list[str]) -> int:
    if not args:
        ctx.error(GET_USAGE)
        return USAGE
    for name in args:
        if name not in settings_table.BY_NAME:
            return _unknown(ctx, name, GET_USAGE)
    status, listening = None, False
    if any(name in _NEEDS_STATUS for name in args):
        status, listening = _status(ctx)
    if "audiosmoothing" in args and status is None:
        if not listening:
            ctx.error("the service is not running, so audiosmoothing has no value")
            return ENGINE_DOWN
        ctx.error("the service is not answering")
        return REFUSED
    values: dict[str, str] = {}
    lines: list[str] = []
    for name in args:
        value, _source, problem = settings_table.read(name, status)
        if problem is not None:
            ctx.error(f"invalid: {problem}")
        text = settings_table.BY_NAME[name].format(value)
        values[name] = text
        lines.append(text)
    if ctx.json:
        _print_json(ctx, values)
    else:
        for text in lines:
            print(text, file=ctx.out)
    return DONE


def _config(ctx: Context, args: list[str]) -> int:
    if args[:1] == ["unset"]:
        return _quietly(_unset, ctx, args[1:])
    if len(args) > 1:
        return _quietly(_config_set, ctx, args[0], args[1:])
    if args and args[0] not in settings_table.BY_NAME:
        return _unknown(ctx, args[0], CONFIG_USAGE)
    names = args or [row["name"] for row in vocabulary.SETTINGS if row["name"] in settings_table.BY_NAME]
    status = _status(ctx)[0] if any(name in _NEEDS_STATUS for name in names) else None
    rows = []
    for name in names:
        value, source, problem = settings_table.read(name, status)
        rows.append({"name": name, "value": settings_table.BY_NAME[name].format(value), "source": source,
                     "invalid": problem})
    if ctx.json:
        _print_json(ctx, rows[0] if args else {"settings": rows})
        return DONE
    for row in rows:
        line = f"{row['name']}  {row['value']}  {row['source']}"
        if row["invalid"] is not None:
            line += f"  invalid: {row['invalid']}"
        print(line, file=ctx.out)
    return DONE


def _refuse(ctx: Context, message: str, code: int) -> int:
    ctx.error(message)
    return code


def _running_refusal(ctx: Context) -> int | None:
    """One status read before any lock: REFUSED, named on stderr, for a running engine from another
    build; None when the engine answers from this build or does not answer."""
    from ... import api_client, version
    status = api_client.status()
    refusal = version.running_refusal(status, version.panel_stamp()) if status is not None else None
    return None if refusal is None else _refuse(ctx, refusal, REFUSED)


def _help(ctx: Context, name: str) -> int:
    """A setting typed alone: its help page."""
    from .. import help_pages
    text = help_pages.page(name)
    if text is None:
        values = next(row["values"] for row in vocabulary.SETTINGS if row["name"] == name)
        text = help_text.RESCLAMP if name == "resclamp" else f"{name} {values}"
    ctx.out.write(text if text.endswith("\n") else text + "\n")
    return DONE


def _write(fn: Callable[[dict], dict], key: str) -> bool:
    """settings.modify(fn) under the settings lock, the one place every command write of a setting
    passes; True when a setting's line changed. A session override named by `key` is taken from the
    window in that same write, whether or not its value changes, so a write that fails takes nothing."""
    from ...storage import lock, settings
    with lock.held("settings"):
        before = settings.setting_lines()
        settings.modify(fn, taken=(key,))
        return settings.setting_lines() != before


def _receipt(ctx: Context, name: str, saved: bool, env_state: str | None = None) -> int:
    """The receipt for the value now in force; a rebuild that found no screens says so."""
    row = settings_table.BY_NAME[name]
    shown = row.format(settings_table.read(name, None)[0])
    r = report.receipt(name, shown, saved, row.reach)
    if env_state == "no screens":
        r["reason"] = NO_SCREENS_SAVED
        if not ctx.json:
            print(f"{name} {shown}: {'saved' if saved else 'unchanged'}; {NO_SCREENS_SAVED}.", file=ctx.out)
            return DONE
    report.emit(ctx, r)
    return DONE


def _restart_write(ctx: Context, name: str, fn: Callable[[dict], dict]) -> int:
    """A restart setting: the status read, its line under the settings lock and, still holding it,
    engine-env rebuilt; nothing sent."""
    from ...engine import daemon_unit
    from ...storage import lock
    refused = _running_refusal(ctx)
    if refused is not None:
        return refused
    with lock.held("settings"):
        saved = _write(fn, settings_table.BY_NAME[name].key)
        try:
            env_state = daemon_unit.write_env()
        except (OSError, ValueError) as exc:
            state = "saved" if saved else "unchanged"
            return _refuse(ctx, f"{state}, but the engine file could not be written: {exc}", REFUSED)
    return _receipt(ctx, name, saved, env_state)


def _line_write(ctx: Context, name: str, text: str | None) -> int:
    """watchdog or color: the status read, then its engine-env line alone; nothing is saved when no
    screen is found."""
    from ...engine import daemon_unit
    from ...storage.lock import StoreBusy
    refused = _running_refusal(ctx)
    if refused is not None:
        return refused
    try:
        env_state = daemon_unit.write_env({settings_table.BY_NAME[name].key: text})
    except StoreBusy:
        raise
    except (OSError, ValueError) as exc:
        return _refuse(ctx, f"not saved: the engine file could not be written: {exc}", REFUSED)
    if env_state == "no screens":
        return _refuse(ctx, NO_SCREENS_REFUSED, REFUSED)
    return _receipt(ctx, name, env_state == "written")


def _live_write(ctx: Context, name: str, fn: Callable[[dict], dict], edge: list[int] | None = None) -> int:
    """A live or next-wallpaper setting: one status read, then its line and the key's targeted push
    through the change runner; the receipt is the runner's outcome."""
    from ... import version
    from ...engine import push
    from ...storage import wp
    row = settings_table.BY_NAME[name]
    status = push.read_status()
    if status[0] == "ok":
        refusal = version.running_refusal(status[1], version.panel_stamp())
        if refusal is not None:
            return _refuse(ctx, refusal, REFUSED)
    saved: list[bool] = []
    kind = push.SETTING_ROWS.get(row.key, "none")
    locks = ("settings", "env") if kind in ("tuning", "restart") else ("settings",)
    outcome = push.run_change(locks, lambda: saved.append(_write(fn, row.key)), [(kind, row.key)], status=status,
                              run="command")
    shown = row.format(settings_table.read(name, None)[0])
    report.emit(ctx, report.receipt(name, shown, bool(saved and saved[0]), row.reach, outcome.kind,
                                    outcome.message or ""))
    if not ctx.json:
        if edge:
            print(f"{name} stops at {edge[0]}", file=ctx.out)
        current = status[1].get("current") if status[0] == "ok" else None
        on_screen = str(current.get("ui_id") or "") if isinstance(current, dict) else ""
        if on_screen and row.wp_key:
            try:
                own = row.wp_key in wp.load_set(on_screen)
            except OSError:
                own = False
            if own:
                print(OWN_VALUE.format(name=name), file=ctx.out)
    if outcome.env is not None and outcome.env not in ("written", "unchanged"):
        ctx.error(f"warning: engine-env was not rewritten: {outcome.env}")
    return REFUSED if outcome.kind == "refused" else DONE


def _engine_only(ctx: Context, name: str, shown: str, verb: str, value: float, applies: str) -> int:
    """speed 0 or audiosmoothing: the status read, then the engine's verb under sync; nothing saved."""
    from ... import api_client, version
    from ...storage import lock
    status = api_client.status()
    if status is None:
        return _refuse(ctx, f"the service is not running, so {name} {shown} has nothing to apply to", ENGINE_DOWN)
    refusal = version.running_refusal(status, version.panel_stamp())
    if refusal is not None:
        return _refuse(ctx, refusal, REFUSED)
    with lock.held("sync"):
        reply = api_client.set_speed(value) if verb == "set_speed" else api_client.set_tuning(audio_smooth=value)
    if not isinstance(reply, dict):
        return _refuse(ctx, f"{name} {shown}: the engine did not answer", REFUSED)
    if not reply.get("ok"):
        return _refuse(ctx, f"{name} {shown}: the engine refused it: {reply.get('error') or 'no reason given'}",
                       REFUSED)
    if ctx.json:
        _print_json(ctx, {"setting": name, "value": shown, "saved": False, "outcome": "applied", "applies": "now",
                          "reason": ""})
    else:
        print(f"{name} {shown}: {applies}.", file=ctx.out)
    return DONE


def _wp_line_problem(wid: str, key: str) -> str | None:
    """The wallpaper file's own line for `key`, when it holds a value this build cannot use."""
    from ... import constants as C
    from ...storage import migrate, paths, tier_a
    try:
        lines = paths.wp_file(wid).read_bytes().decode("utf-8").split("\n")
    except OSError:
        return None
    found = None
    for number, line in enumerate(lines, 1):
        raw = tier_a.parse(line)
        if key in raw:
            found = (number, line.strip(), raw[key])
    if found is None:
        return None
    kind, _value, reason = migrate.coerce(C.WP_SCHEMA[key], found[2], False)
    return None if kind == "ok" else f"wp/{wid}.conf line {found[0]} {found[1]} ({reason or 'out of range'})"


def _load(ctx: Context, name: str) -> int:
    """<setting> load: the saved value and the on-screen wallpaper's own key, re-read through the show
    resolver after their lines are checked, sent under sync; nothing is written."""
    from ... import api_client, version
    from ...engine import resolve
    from ...storage import lock, settings
    row = settings_table.BY_NAME[name]
    status = api_client.status()
    if status is None:
        return _refuse(ctx, f"the service is not running, so {name} load has nothing to apply to", ENGINE_DOWN)
    refusal = version.running_refusal(status, version.panel_stamp())
    if refusal is not None:
        return _refuse(ctx, refusal, REFUSED)
    current = status.get("current")
    screen = str(current.get("ui_id") or "") if isinstance(current, dict) else ""
    if name != "audiosmoothing":
        problem = settings_table.read(name, None)[2]
        if problem is None and screen and row.wp_key:
            problem = _wp_line_problem(screen, row.wp_key)
        if problem is not None:
            return _refuse(ctx, f"{name} load: invalid: {problem}", REFUSED)
    s = settings.load()
    shown_args = resolve.resolve_show_args(screen)[1] if screen else {}
    if name == "audiosmoothing":
        entry = (status.get("config") or {}).get("LWE_AUDIOSMOOTH")
        value = entry.get("value") if isinstance(entry, dict) else None
        if value is None:
            return _refuse(ctx, "audiosmoothing load: the engine reports no start value", REFUSED)
        call, shown = (lambda: api_client.set_tuning(audio_smooth=float(value))), row.format(float(value))
    elif name == "speed":
        speed = resolve.effective_speed(screen)
        call, shown = (lambda: api_client.set_speed(speed)), row.format(speed)
    elif name in ("lightdimming", "lightfalloff", "audiogain"):
        tuning = resolve.resolved_tuning(screen)
        call, shown = (lambda: api_client.set_tuning(**tuning)), row.format(settings_table.read(name, None)[0])
    elif name == "fps":
        fps = max(1, min(480, int(s["ENGINE_FPS"])))
        call, shown = (lambda: api_client.set_fps(fps)), str(fps)
    elif name == "parallax":
        on = bool(s["PARALLAX_DEFAULT"]) and not s["OVERRIDE_PARALLAX_OFF"]
        call, shown = (lambda: api_client.set_parallax(on)), "on" if on else "off"
    elif name == "particles":
        on = bool(s["PARTICLES_DEFAULT"])
        call, shown = (lambda: api_client.set_particles(on)), "on" if on else "off"
    elif name == "fullscreen":
        behavior = shown_args.get("fullscreen_behavior") or resolve.resolve_fullscreen_behavior(s)
        call, shown = (lambda: api_client.set_fullscreen(behavior)), row.format(settings_table.read(name, None)[0])
    elif not screen:
        print(f"{name} load: no wallpaper is on screen, so nothing was sent", file=ctx.out)
        return DONE
    elif name in ("volume", "mute"):
        call, shown = (lambda: api_client.set_volume(shown_args["volume"])), str(shown_args["volume"])
    elif name == "audioreactive":
        on = bool(shown_args["audio_processing"])
        call, shown = (lambda: api_client.set_audio(on)), "on" if on else "off"
    else:
        on = bool(shown_args["mouse"])
        call, shown = (lambda: api_client.set_mouse(on)), "on" if on else "off"
    with lock.held("sync"):
        reply = call()
    if not isinstance(reply, dict):
        return _refuse(ctx, f"{name} load: the engine did not answer", REFUSED)
    if not reply.get("ok"):
        return _refuse(ctx, f"{name} load: the engine refused it: {reply.get('error') or 'no reason given'}", REFUSED)
    print(f"{name} {shown}: the saved value, applied now; nothing written.", file=ctx.out)
    return DONE


def _reshow_load(ctx: Context, name: str) -> int:
    """resclamp, effectclamp, texturecache or texturedetail load: one re-show of the wallpaper on screen,
    marked automatic as a side effect that must not release a held engine, keeping the speed status
    reported, plus the restart line when the saved value differs from the engine's start value. A re-show
    the engine held sends no skip or speed after it and prints the brake note instead of the shown line."""
    from ... import api_client, version
    from ...engine import push, resolve
    from ...storage import lock, settings
    row = settings_table.BY_NAME[name]
    status = api_client.status()
    if status is None:
        return _refuse(ctx, f"the service is not running, so {name} load has nothing to apply to", ENGINE_DOWN)
    refusal = version.running_refusal(status, version.panel_stamp())
    if refusal is not None:
        return _refuse(ctx, refusal, REFUSED)
    current = status.get("current")
    screen = str(current.get("ui_id") or "") if isinstance(current, dict) else ""
    problem = settings_table.read(name, None)[2]
    if problem is None and screen and row.wp_key:
        problem = _wp_line_problem(screen, row.wp_key)
    if problem is not None:
        return _refuse(ctx, f"{name} load: invalid: {problem}", REFUSED)
    if not screen:
        print(f"{name} load: no wallpaper is on screen, so nothing was sent", file=ctx.out)
        return DONE
    speed = status.get("speed")
    with lock.held("sync"):
        reply = push.show_final(screen, automatic=True)
        held = push._held(reply)
        skips = resolve.resolve_show_args(screen)[1].get("skip_objects")
        if skips and not held:
            api_client.set_skip(skips)
        if isinstance(speed, (int, float)) and not isinstance(speed, bool) and not held:
            api_client.set_speed(speed)
    if not isinstance(reply, dict):
        return _refuse(ctx, f"{name} load: the engine did not answer", REFUSED)
    if not reply.get("ok"):
        return _refuse(ctx, f"{name} load: the engine refused it: {reply.get('error') or 'no reason given'}", REFUSED)
    shown = row.format(settings_table.read(name, None)[0])
    if held:
        ctx.note(push.BRAKED)
    else:
        print(f"{name} {shown}: the wallpaper on screen was shown again with it; nothing written.", file=ctx.out)
    entry = (status.get("config") or {}).get(_RESHOW_LOADS[name])
    started = entry.get("value") if isinstance(entry, dict) else None
    saved = settings.load()[row.key]
    if started is not None:
        if isinstance(saved, bool):
            differs = str(started).strip() != ("1" if saved else "0")
        elif isinstance(saved, float):
            try:
                differs = float(started) != saved
            except (TypeError, ValueError):
                differs = True
        else:
            differs = str(started).strip() != str(saved)
        if differs:
            print(RESTART_LINE, file=ctx.out)
    return DONE


def _set(ctx: Context, name: str, args: list[str]) -> int:
    row = settings_table.BY_NAME[name]
    if not args:
        return _help(ctx, name)
    if len(args) > 1:
        if name == "color":
            return _refuse(ctx, f'color takes one quoted value, such as color "1 1 1 0"; got {len(args)} words', USAGE)
        return _refuse(ctx, f"{name} takes one value; got {' '.join(args)}", USAGE)
    if args[0] == "load" and name in _LOAD_REFUSED:
        return _refuse(ctx, PANEL_ONLY if name in _PANEL else RESTART_ONLY, REFUSED)
    if args[0] == "load" and name in ("scaling", "edge", "automute"):
        return _refuse(ctx, NEXT_ONLY, REFUSED)
    if args[0] == "load" and name in _LOADS:
        return _load(ctx, name)
    if args[0] == "load" and name in _RESHOW_LOADS:
        return _reshow_load(ctx, name)
    try:
        value = row.parse(args[0], ctx.cwd_entered)
    except UsageError as exc:
        return _refuse(ctx, f"{name} {exc}", USAGE)
    except Refused as exc:
        return _refuse(ctx, f"{name}: {exc}", REFUSED)
    if name in _FOLDERS and not os.path.isdir(value):
        return _refuse(ctx, f"{name} takes an existing folder; got {args[0]}", USAGE)
    if name == "assetsfolder" and any(c in value for c in ' \t"\n'):
        return _refuse(ctx, f"{name}: ASSETS_DIR {value!r} contains whitespace or quotes; the engine service "
                            "cannot represent it - move the assets to a plain path or leave ASSETS_DIR empty "
                            "for auto-discovery", USAGE)
    if name in _LINES:
        if name == "watchdog":
            return _line_write(ctx, name, str(value))
        return _line_write(ctx, name, " ".join(format_number(part) for part in value))

    if name == "speed" and value == 0:
        return _engine_only(ctx, "speed", "0", "set_speed", 0.0,
                            "applies now; not saved (speed load brings back the saved speed)")
    if name == "audiosmoothing":
        return _engine_only(ctx, name, row.format(value), "set_tuning", value, NOT_SAVED_NOW)
    edge: list[int] = []

    def change(current: dict) -> dict:
        new = (not current[row.key]) if value == settings_table.TOGGLE else value
        if isinstance(value, settings_table.Step):
            new = max(0, min(128, current[row.key] + value))
            if new != current[row.key] + value:
                edge.append(new)
        return {} if current[row.key] == new else {row.key: new}

    if name in _RESTART:
        return _restart_write(ctx, name, change)
    if name in _LIVE + _CONFIG_ONLY:
        return _live_write(ctx, name, change, edge)
    code = _receipt(ctx, name, _write(change, row.key))
    if name == "libraryfolder":
        _library_warning(ctx, value)
    return code


def _library_warning(ctx: Context, folder: str) -> None:
    """A library folder the engine does not search for wallpapers."""
    from ...storage import paths
    searched = {os.path.realpath(p) for p in (paths.default_wallpapers_dir(),
                                              os.path.expanduser("~/.local/share/lwe/wallpapers"))}
    if os.path.realpath(folder) not in searched:
        ctx.error(f"warning: the engine looks for wallpapers only in {paths.default_wallpapers_dir()} and the Steam "
                  f"Workshop folder, so a wallpaper copied into {folder} may not show")


def _config_set(ctx: Context, name: str, words: list[str]) -> int:
    if name not in settings_table.BY_NAME:
        return _unknown(ctx, name, CONFIG_USAGE)
    from ..registry import discover
    if name in _CONFIG_ONLY:
        return _set(ctx, name, words)
    verb = discover().get(name)
    return _unknown(ctx, name, CONFIG_USAGE) if verb is None else verb.run(ctx, words)


def _unset(ctx: Context, args: list[str]) -> int:
    if len(args) != 1:
        return _refuse(ctx, CONFIG_USAGE, USAGE)
    name = args[0]
    if name == "playlist":
        return _refuse(ctx, "playlist has no default", REFUSED)
    if name == "audiosmoothing":
        return _refuse(ctx, "audiosmoothing is not saved", REFUSED)
    if name in ("order", "interval"):
        try:
            from .playlist import UNSET
        except ImportError:
            return _unknown(ctx, name, CONFIG_USAGE)
        return UNSET[name](ctx)
    if name in _LINES:
        return _line_write(ctx, name, None)
    if name not in _PANEL + _RESTART + _LIVE + _CONFIG_ONLY:
        return _unknown(ctx, name, CONFIG_USAGE)
    from ...storage import settings
    key = settings_table.BY_NAME[name].key

    def change(current: dict) -> dict:
        clamp = name in ("resclamp", "effectclamp")
        changes = settings.clamp_unset_changes(current, key) if clamp else {key: None}
        return changes if changes[key] is not None or key in settings.load_set() else {}

    if name in _RESTART:
        return _restart_write(ctx, name, change)
    if name in _LIVE + _CONFIG_ONLY:
        return _live_write(ctx, name, change)
    return _receipt(ctx, name, _write(change, key))


def _quietly(fn: Callable[..., int], *args: object) -> int:
    """fn with the store's snapped-value warnings kept off stderr, as the reads keep them; config names
    the line."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fn(*args)


def _setting_verb(name: str) -> Callable[[Context, list[str]], int]:
    return lambda ctx, args: _quietly(_set, ctx, name, args)


def _reload(ctx: Context, args: list[str]) -> int:
    if args:
        return _refuse(ctx, f"reload takes no words; got {' '.join(args)}", USAGE)
    return _quietly(_reload_run, ctx)


def _reload_run(ctx: Context) -> int:
    """reload: one status read, every file checked, then the cleanup, sync_all with the re-show,
    engine-env rebuilt and the restart line; the report compares with reload's last snapshot."""
    from ... import version
    from ...engine import daemon_unit, marker, push
    from ...storage import lock
    from .. import validate
    from .backup import restart_line
    from .playlist import apply_cleanups
    first = push.read_status()
    status = first[1] if first[0] == "ok" else None
    if status is not None:
        refusal = version.running_refusal(status, version.panel_stamp())
        if refusal is not None:
            return _refuse(ctx, refusal, REFUSED)
    errors, warnings, cleanups = validate.check_all(status)
    if errors:
        for line in errors:
            ctx.error(line)
        return _refuse(ctx, f"reload refused: {len(errors)} {'error' if len(errors) == 1 else 'errors'}; "
                            "nothing was applied or written", REFUSED)
    changes = validate.changes()
    cleaned = ""
    if cleanups:
        with lock.held("settings"), marker.writing(("BUNDLE", "CURRENT")):
            cleaned = apply_cleanups(cleanups)
    outcome = push.sync_all("command", ("BUNDLE", "CURRENT"))
    env_state, env_error = None, None
    try:
        env_state = daemon_unit.write_env()
    except (OSError, ValueError) as exc:
        env_error = str(exc)
    waiting = restart_line()
    validate.write_snapshot()
    stopped = outcome.kind == report.REFUSED or outcome.reason == "version"
    if ctx.json:
        _print_json(ctx, {"changes": changes, "warnings": warnings, "cleanups": cleaned.splitlines(),
                          "outcome": outcome.kind, "reason": outcome.reason or "",
                          "message": outcome.message or "", "env": env_error or env_state, "restart": waiting})
        return REFUSED if stopped or env_error else DONE
    if changes is None:
        print(RELOAD_FIRST, file=ctx.out)
    elif not changes:
        print(RELOAD_SAME, file=ctx.out)
    for line in changes or []:
        print(line, file=ctx.out)
    for line in warnings:
        print(f"warning: {line}", file=ctx.out)
    if cleaned:
        print(cleaned, file=ctx.out)
    if outcome.kind == report.REFUSED:
        ctx.error(f"The engine refused it: {outcome.message or 'no reason given'}")
    elif outcome.reason == "version":
        ctx.error(outcome.message or "The running engine is from another build; run lwe service restart.")
    elif outcome.kind == report.APPLIED:
        print("Applied.", file=ctx.out)
    elif outcome.kind == report.PENDING:
        print(f"Not applied yet: the service is not running or is busy. {report.OPPORTUNITIES}", file=ctx.out)
    else:
        print(f"The engine did not answer in time, so it may have applied. {report.OPPORTUNITIES}", file=ctx.out)
    if env_state == "no screens":
        print(f"{NO_SCREENS_SAVED}.", file=ctx.out)
    if env_error:
        ctx.error(f"the engine file could not be written: {env_error}")
    if waiting:
        print(waiting, file=ctx.out)
    return REFUSED if stopped or env_error else DONE


VERBS = (
    Verb("get", _get, "Prints bare values, one per line, for scripts and status bars.", "Settings"),
    Verb("config", _config, "Lists every setting with its value and where it came from, or reads or sets one.",
         "Settings"),
    *(Verb(row["name"], _setting_verb(row["name"]), row["what"], "Settings") for row in vocabulary.SETTINGS
      if row["name"] in _PANEL + _RESTART + _LINES + _LIVE + ("audiosmoothing",)),
    Verb("reload", _reload, next(row["what"] for row in vocabulary.COMMANDS if row["name"] == "reload"),
         "Settings"),
)
