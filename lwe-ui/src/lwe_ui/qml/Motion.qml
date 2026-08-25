pragma Singleton
import QtQuick

// Motion singleton (glow and motion bible): the single source of animation
// timing, easing intent, and the reduced-motion accessibility flag. Durations are in ms.
//
// Kept separate from Theme (which owns color and metrics) because motion is its own concern:
// the glow layers, the hover wakes, and the grid removal all read their timings from here, so
// one edit retunes the whole app. The linter resolves this file with no external imports.
QtObject {
    id: motion

    // --- core motion tokens (bible: define once) -------------------------------------------
    readonly property int breath: 2400      // the breathing pulse period (lease bar fill, halo aura)
    readonly property int shimmer: 3200     // travelling-shimmer sweep period on the lease bar
    readonly property int rotate: 6000      // rotating-sheen full turn (optional halo flourish)
    readonly property int wake: 180         // hover wake and other small state transitions
    readonly property int fade: 120         // element fade in/out (lease release, chip swaps)

    // breath asymmetry (organic, not metronomic): inhale a touch shorter than the exhale, so a
    // full breath = breathInhale + breathExhale = breath.
    readonly property int breathInhale: 1100
    readonly property int breathExhale: 1300

    // the bloom lag: the lease-bar glow blooms open a beat AFTER the core fill brightens, which
    // is what reads as "light swelling" rather than a dimmer knob (bible section A).
    readonly property int bloomLag: 140

    // shimmer rest: a pause at the end of each sweep so it glimmers periodically, not a conveyor.
    readonly property int shimmerRest: 700

    // --- grid removal (v2.3.1 interaction contract) ----------------------------------------
    // On Add-to-library / Trash / Purge the model drop is synchronous (kills the stale-cell
    // glitch); the leaving cell fades, the rest reflow. Shared by every item grid and list.
    readonly property int removeFade: 120                    // leaving cell opacity 1 -> 0
    readonly property int removeReflow: 180                  // displaced cells settle
    readonly property int removeReflowEasing: Easing.InOutCubic

    // --- accessibility ----------------------------------------------------------------------
    // When true, all breathing / shimmer / rotation stops and elements render at their calm
    // steady state (each glow spec defines its own). A settable flag so it can bind to a user
    // setting later; defaults to motion-on.
    property bool reducedMotion: false
}
