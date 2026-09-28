#include "HelpText.h"

namespace WallpaperEngine::Application {
const char* engineHelpText () {
    return R"(Usage: linux-wallpaperengine [options] [wallpaper]

  -h, --help                            Prints the help text and exits.
  --version                             Prints the engine's version and exits.
  -w, --window <XxYxWxH>                Runs the wallpaper in a window at this position and size
                                        instead of on the desktop.
  --screen, -r, --screen-root <screen>  Puts a wallpaper on this screen; the --wallpaper,
                                        --steamplaylist, --scaling and --edge that follow apply to
                                        it, and each screen can be named only once.
  --span, --screen-span <screen,screen,...>
                                        Stretches one wallpaper across these screens as a single
                                        picture; the --wallpaper, --scaling and --edge that follow
                                        apply to the group, and a screen can belong to only one
                                        group.
  --wallpaper, -b, --bg <id or folder>  The wallpaper for the screen or span group named just before
                                        it; every --wallpaper also becomes the default wallpaper for
                                        screens and windows that have none.
  --steamplaylist, --playlist <name>    Plays a playlist saved in Wallpaper Engine's own
                                        config.json: after --screen it rotates on that screen, and
                                        with no screen before it, it rotates only in window mode (on
                                        the desktop its first item just becomes the default
                                        wallpaper).
  --scaling <stretch|fit|fill|default>  How the wallpaper is fitted to its screen or window; given
                                        before any screen, it becomes the starting value for every
                                        screen named after it. Default default.
  --edge, --clamp <extend|blank|tile>   How the wallpaper's texture edges are treated; placed and
                                        inherited the same way as --scaling. Default extend. Takes
                                        extend | blank | tile; the old values clamp | border |
                                        repeat still work.
  --layer <background|bottom|top|overlay>
                                        Wayland only: which desktop layer the wallpaper sits on; on
                                        niri, background together with its place-within-backdrop
                                        rule keeps the wallpaper from being copied to every
                                        workspace in the overview. Default bottom.
  -f, --fps <number>                    Caps how many frames per second the wallpaper draws, to save
                                        power; set-fps changes it while running. Default 30.
  --no-fullscreen-pause                 Keeps wallpapers running while an app is fullscreen (sets
                                        the fullscreen handling to off). Same as --fullscreen keep,
                                        unless a later --daemon sets stop.
  --fullscreen-active-only, --fullscreen-pause-only-active
                                        Wayland only: a fullscreen window counts only while it is
                                        the active window.
  --fullscreen-ignore, --fullscreen-pause-ignore-appid <app id>
                                        Wayland only: fullscreen windows whose app id contains this
                                        text (ignoring case) do not trigger the fullscreen handling;
                                        set-fullscreen-ignore replaces the list while running.
  -v, --volume <0-128>                  Sound volume for wallpapers; set-volume changes it while
                                        running. Default 15.
  --mute, -s, --silent                  Mutes all wallpaper sound.
  --no-automute, --noautomute           Keeps wallpaper sound playing while another app plays sound
                                        or is fullscreen (by default it mutes; fullscreen counts
                                        unless --fullscreen keep is given without --listen).
  --no-audioreactive, --no-audio-processing
                                        Turns off audio processing (the sound levels wallpapers
                                        react to); set-audio changes it while running.
  --listen, --api-socket                Opens the command socket so other programs, such as the lwe
                                        panel, can control the engine; the engine will not start if
                                        another engine already holds the socket.
  --daemon                              Starts with no wallpaper and waits for a show command on the
                                        socket (name each screen with --screen, no --wallpaper); it
                                        also turns on --listen, releases the screens while an app is
                                        fullscreen (on the Wayland desktop; elsewhere it pauses),
                                        and saves its state to restore on the next start.
  --screenshot <file>                   Saves one screenshot of the wallpaper to this file once it
                                        has drawn a few frames (see --screenshot-delay), for color
                                        tools such as pywal.
  --screenshot-delay <frames>           How many frames to wait before taking the --screenshot.
                                        Default 5. Counted in frames, 0 to 5.
  --assetsfolder, --assets-dir <folder> Folder holding Wallpaper Engine's shared assets that
                                        wallpapers build on. Default Wallpaper Engine's assets
                                        folder from the Steam install, else an assets folder next to
                                        the program.
  --properties-file <file>              A JSON file of property values per screen ({screen:
                                        {property: value}}) that the engine re-reads each time it
                                        receives the SIGUSR1 signal, for live property changes.
  --no-particles, --disable-particles   Leaves particle systems out of wallpapers; set-particles
                                        changes it while running.
  --no-mouse, --disable-mouse           Stops wallpapers from reacting to the mouse; set-mouse
                                        changes it while running.
  --no-parallax, --disable-parallax     Turns off the parallax effect; set-parallax changes it while
                                        running.
  -l, --list-properties                 Prints every user property of the chosen wallpapers with its
                                        settings, then exits without showing anything.
  --property, --set-property <name=value>
                                        Overrides the starting value of one of the wallpaper's user
                                        properties.
  -z, --dump-structure                  Prints the internal structure of the loaded wallpapers at
                                        startup (the engine keeps running).
  --render-debug <mode>                 Scene debugging: base-only draws image layers without their
                                        effects, no-solid-final skips the last draw of solid layers,
                                        pass-log logs every render pass, object=<id> draws only that
                                        object, skip-object=<id> hides an object, and
                                        skip-effect=<id> builds without an effect.
  --resclamp <0-4>                      Caps the size a scene is drawn at, as a multiple of your
                                        largest screen. Lower saves video memory. It never enlarges
                                        anything; turning it off can remove blur this cap caused.
                                        Same as the resclamp setting; its engine variable still
                                        works. Default 1.
  --effectclamp <0-4>                   The same cap for glow, blur and the other effect layers, set
                                        on its own. Same as the effectclamp setting; its engine
                                        variable still works. Default 1.
  --texturecache <on|off>               Uses the compressed textures that compress builds; off loads
                                        the originals and deletes nothing. Same as the texturecache
                                        setting; its engine variable still works. Default on.
  --texturedetail <auto|full>           auto loads smaller textures when your screen does not need
                                        the full size; full always loads everything. Same as the
                                        texturedetail setting; its engine variable still works.
                                        Default auto.
  --videodecode <software|auto>         How video wallpapers are decoded; auto uses the graphics
                                        card when it can. Same as the videodecode setting; its
                                        engine variable still works. Default software.
  --color <"b c s h">                   The color correction every wallpaper starts with when the
                                        engine runs without the panel. The hue is in degrees. Same
                                        as the color setting; its engine variable still works, with
                                        the hue in radians. Default "1 1 1 0".
  --speed <0-10>                        How fast scene animation runs, from 0 to 10; 1 is normal and
                                        0 freezes it. Same as the speed setting; its engine variable
                                        still works. Default 1.
  --watchdog <seconds>                  Once the panel or another client has pinged the engine, if
                                        the engine then draws nothing and hears nothing for this
                                        long, it frees your screens (on the Wayland desktop;
                                        elsewhere it only logs). It does not restart anything. Takes
                                        seconds, or a whole number with s, m or h. Same as the
                                        watchdog setting; its engine variable still works. Default
                                        300.
  --lightdimming <number>               Overall brightness of the lights inside scenes. Same as the
                                        lightdimming setting; its engine variable still works.
                                        Default 16.
  --lightfalloff <number>               How quickly scene lights fade with distance. Same as the
                                        lightfalloff setting; its engine variable still works.
                                        Default 2.
  --audiogain <number>                  How strongly wallpapers react to sound. Same as the
                                        audiogain setting; its engine variable still works. Default
                                        1.
  --audiosmoothing <ms>                 How smoothly the sound levels wallpapers react to change.
                                        Same as the audiosmoothing setting; its engine variable
                                        still works. Default 90.
  --socket <path>                       Where the engine listens for commands. Same as the socket
                                        setting; its engine variable still works. Default
                                        $XDG_RUNTIME_DIR/lwe/engine.sock, else
                                        /tmp/lwe-<uid>/engine.sock.
  --fullscreen <keep|pause|stop>        What happens while a game or app is fullscreen: keep
                                        playing, pause, or stop and free the screens; stop frees the
                                        screens only on the Wayland desktop and pauses elsewhere.
                                        Default pause, or stop with --daemon. --no-fullscreen-pause
                                        keeps working as --fullscreen keep, unless a later --daemon
                                        sets stop.
  --debug <switch>=<value>              Sets one debugging switch by its plain name; repeat it for
                                        more.
  --help-debug                          Prints the unsupported debugging switches and exits.

The screen options (--wallpaper, --scaling, --edge, --steamplaylist) apply to the screen
or window named just before them. --help-debug lists the unsupported debugging switches.
)";
}
} // namespace WallpaperEngine::Application
