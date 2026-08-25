# lwe-ui

PySide6 + QML control panel for [linux-wallpaperengine](https://github.com/Almamu/linux-wallpaperengine).

Manages a local Wallpaper Engine library: browse wallpapers, build rotation playlists,
edit per-wallpaper engine settings (scaling, fps, clamp mode, color correction, animation
speed, object skips, scene properties), and drive the engine daemon over its command
API. A developer view exposes the engine's debug instruments for parity and rendering
work.

The panel is two processes through one entry point. `--tray` runs a small resident
tray (icon, pause/next/stop quick actions, launcher; no QML) and is what autostart
launches; the bare command opens the full window, which exits on close and returns
all of its memory. They appear as `lwe-ui` and `lwe-ui-tray` in a process monitor
rather than as two anonymous `python3` entries. The engine needs neither to keep running: it persists and
restores its own state. Everything goes over the daemon's Unix-socket API, with
on-disk config at `~/.config/lwe/` as the durable store. The "minimize to tray
when closed" setting decides whether closing the window leaves the tray running
or exits everything.

## Run

```
PYTHONPATH=src python -m lwe_ui           # the window
PYTHONPATH=src python -m lwe_ui --tray    # the resident tray
```

Requires Python 3.11+ and PySide6 >= 6.6.

## Tests

Plain runnable scripts, no pytest needed. Each one sandboxes its config into a temp
directory and never touches a live setup:

```
for t in tests/test_*.py; do
  PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software python3 "$t"
done
```

## Layout

- `src/lwe_ui/` - the app (storage, discovery, engine invocation, bench, QML UI)
- `tests/` - test suites
