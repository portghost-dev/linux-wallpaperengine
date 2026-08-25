# linux-wallpaperengine (extended fork)

A heavily extended fork of [Almamu/linux-wallpaperengine](https://github.com/Almamu/linux-wallpaperengine),
the open-source engine that runs Wallpaper Engine backgrounds on Linux. This project
has used AI to assist in various aspects including parity work, engine architecture
changes, UI development, and documentation. This is also my first attempt on such a
complex project. There are going to be bugs needing to be fixed and methods needing
to be changed along the way.

The point of publishing is not to compete with upstream, but to act as a proof of
concept for the community, and to give back to it. Upstream built the foundation that
makes a project like this possible at all, and anything here that upstream wants is
theirs for the taking. None of this would exist without Almamu's engine, and my work
on this project building on upstream's code has given me a great appreciation for the
incredible effort that it must have taken to build this from scratch.

This fork's goal is a few things: make scene wallpapers render the way they do on
Windows with full parity, make the engine cheap enough on system resources to leave
running all day, and to provide the community with code and concept ideas that it can
take, modify, and use in open-source projects. The parity work here was built and
verified against the real Windows renderer, wallpaper by wallpaper, over several
months of side-by-side comparison. It's still nowhere near 100% complete, but most
scenes run remarkably close.

This was built and tested on a system with a 5000 series NVIDIA GPU and a 9000 series
AMD CPU running CachyOS with Hyprland. Brief verification was done on a VM running
CachyOS with KDE Plasma. The engine was briefly checked for portability using the AMD
iGPU successfully.

## What it looks like

The library, with the engine live on the desktop behind it. The status strip is
real: 75 wallpapers indexed, a 4K scene playing at the capped 30 FPS, 364 MB of
VRAM.

![the library grid](docs/images/library-grid.png)

Texture compression at import. The wizard measures the scene and offers the
real numbers before it touches anything - here, 239 MB of raw textures down to
59 MB of BC7.

![the compression wizard](docs/images/compression-wizard.png)

Per-scene editing: the knobs the scene author exposed, per-object exclusion for
every object in the scene, and the engine's own tuning rail.

![the editor](docs/images/editor.png)

Fourteen themes, defaulting to true-black OLED.

![the theme picker](docs/images/theme-picker.png)

The Quick Panel floats over whatever you are doing - global controls and the
running scene's own properties without opening the full window. Light themes
are real, not an afterthought.

![the quick panel on a light theme](docs/images/quick-panel-light.png)

The repository is a pair that ships together:

- the engine (this directory) - the wallpaper renderer and its daemon
- [`lwe-ui/`](lwe-ui/) - the control panel, a PySide6/QML app split into a small
  tray process and a full window that starts on demand, driving the engine
  through its command API

[`ARCHITECTURE.md`](ARCHITECTURE.md) explains the machine: how the pieces fit, why
the design went this way, and where to start reading.
[`docs/FORK-MAP.md`](docs/FORK-MAP.md) is the per-capability reference behind it,
with file and line anchors for every claim, a complete switch and verb map, and a
guide to which pieces can be lifted on their own.

## What is different from upstream

### Rendering
- Camera and projection work: orthographic and perspective cameras, parallax, zoom,
  and script-driven view changes now behave much closer to the Windows renderer.
- Particles: playback-rate dilation, start times, event-driven children
  (spawn/death/follow), puppet skeletal animation, animated-texture cycle behavior,
  instance overrides, and count-override semantics.
- Scene lighting: light objects, a from-scratch reimplementation of the generated
  lighting shader module, corrected light-direction conventions, and mesh
  winding/chirality fixes. 3D model objects render.
- An HDR bloom ladder (RGBA16F), used when a scene's bloom calls for it.
- Script engine: the module subsystem works, scripts tick values renderers actually
  read, object angles use the documented units, and an audio-response API is
  available to scripts. Per-object sound volume applies as playback gain.
- Text objects: placement, point sizing, and width-limit truncation.

Parity is judged wallpaper by wallpaper against the Windows client; plenty of scenes
now look right, and the ones that do not are how the work continues.

### Performance and VRAM
- Roughly half the VRAM of upstream on typical scenes, and on some scenes
  considerably more than half saved. The pieces: ingest-time BC7/BC4/BC5 texture
  compression (visually gated before it was made the default), FBO pooling for layer
  composites, a mip-residency texture pipeline so VRAM tracks what is actually on
  screen, and per-scene fixes found by measuring against the Windows client's
  footprint.

### Architecture
- Two-service design: the wallpaper engine proper and a separate web-content
  service. Chromium (CEF) is spawned only when a web wallpaper is actually in use
  and torn down to zero when it is not.
- A daemon mode with a Unix-socket command API: switch wallpapers, query status,
  pause, drive rotation, and change settings live without restarting anything.
- The daemon owns its own state: current wallpaper, rotation set, and playback
  settings persist to disk and restore on boot, so a service restart is
  invisible and no client has to babysit the engine. A crash-loop guard boots
  it idle instead of restoring into a repeating failure.
- Live property reload, a fullscreen-app policy handled by the engine itself
  (it frees the outputs when something goes fullscreen and takes them back the
  moment it clears), and a running-apps rule: while a listed process is up, for
  example a local LLM that needs the VRAM, the engine pauses or stands down on
  its own and comes back when the process exits. A stood-down engine is honest
  about it: VRAM is freed and resident memory drops to roughly 60 MB until the
  outputs come back.
- Console output from a misbehaving wallpaper is rate-limited so it cannot
  drown the engine's own logs.
- Hardened parsers for the binary formats a wallpaper package can carry, and hard
  caps on what a client of the command socket can do.

## The control panel (lwe-ui)

The engine's daemon API is the center of the architecture, and `lwe-ui/` is its
main client: a control panel that runs as a small tray process plus a full
window opened on demand, so closing the panel returns its memory while the
quick actions stay a right-click away. Library browsing,
rotation playlists, per-wallpaper settings (scaling, fps, color correction,
animation speed, scene properties), Workshop import with a bench-test wizard,
theming, and a developer view exposing the engine's debug instruments.

The panel is installed by `install.sh` along with the engine. See
[`lwe-ui/README.md`](lwe-ui/README.md) for details.

## Installing

```
git clone https://github.com/portghost-dev/linux-wallpaperengine
cd linux-wallpaperengine
bash install.sh
```

`install.sh` installs the dependencies through pacman, builds the engine, and
puts the panel in its own virtualenv. It prints what it is doing at each step and
lets pacman ask before installing anything.

Scope, stated plainly: the project is x86-64 only, and it has been tested on
CachyOS under Hyprland and KDE Plasma. Other Arch-family systems should work.
Other distributions have not been tried; the build itself is ordinary cmake, so
adapt the dependency list from upstream's README and use the manual steps below.

## Uninstalling

If you enabled the engine service from the panel, stop it first:

```
systemctl --user disable --now lwe-engine.service
rm -f ~/.config/systemd/user/lwe-engine.service
```

Everything else lives under your home directory:

```
rm -f ~/.local/bin/linux-wallpaperengine ~/.local/bin/lwe-web-service
rm -f ~/.local/bin/lwe_bc7enc ~/.local/bin/lwe-ui
rm -rf ~/.local/lib/lwe-engine ~/.local/share/lwe-ui
rm -f ~/.local/share/applications/lwe-ui.desktop
rm -rf ~/.config/lwe ~/.local/state/lwe
```

The last line removes your settings, playlists, and the texture cache; keep it
if you plan to reinstall. The packages pacman installed are ordinary system
packages and stay; remove them with pacman if nothing else uses them.

## Building the engine by hand

```
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
make -C build -j$(nproc)
```

Arch's system Python refuses direct installs, so the panel wants a virtualenv.
Reusing the system Qt bindings keeps it from downloading a second copy of Qt:

```
python -m venv --system-site-packages ~/.local/share/lwe-ui/venv
~/.local/share/lwe-ui/venv/bin/python -m pip install ./lwe-ui
```

The configure step downloads the matching CEF binary distribution (large, one
time). Dependency list and asset discovery are unchanged from upstream; see their
README if you are setting up from nothing.

Two notes for source builds:

- CEF's binary distribution ships a stripped `libvulkan.so.1` that can hijack the
  link when mpv pulls in a Vulkan-enabled libplacebo, surfacing as an undefined
  `vkCreateXlibSurfaceKHR`. The configure step deletes that copy, so a normal
  build resolves Vulkan against the system loader and needs no intervention.
- CEF ships only Release binaries. For a RelWithDebInfo build, symlink
  `RelWithDebInfo -> Release` inside the extracted CEF directory.

## Driving the engine from a shell

The panel is optional. The daemon speaks one JSON object per line over
`$XDG_RUNTIME_DIR/lwe/engine.sock`, and anything that can write to a Unix socket
can drive it:

```
printf '{"id":1,"cmd":"status"}\n' | socat - "UNIX-CONNECT:$XDG_RUNTIME_DIR/lwe/engine.sock"
printf '{"id":2,"cmd":"show","args":{"id":"1311951951"}}\n' | socat - "UNIX-CONNECT:$XDG_RUNTIME_DIR/lwe/engine.sock"
printf '{"id":3,"cmd":"next"}\n' | socat - "UNIX-CONNECT:$XDG_RUNTIME_DIR/lwe/engine.sock"
```

Replies come back as JSON lines with the same `id`; long commands answer
`accepted` first and `done` when the swap finishes. Every state-changing command,
and every scheduled rotation advance, is persisted by the engine itself, so a
rotation set up from the shell survives crashes and restarts with no client
running, and comes back on the wallpaper that was actually up. The full verb list with every
argument and bound is in [`docs/FORK-MAP.md`](docs/FORK-MAP.md) chapters 1 and
8; the wire schema is documented in `src/WallpaperEngine/Api/CommandDispatcher.h`.

## Troubleshooting

- On a machine with no usable GPU, CEF falls back to software rendering through
  `vulkan-swrast`. Web wallpapers still run, but slowly. This is a fallback, not
  a supported configuration.
- On KDE Plasma, the Peek at Desktop shortcut (Meta+D by default) hides the
  wallpaper along with the windows, because the wallpaper is a desktop-layer
  surface. Press it again to bring it back.

## About this repository

This is a snapshot publication: one commit on top of the upstream base it was
forked from.

Two of the calibration instruments used to bring the renderer to parity ship
in `tools/instruments/`: a generated HDR-bloom test scene and a generated
lighting probe, each with its generator, its readout script, a capture, and a
README explaining how to use it.

## Credits

- [Almamu](https://github.com/Almamu) and the linux-wallpaperengine contributors.
  This fork stands entirely on their engine, and it is meant as a thank-you to
  that work, not a replacement for it.
- Fixes and ideas were adopted from the parallel forks by
  [ian-vinson](https://github.com/ian-vinson) and
  [Haberno](https://github.com/Haberno), including CEF bootstrap fixes, live
  property reload, audio device handling, config parsing, package lookup behavior,
  and parts of the shadow and tube-light work that extended this fork's existing
  lighting system. Their work is credited here rather than inline in the source.
- Texture compression uses Intel's
  [ISPC Texture Compressor](https://github.com/GameTechDev/ISPCTextureCompressor),
  vendored under `src/External/ISPCTextureCompressor/` and used under the MIT
  license included there.
- Wallpaper Engine itself is the work of
  [Kristjan Skutta](https://store.steampowered.com/app/431960/Wallpaper_Engine/).
  This project renders content you own through your own Steam license; it ships
  none of Wallpaper Engine's assets.

## License

GPLv3, same as upstream. See LICENSE.
