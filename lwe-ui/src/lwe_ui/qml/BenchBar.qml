import QtQuick
import "."

// The bench presence cue: a breathing amber filament shown while a bench or A/B
// hold owns the display. A release is state on the engine side (no countdown to
// draw), so the bar carries no meter value - its presence IS the information.
Item {
    id: bar
    implicitWidth: 240
    implicitHeight: 3
    clip: false

    readonly property color amber: Theme.warning       // flips to #B26B00 on light via the token
    property color bloomColor: Theme.warning           // light theme can pass a brighter amber

    property real fillPulse: 0.55
    property real bloomPulse: 0.25

    // core fill breath
    SequentialAnimation on fillPulse {
        running: bar.visible && !Motion.reducedMotion; loops: Animation.Infinite
        NumberAnimation { from: 0.55; to: 1.0; duration: Motion.breathInhale; easing.type: Easing.InOutSine }
        NumberAnimation { from: 1.0; to: 0.55; duration: Motion.breathExhale; easing.type: Easing.InOutSine }
    }
    // bloom breath, lagged ~140ms behind the fill - it blooms open a beat after the core
    // brightens, so it reads as light swelling, not a dimmer knob.
    Timer {
        interval: Motion.bloomLag; running: bar.visible && !Motion.reducedMotion
        onTriggered: bloomAnim.restart()
    }
    onVisibleChanged: if (!bar.visible) bloomAnim.stop()
    SequentialAnimation {
        id: bloomAnim; loops: Animation.Infinite
        NumberAnimation { target: bar; property: "bloomPulse"; from: 0.25; to: 0.7
            duration: Motion.breathInhale; easing.type: Easing.InOutSine }
        NumberAnimation { target: bar; property: "bloomPulse"; from: 0.7; to: 0.25
            duration: Motion.breathExhale; easing.type: Easing.InOutSine }
    }
    Connections {
        target: Motion
        function onReducedMotionChanged() { if (Motion.reducedMotion) bloomAnim.stop() }
    }

    // 3. bloom: a soft vertical halo behind the bar, breathing a beat behind the fill
    // (the lag is the trick). Pure gradient - no effect module: two axial fades meeting
    // at the filament approximate the old Glow's falloff for a 240x3 source. It sits
    // OUTSIDE the clipped meter and is declared before it, so it renders BEHIND the
    // crisp bar: a halo, not a wash.
    Item {
        anchors.fill: meter
        anchors.topMargin: -12
        anchors.bottomMargin: -12
        anchors.leftMargin: -6
        anchors.rightMargin: -6
        opacity: Motion.reducedMotion ? 0.40 : bar.bloomPulse
        Rectangle {
            anchors.fill: parent
            radius: height / 2
            gradient: Gradient {
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop { position: 0.5
                    color: Qt.rgba(bar.bloomColor.r, bar.bloomColor.g, bar.bloomColor.b, 0.45) }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }
    }

    // the crisp 240x3 meter: bed + fill + travelling shimmer, all clipped to the rounded bar
    Item {
        id: meter
        anchors.fill: parent
        clip: true
        // 1. bed: the unlit filament
        Rectangle {
            id: bed
            anchors.fill: parent
            radius: 1.5
            color: Qt.rgba(bar.amber.r, bar.amber.g, bar.amber.b, 0.14)
        }
        // 2. fill: the core pulse
        Rectangle {
            id: fill
            anchors.fill: parent
            radius: 1.5
            color: bar.amber
            opacity: Motion.reducedMotion ? 0.85 : bar.fillPulse
        }
        // 4. travelling shimmer: a faint highlight that sweeps then rests (clipped to the meter)
        Rectangle {
            id: shimmer
            width: 60
            height: parent.height
            radius: 1.5
            opacity: 0.30
            visible: !Motion.reducedMotion
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop { position: 0.5; color: Qt.lighter(bar.amber, 1.5) }
                GradientStop { position: 1.0; color: "transparent" }
            }
            SequentialAnimation on x {
                running: bar.visible && !Motion.reducedMotion; loops: Animation.Infinite
                NumberAnimation { from: -60; to: bar.width + 60; duration: Motion.shimmer; easing.type: Easing.InOutCubic }
                PauseAnimation { duration: Motion.shimmerRest }
            }
        }
    }
}
