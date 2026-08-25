# Architecture

**Audience.** A developer who knows upstream linux-wallpaperengine (or C++ graphics
code generally) and wants to understand this fork: what it does differently, why those
choices were made, how the pieces fit, and where the code is. Read top to bottom once;
after that, use `FORK-MAP.md` (the companion document) as the per-capability reference
with file:line anchors.

**Conventions.** Paths are relative to the repository root. Line numbers refer to this
tree as published. "Upstream" is Almamu/linux-wallpaperengine at `b016d7d`, the base
this fork sits on. Everything stated here was verified against the code; anything that
rests on measurement rather than code says so.

---

## 1. The system at 10,000 feet

Upstream is a single process: it loads wallpapers and renders them, driven by the
compositor's frame callbacks, configured entirely at launch time.

This fork turns that into a small system of cooperating processes:

```
                        ┌─────────────────────────────────────────┐
                        │  lwe-ui  (control panel, PySide6/QML)   │
                        │  small tray process + on-demand window; │
                        │  manages systemd units                  │
                        └───────────────┬─────────────────────────┘
                                        │ JSON lines over Unix socket
                                        │ ($XDG_RUNTIME_DIR/lwe/engine.sock)
                                        ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  linux-wallpaperengine  (the engine; one process, one thread that        │
│  matters)                                                                │
│                                                                          │
│  - daemon mode: boots with surfaces up, restores its own persisted       │
│    state (wallpaper, rotation, settings), or waits idle for work         │
│  - command API: 25 verbs, strictly validated, id-correlated replies      │
│  - renders scenes/video into offscreen FBOs, presents to layer surfaces  │
│  - owns all GL, all wallpaper state, the rotation engine, fullscreen     │
│    policy, the running-apps rule, and the VRAM residency machinery       │
└───────────────┬──────────────────────────────────────────────────────────┘
                │ spawns on first web wallpaper (posix_spawn, hardened)
                ▼
┌──────────────────────────────────┐      ┌──────────────────────────────┐
│  lwe-web-service                 │      │  lwe-web-helper              │
│  owns CEF (browser process)      │─────▶│  tiny binary that hosts      │
│  exists only while a web         │ CEF  │  CEF's child processes       │
│  wallpaper does                  │spawn │  (renderer/zygote)           │
└───────────────┬──────────────────┘      └──────────────────────────────┘
                │ frames: shared-memory ring (zero socket traffic steady-state)
                │ control: length-prefixed binary over a second Unix socket
                ▼
        back to the engine, which uploads frames as textures
```

The design drivers, in the order they shaped the system:

1. **A wallpaper engine must answer when nothing is happening.** Upstream's Wayland
   loop blocks in `wl_display_dispatch` until the compositor sends events (upstream
   carries a TODO about exactly this). With monitors asleep (DPMS) there are no events,
   so a control command placed in the loop body would never run. Everything about the
   daemon's event loop follows from this.
2. **Chromium must not be able to kill the desktop.** Upstream links CEF into the
   engine; a renderer crash or CEF bug takes the wallpaper down with it. CEF is also a
   few hundred MB of process that web wallpapers need and nothing else does. So CEF
   lives in a separate, supervised, disposable service.
3. **It must be cheap enough to leave running all day.** Upstream holds every texture
   and FBO at full size forever. The fork caps texture residency to what the display
   can show, pools composite buffers, compresses textures at ingest, and - when a
   fullscreen or listed app needs the machine - releases its outputs entirely (VRAM
   freed, socket still answering) and takes them back on its own the moment the
   claim clears. A release also evicts the process's clean library pages, so a
   released engine reads ~60 MB in a process monitor, not hundreds. Both policies
   live in the engine itself; no outside watcher is involved.
4. **Parity with the Windows renderer is the point.** Scene lighting, 3D models,
   skeletal puppets, particle child systems, text objects, perspective cameras, fog -
   the things upstream stubs or drops are implemented, measured wallpaper by wallpaper.

---

## 2. The engine process

### 2.1 The main loop, and why it looks like this

Upstream's Wayland loop is `wl_display_dispatch(display)`, which blocks until Wayland
traffic arrives, with rendering driven by frame callbacks. The fork replaces it with
the textbook non-blocking Wayland integration in
`src/WallpaperEngine/Render/Drivers/WaylandOpenGLDriver.cpp` (`dispatchEventQueue`,
:502-604):

```
prepare_read → flush → poll(fds, timeout) → read_events/cancel_read → dispatch_pending
```

Three properties matter:

- **The poll set is the Wayland fd plus every command-API fd**
  (`getApiWakeFds`, :537-544). A socket command wakes the loop even when the compositor
  is completely silent.
- **The timeout is always bounded**: 100 ms with the API up, 2000 ms without (:548).
  Nothing ever sleeps forever.
- **`POLLERR|POLLHUP|POLLNVAL` on the display fd requests exit** (:558-562) instead of
  spinning on a dead compositor.

Frame callbacks no longer render inline from the listener. The callback only sets
`framePending` (`Render/Drivers/Output/WaylandOutputViewport.cpp:101-108`); the loop then renders each
signaled viewport once per pass (:577-588). Two further protections keep the loop
alive in adversarial conditions: if no frame has rendered for 2 seconds, every viewport
is re-kicked (keepalive, :590-594 with the app-side stamp at
`WallpaperApplication.cpp:2925-2950`) - this restarts the callback chain after
DPMS-on or hotplug - and `eglSwapInterval(0)` is forced per surface
(`Render/Drivers/Output/WaylandOutputViewport.cpp:300-307`) so a DPMS-off output can never wedge the swap.

At the top of every loop pass, before rendering, the app services its control surface
(`WallpaperApplication::show()` at :2900-2920): pending socket commands, the rotation
tick, the deadman tick, the fullscreen-gate tick, the running-apps poll, and the
crash-guard's survived mark. **Commands are answered even while paused, released, or
parked** - that is the whole point of the architecture.

The same discipline applies to replies: `CommandServer::respond()` waits for a wedged
client in bounded slices (500 ms total) and then drops it
(`Api/CommandServer.cpp:281-317`). A slow reader loses its connection, never the
engine.

### 2.2 The command API

New directory `src/WallpaperEngine/Api/`, two classes:

- **`CommandServer`** - transport. Non-blocking AF_UNIX socket, line-framed. Security
  is hard-coded in three layers: the runtime directory is created 0700 (only when the
  server created it), the socket is 0600, and every accepted peer must pass a
  `SO_PEERCRED` same-uid check (:164-181). A client that never terminates a line is
  dropped at 64 KiB buffered; at most 8 clients. The listen path doubles as a
  single-instance guard with probe-before-unlink semantics (:112-120).
- **`CommandDispatcher`** - validation. One JSON object per line,
  `{"id": int, "cmd": verb, "args": {...}}`. Every verb and argument is validated
  before any handler runs (`CommandDispatcher.cpp:185-443`); nesting depth is pre-capped
  to stop parser stack exhaustion (:188-209); wallpaper ids must match
  `[A-Za-z0-9_-]{1,64}`, so no wire input can be path-shaped (:175-183). The 25 verbs
  and their full argument contracts are tabulated in FORK-MAP.md chapter 8.

Replies follow an **accepted-then-done** pattern: long commands (`show`, `next`,
`prev`) ack immediately and send `done`/`failure` with the same id when the
multi-second work finishes, so a client can tell "never heard you" from "working on
it" (schema comment, `CommandDispatcher.h:16-20`).

The handlers live in `WallpaperApplication.cpp` (`handleApiCommand`, :1302-1683) and
are deliberately the thick part: the Api/ classes stay pure transport+validation, the
app owns all state changes. This is the fork's single largest body of new code and the
main porting cost (see the cherry-pick guide in FORK-MAP.md).

### 2.3 Daemon mode and the boot sequence

`--daemon` (`ApplicationContext.cpp:552-563`) implies the API socket, defaults the
fullscreen policy to Stop (the engine handles it itself; persisted state and client
verbs both override), and exempts the launch from the "at least one background"
requirement. A daemon boot registers every screen's layer surface with an **empty
path** (`--screen-root` with no wallpaper), so outputs exist and the loop runs.

The daemon then restores its own state. Every mutating verb - and every successful
scheduled rotation advance - persists the durable state (current show with args,
the rotation set, pause, fps, volume, toggles, policies) to
`$XDG_STATE_HOME/lwe/engine-state.json` via temp+rename, and an idle daemon boot
replays it through the same core paths a client `show` takes
(`persistRuntimeState` :2297, `restoreRuntimeState` :2345). A crash or restart
therefore comes back on the wallpaper that was actually on screen. A restart is invisible
without any client connected. A crash-loop guard tracks boot survival in
`boot-history.json`: when the last two boots died within 60 seconds of starting,
restore is refused and the engine boots idle, naming the re-arm path in its log.
systemd starts the engine at graphical-session; no client needs to exist.

`--api-socket` alone gives you the socket with a normal (wallpapered) boot.

### 2.4 `show`: the hot swap

`show` is the heart of the system. `applyShowCore`
(`WallpaperApplication.cpp:1969-2156`):

1. Resolve the id against the lwe library roots, then the Steam workshop - never a raw
   path (`resolveLibraryBackground`, :1816-1842). Preflight-parse the project.
2. Apply per-show args (color correction, speed, properties, volume, scaling, clamp,
   skip sets, fullscreen policy) - scaling/clamp are written **before** the rebuild
   because they key the mirror groups (:2017-2059).
3. Rebuild everything: tear down scenes, evict sole-owner cache textures, reload,
   rebuild (`rebuildForCurrentBackgrounds`, :1844-1868).
4. On any exception, restore the 12 snapshotted previous settings and rebuild the old
   set (:2073-2098). A failed show never leaves a half-applied state.
5. On success, push the previous show onto a 20-deep history deque (this is what
   `prev` pops) and stamp the current show with the client's opaque `ui_id`, which
   survives engine-driven advances so the panel can tell "the tile the user clicked"
   apart from "the base wallpaper".

### 2.5 Rotation

`rotate-set` replaces the whole playlist atomically; each entry carries the full
per-show vocabulary. The engine then owns the schedule: sequential / shuffle (full
permutation before reshuffle) / random, and `avoid_repeat` re-rolls against the
current display id. Disabling freezes the countdown: re-pushing an unchanged disabled
set preserves the remaining time (a changed set freezes at the full interval), and
re-enabling the same set resumes where it froze via a backdated clock
(`apiRotateSet`, :2123-2187; the transition logic at :2166-2176; tick at
:2416-2440). The next pick is pre-drawn so `status` can name it. A manual `show`
restarts the countdown. One scheduler, always: the panel pushes sets but never
schedules.

### 2.6 Output lifecycle: release / acquire / deadman / fullscreen-stop / app rule

Outputs are a state machine with a reason stack (`ReleaseReason { Live, Verb, Deadman,
Fullscreen, AppCondition }`, `WallpaperApplication.h:220`):

- **`release-outputs` / `acquire-outputs` verbs** - explicit control. Release tears
  down GL first (while a context is still current) and then the layer surfaces;
  acquire rebuilds. Idempotent; a Verb hold cannot be downgraded by other sources
  (:2664-2738).
- **Deadman switch** - after the first `ping` is ever seen, if both pings and renders
  stop for `LWE_DEADMAN` seconds (default 300), outputs release themselves: the orphan
  reflex for "the panel died, don't burn GPU forever" (`tickDeadman`, :2740-2763).
  A ping under a Deadman hold re-acquires; under a Verb hold it does not.
- **Fullscreen stop** - when the live policy is `stop` and a relevant fullscreen app
  appears, outputs shed; they re-acquire the moment it clears (`tickFullscreenGate`,
  :2845-2875).
- **Running-apps rule** - `set-app-conditions` gives the engine a list of process
  names and a behavior (off/pause/stop). A 3-second poll of `/proc/PID/comm` -
  process existence is the only honest signal for a windowless CLI process like a
  local LLM - drives it: pause is the master-pause fact (timescale 0, prior speed
  restored on release), stop releases the outputs with reason `app` and re-acquires
  when no listed process remains (`tickAppCondition`, :2795-2843). Each mechanism
  only ever undoes what it engaged; holds owned by a bench, the deadman, or the
  fullscreen gate are never stolen.

The fullscreen policy itself is now tri-state - Off / Pause / Stop
(`FullscreenBehavior`, `ApplicationContext.h:31-38`) - live-settable via
`set-fullscreen`, with a live-editable app-id ignore list (`set-fullscreen-ignore`
triggers an immediate detector recount, `Render/Drivers/Detectors/WaylandFullScreenDetector.cpp:282-289`).
Pause means freeze (rendering halts, allocations stay);
Stop means release the outputs entirely.

### 2.7 VRAM: the steady-state pipeline

The shipped answer to VRAM cost is steady-state, not pause-time: composite FBOs are
leased from a per-scene ping-pong pool instead of dedicated per layer (`LWE_FBOPOOL=0`
disables), sized to on-screen coverage and clamped to output size x `LWE_SSFACTOR`
(canvas/view split, `CScene.cpp:1411-1448`), and mip residency can cap uploads to the
largest live output dimension with per-frame demand expansion (on by default,
`LWE_TEXDETAIL=full` opts out;
`MipResidency.cpp`). Offline BC7/BC4/BC5 compression is ingested from a disk cache
keyed by sha256 of the decoded mip0 (`uploadFromTexcache`, `CTexture.cpp:203-311`;
the encoder shim is `tools/texcomp/lwe_bc7enc.cpp`, x86-64 only via ispc; the cache
producer is the panel's import wizard). And the full-reclaim path is not pause at all:
it is `stop` - the fullscreen/app-condition policies that release the outputs or stop
the engine service outright (section 2.6, section 4).

---

## 3. The web stack: CEF out of the engine

Upstream created `WebBrowserContext` inside the engine and linked libcef into it. The
fork moves CEF out of the engine process entirely (the engine lib no longer compiles
or links any CEF code; the `WebBrowser/` classes build into the two web binaries)
and replaces it with three processes:

- **`lwe-web-service`** (`src/web-service-main.cpp`) owns `CefInitialize`, the scheme
  handlers, and the browser instances. It is spawned by the engine only when a web
  wallpaper is actually created (`CWeb`'s ctor -> `HelperClient::create` ->
  `ensureHelper` -> `spawnService`, `WebHelper/HelperClient.cpp:609-613, 251-303,
  122-142`), and it **exits when
  the last web wallpaper goes away** (default 1 s grace, `LWE_WEB_IDLE_EXIT_MS`;
  `Service/HelperServer.cpp:19-58`) or immediately when the engine disconnects.
- **`lwe-web-helper`** (`src/web-helper-main.cpp`) is an 11-line binary set as CEF's
  `browser_subprocess_path`, so CEF's renderer/zygote children exec something tiny
  instead of the service.

**Control channel** (`WebHelper/Protocol.*`, `MessageChannel.*`): length-prefixed
binary over a Unix socket - Create/Resize/MouseMove/MouseClick/InjectProperties/
SetProperty/AudioSpectrum/Destroy down, FrameReady/PageLoaded/PageFailed up. Caps:
1 MiB per message, 8 MiB queued output, 16384x16384 max dimensions. Same-uid
authenticated, 0600 socket, protocol-version handshake at spawn.

**Frame transport** (`WebHelper/FrameContract.*`): per-instance POSIX shm object
(`/dev/shm/lwe-web-<pid>-<id>-<gen>`), fixed header + two BGRA slots, seqlock
publishing. The engine maps it read-only after validating every header field; the
socket only carries a `FrameReady` once per generation. Steady-state video frames
cross zero socket bytes.

**Supervision** (`HelperClient`): spawn hardening captures the startup signal mask and
closes fds >= 3 in the child (`SpawnGate.cpp`); an unexpected death SIGKILLs the child,
unlinks orphaned shm, replays every instance and its properties into a respawned
helper, with doubling backoff and a crash-loop cooldown (`LWE_WEB_CRASHGUARD`). A
Catch2 test proves a non-web session never spawns anything
(`Testing/Cases/WebHelperStartupCost.cpp`), and a 1700-line integration probe
(`tools/web-frame-probe.cpp`) measures frame transport, respawn fidelity, and orphan
hygiene.

The `wp<workshopId>` URL scheme universe is enumerated from the wallpaper library at
spawn and baked into the service's config, so a helper can only ever serve wallpapers
that were in the library when it started.

---

## 4. The control panel

`lwe-ui/` is entirely fork-new (upstream has no UI). A PySide6/QML tray app whose job
is to be the daemon API's reference client and the system's owner:

- It **generates and manages** `~/.config/systemd/user/lwe-engine.service` and the
  engine's env file (`engine/daemon_unit.py`), reconciling drift at every start.
- It **drives the verbs**: `show` with fully resolved per-wallpaper args, `rotate-set`
  on every playlist change, `set-*` for live dials, `ping`+`status` every 2 seconds -
  which doubles as the deadman heartbeat. Policy is pushed once per panel life, on
  first sight of an engine; a restarted engine restores its own persisted state, and
  the panel deliberately does NOT re-push on re-arrival (that would feed the engine's
  crash-loop guard).
- It **owns the library workflow**: browse/search, playlists, per-wallpaper editor
  (autosaving, presence-as-setness conf model), a bench that test-renders new workshop
  items on a throwaway engine while the desktop engine stands down
  (`release-outputs`), and an import wizard that requires a human verdict before
  anything enters rotation.
- Display policy lives in the engine, not the panel: the fullscreen gate and the
  running-apps condition are both engine-side detectors (see section 2), configured by
  the panel via `set-fullscreen`, `set-fullscreen-ignore`, and `set-app-conditions`.
  There are no UI-side watcher processes.

Its durable settings/playlist/per-wallpaper state is shell-sourceable KEY=value files
under `~/.config/lwe/` (Tier A); the same directory also carries JSON state (meta,
discover, theme) and `tags.csv`. The full layout is in FORK-MAP.md chapter 9.

---

## 5. Three flows worth tracing in the code

**A click on a wallpaper tile (daemon era).**
`models.py showNow` -> `api_client.show` with resolved args -> socket ->
`WallpaperApplication.processApiRequests` (the per-pass drain point), which loops
`CommandServer.drain` -> `CommandDispatcher.parse` (validates) -> `handleApiCommand` ->
ack `accepted` -> `applyShowCore` (args, rebuild, rollback on failure, history push) ->
`done` -> panel updates Now Playing from the next `status` poll. Anchors in section 2.2-2.4.

**A web wallpaper frame.**
CEF paints offscreen in the service -> `RenderHandler` copies into the shm back slot ->
seqlock release-store -> `FrameReady` once per generation -> engine `CWeb` maps/validates
the ring -> per frame, `FrameReader.consume()` latches the live slot (abandons after 4
torn reads, keeps old texture) -> `glTexSubImage2D`. Socket silent the whole time.
Anchors in section 3.

**A fullscreen game starts (policy = stop, the shipped reclaim path).**
Wayland detector recount -> `tickFullscreenGate` sees a relevant fullscreen toplevel ->
`apiReleaseOutputs`: GL is torn down while a context is still current, then the layer
surfaces are destroyed - the desktop goes from full scene VRAM to nothing, and the
socket still answers while released. Game exits -> detector clears -> outputs
re-acquire and the wallpaper rebuilds. (With policy = `pause` the scene merely
freezes and keeps its allocations. The outermost option is the panel stopping
`lwe-engine.service` entirely.) Anchors in section 2.6.

---

## 6. Directory guide

| Path | What it is |
|---|---|
| `src/WallpaperEngine/Api/` | **new** - command socket transport + validation |
| `src/WallpaperEngine/WebHelper/` | **new** - protocol, shm frames, spawn gate, client/server |
| `src/WallpaperEngine/Application/` | app context (flags/settings), WallpaperApplication: all daemon handlers, rotation, lifecycle |
| `src/WallpaperEngine/Render/` | scenes, textures, FBOs, camera, drivers; **new**: MipResidency, OverlayLabel |
| `src/WallpaperEngine/Render/Objects/` | renderables; **new**: CModel, CLight, PuppetModel |
| `src/WallpaperEngine/Render/Shaders/` | GLSL assembly incl. the generated LightingV1 module |
| `src/WallpaperEngine/Data/` | parsers, models, BinaryReader/MemoryStream; **new**: AnimationTimeline, Sha256 |
| `src/WallpaperEngine/Scripting/` | QuickJS engine, modules, builtins.js; **new**: LocalStorageObject, VectorModule |
| `src/WallpaperEngine/Audio/`, `Input/`, `VideoPlayback/` | drivers; **new**: NullAudioDriver, PointerMoveGate |
| `src/WallpaperEngine/Logging/` | **new** - InstrumentRegistry (runtime-toggleable log gates) |
| `src/WallpaperEngine/Testing/Cases/` | Catch2 suite (run `build/output/tests`; ctest is not wired) |
| `src/web-service-main.cpp`, `src/web-helper-main.cpp` | **new** - binaries owning CEF |
| `tools/texcomp/`, `tools/web-frame-probe.cpp` | **new** - BC7 encoder shim; web-stack integration probe |
| `lwe-ui/` | **new** - the control panel (own README, own test suite) |

---

## 7. Invariants to respect when changing code

These are the rules the system depends on; breaking any of them is a bug even if it
compiles:

1. **One thread owns the loop.** Rendering, command execution, and all state changes
   happen on the main thread. Nothing in the command path may block it: poll timeouts
   are bounded, replies are budgeted, respawns are one-attempt-per-pass.
2. **Wire input is never trusted.** Ids are charset-validated before resolution; JSON
   depth and size are capped; every handler-side read is preceded by dispatcher
   validation of that exact key. Keep it that way when adding verbs.
3. **GL ids are not stable across rebuilds.** Mip-residency expansion
   (`CTexture::expandResidency`) deletes and recreates texture objects, so holders
   must re-query `getTextureID` rather than cache a `GLuint`. The texture registry
   exists for this path - `expandCappedTexture` resolves a `TextureProvider*` back
   to its live `CTexture` - and the re-query rule stays as long as mip expansion
   exists. Video-backed textures are the exception: their GL object belongs to the
   player and expansion skips them.
4. **The `LWE_*` namespace is three things, not one.** Bisect kill switches (e.g.
   `LWE_FBOPOOL=0`, `LWE_NOBLOOM`) default to the shipped behavior and exist to
   isolate regressions. Plain configuration dials (socket paths, `LWE_DEADMAN`,
   mpv cache/thread caps, `LWE_SSFACTOR`) tune the system. Debug instruments
   (`LWE_*DUMP`, `LWE_*PROBE`) are development logging; the three meant for runtime
   toggling go through `InstrumentRegistry`, which refuses launch-time-only gates.
   One caveat when adding one: the panel writes several of these into the engine
   env file, so the value in force at runtime may have been decided in
   `lwe-ui/src/lwe_ui/engine/daemon_unit.py` rather than at the getenv site.
5. **One scheduler, one owner.** Rotation is owned by the engine in the daemon era;
   the panel pushes sets but never schedules. Output holds stack by reason; a Verb
   hold cannot be downgraded.
6. **The panel and engine agree through the socket, not through files** - except the
   documented config handoffs (`~/.config/lwe/`, the engine-env file). If you add
   state, decide which side owns it and say so in the verb's contract.

## 8. Where to start reading

1. `src/WallpaperEngine/Api/CommandDispatcher.h` - the wire contract, 55 lines.
2. `WaylandOpenGLDriver.cpp:502-604` - the loop.
3. `WallpaperApplication.cpp:2900-2920` - what happens every pass; then
   `handleApiCommand` (:1315) and `applyShowCore` (:1969).
4. `WebHelper/Protocol.h` and `FrameContract.h` - the web stack's two contracts.
5. `CScene.cpp` ctor (:42-353; the assembly body starts at :102) - how a scene comes
   together (bloom ladder, lights, clamping); then `renderFrame`.
6. `lwe-ui/src/lwe_ui/api_client.py` - the client side of the socket, small and
   readable; then `models.py` for how the panel thinks.

After that, FORK-MAP.md gets you from any capability name to its code.

---

*Companion document: `FORK-MAP.md` - the per-capability reference with full switch,
verb, and env-var tables and the cherry-pick guide. If this narrative and the code
ever disagree, the code is right; the anchors are where to check.*
