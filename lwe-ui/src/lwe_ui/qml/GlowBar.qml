import QtQuick
import "."

// The glowing filament shared by the deck progress bar (accent) and the bench presence cue
// (warning): bed, breathing fill, lagged bloom, travelling shimmer. `progress` is the lit
// portion (the bench passes 1: a lease has no meter value); `paused` slows the breath, `flat` is bed only.
Item {
    id: bar
    implicitWidth: 240
    implicitHeight: 3
    clip: false

    property color color: Theme.warning
    property real progress: 1.0
    property bool paused: false
    property bool flat: false
    property string fillName: ""
    // how far the bloom halo reaches past the filament; the bench keeps the full 12 px
    property int bloomReach: 12

    // light theme caps the bloom (Motion.bloomCeiling); dark keeps the full swell
    readonly property real bloomPeak: Theme.isLight ? Math.min(0.7, Motion.bloomCeiling) : 0.7
    readonly property bool breathing: bar.visible && !bar.flat && !Motion.reducedMotion && bar.fillWidth > 0
    readonly property int breathInhale: Motion.breathInhale * (bar.paused ? 2 : 1)
    readonly property int breathExhale: Motion.breathExhale * (bar.paused ? 2 : 1)
    readonly property bool shimmerOn: bar.breathing && !bar.paused
    readonly property real fillWidth: bar.flat ? 0 : bar.width * Math.max(0, Math.min(1, bar.progress))

    property real fillPulse: 0.85
    property real bloomPulse: 0.25

    // core fill breath
    SequentialAnimation on fillPulse {
        id: fillAnim
        running: bar.breathing; loops: Animation.Infinite
        NumberAnimation { from: 0.85; to: 1.0; duration: bar.breathInhale; easing.type: Easing.InOutSine }
        NumberAnimation { from: 1.0; to: 0.85; duration: bar.breathExhale; easing.type: Easing.InOutSine }
    }
    // bloom breath, lagged behind the fill: it blooms open a beat after the core brightens,
    // so it reads as light swelling, not a dimmer knob
    Timer {
        id: lagTimer
        interval: Motion.bloomLag; running: bar.breathing
        onTriggered: bloomAnim.restart()
    }
    onBreathingChanged: if (!bar.breathing) bloomAnim.stop()
    // a pause change restarts the breath from the bottom at the new period, bloom a beat behind
    onPausedChanged: {
        if (!bar.breathing) return;
        bloomAnim.stop();
        bar.bloomPulse = 0.25;
        fillAnim.restart();
        lagTimer.restart();
    }
    SequentialAnimation {
        id: bloomAnim; loops: Animation.Infinite
        NumberAnimation { target: bar; property: "bloomPulse"; from: 0.25; to: bar.bloomPeak
            duration: bar.breathInhale; easing.type: Easing.InOutSine }
        NumberAnimation { target: bar; property: "bloomPulse"; from: bar.bloomPeak; to: 0.25
            duration: bar.breathExhale; easing.type: Easing.InOutSine }
    }

    // 3. bloom: a soft halo behind the lit portion only, breathing a beat behind the fill. Pure
    // gradient, no effect module; declared before the clipped meter so it renders behind it.
    Item {
        x: -bar.bloomReach / 2
        y: -bar.bloomReach
        width: bar.fillWidth + bar.bloomReach
        height: bar.height + 2 * bar.bloomReach
        visible: !bar.flat && bar.fillWidth > 0
        opacity: Motion.reducedMotion ? Math.min(0.40, bar.bloomPeak) : bar.bloomPulse
        Rectangle {
            anchors.fill: parent
            radius: height / 2
            gradient: Gradient {
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop { position: 0.5
                    color: Qt.rgba(bar.color.r, bar.color.g, bar.color.b, 0.45) }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }
    }

    // the crisp meter: bed + fill + travelling shimmer, all clipped to the rounded bar
    Item {
        id: meter
        anchors.fill: parent
        clip: true
        // 1. bed: the unlit filament
        Rectangle {
            id: bed
            anchors.fill: parent
            radius: 1.5
            color: Qt.rgba(bar.color.r, bar.color.g, bar.color.b, 0.14)
        }
        // 2. fill: the lit portion, breathing; the shimmer lives inside it so it is clipped to it
        Rectangle {
            id: fill
            objectName: bar.fillName
            width: bar.fillWidth
            height: parent.height
            radius: 1.5
            clip: true
            color: bar.color
            opacity: Motion.reducedMotion || bar.flat ? 0.85 : bar.fillPulse
            // 4. travelling shimmer: a faint highlight that sweeps the fill then rests, at the
            // bench bar's speed: 360 px (a 240 px bar plus 60 px overshoot each side) per Motion.shimmer
            Rectangle {
                id: shimmer
                width: 60
                height: parent.height
                radius: 1.5
                opacity: 0.30
                visible: bar.shimmerOn
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: "transparent" }
                    GradientStop { position: 0.5; color: Qt.lighter(bar.color, 1.5) }
                    GradientStop { position: 1.0; color: "transparent" }
                }
                SequentialAnimation on x {
                    running: bar.shimmerOn; loops: Animation.Infinite
                    NumberAnimation { from: -60; to: fill.width + 60
                        duration: Math.max(1, Math.round(Motion.shimmer * (fill.width + 120) / 360))
                        easing.type: Easing.InOutCubic }
                    PauseAnimation { duration: Motion.shimmerRest }
                }
            }
        }
    }
}
