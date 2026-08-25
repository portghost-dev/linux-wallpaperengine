import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Shapes
import "."

Item {
    id: addTile

    property string heading: "Get wallpapers"
    property string subcopy: "Opens the Steam Workshop"
    signal clicked()
    signal advancedRequested()

    // 0..1 breathing pulse; stops at a calm steady value under reducedMotion
    property real pulse: 0.0
    readonly property bool hovered: hoverH.hovered

    SequentialAnimation on pulse {
        running: !Motion.reducedMotion
        loops: Animation.Infinite
        NumberAnimation { from: 0; to: 1
            duration: addTile.hovered ? 820 : Motion.breathInhale; easing.type: Easing.InOutSine }
        NumberAnimation { from: 1; to: 0
            duration: addTile.hovered ? 980 : Motion.breathExhale; easing.type: Easing.InOutSine }
    }

    // breathing accent halo behind the tile: nested border rings with falling alpha
    // approximate the old 2-4px RectangularGlow without an effect module. The ring
    // count follows the old glowRadius (2 on light, 4 on dark).
    Item {
        id: tileHalo
        anchors.fill: tileBody
        opacity: (Motion.reducedMotion ? 0.70
                 : 0.30 + addTile.pulse * ((addTile.hovered ? 1.0 : 0.85) - 0.30))
                 * (Theme.isLight ? 0.6 : 1)     // light ceiling ~0.5
        Repeater {
            model: Theme.isLight ? 2 : 4
            delegate: Rectangle {
                required property int index
                anchors.fill: parent
                anchors.margins: -(index + 1)
                radius: Theme.radiusLg + index + 1
                color: "transparent"
                border.width: 1
                border.color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b,
                                      0.55 * (1.0 - index / (Theme.isLight ? 2 : 4)))
            }
        }
    }

    Item {
        anchors.fill: tileBody
        // ABOVE tileBody. The dash rides the border's own centreline, and tileBody's border is
        // OPAQUE accent - declared later, it painted straight over the dash and hid it entirely
        // (measured: border pixels under the dash were identical to bare border). The old
        // ConicalGradient version was only ever visible BECAUSE it was broken - its rotating
        // mask swung off the border into empty space where nothing occluded it.
        z: 1

        Repeater {
            model: 4

            delegate: Shape {
                id: sheen
                anchors.fill: parent
                visible: !Motion.reducedMotion
                readonly property real fadeFrom: 0.90
                readonly property real span: lap * travelFraction
                readonly property real progress: (alive && span > 0)
                                                 ? Math.min(1, -travel / span) : 0
                opacity: alive
                         ? 0.30 * (progress < fadeFrom
                                   ? 1
                                   : Math.max(0, (1 - progress) / (1 - fadeFrom)))
                         : 0
                property bool alive: false
                preferredRendererType: Shape.CurveRenderer

        readonly property real sw: 3             // matches the crisp border stroke
        readonly property real rad: Theme.radiusLg
        // the path rect is inset by HALF A STROKE on every side, so its perimeter is measured on
        // (width - sw) x (height - sw), NOT on the Shape's own size. Measured: using the outer
        // size overstates a 216x168 tile's loop by 12px (747.4 vs 735.4), and since dash+gap is
        // one pattern period, an overstated period drifts the highlight ~12px per lap and can
        // wrap a second stub onto the path.
        readonly property real pathW: width - sw
        readonly property real pathH: height - sw
        // exact rounded-rect perimeter: four straight runs + four corner quarter-arcs
        readonly property real perim: 2 * (pathW - 2 * rad) + 2 * (pathH - 2 * rad)
                                      + 2 * Math.PI * rad
        property real minDashPx: 10
        property real maxDashPx: 30
        property real dashPx: 30
        property real travel: 0

        // dashPattern and dashOffset are both expressed in units of the STROKE WIDTH, so every
        // px figure above is divided by sw. One dash plus a gap of the whole remaining
        // perimeter = exactly one highlight on the loop at a time.
        ShapePath {
            strokeColor: Qt.lighter(Theme.accent, 1.4)
            strokeWidth: sheen.sw
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            strokeStyle: ShapePath.DashLine
            dashPattern: [ sheen.dashPx / sheen.sw,
                           (sheen.perim - sheen.dashPx) / sheen.sw ]
            // birth (random, per life) + travel (literal 0 -> -lap). Combining them HERE rather
            // than inside the animation is what keeps the animation loop-free.
            dashOffset: sheen.birth + sheen.travel

            // inset by half the stroke so this rides the SAME centreline as tileBody's border
            // (a Rectangle draws its border inside its bounds, centered at border.width/2)
            PathRectangle {
                x: sheen.sw / 2
                y: sheen.sw / 2
                width: sheen.pathW
                height: sheen.pathH
                radius: sheen.rad
            }
        }

        // CLOCKWISE. Measured: an INCREASING dashOffset runs top -> left -> bottom -> right,
        // which is counter-clockwise on screen (y grows downward), so a lap counts DOWN.
        //
        // The rest happens while the pulse is DEAD (opacity 0), never mid-flight. v2.3.6's
        // original "one pass then rest" rested the visible dash in place, and because a lap
        // starts and ends at the top-left corner that pause landed in plain sight and read as
        // the shimmer jamming there. A pulse now vanishes at the end of its lap and the next
        // one is born elsewhere, so there is nothing on screen to stall.
        // TIMER + STANDALONE ANIMATION, deliberately NOT a SequentialAnimation. Randomizing a
        // per-cycle value inside a running sequence is a dead end in QML, and this file cost two
        // rounds proving it:
        //   BOUND   (PauseAnimation.duration: someProperty) -> the running animation re-evaluates
        //           it mid-flight, and Qt reports a binding loop every cycle.
        //   ASSIGNED (restPause.duration = ... inside a ScriptAction) -> mutating a child of the
        //           sequence that is currently running re-enters the sequence, so the script calls
        //           itself: "RangeError: Maximum call stack size exceeded" on load.
        // A Timer sidesteps both: its interval is only ever written while it is STOPPED, and the
        // travel animation is standalone so restarting it cannot re-trigger anything upstream.
        property real birth: 0
        readonly property real lap: perim / sw
        readonly property real travelFraction: 0.75

        function _spawn() {
            sheen.birth = Math.random() * sheen.lap;
            sheen.dashPx = sheen.minDashPx
                         + Math.random() * (sheen.maxDashPx - sheen.minDashPx);
            sheen.alive = true;
            travelAnim.restart();
        }

        Timer {
            id: respawn
            repeat: false
            onTriggered: sheen._spawn()
        }

        NumberAnimation {
            id: travelAnim
            target: sheen; property: "travel"
            from: 0
            to: -sheen.lap * sheen.travelFraction
            duration: Motion.shimmer * sheen.travelFraction
            easing.type: Easing.Linear
            onFinished: {
                sheen.alive = false;             // dies; nothing is left on screen to stall
                // one to three rests: two slots on a fixed delay would eventually lock into a
                // rhythm and read as a metronome, which is what the randomness is for. Safe to
                // assign here - the timer is stopped at this moment.
                respawn.interval = Motion.shimmerRest * (1 + Math.random() * 2);
                respawn.restart();
            }
        }

        Component.onCompleted: {
            respawn.interval = Motion.shimmerRest * (1 + Math.random() * 3);
            respawn.restart();
        }
        Connections {
            target: Motion
            function onReducedMotionChanged() {
                if (Motion.reducedMotion) { travelAnim.stop(); respawn.stop(); sheen.alive = false; }
                else sheen._spawn();
            }
        }
            }
        }
    }

    Item {
        id: tileBody
        anchors.fill: parent
        anchors.margins: 4

        // OPAQUE backing. selectionWash is accent at 8% ALPHA, so on its own it let the glow's
        // inner half shine through (measured: an interior floor of 19 blue rising to 78 at the
        // border). This blocks it, so the glow reads as an EDGE light rather than filling the
        // tile. It sits under the wash so the tint color is unchanged.
        Rectangle {
            anchors.fill: parent
            radius: Theme.radiusLg
            color: Theme.base
        }
        // HOVER WAKE (Stage B). The whole tile is one button, so the whole body answers: the
        // accent wash lifts 8% -> 14% and the tube border steps one lightness. No scrim and no
        // revealed action cluster - those belong to CONTENT tiles, which have interior actions
        // to reveal. Timing is Motion.wake, the same token WorkshopTile's hover already uses, so
        // every hover in the app shares one cadence.
        Rectangle {
            anchors.fill: parent
            radius: Theme.radiusLg
            color: addTile.hovered
                   ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.14)
                   : Theme.selectionWash
            border.width: 3
            border.color: addTile.hovered ? Qt.lighter(Theme.accent, 1.15) : Theme.accent
            Behavior on color { ColorAnimation { duration: Motion.wake } }
            Behavior on border.color { ColorAnimation { duration: Motion.wake } }
        }

        Column {
            anchors.centerIn: parent
            spacing: Theme.spacingSm
            Item {
                width: 26; height: 26
                anchors.horizontalCenter: parent.horizontalCenter
                Rectangle { anchors.centerIn: parent; width: 22; height: 2.4; radius: 1.2; color: Theme.accent }
                Rectangle { anchors.centerIn: parent; width: 2.4; height: 22; radius: 1.2; color: Theme.accent }
            }
            Label {
                anchors.horizontalCenter: parent.horizontalCenter
                text: addTile.heading
                color: Theme.textPrimary
                font.pixelSize: Theme.fontBody13
                font.weight: Theme.weightMedium
            }
            Label {
                anchors.horizontalCenter: parent.horizontalCenter
                text: addTile.subcopy
                color: Theme.textTertiary
                font.pixelSize: Theme.fontMeta
            }
        }
    }

    Item {
        id: advanced
        objectName: "addTileAdvanced"
        anchors.right: tileBody.right
        anchors.bottom: tileBody.bottom
        anchors.margins: Theme.spacingSm
        width: 26; height: 26              // a real hit target around a 16px glyph
        opacity: addTile.hovered ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Motion.wake } }

        Rectangle {
            anchors.fill: parent
            radius: Theme.radiusXs
            color: advHover.hovered ? Theme.hoverWash : "transparent"
        }
        IconFolderSearch {
            anchors.centerIn: parent
            size: 16
            color: advHover.hovered ? Theme.textPrimary : Theme.textSecondary
        }
        HoverHandler { id: advHover; cursorShape: Qt.PointingHandCursor }
        // declared BEFORE the tile-wide handler below, so a tap here opens the folder modal
        // instead of falling through to "open the Steam Workshop"
        TapHandler { onTapped: addTile.advancedRequested() }
    }

    HoverHandler { id: hoverH }
    // The tile is one big button, but the Advanced corner is a HOLE in it. A child TapHandler
    // does NOT stop an ancestor's from also firing - a tap on the corner opened the folder modal
    // AND the Steam Workshop - so the tile-wide handler excludes that rectangle explicitly rather
    // than relying on grab order.
    TapHandler {
        onTapped: function (eventPoint) {
            if (advanced.visible) {
                var p = advanced.mapFromItem(null, eventPoint.scenePosition);
                if (p.x >= 0 && p.y >= 0 && p.x <= advanced.width && p.y <= advanced.height)
                    return;
            }
            addTile.clicked();
        }
    }
}
