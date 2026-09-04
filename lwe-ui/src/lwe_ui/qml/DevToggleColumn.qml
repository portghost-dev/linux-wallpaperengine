import QtQuick
import QtQuick.Controls.Basic
import "."

// One column of the A/B toggle grammar: a header row, then 26px rows with a name and two
// 34px cells. Rows carry kind toggle, render or trail.
Column {
    id: col

    property var rows: []
    property int rev: 0
    property int cellW: 34
    property int rowH: 26
    property bool mono: false
    property real nameSize: 11.5

    spacing: 0

    Item {
        width: col.width
        height: 16
        Row {
            anchors.right: parent.right
            Repeater {
                model: ["A", "B"]
                delegate: Item {
                    required property var modelData
                    width: col.cellW
                    height: 16
                    Label {
                        anchors.centerIn: parent
                        text: parent.modelData
                        font.pixelSize: 10
                        color: Theme.textTertiary
                    }
                }
            }
        }
    }

    Repeater {
        model: col.rows
        delegate: Item {
            id: row
            required property var modelData
            width: col.width
            height: col.rowH

            Label {
                id: nameLabel
                anchors.left: parent.left
                anchors.right: cells.left
                anchors.rightMargin: 4
                anchors.verticalCenter: parent.verticalCenter
                text: row.modelData.label || ""
                elide: Text.ElideRight
                font.pixelSize: col.nameSize
                font.family: col.mono ? Theme.monoFamily : Qt.application.font.family
                color: Theme.textPrimary
                HoverHandler { id: nameHover }
                ToolTip.visible: nameHover.hovered && (row.modelData.tip || "") !== ""
                ToolTip.delay: 400
                ToolTip.text: row.modelData.tip || ""
            }

            Row {
                id: cells
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                Repeater {
                    model: ["A", "B"]
                    delegate: Item {
                        id: cell
                        required property var modelData
                        readonly property string side: modelData
                        width: col.cellW
                        height: col.rowH

                        readonly property bool legacy: (col.rev, dev.slotState(cell.side).legacy === true)
                        readonly property bool liveClass: row.modelData.kind === "instrument" && row.modelData.live === true
                        readonly property bool dimmed: cell.liveClass && cell.legacy

                        ThemedSwitch {
                            visible: row.modelData.kind !== "trail"
                            anchors.centerIn: parent
                            pillWidth: 28
                            pillHeight: 16
                            enabled: !cell.dimmed
                            opacity: cell.dimmed ? 0.55 : 1
                            checked: {
                                col.rev;
                                var k = row.modelData.kind;
                                if (k === "toggle") return dev.toggleOn(cell.side, row.modelData.key);
                                if (k === "render") return dev.renderDebugOn(cell.side, row.modelData.key);
                                if (k === "instrument") return dev.instrumentOn(cell.side, row.modelData.key);
                                return false;
                            }
                            onToggled: {
                                var k = row.modelData.kind;
                                if (k === "toggle") dev.setToggle(cell.side, row.modelData.key, checked);
                                else if (k === "render") dev.setRenderDebug(cell.side, row.modelData.key, checked);
                                else if (k === "instrument") dev.setInstrument(cell.side, row.modelData.key, checked);
                            }
                        }

                        Rectangle {
                            id: trailDrop
                            visible: row.modelData.kind === "trail"
                            anchors.centerIn: parent
                            width: col.cellW - 2
                            height: 20
                            radius: 5
                            color: Theme.inputWell
                            border.width: 1
                            border.color: Theme.border
                            Label {
                                anchors.centerIn: parent
                                text: (col.rev, dev.trailMode(cell.side))
                                font.pixelSize: 10
                                color: Theme.textPrimary
                            }
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                            TapHandler {
                                onTapped: {
                                    if (!trailMenu.item) return;
                                    if (trailMenu.item.visible) trailMenu.item.close();
                                    else trailMenu.item.open();
                                }
                            }
                            Loader {
                                id: trailMenu
                                active: row.modelData.kind === "trail"
                                sourceComponent: Menu {
                                    parent: trailDrop
                                    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
                                    y: trailDrop.height + 2
                                    background: Rectangle {
                                        implicitWidth: 80
                                        color: Theme.surfaceVariant
                                        radius: Theme.radiusSm
                                        border.width: 1
                                        border.color: Theme.borderStrong
                                    }
                                    Repeater {
                                        model: dev.trailModes()
                                        delegate: ThemedMenuItem {
                                            required property var modelData
                                            text: modelData
                                            onTriggered: dev.setTrailMode(cell.side, modelData)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
