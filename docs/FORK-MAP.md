# Fork capability map

**What this is.** A complete, code-grounded map of everything this fork adds or changes
relative to upstream [Almamu/linux-wallpaperengine](https://github.com/Almamu/linux-wallpaperengine)
at base commit `b016d7d`. It exists so that upstream maintainers and other developers can
see every capability at a glance, find its code in under a minute, and judge what it would
take to cherry-pick.

**How it was made.** Every claim in this document was produced by reading the fork's actual
source and diffing it against the upstream base. Every claim carries a `file:line` anchor
into the publication tree.
Anything that could not be determined from code is explicitly marked **Uncertain**. Where a
code comment and the code disagreed, the code won and the disagreement is noted.

**Conventions.** All paths are relative to the repository root. Line numbers refer to this
repository's tree. "Upstream" means commit `b016d7d`. `src/External/` is vendored
third-party code and out of scope except where the build wires it in.

## The shape of the delta

- ~135 upstream files modified, plus two whole new engine directories
  (`src/WallpaperEngine/Api/`, `src/WallpaperEngine/WebHelper/`) and ~34 other new engine
  files (new render objects, mip residency, overlay label, animation timeline, instrument
  registry, localStorage, pointer gate, null audio driver, sha256, new binaries:
  `lwe-web-service`, `lwe-web-helper`, `lwe_bc7enc`, the web-frame probe tool).
- The entire `lwe-ui/` control panel is fork-new (upstream has no UI component).
- Vendored dependencies under `src/External/` (previously upstream submodules, plus the fork-added
  ISPCTextureCompressor) are third-party code and not counted.

## The one-paragraph version

The fork does four big things. (1) It turns the engine into a **daemon**: a same-uid-only
Unix socket speaks a validated 25-verb JSON protocol (`show`, `rotate-set`, `next/prev`,
`set-*`, `release/acquire-outputs`), the Wayland event loop is rebuilt around a bounded
`poll()` that services commands even when no frames render, output lifecycle is a
state machine driven by a deadman switch, fullscreen policy, a running-apps rule, and
explicit verbs, and the daemon persists its own state and restores it on boot so a
restart needs no client. (2) It
**removes CEF from the engine process**: web wallpapers run in a supervised
`lwe-web-service` binary that exists only while a web wallpaper does, with frames crossing
a versioned shared-memory ring. (3) It closes a large slice of **rendering parity**:
scene lighting with shadow mapping, 3D model objects, skeletal puppet animation, particle
child systems and events, text objects, perspective cameras, fog, HDR bloom - features
upstream stubs or drops. (4) It makes the engine **cheap to run all day**: FBO pooling,
coverage-sized composites, mip-residency capping, offline BC7 texture compression,
lazy audio, and a stop-the-engine policy for fullscreen or listed apps that hands back
all of it. On top of all of it sits `lwe-ui/`,
a control panel (a small resident tray process plus an on-demand window) that is the
daemon API's reference client.

## Chapters

1. [Daemon mode and the command API](#1-daemon-mode-and-the-command-api)
2. [The two-service web architecture (CEF leaves the engine)](#2-the-two-service-web-architecture)
3. [Render core, VRAM and the texture pipeline](#3-render-core-vram-and-the-texture-pipeline)
4. [Render objects: lighting, 3D, particles, text, shaders](#4-render-objects-lighting-3d-particles-text-shaders)
5. [Data layer, binary parsers, filesystem](#5-data-layer-binary-parsers-filesystem)
6. [Script engine](#6-script-engine)
7. [Audio, input, video playback, fullscreen policy](#7-audio-input-video-playback-fullscreen-policy)
8. [The complete switch and surface map](#8-the-complete-switch-and-surface-map)
9. [The control panel (lwe-ui)](#9-the-control-panel-lwe-ui)
10. [Cherry-pick guide](#10-cherry-pick-guide)
11. [Known rough edges](#11-known-rough-edges)

---

# 1. Daemon mode and the command API


### Unix-domain command socket (transport layer)
- **What it does**: A non-blocking AF_UNIX/SOCK_STREAM server that accepts line-framed requests and writes line-framed replies on the same connection. `listen()` doubles as a single-instance guard: an existing socket file is probed with `connect()` - a live listener means refusal to start (never unlinking a live instance's socket), only `ECONNREFUSED` is treated as a stale file and removed (CommandServer.cpp:112-120). `drain()` accepts all pending connections and buffers partial lines per client until newline; CRLF tolerated, blank lines ignored (CommandServer.cpp:217-298). `respond()` retries a wedged reader in 50 ms poll slices up to a 500 ms total budget, then drops the client so a slow reader can never hang the render loop (CommandServer.cpp:300-336).
- **Where it lives**: fork-only new file `src/WallpaperEngine/Api/CommandServer.{h,cpp}`; destructor closes all fds and removes the socket file only if this instance created it (`m_ownsSocketFile`, CommandServer.cpp:54-70, flag set at :146).
- **Surface**: default path resolution `$LWE_SOCKET` -> `$XDG_RUNTIME_DIR/lwe/engine.sock` -> `/tmp/lwe-<euid>/engine.sock` (CommandServer.cpp:72-84). Limits: `MAX_LINE_BYTES = 64*1024`, `MAX_CLIENTS = 8` (CommandServer.h:17-18). Backlog = MAX_CLIENTS (CommandServer.cpp:153).
- **Coupling**: fully self-contained module (POSIX sockets + `Log.h`); nothing upstream touched. Lifts alone with its CMake entry (CMakeLists.txt:313-316) plus the caller hooks in WallpaperApplication.
- **Tests**: `src/WallpaperEngine/Testing/Cases/CommandSocket.cpp` - 8 cases: 0600 socket / 0700 dir perms (:83), live-instance refusal (:104), stale-socket reclaim (:122), line framing/split-write/CRLF/blank-line (:141), 64 KiB overflow drop (:191), respond round-trip (:219), `fds()` growth (:244), `LWE_SOCKET`/`XDG_RUNTIME_DIR` path resolution (:263).

### Socket security model
- **What it does**: Three concentric controls. (1) Directory created `0700` - but only if the server created it, so a custom path in a shared dir isn't rewritten (CommandServer.cpp:94-110). (2) Socket bound under `umask(0177)` and then explicitly `chmod 0600` (CommandServer.cpp:135-151). (3) Every accepted peer is verified with `SO_PEERCRED` and rejected unless `peer.uid == geteuid()` (CommandServer.cpp:183-200). The same credentials feed an audit trail: `peerDescription()` (:165-181) names the requester (pid + /proc comm), and the engine logs it on every `release-outputs`/`acquire-outputs` - the verbs that blank or restore the desktop. A client that never sends a newline is dropped at 64 KiB buffered (CommandServer.cpp:269-273); the 8-client cap refuses further connections (CommandServer.cpp:236-240).
- **Where it lives**: same fork-only files as above (`authenticatePeer`, CommandServer.cpp:183; called from `drain` at :232).
- **Surface**: no flags; the security posture is hard-coded. `$LWE_SOCKET` can relocate the socket into a less-safe directory - the 0600 socket mode is then the only filesystem control (comment at CommandServer.cpp:102-104).
- **Coupling**: self-contained; same module as above.
- **Tests**: CommandSocket.cpp perms case (:83-102) and overflow-drop case (:191-217). The uid check itself has no test (UNCERTAIN: exercised only implicitly since tests run same-uid).

### Wire protocol + dispatcher (validation layer)
- **What it does**: One JSON object per line: `{"id": int, "cmd": verb, "args": {...}}` (schema documented in CommandDispatcher.h:10-20). `parse()` rejects non-objects, missing/non-integer `id`, missing/non-string `cmd`, non-object `args`, and any verb not in a 25-entry whitelist (CommandDispatcher.cpp:11-35, 186-243). Before JSON parsing it scans bracket depth and refuses nesting > 64 to stop stack exhaustion in nlohmann's recursive parser (CommandDispatcher.cpp:188-209); JSON is parsed with exceptions disabled (:212). Background ids must match `[A-Za-z0-9_-]{1,64}` - no dots, no separators, so no wire input can be path-shaped (CommandDispatcher.cpp:175-183; the rejection deliberately does not echo the value back, :251). Response builders emit `accepted` / `done(result)` / `failure(id, message)` (CommandDispatcher.cpp:445-455).
- **Where it lives**: fork-only `src/WallpaperEngine/Api/CommandDispatcher.{h,cpp}`. Pure parsing/validation, no engine or socket dependency (by design, CommandDispatcher.h:22-27).
- **Surface** - the 25 verbs and their validated argument contracts (all bounds enforced in `parse`, CommandDispatcher.cpp):
  - `status`, `quit`, `list-objects`, `next`, `prev`, `ping`, `pause`, `resume`, `release-outputs`, `acquire-outputs` - no args.
  - `show` - requires string `args.id` (validBackgroundId); optional `cc` [b,c,s 0..4, hue +/-6.4] (:36-53), `speed` 0..20 (:55), `properties` <=64 entries, keys `[A-Za-z0-9_]{1,64}`, string values <=256 chars, number/bool/string only (:64-88), `scaling` stretch/fit/fill/default (:90), `clamp` clamp/border/repeat (:98), `volume` int 0..128 (:106), booleans `audio_processing`/`mouse`/`automute`/`fullscreen_pause` (:114), `fullscreen_behavior` off/pause/stop (:122), `skip_objects` <=256 ints 0..1e6 (:133), `skip_effects` <=64 ints 0..1e7 (:149), `ui_id` string <=128 (:165).
  - `rotate-set` - `entries` <=512, each an object with valid `id` plus the full show vocabulary (:261-282); `interval_s` int 15..604800 (:284); `order` sequential/shuffle/random (:293); booleans `avoid_repeat`/`enabled` (:302); `label` <=128 (:309).
  - `set-skip` - `ids` <=256 ints 0..1e6 (:344). `set-fps` - int 1..480 (:360). `set-speed` - 0..20 (:370). `set-tuning` - at least one of `classic_k`/`classic_exp`/`audio_gain`, finite (:380). `set-volume` - int 0..128 (:400). `set-mouse`/`set-audio`/`set-parallax`/`set-particles` - bool `enabled` (:409-439). `set-instrument` - string `name` + bool `enabled` (:423). `set-fullscreen-ignore` - `app_ids` <=128 non-empty strings <=128 chars (:441). `set-fullscreen` - `behavior` in off/pause/stop, required because the handler indexes it directly (:458-468). `set-app-conditions` - required `behavior` off/pause/stop, optional `names` <=128 strings of 1..64 chars, matched literally against `/proc/PID/comm` (:317-342).
- **Coupling**: self-contained (depends only on nlohmann/json). Lifts alone.
- **Tests**: `src/WallpaperEngine/Testing/Cases/CommandDispatcher.cpp` - 11 cases covering valid parses (:11), malformed-input rejections with parseable JSON error bodies (:36), id echo/null (:63), hostile path-shaped ids incl. `../../etc/passwd`, `$(rm -rf ~)`, 65-char overrun (:71), cc/speed bounds (:93), properties validation incl. the 64-entry cap (:121), show-requires-id (:167), set-skip/skip_effects (:173), show vocabulary (:205), rotation/transport verbs incl. set-fps and set-fullscreen edge values (:242), and response-builder shapes (:312).

### Accepted-then-done command handling
- **What it does**: Short commands reply once with `done`; long commands (`show`, `next`, `prev`) reply `accepted` immediately, then `done` or `failure` with the same id when the multi-second scene rebuild finishes, so clients can distinguish "never heard you" from "working on it" (schema comment, CommandDispatcher.h:16-20). The dispatch point is `processApiRequests()` -> `CommandDispatcher::parse` -> `handleApiCommand` (WallpaperApplication.cpp:1287-1313); parse rejections get the errorResponse written straight back; after every mutating verb the drain loop re-persists the runtime state (:1302-1311). Every handler branch responds; a final catch-all answers "verb accepted but not implemented" if the whitelist and the handler chain ever drift (:1716-1720).
- **Where it lives**: modified upstream `WallpaperApplication.cpp`: `handleApiCommand` :1315-1720; ack sites at :1364 (next/prev) and :1946 (show).
- **Coupling**: deeply woven into WallpaperApplication - every handler reads/writes `m_context.settings`, `m_renderContext`, `m_fullScreenDetector`, or the instrument registry. See per-verb notes below.

### `show` - all-outputs hot swap with rollback and history
- **What it does**: Resolves the id against `$XDG_DATA_HOME/lwe/wallpapers`, `$HOME/.local/share/lwe/wallpapers` (both requiring `<id>/project.json`), then Steam workshop app 431960 - never a raw path (`resolveLibraryBackground`, :1816-1842). Preflights by parsing project.json leniently and requiring `type`+`file` (:431-449), re-acquires outputs if released, makes any viewport current, acks, then `applyShowCore` (:1928-2115): clears skip-sets, applies args (cc, speed, properties, volume/audio/mouse/automute, fullscreen policy, scaling/clamp written into per-screen maps BEFORE rebuild because they key mirror groups, :2017-2059), assigns the new path to every non-span screen, and rebuilds everything via `rebuildForCurrentBackgrounds` (:1844-1868: clearWallpapers -> clear projects -> evict sole-owner texture cache -> loadBackgrounds -> setupProperties -> ensure browser/audio per project -> buildWallpapers -> `malloc_trim`). On exception it restores ~14 previous settings and rebuilds the old set (:2073-2098). Success pushes the previous full show record (id + ui_id + args) onto a 20-deep history deque (:2103-2109) and stamps `m_currentShow` with the opaque `ui_id` echo (:2111-2113). A manual show restarts the rotation countdown (:1916-1924).
- **Where it lives**: modified `WallpaperApplication.cpp`: `apiShow` :1870, `applyShowCore` :1928, `rebuildForCurrentBackgrounds` :1844, `resolveLibraryBackground` :1816. Launch defaults snapshot `m_showDefaults` captured in ctor (:139-147); omitted args fall back to it, not to current values.
- **Coupling**: deeply woven - touches loadBackgrounds (`:176`, now skips screens with empty path when no defaultBackground, :195-199), preset/dependency resolution in `loadBackground` (:223-256, project.json without `type` but with `dependency` recursively loads the base, depth-capped at 4, then applies `preset` property overrides), `buildWallpapers` mirror-group keying (:942-1009), `parseLenient` JSON (also in preflight and ApplicationContext config load), and `RenderContext::evictUnusedTextures`. A porter needs the whole rebuild chain, not just `apiShow`.
- **Tests**: none directly (dispatcher-side arg validation only).

### Rotation-set model (`rotate-set`, `next`, `prev`, timed advance)
- **What it does**: `apiRotateSet` (:2123-2187) replaces the whole set; `enabled` requires entries non-empty (:2152). Disabling freezes the countdown (`frozenRemainingSeconds`); re-enabling the SAME set (same display-id list + interval) resumes the clock via a backdated `lastShow` rather than insta-rotating (:2166-2176). Picking: `sequential` walks an index, `random` is uniform, default `shuffle` exhausts a full permutation before reshuffling (:2203-2217); `avoidRepeat` (default true, :2151) re-rolls up to 8 times against the current display id, where display id = `ui_id` when present so presets sharing a base wallpaper still count as distinct (:2219-2234, helper :2117-2121). The next pick is pre-drawn (`apiRotationPredraw` :2239) so `status.rotation.next_up` can name it (:1745-1754). `tickApiRotation` (:2959-2988) advances when the interval elapses (and persists the runtime state on success), skips entirely while outputs are released (clock keeps counting; overdue advance fires on first tick after acquire), and re-arms a full interval on failure. Advance tries each entry up to set-size attempts, skipping entries that fail resolve/preflight/apply (:2260-2279). `next`/`prev` are manual transports: ack-then-done, `prev` pops history (never re-pushes) and applies with `recordHistory=false` (:1344-1392); prev fails cleanly on empty history (:1346-1348).
- **Where it lives**: state struct `m_apiRotation` + `m_showHistory` + `m_currentShow` in WallpaperApplication.h:304-330; logic in WallpaperApplication.cpp as cited.
- **Coupling**: sits entirely on top of `applyShowCore`/`resolveLibraryBackground`; self-contained given those.
- **Tests**: dispatcher-shape only (CommandDispatcher.cpp:242-310).

### Output release/acquire, deadman switch, fullscreen-stop gate
- **What it does**: A `ReleaseReason { Live, Verb, Deadman, Fullscreen, AppCondition }` state machine (WallpaperApplication.h:220, 343). `apiReleaseOutputs` (:2667-2718) tears down GL first while a surface still holds the context current (scenes, decoders, cached textures die -> VRAM freed), then asks the driver to destroy layer surfaces; failure rebuilds and reports. Idempotent, and a Verb hold cannot be downgraded to Deadman (:2668-2676). After a successful release, `evictResidentPages` (:2720-2799) trims the heap and `MADV_DONTNEED`s every fully-CLEAN read-only private file mapping, so a released engine's RSS drops to skeleton size (~60 MB) instead of parking hundreds of MB of resident-but-reclaimable library pages; only mappings with zero dirty bytes are dropped (RELRO segments are read-only yet hold relocated data - dropping one is a crash), and the pages refault from page cache on acquire. `apiAcquireOutputs` (:2801-2823) re-creates surfaces then rebuilds wallpapers. `tickDeadman` (:2825-2848): if a ping was ever seen, and BOTH no ping and no render for `m_deadmanSeconds`, it releases outputs (orphan reflex); a failed release re-arms by faking a ping. A `ping` (:1410-1424) refreshes the heartbeat AND re-acquires outputs if the hold is Deadman - explicitly why Verb holds can't be downgraded. `tickFullscreenGate` (:2930-2957): when live policy is `FullscreenBehavior::Stop` and the detector reports fullscreen, shed outputs; re-acquire the moment it clears; never touches Verb/Deadman holds. `tickAppCondition` (:2880-2928): a 3-second `/proc/PID/comm` poll for the running-apps rule (`set-app-conditions`, names + off/pause/stop) - pause drives timescale 0 and restores the prior speed, stop releases with reason `app` and re-acquires when no listed process remains; each mechanism only undoes what it engaged. `processApiRequests`, the four policy ticks (fullscreen gate, app condition, rotation, deadman), the property-reload check, the boot-survival mark, and the web-helper pump all run at the top of every main-loop pass in `show()` (:2990-3011), before `render()` - so commands are serviced even while paused or released.
- **Where it lives**: WallpaperApplication.cpp as cited; driver half below.
- **Surface**: `m_deadmanSeconds` default 300 (WallpaperApplication.h:341), overridden by `$LWE_DEADMAN` (0..86400; 0 disables, ctor :149-156). Verbs `release-outputs`/`acquire-outputs` (:1649-1671), `ping`, `set-fullscreen` (:1603-1623, updates both live settings and `m_showDefaults` so later shows don't resurrect the old policy), `set-app-conditions` (:1625-1647; conditions persist with the runtime state). `FullscreenBehavior` enum in ApplicationContext.h:31-38; default `Pause` (ApplicationContext.h:234 in fork's designated init); `--no-fullscreen-pause` now also writes `fullscreenBehavior = Off` (ApplicationContext.cpp:501-504); `--daemon` defaults `Stop` - the engine owns the policy; persisted state and verbs override (:556-563).
- **Coupling**: requires the two new `VideoDriver` virtuals (`releaseOutputSurfaces`/`acquireOutputSurfaces`, default `false` - VideoDriver.h fork addition) and the pause-policy rewiring in `render()` (:1121-1235: pause decision now reads `fullscreenBehavior == Pause` instead of upstream's `pauseOnFullscreen`, plus `m_manualPauseRequested`).
- **Tests**: none.

### Runtime state persistence + crash-loop guard
- **What it does**: Every mutating verb re-persists the durable runtime state - current show (id, ui_id, full args), the rotation set with per-entry args, pause, fps, volume, audio/mouse/parallax/particles toggles, speed, color correction, fullscreen policy + ignore list, tuning globals, and the app conditions - to `$XDG_STATE_HOME/lwe/engine-state.json` via temp+rename (`persistRuntimeState`, WallpaperApplication.cpp:2297-2343; the drain-loop hook :1302-1311). Every successful SCHEDULED rotation advance persists too (`tickApiRotation`, :2985-2987), so a crash or restart restores the wallpaper that was actually on screen, not the one at the last verb. An idle daemon boot (`--daemon`, no CLI backgrounds) restores it by replaying the same core paths a client uses - settings writes, `applyRotateSet`, then resolve/preflight/`applyShowCore` (`restoreRuntimeState`, :2345-2544). Boot survival is tracked in `boot-history.json`: each boot appends `survived:false` and flips it after 60 s of uptime (`markBootSurvived`, :2546-2568); when the last two recorded boots died young, restore is REFUSED and the engine boots idle, naming the re-arm path (delete the history file) in its log.
- **Where it lives**: fork-only additions to `WallpaperApplication.{h,cpp}`; state dir helper `runtimeStateDir` (:2284-2295) honors `$XDG_STATE_HOME` and never touches `~/.config/lwe` (the panel owns it).
- **Surface**: the state file and `boot-history.json`; no new verbs or env vars. A boot with CLI-specified backgrounds never restores.
- **Coupling**: reads/writes the same members the verb handlers use; portable with the daemon layer as a unit.
- **Tests**: exercised live (restart restores in seconds with zero clients; triple-SIGKILL produces the refusal); no Catch2 case yet.

### Running-apps condition (engine-side)
- **What it does**: `set-app-conditions {names:[/proc comm strings], behavior: off|pause|stop}` arms a 3-second poll of `/proc/PID/comm` (`tickAppCondition`, WallpaperApplication.cpp:2880-2928). While a listed process exists: `pause` sets timescale 0 (the master-pause fact) and restores the prior speed on release; `stop` releases the outputs with `ReleaseReason::AppCondition` (status reason `app`) and re-acquires when no listed process remains. Process existence is the only honest signal for windowless CLI processes (a local LLM holding the VRAM is the motivating case). Holds owned by a bench, the deadman, or the fullscreen gate are never stolen; each mechanism only undoes what it engaged. Conditions persist with the runtime state.
- **Where it lives**: handler :1625-1647, tick as cited, validation `CommandDispatcher.cpp:317-342`, status block `app_condition` (:1795-1798).
- **Surface**: the verb; `status.app_condition {behavior, count, engaged}`.
- **Coupling**: uses the release/acquire machinery and `setTimescale`; needs both.
- **Tests**: exercised live (pause and stop legs against a probe process); no Catch2 case yet. Names match EVERY process comm - clients should send specific names (the kernel caps comm at 15 chars).

### Wallpaper console rate-limit
- **What it does**: `console.log`/`console.error` from wallpaper scripts collapse identical messages to one line per 10 s window, with the suppressed count appended on the next emission (`Scripting/ConsoleObject.cpp:16-58`). A broken wallpaper logging per frame (~540 lines/s measured) previously blew past journald's rate limit and suppressed the engine's own diagnostics. The distinct-message map is capped at 512 and cleared wholesale so a message-varying wallpaper cannot grow it.
- **Where it lives**: `Scripting/ConsoleObject.cpp`, file-local.
- **Surface**: none; behavior only.
- **Coupling**: none. Lifts alone.
- **Tests**: none.

### Poll-based Wayland event loop + API wake-up
- **What it does**: Upstream's blocking `wl_display_dispatch` is replaced by a proper prepare_read/flush/poll/read_events/dispatch_pending cycle (WaylandOpenGLDriver.cpp:521-575). The poll set is the Wayland display fd PLUS every API fd from `WallpaperApplication::getApiWakeFds()` (:537-544; app side :1277-1283) - so a socket command wakes the loop even with no frame callbacks arriving. Timeout is bounded always: 100 ms with the API up, 2000 ms without (:548). `POLLERR|POLLHUP|POLLNVAL` on the display fd (dead compositor) requests exit instead of spinning (:558-562). Frame callbacks no longer render inline from the listener: the callback just sets `framePending` (WaylandOutputViewport.cpp:101-108) and the dispatch loop renders each pending viewport ONCE per pass (:577-588).
- **Where it lives**: modified upstream `src/WallpaperEngine/Render/Drivers/WaylandOpenGLDriver.cpp` (`dispatchEventQueue` :502-604), `WaylandOutputViewport.{h,cpp}` (`framePending` flag h:45, callback cpp:101-108). New virtual hooks on `VideoDriver.h` (:57-59 in fork). Only the Wayland driver calls `getApiWakeFds` (grep-verified: single call site, :537).
- **Surface**: FPS cap read fresh each pass via `std::max(1, maximumFPS)` (:517) - this is what makes `set-fps` live; the frame-limiter usleep is at :598-603.
- **Coupling**: confined to the Wayland driver + viewport + the two VideoDriver base virtuals + `getApiWakeFds` on the app. A porter needs those four pieces; the loop is otherwise self-contained.
- **Uncertain**: on X11/GLFW no driver polls API fds, so command latency there depends on that driver's own loop cadence - `processApiRequests` is non-blocking and runs every pass (:2449), X11/GLFW loop timing is not characterized here.

### Render keepalive + DPMS safety
- **What it does**: `WallpaperApplication::update()` stamps an atomic `m_lastRender` tick on every serviced frame (:2464-2468). If `secondsSinceLastRender() > 2.0` (:2485-2495), the driver re-kicks EVERY viewport in the render set (:590-594) - this restarts the frame-callback chain after DPMS-on, hotplug, or a compositor that stopped scheduling callbacks; the always-bounded poll timeout guarantees the check runs (:546-548). Separately, `makeCurrent` forces `eglSwapInterval(0)` once per surface (WaylandOutputViewport.cpp:300-307) so `eglSwapBuffers` can never block on a DPMS-off output and wedge the single-threaded loop (which would take the command socket down with it - comment in code). Viewports without surfaces drop out of the render set because `WaylandOutput::updateViewports` skips `layerSurface == nullptr` (WaylandOutput.cpp:11-26).
- **Where it lives**: WaylandOpenGLDriver.cpp:590-594; WaylandOutputViewport.cpp:290-308 (`swapIntervalConfigured` flag, h:58-59); WallpaperApplication.cpp:2925-2956.
- **Coupling**: small and separable - keepalive needs `m_lastRender` stamp + `secondsSinceLastRender`; swap-interval needs the flag + 3 lines in `makeCurrent`.
- **Tests**: none.

### Output hotplug / hot-unplug
- **What it does**: Add: upstream's `handleGlobalRemoved` TODO is implemented - registry removal matching a viewport's `waylandName` routes to `onLayerClose` (:151-161), which clears `viewportInFocus` if it names the dying viewport (dangling-deref fix for missing pointer-leave on unplug, :269-274), destroys EGL/wl objects INCLUDING `wl_output` (added, :297-300), erases the screen, resets the output set (:266-310). Remove->add: `wl_output.done` now calls `onOutputAnnounced` (WaylandOutputViewport.cpp:63-70), which - only after startup init (`m_outputsInitialized`, set at :438), only for configured screens without a layer surface - sets up xdg-output if needed, creates the layer surface, resets the output set, and primes the frame-callback chain with one `update()` (driver :406-424). `releaseOutputSurfaces`/`acquireOutputSurfaces` (:366-404) reuse the same per-viewport `teardownSurfaces` (WaylandOutputViewport.cpp:168-198, nulls everything and resets all flags) / `setupLS` (:200-286) pair.
- **Where it lives**: WaylandOpenGLDriver.{cpp,h} (:85, :98 for `onOutputAnnounced`/`m_outputsInitialized` declarations) and WaylandOutputViewport.cpp as cited.
- **Coupling**: moderate; needs `shouldSetupScreen` (:350-364, shared with startup) and the viewport teardown. Independent of the socket/API except that release/acquire are the driver's half of the verbs.
- **Tests**: none.

### Daemon mode, CLI flags, signals, env dials
- **What it does**: `--daemon` sets `daemonMode` + `apiSocket` and forces fullscreen policy Off (ApplicationContext.cpp:552-559); it exempts the launch from the "at least one background" requirement (:693-696), and `loadBackgrounds` then skips every screen whose path is empty (WallpaperApplication.cpp:197-201) - surfaces exist (screens registered by `--screen-root` with empty path, ApplicationContext.cpp:315) but no wallpaper until `show`. `--api-socket` alone enables the socket with a normal boot (:547-550); `setupApi` is called from `setup()` (WallpaperApplication.cpp:1093) and fails LOUDLY via `sLog.exception` if the socket can't be bound (:1270-1274). Unknown CLI args are now logged instead of silently ignored (ApplicationContext.cpp:688-692). Signals: SIGUSR1 -> deferred property reload from `--properties-file` JSON ({screen:{prop:value}}), applied via `property->update` and pushed to the scene script engine or CWeb `notifyPropertyChanged` (main.cpp:91, WallpaperApplication.cpp:2958-2970, `checkPropertyReload` :2993-3064; flag ApplicationContext.cpp:586-589); SIGUSR2 toggles manual pause (:2965-2969); SIGINT/SIGTERM stop. main.cpp also adds a crash backtrace handler for SEGV/ILL/BUS/FPE/ABRT that prints to stderr then re-raises for the core dump (:27-52), `mallopt(M_ARENA_MAX,2)` + `M_MMAP_THRESHOLD=128K` under glibc (:60-65), and drops upstream's uncatchable SIGKILL handler.
- **Surface** (env vars): `$LWE_SOCKET`, `$XDG_RUNTIME_DIR` (CommandServer.cpp:73-78); `$LWE_DEADMAN` (:149); `$LWE_CC` "b c s hue" (:122); `$LWE_TIMESCALE` (:130); `$LWE_CLASSICK`/`$LWE_CLASSICEXP`/`$LWE_AUDIOGAIN` seeding the tuning globals (defaults 16.0/2.0/1.0, :56-65); `$LWE_TIMESTATS` (:1162); `$LWE_MOUSEDBG` (driver :28-31); `$LWE_EGLDEBUG` (viewport :315). Kill switches: no `--api-socket` = no server at all (setupApi early-return :1264); `LWE_DEADMAN=0` disables deadman.
- **Related but adjacent**: `set-tuning` writes globals `g_LweClassicDivisor`/`g_LweFalloffExp`/`g_LweAudioGain` clamped 0.01..1000 / 0.5..6 / 0.1..20 (:1451-1475); `set-instrument` round-trips through `Logging::instrumentKnown/instrumentSet` and refuses launch-time gates (:1511-1539; registry seeded from env at show() :2903).
- **Coupling**: CLI/flags are additive hunks in ApplicationContext; signals are small main.cpp changes; the env dials are ctor/static-init reads. Each is individually portable, though `--daemon` is meaningless without the show/rebuild machinery.
- **Tests**: none for these directly (socket path env test at CommandSocket.cpp:263).

### Status introspection (`status` verb)
- **What it does**: `apiStatus` (:1685-1814) returns api version 1, pid, uptime, per-screen wallpaper paths, current show id+ui_id, manual_pause, tuning globals, connected client count, cc, speed, outputs state/reason/deadman_s/ping_seen, full rotation block (enabled, interval, next_in_s - honoring frozen countdown, :1734-1755), volume/audio/mouse/automute/fps/frames/parallax/particles, fullscreen ignore list + behavior, live instruments, and scaling/clamp of the first non-span screen. `list-objects` (:1636-1677) dumps scene object ids/names plus image-effect chains (ids usable with `set-skip`/`skip_effects`), first screen only since mirror groups share one scene.
- **Coupling**: read-only aggregation over the same state; portable with the state it reads.

---

## 5-line summary

The fork adds a daemon/command layer: a same-uid-only UNIX socket server (new `src/WallpaperEngine/Api/`, 0600 socket/0700 dir/SO_PEERCRED/single-instance guard) speaking one-JSON-per-line with a strictly validated 25-verb dispatcher (`show`, `rotate-set`, `next/prev`, `ping`, `pause/resume`, `set-*`, `release/acquire-outputs`, `set-app-conditions`) using an accepted-then-done reply pattern. `--daemon`/`--api-socket` flags boot the engine idle (surfaces up, no wallpaper); an idle daemon boot restores its own persisted state (every mutating verb re-writes `$XDG_STATE_HOME/lwe/engine-state.json` atomically; a crash-loop guard refuses restore after two boots that died young) and otherwise awaits `show`, which hot-swaps all outputs with per-show args, rollback, and 20-deep history; a rotation engine (shuffle-permutation/sequential/random, avoid-repeat, freezable countdown) advances on a timer. Output lifecycle is managed by a release/acquire state machine driven by four sources - verb, a ping-fed deadman switch (`LWE_DEADMAN`, default 300 s), a fullscreen-stop gate, and a running-apps rule polling `/proc` - all serviced from the top of the main loop so commands work while parked. The Wayland driver's blocking dispatch is replaced by a bounded poll loop that includes the API fds, renders each callback-signaled viewport once per pass, forces `eglSwapInterval(0)` (DPMS safety), re-kicks rendering after 2 s of silence (keepalive), and implements output hotplug/hot-unplug. Verified by two Catch2 suites (`CommandSocket.cpp`, `CommandDispatcher.cpp`); the engine-side handlers (~1,900 new lines in `WallpaperApplication.cpp`) are deeply woven into settings/rebuild state and are the main cherry-pick cost.

---

# 2. The two-service web architecture


All paths are relative to the repository root unless prefixed `upstream:`. Every claim below is taken from the code rather than from documentation.

---

### 1. Three-binary split: engine (no CEF) / `lwe-web-service` (CEF browser process) / `lwe-web-helper` (CEF subprocess host)

- **What it does**: The fork takes CEF entirely out of the engine process. Upstream created `WebBrowserContext` inside `WallpaperApplication` (upstream:`src/WallpaperEngine/Application/WallpaperApplication.cpp:330,539`) and linked libcef into `linux-wallpaperengine-lib` (upstream:`CMakeLists.txt:597-598`). The fork builds two new executables: `lwe-web-service` (`src/web-service-main.cpp`), which owns `CefInitialize`, the scheme handlers and the browser instances; and `lwe-web-helper` (`src/web-helper-main.cpp`), a 11-line subprocess binary set as `settings.browser_subprocess_path` (`src/WallpaperEngine/WebBrowser/WebBrowserContext.cpp:119-121`) so CEF's render/zygote children exec a tiny binary instead of the service. The engine lib no longer links or compiles any CEF code - fork `CMakeLists.txt:608-628` link list contains no cef (the lib target still carries CEF include paths and copies the CEF binaries into the output dir - build wiring, not compilation); all `WebBrowser/*` sources moved into the `lwe-web-service` target (`CMakeLists.txt:740-765`), `SubprocessApp` into `lwe-web-helper` (`CMakeLists.txt:718-724`). The service is spawned only when a web wallpaper is actually created: `CWeb`'s constructor calls `HelperClient::create` (`src/WallpaperEngine/Render/Wallpapers/CWeb.cpp:47-50`), which calls `ensureHelper()` -> `spawnService()` (`src/WallpaperEngine/WebHelper/HelperClient.cpp:609-613,251-303`). `WallpaperApplication::setupBrowser` enumerates web wallpapers but deliberately does not start anything (`src/WallpaperEngine/Application/WallpaperApplication.cpp:710-731`).
- **Where it lives**: New files `src/web-service-main.cpp`, `src/web-helper-main.cpp`, all of `src/WallpaperEngine/WebHelper/`. Heavily rewritten upstream files: `WebBrowserContext.{cpp,h}` (constructor now takes `CefMainArgs&`, `SpawnConfig&`, `MediaSource&` instead of `WallpaperApplication&` - `WebBrowserContext.h:22-25`), `BrowserApp.{cpp,h}`, `SubprocessApp.{cpp,h}`, `BrowserClient.{cpp,h}`, `RenderHandler.{cpp,h}`, `WPSchemeHandlerFactory.{cpp,h}`. `WPSchemeHandler.{cpp,h}` are byte-identical to upstream (verified by diff).
- **Surface**: Service CLI switches parsed by `SpawnConfig::fromArguments` (`src/WallpaperEngine/WebHelper/SpawnConfig.cpp:47-101`): `--lwe-assets-dir=` (required), `--lwe-web-socket=` (required), `--lwe-max-fps=`, `--lwe-protocol=` (required; must equal `PROTOCOL_VERSION` = 1, `Protocol.h:13`, mismatch is a hard refuse at `SpawnConfig.cpp:94-98`), and one `--lwe-scheme=<workshopId>=<path>` per wallpaper (`SpawnConfig.cpp:65-74`). Helper-side switch: `--lwe-schemes=<id>,<id>,...`, appended by `BrowserApp::OnBeforeChildProcessLaunch` (`BrowserApp.cpp:107-118`) and parsed pre-CEF by `SubprocessApp::parseSchemeIds` (`SubprocessApp.cpp:44-74`, used at `web-helper-main.cpp:7`). If `lwe-web-helper` is absent next to the service binary, the code logs and leaves `browser_subprocess_path` unset (`WebBrowserContext.cpp:119-126`).
- **Coupling**: Touches upstream broadly but shallowly: `CWallpaper::fromWallpaper` gained a `WebHelper::HelperClient*` parameter (`src/WallpaperEngine/Render/CWallpaper.h:161-165`, `CWallpaper.cpp:421-441`) with three call sites in `WallpaperApplication.cpp` (537, 992, 1063); `WallpaperApplication` gained `m_webHelper`, `enumerateWebBackgrounds`, `ensureWebHelperClient`, `setupBrowser`, `ensureBrowserForProject` and one `pumpEvents()` call per frame (`WallpaperApplication.cpp:2914-2917`); `main.cpp` gained one line (`src/main.cpp:71`). A porter must take: the whole `WebHelper/` tree, the two mains, the rewritten `WebBrowser/` CEF classes, `CWeb.{cpp,h}`, the `fromWallpaper` signature change, the WallpaperApplication wiring, and the CMake target changes. It is a coherent module but not liftable as a single directory - the factory signature change is the invasive part.
- **Tests**: `src/WallpaperEngine/Testing/Cases/WebHelperStartupCost.cpp:26-68` asserts a `HelperClient` never spawns a process across 240 `pumpEvents()` calls and that `allocateInstance()` alone stays `Idle`.
- **Uncertain**: The missing-helper fallback path is questionable from code: a re-exec'd CEF child of `lwe-web-service` would run `web-service-main.cpp`'s `main`, which parses the spawn config and calls `HelperServer::start()` (web-service-main.cpp:56-71) *before* `CefExecuteProcess` (invoked inside `WebBrowserContext`, WebBrowserContext.cpp:55) - and `start()` fails when the parent already holds the socket (`MessageChannel.cpp:267-271`). Whether that actually breaks CEF children depends on which switches CEF propagates to re-exec'd children; CEF's switch propagation is not verified here, and the code's own log message only warns about process-name census miscounting (`WebBrowserContext.cpp:122-125`).

### 2. Spawn hardening (`SpawnGate`)

- **What it does**: `SpawnGate::captureAtStartup()` (`src/WallpaperEngine/WebHelper/SpawnGate.cpp:51-63`) runs early in `main()` after the mallopt tuning and crash-handler install (`src/main.cpp:71`, also in the probe at `tools/web-frame-probe.cpp:1566`), resolving the service binary via `/proc/self/exe` (`SpawnGate.cpp:39-48`), recording the startup thread count from `/proc/self/status` (`SpawnGate.cpp:22-37`) and the process signal mask. `spawn()` (`SpawnGate.cpp:77-167`) uses `posix_spawn` with `POSIX_SPAWN_SETSIGMASK` restoring the startup mask (SpawnGate.cpp:114-115), and closes every fd above stderr in the child: `posix_spawn_file_actions_addclosefrom_np(3)` on glibc >= 2.34, else a `/proc/self/fd` enumeration fallback (SpawnGate.cpp:125-151). Environment is inherited wholesale, which is the documented channel for `LWE_CEFLOG`/`LWE_CEFDEBUG`/`LWE_WEB_IDLE_EXIT_MS` (SpawnGate.cpp:153-156).
- **Where it lives**: New files `src/WallpaperEngine/WebHelper/SpawnGate.{h,cpp}`. One-line hooks in `src/main.cpp:71` and `tools/web-frame-probe.cpp:1566`.
- **Surface**: No flags or env of its own; exposes `serviceBinary()`, `threadsAtCapture()` diagnostics (`SpawnGate.h:21-23`). Late capture degrades gracefully with a logged warning (`SpawnGate.cpp:78-83`).
- **Coupling**: Self-contained; a porter needs the two files plus the `main.cpp` line.
- **Tests**: Indirectly, via `WebHelperStartupCost.cpp` (spawn counters) and the probe's lifecycle scenario.

### 3. Message protocol and channel (socket verbs, caps, peer auth)

- **What it does**: Length-prefixed binary protocol over a Unix stream socket. 8-byte `MessageHeader {u32 length, u16 type, u16 flags}` (`src/WallpaperEngine/WebHelper/Protocol.h:66-73`, static_assert 8 bytes). Commands (engine->helper, type < 0x8000): `Create, Resize, MouseMove, MouseClick, InjectProperties, SetProperty, AudioSpectrum, Destroy` (`Protocol.h:15-24`); events (helper->engine): `FrameReady, PageLoaded, PageFailed` (`Protocol.h:26-29`). Encoders at `Protocol.cpp:148-303`; every payload starts with the `InstanceId`. `PayloadReader` is bounds-checked with a sticky failure flag (`Protocol.cpp:45-133`). Caps: `MAX_PAYLOAD_BYTES` = 1 MiB enforced on receive (`Protocol.h:133`, `MessageChannel.cpp:206-210`); `MAX_QUEUED_BYTES` = 8 MiB outbound queue, trip closes the channel (`MessageChannel.h:15`, `MessageChannel.cpp:119-124`); create/resize dimensions capped at 16384x16384 and non-zero, enforced helper-side (`Service/HelperServer.cpp:159-162,187-190`); `AUDIO_BANDS` = 64 fixed (`Protocol.h:34`). Listener: 0700 directory, 0600 socket (umask 0177 + explicit chmod), refuses to steal a live socket (`MessageChannel.cpp:246-315`), backlog 1 (`MessageChannel.cpp:306-307`), and `accept()` authenticates the peer with `SO_PEERCRED` same-uid check (`MessageChannel.cpp:32-49,328`). Default socket path: `$LWE_WEB_SOCKET`, else `$XDG_RUNTIME_DIR/lwe/web-<pid>.sock`, else `/tmp/lwe-<uid>/web-<pid>.sock` (`SpawnConfig.cpp:103-117`). All fds non-blocking with `MSG_NOSIGNAL` (`MessageChannel.cpp:23-29,141`).
- **Where it lives**: New files `src/WallpaperEngine/WebHelper/Protocol.{h,cpp}`, `MessageChannel.{h,cpp}`, `SpawnConfig.{h,cpp}`.
- **Surface**: Socket path env `LWE_WEB_SOCKET` read at `SpawnConfig.cpp:106`. Protocol version kill switch: `--lwe-protocol` vs `PROTOCOL_VERSION` (`SpawnConfig.cpp:94-98`).
- **Coupling**: Self-contained module (only dependency: `Logging/Log.h`); primary consumers are `HelperClient` (engine) and `HelperServer` (service), with its headers also included by `web-service-main`, `WebBrowserContext`, `BrowserApp`, `WebInstance`, and `PropertyClassifier`.
- **Tests**: None of the Catch2 cases exercise the wire format directly; the probe does end-to-end.

### 4. Shared-memory frame transport (double-slot seqlock ring)

- **What it does**: One POSIX shm object per instance per size, named `/lwe-web-<helperPid>-<instanceId>-<generation>` (`src/WallpaperEngine/WebHelper/FrameContract.h:64-68`). Fixed layout: `FrameHeader` (magic `0x4645574C` "LWEF", version 1, geometry, generation, `std::atomic<uint32_t> sequence`) in the first 64 bytes, then 2 BGRA8 slots (`FrameContract.h:11-62`). Writer (`FrameWriter`, service side): `allocate()` creates with `O_EXCL`, 0600, bumps generation even on failure, and only releases the old mapping after the new one succeeds (`FrameContract.cpp:61-114`); `publish()` memcpy's into the back slot then a release-store on sequence, and *drops* paints whose size doesn't match the allocation (FrameContract.cpp:130-158). Reader (`FrameReader`, engine side): two-stage map (probe header at 64 bytes, validate magic/version/geometry, then map full size read-only) - all header fields from the other process are validated, never trusted (`FrameContract.cpp:167-233`); `consume()` is latch -> copy live slot -> re-check, max `MAX_READ_ATTEMPTS` = 4, then abandons and keeps the old texture (`FrameContract.cpp:252-302`). The only per-lifecycle socket traffic is the `FrameReady` event announcing a new generation (`Service/HelperServer.cpp:340-356`, emitted once per generation per `WebInstance::hasUnannouncedGeneration`, `Service/WebInstance.h:52-55`); steady-state frames cross zero socket bytes. On the engine side `CWeb::syncFrameReader` maps `frameShmName(helperPid, id, generation)` from the event (`CWeb.cpp:86-119`).
- **Where it lives**: New files `src/WallpaperEngine/WebHelper/FrameContract.{h,cpp}`. Consumers: `Service/WebInstance` (writer, `WebInstance.h:97`), `CWeb` (reader, `CWeb.h` member `m_frames`), probe.
- **Surface**: None (no env/flags); kill switch is `FRAME_VERSION` mismatch refuse (`FrameContract.cpp:200-204`).
- **Coupling**: Self-contained; liftable alone given `Logging/Log.h`.
- **Tests**: Probe phases [3]-[5] (`tools/web-frame-probe.cpp:448-581`) assert monotonic sequence, retry/abandon counters, non-uniform content, and generation change across resize. No Catch2 case.

### 5. `wp<id>` custom scheme and its containment

- **What it does**: Scheme name is `"wp" + workshopId` via `generateSchemeName` in the new header `src/WallpaperEngine/WebBrowser/CEF/SchemeName.h:5-11` (moved out of `WebBrowserContext.h`, where upstream had the bare `#define WPENGINE_SCHEME`). The complete scheme universe is the enumerated web library - `WallpaperApplication::enumerateWebBackgrounds` scans `$XDG_DATA_HOME/lwe/wallpapers`, `~/.local/share/lwe/wallpapers`, and Steam workshop roots for app 431960, keeping only `type=="web"` projects with a parseable `workshopid` (`WallpaperApplication.cpp:617-693`) - and is baked into the spawn config (`buildWebHelperSpawnConfig`, WallpaperApplication.cpp:695-708). Registered with `CEF_SCHEME_OPTION_STANDARD | SECURE | FETCH_ENABLED` in `SubprocessApp::OnRegisterCustomSchemes` (`SubprocessApp.cpp:34-42`), handler factories registered per-id in `BrowserApp::OnContextInitialized` (`BrowserApp.cpp:71-76`). The helper parses `project.json` lazily on CEF's IO thread on first request, mutex-guarded, and a parse failure returns `nullptr` so CEF serves an error page while the helper survives (`WPSchemeHandlerFactory.cpp:17-41`). The wallpaper URL is `<scheme>://root/<file>` built in `WebInstance::open` (`Service/WebInstance.cpp:54`). A wallpaper not in the scheme universe simply cannot be loaded by that helper (comment at `SpawnConfig.h:18-21`; behavior follows from the registration loop). Chromium hardening switches (`--disable-web-security`, `--disable-site-isolation-trials`, etc.) are byte-identical to upstream's list (`BrowserApp.cpp:78-105` vs upstream `BrowserApp.cpp:20-47`); what changed is child launch now passes only `--lwe-schemes` instead of copying the engine's entire argv (upstream `BrowserApp.cpp:49-53` -> fork `BrowserApp.cpp:107-118`).
- **Where it lives**: New `SchemeName.h`; rewritten `SubprocessApp.{cpp,h}`, `BrowserApp.{cpp,h}`, `WPSchemeHandlerFactory.{cpp,h}` (upstream factory took a `const Project&`; fork takes id+paths+a `MediaSource&` and parses lazily); unchanged `WPSchemeHandler.{cpp,h}`.
- **Surface**: `--lwe-scheme=<id>=<path>` per wallpaper (`SpawnConfig.cpp:65-74`); `--lwe-schemes=` child switch.
- **Coupling**: The lazy-parse factory depends on `Assets::setupAssetLocator`, `Data::Parsers::ProjectParser` and `Media::MediaSource` (`WPSchemeHandlerFactory.cpp:3-5`) - service-side only, fed by the new `NullMediaSource` (`Service/NullMediaSource.{h,cpp}`, an inert `MediaSource` subclass with no-op `update`/`performUpdate`). Moderately self-contained.
- **Tests**: None direct; probe phase [2] (page-loaded within 30 s) covers the load path.

### 6. Load-state tracking, typed property injection, audio spectrum, audio-listener shim

- **What it does**: `BrowserClient` now implements `CefLoadHandler` and records the first main-frame verdict: `OnLoadError` (minus `ERR_ABORTED`) or an `OnLoadEnd` whose URL is a `chrome-error:` document or whose HTTP status is outside [200,400) (status 0 explicitly not a failure) marks the instance failed (`BrowserClient.cpp:34-82`). The server polls these flags once per tick and emits `PageLoaded`/`PageFailed` exactly once (`Service/HelperServer.cpp:307-338`). Engine-side, `CWeb` gates its one-time property injection on `isPageLoaded` (`CWeb.cpp:159-161`), and `HelperClient` marks failed instances so injection is never attempted (`HelperClient.h:129`, `HelperClient.cpp:532-556`). Properties cross the wire *typed* - Boolean/Number/String `PropertyValue` (`Protocol.h:50-63`), classified once from the parsed model by `PropertyClassifier.cpp:11-44` (color -> "r g b" string, text properties dropped entirely as display-only labels), and rendered into a JS literal calling `window.wallpaperPropertyListener.applyUserProperties` by `WebInstance::renderApplyUserProperties` (`Service/WebInstance.cpp:164-206`). Audio: the engine sends 64 raw floats every other frame from `recorder.audio64` (`CWeb.cpp:143-151`; `audio64` at `src/WallpaperEngine/Audio/Drivers/Recorders/PlaybackRecorder.h:14`), and the helper builds a 128-element `Float32Array` (64 bands written twice) and calls `window.__lweAudioCallback` (`Service/WebInstance.cpp:219-244`). The callback exists because `SubprocessApp` injects an idempotent `wallpaperRegisterAudioListener` shim into every main-frame V8 context (`SubprocessApp.cpp:14-32`), re-run as a backstop on each property injection (`WebInstance.cpp:209`).
- **Where it lives**: New `PropertyClassifier.{h,cpp}`; rewritten `BrowserClient.{cpp,h}` (upstream's was 6 lines, no load handling - verified above); modified `SubprocessApp` (shim); `Service/WebInstance.cpp`; engine-side hooks in `CWeb.cpp:121-151` and `notifyPropertyChanged` (`CWeb.cpp:129-141`), called from the property-reload path at `WallpaperApplication.cpp:3060-3064`.
- **Surface**: No new flags; framerate for `windowless_frame_rate` is `max(60, settings.render.maximumFPS)` computed engine-side (`CWeb.cpp:41-43`) and trusted helper-side (`WebInstance.cpp:59-61`).
- **Coupling**: `PropertyClassifier` depends only on the Data model - self-contained. The CWeb/WallpaperApplication hooks are a few call sites.

### 7. Idle teardown, crash supervision, respawn with state replay, crash-loop guard

- **What it does (service side)**: `HelperServer` exits when the last instance is destroyed and an idle grace elapses: default `DEFAULT_IDLE_EXIT_MS = 1000` ms, overridden by `LWE_WEB_IDLE_EXIT_MS` (read once, `Service/HelperServer.cpp:19-39`), timer starts only after an engine has connected at least once (`HelperServer.cpp:41-58`), canceled if a new instance arrives in the window (`HelperServer.cpp:42-48`). It also exits immediately when the engine disconnects (`web-service-main.cpp:117-120`, `HelperServer.cpp:108-113`, clearing all instances).
- **What it does (engine side)**: `HelperClient` runs a state machine `Idle/Starting/Connected/Draining/Backoff/Cooldown` (`HelperClient.h:42`). When the replay map empties while connected it enters `Draining` and the helper is expected to idle-exit; after `DRAIN_TIMEOUT_MS` = 8000 ms the client forces the socket closed and escalates `waitpid` to `SIGKILL` (`HelperClient.cpp:468-476,333-363,181-211`). An unexpected disconnect while wallpapers are on screen SIGKILLs the child, unlinks orphaned `/dev/shm/lwe-web-<pid>-*` objects (`HelperClient.cpp:213-237`), and schedules a respawn with doubling backoff 250->5000 ms (`BACKOFF_BASE_MS`/`BACKOFF_MAX_MS`, `HelperClient.h:67-68`), reset after a 10 s healthy connection (`HEALTHY_CONNECTION_MS`, `HelperClient.cpp:453-456`). Respawn is driven one attempt per `pumpEvents()` so a crashing helper cannot stall the render loop (`HelperClient.cpp:478-486`). On reconnect, `replayState()` re-sends `Create` for every recorded instance and defers properties until the replacement's `PageLoaded`, where the merged inject+set-property state is replayed (`HelperClient.cpp:161-179,501-531`). Crash-loop guard: >=4 deaths in 60 s -> 30 s `Cooldown` with the death ring cleared (defaults `HelperClient.h:47-49`; logic `HelperClient.cpp:365-398`).
- **Where it lives**: `HelperClient.{h,cpp}` (engine), `Service/HelperServer.{h,cpp}` + `web-service-main.cpp` (service).
- **Surface**: `LWE_WEB_IDLE_EXIT_MS` (`HelperServer.cpp:23`); `LWE_WEB_CRASHGUARD=<deaths>,<windowMs>,<cooldownMs>`, all three or ignored, values validated (`HelperClient.cpp:61-98`). Connect patience: 200 attempts x 25 ms on first spawn only (`HelperClient.h:63-64`, `HelperClient.cpp:275-295`).
- **Coupling**: Self-contained within WebHelper + the one `pumpEvents()` call per frame in `WallpaperApplication.cpp:2914-2917`.
- **Tests**: Probe scenarios (a) teardown-to-zero, (b) SIGKILL respawn with property-replay fidelity, (c) crash-loop guard timing, plus orphan/shm hygiene sweeps (`tools/web-frame-probe.cpp:1085-1563`); `WebHelperStartupCost.cpp` covers the never-spawn invariant.

### 8. CEF external-message-pump integration (wake-driven poll loop)

- **What it does**: The service sets `settings.external_message_pump = 1` (`WebBrowserContext.cpp:130`) and drives CEF itself. `BrowserApp::OnScheduleMessagePumpWork` records the next due time (REPLACE semantics) and writes to a non-blocking eventfd (`BrowserApp.cpp:12-40`); the eventfd is created before `CefInitialize` and registered via `setPumpWakeFd` (`web-service-main.cpp:78-84`). The main loop polls {connection fd, or listen fd when unconnected, wake fd} with a timeout clamped to `POLL_MAX_MS` = 50 ms and to the idle-exit deadline, pumping `CefDoMessageLoopWork` when due with an 8 ms idle floor (`PUMP_IDLE_FLOOR_MS`) so a quiet browser doesn't spin (`web-service-main.cpp:36-46,93-161`). The in-code comment records why: a fixed-interval pump starved CEF's windowless BeginFrame timer and froze animations (web-service-main.cpp:94-99). SIGINT/SIGTERM set a stop flag (`web-service-main.cpp:19-21,53-54`).
- **Where it lives**: `web-service-main.cpp`, additions in `BrowserApp.{cpp,h}` (`nextPumpDueMs`, `setPumpWakeFd`), `WebBrowserContext.cpp:130`.
- **Surface**: None.
- **Coupling**: Internal to the new service binary; nothing upstream-shared.

### 9. CEF runtime configuration & diagnostics in `WebBrowserContext`

- **What it does**: Per-run root cache dir under the system temp dir (uuid), removed recursively in the destructor after `CefShutdown` (`WebBrowserContext.cpp:65-66,144-153`). `resources_dir_path`/`locales_dir_path` resolve relative to `/proc/self/exe`'s directory (WebBrowserContext.cpp:68-72). CEF log goes to `$HOME/.local/state/lwe/cef.log` with 3-generation manual rotation at startup (WebBrowserContext.cpp:74-101). `LWE_CEFLOG=verbose|info|error` adjusts severity from the `LOGSEVERITY_WARNING` default (WebBrowserContext.cpp:102-111); `LWE_CEFDEBUG=<port>` enables the devtools port if 0<port<65536 (WebBrowserContext.cpp:112-117). `CefExecuteProcess` is now only a safety net returning -1 in the browser process (WebBrowserContext.cpp:54-61). Windowless rendering on; `no_sandbox` still behind `CEF_NO_SANDBOX` (WebBrowserContext.cpp:129-133). `pumpMessageLoop()` exists (`WebBrowserContext.cpp:156`) but the service's own loop calls `CefDoMessageLoopWork` directly.
- **Where it lives**: Modified upstream file `src/WallpaperEngine/WebBrowser/WebBrowserContext.{cpp,h}`.
- **Surface**: env `LWE_CEFLOG` (WebBrowserContext.cpp:102), `LWE_CEFDEBUG` (:112); log path fixed under `$HOME` (:76).
- **Coupling**: Single file, service-target only; the engine no longer includes it.

### 10. `CWeb` rewritten as an shm/texture client

- **What it does**: Upstream's `CWeb` (123 lines) held `CefBrowser`/`BrowserClient`/`RenderHandler` in-process. The fork's (257 lines) holds an `InstanceId`, a `HelperClient&` and a `FrameReader` (`CWeb.h:44-68`). Per frame it resizes on viewport change, injects properties once after page-loaded, forwards gated mouse moves/clicks, consumes the shm ring into `glTexSubImage2D` (texture realloc centralized in `allocateTexture`, the only `glTexImage2D`, `CWeb.cpp:53-67`), and sends audio every other frame when the project supports it (`CWeb.cpp:153-196`). It tracks the async resize round-trip by re-deriving texture size from the *buffer's* geometry rather than the viewport (`CWeb.cpp:170-182`). Pointer moves go through the new `Input::PointerMoveGate` (`CWeb.cpp:206-212`) - a separate module with its own test (`Testing/Cases/PointerMoveGate.cpp`). Mouse buttons are Left/Right only (`Protocol.h:37-40`).
- **Where it lives**: Rewritten upstream files `src/WallpaperEngine/Render/Wallpapers/CWeb.{cpp,h}`.
- **Surface**: env `LWE_MOUSEDBG` (any set value), read once, enables a 1/s mouse-trail log (`CWeb.cpp:18-21,222-236`).
- **Coupling**: Depends on capabilities 3, 4, 6; the `fromWallpaper` signature change is the only upstream-shared touchpoint.

### 11. `lwe-web-frame-probe` diagnostics/verification tool

- **What it does**: A standalone BUILD_TESTING-only executable (`CMakeLists.txt:785-795`) that acts as a fake engine: spawns a real service via `HelperClient`, then runs one of three proofs - frame transport (default; sequence monotonicity, tear-free reads under a forced writer-lap stall, resize generation change), `--verbs` (per-verb behavioral assertions measured as frame-delta over a quiet baseline with a 0.10 floor: clicks, moves, live set-property typing, audio, resize), or `--lifecycle` (teardown-to-zero, SIGKILL respawn with property replay, crash-loop guard timing, then a census of `/proc` comms and `/dev/shm` for orphans). CLI: `--wallpaper=<dir>` (required), `--assets=`, `--seconds=`, `--width/--height/--resize-width/--resize-height/--fps=`, `--verbs|--lifecycle`, `--crashguard=`, `--idle-exit=` (the last two setenv the corresponding env vars, tools/web-frame-probe.cpp:1571-1620, 1604-1610).
- **Where it lives**: New file `tools/web-frame-probe.cpp` (1695 lines).
- **Coupling**: None - links only `linux-wallpaperengine-lib`.

---

### Five-line summary

The fork removes CEF from the engine entirely: upstream's in-process `WebBrowserContext` is replaced by a three-process design where the engine (which no longer links libcef) posix_spawns a new `lwe-web-service` binary on first web wallpaper, and that service owns CEF while a tiny `lwe-web-helper` binary hosts CEF's child processes. Frames travel over a versioned, double-slot seqlock ring in per-instance POSIX shm objects (`/dev/shm/lwe-web-<pid>-<id>-<gen>`) that the engine maps read-only, with control (create/resize/mouse/typed properties/audio/destroy, plus loaded/failed/frame-ready events) over a same-uid-authenticated, permission-pinned Unix socket with 1 MiB payload and 8 MiB queue caps. The `wp<workshopId>` scheme universe is pre-enumerated from the wallpaper library and baked into the hardened spawn config (close-from-3, restored signal mask, protocol-version handshake). Lifecycle is fully supervised: the service idle-exits 1 s after the last instance (`LWE_WEB_IDLE_EXIT_MS`), the engine detects deaths, unlinks orphan shm, replays instance+property state into a respawned helper, and a crash-loop guard (`LWE_WEB_CRASHGUARD`) imposes cooldowns. Coverage is one Catch2 case proving zero-cost startup for non-web sessions (`WebHelperStartupCost.cpp`) plus a 1700-line integration probe (`tools/web-frame-probe.cpp`) that measures frame transport, per-verb behavior, respawn fidelity and orphan hygiene; upstream coupling is concentrated in the `CWallpaper::fromWallpaper` signature, `WallpaperApplication` wiring, and a rewritten `CWeb`.

---

# 3. Render core, VRAM and the texture pipeline


### FBO pooling model (pooled ping-pong composite pairs)
- **What it does**: Layer composite FBOs (`_rt_imageLayerComposite_<id>_a/_b`) are leased from a per-scene pool keyed by `WxH_flags_format` instead of being dedicated per layer (`CScene::leaseCompositePair`, CScene.cpp:1502-1537). FBOs whose names are referenced cross-object (found by `collectSharedComposites` scanning every image's effect targets/sources/binds/overrides, CScene.cpp:1450-1500) stay dedicated, as does the bloom composite id -1 (CScene.cpp:340-341). Same-size layers share one a/b pair; `reportPoolHighWater` logs reuse stats (CScene.cpp:1539-1560). Invisible layers still render when their composite is shared (`isCompositeShared` gate, CImage.cpp:1017).
- **Where it lives**: modified `Render/Wallpapers/CScene.{h,cpp}` (CScene.h:176-178 pool members, CScene.cpp:1502 lease) with the sole consumer at `Render/Objects/CImage.cpp:289-299`.
- **Surface**: `LWE_FBOPOOL=0` disables pooling (exact legacy per-layer allocation; CScene.cpp:1505-1507; default ON). `LWE_POOL_HWM=1` enables high-water logging (CScene.cpp:1541).
- **Coupling**: Small surface but semantics-touching: three new CScene methods + members, one CImage call site, plus the shared-composite name scan. Pooled FBOs also interact with the composition-subtree render path below. A porter takes CScene pool members/methods + the CImage lease block; the pool is safe to drop entirely (fallback branch is the legacy path).
- **Tests**: none.

### Coverage-sized composite FBOs + output-derived supersample clamp (canvas/view split)
- **What it does**: Layer composite FBOs are sized to the layer's on-screen coverage (`max(authored size, min(|size*scale|, scene dims))`) instead of the authored size (CImage.cpp:273-285), so scaled-up layers don't upscale from a tiny FBO. All scene FBO sizes then pass through `CScene::clampToCap` (CScene.cpp:1427-1448), which aspect-preservingly clamps to `largestOutputSize() * LWE_SSFACTOR` (max single-output dimension, not the multi-output span; CScene.cpp:1411-1425), with fill/zoom modes clamped to *cover* the cap and fit/stretch to *fit inside* it. Separately, the fork decouples canvas dims (`m_canvasWidth/Height`, CScene.h:146-147) from camera view dims: camera zoom no longer resizes the scene FBO (comment CScene.cpp:95-98; `getWidth/getHeight` now return canvas, CScene.cpp:1562-1564).
- **Where it lives**: modified `Render/Wallpapers/CScene.{h,cpp}`, `Render/Objects/CImage.cpp` (coverage block 273-299, effect-FBO clamp at :703), `Render/CWallpaper.{h,cpp}` (`clampToCap` virtual default identity at CWallpaper.h:142; scene FBO clamped in `setupFramebuffers`, CWallpaper.cpp:388-397).
- **Surface**: `LWE_SSFACTOR` float, default 1.0, `0` disables the clamp (CScene.cpp:27-33, 1428). `LWE_NOFBOCOVERAGE` disables coverage sizing (default ON; CImage.cpp:273). `LWE_FBOCOVERAGE` logs coverage upsizes (CImage.cpp:274).
- **Coupling**: Woven through the render frame: every FBO-creation site (scene FBO, layer composites, effect pass targets via CImage.cpp:703, composition FBOs) routes through `clampToCap`, and the scaling mode read from `WallpaperState` must agree with the present-pass UV logic. A porter needs CScene's `clampToCap`/`largestOutputSize`/`m_canvas*`, the CWallpaper virtual + setupFramebuffers change, and the CImage coverage block.
- **Tests**: none.

### Composition-layer subtree rendering
- **What it does**: Image layers flagged as composition layers with authored children get a scene-sized `_rt_compositionLayer_<id>` FBO that aliases `_rt_FullFrameBuffer` for their subtree (CImage.cpp:168-178). `CScene::renderFrame` walks each object's parent chain (`compositionAncestor`), renders the whole subtree into the composite FBO (with optional `glBlitFramebuffer` background copy for `copiesCompositionBackground`), recurses into nested composition layers, then renders the composite itself (CScene.cpp:1153-1226, lambda `renderCompositionImpl` at :1157). `m_compositionRenderTarget` + `getActiveRenderTarget/resolveRenderTarget` (CScene.cpp:454-465) redirect "the scene FBO" references while inside a subtree. `createObject` gained cycle protection (`m_objectsBeingResolved` + RAII guard, CScene.cpp:478-491) since dependency/parent cycles now terminate with an error instead of infinite recursion.
- **Where it lives**: modified `Render/Wallpapers/CScene.{h,cpp}` and `Render/Objects/CImage.{h,cpp}` (the CImage side is the other half of the change).
- **Surface**: no dedicated env knobs; honors the existing `render.debug.objectFilter`/`skipObjects` settings (gate `enabledByDebug`, CScene.cpp:1128-1133; checks :1231-1237) and `LWE_SKIPGATE=0` to bypass the constructor-time skip gate (CScene.cpp:148-151).
- **Coupling**: Deeply woven into `CScene::renderFrame` and `CImage` construction. Cherry-picking means taking the renderFrame restructure plus CImage's composition-FBO/alias logic together.
- **Tests**: none.
- **Uncertain**: exact semantics of `copiesCompositionBackground()` and `isCompositionLayer()` are defined in CImage; only the call sites are checked here, not the predicate bodies.

### HDR bloom ladder
- **What it does**: When `camera.bloom.enabled && camera.bloom.hdr` (CScene.cpp:116), the scene FBO switches to `TextureFormat_RGBA16161616f` (m_sceneFormat, CScene.cpp:117-119; CWallpaper.h:189, consumed at CWallpaper.cpp:397) and CFBO allocates it as `GL_RGBA16F` (CFBO.cpp:41-45). The fork generates a mip-ladder bloom effect at load: N RGBA16F half-resolution targets `_rt_hdrBloom_1..N` (N = `bloomhdriterations`, clamp 1..12), a prefilter pass with soft-knee blend params + strength normalization `authored/(1+scatter^(N-2))`, down passes, additive up passes with scatter, and a combine pass blending `_rt_imageLayerComposite_-1_a` with level 1 back into `_rt_FullFrameBuffer` (CScene.cpp:230-338). Ladder sizes derive from the largest output, not the canvas (CScene.cpp:256-260). The effect JSON is injected into the project VFS at runtime (CScene.cpp:312-316); the materials/shaders (`materials/wpelinux/hdr_*.json`, `shaders/wpelinux_hdr_downsample.*`, `wpelinux_combine_hdr.*`) are injected earlier by `Assets/AssetLocator.cpp:190-245`, which string-patches `g_RenderVar0`/`g_TexelSize` declarations into material-annotated uniforms. Layer composites also become float under HDR bloom so pre-clamp >1 values survive (`isHdrBloom`, CScene.h:58-60; CImage.cpp:288). Non-HDR bloom additionally gains `bloomtint` (CScene.cpp:190-192, 214-224).
- **Where it lives**: modified `Render/Wallpapers/CScene.cpp` (ctor, lines 116-352), `Render/CFBO.cpp` (RGBA16F allocation), `Render/CWallpaper.{h,cpp}` (m_sceneFormat), `Assets/AssetLocator.cpp` (shader/material VFS injection). Data fields in `Data/Model/Wallpaper.h:77-85`, parsed at `Data/Parsers/WallpaperParser.cpp:72-78`.
- **Surface**: wallpaper-authored properties: `hdr`, `bloomhdriterations` (default 8), `bloomhdrscatter` (1.619), `bloomhdrfeather` (0.1), `bloomhdrstrength` (2.0), `bloomhdrthreshold` (1.0), `bloomtint` (WallpaperParser.cpp:72-78). `LWE_NOBLOOM` env kills all bloom including the HDR ladder (CScene.cpp:339).
- **Coupling**: Touches CFBO format handling, CWallpaper FBO creation, CScene ctor, CImage composite format, AssetLocator VFS injection, and the Wallpaper data model. A porter needs all six; the ladder itself is confined to the CScene ctor block and could be excised as a unit if the data-model fields come along.
- **Tests**: none.

### BC7/BC4/BC5 texture ingest (offline texcache)
- **What it does**: A standalone encoder shim `tools/texcomp/lwe_bc7enc.cpp` wraps vendored ISPCTextureCompressor: stdin `[u32 w][u32 h][u32 fmt(7=BC7,4=BC4,5=BC5)][RGBA8 pixels]`, stdout raw compressed blocks, edge-replicated padding to 4x4 multiples, BC7 at max quality (`GetProfile_alpha_slow`, lwe_bc7enc.cpp:22-66). Built only on x86-64 when `ispc` is found (CMakeLists.txt:813-841). At texture load, `uploadFromTexcache` (CTexture.cpp:203-311) looks up `$HOME/.local/state/lwe/texcache/<sha256-of-decoded-mip0>`: the `.meta` JSON contract is `{"gl": "BC7"|"BC4"|"BC5", "mips": [[w,h,bytes],...]}` and the `.bc` blob is the concatenated mip levels in raster order; eligible formats are ARGB8888->BC7, R8->BC4, RG88->BC5, single-image non-animated textures only (CTexture.cpp:213-231). On hit it uploads via `glCompressedTexImage2D` with mip-residency rebasing applied (startLevel logic, CTexture.cpp:262-303); on any mismatch it silently falls back to the normal path.
- **Where it lives**: new file `tools/texcomp/lwe_bc7enc.cpp`; new function in modified `Render/CTexture.cpp`; CMake wiring CMakeLists.txt:813-841 (the only `src/External/` entanglement: ISPC kernel compiled from `src/External/ISPCTextureCompressor/ispc_texcomp`).
- **Surface**: `LWE_TEXCOMP=0` disables the cache lookup (CTexture.cpp:205-207); `LWE_SRGBALL` also forces fallback (CTexture.cpp:209). Cache location is hardcoded `$HOME/.local/state/lwe/texcache` (CTexture.cpp:247).
- **Coupling**: Self-contained on the engine side (one function + one call site at CTexture.cpp:374) but depends on the fork's LZ4-retained mip model (`materializeMip`, CTexture.cpp:53) to hash decoded pixels. A porter could lift it with a plain RGBA readback in place of `materializeMip`.
- **Tests**: none.
- **Uncertain**: no producer of the `.bc`/`.meta` files exists in this tree - the CMake comment calls it "the wizard's texture-compression shim", so the ingest pipeline that invokes `lwe_bc7enc` and writes the texcache is external and unverifiable here.

### Mip-residency live-cap and demand expansion
- **What it does**: On by default, and disabled by `LWE_TEXDETAIL=full` (MipResidency.cpp:53-56), the fork caps texture uploads so the largest resident mip dimension ~ the largest live output dimension. At scene construction `buildReferenceMap` walks the data-model scene and records a per-texture verdict: cappable only if every consuming pass uses one of six allowlisted shaders (genericimage*, generic4, chroma4) and the object has no animation layers and isn't perspective (MipResidency.cpp:20-22, 87-153); verdicts are process-global and monotonically demote because the TextureCache outlives scenes (comment :24-25). At resolve time `TextureCache` computes `capDim = capDimension(largestOutputDimension(context))` - queried live from current viewports, hotplug-fresh, returning 0 (full chain) outside desktop-background mode - and passes it to the new `CTexture` ctor arg (TextureCache.cpp:82-90). CTexture then skips stored levels above the cap and uploads the remainder rebased to GL level 0 with tightened `GL_TEXTURE_MAX_LEVEL`, on both the raw path (CTexture.cpp:377-519) and the BC7 texcache path (CTexture.cpp:262-303). Per frame, `CImage::render` calls `MipResidency::maybeExpand` with the object's on-canvas quad size (CImage.cpp:1026-1037); a quad exceeding 1.02xcap triggers `expandCappedTexture` -> `CTexture::expandResidency`, which deletes the capped GL object and re-uploads the full chain from retained RAM (MipResidency.cpp:164-174; CTexture.cpp:541-574).
- **Where it lives**: new module `Render/MipResidency.{h,cpp}`; wiring in modified `Render/TextureCache.cpp` (:82-90), `Render/CTexture.{h,cpp}` (ctor capDimension, startLevel logic), call site in `Render/Objects/CImage.cpp:1032`, map build in `Render/Wallpapers/CScene.cpp:52`.
- **Surface**: on unless `LWE_TEXDETAIL=full`, which makes it fully inert (MipResidency.cpp:53-56). `LWE_TEXCAP=<int>=256>` is a test-only cap override (MipResidency.cpp:60-68). `LWE_MIPRESIDENCY_DEBUG` logs verdicts (MipResidency.cpp:32). Default cap fallback is 4096 when no live output size is available (MipResidency.cpp:72).
- **Coupling**: The module itself is small and self-contained, but it threads a new ctor parameter through TextureCache->CTexture and relies on retained mip payloads (LZ4) for expansion. The per-frame hook is one call in CImage. Lifting it requires: MipResidency.*, the TextureCache 4-line block, CTexture's startLevel/rebase logic in both upload paths, and the CImage hook.
- **Tests**: none.
- **Uncertain**: expansion deletes and recreates the GL texture object, so holders must re-query ids per bind (the contract at CTexture.h:32-34); holders of `GLuint` texture ids outside `Render/` have not all been audited against that swap.

### LZ4-backed RAM slimming of retained pixels
- **What it does**: `materializeMip` (CTexture.cpp:53-73) treats `Mipmap::compressedData` (LZ4 block retained by the parser; upstream already kept it for standard .tex, the fork adds the raw-GL path, `Data/Parsers/TextureParser.cpp:86-155`) as the canonical backing store, decoding on demand. After upload, `slimRetainedPixels` (CTexture.cpp:76-98) frees the uncompressed copy wherever both exist - skipping video-backed textures since the player streams from the retained buffer - so idle RAM holds only the LZ4 blocks, which are what `expandResidency` re-uploads from.
- **Where it lives**: modified `Render/CTexture.cpp` (:46-98, slim call at :538); the producer half lives in `Data/Parsers/TextureParser.cpp` (covered separately).
- **Surface**: none (no knobs); logs `LWE-RAMSLIM`.
- **Coupling**: Depends on the retained LZ4 backing (kept by upstream for standard .tex, extended by the fork's parser for raw-GL); without `compressedData` populated it is a no-op.
- **Tests**: none.

### Texture cache eviction
- **What it does**: `TextureCache::evictUnused()` (TextureCache.cpp:111-128) erases every cache entry whose `shared_ptr` use_count is 1 (i.e. the cache is the sole owner), returning the count. Called at two natural quiet points via `RenderContext::evictUnusedTextures` (RenderContext.cpp:70): `rebuildForCurrentBackgrounds` after clearing wallpapers (WallpaperApplication.cpp:1887-1891) and `apiReleaseOutputs` during output release (WallpaperApplication.cpp:2686-2690). `RenderContext::clearWallpapers` (RenderContext.cpp:68) supports the same teardown.
- **Where it lives**: modified `Render/TextureCache.{h,cpp}` and `Render/RenderContext.{h,cpp}`; call sites in WallpaperApplication.
- **Surface**: `LWE_TEXCACHEDUMP` logs survivors with refcount (TextureCache.cpp:112) plus wallpaper ctor/dtor life logs (CWallpaper.cpp:40, 48).
- **Coupling**: Trivially liftable - one method plus three call sites.
- **Tests**: none.

### Camera / projection corrections
- **What it does**: (a) New `setPerspectiveProjection` (Camera.cpp:72-88) for scenes without an ortho projection block (`isPerspective` = null projection JSON, WallpaperParser.cpp:30): Y-flipped `glm::perspective` with clamped near/far, authored eye/center/up as the runtime lookAt, and a separate ortho `m_screenProjection` for screen-space layout, exposed via `getScreenProjection` (Camera.cpp:33) and consumed by CImage/CText (CImage.cpp:1343-1347, CText.cpp:573). (b) `setOrthogonalProjection` rewritten (Camera.cpp:50-70): honors `general.zoom` (view extent = canvas/zoom, zoom<=0 clamped to 1), symmetric +/-halfRange z-range (max(farz,1000)), identity lookAt - dropping upstream's `glm::translate(projection, eye)`. (c) `setScriptedView` (Camera.cpp:90-111) lets scripts override eye/center with NaN/degenerate guards, feeding getEye/getCenter (Camera.cpp:21-27); called from `Scripting/SceneObject.cpp:84`. (d) `getOverrideFov` exposes `perspectiveoverridefov` (Camera.cpp:47; used by CImage.cpp:1352-1353 and CModel.cpp:467-468).
- **Where it lives**: modified `Render/Camera.{h,cpp}`; selection logic in `Render/Wallpapers/CScene.cpp:102-110`.
- **Surface**: wallpaper data keys `general.zoom` / `perspectiveoverridefov` / null `general.orthogonalprojection`; `LWE_CAMPROBE` env logs scripted views (Camera.cpp:91).
- **Coupling**: Camera.cpp is small and liftable as a unit, but consumers (CImage/CText/CModel MVP construction, CScene ctor) must come along; ortho behavior change (zoom, no eye-translate) alters every 2D scene's framing, so it's not drop-in for upstream without the canvas/view split.
- **Tests**: none.

### OverlayLabel debug overlay
- **What it does**: When `LWE_OVERLAY_TEXT` is set, every present pass draws a two-pass (black shadow + white) text label at the top-left of each viewport (OverlayLabel.cpp:209-250). Text is rasterized once with FreeType from a hardcoded DejaVu/Liberation font path list (OverlayLabel.cpp:14-19, 80-143) into an R8 texture; init failure is latched so a broken setup never retries per frame (OverlayLabel.cpp:146-205). GL state (program/VAO/texture/blend) is saved and restored around the draw.
- **Where it lives**: new module `Render/OverlayLabel.{h,cpp}`; single call site in `Render/CWallpaper.cpp:375` (end of `CWallpaper::render`).
- **Surface**: `LWE_OVERLAY_TEXT` env var, unset/empty = off (OverlayLabel.cpp:210-213).
- **Coupling**: Fully self-contained; porter takes the two files plus one call line. Adds a FreeType link dependency - check upstream build has freetype (the fork links it; verify upstream CMake before lifting).
- **Tests**: none.

### AlbumTexture readback fix
- **What it does**: `AlbumTexture::copyContents` switches the GPU->CPU readback from `glGetnTexImage` (GL 4.5) to `glGetTexImage` (GL 1.0) (AlbumTexture.cpp:69-88). The in-code comment states the reason: the engine targets a 3.3 core context and the GLFW/--window driver doesn't set `glewExperimental`, so the 4.5 function pointer is NULL -> SIGSEGV on every MPRIS album-art update.
- **Where it lives**: modified `Render/AlbumTexture.cpp` only (AlbumTexture.h untouched).
- **Surface**: none.
- **Coupling**: One-line lift; trivially cherry-pickable.
- **Tests**: none.

### Driver-level changes (GLFW + factories)
- **What it does**: `GLFWOpenGLDriver`: MSAA samples 4->0 (GLFWOpenGLDriver.cpp:30); frame-time cap recomputed every pass from `settings.render.maximumFPS` so live `set-fps` takes effect (GLFWOpenGLDriver.cpp:112-113, vs upstream's one-time static); window title overridable via `LWE_WINTITLE` (GLFWOpenGLDriver.cpp:181). `VideoDriver.h` gains `releaseOutputSurfaces()`/`acquireOutputSurfaces()` virtuals defaulting false (VideoDriver.h:57-59), implemented only by `WaylandOpenGLDriver.cpp:366/384` and consumed by `apiReleaseOutputs` (WallpaperApplication.cpp:2691). `VideoFactories.cpp:86-87`: in `daemonMode` the fullscreen detector factory is used even when `pauseOnFullscreen` is off.
- **Where it lives**: modified upstream files listed above; the Wayland implementation is covered elsewhere.
- **Surface**: `LWE_WINTITLE` env (GLFWOpenGLDriver.cpp:181); existing `settings.render.maximumFPS`, `settings.render.pauseOnFullscreen`, `settings.general.daemonMode`.
- **Coupling**: Each hunk is independent and small; the surface-release pair needs the Wayland implementation plus the apiReleaseOutputs caller to mean anything.
- **Tests**: none.

### Present-pass color correction + sRGB experiments
- **What it does**: The wallpaper present shader (inline in `CWallpaper::setupShader`) gains `g_CC` (brightness/contrast/saturation/hue-rotation) and `g_SrgbOut` (gamma 1/2.2) uniforms (CWallpaper.cpp:123-141), fed per present from `app.getColorCorrection()` (CWallpaper.cpp:344-348). Companion sRGB internal-format experiments: `LWE_SRGBALBEDO` maps DXT5 to sRGB and forces sRGB output; `LWE_SRGBALL` maps all albedo formats to sRGB (CTexture.cpp:615-631, CWallpaper.cpp:346-347). Also in CWallpaper: mirror-group decode ownership (`setMirrorOwner` gates `renderFrame` to the named screen, CWallpaper.cpp:232-235, 219-221) and a hardcoded `LWE-PRESENT` diagnostic log for the first 3 frames (CWallpaper.cpp:304-310, now env-gated behind `LWE_PRESENTTRACE` and emitted at out-level).
- **Where it lives**: modified `Render/CWallpaper.{h,cpp}`, `Render/CTexture.cpp`.
- **Surface**: `LWE_SRGBALBEDO`, `LWE_SRGBALL` envs; color-correction values come from application settings (getColorCorrection - the settings/API side is covered elsewhere).
- **Coupling**: Shader change is confined to CWallpaper; sRGB format mapping touches `setupInternalFormat` only. Both easily liftable.
- **Tests**: none.

### Render debug instrumentation (consolidated)
Env-gated diagnostics added across the render frame, all default-off read via `getenv`: `LWE_FBOALLOC` (FBOProvider.cpp:35), `LWE_FBOTRACE` (FBOProvider.cpp:78), `LWE_AUDIT` (TextureCache.cpp:92; GPU readback stats CTexture.cpp:478), `LWE_MASKAUDIT` (CTexture.cpp:430), `LWE_BASELEVEL_PROBE` (CTexture.cpp:330; runs a one-time driver behavior probe, baseLevelProbe CTexture.cpp:101), `LWE_CLEARPROBE` (CScene.cpp:137, 1077), `LWE_LEDGER` (CScene.cpp:1105), `LWE_OBJPROBE` + `LWE_OBJPROBE_FLOAT` (CScene.cpp:1252-1261), `LWE_FBDUMP` + `LWE_FBDUMP_FRAME` (CScene.cpp:1301-1304, writes .ppm), `LWE_FBPROFILE` (CScene.cpp:1334; present variant CWallpaper.cpp:354), `LWE_MOUSE_POS` synthetic pointer pin (CScene.cpp:1364), `LWE_NOPARTICLES` (CScene.cpp:933), `LWE_KILLLIGHT` (CScene.cpp:822), `LWE_LIGHTDUMP` (CScene.cpp:416, 902), `LWE_CROPOFF` (CImage.cpp:220), `LWE_IMGDUMP` (CImage.cpp:258). One unconditional diagnostic at `sLog.error` level: `LWE-DEBUG` projection dump in the CScene ctor (CScene.cpp:109-114) and `LWE-SCENEFB` for the first 3 frames (CScene.cpp:1054-1067) - these log unconditionally, with no env gate. No capability here to lift; a porter would drop most of these.

**Tests (whole area)**: `src/WallpaperEngine/Testing/Cases/` contains BinaryParserHardening, CommandDispatcher, CommandSocket, Example (a `REQUIRE(true)` stub), InstrumentRegistry, MouseCoordinates, PointerMoveGate, PropertyParser, WebHelperStartupCost - none reference `Render/`. Nothing in this area has test coverage.

## 5-line summary

The fork turns the render core into a VRAM-conscious pipeline built on retained (LZ4-slimmed) pixel backing: after upload the uncompressed copy is freed and the LZ4 block is the canonical store, serving mip-residency expansion and the texcache hash. Scene rendering is resized end-to-end - canvas/view split, coverage-sized layer composites clamped to output-sizexLWE_SSFACTOR, and a ping-pong composite FBO pool - while an HDR bloom ladder (RGBA16F targets, generated prefilter/down/up/combine passes) replaces the fixed 3-pass bloom when `bloom.hdr` is set. Textures get a capped-mip residency system (on by default, LWE_TEXDETAIL=full opts out) with per-frame demand expansion, plus an offline BC7/BC4/BC5 texcache ingested by a new ispc shim; the CPU texture cache evicts sole-owner entries at rebuild/release points. Camera handling adds true perspective projection, scripted view overrides, zoom-corrected ortho, and a separate screen projection. Everything is env-flag-gated with default-on kill switches (LWE_FBOPOOL=0, LWE_NOFBOCOVERAGE, LWE_SSFACTOR=0, LWE_TEXCOMP=0, LWE_NOBLOOM), but none of it has test coverage, and two `sLog.error` diagnostics in CScene are always-on with no env gate.

---

# 4. Render objects: lighting, 3D, particles, text, shaders

- **What it does**: A new renderable object type for WE scene `model` objects. It parses the binary MDLV container directly (`CModel::loadMesh`, `src/WallpaperEngine/Render/Objects/CModel.cpp:67`): magic check at :72, version digits at :83-93, submesh records with 48-byte vertices (pos3+normal3+tangent4+uv2, u16 indices), with two alternate layouts - skinned tag `0x0180000f` -> stride 80/uv 72 (:145-147) and tag 0 with MDLV version <16 -> stride 52/uv 44 (:148-150). Submesh 0 uses the object material, submeshes 1..N use `extraMaterials` (:190); submeshes are optionally sorted translucent-last when `scene.transparentSorting` is on (:217-230). Each submesh gets its own `Effects::CPass` with the material's first pass, forced `LIGHTING=1` combo if absent (:241-243), mesh attribs `a_Position@0, a_Normal@12, a_Tangent4@24, a_TexCoord@uvOffset` (:280-314), and a geometry callback that binds the submesh VAO, sets `glFrontFace(GL_CW)` and draws `GL_UNSIGNED_SHORT` triangles (:319-349). Handles both perspective scenes (camera lookAt/projection, `CModel::updateMatrices` :419-445) and ortho scenes with CParticle-style Y-flip conventions (:448-487), including per-object `perspective` models using scene fov + `perspectiveoverridefov` (:463-478). A bezier-keyframed `anglesAnimation` (`PropertyAnimation`, 3 channels at 30 fps) is evaluated in `effectiveAngles` (:400-412, `bezierEase`/`evalChannel` :352-395).
- **Where it lives**: fork-only `src/WallpaperEngine/Render/Objects/CModel.cpp` / `CModel.h` (class `CModel : CRenderable, ScriptableObject`, `CModel.h:17`). Instantiated in the fork-modified scene factory `CScene::dispatchObjectType`, `src/WallpaperEngine/Render/Wallpapers/CScene.cpp:924-925`. Data model `ModelObject`/`ModelObjectData` is fork-added in `src/WallpaperEngine/Data/Model/Object.h:658-679`.
- **Surface**: env vars - `LWE_TINTFIX=1|2` (TINTMASKALPHA combo off / force color constant, `CModel.cpp:244-251`), `LWE_SPECFIX` (force roughness 1.0, :252-254), `LWE_LIGHTDUMP` (log LIGHTING define presence, :259-266), `LWE_FRONTFACE=ccw` (:323-327), `LWE_AUDIT` (:328), `LWE_CAMPROBE` (:433). Script properties registered: origin/scale/angles/visible/alpha/color (:21-26). No CLI flag; object renders only when a scene contains a `model` object.
- **Coupling**: new files + one factory branch in CScene + new `ModelObject` data struct (covered elsewhere). Reuses upstream `CPass`, `FBOProvider`, `Material` untouched. A porter needs: `CModel.*`, the `ModelObject` data model + parser entry, the CScene factory branch, and (for lit models) the LightingV1 stack below. Self-contained otherwise.
- **Tests**: none under `src/WallpaperEngine/Testing/Cases/`.
- **Uncertain**: the MDLV submesh-record layout (bounds skip :136, header `2*sizeof(uint32_t)` skip :95) is reconstructed from observation; correctness of the version<16 52-byte layout can't be verified from code alone.

### Puppet skeletal animation (PuppetModel)
- **What it does**: Fork-only MDL/MDLV puppet parser with skeleton and clip support. `PuppetModel::parse` (`src/WallpaperEngine/Render/Objects/PuppetModel.cpp:342`) locates the skinned mesh block via a structured walk of vertex-attribute bit tags (:383-503) with a stride-80 heuristic scan fallback (:507-533), then parses the `MDLS` skeleton block (`parseSkeleton` :91-135; bind world-inverse per bone, D3D row-major matrices transposed by memcpy, `readFileMatrix` :65-78) and `MDLA` animation clips (`parseAnimations` :137-217; loop/mirror/single modes, per-bone channels of 36-byte pos/rot/scale records). Mesh failure -> `nullopt`; skeleton/animation failure degrades to a static mesh (:574-581). `evaluateSkinning` (:269-318) blends active layers (`rate`, `blend`) over rest pose with loop/mirror/single phase wrapping (:228-242) and linear key sampling; `skinPositions` (:320-340) does CPU 4-bone skinning. In CImage this replaces upstream's static puppet loader: `CImage::loadPuppetMesh` now calls `PuppetModel::parse` (`CImage.cpp:456-522`), binds `ImageAnimationLayer`s to clip ids (:501-509), and `CImage::updatePuppetAnimation` (:557-590) re-skins and re-uploads the position VBO every frame (`uploadPuppetPositions` :528-555, `GL_DYNAMIC_DRAW`).
- **Where it lives**: fork-only `src/WallpaperEngine/Render/Objects/PuppetModel.{h,cpp}`; integration in modified upstream `CImage.cpp:407,456-638` and new members in `CImage.h:121-136` (`m_puppetModel`, `m_puppetLayers`, `m_puppetScreenSpace`, scratch vectors). Note: upstream already had a static puppet mesh loader (MDLV0021/0023, upstream `CImage.cpp:74-102,425+`); the fork replaces that parser entirely.
- **Surface**: none - no env vars or flags; driven by the wallpaper's `puppet` model field + `animationLayers` (animation id, `visible`, `rate`, `blend`, `Object.h:90-94`). `LWE_CROPOFF` is explicitly skipped for puppet models (`CImage.cpp:222`).
- **Coupling**: the parser itself is a self-contained module (only depends on Log + glm). The CImage integration replaces upstream `loadPuppetMesh`/`updatePuppetPositionBuffer` bodies and adds the screen-space puppet path (`m_puppetScreenSpace` set in `setupPasses`, `CImage.cpp:913-916`). A porter could take `PuppetModel.*` plus the CImage puppet block as one unit; the animation-layer data (`ImageAnimationLayer` with rate/blend) already exists upstream? - no: `Object.h:90-94` is in the fork's diff region; verify against the data-layer chapter. UNCERTAIN: whether upstream `ImageAnimationLayer` carried `rate`/`blend`.
- **Tests**: none.
- **Uncertain**: the inter-clip padding back-probing heuristic (:152-169) and per-bone flags semantics (:110, "semantics unknown" in code comment) are heuristic by admission of the code itself.

### Light object (CLight) and the LightingV1 generated shader module
- **What it does**: Two halves. (a) `CLight` (`src/WallpaperEngine/Render/Objects/CLight.cpp`) is a scriptable, non-rendering scene object (`render()` is empty, :25) that registers the light's `origin`, `angles`, `visible`, `color`, `intensity` DynamicValues so scripts can tick them (:18-22); its static members compute native-format light data: spot cone cosines (:27-30), spot shadow view-projection (:32-48), camera-frustum-fitted directional shadow VP with texel snapping (:50-144), six-face point-light atlas VPs (:146-167) and projection info (:169-174), tube end position (:176-178). (b) `ShaderUnit::generateLightingV1` (`src/WallpaperEngine/Render/Shaders/ShaderUnit.cpp:467-571`) replaces upstream's stub (which returned `vec3(0.0)` - upstream `ShaderUnit.cpp:366-377`) with a full GLSL module: uniform arrays for 4 point/spot/dir/tube lights + shadow atlas samplers, `lweShadowFeatureFactor`/`lwePointShadowFactor` sampling helpers, and a `PerformLighting_V1` that loops all four light types calling `ComputePBRLightShadow`/`ComputePBRLightShadowInfinite`. Injection is via the pre-existing `#require LightingV1` mechanism (`resolveRequireModule`, `ShaderUnit.cpp:458-461`; upstream had the same hook at :359). On the CPU side, `CPass::refreshLightStage` (`src/WallpaperEngine/Render/Objects/Effects/CPass.cpp:1376-1497`) repacks `CScene::getLights()` into the `LightStageBlock` (`CPass.h:134-164`) each frame for programs that resolved the `lwe_Lit*`/`g_Lights*` uniforms (`m_usesSceneLights`, `CPass.cpp:1329-1332`), including the classic v2 `g_LightsPosition[4]`/`g_LightsColorPremultiplied[3]` interface with radius^2/divisor premultiply, per-renderable local-frame conversion (`toClassicLightSpace(Local)`, `classicLocalRadianceScale`, `CPass.cpp:1476-1494`) and falloff-exponent renorm (:1479).
- **Where it lives**: fork-only `CLight.{h,cpp}`; modified upstream `ShaderUnit.cpp` (`generateLightingV1`), `CPass.{h,cpp}` (`refreshLightStage`, uniform registration `CPass.cpp:1286-1332`, shadow-atlas bind `CPass.cpp:266-273`, per-frame refresh `CPass.cpp:777-779`), new virtuals on `CRenderable.h:46-56`. Scene side (the coupling target): `CScene::updateLights` `CScene.cpp:811+` builds `m_lightState` from `Light` objects (types `lpoint/lspot/ltube/ldirectional`, :867-897), factory branch :927-931, `SceneLight`/`MAX_LIGHTS=4`/`MAX_SHADOW_FEATURES=16` in `CScene.h:78-118`.
- **Surface**: env `LWE_KILLLIGHT=<id>` drops one light (`CScene.cpp:820-824`), `LWE_LIGHTDUMP` (uniform resolution + model pass logs, `CPass.cpp:1304-1311`, `CModel.cpp:259`), `LWE_CLASSICK` (default 16.0, range 0.01-1000) and `LWE_CLASSICEXP` (default 2.0, range 0.5-6) seed globals `g_LweClassicDivisor`/`g_LweFalloffExp` (`src/WallpaperEngine/Application/WallpaperApplication.cpp:65-66`), also settable at runtime via socket command args `classic_k`/`classic_exp` (:1440-1457) and reported in status (:1705-1706). `LWE_NOSCREEN` disables the `g_Screen` uniform (`CPass.cpp:1271`).
- **Coupling**: deeply woven. Touches upstream `ShaderUnit::generateLightingV1` body, `CPass::setupUniforms`/`render`/`setupRenderTexture`, adds pure-virtual-adjacent virtuals to `CRenderable` (with defaults, so non-breaking), and requires the whole fork CScene light/shadow stage (`SceneLight`, `ShadowStage`, `getLights()`, `getShadowStage()`, `getShadowAtlas()`). The GLSL module additionally calls `ComputePBRLightShadow(Infinite)`, which is defined nowhere in the fork tree - it must come from wallpaper-bundled shader includes, so the module only links in shaders that include WE's lighting headers. A porter must take CLight + CScene light snapshot + CPass staging + the generator together; the classic v2 interface also needs `CImage::toClassicLightSpaceLocal`/`classicLocalRadianceScale` (`CImage.cpp:1231-1259`) and `CPass::setClassicLocalFrame`/`setScreenViewProjectionMatrix` wiring in `CImage::setupPasses` (`CImage.cpp:877-884,910-911`).
- **Tests**: none.
- **Uncertain**: whether the premultiplier formula (`radius^2/g_LweClassicDivisor`, `CPass.cpp:1476`) matches native WE output - it's a calibration dial with env/socket overrides, not derived from a spec in code. Whether `ComputePBRLightShadow` exists in workshop shader include files cannot be verified from this tree.

### Shadow mapping
- **What it does**: A depth-only shadow atlas shared by all lit passes. `CScene::setupShadowStage`/`renderShadowAtlas` (`CScene.cpp:550-772`) lay out a square atlas (grid of feature cells + 2x3 blocks per shadowed point light, :581-663) and render each frame; only `CModel` objects cast shadows, via `CModel::renderShadow` (`CModel.cpp:489-556`) which lazily builds an inline `#version 330` depth-only program (:495-532) and draws all submeshes with the light VP. Consumers: `CPass::setupRenderTexture` binds the atlas depth texture to unit 16 (`CPass.cpp:266-273`) and the generated LightingV1 module samples it as `sampler2DShadow lwe_ShadowAtlas` with per-feature matrices/transforms and per-point six-face blocks (`ShaderUnit.cpp:487-524`). `CLight`'s static VP calculators (above) produce the matrices.
- **Where it lives**: fork-only `CLight.*`; modified `CModel.cpp:489-556`; fork-added CScene shadow stage (`CScene.cpp:128-130,550-772`, called from render at :1099); `CPass.cpp:266-273,1315-1324`; generated GLSL in `ShaderUnit.cpp`.
- **Surface**: uniform `lwe_ShadowAtlas` fixed to texture unit 16 (`CPass.cpp:1315`). No dedicated kill switch; lights without `castShadow` get `enabled=0` (`CScene.cpp:676-681` region, feature assignment :697-700).
- **Coupling**: inseparable from the light stack (previous entry) plus CScene's atlas FBO with `ensureDepthTextureAttachment` (:585). Porter takes it as part of the lighting bundle.
- **Tests**: none.
- **Uncertain**: point-light face order is hard-coded to match `CalculateProjectedCoordsPoint` per comment (`CLight.cpp:151-152`) - that native function is not in this tree, so the claim rests on the comment.

### Particle semantics: playback rate, startTime, prewarm, child systems & events, instance overrides
- **What it does**: `CParticle::render` (`CParticle.cpp:324-378`) now gates simulation on authored `startTime` (:341), applies the `rate` instance override as a playback-rate dilation of dt clamped to <=1 (:337,344-346), keeps a separate system clock `m_sysTime` for operators (:520-523), and fast-forward "prewarms" 60s at 30Hz steps when `startTime>0` on root systems (:349-357). Child particle systems (`Particle.children`, `Object.h:524-536,583`) are built recursively in `setupChildren` (:297-315) with inherited multiplicative overrides `m_inhSize/Alpha/Lifetime/Speed/ColorN` captured in the delegating constructor (:117-126). `emitAsChild` (:620-700ish in diff; function at fork `CParticle.cpp`) implements `static` children (own start-time gate, `m_childStartWall`), `eventspawn`/`eventdeath` one-shot burst instances at parent particle positions (`spawnBurstInstance`), and `eventfollow` attachments that track a parent particle by stable `uid`, move their owned particles (and trail nodes) by anchor delta, copy the parent alpha into `followAlpha`, and respect per-instance budgets (`EmitContext{anchor,tag,budget,burst}`, `CParticle.h:108-119`). Parents publish per-frame spawn/death event lists (`recordSpawnRange`, `m_frameSpawns/m_frameDeaths`, `CParticle.h:382-383`). Both emitters now take `EmitContext`, multiply rate by the `count` override at emission time (box: `CParticle.cpp` diff hunk ~:1009; sphere similar), and stamp `uid`/`ownerTag`. Pool sizing for event children is `maxCount x link.maxCount` capped at 4096 (:174-178). Other semantic changes: emitter-origin de-scaling under `flags&1` (box :927-939 area), `worldSizeDivisor` size normalization, gravity de-scaled by object scale and drag halved (movement operator), turbulence operator rewritten (per-slot random speed, z-axis time), oscillatealpha frequency branch (`freqMin>=1` -> 60Hz-normalized cosine with `max(scaleMin, scaleMax*cos)`), oscillateposition converted from derivative-integration to absolute-offset deltas (`lastOffset`), control-point-attract threshold no longer halved, spritesheet frame clock changed to fractional wrap by default (`LWE_ANIMFRACTION=0` restores legacy), `SPRITESHEETBLEND=1` combo always set with SPRITESHEET (:2359), child parent-control-point mirroring (`updateTransformAndControlPoints`), and a pkg-workshop-specific dual `colorrandom` composition initializer (`createPkgDualColorRandomInitializer`, gated on `project.fromPackage`). Script properties registered for all instance overrides: alpha/size/lifetime/rate/speed/count/color/colorn (:131-139).
- **Where it lives**: all in modified upstream `CParticle.{h,cpp}` - new members `CParticle.h:267-272,318-320,361-383`; child ctor `CParticle.h:142-144`; `EmitContext` `CParticle.h:105-119`. Depends on fork-added data: `ParticleChild`, `ParticleInstanceOverride.colornAuthored`, `Particle.startTime/children` (`Object.h:524-590`).
- **Surface**: env kill switches/probes - `LWE_NOPREWARM` (:350), `LWE_ANIMFRACTION=0` (:544), `LWE_NOFOLLOWALPHA` (:743), `LWE_NOCHILDRIDE` (:2575), `LWE_HIDESTPARENT` (:2674), `LWE_BILLBOARD` (:139), `LWE_NOSPRITEVFLIP` (:2564), `LWE_TRAILMODE=exact` (:2928), `LWE_PARTALLOC` (:32), `LWE_VELPROBE` (:1454), `LWE_SIZEPROBE` (:2711); runtime-toggleable instruments via `InstrumentRegistry`: `LWE_PARTSTATS`, `LWE_TWINKLEPROBE`, `LWE_ROPETRAILPROBE` (`src/WallpaperEngine/Logging/InstrumentRegistry.cpp:28-30`; settable via socket - `WallpaperApplication.cpp:1529`), `LWE_NOPARTICLES` in the scene factory (`CScene.cpp:933-936`) plus settings `general.disableParticles`.
- **Coupling**: contained in CParticle + its data model, but the file is heavily rewritten (~1900 diff lines); `EmitterFunc` signature changed (`CParticle.h:122`) so any out-of-tree emitter code breaks. Not liftable piecemeal - rate/startTime/prewarm could be cherry-picked alone (small), but children/events need the data model + EmitContext + emitters rewrite.
- **Tests**: none directly. `Testing/Cases/CommandDispatcher.cpp:265,299` covers the `set-particles` socket command (enable/disable), not semantics.
- **Uncertain**: `createPkgDualColorRandomInitializer`'s channel formulas are wallpaper-specific curve fitting - no in-code reference to validate against. The `rate` override is applied twice in effect: as dt dilation in render() AND `rate` is no longer folded into emitter rate (`const float rate = emitter.rate;` replacing upstream's `emitter.rate * override.rate`) - the net behavior change vs upstream is deliberate per code, but native parity is unverifiable.

### Trail/rope renderer rework + particle buffer pooling
- **What it does**: `renderRope` (`CParticle.cpp` diff ~:2905+) is rewritten from "one strip over the live array" to multi-strip: each particle keeps a time-stamped `TrailNode` history (`CParticle.h:79-86`, recorded in `simCommon` with `m_trailLength` window and 4096-node cap), and per-particle strips are resampled (uniform segments, or `LWE_TRAILMODE=exact` tau-based times), then Catmull-Rom subdivided as before. Short `ropetrail` (length < 0.35) collapses to billboard sprite rendering under `LWE_BILLBOARD` (:139-144). GL upload switches from per-frame `glBufferData` re-spec to capacity-preallocated buffers with `glBufferSubData` (`uploadParticleBuffer`, :85-93; capacity set at :2415-2420).
- **Where it lives**: `CParticle.cpp`/`CParticle.h` (TrailNode, `m_vboCapacity/m_eboCapacity` :300-301).
- **Surface**: `LWE_TRAILMODE=exact`, `LWE_BILLBOARD`, `LWE_PARTALLOC` (pool/upload logging), `LWE_NOROPEUVFLIP` disables a rope-UV flip patch in the vertex shader source (`ShaderUnit.cpp:113-124`).
- **Coupling**: internal to CParticle; self-contained cherry-pick except that trail nodes interact with eventfollow anchor deltas (particles' trails are translated, `emitAsChild`).
- **Tests**: none.

### Text object support (CText fixes and features)
- **What it does**: CText existed upstream (FreeType single-quad text); the fork: (a) rasterizes at `pointSize * 4/3 * sceneH/1080` capped at 512px instead of scale-compensated sizing (`CText.cpp:206-210`); (b) decodes UTF-8 codepoints instead of bytes (`nextUtf8Codepoint`, :231-262, used at :363+); (c) implements `limitwidth`/`maxwidth` truncation with optional ellipsis (:309-340); (d) applies horizontal/vertical alignment offsets from font metrics (`computeTextAlignmentOffset`, :264-289, applied to quad verts :463-470); (e) walks the parent chain (depth <=8, rotation/scale/origin compose) for placement (:530-546); (f) renders Y-flip-corrected via `cam.getScreenProjection()` (ortho scenes also multiply lookAt) and explicitly binds the scene FBO, disables depth/cull, alpha-mask off (:556-577). Scripted text re-rasterizes whenever the script's output string changes (same as upstream; the code comment's "Phase 2" deferral refers to font loading, not text updates).
- **Where it lives**: modified upstream `src/WallpaperEngine/Render/Objects/CText.{cpp,h}` (new member `m_alignOffset`, `CText.h:83`).
- **Surface**: `LWE_LIGHTDUMP` enables a one-shot text dump (`CText.cpp:549`). No flags; driven by `Text` object properties (alignment, verticalalign, maxwidth, limitwidth, limituseellipsis).
- **Coupling**: self-contained within CText; depends only on `Camera::getScreenProjection`/`isOrthogonal` (covered elsewhere). Easy cherry-pick.
- **Tests**: none.
- **Uncertain**: alignment math uses `lineCount=1` hard-coded (:412) - multi-line vertical alignment is approximate by construction.

### CImage compositing: FBO coverage, shared composites, float composites, composition layers
- **What it does**: (a) FBO coverage: composite FBOs are sized to at least the layer's on-screen footprint (`|size*scale|` clamped to scene, `CImage.cpp:273-286`), then `scene.clampToCap`; default on, `LWE_NOFBOCOVERAGE` disables, `LWE_FBOCOVERAGE` logs. (b) Shared composites: `scene.leaseCompositePair(id, ...)` (:289) hands out pooled ping-pong FBOs, and `render()` keeps rendering invisible images whose composite is shared (`isCompositeShared`, :1016-1019); when rendering into a composition the final pass leaves alpha writable (:1056-1059 vs upstream's unconditional `GL_FALSE`). (c) Float composites: format is `RGBA16161616f` when `scene.isHdrBloom()` else ARGB8888 (:288). (d) Composition layers: `isCompositionLayer()` (`models/util/composelayer.json`, :1429-1431) with authored children allocates a scene-sized `_rt_compositionLayer_<id>` FBO and aliases `_rt_FullFrameBuffer` to it on the object (:168-178); `CImage` also exposes `copiesCompositionBackground()` (:1433) and `cursorLocalPosition()` (rotation-aware hit test, :1461-1496). Also fork-added: magenta-neon composite tint compatibility pass (`findMagentaCompositeTint` + tint material append, :45-77,784-807), shape-layer quad geometry from `point0..3` constants (`m_isShape`, `applyShapeGeometry`, :156-157,1261-1337, additive blending :840-842), `buildScreenViewProjection` with per-object perspective support (:1339-1362), rewritten parallax term (:1377-1391), NPOT texture UV clamping (:317-328), `passthrough` copy-projection handling, and effect-build failure rollback (:772-779). `getColor4()` now returns colorxbrightness by value (:1077-1080).
- **Where it lives**: modified upstream `CImage.{cpp,h}`; the pooling/sharing/compostion state lives in fork CScene (`leaseCompositePair`, `isCompositeShared`, `isRenderingToComposition`, `hasAuthoredChildren`, `isHdrBloom`, `clampToCap` - `CScene.h:35-60`), and `CRenderable::detectTexture` now resolves `_rt_`/`_alias_` names through the per-object FBOProvider chain (`this->find`, `CRenderable.cpp:21`) so the `_rt_FullFrameBuffer` alias works per-subtree.
- **Surface**: `LWE_NOFBOCOVERAGE`, `LWE_FBOCOVERAGE`, `LWE_CROPOFF=1|2` (:221-227), `LWE_IMGDUMP`, `LWE_IMGPROBE`, `LWE_CURSORDBG`, `LWE_PASSPROBE[=<id>|final]` + `LWE_PASSPROBE_DUMP` (`CPass.cpp:786-873`). No config keys.
- **Coupling**: woven into CImage's constructor/setupPasses/render and depends on several new CScene APIs - a porter needs the CScene composite-pool half (covered elsewhere) plus `FBOProvider::alias` (`src/WallpaperEngine/Render/FBOProvider.h:20-21`, fork-changed file). The shape-geometry and magenta-tint blocks are individually excisable; coverage sizing is a ~15-line block.
- **Tests**: none.
- **Uncertain**: `isMagentaNeonTint` thresholds (:45) and the tint compat pass are targeted wallpaper workarounds; "shape" detection keys on a fork-specific material filename `materials/wpenginelinux_shape.json` (:157) whose origin is not verified here.

### Sound-object gain
- **What it does**: Each `AudioStream` created for a sound object now gets the authored object volume clamped to [0,1] (`CSound.cpp:33-35`); upstream set only repeat mode. Kill switch `LWE_NOOBJVOL` forces 1.0.
- **Where it lives**: modified upstream `src/WallpaperEngine/Render/Objects/CSound.cpp:30-35` (header unchanged). Uses upstream `AudioStream::setVolume`.
- **Surface**: env `LWE_NOOBJVOL` (:33); data: `Sound.volume`.
- **Coupling**: 4 lines, trivially cherry-pickable.
- **Tests**: none.

### Texture animation playback control (script-driven pause/play/frame)
- **What it does**: `CRenderable` gains a `TextureAnimationPlayback` state (`CRenderable.h:23-30`) with pause (freeze clock at current g_Time), play (reset to free-run), and setFrame/getFrame/getFrameCount/isPlaying (`CRenderable.cpp:45-75`). `CPass::resolveTextureAnimationState` honors it: frame override picks the frame directly; paused uses the frozen `baseTime` instead of driver render time; animation time is now summed from the texture's own frames with a <=0 guard (`CPass.cpp:314-352`). Exposed to scene scripts through the QuickJS adapter (`ScriptableObjectAdapter.cpp:41-56`, magic 0-5).
- **Where it lives**: modified upstream `CRenderable.{h,cpp}`, `CPass.cpp`; adapter in `src/WallpaperEngine/Scripting/Adapters/ScriptableObjectAdapter.cpp` (covered in the script-engine chapter).
- **Surface**: script functions only; `LWE_ANIMSTATS` instruments frame advance rates (`CPass.cpp:367-401`).
- **Coupling**: small and additive; porter needs CRenderable block + the CPass frame-selection hunk + adapter entry.
- **Tests**: none.

### Shader pipeline compatibility layer (ShaderUnit/Shader/GLSLContext)
- **What it does**: A set of source-rewrites applied while assembling workshop GLSL: `preprocessGlobalConsts` demotes global `const` decls initialized from non-constant expressions into `#define`s wrapped in the declared type constructor, and adds explicit type constructors for function-scope ones (HLSL narrowing -> GLSL legality) (`ShaderUnit.cpp:150-192`); a sign flip in the skylight/ambient mix (`vec3(0,1,0)`->`vec3(0,-1,0)`, :91-99); rope UV V-flip for `genericropeparticle` (:113-124); `g_LWEAxisComp` scale-compensation injection into `genericparticle` vertex shaders (:126-148); `applyTextureResolutionSwizzleCompatibility` adds `.xy` when `g_TextureNResolution` (vec4) is combined with vec2 (:611-625); `paintdefaultcolor` sampler metadata creates 1x1 solid-color default textures (`m_paintDefaultColors` :752-775, consumed in `CPass.cpp:1213-1222` via `SolidColorTexture`, `CPass.cpp:38-81`); component-combo auto-enabling for `METALLIC_MAP`/`ROUGHNESS_MAP`/`REFLECTION_MAP` based on material constants (:856-895); float and numeric-string combo defaults now parse instead of throwing (:680-695); combo/parameter JSON uses `parseLenient` (:655,704); `Shader`/`ShaderUnit` constructors thread a new `materialConstants` map (pass-level constants) through to uniform binding (`Shader.cpp:21-31`, `CPass.cpp:1025-1028`); FOG combos `FOG_DIST/FOG_HEIGHT/FOG_COMPUTED` derived from scene fog (`CPass.cpp:1005-1011`); texture uniforms now registered for units 0-15 (`CPass.cpp:1241-1243`); uniform uploads became count-aware for vec3/vec4/mat4 arrays (`CPass.cpp:614-634`).
- **Where it lives**: modified upstream `ShaderUnit.{cpp,h}`, `Shader.{cpp,h}`, `GLSLContext.cpp`, `CPass.{cpp,h}`.
- **Surface**: `LWE_FORCECOMBO=NAME=value` (global combo override, `ShaderUnit.cpp:936-951`), `LWE_SHADERDUMP` (dump failing fragment source, `GLSLContext.cpp:164-170`), `LWE_SHADERDUMP_MATCH=<substr>` (write assembled units to `~/.local/state/lwe/`, `ShaderUnit.cpp:1011-1024`), `LWE_NOROPEUVFLIP`, `LWE_AUDIT`, `LWE_UNIFDUMP`/`LWE_UNIFVALS` (`CPass.cpp:1572+,505+`), `LWE_NOSCREEN`.
- **Coupling**: `Shader` ctor gained a parameter - single call site (`CPass.cpp:1026`), so contained. The GLSL patches are sequential string rewrites inside `ShaderUnit::preprocess`/`generateFinal`; individually excisable but order-sensitive. `parseLenient` is a data-layer dependency.
- **Tests**: none.
- **Uncertain**: the ambient sign flip and axis-compensation rewrites are calibrated to specific wallpapers; no in-code reference for native correctness.

---

**Area summary (5 lines)**: The fork adds full 3D scene-object support: a new `CModel` renders WE `model` objects by parsing MDLV meshes directly (`CModel.cpp:67`), and `PuppetModel` upgrades upstream's static puppet loader to CPU-skinned skeletal animation with loop/mirror/single clips (`PuppetModel.cpp:342`, `CImage.cpp:557`). Scene lighting is implemented end-to-end: a scriptable `CLight` object, a CScene light/shadow stage, and `ShaderUnit::generateLightingV1` (`ShaderUnit.cpp:467`) replacing upstream's zero-stub with a PBR lighting + shadow-atlas module injected via `#require LightingV1`, fed per-frame by `CPass::refreshLightStage` (`CPass.cpp:1376`). Particles gain playback rate/startTime/prewarm, recursive child systems with eventspawn/eventdeath/eventfollow instance semantics, per-particle trail ribbons, and pooled GL buffers - all inside a heavily rewritten `CParticle`. CImage gets scale-aware FBO coverage, shared/float composite pools, composition-layer FBO aliasing, shape geometry and perspective layers; CText gains UTF-8, alignment, width-limiting and parent-chain placement; CSound honors authored volume (env kill switch `LWE_NOOBJVOL`). Everything is controlled by env vars and runtime-toggleable instruments (`LWE_*`, `InstrumentRegistry`), with essentially no test coverage under `Testing/Cases/` for this area; the lighting stack is the most deeply woven (CScene + CPass + ShaderUnit + CRenderable virtuals), while CText/CSound/texture-animation control are easy cherry-picks.

---

# 5. Data layer, binary parsers, filesystem


### Lenient JSON parsing pipeline (`parseLenient`)
- **What it does**: `JSON::parseLenient` first tries a strict nlohmann parse with `ignore_comments=true`; on `parse_error` it retries after blanking out `//` and `/* */` comments (string/escape aware, newlines preserved) and dropping commas whose next non-whitespace char is `]` or `}` (src/WallpaperEngine/Data/JSON.cpp:117, helpers `stripComments` at JSON.cpp:23 and `stripTrailingCommas` at JSON.cpp:73). Declared at src/WallpaperEngine/Data/JSON.h:199. This lets the fork read WE-authored JSON that carries comments and trailing commas, which upstream's bare `JSON::parse` rejects.
- **Where it lives**: modified upstream files src/WallpaperEngine/Data/JSON.cpp / JSON.h. Call sites swapped from `JSON::parse` to `parseLenient`: src/WallpaperEngine/Data/Parsers/EffectParser.cpp:18, MaterialParser.cpp:17, ModelParser.cpp:14, WallpaperParser.cpp:25, ObjectParser.cpp:520 and :1046 (particle definition files). It is also used at src/WallpaperEngine/Render/Shaders/ShaderUnit.cpp:658,707, src/WallpaperEngine/Application/WallpaperApplication.cpp:227,435,650, src/WallpaperEngine/Application/ApplicationContext.cpp:81, src/WallpaperEngine/WebBrowser/CEF/WPSchemeHandlerFactory.cpp:32.
- **Surface**: no flags/env; always on at those call sites. On the fallback path it logs `JSON strict parse failed - retrying with trailing commas stripped` (JSON.cpp:121).
- **Coupling**: low. `parseLenient` is an additive free function; call-site changes are one-liners. A porter needs JSON.cpp/JSON.h plus the call-site swaps they want. `EffectParser::load` and `MaterialParser::load` additionally throw on empty file content (EffectParser.cpp:14-16, MaterialParser.cpp:14-16) - separate micro-hardening, same hunk.
- **Tests**: none in src/WallpaperEngine/Testing/Cases/.
- **Uncertain**: whether every WE file with comments actually parses after stripping (the stripper is hand-rolled); not verifiable statically.

### JSON type-coercion hardening (numeric strings, exception-safe optionals)
- **What it does**: `JSON::coerceNumericString<T>` (src/WallpaperEngine/Data/JSON.h:63) converts string values like `"0.5"`, `"true"`/`"false"` to arithmetic T via `strtod`, rejecting non-finite parses (`inf`/`nan`) so integral casts avoid UB. `require<T>` now tries coercion before the plain get (JSON.h:95). Both `optional<T>` overloads (JSON.h:112 and :137) lost their `noexcept`, coerce numeric strings, and catch conversion exceptions, logging `Ignoring optional value '<key>' of mismatched type` and returning nullopt/default instead of crashing the whole parse.
- **Where it lives**: modified src/WallpaperEngine/Data/JSON.h only.
- **Surface**: none; automatic for every `require`/`optional` call engine-wide.
- **Coupling**: header-only templates, additive; behavior change is global to all JSON consumers. Self-contained to lift, but it changes semantics everywhere it's included (silent defaulting instead of exceptions).
- **Tests**: none directly.

### BinaryReader fail-fast reads + stream-size bounds
- **What it does**: New private `readExact` (src/WallpaperEngine/Data/Utils/BinaryReader.cpp:14) throws via `sLog.exception` when a short read occurs, instead of upstream's silent partially-uninitialized buffers. New `remaining()` (BinaryReader.cpp:22) reports bytes left (0 for non-seekable streams). `nextSizedString` rejects a declared length larger than `remaining()` before allocating (BinaryReader.cpp:80). `nextNullTerminatedString` was rewritten to read byte-wise via `get()` so a missing terminator stops at EOF instead of spinning on an indeterminate byte (BinaryReader.cpp:60). `nextInt` is now implemented as a cast of `nextUInt32` (BinaryReader.cpp:49).
- **Where it lives**: modified src/WallpaperEngine/Data/Utils/BinaryReader.cpp/.h (new members declared at BinaryReader.h:24-39).
- **Surface**: none.
- **Coupling**: same public API, strictly-additive members; all existing callers get hardening for free. Trivially liftable.
- **Tests**: src/WallpaperEngine/Testing/Cases/BinaryParserHardening.cpp:62-99 (truncated u32 throws, valid u32, unterminated string stops at EOF, oversized sized-string rejected pre-allocation).

### MemoryStream seek bounds
- **What it does**: `seekoff` now computes an absolute target and rejects (returns -1) any seek landing outside `[eback, egptr]` (src/WallpaperEngine/Data/Utils/MemoryStream.h:14-38), where upstream would `gbump`/`setg` the get pointer out of bounds. Adds a `seekpos` override forwarding to `seekoff` (MemoryStream.h:40).
- **Where it lives**: modified src/WallpaperEngine/Data/Utils/MemoryStream.h.
- **Surface**: none.
- **Coupling**: self-contained streambuf override; lift alone.
- **Tests**: BinaryParserHardening.cpp:101-116 (in-range seek succeeds, out-of-range seek sets failbit).

### Package (.pkg) parser hardening
- **What it does**: Two new checks in src/WallpaperEngine/Data/Parsers/PackageParser.cpp: after parsing the file list, every entry's `offset+length` must fit inside the remaining payload bytes (PackageParser.cpp:31-37); and the declared file count is capped at `remaining()/12` (minimum entry size) before `reserve()` (PackageParser.cpp:48-52), so a hostile header can't trigger multi-GB allocations or out-of-bounds reads.
- **Where it lives**: modified src/WallpaperEngine/Data/Parsers/PackageParser.cpp.
- **Surface**: none; throws `sLog.exception` on violation.
- **Coupling**: self-contained; depends on `BinaryReader::remaining()` above.
- **Tests**: BinaryParserHardening.cpp:118-136 (well-formed empty package parses; 0xFFFFFFFF count rejected).

### Texture (.tex) parser hardening + raw-GL TEXB0004 variant + lenient flags
- **What it does**: (a) New `validateMipmapPayloadBounds` (src/WallpaperEngine/Data/Parsers/TextureParser.cpp:44) rejects negative sizes, `uncompressedSize > 1 GiB` (`MAX_MIPMAP_BYTES`, TextureParser.cpp:51), and payloads larger than `file.remaining()`; called for both the raw-GL path (TextureParser.cpp:84) and the classic path (TextureParser.cpp:136). (b) LZ4 decode must now produce exactly `uncompressedSize` bytes, not merely `>= 0` (TextureParser.cpp:150-153). (c) Animated textures with zero frames throw instead of dereferencing `frames.begin()` (TextureParser.cpp:344-348). (d) New format variant: TEXB0004 containers whose `freeImageFormat` is `FIF_UNKNOWN` are treated as raw-GL textures - header carries `rawGLMipLevels` (capped 1..16, TextureParser.cpp:288-295; new field src/WallpaperEngine/Data/Assets/Texture.h:163), the declared per-image mipmap count is overridden by `rawGLMipLevels` (TextureParser.cpp:21-22), and each mip reads optional per-mip width/height/flag plus LZ4-or-raw payload (TextureParser.cpp:68-102). Upstream cannot read these at all (it would force TEXB0003 semantics). (e) `parseTextureFlags` now masks unknown bits and logs once instead of throwing (TextureParser.cpp:384-392) - wallpapers with unknown flag bits (comment cites 0x800000) no longer lose whole objects.
- **Where it lives**: modified src/WallpaperEngine/Data/Parsers/TextureParser.cpp/.h (`parseMipmap` signature gained `imageIndex, mipIndex`, TextureParser.h:25-26), src/WallpaperEngine/Data/Assets/Texture.h:163.
- **Surface**: none.
- **Coupling**: mostly self-contained in the parser; the raw-GL variant only becomes renderable through `CTexture`'s `FIF_UNKNOWN` upload path (src/WallpaperEngine/Render/CTexture.cpp:414,432,505,521,599,619 - Render area), so a data-layer-only port gets parsing but not display. Depends on `BinaryReader::remaining()`.
- **Tests**: BinaryParserHardening.cpp:138-154 (valid TEXV0005/TEXB0001 parses; negative mipmap size rejected; zero-frame TEXS0001 rejected). No test for the raw-GL variant or flag masking.

### Directory adapter path-containment fix
- **What it does**: Replaces the string-prefix check (`finalpath.find(basepath)==0`, which also matches sibling dirs like `1234-evil` for base `123`) with component-aware `within()` using `lexically_relative` and rejecting `..` (src/WallpaperEngine/FileSystem/Adapters/Directory.cpp:14-18), applied in `open`, `exists`, `physicalPath` (Directory.cpp:24,45,68).
- **Where it lives**: modified src/WallpaperEngine/FileSystem/Adapters/Directory.cpp.
- **Surface**: none.
- **Coupling**: self-contained, lift alone.
- **Tests**: none.

### Case-insensitive package adapter index
- **What it does**: `PackageAdapter` now builds an `unordered_map` of lowercased filename -> `FileEntry*` at construction (src/WallpaperEngine/FileSystem/Adapters/Package.cpp:28-33) and both `open` and `exists` look up lowercased paths (Package.cpp:35-59), so scenes authored on Windows resolve assets whose case differs inside the pkg. Also replaces the linear scans.
- **Where it lives**: modified src/WallpaperEngine/FileSystem/Adapters/Package.cpp/.h (new `find` + `m_index`, Package.h:21-33).
- **Surface**: none.
- **Coupling**: self-contained; behavior change confined to the adapter.
- **Tests**: none.

### Virtual adapter stream rewind
- **What it does**: `VirtualAdapter::open` now `clear()`s and `seekg(0)`s the stored shared stream before returning it (src/WallpaperEngine/FileSystem/Adapters/Virtual.cpp:17-18), so re-reading the same virtual file doesn't return a stream stuck at EOF/fail state from a previous reader.
- **Where it lives**: modified src/WallpaperEngine/FileSystem/Adapters/Virtual.cpp.
- **Surface**: none.
- **Coupling**: 3 lines, lift alone. Note shared-stream design means concurrent readers still share one cursor.
- **Tests**: none.

### Centralized asset mounting + VFS-injected virtual assets (`Assets::setupAssetLocator`)
- **What it does**: The mounting logic moved out of `WallpaperApplication` into a new free function `Assets::setupAssetLocator` (src/WallpaperEngine/Assets/AssetLocator.cpp:103-311, declared AssetLocator.h:35-36). Changes vs the old upstream mounting code: it mounts **every** `.pkg` found in the wallpaper directory (AssetLocator.cpp:114-123; upstream mounted only `scene.pkg` and `gifscene.pkg` by name), swallows mount failures per-package, mounts the assets dir, every `assets/effects/*` subdirectory, and the process CWD (AssetLocator.cpp:126-145), then registers a set of synthesized virtual files into the VFS: a classic camera bloom effect (`effects/wpenginelinux/bloomeffect.json`, AssetLocator.cpp:158-184 - carried over from upstream, as are the assets-dir/CWD mounts and the wpenginelinux/copy virtual files below), an HDR bloom material ladder built by string-patching `g_RenderVar0`/`g_TexelSize` uniform annotations into copies of `hdr_downsample`/`combine_hdr` shaders (AssetLocator.cpp:186-247, graceful log-and-skip if the shaders or declarations are missing), pass-through `models/wpenginelinux.json` + `materials/wpenginelinux.json` (AssetLocator.cpp:249-262), `shaders/commands/copy.{frag,vert}` (AssetLocator.cpp:264-281), and a transparent solid-layer shape model + `commands/transparent` shaders (AssetLocator.cpp:283-311 - both new in the fork). New `AssetLocator::getVFS()` (AssetLocator.cpp:67, AssetLocator.h:25) exposes the VFS so `CScene` can add a scene-dependent HDR bloom effect file at construction (src/WallpaperEngine/Render/Wallpapers/CScene.cpp:312).
- **Where it lives**: modified src/WallpaperEngine/Assets/AssetLocator.cpp/.h. Caller: src/WallpaperEngine/Application/WallpaperApplication.cpp:172-174 (thin wrapper) and WPSchemeHandlerFactory.cpp:29.
- **Surface**: no flags in this file; the HDR-bloom virtual files are consumed by CScene, which is gated by `LWE_NOBLOOM` (CScene.cpp:338, Render area).
- **Coupling**: invasive in behavior but mechanically simple - it's a relocated/upgraded version of `WallpaperApplication::setupAssetLocator`; a porter would take AssetLocator.cpp/.h plus fix up the (single) application call site. It also depends on `MediaCoverFactory` and `Media::MediaSource` (both pre-existing upstream). The virtual-file payloads are plain data, no engine hooks.
- **Tests**: none.
- **Uncertain**: the comment at AssetLocator.cpp:150-152 says the classic bloom effect file is loaded by an in-scene image; the actual CScene wiring is covered in the render chapter.

### Steam workshop content-root discovery
- **What it does**: New `Steam::FileSystem::workshopContentRoots(appID)` (src/Steam/FileSystem/FileSystem.cpp:70-84, declared FileSystem.h:11) returns **all existing** `workshop/content/<appID>` roots across the candidate Steam library paths (a hardcoded set of `$HOME`-relative candidates, unchanged from upstream - there is no `libraryfolders.vdf` parsing, so custom library drives are not auto-detected), not just one. Used to enumerate wallpapers across every library folder when building the library list (src/WallpaperEngine/Application/WallpaperApplication.cpp:629, app id 431960).
- **Where it lives**: modified src/Steam/FileSystem/FileSystem.cpp/.h.
- **Surface**: none; additive API.
- **Coupling**: self-contained (reuses existing `detectHomepath`/`workshopDirectoryPaths`); one call site. Trivially liftable.
- **Tests**: none.

### Keyframed property animations (AnimationTimeline)
- **What it does**: New data model for WE scene "animation" blocks on dynamic values. `AnimationTimeline` (new file src/WallpaperEngine/Data/Model/AnimationTimeline.h:28-42) holds up to 4 channels of sorted keyframes with cubic-bezier tangents, `fps`, `length` (frames), `mode` (single/loop/mirror, AnimationTimeline.h:21-26), `relative`, and `baseValue`. `evaluate(elapsedSeconds)` (new file AnimationTimeline.cpp:58-71) converts time to frames, evaluates each channel with a Newton-solved bezier ease (x-only handles mapped to [0,1], AnimationTimeline.cpp:9-55), clamps outside keyframe range, and adds `baseValue` when `relative`. Note: `evaluate` itself ignores `mode` - loop/mirror time folding is done by the caller, `CScene::tickAnimations` (src/WallpaperEngine/Render/Wallpapers/CScene.cpp:382-414); "single" just holds the last keyframe value by clamping. Parsed by `DynamicValueParser::parseAnimation` (src/WallpaperEngine/Data/Parsers/DynamicValueParser.cpp:134-174) when a value object has an `animation` object (DynamicValueParser.cpp:80-85); channels `c0..c3`, options `fps/length/mode`, keys `frame/value/front/back`. Stored on `DynamicValue` via `setAnimation`/`getAnimation` (src/WallpaperEngine/Data/Model/DynamicValue.cpp:299-301) with new `UpdateSource::Animation = 3` (DynamicValue.h:50).
- **Where it lives**: new files AnimationTimeline.h/.cpp; modified DynamicValueParser.cpp/.h (new private method, DynamicValueParser.h:14), DynamicValue.h/.cpp.
- **Surface**: driven purely by wallpaper scene JSON (`animation` blocks). Trace logging under `LWE_LIGHTDUMP` env in the consumer (CScene.cpp:417, Render area).
- **Coupling**: the Data-layer portion (timeline struct + parser + DynamicValue storage) is self-contained and liftable; animation only visibly runs with the CScene ticker and per-object `queueAnimation` hookup (CScene.cpp:365-380, 396-435) plus `UpdateSource::Animation` handling wherever updates are dispatched.
- **Tests**: none.
- **Uncertain**: `AnimationTangent.y` is parsed (DynamicValueParser.cpp:91-101) but never used by the evaluator (bezier ease uses x components only) - matches the "y ignored" comment in Object.h:642 but means non-unit-y curves evaluate approximately.

### New scene object kinds: model objects (.mdl), lights, shapes
- **What it does**: `ObjectParser` now handles three previously-unsupported object kinds. (a) Objects with a string `model` key are parsed as `ModelObject` (new struct src/WallpaperEngine/Data/Model/Object.h:658-682): the parser reads the binary `.mdl` itself - verifies `MDLV` magic, reads submesh count at offset 17 (must be 1..16), walks null-terminated material paths and skips per-submesh bounds/vertex/index blocks with truncation checks (src/WallpaperEngine/Data/Parsers/ObjectParser.cpp:183-235), loads each submesh's material via `MaterialParser`, and parses an optional keyframed `angles.animation` into `PropertyAnimation` (Object.h:652-656; ObjectParser.cpp:238-262). Parse failure falls back to a placeholder ObjectData named "model-parse-failed" (ObjectParser.cpp:82-92). (b) Objects with a string `light` key parse as `Light` (Object.h:685-708; `parseLight` ObjectParser.cpp:276-300) with type, color, intensity, radius/exponent, inner/outer cone, control point, cascade distances, castShadow; upstream only logged "not supported". (c) Objects with a `shape` key now render as an image using the synthesized transparent shape model `models/wpenginelinux_shape.json` (ObjectParser.cpp:97-106) instead of being dropped.
- **Where it lives**: modified src/WallpaperEngine/Data/Parsers/ObjectParser.cpp/.h (new private methods, ObjectParser.h:27-30) and src/WallpaperEngine/Data/Model/Object.h, Types.h:23-24,56-57 (new forward decls + ptr aliases).
- **Surface**: env var `LWE_SHAPES` read once at ObjectParser.cpp:98-100: shapes render **by default**; `LWE_SHAPES=0` disables. Note the code/log mismatch: the skip message says "LWE_SHAPES=1 to render" (ObjectParser.cpp:105) but the actual logic is enabled-unless-`0` - the code wins.
- **Coupling**: parsing is self-contained in ObjectParser/Object.h, but the objects do nothing without their render counterparts (CModelObject/CLight render classes and the shape model VFS entry from `setupAssetLocator`). The .mdl walking is hand-rolled binary parsing independent of `BinaryReader`.
- **Tests**: none.
- **Uncertain**: the .mdl layout offsets (magic 4 + 9-byte version + two u32s, count at byte 17, records at 21) are asserted by comments for "MDLV00xx" files; only guarded by the truncation checks, not validated against a spec.

### Scene parsing additions: fog, HDR bloom, perspective camera, misc
- **What it does**: `WallpaperParser::parseScene` gains: distance and height fog blocks (12 `fogdistance*`/`fogheight*` keys, src/WallpaperEngine/Data/Parsers/WallpaperParser.cpp:47-63; model src/WallpaperEngine/Data/Model/Wallpaper.h:46-56); HDR bloom parameters `hdr`, `bloomhdriterations` (default 8), `bloomhdrscatter` (1.619), `bloomhdrfeather` (0.1), `bloomhdrstrength` (2.0), `bloomhdrthreshold` (1.0), plus `bloomtint` (WallpaperParser.cpp:70-78; Wallpaper.h:77-85); perspective-scene support where `general.orthogonalprojection == null` sets `isPerspective` and skips required width/height (WallpaperParser.cpp:30,98-103; Wallpaper.h:126); `nearz`/`farz`/`fov` now also accepted from `general` in addition to `camera` (WallpaperParser.cpp:104-109); new `perspectiveoverridefov` and `zoom` user settings (WallpaperParser.cpp:110-111); and `transparentsorting` flag (WallpaperParser.cpp:114; Wallpaper.h:135). Also ambient/skylight color defaults changed 0.0 -> 0.3 (WallpaperParser.cpp:43-44).
- **Where it lives**: modified src/WallpaperEngine/Data/Parsers/WallpaperParser.cpp and src/WallpaperEngine/Data/Model/Wallpaper.h.
- **Surface**: all scene-JSON keys listed above; no env/CLI in the parser itself.
- **Coupling**: parser + POD fields are additive and liftable; fog/HDR-bloom/perspective rendering all live in Render. Changed default ambient (0.3) alters look of existing wallpapers if ported alone.
- **Tests**: none.

### Particle system parser rework
- **What it does**: `parseParticle` was refactored to extract `parseParticleCore(json, project, depth)` (src/WallpaperEngine/Data/Parsers/ObjectParser.cpp:550) so child particle files are now **fully parsed recursively** into `ParticleChild::definition` (Object.h:535) with a recursion cap of depth 4 (ObjectParser.cpp:624-626); a child `name` ending in `.json` is treated as the file directly (ObjectParser.cpp:1033-1039). Other changes: new `parentcontrolpoint` key (default -1) on control points (ObjectParser.cpp:1015; Object.h:164); `colornAuthored` tracks whether `colorn` was present (ObjectParser.cpp:1110; Object.h:551) - consumed with `Project::fromPackage` in CParticle (src/WallpaperEngine/Render/Objects/CParticle.cpp:1277-1391); "instant" emitters get different defaults (distanceMax 0, instantaneous from `count`, rate 0) (ObjectParser.cpp:779-793); ropetrail renderers get rope-like subdivision/length defaults (ObjectParser.cpp:970-971); a `sizechange` operator with only `starttime` maps to a shrink-over-remaining-lifetime form (ObjectParser.cpp:885-895); child `maxcount` default changed 20 -> 10 (ObjectParser.cpp:1088); effect lists skip non-object entries and entries without `file`, and a failing effect is logged and skipped instead of aborting the image (ObjectParser.cpp:364-376); dependencies arrays accept either numbers or objects with `id` (ObjectParser.cpp:127-137); particle files parse via `parseLenient` (ObjectParser.cpp:520,1046).
- **Where it lives**: modified src/WallpaperEngine/Data/Parsers/ObjectParser.cpp/.h, src/WallpaperEngine/Data/Model/Object.h.
- **Surface**: scene/particle JSON keys only.
- **Coupling**: deeply woven into ObjectParser; a porter would take the whole ObjectParser.cpp + Object.h delta. `definition` and `colornAuthored`/`fromPackage` semantics only matter with the CParticle render changes.
- **Tests**: none.
- **Uncertain**: the depth-4 cap silently drops deeper children (only logs); whether real wallpapers nest deeper is not determinable from code.

### Misc data-layer additions
- **0..1 float color strings**: `ColorBuilder` now returns values as-is when all int components are <= 1, instead of dividing by 255 (src/WallpaperEngine/Data/Builders/ColorBuilder.cpp:62-68) - supports scenes that author colors as float vectors.
- **Textinput property without value**: `parseTextInput` defaults a missing `value` to `""` instead of throwing (src/WallpaperEngine/Data/Parsers/PropertyParser.cpp:150). Combo properties still require a value, same as upstream.
- **Sound volume**: sounds parse a `volume` user setting, default 1.0 (ObjectParser.cpp:153; Object.h:145); `parseSound` signature gained `const Project&` (ObjectParser.h:23).
- **Text limits**: `limitwidth`/`maxwidth`/`limitrows`/`maxrows`/`limituseellipsis` parsed (ObjectParser.cpp:173-177; Object.h:624-628) - upstream had a TODO.
- **Image flags**: `copybackground` and `perspective` booleans (ObjectParser.cpp:325,330; Object.h:123,130).
- **Puppet crop offset**: model JSON `cropoffset` string vec2, can be negative (ModelParser.cpp:34; Model.h:33); animation layers get `additive` default true (ObjectParser.cpp:464; Object.h:98).
- **`Project::fromPackage`**: set when `<bg>/scene.pkg` exists (src/WallpaperEngine/Application/WallpaperApplication.cpp:277; Project.h:31); consumed only by CParticle color random handling (CParticle.cpp:1277-1391).
- **Sha256 utility**: new header-only public-domain-style SHA-256 (src/WallpaperEngine/Data/Utils/Sha256.h:10-83), stated to match `hashlib.sha256`; sole consumer is the BC7 texture disk cache key in src/WallpaperEngine/Render/CTexture.cpp:242 (cache at `$HOME/.local/state/lwe/texcache`). Self-contained, liftable alone; no tests.
- Each is small and independently liftable except where noted; none have flags/env.

### Modified-in-name-only / unchanged notes
- src/WallpaperEngine/Data/Parsers/ProjectParser.cpp, ShaderConstantParser.cpp, UserSettingParser.cpp, DynamicValueParser.h changes are confined to what's listed above; ProjectParser and ShaderConstantParser are **byte-identical to upstream**. VectorBuilder, UserSettingBuilder, TypeCaster unchanged.
- src/WallpaperEngine/FileSystem/Container.cpp/.h and Adapters/MediaCover.* are **unchanged** vs upstream (`Container::getVFS` already existed upstream at Container.h:53 - the fork only exposes it through AssetLocator).

## 5-line summary

The fork's data layer is heavily hardened against malformed content: `BinaryReader` now fails on short reads and bounds-checks lengths against `remaining()`, `MemoryStream` rejects out-of-range seeks, and the package/texture parsers cap counts and sizes before allocating (all covered by the new `BinaryParserHardening.cpp` test). A lenient JSON pipeline (`parseLenient` + comment/trailing-comma stripping + numeric-string coercion + exception-safe `optional`) is wired through every scene/model/material/effect/particle parse path. New readable formats: raw-GL TEXB0004 textures, perspective-projection scenes, `.mdl` model objects, light/shape objects, keyframed `AnimationTimeline` property animations, recursive child particles, fog, and HDR-bloom scene keys - all unsupported upstream. Filesystem-wise, mounting was centralized into `Assets::setupAssetLocator` (mounts every `.pkg`, all effect dirs, and injects synthesized bloom/HDR/shape virtual files into the VFS), package lookup is case-insensitive, directory containment is fixed, and all candidate Steam library workshop roots are probed. Only env knob in this area: `LWE_SHAPES=0` (shapes render by default; the skip log message contradicts the code).

---

# 6. Script engine

- **What it does**: The fork fixes how native script modules publish their exports. Upstream called `JS_SetModuleExport()` in the module constructor and (incorrectly) `JS_AddModuleExport()` in the init callback; the fork flips this - exports are *declared* with `JS_AddModuleExport()` at construction and *valued* with `JS_SetModuleExport()` inside the init callback, which QuickJS runs at module instantiation. The init callbacks now also locate the module by matching `getDefinition()` against a global registry and throw a ReferenceError if unregistered (WEVector's own init does the same walk but silently returns instead of throwing). A new `WEVector` module adds `vectorAngle2(vec)` (atan2 in degrees) and `vectorAngle3(a, b)` (clamped-acos angle in degrees, zero-safe).
- **Where it lives**: modified `src/WallpaperEngine/Scripting/Modules/MathModule.cpp` (`wemath_init` :16, ctor :81), `Modules/ColorModule.cpp` (`wecolor_init` :18, ctor :255); new files `Modules/VectorModule.cpp` (`wevector_vectorangle2` :22, `wevector_vectorangle3` :32, `wevector_init` :51, ctor :70) and `Modules/VectorModule.h`. Module registered in `ScriptEngine.cpp:234-237`. `Modules/ScriptModule.{h,cpp}` are unchanged.
- **Surface**: script-visible module names `WEMath` (`smoothStep`, `mix`, `deg2rad`, `rad2deg`), `WEColor` (`rgb2hsv`, `hsv2rgb`, `normalizeColor`, `expandColor`), `WEVector` (`vectorAngle2`, `vectorAngle3`). Unresolvable imports now throw `could not resolve module '%s'` (`ScriptEngine.cpp:83`) instead of returning null silently.
- **Coupling**: self-contained. MathModule/ColorModule changes are internal restructuring (no behavior change intended); VectorModule is two new files plus three lines in `ScriptEngine.cpp` (include :9, construct :234, emplace :238) and two lines in `CMakeLists.txt`. A porter could take VectorModule alone trivially; the init-callback restructuring is only needed if upstream's export timing actually misbehaves on their QuickJS version.
- **Tests**: none (no scripting cases exist under `src/WallpaperEngine/Testing/Cases/`).

### Script module load pipeline: compile-only eval, pending jobs, `init()` hook, error containment
- **What it does**: `queueScript()` was rewritten from a single `JS_Eval` into a staged pipeline: compile with `JS_EVAL_FLAG_COMPILE_ONLY`, report compile errors with module key, evaluate via `JS_EvalFunction`, drain up to 64 pending jobs, detect rejected evaluation promises, then capture the module namespace via `JS_GetModuleNamespace` and invoke a new `init(value)` export before the first `update(value)`. `logJSException` was hardened: detects value-less exceptions (host callback returned bare `JS_EXCEPTION`), and prints the JS `stack` property when present.
- **Where it lives**: modified `src/WallpaperEngine/Scripting/ScriptEngine.cpp` (`queueScript` :607, compile :629-636, job drain :666-676, namespace :698, init call :731-743; `logJSException` :303-331; `call` helper unchanged at :595).
- **Surface**: script-facing contract gains an optional `export function init(value)`. Per-module `update()` exceptions are logged at most 3 times per module key then suppressed (`ScriptEngine.cpp:820-826`); the write-back of the update result is wrapped in `try/catch (const std::exception&)` and swallowed (:828-842). Debug env var `LWE_SCRIPTDBG` (read at :608) traces register/skip decisions.
- **Coupling**: contained to `ScriptEngine.cpp`/`ScriptEngine.h`. The error-shape fixes (replacing bare `JS_EXCEPTION` returns with `JS_ThrowTypeError`) span EngineObject/InputObject/SceneObject/ScriptPropertiesObject/VectorAdapter/ScriptableObjectAdapter but are mechanical and independently cherry-pickable per file.
- **Uncertain**: the 64-job drain cap silently drops further pending jobs; no code comment states whether that bound was measured.

### Degrees<->radians bridge for `angles_*` scripts
- **What it does**: WE script-space angles are degrees; the engine stores radians. For any script module whose key starts with `angles_` and whose value is a Vec3, the engine creates a persistent `degreesMirror` DynamicValue; each tick it refreshes the mirror from radians, hands the *mirror* to `init()`/`update()` (so linked vector args write through in degrees), and converts the result back with `glm::radians` before storing.
- **Where it lives**: `ScriptEngine.h` `LoadedModule::degreesMirror` :51-55; `ScriptEngine.cpp` mirror creation :647-650, queueScript store-back lambda :718-728, tick bridge :806-840.
- **Surface**: triggered purely by the property key prefix `angles_` (:647). Trace env var `LWE_LIGHTDUMP` dumps angles for keys containing `angles_112` every 90 ticks (:794-799).
- **Coupling**: self-contained in ScriptEngine, but depends on the upstream-unchanged script-key naming convention (`angles_<objectId>`); a porter must confirm key generation matches. Logic lives entirely in queueScript/tick.
- **Uncertain**: the `LWE_LIGHTDUMP` trace hardcodes the substring `angles_112`, so it traces one specific wallpaper's property keys rather than a general mechanism.

### Per-frame tick model
- **What it does**: `CScene::renderFrame` calls `getScriptEngine().tick()` once per rendered frame, before `tickAnimations()` (`src/WallpaperEngine/Render/Wallpapers/CScene.cpp:1025-1027`). Tick order: (1) `EngineObject::tick()` - refresh registered audio buffers, then fire due intervals/timeouts (`EngineObject.cpp:403-434`); (2) `dispatchCursorEvents()`; (3) per loaded script module, set `m_runningModule`, call `update(value)`, write result back. Interval/timeout callbacks are invoked with `JS_Call` and their results are **not** exception-checked (`EngineObject.cpp:410-428`).
- **Where it lives**: `ScriptEngine.cpp:784-843` (tick), `EngineObject.cpp:403` (timer tick); call site `CScene.cpp:1025`.
- **Surface**: none (no config). `g_Time - g_TimeLast` drives animation timing; `engine.frametime` now clamps non-positive deltas to 1/60 (`EngineObject.cpp:27-30`).
- **Coupling**: tick itself is upstream structure; the fork inserts one line (`dispatchCursorEvents()` at :788) and the angles bridge. A porter wanting only cursor events can take dispatchCursorEvents + one call-site line.
- **Uncertain**: exceptions thrown inside `setInterval`/`setTimeout` callbacks are never retrieved (`JS_Call` result discarded, no `JS_GetException`) - this may leave a pending exception that surfaces in an unrelated later call. Behavior not verifiable statically beyond noting the missing check.

### Audio response buffers (`engine.registerAudioBuffers`)
- **What it does**: Scripts call `engine.registerAudioBuffers(resolution)` and receive `{average, left, right}` JS arrays of length 16/32/64 (requested resolution snaps up to the nearest supported size, with an error log). The engine keeps the returned arrays alive and rewrites every element from the playback recorder's spectrum bands at the top of every tick, before any script `update()` runs. Capture is mono, so `left`/`right` mirror `average`.
- **Where it lives**: modified `EngineObject.h` (`AudioBufferLink` struct :42, `m_audioBuffers` :53, `registerAudioBuffers` decl :26, `updateAudioBuffers` decl :31); `EngineObject.cpp` (JS binding `engine_register_audio_buffers` :170-186, property registration :237-243, impl `registerAudioBuffers` :336-366, `updateAudioBuffers` :368-401, dtor frees :277-281, tick call :404-405). Data source: `src/WallpaperEngine/Audio/Drivers/Recorders/PlaybackRecorder.h:12-14` (`audio16/32/64` arrays - the `.h` differs from upstream; the fork also adds an `enabled` flag at :10).
- **Surface**: script API `engine.registerAudioBuffers([resolution])`, default 16 (:176), allowed {16,32,64} (:337). Debug env var `LWE_AUDIOSTATS` (:389) prints buffer count and first bands every 30 ticks.
- **Coupling**: moderate. EngineObject side is self-contained, but it reads `scene.getAudioContext().getRecorder()` and the recorder's `audio16/32/64` arrays (declared upstream; the fork adds only the `enabled` flag) - a porter also needs whatever fills `audio16/32/64` in the audio driver (see chapter 7).
- **Tests**: none.

### Native persistent `localStorage`
- **What it does**: A new native `ILocalStorage` object replaces upstream's pure-JS in-memory shim. It persists per-wallpaper to `$HOME/.local/share/lwe/storage/<workshopId|filename>.json` (slashes sanitized, `default` fallback; temp-dir base if `$HOME` unset). Two API flavors: WE-style `get/set/remove` (values JSON-stringified on write, JSON-parsed on read with string fallback) and DOM-style `getItem/setItem/removeItem/key/clear/length` (raw strings). Saves are atomic: write `<file>.tmp`, verify stream health, rename over the target.
- **Where it lives**: new files `Scripting/LocalStorageObject.cpp` (ctor+path :172-194, WE-style :24-97, DOM-style :99-153, `load` :271-287, `save` :289-316) and `LocalStorageObject.h`. Wired in `ScriptEngine.h:7` (include), `ScriptEngine.cpp:230` (construct), `:259-262` (global define), `:290` (teardown); added to `CMakeLists.txt` sources.
- **Surface**: globals `localStorage`, constants `LOCATION_SCREEN`/`LOCATION_GLOBAL` exposed as string constants on the object (:214-219) but **accepted nowhere** - no native function reads a location argument; the namespacing exists only in the builtins.js fallback. Env: `$HOME` read at :174. No opt-out/kill switch.
- **Coupling**: nearly self-contained - porter needs the two new files, the three wiring points in ScriptEngine, and CMake lines. Note ordering: `installBuiltins()` runs first (:242), so builtins' JS fallback installs first and the native define at :259 overwrites it; keep that order.
- **Uncertain**: `LOCATION_*` constants suggest per-screen storage was planned; whether WE scripts passing a location get silent cross-screen leakage is untestable from code (the arg is ignored).

### Engine object: new properties + `setInterval`/`setTimeout` repair
- **What it does**: Adds `engine.screenResolution` (always `{x: sceneWidth, y: sceneHeight}`) and `engine.canvasSize` (same, but `undefined` unless the camera is orthogonal). Fixes upstream's broken timers: upstream stored `EngineObject&` in `engineInstances` but **never inserted into it** (only erased in the dtor) and registered handlers with `JS_CFUNC_generic` though the signatures expect magic - so every setInterval/setTimeout/clear* call hit the not-found path and returned `JS_EXCEPTION`. The fork inserts the instance in the ctor, switches the map to pointers, and uses `JS_CFUNC_generic_magic`.
- **Where it lives**: `EngineObject.cpp` (map :17, ctor insert :190, `engine_get_screenresolution` :36-48, `engine_get_canvassize` :50-64, property registration :205-215, magic registrations :245-259); upstream bug visible at upstream `EngineObject.cpp:14` + missing insert.
- **Surface**: script APIs `engine.screenResolution`, `engine.canvasSize`, `engine.frametime` (clamped), `engine.runtime`, `engine.setInterval/setTimeout` (now functional; cancellation uses the stop-closure they return - there are no `clearInterval`/`clearTimeout` JS bindings). Read-only setters throw `property is read-only` (:19-21). No env/config.
- **Coupling**: confined to EngineObject.{h,cpp}. The timer repair is a self-contained bug fix; screenResolution/canvasSize need `scene.getWidth/getHeight/getCamera().isOrthogonal()` (upstream APIs).
- **Uncertain**: `engine.openUserShortcut` remains a stub returning undefined (both trees).

### Camera transforms API (`thisScene.getCameraTransforms` / `setCameraTransforms`)
- **What it does**: Scripts can read the camera's eye/center as real builtins `Vec3` instances (constructed by calling the global `Vec3` constructor so methods like `.subtract()` exist) and write them back. The setter parses `eye`/`center` objects field-by-field with the current camera value as fallback, then calls `Camera::setScriptedView`, which rejects non-finite or degenerate (eye~center) input and stores a scripted view that recomputes the look-at matrix for perspective scenes.
- **Where it lives**: `SceneObject.cpp` (`makeVec3` :17-30, `readVec3` :31-55, `scene_get_camera_transforms` :57-73, `scene_set_camera_transforms` :75-87, registration :381-390); target method `src/WallpaperEngine/Render/Camera.cpp:90` (`setScriptedView`), declared `Camera.h:27`.
- **Surface**: script APIs only. Debug env var `LWE_CAMPROBE` (`SceneObject.cpp:61`, `Camera.cpp:91`) logs eye/center, capped at 10 (get) / 40 (set) lines.
- **Coupling**: SceneObject additions are self-contained; the setter requires the fork's `Camera::setScriptedView` + `m_hasScriptedView` state in Render/Camera (cross-area dependency - porter needs the Camera diff too).
- **Uncertain**: how `m_hasScriptedView` interacts with the per-frame view recomputation elsewhere in Camera is outside the files I diffed in depth.

### Cursor event system (`cursorEnter/Leave/Move/Down/Up/Click`)
- **What it does**: After a module is evaluated, the engine scans its namespace for any of the six cursor hook exports; if found, the module is flagged `cursorEvents`. Each tick (when any flagged module exists) the engine computes a world-space cursor position from the normalized mouse and scene dimensions, hit-tests each flagged module's object via `CImage::cursorLocalPosition` (which un-rotates the point into the image quad's frame and bounds-checks against half-extents), and fires edge/edge-exit/move/down/up/click hooks with an event object `{worldPosition, localPosition}` of linked vec3 adapters. Click requires the press to have started inside.
- **Where it lives**: `ScriptEngine.h` (`LoadedModule::{object,cursorEvents,cursorInside,cursorPressedInside}` :56-59, decls :164-168); `ScriptEngine.cpp` (hook scan :700-716, `makeCursorEvent` :907, `dispatchCursorEvents` :916-1027, tick call :788); hit test `src/WallpaperEngine/Render/Objects/CImage.cpp:1461-1496`.
- **Surface**: script exports `cursorEnter, cursorLeave, cursorMove, cursorDown, cursorUp, cursorClick` (list at :701-702). Debug env var `LWE_CURSORDBG` (:709, :945, and `CImage.cpp:1473`) traces exports, probes, edges, and fires. No config keys.
- **Coupling**: needs `LoadedModule` fields, `CImage::cursorLocalPosition` (Render area), `CScene::getMousePositionNormalized` and input context mouse state (both pre-existing). Non-CImage objects never report `inside`, so only image layers get events. Moderately portable: ScriptEngine side is additive; must take the CImage method too.
- **Uncertain**: movement detection uses first-frame `value_or(worldPosition + vec3(1))`, so the very first tick always counts as "moved" - deliberate or not is undeterminable.

### Layer adapter: texture animation control, `thisLayer.size`, and the exotic get_property fix
- **What it does**: Three things. (1) The fork assigns `m_exoticMethods.get_property` - upstream declared the handler but never wired it, so `thisLayer.<prop>` reads through the exotic path were dead. (2) New `thisLayer.size` returns `{x,y}` from `CImage::getSize()` for image layers (undefined otherwise). (3) New `thisLayer.getTextureAnimation()` returns a controller with `pause/play/stop/setFrame/getFrame/getFrameCount/isPlaying` delegating to `CRenderable` texture-animation methods (`stop` aliases pause).
- **Where it lives**: `Adapters/ScriptableObjectAdapter.cpp` (exotic wiring :148, `textureanim_op` :23-64, controller builder :66-79, `size` :98-110, `getTextureAnimation` :112-116); target methods on `src/WallpaperEngine/Render/Objects/CRenderable` (declared via includes at :6-7; definitions in Render area).
- **Surface**: script APIs above. No env/config. Texture-animation methods are no-ops (undefined) on non-renderable layers (:35-38).
- **Coupling**: controller needs the fork's `CRenderable::pauseTextureAnimation/play/set/get...` methods (Render area). The exotic wiring one-liner is independently cherry-pickable and arguably a pure upstream bug fix.
- **Tests**: none.

### ScriptableObject lifecycle: property registration, animation queue, unregistration
- **What it does**: The base-class ctor no longer registers `origin/scale/angles/visible` from the *group* values (upstream did); per-subclass registrations (e.g. `CImage.cpp:148-154`, which existed upstream) now solely govern those names. `registerProperty` still queues the script but now also calls `scene.queueAnimation(value, object)`. A new destructor calls `scene.forgetObjectAnimations(*this)` and `scriptEngine.unregisterScriptable(this)`, which erases all script modules owned by the dying object (freeing their module namespace JSValues and clearing `m_runningModule` if it pointed at them).
- **Where it lives**: `ScriptableObject.cpp` (dtor :10-13, queueAnimation :42), `ScriptableObject.h:18`; `ScriptEngine.cpp` `unregisterScriptable` :762-782; scene-side `CScene::queueAnimation` :367 / `forgetObjectAnimations` :448 / animated list `CScene.h:38-39`.
- **Surface**: none. (Note: `CScene::queueAnimation` logs an unconditional `LWE-TIMELINE` line via `sLog.out` at `CScene.cpp:375-380` for every animated property - Render area, but visible to anyone tailing logs.)
- **Coupling**: the unregister half is self-contained (ScriptableObject + ScriptEngine); the animation-queue half requires the fork's `CScene::m_animatedProperties`/`tickAnimations` machinery (Render area). The dtor ordering matters: `CObject` base must still be alive, which it is (member-typed base).
- **Uncertain**: whether dropping the base-ctor group-value registrations changes behavior for object types whose subclass never re-registers those names - no such subclass was found in the grep (CText/CModel/CLight/CParticle/CImage all register their own), but a type outside that list would now lack `origin` etc.

### Live user-property updates (`applyUserProperties`)
- **What it does**: When the application applies changed user properties (e.g. from the properties UI/socket), it calls `ScriptEngine::notifyUserPropertiesChanged(changed)`; the engine then, per loaded module, rebinds `thisLayer` to that module's object and calls its `applyUserProperties(props)` export with a plain JS object of changed property name -> value. Empty change sets short-circuit.
- **Where it lives**: `ScriptEngine.cpp` (`notifyUserPropertiesChanged` :1040-1062, `buildUserPropertiesObject` :1030-1038), decl `ScriptEngine.h:89`; sole caller `src/WallpaperEngine/Application/WallpaperApplication.cpp:3055`.
- **Surface**: script export `applyUserProperties(properties)`. No env/config; driven entirely by the caller in WallpaperApplication (screen-override JSON path at :2570-2602).
- **Coupling**: needs the `LoadedModule::object` pointer (shared with cursor events) and the application-side call site. Small, liftable as: the two ScriptEngine methods + one call site.

### ScriptPropertiesObject correctness fixes
- **What it does**: `add*()` on the script-properties creator now `JS_DupValue`s `this_val` instead of returning a borrowed reference (upstream leaked/mis-counted the ref). `finish()` null-guards the opaque. Property-set exotic now throws a real TypeError before returning -1 (upstream returned -1 with no pending exception, which printed as "[uninitialized]"). `createScriptProperties` is registered with `JS_CFUNC_generic_magic` so the instance-id magic actually arrives (upstream used `JS_CFUNC_generic`, same bug class as the engine timers). Property-get exceptions now carry the property name and message.
- **Where it lives**: `ScriptPropertiesObject.cpp` (`scriptproperties_property_get` :23-63, `_set` :66-77, `scriptpropertiescreator_add` :73-75 area per diff, `finish` :79-102, magic fix :188-192).
- **Surface**: script API unchanged (`createScriptProperties().add*().finish()`); trace env var `LWE_LIGHTDUMP` (:40) logs property lookups, capped at 24 lines.
- **Coupling**: self-contained file; pure correctness fixes, individually cherry-pickable.

### VectorAdapter + vector write-back fixes
- **What it does**: (1) `jsToDynamicValue` fixes: upstream tested `JS_IsNumber(y)` without negation (so valid 2-component writes threw) and never converted z/w to doubles before use; the fork negates the y check and adds the missing `JS_ToFloat64` calls; it also no longer marks the source `Script`-updated when the script returns undefined. (2) `OpaqueVectorAdapter` gains `adapterInstanceId`; the finalizer only frees the pooled DynamicValue if the owning adapter instance still exists - preventing frees into a destroyed adapter after engine teardown/hot-swap. (3) Error hygiene: bare `JS_EXCEPTION`/`-1` returns replaced with thrown TypeErrors; unknown vector property reads now return `undefined` instead of throwing (:195).
- **Where it lives**: `Adapters/VectorAdapter.cpp` (field :40, finalizer guard :409-412, instantiate sites :954/:972/:992, error fixes throughout); `ScriptEngine.cpp` `jsToDynamicValue` :113-183.
- **Surface**: behavioral only; one softening - reading an unknown property off a linked vector yields `undefined` rather than an exception.
- **Coupling**: self-contained. The finalizer guard pairs with the fork's engine teardown ordering (adapters reset in `~ScriptEngine` :280-292).

### builtins.js expansion (Vec/Mat library + guarded fallbacks)
- **What it does**: The embedded preamble grows from ~16 lines to ~886. New: `Vec2/Vec3/Vec4` classes with scalar-or-vector arithmetic (`add/subtract/multiply/divide/length/normalize` inline; `distance/negate/reflect/refract/project/angle*/rotate/clamp/fract/mod/step/smoothStep/toSpherical/fromSpherical` added via prototype only-if-missing), full column-major `Mat3`/`Mat4` (`lookAt`, `compose`, `decompose`, `inverse`, `transformPoint/Direction`, `normalMatrix`, Euler extract), JS fallbacks for `WEMath`/`WEVector`, and a namespaced in-memory `localStorage` fallback. Upstream's JS localStorage shim is replaced by this namespaced fallback (both are guarded). Global class bindings are `||`-guarded and the Vec extras are add-only-if-missing so native C++ objects win (the Mat3/Mat4 prototype methods are unconditional assignments - harmless today since no native Mat3/Mat4 exists - and the internal `__intervals` hook is an unguarded leftover); the native `localStorage` define at `ScriptEngine.cpp:259` overwrites the fallback after install.
- **Where it lives**: `resources/builtins.js` (whole file is the diff); embedded via upstream's existing CMake machinery (`CMakeLists.txt:264-276` generates `Builtins.generated.h`; present in both trees) and installed by `installBuiltins()` (`ScriptEngine.cpp:334-348`, called at :242 - both upstream).
- **Surface**: script globals `Vec2, Vec3, Vec4, Mat3, Mat4, WEMath, WEVector, localStorage, MediaPlaybackEvent` (the last is upstream). Angles in these helpers are degrees (DEG2RAD/RAD2DEG constants at builtins.js top of IIFE).
- **Coupling**: zero native coupling - drop-in file, build system untouched. The most easily cherry-picked item in this area.
- **Uncertain**: the C++ value bridge (`jsToDynamicValue`) reads plain x/y/z/w fields, so builtins Vecs and native adapter vectors are interchangeable by shape; but `instanceof` checks in scripts would distinguish them - whether any real script does is unknowable here.

---

### Summary (5 lines)

The fork turns the script engine from a skeleton into a working WE-compatible runtime: native script modules (`WEMath`/`WEColor` export timing fixed, new `WEVector`), a staged compile/eval pipeline with `init()`, per-module error suppression, and stack-trace logging. New script APIs: `engine.registerAudioBuffers` (16/32/64-band spectrum arrays refreshed each tick from the playback recorder), persistent per-wallpaper `localStorage` (atomic JSON file under `$HOME/.local/share/lwe/storage/`), `engine.screenResolution/canvasSize`, `thisScene.get/setCameraTransforms`, six cursor event hooks hit-tested against image quads, `thisLayer.size`, texture-animation control, and `applyUserProperties` live-update. Upstream bugs fixed en route: dead `setInterval/setTimeout` (never-inserted registry + wrong CFunc magic), the unwired layer exotic `get_property`, the inverted y-check and missing z/w conversions in vector write-back, a dangling album-art listener, and ref-count/exception-shape errors across adapters. Per-frame model: `CScene::renderFrame` -> `ScriptEngine::tick` -> audio buffers + timers -> cursor dispatch -> per-module `update()` with a degrees<->radians mirror for `angles_*` scripts. Debug surface is env vars only (`LWE_SCRIPTDBG`, `LWE_CURSORDBG`, `LWE_LIGHTDUMP`, `LWE_AUDIOSTATS`, `LWE_CAMPROBE`); no test cases cover any of this under `Testing/Cases/`.

---

# 7. Audio, input, video playback, fullscreen policy


- **What it does**: `AudioContext` no longer holds its driver by const reference; it holds a pointer and exposes `setDriver()`, so the concrete driver can be swapped at runtime. A new `NullAudioDriver` (fork-only) satisfies the `AudioDriver` interface while opening no SDL device: `addStream()` returns -1, `getFormat/getSampleRate/getChannels` return fixed FLT/48000/2 (`src/WallpaperEngine/Audio/Drivers/NullAudioDriver.cpp:9-17`). `WallpaperApplication::setupAudio()` builds the SDL driver only if some loaded scene actually contains a Sound object and audio is enabled, otherwise the Null driver (`src/WallpaperEngine/Application/WallpaperApplication.cpp:913-931`). `ensureAudioForProject()` (`WallpaperApplication.cpp:365-416`, called from `WallpaperApplication.cpp:523` and `:1856`) lazily upgrades Null->SDL when a newly shown project has sound objects (never downgrades a live SDL driver, `WallpaperApplication.cpp:407-410`), and also upgrades the recorder to PulseAudio when a project sets `supportsaudioprocessing`, rebuilding the driver against the new recorder first (`WallpaperApplication.cpp:366-389`).
- **Where it lives**: new files `src/WallpaperEngine/Audio/Drivers/NullAudioDriver.{h,cpp}`; modified `src/WallpaperEngine/Audio/AudioContext.h:69` (`setDriver`), `AudioContext.cpp:4-24` (member `m_driver` is now `AudioDriver*`, all accessors dereference it); call sites in `WallpaperApplication.cpp`.
- **Surface**: no flag/env of its own; driven by the existing `settings.audio.enabled` (`--no-audio`-style flags) and per-project sound/audio-processing metadata. Kill switch is implicit: with no sound objects the SDL device is never opened.
- **Coupling**: touches the upstream `AudioContext` class shape (reference->pointer - source-compatible for callers) plus two functions in `WallpaperApplication`. A porter needs: `AudioContext.{h,cpp}` diff, the two NullAudioDriver files, and the `setupAudio`/`ensureAudioForProject` logic. Self-contained otherwise; CMake lines `CMakeLists.txt:354-355`.
- **Tests**: none directly.
- **Uncertain**: whether an `addStream()` failure (-1) on a Null driver that was never upgraded is ever user-visible; the header comment (`NullAudioDriver.h:13-19`) claims it is unreachable, which matches the `ensureAudioForProject` logic though not every `CSound::load` path is traced here.

### 2. Per-object (authored) sound volume in the SDL mixer

- **What it does**: `AudioStream` gains a per-stream linear gain `m_volume` (default 1.0) with `setVolume/getVolume` (`src/WallpaperEngine/Audio/AudioStream.cpp:417-419`). `CSound::load()` reads the wallpaper-authored `sound.volume` property, clamps to 0..1, and sets it (`src/WallpaperEngine/Render/Objects/CSound.cpp:33-35`). The SDL mix callback multiplies the global `state.audio.volume` by the stream's gain before `SDL_MixAudioFormat` (`src/WallpaperEngine/Audio/Drivers/SDLAudioDriver.cpp:60-66`). Upstream mixed every stream at the single global volume.
- **Where it lives**: modified `AudioStream.h:87-90,173`, `AudioStream.cpp:417-419`, `SDLAudioDriver.cpp:60-66`, `CSound.cpp:33-35`.
- **Surface**: env kill switch `LWE_NOOBJVOL` (any value forces per-object volume to 1.0), read once at `CSound.cpp:33`.
- **Coupling**: small and linear - four files, one call chain. Self-contained cherry-pick; no other mixer exists (SDL is the only real driver).
- **Tests**: none.

### 3. AudioStream resample output-channel fix + per-stream packet cursor

- **What it does**: Two bug fixes in `AudioStream`. (a) In `resampleAudio()`'s modern-FFmpeg path, upstream allocated the resampled buffer using the **input** stream's channel count (`ch_layout.nb_channels`); the fork derives the output layout from the driver's requested channel count via `av_channel_layout_from_mask` (mono/stereo/surround), matching how the swr context was configured at init (`src/WallpaperEngine/Audio/AudioStream.cpp:496-518`; init side at `:270-294` is upstream-identical). On mismatch the function now logs and returns -1 instead of proceeding. (b) The `static int audio_pkt_size` in `decodeFrame()` - shared across all streams - becomes per-instance member `m_audioPktSize` (`AudioStream.cpp:598,617,621,642`; member declared `AudioStream.h:181`).
- **Where it lives**: modified `src/WallpaperEngine/Audio/AudioStream.{h,cpp}` only.
- **Surface**: none.
- **Coupling**: fully self-contained; each hunk lifts independently.
- **Tests**: none.
- **Uncertain**: (a) is only reachable when `FF_API_OLD_CHANNEL_LAYOUT` is unset (newer FFmpeg); not runtime-verified here.

### 4. Audio-processing recorder runtime toggle (script-facing FFT freeze)

- **What it does**: `PlaybackRecorder` gains a public `bool enabled = true` (`src/WallpaperEngine/Audio/Drivers/Recorders/PlaybackRecorder.h:10`). Every frame, `AudioDriver::update()` copies `settings.audio.audioprocessing` into it before updating (`src/WallpaperEngine/Audio/Drivers/AudioDriver.cpp:14`). When disabled, `PulseAudioPlaybackRecorder::update()` still iterates the pa mainloop but decays all three band arrays toward 0 (`movetowards(..., 0.0f, 0.3f)`), clears `fullFrameReady`, and returns early (`PulseAudioPlaybackRecorder.cpp:220-234`) - so audio-reactive scripts smoothly settle to silence instead of freezing on the last frame. Band computations were also clamped with `fmax(0.0f, ...)` so bands can't go negative (`PulseAudioPlaybackRecorder.cpp:279-285`).
- **Where it lives**: modified `PlaybackRecorder.h`, `AudioDriver.cpp`, `PulseAudioPlaybackRecorder.cpp`; setting written by socket verb `set-audio` (`WallpaperApplication.cpp:1503-1508`), the `show` arg `audio_processing` (`WallpaperApplication.cpp:2041`), and CLI `--no-audio-processing` (upstream, `ApplicationContext.cpp:542-543`).
- **Surface**: socket verb `set-audio {enabled:bool}` (`WallpaperApplication.cpp:1503`); `show` arg `audio_processing`; status key `audio_processing` (`WallpaperApplication.cpp:1799`); CLI `--no-audio-processing`. Default on (`ApplicationContext.h:258`).
- **Coupling**: three small hunks plus whatever writes the setting. The recorder/decay part lifts alone; live control depends on the fork's command socket (covered elsewhere).
- **Tests**: none for the recorder itself; `set-audio` validation is covered indirectly by `Testing/Cases/CommandDispatcher.cpp` (verb list).

### 5. Live FFT band-gain dial (`g_LweAudioGain`)

- **What it does**: A process-global float scales every computed FFT band before the destination arrays are written (`PulseAudioPlaybackRecorder.cpp:277`, `f1 *= g_LweAudioGain`). It is env-seeded and live-settable; `set-tuning` clamps to 0.1..20.
- **Where it lives**: `extern float g_LweAudioGain` read at `src/WallpaperEngine/Audio/Drivers/Recorders/PulseAudioPlaybackRecorder.cpp:8,277`; defined at `src/WallpaperEngine/Application/WallpaperApplication.cpp:67`.
- **Surface**: env `LWE_AUDIOGAIN` (default 1.0, clamped 0.1..20 at read, `WallpaperApplication.cpp:58-67`); socket verb `set-tuning {audio_gain}` (`WallpaperApplication.cpp:1458-1459`, validated at `src/WallpaperEngine/Api/CommandDispatcher.cpp:352-369`); status key `audio_gain` (`WallpaperApplication.cpp:1744`).
- **Coupling**: one line in the recorder plus the global definition; trivially portable but pointless without the socket unless env-seeding is enough.
- **Tests**: `set-tuning` arg validation in `src/WallpaperEngine/Testing/Cases/CommandDispatcher.cpp` (verb registered at `Api/CommandDispatcher.cpp:34`).

### 6. Normalized pointer API on MouseInput (+ GLFW implementation)

- **What it does**: The `MouseInput` interface gains two pure virtuals: `normalized()` - pointer position in [0,1]^2 of the active output, y-down - and `hasPointer()` - whether a pointer position is known at all (`src/WallpaperEngine/Input/MouseInput.h:33-35`). GLFW's implementation (`src/WallpaperEngine/Input/Drivers/GLFWMouseInput.cpp:35-52`) undoes the y-flip its `update()` applies, divides by framebuffer size, clamps to [0,1], and returns nullopt (-> `hasPointer()==false`, `normalized()=={0.5,0.5}`) when mouse input is disabled or the framebuffer is degenerate.
- **Where it lives**: modified `MouseInput.h`, `GLFWMouseInput.{h,cpp}` (`resolveNormalized` helper, `GLFWMouseInput.h:45`); test double updated at `src/WallpaperEngine/Testing/Input/TestingMouseInput.{h,cpp}` (`setNormalized`, default `{0.5,0.5}`).
- **Surface**: none directly; consumers below. `settings.mouse.enabled` (upstream `--mouse-enable`/socket `set-mouse`, `WallpaperApplication.cpp:1480-1487`) gates `hasPointer`.
- **Coupling**: adding pure virtuals to upstream `MouseInput` breaks every subclass - the fork updates all three (GLFW, Wayland, Testing). Consumers are `CScene::updateMouse` (`src/WallpaperEngine/Render/Wallpapers/CScene.cpp:1383`) and `CWeb::updateMouse` (`src/WallpaperEngine/Render/Wallpapers/CWeb.cpp:201-202`). Moderate: an upstream porter must take the interface change plus one implementation per driver they keep.

### 7. Wayland pointer: global-layout coordinates + frozen-pointer semantics

- **What it does**: Rewrites `WaylandMouseInput` coordinate math. `update()` now stores positions in **global scaled layout coordinates** (surface-local `mousePos` + `globalPosition * scale`) instead of surface-local, fixing non-origin monitors (`src/WallpaperEngine/Input/Drivers/WaylandMouseInput.cpp:23-31`); the Hyprland-IPC fallback path likewise adds `globalPosition` and drops the old y-flip (`:53-59`). `position()` (`:67-99`) returns global coords for the focused viewport, falls back to the last IPC-tracked position, then to the viewport's last hover, then to the viewport **center** when the mouse is disabled (previously `{0,0}`). `resolveNormalized()` (`:107-141`) implements the [0,1]^2 contract from either the focused viewport or the IPC-tracked global position; mouse disabled -> nullopt. Click getters now read the focused viewport instead of "active output viewport" (`leftClick`/`rightClick`, fork `:217-224,231-237`).
- **Where it lives**: modified `src/WallpaperEngine/Input/Drivers/WaylandMouseInput.{h,cpp}`. Uses the pre-existing upstream `OutputViewport::globalPosition` field (`src/WallpaperEngine/Render/Drivers/Output/OutputViewport.h:16` - present at b016d7d, verified).
- **Surface**: none new; same `settings.mouse.enabled` gate. (Upstream already had the Hyprland IPC query; fork reuses it.)
- **Coupling**: contained to the Wayland driver files, but behaviorally entangled with capability 6 (same interface) and with `CScene::updateMouse` consuming normalized coords. The global-coords fix alone would lift as ~3 hunks if upstream's consumers still subtract origins - note the fork comment at `WaylandMouseInput.cpp:26-28` says `CScene::updateMouse` subtracts the rendered output's layout origin, so the position() contract and the consumer changed together; a porter must take both sides or neither.
- **Tests**: `Testing/Cases/MouseCoordinates.cpp` is upstream's and its fork diff is cosmetic only (one comment ASCII-fied, 4 diff lines). The behavioral coverage is in `PointerMoveGate.cpp` below.

### 8. PointerMoveGate - dedup/unknown-pointer gate for web wallpapers

- **What it does**: New per-`CWeb` class (`src/WallpaperEngine/Input/PointerMoveGate.{h,cpp}`). `update(optional<normalized>, w, h)` scales to pixel coords and returns `send=false` when the pointer is unknown or size is degenerate - crucially without touching `m_lastSent`, so an unknown stretch can't poison dedup (`PointerMoveGate.cpp:12-16`). Identical successive positions are suppressed (`:24-28`); the first known sample always sends, even at (0,0). `CWeb::updateMouse` (`src/WallpaperEngine/Render/Wallpapers/CWeb.cpp:198-255`) sends `mouseMove` only when the gate says so, and aims edge-triggered clicks at the last-sent position rather than a fabricated center (`:214-220`). Counters (`movesSent/Suppressed/Unknown`) feed a 1s-interval debug log.
- **Where it lives**: new `PointerMoveGate.{h,cpp}`; consumer `CWeb.cpp:198-255` + member `CWeb.h:63`. CMake `CMakeLists.txt:364-365`.
- **Surface**: env `LWE_MOUSEDBG` (read once, `CWeb.cpp:19`; also gates compositor-side pointer logging in `src/WallpaperEngine/Render/Drivers/WaylandOpenGLDriver.cpp:28-42`).
- **Coupling**: the gate class is fully self-contained; wiring it needs capability 6's interface plus the `CWeb::updateMouse` rewrite.
- **Tests**: `src/WallpaperEngine/Testing/Cases/PointerMoveGate.cpp` - seven cases: first-sample send, origin send, motion tracking, unknown sends nothing, unknown doesn't poison dedup, degenerate size treated as unknown, counter reset semantics (CMake `CMakeLists.txt:591`).

### 9. MPV playback: hwdec default off, resource caps, speed, GL state hygiene

- **What it does**: Four changes in `GLPlayer`. (a) `hwdec` default changed from upstream `"auto"` to `"no"`, env-overridable (`src/WallpaperEngine/VideoPlayback/MPV/GLPlayer.cpp:268-269`); `hwdec-extra-frames` capped at 2 (libmpv default 6) to cut NVDEC VRAM (`:272-273`). (b) Demuxer cache capped: `demuxer-max-bytes` 48MiB, `demuxer-max-back-bytes` 16MiB (`:241-255`); `vd-lavc-threads` capped at min(4, cores) (`:256-261`). (c) New `setSpeed(double)` clamps to mpv's 0.01..100 and applies pre-init or live (`GLPlayer.cpp:110-116,277`; member `m_speed`, `GLPlayer.h:73`); reached via new fork-only virtual chain `WallpaperApplication::setTimescale` (`WallpaperApplication.cpp:2936-2944`) -> `RenderContext::setPlaybackSpeed` (`RenderContext.cpp:78-81`) -> `CVideo::setPlaybackSpeed` (`CVideo.cpp:64`). (d) GL state fixes: `glColorMask(true,true,true,true)` before mpv render and before clear, and clear-color save/restore around a transparent-black clear (`GLPlayer.cpp:186,212-217`).
- **Where it lives**: modified `src/WallpaperEngine/VideoPlayback/MPV/GLPlayer.{h,cpp}`; speed call chain in `CWallpaper.h:63`, `RenderContext.{h,cpp}`, `CVideo.{h,cpp}`, `WallpaperApplication.cpp`.
- **Surface**: env `LWE_HWDEC` (any mpv hwdec string; default "no", `GLPlayer.cpp:268-269`), `LWE_MPV_EXTRA_FRAMES` (`:272-273`), `LWE_MPV_DEMUX_MB` (positive int, default 48, `:241-250`), `LWE_MPV_THREADS` (`:256-261`); socket verb `set-speed {speed:0..20}` (`WallpaperApplication.cpp:1442-1448`) and env `LWE_TIMESCALE` (`WallpaperApplication.cpp:133-138`).
- **Coupling**: (a), (b), (d) are self-contained inside GLPlayer - clean cherry-picks. (c) requires the four-class `setPlaybackSpeed` virtual chain plus the timescale machinery (timescale itself is covered elsewhere).
- **Tests**: none for GLPlayer.

### 10. Fullscreen policy model: Off / Pause / Stop + live ignore-list

- **What it does**: Upstream's boolean `pauseOnFullscreen` is superseded at runtime by a new tri-state enum `FullscreenBehavior { Off, Pause, Stop }` (`src/WallpaperEngine/Application/ApplicationContext.h:22-37`; settings field `:145`, default Pause `:234`). Pause = freeze + `setPause(true)`; the pause edges are evaluated in `render()` at `:1123-1129` and `:1225-1235`, now keyed on `fullscreenBehavior == Pause` instead of upstream's bare `anythingFullscreen()`. Stop = release the outputs entirely (surfaces torn down) via the output release/acquire machinery, once per edge, in `tickFullscreenGate()` (`WallpaperApplication.cpp:2845-2875`). The Wayland detector gains `recomputeRelevance()`: a registry of all live toplevels (`WaylandFullScreenDetector.cpp:57-64`, insert/erase at `:170,:199`) is re-counted on demand after a roundtrip (`:282-289`), so live-editing the app-id ignore list takes effect immediately instead of on the next window event; base-class virtual added at `FullScreenDetector.h:28` / stub `FullScreenDetector.cpp:18`.
- **Where it lives**: modified `ApplicationContext.{h,cpp}`, `Render/Drivers/Detectors/FullScreenDetector.{h,cpp}`, `WaylandFullScreenDetector.{h,cpp}`, `WallpaperApplication.cpp` (policy application, verbs, show args). The relevance predicate itself (`isRelevant` with `pauseOnFullscreenOnlyWhenActive` + `fullscreenPauseIgnoreAppIds`) is upstream-unchanged.
- **Surface**: CLI `--no-fullscreen-pause` now also writes `fullscreenBehavior=Off` (`ApplicationContext.cpp:498-504`); `--daemon` defaults Stop, overridden by persisted state and verbs (`:545-563`); upstream `--fullscreen-pause-only-active` and `--fullscreen-pause-ignore-appid` unchanged. Socket verbs: `set-fullscreen {behavior:"off"|"pause"|"stop"}` (`WallpaperApplication.cpp:1603-1623`; validated in `Api/CommandDispatcher.cpp`), `set-fullscreen-ignore {app_ids:[...<=128]}` (`WallpaperApplication.cpp:1583-1601`; triggers `recomputeRelevance()` at `:1593`); `show` args `fullscreen_behavior` / legacy bool `fullscreen_pause` (`WallpaperApplication.cpp:2046-2058`); status keys `fullscreen_behavior`, `fullscreen_pause` (`:1807-1812`). Signal SIGUSR2 and verbs `pause`/`resume` share the same pause path via `m_manualPauseRequested` (`WallpaperApplication.cpp:1423-1429,2958-2970`).
- **Coupling**: deeply woven into `WallpaperApplication` (render loop, output release machinery, show-args, status). The detector-side `recomputeRelevance` (two detector files + one call site) lifts alone. The Pause/Stop split cannot be lifted without the fork's release plumbing (`apiReleaseOutputs`/`ReleaseReason` - owned by the socket/output-lifecycle area).
- **Tests**: `src/WallpaperEngine/Testing/Cases/CommandDispatcher.cpp:255-257,266-267` (valid forms) and `:285-288,300-303` (rejection forms) cover the two verbs' wire validation, not the runtime behavior.

---

**Area summary (5 lines):**
The fork makes audio lazy: a new NullAudioDriver plus a re-bindable `AudioContext::setDriver` means no SDL device is opened until a shown wallpaper actually has sound, and the PulseAudio recorder is likewise upgraded only for audio-reactive projects (`AudioContext.cpp:24`, `NullAudioDriver.cpp`, `WallpaperApplication.cpp:365-416,911-929`). Audio correctness fixes include per-object authored volume mixed in SDL (`SDLAudioDriver.cpp:60-66`, kill switch `LWE_NOOBJVOL`), an output-channel resample fix and a static->per-instance packet cursor in `AudioStream.cpp:496-518,598`, plus a runtime audio-processing toggle that decays FFT bands to zero and a live `audio_gain` dial (`LWE_AUDIOGAIN` / `set-tuning`). Input gains a normalized, presence-aware pointer API (`MouseInput.h:33-35`) with a Wayland global-layout coordinate fix for multi-monitor, and a new self-contained `PointerMoveGate` dedups/suppresses move events to web wallpapers (7 test cases). MPV playback defaults to software decode with capped demuxer/thread/VRAM footprint (env-tunable: `LWE_HWDEC`, `LWE_MPV_DEMUX_MB`, `LWE_MPV_THREADS`, `LWE_MPV_EXTRA_FRAMES`) and supports live playback speed through a new `setPlaybackSpeed` virtual chain. Fullscreen handling becomes a tri-state Off/Pause/Stop policy (`ApplicationContext.h:22-37`): Pause freezes and optionally evicts VRAM, Stop releases outputs entirely, and the ignore list is live-editable via `set-fullscreen-ignore` with detector recount (`WaylandFullScreenDetector.cpp:282-289`) - but the policy is deeply woven into `WallpaperApplication`'s render loop and output-release machinery, so only the detector recount and the env-tunable MPV/audio/input pieces are clean cherry-picks.

---

# 8. The complete switch and surface map


Diffing both files against upstream shows exactly **three new flags**, one changed flag action, and changed parse/signal behavior. Everything else in the arg parser (`--render-debug`, `--layer`, `--screen-span`, `--fullscreen-pause-only-active`, `--fullscreen-pause-ignore-appid`, `--playlist`, `--scaling`, `--clamp`, ...) already exists upstream - verified by greping the upstream file.

| Flag | Read/defined at | Effect (from code) | Default |
|---|---|---|---|
| `--api-socket` | `ApplicationContext.cpp:547-550` | `.flag()`; sets `settings.general.apiSocket = true` (field default `false`, `ApplicationContext.h:218`). Gate checked at `WallpaperApplication.cpp:1264`. | off |
| `--daemon` | `ApplicationContext.cpp:552-559` | Sets `daemonMode = true`, implies `apiSocket = true`, forces `fullscreenBehavior = Off`. Also relaxes the "at least one background required" throw at `ApplicationContext.cpp:692`. | off |
| `--properties-file <path>` | `ApplicationContext.cpp:586-589` | Stores path in `settings.general.propertiesFile` (declared `ApplicationContext.h:110`; default `""` via `ApplicationContext.cpp:585`). Re-read on SIGUSR1 by `WallpaperApplication::checkPropertyReload` (`WallpaperApplication.cpp:2998-3017`). | `""` (disabled) |
| `--no-fullscreen-pause` (modified) | `ApplicationContext.cpp:498-504` | Upstream only cleared `pauseOnFullscreen`; fork additionally sets `fullscreenBehavior = FullscreenBehavior::Off`. | behavior default `Pause` (`ApplicationContext.h:234`) |

Other entry-point changes (no new flags, but surface-relevant):

- `ApplicationContext.cpp:688-692`: unknown arguments are now collected from `parse_known_args` and each logged with `sLog.error ("Ignoring unrecognized command line argument: ...")`; upstream discarded them silently.
- `main.cpp:27-52,69`: fork installs a `sigaction` crash handler for SIGSEGV/SIGILL/SIGBUS/SIGFPE/SIGABRT that writes a backtrace to stderr, then re-raises with the default handler. Installed before anything else, so CEF helper subprocesses get it too.
- `main.cpp:60-65`: under glibc, `mallopt(M_ARENA_MAX, 2)` and `mallopt(M_MMAP_THRESHOLD, 128*1024)` at startup.
- `main.cpp:71`: `SpawnGate::captureAtStartup()` runs before `initLogging`.
- `main.cpp:74` vs upstream: upstream suppressed logging for `--type=zygote`/`--type=utility` CEF subprocesses; the fork deleted that scan and always calls `initLogging()`.
- `main.cpp:91-92`: fork registers SIGUSR1/SIGUSR2 to `app->signal()`; upstream registered SIGKILL (a no-op, uncatchable) - removed. Handlers: SIGUSR1 -> property reload (`WallpaperApplication.cpp:2958-2970`), SIGUSR2 -> manual pause toggle (`2544-2548`).

**Coupling**: confined to `main.cpp` and `ApplicationContext.{h,cpp}` (new fields `apiSocket`, `daemonMode`, `propertiesFile`, `fullscreenBehavior` in `ApplicationContext.h:104-110,145`); a porter needs the two files plus the `FullscreenBehavior` enum. Self-contained.

---

### 2. Environment variables (all reads in `src/`, excluding `src/External`)

Upstream read only `HOME`, `XDG_SESSION_TYPE`, `HYPRLAND_INSTANCE_SIGNATURE`, `XDG_RUNTIME_DIR`, `XCURSOR_SIZE`, `XCURSOR_THEME` (verified by greping upstream). **Every `LWE_*` variable below is fork-introduced**, as are the new `XDG_DATA_HOME`/`XDG_RUNTIME_DIR` read sites noted. Presence-checks mean "any value, even empty, enables" unless noted.

**API / daemon / web-helper surface**

| Variable | Read at | Gates | Default when unset |
|---|---|---|---|
| `LWE_SOCKET` | `Api/CommandServer.cpp:73` | Overrides the command-socket path | `$XDG_RUNTIME_DIR/lwe/engine.sock` (`:77`), else `/tmp/lwe-<euid>/engine.sock` (`:83`) |
| `LWE_WEB_SOCKET` | `WebHelper/SpawnConfig.cpp:106` | Overrides the web-helper socket path | `$XDG_RUNTIME_DIR/lwe/web-<pid>.sock` (`:110-111`), else `/tmp/lwe-<euid>/...` (`:116`) |
| `LWE_WEB_CRASHGUARD` | `WebHelper/HelperClient.cpp:63` (parse 63-97) | Helper crash-loop guard `<deaths>,<windowMs>,<cooldownMs>`; all three or ignored with error | `deaths=4, windowMs=60000, cooldownMs=30000` (`HelperClient.h:47-49`) |
| `LWE_WEB_IDLE_EXIT_MS` | `WebHelper/Service/HelperServer.cpp:23` | Helper self-exit grace after last instance; read once | `DEFAULT_IDLE_EXIT_MS = 1000` (`HelperServer.h:30`) |
| `LWE_CEFLOG` | `WebBrowser/WebBrowserContext.cpp:102` | CEF log severity: `verbose`/`info`/`error` | `LOGSEVERITY_WARNING` (`:101`) |
| `LWE_CEFDEBUG` | `WebBrowser/WebBrowserContext.cpp:112` | CEF remote-debugging port (1-65535) | off |
| `LWE_DEADMAN` | `Application/WallpaperApplication.cpp:151` | Dead-man seconds (0-86400) before outputs release with no ping/frames (`tickDeadman`, `:2360-2373`) | `m_deadmanSeconds = 300` (`WallpaperApplication.h:341`) |

These four helper/CEF vars reach the helper via inherited `environ` in `posix_spawn` (`WebHelper/SpawnGate.cpp:154-156`); no switch encodes them.

**Live tuning dials** (all read once into globals / constructor)

| Variable | Read at | Gates | Default |
|---|---|---|---|
| `LWE_CLASSICK` | `WallpaperApplication.cpp:67` | Classic-light divisor `g_LweClassicDivisor`, clamp 0.01-1000 | 16.0 |
| `LWE_CLASSICEXP` | `WallpaperApplication.cpp:66` | Falloff exponent `g_LweFalloffExp`, clamp 0.5-6 | 2.0 |
| `LWE_AUDIOGAIN` | `WallpaperApplication.cpp:67` | `g_LweAudioGain`, clamp 0.1-20 | 1.0 |
| `LWE_CC` | `WallpaperApplication.cpp:124` | `"b c s h"` floats -> `setColorCorrection` (clamped 0-4/+/-6.4, `:2470-2473`) | `{1,1,1,0}` (`WallpaperApplication.h:292`) |
| `LWE_TIMESCALE` | `WallpaperApplication.cpp:132` | Playback timescale >=0 -> `setTimescale` (clamp 0-20, `:2475-2483`) | 1.0 (`WallpaperApplication.h:294`) |

**Behavior kill switches / escape hatches** (change what gets built; launch-scoped)

| Variable | Read at | Gates | Default |
|---|---|---|---|
| `LWE_SHAPES` | `Data/Parsers/ObjectParser.cpp:99` | `"0"` skips shape objects; **default renders them** (`env == nullptr \|\| env != "0"`) | shapes on |
| `LWE_SKIPGATE` | `Render/Wallpapers/CScene.cpp:149` | `"0"` disables the render-debug skip-object list; **default honors it** | gate on |
| `LWE_KILLLIGHT` | `CScene.cpp:822` | Integer light id to drop in `updateLights` | -1 (none) |
| `LWE_NOBLOOM` | `CScene.cpp:339` | Skips bloom object construction | bloom on |
| `LWE_NOPARTICLES` | `CScene.cpp:933` | Skips every particle object | particles on |
| `LWE_NOROPEUVFLIP` | `Render/Shaders/ShaderUnit.cpp:114` | Disables the genericropeparticle V-flip source patch | flip applied |
| `LWE_FORCECOMBO` | `ShaderUnit.cpp:937` | `NAME=value` injected as a combo define into all shader units | off |
| `LWE_BILLBOARD` | `Render/Objects/CParticle.cpp:139` | Collapses short ropetrails (length < 0.35) to the spritetrail path | off |
| `LWE_NOPREWARM` | `CParticle.cpp:350` | Disables the 60 s prewarm sim for `startTime > 0` systems | prewarm on |
| `LWE_ANIMFRACTION` | `CParticle.cpp:544` | `"0"` selects the legacy spritesheet anim clock; any other/unset = new fraction clock | new clock |
| `LWE_NOFOLLOWALPHA` | `CParticle.cpp:743` | Disables child-particle alpha-follow | follow on |
| `LWE_NOSPRITEVFLIP` | `CParticle.cpp:2564` | Sets sprite orientation-up to +Y (disables V flip) | flip (up = -Y) |
| `LWE_NOCHILDRIDE` | `CParticle.cpp:2575` | Treats all child systems as anchored for axis compensation | off |
| `LWE_HIDESTPARENT` | `CParticle.cpp:2674` | Skips rendering spritetrail parents that have children | off |
| `LWE_TRAILMODE` | `CParticle.cpp:2928` | `"exact"` = tau-sampled trail nodes; else span sampling | span sampling |
| `LWE_TINTFIX` | `Render/Objects/CModel.cpp:244` | `"1"` forces combo `TINTMASKALPHA=0`; `"2"` forces a blue `color` constant | off |
| `LWE_SPECFIX` | `CModel.cpp:252` | Forces `roughness=1.0` constant on model pass | off |
| `LWE_FRONTFACE` | `CModel.cpp:324` | `"ccw"` -> `glFrontFace(GL_CCW)` | `GL_CW` |
| `LWE_CROPOFF` | `Render/Objects/CImage.cpp:221` | `"1"`/`"2"` apply +/-cropOffset to image origin (puppet excluded) | off |
| `LWE_NOFBOCOVERAGE` | `CImage.cpp:273` | Presence **disables** coverage-based FBO sizing | coverage on |
| `LWE_NOSCREEN` | `Render/Objects/Effects/CPass.cpp:1271` | Skips registering the `g_Screen` uniform | registered |
| `LWE_SSFACTOR` | `CScene.cpp:29` | Supersample factor for the resolution cap (`CScene.h:44-45`) | 1.0 |
| `LWE_TEXDETAIL` | `Render/MipResidency.cpp:54` | `"full"` disables mip-residency capping | on (`auto`) |
| `LWE_TEXCAP` | `MipResidency.cpp:61` | Test override of cap dimension (>=256) | live output query, fallback 4096 (`:72`) |
| `LWE_MPV_DEMUX_MB` | `VideoPlayback/MPV/GLPlayer.cpp:243` | mpv `demuxer-max-bytes` in MiB | 48 |
| `LWE_MPV_THREADS` | `GLPlayer.cpp:258` | mpv `vd-lavc-threads` | `min(4, cores)` |
| `LWE_HWDEC` | `GLPlayer.cpp:268` | mpv `hwdec` property string | `"no"` |
| `LWE_MPV_EXTRA_FRAMES` | `GLPlayer.cpp:272` | mpv `hwdec-extra-frames` | `"2"` |
| `LWE_WINTITLE` | `Render/Drivers/GLFWOpenGLDriver.cpp:181` | Window title for EXPLICIT_WINDOW mode | `"wallpaperengine"` |
| `LWE_OVERLAY_TEXT` | `Render/OverlayLabel.cpp:210` | Non-empty string draws a GL text overlay each frame | off |
| `LWE_MOUSE_POS` | `CScene.cpp:1364` | `"fx,fy"` pins a synthetic normalized mouse position | off |
| `LWE_NOOBJVOL` | `Render/Objects/CSound.cpp:33` | Ignores authored per-sound volume, plays at 1.0 | authored volume |

**Pure log gates / probes** (all default off; presence enables)

| Variable | Read at | Output |
|---|---|---|
| `LWE_TIMESTATS` | `WallpaperApplication.cpp:1162` | g_Time vs wall-clock drift stats every 5 s |
| `LWE_AUDIT` | `Render/TextureCache.cpp:92`; `ShaderUnit.cpp:880`; `CModel.cpp:328` | Texture-resolve, PBR-mask combo, model GL-state audits |
| `LWE_TEXCACHEDUMP` | `TextureCache.cpp:112` | Eviction-survivor list |
| `LWE_SHADERDUMP` | `Render/Shaders/GLSLContext.cpp:165` | Failing fragment source, line-numbered |
| `LWE_SHADERDUMP_MATCH` | `ShaderUnit.cpp:1012` | Writes final source of matching shaders to `~/.local/state/lwe/shaderdump-*` |
| `LWE_MOUSEDBG` | `Render/Drivers/WaylandOpenGLDriver.cpp:29`; `Render/Wallpapers/CWeb.cpp:19` | Pointer enter/motion and web mouse-trail logging |
| `LWE_EGLDEBUG` | `Render/Drivers/Output/WaylandOutputViewport.cpp:315` | EGL surface vs viewport sizes (4 lines) |
| `LWE_CLEARPROBE` | `CScene.cpp:137,1077` | Clear color/mask readback, ctor + first 3 frames |
| `LWE_LEDGER` | `CScene.cpp:1105` | Per-draw dirty-pixel ledger, first 2 frames |
| `LWE_OBJPROBE` / `LWE_OBJPROBE_FLOAT` | `CScene.cpp:1252,1261` | `"x0 y0 x1 y1 skip"` rect mean readback per object; FLOAT variant reads FP16 raw |
| `LWE_FBDUMP` / `LWE_FBDUMP_FRAME` | `CScene.cpp:1301,1304` | Scene FBO -> `<value>.ppm` at frame 3 and refresh frame (default 150, min 4) |
| `LWE_FBPROFILE` | `CScene.cpp:1334` | 32x32 luminance profile at frame 150 |
| `LWE_FBOPOOL` | `CScene.cpp:1505` | `"0"` disables composite-FBO pooling; **default pools** |
| `LWE_POOL_HWM` | `CScene.cpp:1541` | `"1"` prints pool high-water report |
| `LWE_MIPRESIDENCY_DEBUG` | `MipResidency.cpp:32` | Per-texture cappability verdicts |
| `LWE_FBOALLOC` / `LWE_FBOTRACE` | `Render/FBOProvider.cpp:35,78` | FBO allocation totals / alias trace |
| `LWE_PARTALLOC` | `CParticle.cpp:32` | Particle pool VBO/EBO byte logging |
| `LWE_VELPROBE` | `CParticle.cpp:1454` | Per-spawn velocity log |
| `LWE_SIZEPROBE` | `CParticle.cpp:2711` | `g_LWEAxisComp` uniform readback probe |
| `LWE_IMGDUMP` | `CImage.cpp:257,367,928,1412` | Image rect/texture/pass/MVP dumps |
| `LWE_IMGPROBE` | `CImage.cpp:1218,1282,1319` | Image position/shape/buffer probes |
| `LWE_FBOCOVERAGE` | `CImage.cpp:274` | Logs coverage-driven FBO upsizes |
| `LWE_CAMPROBE` | `Render/Camera.cpp:91`; `CModel.cpp:433`; `Scripting/SceneObject.cpp:61` | Camera eye/center, VP-matrix, scripted-get probes |
| `LWE_CURSORDBG` | `Scripting/ScriptEngine.cpp:709,945`; `CImage.cpp:1473` | Cursor-hook export and hit-quad logging |
| `LWE_SCRIPTDBG` | `ScriptEngine.cpp:608` | Script module register/skip logging |
| `LWE_AUDIOSTATS` | `Scripting/EngineObject.cpp:389` | Audio band averages every 30 ticks |
| `LWE_ANIMSTATS` | `CPass.cpp:367` | GIF/anim frame-advance stats |
| `LWE_PASSPROBE` / `LWE_PASSPROBE_DUMP` | `CPass.cpp:431,786,866` | Pass readback profile by object id or `"final"`; DUMP writes `~/.local/state/lwe/passprobe-post.ppm` |
| `LWE_UNIFVALS` | `CPass.cpp:505,1334` | Uniform value + g_Color trace dumps |
| `LWE_LIGHTDUMP` | `CModel.cpp:259`; `Render/Objects/CText.cpp:549`; `Scripting/ScriptPropertiesObject.cpp:40`; `ScriptEngine.cpp:794`; `CScene.cpp:416,902`; `CPass.cpp:1304` | Bundle of one-shot lighting/text/property/timeline/uniform-resolution dumps |

**Non-`LWE_` reads, fork-new sites**: `XDG_RUNTIME_DIR` at `CommandServer.cpp:77` and `SpawnConfig.cpp:110`; `XDG_DATA_HOME` at `WallpaperApplication.cpp:621,1820` (lwe wallpaper library roots); `HOME` at `WebBrowserContext.cpp:75` (CEF log path), `ShaderUnit.cpp:1014`, `CPass.cpp:870`, `WallpaperApplication.cpp:625,1824`, `Scripting/LocalStorageObject.cpp:174`. **Preserved upstream reads**: `HOME` (`Steam/FileSystem/FileSystem.cpp:24`), `XDG_SESSION_TYPE` (`WallpaperApplication.cpp:866`), `HYPRLAND_INSTANCE_SIGNATURE`+`XDG_RUNTIME_DIR` (`Input/Drivers/WaylandMouseInput.cpp:168-169`), `XCURSOR_SIZE`/`XCURSOR_THEME` (`WaylandOutputViewport.cpp:263-265`).

**Coupling**: each var is read at 1-4 sites, almost all behind `static const` one-shot gates. Individually trivial to cherry-pick or drop; no shared infrastructure except the InstrumentRegistry (below).

**Tests**: `Testing/Cases/InstrumentRegistry.cpp` covers the three registry-backed vars; no other env var has direct test coverage.

---

### 3. Command API verb & argument vocabulary (`src/WallpaperEngine/Api/CommandDispatcher.cpp` - fork-only file)

`KNOWN_VERBS` at `CommandDispatcher.cpp:11-35` (25 verbs):

`status`, `show`, `quit`, `set-skip`, `list-objects`, `rotate-set`, `next`, `prev`, `ping`, `pause`, `resume`, `release-outputs`, `acquire-outputs`, `set-fullscreen`, `set-fullscreen-ignore`, `set-fps`, `set-speed`, `set-volume`, `set-mouse`, `set-audio`, `set-parallax`, `set-particles`, `set-instrument`, `set-tuning`, `set-app-conditions`.

Request envelope: integer `id` required (`:218`), string `cmd` (`:224`), optional object `args` (`:236`); nesting capped at depth 64 pre-parse (`:188-209`); unknown verbs rejected (`:230`).

**`show` args** (validated in `validateShowArgs`, `:36-172`; `id` itself at `:244-252` matching `[A-Za-z0-9_-]{1,64}`, `:175-183`):

| Arg | Type / range | Cited at |
|---|---|---|
| `cc` | `[b,c,s,h]` floats; b/c/s 0-4, hue +/-6.4 | `:37-53` |
| `speed` | number 0-20 | `:55-62` |
| `properties` | object <=64 entries; keys `[A-Za-z0-9_]{1,64}`; values string <=256 chars / number / bool | `:64-88` |
| `scaling` | `stretch\|fit\|fill\|default` | `:90-96` |
| `clamp` | `clamp\|border\|repeat` | `:98-104` |
| `volume` | int 0-128 | `:106-112` |
| `audio_processing`, `mouse`, `automute`, `fullscreen_pause` | booleans | `:114-118` |
| `fullscreen_behavior` | `off\|pause\|stop` | `:122-129` |
| `skip_objects` | <=256 ints, 0-1 000 000 | `:133-145` |
| `skip_effects` | <=64 ints, 0-10 000 000 | `:149-161` |
| `ui_id` | string <=128, opaque echo | `:165-169` |

**`rotate-set`** (`:261-314`): `entries` array <=512 (each = show-args + `id`, re-validated via `validateShowArgs` `:276`), `interval_s` int 15-604800, `order` = `sequential|shuffle|random`, booleans `avoid_repeat`/`enabled`, `label` <=128 chars.

**Other verbs' args**: `set-skip` `ids` <=256 ints 0-1e6 (`:316-330`); `set-fps` int 1-480 (`:332-340`); `set-speed` 0-20 (`:342-350`); `set-tuning` >=1 of `classic_k`/`classic_exp`/`audio_gain`, finite numbers (`:352-370`); `set-volume` int 0-128 (`:372-379`); `set-mouse`/`set-audio`/`set-parallax`/`set-particles` `enabled` bool (`:381-411`); `set-instrument` `name` string + `enabled` bool (`:395-404`); `set-fullscreen-ignore` `app_ids` <=128 non-empty strings <=128 chars (`:413-428`); `set-fullscreen` `behavior` = `off|pause|stop` (`:430-440`). Replies: `accepted`/`done`/`failure` builders `:445-455`.

**Coupling**: `CommandDispatcher.{h,cpp}` + `CommandServer.{h,cpp}` are fork-only and self-contained; execution side lives in `WallpaperApplication.cpp` (`apiShow` `:1871`, `apiRotateSet` `:2123`, `instrumentSet` call `:1516`, status `:1685`) - that handler layer is woven into the app class, so a porter takes the Api/ directory plus the handler block.

**Tests**: `Testing/Cases/CommandDispatcher.cpp`, `Testing/Cases/CommandSocket.cpp` (incl. the `LWE_SOCKET` override case at `CommandSocket.cpp:263-266`).

---

### 4. Instrument registry (`src/WallpaperEngine/Logging/InstrumentRegistry.{h,cpp}` - fork-only)

- **What it is**: a fixed 3-entry table of runtime-toggleable log gates (`InstrumentRegistry.cpp:27-31`): `LWE_PARTSTATS`, `LWE_TWINKLEPROBE`, `LWE_ROPETRAILPROBE`. Each entry is `{name, atomic<bool> on, atomic<uint32> epoch}`; lookup is a lock-free `strcmp` scan (`:33-43`).
- **Semantics**: `instrumentSet` bumps the epoch only on an off->on edge (`:56-69`); `instrumentSeedFromEnv` seeds initial state from getenv and sets epoch 1 for launch-enabled entries (`:71-81`), called once at the top of `WallpaperApplication::show()` (`WallpaperApplication.cpp:2903`). Consumers poll `instrumentOn`/`instrumentEpoch` per frame and reset latched accumulators on epoch change (`CParticle.cpp:410-419`, `2691-2698`, `3003`).
- **Surface**: toggled live via the `set-instrument` socket verb (`WallpaperApplication.cpp:1529`); `instrumentsEnabled()` (`:83-94`) feeds status output. Launch-time env vars of the same names still work via seeding. The header comment (`InstrumentRegistry.cpp:14-16`) and test (`Testing/Cases/InstrumentRegistry.cpp:11-15`) document that build-affecting switches (e.g. `LWE_TEXCOMP`) are deliberately *refused* - `instrumentKnown`/`instrumentSet` return false for unregistered names.
- **Coupling**: fully self-contained module; consumers opt in per call site. Trivially liftable alone.
- **Tests**: `Testing/Cases/InstrumentRegistry.cpp` covers known/unknown names, epoch edges, re-enable no-op, seeding, `instrumentsEnabled` formatting.

---

### 5. Config-file keys

- **`config.json` (Wallpaper Engine's own)**: the fork consumes the **same keys as upstream** - `steamuser` section and playlist entries (`name`, `settings.delay` default 60, `settings.mode` default `"timer"`, `settings.order` default `"sequential"`, `settings.updateonpause`, `settings.videosequence`, `items`) at `ApplicationContext.cpp:88-157`. The only fork change is the parser: `JSON::parse(configFile)` -> `Data::JSON::parseLenient(contents)` with error logged instead of thrown (`ApplicationContext.cpp:78-86`). **No new config.json keys.**
- **`--properties-file`** (fork-new config surface): JSON of shape `{screen: {property: value}}`, re-read on SIGUSR1; per-screen keys are matched against each project's properties, string values used raw, non-strings `dump()`ed, changes pushed via `Property::update(..., User)` and script/web notification (`WallpaperApplication.cpp:2993-3064`).
- **lwe wallpaper library** (fork-new lookup, not a keys file): `$XDG_DATA_HOME/lwe/wallpapers/<id>/project.json` and `~/.local/share/lwe/wallpapers/<id>/project.json`, consulted before the Steam workshop roots (`WallpaperApplication.cpp:1857-1883` resolve, `:615-650` enumerate). `project.json` itself is parsed with `parseLenient` (`:225,435,650`).

**Uncertain**: whether `parseLenient` accepts a formally different grammar than upstream's strict `JSON::parse` (e.g. comments/trailing commas) is defined in `src/WallpaperEngine/Data/JSON.cpp`, which is not examined here - only its call sites. The fork introduces no new *keys*, but the accepted syntax may be broader.

---

### Area summary (5 lines)

The fork adds exactly three CLI flags - `--api-socket`, `--daemon`, `--properties-file` (`ApplicationContext.cpp:547-586`) - plus startup crash-handler/mallopt changes and SIGUSR1/SIGUSR2 handlers in `main.cpp`. It introduces 87 `LWE_*` environment variables (upstream had none): socket-path overrides, web-helper/CEF controls, five live tuning dials, ~30 behavior kill-switches, and ~40 pure log probes. 81 are enumerated in the tables above; the remaining six (the `LWE_TEXCOMP`/`LWE_SRGBALL`/`LWE_SRGBALBEDO` texture gates and the `LWE_UNIFDUMP`/`LWE_BASELEVEL_PROBE`/`LWE_MASKAUDIT` probes) are covered in chapter 3's instrumentation list. The three tuning dials are read via the `lweEnvFloat` clamp helper and the three instrument names via the registry's seed loop; the rest at cited one-shot `getenv` sites. A fork-only unix-socket command API (`Api/CommandDispatcher.cpp:11-35`) exposes 25 verbs with a strictly validated per-`show` property vocabulary (`:36-172`). A 3-entry runtime InstrumentRegistry (`Logging/InstrumentRegistry.cpp:27-31`) makes `LWE_PARTSTATS`/`LWE_TWINKLEPROBE`/`LWE_ROPETRAILPROBE` toggleable via the `set-instrument` verb, seeded from env at startup. Config-file consumption is upstream-identical in keys but parsed leniently, with the fork adding the SIGUSR1-reloaded properties file and the `~/.local/share/lwe/wallpapers` library root.

---

# 9. The control panel (lwe-ui)


**Tree status: `lwe-ui/` is 100% fork-only.** Upstream commit b016d7d has no `lwe-ui` directory (the upstream tree contains no such directory). Nothing upstream was modified by this area; it is a standalone PySide6+QML Python package (`pyproject.toml:24-27` installs `lwe-ui`, `lwe-migrate`, `lwe-discover` console scripts; requires Python >=3.11, PySide6 >=6.6, Pillow). Its coupling to the fork is entirely through the fork's *engine-side* additions - the daemon Unix-socket API, `--daemon`/`--layer`/`--clamp` flags, and `LWE_*` env vars - never through shared source.

---

### Engine daemon API client (`api_client`)
- **What it does**: Stdlib-only Unix-socket client for the engine's command API: one JSON object per line, replies correlated by id, in `$XDG_RUNTIME_DIR/lwe/engine.sock`. Short verbs answer once with `status="done"`; `show` answers `accepted` first, then `done` after the scene load. Every call is one bounded connect-send-read (3 s ack timeout, 30 s for `show` done, 64 KiB reply cap); a dead or absent engine returns `None`, never raises (`api_client.py:23-31`, `57-105`).
- **Where it lives**: fork-only `lwe-ui/src/lwe_ui/api_client.py`.
- **Surface**: env override `LWE_SOCKET` (`api_client.py:36`); default socket path (`api_client.py:39-40`). Full verb surface the panel can drive, each a thin function:
  - `show` with per-wallpaper resolved args: `id`, `cc` [b,c,s,hue_rad], `speed`, `properties` (PROP_ strings), `scaling`, `clamp`, `volume` 0..128, `audio_processing`, `mouse`, `automute`, `fullscreen_pause`, `fullscreen_behavior`, `skip_objects`, `ui_id` identity echo (`api_client.py:108-168`)
  - `status` (`:171`), `rotate-set` (entries = complete show-args, interval clamped 15..604800 s, order, avoid_repeat, enabled, label <=128 chars) (`:180-203`), `next`/`prev` ack-only (`:206-213`), `ping` dead-man heartbeat (`:216-219`)
  - `list-objects` (`:222-234`), `set-skip` (`:237-243`), `set-fps` 1..480 (`:246-252`), `set-speed` 0..20 (`:255-265`), `set-volume` (`:268-275`), `set-mouse` (`:278-280`), `set-audio` (`:283-292`), `set-tuning` partial-update (`:295-308`), `set-parallax` (`:311-313`), `set-particles` (rebuilds scene) (`:316-323`), `set-fullscreen-ignore` substring app_ids (`:326-332`), `set-instrument` (`:335-343`), `set-fullscreen` off|pause|stop (`:346-354`)
  - Raw `release-outputs` / `acquire-outputs` via `request()` in `bench_courier.py:30-47,68-74`.
- **Coupling**: Self-contained module; depends only on the fork's engine daemon API existing. Lift-alone = this file plus an engine that speaks the verbs.
- **Tests**: `lwe-ui/tests/test_api_client.py`, `test_api_shownow.py`.
- **Uncertain**: none.

### App shell: entry point, tray, single instance
- **What it does**: `python -m lwe_ui` -> `app.main()`. Two entry paths, one codebase: `--tray` dispatches to `tray.main` BEFORE any heavy import (`app.py:52-57`) - the resident tray is its own QML-free, theme-stripped process (`tray.py:77`, menu = pause/resume, next, stop/restore outputs, open panel, exit) that opens the full window as a child process and re-raises a live one over the window's single-instance socket (`tray.py:60-75,207-223`). The bare window process ensures config/state dirs, takes the single-instance guard on `ui.sock` (a second launch asks the running window to present itself and exits 0, `app.py:100-111`), builds a `QApplication`, registers theme tokens + ten bridge objects (`backend`, `themeBridge`, `importBridge`, `settingsBridge`, `editor`, `bench`, `deckPopup`, `dev`, `workshop`, `wizardBridge`) as QML context properties (`app.py:128-167`), loads `qml/Main.qml` (`app.py:175-176`), and EXITS when the window closes - the window's memory returns to zero while the tray stays. On startup it reconciles the engine env file (`app.py:188`) and the autostart entry (autostart launches the tray only, `app.py:195`, `models.py:1317`). Both processes name themselves via `PR_SET_NAME` (`proctitle.py`, stdlib ctypes) - `lwe-ui` for the window, `lwe-ui-tray` for the tray - because otherwise both report as `python3` and neither can be found by name in a process monitor; the COMMAND column instead follows argv[0], which a pip install gets right from its console script.
- **Where it lives**: fork-only `app.py`, `tray.py`, `single_instance.py` (QLocalServer on `$XDG_RUNTIME_DIR/lwe/ui.sock`, `single_instance.py:22-24`).
- **Surface**: CLI flag `--tray` (`app.py:52-57`); setting `CLOSE_TO_TRAY` default True (`constants.py:93`); XDG autostart file `~/.config/autostart/lwe-ui.desktop` (launches the tray only) written/reconciled by `Backend` (`models.py:1282-1338`).
- **Coupling**: Self-contained; no upstream contact. Tests: `test_app_load.py`, `test_single_instance.py`, `test_autostart.py`, `test_tray_and_fullscreen.py`.

### Library browser + show-now
- **What it does**: `LibraryModel` rows = union of `tags.csv` ids and `WALLPAPERS_DIR` subdirs, with title/thumb/type from `discovery.project.read`; a sort/filter proxy drives search + favorites (`models.py:1-13, 60-75, 600-618`). `showNow(wid)` re-arms a stopped master service, then sends `show` with the fully resolved per-wallpaper args from `resolve_show_args` plus a follow-up `set-tuning`; a rejected or failed show is an honest False (`models.py:625-654`, resolver at `models.py:205-267`). A periodic status poll sends `ping` (the engine's dead-man heartbeat) + `status`; on FIRST sight of an engine pid per panel life it pushes the panel's stored policy (rotation set, fullscreen behavior, live globals) - engine re-arrivals get nothing, because the engine restores its own persisted state and an automatic re-push would feed its crash-loop guard (`models.py:1740-1806`).
- **Surface**: verbs `show`, `set-tuning`, `ping`, `status`.
- **Coupling**: UI-side only; needs the fork engine's `show`-with-args semantics. Tests: `test_api_shownow.py`, `test_engine_sync.py`, `test_filter_model.py`.

### Rotation playlists + deck transport
- **What it does**: Named playlists stored as shell-sourceable `playlists/<slug>.conf` (NAME/MODE/INTERVAL/UNIT/MEMBERS, schema at `constants.py:147-153`). The active playlist is resolved into complete per-entry show-args and pushed wholesale to the engine via `rotate-set` on every relevant change (`models.py:869-921`); deck next/prev buttons send `next`/`prev` (`models.py:924-955`). Deck animation freeze is `set-speed 0` - deliberately never the engine `pause` verb - and resume restores the resolved rate (`models.py:1005-1046`); session speed dial is `set-speed` 0..10 (`models.py:1056-1082`).
- **Surface**: verbs `rotate-set`, `next`, `prev`, `set-speed`; settings `ROTATION_ENABLED`, `ORDER`, `INTERVAL` 60..7200, `ACTIVE_PLAYLIST` (`constants.py:58-63`); schedule keys exist but `SCHEDULE_UI = False` gates the section out (`constants.py:55`, `settings_bridge.py:87-94`).
- **Coupling**: Self-contained UI + conf files. Tests: `test_playlists.py`, `test_playlist_bridge.py`, `test_playlist_strip_render.py`, `test_deck_states.py`.

### Per-wallpaper editor (realtime autosave)
- **What it does**: `EditorBridge` (QML `editor`) edits one wallpaper's `wp/<id>.conf` in place - no draft/Save; set-ness is key *presence* (choosing "Global" deletes the key) (`editor.py:1-49`). Live-class keys push engine verbs immediately (SPEED->`set-speed`, VOLUME->`set-volume`, AUDIO_REACTIVE->`set-audio`, MOUSE->`set-mouse`, SKIP->`set-skip`, audio dials->`set-tuning`, FPS->`set-fps` clamped 1..480); build-consumed keys (SCALING, AUTOMUTE, CC, PROP_*) trigger a debounced 600 ms re-show - and only when the edited wallpaper is the one on screen (`editor.py:20-28, 628-641, 728-731, 854-927, 1012`). Session marks/revert live in `wp_session.SESSION`. Generated Objects and Scene-properties panels come from the discovery indexes.
- **Surface**: wp conf schema `WP_SCHEMA` (`constants.py:118-138`), `PROP_` prefix (`:139`); dial ranges `editor.py:78-100`.
- **Coupling**: Self-contained; reads `project.json` via pure-Python discovery. Tests: `test_editor.py`, `test_editor_draft.py`, `test_editor_panels.py`, `test_editor_ui.py`, `test_draft.py`, `test_audio_dial_persistence.py`, `test_preset_identity.py`.

### Deck settings popup
- **What it does**: `DeckPopupBridge` (QML `deckPopup`) tracks whatever the engine is currently showing and offers global capsule controls (Speed/Volume/FPS via live verbs, persist-on-confirm) plus this-wallpaper Scaling and scene PROP_ properties (conf write + debounced re-show) (`deck_popup.py:1-33`; verb pushes at `:242,267,312,441,461`).
- **Coupling/Tests**: Self-contained; `test_ab_gestures.py`, `test_combo_close.py` and deck tests cover it.

### Settings surface
- **What it does**: `SettingsBridge` (QML `settingsBridge`) is the store access for the Settings pages: schema-driven validation before write, verb-first/persist-on-confirmation for live keys, and a derived "reach" receipt per key (`settings_bridge.py:1-25, 53-120`). Key classes declared at `settings_bridge.py:40-48`: live (verbs at `:240-256` - `set_speed/volume/audio/mouse/parallax/particles/fps/tuning`), next-show, service-restart (writes env file + `systemctl --user restart lwe-engine.service` at `:619`), boundary, next-scan, re-arm, next-import. Also owns autostart slot (`:381`) and pushes `set-fullscreen-ignore` (`:428`).
- **Surface**: full `SETTINGS_SCHEMA` with types/defaults/ranges at `constants.py:64-128`.
- **Tests**: `test_settings_bridge.py`, `test_settings_ui.py`, `test_engine_sync.py`.

### Engine daemon systemd unit management
- **What it does**: `engine/daemon_unit.py` generates `~/.config/systemd/user/lwe-engine.service` (Type=simple, `ExecStart="<bin>" --daemon $LWE_ENGINE_ARGS`, Restart=always, MemoryHigh=2G/MemoryMax=3G/MemorySwapMax=2G, WantedBy=graphical-session.target) and the env file `~/.config/lwe/engine-env`, then runs `systemctl --user daemon-reload` (`daemon_unit.py:24-53, 309-338`). Launch shape (assets dir, `--screen-root` per detected monitor, `--layer`, hwdec, texcomp, dead-man, audio dials) is resolved from settings into the env file; monitor enumeration tries hyprctl -> kscreen-doctor -> wlr-randr (`:58-119`). Hand-edited foreign env lines are preserved across regenerates (`:178-218`); `reconcile_env()` repairs drift on every panel start without touching the running engine (`:279-310`). Enable/disable is the master switch's job: `Backend.setMaster` runs `systemctl --user enable/disable --now` on `lwe-engine.service` (`models.py:831-866`).
- **Surface**: managed env keys `LWE_ENGINE_ARGS, LWE_HWDEC, LWE_TEXCOMP, LWE_DEADMAN, LWE_NOPAUSEVRAM, LWE_TEXDETAIL, LWE_TEXCAP, LWE_AUDIOGAIN, LWE_CLASSICK, LWE_CLASSICEXP` (`daemon_unit.py:159-163`; `LWE_NOPAUSEVRAM` is owned but never emitted; regeneration drops stale lines); `LWE_DEADMAN=300` hardcoded (`:254`); engine binary lookup ENGINE_BIN -> PATH -> `~/.local/bin` -> source-checkout path (`:122-143`). Unit name `constants.py:10`.
- **Coupling**: Pure file generation + systemctl; lifts alone but is meaningless without the fork engine's `--daemon` mode. Tests: `test_daemon_unit.py`.

### Bench (test-render an item before approving it)
- **What it does**: The bench launches a *separate* non-detached test engine process rendering the item's own conf through the same argv builder live launches use (`bench.py:1-14`, `engine/invocation.py`), while the desktop engine is stood down over the socket: `release-outputs` frees the surfaces + scene VRAM with the daemon still serving, `acquire-outputs` hands them back (`bench_courier.py:21-74`). A release is STATE - it holds until acquire, with the engine's own dead-man reflex as the abnormal-exit backstop - so there is no lease and nothing renews. `BenchBridge` owns the QProcess lifecycle and routes approve/reject through `commit.py` (`bench_bridge.py:1-30`). Test engines are reaped before commit/reject; commit off the GUI thread.
- **Coupling**: UI + `commit.py` gate; depends on fork's `release/acquire-outputs` verbs. Tests: `test_bench.py`, `test_bench_bridge.py`, `test_bench_standdown.py`, `test_bench_fixes.py`, `test_commit.py`, `test_commit_packed_scene.py`.

### Import wizard (human-verdict approval of new items)
- **What it does**: `WizardBridge` (QML `wizardBridge`) runs a phase machine for each pending Workshop item: static census -> pause live wallpaper -> launch a *windowed* test engine -> watch for `LWE-PRESENT`/fatal lines via a `BenchSession` -> human clicks pass/fixable/fail; terminal actions write records events and graduate or trash the item (`wizard_bridge.py:1-18, 36-73`). Crash-vs-close keys off "did it present a frame", not exit signal. Uses `bench_courier` standdown and `texcomp` scanning/encoding.
- **Tests**: `test_wizard.py`, `test_wizard_bridge.py`, `test_bench_verdict.py`.

### Workshop scope + import pipeline
- **What it does**: `WorkshopBridge` (QML `workshop`) handles steam:// handler detection, Workshop deep links, pending-item tiles, and the trash chain (deletion record + copy-mode delete + unsubscribe link) (`workshop.py:1-12, 26-36`). The mechanical import (`storage/importer.py`) scans `WORKSHOP_DIR` (native or Flatpak Steam, auto-detected `paths.py:184-200`), type-classifies, derives color correction, resolves thumbnails, dedups; `REVIEW_REQUIRED` decides review-vs-good landing tag, `STORAGE_POLICY` copy-vs-reference (`importer.py:1-24`). Hand-added folders go to `~/.local/share/lwe/manual` as a second pending source (`paths.py:156-164`). Steam Web API browse constants exist (`constants.py:245-249`).
- **Surface**: settings `WORKSHOP_DIR`, `STEAM_DIR`, `REVIEW_REQUIRED`, `STORAGE_POLICY`, `DETECT_MODE`/`DETECT_INTERVAL_SEC` (`constants.py:94-104`); `discover.json` holds apiKey/acquireMethod defaults (`constants.py:156`).
- **Tests**: `test_importer.py`, `test_discovery.py`, `test_workshop_*.py`, `test_tombstone_manager.py`.

### Commit gate + records
- **What it does**: `commit.commit` is the single lifecycle-advance function: first-commit copies `{project.json, payload, preview.*}` (never `shaders/`) atomically into the library, rewrites conf BG, tags good, builds indexes, signals reload + bench-resume; re-commit is a no-op promote; `reject` tags bad (`commit.py:1-27, 40-45`). Per-wallpaper lifecycle events live in append-only `state/records/<wid>.jsonl` (`paths.py:115-122`), migrated from a legacy tombstones.json (`workshop.py:50-55`).
- **Tests**: `test_commit.py`, `test_records.py`, `test_records_view.py`, `test_journal_and_toggles.py`.

### Fullscreen + running-apps policy (engine-owned; the panel only pushes it)
- **What it does**: Both display policies live IN the engine now - see the engine
  chapters ("Fullscreen policy" and "Running-apps condition (engine-side)"). The
  panel's whole role is configuration: it pushes `set-fullscreen` /
  `set-fullscreen-ignore` / `set-app-conditions` on settings commits, and the
  engine detects, acts, and persists on its own. The old UI-side watcher
  processes are gone.
- **Surface**: settings `FULLSCREEN_BEHAVIOR` (off/pause/stop), `APP_CONDITION_BEHAVIOR` (`constants.py`); config files `app-condition.txt`, `pause-blacklist.txt` fullscreen-ignore app_ids, mtime-watched and pushed via `set-fullscreen-ignore` (`models.py:1476,1810-1819`).
- **Tests**: `test_tray_and_fullscreen.py`.

### Developer cockpit
- **What it does**: `DevBridge` (QML `dev`) is the parity/debug surface: launches probe engines with env instruments and A/B fix toggles (six toggles, e.g. `LWE_FRONTFACE`, `LWE_TEXCOMP`, `LWE_SHAPES` - `dev.py:31-45`), render-debug flags (`:50-54`), log instruments with per-tag streaming readout (`:56-87`), live object isolation via `list-objects` + `set-skip` on the running engine (`:360-476`), live instrument toggles via `set-instrument` (`:494-508`), pending env-line edits for the engine-env (`:580-608`), and a verdict/history log. Participates in a one-engine-at-a-time conflict gate with bench/wizard (`app.py:130-144`).
- **Tests**: `test_dev_bridge.py`, `test_dev_readout.py`, `test_dev_engine_readout.py`, `test_dev_view_ui.py`, `test_isolator_*.py`.

### Engine invocation builder + discovery/migration utilities
- **What it does**: `engine/invocation.py` is the single definition of engine argv/env for mirror rendering and `--window` preview: flag order documented and built from `constants.ENGINE_FLAGS` (`--scaling` before `--screen-root` list, `--clamp` not `--clamping`, `--bg` last, env `LWE_CC`/`LWE_TIMESCALE`) (`invocation.py:1-30, 116-189`; flags table `constants.py:234-259`). `discover_cli.py` (`lwe-discover`) rebuilds per-wallpaper object/property indexes in pure Python (state/objindex, state/propindex); `migrate.py` (`lwe-migrate`) converts legacy `wallpapers.tsv`+`tags.csv` into the new conf layout with dry-run (`migrate.py:1-19`).
- **Tests**: `test_invocation.py`, `test_discovery.py`, `test_migrate.py`, `test_texcomp.py`, `test_storage.py`, `test_light_mode.py`, `test_theme_bridge.py`, `test_themes.py`.

---

### Panel-read environment variables

Two `LWE_*` variables are read by the panel itself (not the engine): `LWE_BC7ENC`
(`texcomp.py:74`) overrides the path to the `lwe_bc7enc` encoder shim, and
`LWE_DEV_MONITOR` (`dev.py:664-666`) pins one monitor for the dev area's windowed engine
launch (empty = refuse). The panel also *writes* engine-side vars into the generated
`engine-env` file - see "Engine daemon systemd unit management" above.

### On-disk layout (all from `storage/paths.py` unless noted)
- `~/.config/lwe/` (XDG_CONFIG_HOME): `settings.conf` (shell-sourceable Tier A, schema `constants.py:57-116`), `tags.csv`, `wp/<id>.conf` (per-wallpaper, schema `constants.py:118-138`), `playlists/<slug>.conf`, `meta.json`, `discover.json`, `theme.json`, `engine-env` (generated, `daemon_unit.py:26`), `pause-blacklist.txt`, `app-condition.txt`, `legacy/` (tombstones).
- `~/.local/state/lwe/`: `objindex/`, `propindex/`, `records/<wid>.jsonl`, `draft/`, `wallpaper.log`, `playlist-manual-hold` (`paths.py:83-137`).
- `~/.local/share/lwe/`: `assets/`, `wallpapers/` (library), `manual/` (hand-added pending) (`paths.py:148-164`).
- `~/.config/systemd/user/lwe-engine.service` (`daemon_unit.py:309-338`); `~/.config/autostart/lwe-ui.desktop` (`models.py:1282-1338`).
- Sockets: `$XDG_RUNTIME_DIR/lwe/engine.sock` (engine API, `LWE_SOCKET` override) and `$XDG_RUNTIME_DIR/lwe/ui.sock` (single-instance).
- systemd user units touched: **`lwe-engine.service`** (written, enabled/disabled, restarted, stopped/started).

### Tests note
The panel has no C++ coverage under `src/WallpaperEngine/Testing/Cases/` (those cases are engine-side). Its coverage is the 60+ script suites in `lwe-ui/tests/test_*.py`, run offscreen per `README.md:29-32` (README usage confirmed by test filenames; harness claim itself is README prose, not verified by me).

### 5-line summary
`lwe-ui/` is an entirely fork-added PySide6+QML control panel (`python -m lwe_ui`) that manages a Wallpaper Engine library: browsing, playlists/rotation, per-wallpaper settings with realtime autosave, a test-bench + import wizard for new Workshop items, and a developer debug cockpit. It runs as two processes through one entry point: `--tray` is a small resident tray (icon, quick actions, launcher; no QML - what autostart runs), and the bare command is the full window, which exits on close and returns its memory; the repurposed "minimize to tray when closed" setting decides whether a window close leaves the tray running. It drives the fork engine live over a JSON-lines Unix socket (`$XDG_RUNTIME_DIR/lwe/engine.sock`: `show`, `rotate-set`, `set-speed/fps/volume/skip/tuning/instrument/fullscreen/app-conditions...`). It owns the `lwe-engine.service` systemd user unit and its generated env file, and persists everything under `~/.config/lwe/` (shell-sourceable confs) plus state/data dirs. Coupling to upstream code is zero - it is a separate package whose only dependency is the fork's engine-side daemon API and flags, so cherry-picking it requires the engine daemon work first. Coverage is via ~60 Python test scripts in `lwe-ui/tests/`, not the C++ test tree.

---

# 10. Cherry-pick guide

Ranked by what a porter would actually have to take, using the coupling assessments from
each chapter. "Self-contained" means new files plus at most a few call-site lines.

## Tier 1 - clean lifts (self-contained; hours, not days)

| Capability | Take | Notes |
|---|---|---|
| builtins.js script library (Vec2-4/Mat3/Mat4, guarded fallbacks) | `Scripting/resources/builtins.js` | zero native coupling; build machinery already upstream |
| BinaryParser hardening (fail-fast reads, seek bounds, package/texture caps) | `BinaryReader.*`, `MemoryStream.h`, `PackageParser` + `TextureParser` hunks, `Testing/Cases/BinaryParserHardening.cpp` | same public API; test suite included |
| Lenient JSON (`parseLenient`, comment/trailing-comma tolerance, numeric-string coercion, exception-safe optionals) | `Data/JSON.{cpp,h}` + one-line call-site swaps | changes parse semantics engine-wide (silent defaulting) - port deliberately |
| Directory-adapter containment fix | `FileSystem/Adapters/Directory.cpp` `within()` | security fix; upstream's prefix check matches sibling dirs |
| Case-insensitive package index | `FileSystem/Adapters/Package.{cpp,h}` | fixes Windows-authored case drift |
| AlbumTexture readback crash fix (glGetnTexImage -> glGetTexImage) | `Render/AlbumTexture.cpp:69-88` | one hunk; fixes a NULL-function-pointer segfault |
| Per-object sound volume | `CSound.cpp:33-35`, `AudioStream` volume member, `SDLAudioDriver.cpp:60-66` | 4 files, one call chain |
| MPV resource caps (demuxer/thread/hwdec defaults) | `VideoPlayback/MPV/GLPlayer.cpp:241-273` | self-contained; cuts VRAM with NVDEC |
| PointerMoveGate (dedup/unknown-pointer gate) | `Input/PointerMoveGate.*` + its test | class lifts alone; wiring needs the normalized-pointer interface |
| NullAudioDriver + lazy audio bring-up | `Audio/Drivers/NullAudioDriver.*`, `AudioContext` reference->pointer diff, `WallpaperApplication::setupAudio/ensureAudioForProject` | no SDL device until a shown wallpaper has sound |
| InstrumentRegistry (runtime-toggleable log gates) | `Logging/InstrumentRegistry.*` + test | consumers opt in per call site |
| OverlayLabel debug overlay | `Render/OverlayLabel.*` + one call line in `CWallpaper::render` | self-contained (FreeType is already linked upstream) |
| Steam workshop multi-root discovery | `Steam/FileSystem/FileSystem.cpp:70-84` | one function, one call site |
| Texture-cache sole-owner eviction | `TextureCache::evictUnused` + its wrapper's two call sites | trivial |
| Sha256 utility | `Data/Utils/Sha256.h` | header-only, stated to match `hashlib.sha256` |
| Script-engine correctness fixes (dead `setInterval`/`setTimeout`, unwired layer `get_property`, vector write-back y-check + z/w conversions, ScriptPropertiesObject ref-count/exception shapes) | `EngineObject`, `ScriptableObjectAdapter`, `VectorAdapter`, `ScriptPropertiesObject` - per-file hunks | arguably pure upstream bug fixes; each file's hunks lift independently |
| Wayland fullscreen-detector live recount | `WaylandFullScreenDetector::recomputeRelevance` + base virtual + one call site | makes ignore-list edits immediate |
| `web-frame-probe` verification tool | `tools/web-frame-probe.cpp` | links only the engine lib; needs the WebHelper stack to probe |

## Tier 2 - a subsystem at a time (one coherent bundle each)

| Bundle | Contents | Why it's a bundle |
|---|---|---|
| Command socket pair | `Api/CommandServer.*` + `Api/CommandDispatcher.*` | fully self-contained transport+validation - but useless without the WallpaperApplication handlers (Tier 3) |
| Wayland poll loop + keepalive + DPMS safety | `WaylandOpenGLDriver::dispatchEventQueue`, `WaylandOutputViewport` framePending/swapInterval, `WallpaperApplication` render-stamp | replaces upstream's blocking dispatch; needs `getApiWakeFds` hook or a stub |
| Output hotplug/hot-unplug | `handleGlobalRemoved`/`onLayerClose`/`onOutputAnnounced` blocks | moderate; independent of the socket |
| Camera corrections (perspective projection, zoom-corrected ortho, scripted view, screen projection) | `Render/Camera.*` + consumer hunks in CImage/CText/CModel | changes every 2D scene's framing; not drop-in without the canvas/view split |
| FBO pooling + coverage sizing + clampToCap | CScene pool methods, `clampToCap`/`largestOutputSize`, CWallpaper virtual, CImage coverage block | safe to drop pooling alone (legacy fallback branch), but coverage sizing is woven through FBO creation |
| HDR bloom ladder | CScene ctor block, CFBO RGBA16F, CWallpaper `m_sceneFormat`, AssetLocator shader injection, Wallpaper data fields | the ladder excises as a unit if the data fields come along |
| Mip-residency cap + demand expansion | `Render/MipResidency.*`, TextureCache 4-line block, CTexture startLevel/rebase (both upload paths), one CImage hook | needs retained LZ4 mip payloads (data-layer change) |
| BC7 texcache ingest | `tools/texcomp/lwe_bc7enc.cpp`, `CTexture::uploadFromTexcache`, CMake block; vendored ISPCTextureCompressor (MIT) | x86-64 only (ispc); cache producer lives in lwe-ui |
| Puppet skeletal animation | `PuppetModel.*` + the CImage puppet block | replaces upstream's static puppet loader |
| 3D model objects | `CModel.*`, `ModelObject` data struct + parser entry, CScene factory branch | lit models also need the lighting stack (Tier 3) |
| Text object fixes (UTF-8, alignment, width limits, placement) | `CText.*` + `Camera::getScreenProjection` | self-contained otherwise |
| Script cursor events | ScriptEngine hook scan + dispatch + `CImage::cursorLocalPosition` | only image layers report hits |
| Persistent localStorage | `Scripting/LocalStorageObject.*` + three ScriptEngine wiring points | keep install order: builtins first, native overwrite |
| Web stack (three-binary CEF split) | all of `WebHelper/`, the two new mains, rewritten `WebBrowser/` classes, `CWeb.*`, `CWallpaper::fromWallpaper` signature change, CMake targets | coherent module; the factory signature is the invasive part |
| AnimationTimeline (keyframed property animations) | `Data/Model/AnimationTimeline.*`, parser hook, CScene ticker | data half lifts alone; needs CScene ticker to run |

## Tier 3 - deeply woven (take the subsystem or nothing)

- **The daemon command handlers** (`WallpaperApplication.cpp` `handleApiCommand` and
  everything below it: `applyShowCore`, `rebuildForCurrentBackgrounds`, rotation engine,
  release/acquire state machine, deadman, status aggregation - ~1,700 new lines). Every
  handler reads/writes settings, render context, detector, and instrument state. This is
  the single largest porting cost in the fork - but it is also the daemon itself.
- **The lighting stack** (CLight + CScene light/shadow stage + CPass light staging +
  `ShaderUnit::generateLightingV1` + CRenderable virtuals). The GLSL module calls WE's
  own `ComputePBRLightShadow*` from wallpaper shader includes, so it only links in
  shaders that carry WE's lighting headers.
- **The particle rewrite** (`CParticle.{h,cpp}`, ~1,900 diff lines: child systems,
  events, instance overrides, trail ribbons, buffer pooling). `EmitterFunc` signature
  changed; not liftable piecemeal except the small rate/startTime/prewarm gates.
- **Composition-layer subtree rendering** (CScene `renderFrame` restructure + CImage
  composition FBO/alias logic). Must move as one piece.

## What a porter should NOT take

The ~40 pure log-probe env vars (`LWE_*DUMP`, `LWE_*PROBE`, ...) are development
instrumentation; take the 5-6 you need while debugging a port, leave the rest. The
handful of unconditional diagnostics listed in "Known rough edges" should be dropped or
gated in any port.

---

# 11. Known rough edges

Listed because this document is meant to be honest, and because upstream readers will
find them anyway:

- **Unconditional diagnostics that fire on normal operation** - these look
  like leftover development instrumentation and are candidates for env-gating:
  the `LWE-DEBUG` projection dump in the CScene ctor (`CScene.cpp:109-114`, error level),
  `LWE-SCENEFB` for the first 3 frames (`CScene.cpp:1054-1067`, error level),
  and `LWE-TIMELINE` logged per animated property (`CScene.cpp:375-380`, out level).
  (`LWE-PRESENT` used to be on this list; it is now env-gated behind `LWE_PRESENTTRACE`
  and demoted to out level.)
- **`LWE_LIGHTDUMP` contains a hardcoded wallpaper-specific trace** (`angles_112`,
  `ScriptEngine.cpp:794-799`).
- **Test coverage is concentrated in the new infrastructure** (command socket,
  dispatcher, binary parsers, instrument registry, pointer gate, web-helper startup).
  The render, scripting, audio, and video chapters have no `Testing/Cases/` coverage;
  behavioral confidence there comes from live burn-in, not the suite.
- **Observation-derived formats**: the MDLV submesh layouts, puppet clip padding, and
  point-light shadow face order are reconstructed from observation, not specs - each is
  marked Uncertain in its chapter. They are guarded against malformed input but their
  native-parity claims rest on measurement, not documentation.
- **`VirtualAdapter::open` returns a shared stream with a shared cursor** - safe only
  because current usage is strictly sequential on one thread.
- **`LWE_SHAPES` log message contradicts its code** (message says `=1` to render;
  code renders by default, `=0` disables; `ObjectParser.cpp:99-105`).

---

*Generated by reading the fork's source against upstream `b016d7d`. Every claim carries
its anchor; if a claim and the code ever disagree, the code is right and the anchor is
where to check.*
