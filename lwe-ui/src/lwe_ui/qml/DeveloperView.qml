import QtQuick
import QtQuick.Controls.Basic
import "."

// Developer view: two exhibit slots, three launch verbs. Flagship is three columns;
// below compactBelow the columns become three segments with identical contents.
Rectangle {
    id: view

    signal closed()

    color: Theme.base
    clip: true

    property int rev: 0
    property int compactBelow: 1100
    readonly property bool compactLaw: Theme.usableWidth < compactBelow
    property int pane: 0
    readonly property int padV: compactLaw ? 12 : 14
    readonly property int padH: compactLaw ? 16 : 18

    Connections {
        target: dev
        function onStateChanged() { view.rev++ }
    }

    onVisibleChanged: dev.setFollowingDaemon(visible)
    Component.onCompleted: if (visible) dev.setFollowingDaemon(true)

    readonly property bool aliveA: (rev, dev.alive("A"))
    readonly property bool aliveB: (rev, dev.alive("B"))
    readonly property string runMode: (rev, dev.runMode())
    readonly property bool busy: (rev, dev.verbsBusy())
    readonly property string primaryVerb: runMode !== "window" ? ""
                                        : aliveA && aliveB ? "both" : aliveA ? "A" : aliveB ? "B" : ""

    // A launch verb is a toggle: it reads Stop while its exhibit runs. The width is the wider
    // of its two labels, fixed, so the row never shifts when a verb flips.
    component VerbButton: Rectangle {
        id: vb
        property string text: ""
        property string activeText: "Stop"
        property bool active: false
        property bool primary: false
        property bool dim: false
        property string tip: ""
        signal clicked()
        height: 26
        width: Math.ceil(Math.max(idleMetrics.width, activeMetrics.width)) + 24
        radius: 5
        color: vb.primary ? Theme.segmentWash : "transparent"
        border.width: 1
        border.color: Theme.hairlineStrong
        opacity: vb.enabled ? 1 : 0.55
        TextMetrics { id: idleMetrics; font.pixelSize: 11; text: vb.text }
        TextMetrics { id: activeMetrics; font.pixelSize: 11; text: vb.activeText }
        Label {
            id: vbLabel
            anchors.centerIn: parent
            text: vb.active ? vb.activeText : vb.text
            font.pixelSize: 11
            color: vb.dim ? Theme.textTertiary : Theme.textPrimary
        }
        HoverHandler { id: vbHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: vb.clicked() }
        ToolTip.visible: vb.tip !== "" && vbHover.hovered
        ToolTip.delay: 400
        ToolTip.text: vb.tip
    }

    component Verbs: Row {
        id: verbs
        property bool short: false
        spacing: 8
        VerbButton {
            objectName: "devVerbA"
            text: verbs.short ? "A" : "Launch A"
            active: view.aliveA
            primary: view.aliveA
            enabled: !view.busy
            tip: view.aliveA ? "Stops A." : "Opens A as a window in the top-left quadrant of the focused monitor. The desktop stays."
            onClicked: view.aliveA ? dev.stopSide("A") : dev.launch("A")
        }
        VerbButton {
            objectName: "devVerbB"
            text: verbs.short ? "B" : "Launch B"
            active: view.aliveB
            primary: view.aliveB
            enabled: !view.busy
            tip: view.aliveB ? "Stops B." : "Opens B as a window in the top-right quadrant of the focused monitor. The desktop stays."
            onClicked: view.aliveB ? dev.stopSide("B") : dev.launch("B")
        }
        VerbButton {
            objectName: "devVerbBoth"
            text: verbs.short ? "Both" : "Launch both"
            active: view.aliveA && view.aliveB
            primary: view.aliveA && view.aliveB
            enabled: !view.busy
            tip: (view.aliveA && view.aliveB) ? "Stops both." : "Opens A top-left and B top-right, side by side."
            onClicked: (view.aliveA && view.aliveB) ? dev.stop() : dev.launchBoth()
        }
    }

    Item {
        id: segRow
        visible: view.compactLaw
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.leftMargin: view.padH
        anchors.rightMargin: view.padH
        anchors.topMargin: view.padV
        height: visible ? 26 : 0

        SegmentControl {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            sizeClass: "h24"
            model: ["Setup", "Isolator", "Console"]
            currentIndex: view.pane
            onActivated: function(i) { view.pane = i }
        }
        Verbs {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            short: true
        }
    }

    Item {
        id: body
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: segRow.bottom
        anchors.bottom: parent.bottom
        anchors.topMargin: view.compactLaw ? 4 : 0

        readonly property int setupW: 500
        readonly property int isoW: 320

        Item {
            id: setupCol
            objectName: "devSetupColumn"
            x: 0
            width: view.compactLaw ? body.width : body.setupW
            height: body.height
            visible: !view.compactLaw || view.pane === 0

            Column {
                anchors.fill: parent
                anchors.margins: view.padV
                anchors.leftMargin: view.padH
                anchors.rightMargin: view.padH
                spacing: 0

                Item {
                    id: verbRow
                    width: parent.width
                    height: view.compactLaw ? 0 : 26
                    visible: !view.compactLaw
                    Label {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Advanced scene control"
                        font.pixelSize: 11
                        font.weight: Theme.weightMedium
                        color: Theme.textSecondary
                    }
                    Verbs {
                        objectName: "devVerbs"
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }

                Item { width: 1; height: view.compactLaw ? 0 : 10 }

                Row {
                    id: cards
                    width: parent.width
                    spacing: 10
                    DevSlotCard {
                        objectName: "devSlotA"
                        side: "A"
                        rev: view.rev
                        width: (cards.width - cards.spacing) / 2
                    }
                    DevSlotCard {
                        objectName: "devSlotB"
                        side: "B"
                        rev: view.rev
                        width: (cards.width - cards.spacing) / 2
                    }
                }

                Item { width: 1; height: 10 }

                Item {
                    id: toggleRule
                    width: parent.width
                    height: 24
                    Label {
                        id: toggleRuleLabel
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Feature toggles"
                        font.pixelSize: 11
                        font.weight: Theme.weightMedium
                        color: Theme.textSecondary
                    }
                    Rectangle {
                        anchors.left: toggleRuleLabel.right
                        anchors.leftMargin: 8
                        anchors.right: rawEnvButton.left
                        anchors.rightMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        height: 1
                        color: Theme.hairline
                    }
                    Rectangle {
                        id: rawEnvButton
                        objectName: "devRawEnvButton"
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        height: 20
                        width: rawEnvLabel.implicitWidth + 16
                        radius: 5
                        color: "transparent"
                        border.width: 1
                        border.color: Theme.hairlineStrong
                        Label {
                            id: rawEnvLabel
                            anchors.centerIn: parent
                            text: "Raw env"
                            font.pixelSize: 10
                            color: Theme.textPrimary
                        }
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: rawEnv.open() }
                    }
                }

                Flickable {
                    id: toggleFlick
                    width: parent.width + view.padH
                    height: Math.max(0, parent.height - y)
                    contentHeight: toggleGrid.implicitHeight
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    Row {
                        id: toggleGrid
                        objectName: "devToggleGrid"
                        width: toggleFlick.width - view.padH
                        spacing: 16

                        readonly property var rows: {
                            view.rev;
                            var t = dev.featureToggles();
                            var r = dev.renderDebugFlags();
                            var all = [];
                            for (var i = 0; i < t.length; i++)
                                all.push({kind: "toggle", key: t[i].key, label: t[i].label, tip: t[i].tip});
                            for (var j = 0; j < r.length; j++)
                                all.push({kind: "render", key: r[j].key, label: r[j].label, tip: r[j].tip});
                            var half = Math.ceil(all.length / 2);
                            var left = all.slice(0, half);
                            left.push({kind: "trail", key: "trail", label: "Trail mode", tip: dev.trailTip()});
                            return [left, all.slice(half)];
                        }

                        Repeater {
                            model: 2
                            delegate: DevToggleColumn {
                                required property int index
                                width: (toggleGrid.width - toggleGrid.spacing) / 2
                                rows: toggleGrid.rows[index]
                                rev: view.rev
                            }
                        }
                    }
                }
            }
        }

        Rectangle {
            visible: !view.compactLaw
            x: body.setupW
            width: 1
            height: body.height
            color: Theme.hairline
        }

        DevIsolator {
            id: isoCol
            objectName: "devIsolatorColumn"
            x: view.compactLaw ? 0 : body.setupW + 1
            width: view.compactLaw ? body.width : body.isoW
            height: body.height
            visible: !view.compactLaw || view.pane === 1
            padV: view.padV
            padH: view.padH
            rev: view.rev
        }

        Rectangle {
            visible: !view.compactLaw
            x: body.setupW + 1 + body.isoW
            width: 1
            height: body.height
            color: Theme.hairline
        }

        DevConsole {
            id: consoleCol
            objectName: "devConsoleColumn"
            x: view.compactLaw ? 0 : body.setupW + 2 + body.isoW
            width: view.compactLaw ? body.width : Math.max(0, body.width - x)
            height: body.height
            visible: !view.compactLaw || view.pane === 2
            padV: view.padV
            padH: view.padH
            rev: view.rev
        }
    }

    DevRawEnv {
        id: rawEnv
        parent: Overlay.overlay
        rev: view.rev
    }
}
