import QtQuick
import QtQuick.Controls.Basic
import "."

// The clock popover: the one home for a playlist's Mode and Every. No title, no
// buttons; every change commits live. Anchored by its owner above the clock cell.
Popup {
    id: pop

    // the active playlist as the strip holds it: {mode, interval, unit, ...}
    property var activePl: ({mode: "shuffle", interval: 900, unit: "min"})
    readonly property bool isStatic: (activePl.mode || "shuffle") === "static"

    // the interval as the field DISPLAYS it (seconds verbatim, else minutes): the comparand
    // that keeps a commit from re-firing when nothing actually changed
    function shownInterval() {
        var iv = activePl.interval || 900;
        return activePl.unit === "s" ? iv : Math.round(iv / 60);
    }
    function titleCase(s) { return s.length ? s.charAt(0).toUpperCase() + s.slice(1) : s }
    // the one commit path: a typed value lands, an empty or zero field re-reads the stored one
    function commitField() {
        var v = parseInt(intervalField.text);
        var top = activePl.unit === "s" ? 86400 : 1440;
        if (isNaN(v) || v < 1 || v > top) { intervalField.text = String(shownInterval()); return; }
        if (v === shownInterval())
            return;
        backend.setPlaylistInterval(v, activePl.unit || "min");
    }

    width: 300
    topPadding: 6
    bottomPadding: 6
    leftPadding: 14
    rightPadding: 14
    property bool justClosed: false
    onClosed: { justClosed = true; guard.restart() }
    Timer { id: guard; interval: 150; onTriggered: pop.justClosed = false }

    background: Rectangle {
        color: Theme.surface
        radius: Theme.radiusMd
        border.width: 1
        border.color: Theme.hairlineStrong
    }

    contentItem: Column {
        spacing: Theme.spacingXs

        // Mode: Sequential | Shuffle | Static
        Item {
            width: parent.width
            height: 32
            Label {
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: parent.left
                text: "Mode"
                color: Theme.textSecondary
                font.pixelSize: Theme.fontControl
            }
            Rectangle {
                id: modeSeg
                objectName: "modeSegment"
                anchors.verticalCenter: parent.verticalCenter
                anchors.right: parent.right
                width: modeRow.implicitWidth + 2
                height: 24
                color: Theme.surface
                radius: Theme.radiusSm
                border.width: 1
                border.color: Theme.borderStrong
                clip: true
                Row {
                    id: modeRow
                    anchors.fill: parent
                    anchors.margins: 1
                    Repeater {
                        model: ["sequential", "shuffle", "static"]
                        delegate: Rectangle {
                            id: cell
                            required property string modelData
                            required property int index
                            readonly property bool active: cell.modelData === (pop.activePl.mode || "shuffle")
                            width: cellLabel.implicitWidth + Theme.spacingMd * 2
                            height: parent.height
                            color: active ? Theme.surfaceVariant : cellHover.hovered ? Theme.hoverWash : "transparent"
                            Rectangle {
                                visible: cell.index > 0
                                width: 1; height: parent.height
                                color: Theme.border
                            }
                            Label {
                                id: cellLabel
                                anchors.centerIn: parent
                                text: pop.titleCase(cell.modelData)
                                color: cell.active ? Theme.textPrimary : Theme.textSecondary
                                font.pixelSize: Theme.fontControl
                            }
                            HoverHandler { id: cellHover }
                            TapHandler {
                                onTapped: {
                                    pop.commitField();
                                    if (!cell.active) backend.setPlaylistMode(cell.modelData);
                                }
                            }
                        }
                    }
                }
            }
        }

        // Every: the number and its unit; asleep in Static
        Item {
            objectName: "everyRow"
            width: parent.width
            height: 32
            opacity: pop.isStatic ? 0.55 : 1
            Label {
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: parent.left
                text: "Every"
                color: Theme.textSecondary
                font.pixelSize: Theme.fontControl
            }
            Row {
                anchors.verticalCenter: parent.verticalCenter
                anchors.right: parent.right
                spacing: Theme.spacingSm
                TextField {
                    id: intervalField
                    objectName: "intervalField"
                    anchors.verticalCenter: parent.verticalCenter
                    width: 48
                    height: 24
                    enabled: !pop.isStatic
                    text: String(pop.shownInterval())
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontControl
                    horizontalAlignment: Text.AlignRight
                    // 1..1440 minutes, 1..86400 seconds. The validator only caps the top so a
                    // typed 0 or an emptied field still reaches the handler, which rejects and re-reads
                    validator: IntValidator { bottom: 0; top: pop.activePl.unit === "s" ? 86400 : 1440 }
                    background: Rectangle {
                        color: Theme.inputWell
                        radius: Theme.radiusXs
                        border.width: 1
                        border.color: intervalField.activeFocus ? Theme.borderStrong : Theme.border
                    }
                    // Enter and focus loss both commit; an empty field never reaches
                    // editingFinished (not acceptable to the validator), so they are handled here
                    Keys.onReturnPressed: { pop.commitField(); focus = false }
                    Keys.onEnterPressed: { pop.commitField(); focus = false }
                    onActiveFocusChanged: if (!activeFocus) pop.commitField()
                    onEditingFinished: pop.commitField()
                }
                Rectangle {
                    objectName: "unitSegment"
                    anchors.verticalCenter: parent.verticalCenter
                    width: unitRow.implicitWidth + 2
                    height: 24
                    color: Theme.surface
                    radius: Theme.radiusSm
                    border.width: 1
                    border.color: Theme.borderStrong
                    clip: true
                    Row {
                        id: unitRow
                        anchors.fill: parent
                        anchors.margins: 1
                        Repeater {
                            model: ["min", "s"]
                            delegate: Rectangle {
                                id: unit
                                required property string modelData
                                required property int index
                                readonly property bool active: (pop.activePl.unit || "min") === unit.modelData
                                width: unitLabel.implicitWidth + Theme.spacingMd * 2
                                height: parent.height
                                color: active ? Theme.surfaceVariant : unitHover.hovered ? Theme.hoverWash : "transparent"
                                Rectangle { visible: unit.index > 0; width: 1; height: parent.height; color: Theme.border }
                                Label {
                                    id: unitLabel
                                    anchors.centerIn: parent
                                    text: unit.modelData
                                    color: unit.active ? Theme.textPrimary : Theme.textSecondary
                                    font.pixelSize: Theme.fontControl
                                }
                                HoverHandler { id: unitHover; enabled: !pop.isStatic }
                                TapHandler {
                                    onTapped: {
                                        if (pop.isStatic || unit.active) return;
                                        // the number in the field is the one that changes unit, typed or not
                                        var shown = parseInt(intervalField.text);
                                        var iv = isNaN(shown) || shown < 1 ? (pop.activePl.interval || 900)
                                               : (pop.activePl.unit === "s" ? shown : shown * 60);
                                        if (unit.modelData === "s")
                                            backend.setPlaylistInterval(Math.min(86400, iv), "s");
                                        else
                                            backend.setPlaylistInterval(Math.max(1, Math.min(1440, Math.round(iv / 60))), "min");
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
